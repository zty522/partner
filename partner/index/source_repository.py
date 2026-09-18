"""SourceRepository: external-source registry with version, hash,
and per-section/page extraction.

Replaces ad-hoc file fetches in active_learning / improvement handlers
with a single registry that:
  - records source_uri, version (paper version / commit SHA), download
    time, sha256
  - stores extraction metadata (page, section, paragraph offsets)
  - is reusable across Events: a previously registered source is
    *not* re-downloaded or re-parsed unless its sha256 has changed
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .runtime_storage import workspace_dir
from .sqlite_base import get_connection

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id    TEXT PRIMARY KEY,
    source_type  TEXT NOT NULL,
    source_uri   TEXT NOT NULL,
    version      TEXT,
    title        TEXT,
    authors      TEXT,
    license      TEXT,
    downloaded_at REAL,
    raw_path     TEXT,
    raw_sha256   TEXT,
    text_path    TEXT,
    text_sha256  TEXT,
    parsed_at    REAL,
    meta_json    TEXT,
    UNIQUE (source_uri, version)
);

CREATE TABLE IF NOT EXISTS source_extractions (
    source_id    TEXT NOT NULL,
    locator      TEXT NOT NULL,
    locator_type TEXT NOT NULL DEFAULT 'section',
    excerpt      TEXT NOT NULL,
    byte_offset  INTEGER,
    byte_length  INTEGER,
    sha256       TEXT,
    PRIMARY KEY (source_id, locator)
);

CREATE TABLE IF NOT EXISTS source_claims (
    claim_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT NOT NULL,
    claim         TEXT NOT NULL,
    evidence_locator TEXT,
    adopted       INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS source_adoptions (
    adoption_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT NOT NULL,
    claim_id      INTEGER,
    partner_capability TEXT,
    decision      TEXT,
    rationale     TEXT,
    experiment_id TEXT,
    created_at    REAL NOT NULL
);
"""


def init(repo_root: Path) -> "SourceRepository":
    ws_dir = workspace_dir(repo_root)
    db_path = ws_dir / "sources.db"
    repo = SourceRepository(db_path, repo_root)
    repo._ensure_schema()
    return repo


class SourceRepository:
    def __init__(self, db_path: Path, repo_root: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.repo_root = Path(repo_root)
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

    def register(self, *, source_uri: str, source_type: str,
                 version: str = "", title: str = "",
                 authors: str = "", license: str = "",
                 raw_path: str = "", text_path: str = "",
                 meta: dict[str, Any] | None = None) -> str:
        self._ensure_schema()
        now = time.time()
        raw_sha = ""
        if raw_path:
            try:
                raw_sha = hashlib.sha256(Path(raw_path).read_bytes()).hexdigest()
            except OSError:
                pass
        text_sha = ""
        if text_path:
            try:
                text_sha = hashlib.sha256(Path(text_path).read_bytes()).hexdigest()
            except OSError:
                pass
        conn = get_connection(self.db_path)
        source_id = f"source_{hashlib.sha256((source_uri + version).encode()).hexdigest()[:16]}"
        conn.execute(
            """INSERT INTO sources
            (source_id, source_type, source_uri, version, title, authors, license,
             downloaded_at, raw_path, raw_sha256, text_path, text_sha256,
             parsed_at, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_uri, version) DO UPDATE SET
                title = excluded.title,
                authors = excluded.authors,
                license = excluded.license,
                downloaded_at = excluded.downloaded_at,
                raw_path = excluded.raw_path,
                raw_sha256 = excluded.raw_sha256,
                text_path = excluded.text_path,
                text_sha256 = excluded.text_sha256,
                parsed_at = excluded.parsed_at,
                meta_json = excluded.meta_json""",
            (
                source_id, source_type, source_uri, version, title,
                authors, license, now, raw_path, raw_sha,
                text_path, text_sha, now,
                json.dumps(meta or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        return source_id

    def add_extraction(self, source_id: str, *, locator: str,
                       locator_type: str, excerpt: str,
                       byte_offset: int = 0, byte_length: int = 0) -> None:
        self._ensure_schema()
        sha = hashlib.sha256(excerpt.encode("utf-8", errors="replace")).hexdigest()
        conn = get_connection(self.db_path)
        conn.execute(
            """INSERT INTO source_extractions
            (source_id, locator, locator_type, excerpt, byte_offset, byte_length, sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, locator) DO UPDATE SET
                excerpt = excluded.excerpt,
                byte_offset = excluded.byte_offset,
                byte_length = excluded.byte_length,
                sha256 = excluded.sha256""",
            (source_id, locator, locator_type, excerpt, byte_offset, byte_length, sha),
        )

    def record_claim(self, source_id: str, claim: str,
                      evidence_locator: str = "") -> int:
        self._ensure_schema()
        conn = get_connection(self.db_path)
        cur = conn.execute(
            """INSERT INTO source_claims
            (source_id, claim, evidence_locator, created_at)
            VALUES (?, ?, ?, ?)""",
            (source_id, claim, evidence_locator, time.time()),
        )
        return int(cur.lastrowid)

    def adopt(self, *, source_id: str, claim_id: int | None,
              partner_capability: str, decision: str,
              rationale: str = "", experiment_id: str = "") -> int:
        self._ensure_schema()
        conn = get_connection(self.db_path)
        cur = conn.execute(
            """INSERT INTO source_adoptions
            (source_id, claim_id, partner_capability, decision, rationale,
             experiment_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source_id, claim_id, partner_capability, decision, rationale,
             experiment_id, time.time()),
        )
        return int(cur.lastrowid)

    # ----- queries -----
    def find(self, *, source_uri: str = "", version: str = "",
             source_type: str = "") -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses = []
        args: list[Any] = []
        if source_uri:
            clauses.append("source_uri = ?"); args.append(source_uri)
        if version:
            clauses.append("version = ?"); args.append(version)
        if source_type:
            clauses.append("source_type = ?"); args.append(source_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = get_connection(self.db_path).execute(
            f"""SELECT source_id, source_type, source_uri, version, title, authors,
            license, downloaded_at, raw_path, raw_sha256, text_path, text_sha256,
            parsed_at, meta_json
            FROM sources {where} ORDER BY downloaded_at DESC LIMIT 50""",
            args,
        ).fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            try:
                entry["meta"] = json.loads(entry.pop("meta_json") or "{}")
            except Exception:
                entry["meta"] = {}
            out.append(entry)
        return out

    def get_extractions(self, source_id: str) -> list[dict[str, Any]]:
        self._ensure_schema()
        rows = get_connection(self.db_path).execute(
            """SELECT locator, locator_type, excerpt, byte_offset, byte_length,
            sha256 FROM source_extractions WHERE source_id = ?""",
            (source_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def is_fresh(self, source_uri: str, *, version: str, sha256: str) -> bool:
        """Returns True if the source is registered with the same sha256."""
        self._ensure_schema()
        row = get_connection(self.db_path).execute(
            """SELECT raw_sha256, text_sha256 FROM sources
            WHERE source_uri = ? AND version = ?""",
            (source_uri, version),
        ).fetchone()
        if row is None:
            return False
        return (row["text_sha256"] or "") == sha256
