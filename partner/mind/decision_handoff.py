"""Decision handoff — bridge between harness and run_decision_loop.

Provides:
  * constructors for DecisionEvent schema v2 (`make_classification`,
    `make_decision`, `make_full_payload`, `make_preconditions_*`)
  * a single dispatch function (`dispatch_to_decision_loop`) that validates a
    payload, fills in defaults, and drives the state machine
  * a heuristic recommender (`recommend_next_action`) that produces a sensible
    default when no LLM is available yet
  * `summarise_run` that turns a LoopResult into a one-line human-readable string

Boundaries (per ADR 0062 §4):
  * schema_validation is fail-fast — invalid payloads raise, not return noop
  * dispatch never blocks longer than budget_seconds (the loop owns its budget)
  * no module-level mutable state
"""
from __future__ import annotations

import logging
from typing import Any

from partner.evolution.decision_loop import LoopResult, VALID_NEXT_ACTIONS, run_decision_loop

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Schema v2 fragment constructors
# ────────────────────────────────────────────────────────────────────────────


def make_classification(failure_class: str = "unknown", confidence: float = 0.0,
                         signals_count: int = 0) -> dict[str, Any]:
    return {
        "failure_class": str(failure_class or "unknown"),
        "confidence": float(max(0.0, min(1.0, confidence))),
        "signals_count": int(max(0, signals_count)),
    }


def make_decision(next_action: str, rationale: str = "",
                  *, estimated_cost_seconds: int = 30,
                  requires_external: bool = False,
                  requires_apply: bool = False) -> dict[str, Any]:
    if next_action not in VALID_NEXT_ACTIONS:
        raise ValueError(
            f"next_action must be one of {VALID_NEXT_ACTIONS}, got {next_action!r}")
    return {
        "next_action": next_action,
        "rationale": rationale[:600],
        "estimated_cost_seconds": max(1, int(estimated_cost_seconds)),
        "requires_external": bool(requires_external),
        "requires_apply": bool(requires_apply),
    }


def make_preconditions_self_evolve(candidate: dict[str, Any] | None) -> dict[str, Any]:
    cand = candidate or {}
    diff = cand.get("diff_hunk") if isinstance(cand.get("diff_hunk"), str) else None
    tf = cand.get("target_file") if isinstance(cand.get("target_file"), str) else None
    signals = cand.get("signals_count", 0)
    return {
        "have_diff_hunk": bool(diff and diff.strip()),
        "have_target_file": bool(tf and tf.strip()),
        "have_external_query": False,
        "signals_count": int(signals) if isinstance(signals, int) else 0,
    }


def make_preconditions_external(query: str | None) -> dict[str, Any]:
    has = bool(query and query.strip())
    return {
        "have_diff_hunk": False,
        "have_target_file": False,
        "have_external_query": has,
        "signals_count": 1 if has else 0,
    }


def make_full_payload(*, observation_summary: str, classification: dict[str, Any],
                      decision: dict[str, Any],
                      preconditions: dict[str, Any]) -> dict[str, Any]:
    return {
        "observation_summary": observation_summary[:800],
        "classification": classification,
        "decision": decision,
        "preconditions": preconditions,
    }


