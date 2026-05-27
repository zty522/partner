"""Proactive Notifier — 简化版。

在 cron skill 接管通知推送后，此模块只做简单的状态检查：
检查是否有待推送的 Event Bus 事件。
"""

import json
import os
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class Notification:
    """一条通知。"""
    def __init__(self, title: str, body: str = "", priority: str = "normal"):
        self.title = title
        self.body = body
        self.priority = priority  # "low" | "normal" | "high"


class ProactiveNotifier:
    """简化版主动通知器 — 检查 Event Bus 的未推送事件。"""

    def __init__(self, knowledge, journal, state, workspace: str = ""):
        self.workspace = workspace
        state_dir = os.path.join(workspace, "state") if workspace else None
        self._event_bus_path = os.path.join(state_dir, "event_bus.jsonl") if state_dir else None

    def check_and_notify(self) -> List[Notification]:
        """检查是否有未推送的事件需要通知用户。

        读取 event_bus.jsonl 中仍未推送的高优先级事件。
        """
        if not self._event_bus_path or not os.path.exists(self._event_bus_path):
            return []

        notifications = []
        try:
            with open(self._event_bus_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if not data.get("pushed", False) and data.get("priority", 0) >= 8:
                            notifications.append(Notification(
                                title=data.get("title", ""),
                                body=data.get("body", ""),
                                priority="high" if data.get("priority", 0) >= 9 else "normal",
                            ))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except Exception as e:
            logger.warning(f"Failed to read event bus: {e}")

        return notifications

    def format_notifications(self, notifications: List[Notification]) -> str:
        """格式化通知为可读文本。"""
        if not notifications:
            return ""
        lines = ["📬 Partner 有新的发现：", ""]
        for n in notifications:
            icon = "🔴" if n.priority == "high" else "🟡"
            lines.append(f"  {icon} {n.title}")
            if n.body:
                lines.append(f"    {n.body}")
        return "\n".join(lines)
