import hashlib
import json
from pathlib import Path

from partner.governance.manual_runtime import (
    preflight_manual_artifact_truth,
    record_manual_task_outcome,
)
from partner.governance.storage import latest_receipt, load_project_state


def _workspace(tmp_path):
    value = tmp_path / "workspace" / "instances" / "04"
    value.mkdir(parents=True)
    return str(value)


def _candidate_source_task(workspace: str, tmp_path: Path, *, valid_hash: bool = True):
    root = Path(workspace).parents[1]
    source = root / "external/code/source.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    quote = "The runtime tool records every event and preserves task state for recovery."
    source.write_text(quote + "\n", encoding="utf-8")
    task_id = "candidate-source-truth"
    task_dir = Path(workspace) / "state/tasks" / task_id
    task_dir.mkdir(parents=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if not valid_hash:
        digest = "0" * 64
    (task_dir / "_step_candidate.result.json").write_text(json.dumps({
        "event_type": "execute_candidate", "ok": True,
        "result": {
            "ok": True,
            "candidate_event_type": "research_adoption_context_shadow",
            "verified_source_evidence": [{
                "source_path": str(source), "source_sha256": digest,
                "evidence_quote": quote, "investigation_id": "evidence_1",
            }],
        },
    }), encoding="utf-8")
    artifact = tmp_path / "candidate_report.md"
    artifact.write_text(
        "claim_id: CL-001\n"
        "claim_text: The runtime records every event and preserves task state.\n"
        "claim_axes: event_recording task_lifecycle\n"
        f"source_path: {source}\n"
        "source_identity: source.md\n"
        f"evidence_quote: {quote}\n"
        "support_type: direct\n"
        "rationale: the source directly states event recording and task state preservation\n",
        encoding="utf-8",
    )
    return task_id, source, artifact


def test_research_candidate_typed_sources_enter_truth_gate_after_hash_and_quote_check(tmp_path):
    workspace = _workspace(tmp_path)
    task_id, source, artifact = _candidate_source_task(workspace, tmp_path)
    result = record_manual_task_outcome(workspace, {
        "task_id": task_id,
        "goal": ("[strategy_id=candidate_evidence_trajectory_context_v1] "
                 "[policy_arm=candidate] [experiment_id=experiment_truth] [match_key=pair_1] report"),
        "inputs": [], "actions_executed": ["execute_candidate", "create_file"],
        "artifacts": [str(artifact)], "findings": ["grounded report"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["truth_audit"]["passed"] is True
    assert str(source) in result["truth_audit"]["verified_sources"]


def test_research_candidate_source_with_wrong_digest_is_not_admitted(tmp_path):
    workspace = _workspace(tmp_path)
    task_id, _, artifact = _candidate_source_task(workspace, tmp_path, valid_hash=False)
    result = record_manual_task_outcome(workspace, {
        "task_id": task_id,
        "goal": ("[strategy_id=candidate_evidence_trajectory_context_v1] "
                 "[policy_arm=candidate] [experiment_id=experiment_truth] [match_key=pair_bad] report"),
        "inputs": [], "actions_executed": ["execute_candidate", "create_file"],
        "artifacts": [str(artifact)], "findings": ["grounded report"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["ok"] is False
    assert result["status"] == "candidate_truth_gate_failed"
    assert result["truth_audit"]["passed"] is False
    assert result["truth_audit"]["required_sources"] == []


def test_native_project_event_uses_event_truth_contract_not_claim_ledger(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "continuation.md"
    source.write_text("previous handoff", encoding="utf-8")
    artifact = tmp_path / "04_runtime_inventory.md"
    artifact.write_text("machine-derived inventory", encoding="utf-8")
    goal = (
        "[instance_native=true] [native_kind=project] "
        "[project_id=literature_github_learning] [policy_arm=candidate]"
    )

    preflight = preflight_manual_artifact_truth(workspace, {
        "task_id": "native-project-preflight", "goal": goal,
        "inputs": [str(source)], "artifacts": [str(artifact)],
        "actions_executed": ["continuous_project_step"],
    })
    assert preflight == {
        "applicable": False, "passed": True, "production_mutation": False,
    }

    result = record_manual_task_outcome(workspace, {
        "task_id": "native-project-outcome", "goal": goal,
        "inputs": [str(source)],
        "actions_executed": ["continuous_project_step"],
        "artifacts": [str(artifact)],
        "findings": ["04_runtime_inventory: contracts_present=4"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["status"] != "candidate_truth_gate_failed"


def test_manual_success_records_receipt_and_only_proposes_next_action(tmp_path):
    workspace = _workspace(tmp_path)
    artifact = tmp_path / "round1.md"
    artifact.write_text("evidence", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "manual-1", "goal": "核对官方来源", "inputs": [],
        "actions_executed": ["atomic_inspect_file", "extract"],
        "artifacts": [str(artifact)], "findings": ["引文匹配"],
        "next_action": "核对第三个异构官方来源", "delivery_confirmed": True,
        "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["next_action_auto_enqueued"] is False
    assert result["evidence_archive"]["ok"] is True
    receipt = latest_receipt(workspace, "literature_github_learning")
    assert receipt.artifacts[0] != str(artifact)
    assert "share/evidence/literature_github_learning/manual/manual-1" in receipt.artifacts[0]
    assert receipt.next_actions[0].status == "proposed"
    assert receipt.next_actions[0].task_id == ""
    assert load_project_state(workspace, "literature_github_learning").status == "active"
    trajectory = result["trajectory"]["trajectory"]
    assert trajectory["kind"] == "manual_project_iteration"
    assert trajectory["policy_eligible"] is True
    assert trajectory["outcome"]["business_progress"] is True


def test_repeated_semantic_outcome_is_not_positive_rl_progress(tmp_path):
    workspace = _workspace(tmp_path)
    outcomes = []
    for index in (1, 2):
        artifact = tmp_path / f"round-{index}.json"
        artifact.write_text(json.dumps({"run": index}), encoding="utf-8")
        outcomes.append(record_manual_task_outcome(workspace, {
            "task_id": f"semantic-repeat-{index}", "goal": "bounded native-like run",
            "inputs": [], "actions_executed": ["continuous_project_step"],
            "artifacts": [str(artifact)],
            "findings": ["same measured conclusion: stable=1 drift=0.01"],
            "delivery_confirmed": True, "completion_ok": True,
        }))
    first = outcomes[0]["trajectory"]["trajectory"]
    second = outcomes[1]["trajectory"]["trajectory"]
    assert first["outcome"]["business_progress"] is True
    assert first["reward"] > 0
    assert second["outcome"]["business_progress"] is False
    assert second["outcome"]["duplicate_outcome"] is True
    assert second["policy_eligible"] is False
    assert second["reward"] == -0.1


def test_changed_ids_paths_and_numbers_do_not_manufacture_novelty(tmp_path):
    workspace = _workspace(tmp_path)
    results = []
    for index, drift in ((1, "0.013"), (2, "0.027")):
        artifact = tmp_path / f"md_run_{index}.json"
        artifact.write_text(json.dumps({"seed": index, "drift": drift}), encoding="utf-8")
        results.append(record_manual_task_outcome(workspace, {
            "task_id": f"numeric-churn-{index}", "goal": "bounded native-like run",
            "inputs": [], "actions_executed": ["continuous_project_step"],
            "artifacts": [str(artifact)],
            "findings": [f"MD stability run={index}; drift={drift}; output={artifact}; "
                         f"动作选择=project_action_round_{index}; 学习干预={'True' if index == 2 else 'False'}"],
            "delivery_confirmed": True, "completion_ok": True,
        })["trajectory"]["trajectory"])
    assert results[0]["outcome"]["business_progress"] is True
    assert results[1]["outcome"]["duplicate_outcome"] is True
    assert results[1]["reward"] == -0.1


def test_manual_generic_harness_next_action_is_treated_as_stop(tmp_path):
    workspace = _workspace(tmp_path)
    artifact = tmp_path / "decision.md"
    artifact.write_text("verified decision evidence", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "manual-decision", "goal": "形成最终决策", "inputs": [],
        "actions_executed": ["decide_manual_canary"], "artifacts": [str(artifact)],
        "findings": ["all gates passed"],
        "next_action": "根据 Harness 执行结果选择下一步 event；若目标已满足则停止。",
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["receipt"]["next_actions"] == []
    assert result["receipt"]["stop_reason"]


def test_native_report_only_round_is_negative_learning_observation(tmp_path):
    workspace = _workspace(tmp_path)
    report = tmp_path / "gap_analysis_report.md"
    report.write_text("# gap\nOnly a generated status report.", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "native-report-only",
        "goal": "continue project [instance_native=true] [project_id=literature_github_learning]",
        "actions_executed": ["generate_text", "create_file"],
        "artifacts": [str(report)], "findings": ["已完成本地微计划执行"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["ok"] is False
    assert result["status"] == "native_real_action_rejected"
    trajectory = result["trajectory"]["trajectory"]
    assert trajectory["reward"] < 0
    assert trajectory["policy_eligible"] is False
    assert trajectory["learning_observation_eligible"] is True
    assert trajectory["outcome"]["failure_mechanism"].startswith("project/")


def test_partial_artifact_cannot_convert_failed_execution_to_success(tmp_path):
    workspace = _workspace(tmp_path)
    artifact = tmp_path / "partial.pdf"
    artifact.write_bytes(b"%PDF partial evidence")
    task_dir = Path(workspace) / "state/tasks/native-partial"
    task_dir.mkdir(parents=True)
    (task_dir / "task_log.jsonl").write_text(json.dumps({
        "event": "harness_batch_plan_failed", "error": "timeout",
        "failure_owner": "environment",
        "mechanism": "planning/batch_planner_timeout",
    }) + "\n", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "native-partial",
        "goal": "continue [instance_native=true] [project_id=literature_github_learning]",
        "actions_executed": ["batch_plan"], "artifacts": [str(artifact)],
        "findings": ["planner timed out before validation"],
        "delivery_confirmed": False, "completion_ok": False,
    })
    assert result["ok"] is False
    assert result["status"] == "manual_outcome_rejected"
    trajectory = result["trajectory"]["trajectory"]
    assert trajectory["outcome"]["status"] == "failed"
    assert trajectory["outcome"]["artifact_completion"] == "partial"
    assert trajectory["reward"] <= 0
    assert trajectory["learning_observation_eligible"] is True


def test_native_project_marker_overrides_stale_instance_role(tmp_path):
    root = tmp_path / "workspace"
    workspace = root / "instances/03"
    workspace.mkdir(parents=True)
    artifact = tmp_path / "md_run.json"
    artifact.write_text('{"temperature":300,"steps":100}', encoding="utf-8")
    result = record_manual_task_outcome(str(workspace), {
        "task_id": "native-md", "goal": (
            "run simulation [instance_native=true] "
            "[project_id=molecular_dynamics_study]"
        ),
        "actions_executed": ["scientific_run"], "artifacts": [str(artifact)],
        "findings": ["100-step molecular dynamics smoke run completed at 300 K"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["receipt"]["project_id"] == "molecular_dynamics_study"


def test_native_learning_is_rewarded_without_advancing_project_receipt(tmp_path):
    workspace = _workspace(tmp_path)
    root = Path(workspace).parents[1]
    artifact = root / "share/mind/governance/active_learning/experiments/native_exp.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"decision":"candidate_validated"}', encoding="utf-8")
    before = latest_receipt(workspace, "literature_github_learning")
    result = record_manual_task_outcome(workspace, {
        "task_id": "native-learning", "goal": (
            "learn from failure [instance_native=true] [native_kind=learning] "
            "[project_id=literature_github_learning]"
        ),
        "actions_executed": ["agent_active_learning_manual_failure_matched"],
        "artifacts": [str(artifact)], "evidence_refs": [str(artifact)],
        "findings": ["matched candidate passed frozen failure fixture"],
        "delivery_confirmed": False, "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["status"] == "native_learning_observation_recorded"
    assert result["project_state_mutated"] is False
    assert latest_receipt(workspace, "literature_github_learning") == before
    trajectory = result["trajectory"]["trajectory"]
    assert trajectory["outcome"]["business_progress"] is False
    assert trajectory["outcome"]["learning_progress"] is True
    assert trajectory["reward"] > 0
    assert trajectory["policy_eligible"] is False


def test_expected_missing_observation_is_accepted_but_reward_neutral(tmp_path):
    workspace = _workspace(tmp_path)
    obs_artifact = tmp_path / "expected_obs.log"
    obs_artifact.write_text("目标路径不存在，符合预期\n", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "expected-missing", "goal": "确认预期文件不存在",
        "inputs": [], "actions_executed": ["atomic_inspect_file"],
        "artifacts": [str(obs_artifact)],
        "findings": ["目标路径不存在，符合预期；责任归类=input_state"],
        "next_action": "等待用户下一条消息。",
        "delivery_confirmed": True, "completion_ok": True,
        "expected_observation_completed": True,
    })
    assert result["ok"] is True
    trajectory = result["trajectory"]["trajectory"]
    assert trajectory["reward"] == 0.0
    assert trajectory["policy_eligible"] is False
    assert trajectory["outcome"]["monitor_only"] is True
    assert trajectory["reward_components"]["accepted_completed"] == 0.0
    assert trajectory["reward_components"]["delivery_contract"] == 0.0


def test_manual_followup_requires_actual_previous_artifact_input(tmp_path):
    # Hermes 2026-08-27 update: the previous test expected the empty-inputs
    # case (`task_id="two"`) to be hard-rejected as "unlinked_previous_receipt".
    # The manual_runtime fix reclassifies empty `inputs=[]` as
    # "shape (a) inbox-triggered standalone task" and only rejects the
    # "shape (b)" case where the task carries `inputs` that intentionally
    # omit every previous-artifact path. So this test now exercises both
    # shapes and asserts the contract change:
    #   - task "one" with empty inputs        → accepted (shape a)
    #   - task "two" with empty inputs        → accepted (shape a, opt-in)
    #   - task "three" with a non-handoff input → rejected (shape b)
    #   - task "four" with the previous artifact  → accepted, iter=2
    workspace = _workspace(tmp_path)
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    unrelated = tmp_path / "unrelated.md"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    unrelated.write_text("noise", encoding="utf-8")
    base = {
        "goal": "继续核对来源", "actions_executed": ["extract", "exec:python3 -m pytest"],
        "next_action": "继续", "delivery_confirmed": True,
        "completion_ok": True,
    }
    findings_seed = ["first source confirms quote from official registry v1"]
    findings_followup = ["second source cross-checks the registry quote verbatim"]
    findings_third = ["third source spot-check rejects the registry meta row"]
    findings_fourth = ["fourth source archives the verified source set for next iter"]
    # Shape (a): empty inputs, no previous receipt exists yet → accepted.
    assert record_manual_task_outcome(workspace, {
        **base, "task_id": "one", "inputs": [], "artifacts": [str(first)],
        "findings": findings_seed, "ignore_handoff_check": True,
    })["ok"] is True
    # Shape (a): empty inputs, previous receipt exists, but caller marks
    # this as inbox-triggered via opt-in flag → accepted.
    assert record_manual_task_outcome(workspace, {
        **base, "task_id": "two", "inputs": [], "artifacts": [str(second)],
        "findings": findings_followup, "ignore_handoff_check": True,
    })["ok"] is True
    # Shape (b): non-empty inputs that intentionally omit every previous
    # artifact path → still hard-rejected as the design contract demands.
    rejected = record_manual_task_outcome(workspace, {
        **base, "task_id": "three", "inputs": [str(unrelated)],
        "artifacts": [str(second)], "findings": findings_third,
        "continuation_requested": True,
    })
    assert rejected["status"] == "unlinked_previous_receipt"
    # Followup that legitimately hands off the previous artifact → accepted.
    # Task "two" wrote artifacts=[second], so task "four" inputs=[second] is the
    # correct handoff (not inputs=[first] which would be shape (b) and rejected).
    accepted = record_manual_task_outcome(workspace, {
        **base, "task_id": "four", "inputs": [str(second)], "artifacts": [str(second)],
        "findings": findings_fourth, "continuation_requested": True,
    })
    assert accepted["ok"] is True
    assert latest_receipt(workspace, "literature_github_learning").iteration == 3
    assert accepted["trajectory"]["trajectory"]["state"]["continuation_requested"] is True
    assert accepted["trajectory"]["trajectory"]["outcome"]["handoff_consumed"] is True
    assert accepted["trajectory"]["trajectory"]["reward_components"]["handoff_consumed"] == 0.15


def test_manual_standalone_task_with_inputs_does_not_fake_continuation(tmp_path):
    workspace = _workspace(tmp_path)
    first = tmp_path / "previous.md"
    source = tmp_path / "source.py"
    output = tmp_path / "diagnosis.md"
    first.write_text("previous task", encoding="utf-8")
    source.write_text("VALUE = 1\n", encoding="utf-8")
    output.write_text("independent diagnosis", encoding="utf-8")
    base = {
        "goal": "独立诊断", "actions_executed": ["atomic_inspect_file"],
        "delivery_confirmed": True, "completion_ok": True,
    }
    assert record_manual_task_outcome(workspace, {
        **base, "task_id": "seed", "inputs": [], "artifacts": [str(first)],
        "findings": ["seed diagnostic recorded the prior failure root cause"],
        "continuation_requested": False,
    })["ok"] is True

    result = record_manual_task_outcome(workspace, {
        **base, "task_id": "standalone", "inputs": [str(source)],
        "artifacts": [str(output)],
        "findings": ["standalone diagnostic returned a fresh independent verdict"],
        "continuation_requested": False,
    })
    assert result["ok"] is True
    assert result["status"] == "recorded"
    assert result["receipt"]["iteration"] == 2
    assert result["trajectory"]["trajectory"]["state"]["continuation_requested"] is False
    assert result["trajectory"]["trajectory"]["outcome"]["handoff_consumed"] is False
    assert result["trajectory"]["trajectory"]["reward_components"]["handoff_consumed"] == 0.0


def test_manual_followup_accepts_timestamped_delivery_copy_of_old_receipt(tmp_path):
    workspace = _workspace(tmp_path)
    first = tmp_path / "manual_canary_06.md"
    first.write_text("old receipt artifact", encoding="utf-8")
    base = {
        "goal": "继续", "actions_executed": ["extract"], "findings": ["evidence"],
        "next_action": "继续", "delivery_confirmed": True, "completion_ok": True,
    }
    assert record_manual_task_outcome(workspace, {
        **base, "task_id": "old", "inputs": [], "artifacts": [str(first)],
    })["ok"] is True
    delivered = tmp_path / "20260826_031159_563619_manual_canary_06.md"
    delivered.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
    second = tmp_path / "next.md"
    second.write_text("next evidence", encoding="utf-8")
    accepted = record_manual_task_outcome(workspace, {
        **base, "task_id": "next", "inputs": [str(delivered)], "artifacts": [str(second)],
    })
    assert accepted["ok"] is True


def test_manual_failed_delivery_creates_issue_not_receipt(tmp_path):
    workspace = _workspace(tmp_path)
    result = record_manual_task_outcome(workspace, {
        "task_id": "failed", "goal": "test", "actions_executed": ["extract"],
        "artifacts": [str(tmp_path / "missing.md")], "delivery_confirmed": False,
        "completion_ok": True,
    })
    assert result["status"] == "manual_outcome_rejected"
    assert latest_receipt(workspace, "literature_github_learning") is None


def test_candidate_truth_audit_records_arm_and_verifies_each_input(tmp_path):
    workspace = _workspace(tmp_path)
    source_a = tmp_path / "a.md"
    source_b = tmp_path / "b.md"
    quote_a = "Alpha source contains this exact and sufficiently long evidence sentence."
    quote_b = "Beta source contains another exact and sufficiently long evidence sentence."
    source_a.write_text(quote_a, encoding="utf-8")
    source_b.write_text(quote_b, encoding="utf-8")
    artifact = tmp_path / "report.md"
    artifact.write_text(
        f"source_path: {source_a}\nevidence_quote: {quote_a}\n"
        f"source_path: {source_b}\nevidence_quote: {quote_b}\n",
        encoding="utf-8",
    )
    goal = (
        "[strategy_id=manual_stable_truth_audit_v2] "
        "[policy_decision=truth] [policy_arm=candidate] [experiment_id=experiment_test] audit"
    )
    result = record_manual_task_outcome(workspace, {
        "task_id": "candidate-pass", "goal": goal,
        "inputs": [str(source_a), str(source_b)], "actions_executed": ["extract", "create_file"],
        "artifacts": [str(artifact)], "findings": ["grounded"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["ok"] is True
    row = result["trajectory"]["trajectory"]
    assert row["action"]["policy_arm"] == "candidate"
    assert row["action"]["experiment_id"] == "experiment_test"
    assert row["outcome"]["truth_audit"]["passed"] is True


def test_matched_experiment_observation_does_not_advance_project_receipt(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "matched_source.md"
    quote = "This exact matched source sentence is sufficiently long for both arms."
    source.write_text(quote, encoding="utf-8")
    baseline_artifact = tmp_path / "baseline_iter.json"
    baseline_artifact.write_text('{"kind": "baseline", "ok": true}\n', encoding="utf-8")
    before = record_manual_task_outcome(workspace, {
        "task_id": "ordinary-before", "goal": "ordinary project iteration",
        "inputs": [], "actions_executed": ["create_file"],
        "artifacts": [str(baseline_artifact)],
        "findings": ["baseline project state"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert before["ok"] is True, before
    latest_before = before["receipt"]["receipt_id"]
    artifact = tmp_path / "matched_report.md"
    artifact.write_text(f"source_path: {source}\nevidence_quote: {quote}\n", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "matched-candidate",
        "goal": ("[strategy_id=candidate_preflight_contract_v2] [policy_arm=candidate] "
                 "[experiment_id=experiment_test] [match_key=pair_1] compare"),
        "inputs": [str(source)], "actions_executed": ["extract", "create_file"],
        "artifacts": [str(artifact)], "findings": ["matched evidence"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["status"] == "experiment_observation_recorded"
    assert result["project_state_mutated"] is False
    assert result["truth_audit"]["passed"] is True
    assert result["trajectory"]["trajectory"]["action"]["match_key"] == "pair_1"
    assert result["trajectory"]["trajectory"]["learning_observation_eligible"] is True
    from partner.governance.storage import latest_receipt
    assert latest_receipt(workspace, "literature_github_learning").receipt_id == latest_before


def test_candidate_truth_audit_accepts_markdown_bullet_and_code_labels(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "source.md"
    quote = "This exact Markdown-labelled evidence sentence is sufficiently long."
    source.write_text(quote, encoding="utf-8")
    artifact = tmp_path / "report.md"
    artifact.write_text(
        f"- `source_path`: `{source}`\n- `evidence_quote`: {quote}\n",
        encoding="utf-8",
    )
    result = record_manual_task_outcome(workspace, {
        "task_id": "candidate-markdown-labels",
        "goal": "[policy_arm=candidate] [experiment_id=experiment_test] audit",
        "inputs": [str(source)], "actions_executed": ["extract", "create_file"],
        "artifacts": [str(artifact)], "findings": ["grounded"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["trajectory"]["trajectory"]["outcome"]["truth_audit"]["passed"] is True


def test_candidate_truth_audit_fails_closed_on_paraphrased_quote(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "source.md"
    source.write_text("The source has a precise sentence which must be copied exactly.", encoding="utf-8")
    artifact = tmp_path / "report.md"
    artifact.write_text(
        f"source_path: {source}\nevidence_quote: The source has a sentence that was paraphrased and is not exact.\n",
        encoding="utf-8",
    )
    result = record_manual_task_outcome(workspace, {
        "task_id": "candidate-fail",
        "goal": "[strategy_id=v2] [policy_decision=truth] [policy_arm=candidate] [experiment_id=experiment_test] audit",
        "inputs": [str(source)], "actions_executed": ["extract", "create_file"],
        "artifacts": [str(artifact)], "findings": ["claimed grounded"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["status"] == "candidate_truth_gate_failed"
    row = result["trajectory"]["trajectory"]
    assert row["outcome"]["false_success"] is True
    assert row["reward"] < 0


def test_candidate_truth_audit_rejects_stale_capability_claim_contradicted_by_runtime(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "source.md"
    quote = "This source contains an exact and sufficiently long evidence sentence."
    source.write_text(quote, encoding="utf-8")
    artifact = tmp_path / "report.md"
    artifact.write_text(
        "当前回合没有可用的 shell / file-write 工具，只能请用户复制。\n"
        f"source_path: {source}\nevidence_quote: {quote}\n",
        encoding="utf-8",
    )
    result = record_manual_task_outcome(workspace, {
        "task_id": "candidate-stale-capability",
        "goal": "[policy_arm=candidate] [experiment_id=experiment_test] audit",
        "inputs": [str(source)], "actions_executed": ["extract", "create_file"],
        "artifacts": [str(artifact)], "findings": ["claimed grounded"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["status"] == "candidate_truth_gate_failed"
    audit = result["truth_audit"]
    assert audit["capability_contradictions"]
    assert audit["passed"] is False


def test_candidate_truth_audit_allows_historical_false_capability_postmortem(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "source.md"
    quote = "This source contains an exact and sufficiently long evidence sentence."
    source.write_text(quote, encoding="utf-8")
    artifact = tmp_path / "report.md"
    artifact.write_text(
        "历史错误轮次曾虚假声称没有写文件能力，因此该旧 Receipt 已被作废。\n"
        f"source_path: {source}\nevidence_quote: {quote}\n",
        encoding="utf-8",
    )
    result = record_manual_task_outcome(workspace, {
        "task_id": "candidate-historical-postmortem",
        "goal": "[policy_arm=candidate] [experiment_id=experiment_test] audit",
        "inputs": [str(source)], "actions_executed": ["extract", "create_file"],
        "artifacts": [str(artifact)], "findings": ["historical audit"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["trajectory"]["trajectory"]["outcome"]["truth_audit"]["passed"] is True


def test_failed_canary_acceptance_is_not_dropped_from_trajectory(tmp_path):
    workspace = _workspace(tmp_path)
    artifact = tmp_path / "bad.md"
    artifact.write_text("produced but not accepted", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "baseline-fail",
        "goal": "[strategy_id=v1] [policy_decision=truth] [policy_arm=baseline] [experiment_id=experiment_test] audit",
        "inputs": [], "actions_executed": ["generate_text", "create_file"],
        "artifacts": [str(artifact)], "findings": ["citation gate failed"],
        "delivery_confirmed": False, "completion_ok": False,
    })
    assert result["status"] == "manual_outcome_rejected"
    row = result["trajectory"]["trajectory"]
    assert row["action"]["policy_arm"] == "baseline"
    assert row["outcome"]["status"] == "failed"
    assert row["outcome"]["false_success"] is True
    assert row["policy_eligible"] is False


def test_matched_experiment_uses_durable_local_observation_when_channel_is_offline(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "source.md"
    quote = "This exact source sentence is long enough for deterministic verification."
    source.write_text(quote, encoding="utf-8")
    artifact = tmp_path / "report.md"
    artifact.write_text(
        f"source_path: {source}\nevidence_quote: {quote}\n", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "candidate-local-observation",
        "goal": (
            "[strategy_id=candidate_preflight_contract_v2] "
            "[policy_arm=candidate] [experiment_id=experiment_local] "
            "[match_key=source_readme] audit"
        ),
        "inputs": [str(source)], "actions_executed": ["extract", "create_file"],
        "artifacts": [str(artifact)], "findings": ["grounded"],
        "delivery_confirmed": False, "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["status"] == "experiment_observation_recorded"
    assert result["local_observation_confirmed"] is True
    assert result["receipt"]["delivery_confirmed"] is False
    trajectory = result["trajectory"]["trajectory"]
    assert trajectory["outcome"]["status"] == "completed"
    assert trajectory["reward_components"]["delivery_contract"] == 0.0
    assert trajectory["learning_observation_eligible"] is True


def test_non_experiment_artifact_still_requires_real_delivery(tmp_path):
    workspace = _workspace(tmp_path)
    artifact = tmp_path / "report.md"
    artifact.write_text("real work", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "production-no-delivery", "goal": "ordinary production work",
        "artifacts": [str(artifact)], "findings": ["done"],
        "delivery_confirmed": False, "completion_ok": True,
    })
    assert result["status"] == "manual_outcome_rejected"


def test_promoted_truth_policy_applies_to_unmarked_04_markdown_task(tmp_path):
    workspace = _workspace(tmp_path)
    control = tmp_path / "workspace" / "share" / "mind" / "governance" / "experience_guided_policy" / "control_policy.json"
    control.parent.mkdir(parents=True)
    control.write_text('{"promoted":{"literature_github_learning:manual_final_artifact_truth":"manual_stable_truth_audit_v2"}}', encoding="utf-8")
    source = tmp_path / "source.md"
    quote = "This exact source sentence is sufficiently long for production verification."
    source.write_text(quote, encoding="utf-8")
    artifact = tmp_path / "report.md"
    artifact.write_text(f"source_path: {source}\nevidence_quote: {quote}\n", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "production-v2", "goal": "普通04来源报告",
        "inputs": [str(source)], "actions_executed": ["extract", "create_file"],
        "artifacts": [str(artifact)], "findings": ["verified"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    row = result["trajectory"]["trajectory"]
    assert row["action"]["policy_arm"] == "production"
    assert row["action"]["strategy_id"] == "manual_stable_truth_audit_v2"
    assert row["outcome"]["truth_audit"]["passed"] is True


def test_campaign_report_is_neutral_monitor_and_does_not_mutate_project(tmp_path):
    workspace = _workspace(tmp_path)
    control = tmp_path / "workspace/share/mind/governance/experience_guided_policy/control_policy.json"
    control.parent.mkdir(parents=True)
    control.write_text(
        '{"promoted":{"literature_github_learning:planning.semantic_preflight":'
        '"candidate_preflight_contract_v2"}}', encoding="utf-8")
    report = tmp_path / "workspace/state/campaigns/c1/report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Campaign status\nmonitor only\n", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "campaign-report-monitor", "goal": "Campaign 定时进度摘要",
        "actions_executed": ["campaign_report_delivery"],
        "artifacts": [str(report)], "findings": ["message delivered"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    row = result["trajectory"]["trajectory"]
    assert result["status"] == "campaign_monitor_recorded"
    assert result["project_state_mutated"] is False
    assert row["outcome"]["monitor_only"] is True
    assert row["reward"] == 0.0
    assert row["policy_eligible"] is False
    assert row["learning_observation_eligible"] is False
    assert latest_receipt(workspace, "literature_github_learning") is None


def test_campaign_report_channel_failure_remains_neutral_completed_monitor(tmp_path):
    workspace = _workspace(tmp_path)
    report = tmp_path / "workspace/state/campaigns/c1/report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Campaign status\nlocal durable report\n", encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "campaign-report-no-ack", "goal": "Campaign 定时进度摘要",
        "actions_executed": ["campaign_report_delivery"],
        "artifacts": [str(report)], "findings": ["local report recorded"],
        "delivery_confirmed": False, "completion_ok": False,
    })
    row = result["trajectory"]["trajectory"]
    assert result["ok"] is True
    assert row["outcome"]["status"] == "completed"
    assert row["outcome"]["monitor_only"] is True
    assert row["reward"] == 0.0
    assert row["learning_observation_eligible"] is False


def test_sprint18_shadow_artifact_is_local_observation_without_channel_delivery(tmp_path):
    workspace = _workspace(tmp_path)
    artifact = tmp_path / "workspace/instances/04/state/tasks/shadow/research_adoption_context.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"production_effective": false, "context_digest": "abc"}', encoding="utf-8")
    evidence = tmp_path / "workspace/share/mind/governance/research_learning/p/evidence/e.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"evidence_found": true}', encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "shadow-context", "goal": "[sprint18=true] isolated context shadow",
        "actions_executed": ["research_adoption_context_shadow"],
        "artifacts": [str(artifact)], "findings": ["context candidate persisted"],
        "evidence_refs": [str(evidence)],
        "delivery_confirmed": False, "completion_ok": True,
    })
    assert result["ok"] is True
    assert result["status"] == "isolated_learning_observation_recorded"
    assert result["project_state_mutated"] is False
    row = result["trajectory"]["trajectory"]
    assert row["outcome"]["learning_progress"] is True
    assert row["state"]["delivery_confirmed"] is False
    assert row["policy_eligible"] is False


def test_native_no_new_candidate_is_learning_not_business_or_project_progress(tmp_path):
    workspace = _workspace(tmp_path)
    artifact = tmp_path / "no_new_candidate.json"
    artifact.write_text(json.dumps({
        "candidate_id": "candidate_existing_1",
        "decision": "no_new_candidate",
        "production_effective": False,
        "existing_production_effective": True,
    }), encoding="utf-8")
    result = record_manual_task_outcome(workspace, {
        "task_id": "native-no-new-candidate",
        "goal": ("[instance_native=true] [native_kind=project] "
                 "[project_id=hermes_partner_explore]"),
        "actions_executed": ["continuous_project_step"],
        "artifacts": [str(artifact)],
        "findings": ["Candidate decision=no_new_candidate; production_effective=False"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    row = result["trajectory"]["trajectory"]
    assert result["status"] == "candidate_no_change_observation_recorded"
    assert result["project_state_mutated"] is False
    assert result["production_effective"] is False
    assert row["outcome"]["business_progress"] is False
    assert row["outcome"]["learning_progress"] is True
    assert row["reward_components"]["business_progress"] == 0.0
    assert row["policy_eligible"] is False
    assert latest_receipt(workspace, "hermes_partner_explore") is None

    repeated = record_manual_task_outcome(workspace, {
        "task_id": "native-no-new-candidate-repeat",
        "goal": ("[instance_native=true] [native_kind=project] "
                 "[project_id=hermes_partner_explore]"),
        "actions_executed": ["continuous_project_step"],
        "artifacts": [str(artifact)],
        "findings": ["Candidate decision=no_new_candidate; production_effective=False"],
        "delivery_confirmed": True, "completion_ok": True,
    })
    repeated_row = repeated["trajectory"]["trajectory"]
    assert repeated_row["outcome"]["duplicate_outcome"] is True
    assert repeated_row["outcome"]["learning_progress"] is False
    assert repeated_row["reward"] == -0.1
