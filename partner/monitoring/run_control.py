"""Persistent instance run controls shared by CLI and instance startup."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime


def control_path(workspace_root: str) -> str:
    return os.path.join(os.path.abspath(workspace_root), "state", "instance_control.json")


def _root_from_instance_workspace(workspace: str) -> str:
    normalized = os.path.abspath(workspace)
    marker = os.sep + "instances" + os.sep
    return normalized.split(marker, 1)[0] if marker in normalized else normalized


def load_control(workspace_root: str) -> dict:
    path = control_path(workspace_root)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"paused_instances": [], "updated_at": ""}


def set_paused(workspace_root: str, instance_ids: list[str], paused: bool) -> dict:
    root = os.path.abspath(workspace_root)
    data = load_control(root)
    current = {str(value) for value in data.get("paused_instances", [])}
    if paused:
        current.update(str(value) for value in instance_ids)
    else:
        current.difference_update(str(value) for value in instance_ids)
    data = {"paused_instances": sorted(current), "updated_at": datetime.now().isoformat(timespec="seconds")}
    path = control_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="instance_control_", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return data


def is_instance_paused(workspace: str, instance_id: str) -> bool:
    root = _root_from_instance_workspace(workspace)
    return str(instance_id) in {str(value) for value in load_control(root).get("paused_instances", [])}
