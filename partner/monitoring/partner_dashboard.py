"""Deterministic single-page health dashboard for Partner.

Collects evidence from systemd (service state), heartbeat.json, ProjectState,
latest IterationReceipt, pytest results and QQ delivery receipts, then exposes
a single ``snapshot(workspace) -> dict`` API that CLI scripts and future
dashboards can render as plain text or JSON.

Design constraints (per docs/handoff/verification_rules.md):

* Reading files is not claiming success. The snapshot only reports what is
  present on disk at call time. If a file is missing the corresponding field
  is empty / "unknown" rather than guessed.
* No LLM calls. The dashboard is purely deterministic so it is safe to run
  from any cost-constrained environment.
* The snapshot never modifies workspace files.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any

JsonDict = dict[str, Any]

INSTANCE_IDS = ("01", "02", "03", "04", "05")
DEFAULT_WORKSPACE = "/mnt/e/work/partner_workspace"
DEFAULT_CODE_ROOT = "/mnt/e/work/partner"
DEFAULT_PYTEST_RESULTS = os.path.join(
    DEFAULT_CODE_ROOT, "tests", ".pytest_cache", "last_failed"
)
DEFAULT_PYTEST_SUMMARY_PATH = os.path.join(
    DEFAULT_CODE_ROOT, "docs", "testing", "last_pytest.txt"
)


def _read_json(path: str) -> JsonDict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_text(path: str, limit: int = 2000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()[-limit:]
    except OSError:
        return ""


def _parse_iso(value: str):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(stamp: str):
    parsed = _parse_iso(stamp)
    if not parsed:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed
    return max(0, int(delta.total_seconds()))


def _service_state(instance_id: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", f"partner-{instance_id}.service"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    state = (result.stdout or result.stderr or "").strip()
    return state or "unknown"


def _instance_heartbeat(instance_workspace: str) -> JsonDict:
    """Read heartbeat.json + runtime lock; return status, age, cycle count."""
    state_dir = os.path.join(instance_workspace, "state")
    heartbeat = _read_json(os.path.join(state_dir, "heartbeat.json"))
    runtime_lock = _read_json(os.path.join(state_dir, "instance_runtime.lock"))
    age = _age_seconds(heartbeat.get("last_heartbeat", ""))
    return {
        "last_heartbeat": heartbeat.get("last_heartbeat", ""),
        "age_seconds": age if age is not None else -1,
        "cycle_count": int(heartbeat.get("cycle_count", 0) or 0),
        "crash_count": int(heartbeat.get("crash_count", 0) or 0),
        "current_task_id": heartbeat.get("current_task_id", ""),
        "status_text": heartbeat.get("status", ""),
        "pid": int(runtime_lock.get("pid", 0) or 0),
        "started_at": runtime_lock.get("started_at", ""),
    }


def _delivery_state(instance_workspace: str) -> JsonDict:
    state = _read_json(os.path.join(instance_workspace, "state", "qq_delivery_state.json"))
    return {
        "delivery_ready": bool(state.get("delivery_ready", False)),
        "delivery_status": str(state.get("status") or "unknown"),
        "delivery_error_type": str(state.get("error_type") or ""),
        "delivery_updated_at": str(state.get("updated_at") or ""),
    }


def _latest_receipt_summary(workspace_root: str, project_id: str) -> JsonDict:
    """Read the latest IterationReceipt for a governance project."""
    action_history = os.path.join(
        workspace_root, "share", "projects", project_id, "governance", "action_history.jsonl",
    )
    effective_actions = {}
    try:
        for line in _read_text(action_history, limit=200_000).splitlines():
            row = json.loads(line)
            if isinstance(row, dict) and row.get("action_id"):
                effective_actions[str(row["action_id"])] = row
    except (ValueError, TypeError):
        effective_actions = {}

    def summarize_action(action):
        current = effective_actions.get(str(action.get("action_id") or ""), action)
        return {
            "title": current.get("title", action.get("title", "")),
            "event_type": current.get("event_type", action.get("event_type", "")),
            "status": current.get("status", action.get("status", "")),
            "task_id": current.get("task_id", action.get("task_id", "")),
        }
    try:
        from partner.governance.storage import latest_receipt

        receipt = latest_receipt(workspace_root, project_id)
        if receipt is not None:
            data = receipt.to_dict()
        else:
            data = {}
    except Exception:
        data = {}
    if data:
        actions = data.get("next_actions") or []
        return {
            "iteration": int(data.get("iteration", 0) or 0),
            "goal": data.get("goal", ""),
            "actions_executed": data.get("actions_executed", []),
            "artifacts": data.get("artifacts", []),
            "findings": data.get("findings", []),
            "stop_reason": data.get("stop_reason", ""),
            "delivery_confirmed": bool(data.get("delivery_confirmed", False)),
            "next_actions": [summarize_action(action) for action in actions if isinstance(action, dict)],
            "created_at": data.get("created_at", ""),
        }
    gov_dir = os.path.join(
        workspace_root, "share", "projects", project_id, "governance"
    )
    receipts_dir = os.path.join(gov_dir, "receipts")
    paths = []
    if os.path.isdir(receipts_dir):
        paths = sorted(
            os.path.join(receipts_dir, name)
            for name in os.listdir(receipts_dir)
            if name.endswith(".json")
        )
    if not paths:
        return {}
    try:
        with open(paths[-1], "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    actions = data.get("next_actions") or []
    next_action_summary = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        next_action_summary.append(summarize_action(action))
    return {
        "iteration": int(data.get("iteration", 0) or 0),
        "goal": data.get("goal", ""),
        "actions_executed": data.get("actions_executed", []),
        "artifacts": data.get("artifacts", []),
        "findings": data.get("findings", []),
        "stop_reason": data.get("stop_reason", ""),
        "delivery_confirmed": bool(data.get("delivery_confirmed", False)),
        "next_actions": next_action_summary,
        "created_at": data.get("created_at", ""),
    }


def _project_state_summary(workspace_root: str, project_id: str) -> JsonDict:
    gov_path = os.path.join(
        workspace_root, "share", "projects", project_id, "governance",
        "project_state.json",
    )
    return _read_json(gov_path)


def _pytest_summary(path: str = DEFAULT_PYTEST_SUMMARY_PATH) -> JsonDict:
    """Read the persisted pytest summary written by CI / pre-deploy.

    Returns ``{source, passed, failed, collected, raw_excerpt}``. If no summary
    file exists, attempts to read the last-failed pytest cache. Missing source
    yields ``{"source": "", "passed": 0, "failed": 0, "collected": 0}``.
    """
    raw = _read_text(path, limit=4000)
    if not raw:
        cache = _read_json(DEFAULT_PYTEST_RESULTS)
        if cache:
            return {"source": "pytest_cache:last_failed", "passed": 0, "failed": 0,
                    "collected": 0, "last_failed": cache.get("last_failed") or []}
        return {"source": "", "passed": 0, "failed": 0, "collected": 0}

    def _search(pattern):
        match = re.search(pattern, raw)
        return int(match.group(1)) if match else 0

    return {
        "source": path,
        "passed": _search(r"(\d+)\s+passed"),
        "failed": _search(r"(\d+)\s+failed"),
        "collected": _search(r"collected\s+(\d+)\s+items"),
        "raw_excerpt": raw[-1200:],
    }


def _format_age(seconds):
    if seconds is None or seconds < 0:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _project_id_for(instance_id: str) -> str:
    """Return the canonical governance project_id for an instance.

    Reads the authoritative role map from ``partner.governance.scheduler`` so
    that adding a new role there automatically surfaces in the dashboard.
    Falls back to empty string when the scheduler module is unavailable or
    the instance is not registered (e.g. unit tests with stripped imports).
    """
    try:
        from partner.governance.scheduler import ROLES as _ROLES
    except Exception:
        return ""
    role = _ROLES.get(str(instance_id), "")
    return str(role or "")


def _instance_row(instance_id, *, workspace_root, service_state, heartbeat, delivery_state, project_id):
    project_state = _project_state_summary(workspace_root, project_id)
    receipt_summary = _latest_receipt_summary(workspace_root, project_id)
    age = heartbeat.get("age_seconds", -1)
    healthy = (
        service_state == "active"
        and isinstance(age, int) and age >= 0 and age < 600
        and heartbeat.get("crash_count", 0) == 0
    )
    return {
        "instance_id": instance_id,
        "service": service_state,
        "healthy": bool(healthy),
        "user_ready": bool(healthy and delivery_state.get("delivery_ready")),
        **delivery_state,
        "age": _format_age(age) if isinstance(age, int) and age >= 0 else "unknown",
        "cycles": heartbeat.get("cycle_count", 0),
        "crashes": heartbeat.get("crash_count", 0),
        "pid": heartbeat.get("pid", 0),
        "project_id": project_id,
        "project_status": project_state.get("status", ""),
        "project_goal": project_state.get("goal", ""),
        "current_iteration": int(project_state.get("current_iteration", 0) or 0),
        "blocked_reason": project_state.get("blocked_reason", ""),
        "resume_event": project_state.get("resume_event", ""),
        "latest_receipt": receipt_summary,
    }


def _campaign_summary(workspace_root: str) -> JsonDict:
    try:
        from partner.governance.campaign import campaign_snapshot
        from partner.governance.campaign_storage import active_campaign_id

        campaign_id = active_campaign_id(workspace_root)
        data = campaign_snapshot(workspace_root, campaign_id) if campaign_id else {}
        campaign = data.get("campaign") or {}
        counts: dict[str, int] = {}
        for item in data.get("work_items") or []:
            status = str(item.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return {
            "campaign_id": campaign_id,
            "status": campaign.get("status", "missing" if campaign_id else "none"),
            "goal": campaign.get("goal", ""),
            "deadline_at": campaign.get("deadline_at", ""),
            "active_instances": campaign.get("active_instances", []),
            "stop_reason": campaign.get("stop_reason", ""),
            "work_items": counts,
            "usage": campaign.get("usage", {}),
        }
    except Exception:
        return {"campaign_id": "", "status": "unknown", "work_items": {}}


def snapshot(
    *,
    workspace_root: str = DEFAULT_WORKSPACE,
    code_root: str = DEFAULT_CODE_ROOT,
    pytest_summary_path: str = DEFAULT_PYTEST_SUMMARY_PATH,
    include_inactive: bool = True,
) -> JsonDict:
    """Collect the full dashboard snapshot as a dict.

    The function is deterministic and side-effect-free. CLI scripts render
    this dict either as JSON or as a fixed-column text table.
    """
    instances = []
    for inst_id in INSTANCE_IDS:
        service = _service_state(inst_id)
        heartbeat = _instance_heartbeat(
            os.path.join(workspace_root, "instances", inst_id)
        )
        delivery_state = _delivery_state(
            os.path.join(workspace_root, "instances", inst_id)
        )
        project_id = _project_id_for(inst_id)
        row = _instance_row(
            inst_id,
            workspace_root=workspace_root,
            service_state=service,
            heartbeat=heartbeat,
            delivery_state=delivery_state,
            project_id=project_id,
        )
        if not include_inactive and service != "active":
            continue
        instances.append(row)

    try:
        from partner.state.config import runtime_mode

        mode = runtime_mode(workspace_root)
    except Exception:
        mode = "manual_stable"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace_root": workspace_root,
        "code_root": code_root,
        "active_count": sum(1 for r in instances if r["service"] == "active"),
        "healthy_count": sum(1 for r in instances if r["healthy"]),
        "user_ready_count": sum(1 for r in instances if r["user_ready"]),
        "max_active": 2,
        "runtime_mode": mode,
        "instances": instances,
        "pytest": _pytest_summary(pytest_summary_path),
        "campaign": _campaign_summary(workspace_root),
    }


def render_text(snapshot_data):
    """Render snapshot as a fixed-column plain text panel suitable for terminals."""
    lines = []
    lines.append(f"Partner Dashboard @ {snapshot_data['generated_at']}")
    lines.append(
        f"workspace={snapshot_data['workspace_root']}  mode={snapshot_data.get('runtime_mode', 'manual_stable')}  active="
        f"{snapshot_data['active_count']}/{snapshot_data['max_active']}  "
        f"healthy={snapshot_data['healthy_count']}/{len(INSTANCE_IDS)}  "
        f"user-ready={snapshot_data.get('user_ready_count', 0)}/{len(INSTANCE_IDS)}"
    )
    pytest_data = snapshot_data.get("pytest") or {}
    if pytest_data.get("passed") or pytest_data.get("failed"):
        lines.append(
            "pytest: passed="
            f"{pytest_data.get('passed', 0)}  failed={pytest_data.get('failed', 0)}"
        )
    elif pytest_data.get("source"):
        lines.append(f"pytest: source={pytest_data['source']} (no numeric summary)")
    else:
        lines.append("pytest: no summary recorded yet")
    campaign = snapshot_data.get("campaign") or {}
    if campaign.get("campaign_id"):
        lines.append(
            f"campaign: {campaign['campaign_id']} status={campaign.get('status', '?')} "
            f"active={','.join(campaign.get('active_instances') or []) or '-'} "
            f"work={campaign.get('work_items', {})}"
        )
    lines.append("")
    header = (
        f"{'inst':<4} {'svc':<10} {'healthy':<8} {'qq':<10} {'age':<10} "
        f"{'cycles':<8} {'crash':<6} {'pid':<7} {'proj':<8} {'status':<10}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in snapshot_data.get("instances", []):
        lines.append(
            f"{row['instance_id']:<4} "
            f"{row['service']:<10} "
            f"{str(row['healthy']):<8} "
            f"{row.get('delivery_status', 'unknown'):<10} "
            f"{row['age']:<10} "
            f"{row['cycles']:<8} "
            f"{row['crashes']:<6} "
            f"{row['pid']:<7} "
            f"{row.get('project_id', '-'):<8} "
            f"{row.get('project_status', '-'):<10}"
        )
    lines.append("")
    lines.append("Project state details:")
    for row in snapshot_data.get("instances", []):
        project_id = row.get("project_id")
        if not project_id:
            continue
        lines.append(
            f"  {row['instance_id']} {project_id}: "
            f"iter={row.get('current_iteration', 0)} status={row.get('project_status', '-')}"
        )
        if row.get("blocked_reason"):
            lines.append(f"    blocked_reason: {row['blocked_reason']}")
        if row.get("resume_event"):
            lines.append(f"    resume_event: {row['resume_event']}")
        latest = row.get("latest_receipt") or {}
        if latest:
            lines.append(
                f"    latest receipt iter={latest.get('iteration', '-')} "
                f"delivery_confirmed={latest.get('delivery_confirmed', False)} "
                f"stop_reason={latest.get('stop_reason', '')[:80]}"
            )
            for action in latest.get("next_actions", [])[:3]:
                lines.append(
                    f"    next: {action.get('status', '?')} {action.get('event_type', '?')} "
                    f"task_id={action.get('task_id', '')}"
                )
    return "\n".join(lines) + "\n"


__all__ = ["snapshot", "render_text"]
