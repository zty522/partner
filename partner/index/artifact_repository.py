"""ArtifactRepository: register and look up artifacts produced by
production Events.

Artifacts are recorded once they exist on disk.  This avoids the
recursive discovery pattern that previously re-scanned entire
project / state trees on every status query.
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

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id      TEXT PRIMARY KEY,
    producer_event   TEXT,
    job_id           TEXT,
    flow_id          TEXT,
    kind             TEXT,
    purpose          TEXT,
    path             TEXT NOT NULL,
    byte_size        INTEGER NOT NULL DEFAULT 0,
    sha256           TEXT,
    media_type       TEXT,
    state            TEXT NOT NULL DEFAULT 'present',
    created_at       REAL NOT NULL,
    source_uri       TEXT
);

CREATE INDEX IF NOT EXISTS idx_artifacts_job
    ON artifacts(job_id, kind);
CREATE INDEX IF NOT EXISTS idx_artifacts_flow
    ON artifacts(flow_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_kind
    ON artifacts(kind, created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_path
    ON artifacts(path);
"""


def init(repo_root: Path) -> "ArtifactRepository":
    ws_dir = workspace_dir(repo_root)
    db_path = ws_dir / "artifacts.db"
    repo = ArtifactRepository(db_path)
    repo._ensure_schema()
    return repo


class ArtifactRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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

    # ----- registration -----
    def register(self, *, path: str, kind: str = "", purpose: str = "",
                 job_id: str = "", flow_id: str = "",
                 producer_event: str = "", media_type: str = "",
                 source_uri: str = "") -> str:
        self._ensure_schema()
        artifact_id = f"artifact_{time.time_ns()}_{abs(hash(path)) % 10**8}"
        byte_size = 0
        sha = ""
        try:
            full = Path(path)
            if full.exists() and full.is_file():
                data = full.read_bytes()
                byte_size = len(data)
                import hashlib
                sha = hashlib.sha256(data).hexdigest()
        except OSError:
            pass
        now = time.time()
        conn = get_connection(self.db_path)
        conn.execute(
            """INSERT INTO artifacts
            (artifact_id, producer_event, job_id, flow_id, kind, purpose, path,
             byte_size, sha256, media_type, state, created_at, source_uri)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'present', ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                byte_size = excluded.byte_size,
                sha256 = excluded.sha256,
                state = excluded.state""",
            (artifact_id, producer_event, job_id, flow_id, kind, purpose,
             path, byte_size, sha, media_type, now, source_uri),
        )
        return artifact_id

    def mark_state(self, artifact_id: str, state: str) -> None:
        self._ensure_schema()
        get_connection(self.db_path).execute(
            "UPDATE artifacts SET state = ? WHERE artifact_id = ?",
            (state, artifact_id),
        )

    # ----- queries -----
    def by_job(self, job_id: str, *, kind: str = "",
               limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses = ["job_id = ?"]
        args: list[Any] = [job_id]
        if kind:
            clauses.append("kind = ?"); args.append(kind)
        sql = f"""SELECT artifact_id, producer_event, job_id, flow_id, kind, purpose,
        path, byte_size, sha256, media_type, state, created_at, source_uri
        FROM artifacts WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC LIMIT ?"""
        args.append(int(limit))
        rows = get_connection(self.db_path).execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def by_flow(self, flow_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        rows = get_connection(self.db_path).execute(
            """SELECT artifact_id, producer_event, job_id, flow_id, kind, purpose,
            path, byte_size, sha256, media_type, state, created_at, source_uri
            FROM artifacts WHERE flow_id = ?
            ORDER BY created_at DESC LIMIT ?""",
            (flow_id, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def by_kind(self, kind: str, *, limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        rows = get_connection(self.db_path).execute(
            """SELECT artifact_id, producer_event, job_id, flow_id, kind, purpose,
            path, byte_size, sha256, media_type, state, created_at, source_uri
            FROM artifacts WHERE kind = ?
            ORDER BY created_at DESC LIMIT ?""",
            (kind, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def by_path(self, path: str) -> dict[str, Any] | None:
        self._ensure_schema()
        row = get_connection(self.db_path).execute(
            """SELECT artifact_id, producer_event, job_id, flow_id, kind, purpose,
            path, byte_size, sha256, media_type, state, created_at, source_uri
            FROM artifacts WHERE path = ?""",
            (path,),
        ).fetchone()
        return dict(row) if row else None

    def by_hash(self, sha256: str) -> list[dict[str, Any]]:
        self._ensure_schema()
        rows = get_connection(self.db_path).execute(
            """SELECT artifact_id, producer_event, job_id, flow_id, kind, purpose,
            path, byte_size, sha256, media_type, state, created_at, source_uri
            FROM artifacts WHERE sha256 = ?""",
            (sha256,),
        ).fetchall()
        return [dict(r) for r in rows]

    def bounded_incremental_discover(self, root: Path, *, max_files: int = 200,
                                     max_bytes: int = 50_000_000,
                                     kind: str = "runtime_discovery") -> int:
        """Discover and register artifacts within a *bounded* directory.

        This is the only discovery entry point allowed for runtime use.
        It caps both file count and total bytes so a misplaced `root`
        cannot cascade into a full-tree scan.
        """
        self._ensure_schema()
        if not root.exists():
            return 0
        registered = 0
        bytes_seen = 0
        for path in sorted(root.rglob("*")):
            if registered >= max_files or bytes_seen >= max_bytes:
                break
            if not path.is_file():
                continue
            if any(p.startswith(".") for p in path.parts):
                continue
            size = path.stat().st_size
            if size > max_bytes - bytes_seen:
                continue
            bytes_seen += size
            self.register(path=str(path), kind=kind, purpose="discovered")
            registered += 1
        return registered
