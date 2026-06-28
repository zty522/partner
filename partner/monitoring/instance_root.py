"""Resolve the runtime root for Partner multi-instance data."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _detect_platform() -> str:
    """Detect platform: 'windows', 'wsl', or 'linux'."""
    if os.name == "nt":
        return "windows"
    try:
        with open("/proc/version") as f:
            content = f.read().lower()
            if "microsoft" in content or "wsl" in content:
                return "wsl"
    except OSError:
        pass
    return "linux"


def _default_workspace_candidates() -> list[Path]:
    """Return platform-appropriate default workspace candidates.

    For Windows: C:\\Users\\<username>\\partner_workspace
    For WSL:     /mnt/e/work/partner_workspace (if exists), else ~/partner_workspace
    For Linux:   ~/partner_workspace
    """
    platform = _detect_platform()
    home = Path.home()

    if platform == "windows":
        return [
            home / "partner_workspace",
            home / ".partner_workspace",
        ]
    elif platform == "wsl":
        # On WSL, user may have workspace on NTFS mount
        candidates = []
        e_ws = Path("/mnt/e/work/partner_workspace")
        if e_ws.exists():
            candidates.append(e_ws)
        candidates.append(home / "partner_workspace")
        candidates.append(home / ".partner_workspace")
        return candidates
    else:
        return [
            home / "partner_workspace",
            home / ".partner_workspace",
        ]


def _wsl_to_windows(path: str) -> str:
    """Convert /mnt/e/work -> E:\\work (for cross-platform pointer resolution)."""
    if not path:
        return path
    clean = path.replace("/", "\\").strip("\\")
    if clean.startswith("mnt\\"):
        drive = clean[4]
        rest = clean[5:]  # Include the backslash after drive letter
        return f"{drive.upper()}:{rest}"
    return path


def _read_pointer(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _normalize_pointer_path(pointed: str) -> str:
    """Convert WSL-style paths to Windows paths when running on Windows."""
    if os.name == "nt" and pointed.startswith("/mnt/"):
        return _wsl_to_windows(pointed)
    return pointed


def resolve_partner_root() -> Path:
    """Return the base directory that stores instances and manager state.

    Priority:
    1. PARTNER_HOME env var
    2. ~/.partner_workspace pointer target
    3. Platform-specific defaults (see _default_workspace_candidates)
    4. ~/.partner fallback
    """
    env_home = os.environ.get("PARTNER_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()

    pointer = Path.home() / ".partner_workspace"
    if pointer.is_file():
        pointed = _read_pointer(pointer)
        if pointed:
            return Path(_normalize_pointer_path(pointed)).expanduser()

    candidates = _default_workspace_candidates()

    # On Windows, also probe WSL UNC paths (for WSL2 users with Linux partition)
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
