from __future__ import annotations

import json
from pathlib import Path

from partner.governance.active_learning import (
    observe_manual_failure,
    run_manual_learning_matched_experiment,
)
from partner.v2.active_learning_events import atomic_agent_active_learning_manual_failure_matched
from partner.governance.episode_trace import apply_episode_trajectory_correction, reward_vector
from partner.governance.experience_policy import (
    append_expected_observation_correction,
    append_learning_success_correction,
    append_trajectory_correction,
)
from partner.governance.storage import append_jsonl
from partner.mind.claim_ledger import audit_claim_artifacts


def _claim_report(path: Path, source: Path, *, claim: str, axis: str, quote: str) -> None:
    path.write_text(
        "claim_id: c1\n"
        f"claim_text: {claim}\n"
        f"claim_axes: {axis}\n"
        f"source_path: {source}\n"
        f"source_identity: {source.name}\n"
        f"evidence_quote: {quote}\n"
        "support_type: direct\n"
        "rationale: grounded comparison\n",
        encoding="utf-8",
    )


def test_claim_gate_accepts_supported_and_rejects_semantic_confusion(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    quote = "The runtime records every tool event and retry in an append-only event log."
    source.write_text(f"# Heading\n{quote}\n", encoding="utf-8")
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    _claim_report(good, source, claim="runtime records every tool event in an event log",
                  axis="event_recording", quote=quote)
    _claim_report(bad, source, claim="runtime automatically recovers every failed task",
                  axis="failure_recovery", quote=quote)

    assert audit_claim_artifacts([str(good)], named_input_sources=[str(source)])["passed"] is True
    bad_audit = audit_claim_artifacts([str(bad)], named_input_sources=[str(source)])
    assert bad_audit["passed"] is False
    assert bad_audit["semantically_unsupported"] == ["c1"]


def test_claim_gate_rejects_heading_and_cross_source_swap(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    other = tmp_path / "other.md"
    source.write_text("# Harness Architecture\nreal event recording details\n", encoding="utf-8")
    other.write_text("failure recovery retries a task once\n", encoding="utf-8")
    report = tmp_path / "report.md"
    _claim_report(report, other, claim="event recording exists", axis="event_recording",
                  quote="failure recovery retries a task once")
    audit = audit_claim_artifacts([str(report)], named_input_sources=[str(source)])
    assert audit["passed"] is False
    assert audit["cross_source_swaps"] == ["c1"]


def test_claim_gate_rejects_ledger_truncated_mid_block(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("A complete source sentence about runtime feedback.\n", encoding="utf-8")
    report = tmp_path / "truncated.md"
    report.write_text(
        "claim_id: c_truncated\n"
        "claim_text: runtime feedback improves the next task\n",
        encoding="utf-8",
    )

    audit = audit_claim_artifacts([str(report)], named_input_sources=[str(source)])

    assert audit["passed"] is False
    assert audit["failed_claims"] == ["c_truncated"]
    assert audit["incomplete_claims"] == ["c_truncated"]


def test_failed_trajectory_cannot_receive_truth_or_policy_reward() -> None:
    trajectory = {
        "outcome": {"status": "failed", "false_success": False,
                    "business_progress": False, "truth_audit": {}},
        "state": {"delivery_confirmed": True},
    }
    reduced = {"status": "failed", "failure_classes": ["tool.generate_pdf.failed"],
               "tool_calls": [], "model_calls": [], "conversation_items": [],
               "delivery": {"confirmed": True}}
    reward = reward_vector(trajectory, reduced)
    assert reward["values"]["truth"] == 0.0
    assert reward["policy_eligible"] is False
    assert reward["scalar"] == 0.0


def test_manual_failure_is_automatically_observed_and_proposed(tmp_path: Path) -> None:
    state_dir = tmp_path / "share/mind/governance/episodes/episode_x"
    state_dir.mkdir(parents=True)
    state = {
        "episode_id": "episode_x", "instance_id": "04", "status": "failed",
        "failure_classes": ["planning.output_reference_contract/typed_reference_unresolved"],
        "failure_details": [{
            "class": "planning.output_reference_contract/typed_reference_unresolved",
            "mechanism": "planning.output_reference_contract/typed_reference_unresolved",
        }],
        "source_refs": [],
    }
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    result = observe_manual_failure(
        str(tmp_path), instance_id="04", task_id="task_x",
        reduced={"ok": True, "bundle": str(state_dir), "state": state},
    )
    assert result["ok"] is True
    assert result["proposal"]["intervention"] == "typed_output_reference_resolution_v1"
    assert result["proposal"]["production_mutation"] is False
    assert result["diagnosis"]["diagnosis"]["episode_count"] == 1
    assert Path(result["path"]).is_file()


def test_failed_episode_without_tool_class_still_gets_bounded_outcome_proposal(tmp_path: Path) -> None:
    state_dir = tmp_path / "share/mind/governance/episodes/episode_restart"
    state_dir.mkdir(parents=True)
    state = {
        "episode_id": "episode_restart", "instance_id": "04", "status": "failed",
        "failure_classes": [], "failure_details": [], "source_refs": [],
    }
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    result = observe_manual_failure(
        str(tmp_path), instance_id="04", task_id="task_restart",
        reduced={"ok": True, "bundle": str(state_dir), "state": state},
    )

    assert result["ok"] is True, result
    assert result["proposal"]["failure_class"] == "outcome.no_business_progress"
    assert result["proposal"]["production_mutation"] is False


def test_matched_learning_experiment_closes_feedback_loop(tmp_path: Path) -> None:
    result = run_manual_learning_matched_experiment(
        str(tmp_path), experiment_id="experiment_test_manual_learning",
    )
    assert result["ok"] is True
    assert all(result["experiment"]["gates"].values())
    assert result["feedback"]["status"] == "feedback_recorded"
    assert result["decision"]["production_effective"] is False
    replay = run_manual_learning_matched_experiment(
        str(tmp_path), experiment_id="experiment_test_manual_learning",
    )
    assert replay["status"] == "idempotent_experiment_replay"


def test_native_matched_experiment_is_bound_to_failed_episode_project(tmp_path: Path) -> None:
    config = tmp_path / "config/partner_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"runtime": {"instance_native_autonomy": True,
        "instance_native_enabled_instances": ["04"]}}), encoding="utf-8")
    episode = tmp_path / "share/mind/governance/episodes/episode_native/state.json"
    episode.parent.mkdir(parents=True)
    episode.write_text(json.dumps({"episode_id": "episode_native", "instance_id": "04",
        "failure_classes": ["project/missing_external_action"]}), encoding="utf-8")
    result = run_manual_learning_matched_experiment(
        str(tmp_path), experiment_id="native_episode_native")
    assert result["ok"] is True
    matched = result["experiment"]["mechanism_match"]
    assert matched["project_id"] == "literature_github_learning"
    assert matched["candidate_event_types"] == ["external_knowledge_scout"]
    assert matched["passed"] is True


