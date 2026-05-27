"""Core — Partner 编排器（精简版）。

Partner 的实际执行引擎是 Hermes cron 驱动的 partner-research skill。
此模块仅保留最小的 Python 接口用于状态管理和 CLI 交互。

执行逻辑（已迁移到 Hermes cron）：
  每 15 分钟 cron 读取 state/ → 5 分支决策树 → 执行 → 推送 QQ
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from .config import PartnerConfig
from .task_queue import TaskQueue, Task
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager
from .event_bus import EventBus, PushEvent
from .self_check import SelfChecker
from .conversation import ConversationEngine


class Partner:
    """Partner — 自主研究伙伴。

    职责范围：
    - 初始化组件（task_queue, knowledge, journal, state）
    - 提供 run_cycle() 接口（为 cron 调用保留，逻辑已迁移到 skill）
    - 对话接口（通过 QQ/conversation）
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

        # 新增：Event Bus + Self Check
        self.event_bus = EventBus(state_dir)
        self.self_checker = SelfChecker(state_dir)

        # 对话引擎
        self.conversation = ConversationEngine(
            self.journal, self.knowledge, self.task_queue, self.state,
            workspace=self.workspace,
        )

        self._cycle_count = 0

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
        """运行一个研究周期（为向后兼容保留）。

        实际逻辑已迁移到 Hermes cron。此方法仅保留轻量维护：
        1. 自检
        2. 推送未推送事件
        3. 更新心跳
        """
        self.state.heartbeat(status="working")

        result = None

        # 自检
        try:
            check_events = self.self_checker.run_all()
            if check_events:
                result = f"自检发现 {len(check_events)} 个问题"
                for ev in check_events:
                    result += f"\n  [{ev.subtype}] {ev.title}"
        except Exception as e:
            logger.warning(f"Self-check failed: {e}")

        # 检查是否有待处理的任务
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
        """添加研究任务。"""
        task = Task(
            type=task_type,
            title=title,
            description=description,
            priority=priority,
        )
        return self.task_queue.add_task(task)

    def _recover(self):
        """崩溃后恢复。"""
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