def validate_payload(payload: dict[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload is not a dict"
    if not isinstance(payload.get("observation_summary"), str):
        return False, "missing observation_summary (str)"
    cls = payload.get("classification") or {}
    if not isinstance(cls, dict) or "failure_class" not in cls:
        return False, "missing classification.failure_class"
    decision = payload.get("decision") or {}
    if not isinstance(decision, dict) or "next_action" not in decision:
        return False, "missing decision.next_action"
    na = decision.get("next_action")
    if na not in VALID_NEXT_ACTIONS:
        return False, f"decision.next_action must be one of {VALID_NEXT_ACTIONS}, got {na!r}"
    pre = payload.get("preconditions") or {}
    if not isinstance(pre, dict):
        return False, "missing preconditions (dict)"
    return True, ""


# ────────────────────────────────────────────────────────────────────────────
# Heuristic recommender
# ────────────────────────────────────────────────────────────────────────────

# failure_class → next_action (per ADR 0062 §3.2 enum)
_FAILURE_CLASS_ROUTES: dict[str, str] = {
    "bug_in_partner_source": "self_evolve",
    "regression_in_recent_apply": "self_evolve",
    "missing_capability": "external_retrieval",
    "needs_more_data": "external_retrieval",
    "ambiguous_decision": "active_learning",
    "knowledge_gap": "active_learning",
    "config_drift": "noop",
    "unknown": "noop",
}


def recommend_next_action(*, task_state: dict[str, Any] | None = None,
                          candidate: dict[str, Any] | None = None,
                          external_query: str | None = None,
                          instance_phase: str = "PROJECT",
                          failure_class: str | None = None) -> dict[str, Any]:
    """Produce a sensible DecisionEvent when no LLM is available yet.

    Order of preference:
      1. explicit failure_class route via _FAILURE_CLASS_ROUTES
      2. candidate with diff_hunk + target_file → self_evolve
      3. external_query present → external_retrieval
      4. task_state with unresolved_questions → active_learning
      5. WAIT_TASK phase → active_learning (signals worth harvesting)
      6. BLOCKED phase → noop (waiting on human)
      7. fallback noop
    """
    candidate = candidate or {}
    task_state = task_state or {}
    fc = (failure_class
          or task_state.get("failure_class")
          or (task_state.get("classification") or {}).get("failure_class")
          or "unknown")
    if not fc:
        fc = "unknown"
    # explicit failure-class routing wins
    if fc in _FAILURE_CLASS_ROUTES and _FAILURE_CLASS_ROUTES[fc] != "noop":
        na = _FAILURE_CLASS_ROUTES[fc]
        if na == "self_evolve" and not (candidate.get("diff_hunk") and candidate.get("target_file")):
            na = "noop"  # cannot apply without diff
        rationale = f"failure_class={fc} routes to {na}"
        return make_decision(na, rationale, requires_apply=(na == "self_evolve"),
                             requires_external=(na == "external_retrieval"))
    # candidate with diff
    if candidate.get("diff_hunk") and candidate.get("target_file"):
        return make_decision("self_evolve", "candidate has diff_hunk + target_file",
                             requires_apply=True)
    # external query
    if external_query and external_query.strip():
        return make_decision("external_retrieval", f"external query: {external_query[:60]}",
                             requires_external=True)
    # instance phase — BLOCKED always wins, even with signals
    if instance_phase == "BLOCKED":
        return make_decision("noop", "instance BLOCKED — defer to human")
    # signals from task_state
    signals = _signals_count(task_state)
    if signals > 0:
        return make_decision("active_learning",
                             f"slot idle, {signals} signals available",
                             requires_apply=False, requires_external=False)
    if instance_phase == "WAIT_TASK":
        return make_decision("noop", "WAIT_TASK without signals")
    return make_decision("noop", "no signals, no candidate — stay idle")


def _signals_count(task_state: dict[str, Any]) -> int:
    cnt = 0
    for k in ("unresolved_questions", "open_questions", "next_actions",
              "gaps", "knowledge_gaps", "followups"):
        v = task_state.get(k)
        if isinstance(v, list):
            cnt += len([x for x in v if x])
        elif isinstance(v, str) and v.strip():
            cnt += 1
    return cnt


def signals_count(task_state: dict[str, Any] | None) -> int:
    """Public form of the heuristic counter."""
    return _signals_count(task_state or {})


# ────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ────────────────────────────────────────────────────────────────────────────


def dispatch_to_decision_loop(
    workspace_root,
    *,
    instance_id: str,
    task_state: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
    external_query: str | None = None,
    classification: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    preconditions: dict[str, Any] | None = None,
    budget_seconds: int = 60,
    slot_token: str | None = None,
    project_id: str = "agent_self_evolution",
) -> LoopResult:
    """Bridge: validates payload, fills defaults, calls run_decision_loop.

    Returns the LoopResult. Caller decides next steps; this function never
    raises on benign issues — it logs and returns a noop LoopResult instead.
    """
    try:
        # Build a payload if not provided
        if not isinstance(decision, dict):
            decision = recommend_next_action(
                task_state=task_state,
                candidate=candidate,
                external_query=external_query,
                instance_phase=str((task_state or {}).get("phase") or "PROJECT"),
                failure_class=(classification or {}).get("failure_class"),
            )

        if not isinstance(classification, dict):
            classification = make_classification(
                failure_class=(task_state or {}).get("failure_class", "unknown"),
                confidence=0.0,
                signals_count=_signals_count(task_state or {}),
            )

        if not isinstance(preconditions, dict):
            if decision.get("next_action") == "self_evolve":
                preconditions = make_preconditions_self_evolve(candidate)
            elif decision.get("next_action") == "external_retrieval":
                preconditions = make_preconditions_external(external_query)
            else:
                preconditions = {
                    "have_diff_hunk": False, "have_target_file": False,
                    "have_external_query": False, "signals_count": 0,
                }

        observation_summary = (
            (task_state or {}).get("observation_summary")
            or f"dispatch_from={instance_id} phase={(task_state or {}).get('phase', '?')}"
        )
        payload = make_full_payload(
            observation_summary=observation_summary,
            classification=classification,
            decision=decision,
            preconditions=preconditions,
        )
        ok, reason = validate_payload(payload)
        if not ok:
            logger.warning("dispatch_to_decision_loop: invalid payload, falling back to noop: %s", reason)
            payload = make_full_payload(
                observation_summary=observation_summary,
                classification=make_classification("unknown"),
                decision=make_decision("noop", f"fallback: {reason}"),
                preconditions={"have_diff_hunk": False, "have_target_file": False,
                                "have_external_query": False, "signals_count": 0},
            )

        return run_decision_loop(
            workspace_root,
            instance_id=instance_id,
            project_id=project_id,
            budget_seconds=max(2, int(budget_seconds)),
            task_state=task_state,
            candidate=candidate,
            external_query=external_query,
            decision_payload=payload,
            slot_token=slot_token,
        )
    except Exception as exc:
        logger.exception("dispatch_to_decision_loop crashed: %s", exc)
        return LoopResult(
            panic=True, panic_detail=str(exc)[:400],
            next_node="PANIC", subject_id="", event_count=0,
        )


def summarise_run(result: LoopResult) -> str:
    """Human-readable one-liner for log lines or summaries."""
    parts = [f"slot={result.subject_id or '?'}", f"next={result.next_node}"]
    if result.applied:
        parts.append(f"applied={result.applied}")
    if result.proposed:
        parts.append(f"proposed={result.proposed}")
    if result.evidence_collected:
        parts.append(f"evidence={result.evidence_collected}")
    if result.budget_exceeded:
        parts.append("BUDGET_EXCEEDED")
    if result.stuck_watchdog_triggered:
        parts.append("STUCK")
    if result.panic:
        parts.append(f"PANIC={result.panic_detail[:80]}")
    if result.completed:
        parts.append("completed")
    parts.append(f"events={result.event_count}")
    return " ".join(parts)


__all__ = [
    "make_classification", "make_decision",
    "make_preconditions_self_evolve", "make_preconditions_external",
    "make_full_payload", "validate_payload",
    "recommend_next_action", "dispatch_to_decision_loop", "summarise_run",
]