def test_native_matched_experiment_rejects_empty_unclassified_failure(tmp_path: Path) -> None:
    config = tmp_path / "config/partner_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"runtime": {"instance_native_autonomy": True,
        "instance_native_enabled_instances": ["04"]}}), encoding="utf-8")
    episode = tmp_path / "share/mind/governance/episodes/episode_empty/state.json"
    episode.parent.mkdir(parents=True)
    episode.write_text(json.dumps({"episode_id": "episode_empty", "instance_id": "04",
        "task_id": "missing-task-log", "status": "failed", "failure_classes": []}), encoding="utf-8")
    result = run_manual_learning_matched_experiment(
        str(tmp_path), experiment_id="native_episode_empty_mechanism_v2")
    assert result["ok"] is False
    matched = result["experiment"]["mechanism_match"]
    assert matched["failure_classes"] == []
    assert matched["candidate_binding"] == "unbound_failure_mechanism"
    assert matched["passed"] is False


def test_native_matched_experiment_recovers_typed_acceptance_failure_from_log(tmp_path: Path) -> None:
    config = tmp_path / "config/partner_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"runtime": {"instance_native_autonomy": True,
        "instance_native_enabled_instances": ["04"]}}), encoding="utf-8")
    episode = tmp_path / "share/mind/governance/episodes/episode_legacy/state.json"
    episode.parent.mkdir(parents=True)
    episode.write_text(json.dumps({"episode_id": "episode_legacy", "instance_id": "04",
        "task_id": "legacy-task", "status": "failed", "failure_classes": []}), encoding="utf-8")
    task_log = tmp_path / "instances/04/state/tasks/legacy-task/task_log.jsonl"
    task_log.parent.mkdir(parents=True)
    task_log.write_text(json.dumps({"event": "iteration_check",
        "missing": ["named_artifact:continuation.md"]}) + "\n", encoding="utf-8")
    result = run_manual_learning_matched_experiment(
        str(tmp_path), experiment_id="native_episode_legacy_mechanism_v2")
    assert result["ok"] is True
    matched = result["experiment"]["mechanism_match"]
    assert matched["failure_classes"] == [
        "verification.acceptance_contract/implicit_handoff_artifact"]
    assert matched["passed"] is True


