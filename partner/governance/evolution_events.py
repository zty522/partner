"""Append-only Event-first ledger for governed Partner evolution.

This ledger is the control-plane source of truth for evolution transitions.
Candidate files, experiment JSON and control-policy entries are artifacts or
projections referenced by events; their existence alone is never evidence that
an action ran or an intervention improved production.
"""
from __future__ import annotations

import hashlib
import json
import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import append_jsonl, governance_log


EVENT_TYPES = {
    "issue/recorded",
    "candidate/proposed",
    "candidate/execution_requested",
    "candidate/execution_completed",
    "experiment/started",
    "experiment/completed",
    "policy/promoted",
    "policy/activated",
    "policy/canary_activated",
    "policy/canary_rolled_back",
    "policy/rejected",
    "policy/inconclusive",
    "record/invalidated",
    "active_learning/query_proposed",
    "active_learning/diagnosis_completed",
    "active_learning/feedback_recorded",
    "active_learning/strategy_revised",
    "active_learning/repair_evaluated",
    "active_learning/repair_canary_completed",
    "active_learning/repair_candidate_proposed",
    "active_learning/matched_experiment_completed",
    "active_learning/robustness_evaluated",
    "active_learning/uncertainty_diagnostic",
    "active_learning/topic_selected",
    "active_learning/waiting_evidence",
    "active_learning/candidate_bundled",
    "active_learning/policy_updated",
    "project/action_selected",
    "active_learning/project_action_selected",
}


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


@contextmanager
def _exclusive_ledger_lock(path: Path):
    """Serialize the read-sequence-append transaction across Partner slots."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_evolution_event(
    workspace: str,
    event_type: str,
    *,
    subject_id: str,
    payload: dict[str, Any],
    evidence_refs: list[str] | None = None,
    parents: list[str] | None = None,
    actor: str = "partner-governance",
    project_id: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Validate and append one hash-linked evolution event.

    ``idempotency_key`` is mandatory so retries cannot create duplicate state
    transitions. The event hash covers the full event except ``event_hash``.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported evolution event_type: {event_type}")
    subject_id = str(subject_id or "").strip()
    idempotency_key = str(idempotency_key or "").strip()
    if not subject_id:
        raise ValueError("subject_id is required")
    if not idempotency_key:
        raise ValueError("idempotency_key is required")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    path = governance_log(workspace, "evolution_events")
    with _exclusive_ledger_lock(path):
        rows = _rows(path)
        prior = next(
            (row for row in reversed(rows) if row.get("idempotency_key") == idempotency_key),
            None,
        )
        if prior is not None:
            return prior

        seq = int(rows[-1].get("seq") or 0) + 1 if rows else 1
        prev_hash = str(rows[-1].get("event_hash") or "GENESIS") if rows else "GENESIS"
        event_id = "evoevt_" + hashlib.sha256(
            f"{event_type}|{subject_id}|{idempotency_key}".encode("utf-8")
        ).hexdigest()[:20]
        event = {
            "schema_version": 1,
            "seq": seq,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": now_iso(),
            "actor": str(actor or "partner-governance"),
            "subject_id": subject_id,
            "project_id": str(project_id or ""),
            "parents": [str(value) for value in parents or [] if str(value)],
            "payload": payload,
            "evidence_refs": [str(value) for value in evidence_refs or [] if str(value)],
            "idempotency_key": idempotency_key,
            "prev_hash": prev_hash,
        }
        event["event_hash"] = hashlib.sha256(_canonical(event)).hexdigest()
        append_jsonl(path, event)
        return event


def load_evolution_events(workspace: str) -> list[dict[str, Any]]:
    return _rows(governance_log(workspace, "evolution_events"))


def verify_evolution_ledger(workspace: str) -> dict[str, Any]:
    rows = load_evolution_events(workspace)
    previous = "GENESIS"
    seen_keys: set[str] = set()
    legacy_records: list[dict[str, Any]] = []
    legacy_forks: list[dict[str, Any]] = []
    legacy_sequence_collisions: list[dict[str, Any]] = []
    previous_seq = 0
    for line_number, row in enumerate(rows, start=1):
        key = str(row.get("idempotency_key") or "")
        legacy_unkeyed = not key and not str(row.get("event_id") or "")
        # Before ADR 0067 retired the parallel Campaign/decision-loop writers,
        # multiple append implementations could race. Their immutable hashes
        # are still checkable, but sequence/parent continuity may fork.
        historical_parallel_writer = str(row.get("occurred_at") or "") < "2026-09-07"
        row_seq = int(row.get("seq") or 0)
        if row_seq != previous_seq + 1:
            if historical_parallel_writer and row_seq <= previous_seq:
                legacy_sequence_collisions.append({"line": line_number, "seq": row_seq})
            else:
                return {"ok": False, "reason": "seq_mismatch", "seq": row_seq,
                        "line": line_number, "expected_seq": previous_seq + 1}
        if row.get("prev_hash") != previous:
            if historical_parallel_writer:
                # Multiple retired writers appended without a shared lock,
                # producing an immutable fork whose record hash is still
                # independently verifiable. Never grant this exception to
                # current idempotent events.
                legacy_forks.append({"seq": row_seq, "line": line_number,
                                     "declared_prev_hash": str(row.get("prev_hash") or ""),
                                     "expected_prev_hash": previous})
            else:
                return {"ok": False, "reason": "prev_hash_mismatch", "seq": row_seq}
        if legacy_unkeyed:
            # The retired decision-loop/overnight writer used the same hash
            # chain before idempotency_key/event_id became mandatory. Preserve
            # and verify those immutable records, but expose them explicitly;
            # all current schema-v1 appends remain strict.
            legacy_records.append({"seq": row_seq, "line": line_number,
                                   "event_type": str(row.get("event_type") or ""),
                                   "subject_id": str(row.get("subject_id") or "")})
        elif not key or key in seen_keys:
            return {"ok": False, "reason": "idempotency_key_invalid", "seq": row_seq}
        check = dict(row)
        actual = str(check.pop("event_hash", ""))
        if legacy_unkeyed:
            legacy_body = json.dumps(check, ensure_ascii=False, sort_keys=True)
            expected = hashlib.sha256((str(check.get("prev_hash") or "")
                                       + legacy_body).encode("utf-8")).hexdigest()
        else:
            expected = hashlib.sha256(_canonical(check)).hexdigest()
        if actual != expected:
            return {"ok": False, "reason": "event_hash_mismatch", "seq": row_seq}
        if key:
            seen_keys.add(key)
        previous = actual
        previous_seq = max(previous_seq, row_seq)
    return {"ok": True, "event_count": len(rows), "head_hash": previous,
            "legacy_non_idempotent_count": len(legacy_records),
            "legacy_non_idempotent_records": legacy_records,
            "legacy_fork_count": len(legacy_forks),
            "legacy_forks": legacy_forks,
            "legacy_sequence_collision_count": len(legacy_sequence_collisions),
            "legacy_sequence_collisions": legacy_sequence_collisions}
