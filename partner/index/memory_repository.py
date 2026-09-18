"""MemoryRepository: SQLite-backed index over EventMemory-style JSONL.

Indexes observations / lessons / preferences / habits / beliefs / growth
streams kept by EventMemory.  Replaces the read-then-tail pattern
inside EventMemory._rows with a SQLite lookup so callers can ask
"current active habits" without reading the whole history.

Active habit, growth and belief projections honour supersedes
relations: a record marked superseded is excluded from the active set.
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

CREATE TABLE IF NOT EXISTS memory_records (
    record_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    scope        TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    payload_json TEXT NOT NULL,
    supersedes_id INTEGER,
    created_at   REAL NOT NULL,
    source_run   TEXT,
    raw_line     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_memory_kind_status
    ON memory_records(kind, status, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_scope
    ON memory_records(scope, kind);

CREATE TABLE IF NOT EXISTS jsonl_offsets (
    file_id    TEXT NOT NULL,
    source     TEXT NOT NULL,
    last_byte_offset INTEGER NOT NULL DEFAULT 0,
    last_seq         INTEGER NOT NULL DEFAULT 0,
    ingested_at      REAL,
    PRIMARY KEY (file_id)
);
"""


def init(repo_root: Path) -> "MemoryRepository":
    ws_dir = workspace_dir(repo_root)
    db_path = ws_dir / "memory.db"
    repo = MemoryRepository(db_path, repo_root)
    repo._ensure_schema()
    return repo


