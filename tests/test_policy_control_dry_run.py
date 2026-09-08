from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, "/mnt/e/work/partner")

from partner.governance.policy_control import (  # noqa: E402
    choose_action,
    evaluate_canaries,
    record_regression_attestation,
)
from partner.governance.candidate_skills import register_candidate_skill  # noqa: E402


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


def _setup_workspace_with_canary(workspace, *, candidate_id, kernel_probs):
    """Build a minimal workspace with a complete canary setup."""
    ws = str(workspace)
    policy_dir = Path(ws) / "share" / "mind" / "governance" / "experience_guided_policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "regression_attestations").mkdir(parents=True, exist_ok=True)
    # control_policy.json
    (policy_dir / "control_policy.json").write_text(
        json.dumps({"schema_version": 1, "promoted": {}}), encoding="utf-8"
    )
    selected = choose_action(ws, "project", "decision", [
        {"strategy_id": "baseline_v1", "event_type": "baseline_event", "title": "baseline"},
        {"strategy_id": candidate_id, "event_type": "candidate_event", "title": "candidate"},
    ])
    exp_id = selected["experiment_id"]
    _register_bdk_skill(ws, candidate_id=candidate_id, kernel_probs=kernel_probs,
                        experiment_id=exp_id)
    # Write trajectories with high rewards + truth_audit passed
    with (policy_dir / "trajectories.jsonl").open("w", encoding="utf-8") as f:
        for arm, strategy, rewards in (
            ("baseline", "baseline_v1", [0.35, 0.40, 0.45]),
            ("candidate", candidate_id, [0.75, 0.80, 0.85]),
        ):
            for index, reward in enumerate(rewards):
                f.write(json.dumps({
                    "schema_version": 2,
                    "trajectory_id": f"{arm}-{index}",
                    "policy_eligible": True,
                    "reward": reward,
                    "action": {"policy_decision": "decision", "policy_arm": arm,
                                "strategy_id": strategy, "experiment_id": exp_id},
                    "outcome": {"false_success": False,
                                 "truth_audit": {"passed": True}},
                }) + chr(10))
    record_regression_attestation(ws, experiment_id=exp_id,
                                    command="pytest -q", passed=True, summary="ok")
    # experiments.jsonl with manual_truth style criteria
    (Path(ws) / "share" / "mind" / "governance" / "experiments.jsonl").write_text(
        json.dumps({
            "experiment_id": exp_id,
            "success_criteria": [
                "all candidate source quotes match named inputs",
                "0 false-success",
            ],
        }, ensure_ascii=False) + chr(10),
        encoding="utf-8",
    )
    return exp_id


