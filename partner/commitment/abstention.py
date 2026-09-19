"""Abstention: the harness declining to wager, as a first-class terminal state.

An abstention is *not* a failed run and *not* a blocked one.  Nothing was executed, so
there is no measurement to argue about; the only thing that happened is a decision, and
that decision is recorded as an artifact.  Three invariants keep the distinction honest:

1. ``settlement_class == "abstained"`` carries **no expectation outcomes** and **no**
   ``expectations_met`` / ``improvement_over_baseline`` / ``baseline_already_satisfied``
   claim (enforced in :mod:`partner.commitment.models`).  A reader cannot mistake
   "we did not run" for "we ran and here is the result".
2. The measurement handed to the lifecycle is explicitly ``validity="missing"`` with the
   abstention as its reason, so nothing downstream can treat it as evidence.
3. The execution receipt is ``completed`` for the action that *did* happen (recording the
   abstention) and declares ``model_calls=0`` and ``actions=0``: no proposer, no patch,
   no arm run.

The experience emitted is ``level="abstention"``: still one episode, still never a habit
or a growth claim, and still unpromotable (promotion requires a supported settlement).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping

from .evaluator import MeasurementResult
from .models import (
    BLOCKER_ABSTAINED, ContractError, ComparisonProof, ExecutionReceipt, ExperienceRecord,
    LEVEL_ABSTENTION, OutcomeMeasurement, SettlementDecision, sha256_of,
)

#: The two "executors" of an abstention.  Neither runs an arm: they only record the decision.
ABSTAIN_EXECUTOR_ID = "abstention-recorder"
ABSTAIN_EVALUATOR_ID = "abstention-not-measured"
ABSTAIN_VERSION = "abstention/1"

#: The one action an abstention actually performs.
ABSTAIN_ACTION = "abstain"


def build_abstention_receipt(*, bet, reason: str, artifact_path: str | Path,
                             artifact_hash: str, started_epoch: float | None = None,
                             finished_epoch: float | None = None) -> ExecutionReceipt:
    """The receipt for the *only* thing an abstention executes: recording the decision.

    ``requested_action == executed_action == "abstain"`` and the artifact is the abstention
    record itself, so the receipt proves a real, inspectable output while declaring zero
    model calls and zero arms.
    """
    path = str(artifact_path)
    if not artifact_hash:
        raise ContractError("build_abstention_receipt: the abstention record must be hashed")
    started = float(started_epoch if started_epoch is not None else time.time())
    finished = float(finished_epoch if finished_epoch is not None else time.time())
    return ExecutionReceipt(
        receipt_id=f"rcp_{bet.bet_id}_abstained",
        bet_id=bet.bet_id,
        requested_action=ABSTAIN_ACTION,
        executed_action=ABSTAIN_ACTION,
        executor_id=ABSTAIN_EXECUTOR_ID,
        executor_version=ABSTAIN_VERSION,
        started_epoch=min(started, finished),
        finished_epoch=max(started, finished),
        status="completed",
        exit_code=0,
        artifacts=(path,),
        artifact_hashes={path: str(artifact_hash)},
        log_ref=f"abstention:{reason}",
        data_hash=sha256_of({"action": ABSTAIN_ACTION, "reason": str(reason)}),
        budget_consumed={"model_calls": 0, "actions": 0, "wall_clock_seconds": 0, "rounds": 0},
        human_intervention=False,
        idempotency_key=f"{ABSTAIN_ACTION}:{bet.bet_id}:r{bet.revision}",
        failure_reason="",
    )


def not_measured(*, bet, receipt, reason: str) -> tuple[OutcomeMeasurement, MeasurementResult]:
    """The explicitly-not-measured pair handed to the lifecycle.

    The primary metric is named (so the object is traceable back to the frozen
    expectation) but carries no value: ``validity="missing"`` and the abstention as its
    reason.  There is no reading here for anyone to mistake for a result.
    """
    effect = bet.expected_effects[0]
    primary = OutcomeMeasurement(
        measurement_id=f"mst_{bet.bet_id}_abstained",
        bet_id=bet.bet_id, receipt_id=receipt.receipt_id,
        metric=effect.metric, value=None, direction=effect.direction, unit=effect.unit,
        measured_by=ABSTAIN_EVALUATOR_ID, evaluator_version=ABSTAIN_VERSION,
        evidence_refs=(), missing_reason=str(reason), validity="missing",
        agent_artifacts_unchanged=True, artifact_hashes_before={}, artifact_hashes_after={})
    measured = MeasurementResult(
        measurements=(primary,), metric_values={effect.metric: None}, validity="missing",
        missing_reason=str(reason), artifact_hashes_before={}, artifact_hashes_after={},
        artifacts_read=())
    return primary, measured


def settle_abstained(*, bet, reason: str, evidence: Mapping[str, Any],
                     next_state: str = "CLOSED", now_iso: str = "") -> SettlementDecision:
    """The machine verdict for a bet that was deliberately not run.

    There is no baseline, no candidate and no comparison: the rules record the abstention
    reason and the frozen prior evidence, and every claim field stays unclaimed.
    """
    if not str(reason).strip():
        raise ContractError("settle_abstained: an abstention must state its reason")
    evidence = dict(evidence or {})
    rules = [
        "abstained: no action was executed, no arm was run and no metric was read",
        f"abstain_reason={reason}",
    ]
    for key in sorted(evidence):
        rules.append(f"abstain_evidence[{key}]={json.dumps(evidence[key], sort_keys=True, default=str)}")
    rules.append(
        "expectations_met, improvement_over_baseline and baseline_already_satisfied are not "
        "claimed here: nothing was measured")
    return SettlementDecision(
        settlement_id=f"stl_{bet.bet_id}_r{bet.revision}",
        bet_id=bet.bet_id, settlement_class="abstained",
        expectation_outcomes=(), new_regressions=(), pre_existing_failures=(),
        comparison=ComparisonProof(
            inputs_identical=False, evaluator_identical=False, protocol_identical=False,
            budget_comparable=False, environment_identical=False,
            harness_version_identical=False, baseline_treatment="", candidate_treatment="",
            treatment_diff_hash="", expected_treatment_diff_hash="", baseline_code_version="",
            candidate_code_version="", baseline_value=None, candidate_value=None, delta=None,
            detail="no comparison was computed: the abstention declared no action"),
        expectations_met=False, improvement_over_baseline=False, baseline_already_satisfied=False,
        supported_claim="none", environment=bet.environment, improvement_observed=False,
        publish_eligible=False, publish_blockers=(BLOCKER_ABSTAINED,),
        next_state=str(next_state or "CLOSED"), machine_rules=tuple(rules),
        llm_explanation="", llm_explanation_authoritative=False,
        created_at=str(now_iso or time.strftime("%Y-%m-%dT%H:%M:%S")))


def build_abstention_experience(*, settlement: SettlementDecision, bet,
                                receipt, evidence_refs: tuple[str, ...] = ()) -> ExperienceRecord:
    """The experience of a declined wager: one episode, level=abstention, never promotable."""
    if settlement.settlement_class != "abstained":
        raise ContractError(
            f"build_abstention_experience: settlement_class={settlement.settlement_class!r} is not "
            "an abstention")
    evidence = tuple(dict.fromkeys(
        (f"settlement:{settlement.settlement_id}",) + tuple(str(r) for r in evidence_refs if r)))
    return ExperienceRecord(
        experience_id=f"exp_{bet.bet_id}_r{bet.revision}",
        bet_id=bet.bet_id, run_id=bet.run_id, settlement_ref=settlement.settlement_id,
        settlement_class=settlement.settlement_class,
        state_action_outcome_chain=(
            f"state:{bet.context_snapshot_hash}",
            f"action:{bet.selected_action}",
            f"receipt:{receipt.receipt_id}",
            f"settlement:{settlement.settlement_id}",
        ),
        scope=bet.project_id, confidence=0.5, evidence_refs=evidence,
        level=LEVEL_ABSTENTION, authoritative=False)


def abstention_record(*, bet, reason: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """The artifact an abstention writes: what was decided, on what evidence, and what ran."""
    return {
        "schema_version": ABSTAIN_VERSION,
        "bet_id": bet.bet_id, "run_id": bet.run_id, "project_id": bet.project_id,
        "selected_action": bet.selected_action, "abstain_reason": str(reason),
        "evidence": dict(evidence or {}),
        "executed": {"model_calls": 0, "actions": 0, "arms_run": 0, "patch_proposed": False,
                     "metric_read": False},
        "declared_but_not_run": [effect.metric for effect in bet.expected_effects],
    }


def record_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(payload), sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()


__all__ = ["ABSTAIN_ACTION", "ABSTAIN_EXECUTOR_ID", "ABSTAIN_EVALUATOR_ID", "ABSTAIN_VERSION",
           "abstention_record", "build_abstention_experience", "build_abstention_receipt",
           "not_measured", "record_hash", "settle_abstained"]
