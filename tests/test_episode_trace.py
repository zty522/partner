import json
from pathlib import Path

from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.episode_trace import reduce_manual_history, reduce_task_episode, reward_vector
from partner.governance.shadow_evolution import run_shadow_evolution
from partner.governance.shadow_replay import evaluate_preflight_shadow
from partner.governance.strategy_space import write_strategy_catalog


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
    assert not Path(root, "share/mind/governance/rl/control_policy.json").exists()
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
    trajectory_path = Path(root, "share/mind/governance/rl/trajectories.jsonl")
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
