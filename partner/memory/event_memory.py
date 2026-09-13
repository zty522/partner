"""Canonical Event-derived memory.

Raw terminal summaries are projected automatically.  Semantic changes such as
activating a habit or confirming growth remain explicit governed Events.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import fcntl
import json
import os


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(workspace: str | Path) -> Path:
    value = Path(workspace).resolve()
    return value.parent.parent if value.parent.name == "instances" else value


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class EventMemory:
    """Append-only facts plus small versioned semantic projections."""

    def __init__(self, workspace: str | Path):
        self.root = _root(workspace)
        self.directory = self.root / "share/mind/memory"
        self.observations = self.directory / "observations.jsonl"
        self.lessons = self.directory / "lessons.jsonl"
        self.preferences = self.directory / "user_preferences.jsonl"
        self.habits = self.directory / "habits.jsonl"
        self.beliefs = self.directory / "beliefs.jsonl"
        self.growth = self.directory / "growth.jsonl"

    def project_terminal(self, envelope: Mapping[str, Any], summary: Mapping[str, Any]) -> None:
        """Persist one raw experience without creating a recursive Event."""
        _append(self.observations, {
            "schema_version": 1, "recorded_at": _now(),
            "event_id": summary.get("event_id"), "event_type": envelope.get("event_type"),
            "series": envelope.get("series"), "flow_id": envelope.get("flow_id"),
            "project_id": envelope.get("project_id"), "instance_id": envelope.get("instance_id"),
            "status": summary.get("status"), "headline": summary.get("headline"),
            "outcome": summary.get("outcome"), "claims": summary.get("claims") or [],
            "evidence_refs": summary.get("evidence_refs") or [],
            "metrics": summary.get("metrics") or {},
            "failure_class": summary.get("failure_class") or "",
            "mechanism": summary.get("mechanism") or "",
            "business_delta": bool(summary.get("business_delta")),
            "learning_delta": bool(summary.get("learning_delta")),
            "evolution_delta": bool(summary.get("evolution_delta")),
        })

    @staticmethod
    def _rows(path: Path, limit: int = 500) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def recall(self, *, project_id: str = "", query: str = "", limit: int = 8) -> dict[str, Any]:
        words = {part.lower() for part in str(query).replace("/", " ").split() if len(part) > 1}

        def relevant(row: Mapping[str, Any]) -> bool:
            if project_id and row.get("project_id") not in {"", project_id}:
                return False
            if not words:
                return True
            body = json.dumps(dict(row), ensure_ascii=False).lower()
            return any(word in body for word in words)

        observations = [row for row in self._rows(self.observations) if relevant(row)][-limit:]
        return {
            "observations": observations,
            "preferences": self._rows(self.preferences, 100)[-limit:],
            "active_habits": [row for row in self._rows(self.habits, 200)
                              if row.get("status") == "active" and relevant(row)][-limit:],
            "beliefs": [row for row in self._rows(self.beliefs, 200)
                        if not project_id or row.get("project_id") == project_id][-limit:],
            "growth": [row for row in self._rows(self.growth, 100) if relevant(row)][-limit:],
        }

    def append_semantic(self, kind: str, record: Mapping[str, Any]) -> str:
        paths = {"lesson": self.lessons, "user_preference": self.preferences,
                 "habit": self.habits, "belief": self.beliefs, "growth": self.growth}
        if kind not in paths:
            raise ValueError(f"unknown memory kind: {kind}")
        row = {"schema_version": 1, "recorded_at": _now(), **dict(record)}
        _append(paths[kind], row)
        return str(paths[kind])


class MemoryProjector:
    def __init__(self, workspace: str | Path):
        self.memory = EventMemory(workspace)

    def project_terminal(self, envelope: Mapping[str, Any], summary: Mapping[str, Any]) -> None:
        self.memory.project_terminal(envelope, summary)
