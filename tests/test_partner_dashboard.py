"""Unit tests for partner_dashboard.collector + render.

These tests use a tmp_path workspace to avoid touching the real Partner
workspace on disk. Each test mocks the systemd lookup by monkeypatching
``_service_state`` and writes a stub heartbeat.json.

The dashboard contract is small: read files, never write, never call LLM.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from partner.monitoring import partner_dashboard as pd


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _make_workspace(tmp_path):
    """Build a minimal workspace skeleton covering all five instances."""
    ws = tmp_path / "workspace"
    for inst in ("01", "02", "03", "04", "05"):
        (ws / "instances" / inst / "state").mkdir(parents=True, exist_ok=True)
    return ws


def _heartbeat(workspace_root, instance_id, *, age_seconds, cycles=100, crashes=0, pid=12345):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat(
        timespec="seconds"
    )
    state_dir = workspace_root / "instances" / instance_id / "state"
    _write_json(
        state_dir / "heartbeat.json",
        {
            "last_heartbeat": stamp,
            "status": "running",
            "current_task_id": "task-1",
            "cycle_count": cycles,
            "crash_count": crashes,
        },
    )
    _write_json(
        state_dir / "instance_runtime.lock",
        {"pid": pid, "instance_id": instance_id, "started_at": stamp},
    )


def _governance(workspace_root, project_id, *, owner_instance, status,
                 current_iteration, blocked_reason="", resume_event="",
                 latest_receipt=None):
    gov_dir = workspace_root / "share" / "projects" / project_id / "governance"
    gov_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        gov_dir / "project_state.json",
        {
            "project_id": project_id,
            "owner_instance": owner_instance,
            "status": status,
            "goal": f"goal for {project_id}",
            "current_iteration": current_iteration,
            "blocked_reason": blocked_reason,
            "resume_event": resume_event,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    if latest_receipt is not None:
        receipts_dir = gov_dir / "receipts"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        _write_json(receipts_dir / "0001_receipt_test.json", latest_receipt)


def test_snapshot_is_deterministic_and_readonly(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path)
    _heartbeat(workspace, "01", age_seconds=10, cycles=42, crashes=0)
    _heartbeat(workspace, "02", age_seconds=9999, cycles=300, crashes=5)
    _governance(
        workspace, "xiaohongshu_operations", owner_instance="01",
        status="completed", current_iteration=2,
        latest_receipt={
            "project_id": "xiaohongshu_operations",
            "iteration": 2,
            "goal": "round2",
            "actions_executed": ["xhs_inspect"],
            "artifacts": ["step3.png"],
            "findings": ["upload requirements captured"],
            "delivery_confirmed": True,
            "next_actions": [],
            "stop_reason": "phase complete",
            "receipt_id": "receipt_test",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )

    monkeypatch.setattr(
        pd, "_service_state",
        lambda inst: "active" if inst == "01" else "inactive",
    )
    snapshot = pd.snapshot(
        workspace_root=str(workspace),
        code_root=str(tmp_path),
        pytest_summary_path=str(tmp_path / "missing.txt"),
        include_inactive=True,
    )

    assert snapshot["active_count"] == 1
    assert snapshot["healthy_count"] == 1
    assert snapshot["max_active"] == 2
    ids = [row["instance_id"] for row in snapshot["instances"]]
    assert ids == ["01", "02", "03", "04", "05"]

    row01 = snapshot["instances"][0]
    assert row01["service"] == "active"
    assert row01["healthy"] is True
    assert row01["cycles"] == 42
    assert row01["crashes"] == 0
    assert row01["project_status"] == "completed"
    assert row01["latest_receipt"]["iteration"] == 2
    assert row01["latest_receipt"]["delivery_confirmed"] is True

    row02 = snapshot["instances"][1]
    assert row02["service"] == "inactive"
    assert row02["healthy"] is False


def test_active_only_filter_drops_inactive(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path)
    for inst in ("01", "02", "03", "04", "05"):
        _heartbeat(workspace, inst, age_seconds=5, cycles=10, crashes=0)
    # Only 01/02 are running in this scenario; 03-05 stay paused.
    monkeypatch.setattr(
        pd, "_service_state",
        lambda inst: "active" if inst in {"01", "02"} else "inactive",
    )

    snapshot = pd.snapshot(
        workspace_root=str(workspace),
        code_root=str(tmp_path),
        pytest_summary_path=str(tmp_path / "missing.txt"),
        include_inactive=False,
    )
    assert [row["instance_id"] for row in snapshot["instances"]] == ["01", "02"]
    assert snapshot["active_count"] == 2
    assert snapshot["healthy_count"] == 2


def test_blocked_project_surfaces_resume_event(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path)
    _heartbeat(workspace, "02", age_seconds=20, cycles=400, crashes=0)
    _governance(
        workspace, "molecular_generation", owner_instance="02",
        status="blocked", current_iteration=4,
        blocked_reason="missing target activity data",
        resume_event="molecular_target_data_available",
        latest_receipt={
            "project_id": "molecular_generation",
            "iteration": 4,
            "goal": "optimization",
            "actions_executed": ["molecular_goal_optimization_benchmark"],
            "artifacts": ["top20.csv"],
            "findings": ["top20 collapse"],
            "delivery_confirmed": True,
            "next_actions": [],
            "stop_reason": "blocked: missing activity data",
            "receipt_id": "receipt_blocked",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    monkeypatch.setattr(pd, "_service_state", lambda inst: "active" if inst == "02" else "inactive")

    snapshot = pd.snapshot(
        workspace_root=str(workspace),
        code_root=str(tmp_path),
        pytest_summary_path=str(tmp_path / "missing.txt"),
        include_inactive=False,
    )
    row = snapshot["instances"][0]
    assert row["project_status"] == "blocked"
    assert row["blocked_reason"] == "missing target activity data"
    assert row["resume_event"] == "molecular_target_data_available"
    assert row["latest_receipt"]["delivery_confirmed"] is True
    assert "blocked" in row["latest_receipt"]["stop_reason"]


def test_render_text_includes_header_and_blocked_details(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path)
    _heartbeat(workspace, "02", age_seconds=20, cycles=400, crashes=0)
    _governance(
        workspace, "molecular_generation", owner_instance="02",
        status="blocked", current_iteration=4,
        blocked_reason="missing target activity data",
        resume_event="molecular_target_data_available",
    )
    monkeypatch.setattr(
        pd, "_service_state",
        lambda inst: "active" if inst == "02" else "inactive",
    )

    snapshot = pd.snapshot(
        workspace_root=str(workspace),
        code_root=str(tmp_path),
        pytest_summary_path=str(tmp_path / "missing.txt"),
        include_inactive=True,
    )
    text = pd.render_text(snapshot)

    assert "Partner Dashboard @" in text
    assert "02" in text and "blocked" in text
    assert "missing target activity data" in text
    assert "molecular_target_data_available" in text
    assert "pytest: no summary recorded yet" in text


def test_pytest_summary_parses_real_output(tmp_path):
    summary = tmp_path / "summary.txt"
    summary.write_text(
        "============================= test session starts ==============================\n"
        "collected 98 items\n"
        "\n"
        "tests/test_a.py ..........................                              [ 30%]\n"
        "============================= 98 passed in 6.43s ==============================\n",
        encoding="utf-8",
    )
    data = pd._pytest_summary(str(summary))
    assert data["passed"] == 98
    assert data["failed"] == 0
    assert data["collected"] == 98
    assert data["source"] == str(summary)


def test_pytest_summary_handles_missing_file(tmp_path):
    data = pd._pytest_summary(str(tmp_path / "missing.txt"))
    assert data["passed"] == 0
    assert data["failed"] == 0
    assert data["collected"] == 0
    assert data["source"] == ""


def test_format_age_handles_unknown():
    assert pd._format_age(None) == "unknown"
    assert pd._format_age(-1) == "unknown"
    assert pd._format_age(45) == "45s"
    assert pd._format_age(125) == "2m05s"
    assert pd._format_age(3700) == "1h01m"

def test_project_id_for_uses_scheduler_roles():
    """`_project_id_for` must consult `partner.governance.scheduler.ROLES`
    rather than hardcoded constants, so adding a role there automatically
    surfaces in the dashboard.
    """
    assert pd._project_id_for("01") == "xiaohongshu_operations"
    assert pd._project_id_for("02") == "molecular_generation"
    assert pd._project_id_for("03") == "partner_framework_frontend"
    assert pd._project_id_for("04") == "literature_github_learning"
    assert pd._project_id_for("05") == "agent_self_evolution"
    assert pd._project_id_for("99") == ""


def test_snapshot_includes_paused_projects_for_unstarted_roles(tmp_path, monkeypatch):
    """03/04/05 should expose their paused governance state in the
    dashboard so operators can see why they are not active.
    """
    workspace = _make_workspace(tmp_path)
    # Write paused governance states for all five instances.
    _governance(workspace, "xiaohongshu_operations", owner_instance="01",
                status="completed", current_iteration=2)
    _governance(workspace, "molecular_generation", owner_instance="02",
                status="blocked", current_iteration=4,
                blocked_reason="missing activity data",
                resume_event="molecular_target_data_available")
    _governance(workspace, "partner_framework_frontend", owner_instance="03",
                status="paused", current_iteration=0,
                resume_event="user_slot_assignment")
    _governance(workspace, "literature_github_learning", owner_instance="04",
                status="paused", current_iteration=0,
                resume_event="user_slot_assignment")
    _governance(workspace, "agent_self_evolution", owner_instance="05",
                status="paused", current_iteration=0,
                resume_event="user_slot_assignment")

    for inst in ("01", "02", "03", "04", "05"):
        _heartbeat(workspace, inst, age_seconds=5, cycles=10, crashes=0)

    monkeypatch.setattr(pd, "_service_state",
                        lambda inst: "active" if inst in {"01", "02"} else "inactive")

    snapshot = pd.snapshot(
        workspace_root=str(workspace),
        code_root=str(tmp_path),
        pytest_summary_path=str(tmp_path / "missing.txt"),
        include_inactive=True,
    )

    statuses = {row["instance_id"]: row["project_status"] for row in snapshot["instances"]}
    assert statuses["01"] == "completed"
    assert statuses["02"] == "blocked"
    assert statuses["03"] == "paused"
    assert statuses["04"] == "paused"
    assert statuses["05"] == "paused"

    project_ids = {row["instance_id"]: row["project_id"] for row in snapshot["instances"]}
    assert project_ids["03"] == "partner_framework_frontend"
    assert project_ids["04"] == "literature_github_learning"
    assert project_ids["05"] == "agent_self_evolution"

def test_healthy_flag_flips_for_stale_or_crashing_instances(tmp_path, monkeypatch):
    """An active service whose heartbeat is stale (>= 600s old) or whose
    crash_count > 0 must be flagged ``healthy=False``. Inactive services
    are always ``healthy=False`` regardless of stored heartbeat values.
    """
    workspace = _make_workspace(tmp_path)
    _governance(workspace, "xiaohongshu_operations", owner_instance="01",
                status="completed", current_iteration=2)

    # Fresh heartbeat, active service => healthy True.
    _heartbeat(workspace, "01", age_seconds=5, cycles=10, crashes=0, pid=1111)
    # Stale heartbeat (700s old), active service => healthy False.
    _heartbeat(workspace, "02", age_seconds=700, cycles=10, crashes=0, pid=2222)
    # Crashes recorded, active service => healthy False.
    _heartbeat(workspace, "03", age_seconds=5, cycles=10, crashes=2, pid=3333)
    # Inactive service with fresh heartbeat => healthy False.
    _heartbeat(workspace, "04", age_seconds=5, cycles=10, crashes=0, pid=4444)
    # Missing runtime lock, inactive service => healthy False.
    _heartbeat(workspace, "05", age_seconds=5, cycles=10, crashes=0, pid=5555)

    monkeypatch.setattr(
        pd, "_service_state",
        lambda inst: "active" if inst in {"01", "02", "03"} else "inactive",
    )

    snapshot = pd.snapshot(
        workspace_root=str(workspace),
        code_root=str(tmp_path),
        pytest_summary_path=str(tmp_path / "missing.txt"),
        include_inactive=True,
    )
    healthy_by_id = {row["instance_id"]: row["healthy"] for row in snapshot["instances"]}
    assert healthy_by_id["01"] is True, "active + fresh + no crash must be healthy"
    assert healthy_by_id["02"] is False, "stale heartbeat must not be healthy"
    assert healthy_by_id["03"] is False, "non-zero crash count must not be healthy"
    assert healthy_by_id["04"] is False, "inactive service must not be healthy"
    assert healthy_by_id["05"] is False, "inactive service must not be healthy"

    # Healthy count is reported in the snapshot.
    assert snapshot["healthy_count"] == 1
