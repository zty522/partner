from partner.governance.research_downstream import (
    evaluate_downstream_output,
    frozen_tasks,
    parse_json_response,
    sanitize_external_context,
    local_grounded_consumer,
)


def _tasks():
    return frozen_tasks(
        receipt_id="receipt_real", receipt_actions=["observe", "select"],
        jitrl_path="/evidence/jitrl.pdf", hermes_path="/evidence/context.py")


def test_parse_json_response_accepts_plain_and_fenced_json():
    plain, error = parse_json_response('{"ok": true}')
    assert plain == {"ok": True} and not error
    fenced, error = parse_json_response('```json\n{"ok": false}\n```')
    assert fenced == {"ok": False} and not error


def test_parse_json_response_accepts_complete_json_after_think_but_not_truncation():
    parsed, error = parse_json_response('<think>{"draft":1}</think>\n{"ok":true}')
    assert parsed == {"ok": True} and not error
    parsed, error = parse_json_response('<think>reason</think>\n{"ok":')
    assert parsed == {} and "invalid_json" in error


def test_source_ref_label_is_normalized_to_stable_uri():
    task = frozen_tasks(
        receipt_id="receipt_real", receipt_actions=["observe", "select"],
        jitrl_path="source://jitrl-paper", hermes_path="source://hermes-context")[1]
    raw = ('{"answer":"state action reward without gradient","receipt_id":"",'
           '"mechanism_points":["state action reward gradient"],'
           '"source_refs":["paper evidence (source://jitrl-paper)"],'
           '"production_effective":false,"next_action":"test","uncertainties":[]}')
    result = evaluate_downstream_output(
        task, raw, context="source://jitrl-paper", budget_chars=100)
    assert result["ok"] is True


def test_evidence_id_can_ground_named_source_through_typed_context():
    task = frozen_tasks(
        receipt_id="receipt_real", receipt_actions=["observe"],
        jitrl_path="source://jitrl-paper", hermes_path="source://hermes-context")[2]
    context = ('<!-- research_evidence:research_evidence_abc -->\n'
               '{"investigation_id":"research_evidence_abc",'
               '"source_path":"source://hermes-context",'
               '"evidence_quote":"handoff summary preserves state"}')
    raw = ('{"answer":"通过交接摘要保留状态","receipt_id":"",'
           '"mechanism_points":["交接","摘要","状态"],'
           '"source_refs":["research_evidence:research_evidence_abc"],'
           '"production_effective":false,"next_action":"有界验证","uncertainties":[]}')
    result = evaluate_downstream_output(task, raw, context=context, budget_chars=1000)
    assert result["ok"] is True


def test_receipt_task_requires_exact_identity_and_grounded_ref():
    task = _tasks()[0]
    raw = ('{"answer":"observe select completed","receipt_id":"receipt_real",'
           '"mechanism_points":["observe","select"],"source_refs":["receipt_real"],'
           '"production_effective":false,"next_action":"run bounded comparison",'
           '"uncertainties":[]}')
    result = evaluate_downstream_output(task, raw, context="receipt_real observe select", budget_chars=100)
    assert result["ok"] is True
    assert result["reward"] == 1.0


def test_fabricated_source_or_wrong_production_claim_hard_zeros_reward():
    task = _tasks()[1]
    raw = ('{"answer":"state action reward without gradient","receipt_id":"",'
           '"mechanism_points":["state action reward gradient"],'
           '"source_refs":["/invented/paper.pdf"],"production_effective":true,'
           '"next_action":"test","uncertainties":[]}')
    result = evaluate_downstream_output(task, raw, context="/evidence/jitrl.pdf", budget_chars=100)
    assert result["ok"] is False
    assert result["reward"] == 0
    assert result["checks"]["source_refs_grounded"] is False
    assert result["checks"]["production_boundary_truthful"] is False


def test_missing_required_mechanism_is_not_partial_truth_reward():
    task = _tasks()[2]
    raw = ('{"answer":"keep a summary","receipt_id":"",'
           '"mechanism_points":["summary"],"source_refs":["/evidence/context.py"],'
           '"production_effective":false,"next_action":"continue","uncertainties":[]}')
    result = evaluate_downstream_output(task, raw, context="/evidence/context.py", budget_chars=100)
    assert result["ok"] is False
    assert result["reward"] == 0


def test_external_context_projection_redacts_local_identity_and_credentials():
    result = sanitize_external_context(
        """<!-- protected_project_handoff -->
        /mnt/e/work/private/file receipt_deadbeef traj_manual_deadbeef
        experiment_deadbeef aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
        API_KEY=secret

        JitRL state action reward gradient evidence.
        """,
        query="JitRL state action reward", aliases={}, limit_chars=1000)
    assert result["ok"] is True
    assert "/mnt/" not in result["context"]
    assert "receipt_deadbeef" not in result["context"]
    assert "traj_manual_deadbeef" not in result["context"]
    assert "secret" not in result["context"]
    assert "state action reward" in result["context"]


def test_local_consumer_reads_typed_receipt_without_oracle_access():
    task = _tasks()[0]
    context = ('<!-- protected_project_handoff -->\n'
               '{"receipt_id":"receipt_real","iteration":3,'
               '"actions_executed":["observe","select"],"next_actions":[]}')
    raw = local_grounded_consumer(task, context)
    result = evaluate_downstream_output(task, raw, context=context, budget_chars=1000)
    assert result["ok"] is True


def test_local_consumer_refuses_to_invent_missing_typed_evidence():
    task = _tasks()[1]
    raw = local_grounded_consumer(task, "generic prose without a source record")
    value, error = parse_json_response(raw)
    assert not error
    assert value["source_refs"] == []
    assert value["uncertainties"]
    result = evaluate_downstream_output(task, raw, context="generic prose", budget_chars=1000)
    assert result["ok"] is False
