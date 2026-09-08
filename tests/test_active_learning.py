from __future__ import annotations

import json
from pathlib import Path

from partner.cognition.active_learning import (
    ActiveLearningMemory,
    ActiveLearningOption,
    expected_information_gain,
    rank_active_learning_options,
)
from partner.cognition.world_model import WorldModelEngine
from partner.governance.active_learning import (
    diagnose_agent_failure,
    evaluate_skipped_terminal_repair,
    run_skipped_terminal_fresh_canary,
    run_handoff_intent_fresh_canary,
    run_preflight_input_manifest_fresh_canary,
    refine_failure_taxonomy,
    record_agent_experiment_feedback,
    select_agent_experiment,
)
from partner.governance.evolution_events import verify_evolution_ledger
from partner.v2 import get_all_events


def _episode(root: Path, index: int, failure: str) -> Path:
    path = root / "share/mind/governance/episodes" / f"episode_{index}" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 3, "episode_id": f"episode_{index}",
        "instance_id": "03", "project_id": "partner_framework_frontend",
        "status": "failed", "failure_classes": [failure],
        "reward_vector": {"scalar": 0.0, "policy_eligible": False},
    }), encoding="utf-8")
    return path


def test_information_gain_prefers_an_experiment_that_distinguishes_hypotheses():
    prior = {"systematic": .5, "transient": .5}
    informative = {"systematic": {"yes": .9, "no": .1},
                   "transient": {"yes": .1, "no": .9}}
    uninformative = {"systematic": {"yes": .5, "no": .5},
                     "transient": {"yes": .5, "no": .5}}
    assert expected_information_gain(prior, informative) > 0.0
    assert expected_information_gain(prior, uninformative) == 0.0
    ranked = rank_active_learning_options(prior, [
        ActiveLearningOption("uninformative", "noop", uninformative),
        ActiveLearningOption("informative", "inspect", informative),
    ])
    assert ranked[0]["option_id"] == "informative"


def test_active_learning_memory_changes_future_success_estimate_without_overwrite():
    memory = ActiveLearningMemory()
    assert memory.success_probability("timeout", "resample") == .5
    memory.observe("timeout", "resample", success=True)
    memory.observe("timeout", "resample", success=True)
    memory.observe("timeout", "resample", success=False)
    assert memory.success_probability("timeout", "resample") == .6
    assert memory.to_dict()["outcomes"]["timeout|resample"] == {"success": 2, "failure": 1}


def test_numeric_world_model_uses_information_value_for_next_observation():
    engine = WorldModelEngine()
    result = engine.observe([0, .15, .3, .5, .7, .85, 1],
                            [0, .8, .95, 0, -.95, -.8, 0], domain_id="active")
    query = result["next_observation"]
    assert query["acquisition_strategy"] == "gaussian_information_gain_x_coverage_v1"
    assert query["expected_information_gain"] >= 0.0


def test_agent_selector_reads_real_episode_evidence_and_only_proposes(tmp_path):
    root = tmp_path / "workspace"
    evidence = [_episode(root, index, "planning.semantic_preflight") for index in range(3)]
    result = select_agent_experiment(str(root), instance_ids=["03"])
    assert result["ok"] is True
    decision = result["decision"]
    assert decision["target_failure_class"] == "planning.semantic_preflight"
    assert decision["execution_authorized"] is False
    assert decision["selected_option"]["expected_information_gain"] > 0.0
    assert result["production_mutation"] is False
    assert verify_evolution_ledger(str(root))["ok"] is True

    feedback = record_agent_experiment_feedback(
        str(root), context_key="planning.semantic_preflight", option_id="bounded_repair",
        success=True, evidence_refs=[str(evidence[0])],
    )
    assert feedback["ok"] is True and feedback["after"] > feedback["before"]
    assert verify_evolution_ledger(str(root))["event_count"] == 2


def test_derived_no_business_progress_class_is_diagnosable_with_same_contract(tmp_path):
    root = tmp_path / "workspace"
    refs = []
    for index in range(3):
        state_path = _episode(root, index, "placeholder")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["failure_classes"] = []
        state_path.write_text(json.dumps(state), encoding="utf-8")
        trace = state_path.parent / "trace.jsonl"
        trace.write_text(json.dumps({
            "type": "plan_executor_step_started",
            "payload": {"event": "plan_executor_step_started", "step_id": "deliver",
                        "event_type": "push_files"},
        }) + "\n", encoding="utf-8")
        refs.append(str(state_path))
    selected = select_agent_experiment(str(root), instance_ids=["03"])
    assert selected["decision"]["target_failure_class"] == "outcome.no_business_progress"
    diagnosis = diagnose_agent_failure(
        str(root), failure_class="outcome.no_business_progress", evidence_refs=refs)
    assert diagnosis["diagnosis"]["episode_count"] == 3
    assert diagnosis["diagnosis"]["classification"] == "systematic_failure"


