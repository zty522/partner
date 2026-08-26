"""Persist the user-facing reply channel for the currently handled message.

The Partner process has one bounded foreground task at a time.  Keeping this
small context on disk lets the runtime distinguish a local desktop/TUI task
from a QQ task after the USER_MESSAGE event has been converted into a later
BATCH_PLAN event.  A history write is accepted as delivery only for an
explicit local UI channel; QQ tasks still require the QQ bridge callback.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOCAL_SOURCES = {"desktop_gui", "tui", "tui_user", "local", "local_canary"}


def _path(workspace: str) -> Path:
    return Path(workspace) / "state" / "active_user_context.json"


def record_active_user_context(
    workspace: str,
    *,
    source: str,
    sender_id: str,
    sender_name: str = "",
    message_id: str = "",
) -> dict[str, Any]:
    normalized_source = str(source or "").strip().lower()
    normalized_sender = str(sender_id or "").strip()
    local = normalized_source in LOCAL_SOURCES or normalized_sender in LOCAL_SOURCES
    data = {
        "schema_version": 1,
        "channel": "local" if local else "qq",
        "source": normalized_source,
        "sender_id": normalized_sender,
        "sender_name": str(sender_name or ""),
        "message_id": str(message_id or ""),
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    path = _path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return data


def load_active_user_context(workspace: str) -> dict[str, Any]:
    try:
        value = json.loads(_path(workspace).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def local_delivery_target(workspace: str) -> str:
    value = load_active_user_context(workspace)
    if value.get("channel") != "local":
        return ""
    return str(value.get("sender_id") or value.get("source") or "desktop_gui")
