"""Invariant 1: the state machine is the only writer of lifecycle truth."""
from __future__ import annotations

import pytest

from partner.commitment import models as M
from partner.commitment import state_machine as sm
from partner.commitment.state_machine import (
    IllegalTransition, InvariantViolation, TransitionRequest,
)


def _valid_receipt(bet_id="bet_x"):
    return M.ExecutionReceipt(
        receipt_id="r1", bet_id=bet_id, requested_action="c", executed_action="c",
        executor_id="e", executor_version="1", started_epoch=1.0, finished_epoch=2.0,
        status="completed", exit_code=0, artifacts=("a.json",),
        artifact_hashes={"a.json": "deadbeef"}, log_ref="", data_hash="",
        budget_consumed={}, human_intervention=False, idempotency_key="k")


def _measurement(bet_id="bet_x", validity="valid"):
    return M.OutcomeMeasurement(
        measurement_id="m1", bet_id=bet_id, receipt_id="r1", metric="mean",
        value=1.0, direction="increase", unit="", measured_by="ev", evaluator_version="1",
        evidence_refs=("a.json",) if validity == "valid" else (),
        missing_reason="" if validity == "valid" else "no data", validity=validity,
        agent_artifacts_unchanged=True, artifact_hashes_before={}, artifact_hashes_after={})


def _settlement(bet_id="bet_x", settlement_class="supported"):
    return M.SettlementDecision(
        settlement_id="s1", bet_id=bet_id, settlement_class=settlement_class,
        expectation_outcomes=(M.ExpectationOutcome(metric="mean", direction="increase",
                                                   threshold=1.0, observed=1.0, met=True),),
        comparison=_proof(0.0, 1.0),
        new_regressions=(), pre_existing_failures=(),
        expectations_met=True, improvement_over_baseline=True, baseline_already_satisfied=False,
        supported_claim="improvement_over_baseline", environment="production_canary",
        improvement_observed=True,
        publish_eligible=True, publish_blockers=(), next_state="CLOSED",
        machine_rules=("rule",))


def _proof(baseline_value, candidate_value):
    """A matched v2 proof: controls identical, treatment as frozen, code unchanged."""
    from partner.commitment.settlement import treatment_diff_hash
    diff = treatment_diff_hash(baseline_treatment="control",
                               candidate_treatment="cand_small_gain:{}")
    return M.ComparisonProof(
        inputs_identical=True, evaluator_identical=True, protocol_identical=True,
        budget_comparable=True, environment_identical=True, harness_version_identical=True,
        baseline_treatment="control", candidate_treatment="cand_small_gain:{}",
        treatment_diff_hash=diff, expected_treatment_diff_hash=diff,
        baseline_code_version="v1", candidate_code_version="v1",
        baseline_value=baseline_value, candidate_value=candidate_value,
        delta=None if baseline_value is None else candidate_value - baseline_value)


def test_happy_path_edges_are_legal():
    lifecycle = sm.BetLifecycle(bet_id="bet_x")
    for target in (sm.PROPOSED, sm.COMMITTED):
        sm.advance(lifecycle, TransitionRequest(target=target, now=1.0))
    sm.advance(lifecycle, TransitionRequest(target=sm.EXECUTING, now=2.0))
    sm.advance(lifecycle, TransitionRequest(target=sm.MEASURED, now=3.0, receipt=_valid_receipt()))
    sm.advance(lifecycle, TransitionRequest(target=sm.SETTLED, now=4.0, measurement=_measurement(),
                                            settlement=_settlement()))
    sm.advance(lifecycle, TransitionRequest(target=sm.CLOSED, now=5.0))
    assert lifecycle.state == sm.CLOSED
    assert [row["to"] for row in lifecycle.history] == [
        sm.PROPOSED, sm.COMMITTED, sm.EXECUTING, sm.MEASURED, sm.SETTLED, sm.CLOSED]


@pytest.mark.parametrize("current,target", [
    (sm.DRAFT, sm.COMMITTED), (sm.DRAFT, sm.EXECUTING), (sm.DRAFT, sm.MEASURED),
    (sm.PROPOSED, sm.EXECUTING), (sm.PROPOSED, sm.MEASURED), (sm.PROPOSED, sm.SETTLED),
    (sm.COMMITTED, sm.MEASURED), (sm.COMMITTED, sm.SETTLED), (sm.EXECUTING, sm.SETTLED),
    (sm.EXECUTING, sm.CLOSED), (sm.MEASURED, sm.CLOSED), (sm.MEASURED, sm.EXECUTING),
])
def test_illegal_skips_are_rejected(current, target):
    lifecycle = sm.BetLifecycle(bet_id="bet_x", state=current)
    with pytest.raises(IllegalTransition):
        sm.advance(lifecycle, TransitionRequest(target=target, now=1.0))


