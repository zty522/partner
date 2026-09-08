"""Promotion plumbing tests isolated from the real Partner workspace.

These tests prove file/control-path behavior only. They do not provide real
business reward evidence and must never append to partner_workspace ledgers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/e/work/partner")

from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.policy_control import evaluate_canaries
from partner.learn.bdk_ocamms_promotion_guard import bdk_ocamms_promotion_guard


def _register_skill(workspace: str, experiment_id: str) -> dict:
    candidate_id = "candidate_bdk_isolated_test"
    result = register_candidate_skill(workspace, {
        "candidate_id": candidate_id,
        "title": "isolated BDK plumbing test",
        "status": "candidate",
        "artifact_type": "model_policy",
        "project_id": "bdk_isolated_test",
        "experiment_id": experiment_id,
        "strategy_id": candidate_id,
        "source_episode_ids": ["fixture:bdk-isolated"],
        "success_criteria": ["isolated plumbing test passes"],
        "applicability": ["pytest isolated workspace"],
        "intervention": json.dumps({
            "bdk_module": "bdk.function_pool.FunctionPool",
            "kernel_names": ["linear", "quadratic", "fourier", "expdecay"],
            "kernel_probs": [0.5354, 0.1782, 0.1782, 0.1081],
            "kernel_logits": [0.6, -0.5, -0.5, -1.0],
        }),
        "execution_contract": {
            "ready": True,
            "kind": "event",
            "event_type": "targetdiff_bdk_function_pool",
            "allowed_instances": ["02"],
            "default_params": {"epochs": 1, "run_id": "isolated-test"},
        },
    })
    return result["candidate"]


def _setup_canary_workspace(workspace: str, *, decision_key: str,
                            target_skill: dict, experiment_id: str) -> None:
    root = Path(workspace)
    policy_dir = root / "share" / "mind" / "governance" / "experience_guided_policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    assignments = policy_dir / "canary_assignments.jsonl"
    trajectories = policy_dir / "trajectories.jsonl"
    experiments = root / "share" / "mind" / "governance" / "experiments.jsonl"
    experiments.parent.mkdir(parents=True, exist_ok=True)
    attestation_dir = policy_dir / "regression_attestations"
    attestation_dir.mkdir(parents=True, exist_ok=True)

    target_strategy = target_skill["candidate_id"]
    with assignments.open("a", encoding="utf-8") as handle:
        for arm, strategy in (("baseline", "baseline_bdk_fixture"),
                              ("candidate", target_strategy)):
            handle.write(json.dumps({
                "project_id": "bdk_isolated_test", "decision_key": decision_key,
                "strategy_id": strategy, "policy_arm": arm,
                "experiment_id": experiment_id, "selected_at": "2026-08-29T00:00:00+08:00",
            }) + "\n")
    with trajectories.open("a", encoding="utf-8") as handle:
        for arm, strategy, rewards in (
            ("baseline", "baseline_bdk_fixture", [0.35, 0.40, 0.45]),
            ("candidate", target_strategy, [0.75, 0.80, 0.85]),
        ):
            for index, reward in enumerate(rewards):
                handle.write(json.dumps({
                    "schema_version": 2,
                    "trajectory_id": f"isolated_{arm}_{index}",
                    "policy_eligible": True,
                    "reward": reward,
                    "action": {"policy_decision": decision_key, "policy_arm": arm,
                               "strategy_id": strategy, "experiment_id": experiment_id},
                    "outcome": {"false_success": False,
                                "truth_audit": {"passed": True}},
                }) + "\n")
    (attestation_dir / f"{experiment_id}.json").write_text(
        json.dumps({"passed": True, "summary": "isolated synthetic fixture"}), encoding="utf-8")
    with experiments.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "experiment_id": experiment_id,
            "success_criteria": ["all candidate source quotes match named inputs",
                                 "0 false-success"],
        }) + "\n")


def _prepared_workspace(tmp_path: Path, suffix: str) -> tuple[str, str, dict]:
    workspace = str(tmp_path / suffix)
    Path(workspace).mkdir()
    experiment_id = f"experiment_{suffix}"
    skill = _register_skill(workspace, experiment_id)
    _setup_canary_workspace(workspace, decision_key=suffix,
                            target_skill=skill, experiment_id=experiment_id)
    return workspace, experiment_id, skill


def test_bdk_skill_passes_ocamms_guard(tmp_path):
    workspace, _experiment_id, skill = _prepared_workspace(tmp_path, "bdk_guard")
    verdict = bdk_ocamms_promotion_guard(workspace, skill["candidate_id"])
    assert verdict["not_applicable"] is False
    assert verdict["allowed"] is True


def test_bdk_skill_can_write_isolated_control_policy(tmp_path, monkeypatch):
    workspace, _experiment_id, skill = _prepared_workspace(tmp_path, "bdk_promote")
    monkeypatch.setenv("PARTNER_BDK_OCAMMS_ENFORCE", "1")
    result = evaluate_canaries(workspace, dry_run=False)
    assert len(result.get("decisions") or []) == 1
    decision = result["decisions"][0]
    assert decision["decision"] == "promoted"
    control = json.loads(Path(result["control_policy_path"]).read_text(encoding="utf-8"))
    assert control["promoted"]["bdk_promote"] == skill["candidate_id"]


def test_promotion_decision_logs_bdk_evidence_in_isolated_workspace(tmp_path, monkeypatch):
    workspace, experiment_id, skill = _prepared_workspace(tmp_path, "bdk_audit")
    monkeypatch.setenv("PARTNER_BDK_OCAMMS_ENFORCE", "1")
    evaluate_canaries(workspace, dry_run=False)
    path = Path(workspace) / "share/mind/governance/promotion_decisions.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    matching = [row for row in rows if row.get("experiment_id") == experiment_id]
    assert len(matching) == 1
    evidence = [row for row in matching[0]["evidence"]
                if isinstance(row, dict) and row.get("kind") == "bdk_ocamms_guard"]
    assert len(evidence) == 1
    assert evidence[0]["candidate_id"] == skill["candidate_id"]
