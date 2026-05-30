"""Core — Partner 编排器（集成 Mind 系统版）。

Mind 系统接管了原来的 cron 驱动架构：
- 念头池 asyncio.PriorityQueue 作为执行引擎
- mind_loop() 永久运行，不断处理念头
- cron 心跳只做"注入唤醒脉冲"
- 所有自主行为通过念头产生+执行
"""

import os
import json
import time
import asyncio
import logging
import threading
import traceback
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from .config import PartnerConfig
from .task_queue import TaskQueue, Task
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager
from .autocheck import EventBus, SelfChecker, PushEvent
from .conversation import ConversationEngine

from .mind import (
    MindPool, mind_loop, init_executor,
    curiosity, report, cron_tick, user_message as user_msg_event,
)


class Partner:
    """Partner — 自主研究伙伴 + Mind 系统。

    运行时架构：
    ┌──────────────────────────────────┐
    │  mind_loop() (asyncio 主循环)    │
    │  ├─ 从池取念头 → 创建 Task 执行  │
    │  ├─ 休眠 0.1s（池空时）          │
    │  └─ 捕获异常不崩溃              │
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

        state_dir = os.path.join(self.workspace, "state")
        os.makedirs(state_dir, exist_ok=True)
        os.makedirs(os.path.join(self.workspace, "knowledge"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace, "logs"), exist_ok=True)

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

        # Mind 系统
        self._pool: Optional[MindPool] = None
        self._mind_thread: Optional[threading.Thread] = None
        self._mind_loop: Optional[asyncio.AbstractEventLoop] = None

        self._cycle_count = 0

    # ── 通知管理 ───────────────────────────────────────────────

    def _notify_admin(self, msg: str):
        """发送管理通知（写 notification 文件）。"""
        try:
            log_dir = os.path.join(
                getattr(self, '_workspace', self.workspace),
                "10_logs" if os.path.isdir(os.path.join(self.workspace, "10_logs"))
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

    async def _init_mind(self):
        """初始化 Mind 系统（异步）。"""
        self._pool = await MindPool.get_instance()

        # 从 adapter 获取后端
        from .adapter import create_adapter
        adapter = create_adapter(self.config.agent.backend, self.workspace)

        # 初始化 executor 上下文
        init_executor(
            workspace=self.workspace,
            adapter=adapter,
            knowledge=self.knowledge,
            journal=self.journal,
            state=self.state,
        )

    def start_mind(self):
        """启动 Mind 循环，带异常捕获和自动重启。"""
        if self._mind_thread and self._mind_thread.is_alive():
            logger.warning("Mind loop 已在运行")
            return

        def _run():
            retry_count = 0
            max_retries_per_hour = 3
            cool_off = 120  # 冷却2分钟
            last_retry_time = 0

            while True:
                try:
                    self._mind_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self._mind_loop)
                    # 初始化
                    self._mind_loop.run_until_complete(self._init_mind())
                    # 启动 mind_loop（永久运行）
                    self._mind_loop.run_until_complete(mind_loop())
                except asyncio.CancelledError:
                    logger.info("[Core] Mind loop cancelled, exiting")
                    break
                except Exception as e:
                    now = time.time()
                    # 重置计数器：如果距离上次重试超过1小时
                    if now - last_retry_time > 3600:
                        retry_count = 0
                    retry_count += 1
                    last_retry_time = now

                    # 记录崩溃
                    log_dir = os.path.join(
                        getattr(self, '_workspace', self.workspace),
                        "10_logs" if os.path.isdir(os.path.join(self.workspace, "10_logs"))
                        else "logs"
                    )
                    os.makedirs(log_dir, exist_ok=True)
                    crash_log = os.path.join(log_dir, "crash.log")
                    try:
                        with open(crash_log, "a", encoding="utf-8") as f:
                            f.write(f"[{datetime.now().isoformat()}] Mind loop crashed: {e}\n")
                            traceback.print_exc(file=f)
                            f.write("\n")
                    except Exception:
                        pass

                    if retry_count > max_retries_per_hour:
                        logger.critical(
                            f"[Core] Mind loop crashed {retry_count}x in 1h, stopping"
                        )
                        self._notify_admin(
                            f"⚠️ Partner 崩溃超过{max_retries_per_hour}次/小时，已停止"
                        )
                        break

                    wait = min(cool_off * retry_count, 300)  # 退避最长5分钟
                    logger.warning(
                        f"[Core] Mind loop crashed ({e}), "
                        f"restarting in {wait}s "
                        f"(attempt {retry_count}/{max_retries_per_hour})"
                    )
                    time.sleep(wait)
                    # 重新创建事件循环
                    if self._mind_loop and not self._mind_loop.is_closed():
                        try:
                            self._mind_loop.close()
                        except Exception:
                            pass
                    self._mind_loop = None
                    continue

        self._mind_thread = threading.Thread(target=_run, daemon=True, name="mind-loop")
        self._mind_thread.start()
        logger.info("🧠 Mind loop 已启动（后台线程，带异常保护）")

    def stop_mind(self):
        """停止 mind_loop。"""
        if self._mind_loop and self._mind_loop.is_running():
            self._mind_loop.stop()
            logger.info("🧠 Mind loop 已停止")

    async def feed_cron_tick(self):
        """放入 cron_tick 念头（由 cron handler 调用）。"""
        pool = await MindPool.get_instance()
        await pool.put(cron_tick(source="hermes_cron"))

    async def feed_user_message(self, text: str, sender_id: str = "",
                                 sender_name: str = ""):
        """放入一个用户消息念头（由 QQ bridge 调用）。"""
        pool = await MindPool.get_instance()
        ev = user_msg_event(text, sender_id, sender_name)
        await pool.put(ev)
        logger.info(f"[核心] 用户消息已放入念头池: {text[:50]}")

    # ── 原有接口（保留兼容） ────────────────────────────────────

    def start(self):
        """启动（后台模式）。"""
        print(f"🤝 Partner is starting...")
        print(f"   Workspace: {self.workspace}")
        print(f"   Backend: {self.config.agent.backend}")
        print(f"   Interval: {self.config.scheduler.interval_minutes} minutes")

        if self.state.detect_crash():
            print("⚠️  Detected previous crash. Recovering...")
            self._recover()

        self.state.heartbeat(status="idle")
        config_path = os.path.join(self.workspace, "partner_config.json")
        self.config.save(config_path)
        print("✅ Partner is running.")

    def run_cycle(self) -> Optional[str]:
        """运行一个研究周期（为向后兼容保留）。"""
        self.state.heartbeat(status="working")
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
