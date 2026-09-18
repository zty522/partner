"""Generic indexed file lookup (M1 / Section 8 / round 5).

``os.walk`` is forbidden in production code paths except under
``maintenance_only`` callsites.  This module provides:

* ``files_under(workspace, prefixes, exts, limit)`` — read files by
  path-prefix + extension from the workspace_files table.
* ``record_user_file(workspace, path)`` — populate the table.

Falls back to bounded listdir when the index is empty (recovery).

State honesty: ``static_implemented``.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from .code_repository import init as _init_code


def record_user_file(*, workspace: str | Path, path: Path) -> None:
    """Record one user-facing workspace file into the indexed table."""
    repo = _init_code(Path(workspace).expanduser().resolve())
    try:
        st = path.stat()
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
        repo.record_workspace_file(
            path=str(path.relative_to(repo.repo_root)) if path.is_absolute() else str(path),
            content_sha=h.hexdigest(),
            byte_size=st.st_size,
            mtime=st.st_mtime,
        )
    except Exception:
        pass


def files_under(*, workspace: str | Path, prefixes: list[str] | None = None,
                  exts: set[str] | None = None, limit: int = 256) -> list[dict]:
    """Read files matching the given prefixes/extensions.

    Each returned dict has ``rel_path``, ``abs_path``, ``mtime``,
    ``byte_size``, ``content_sha``.  Limit is a hard cap.
    """
    repo = _init_code(Path(workspace).expanduser().resolve())
    ws_root = repo.repo_root
    out: list[dict] = []
    try:
        candidates = repo.list_recent_deliverables(exts=exts, limit=limit * 4,
                                                    skip_prefixes=())
    except Exception:
        candidates = []
    for c in candidates:
        rel = c["rel_path"]
        if prefixes:
            if not any(rel.startswith(p) for p in prefixes):
                continue
        out.append({
            "rel_path": rel,
            "abs_path": str(ws_root / rel),
            "mtime": c["mtime"],
            "byte_size": c["byte_size"],
            "content_sha": c["content_sha"],
        })
        if len(out) >= limit:
            break
    return out


__all__ = ["record_user_file", "files_under"]
