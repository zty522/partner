"""Authoritative historical-event index.

The append-only JSONL files (events.jsonl, summaries.jsonl,
work_items.jsonl, selections.jsonl) remain the canonical durable
stream for chain-of-custody.  This repository owns an indexed
projection over them so that point-in-time queries do not require
re-reading the entire 16M+58M+80M JSONL blob from a 9p mount.

The index lives on a native ext4 filesystem (via runtime_storage)
so reads do not stall.  The projection is rebuilt from the JSONL on
first use and then kept current through incremental ingest.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .runtime_storage import workspace_dir
from .sqlite_base import get_connection


SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id           TEXT PRIMARY KEY,
    event_type         TEXT NOT NULL,
    series             TEXT,
    project_id         TEXT,
    instance_id        TEXT,
    job_id             TEXT,
    flow_id            TEXT,
    parent_event_id    TEXT,
    status             TEXT,
    at                 REAL,
    finished_at        REAL,
    summary            TEXT,
    payload_json       TEXT,
    record_kind        TEXT,
    source_file        TEXT,
    source_byte_offset INTEGER,
    ingested_at        REAL
);

CREATE INDEX IF NOT EXISTS idx_events_type_at
    ON events(event_type, at DESC);
CREATE INDEX IF NOT EXISTS idx_events_job
    ON events(job_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_events_flow
    ON events(flow_id);
CREATE INDEX IF NOT EXISTS idx_events_project
    ON events(project_id, at DESC);

CREATE TABLE IF NOT EXISTS flows (
    flow_id            TEXT PRIMARY KEY,
    flow_type          TEXT NOT NULL,
    definition_version TEXT,
    task_id            TEXT,
    parent_flow_id     TEXT,
    project_id         TEXT,
    instance_id        TEXT,
    status             TEXT,
    current_node       TEXT,
    next_check_at      REAL,
    created_at         REAL,
    updated_at         REAL,
    ingested_at        REAL,
    summary_json       TEXT
);

CREATE INDEX IF NOT EXISTS idx_flows_task ON flows(task_id);
CREATE INDEX IF NOT EXISTS idx_flows_type ON flows(flow_type, status);

CREATE TABLE IF NOT EXISTS work_items (
    work_item_id       TEXT PRIMARY KEY,
    job_id             TEXT,
    kind               TEXT,
    status             TEXT,
    started_at         REAL,
    finished_at        REAL,
    parent_event_id    TEXT,
    result_excerpt     TEXT,
    source_file        TEXT,
    source_byte_offset INTEGER,
    ingested_at        REAL
);

CREATE INDEX IF NOT EXISTS idx_work_items_job
    ON work_items(job_id, started_at DESC);

CREATE TABLE IF NOT EXISTS jsonl_offsets (
    file_id    TEXT NOT NULL,
    source     TEXT NOT NULL,
    last_byte_offset INTEGER NOT NULL DEFAULT 0,
    last_seq         INTEGER NOT NULL DEFAULT 0,
    last_hash        TEXT,
    ingested_at      REAL,
    PRIMARY KEY (file_id)
);

CREATE INDEX IF NOT EXISTS idx_jsonl_offsets_source
    ON jsonl_offsets(source);
"""


def init(workspace_root: Path) -> "HistoryRepository":
    ws_dir = workspace_dir(workspace_root)
    db_path = ws_dir / "history.db"
    repo = HistoryRepository(db_path, workspace_root)
    repo._ensure_schema()
    return repo


def connect_existing(workspace_root: Path) -> "HistoryRepository":
    ws_dir = workspace_dir(workspace_root)
    db_path = ws_dir / "history.db"
    return HistoryRepository(db_path, workspace_root)


