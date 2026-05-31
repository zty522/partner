"""Resolve the runtime root for Partner multi-instance data."""

from __future__ import annotations

import os
from pathlib import Path


def _read_pointer(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def resolve_partner_root() -> Path:
    """Return the base directory that stores instances and manager state.

    Priority:
    1. PARTNER_HOME env var
    2. ~/.partner_workspace pointer target
    3. /mnt/e/work/partner_workspace when present
    4. ~/partner_workspace when present
    5. ~/.partner fallback
    """
    env_home = os.environ.get("PARTNER_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()

    pointer = Path.home() / ".partner_workspace"
    if pointer.is_file():
        pointed = _read_pointer(pointer)
        if pointed:
            return Path(pointed).expanduser()

    candidates = [
        Path("/mnt/e/work/partner_workspace"),
        Path.home() / "partner_workspace",
        Path.home() / ".partner_workspace",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return Path.home() / ".partner"


def resolve_instances_dir() -> Path:
    return resolve_partner_root() / "instances"


def resolve_global_config_path() -> Path:
    return resolve_partner_root() / "global_config.json"


def resolve_instance_workspace(instance_id: str) -> Path:
    return resolve_instances_dir() / instance_id
