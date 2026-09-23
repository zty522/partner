"""Deterministic trigger policy for project, learning and self-evolution routes."""
from __future__ import annotations

from dataclasses import dataclass

from .models import Route, TypedJudgment


@dataclass(frozen=True)
class TriggerEvidence:
    user_authorized: bool = False
    objective_complete: bool = False
    settled: bool = False
    new_evidence: bool = False
    executable_next_action: bool = False
    budget_remaining: bool = False
    repeated_falsified_route: bool = False
    epistemic_gap: bool = False
    external_evidence_can_resolve: bool = False
    mechanism_defect: bool = False
    reproducible_defect: bool = False
    independent_evaluator: bool = False
    scientific_negative_result: bool = False
    data_scarcity: bool = False


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str
    jev_agrees: bool | None = None


def _jev_route(judgment: TypedJudgment | None) -> str:
    if judgment is None or judgment.status != "completed":
        return ""
    answer = judgment.answers.get("route") if isinstance(judgment.answers, dict) else None
    return str(answer.get("choice") or "") if isinstance(answer, dict) else ""


def route_next(evidence: TriggerEvidence, judgment: TypedJudgment | None = None) -> RouteDecision:
    """Choose a route from measured facts; Jev is recorded only as agreement.

    The ordering is intentional.  Verified completion closes the goal.  A Partner
    defect may trigger self-evolution only with a reproducer and independent
    evaluator.  Missing knowledge may trigger active learning only when external
    evidence can resolve it.  Negative science and data scarcity never become a
    software self-repair request.
    """
    if evidence.objective_complete and evidence.settled:
        chosen = Route.COMPLETE
        reason = "objective has settlement-backed completion evidence"
    elif (evidence.mechanism_defect and evidence.reproducible_defect
          and evidence.independent_evaluator
          and not evidence.scientific_negative_result and not evidence.data_scarcity):
        chosen = Route.SELF_EVOLUTION
        reason = "reproducible Partner mechanism defect has an independent evaluator"
    elif (evidence.epistemic_gap and evidence.external_evidence_can_resolve
          and not evidence.mechanism_defect):
        chosen = Route.ACTIVE_LEARNING
        reason = "external evidence can resolve the identified epistemic gap"
    elif (evidence.user_authorized and evidence.settled and evidence.new_evidence
          and evidence.executable_next_action and evidence.budget_remaining
          and not evidence.repeated_falsified_route):
        chosen = Route.CONTINUE_PROJECT
        reason = "settled evidence supports one bounded executable project iteration"
    else:
        chosen = Route.WAITING
        reason = "no deterministic trigger contract is fully satisfied"
    advice = _jev_route(judgment)
    return RouteDecision(chosen, reason, None if not advice else advice == chosen.value)