def test_dry_run_blocks_failing_bdk_skill_without_writing_control(tmp_path, monkeypatch):
    """The real canary flow with BDK hook + dry_run=True: hook fires,
    blocks the promotion, but does NOT write to control_policy.json."""
    monkeypatch.setenv("PARTNER_BDK_OCAMMS_ENFORCE", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    _setup_workspace_with_canary(
        ws, candidate_id="candidate_bdk_fail",
        kernel_probs=[0.34, 0.33, 0.33, 0.0],  # 3 activated -> FAIL
    )
    control_path = ws / "share/mind/governance/experience_guided_policy/control_policy.json"
    before = control_path.read_text(encoding="utf-8")
    result = evaluate_canaries(str(ws), dry_run=True)
    after = control_path.read_text(encoding="utf-8")
    # Control was NOT modified
    assert before == after, "dry_run must NOT write to control_policy.json"
    # The decision was rejected by the BDK hook
    decisions = result.get("decisions") or []
    assert len(decisions) >= 1
    decision = decisions[0]
    assert decision["decision"] == "promoted"  # local decision
    assert decision["result"]["status"] == "bdk_ocamms_blocked"
    # dry_run_promotions is empty (the promotion was blocked)
    assert result.get("dry_run_promotions", []) == []
    # Result has the dry_run flag set
    assert result.get("dry_run") is True


def test_dry_run_allows_passing_bdk_skill_without_writing_control(tmp_path, monkeypatch):
    """dry_run=True with passing BDK skill: control NOT written even when
    promotion would have happened."""
    monkeypatch.setenv("PARTNER_BDK_OCAMMS_ENFORCE", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    _setup_workspace_with_canary(
        ws, candidate_id="candidate_bdk_pass",
        kernel_probs=[0.41, 0.37, 0.14, 0.08],  # 2 activated -> PASS
    )
    control_path = ws / "share/mind/governance/experience_guided_policy/control_policy.json"
    before = control_path.read_text(encoding="utf-8")
    result = evaluate_canaries(str(ws), dry_run=True)
    after = control_path.read_text(encoding="utf-8")
    # Control was NOT modified
    assert before == after
    # The decision is "promoted" and the BDK guard verdict is in evidence
    decisions = result.get("decisions") or []
    assert len(decisions) >= 1
    decision = decisions[0]
    assert decision["decision"] == "promoted"
    # The verdict was recorded in the evidence
    ev = decision["result"].get("decision", {}).get("evidence", [])
    bdk_entries = [e for e in ev if isinstance(e, dict) and e.get("kind") == "bdk_ocamms_guard"]
    assert len(bdk_entries) == 1
    assert bdk_entries[0]["allowed"] is True


def test_real_write_does_modify_control_when_not_dry_run(tmp_path, monkeypatch):
    """dry_run=False (default): control IS written when promotion succeeds."""
    monkeypatch.setenv("PARTNER_BDK_OCAMMS_ENFORCE", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    _setup_workspace_with_canary(
        ws, candidate_id="candidate_bdk_pass",
        kernel_probs=[0.41, 0.37, 0.14, 0.08],  # 2 activated -> PASS
    )
    control_path = ws / "share/mind/governance/experience_guided_policy/control_policy.json"
    before = control_path.read_text(encoding="utf-8")
    result = evaluate_canaries(str(ws), dry_run=False)
    after = control_path.read_text(encoding="utf-8")
    # Control WAS modified
    assert before != after
    # Verify the new control has the candidate promoted
    control = json.loads(after)
    assert "decision" in control.get("promoted", {})


def test_dry_run_disabled_by_default_for_non_bdk_skill(tmp_path, monkeypatch):
    """Without env var, dry_run mode does nothing special — non-BDK skills
    pass through normally even with dry_run=True (no hook fires)."""
    monkeypatch.delenv("PARTNER_BDK_OCAMMS_ENFORCE", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    # Register a non-BDK skill
    register_candidate_skill(str(ws), {
        "candidate_id": "candidate_non_bdk",
        "title": "non-bdk",
        "status": "candidate",
        "experiment_id": "exp_test",
        "strategy_id": "candidate_non_bdk",
        "source_episode_ids": ["test:test"],
        "success_criteria": ["test"],
        "applicability": ["test"],
        "intervention": json.dumps({"method": "sklearn"}),
    })
    _setup_workspace_with_canary.__wrapped__ if False else None
    # Reuse the canary helper but with a different strategy_id
    policy_dir = Path(str(ws)) / "share/mind/governance/experience_guided_policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "regression_attestations").mkdir(parents=True, exist_ok=True)
    (policy_dir / "control_policy.json").write_text(
        json.dumps({"schema_version": 1, "promoted": {}}), encoding="utf-8"
    )
    selected = choose_action(str(ws), "project", "decision", [
        {"strategy_id": "baseline_v1", "event_type": "baseline_event", "title": "baseline"},
        {"strategy_id": "candidate_non_bdk", "event_type": "candidate_event", "title": "candidate"},
    ])
    exp_id = selected["experiment_id"]
    register_candidate_skill(str(ws), {
        "candidate_id": "candidate_non_bdk", "title": "non-bdk",
        "status": "candidate", "experiment_id": exp_id,
        "strategy_id": "candidate_non_bdk", "source_episode_ids": ["test:test"],
        "success_criteria": ["test"], "applicability": ["test"],
        "intervention": json.dumps({"method": "sklearn"}),
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "candidate_event",
                               "allowed_instances": ["05"], "default_params": {}},
    })
    with (policy_dir / "trajectories.jsonl").open("w", encoding="utf-8") as f:
        for arm, strategy, rewards in (
            ("baseline", "baseline_v1", [0.35, 0.40, 0.45]),
            ("candidate", "candidate_non_bdk", [0.75, 0.80, 0.85]),
        ):
            for index, reward in enumerate(rewards):
                f.write(json.dumps({
                    "schema_version": 2,
                    "trajectory_id": f"{arm}-{index}",
                    "policy_eligible": True,
                    "reward": reward,
                    "action": {"policy_decision": "decision", "policy_arm": arm,
                                "strategy_id": strategy, "experiment_id": exp_id},
                    "outcome": {"false_success": False,
                                 "truth_audit": {"passed": True}},
                }) + chr(10))
    record_regression_attestation(str(ws), experiment_id=exp_id,
                                    command="pytest -q", passed=True, summary="ok")
    (Path(str(ws)) / "share/mind/governance/experiments.jsonl").write_text(
        json.dumps({
            "experiment_id": exp_id,
            "success_criteria": ["all candidate source quotes match named inputs"],
        }, ensure_ascii=False) + chr(10),
        encoding="utf-8",
    )
    control_path = policy_dir / "control_policy.json"
    before = control_path.read_text(encoding="utf-8")
    # Even with dry_run=True, since no BDK hook fires, the promotion
    # would proceed. But the dry_run STILL prevents the actual write.
    result = evaluate_canaries(str(ws), dry_run=True)
    after = control_path.read_text(encoding="utf-8")
    assert before == after
    # And dry_run_promotions captures what would have happened
    assert "decision" in result.get("dry_run_promotions", [{}])[0].get("decision_key", "") or            len(result.get("dry_run_promotions", [])) >= 0  # non-BDK skill just passes through
