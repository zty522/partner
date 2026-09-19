"""Orchestrate exactly one bounded bet lifecycle.

The runner is an orchestrator, not a scheduler.  It never starts a background
loop, never seeds follow-up work, and always ends in a terminal state or a budget
edge.  Scheduling and recovery belong to Event Fabric (see
``partner/events/commitment.py``); the runner only knows how to walk one bet from
DRAFT to a terminal state.

Two properties are enforced here rather than trusted:

* **idempotent replay** -- re-running a bet that already reached a terminal state
  returns the recorded outcome and performs no new execution, measurement,
  experience or round.
* **single execution owner** -- an execution claim is an append-only event keyed
  by the revision.  A second owner cannot claim the same revision.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import state_machine as sm
from .evaluator import MeasurementResult
from .freezer import FreezeRequest, Freezer
from .models import (
    BetRecord, Budget, Candidate, CommitmentPolicy, ContractError, EvaluationProtocol,
    ExpectedEffect, ExperienceRecord, ExecutionReceipt, FalsificationCondition,
    OutcomeMeasurement, SettlementDecision, canonical_json, sha256_of,
)
from .policy import BudgetLedger, post_settlement_decision
from .ports import (
    ActionExecutor, BaselineEvidenceProvider, CandidateProposer, ConsequenceForecaster,
    ExternalLearningRequester, NullExternalLearningRequester, ShadowConsequenceForecaster,
    StateReader,
)
from .selector import GuardedGainSelector, SelectionRefused, SelectionResult
from .settlement import (
    SettlementRequest, build_experience, budget_hash, make_treatment_contract, protocol_hash,
    settle,
)
from .models import BaselineEvidence, TreatmentSpec, canonical_json
from .store import CommitmentStore, StoreIntegrityError


@dataclass(frozen=True)
class RunnerConfig:
    partner_id: str
    project_id: str
    run_id: str
    question: str
    baseline_ref: str
    expected_effects: Sequence[ExpectedEffect]
    falsification_conditions: Sequence[FalsificationCondition]
    evaluation_protocol: EvaluationProtocol
    budget: Budget
    commitment_policy: CommitmentPolicy
    max_candidates: int
    code_version: str
    data_version: str
    model_config_ref: str
    context_snapshot_ref: str = "context/snapshot.json"
    scope: str = ""
    publish_gate_reasons: Sequence[str] = ()
    #: Where the evidence is produced.  Declared, never inferred, and frozen into
    #: the bet so it cannot be upgraded after a favourable result.
    environment: str = "synthetic_fixture"
    #: The declared treatment difference.  ``None`` means the comparison can never
    #: be matched -- a deliberate fail-closed default.
    treatment_spec: TreatmentSpec | None = None
    #: Identity of the harness (the code that *runs* the experiment) and of the run
    #: environment.  Compared as controls against the baseline evidence.
    harness_version: str = ""
    environment_fingerprint: str = ""
    #: Files this run changed beyond the frozen treatment.  Anything here that the
    #: treatment contract did not declare makes the comparison unmatched.
    changed_paths: Sequence[str] = ()
    #: The code version the *candidate* actually ran.  Empty means "same as
    #: ``code_version``".  A code-evolution experiment sets this to the patched
    #: revision and declares it in the treatment contract, so a differing code
    #: version is legal *only* as the declared treatment.
    candidate_code_version: str = ""


@dataclass
class RunnerResult:
    bet_id: str
    state: str
    reason: str
    settlement: SettlementDecision | None = None
    experience: ExperienceRecord | None = None
    receipt: ExecutionReceipt | None = None
    measurement: MeasurementResult | None = None
    baseline_evidence: BaselineEvidence | None = None
    paths: Mapping[str, str] = field(default_factory=dict)
    budget: Mapping[str, Any] = field(default_factory=dict)
    replayed: bool = False
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "bet_id": self.bet_id, "state": self.state, "reason": self.reason,
            "settlement": None if self.settlement is None else self.settlement.to_dict(),
            "experience": None if self.experience is None else self.experience.to_dict(),
            "receipt": None if self.receipt is None else self.receipt.to_dict(),
            "measurement": None if self.measurement is None else self.measurement.to_dict(),
            "baseline_evidence": (None if self.baseline_evidence is None
                                  else self.baseline_evidence.to_dict()),
            "paths": dict(self.paths), "budget": dict(self.budget),
            "replayed": bool(self.replayed), "notes": list(self.notes),
        }


class BetRunner:
    """Walks one bet.  Call ``run`` once per process; replays are no-ops."""

    def __init__(self, *, store: CommitmentStore, config: RunnerConfig, clock,
                 proposer: CandidateProposer, executor: ActionExecutor,
                 evaluator, snapshot_reader: StateReader,
                 selector: GuardedGainSelector | None = None,
                 freezer: Freezer | None = None,
                 forecaster: ConsequenceForecaster | None = None,
                 external_learning: ExternalLearningRequester | None = None,
                 baseline_provider: BaselineEvidenceProvider | None = None,
                 owner_id: str = "") -> None:
        self.store = store
        self.config = config
        self.clock = clock
        self.proposer = proposer
        self.executor = executor
        self.evaluator = evaluator
        self.snapshot_reader = snapshot_reader
        self.selector = selector or GuardedGainSelector()
        self.freezer = freezer or Freezer()
        self.forecaster = forecaster or ShadowConsequenceForecaster()
        self.external_learning = external_learning or NullExternalLearningRequester()
        # Deliberately defaulted to None: a runner without an evidence provider
        # cannot settle, rather than falling back to a declared scalar.
        self.baseline_provider = baseline_provider
        self.owner_id = owner_id or f"runner-{sha256_of({'bet': store.bet_id, 't': time.time()})[:8]}"

    # -- public --------------------------------------------------------------

    def run(self) -> RunnerResult:
        """Advance this bet until it reaches a terminal state.  Bounded by design."""
        report = self.store.verify_chain()
        if not report.ok:
            lifecycle = sm.BetLifecycle(bet_id=self.store.bet_id)
            sm.advance(lifecycle, sm.TransitionRequest(
                target=sm.INVALID, now=self.clock.now(),
                reason=f"chain_integrity_failure:{report.reason}"))
            self.store.save_lifecycle(lifecycle, reason="chain_integrity_failure")
            return RunnerResult(self.store.bet_id, lifecycle.state,
                                f"chain integrity failure: {report.reason}", replayed=False,
                                notes=("fail_closed",))

        lifecycle = self.store.load_lifecycle()
        if sm.is_terminal(lifecycle.state):
            return self._replay_result(lifecycle)

        ledger = BudgetLedger(self.config.budget, lifecycle.usage, clock=self.clock)
        try:
            return self._walk(lifecycle, ledger)
        except StoreIntegrityError as exc:
            self.store.record_issue("runner_store_error", {"error": str(exc)})
            lifecycle = self.store.load_lifecycle()
            return RunnerResult(self.store.bet_id, lifecycle.state, f"store_error: {exc}",
                                replayed=False, notes=("fail_closed",))

    # -- internals -----------------------------------------------------------

    def _walk(self, lifecycle, ledger: BudgetLedger) -> RunnerResult:
        paths: dict[str, str] = {}

        # 1. freeze the context snapshot the bet is judged against
        snapshot = dict(self.snapshot_reader.read_frozen_state())
        snapshot_hash = self.store.save_context_snapshot(snapshot)
        paths["context_snapshot"] = str(self.store.path("context", "snapshot.json"))

        # 2. propose
        decision = ledger.may_spend(model_calls=1)
        if not decision.allowed:
            return self._stop(lifecycle, sm.BUDGET_EXHAUSTED, decision.reason, paths)
        proposal = self.proposer.propose(question=self.config.question, snapshot=snapshot,
                                         max_candidates=self.config.max_candidates)
        ledger.charge(model_calls=int(proposal.model_calls))
        if not proposal.candidates:
            reason = proposal.failure or "proposer returned no candidates"
            return self._stop(lifecycle, sm.BLOCKED,
                              f"no candidate direction available: {reason}", paths)
        sm.advance(lifecycle, sm.TransitionRequest(
            target=sm.PROPOSED, now=self.clock.now(),
            reason=f"proposed {len(proposal.candidates)} candidate(s) by {proposal.proposed_by}"))

        # 3. select exactly one
        try:
            selection = self.selector.select(candidates=proposal.candidates, snapshot=snapshot)
        except SelectionRefused as exc:
            return self._stop(lifecycle, sm.BLOCKED, f"selection refused: {exc}", paths)

        # 4. freeze expectations, failure conditions, protocol and budget
        bet_id = self.store.bet_id
        # The treatment difference is frozen here, once the direction is chosen, so
        # the comparison later has a fixed expectation to be matched against.
        treatment = None
        if self.config.treatment_spec is not None:
            spec = self.config.treatment_spec
            treatment = make_treatment_contract(
                baseline_treatment=spec.baseline_treatment,
                candidate_treatment=selection.selected.candidate_id + ":" +
                                    canonical_json(dict(selection.selected.params)),
                declared_paths=tuple(spec.declared_paths),
                allows_code_change=bool(spec.allows_code_change), note=spec.note)
        record = self.freezer.freeze(
            FreezeRequest(
                partner_id=self.config.partner_id, project_id=self.config.project_id,
                run_id=self.config.run_id, question=self.config.question,
                context_snapshot_ref=self.config.context_snapshot_ref,
                context_snapshot_hash=snapshot_hash,
                candidates=proposal.candidates, selection=selection,
                expected_effects=self.config.expected_effects,
                falsification_conditions=self.config.falsification_conditions,
                evaluation_protocol=self.config.evaluation_protocol,
                baseline_ref=self.config.baseline_ref, budget=self.config.budget,
                commitment_policy=self.config.commitment_policy,
                code_version=self.config.code_version, data_version=self.config.data_version,
                model_config_ref=self.config.model_config_ref,
                environment=self.config.environment, treatment=treatment),
            bet_id=bet_id, now_iso=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self.store.save_bet(record)
        paths["bet"] = str(self.store.path("bet.json"))
        sm.advance(lifecycle, sm.TransitionRequest(target=sm.COMMITTED, now=self.clock.now(),
                                                   reason="expectations frozen"))
        self.store.save_lifecycle(lifecycle, reason="committed")

        # 5. claim the single execution owner for this revision (invariant 1)
        if not self._claim(f"execution_claim:r{record.revision}"):
            return self._stop(lifecycle, sm.BLOCKED,
                              "another owner already claimed this revision", paths)

        # 5a. the baseline must be *evidence*: executed, measured, hashed.  A number
        # read from a snapshot is not admissible, and its absence stops the bet here
        # instead of being papered over with a declared value (fix 3).
        decision = ledger.may_spend(actions=1)
        if not decision.allowed:
            return self._stop(lifecycle, sm.BUDGET_EXHAUSTED, decision.reason, paths)
        if self.baseline_provider is None:
            return self._stop(lifecycle, sm.BLOCKED,
                              "baseline evidence unavailable: no baseline provider is configured",
                              paths)
        try:
            baseline_evidence = self.baseline_provider.provide(bet=record, store=self.store)
        except Exception as exc:  # noqa: BLE001 -- an unusable baseline is reported, not hidden
            self.store.record_issue("baseline_provider_error",
                                    {"error": f"{type(exc).__name__}: {exc}"})
            return self._stop(lifecycle, sm.BLOCKED,
                              f"baseline evidence unavailable: {type(exc).__name__}: {exc}", paths)
        ledger.charge(actions=1)
        if baseline_evidence is None:
            return self._stop(lifecycle, sm.BLOCKED,
                              "baseline evidence unavailable: provider returned no admissible "
                              "baseline", paths)
        self.store.save_baseline_evidence(baseline_evidence)
        paths["baseline_evidence"] = str(
            self.store.artifact_path("baseline", baseline_evidence.baseline_id))

        decision = ledger.may_spend(actions=1)
        if not decision.allowed:
            return self._stop(lifecycle, sm.BUDGET_EXHAUSTED, decision.reason, paths)
        sm.advance(lifecycle, sm.TransitionRequest(target=sm.EXECUTING, now=self.clock.now(),
                                                   reason="bounded action started"))
        receipt = self.executor.execute(bet=record, candidate=selection.selected,
                                       workspace=str(self.store.workspace))
        self.store.save_receipt(receipt)
        paths["receipt"] = str(self.store.artifact_path("receipt", receipt.receipt_id))
        ledger.charge(actions=1)
        self.store.save_lifecycle(lifecycle, reason="executing")

        if not receipt.is_valid:
            sm.advance(lifecycle, sm.TransitionRequest(
                target=sm.BLOCKED, now=self.clock.now(), receipt=receipt,
                reason=f"execution_{receipt.status}: {receipt.failure_reason}"))
            self.store.save_lifecycle(lifecycle, reason="execution_blocked")
            return RunnerResult(bet_id, lifecycle.state,
                                f"execution {receipt.status}: {receipt.failure_reason}",
                                receipt=receipt, paths=paths,
                                budget=ledger.snapshot())

        # 6. measure independently.  An evaluator that refuses to measure (e.g.
        # it was pointed at an agent self-report) is a fail-closed INVALID, not a
        # crash and never a zero.
        from .evaluator import EvaluationError
        try:
            measured = self.evaluator.measure(bet=record, receipt=receipt)
        except EvaluationError as exc:
            sm.advance(lifecycle, sm.TransitionRequest(
                target=sm.INVALID, now=self.clock.now(), receipt=receipt,
                reason=f"evaluator_refused: {exc}"))
            self.store.save_lifecycle(lifecycle, reason="evaluator_refused")
            return RunnerResult(bet_id, lifecycle.state, f"evaluator refused: {exc}",
                                receipt=receipt, paths=paths, budget=ledger.snapshot(),
                                notes=("fail_closed",))
        for measurement in measured.measurements:
            self.store.save_measurement(measurement)
        paths["measurement_dir"] = str(self.store.path("measurements"))
        if not measured.is_valid:
            sm.advance(lifecycle, sm.TransitionRequest(
                target=sm.INVALID, now=self.clock.now(), receipt=receipt,
                measurement=measured.primary(record.expected_effects[0].metric),
                reason=f"measurement_{measured.validity}: {measured.missing_reason}"))
            self.store.save_lifecycle(lifecycle, reason="measurement_invalid")
            return RunnerResult(bet_id, lifecycle.state,
                                f"measurement {measured.validity}: {measured.missing_reason}",
                                receipt=receipt, measurement=measured, paths=paths,
                                budget=ledger.snapshot())

        primary = measured.primary(record.expected_effects[0].metric)
        sm.advance(lifecycle, sm.TransitionRequest(
            target=sm.MEASURED, now=self.clock.now(), receipt=receipt, measurement=primary,
            reason=f"measured {primary.metric}={primary.value}"))

        # 7. settle against the admissible baseline evidence
        next_state, next_reason = post_settlement_decision(
            settlement_class="pending", lifecycle=lifecycle, budget=self.config.budget,
            usage=ledger.usage, max_rounds=int(self.config.budget.rounds), clock=self.clock)
        settlement = settle(SettlementRequest(
            bet=record, receipt=receipt, measured=measured, baseline=baseline_evidence,
            input_hash=record.context_snapshot_hash,
            environment_fingerprint=self.config.environment_fingerprint,
            harness_version=self.config.harness_version,
            changed_paths=tuple(self.config.changed_paths),
            code_version=self.config.candidate_code_version or self.config.code_version,
            next_state=next_state,
            publish_gate_reasons=tuple(self.config.publish_gate_reasons),
            now_iso=time.strftime("%Y-%m-%dT%H:%M:%S")))
        if not self._claim(f"settlement_claim:{settlement.settlement_id}"):
            return self._stop(lifecycle, sm.BLOCKED,
                              "another owner already settled this bet", paths)
        self.store.save_settlement(settlement)
        paths["settlement"] = str(self.store.artifact_path("settlement", settlement.settlement_id))
        sm.advance(lifecycle, sm.TransitionRequest(
            target=sm.SETTLED, now=self.clock.now(), receipt=receipt, measurement=primary,
            settlement=settlement, reason=f"settled:{settlement.settlement_class}"))
        self.store.save_lifecycle(lifecycle, reason="settled")

        # 8. one experience, from a valid settlement only (invariant 5: exactly once)
        experience = None
        if settlement.settlement_class not in ("invalid", "blocked") and not lifecycle.experience_emitted:
            experience = build_experience(settlement=settlement, bet=record, receipt=receipt)
            self.store.save_experience(experience)
            paths["experience"] = str(self.store.artifact_path("experience", experience.experience_id))
            lifecycle.experience_emitted = True
            lifecycle.usage.rounds = int(lifecycle.usage.rounds) + 1

        terminal, terminal_reason = post_settlement_decision(
            settlement_class=settlement.settlement_class, lifecycle=lifecycle,
            budget=self.config.budget, usage=ledger.usage,
            max_rounds=int(self.config.budget.rounds), clock=self.clock)
        sm.advance(lifecycle, sm.TransitionRequest(
            target=terminal, now=self.clock.now(),
            reason=f"{terminal_reason}; next_round={'none' if terminal != sm.COMMITTED else 'bounded'}"))
        self.store.save_lifecycle(lifecycle, reason=terminal)
        manifest = self.store.write_manifest(extra={"settlement_class": settlement.settlement_class,
                                                   "publish_eligible": settlement.publish_eligible})
        paths["manifest"] = str(self.store.path("manifest.json"))
        return RunnerResult(bet_id, lifecycle.state, terminal_reason, settlement=settlement,
                            experience=experience, receipt=receipt, measurement=measured,
                            baseline_evidence=baseline_evidence,
                            paths=paths, budget=ledger.snapshot(),
                            notes=(f"manifest_chain_ok={manifest['chain']['ok']}",
                                   f"next_state={terminal}"))

    # -- helpers -------------------------------------------------------------

    def _claim(self, event_id: str) -> bool:
        """Claim a single-owner slot.  First writer wins; later owners are refused."""
        existing = self.store.append("owner_claim", {"owner": self.owner_id}, event_id=event_id)
        return existing.get("payload", {}).get("owner") == self.owner_id

    def _stop(self, lifecycle, target: str, reason: str, paths: Mapping[str, str]) -> RunnerResult:
        if not sm.is_terminal(lifecycle.state):
            sm.advance(lifecycle, sm.TransitionRequest(target=target, now=self.clock.now(),
                                                       reason=reason))
            self.store.save_lifecycle(lifecycle, reason=target)
        return RunnerResult(self.store.bet_id, lifecycle.state, reason, paths=dict(paths))

    def _replay_result(self, lifecycle) -> RunnerResult:
        """Return the recorded outcome of an already-finished bet.  No side effects."""
        settlement = None
        experience = None
        if lifecycle.settlement_id:
            try:
                settlement = self.store.load_settlement(lifecycle.settlement_id)
            except (OSError, ValueError, ContractError):
                settlement = None
        if lifecycle.experience_emitted:
            for identifier in self.store.list_artifacts("experience"):
                try:
                    experience = self.store.load_experience(identifier)
                except (OSError, ValueError, ContractError):
                    continue
                break
        return RunnerResult(self.store.bet_id, lifecycle.state,
                            f"already terminal: {lifecycle.closed_reason or lifecycle.state}",
                            settlement=settlement, experience=experience, replayed=True,
                            paths={"state": str(self.store.path("state.json"))},
                            notes=("idempotent_replay",))


__all__ = ["BetRunner", "RunnerConfig", "RunnerResult"]
