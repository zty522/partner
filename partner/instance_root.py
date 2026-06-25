"""Resolve the runtime root for Partner multi-instance data."""

from __future__ import annotations

import os
import subprocess
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
    4. \\\\wsl$\\<distro>\\e\\work\\partner_workspace when on Windows
    5. ~/partner_workspace when present
    6. ~/.partner fallback
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
    # On Windows, also probe WSL UNC paths
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["wsl.exe", "-l", "-q"], capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            for distro in result.stdout.strip().splitlines():
                distro = distro.strip()
                if distro:
                    candidates.insert(0, Path(f"\\\\wsl$\\{distro}\\e\\work\\partner_workspace"))
        except Exception:
            pass

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return Path.home() / ".partner"


def resolve_instances_dir() -> Path:
    return resolve_partner_root() / "instances"


def resolve_global_config_path() -> Path:
    return resolve_partner_root() / "config" / "global_config.json"


def resolve_instance_workspace(instance_id: str) -> Path:
    return resolve_instances_dir() / instance_id
