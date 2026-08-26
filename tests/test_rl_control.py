import json
from pathlib import Path

from partner.governance.rl_control import (
    assign_manual_canary,
    choose_action,
    evaluate_canaries,
    record_regression_attestation,
    start_manual_truth_canary,
)
from partner.governance.storage import append_jsonl, governance_log


CHOICES = [
    {"strategy_id": "baseline", "event_type": "baseline_event", "title": "baseline"},
    {"strategy_id": "candidate", "event_type": "candidate_event", "title": "candidate"},
]


def test_unpromoted_policy_alternates_candidate_and_baseline(tmp_path):
    root = str(tmp_path / "workspace")
    first = choose_action(root, "project", "decision", CHOICES)
    second = choose_action(root, "project", "decision", CHOICES)
    assert (first["strategy_id"], first["policy_arm"]) == ("candidate", "candidate")
    assert (second["strategy_id"], second["policy_arm"]) == ("baseline", "baseline")
    assert first["experiment_id"] and second["experiment_id"] == first["experiment_id"]


def test_verified_canary_promotes_candidate_into_control_policy(tmp_path):
    root = str(tmp_path / "workspace")
    selected = choose_action(root, "project", "decision", CHOICES)
    assert selected["policy_arm"] == "candidate"
    trajectory_path = Path(root) / "share/mind/governance/rl/trajectories.jsonl"
    for arm, strategy, rewards in (
        ("baseline", "baseline", [0.35, 0.40, 0.45]),
        ("candidate", "candidate", [0.75, 0.80, 0.85]),
    ):
        for index, reward in enumerate(rewards):
            append_jsonl(trajectory_path, {
                "schema_version": 2,
                "trajectory_id": f"{arm}-{index}",
                "policy_eligible": True,
                "reward": reward,
                "action": {
                    "policy_decision": "decision", "policy_arm": arm,
                    "strategy_id": strategy, "experiment_id": selected["experiment_id"],
                },
            })
    pending = evaluate_canaries(root)
    assert pending["decisions"] == []
    record_regression_attestation(
        root, experiment_id=selected["experiment_id"], command="pytest -q",
        passed=True, summary="all tests passed",
    )
    result = evaluate_canaries(root)
    assert result["decisions"][0]["decision"] == "promoted"
    control = json.loads(Path(result["control_policy_path"]).read_text(encoding="utf-8"))
    assert control["promoted"]["decision"] == "candidate"
    rows = [json.loads(line) for line in governance_log(root, "promotion_decisions").read_text(
        encoding="utf-8"
    ).splitlines()]
    assert rows[-1]["decision"] == "promoted"
    production = choose_action(root, "project", "decision", CHOICES)
    assert production["strategy_id"] == "candidate"
    assert production["policy_arm"] == "production"


def test_manual_canary_assignment_alternates_and_is_experiment_bound(tmp_path):
    root = str(tmp_path / "workspace")
    started = start_manual_truth_canary(root, "literature_github_learning")
    experiment_id = started["experiment"]["experiment_id"]
    first = assign_manual_canary(
        root, project_id="literature_github_learning", experiment_id=experiment_id,
        decision_key=started["decision_key"],
    )
    second = assign_manual_canary(
        root, project_id="literature_github_learning", experiment_id=experiment_id,
        decision_key=started["decision_key"],
    )
    assert first["policy_arm"] == "candidate"
    assert "source_path" in first["requirement"]
    assert second["policy_arm"] == "baseline"
    assert second["requirement"] == ""
