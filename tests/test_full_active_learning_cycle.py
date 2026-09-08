"""ADR 0061 v2 + full active-learning cycle."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/e/work/partner")

import pytest

from partner.governance.instance_native import PROJECTS, enabled_instances
from partner.governance.real_action_contract import assess
from partner.governance.evolution_events import load_evolution_events, verify_evolution_ledger


def _root(tmp_path: Path):
    root = tmp_path / "workspace"
    (root / "config").mkdir(parents=True)
    (root / "config/partner_config.json").write_text(json.dumps({
        "runtime": {
            "mode": "manual_stable",
            "instance_native_autonomy": True,
            "instance_native_enabled_instances": list(PROJECTS),
            "instance_native_max_active": 2,
            "instance_native_max_learning_interruptions_per_failure": 1,
            "instance_native_slot_quantum_project_steps": 1,
            "instance_native_max_repeat_findings": 2,
            "instance_native_require_external_artifact": True,
        }
    }), encoding="utf-8")
    for pid in PROJECTS.values():
        (root / "share/projects" / pid[0] / "governance/receipts").mkdir(parents=True)
    return root


def test_assess_accepts_governance_receipt_as_real_artifact(tmp_path):
    """ADR 0061 v2: a project step that writes its governance receipt is real,
    even without external actions; reports-only paths are still rejected."""
    root = _root(tmp_path)
    receipt_path = root / "share/projects" / list(PROJECTS.values())[0][0] / "governance/receipts/0001_x.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text('{"receipt_id": "x"}', encoding="utf-8")
    ok = assess(
        workspace_root_path=root, project_id=list(PROJECTS.values())[0][0],
        findings=["real governance artefact written under receipts folder"],
        actions_executed=["atomic_inspect_file", "pytest"],
        artifacts=[str(receipt_path)],
    )
    assert ok["ok"] is True
    assert str(receipt_path) in ok.get("governance_artifacts", [])
    # reports/ still counts as fake
    reports_file = root / "share/projects" / list(PROJECTS.values())[0][0] / "reports" / "trick.md"
    reports_file.parent.mkdir(parents=True, exist_ok=True)
    reports_file.write_text("just a report", encoding="utf-8")
    bad = assess(
        workspace_root_path=root, project_id=list(PROJECTS.values())[0][0],
        findings=["read project files and write report"], actions_executed=["generate_text"],
        artifacts=[str(reports_file)],
    )
    assert bad["ok"] is False
    assert bad["violation"] == "missing_external_action" or bad["violation"] == "fake_artifact_paths"


def test_external_knowledge_event_counts_as_real_external_action(tmp_path):
    root = _root(tmp_path)
    project_id = PROJECTS["04"][0]
    artifact = root / "external/insights/grounded_learning.pdf"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"%PDF-1.4\n" + b"x" * 1200)
    result = assess(
        workspace_root_path=root,
        project_id=project_id,
        findings=["github clone and paper PDF produced a new falsifiable mechanism"],
        actions_executed=["external_knowledge_scout"],
        artifacts=[str(artifact)],
    )
    assert result["ok"] is True, result


def test_full_active_learning_cycle_runs_per_instance(tmp_path):
    sys.path.insert(0, "/mnt/e/work/partner/scripts")
    import run_full_active_learning_cycle
    root = _root(tmp_path)
    # Force a deterministic receipt per project so diagnose has data.
    for iid, (pid, _) in PROJECTS.items():
        rec = root / "share/projects" / pid / "governance/receipts/0001_seed.json"
        rec.write_text(json.dumps({
            "receipt_id": f"seed_{iid}",
            "findings": [f"initial seed finding for {iid}"],
            "actions_executed": ["atomic_inspect_file"],
            "artifacts": [str(rec)],
            "unresolved_questions": [f"what is the next move for {iid}?"],
        }), encoding="utf-8")
    summary = run_full_active_learning_cycle._run_cycle(root, "04")
    assert summary["instance_id"] == "04"
    assert summary["candidate_path"].endswith(".json")
    assert Path(summary["candidate_path"]).exists()
    for ev in summary["events"].values():
        assert ev.startswith("evoevt_")
    events = load_evolution_events(str(root))
    types_seen = {row["event_type"] for row in events
                  if row["subject_id"] == f"instance.04.project.{PROJECTS['04'][0]}"}
    assert {"active_learning/topic_selected",
            "active_learning/diagnosis_completed",
            "active_learning/query_proposed",
            "active_learning/candidate_bundled",
            "active_learning/matched_experiment_completed"} <= types_seen
    verification = verify_evolution_ledger(str(root))
    assert verification["ok"] is True, verification


def test_cycle_is_idempotent_within_the_same_minute(tmp_path):
    sys.path.insert(0, "/mnt/e/work/partner/scripts")
    import run_full_active_learning_cycle
    root = _root(tmp_path)
    for iid, (pid, _) in PROJECTS.items():
        rec = root / "share/projects" / pid / "governance/receipts/0001_seed.json"
        rec.write_text(json.dumps({
            "receipt_id": f"seed_{iid}",
            "findings": [f"seed for {iid}"],
            "actions_executed": ["atomic_inspect_file"],
            "artifacts": [str(rec)],
        }), encoding="utf-8")
    first = run_full_active_learning_cycle._run_cycle(root, "04")
    second = run_full_active_learning_cycle._run_cycle(root, "04")
    # Same minute -> same idempotency keys -> ledger must not grow.
    count = len(load_evolution_events(str(root)))
    assert first["events"] == second["events"]
    assert count <= 5
