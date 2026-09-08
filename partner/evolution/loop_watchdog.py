"""Sprint18 decision-loop watchdog.

Scans ``share/mind/governance/evolution_events.jsonl`` for subjects that
entered the loop but never reached ``loop/completion_recorded``. Each
stalled subject gets a ``loop/stuck_recorded`` event so the LLM can see
that the previous run was empty/stuck.

Also computes a per-subject completion summary that callers can attach
to user-facing diagnostics — the gap between "events written" and
"loop completed" is what determines whether a run was empty, stuck, or
healthy.

Public API:
    detect_stuck(workspace, *, max_age_seconds=120) -> list[dict]
    summarise_subject(workspace, subject_id) -> dict
    healthy_subjects(workspace, *, since_seq=0) -> list[dict]
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import ledger as _ledger


TERMINAL_EVENTS = {
    "loop/completion_recorded",
    "loop/stuck_recorded",
    "loop/branch_panic_recorded",
}

ENTRY_EVENT = "observe/context_recorded"


def _parse_iso(ts_str: str) -> datetime:
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def summarise_subject(workspace_root: Path | str, subject_id: str) -> dict[str, Any]:
    events = _ledger.read_events(workspace_root, subject_id=subject_id)
    if not events:
        return {"subject_id": subject_id, "exists": False}
    event_types = [e.get("event_type", "") for e in events]
    has_observe = ENTRY_EVENT in event_types
    has_diag = any(t.startswith("diagnose/") for t in event_types)
    has_decide = any(t.startswith("decide/") for t in event_types)
    has_completion = any(t in TERMINAL_EVENTS for t in event_types)
    branch_substeps = [
        t for t in event_types
        if t not in TERMINAL_EVENTS
        and not t.startswith(("observe/", "diagnose/", "decide/"))
    ]
    first_decide = next((e for e in events if e.get("event_type", "").startswith("decide/")), None)
    decided_action = ""
    if first_decide:
        decided_action = str((first_decide.get("payload") or {}).get("decision", {}).get("next_action", ""))
    return {
        "subject_id": subject_id,
        "exists": True,
        "events": len(events),
        "has_observe": has_observe,
        "has_diagnose": has_diag,
        "has_decide": has_decide,
        "has_completion": has_completion,
        "branch_substeps": branch_substeps,
        "decided_action": decided_action,
        "first_at": events[0].get("occurred_at"),
        "last_at": events[-1].get("occurred_at"),
    }


def detect_stuck(workspace_root: Path | str, *, max_age_seconds: int = 120) -> list[dict[str, Any]]:
    """Subjects whose first event is older than max_age_seconds and that have
    not written any terminal event yet. Each gets a ``loop/stuck_recorded`` event."""
    workspace_root = Path(workspace_root)
    events = _ledger.read_events(workspace_root)
    if not events:
        return []
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        sid = e.get("subject_id", "") or ""
        if not sid:
            continue
        by_subject.setdefault(sid, []).append(e)

    stuck: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for sid, evs in by_subject.items():
        terminal = [e for e in evs if e.get("event_type") in TERMINAL_EVENTS]
        if terminal:
            continue
        first_ts = _parse_iso(evs[0].get("occurred_at", ""))
        age = (now - first_ts).total_seconds()
        if age < max_age_seconds:
            continue
        # write stuck event
        _ledger.append_event(
            workspace_root, event_type="loop/stuck_recorded",
            subject_id=sid, project_id=evs[0].get("project_id", "agent_self_evolution"),
            actor="loop_watchdog",
            payload={"age_seconds": age, "max_age_seconds": max_age_seconds,
                     "events_so_far": len(evs)},
        )
        stuck.append({"subject_id": sid, "age_seconds": age, "events_so_far": len(evs)})
    return stuck


def healthy_subjects(workspace_root: Path | str, *, since_seq: int = 0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    events = _ledger.read_events(workspace_root, since_seq=since_seq)
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        sid = e.get("subject_id", "") or ""
        if sid:
            by_subject.setdefault(sid, []).append(e)
    for sid, evs in by_subject.items():
        summary = summarise_subject(workspace_root, sid)
        if summary.get("has_completion"):
            out.append({**summary, "completed": True})
    return out


__all__ = ["detect_stuck", "summarise_subject", "healthy_subjects"]
