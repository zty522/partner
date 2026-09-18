"""Authoritative job queue backed by SQLite."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from .runtime_storage import workspace_dir
from .sqlite_base import get_connection


SCHEMA_VERSION = 2

SCHEMA_BASE_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id              TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    title               TEXT,
    request             TEXT,
    route               TEXT,
    channel             TEXT,
    sender_id           TEXT,
    sender_name         TEXT,
    persona_hint        TEXT,
    origin_instance     TEXT,
    assigned_instance   TEXT,
    intake_instance_id  TEXT,
    message_id          TEXT,
    root_event_id       TEXT,
    event_catalog_version TEXT,
    flow_id             TEXT,
    flow_type           TEXT,
    current_event_id    TEXT,
    intent_contract_path TEXT,
    intent_contract_json TEXT,
    intent_model_calls  INTEGER NOT NULL DEFAULT 0,
    report_policy       TEXT,
    error               TEXT,
    priority            INTEGER NOT NULL DEFAULT 100,
    status              TEXT NOT NULL DEFAULT 'queued',
    revision            INTEGER NOT NULL DEFAULT 0,
    lease_owner         TEXT,
    lease_expiry        REAL,
    fencing_token       INTEGER NOT NULL DEFAULT 0,
    cancel_requested    INTEGER NOT NULL DEFAULT 0,
    next_run_at         REAL NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    started_at          REAL,
    finished_at         REAL,
    ready_event_ids_json TEXT,
    completed_event_ids_json TEXT,
    suspended_flows_json TEXT,
    attachments_json    TEXT,
    metadata_json       TEXT,
    request_id          TEXT,
    request_fingerprint TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_next
    ON jobs(status, next_run_at, priority);
CREATE INDEX IF NOT EXISTS idx_jobs_project
    ON jobs(project_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_assigned_instance
    ON jobs(assigned_instance, status);
CREATE INDEX IF NOT EXISTS idx_jobs_lease_expiry
    ON jobs(lease_owner, lease_expiry);
CREATE INDEX IF NOT EXISTS idx_jobs_updated
    ON jobs(updated_at);
CREATE TABLE IF NOT EXISTS job_history (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,
    at              REAL NOT NULL,
    actor           TEXT,
    kind            TEXT NOT NULL,
    from_status     TEXT,
    to_status       TEXT,
    fencing_token   INTEGER,
    detail_json     TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_history_job
    ON job_history(job_id, seq);

CREATE TABLE IF NOT EXISTS outbox (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,
    path            TEXT NOT NULL,
    body            TEXT NOT NULL,
    enqueued_at     REAL NOT NULL,
    delivered       INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON outbox(delivered, seq);

CREATE TABLE IF NOT EXISTS ready_jobs (
    job_id          TEXT PRIMARY KEY,
    next_run_at     REAL NOT NULL,
    priority        INTEGER NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);
CREATE INDEX IF NOT EXISTS idx_ready_jobs_order
    ON ready_jobs(next_run_at, priority);
"""

# Extension indexes — depend on columns added by _migrate.  Created
# AFTER migration in _ensure_schema so they never reference a missing
# column on an old DB.
SCHEMA_EXTENSION_INDEXES_SQL = """CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_request_id
    ON jobs(request_id) WHERE request_id IS NOT NULL AND request_id != '';

"""

SCHEMA_SQL = SCHEMA_BASE_SQL  # backwards compat: SCHEMA_SQL points at the base.


def _now() -> float:
    return time.time()


def init(project_root):
    ws_dir = workspace_dir(project_root)
    db_path = ws_dir / "jobs.db"
    repo = JobRepository(db_path)
    repo._ensure_schema()
    return repo


def connect_existing(project_root):
    ws_dir = workspace_dir(project_root)
    db_path = ws_dir / "jobs.db"
    return JobRepository(db_path)


