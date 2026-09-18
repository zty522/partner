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

    def link_usage_outcome(self, envelope, summary):
        """An observed terminal is not proof that a recalled lesson caused improvement."""
        from partner.index.stream_projection import StreamProjection
        event_id=str(summary.get('event_id') or '')
        if not event_id:return
        rows=StreamProjection(self.root).rows(self.directory/'usage.jsonl',entity=event_id,limit=100)
        for usage in rows:
            _append(self.directory/'usage_outcomes.jsonl',{
                'event_id':event_id,'flow_id':envelope.get('flow_id'),
                'project_id':envelope.get('project_id'),'memory_ids':usage['memory_ids'],
                'status':'outcome_observed','terminal_status':summary.get('status'),
                'evidence_refs':summary.get('evidence_refs') or [],
                'business_delta':bool(summary.get('business_delta')),
                'causal_improvement_verified':False,'recorded_at':_now()})

    def _rows(self,path,limit=500):
        from partner.index.stream_projection import StreamProjection
        return list(reversed(StreamProjection(self.root).rows(path,limit=limit)))

    def recall(self, *, project_id='', query='', limit=8):
        from partner.index.stream_projection import StreamProjection
        repo=StreamProjection(self.root)
        def fetch(path,status=None):
            return repo.memory(path,project_id=project_id,query=query,limit=limit,status=status)
        return {'observations':fetch(self.observations), 'lessons':fetch(self.lessons),
            'preferences':fetch(self.preferences),'active_habits':fetch(self.habits,'active'),
            'candidate_habits':fetch(self.habits,'candidate'),'beliefs':fetch(self.beliefs),
            'growth':fetch(self.growth)}

    def record_usage(self, *, event_id, flow_id, memory_ids, effect, status='referenced'):
        if status not in ('referenced','applied','rejected'):
            raise ValueError('invalid memory consumption status')
        _append(self.directory/'usage.jsonl',{'event_id':event_id,'flow_id':flow_id,
            'memory_ids':list(dict.fromkeys(memory_ids)), 'effect':effect,'status':status,'recorded_at':_now()})

    def append_semantic(self, kind: str, record: Mapping[str, Any]) -> str:
        paths = {"lesson": self.lessons, "user_preference": self.preferences,
                 "habit": self.habits, "belief": self.beliefs, "growth": self.growth}
        if kind not in paths:
            raise ValueError(f"unknown memory kind: {kind}")
        import uuid
        row = {"schema_version": 1, "recorded_at": _now(), "record_id":uuid.uuid4().hex, **dict(record)}
        _append(paths[kind], row)
        return str(paths[kind])


class MemoryProjector:
    def __init__(self, workspace: str | Path):
        self.memory = EventMemory(workspace)

    def project_terminal(self, envelope: Mapping[str, Any], summary: Mapping[str, Any]) -> None:
        self.memory.project_terminal(envelope, summary)
        self.memory.link_usage_outcome(envelope, summary)
