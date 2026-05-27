"""Dialog History - persistent conversation history storage.

Stores each conversation turn as a JSON line in dialog_history.jsonl.
Supports loading recent turns and searching by topic.
"""

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import List, Optional


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
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def append(self, turn: DialogTurn):
        """Append a single turn to the history file."""
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")

    def load_recent(self, n: int = 20) -> List[DialogTurn]:
        """Load the most recent N turns from history."""
        if not os.path.exists(self.path):
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
        if not os.path.exists(self.path):
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
                next_nl = stripped.find('\n', end)
                if next_nl == -1:
                    break
                pos = pos + (len(content[pos:]) - len(stripped)) + next_nl + 1
            except (json.JSONDecodeError, TypeError):
                next_nl = content.find('\n', pos)
                if next_nl == -1:
                    break
                pos = next_nl + 1

        return results[-limit:]

    def count(self) -> int:
        """Count total turns in history."""
        if not os.path.exists(self.path):
            return 0
        with open(self.path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def clear(self):
        """Clear all history."""
        if os.path.exists(self.path):
            os.remove(self.path)
