"""Invariant 13 + the experience rules: every abnormal path stops, transparently."""
from __future__ import annotations

import pytest

from partner.commitment import models as M
from partner.commitment import state_machine as sm
from partner.commitment.settlement import build_experience
from conftest import ScriptedExecutor, build_bet, default_snapshot, make_config


def test_execution_failure_blocks_and_does_not_settle(workspace):
    runner, store, _ = build_bet(workspace, executor=ScriptedExecutor(
        workspace=workspace, values=[12.0], mode="fail"))
    result = runner.run()
    assert result.state == "BLOCKED"
    assert "scripted_execution_failure" in result.reason
    assert result.settlement is None
    assert store.list_artifacts("experience") == []
    assert store.list_artifacts("settlement") == []


def test_cancelled_is_a_terminal_state_that_stops_the_loop():
    lifecycle = sm.BetLifecycle(bet_id="bet_cancel", state=sm.COMMITTED)
    sm.advance(lifecycle, sm.TransitionRequest(target=sm.CANCELLED, now=1.0,
                                               reason="operator cancelled"))
    assert sm.is_terminal(lifecycle.state)
    assert lifecycle.closed_reason == "operator cancelled"
    with pytest.raises((sm.IllegalTransition, sm.InvariantViolation)):
        sm.advance(lifecycle, sm.TransitionRequest(target=sm.EXECUTING, now=2.0))


def test_blocked_and_budget_exhausted_are_terminal():
    for state in (sm.BLOCKED, sm.BUDGET_EXHAUSTED, sm.INVALID):
        lifecycle = sm.BetLifecycle(bet_id="b", state=state)
        assert sm.is_terminal(state)
        with pytest.raises((sm.IllegalTransition, sm.InvariantViolation)):
            sm.advance(lifecycle, sm.TransitionRequest(target=sm.COMMITTED, now=1.0))


def test_every_runner_result_ends_in_a_terminal_state(workspace):
    for mode, expected in (("ok", "CLOSED"), ("fail", "BLOCKED"), ("missing", "INVALID")):
        runner, _, _ = build_bet(workspace, bet_id=f"bet_mode_{mode}",
                                 executor=ScriptedExecutor(workspace=workspace,
                                                           values=[13.0, 14.0], mode=mode))
        result = runner.run()
        assert result.state == expected, (mode, result.state, result.reason)
        assert sm.is_terminal(result.state)


def _dummy_receipt():
    return M.ExecutionReceipt(
        receipt_id="rcpt_dummy", bet_id="b", requested_action="a", executed_action="a",
        executor_id="scripted", executor_version="1.0.0", started_epoch=1.0, finished_epoch=1.1,
        status="completed", exit_code=0, artifacts=("dummy.json",),
        artifact_hashes={"dummy.json": "0" * 64}, log_ref="",
        data_hash="", budget_consumed={"actions": 1}, human_intervention=False,
        idempotency_key="idem-dummy")


def test_experience_is_refused_for_invalid_or_blocked_settlements():
    for settlement_class in ("invalid", "blocked"):
        decision = M.SettlementDecision(
            settlement_id="s", bet_id="b", settlement_class=settlement_class,
            expectation_outcomes=(),
            comparison=M.ComparisonProof(
                inputs_identical=False, evaluator_identical=False, protocol_identical=False,
                budget_comparable=False, environment_identical=False,
                harness_version_identical=False, baseline_treatment="",
                candidate_treatment="", treatment_diff_hash="", expected_treatment_diff_hash="",
                baseline_code_version="", candidate_code_version="", baseline_value=None,
                candidate_value=None, delta=None),
            new_regressions=(), pre_existing_failures=(),
            expectations_met=False, improvement_over_baseline=False,
            baseline_already_satisfied=False, supported_claim="none",
            environment="synthetic_fixture", improvement_observed=False,
            publish_eligible=False, publish_blockers=(), next_state="CLOSED",
            machine_rules=("r",))
        with pytest.raises(M.ContractError, match="cannot mint an"):
            build_experience(settlement=decision, bet=_dummy_bet(), receipt=_dummy_receipt())


