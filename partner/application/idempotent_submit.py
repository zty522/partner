"""Atomic idempotent submission (M1 / Section 4) — round 6.

The reservation table is keyed by the natural triple
``(workspace_root, subject_id, request_id)``.  A single synthetic
auto-increment ``row_id`` is the only PRIMARY KEY.

Pre-allocated ``job_id`` (round 6)
---------------------------------
A fresh row stores a pre-allocated ``job_id`` (UUID) at insert time.
The orchestrator passes the same id into ``PartnerApplicationService.submit``
via ``preallocated_job_id`` so the Job record and the reservation row
reference the same Job from the start.  On crash, recovery reads
``idem_reservations.job_id`` and finds the Job record by that id.

State honesty: ``static_implemented``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3 as _sq
import time
import uuid
from typing import Any


class IdempotencyConflict(Exception):
    """``request_id`` reused with a different payload / scope."""


class IdempotencyScopeError(Exception):
    """The caller's subject/workspace does not match the existing
    reservation."""


class NotYetOwned(Exception):
    """The caller asked for the existing row but did not provide the
    correct fencing token; ownership was not transferred."""


class UnauthorisedInstanceError(Exception):
    """Caller is not allowed to submit to this instance."""


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def canonical_payload(*, instance: str, message: str,
                       project_id: str | None, mode: str | None,
                       scope: str | None, sender_id: str, reply_to: str,
                       recipient_ref: str | None,
                       attachments: list[dict] | None,
                       execution_constraints: dict) -> dict:
    return {
        "instance": instance,
        "message": str(message or "").strip(),
        "project_id": project_id or "",
        "mode": mode or "",
        "scope": scope or "",
        "sender_id": sender_id,
        "reply_to": reply_to,
        "recipient_ref": recipient_ref or "",
        "execution_constraints": json.dumps(
            execution_constraints, sort_keys=True, separators=(",", ":")
        ),
        "attachments": sorted(
            (json.dumps(a, sort_keys=True, separators=(",", ":"))
             for a in (attachments or [])),
            key=lambda s: s,
        ),
    }


def compute_fingerprint(*, instance: str, message: str,
                         project_id: str | None, mode: str | None,
                         scope: str | None, sender_id: str, reply_to: str,
                         recipient_ref: str | None,
                         attachments: list[dict] | None,
                         execution_constraints: dict) -> str:
    payload = canonical_payload(
        instance=instance, message=message, project_id=project_id,
        mode=mode, scope=scope, sender_id=sender_id, reply_to=reply_to,
        recipient_ref=recipient_ref,
        attachments=attachments, execution_constraints=execution_constraints,
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Connection factory — single source of truth, sets row_factory
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.time()


def _connect(db_path: str) -> _sq.Connection:
    conn = _sq.connect(db_path, isolation_level=None, timeout=30.0)
    conn.row_factory = _sq.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# ---------------------------------------------------------------------------
# Schema — single PRIMARY KEY, explicit UNIQUE constraint
# ---------------------------------------------------------------------------


RESERVATION_STATES = ("reserved", "intent_pending", "submitted",
                       "failed", "expired")

_LEGAL_TRANSITIONS = {
    "reserved":      {"intent_pending", "submitted", "failed"},
    "intent_pending": {"submitted", "failed"},
    "submitted":     set(),
    "failed":        {"reserved", "expired"},
    "expired":       set(),
}


def _ensure_reservations_table(conn) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS idem_reservations (
        row_id              INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id          TEXT NOT NULL,
        workspace_root      TEXT NOT NULL,
        subject_id          TEXT NOT NULL,
        fingerprint         TEXT NOT NULL,
        instance            TEXT NOT NULL,
        persona_hint        TEXT NOT NULL,
        state               TEXT NOT NULL CHECK (state IN
                                ('reserved','intent_pending','submitted',
                                 'failed','expired')),
        job_id              TEXT,
        assigned_instance   TEXT,
        owner_token         TEXT NOT NULL,
        preallocated_job_id TEXT,
        created_at          REAL NOT NULL,
        expires_at          REAL NOT NULL,
        updated_at          REAL NOT NULL,
        UNIQUE (workspace_root, subject_id, request_id)
    );
    CREATE INDEX IF NOT EXISTS idx_idem_state_expiry
        ON idem_reservations(state, expires_at);
    CREATE INDEX IF NOT EXISTS idx_idem_job
        ON idem_reservations(job_id);
    CREATE INDEX IF NOT EXISTS idx_idem_preallocated_job
        ON idem_reservations(preallocated_job_id);
    """)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _new_owner_token(subject_id: str, request_id: str, ts: float) -> str:
    h = hashlib.sha256(
        (subject_id + ":" + request_id + ":" + str(ts)).encode("utf-8")
    ).hexdigest()[:16]
    return "owner_" + h


