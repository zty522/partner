"""Runtime storage location resolver.

Production queue / state / index DBs must live on a non-9p filesystem
when the WSL2 mount is used (drvfs at /mnt/<drive>).  This module
resolves the path and refuses to write to a 9p mount when the policy
calls for native ext4.  The location is stable per workspace_id and
contains a format version + workspace fingerprint so two coexisting
workspaces cannot silently share one queue.

Workspace identity is derived from a deterministic hash of the
project root path.  The fingerprint is computed once at first use and
persisted in a workspace.json file under the runtime root.

Avoiding 9p hangs: we never rglob or list the project root here; the
fingerprint uses the resolved path only.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NATIVE_DEFAULT_ROOT = Path("/home/os/.local/share/partner/runtime")
FORMAT_VERSION = 1

DRVFS_HINTS = ("9p", "drvfs", "v9fs")


def _mount_table_hint(path: Path) -> str:
    try:
        with open("/proc/mounts", encoding="utf-8") as handle:
            rows = handle.readlines()
    except OSError:
        return ""
    best = ""
    longest = -1
    spath = str(path)
    for row in rows:
        parts = row.split()
        if len(parts) < 3:
            continue
        mount_point, fstype = parts[1], parts[2].lower()
        if spath == mount_point or spath.startswith(mount_point.rstrip("/") + "/"):
            if len(mount_point) > longest:
                best = fstype
                longest = len(mount_point)
    return best


def _check_native(path: Path) -> None:
    fstype = _mount_table_hint(path)
    if not fstype:
        raise RuntimeError(
            f"Cannot verify runtime storage filesystem at {path}; refusing to write."
        )
    if any(hint in fstype for hint in DRVFS_HINTS):
        raise RuntimeError(
            f"Refusing to use 9p/drvfs filesystem ({fstype}) at {path} for "
            "runtime storage. Move to a Linux-native filesystem "
            f"({NATIVE_DEFAULT_ROOT} is the default)."
        )


def _workspace_fingerprint(project_root: Path) -> str:
    """Stable fingerprint for a workspace.

    Uses resolved path only; never enumerates the project tree because
    9p mounts can hang on full-tree traversal.
    """
    h = hashlib.sha256()
    h.update(str(project_root).encode("utf-8"))
    return h.hexdigest()[:16]


def resolve_runtime_root(project_root: Path, *, override: str = "") -> Path:
    """Resolve and verify the runtime storage root for this workspace.

    `override` (env PARTNER_RUNTIME_ROOT or explicit argument) wins if set.
    Refuses to create the directory on a 9p mount.
    """
    env_override = os.environ.get("PARTNER_RUNTIME_ROOT", "").strip()
    raw = override or env_override or str(NATIVE_DEFAULT_ROOT)
    root = Path(raw).expanduser()
    if not root.exists():
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Cannot create runtime root {root}: {exc}") from exc
    _check_native(root)
    return root


def workspace_dir(project_root: Path) -> Path:
    """Resolve and persist the per-workspace runtime directory."""
    runtime_root = resolve_runtime_root(project_root)
    fp = _workspace_fingerprint(project_root)
    ws_dir = runtime_root / fp
    ws_dir.mkdir(parents=True, exist_ok=True)
    workspace_json = ws_dir / "workspace.json"
    if not workspace_json.exists():
        workspace_json.write_text(
            json.dumps(
                {
                    "format_version": FORMAT_VERSION,
                    "workspace_fingerprint": fp,
                    "project_root": str(project_root),
                    "created_at": _now(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return ws_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_workspace(ws_dir: Path) -> dict[str, Any]:
    path = ws_dir / "workspace.json"
    if not path.exists():
        raise FileNotFoundError(f"workspace.json missing under {ws_dir}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("format_version") or 0) != FORMAT_VERSION:
        raise RuntimeError(
            f"runtime storage format mismatch: {data.get('format_version')} vs {FORMAT_VERSION}"
        )
    return data
