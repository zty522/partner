"""Append-only, hash-linked Event Fabric ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import EVENT_SERIES, TERMINAL_STATUSES, EventEnvelope, EventSelection, EventSummary


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class EventLedger:
    """One local truth stream; execution details never depend on a frontend."""

    def __init__(self, workspace_root: str | os.PathLike):
        root = Path(workspace_root).expanduser().resolve()
        if root.parent.name == "instances":
            root = root.parent.parent
        self.root = root
        self.directory = root / "state" / "event_fabric"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_path = self.directory / "events.jsonl"
        self.summaries_path = self.directory / "summaries.jsonl"
        self.selections_path = self.directory / "selections.jsonl"
        # Canonical work truth.  The older event/summary files below remain
        # query projections for compatibility; this stream keeps envelope and
        # terminal outcome in one hash-linked history.
        self.work_items_path = self.directory / "work_items.jsonl"

    @contextmanager
    def _locked(self):
        with (self.directory / ".lock").open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _append(path: Path, record: Mapping[str, Any]) -> None:
        previous = ""
        if path.exists():
            with path.open("rb") as existing:
                existing.seek(0, os.SEEK_END)
                end = existing.tell()
                window = 65536
                while end:
                    start = max(0, end - window)
                    existing.seek(start)
                    tail = existing.read().rstrip(b'\r\n')
                    boundary = tail.rfind(b'\n')
                    if start == 0 or boundary >= 0:
                        last = tail[boundary+1:]
                        previous = str(json.loads(last).get('record_hash') or '')
                        if not previous:
                            raise ValueError('existing ledger tail has no record_hash')
                        break
                    window *= 2
            # A malformed nonempty tail is an integrity error, never a reason
            # to silently start a new hash chain inside the same file.
        row = dict(record)
        row["previous_hash"] = previous
        row["record_hash"] = hashlib.sha256((previous + _canonical(row)).encode("utf-8")).hexdigest()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def verify_chain(path: Path) -> dict[str, Any]:
        previous = ""
        count = 0
        if not path.exists():
            return {"ok": True, "records": 0, "path": str(path)}
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
            row = json.loads(line)
            stored = str(row.pop("record_hash", ""))
            linked = str(row.get("previous_hash") or "")
            expected = hashlib.sha256((previous + _canonical(row)).encode("utf-8")).hexdigest()
            if linked != previous or stored != expected:
                return {"ok": False, "records": count, "line": line_number,
                        "path": str(path), "error": "hash_chain_mismatch"}
            previous = stored
            count += 1
        return {"ok": True, "records": count, "path": str(path), "head": previous}

    def verify(self) -> dict[str, Any]:
        results = [self.verify_chain(path) for path in (
            self.events_path, self.summaries_path, self.selections_path,
            self.work_items_path,
        )]
        return {"ok": all(item["ok"] for item in results), "ledgers": results}

    def create(self, event_type: str, series: str, **values: Any) -> EventEnvelope:
        if series not in EVENT_SERIES:
            raise ValueError(f"unknown Event series: {series}")
        stamp = _now()
        event = EventEnvelope(
            event_id=str(values.pop("event_id", "") or f"evt_{uuid.uuid4().hex[:16]}"),
            event_type=event_type, series=series, created_at=stamp, updated_at=stamp, **values,
        )
        if not event.root_event_id:
            event.root_event_id = event.event_id
        if not event.work_item_id:
            event.work_item_id = event.job_id or event.correlation_id or event.root_event_id
        if not event.continuation_owner:
            event.continuation_owner = {
                "project": "project_runtime",
                "active_learning": "active_learning_runtime",
                "self_evolution": "self_evolution_runtime",
                "selector": "selector",
            }.get(event.series, "none")
        with self._locked():
            self._append(self.events_path, {"record_kind": "event_created", **event.to_dict()})
            self._append(self.work_items_path, {
                "record_kind": "work_item_event", "work_item_id": event.work_item_id,
                "event": event.to_dict(), "at": stamp,
            })
        return event

    def transition(self, event: EventEnvelope | str, status: str, **detail: Any) -> None:
        event_id = event.event_id if isinstance(event, EventEnvelope) else str(event)
        envelope = event.to_dict() if isinstance(event, EventEnvelope) else self.get_event_history(event_id)
        with self._locked():
            self._append(self.events_path, {
                "record_kind": "event_transition", "event_id": event_id,
                "status": status, "at": _now(), "detail": detail,
            })
            self._append(self.work_items_path, {
                "record_kind": "work_item_transition",
                "work_item_id": str(envelope.get("work_item_id") or envelope.get("job_id")
                                    or envelope.get("correlation_id") or envelope.get("root_event_id")
                                    or event_id),
                "event_id": event_id, "status": status, "detail": detail, "at": _now(),
            })

    def complete(self, event: EventEnvelope | str, summary: EventSummary) -> None:
        event_id = event.event_id if isinstance(event, EventEnvelope) else str(event)
        envelope = event.to_dict() if isinstance(event, EventEnvelope) else self.get_event_history(event_id)
        if summary.event_id != event_id:
            raise ValueError("summary event_id must match Event")
        if summary.status not in TERMINAL_STATUSES:
            raise ValueError("terminal Event summary requires a terminal status")
        if not summary.headline.strip():
            raise ValueError("terminal Event summary requires a headline")
        if not summary.created_at:
            summary.created_at = _now()
        with self._locked():
            self._append(self.summaries_path, {"record_kind": "event_summary", **summary.to_dict()})
            self._append(self.events_path, {
                "record_kind": "event_transition", "event_id": event_id,
                "status": summary.status, "at": summary.created_at,
                "summary_ref": f"summary:{event_id}",
            })
            self._append(self.work_items_path, {
                "record_kind": "work_item_terminal",
                "work_item_id": str(envelope.get("work_item_id") or envelope.get("job_id")
                                    or envelope.get("correlation_id") or envelope.get("root_event_id")
                                    or event_id),
                "event": envelope, "summary": summary.to_dict(),
                "status": summary.status, "at": summary.created_at,
            })
        # Memory is a projection of Event truth, not a second writer of truth.
        # Projection failure must not rewrite or invalidate the completed Event.
        try:
            from partner.memory import MemoryProjector
            MemoryProjector(self.root).project_terminal(envelope, summary.to_dict())
        except Exception:
            pass

    def work_item_history(self, work_item_id: str) -> list[dict[str, Any]]:
        """Read the canonical history for one unit of user or background work."""
        return list(reversed(self._projection().work_item_rows(self.work_items_path, work_item_id)))

    def record_selection(self, selection: EventSelection) -> None:
        if not selection.created_at:
            selection.created_at = _now()
        with self._locked():
            self._append(self.selections_path, {"record_kind": "event_selection", **selection.to_dict()})

    def _projection(self):
        from partner.index.stream_projection import StreamProjection
        return StreamProjection(self.root)

    def recent_summaries(self, *, limit=100, series='', project_id='', include_audit=False):
        return self._projection().summaries(self.events_path,self.summaries_path,
            limit=limit,series=series,project_id=project_id,include_audit=include_audit)

    def has_summary(self, event_id):
        return bool(self._projection().rows(self.summaries_path,entity=str(event_id).removeprefix('summary:'),limit=1))

    def get_event_history(self,event_id):
        return self._projection().event(self.events_path,str(event_id).removeprefix('summary:'))

    def batch_get_events(self,event_ids):
        return {eid:self.get_event_history(eid) for eid in event_ids}

    def event_index(self):
        # Compatibility maintenance API, without silently dropping older IDs.
        from partner.index.sqlite_base import get_connection
        projection=self._projection();projection.sync(self.events_path)
        rows=get_connection(projection.db).execute('SELECT payload FROM records WHERE path=? ORDER BY offset', (str(self.events_path.resolve()),))
        result={}
        for row in rows:
            value=json.loads(row[0]);eid=value.get('event_id')
            if eid:result.setdefault(eid,{}).update(value)
        return result
