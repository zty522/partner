from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/mnt/e/work/partner")

from partner.governance.policy_control import (  # noqa: E402
    choose_action,
    evaluate_canaries,
    record_regression_attestation,
)
from partner.governance.candidate_skills import register_candidate_skill  # noqa: E402


CHOICES = [
    {"strategy_id": "baseline_v1", "event_type": "baseline_event", "title": "baseline"},
    {"strategy_id": "candidate_v2", "event_type": "candidate_event", "title": "candidate"},
]


def _register_bdk_skill(workspace, *, candidate_id, kernel_probs, experiment_id):
    intervention = {
        "bdk_module": "bdk.function_pool.FunctionPool",
        "kernel_names": ["linear", "quadratic", "fourier", "expdecay"],
        "kernel_probs": kernel_probs,
        "kernel_logits": [0.6, 0.5, -0.5, -1.0],
    }
    register_candidate_skill(workspace, {
        "candidate_id": candidate_id,
        "title": "bdk skill",
        "status": "candidate",
        "experiment_id": experiment_id,
        "strategy_id": candidate_id,
        "source_episode_ids": ["test:test"],
        "success_criteria": ["test"],
        "applicability": ["test"],
        "intervention": json.dumps(intervention, ensure_ascii=False),
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "candidate_event",
                               "allowed_instances": ["05"], "default_params": {}},
    })


def _register_non_bdk_skill(workspace, candidate_id, experiment_id):
    register_candidate_skill(workspace, {
        "candidate_id": candidate_id,
        "title": "non-bdk",
        "status": "candidate",
        "experiment_id": experiment_id,
        "strategy_id": candidate_id,
        "source_episode_ids": ["test:test"],
        "success_criteria": ["test"],
        "applicability": ["test"],
        "intervention": json.dumps({"method": "sklearn"}),
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "candidate_event",
                               "allowed_instances": ["05"], "default_params": {}},
    })


def _write_trajectories(workspace, *, experiment_id, decision_key,
                         baseline_rewards, candidate_rewards):
    """Write trajectories mirroring existing test_policy_control.py pattern."""
    policy_dir = Path(workspace) / "share" / "mind" / "governance" / "experience_guided_policy"
    trajectory_path = policy_dir / "trajectories.jsonl"
    for arm, strategy, rewards in (
        ("baseline", "baseline_v1", baseline_rewards),
        ("candidate", "candidate_v2", candidate_rewards),
    ):
        for index, reward in enumerate(rewards):
            row = {
                "schema_version": 2,
                "trajectory_id": f"{arm}-{index}",
                "policy_eligible": True,
                "reward": reward,
                "action": {
                    "policy_decision": decision_key, "policy_arm": arm,
                    "strategy_id": strategy, "experiment_id": experiment_id,
                },
            }
            with trajectory_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + chr(10))


def test_auto_enforce_off_by_default(tmp_path, monkeypatch):
    """Without PARTNER_BDK_OCAMMS_ENFORCE=1, no enforcement happens."""
    monkeypatch.delenv("PARTNER_BDK_OCAMMS_ENFORCE", raising=False)
    root = str(tmp_path / "workspace")
    selected = choose_action(root, "project", "decision", CHOICES)
    assert selected["policy_arm"] == "candidate"
    _register_bdk_skill(root, candidate_id="candidate_v2", experiment_id=selected["experiment_id"],
                         kernel_probs=[0.34, 0.33, 0.33, 0.0])  # would FAIL if enforced
    _write_trajectories(root, experiment_id=selected["experiment_id"],
                         decision_key="decision",
                         baseline_rewards=[0.35, 0.40, 0.45],
                         candidate_rewards=[0.75, 0.80, 0.85])
    record_regression_attestation(
        root, experiment_id=selected["experiment_id"], command="pytest -q",
        passed=True, summary="ok",
    )
    result = evaluate_canaries(root)
    assert result["decisions"][0]["decision"] == "promoted"


def test_auto_enforce_blocks_failing_bdk_skill(tmp_path, monkeypatch):
    """With PARTNER_BDK_OCAMMS_ENFORCE=1, a failing BDK skill blocks promotion."""
    monkeypatch.setenv("PARTNER_BDK_OCAMMS_ENFORCE", "1")
    root = str(tmp_path / "workspace")
    selected = choose_action(root, "project", "decision", CHOICES)
    _register_bdk_skill(root, candidate_id="candidate_v2", experiment_id=selected["experiment_id"],
                         kernel_probs=[0.34, 0.33, 0.33, 0.0])  # 3 activated -> FAIL
    _write_trajectories(root, experiment_id=selected["experiment_id"],
                         decision_key="decision",
                         baseline_rewards=[0.35, 0.40, 0.45],
                         candidate_rewards=[0.75, 0.80, 0.85])
    record_regression_attestation(
        root, experiment_id=selected["experiment_id"], command="pytest -q",
        passed=True, summary="ok",
    )
    result = evaluate_canaries(root)
    # The local "decision" variable in policy_control.py is still "promoted"
    # (it was decided BEFORE the guard ran).  But the actual outcome from
    # decide_experiment must reflect the block.  Verify:
    # 1. outcome["status"] is bdk_ocamms_blocked
    # 2. control["promoted"] does NOT include the decision_key
    outcome = result["decisions"][0]["result"]
    assert outcome.get("status") == "bdk_ocamms_blocked"
    assert outcome.get("ok") is False
    control = json.loads(Path(result["control_policy_path"]).read_text(encoding="utf-8"))
    assert "decision" not in control.get("promoted", {})


def test_auto_enforce_allows_passing_bdk_skill(tmp_path, monkeypatch):
    """With PARTNER_BDK_OCAMMS_ENFORCE=1, a passing BDK skill proceeds normally."""
    monkeypatch.setenv("PARTNER_BDK_OCAMMS_ENFORCE", "1")
    root = str(tmp_path / "workspace")
    selected = choose_action(root, "project", "decision", CHOICES)
    _register_bdk_skill(root, candidate_id="candidate_v2", experiment_id=selected["experiment_id"],
                         kernel_probs=[0.41, 0.37, 0.14, 0.08])  # 2 activated -> PASS
    _write_trajectories(root, experiment_id=selected["experiment_id"],
                         decision_key="decision",
                         baseline_rewards=[0.35, 0.40, 0.45],
                         candidate_rewards=[0.75, 0.80, 0.85])
    record_regression_attestation(
        root, experiment_id=selected["experiment_id"], command="pytest -q",
        passed=True, summary="ok",
    )
    result = evaluate_canaries(root)
    assert result["decisions"][0]["decision"] == "promoted"


def test_auto_enforce_ignores_non_bdk_skill(tmp_path, monkeypatch):
    """A non-BDK skill should NOT trigger enforcement even with env var set."""
    monkeypatch.setenv("PARTNER_BDK_OCAMMS_ENFORCE", "1")
    root = str(tmp_path / "workspace")
    selected = choose_action(root, "project", "decision", CHOICES)
    _register_non_bdk_skill(root, candidate_id="candidate_v2", experiment_id=selected["experiment_id"])
    _write_trajectories(root, experiment_id=selected["experiment_id"],
                         decision_key="decision",
                         baseline_rewards=[0.35, 0.40, 0.45],
                         candidate_rewards=[0.75, 0.80, 0.85])
    record_regression_attestation(
        root, experiment_id=selected["experiment_id"], command="pytest -q",
        passed=True, summary="ok",
    )
    result = evaluate_canaries(root)
    assert result["decisions"][0]["decision"] == "promoted"