def test_a_single_experience_can_never_be_a_habit_or_growth():
    for level in ("habit", "growth", "belief", "lesson"):
        with pytest.raises(M.ContractError, match="level must be 'experience'"):
            M.ExperienceRecord(
                experience_id="e", bet_id="b", run_id="r", settlement_ref="s",
                settlement_class="supported", state_action_outcome_chain=("a",),
                scope="x", confidence=0.5, evidence_refs=("ev",), level=level)


def test_experience_cannot_declare_itself_authoritative():
    with pytest.raises(M.ContractError, match="authoritative must stay False"):
        M.ExperienceRecord(
            experience_id="e", bet_id="b", run_id="r", settlement_ref="s",
            settlement_class="supported", state_action_outcome_chain=("a",), scope="x",
            confidence=0.5, evidence_refs=("ev",), authoritative=True)


def test_experience_needs_the_full_state_action_outcome_chain():
    with pytest.raises(M.ContractError, match="state_action_outcome_chain"):
        M.ExperienceRecord(
            experience_id="e", bet_id="b", run_id="r", settlement_ref="s",
            settlement_class="supported", state_action_outcome_chain=(), scope="x",
            confidence=0.5, evidence_refs=("ev",))


def test_runner_mints_exactly_one_experience_bound_to_the_settlement(workspace):
    runner, store, _ = build_bet(workspace)
    result = runner.run()
    assert len(store.list_artifacts("experience")) == 1
    experience = result.experience
    assert experience.settlement_ref == result.settlement.settlement_id
    assert experience.settlement_class == result.settlement.settlement_class
    assert experience.level == "experience"
    assert experience.authoritative is False
    assert len(experience.state_action_outcome_chain) == 4


def _dummy_bet():
    return M.BetRecord.from_dict(_bet_payload())


def _bet_payload():
    from partner.commitment.freezer import FreezeRequest, Freezer
    from partner.commitment.selector import GuardedGainSelector
    snapshot = default_snapshot()
    config = make_config()
    candidates = tuple(M.Candidate(candidate_id=e["candidate_id"], description=e["description"],
                                   params=e["params"]) for e in snapshot["candidate_space"])
    selection = GuardedGainSelector(max_risk=0.5).select(candidates=candidates, snapshot=snapshot)
    request = FreezeRequest(
        partner_id=config.partner_id, project_id=config.project_id, run_id=config.run_id,
        question=config.question, context_snapshot_ref=config.context_snapshot_ref,
        context_snapshot_hash="h", candidates=candidates, selection=selection,
        expected_effects=config.expected_effects,
        falsification_conditions=config.falsification_conditions,
        evaluation_protocol=config.evaluation_protocol, baseline_ref=config.baseline_ref,
        budget=config.budget, commitment_policy=config.commitment_policy,
        code_version=config.code_version, data_version=config.data_version,
        model_config_ref=config.model_config_ref)
    return Freezer().freeze(request, bet_id="bet_dummy", now_iso="2026-09-19T00:00:00+0800").to_dict()


class _EmptyProposer:
    def propose(self, *, question, snapshot, max_candidates):
        from partner.commitment.ports import ProposalResult
        return ProposalResult((), "llm", model_calls=1, provider="p", model="m",
                              failure="llm_response_unparsable")


def test_proposer_failure_blocks_instead_of_crashing(workspace):
    """A proposer that yields nothing is a legal terminal, not an illegal edge."""
    runner, store, executor = build_bet(workspace, proposer=_EmptyProposer())
    result = runner.run()
    assert result.state == "BLOCKED"
    assert "llm_response_unparsable" in result.reason
    assert executor.executions == 0
    assert store.list_artifacts("settlement") == []


def test_draft_to_blocked_is_a_declared_edge():
    assert sm.can_transition(sm.DRAFT, sm.BLOCKED)
    lifecycle = sm.BetLifecycle(bet_id="b")
    sm.advance(lifecycle, sm.TransitionRequest(target=sm.BLOCKED, now=1.0, reason="no direction"))
    assert sm.is_terminal(lifecycle.state)

