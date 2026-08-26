import json

from partner.governance.rl_evolution import evaluate_manual_evolution_evidence
from partner.governance.project_loop import invalidate_receipt, record_iteration
from partner.governance.storage import append_jsonl


def _row(index, family):
    return {
        "schema_version": 2, "trajectory_id": f"traj-{index}",
        "project_id": "literature_github_learning", "instance_id": "04",
        "kind": "manual_project_iteration", "policy_eligible": True,
        "state": {"receipt_id": f"receipt-{index}", "source_families": [family]},
        "outcome": {"outcome_fingerprint": f"fingerprint-{index}"},
    }


def test_manual_evolution_gate_requires_heterogeneous_samples(tmp_path):
    workspace = str(tmp_path / "workspace" / "instances" / "05")
    path = tmp_path / "workspace" / "share" / "mind" / "governance" / "rl" / "trajectories.jsonl"
    for index in range(1, 4):
        append_jsonl(path, _row(index, "deepseek-harness"))
    insufficient = evaluate_manual_evolution_evidence(workspace, project_id="literature_github_learning")
    assert insufficient["status"] == "insufficient_evidence"
    assert insufficient["experiment_created"] is False

    append_jsonl(path, _row(4, "openai-codex"))
    ready = evaluate_manual_evolution_evidence(workspace, project_id="literature_github_learning")
    assert ready["status"] == "candidate_ready"
    assert ready["experiment_created"] is True
    assert ready["promotion"] is False


def test_manual_evolution_gate_excludes_invalidated_receipt_trajectory(tmp_path):
    workspace = str(tmp_path / "workspace" / "instances" / "05")
    path = tmp_path / "workspace" / "share" / "mind" / "governance" / "rl" / "trajectories.jsonl"
    for index, family in ((1, "deepseek-harness"), (2, "openai-codex"), (3, "github")):
        recorded = record_iteration(workspace, {
            "project_id": "literature_github_learning", "owner_instance": "04",
            "goal": f"round {index}", "inputs": [] if index == 1 else [f"a{index-1}.md"],
            "actions_executed": ["extract"], "artifacts": [f"a{index}.md"],
            "findings": ["grounded"], "delivery_confirmed": True, "stop_reason": "bounded",
        })
        row = _row(index, family)
        row["state"]["receipt_id"] = recorded["receipt"]["receipt_id"]
        append_jsonl(path, row)
    latest = recorded["receipt"]["receipt_id"]
    corrected = invalidate_receipt(
        workspace, "literature_github_learning", latest,
        reason="quality gate failed", evidence=["iteration_check.satisfied=false"],
    )
    assert corrected["ok"] is True
    result = evaluate_manual_evolution_evidence(workspace, project_id="literature_github_learning")
    assert result["status"] == "insufficient_evidence"
    assert result["summary"]["samples"] == 2