class JobRepository:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_ready = False

    def _ensure_schema(self):
        if self._schema_ready:
            return
        conn = get_connection(self.db_path)
        conn.executescript(SCHEMA_SQL)
        self._migrate(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self._schema_ready = True

    def _migrate(self, conn):
        existing = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        cols = [
            ("route", "TEXT"), ("event_catalog_version", "TEXT"),
            ("intent_contract_path", "TEXT"), ("intent_contract_json", "TEXT"),
            ("intent_model_calls", "INTEGER NOT NULL DEFAULT 0"),
            ("report_policy", "TEXT"), ("ready_event_ids_json", "TEXT"),
            ("completed_event_ids_json", "TEXT"), ("suspended_flows_json", "TEXT"),
            ("attachments_json", "TEXT"), ("metadata_json", "TEXT"),
            ("message_id", "TEXT"), ("started_at", "REAL"), ("finished_at", "REAL"),
            ("root_event_id", "TEXT"), ("current_event_id", "TEXT"),
            ("persona_hint", "TEXT"), ("sender_name", "TEXT"),
            ("flow_id", "TEXT"), ("flow_type", "TEXT"),
            ("title", "TEXT"), ("flow_version", "TEXT"), ("error", "TEXT"),
            ("origin_instance", "TEXT"), ("assigned_instance", "TEXT"),
            ("intake_instance_id", "TEXT"), ("sender_id", "TEXT"),
            ("request_id", "TEXT"), ("request_fingerprint", "TEXT"),
        ]
        for col, decl in cols:
            if col in existing:
                continue
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN " + col + " " + decl)
            except Exception as exc:
                # Migration must propagate failures so callers don't silently
                # mark the schema as ready and then race on missing columns.
                raise RuntimeError(
                    f"job_repository _migrate failed adding column {col!r}: {exc}"
                )
        # Idempotency index.  Only created AFTER the columns above exist; this
        # is the fix for Audit #1 (newly-created DB used to fail because the
        # inline CREATE INDEX ran before the new columns were in the schema).
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_request_id "
                     "ON jobs(request_id) WHERE request_id IS NOT NULL AND request_id != ''")
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def _record_to_row(self, record):
        def _ts(v):
            if v is None or v == "":
                return _now()
            if isinstance(v, (int, float)):
                return float(v)
            try:
                from datetime import datetime
                text = str(v)
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                return datetime.fromisoformat(text).timestamp()
            except Exception:
                return _now()

        def _js(v):
            if v is None:
                return None
            try:
                return json.dumps(v, ensure_ascii=False, sort_keys=True)
            except Exception:
                return None

        def _str(v):
            if v is None:
                return None
            if isinstance(v, (dict, list)):
                return json.dumps(v, ensure_ascii=False, sort_keys=True)
            return str(v)

        return (
            record.get("job_id", ""),
            record.get("project_id", ""),
            _str(record.get("title")),
            record.get("request"),
            record.get("route"),
            record.get("channel"),
            record.get("sender_id"),
            record.get("sender_name"),
            record.get("persona_hint"),
            record.get("origin_instance"),
            record.get("assigned_instance"),
            record.get("intake_instance_id"),
            record.get("message_id"),
            record.get("root_event_id"),
            record.get("event_catalog_version"),
            record.get("flow_id"),
            record.get("flow_type"),
            record.get("current_event_id"),
            record.get("intent_contract_path"),
            _js(record.get("intent_contract")),
            int(record.get("intent_model_calls") or 0),
            record.get("report_policy"),
            record.get("error"),
            int(record.get("priority") or 100),
            record.get("status", "queued"),
            int(record.get("revision") or 0),
            int(record.get("fencing_token") or 0),
            1 if record.get("cancel_requested") else 0,
            float(record.get("next_run_at") or _ts(record.get("updated_at"))),
            _ts(record.get("created_at")),
            _ts(record.get("updated_at")),
            _ts(record.get("started_at")),
            _ts(record.get("finished_at")),
            _js(record.get("ready_event_ids")),
            _js(record.get("completed_event_ids")),
            _js(record.get("suspended_flows")),
            _js(record.get("attachments")),
            _js(record.get("metadata")),
        )

    def upsert_from_record(self, record, actor="ApplicationService._save", *, owner=None, fencing_token=None, projection_path=None):
        self._ensure_schema()
        record = dict(record)
        job_id = str(record.get("job_id") or "")
        if not job_id:
            return
        row = self._record_to_row(record)
        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT status, fencing_token, lease_owner, lease_expiry, cancel_requested FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if owner is not None and (not existing or existing['lease_owner'] != owner
                    or existing['fencing_token'] != fencing_token
                    or (existing['lease_expiry'] or 0) <= _now()
                    or (existing['cancel_requested'] and record.get('status') != 'cancelled')):
                raise RuntimeError('lease ownership lost; checkpoint rejected')

            conn.execute(
                """INSERT INTO jobs
                (job_id, project_id, title, request, route, channel, sender_id,
                 sender_name, persona_hint, origin_instance, assigned_instance,
                 intake_instance_id, message_id, root_event_id,
                 event_catalog_version, flow_id, flow_type, current_event_id,
                 intent_contract_path, intent_contract_json, intent_model_calls,
                 report_policy, error, priority, status, revision, fencing_token,
                 cancel_requested, next_run_at, created_at, updated_at,
                 started_at, finished_at, ready_event_ids_json,
                 completed_event_ids_json, suspended_flows_json,
                 attachments_json, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    title = excluded.title,
                    request = excluded.request,
                    route = excluded.route,
                    channel = excluded.channel,
                    sender_id = excluded.sender_id,
                    sender_name = excluded.sender_name,
                    persona_hint = excluded.persona_hint,
                    origin_instance = excluded.origin_instance,
                    assigned_instance = excluded.assigned_instance,
                    intake_instance_id = excluded.intake_instance_id,
                    message_id = excluded.message_id,
                    root_event_id = excluded.root_event_id,
                    event_catalog_version = excluded.event_catalog_version,
                    flow_id = excluded.flow_id,
                    flow_type = excluded.flow_type,
                    current_event_id = excluded.current_event_id,
                    intent_contract_path = excluded.intent_contract_path,
                    intent_contract_json = excluded.intent_contract_json,
                    intent_model_calls = excluded.intent_model_calls,
                    report_policy = excluded.report_policy,
                    error = excluded.error,
                    priority = COALESCE(jobs.priority, 100),
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    ready_event_ids_json = excluded.ready_event_ids_json,
                    completed_event_ids_json = excluded.completed_event_ids_json,
                    suspended_flows_json = excluded.suspended_flows_json,
                    attachments_json = excluded.attachments_json,
                    metadata_json = excluded.metadata_json""",
                row,
            )
            from_status = existing["status"] if existing else None
            to_status = row[24]
            if existing is None or existing["status"] != to_status:
                conn.execute(
                    """INSERT INTO job_history
                    (job_id, at, actor, kind, from_status, to_status, fencing_token, detail_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job_id, time.time(), actor,
                     "save" if existing else "create",
                     from_status, to_status,
                     existing["fencing_token"] if existing else 0,
                     json.dumps({"event": "ApplicationService._save"})),
                )
            if to_status == "queued":
                conn.execute(
                    """INSERT INTO ready_jobs(job_id, next_run_at, priority)
                    VALUES (?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        next_run_at = excluded.next_run_at,
                        priority = excluded.priority""",
                    (job_id, row[29], row[23]),
                )
            else:
                conn.execute(
                    "DELETE FROM ready_jobs WHERE job_id = ?",
                    (job_id,),
                )

            if projection_path:
                conn.execute("INSERT INTO outbox(job_id,path,body,enqueued_at) VALUES (?,?,?,?)",
                             (job_id, str(projection_path), json.dumps(record, ensure_ascii=False), _now()))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def submit(self, *, project_id, request, title="", channel="", sender_id="",
               sender_name="", persona_hint="", origin_instance="",
               assigned_instance="", intake_instance_id="", priority=100,
               flow_type="", flow_version="", metadata=None):
        self._ensure_schema()
        job_id = "job_" + uuid.uuid4().hex[:16]
        now = _now()
        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN")
            conn.execute(
                """INSERT INTO jobs
                (job_id, project_id, title, request, channel, sender_id, sender_name,
                 persona_hint, origin_instance, assigned_instance, intake_instance_id,
                 priority, status, flow_type, flow_version, revision, fencing_token,
                 cancel_requested, next_run_at, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'queued', ?, ?, 0, 0, 0, ?, ?, ?, ?)""",
                (
                    job_id, project_id, title, request, channel, sender_id, sender_name,
                    persona_hint, origin_instance, assigned_instance, intake_instance_id,
                    priority, flow_type, flow_version, now, now, now,
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            conn.execute(
                "INSERT INTO ready_jobs(job_id, next_run_at, priority) VALUES (?, ?, ?)",
                (job_id, now, priority),
            )
            conn.execute(
                """INSERT INTO job_history
                (job_id, at, actor, kind, from_status, to_status, fencing_token, detail_json)
                VALUES (?, ?, ?, ?, NULL, 'queued', 0, ?)""",
                (job_id, now, "JobRepository.submit", "submit", "{}"),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return job_id

    def claim_next(self, *, owner, instance_id="", now=None, statuses=("queued",)):
        self._ensure_schema()
        now = now if now is not None else _now()
        statuses = tuple(statuses)
        if not statuses:
            return None
        placeholders = ",".join("?" for _ in statuses)
        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"""SELECT job_id, fencing_token
                FROM jobs
                WHERE status IN ({placeholders})
                  AND cancel_requested = 0
                  AND next_run_at <= ?
                  AND (lease_owner IS NULL OR (status != 'running' AND (lease_expiry IS NULL OR lease_expiry < ?)))
                ORDER BY priority ASC, next_run_at ASC, created_at ASC
                LIMIT 1""",
                (*statuses, now, now),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            new_token = int(row["fencing_token"]) + 1
            conn.execute(
                """UPDATE jobs
                SET status = 'running',
                    lease_owner = ?, lease_expiry = ?, fencing_token = ?,
                    updated_at = ?, started_at = COALESCE(started_at, ?)
                WHERE job_id = ?""",
                (owner, now + 60.0, new_token, now, now, row["job_id"]),
            )
            conn.execute("DELETE FROM ready_jobs WHERE job_id = ?", (row["job_id"],))
            from_status = conn.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()["status"]
            conn.execute(
                """INSERT INTO job_history
                (job_id, at, actor, kind, from_status, to_status, fencing_token, detail_json)
                VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
                (row["job_id"], now, owner, "claim", from_status, new_token,
                 json.dumps({"instance_id": instance_id}, sort_keys=True)),
            )
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            conn.execute("COMMIT")
            return dict(job) if job else None
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def claim_job(self, job_id, *, owner, instance_id="", now=None,
                  statuses=("queued", "dispatched", "running")):
        """Atomically claim a SPECIFIC job_id.

        Unlike claim_next() which claims the next runnable job in
        priority order, this claims exactly job_id.  Returns the full
        updated row (with new fencing_token and lease) on success, or
        None if the job does not exist, is not runnable, or is already
        leased to another worker with an unexpired lease.
        """
        self._ensure_schema()
        now = now if now is not None else _now()
        statuses = tuple(statuses)
        if not statuses:
            return None
        placeholders = ",".join("?" for _ in statuses)
        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"""SELECT job_id, fencing_token, status
                FROM jobs
                WHERE job_id = ?
                  AND status IN ({placeholders})
                  AND cancel_requested = 0
                  AND (lease_owner IS NULL OR (status != 'running' AND (lease_expiry IS NULL OR lease_expiry < ?)))""",
                (job_id, *statuses, now),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            new_token = int(row["fencing_token"]) + 1
            conn.execute(
                """UPDATE jobs SET status = 'running',
                    lease_owner = ?, lease_expiry = ?, fencing_token = ?,
                    updated_at = ?, started_at = COALESCE(started_at, ?)
                WHERE job_id = ?""",
                (owner, now + 60.0, new_token, now, now, job_id),
            )
            conn.execute("DELETE FROM ready_jobs WHERE job_id = ?", (job_id,))
            from_status = row["status"]
            conn.execute(
                """INSERT INTO job_history
                (job_id, at, actor, kind, from_status, to_status, fencing_token, detail_json)
                VALUES (?, ?, ?, 'claim', ?, 'running', ?, ?)""",
                (job_id, now, owner, from_status, new_token,
                 json.dumps({"instance_id": instance_id}, sort_keys=True)),
            )
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            conn.execute("COMMIT")
            return dict(job) if job else None
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def request_cancel(self, job_id, *, actor="system"):
        self._ensure_schema()
        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT status, fencing_token FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            now = _now()
            conn.execute(
                """UPDATE jobs SET cancel_requested = 1, updated_at = ?,
                status = CASE WHEN status IN ('completed','failed','cancelled')
                              THEN status ELSE 'cancelled' END
                WHERE job_id = ?""",
                (now, job_id),
            )
            conn.execute("DELETE FROM ready_jobs WHERE job_id = ?", (job_id,))
            conn.execute(
                """INSERT INTO job_history
                (job_id, at, actor, kind, from_status, to_status, fencing_token, detail_json)
                VALUES (?, ?, ?, 'request_cancel', ?, 'cancelled', ?, '{}')""",
                (job_id, now, actor, row["status"], row["fencing_token"]),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def release_owned(self, job_id, *, owner, fencing_token):
        conn = get_connection(self.db_path)
        conn.execute("UPDATE jobs SET lease_owner=NULL, lease_expiry=NULL WHERE job_id=? AND lease_owner=? AND fencing_token=?",
                     (job_id, owner, fencing_token))

    def renew_lease(self, job_id, *, owner, fencing_token, extend_seconds=60.0):
        self._ensure_schema()
        now = _now()
        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN")
            cur = conn.execute(
                """UPDATE jobs SET lease_expiry = ?, updated_at = ?
                WHERE job_id = ? AND lease_owner = ? AND fencing_token = ?
                  AND cancel_requested = 0""",
                (now + extend_seconds, now, job_id, owner, fencing_token),
            )
            conn.execute("COMMIT")
            return cur.rowcount > 0
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def finalize(self, job_id, *, owner, fencing_token, final_status,
                 detail=None):
        self._ensure_schema()
        if final_status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid final status {final_status!r}")
        now = _now()
        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN")
            cur = conn.execute(
                """UPDATE jobs SET status = ?, finished_at = ?, updated_at = ?,
                lease_owner = NULL, lease_expiry = NULL
                WHERE job_id = ? AND lease_owner = ? AND fencing_token = ?
                  AND status NOT IN ('completed','failed','cancelled')""",
                (final_status, now, now, job_id, owner, fencing_token),
            )
            if cur.rowcount == 0:
                conn.execute("COMMIT")
                return False
            conn.execute(
                """INSERT INTO job_history
                (job_id, at, actor, kind, from_status, to_status, fencing_token, detail_json)
                VALUES (?, ?, ?, 'finalize', 'running', ?, ?, ?)""",
                (job_id, now, owner, final_status, fencing_token,
                 json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def schedule(self, job_id, *, next_run_at):
        """Re-queue a job for a later run.

        Sprint 37: this must never revive a cancelled job.  ``claim_next``
        filters ``cancel_requested = 0``, so a row rewritten to ``queued`` after
        cancellation would either be invisible (stuck) or, once the flag was
        cleared, re-dispatched -- silently undoing a cancellation.  Only a live,
        non-terminal row may be rescheduled.  Returns True when it was.
        """
        self._ensure_schema()
        now = _now()
        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN")
            cur = conn.execute(
                """UPDATE jobs SET status = 'queued', next_run_at = ?,
                lease_owner = NULL, lease_expiry = NULL, updated_at = ?
                WHERE job_id = ?
                  AND cancel_requested = 0
                  AND status NOT IN ('completed', 'failed', 'cancelled')""",
                (next_run_at, now, job_id),
            )
            if cur.rowcount == 0:
                conn.execute("COMMIT")
                return False
            conn.execute(
                "INSERT OR REPLACE INTO ready_jobs(job_id, next_run_at, priority) VALUES (?, ?, 100)",
                (job_id, next_run_at),
            )
            conn.execute(
                """INSERT INTO job_history
                (job_id, at, actor, kind, from_status, to_status, fencing_token, detail_json)
                VALUES (?, ?, ?, 'schedule', 'running', 'queued', 0, '{}')""",
                (job_id, now, "JobRepository.schedule"),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get(self, job_id):
        self._ensure_schema()
        row = get_connection(self.db_path).execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_record(self, job_id):
        row = self.get(job_id)
        if row is None:
            return None
        return _row_to_record(row)

    def history(self, job_id):
        self._ensure_schema()
        rows = get_connection(self.db_path).execute(
            """SELECT seq, job_id, at, actor, kind, from_status, to_status,
            fencing_token, detail_json
            FROM job_history WHERE job_id = ? ORDER BY seq""",
            (job_id,),
        ).fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            if entry.get("detail_json"):
                try:
                    entry["detail"] = json.loads(entry.pop("detail_json"))
                except Exception:
                    entry["detail"] = None
            else:
                entry.pop("detail_json", None)
            out.append(entry)
        return out

    def list_by_status(self, statuses, *, limit=100):
        self._ensure_schema()
        statuses = list(statuses)
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = get_connection(self.db_path).execute(
            f"""SELECT job_id, project_id, status, priority, assigned_instance,
            next_run_at, created_at, updated_at, fencing_token, cancel_requested
            FROM jobs WHERE status IN ({placeholders})
            ORDER BY priority ASC, created_at ASC LIMIT ?""",
            (*statuses, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def outbox_enqueue(self, job_id, path, body):
        self._ensure_schema()
        now = _now()
        get_connection(self.db_path).execute(
            "INSERT INTO outbox(job_id, path, body, enqueued_at) VALUES (?, ?, ?, ?)",
            (job_id, path, body, now),
        )

    def outbox_pending(self, limit=50):
        self._ensure_schema()
        rows = get_connection(self.db_path).execute(
            """SELECT seq, job_id, path, body, enqueued_at, attempts, last_error
            FROM outbox WHERE delivered = 0 ORDER BY seq LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    def outbox_mark(self, seq, *, delivered, error=""):
        conn = get_connection(self.db_path)
        if delivered:
            conn.execute(
                "UPDATE outbox SET delivered = 1, last_error = ? WHERE seq = ?",
                (error, int(seq)),
            )
        else:
            conn.execute(
                "UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE seq = ?",
                (error, int(seq)),
            )

    def outbox_emit_legacy_json(self, seq):
        # Local projection lock does not block the queue DB during slow 9p I/O.
        import os, fcntl
        conn=get_connection(self.db_path)
        with self.db_path.with_suffix('.projection.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            row=conn.execute('SELECT job_id,path FROM outbox WHERE seq=?',(int(seq),)).fetchone()
            if not row:return
            try:
                record=self.get_record(row['job_id'])
                path=Path(row['path']);path.parent.mkdir(parents=True,exist_ok=True)
                tmp=path.with_name(path.name+f'.{os.getpid()}.outbox.tmp')
                tmp.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8');tmp.replace(path)
                self.outbox_mark(seq,delivered=True)
            except OSError as exc:
                self.outbox_mark(seq,delivered=False,error=str(exc))

    def lookup_by_request_id(self, request_id):
        """Look up a job by idempotency key (request_id).

        Returns the row dict or None.  Used by ApplicationService and the
        web API to enforce idempotent submissions.
        """
        if not request_id:
            return None
        self._ensure_schema()
        conn = get_connection(self.db_path)
        row = conn.execute(
            "SELECT * FROM jobs WHERE request_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (str(request_id),),
        ).fetchone()
        return dict(row) if row else None

    def submit_idempotent(self, *, request_id, request_fingerprint="",
                          project_id, request, title="", channel="",
                          sender_id="", sender_name="", persona_hint="",
                          origin_instance="", assigned_instance="",
                          intake_instance_id="", priority=100, flow_type="",
                          flow_version="", metadata=None):
        """Submit a job with ``request_id`` idempotency.

        * If no job exists for this ``request_id``, insert one (transactional).
        * If a job exists with the **same** ``request_fingerprint``, return
          the existing row (idempotent success).
        * If a job exists with a **different** ``request_fingerprint``, raise
          ``RuntimeError("idempotency_conflict")`` — caller must reject
          the duplicate with HTTP 409 / appropriate CLI exit.
        """
        self._ensure_schema()
        if not request_id:
            # No idempotency key — fall back to plain submit, but return the
            # same dict shape as the idempotent path (a record, not job_id).
            job_id = self.submit(
                project_id=project_id, request=request, title=title,
                channel=channel, sender_id=sender_id, sender_name=sender_name,
                persona_hint=persona_hint, origin_instance=origin_instance,
                assigned_instance=assigned_instance,
                intake_instance_id=intake_instance_id, priority=priority,
                flow_type=flow_type, flow_version=flow_version,
                metadata=metadata,
            )
            return self.get_record(job_id)
        conn = get_connection(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT job_id, request_fingerprint, status FROM jobs "
                "WHERE request_id = ? LIMIT 1",
                (str(request_id),),
            ).fetchone()
            if row:
                existing_fp = row["request_fingerprint"] or ""
                if existing_fp and existing_fp != request_fingerprint:
                    conn.execute("ROLLBACK")
                    raise RuntimeError("idempotency_conflict")
                conn.execute("COMMIT")
                # Idempotent hit — return existing row
                return self.get_record(row["job_id"])
            # New row.  Generate job_id here so the unique index can catch
            # races on request_id.
            import uuid as _uuid
            job_id = "job_" + _uuid.uuid4().hex[:16]
            now = _now()
            conn.execute(
                """INSERT INTO jobs
                (job_id, project_id, title, request, channel, sender_id,
                 sender_name, persona_hint, origin_instance, assigned_instance,
                 intake_instance_id, priority, status, flow_type, flow_version,
                 revision, fencing_token, cancel_requested, next_run_at,
                 created_at, updated_at, metadata_json, request_id,
                 request_fingerprint)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,
                        'queued', ?, ?, 0, 0, 0, ?, ?, ?, ?, ?, ?)""",
                (job_id, project_id, title, request, channel, sender_id,
                 sender_name, persona_hint, origin_instance, assigned_instance,
                 intake_instance_id, priority, flow_type, flow_version,
                 now, now, now,
                 json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                 str(request_id), str(request_fingerprint)),
            )
            conn.execute("COMMIT")
            return self.get_record(job_id)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def stats(self):
        self._ensure_schema()
        rows = get_connection(self.db_path).execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}


def _row_to_record(row):
    out = dict(row)
    for json_col in ("intent_contract_json", "ready_event_ids_json",
                     "completed_event_ids_json", "suspended_flows_json",
                     "attachments_json", "metadata_json"):
        v = out.pop(json_col, None)
        if v:
            try:
                out[json_col.removesuffix("_json")] = json.loads(v)
            except Exception:
                out[json_col.removesuffix("_json")] = None
        else:
            out[json_col.removesuffix("_json")] = None
    from datetime import datetime, timezone
    for col in ("created_at", "updated_at", "started_at", "finished_at"):
        v = out.get(col)
        if isinstance(v, (int, float)) and v > 0:
            out[col] = datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat()
    out.pop("schema_version", None)
    return out