def test_rejected_candidate_is_successful_learning_event_terminal(tmp_path: Path) -> None:
    episode = tmp_path / "share/mind/governance/episodes/episode_empty/state.json"
    episode.parent.mkdir(parents=True)
    episode.write_text(json.dumps({"episode_id": "episode_empty", "instance_id": "04",
        "task_id": "no-log", "status": "failed", "failure_classes": []}), encoding="utf-8")
    ctx = type("Ctx", (), {"workspace": str(tmp_path)})()
    result = atomic_agent_active_learning_manual_failure_matched(
        ctx, {"experiment_id": "native_episode_empty_event_semantics"})
    assert result["ok"] is True
    assert result["status"] == "matched_experiment_recorded"
    assert result["experiment_verdict"] == "rejected"
    assert result["candidate_validated"] is False
    assert result["production_effective"] is False


def test_repeated_result_match_fails_closed_without_source_action(tmp_path: Path) -> None:
    config = tmp_path / "config/partner_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"runtime": {"instance_native_autonomy": True,
        "instance_native_enabled_instances": ["02"]}}), encoding="utf-8")
    episode = tmp_path / "share/mind/governance/episodes/episode_repeat/state.json"
    episode.parent.mkdir(parents=True)
    episode.write_text(json.dumps({"episode_id": "episode_repeat", "instance_id": "02",
        "task_id": "missing-source-action", "status": "failed",
        "failure_classes": ["project/repeated_findings"]}), encoding="utf-8")
    result = run_manual_learning_matched_experiment(
        str(tmp_path), experiment_id="native_episode_repeat")
    matched = result["experiment"]["mechanism_match"]
    assert result["ok"] is False
    assert matched["candidate_changes_action"] is False
    assert matched["passed"] is False


