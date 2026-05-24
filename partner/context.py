"""Context Manager - sliding window conversation context.

Maintains recent conversation turns in memory for context-aware
intent parsing and topic tracking. Loads from DialogHistory on init.
"""

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional

from .dialog_history import DialogHistory, DialogTurn


class ContextManager:
    """Manages conversation context with a sliding window."""

    def __init__(self, history_path: str, max_turns: int = 10):
        self.history = DialogHistory(history_path)
        self.max_turns = max_turns
        self.recent_turns: List[DialogTurn] = []
        self._load_history()

    def _load_history(self):
        """Load recent history from disk into memory."""
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
        # Keep only the most recent N turns in memory
        if len(self.recent_turns) > self.max_turns:
            self.recent_turns = self.recent_turns[-self.max_turns:]
        # Persist to disk
        self.history.append(turn)

    def get_active_topic(self) -> Optional[str]:
        """Get the most recent topic mentioned by the user."""
        for turn in reversed(self.recent_turns):
            if turn.topic:
                return turn.topic
        return None

    def get_context_summary(self) -> str:
        """Generate a context summary for intent parsing.

        Returns the last 5 turns as a compact text block,
        plus the active topic if one exists.
        """
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
