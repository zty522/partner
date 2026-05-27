"""Event Bus — 基于 jsonl 的推送事件系统。

Partner 内部事件通过 Event Bus 传递：研究结果、自检发现、心跳状态等。
PushEvent 会在下次 cron 心跳时读取并推送到 QQ。

核心原则：
- 只追加，不覆写（jsonl 格式）
- 生产者和消费者解耦
- 优先级决定推送时机
"""

import json
import os
from datetime import datetime
from typing import Optional


class PushEvent:
    """一个推送事件记录。"""
    def __init__(self, type: str, subtype: str, title: str,
                 body: str = "", priority: int = 5,
                 id: str = "", created_at: str = "",
                 pushed: bool = False, push_target: str = "qq"):
        self.id = id or f"ev_{int(__import__('time').time())}_{abs(hash(str(vars()))) % 10000}"
        self.type = type
        self.subtype = subtype
        self.title = title
        self.body = body
        self.priority = priority
        self.created_at = created_at or datetime.now().isoformat(timespec="seconds")
        self.pushed = pushed
        self.push_target = push_target

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "subtype": self.subtype,
            "title": self.title, "body": self.body, "priority": self.priority,
            "created_at": self.created_at, "pushed": self.pushed,
            "push_target": self.push_target,
        }


class EventBus:
    """Event Bus — 读写 state/event_bus.jsonl。"""

    def __init__(self, state_dir: str):
        self.path = os.path.join(state_dir, "event_bus.jsonl")
        os.makedirs(state_dir, exist_ok=True)

    def push(self, event: PushEvent):
        """写入一条事件（追加）。"""
        event.pushed = False
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def pop_pending(self, min_priority: int = 5) -> list[PushEvent]:
        """获取所有未推送的事件（按优先级降序），并标记为已推。"""
        if not os.path.exists(self.path):
            return []

        pending = []
        remaining = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    ev = PushEvent(**data)
                    if not ev.pushed and ev.priority >= min_priority:
                        ev.pushed = True
                        pending.append(ev)
                    remaining.append(ev)
                except (json.JSONDecodeError, TypeError):
                    continue

        # 重写文件（标记已推的事件）
        if pending:
            with open(self.path, "w", encoding="utf-8") as f:
                for ev in remaining:
                    f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
            # 按优先级降序排列
            pending.sort(key=lambda e: -e.priority)

        return pending

    def peek_recent(self, n: int = 5) -> list[PushEvent]:
        """查看最近 n 条事件（不修改推送状态）。"""
        if not os.path.exists(self.path):
            return []
        events = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(PushEvent(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        return events[-n:]

    def count_unpushed(self) -> int:
        """统计未推送事件数。"""
        if not os.path.exists(self.path):
            return 0
        count = 0
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if not data.get("pushed", False):
                        count += 1
                except (json.JSONDecodeError, TypeError):
                    continue
        return count

    def clear_pushed(self):
        """清理已推送事件（保留未推送的）。"""
        if not os.path.exists(self.path):
            return
        remaining = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if not data.get("pushed", False):
                        remaining.append(data)
                except (json.JSONDecodeError, TypeError):
                    continue
        if remaining:
            with open(self.path, "w", encoding="utf-8") as f:
                for ev in remaining:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        elif os.path.exists(self.path):
            os.remove(self.path)