def test_outcome_diagnostic_uses_governance_reasons_not_only_unclosed_steps(tmp_path):
    root = tmp_path / "workspace"
    refs = []
    statuses = ["unlinked_previous_receipt"] * 3 + ["manual_outcome_rejected"] * 2
    for index, status in enumerate(statuses):
        state_path = _episode(root, index, "placeholder")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["failure_classes"] = []
        state_path.write_text(json.dumps(state), encoding="utf-8")
        trace = state_path.parent / "trace.jsonl"
        trace.write_text(json.dumps({
            "type": "manual_iteration_governance",
            "payload": {"event": "manual_iteration_governance", "status": status},
        }) + "\n", encoding="utf-8")
        refs.append(str(state_path))
    diagnosis = diagnose_agent_failure(
        str(root), failure_class="outcome.no_business_progress", evidence_refs=refs)
    value = diagnosis["diagnosis"]
    assert value["diagnostic_version"] == 6
    assert value["classification"] == "heterogeneous_failure_class"
    assert value["recommended_next_option"] == "split_failure_class_by_outcome_reason"
    assert value["unmatched_step_signatures"] == {
        "governance.unlinked_previous_receipt": 3,
        "governance.manual_outcome_rejected": 2,
    }


def test_preflight_diagnostic_uses_contract_errors_not_unclosed_step_types(tmp_path):
    root = tmp_path / "workspace"
    refs = []
    errors = [
        "step1: atomic_inspect_file requires path",
        "step2: evidence-dependent output must reference a dependency result, not embed a static template",
        "step3: event_type check_quality is not allowed in manual_stable",
        "file expected_artifacts require an explicit produces_artifact write step",
        "step5: atomic_inspect_file requires path",
    ]
    for index, error in enumerate(errors):
        state_path = _episode(root, index, "planning.semantic_preflight")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["failure_details"] = [{"class": "planning.semantic_preflight", "error": error}]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        (state_path.parent / "trace.jsonl").write_text("", encoding="utf-8")
        refs.append(str(state_path))
    result = diagnose_agent_failure(
        str(root), failure_class="planning.semantic_preflight", evidence_refs=refs)
    diagnosis = result["diagnosis"]
    assert diagnosis["diagnostic_version"] == 6
    assert diagnosis["unmatched_step_signatures"] == {
        "input_path_contract": 2,
        "evidence_output_contract": 1,
        "event_contract": 1,
        "artifact_contract": 1,
    }
    taxonomy = refine_failure_taxonomy(str(root), diagnosis_path=result["path"])
    assert taxonomy["ok"] is True
    assert set(taxonomy["taxonomy"]["children"]) == {
        "planning.semantic_preflight/input_path_contract",
        "planning.semantic_preflight/evidence_output_contract",
        "planning.semantic_preflight/event_contract",
        "planning.semantic_preflight/artifact_contract",
    }


