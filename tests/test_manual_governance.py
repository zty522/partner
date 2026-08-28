from partner.governance.manual_runtime import record_manual_task_outcome
from partner.governance.storage import latest_receipt, load_project_state


def _workspace(tmp_path):
    value = tmp_path / "workspace" / "instances" / "04"
    value.mkdir(parents=True)
    return str(value)


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
        "goal": "继续核对来源", "actions_executed": ["extract"],
        "findings": ["evidence"], "next_action": "继续", "delivery_confirmed": True,
        "completion_ok": True,
    }
    # Shape (a): empty inputs, no previous receipt exists yet → accepted.
    assert record_manual_task_outcome(workspace, {
        **base, "task_id": "one", "inputs": [], "artifacts": [str(first)],
        "ignore_handoff_check": True,
    })["ok"] is True
    # Shape (a): empty inputs, previous receipt exists, but caller marks
    # this as inbox-triggered via opt-in flag → accepted.
    assert record_manual_task_outcome(workspace, {
        **base, "task_id": "two", "inputs": [], "artifacts": [str(second)],
        "ignore_handoff_check": True,
    })["ok"] is True
    # Shape (b): non-empty inputs that intentionally omit every previous
    # artifact path → still hard-rejected as the design contract demands.
    rejected = record_manual_task_outcome(workspace, {
        **base, "task_id": "three", "inputs": [str(unrelated)],
        "artifacts": [str(second)],
    })
    assert rejected["status"] == "unlinked_previous_receipt"
    # Followup that legitimately hands off the previous artifact → accepted.
    # Task "two" wrote artifacts=[second], so task "four" inputs=[second] is the
    # correct handoff (not inputs=[first] which would be shape (b) and rejected).
    accepted = record_manual_task_outcome(workspace, {
        **base, "task_id": "four", "inputs": [str(second)], "artifacts": [str(second)],
    })
    assert accepted["ok"] is True
    assert latest_receipt(workspace, "literature_github_learning").iteration == 3


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
    before = record_manual_task_outcome(workspace, {
        "task_id": "ordinary-before", "goal": "ordinary project iteration",
        "inputs": [], "actions_executed": ["create_file"], "artifacts": [],
        "findings": ["baseline project state"], "delivery_confirmed": True,
        "completion_ok": True,
    })
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


def test_promoted_truth_policy_applies_to_unmarked_04_markdown_task(tmp_path):
    workspace = _workspace(tmp_path)
    control = tmp_path / "workspace" / "share" / "mind" / "governance" / "rl" / "control_policy.json"
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