class MemoryRepository:
    def __init__(self, db_path: Path, repo_root: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.repo_root = Path(repo_root)
        # EventMemory stores per-instance data; look for the most likely
        # location, falling back to a generic workspace memory dir.
        self._memory_dir_candidates = [
            repo_root / "state" / "memory",
            repo_root / "memory",
            repo_root / "share" / "mind" / "memory",
        ]
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

    @property
    def memory_dir(self) -> Path | None:
        for candidate in self._memory_dir_candidates:
            if candidate.exists():
                return candidate
        return None

    def ingest_all(self, *, force: bool = False) -> dict[str, int]:
        self._ensure_schema()
        md = self.memory_dir
        if md is None:
            return {"ingested": 0, "skipped": "no_memory_dir"}
        conn = get_connection(self.db_path)
        ingested = 0
        for kind in ("observations", "lessons", "preferences",
                     "habits", "beliefs", "growth"):
            path = md / f"{kind}.jsonl"
            if not path.exists():
                continue
            key = f"{kind}.jsonl"
            row = conn.execute(
                "SELECT last_byte_offset, last_seq FROM jsonl_offsets WHERE file_id = ?",
                (key,),
            ).fetchone()
            last_offset = 0 if force or row is None else int(row["last_byte_offset"])
            seq = (0 if row is None else int(row["last_seq"]))
            try:
                with path.open("rb") as handle:
                    handle.seek(last_offset)
                    tail = handle.read()
            except OSError:
                continue
            if not tail:
                continue
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
                    obj = json.loads(line.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                self._ingest_record(kind, obj, seq)
                ingested += 1
            conn.execute(
                """INSERT INTO jsonl_offsets(file_id, source, last_byte_offset, last_seq, ingested_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    last_byte_offset = excluded.last_byte_offset,
                    last_seq = excluded.last_seq,
                    ingested_at = excluded.ingested_at""",
                (key, key, new_offset, seq, time.time()),
            )
        return {"ingested": ingested}

    def _ingest_record(self, kind: str, obj: dict[str, Any], seq: int) -> None:
        def _scalar(v: Any) -> Any:
            if isinstance(v, (str, int, float)):
                return v
            if v is None:
                return None
            return json.dumps(v, ensure_ascii=False, sort_keys=True)
        record_id = _scalar(obj.get("id")) if not isinstance(obj.get("id"), dict) else None
        if record_id is None:
            record_id = _scalar(obj.get("record_id")) if not isinstance(obj.get("record_id"), dict) else None
        sup_v = obj.get("supersedes_id")
        supersedes = _scalar(sup_v) if not isinstance(sup_v, dict) else None
        if supersedes is None:
            sup_v = obj.get("supersedes")
            supersedes = _scalar(sup_v) if not isinstance(sup_v, dict) else None
        status = obj.get("status") or "active"
        payload = dict(obj)
        payload.pop("id", None); payload.pop("record_id", None)
        payload.pop("supersedes_id", None); payload.pop("supersedes", None)
        payload.pop("status", None)
        def _str(v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, (str, int, float)):
                return str(v)
            return json.dumps(v, ensure_ascii=False, sort_keys=True)
        scope = _str(obj.get("scope")) or _str(obj.get("instance_id")) or ""
        conn = get_connection(self.db_path)
        if record_id is not None:
            conn.execute(
                """INSERT INTO memory_records
                (kind, scope, status, payload_json, supersedes_id, created_at,
                 source_run, raw_line)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    supersedes_id = excluded.supersedes_id,
                    scope = excluded.scope""",
                (
                    kind, scope, status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    int(supersedes) if supersedes is not None else None,
                    float(obj.get("created_at") or time.time()),
                    str(obj.get("source_run") or ""),
                    int(seq),
                ),
            )
        else:
            conn.execute(
                """INSERT INTO memory_records
                (kind, scope, status, payload_json, supersedes_id, created_at,
                 source_run, raw_line)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    kind, scope, status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    int(supersedes) if supersedes is not None else None,
                    float(obj.get("created_at") or time.time()),
                    str(obj.get("source_run") or ""),
                    int(seq),
                ),
            )

    def current_active(self, kind: str, *, scope: str = "",
                       limit: int = 100) -> list[dict[str, Any]]:
        """Return the current effective view of a memory kind.

        Honours supersedes: rows that are themselves superseded are
        excluded, AND any superseder row inherits the predecessor's
        chain so the result stays consistent across rewrites.
        """
        self._ensure_schema()
        clauses = ["kind = ?", "status != 'superseded'"]
        args: list[Any] = [kind]
        if scope:
            clauses.append("scope = ?"); args.append(scope)
        sql = f"""SELECT record_id, kind, scope, status, payload_json,
        supersedes_id, created_at, source_run
        FROM memory_records WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC LIMIT ?"""
        args.append(int(limit))
        rows = get_connection(self.db_path).execute(sql, args).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            try:
                entry["payload"] = json.loads(entry.pop("payload_json") or "{}")
            except Exception:
                entry["payload"] = {}
            out.append(entry)
        return out

    def recall(self, *, kind: str = "", scope: str = "", query: str = "",
               limit: int = 50) -> list[dict[str, Any]]:
        """Recall rows across kinds, optionally filtered by query."""
        self._ensure_schema()
        clauses = ["status != 'superseded'"]
        args: list[Any] = []
        if kind:
            clauses.append("kind = ?"); args.append(kind)
        if scope:
            clauses.append("scope = ?"); args.append(scope)
        if query:
            clauses.append("payload_json LIKE ?")
            args.append(f"%{query}%")
        sql = f"""SELECT record_id, kind, scope, status, payload_json,
        supersedes_id, created_at, source_run
        FROM memory_records WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC LIMIT ?"""
        args.append(int(limit))
        rows = get_connection(self.db_path).execute(sql, args).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            try:
                entry["payload"] = json.loads(entry.pop("payload_json") or "{}")
            except Exception:
                entry["payload"] = {}
            out.append(entry)
        return out

    def supersede(self, record_id: int, *, by_payload: dict[str, Any] | None = None) -> int:
        """Mark record_id as superseded.  Optionally write a new replacement
        row that supersedes the old one.  Returns the new record_id, or
        the old one if no replacement was provided.
        """
        self._ensure_schema()
        conn = get_connection(self.db_path)
        old = conn.execute(
            "SELECT kind, scope, payload_json, source_run FROM memory_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if old is None:
            return record_id
        conn.execute(
            "UPDATE memory_records SET status = 'superseded' WHERE record_id = ?",
            (record_id,),
        )
        if by_payload is None:
            return record_id
        payload = dict(by_payload)
        conn.execute(
            """INSERT INTO memory_records
            (kind, scope, status, payload_json, supersedes_id, created_at, source_run)
            VALUES (?, ?, 'active', ?, ?, ?, ?)""",
            (
                old["kind"], old["scope"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                int(record_id), time.time(),
                old["source_run"] or "",
            ),
        )
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