def test_taxonomy_index_preserves_multiple_parent_hypothesis_spaces(tmp_path):
    root = tmp_path / "workspace"
    lifecycle_refs, outcome_refs = [], []
    for index, step_type in enumerate(
            ["generate_text", "generate_text", "create_file", "create_file",
             "push_files", "push_files"]):
        state_path = _episode(root, index, "lifecycle.unclosed_tool")
        (state_path.parent / "trace.jsonl").write_text(json.dumps({
            "type": "plan_executor_step_started", "payload": {
                "event": "plan_executor_step_started", "step_id": f"s{index}",
                "event_type": step_type}}) + "\n", encoding="utf-8")
        lifecycle_refs.append(str(state_path))
    lifecycle_diagnosis = diagnose_agent_failure(
        str(root), failure_class="lifecycle.unclosed_tool", evidence_refs=lifecycle_refs)
    refine_failure_taxonomy(str(root), diagnosis_path=lifecycle_diagnosis["path"])

    statuses = ["unlinked_previous_receipt"] * 3 + ["manual_outcome_rejected"] * 2
    for offset, status in enumerate(statuses, start=10):
        state_path = _episode(root, offset, "placeholder")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["failure_classes"] = []
        state_path.write_text(json.dumps(state), encoding="utf-8")
        (state_path.parent / "trace.jsonl").write_text(json.dumps({
            "type": "manual_iteration_governance", "payload": {
                "event": "manual_iteration_governance", "status": status}}) + "\n", encoding="utf-8")
        outcome_refs.append(str(state_path))
    outcome_diagnosis = diagnose_agent_failure(
        str(root), failure_class="outcome.no_business_progress", evidence_refs=outcome_refs)
    refine_failure_taxonomy(str(root), diagnosis_path=outcome_diagnosis["path"])

    index = json.loads((root / "share/mind/governance/active_learning/taxonomy_index.json").read_text())
    assert index["schema_version"] == 2
    assert set(index["by_parent"]) == {
        "lifecycle.unclosed_tool", "outcome.no_business_progress"}
    selected_lifecycle = select_agent_experiment(
        str(root), instance_ids=["03"], focus_failure_class="lifecycle.unclosed_tool")
    selected_outcome = select_agent_experiment(
        str(root), instance_ids=["03"], focus_failure_class="outcome.no_business_progress")
    assert selected_lifecycle["decision"]["target_failure_class"].startswith(
        "lifecycle.unclosed_tool/")
    assert selected_outcome["decision"]["target_failure_class"].startswith(
        "outcome.no_business_progress/")
    child = selected_outcome["decision"]["target_failure_class"]
    child_refs = selected_outcome["decision"]["selected_option"]["evidence_refs"]
    child_diagnosis = diagnose_agent_failure(
        str(root), failure_class=child, evidence_refs=child_refs)
    assert child_diagnosis["diagnosis"]["classification"] == "systematic_failure"
    assert child_diagnosis["diagnosis"]["episode_count"] == 3


def test_diagnostic_reads_raw_step_terminals_and_recommends_repair(tmp_path):
    root = tmp_path / "workspace"
    refs = []
    for index in range(3):
        state_path = _episode(root, index, "lifecycle.unclosed_tool")
        task = root / f"instances/03/state/tasks/task_{index}"
        task.mkdir(parents=True)
        log = task / "task_log.jsonl"
        log.write_text(json.dumps({"event": "plan_executor_step_started",
                                   "step_id": "write", "event_type": "create_file"}) + "\n",
                       encoding="utf-8")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["source_refs"] = [str(task / "task_instance.json"), str(log)]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        refs.append(str(state_path))
    result = diagnose_agent_failure(
        str(root), failure_class="lifecycle.unclosed_tool", evidence_refs=refs)
    assert result["ok"] is True
    assert result["diagnosis"]["classification"] == "systematic_failure"
    assert result["diagnosis"]["top_signature"] == "create_file"
    assert result["diagnosis"]["recommended_next_option"] == "bounded_repair"


def test_diagnostic_replays_episode_trace_when_original_task_log_was_cleaned(tmp_path):
    root = tmp_path / "workspace"
    refs = []
    for index in range(3):
        state_path = _episode(root, index, "lifecycle.unclosed_tool")
        missing_log = root / f"instances/03/state/tasks/deleted_{index}/task_log.jsonl"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["source_refs"] = [str(missing_log)]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        trace = state_path.parent / "trace.jsonl"
        trace.write_text(json.dumps({
            "type": "plan_executor_step_started",
            "payload": {"event": "plan_executor_step_started", "step_id": "push",
                        "event_type": "push_files"},
        }) + "\n", encoding="utf-8")
        refs.append(str(state_path))
    result = diagnose_agent_failure(
        str(root), failure_class="lifecycle.unclosed_tool", evidence_refs=refs)
    diagnosis = result["diagnosis"]
    assert diagnosis["evidence_coverage"] == 1.0
    assert diagnosis["evidence_source_counts"] == {"episode_trace": 3}
    assert diagnosis["classification"] == "systematic_failure"
    assert diagnosis["top_signature"] == "push_files"