class HistoryRepository:
    def __init__(self, db_path: Path, workspace_root: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_root = Path(workspace_root)
        self.fabric_dir = self.workspace_root / "state" / "event_fabric"
        self.flow_dir = self.workspace_root / "state" / "event_flows"
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        conn = get_connection(self.db_path)
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self._schema_ready = True

    # ----- ingest -----
    def ingest_jsonl(self, source: str, *, force_full: bool = False) -> dict[str, int]:
        self._ensure_schema()
        path = self.fabric_dir / Path(source).name
        if not path.exists():
            return {"ingested": 0, "skipped": 0, "errors": 0, "fresh_byte_offset": 0}
        conn = get_connection(self.db_path)
        row = conn.execute(
            "SELECT last_byte_offset, last_seq FROM jsonl_offsets WHERE file_id = ?",
            (source,),
        ).fetchone()
        last_offset = 0 if force_full or row is None else int(row["last_byte_offset"])
        ingested = 0
        errors = 0
        new_offset = last_offset
        seq = (0 if row is None else int(row["last_seq"]))
        with path.open("rb") as handle:
            handle.seek(last_offset)
            tail = handle.read()
        if not tail:
            return {"ingested": 0, "skipped": 0, "errors": 0,
                    "fresh_byte_offset": last_offset}
        # Split lines; treat unterminated tail conservatively.
        lines = tail.split(b"\n")
        if tail and not tail.endswith(b"\n"):
            partial = lines.pop()
        else:
            partial = b""
        new_offset = last_offset + len(tail) - len(partial)
        for line in lines:
            if not line.strip():
                continue
            seq += 1
            try:
                row_dict = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                errors += 1
                continue
            try:
                self._ingest_record(source, row_dict, seq)
                ingested += 1
            except Exception:
                errors += 1
        conn.execute(
            """INSERT INTO jsonl_offsets(file_id, source, last_byte_offset, last_seq, ingested_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                last_byte_offset = excluded.last_byte_offset,
                last_seq = excluded.last_seq,
                ingested_at = excluded.ingested_at""",
            (source, source, new_offset, seq, time.time()),
        )
        return {
            "ingested": ingested, "skipped": 0, "errors": errors,
            "fresh_byte_offset": new_offset,
        }

    def _ingest_record(self, source: str, row: dict[str, Any], seq: int) -> None:
        conn = get_connection(self.db_path)
        if source.endswith("events.jsonl"):
            kind = row.get("record_kind")
            event_id = str(row.get("event_id") or row.get("id") or "")
            if not event_id:
                return
            if kind == "event_created":
                conn.execute(
                    """INSERT OR REPLACE INTO events
                    (event_id, event_type, series, project_id, instance_id, job_id,
                     flow_id, parent_event_id, status, at, payload_json, record_kind,
                     source_file, source_byte_offset, ingested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_id,
                        str(row.get("event_type") or ""),
                        str(row.get("series") or ""),
                        str(row.get("project_id") or ""),
                        str(row.get("instance_id") or ""),
                        str(row.get("job_id") or row.get("task_id") or ""),
                        str(row.get("flow_id") or ""),
                        str(row.get("parent_event_id") or ""),
                        str(row.get("status") or ""),
                        _parse_ts(row.get("at") or row.get("created_at")),
                        json.dumps(_safe_dict(row.get("payload") or {}), ensure_ascii=False, sort_keys=True),
                        str(kind or ""),
                        source,
                        0,
                        time.time(),
                    ),
                )
            else:
                conn.execute(
                    """UPDATE events SET status = COALESCE(?, status),
                    payload_json = COALESCE(?, payload_json)
                    WHERE event_id = ?""",
                    (
                        str(row.get("status") or "") or None,
                        json.dumps(_safe_dict(row.get("payload") or {}), ensure_ascii=False, sort_keys=True) if row.get("payload") else None,
                        event_id,
                    ),
                )
        elif source.endswith("summaries.jsonl"):
            event_id = str(row.get("event_id") or row.get("id") or "")
            if not event_id:
                return
            summary = row.get("summary") if isinstance(row.get("summary"), dict) else row
            conn.execute(
                """INSERT INTO events
                (event_id, event_type, series, project_id, instance_id, job_id,
                 flow_id, parent_event_id, status, at, finished_at, summary, payload_json,
                 record_kind, source_file, source_byte_offset, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    summary = COALESCE(excluded.summary, events.summary),
                    status = COALESCE(excluded.status, events.status),
                    finished_at = COALESCE(excluded.finished_at, events.finished_at),
                    payload_json = COALESCE(excluded.payload_json, events.payload_json),
                    ingested_at = excluded.ingested_at""",
                (
                    event_id,
                    str(summary.get("event_type") or row.get("event_type") or ""),
                    str(row.get("series") or ""),
                    str(row.get("project_id") or ""),
                    str(row.get("instance_id") or ""),
                    str(row.get("job_id") or row.get("correlation_id") or ""),
                    str(row.get("flow_id") or ""),
                    str(row.get("parent_event_id") or ""),
                    str(summary.get("status") or ""),
                    _parse_ts(summary.get("at") or row.get("at") or row.get("created_at")),
                    _parse_ts(summary.get("finished_at") or row.get("finished_at")),
                    json.dumps(_safe_dict(summary), ensure_ascii=False, sort_keys=True)[:60000],
                    json.dumps(_safe_dict(row.get("payload") or {}), ensure_ascii=False, sort_keys=True),
                    "summary",
                    source,
                    0,
                    time.time(),
                ),
            )
        elif source.endswith("work_items.jsonl"):
            work_item_id = str(row.get("work_item_id") or row.get("id") or "")
            if not work_item_id:
                return
            conn.execute(
                """INSERT OR REPLACE INTO work_items
                (work_item_id, job_id, kind, status, started_at, finished_at,
                 parent_event_id, result_excerpt, source_file, source_byte_offset, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    work_item_id,
                    str(row.get("job_id") or row.get("task_id") or ""),
                    str(row.get("kind") or ""),
                    str(row.get("status") or ""),
                    _parse_ts(row.get("started_at") or row.get("created_at")),
                    _parse_ts(row.get("finished_at") or row.get("updated_at")),
                    str(row.get("parent_event_id") or ""),
                    json.dumps(_safe_dict(row.get("result") or row.get("summary") or {}), ensure_ascii=False, sort_keys=True)[:6000],
                    source,
                    0,
                    time.time(),
                ),
            )
        elif source.endswith("selections.jsonl"):
            event_id = str(row.get("event_id") or row.get("id") or "")
            if not event_id:
                return
            conn.execute(
                """INSERT INTO events
                (event_id, event_type, series, project_id, instance_id, job_id,
                 flow_id, parent_event_id, status, at, payload_json, record_kind,
                 source_file, source_byte_offset, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    status = COALESCE(excluded.status, events.status),
                    payload_json = COALESCE(excluded.payload_json, events.payload_json),
                    ingested_at = excluded.ingested_at""",
                (
                    event_id,
                    str(row.get("event_type") or ""),
                    str(row.get("series") or ""),
                    str(row.get("project_id") or ""),
                    str(row.get("instance_id") or ""),
                    str(row.get("job_id") or row.get("task_id") or ""),
                    str(row.get("flow_id") or ""),
                    str(row.get("parent_event_id") or ""),
                    str(row.get("status") or ""),
                    _parse_ts(row.get("at") or row.get("created_at")),
                    json.dumps(_safe_dict(row.get("payload") or row), ensure_ascii=False, sort_keys=True),
                    str(row.get("record_kind") or "selection"),
                    source,
                    0,
                    time.time(),
                ),
            )

    def ingest_flow_state(self, flow_path: Path) -> None:
        self._ensure_schema()
        if not flow_path.exists():
            return
        try:
            data = json.loads(flow_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        flow_id = str(data.get("flow_id") or "")
        if not flow_id:
            return
        conn = get_connection(self.db_path)
        conn.execute(
            """INSERT INTO flows
            (flow_id, flow_type, definition_version, task_id, project_id, instance_id,
             status, current_node, next_check_at, created_at, updated_at, ingested_at,
             summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(flow_id) DO UPDATE SET
                flow_type = excluded.flow_type,
                definition_version = excluded.definition_version,
                task_id = excluded.task_id,
                project_id = excluded.project_id,
                instance_id = excluded.instance_id,
                status = excluded.status,
                current_node = excluded.current_node,
                next_check_at = excluded.next_check_at,
                updated_at = excluded.updated_at,
                ingested_at = excluded.ingested_at,
                summary_json = excluded.summary_json""",
            (
                flow_id,
                str(data.get("flow_type") or ""),
                str(data.get("definition_version") or ""),
                str(data.get("task_id") or ""),
                str(data.get("project_id") or ""),
                str(data.get("instance_id") or ""),
                str(data.get("status") or ""),
                str(data.get("current_event_id") or ""),
                float(data.get("next_check_at") or 0.0),
                _parse_ts(data.get("created_at")),
                _parse_ts(data.get("updated_at")),
                time.time(),
                json.dumps({k: v for k, v in data.items()
                            if k not in ("node_outputs", "node_summaries")},
                           ensure_ascii=False, sort_keys=True)[:60000],
            ),
        )

    # ----- queries -----
    def get_event(self, event_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        row = get_connection(self.db_path).execute(
            """SELECT event_id, event_type, series, project_id, instance_id, job_id,
            flow_id, parent_event_id, status, at, finished_at, summary, payload_json,
            record_kind, source_file, source_byte_offset, ingested_at
            FROM events WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def has_summary(self, event_id: str) -> bool:
        self._ensure_schema()
        row = get_connection(self.db_path).execute(
            "SELECT 1 FROM events WHERE event_id = ? AND summary IS NOT NULL",
            (event_id,),
        ).fetchone()
        return row is not None

    def recent_events(self, *, series: str = "", project_id: str = "",
                      event_type: str = "", instance_id: str = "",
                      job_id: str = "", flow_id: str = "",
                      status: str = "", include_audit: bool = False,
                      limit: int = 100, cursor: float | None = None) -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses = []
        args: list[Any] = []
        if series:
            clauses.append("series = ?"); args.append(series)
        if project_id:
            clauses.append("project_id = ?"); args.append(project_id)
        if event_type:
            clauses.append("event_type = ?"); args.append(event_type)
        if instance_id:
            clauses.append("instance_id = ?"); args.append(instance_id)
        if job_id:
            clauses.append("job_id = ?"); args.append(job_id)
        if flow_id:
            clauses.append("flow_id = ?"); args.append(flow_id)
        if status:
            clauses.append("status = ?"); args.append(status)
        if not include_audit:
            clauses.append("(payload_json IS NULL OR payload_json NOT LIKE '%acceptance_run%')")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        if cursor is not None:
            where += " AND at < ?"; args.append(float(cursor))
        sql = f"""SELECT event_id, event_type, series, project_id, instance_id, job_id,
        flow_id, parent_event_id, status, at, finished_at, summary, payload_json
        FROM events {where}
        ORDER BY at DESC LIMIT ?"""
        args.append(int(limit))
        rows = get_connection(self.db_path).execute(sql, args).fetchall()
        return [_row_to_dict(r) for r in rows]

    def batch_get_events(self, event_ids):
        """Return {event_id: row} for the requested exact event_ids."""
        if not event_ids:
            return {}
        ids = [str(e) for e in event_ids if e]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = get_connection(self.db_path).execute(
            f"""SELECT event_id, event_type, series, project_id, instance_id,
            job_id, flow_id, parent_event_id, status, at, finished_at,
            summary, payload_json
            FROM events WHERE event_id IN ({placeholders})""",
            ids,
        ).fetchall()
        out = {}
        for r in rows:
            d = _row_to_dict(r)
            out[d.get("event_id")] = d
        return out

    def list_flows(self, *, task_id: str = "", flow_type: str = "",
                   status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses = []
        args: list[Any] = []
        if task_id:
            clauses.append("task_id = ?"); args.append(task_id)
        if flow_type:
            clauses.append("flow_type = ?"); args.append(flow_type)
        if status:
            clauses.append("status = ?"); args.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""SELECT flow_id, flow_type, definition_version, task_id, project_id,
        instance_id, status, current_node, next_check_at, created_at, updated_at
        FROM flows {where}
        ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?"""
        args.append(int(limit))
        rows = get_connection(self.db_path).execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def history_by_job(self, job_id: str, *, limit: int = 200, after=None) -> list[dict[str, Any]]:
        """Return events for ``job_id``, newest first.

        ``after`` is an optional cursor: when given, only events whose ``at``
        timestamp is strictly greater than ``after`` are returned.  Callers can
        pass the last-seen ``at`` to fetch only events appended since then.
        """
        self._ensure_schema()
        if after is None:
            rows = get_connection(self.db_path).execute(
                """SELECT event_id, event_type, series, project_id, instance_id, job_id,
                flow_id, parent_event_id, status, at, finished_at, summary, payload_json
                FROM events WHERE job_id = ?
                ORDER BY at DESC LIMIT ?""",
                (job_id, int(limit)),
            ).fetchall()
        else:
            rows = get_connection(self.db_path).execute(
                """SELECT event_id, event_type, series, project_id, instance_id, job_id,
                flow_id, parent_event_id, status, at, finished_at, summary, payload_json
                FROM events WHERE job_id = ? AND at > ?
                ORDER BY at DESC LIMIT ?""",
                (job_id, after, int(limit)),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def waiting_actions(self, *, flow_id: str = "",
                        statuses: Iterable[str] = ("waiting", "scheduled", "queued"),
                        limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        statuses = list(statuses)
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        args: list[Any] = []
        if flow_id:
            sql = f"""SELECT event_id, event_type, job_id, flow_id, status, at
            FROM events WHERE flow_id = ? AND status IN ({placeholders})
            ORDER BY at DESC LIMIT ?"""
            args.append(flow_id)
        else:
            sql = f"""SELECT event_id, event_type, job_id, flow_id, status, at
            FROM events WHERE status IN ({placeholders})
            ORDER BY at DESC LIMIT ?"""
        args.extend(statuses)
        args.append(int(limit))
        rows = get_connection(self.db_path).execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def offset(self, source: str) -> dict[str, Any]:
        self._ensure_schema()
        row = get_connection(self.db_path).execute(
            "SELECT last_byte_offset, last_seq, ingested_at FROM jsonl_offsets WHERE file_id = ?",
            (source,),
        ).fetchone()
        return dict(row) if row else {"last_byte_offset": 0, "last_seq": 0, "ingested_at": None}

    def rebuild(self) -> dict[str, int]:
        self._ensure_schema()
        stats: dict[str, int] = {}
        for source in ("events.jsonl", "summaries.jsonl",
                       "work_items.jsonl", "selections.jsonl"):
            stats[source] = self.ingest_jsonl(source, force_full=True).get("ingested", 0)
        flow_count = 0
        if self.flow_dir.exists():
            for path in self.flow_dir.glob("flow_*.json"):
                self.ingest_flow_state(path)
                flow_count += 1
        stats["flows"] = flow_count
        return stats


def _parse_ts(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return None
    except Exception:
        return None


def _safe_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"value": value}


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    out = dict(row)
    payload = out.pop("payload_json", None)
    if payload:
        try:
            out["payload"] = json.loads(payload)
        except Exception:
            out["payload"] = None
    summary = out.pop("summary", None)
    if summary:
        try:
            out["summary"] = json.loads(summary)
        except Exception:
            pass
    return out
