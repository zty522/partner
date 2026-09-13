"""Persistent five-project, resource-adaptive scheduling policy.

The runtime used to be hard-capped at two concurrent active instances.  That
was a conservative scaffold from the early two-slot campaign era.  ADR 0062
replaces it with a three-layer resource budget:

1. Operator override (``partner_config.json::runtime.instance_native_max_active``)
2. Host-resource estimation (``/proc/meminfo`` + ``/proc/cpuinfo`` + ``/proc/loadavg``)
3. Fail-closed single-lane floor (only when host metrics are unavailable)

Five projects may rotate.  The operator value is a safety *ceiling*, not a
fixed slot count; the live host estimate may reduce it at every watchdog
sweep.  This keeps the scheduler responsive to memory/load pressure without
turning a historical two-slot assumption into architecture.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from partner.monitoring.run_control import load_control, set_paused

from .models import now_iso
from .storage import atomic_json, workspace_root


ALL_INSTANCES = ("01", "02", "03", "04", "05")

# 2 has been lifted from a hard ceiling to an emergency floor: it only
# applies when neither the operator override nor /proc can be read.
MAX_ACTIVE_FLOOR = 1
MAX_ACTIVE_CEILING = len(ALL_INSTANCES)
RESERVED_MEMORY_MB = 2048.0
MEMORY_PER_INSTANCE_MB = 1536.0
RESERVED_CPU_CORES = 2.0
CPU_CORES_PER_INSTANCE = 2.0


def _read_host_resources() -> dict[str, float]:
    """Inspect /proc/{meminfo,cpuinfo,loadavg}.  Each metric absent → 0.

    Always returns a dict so callers can do an explicit floor on each.
    """
    out: dict[str, float] = {
        "cpu_count": 0.0, "mem_total_mb": 0.0,
        "mem_available_mb": 0.0, "loadavg_5m": 0.0,
    }
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            out["cpu_count"] = float(
                sum(1 for line in handle if line.startswith("processor"))
            )
    except OSError:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    out["mem_total_mb"] = float(line.split()[1]) / 1024.0
                elif line.startswith("MemAvailable:"):
                    out["mem_available_mb"] = float(line.split()[1]) / 1024.0
    except OSError:
        pass
    try:
        with open("/proc/loadavg", encoding="utf-8") as handle:
            parts = handle.read().split()
        if len(parts) >= 2:
            out["loadavg_5m"] = float(parts[1])
    except OSError:
        pass
    return out


def _estimated_max_active(host: dict[str, float]) -> int:
    """Allocate slots from free resources per ADR 0062 §1.

    Heuristic: reserve 2 logical cores and 2 GiB for the host, then budget
    2 logical cores and 1.5 GiB of currently available memory per lane.
    """
    cores = float(host.get("cpu_count") or 0.0)
    mem_mb = float(host.get("mem_available_mb") or host.get("mem_mb") or 0.0)
    load = host["loadavg_5m"]

    # Subtract current sustained load before allocating new workers.  One
    # Partner lane is budgeted as two logical cores because PDF, browser and
    # local analysis steps can briefly become CPU-heavy.
    cores_left = max(0.0, cores - max(load, 0.0) - RESERVED_CPU_CORES)
    by_cpu = int(cores_left / CPU_CORES_PER_INSTANCE)
    # MemAvailable (not MemTotal) is the actionable pressure signal.  Preserve
    # 2 GiB for the desktop/WSL host, then budget 1.5 GiB per lane.
    by_mem = int(max(0.0, mem_mb - RESERVED_MEMORY_MB) /
                 MEMORY_PER_INSTANCE_MB) if mem_mb > 0 else MAX_ACTIVE_FLOOR
    estimate = min(by_cpu, by_mem) if by_cpu > 0 else by_mem
    return max(MAX_ACTIVE_FLOOR, estimate)


def _operator_override(workspace_root: str) -> int | None:
    try:
        from partner.state.config import load_partner_config_data
        runtime = ((load_partner_config_data(workspace_root) or {})
                   .get("runtime") or {})
        raw = runtime.get("instance_native_max_active")
    except Exception:
        return None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def effective_max_active(workspace_root: str, *, host: dict[str, float] | None = None) -> int:
    """Maximum concurrent active slots for the durable scheduler.

    Three-layer fallback per ADR 0062:
    1. ``partner_config.json::runtime.instance_native_max_active`` if positive.
    2. ``/proc``-based estimate bounded by available CPU and memory.
    3. ``MAX_ACTIVE_FLOOR`` (only if all reads fail; e.g. macOS / sealed sandbox).
    """
    explicit = _operator_override(workspace_root)
    operator_ceiling = (max(1, min(MAX_ACTIVE_CEILING, explicit))
                        if explicit is not None and explicit > 0
                        else MAX_ACTIVE_CEILING)
    host = host if host is not None else _read_host_resources()
    if (float(host.get("cpu_count") or 0) <= 0
            and float(host.get("mem_available_mb") or host.get("mem_mb") or 0) <= 0):
        return min(MAX_ACTIVE_FLOOR, operator_ceiling)
    estimate = _estimated_max_active(host)
    return max(1, min(operator_ceiling, estimate))


def resource_capacity_snapshot(workspace_root: str) -> dict[str, Any]:
    """Return the auditable live inputs and resulting slot decision."""
    host = _read_host_resources()
    explicit = _operator_override(workspace_root)
    return {
        "host": host,
        "operator_ceiling": explicit,
        "effective_max_active": effective_max_active(workspace_root, host=host),
        "policy": "available_memory_and_5m_load_v2",
        "measured_at": now_iso(),
    }


# Backward-compatible name.  ADR 0062 trims the legacy API but keeps this
# symbol pointing at the active calculation, never a constant.
MAX_ACTIVE = effective_max_active

ROLES = {
    "01": "xiaohongshu_operations",
    "02": "molecular_generation",
    "03": "molecular_dynamics_study",
    "04": "literature_github_learning",
    "05": "hermes_partner_explore",
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
    effective = effective_max_active(workspace_root)
    if len(normalized) > effective:
        raise ValueError(f"at most {effective} instances may be active "
                         f"(runtime.instance_native_max_active={effective})")
    previous = load_scheduler(workspace_root)
    previous_active = list(previous.get("active_slots") or [])
    # Slot admission and operator pause are orthogonal.  The old implementation
    # marked every temporarily unselected lane as persistently paused, so the
    # next resource sweep could never rotate it back in and could even replace
    # an explicitly selected single-lane canary with the other four lanes.
    paused = list(load_control(workspace_root).get("paused_instances") or [])
    capacity = resource_capacity_snapshot(workspace_root)
    data = {
        "version": 1,
        "max_active": effective,
        "active_slots": normalized,
        "paused_instances": sorted(str(value) for value in paused),
        "roles": ROLES,
        "resource_capacity": capacity,
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