def test_diagnostic_accommodates_when_one_failure_label_contains_many_mechanisms(tmp_path):
    root = tmp_path / "workspace"
    refs = []
    step_types = ["generate_text", "create_file", "push_files", "run_command", "execute_code"]
    for index, step_type in enumerate(step_types):
        state_path = _episode(root, index, "lifecycle.unclosed_tool")
        trace = state_path.parent / "trace.jsonl"
        trace.write_text(json.dumps({
            "type": "plan_executor_step_started",
            "payload": {"event": "plan_executor_step_started", "step_id": f"step_{index}",
                        "event_type": step_type},
        }) + "\n", encoding="utf-8")
        refs.append(str(state_path))
    result = diagnose_agent_failure(
        str(root), failure_class="lifecycle.unclosed_tool", evidence_refs=refs)
    diagnosis = result["diagnosis"]
    assert diagnosis["classification"] == "heterogeneous_failure_class"
    assert diagnosis["recommended_next_option"] == "split_failure_class_by_step_type"


def test_taxonomy_refinement_changes_next_query_without_rewriting_episodes(tmp_path):
    root = tmp_path / "workspace"
    refs = []
    before = {}
    step_types = ["generate_text", "generate_text", "generate_text",
                  "create_file", "push_files", "run_command", "execute_code"]
    for index, step_type in enumerate(step_types):
        state_path = _episode(root, index, "lifecycle.unclosed_tool")
        trace = state_path.parent / "trace.jsonl"
        trace.write_text(json.dumps({
            "type": "plan_executor_step_started",
            "payload": {"event": "plan_executor_step_started", "step_id": f"step_{index}",
                        "event_type": step_type},
        }) + "\n", encoding="utf-8")
        refs.append(str(state_path))
        before[str(state_path)] = state_path.read_bytes()

    diagnostic = diagnose_agent_failure(
        str(root), failure_class="lifecycle.unclosed_tool", evidence_refs=refs)
    refined = refine_failure_taxonomy(str(root), diagnosis_path=diagnostic["path"])
    assert refined["ok"] is True
    assert refined["taxonomy"]["production_effective"] is False
    assert len(refined["taxonomy"]["children"]) == 5
    assert all(Path(path).read_bytes() == content for path, content in before.items())

    next_query = select_agent_experiment(
        str(root), instance_ids=["03"], focus_failure_class="lifecycle.unclosed_tool")
    assert next_query["decision"]["target_failure_class"] == (
        "lifecycle.unclosed_tool/generate_text"
    )
    assert next_query["decision"]["failure_counts"][
        "lifecycle.unclosed_tool/generate_text"
    ] == 3
    assert "lifecycle.unclosed_tool" not in next_query["decision"]["failure_counts"]
    assert verify_evolution_ledger(str(root))["ok"] is True

    child_refs = list(next_query["decision"]["selected_option"]["evidence_refs"])
    child_diagnosis = diagnose_agent_failure(
        str(root), failure_class="lifecycle.unclosed_tool/generate_text",
        evidence_refs=child_refs,
    )
    assert child_diagnosis["diagnosis"]["classification"] == "systematic_failure"
    after_diagnosis = select_agent_experiment(
        str(root), instance_ids=["03"], focus_failure_class="lifecycle.unclosed_tool")
    selected = after_diagnosis["decision"]["selected_option"]
    assert selected["option_id"] == "bounded_repair"
    assert selected["event_type"] == "agent_active_learning_skipped_terminal_repair_shadow"
    assert selected["params"]["diagnosis_path"] == child_diagnosis["path"]
    assert selected["acquisition_score"] > 0.0
    assert after_diagnosis["decision"]["selector_version"] == 2


def test_active_learning_is_registered_as_event_first_not_direct_side_path():
    names = {row[0] for row in get_all_events()}
    assert {"agent_active_learning_select", "agent_active_learning_feedback",
            "agent_active_learning_diagnostic_shadow",
            "agent_active_learning_refine_taxonomy",
            "agent_active_learning_skipped_terminal_repair_shadow",
            "agent_active_learning_skipped_terminal_fresh_canary",
            "agent_active_learning_handoff_intent_fresh_canary",
            "agent_active_learning_preflight_manifest_fresh_canary"} <= names


def test_preflight_manifest_fresh_canary_is_feature_isolated(tmp_path):
    result = run_preflight_input_manifest_fresh_canary(
        str(tmp_path / "workspace"), canary_id="manifest_contract")
    assert result["ok"] is True
    assert all(result["criteria"].values())
    assert result["production_mutation"] is False


def test_handoff_intent_fresh_canary_keeps_standalone_and_continuation_distinct(tmp_path):
    root = tmp_path / "workspace"
    result = run_handoff_intent_fresh_canary(str(root), canary_id="handoff_contract")
    assert result["ok"] is True
    assert all(result["criteria"].values())
    assert result["observations"] == {
        "seed_status": "recorded",
        "standalone_status": "recorded",
        "explicit_missing_status": "unlinked_previous_receipt",
        "explicit_linked_status": "recorded",
        "final_iteration": 3,
        "standalone_handoff_consumed": False,
        "linked_handoff_consumed": True,
    }
    assert result["production_mutation"] is False
    assert verify_evolution_ledger(str(root))["ok"] is True


