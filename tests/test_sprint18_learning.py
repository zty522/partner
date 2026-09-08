import json
from pathlib import Path

from partner.governance.sprint18_learning import (
    MIN_EXPLORATION_PROBABILITY,
    choose_repair_recipe,
    ingest_learning_observations,
    rank_learning_topics,
    robustness_event_to_observation,
    run_sprint18_learning_cycle,
    trajectory_to_observation,
    uncertainty_diagnostic_event_to_observation,
    update_learning_policy,
)
from partner.v2 import get_all_events


def _trajectory(identity: str, *, completed: bool = True, delivered: bool = True,
                business: bool = True, mechanism: str = "", strategy: str = "candidate",
                monitor_only: bool = False) -> dict:
    return {
        "trajectory_id": identity,
        "episode_id": f"episode_{identity}",
        "work_item_id": f"work_{identity}",
        "project_id": "real_project",
        "instance_id": "04",
        "state": {"receipt_id": f"receipt_{identity}" if delivered else "",
                  "delivery_confirmed": delivered, "source_families": ["github", "paper"]},
        "action": {"action_key": "04:manual:research", "strategy_id": strategy,
                   "policy_arm": strategy, "match_key": "2026-09-01:real_project"},
        "outcome": {
            "status": "completed" if completed else "failed",
            "artifacts": [f"/evidence/{identity}.json"],
            "business_progress": business,
            "false_success": not completed,
            "failure_owner": "runtime" if not completed else "",
            "failure_mechanism": mechanism,
            "monitor_only": monitor_only,
            "truth_audit": {"passed": completed},
        },
        "reward": 0.8 if completed else -0.5,
        "reward_components": {"business_progress": .45 if business else 0.0},
        "created_at": "2026-09-01T12:00:00+08:00",
    }


def _seed(workspace: Path, rows: list[dict]) -> None:
    path = workspace / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_learning_and_promotion_gates_are_separate():
    promoted = trajectory_to_observation(_trajectory("good"))
    negative = trajectory_to_observation(
        _trajectory("bad", completed=False, delivered=False, business=False,
                    mechanism="claim_level_truth_gate"))
    partial = trajectory_to_observation(_trajectory("partial", delivered=False))
    assert promoted["learning_eligible"] is True
    assert promoted["promotion_eligible"] is True
    assert negative["learning_eligible"] is True
    assert negative["promotion_eligible"] is False
    assert negative["learning_polarity"] == "negative"
    assert partial["learning_eligible"] is True
    assert partial["promotion_eligible"] is False


def test_ingestion_is_idempotent(tmp_path: Path):
    _seed(tmp_path, [_trajectory("a"), _trajectory("b", completed=False,
                                                        mechanism="delivery_ack_missing")])
    first = ingest_learning_observations(str(tmp_path))
    second = ingest_learning_observations(str(tmp_path))
    assert first["added"] == 2
    assert second["added"] == 0
    assert second["learning_eligible"] == 2
    assert second["promotion_eligible"] == 1


def test_topic_ranking_uses_failure_signal_and_repetition_penalty(tmp_path: Path):
    _seed(tmp_path, [
        _trajectory("a", completed=False, business=False, mechanism="claim_level_truth_gate"),
        _trajectory("b", completed=True),
    ])
    ingest_learning_observations(str(tmp_path))
    before = rank_learning_topics(str(tmp_path))
    claim = next(row for row in before if row["topic_key"] == "repair:claim_level_truth_gate")
    assert claim["failures"] == 1
    selections = tmp_path / "share/mind/governance/active_learning/sprint18/topic_selections.jsonl"
    selections.write_text("".join(json.dumps({"topic_key": claim["topic_key"],
                                              "evidence_digest": f"new_{index}"}) + "\n"
                                  for index in range(3)), encoding="utf-8")
    after = rank_learning_topics(str(tmp_path))
    repeated = next(row for row in after if row["topic_key"] == claim["topic_key"])
    assert repeated["acquisition_score"] < claim["acquisition_score"]
    assert "repeated_without_new_evidence" in repeated["critic"]["reasons"]


def test_monitor_only_probe_is_learnable_but_never_becomes_curriculum_topic(tmp_path: Path):
    _seed(tmp_path, [
        _trajectory("probe", completed=True, business=False,
                    mechanism="user_input/expected_missing_input", monitor_only=True),
        _trajectory("failure", completed=False, business=False,
                    mechanism="typed_output_reference"),
    ])
    ingest_learning_observations(str(tmp_path))
    topics = rank_learning_topics(str(tmp_path))
    assert all("expected_missing_input" not in row["topic_key"] for row in topics)
    assert any(row["topic_key"] == "repair:typed_output_reference" for row in topics)


