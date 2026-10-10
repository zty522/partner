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
    """Append-only facts plus small versioned semantic projections.

    The underlying ledger is the unified Mind Notes store
    (``share/mind/notes/notes.jsonl``) since Sprint 43: observations, lessons,
    preferences, habits, beliefs and growth are note types in one ledger
    instead of six parallel JSONL files.  The legacy JSONL files under
    ``share/mind/memory/`` are kept as migrated history (read-only); new
    semantic writes go to the note store.
    """

    # note_type 映射：memory kind -> note type
    _NOTE_TYPE = {"lesson": "lesson", "user_preference": "preference",
                  "habit": "habit", "belief": "belief", "growth": "growth",
                  "observation": "observation"}
    _STATUS = {"habit": "candidate", "growth": "confirmed",
               "lesson": "open", "belief": "open", "user_preference": "active",
               "observation": "recorded"}

    def __init__(self, workspace: str | Path):
        self.root = _root(workspace)
        self.directory = self.root / "share/mind/memory"
        self.observations = self.directory / "observations.jsonl"
        self.lessons = self.directory / "lessons.jsonl"
        self.preferences = self.directory / "user_preferences.jsonl"
        self.habits = self.directory / "habits.jsonl"
        self.beliefs = self.directory / "beliefs.jsonl"
        self.growth = self.directory / "growth.jsonl"
        from partner.mind_lab.notes import NoteStore
        self._notes = NoteStore(workspace)

    def _as_note(self, kind: str, row: Mapping[str, Any]) -> dict[str, Any]:
        note_type = self._NOTE_TYPE.get(kind, "observation")
        status = str(row.get("status") or self._STATUS.get(kind, "open"))
        if note_type == "habit" and status not in {"candidate", "active", "dismissed"}:
            status = "candidate"
        if note_type == "growth" and status not in {"confirmed", "dismissed"}:
            status = "confirmed"
        content = str(row.get("content") or row.get("headline") or "").strip()
        if not content:
            content = str(row.get("outcome") or "").strip()
        return {
            "content": content,
            "source": f"memory:{kind}",
            "confidence": float(row.get("confidence") or 0.5),
            "evidence_refs": list(row.get("evidence_refs") or []),
            "project_id": str(row.get("project_id") or ""),
            "scope": str(row.get("scope") or ""),
            "layer": str(row.get("layer") or "task_specific"),
            "counterexample": str(row.get("counterexample") or ""),
            "status": status,
            "created_at": str(row.get("recorded_at") or _now()),
        }

    def project_terminal(self, envelope: Mapping[str, Any], summary: Mapping[str, Any]) -> None:
        """Persist one raw experience into the note store (observation type)."""
        row = {
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
        }
        try:
            self._notes.append("observation", self._as_note("observation", row))
        except Exception:
            # fallback: legacy JSONL so observation projection never breaks the run
            _append(self.observations, row)

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
        """Recall from the unified note store, preserving the legacy dict shape."""
        from partner.index.stream_projection import StreamProjection
        repo = StreamProjection(self.root)
        def fetch(path, note_type=None, status=None):
            if note_type:
                notes = self._notes.recall(query=query, types=[note_type],
                                           statuses=[status] if status else None,
                                           limit=limit, project_id=project_id)
                if not notes:
                    # legacy fallback: historical rows still live in old JSONL
                    return repo.memory(path, project_id=project_id, query=query,
                                       limit=limit, status=status)
                return notes
            return repo.memory(path, project_id=project_id, query=query,
                               limit=limit, status=status)
        return {'observations': fetch(self.observations, 'observation'),
                'lessons': fetch(self.lessons, 'lesson'),
                'preferences': fetch(self.preferences, 'preference'),
                'active_habits': fetch(self.habits, 'habit', 'active'),
                'candidate_habits': fetch(self.habits, 'habit', 'candidate'),
                'beliefs': fetch(self.beliefs, 'belief'),
                'growth': fetch(self.growth, 'growth')}

    def record_usage(self, *, event_id, flow_id, memory_ids, effect, status='referenced'):
        if status not in ('referenced','applied','rejected'):
            raise ValueError('invalid memory consumption status')
        _append(self.directory/'usage.jsonl',{'event_id':event_id,'flow_id':flow_id,
            'memory_ids':list(dict.fromkeys(memory_ids)), 'effect':effect,'status':status,'recorded_at':_now()})

    def append_semantic(self, kind: str, record: Mapping[str, Any]) -> str:
        """Append a semantic memory into the unified note store.

        Returns the legacy JSONL path for backward-compatible callers; the
        actual row lands in ``share/mind/notes/notes.jsonl``.
        """
        paths = {"lesson": self.lessons, "user_preference": self.preferences,
                 "habit": self.habits, "belief": self.beliefs, "growth": self.growth}
        if kind not in paths:
            raise ValueError(f"unknown memory kind: {kind}")
        supplied = dict(record)
        note = self._as_note(kind, supplied)
        try:
            self._notes.append(self._NOTE_TYPE.get(kind, "observation"), note)
        except Exception:
            # fallback to legacy JSONL so semantic memory never breaks the run
            import uuid
            default_id = (f"{kind}_{supplied.get('scope')}"
                          if supplied.get("scope") and supplied.get("content") == supplied.get("scope")
                          else uuid.uuid4().hex)
            row = {"schema_version": 1, "recorded_at": _now(),
                   "record_id": default_id, **supplied}
            _append(paths[kind], row)
        return str(paths[kind])


class MemoryProjector:
    def __init__(self, workspace: str | Path):
        self.memory = EventMemory(workspace)

    def project_terminal(self, envelope: Mapping[str, Any], summary: Mapping[str, Any]) -> None:
        self.memory.project_terminal(envelope, summary)
        self.memory.link_usage_outcome(envelope, summary)