def reserve(*, db_path: str, request_id: str, workspace_root: str,
            subject_id: str, fingerprint: str, instance: str,
            persona_hint: str, ttl_seconds: int = 600) -> dict:
    if not request_id:
        raise ValueError("request_id required")
    if not workspace_root:
        raise ValueError("workspace_root required")
    if not subject_id:
        raise ValueError("subject_id required")
    if not fingerprint:
        raise ValueError("fingerprint required")

    conn = _connect(db_path)
    try:
        _ensure_reservations_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM idem_reservations "
            "WHERE workspace_root = ? AND subject_id = ? AND request_id = ?",
            (workspace_root, subject_id, request_id),
        ).fetchone()

        if existing is not None:
            existing_fp = existing["fingerprint"]
            if existing_fp != fingerprint:
                conn.execute("ROLLBACK")
                raise IdempotencyConflict(
                    f"request_id={request_id!r} already bound to a "
                    f"different payload (state={existing['state']})"
                )
            out = _row_to_dict(existing)
            out["result"] = (
                "submitted" if existing["state"] == "submitted"
                else "in_progress"
            )
            conn.execute("COMMIT")
            return out

        now = _now()
        owner_token = _new_owner_token(subject_id, request_id, now)
        preallocated_job_id = "job_" + uuid.uuid4().hex[:16]
        cur = conn.execute(
            """INSERT INTO idem_reservations
            (request_id, workspace_root, subject_id, fingerprint,
             instance, persona_hint, state, owner_token,
             preallocated_job_id, created_at, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?)""",
            (request_id, workspace_root, subject_id, fingerprint,
             instance, persona_hint, owner_token, preallocated_job_id,
             now, now + ttl_seconds, now),
        )
        conn.execute("COMMIT")
        out = _row_to_dict(conn.execute(
            "SELECT * FROM idem_reservations WHERE row_id = ?",
            (cur.lastrowid,),
        ).fetchone())
        out["result"] = "created"
        return out
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def acquire_ownership(db_path: str, request_id: str,
                       workspace_root: str, subject_id: str,
                       owner_token: str) -> dict:
    conn = _connect(db_path)
    try:
        _ensure_reservations_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM idem_reservations "
            "WHERE workspace_root = ? AND subject_id = ? AND request_id = ?",
            (workspace_root, subject_id, request_id),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            raise LookupError(f"no reservation for {request_id!r}")
        if row["owner_token"] != owner_token:
            conn.execute("ROLLBACK")
            raise NotYetOwned(
                f"owner_token mismatch on {request_id!r}; current owner "
                f"is {row['owner_token'][:12]}..."
            )
        if row["state"] not in ("reserved", "failed"):
            conn.execute("ROLLBACK")
            raise NotYetOwned(
                f"reservation is in state {row['state']!r}; cannot transfer"
            )
        conn.execute(
            "UPDATE idem_reservations SET state='intent_pending', "
            "updated_at=? WHERE row_id = ? AND owner_token = ?",
            (_now(), row["row_id"], owner_token),
        )
        conn.execute("COMMIT")
        out = _row_to_dict(conn.execute(
            "SELECT * FROM idem_reservations WHERE row_id = ?",
            (row["row_id"],),
        ).fetchone())
        out["result"] = "owned"
        return out
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def mark_state(db_path: str, request_id: str, *, new_state: str,
                 workspace_root: str, subject_id: str, owner_token: str,
                 job_id: str | None = None,
                 assigned_instance: str | None = None) -> dict:
    if new_state not in RESERVATION_STATES:
        raise ValueError(f"invalid reservation state: {new_state!r}")
    conn = _connect(db_path)
    try:
        _ensure_reservations_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM idem_reservations "
            "WHERE workspace_root = ? AND subject_id = ? AND request_id = ?",
            (workspace_root, subject_id, request_id),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            raise LookupError(f"no reservation for {request_id!r}")
        if row["owner_token"] != owner_token:
            conn.execute("ROLLBACK")
            raise NotYetOwned(
                f"owner_token mismatch on {request_id!r}; state transition refused"
            )
        current = row["state"]
        if new_state not in _LEGAL_TRANSITIONS.get(current, set()):
            conn.execute("ROLLBACK")
            raise RuntimeError(
                f"illegal reservation transition {current!r} -> {new_state!r}"
            )
        if new_state == "submitted" and not job_id:
            conn.execute("ROLLBACK")
            raise ValueError("mark_state(submitted) requires job_id")
        cols = ["state = ?", "updated_at = ?"]
        args: list[Any] = [new_state, _now()]
        if job_id is not None:
            cols.append("job_id = ?")
            args.append(job_id)
        if assigned_instance is not None:
            cols.append("assigned_instance = ?")
            args.append(assigned_instance)
        args.extend([row["row_id"], owner_token])
        cur = conn.execute(
            f"UPDATE idem_reservations SET {', '.join(cols)} "
            "WHERE row_id = ? AND owner_token = ?",
            args,
        )
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            raise NotYetOwned(f"row {row['row_id']} no longer owned")
        conn.execute("COMMIT")
        out = _row_to_dict(conn.execute(
            "SELECT * FROM idem_reservations WHERE row_id = ?",
            (row["row_id"],),
        ).fetchone())
        out["result"] = "marked"
        return out
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def reap_expired(db_path: str) -> int:
    conn = _connect(db_path)
    try:
        _ensure_reservations_table(conn)
        now = _now()
        # autocommit connection (isolation_level=None): the UPDATE commits
        # immediately; no explicit COMMIT (which would raise "no transaction").
        cur = conn.execute(
            "UPDATE idem_reservations SET state='expired', updated_at=? "
            "WHERE state IN ('reserved','intent_pending') AND expires_at < ?",
            (now, now),
        )
        return cur.rowcount
    finally:
        conn.close()


