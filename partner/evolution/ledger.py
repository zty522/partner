"""Append-only hash-chained ledger writer for Sprint18 decision loop.

Every event the loop writes goes through this module so that:
  * the schema (event_type, payload) is fixed;
  * the prev_hash chain is computed consistently;
  * readers can locate the events path without touching governance code.

Public API:
    append_event(workspace, *, event_type, subject_id, project_id, actor,
                 payload=None, evidence_refs=()) -> dict  (the full record)
    read_events(workspace, *, since_seq=0, event_type_prefix=None) -> list
    find_latest_subject(workspace, subject_id) -> dict | None
    count_subjects(workspace) -> dict  # {subject_id: event_count}
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEDGER_RELATIVE = "share/mind/governance/evolution_events.jsonl"
DECISION_LEDGER_RELATIVE = "share/mind/governance/decision_loop_events.jsonl"
SCHEMA_VERSION = 2


def ledger_path(workspace_root: Path | str) -> Path:
    root = Path(workspace_root)
    epoch = root / "share/mind/governance/evolution_ledger_epoch.json"
    # Once the governance epoch is split, this retired schema-v2 loop gets a
    # separate evidence log and can no longer corrupt the promotion ledger.
    return root / (DECISION_LEDGER_RELATIVE if epoch.is_file() else LEDGER_RELATIVE)


def _read_last_hash(p: Path) -> str:
    if not p.exists():
        return ""
    last = ""
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if last:
            return json.loads(last).get("event_hash", "")
    except Exception:
        return ""
    return ""


def _read_seq(p: Path) -> int:
    if not p.exists():
        return 0
    seq = 0
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    s = r.get("seq", 0)
                    if isinstance(s, int) and s > seq:
                        seq = s
                except Exception:
                    pass
    except Exception:
        return 0
    return seq


def append_event(
    workspace_root: Path | str,
    *,
    event_type: str,
    subject_id: str,
    project_id: str,
    actor: str,
    payload: dict[str, Any] | None = None,
    evidence_refs: tuple[str, ...] = (),
    parents: tuple[str, ...] = (),
) -> dict[str, Any]:
    p = ledger_path(workspace_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = _read_last_hash(p)
    seq = _read_seq(p) + 1
    record = {
        "schema_version": SCHEMA_VERSION,
        "seq": seq,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "actor": actor,
        "subject_id": subject_id,
        "project_id": project_id,
        "parents": list(parents),
        "payload": payload or {},
        "evidence_refs": list(evidence_refs),
        "prev_hash": prev_hash,
    }
    body = json.dumps(record, ensure_ascii=False, sort_keys=True)
    record["event_hash"] = hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_events(
    workspace_root: Path | str,
    *,
    since_seq: int = 0,
    event_type_prefix: str | None = None,
    subject_id: str | None = None,
) -> list[dict[str, Any]]:
    p = ledger_path(workspace_root)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("seq", 0) < since_seq:
                    continue
                if event_type_prefix and not r.get("event_type", "").startswith(event_type_prefix):
                    continue
                if subject_id and r.get("subject_id") != subject_id:
                    continue
                out.append(r)
    except Exception:
        return []
    return out


def find_latest_subject(workspace_root: Path | str, subject_id: str) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    for r in read_events(workspace_root, subject_id=subject_id):
        last = r
    return last


def count_subjects(workspace_root: Path | str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in read_events(workspace_root):
        sid = r.get("subject_id", "")
        if sid:
            out[sid] = out.get(sid, 0) + 1
    return out


def events_for_slot(workspace_root: Path | str, subject_id: str) -> list[dict[str, Any]]:
    """Return the per-event chronological view of one slot's run."""
    return read_events(workspace_root, subject_id=subject_id)


__all__ = [
    "append_event",
    "read_events",
    "find_latest_subject",
    "count_subjects",
    "events_for_slot",
    "ledger_path",
    "SCHEMA_VERSION",
]