def test_reward_correction_is_append_only_and_attributable(tmp_path: Path) -> None:
    path = tmp_path / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    append_jsonl(path, {
        "schema_version": 2, "trajectory_id": "traj_x", "instance_id": "04",
        "kind": "manual_project_iteration", "action": {"action_key": "04:manual:generic"},
        "outcome": {"status": "failed", "artifacts": []}, "reward": -0.45,
        "reward_components": {"accepted_completed": 0.05, "artifact_contract": 0.05},
    })
    result = append_trajectory_correction(
        str(tmp_path), trajectory_id="traj_x", failure_owner="output_reference",
        failure_mechanism="planning.output_reference_contract/typed_reference_unresolved",
        partial_artifacts=["partial.md"], evidence_refs=["episode/state.json"],
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert result["ok"] is True
    assert len(rows) == 2
    assert rows[0]["schema_version"] == 2
    assert rows[1]["reward_components"]["accepted_completed"] == 0.0
    assert rows[1]["reward_components"]["artifact_contract"] == 0.0
    assert rows[1]["reward_components"]["partial_artifact"] == 0.05
    assert rows[1]["learning_observation_eligible"] is True
    episode = tmp_path / "share/mind/governance/episodes/episode_x/state.json"
    episode.parent.mkdir(parents=True)
    episode.write_text(json.dumps({"status": "failed", "failure_classes": []}), encoding="utf-8")
    projection = apply_episode_trajectory_correction(
        str(tmp_path), episode_id="episode_x", trajectory=rows[1],
    )
    assert projection["state"]["reward_vector"]["values"]["truth"] == 0.0
    assert projection["state"]["failure_classes"] == [
        "planning.output_reference_contract/typed_reference_unresolved"
    ]


def test_expected_observation_correction_is_append_only_and_reward_neutral(tmp_path: Path) -> None:
    path = tmp_path / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    append_jsonl(path, {
        "schema_version": 3, "trajectory_id": "traj_probe", "instance_id": "04",
        "kind": "manual_project_iteration", "action": {"action_key": "04:manual:inspect"},
        "outcome": {"status": "completed", "monitor_only": False}, "reward": -0.45,
        "reward_components": {"accepted_completed": 0.05, "delivery_contract": 0.05},
    })
    result = append_expected_observation_correction(
        str(tmp_path), trajectory_id="traj_probe", evidence_refs=["task_log.jsonl"],
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert result["ok"] is True
    assert len(rows) == 2
    assert rows[0]["reward"] == -0.45
    assert rows[1]["revision"] == 2
    assert rows[1]["reward"] == 0.0
    assert rows[1]["outcome"]["monitor_only"] is True
    assert rows[1]["policy_eligible"] is False
    assert rows[1]["learning_observation_eligible"] is False


def test_learning_success_correction_requires_real_durable_evidence(tmp_path: Path) -> None:
    path = tmp_path / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    append_jsonl(path, {
        "schema_version": 3, "trajectory_id": "traj_learning", "instance_id": "05",
        "kind": "manual_project_iteration",
        "state": {"delivery_confirmed": True, "receipt_id": "receipt_x"},
        "action": {"action_key": "05:manual_project_iteration:atomic_inspect_file"},
        "outcome": {"status": "completed", "evidence_refs": [],
                    "learning_progress": False, "business_progress": False},
        "reward": -0.45, "reward_components": {"accepted_completed": 0.05},
    })
    evidence = tmp_path / "share/mind/governance/active_learning/experiments/e1/result.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"passed": true}', encoding="utf-8")

    result = append_learning_success_correction(
        str(tmp_path), trajectory_id="traj_learning", evidence_refs=[str(evidence)],
        learning_mechanism="agent_active_learning_manual_failure_matched",
    )
    replay = append_learning_success_correction(
        str(tmp_path), trajectory_id="traj_learning", evidence_refs=[str(evidence)],
        learning_mechanism="agent_active_learning_manual_failure_matched",
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert result["status"] == "learning_success_correction_appended"
    assert replay["status"] == "idempotent_learning_success_correction"
    assert len(rows) == 2
    assert rows[0]["reward"] == -0.45
    assert rows[1]["revision"] == 2
    assert rows[1]["outcome"]["learning_progress"] is True
    assert rows[1]["outcome"]["evidence_refs"] == [str(evidence)]
    assert rows[1]["reward"] == 0.55
    assert rows[1]["policy_eligible"] is False
    assert rows[1]["learning_observation_eligible"] is True