def test_skipped_terminal_repair_requires_trace_evidence_and_preserves_history(tmp_path):
    root = tmp_path / "workspace"
    refs = []
    for index in range(3):
        state_path = _episode(root, index, "lifecycle.unclosed_tool")
        trace = state_path.parent / "trace.jsonl"
        trace.write_text("\n".join([
            json.dumps({"type": "plan_executor_step_started", "payload": {
                "event": "plan_executor_step_started", "step_id": "compose",
                "event_type": "generate_text"}}),
            json.dumps({"type": "remediation_triggered", "payload": {
                "event": "remediation_triggered", "failures": [{
                    "step_id": "compose", "error": "skipped: required dependencies failed (source)"}]}}),
        ]) + "\n", encoding="utf-8")
        refs.append(str(state_path))
    diagnosis = diagnose_agent_failure(
        str(root), failure_class="lifecycle.unclosed_tool/generate_text", evidence_refs=refs)
    before = {path: Path(path).read_bytes() for path in refs}
    repair = evaluate_skipped_terminal_repair(str(root), diagnosis_path=diagnosis["path"])
    metrics = repair["evaluation"]["metrics"]
    assert metrics["baseline_unclosed_count"] == 3
    assert metrics["bounded_repair_terminalized_count"] == 3
    assert metrics["bounded_repair_remaining_unclosed_count"] == 0
    assert metrics["matched_resample_expected_change"] == "unknown_without_new_execution"
    assert metrics["historical_evidence_immutable"] is True
    assert all(Path(path).read_bytes() == value for path, value in before.items())


def test_skipped_terminal_repair_replays_plan_refs_without_remediation_event(tmp_path):
    root = tmp_path / "workspace"
    refs = []
    for index in range(3):
        state_path = _episode(root, index, "lifecycle.unclosed_tool")
        trace = state_path.parent / "trace.jsonl"
        trace.write_text("\n".join([
            json.dumps({"type": "batch_plan_created", "payload": {
                "event": "batch_plan_created", "steps": [
                    {"id": "source", "event_type": "read", "parameters": {}, "depends_on": []},
                    {"id": "compose", "event_type": "generate_text",
                     "parameters": {"data": "$source.result.content"}, "depends_on": ["source"]},
                ]}}),
            json.dumps({"type": "plan_executor_step_started", "payload": {
                "event": "plan_executor_step_started", "step_id": "source", "event_type": "read"}}),
            json.dumps({"type": "plan_executor_step_completed", "payload": {
                "event": "plan_executor_step_completed", "step_id": "source",
                "event_type": "read", "ok": False}}),
            json.dumps({"type": "plan_executor_step_started", "payload": {
                "event": "plan_executor_step_started", "step_id": "compose",
                "event_type": "generate_text", "depends_on": ["source"]}}),
        ]) + "\n", encoding="utf-8")
        refs.append(str(state_path))
    diagnosis = diagnose_agent_failure(
        str(root), failure_class="lifecycle.unclosed_tool/generate_text", evidence_refs=refs)
    repair = evaluate_skipped_terminal_repair(str(root), diagnosis_path=diagnosis["path"])
    metrics = repair["evaluation"]["metrics"]
    assert metrics["bounded_repair_terminalized_count"] == 3
    assert metrics["bounded_repair_remaining_unclosed_count"] == 0
    assert all(row["dependency_graph_inferred_skip_evidence"] == ["compose"]
               for row in repair["evaluation"]["episodes"])


def test_fresh_parallel_terminal_canary_exercises_real_executor_without_model(tmp_path):
    result = run_skipped_terminal_fresh_canary(
        str(tmp_path / "workspace"), canary_id="canary_test")
    assert result["ok"] is True
    assert all(result["criteria"].values())
    assert result["handler_calls"] == ["source", "sibling", "artifact"]
    assert result["terminal_status"]["compose"] == "skipped"
    assert result["terminal_status"]["deliver"] == "skipped"
    assert result["model_calls"] == 0
    repeated = run_skipped_terminal_fresh_canary(
        str(tmp_path / "workspace"), canary_id="canary_test")
    assert repeated["status"] == "idempotent_canary_replay"
