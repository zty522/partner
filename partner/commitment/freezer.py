"""Freezing expectations, so that a later result can actually contradict them.

The freeze step is where a bet becomes falsifiable and where the kernel earns the
right to judge it later.  Two things must be true at freeze time:

* the evaluation protocol is machine-measurable and independent of agent output
* the budget is absolute, and the commitment policy states when a turn would be
  allowed, so a later change of direction cannot be justified retroactively
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .models import (
    EXECUTION_ENVIRONMENTS, BetRecord, Budget, Candidate, CommitmentPolicy, ContractError,
    EvaluationProtocol, ExpectedEffect, FalsificationCondition, RejectedAlternative,
    TreatmentContract, sha256_of,
)
from .selector import SelectionResult
from .state_machine import COMMITTED


@dataclass(frozen=True)
class FreezeRequest:
    partner_id: str
    project_id: str
    run_id: str
    question: str
    context_snapshot_ref: str
    context_snapshot_hash: str
    candidates: Sequence[Candidate]
    selection: SelectionResult
    expected_effects: Sequence[ExpectedEffect]
    falsification_conditions: Sequence[FalsificationCondition]
    evaluation_protocol: EvaluationProtocol
    baseline_ref: str
    budget: Budget
    commitment_policy: CommitmentPolicy
    code_version: str
    data_version: str
    model_config_ref: str
    #: Where the evidence will be produced.  Frozen, so an isolated run cannot be
    #: promoted to publishable after the result is known.
    environment: str = "synthetic_fixture"
    #: The single allowed treatment difference, or None when the bet never declares
    #: one (settlement then refuses to call the comparison matched).
    treatment: TreatmentContract | None = None


class Freezer:
    """Builds a COMMITTED-ready BetRecord with a verifiable freeze hash."""

    def freeze(self, request: FreezeRequest, *, bet_id: str, now_iso: str) -> BetRecord:
        if request.selection.selected.candidate_id not in {c.candidate_id for c in request.candidates}:
            raise ContractError("Freezer: the selected candidate is not in the candidate set")
        record = BetRecord(
            bet_id=bet_id,
            partner_id=request.partner_id,
            project_id=request.project_id,
            run_id=request.run_id,
            question=request.question,
            context_snapshot_ref=request.context_snapshot_ref,
            context_snapshot_hash=request.context_snapshot_hash,
            candidates=tuple(request.candidates),
            selected_action=request.selection.selected.candidate_id,
            rejected_alternatives=tuple(request.selection.rejected),
            selection_reason=request.selection.reason,
            expected_effects=tuple(request.expected_effects),
            falsification_conditions=tuple(request.falsification_conditions),
            evaluation_protocol=request.evaluation_protocol,
            baseline_ref=request.baseline_ref,
            budget=request.budget,
            commitment_policy=request.commitment_policy,
            code_version=request.code_version,
            data_version=request.data_version,
            model_config_ref=request.model_config_ref,
            environment=str(request.environment or "synthetic_fixture"),
            treatment=request.treatment,
            status=COMMITTED,
            revision=1,
            parent_revision=0,
            created_at=now_iso,
            updated_at=now_iso,
        )
        # A freeze that cannot be falsified is not a commitment.
        self.assert_falsifiable(record)
        return record

    @staticmethod
    def assert_falsifiable(record: BetRecord) -> None:
        for effect in record.expected_effects:
            if effect.direction not in ("increase", "decrease", "equal"):
                raise ContractError(f"Freezer: expectation {effect.metric} has no machine direction")
            if effect.threshold is None:
                raise ContractError(f"Freezer: expectation {effect.metric} has no threshold")
        codes = [c.code for c in record.falsification_conditions]
        if len(set(codes)) != len(codes):
            raise ContractError("Freezer: falsification condition codes must be unique")
        metrics = {spec.get("metric") for spec in record.evaluation_protocol.metric_specs}
        for effect in record.expected_effects:
            if effect.metric not in metrics:
                raise ContractError(
                    f"Freezer: expectation metric {effect.metric!r} is not produced by the "
                    f"evaluation protocol {sorted(m for m in metrics if m)}; an expectation the "
                    "protocol cannot measure is unfalsifiable")
        if not record.baseline_ref:
            raise ContractError("Freezer: a bet without a baseline reference cannot be compared")
        if record.environment not in EXECUTION_ENVIRONMENTS:
            raise ContractError(f"Freezer: unknown execution environment {record.environment!r}")
        if record.environment in ("production_canary", "production") and record.treatment is None:
            raise ContractError(
                "Freezer: a publishable environment must declare a treatment contract; without one "
                "the comparison can never be shown to differ in exactly one variable")

    def revise(self, record: BetRecord, *, selection: SelectionResult,
               expected_effects: Sequence[ExpectedEffect],
               falsification_conditions: Sequence[FalsificationCondition],
               reason: str, now_iso: str) -> BetRecord:
        """Create a new revision.  The previous revision is never edited."""
        if not str(reason).strip():
            raise ContractError("Freezer.revise: a new revision must state why")
        candidates = record.candidates
        if selection.selected.candidate_id not in {c.candidate_id for c in candidates}:
            raise ContractError("Freezer.revise: the selected candidate is not in the candidate set")
        revised = BetRecord(
            bet_id=record.bet_id, partner_id=record.partner_id, project_id=record.project_id,
            run_id=record.run_id, question=record.question,
            context_snapshot_ref=record.context_snapshot_ref,
            context_snapshot_hash=record.context_snapshot_hash,
            candidates=tuple(candidates), selected_action=selection.selected.candidate_id,
            rejected_alternatives=tuple(selection.rejected), selection_reason=selection.reason,
            expected_effects=tuple(expected_effects),
            falsification_conditions=tuple(falsification_conditions),
            evaluation_protocol=record.evaluation_protocol, baseline_ref=record.baseline_ref,
            budget=record.budget, commitment_policy=record.commitment_policy,
            code_version=record.code_version, data_version=record.data_version,
            model_config_ref=record.model_config_ref,
            environment=record.environment, treatment=record.treatment,
            status=COMMITTED, revision=int(record.revision) + 1,
            parent_revision=int(record.revision),
            created_at=record.created_at, updated_at=now_iso,
        )
        self.assert_falsifiable(revised)
        return revised


__all__ = ["Freezer", "FreezeRequest"]
