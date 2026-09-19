"""Evidence settlement: the machine verdict on a bet.

This is the module that must never be talked into a conclusion.  Three
corrections from 2026-09-19 are structural here, not cosmetic:

1. **Reaching a threshold is not improving on the baseline.**  ``expectations_met``,
   ``improvement_over_baseline`` and ``baseline_already_satisfied`` are three
   different machine answers computed from the expectation *kind*, never from a
   field name.
2. **A baseline is evidence, not a number.**  No measurement without an
   admissible :class:`~.models.BaselineEvidence`; a bare scalar yields
   ``inconclusive`` and is never patched with zeros or declarations.
3. **The environment decides publishability.**  Isolated, synthetic and shadow
   evidence can be *supported* and still be unpublishable by contract.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .evaluator import MeasurementResult
from .evidence import CONTROL_CHECKS, admissibility, compatibility_report
from .models import (
    BLOCKER_BASELINE_INCOMPATIBLE, BLOCKER_ISOLATED_SAMPLE, BLOCKER_NOT_PUBLISHABLE_ENVIRONMENT,
    BLOCKER_SHADOW, BLOCKER_SINGLE_EPISODE, BLOCKER_SYNTHETIC, BLOCKER_TREATMENT_UNDECLARED,
    CLAIM_BY_KIND, NON_PUBLISHABLE_ENVIRONMENTS, BaselineEvidence, BetRecord, ComparisonProof,
    ContractError, ExpectationOutcome, ExperienceRecord, ExecutionReceipt, FalsificationCondition,
    SettlementDecision, TreatmentContract, canonical_json, sha256_of,
)

#: Claim priority when several expectations are met at once: the strongest
#: statement the evidence actually licenses.
CLAIM_PRIORITY = ("improvement_over_baseline", "absolute_attainment", "non_inferiority",
                  "guardrail_held")


def protocol_hash(bet: BetRecord) -> str:
    return sha256_of(bet.evaluation_protocol.to_dict())


def budget_hash(bet: BetRecord) -> str:
    """The budget *口径*, not its consumption: what the arms were allowed."""
    return sha256_of({"wall_clock_seconds": int(bet.budget.wall_clock_seconds),
                      "model_calls": int(bet.budget.model_calls),
                      "actions": int(bet.budget.actions),
                      "rounds": int(bet.budget.rounds)})


def treatment_diff_hash(*, baseline_treatment: str, candidate_treatment: str,
                        changed_paths: tuple[str, ...] = ()) -> str:
    """Identity of the *difference* between the arms.

    Two runs are comparable when this hash equals the frozen expectation: the
    declared treatment difference, and nothing besides it, is what changed.
    """
    return sha256_of({"baseline_treatment": baseline_treatment,
                      "candidate_treatment": candidate_treatment,
                      "changed_paths": sorted(str(p) for p in changed_paths)})


def make_treatment_contract(*, baseline_treatment: str, candidate_treatment: str,
                            declared_paths: tuple[str, ...] = (),
                            allows_code_change: bool = False,
                            note: str = "") -> TreatmentContract:
    """Freeze the single allowed treatment difference.

    ``expected_diff_hash`` is derived here rather than typed in by hand, so the
    frozen value and the settlement-time recomputation cannot drift apart.
    """
    return TreatmentContract(
        baseline_treatment=baseline_treatment, candidate_treatment=candidate_treatment,
        expected_diff_hash=treatment_diff_hash(baseline_treatment=baseline_treatment,
                                              candidate_treatment=candidate_treatment,
                                              changed_paths=declared_paths),
        declared_paths=tuple(declared_paths), allows_code_change=bool(allows_code_change),
        note=note)


def treatment_descriptor(bet: BetRecord) -> str:
    """The candidate arm's treatment, derived from the *frozen* record.

    Binding it to the frozen selection (id plus parameters) means a settlement
    cannot silently describe a different treatment than the one that was
    committed to, and the value is inside the freeze hash.
    """
    selected = next((c for c in bet.candidates if c.candidate_id == bet.selected_action), None)
    params = {} if selected is None else dict(selected.params)
    return f"{bet.selected_action}:{canonical_json(params)}"


@dataclass(frozen=True)
class SettlementRequest:
    """Everything settlement is allowed to look at.

    Note what is *not* here: no agent verdict, no LLM score, no snapshot scalar
    standing in for a baseline.
    """

    bet: BetRecord
    receipt: ExecutionReceipt
    measured: MeasurementResult
    baseline: BaselineEvidence | None = None
    # -- candidate-side control context -------------------------------------
    input_hash: str = ""
    environment_fingerprint: str = ""
    harness_version: str = ""
    treatment: str = ""
    changed_paths: tuple[str, ...] = ()
    code_version: str = ""
    # -- optional human/gate input -----------------------------------------
    publish_gate_reasons: tuple[str, ...] = ()
    next_state: str = "CLOSED"
    now_iso: str = ""
    llm_explanation: str = ""


def settle(request: SettlementRequest) -> SettlementDecision:
    bet = request.bet
    receipt = request.receipt
    measured = request.measured
    rules: list[str] = []

    # -- execution and measurement gates -------------------------------------
    if not receipt.is_valid:
        return _decision(bet, request, "blocked",
                         rules + [f"receipt_invalid:{receipt.failure_reason or receipt.status}"],
                         expectations_met=False, improvement=False, baseline_satisfied=False,
                         claim="none", outcomes=(), comparison=None,
                         blockers=[BLOCKER_NOT_PUBLISHABLE_ENVIRONMENT
                                   if bet.environment in NON_PUBLISHABLE_ENVIRONMENTS else
                                   "execution_not_successful"])
    if not measured.is_valid:
        return _decision(bet, request, "invalid",
                         rules + [f"measurement_invalid:{measured.validity}:"
                                  f"{measured.missing_reason}"],
                         expectations_met=False, improvement=False, baseline_satisfied=False,
                         claim="none", outcomes=(), comparison=None,
                         blockers=["measurement_not_valid"])

    # -- baseline admissibility (fix: a scalar is not evidence) --------------
    admissible, reasons, checks = admissibility(
        request.baseline, bet=bet,
        candidate_receipt=receipt,
        candidate_evaluator_id=measured.primary(bet.expected_effects[0].metric).measured_by,
        candidate_evaluator_version=measured.primary(
            bet.expected_effects[0].metric).evaluator_version,
        candidate_input_hash=request.input_hash,
        candidate_environment=bet.environment,
        candidate_environment_fingerprint=request.environment_fingerprint,
        candidate_harness_version=request.harness_version,
        candidate_protocol_hash=protocol_hash(bet),
        candidate_budget_hash=budget_hash(bet))
    baseline = request.baseline

    # -- treat/control comparison (fix: whole-code-identity is not the gate) --
    comparison = _comparison(bet, request, measured, baseline, checks)
    rules.append(f"treatment_diff_hash={comparison.treatment_diff_hash[:12] or 'none'} "
                 f"expected={comparison.expected_treatment_diff_hash[:12] or 'none'}")
    rules.append(f"controls_identical={comparison.controls_identical} "
                 f"treatment_as_frozen={comparison.treatment_as_frozen}")
    if comparison.code_changed:
        rules.append("code_changed=True (legal only when the frozen contract declares it)")

    # -- per-expectation outcomes, computed by kind --------------------------
    outcomes: list[ExpectationOutcome] = []
    for effect in bet.expected_effects:
        candidate_value = measured.metric_values.get(effect.metric)
        baseline_value = None if baseline is None else baseline.metric_values.get(effect.metric)
        relative: bool | None = None
        baseline_satisfied: bool | None = None
        if effect.kind == "non_inferiority":
            # a non-inferiority expectation has no absolute rule at all: its
            # acceptance rule *is* the relative one, and the baseline cannot be
            # "already satisfied" by itself.
            if candidate_value is None:
                met, note = False, "metric_missing"
            elif baseline_value is None:
                met, note = False, "baseline_value_missing"
            else:
                relative = effect.improvement_over(float(baseline_value), float(candidate_value))
                met = bool(relative)
                note = "within_tolerance_of_baseline" if met else "degraded_beyond_tolerance"
            outcomes.append(ExpectationOutcome(
                metric=effect.metric, direction=effect.direction, threshold=float(effect.threshold),
                observed=None if candidate_value is None else float(candidate_value), met=bool(met),
                note=note, kind=effect.kind, baseline=baseline_value,
                improvement_over_baseline=relative, baseline_satisfied=None))
            continue
        met = candidate_value is not None and effect.met_by(float(candidate_value))
        if candidate_value is None:
            note = "metric_missing"
        elif baseline_value is None:
            note = "baseline_value_missing"
        else:
            baseline_satisfied = effect.met_by(float(baseline_value))
            if effect.kind == "delta_over_baseline":
                relative = effect.improvement_over(float(baseline_value), float(candidate_value))
            if effect.kind == "delta_over_baseline":
                delta = float(candidate_value) - float(baseline_value)
                if met and relative:
                    note = f"improvement_over_baseline delta={round(delta, 6)}>={effect.min_delta}"
                elif relative:
                    note = f"delta={round(delta, 6)} but below threshold {effect.threshold}"
                else:
                    note = f"delta={round(delta, 6)} is not an improvement over the baseline"
            elif effect.kind == "non_inferiority":
                note = ("within_tolerance_of_baseline" if relative
                        else "degraded_beyond_tolerance")
            elif effect.kind == "guardrail":
                note = "guardrail_held" if met else "guardrail_breached"
            else:
                note = "absolute_threshold_met" if met else "absolute_threshold_missed"
            if baseline_satisfied and not met:
                note += ";baseline_already_satisfied_here"
        outcomes.append(ExpectationOutcome(
            metric=effect.metric, direction=effect.direction, threshold=float(effect.threshold),
            observed=None if candidate_value is None else float(candidate_value), met=bool(met),
            note=note, kind=effect.kind, baseline=baseline_value,
            improvement_over_baseline=relative, baseline_satisfied=baseline_satisfied))

    # -- the three separated concepts ---------------------------------------
    expectations_met = bool(outcomes) and all(o.met for o in outcomes)
    delta_outcomes = [o for o in outcomes if o.kind == "delta_over_baseline"]
    improvement = bool(delta_outcomes) and all(
        o.met and bool(o.improvement_over_baseline) for o in delta_outcomes)
    graded_baseline = [o for o in outcomes if o.baseline_satisfied is not None]
    baseline_already_satisfied = bool(graded_baseline) and all(
        bool(o.baseline_satisfied) for o in graded_baseline)
    rules.append(f"expectations_met={expectations_met}")
    rules.append(f"improvement_over_baseline={improvement}")
    rules.append(f"baseline_already_satisfied={baseline_already_satisfied}")
    if expectations_met and baseline_already_satisfied and not improvement:
        rules.append("thresholds reached but the baseline already satisfied them: "
                     "no improvement is claimed")

    # -- regressions ---------------------------------------------------------
    new_regressions: list[str] = []
    pre_existing: list[str] = []
    for outcome in outcomes:
        if outcome.baseline_satisfied is None:
            continue
        if outcome.baseline_satisfied and not outcome.met:
            new_regressions.append(outcome.metric)
            rules.append(f"new_regression[{outcome.metric}]: baseline met it, candidate did not")
        elif not outcome.baseline_satisfied and not outcome.met:
            pre_existing.append(outcome.metric)
            rules.append(f"pre_existing_failure[{outcome.metric}]: both arms failed it")

    # -- frozen falsification conditions ------------------------------------
    violations = evaluate_falsification_conditions(bet, measured)
    for condition in violations:
        rules.append(f"falsification[{condition['code']}] violated: {condition['detail']}")

    # -- class ---------------------------------------------------------------
    if not admissible:
        settlement_class = "inconclusive"
        rules.append("baseline_not_admissible: " + "; ".join(reasons))
        blockers = [BLOCKER_BASELINE_INCOMPATIBLE] + list(reasons)
    elif not comparison.matched:
        settlement_class = "inconclusive"
        rules.append("comparison_mismatched: " + ", ".join(comparison.mismatch_reasons()))
        blockers = [f"comparison_mismatch:{name}" for name in comparison.mismatch_reasons()]
    elif expectations_met and not new_regressions:
        settlement_class = "supported"
        rules.append("thresholds met under a matched comparison")
        blockers = []
    else:
        settlement_class = "falsified"
        rules.append("at least one expectation was not met under a matched comparison")
        blockers = []

    claim = _claim(outcomes, expectations_met, improvement)
    rules.append(f"supported_claim={claim}")

    # -- publish gate: environment first, then evidence quality -------------
    if bet.environment in NON_PUBLISHABLE_ENVIRONMENTS:
        blockers.append(f"{BLOCKER_NOT_PUBLISHABLE_ENVIRONMENT}:{bet.environment}")
        if bet.environment == "synthetic_fixture":
            blockers.append(BLOCKER_SYNTHETIC)
        if bet.environment == "isolated_sample":
            blockers.append(BLOCKER_ISOLATED_SAMPLE)
        if bet.environment == "shadow":
            blockers.append(BLOCKER_SHADOW)
    if int(bet.evaluation_protocol.replicates) < 2:
        blockers.append(BLOCKER_SINGLE_EPISODE)
    if bet.treatment is None:
        blockers.append(BLOCKER_TREATMENT_UNDECLARED)
    blockers.extend(request.publish_gate_reasons)
    blockers.extend(f"falsification_violation:{c['code']}" for c in violations)
    blockers.extend(f"new_regression:{metric}" for metric in new_regressions)
    if not expectations_met:
        blockers.append("expectations_not_met")
    if settlement_class != "supported":
        blockers.append(f"settlement_class_not_supported:{settlement_class}")
    blockers = list(dict.fromkeys(blockers))

    rules.append(f"publish_blockers={blockers or ['none']}")
    publish_eligible = settlement_class == "supported" and not blockers
    rules.append(f"publish_eligible={publish_eligible}")

    return _decision(bet, request, settlement_class, rules,
                     expectations_met=expectations_met, improvement=improvement,
                     baseline_satisfied=baseline_already_satisfied, claim=claim,
                     outcomes=tuple(outcomes), comparison=comparison, blockers=blockers,
                     new_regressions=tuple(new_regressions),
                     pre_existing=tuple(pre_existing))


def _claim(outcomes, expectations_met: bool, improvement: bool) -> str:
    """Which statement the evidence actually licenses.

    Thresholds being reached is a weaker claim than beating the baseline, so when
    the baseline already satisfied them the honest claim is absolute attainment --
    never improvement, and never a silent ``none`` that would contradict a
    ``supported`` verdict.
    """
    if not expectations_met:
        return "none"
    has_relative = any(o.kind == "delta_over_baseline" for o in outcomes)
    if has_relative and improvement:
        return "improvement_over_baseline"
    if has_relative or any(o.kind == "absolute_threshold" for o in outcomes):
        return "absolute_attainment"
    if any(o.kind == "non_inferiority" for o in outcomes):
        return "non_inferiority"
    if any(o.kind == "guardrail" for o in outcomes):
        return "guardrail_held"
    return "none"


def _comparison(bet: BetRecord, request: SettlementRequest, measured: MeasurementResult,
                baseline: BaselineEvidence | None, checks: Mapping[str, bool]) -> ComparisonProof:
    contract = bet.treatment
    if contract is None:
        expected_diff = ""
        baseline_treatment = ""
        candidate_treatment = request.treatment
    else:
        expected_diff = contract.expected_diff_hash
        baseline_treatment = contract.baseline_treatment
        candidate_treatment = request.treatment or treatment_descriptor(bet)
    actual_diff = treatment_diff_hash(baseline_treatment=baseline_treatment,
                                      candidate_treatment=candidate_treatment,
                                      changed_paths=tuple(request.changed_paths))
    harness_identical = bool(checks.get("harness_version_identical"))
    code_changed = False
    if contract is not None:
        undeclared = sorted(set(request.changed_paths) - set(contract.declared_paths))
        if undeclared:
            harness_identical = False
        if baseline is not None and request.code_version and baseline.harness_version:
            code_changed = request.code_version != bet.code_version
            if code_changed and not contract.allows_code_change:
                harness_identical = False
    baseline_value = None
    if baseline is not None:
        metric = bet.expected_effects[0].metric
        baseline_value = baseline.metric_values.get(metric)
    metric0 = bet.expected_effects[0].metric
    candidate_value = measured.metric_values.get(metric0)
    delta = (None if baseline_value is None or candidate_value is None
             else round(float(candidate_value) - float(baseline_value), 9))
    return ComparisonProof(
        inputs_identical=bool(checks.get("inputs_identical")),
        evaluator_identical=bool(checks.get("evaluator_identical")),
        protocol_identical=bool(checks.get("protocol_identical")),
        budget_comparable=bool(checks.get("budget_comparable")),
        environment_identical=bool(checks.get("environment_identical")),
        harness_version_identical=harness_identical,
        baseline_treatment=baseline_treatment, candidate_treatment=candidate_treatment,
        treatment_diff_hash=actual_diff, expected_treatment_diff_hash=expected_diff,
        baseline_code_version="" if baseline is None else bet.code_version,
        candidate_code_version=request.code_version or bet.code_version,
        baseline_value=baseline_value, candidate_value=candidate_value, delta=delta,
        baseline_evidence_ref="" if baseline is None else baseline.baseline_id,
        baseline_provenance="" if baseline is None else baseline.provenance,
        detail="control variables must match and the treatment difference must be exactly the "
               "frozen one")


def _decision(bet: BetRecord, request: SettlementRequest, settlement_class: str,
              rules: list[str], *, expectations_met: bool, improvement: bool,
              baseline_satisfied: bool, claim: str,
              outcomes: tuple[ExpectationOutcome, ...],
              comparison: ComparisonProof | None,
              blockers: list[str],
              new_regressions: tuple[str, ...] = (),
              pre_existing: tuple[str, ...] = ()) -> SettlementDecision:
    now = time.time()
    if comparison is None:
        comparison = ComparisonProof(
            inputs_identical=False, evaluator_identical=False, protocol_identical=False,
            budget_comparable=False, environment_identical=False,
            harness_version_identical=False, baseline_treatment="", candidate_treatment="",
            treatment_diff_hash="", expected_treatment_diff_hash="",
            baseline_code_version="", candidate_code_version=request.code_version,
            baseline_value=None, candidate_value=None, delta=None,
            detail="no comparison was computed")
    blockers = list(dict.fromkeys(blockers))
    return SettlementDecision(
        settlement_id=f"stl_{bet.bet_id}_r{bet.revision}",
        bet_id=bet.bet_id, settlement_class=settlement_class,
        expectation_outcomes=tuple(outcomes), comparison=comparison,
        new_regressions=tuple(new_regressions), pre_existing_failures=tuple(pre_existing),
        expectations_met=bool(expectations_met), improvement_over_baseline=bool(improvement),
        baseline_already_satisfied=bool(baseline_satisfied), supported_claim=claim,
        environment=bet.environment, improvement_observed=bool(improvement),
        publish_eligible=(settlement_class == "supported" and not blockers),
        publish_blockers=tuple(blockers), next_state=request.next_state,
        machine_rules=tuple(rules or ["no rule recorded"]),
        llm_explanation=request.llm_explanation,
        llm_explanation_authoritative=False,
        created_at=request.now_iso or time.strftime("%Y-%m-%dT%H:%M:%S"))


def evaluate_falsification_conditions(bet: BetRecord,
                                     measured: MeasurementResult) -> list[dict[str, Any]]:
    """Machine evaluation of the frozen falsification conditions.

    ``metric_violation``      handled by the expectation outcomes
    ``guardrail_violation``   ``params={metric, limit}`` -- crosses the limit
    ``missing_evidence``      ``params={metric}`` -- metric absent or unparsable

    An unknown kind is reported as a violation, not ignored: a condition the
    kernel cannot evaluate must not silently pass.
    """
    violations: list[dict[str, Any]] = []
    for condition in bet.falsification_conditions:
        params = dict(condition.params or {})
        kind = condition.kind
        if kind == "metric_violation":
            continue  # expressed as an expectation and checked there
        if kind == "guardrail_violation":
            metric = str(params.get("metric") or "")
            limit = params.get("limit")
            value = measured.metric_values.get(metric)
            if value is None:
                violations.append({"code": condition.code, "kind": kind,
                                   "detail": f"{metric} unavailable, so the guardrail is unverified"})
                continue
            direction = str(params.get("direction") or "max")
            breached = (float(value) > float(limit)) if direction == "max" \
                else (float(value) < float(limit))
            if breached:
                violations.append({"code": condition.code, "kind": kind,
                                   "detail": f"{metric}={value} violates {direction} {limit}"})
            continue
        if kind == "missing_evidence":
            metric = str(params.get("metric") or "")
            if measured.metric_values.get(metric) is None:
                violations.append({"code": condition.code, "kind": kind,
                                   "detail": f"{metric} is missing"})
            continue
        violations.append({"code": condition.code, "kind": kind,
                           "detail": f"unknown falsification kind {kind!r}; treated as violated"})
    return violations


def build_experience(*, settlement: SettlementDecision, bet: BetRecord,
                     receipt: ExecutionReceipt) -> ExperienceRecord:
    """Experiences come only from valid settlements, never from blocked or invalid ones."""
    if settlement.settlement_class in ("invalid", "blocked"):
        raise ContractError(
            f"build_experience: settlement_class={settlement.settlement_class} cannot mint an "
            "experience; a failed measurement teaches nothing that can be stored as evidence")
    return ExperienceRecord(
        experience_id=f"exp_{bet.bet_id}_r{bet.revision}",
        bet_id=bet.bet_id, run_id=bet.run_id,
        settlement_ref=settlement.settlement_id,
        settlement_class=settlement.settlement_class,
        state_action_outcome_chain=(
            f"state:{bet.context_snapshot_hash}",
            f"action:{bet.selected_action}",
            f"receipt:{receipt.receipt_id}",
            f"settlement:{settlement.settlement_id}",
        ),
        scope=bet.project_id,
        confidence=1.0 if settlement.settlement_class == "supported" else 0.5,
        evidence_refs=(f"settlement:{settlement.settlement_id}",) +
                      tuple(f"metric:{o.metric}={o.observed}" for o in settlement.expectation_outcomes),
        level="experience", authoritative=False,
    )


__all__ = ["SettlementRequest", "settle", "build_experience", "protocol_hash", "budget_hash",
           "treatment_diff_hash", "make_treatment_contract", "treatment_descriptor",
           "evaluate_falsification_conditions",
           "CLAIM_PRIORITY", "CONTROL_CHECKS"]
