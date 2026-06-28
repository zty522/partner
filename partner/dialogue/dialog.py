"""Dialog — 对话历史与上下文管理。

合并自 dialog_history.py + context.py。

- DialogTurn / DialogHistory: 持久化对话历史（JSONL 存储）
- ContextManager: 滑动窗口上下文管理，用于多轮对话理解
"""

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import List, Optional


# ════════════════════════════════════════════════════════════════
# DialogTurn + DialogHistory（来自 dialog_history.py）
# ════════════════════════════════════════════════════════════════

@dataclass
class DialogTurn:
    """A single conversation turn."""
    role: str  # "user" | "partner"
    content: str
    timestamp: str
    intent: Optional[str] = None
    topic: Optional[str] = None


class DialogHistory:
    """Persistent dialog history stored as JSONL."""

    def __init__(self, path: str):
        self.path = path
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)

    def append(self, turn: DialogTurn):
        """Append a single turn to the history file."""
        if not self.path:
            return
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")

    def load_recent(self, n: int = 20) -> List[DialogTurn]:
        """Load the most recent N turns from history."""
        if not self.path or not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        turns = []
        for line in lines[-n:]:
            try:
                data = json.loads(line.strip(), strict=False)
                turns.append(DialogTurn(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        return turns

    def search_by_topic(self, topic: str, limit: int = 5) -> List[DialogTurn]:
        """Search history for turns matching a topic keyword."""
        if not self.path or not os.path.exists(self.path):
            return []
        results = []
        decoder = json.JSONDecoder(strict=False)
        with open(self.path, "r", encoding="utf-8") as f:
            content = f.read()
        pos = 0
        while pos < len(content):
            stripped = content[pos:].lstrip()
            if not stripped:
                break
            try:
                obj, end = decoder.raw_decode(stripped)
                if topic.lower() in obj.get("content", "").lower() or \
                   topic.lower() in (obj.get("topic") or "").lower():
                    results.append(DialogTurn(**obj))
                next_nl = stripped.find("\n", end)
                if next_nl == -1:
                    break
                pos = pos + (len(content[pos:]) - len(stripped)) + next_nl + 1
            except (json.JSONDecodeError, TypeError):
                next_nl = content.find("\n", pos)
                if next_nl == -1:
                    break
                pos = next_nl + 1
        return results[-limit:]

    def count(self) -> int:
        """Count total turns in history."""
        if not self.path or not os.path.exists(self.path):
            return 0
        with open(self.path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def clear(self):
        """Clear all history."""
        if self.path and os.path.exists(self.path):
            os.remove(self.path)


# ════════════════════════════════════════════════════════════════
# ContextManager（来自 context.py）
# ════════════════════════════════════════════════════════════════

class ContextManager:
    """Manages conversation context with a sliding window.

    Maintains recent conversation turns in memory for context-aware
    intent parsing and topic tracking. Loads from DialogHistory on init.
    """

    def __init__(self, history_path: str, max_turns: int = 10):
        self.history = DialogHistory(history_path) if history_path else None
        self.max_turns = max_turns
        self.recent_turns: List[DialogTurn] = []
        self._load_history()

    def _load_history(self):
        """Load recent history from disk into memory."""
        if self.history:
            self.recent_turns = self.history.load_recent(self.max_turns)

    def add_turn(self, role: str, content: str,
                 intent: str = None, topic: str = None):
        """Add a conversation turn."""
        turn = DialogTurn(
            role=role,
            content=content,
            timestamp=datetime.now().isoformat(),
            intent=intent,
            topic=topic,
        )
        self.recent_turns.append(turn)
        if len(self.recent_turns) > self.max_turns:
            self.recent_turns = self.recent_turns[-self.max_turns:]
        if self.history:
            self.history.append(turn)

    def get_active_topic(self) -> Optional[str]:
        """Get the most recent topic mentioned by the user."""
        for turn in reversed(self.recent_turns):
            if turn.topic:
                return turn.topic
        return None

    def get_context_summary(self) -> str:
        """Generate a context summary for intent parsing."""
        if not self.recent_turns:
            return ""
        lines = ["[对话上下文]"]
        for turn in self.recent_turns[-5:]:
            role_label = "用户" if turn.role == "user" else "Partner"
            lines.append(f"{role_label}: {turn.content[:100]}")
        active_topic = self.get_active_topic()
        if active_topic:
            lines.append(f"[当前话题: {active_topic}]")
        return "\n".join(lines)

    def get_last_partner_response(self) -> Optional[str]:
        """Get the most recent partner response text."""
        for turn in reversed(self.recent_turns):
            if turn.role == "partner":
                return turn.content
        return None

    def get_recent_user_messages(self, n: int = 3) -> List[str]:
        """Get the N most recent user messages."""
        user_msgs = [t.content for t in self.recent_turns if t.role == "user"]
        return user_msgs[-n:]

    def has_recent_context(self) -> bool:
        """Check if there's any recent context to work with."""
        return len(self.recent_turns) > 0

    def clear(self):
        """Clear all in-memory context (does not clear disk history)."""
        self.recent_turns = []