def test_measured_requires_a_valid_receipt():
    lifecycle = sm.BetLifecycle(bet_id="bet_x", state=sm.EXECUTING)
    with pytest.raises(InvariantViolation):
        sm.advance(lifecycle, TransitionRequest(target=sm.MEASURED, now=1.0))
    failed_payload = _valid_receipt().to_dict()
    failed_payload.update({"status": "failed", "artifacts": [], "artifact_hashes": {},
                           "failure_reason": "boom"})
    failed = M.ExecutionReceipt.from_dict(failed_payload)
    with pytest.raises(InvariantViolation):
        sm.advance(lifecycle, TransitionRequest(target=sm.MEASURED, now=1.0, receipt=failed))
    assert lifecycle.state == sm.EXECUTING


def test_settled_requires_an_independent_measurement():
    lifecycle = sm.BetLifecycle(bet_id="bet_x", state=sm.MEASURED)
    with pytest.raises(InvariantViolation):
        sm.advance(lifecycle, TransitionRequest(target=sm.SETTLED, now=1.0))
    with pytest.raises(InvariantViolation):
        sm.advance(lifecycle, TransitionRequest(target=sm.SETTLED, now=1.0,
                                                measurement=_measurement(validity="missing")))
    with pytest.raises(InvariantViolation):
        sm.advance(lifecycle, TransitionRequest(target=sm.SETTLED, now=1.0,
                                                measurement=_measurement(), settlement=None))
    assert lifecycle.state == sm.MEASURED


def test_settlement_is_idempotent():
    lifecycle = sm.BetLifecycle(bet_id="bet_x", state=sm.MEASURED)
    sm.advance(lifecycle, TransitionRequest(target=sm.SETTLED, now=1.0, measurement=_measurement(),
                                            settlement=_settlement()))
    assert lifecycle.settled is True
    # a repeat message while still in SETTLED must not settle twice
    with pytest.raises(IllegalTransition):
        sm.advance(lifecycle, TransitionRequest(target=sm.SETTLED, now=2.0,
                                               measurement=_measurement(), settlement=_settlement()))


def test_terminal_states_are_final():
    for state in sm.TERMINAL_STATES:
        lifecycle = sm.BetLifecycle(bet_id="bet_x", state=state)
        for target in (sm.PROPOSED, sm.EXECUTING, sm.SETTLED, sm.COMMITTED):
            with pytest.raises((IllegalTransition, InvariantViolation)):
                sm.advance(lifecycle, TransitionRequest(target=target, now=1.0))


def test_turn_requires_new_evidence_and_a_policy_allowance():
    lifecycle = sm.BetLifecycle(bet_id="bet_x", state=sm.SETTLED, settled=True, turn_round=0)
    with pytest.raises(InvariantViolation):
        sm.advance(lifecycle, TransitionRequest(target=sm.COMMITTED, now=1.0, turn_allowed=False))
    with pytest.raises(InvariantViolation):
        sm.advance(lifecycle, TransitionRequest(target=sm.COMMITTED, now=1.0, turn_allowed=True))
    sm.advance(lifecycle, TransitionRequest(target=sm.COMMITTED, now=1.0, turn_allowed=True,
                                            new_evidence_refs=("evidence:new",)))
    assert lifecycle.revision == 2 and lifecycle.turn_round == 1 and lifecycle.settled is False


def test_assert_turn_allowed_machine_rule():
    policy = M.CommitmentPolicy(earliest_turn_round=2, max_turns=2, require_new_evidence_to_turn=True)
    lifecycle = sm.BetLifecycle(bet_id="bet_x", turn_round=0)
    assert sm.assert_turn_allowed(policy, lifecycle, new_evidence_refs=("e",)) == (
        False, "earliest permitted turn not reached")
    lifecycle = sm.BetLifecycle(bet_id="bet_x", turn_round=1)
    assert sm.assert_turn_allowed(policy, lifecycle, new_evidence_refs=()) == (False, "no new evidence")
    assert sm.assert_turn_allowed(policy, lifecycle, new_evidence_refs=("e",))[0] is True
    lifecycle = sm.BetLifecycle(bet_id="bet_x", turn_round=2)
    assert sm.assert_turn_allowed(policy, lifecycle, new_evidence_refs=("e",)) == (
        False, "turn budget exhausted")
