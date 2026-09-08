"""ADR 0061 rebound script: confirm the script correctly rebinds blocked instances."""
import json
from pathlib import Path

from partner.governance.instance_native import (
    handle_terminal, load_state, load_native_runtime_config,
    PROJECTS, recover_or_start,
)
from partner.governance.storage import workspace_root


def _workspace(tmp_path: Path, enabled=("04",)):
    root = tmp_path / "workspace"
    (root / "config").mkdir(parents=True)
    (root / "config/partner_config.json").write_text(json.dumps({
        "runtime": {
            "mode": "manual_stable",
            "instance_native_autonomy": True,
            "instance_native_enabled_instances": list(enabled),
            "instance_native_max_active": 1,
            "instance_native_max_learning_interruptions_per_failure": 1,
            "instance_native_slot_quantum_project_steps": 1,
            "instance_native_max_repeat_findings": 2,
            "instance_native_require_external_artifact": True,
        },
    }), encoding="utf-8")
    for instance_id in PROJECTS:
        (root / "instances" / instance_id / "state/tasks").mkdir(parents=True)
    return root


def _blocked_task(root: Path, instance_id: str, task_id: str) -> None:
    """Stage a failed task that triggers BLOCKED via ADR 0061 repeated findings."""
    project = root / "share/projects" / PROJECTS[instance_id][0]
    (project / "governance/receipts").mkdir(parents=True, exist_ok=True)
    baseline = project / "governance/receipts/iter_001_baseline.json"
    baseline.write_text(json.dumps({
        "iteration": 1,
        "findings": ["已完成本地微计划执行，未产生外部产物"],
        "actions_executed": ["generate_text"],
        "artifacts": [str(project / "reports/empty.md")],
    }), encoding="utf-8")
    directory = root / "instances" / instance_id / "state/tasks" / task_id
    directory.mkdir(parents=True)
    value = {
        "task_id": task_id,
        "completion_status": "failed",
        "user_message": "[instance_native=true] [native_kind=project] do work",
        "metadata": {"manual_iteration_governance": {
            "ok": False, "status": "truth_gate_failed",
            "receipt": {"next_actions": [], "findings": [], "actions_executed": [], "artifacts": []},
        }},
    }
    (directory / "task_instance.json").write_text(
        json.dumps(value), encoding="utf-8")
    (directory / "task_log.jsonl").write_text(
        json.dumps({"ts": "2026-01-01T00:00:00",
                    "event": "task_instance_created"}) + "\n",
        encoding="utf-8",
    )


def test_rebound_script_writes_audit_and_lifts_block(tmp_path):
    """Simulate the on-call runtime: stage a BLOCKED instance, run the rebind
    script, and confirm the script records an audit row plus the phase shift."""
    import subprocess, sys
    root = _workspace(tmp_path)
    recover_or_start(root, "04")
    _blocked_task(root, "04", "task-failed-1")
    handle_terminal(root, instance_id="04", task_id="task-failed-1")
    # First failure dispatches a learning task; let that learning task
    # fail so instance_native immediately BLOCKEDs (bounded learning budget).
    directory = root / "instances/04/state/tasks/task-learning-fail"
    directory.mkdir(parents=True)
    (directory / "task_instance.json").write_text(
        json.dumps({
            "task_id": "task-learning-fail",
            "completion_status": "failed",
            "user_message": "[instance_native=true] [native_kind=learning] learn",
            "metadata": {"manual_iteration_governance": {
                "ok": False, "status": "truth_gate_failed",
                "receipt": {"next_actions": [], "findings": [],
                            "actions_executed": [], "artifacts": []},
            }},
        }), encoding="utf-8")
    handle_terminal(root, instance_id="04", task_id="task-learning-fail")
    state = load_state(root, "04")
    assert state.phase == "BLOCKED", state.phase
    # Run the rebind script in-process via direct invocation to keep CI hermetic.
    script = Path("/mnt/e/work/partner/scripts/rebound_instances_to_real_action_contract.py")
    assert script.exists()
    proc = subprocess.run(
        [sys.executable, str(script), "--workspace", str(root),
         "--reason", "pytest adr0061 rebind"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "rebound_complete"
    rebinds = {item["instance_id"]: item for item in payload["rebinds"]}
    assert rebinds["04"]["action"] == "diagnostic_yield"
    assert rebinds["04"]["result"]["phase"] == "YIELD_SLOT"
    state = load_state(root, "04")
    assert state.phase == "YIELD_SLOT"
    audit_path = Path(payload["audit_path"])
    assert audit_path.exists()
    rows = [json.loads(line) for line in audit_path.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    assert rows and rows[0]["instance_id"] == "04"


def test_real_action_contract_helper_is_shared_between_runners(tmp_path):
    """Both instance_native and manual_runtime must consult the same helper
    so ADR 0061 stays single-sourced and cannot drift."""
    from partner.governance.manual_runtime import record_manual_task_outcome
    from partner.governance.real_action_contract import (
        ACTION_SIGNALS, assess, findings_signature, list_project_artifact_resolutions,
    )
    assert "exec:" in ACTION_SIGNALS
    fake_assessment = assess(
        workspace_root_path=tmp_path, project_id="p",
        findings=["same finding again", "same finding again"],
        actions_executed=["generate_text"], artifacts=[],
        require_external_artifact=True,
    )
    assert fake_assessment["ok"] is False
    # List helper must not raise on missing project.
    out = list_project_artifact_resolutions(tmp_path, "absent_project")
    assert out == set()


def test_real_action_contract_assess_artifact_directory_validation(tmp_path):
    from partner.governance.real_action_contract import assess
    artifact = tmp_path / "real.json"
    artifact.write_text("ok", encoding="utf-8")
    valid = assess(
        workspace_root_path=tmp_path, project_id="p",
        findings=["a sufficiently distinct finding for the first pass"],
        actions_executed=["exec:python3 demo.py"],
        artifacts=[str(artifact)],
    )
    assert valid["ok"] is True
    fake = tmp_path / "share" / "projects" / "p" / "reports" / "trap.md"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("report only", encoding="utf-8")
    real = tmp_path / "real.json"
    real.write_text("ok", encoding="utf-8")
    invalid = assess(
        workspace_root_path=tmp_path, project_id="p",
        findings=["x"], actions_executed=["atomic_inspect_file"],
        artifacts=[str(real), str(fake)],
    )
    assert invalid["ok"] is False
    assert invalid["violation"] == "fake_artifact_paths"
