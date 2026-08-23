"""Persistent five-instance/two-slot scheduling policy."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from partner.monitoring.run_control import load_control, set_paused

from .models import now_iso
from .storage import atomic_json, workspace_root


ALL_INSTANCES = ("01", "02", "03", "04", "05")
MAX_ACTIVE = 2
ROLES = {
    "01": "xiaohongshu_operations",
    "02": "molecular_generation",
    "03": "partner_framework_frontend",
    "04": "literature_github_learning",
    "05": "agent_self_evolution",
}


def scheduler_path(workspace_root: str) -> Path:
    return workspace_root_path(workspace_root) / "state" / "instance_scheduler.json"


def workspace_root_path(value: str) -> Path:
    return workspace_root(value)


def load_scheduler(workspace_root: str) -> dict[str, Any]:
    path = scheduler_path(workspace_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "max_active": MAX_ACTIVE, "active_slots": ["01", "02"],
            "roles": ROLES, "updated_at": ""}


def set_active_slots(workspace_root: str, instance_ids: list[str], *, reason: str = "") -> dict[str, Any]:
    workspace_root = str(workspace_root_path(workspace_root))
    normalized = list(dict.fromkeys(str(value) for value in instance_ids))
    unknown = sorted(set(normalized) - set(ALL_INSTANCES))
    if unknown:
        raise ValueError(f"unknown instances: {unknown}")
    if len(normalized) > MAX_ACTIVE:
        raise ValueError(f"at most {MAX_ACTIVE} instances may be active")
    previous = load_scheduler(workspace_root)
    previous_active = list(previous.get("active_slots") or [])
    paused = [value for value in ALL_INSTANCES if value not in normalized]
    set_paused(workspace_root, paused, True)
    set_paused(workspace_root, normalized, False)
    data = {
        "version": 1,
        "max_active": MAX_ACTIVE,
        "active_slots": normalized,
        "paused_instances": paused,
        "roles": ROLES,
        "reason": str(reason),
        "previous_active_slots": previous_active,
        "updated_at": now_iso(),
    }
    atomic_json(scheduler_path(workspace_root), data)
    return data


def assert_start_allowed(workspace_root: str, instance_id: str) -> None:
    workspace_root = str(workspace_root_path(workspace_root))
    state = load_scheduler(workspace_root)
    if str(instance_id) not in set(state.get("active_slots") or []):
        raise RuntimeError(f"instance {instance_id} is not assigned to an active slot")
    if str(instance_id) in set(load_control(workspace_root).get("paused_instances") or []):
        raise RuntimeError(f"instance {instance_id} is persistently paused")
