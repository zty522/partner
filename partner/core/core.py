"""Core — Partner 编排器（Harness 驱动版）。

Harness 系统接管了事件执行：
- Harness (task_queue) 作为执行引擎
- Mind 系统只提供念头类型定义和执行函数
- cron 心跳只做"注入唤醒脉冲"
- 所有自主行为通过 Harness 直接调度
"""

import os
import json
import time
import asyncio
import logging
import threading
import traceback
from dataclasses import asdict
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from ..state.config import PartnerConfig, save_partner_config_data
from ..tasks.task_queue import TaskQueue, Task
from ..knowledge.knowledge import KnowledgeBase
from ..journal import Journal, JournalEntry
from ..state.state import StateManager
from ..monitoring.autocheck import EventBus, SelfChecker, PushEvent
from ..dialogue.conversation import ConversationEngine

from ..mind import (
    init_executor,
    report, cron_tick,
)


class Partner:
    """Partner — 自主研究伙伴（Harness 驱动版）。

    运行时架构：
    ┌──────────────────────────────────┐
    │  Harness (task_queue 驱动)       │
    │  ├─ 从队列取 Task → 执行         │
    │  ├─ 休眠 0.1s（队列空时）        │
    │  └─ 捕获异常不崩溃               │
    ├──────────────────────────────────┤
    │  QQ Bridge (独立线程/进程)        │
    │  ├─ 收到消息 → 直接回复          │
    │  └─ 同时放入 user_message 念头    │
    ├──────────────────────────────────┤
    │  Cron 心跳 (hermes cron)         │
    │  └─ 只做一件事: 放入 cron_tick   │
    └──────────────────────────────────┘
    """

    def __init__(self, config: PartnerConfig):
        self.config = config
        self.workspace = config.workspace.path
        self._current_status = "starting"

        state_dir = os.path.join(self.workspace, "state")
        os.makedirs(state_dir, exist_ok=True)

        # 核心状态组件
        self.task_queue = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        self.knowledge = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
        self.journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        self.state = StateManager(state_dir)

        # Event Bus + Self Check
        self.event_bus = EventBus(state_dir)
        self.self_checker = SelfChecker(state_dir)

        # 对话引擎
        self.conversation = ConversationEngine(
            self.journal, self.knowledge, self.task_queue, self.state,
            workspace=self.workspace,
        )

    # ── 通知管理 ───────────────────────────────────────────────

    def _notify_admin(self, msg: str):
        """发送管理通知（写 notification 文件）。"""
        try:
            log_dir = os.path.join(
                getattr(self, '_workspace', self.workspace),
                "state/record" if os.path.isdir(os.path.join(self.workspace, "state/record"))
                else "logs"
            )
            os.makedirs(log_dir, exist_ok=True)
            ntf_path = os.path.join(log_dir, "admin_notify.log")
            with open(ntf_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {msg}\n")
            logger.warning(f"[NOTIFY] {msg}")
        except Exception as e:
            logger.error(f"[NOTIFY] 写入通知失败: {e}")

    # ── Mind 系统控制 ──────────────────────────────────────────

    def start_mind(self):
        """兼容旧接口 — 直接调用 init_executor。"""
        self.run_mind_startup()

    def run_mind_startup(self):
        """初始化 executor 上下文（非阻塞）。移除 MindPool 后简化为同步初始化。"""
        from ..monitoring.restart_tracker import RestartTracker
        tracker = RestartTracker(self.workspace)

        # 从 adapter 获取后端
        from ..adapters.adapter import create_adapter
        adapter = create_adapter(
            self.config.agent.backend,
            self.workspace,
            model=self.config.agent.model,
            provider=self.config.agent.provider,
        )

        # 初始化 executor 上下文
        init_executor(
            workspace=self.workspace,
            adapter=adapter,
            knowledge=self.knowledge,
            journal=self.journal,
            task_queue=self.task_queue,
            state=self.state,
            round_interval_sec=max(60, int(self.config.scheduler.interval_minutes) * 60),
        )
        from ..mind.executor import start_event_loop
        start_event_loop()
        logger.info("🧠 Mind executor 初始化完成")

    def stop_mind(self):
        """保留兼容 — MindPool 已移除，无需停止。"""
        logger.debug("stop_mind no-op (MindPool removed)")

    async def feed_cron_tick(self):
        """已废弃 — cron 心跳通过 cron 模块直接交付。"""
        import warnings
        warnings.warn("feed_cron_tick is deprecated — cron ticks go through the event queue", DeprecationWarning, stacklevel=2)
        logger.debug("feed_cron_tick no-op (Harness handles cron)")
        pass

    async def feed_user_message(self, text: str, sender_id: str = "",
                                 sender_name: str = ""):
        """已废弃 — 用户消息改由 bridge 通过 enqueue_user_message 直接注入事件。"""
        import warnings
        warnings.warn("feed_user_message is deprecated — use enqueue_user_message() instead", DeprecationWarning, stacklevel=2)
        logger.debug(f"[核心] feed_user_message 已废弃，忽略: {text[:50]}")

    # ── 原有接口（保留兼容） ────────────────────────────────────

    def start(self):
        """启动（后台模式）。"""
        # 应用资源限制
        from ..monitoring.resource_limiter import apply_limits
        apply_limits()

        print(f"🤝 Partner is starting...")
        print(f"   Workspace: {self.workspace}")
        print(f"   Backend: {self.config.agent.backend}")
        print(f"   Interval: {self.config.scheduler.interval_minutes} minutes")

        if self.state.detect_crash():
            print("⚠️  Detected previous crash. Recovering...")
            self._recover()

        self.state.heartbeat(status="idle")
        save_partner_config_data(self.workspace, asdict(self.config))
        print("✅ Partner is running.")

        # Background heartbeat thread — updates every 60s so other
        # platforms (e.g. Windows GUI) can detect this instance as alive
        # even when the main loop interval is long (e.g. 30 min).
        def _background_heartbeat():
            while True:
                time.sleep(60)
                try:
                    self.state.heartbeat(status=self._current_status)
                except Exception:
                    pass

        self._current_status = "idle"
        t = threading.Thread(target=_background_heartbeat, daemon=True)
        t.start()

    def run_cycle(self) -> Optional[str]:
        """运行一个研究周期（为向后兼容保留）。"""
        self.state.heartbeat(status="working")
        self._current_status = "working"
        result = None

        try:
            check_events = self.self_checker.run_all()
            if check_events:
                result = f"自检发现 {len(check_events)} 个问题"
                for ev in check_events:
                    result += f"\n  [{ev.subtype}] {ev.title}"
        except Exception as e:
            logger.warning(f"Self-check failed: {e}")

        try:
            task = self.task_queue.get_next()
            if task and not result:
                result = f"队列中有待执行任务: {task.title}"
        except Exception:
            pass

        self.state.heartbeat(status="idle")
        self._current_status = "idle"
        return result

    def chat(self, message: str) -> str:
        """与 Partner 对话。"""
        return self.conversation.respond(message)

    def status(self) -> str:
        """查看 Partner 状态。"""
        return self.conversation._handle_status()

    def add_task(self, title: str, description: str,
                 task_type: str = "deep_dive", priority: int = 5) -> str:
        task = Task(
            type=task_type,
            title=title,
            description=description,
            priority=priority,
        )
        return self.task_queue.add_task(task)

    def _recover(self):
        latest_cp = self.state.get_latest_checkpoint()
        if latest_cp:
            success = self.state.restore_from_checkpoint(
                latest_cp, self.task_queue.path, self.knowledge.path
            )
            if success:
                self.task_queue._load()
                self.knowledge._load()
                print(f"✅ Recovered from checkpoint: {latest_cp}")
            else:
                print(f"❌ Failed to recover from checkpoint: {latest_cp}")
        else:
            print("ℹ️  No checkpoint found. Starting fresh.")
