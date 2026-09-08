import json
from pathlib import Path

from partner.governance.production_readiness import (
    assess_general_llm,
    assess_longitudinal_policy_learning,
    assess_production_readiness,
    assess_sustained_business,
    verify_readiness_attestation,
)


def _llm_result(path: Path, provider: str, model: str) -> str:
    value = {
        "model_contract": {"provider": provider, "model": model,
                           "local_deterministic": False},
        "criteria": {"truth": True, "safety": True},
        "metrics": {"mean_reward_delta": .4},
        "pairs": [{"task_id": f"task-{index}", "external_payload_audits": {
            "baseline": {"violations": []}, "candidate": {"violations": []}}}
                  for index in range(3)],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def _trajectory(index: int, arm: str, artifact: Path) -> dict:
    success = arm == "candidate" or index % 4
    reward = .85 if arm == "candidate" else (.5 if success else 0)
    return {
        "schema_version": 3, "trajectory_id": f"traj_{arm}_{index}",
        "project_id": "p1" if index % 2 else "p2",
        "created_at": f"2026-08-{20 + index % 3:02d}T00:00:00+08:00",
        "state": {"delivery_confirmed": arm == "candidate"},
        "action": {"strategy_id": "candidate_ready" if arm == "candidate" else "baseline",
                   "experiment_id": "experiment_ready", "policy_arm": arm},
        "outcome": {"status": "completed" if success else "failed",
                    "business_progress": bool(success), "false_success": False,
                    "artifacts": [str(artifact)] if arm == "candidate" else [],
                    "outcome_fingerprint": f"fp_{arm}_{index}"},
        "reward": reward,
    }


def test_general_llm_requires_two_external_families(tmp_path):
    one = _llm_result(tmp_path / "one.json", "provider-a", "model-a")
    assert assess_general_llm([one])["ok"] is False
    two = _llm_result(tmp_path / "two.json", "provider-b", "model-b")
    assert assess_general_llm([one, two])["ok"] is True


def test_single_authorized_model_requires_two_independent_experiments(tmp_path):
    one = _llm_result(tmp_path / "one.json", "minimax", "m3")
    two = _llm_result(tmp_path / "two.json", "minimax", "m3")
    assert assess_general_llm(
        [one], min_model_families=1, min_experiments_per_family=2)["ok"] is False
    result = assess_general_llm(
        [one, two], min_model_families=1, min_experiments_per_family=2)
    assert result["ok"] is True
    assert result["scope"] == "single_authorized_general_purpose_model"


def test_business_and_longitudinal_gates_require_real_diversity(tmp_path):
    artifact = tmp_path / "report.md"
    artifact.write_text("real grounded report\n" * 30, encoding="utf-8")
    rows = [_trajectory(index, arm, artifact)
            for arm in ("baseline", "candidate") for index in range(24)]
    assert assess_sustained_business(rows, candidate_id="candidate_ready")["ok"] is True
    rl = assess_longitudinal_policy_learning(rows, candidate_id="candidate_ready",
                                rollback_drill_passed=True)
    assert rl["ok"] is False
    assert rl["checks"]["negative_samples_in_both_arms"] is False


def test_readiness_counts_only_latest_append_only_trajectory_revision(tmp_path):
    artifact = tmp_path / "report.md"
    artifact.write_text("grounded\n" * 50, encoding="utf-8")
    baseline = _trajectory(1, "baseline", artifact)
    candidate = _trajectory(1, "candidate", artifact)
    stale = dict(candidate)
    stale["outcome"] = dict(candidate["outcome"], false_success=True)
    stale["reward"] = -0.4
    corrected = dict(candidate)
    corrected["revision"] = 2
    result = assess_longitudinal_policy_learning(
        [baseline, stale, corrected], candidate_id="candidate_ready",
        rollback_drill_passed=False, min_samples_per_arm=1,
    )
    experiment = result["best_experiment"]
    assert experiment["baseline_samples"] == 1
    assert experiment["candidate_samples"] == 1
    assert experiment["candidate_false_success"] == 0
    assert experiment["candidate_mean"] == .85


def test_readiness_attestation_fails_closed_and_detects_tamper(tmp_path):
    result = assess_production_readiness(
        str(tmp_path), candidate_id="candidate_missing",
        llm_experiment_paths=[], write_attestation=True)
    assert result["production_ready"] is False
    ok, reason, _ = verify_readiness_attestation(
        result["path"], workspace=str(tmp_path), candidate_id="candidate_missing")
    assert ok is False and "not passed" in reason
    data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    data["production_ready"] = True
    Path(result["path"]).write_text(json.dumps(data), encoding="utf-8")
    ok, reason, _ = verify_readiness_attestation(
        result["path"], workspace=str(tmp_path), candidate_id="candidate_missing")
    assert ok is False and "digest mismatch" in reason
