"""SQLite connection lifecycle and concurrency primitives.

Goals:
- One logical connection per thread (sqlite connections are not safe to
  share across threads); we lazily create them and cache by thread id.
- Pragmas: WAL journal, busy_timeout, foreign keys ON, synchronous NORMAL.
- Refuse to open a database file on a 9p / drvfs mount.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .runtime_storage import _check_native

_thread_local = threading.local()


def open_connection(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults; refuse 9p paths."""
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _check_native(db_path.parent)
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,  # autocommit; we use explicit BEGIN
        timeout=30.0,
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Return a thread-local connection; create if missing."""
    key = str(db_path.resolve())
    cached = getattr(_thread_local, "connections", {}).get(key)
    if cached is not None:
        return cached
    conn = open_connection(db_path)
    setattr(_thread_local, "connections", {key: conn, **getattr(_thread_local, "connections", {})})
    return conn


def close_thread_connections() -> None:
    conns = getattr(_thread_local, "connections", {})
    for conn in conns.values():
        try:
            conn.close()
        except Exception:
            pass
    _thread_local.connections = {}


@staticmethod
def _to_py(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
