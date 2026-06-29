"""Local partner config — persists workspace/instance preferences at the user level.

File location: %LOCALAPPDATA%/Partner/partner_local.json
This is written when the user changes settings and read on startup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


LOCAL_CONFIG_FILENAME = "partner_local.json"


def _local_config_dir() -> str:
    """Return %LOCALAPPDATA%/Partner (Windows) or ~/.config/Partner (Linux)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, "Partner")


def local_config_path() -> str:
    """Return the full path to the local config file."""
    return os.path.join(_local_config_dir(), LOCAL_CONFIG_FILENAME)


DEFAULT_CONFIG = {
    "default_workspace_path": str(Path.home() / "partner_workspace"),
    "default_instance_id": "default",
    "last_workspace_path": "",
    "last_instance_id": "",
    "version": "1",
}


def load_local_config() -> dict:
    """Load the local config, returning defaults if file doesn't exist."""
    path = local_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT_CONFIG)
        # Ensure all default keys exist
        result = dict(DEFAULT_CONFIG)
        result.update(data)
        return result
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)


def save_local_config(data: dict) -> None:
    """Save the local config to disk."""
    path = local_config_path()
    config_dir = os.path.dirname(path)
    os.makedirs(config_dir, exist_ok=True)
    # Merge with defaults to preserve any missing keys
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # Fail silently if we can't write