def test_explicitly_ineligible_campaign_monitor_correction_updates_observation(tmp_path: Path):
    original = _trajectory("campaign-monitor", completed=False, delivered=False,
                           business=False, monitor_only=True)
    original["learning_observation_eligible"] = True
    _seed(tmp_path, [original])
    first = ingest_learning_observations(str(tmp_path))
    assert first["learning_eligible"] == 1

    corrected = json.loads(json.dumps(original))
    corrected["revision"] = 2
    corrected["outcome"]["status"] = "completed"
    corrected["outcome"]["false_success"] = False
    corrected["outcome"]["truth_audit"] = {}
    corrected["learning_observation_eligible"] = False
    path = tmp_path / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(corrected) + "\n")
    second = ingest_learning_observations(str(tmp_path))
    assert second["added"] == 1
    assert second["learning_eligible"] == 0
    assert second["positive"] == 0
    assert second["negative"] == 0


def test_recipe_is_mechanism_specific():
    assert choose_repair_recipe("repair:claim_level_truth_gate")["recipe_id"] == "recipe_claim_ledger_owner_v1"
    assert choose_repair_recipe("repair:typed_output_reference")["recipe_id"] == "recipe_typed_output_reference_v1"
    assert (choose_repair_recipe("repair:active_learning.targetdiff.acquisition_not_robust")["recipe_id"]
            == "recipe_uncertainty_calibration_probe_v1")
    assert (choose_repair_recipe("repair:active_learning.targetdiff.uncertainty_miscalibration")["recipe_id"]
            == "recipe_uncertainty_estimator_comparison_v1")
    assert choose_repair_recipe("repair:unknown")["recipe_id"] == "recipe_diagnostic_probe_v1"


def test_rejected_robustness_event_is_truthful_negative_learning_observation():
    observation = robustness_event_to_observation({
        "event_id": "evoevt_robust", "event_hash": "abc",
        "event_type": "active_learning/robustness_evaluated",
        "occurred_at": "2026-09-01T20:00:00+08:00",
        "project_id": "molecular_generation", "subject_id": "robust_v1",
        "payload": {"decision": "rejected_not_robust"},
        "evidence_refs": ["/evidence/robust.json"],
    })
    assert observation["truth_passed"] is True
    assert observation["learning_polarity"] == "negative"
    assert observation["learning_eligible"] is True
    assert observation["promotion_eligible"] is False
    assert observation["mechanism"] == "active_learning.targetdiff.acquisition_not_robust"


def test_uncertainty_diagnostic_becomes_learning_not_promotion_evidence():
    observation = uncertainty_diagnostic_event_to_observation({
        "event_id": "evoevt_uncertainty", "event_hash": "def",
        "event_type": "active_learning/uncertainty_diagnostic",
        "occurred_at": "2026-09-01T22:00:00+08:00",
        "project_id": "molecular_generation", "subject_id": "diag_v1",
        "payload": {"decision": "uncertainty_weak_or_miscalibrated"},
        "evidence_refs": ["/evidence/diagnostic.json"],
    })
    assert observation["truth_passed"] is True
    assert observation["learning_polarity"] == "negative"
    assert observation["learning_eligible"] is True
    assert observation["promotion_eligible"] is False
    assert observation["mechanism"] == "active_learning.targetdiff.uncertainty_miscalibration"


def test_policy_learns_from_positive_and_negative_samples(tmp_path: Path):
    _seed(tmp_path, [
        _trajectory("a", strategy="baseline"),
        _trajectory("b", completed=False, business=False,
                    mechanism="claim_level_truth_gate", strategy="baseline"),
        _trajectory("c", strategy="candidate"),
    ])
    ingest_learning_observations(str(tmp_path))
    policy = update_learning_policy(str(tmp_path))["policy"]
    assert policy["observation_count"] == 3
    assert any(value["failures"] == 1 for value in policy["posteriors"].values())
    for probabilities in policy["selection_probabilities"].values():
        if len(probabilities) > 1:
            assert min(probabilities.values()) >= MIN_EXPLORATION_PROBABILITY


def test_full_cycle_creates_a_bounded_candidate_without_production_mutation(tmp_path: Path):
    _seed(tmp_path, [
        _trajectory("a", completed=False, business=False, mechanism="claim_level_truth_gate"),
        _trajectory("b", completed=False, business=False, mechanism="claim_level_truth_gate"),
        _trajectory("c"),
    ])
    result = run_sprint18_learning_cycle(str(tmp_path))
    assert result["status"] == "candidate_ready"
    assert result["candidate"]["candidate"]["recipe"]["recipe_id"] == "recipe_claim_ledger_owner_v1"
    assert result["production_mutation"] is False
    assert result["candidate"]["production_effective"] is False
    assert Path(result["candidate"]["path"]).is_file()


def test_sprint18_events_are_registered():
    names = {row[0] for row in get_all_events()}
    assert {"learning_observation_ingest", "learning_topic_select",
            "learning_candidate_propose", "learning_policy_update",
            "sprint18_learning_cycle", "targetdiff_active_learning",
            "targetdiff_active_robustness",
            "targetdiff_uncertainty_diagnostic",
            "targetdiff_uncertainty_candidate"} <= names