def lookup(db_path: str, request_id: str, *,
            workspace_root: str, subject_id: str) -> dict | None:
    conn = _connect(db_path)
    try:
        _ensure_reservations_table(conn)
        row = conn.execute(
            "SELECT * FROM idem_reservations "
            "WHERE workspace_root = ? AND subject_id = ? AND request_id = ?",
            (workspace_root, subject_id, request_id),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def lookup_by_preallocated_job(db_path: str, preallocated_job_id: str) -> dict | None:
    """Recovery: find the reservation that pre-allocated the given
    ``preallocated_job_id`` (the id the orchestrator generated
    before any Job row was inserted).
    """
    conn = _connect(db_path)
    try:
        _ensure_reservations_table(conn)
        row = conn.execute(
            "SELECT * FROM idem_reservations WHERE preallocated_job_id = ? "
            "ORDER BY row_id DESC LIMIT 1",
            (preallocated_job_id,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def lookup_by_job(db_path: str, job_id: str) -> dict | None:
    conn = _connect(db_path)
    try:
        _ensure_reservations_table(conn)
        row = conn.execute(
            "SELECT * FROM idem_reservations WHERE job_id = ? ORDER BY row_id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()



def reacquire(*, db_path: str, request_id: str, workspace_root: str,
              subject_id: str, fingerprint: str, instance: str,
              persona_hint: str, ttl_seconds: int = 600) -> dict:
    """Recovery: re-take fenced ownership of an expired / failed
    reservation so a new caller can drive the same logical request to a
    Job.  Only allowed when the previous owner is gone:

    * ``state == "expired"`` (reap_expired marked it), or
    * ``state == "failed"`` (execution crashed after claiming), or
    * ``state in {"reserved","intent_pending"}`` with ``expires_at`` in the
      past (lease elapsed).

    ``state == "submitted"`` is NOT reacquired here — that Job already
    exists and must be replayed via ``lookup_by_job`` / the submitted
    path.  A fingerprint mismatch is still a hard ``IdempotencyConflict``.
    """
    if not request_id or not workspace_root or not subject_id or not fingerprint:
        raise ValueError("request_id / workspace_root / subject_id / fingerprint required")

    conn = _connect(db_path)
    try:
        _ensure_reservations_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM idem_reservations "
            "WHERE workspace_root = ? AND subject_id = ? AND request_id = ?",
            (workspace_root, subject_id, request_id),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            raise LookupError(f"no reservation for {request_id!r}")
        if row["fingerprint"] != fingerprint:
            conn.execute("ROLLBACK")
            raise IdempotencyConflict(
                f"request_id={request_id!r} already bound to a different payload"
            )
        state = row["state"]
        now = _now()
        expired_lease = (state in ("reserved", "intent_pending")
                         and float(row["expires_at"] or 0) < now)
        if state == "submitted":
            conn.execute("ROLLBACK")
            out = _row_to_dict(row)
            out["result"] = "submitted"
            return out
        if state not in ("expired", "failed") and not expired_lease:
            conn.execute("ROLLBACK")
            raise IdempotencyScopeError(
                f"cannot reacquire {request_id!r}: owner still active "
                f"(state={state!r})"
            )
        new_owner = _new_owner_token(subject_id, request_id, now)
        conn.execute(
            "UPDATE idem_reservations SET state='reserved', owner_token=?, "
            "expires_at=?, updated_at=?, job_id=NULL, assigned_instance=NULL "
            "WHERE row_id = ?",
            (new_owner, now + ttl_seconds, now, row["row_id"]),
        )
        conn.execute("COMMIT")
        out = _row_to_dict(conn.execute(
            "SELECT * FROM idem_reservations WHERE row_id = ?",
            (row["row_id"],),
        ).fetchone())
        out["result"] = "reacquired"
        return out
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


__all__ = [
    "IdempotencyConflict", "IdempotencyScopeError",
    "NotYetOwned", "UnauthorisedInstanceError",
    "RESERVATION_STATES",
    "canonical_payload", "compute_fingerprint",
    "reserve", "acquire_ownership", "mark_state", "reacquire",
    "reap_expired", "lookup", "lookup_by_job", "lookup_by_preallocated_job",
]
