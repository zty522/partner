import json
from pathlib import Path

from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.episode_trace import reduce_manual_history, reduce_task_episode, reward_vector
from partner.governance.shadow_evolution import run_shadow_evolution
from partner.governance.shadow_replay import evaluate_isolated_preflight_canary, evaluate_preflight_shadow
from partner.governance.strategy_space import write_strategy_catalog
from partner.mind.harness import _preserve_candidate_verified_sources


def _task(tmp_path: Path, *, failed_preflight: bool = True, task_id: str = "task-episode") -> tuple[str, str]:
    root = tmp_path / "workspace"
    task_dir = root / "instances/04/state/tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "task_instance.json").write_text(json.dumps({
        "task_id": task_id, "user_message": "grounded report", "created_at": "2026-01-01T00:00:00Z",
        "completion_status": "done",
    }), encoding="utf-8")
    artifact = task_dir / "report.md"
    artifact.write_text("grounded evidence", encoding="utf-8")
    rows = [
        {"ts": "2026-01-01T00:00:00Z", "event": "task_instance_created"},
        {"ts": "2026-01-01T00:00:01Z", "event": "robust_execute_start", "event_name": "batch_planner",
         "metadata": {"model": "test", "attempt": 1}},
    ]
    if failed_preflight:
        rows.append({"ts": "2026-01-01T00:00:02Z", "event": "manual_plan_preflight_failed",
                     "error": "evidence-dependent output must reference source step", "attempt": 1,
                     "rejected_plan": [{"id": "write", "event_type": "create_file"}]})
    rows.extend([
        {"ts": "2026-01-01T00:00:03Z", "event": "robust_execute_success", "event_name": "batch_planner", "attempt": 1},
        {"ts": "2026-01-01T00:00:04Z", "event": "plan_executor_step_started", "step_id": "write",
         "event_type": "create_file", "depends_on": []},
        {"ts": "2026-01-01T00:00:05Z", "event": "plan_executor_step_completed", "step_id": "write",
         "event_type": "create_file", "ok": True, "files": [str(artifact)], "elapsed_sec": 1.0},
        {"ts": "2026-01-01T00:00:06Z", "event": "manual_iteration_governance", "receipt": {
            "project_id": "literature_github_learning", "receipt_id": "receipt", "delivery_confirmed": True,
            "artifacts": [str(artifact)],
        }},
    ])
    (task_dir / "task_log.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return str(root), task_id


def test_episode_trace_reduces_raw_log_into_correlated_graph(tmp_path):
    root, task_id = _task(tmp_path)
    trajectory = {
        "project_id": "literature_github_learning",
        "state": {"delivery_confirmed": True},
        "outcome": {"status": "completed", "business_progress": True, "handoff_consumed": True,
                    "false_success": False, "truth_audit": {"passed": True}},
    }
    result = reduce_task_episode(root, instance_id="04", task_id=task_id, trajectory=trajectory)
    assert result["ok"] is True
    state = result["state"]
    assert state["tool_calls"][0]["status"] == "completed"
    assert state["model_calls"][0]["purpose"] == "batch_planner"
    assert "planning.semantic_preflight" in state["failure_classes"]
    assert state["failure_details"][0]["attempt"] == 1
    assert state["reward_vector"]["hard_gate_passed"] is True
    assert Path(result["bundle"], "trace.jsonl").is_file()


def test_episode_trace_treats_dependency_skip_as_terminal_not_unclosed_failure(tmp_path):
    root, task_id = _task(tmp_path, failed_preflight=False, task_id="task-skipped")
    task_dir = Path(root) / "instances/04/state/tasks" / task_id
    rows = [json.loads(line) for line in (task_dir / "task_log.jsonl").read_text().splitlines()]
    rows.extend([
        {"ts": "2026-01-01T00:00:07Z", "event": "plan_executor_step_started",
         "step_id": "compose", "event_type": "generate_text", "depends_on": ["source"]},
        {"ts": "2026-01-01T00:00:08Z", "event": "plan_executor_step_completed",
         "step_id": "compose", "event_type": "generate_text", "ok": False,
         "terminal_status": "skipped", "terminal_reason": "required_dependencies_failed",
         "failed_dependencies": ["source"], "files": [], "elapsed_sec": 0},
    ])
    (task_dir / "task_log.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    state = reduce_task_episode(root, instance_id="04", task_id=task_id)["state"]
    compose = next(row for row in state["tool_calls"] if row["step_id"] == "compose")
    assert compose["status"] == "skipped"
    assert compose["terminal_reason"] == "required_dependencies_failed"
    assert "lifecycle.unclosed_tool" not in state["failure_classes"]
    assert "tool.generate_text.failed" not in state["failure_classes"]


def test_episode_trace_classifies_final_acceptance_failure_without_failed_tool(tmp_path):
    root, task_id = _task(tmp_path, failed_preflight=False, task_id="task-acceptance-failed")
    task_dir = Path(root) / "instances/04/state/tasks" / task_id
    rows = [json.loads(line) for line in (task_dir / "task_log.jsonl").read_text().splitlines()]
    rows.insert(-1, {"ts": "2026-01-01T00:00:05.5Z", "event": "iteration_check",
                     "satisfied": False, "missing": ["named_artifact:continuation.md"]})
    rows[-1] = {"ts": "2026-01-01T00:00:06Z", "event": "manual_iteration_governance",
                "status": "manual_outcome_rejected", "ok": False,
                "trajectory": {"trajectory": {
                    "project_id": "literature_github_learning",
                    "outcome": {"status": "failed", "failure_mechanism": ""},
                }}}
    (task_dir / "task_log.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    state = reduce_task_episode(root, instance_id="04", task_id=task_id)["state"]
    expected = "verification.acceptance_contract/implicit_handoff_artifact"
    assert expected in state["failure_classes"]
    assert any(row.get("source") == "final_acceptance_governance"
               and row.get("mechanism") == expected
               for row in state["failure_details"])


def test_episode_trace_marks_completed_duplicate_as_learning_signal(tmp_path):
    root, task_id = _task(tmp_path, failed_preflight=False, task_id="task-duplicate")
    trajectory = {
        "project_id": "literature_github_learning",
        "outcome": {"status": "completed", "duplicate_outcome": True,
                    "failure_mechanism": ""},
    }
    state = reduce_task_episode(root, instance_id="04", task_id=task_id,
                                trajectory=trajectory)["state"]
    assert "outcome.duplicate_semantic_result" in state["failure_classes"]


def test_reward_vector_truth_failure_cannot_be_compensated():
    value = reward_vector({
        "state": {"delivery_confirmed": True},
        "outcome": {"business_progress": True, "handoff_consumed": True,
                    "false_success": True, "truth_audit": {"passed": False}},
    }, {"tool_calls": [{"status": "completed"}], "model_calls": [], "failure_classes": [],
        "conversation_items": [{"kind": "progress"}], "delivery": {"confirmed": True}})
    assert value["values"]["truth"] == 0.0
    assert value["scalar"] == 0.0
    assert value["policy_eligible"] is False


def test_candidate_verified_source_footer_is_exact_and_candidate_only():
    source = "/tmp/source.md"
    quote = "This exact evidence quote is comfortably longer than twenty characters."
    data = {"verified_sources": {"source": {"source_path": source, "evidence_quote": quote}}}
    bad = "# Report\nsource_path: /wrong/path.md\nevidence_quote: too short\nBody"
    marker = ("[strategy_id=candidate_preflight_contract_v2] [policy_arm=candidate] "
              "[experiment_id=e] [match_key=p]")
    repaired = _preserve_candidate_verified_sources(marker, data, bad)
    assert "/wrong/path.md" not in repaired
    assert f"source_path: {source}" in repaired
    assert f"evidence_quote: {quote}" in repaired
    assert _preserve_candidate_verified_sources("ordinary production", data, bad) == bad


def test_continuous_candidate_preserves_list_shaped_verified_evidence():
    source = "/tmp/continuous-source.py"
    quote = "A machine-verified continuous campaign quote longer than twenty characters."
    data = {"verified_source_evidence": [{
        "source_path": source,
        "evidence_quote": quote,
        "question": "How should context be retained?",
    }]}
    marker = ("[strategy_id=candidate_evidence_trajectory_context_v1] "
              "[policy_arm=candidate] [experiment_id=continuous] [match_key=p]")
    repaired = _preserve_candidate_verified_sources(
        marker,
        data,
        "# Report\nBody\n\n# 七、Claim Ledger — C1\nclaim_id: C1\nclaim_text: incomplete",
    )
    assert f"source_path: {source}" in repaired
    assert f"evidence_quote: {quote}" in repaired
    assert "support_type: direct" in repaired
    assert "claim_id: C1" not in repaired


def test_continuous_baseline_does_not_receive_candidate_evidence_footer():
    data = {"verified_source_evidence": [{
        "source_path": "/tmp/source.py",
        "evidence_quote": "A baseline must not receive this candidate-only verified quote.",
    }]}
    marker = ("[strategy_id=baseline_governed_context_v1] "
              "[policy_arm=baseline] [experiment_id=continuous] [match_key=p]")
    original = "# Baseline\nNo named evidence."
    assert _preserve_candidate_verified_sources(marker, data, original) == original


def test_shadow_evolution_creates_candidate_without_production_mutation(tmp_path):
    root, task_id = _task(tmp_path)
    trajectory = {"project_id": "literature_github_learning", "state": {"delivery_confirmed": True},
                  "outcome": {"business_progress": True, "false_success": False}}
    reduce_task_episode(root, instance_id="04", task_id=task_id, trajectory=trajectory)
    result = run_shadow_evolution(root, project_id="literature_github_learning")
    assert result["status"] == "candidate_created"
    assert result["promotion"] is False
    assert result["production_mutation"] is False
    assert result["target_failure_class"] == "planning.semantic_preflight"
    assert not Path(root, "share/mind/governance/experience_guided_policy/control_policy.json").exists()
    repeated = run_shadow_evolution(root, project_id="literature_github_learning")
    assert repeated["experiment_id"] == result["experiment_id"]
    experiments = Path(root, "share/mind/governance/experiments.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(experiments) == 1


def test_episode_reducer_applies_append_only_receipt_invalidation(tmp_path):
    root, task_id = _task(tmp_path, failed_preflight=False)
    corrections = Path(root, "share/projects/literature_github_learning/governance/receipt_corrections.jsonl")
    corrections.parent.mkdir(parents=True)
    corrections.write_text(json.dumps({"receipt_id": "receipt", "action": "invalidate"}) + "\n", encoding="utf-8")
    trajectory = {"project_id": "literature_github_learning", "state": {"receipt_id": "receipt"},
                  "outcome": {"business_progress": True, "false_success": False, "truth_audit": {"passed": True}}}
    state = reduce_task_episode(root, instance_id="04", task_id=task_id, trajectory=trajectory)["state"]
    assert state["delivery"]["receipt_invalidated"] is True
    assert "verification.invalidated_receipt" in state["failure_classes"]
    assert state["reward_vector"]["values"]["truth"] == 0.0
    assert state["reward_vector"]["policy_eligible"] is False


def test_bulk_reduce_strategy_catalog_and_shadow_registry(tmp_path):
    root = ""
    for index in range(10):
        root, task_id = _task(tmp_path, task_id=f"task-{index:02d}")
        # Bulk reduction deliberately reconstructs from governed task logs.
        assert task_id
    history = reduce_manual_history(root, instance_id="04", project_id="literature_github_learning")
    assert history["reduced"] == 10
    catalog = write_strategy_catalog(root)
    assert len(catalog["strategies"]) == 6
    assert catalog["automatic_production_promotion"] is False
    shadow = evaluate_preflight_shadow(
        root, project_id="literature_github_learning", experiment_id="experiment-test",
    )
    assert shadow["pairs"] == 10
    assert shadow["sample_gate_passed"] is True
    assert shadow["causal_status"] == "projected_not_executed"
    assert shadow["promotion"] is False
    assert shadow["candidate_skill"]["status"] == "shadow"
    assert shadow["candidate_skill"]["production_effective"] is False
    assert shadow["intervention_isolated"] is False
    assert "baseline/candidate execution path is not feature-isolated" in shadow["promotion_blockers"]
    assert "baseline/candidate intervention path is feature-isolated" in shadow["candidate_skill"]["success_criteria"]


def test_shadow_replay_separates_executed_canary_from_projected_baseline(tmp_path):
    root, task_id = _task(tmp_path, failed_preflight=False, task_id="candidate-run")
    trajectory = {
        "work_item_id": task_id,
        "project_id": "literature_github_learning",
        "action": {"strategy_id": "candidate_preflight_aware_planning_v1",
                   "experiment_id": "experiment-test"},
        "state": {"delivery_confirmed": True},
        "outcome": {"status": "completed", "business_progress": True,
                    "handoff_consumed": True, "false_success": False,
                    "truth_audit": {"passed": True}},
    }
    reduce_task_episode(root, instance_id="04", task_id=task_id, trajectory=trajectory)
    trajectory_path = Path(root, "share/mind/governance/experience_guided_policy/trajectories.jsonl")
    trajectory_path.parent.mkdir(parents=True, exist_ok=True)
    trajectory_path.write_text(json.dumps(trajectory) + "\n", encoding="utf-8")
    result = evaluate_preflight_shadow(
        root, project_id="literature_github_learning", experiment_id="experiment-test",
        minimum_pairs=1,
    )
    assert result["pairs"] == 0
    assert result["causal_status"] == "projected_plus_bounded_canary"
    assert result["executed_canaries"]["metrics"]["executed"] == 1
    assert result["executed_canaries"]["metrics"]["policy_eligible"] == 1
    assert result["candidate_skill"]["status"] == "canary"
    assert result["promotion"] is False


def test_isolated_canary_requires_matched_distinct_routes(tmp_path):
    trajectory_rows = []
    root = ""
    for arm, strategy, active, route in (
        ("baseline", "baseline_current_preflight_v1", False, "baseline_current_contract"),
        ("candidate", "candidate_preflight_contract_v2", True, "candidate_prompt_contract_v2"),
    ):
        task_id = f"task-{arm}"
        root, _ = _task(tmp_path, failed_preflight=False, task_id=task_id)
        log_path = Path(root, f"instances/04/state/tasks/{task_id}/task_log.jsonl")
        rows = log_path.read_text(encoding="utf-8").splitlines()
        proof = {
            "event": "planner_experiment_intervention", "experiment_id": "experiment-isolated",
            "match_key": "pair-1", "policy_arm": arm, "strategy_id": strategy,
            "marked": True, "active": active, "route": route, "intervention": "test",
        }
        rows.insert(1, json.dumps(proof))
        log_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        trajectory = {
            "work_item_id": task_id, "project_id": "literature_github_learning",
            "action": {"strategy_id": strategy, "policy_arm": arm,
                       "experiment_id": "experiment-isolated", "match_key": "pair-1"},
            "state": {"delivery_confirmed": True},
            "outcome": {"status": "completed", "business_progress": True,
                        "handoff_consumed": True, "false_success": False,
                        "truth_audit": {"passed": True}},
        }
        trajectory_rows.append(trajectory)
        reduce_task_episode(root, instance_id="04", task_id=task_id, trajectory=trajectory)
    trajectory_path = Path(root, "share/mind/governance/experience_guided_policy/trajectories.jsonl")
    trajectory_path.parent.mkdir(parents=True, exist_ok=True)
    trajectory_path.write_text(
        "".join(json.dumps(row) + "\n" for row in trajectory_rows), encoding="utf-8"
    )
    result = evaluate_isolated_preflight_canary(
        root, project_id="literature_github_learning",
        experiment_id="experiment-isolated", minimum_pairs=1,
    )
    assert result["pairs"] == 1
    assert result["intervention_isolated"] is True
    assert result["independent_task_ids"] is True
    assert result["quality_gate_passed"] is True
    assert result["sustained_business_improvement_passed"] is False
    assert result["decision"] == "inconclusive"
    assert result["promotion"] is False


def test_isolated_canary_uses_latest_executed_retry_for_same_arm_and_match(tmp_path):
    root, old_task = _task(tmp_path, failed_preflight=False, task_id="candidate-old")
    root, new_task = _task(tmp_path, failed_preflight=False, task_id="candidate-new")
    trajectory_path = Path(root, "share/mind/governance/experience_guided_policy/trajectories.jsonl")
    trajectory_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for task_id, created_at in ((old_task, "2026-01-01T00:00:00Z"),
                                (new_task, "2026-01-02T00:00:00Z")):
        log_path = Path(root, f"instances/04/state/tasks/{task_id}/task_log.jsonl")
        log_rows = log_path.read_text(encoding="utf-8").splitlines()
        log_rows.insert(1, json.dumps({
            "event": "planner_experiment_intervention", "experiment_id": "experiment-retry",
            "match_key": "pair", "policy_arm": "candidate",
            "strategy_id": "candidate_preflight_contract_v2", "active": True,
            "route": "candidate_prompt_contract_v2",
        }))
        log_path.write_text("\n".join(log_rows) + "\n", encoding="utf-8")
        trajectory = {
            "work_item_id": task_id, "project_id": "literature_github_learning",
            "created_at": created_at,
            "action": {"strategy_id": "candidate_preflight_contract_v2", "policy_arm": "candidate",
                       "experiment_id": "experiment-retry", "match_key": "pair"},
            "outcome": {"status": "completed", "business_progress": True,
                        "false_success": False, "truth_audit": {"passed": True}},
        }
        rows.append(trajectory)
        reduce_task_episode(root, instance_id="04", task_id=task_id, trajectory=trajectory)
    root, baseline_task = _task(tmp_path, failed_preflight=False, task_id="baseline")
    baseline_log = Path(root, f"instances/04/state/tasks/{baseline_task}/task_log.jsonl")
    baseline_rows = baseline_log.read_text(encoding="utf-8").splitlines()
    baseline_rows.insert(1, json.dumps({
        "event": "planner_experiment_intervention", "experiment_id": "experiment-retry",
        "match_key": "pair", "policy_arm": "baseline",
        "strategy_id": "baseline_current_preflight_v1", "active": False,
        "route": "baseline_current_contract",
    }))
    baseline_log.write_text("\n".join(baseline_rows) + "\n", encoding="utf-8")
    baseline_trajectory = {
        "work_item_id": baseline_task, "project_id": "literature_github_learning",
        "created_at": "2026-01-01T12:00:00Z",
        "action": {"strategy_id": "baseline_current_preflight_v1", "policy_arm": "baseline",
                   "experiment_id": "experiment-retry", "match_key": "pair"},
        "outcome": {"status": "completed", "business_progress": True,
                    "false_success": False, "truth_audit": {"passed": True}},
    }
    rows.append(baseline_trajectory)
    reduce_task_episode(root, instance_id="04", task_id=baseline_task, trajectory=baseline_trajectory)
    trajectory_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = evaluate_isolated_preflight_canary(
        root, project_id="literature_github_learning", experiment_id="experiment-retry", minimum_pairs=1,
    )
    assert result["pairs"] == 1
    assert result["pairs_detail"][0]["candidate"]["task_id"] == new_task
    assert result["rejected_executions"] == []


def test_candidate_skill_cannot_activate_from_status_label_alone(tmp_path):
    payload = {
        "candidate_id": "candidate_test", "status": "promoted",
        "source_episode_ids": ["episode_1"], "applicability": ["manual_stable"],
        "success_criteria": ["truth=1"],
    }
    try:
        register_candidate_skill(str(tmp_path), payload)
    except ValueError as exc:
        assert "promotion_decision_id" in str(exc)
    else:
        raise AssertionError("promoted candidate must require a decision record")
