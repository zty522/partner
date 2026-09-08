"""Event-first activation after a signed full production-readiness pass."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .production_readiness import assess_production_readiness


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def review_and_maybe_activate(root: Path, *, candidate_id: str,
                              llm_experiments: list[str],
                              auto_activate_authorized: bool = False) -> dict[str, Any]:
    """Recompute hard gates; only a real pass may emit promotion/activation."""
    drill_path = root / "share/mind/governance/experience_guided_policy/rollback_drills" / f"{candidate_id}.json"
    drill = _load(drill_path)
    attestation = assess_production_readiness(
        str(root), candidate_id=candidate_id,
        llm_experiment_paths=llm_experiments,
        rollback_drill_passed=drill.get("ok") is True,
        single_authorized_model=True,
    )
    result: dict[str, Any] = {
        "production_ready": attestation["production_ready"],
        "decision": attestation["decision"],
        "path": attestation.get("path"),
        "auto_activate_authorized": bool(auto_activate_authorized),
        "activated": False,
    }
    if not (attestation["production_ready"] and auto_activate_authorized):
        return result

    from .candidate_execution import load_candidate
    from .candidate_skills import activate_promoted_candidate
    from .evolution_loop import decide_experiment
    from .production_canary import rollback_production_canary

    candidate = load_candidate(str(root), candidate_id) or {}
    experiment_id = str(candidate.get("experiment_id") or "")
    criteria = {
        "general_llm": attestation["general_llm"]["ok"],
        "sustained_business": attestation["sustained_business"]["ok"],
        "longitudinal_policy_learning": attestation["longitudinal_policy_learning"]["ok"],
        "evolution_ledger": attestation["evolution_ledger"]["ok"],
    }
    decision = decide_experiment(str(root), {
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "decision": "promoted",
        "criteria_results": criteria,
        "regression_passed": True,
        "project_id": "agent_self_evolution",
        "evidence": [str(attestation.get("path") or ""), str(drill_path), *llm_experiments],
        "metrics_before": {"strategy": "baseline_governed_context_v1"},
        "metrics_after": {"strategy": str(attestation.get("strategy_id") or "")},
        "rollback_required": False,
        "reason": "all signed readiness gates passed under explicit user authorization",
    })
    policy_event = next((row for row in decision.get("events") or []
                         if row.get("event_type") == "policy/promoted"), {})
    if not decision.get("promoted") or not policy_event:
        return {**result, "decision_result": decision,
                "error": "promotion_event_not_created"}
    activation = activate_promoted_candidate(
        str(root), candidate_id=candidate_id,
        decision_key="literature_github_learning:research_adoption_context",
        policy_event_id=str(policy_event.get("event_id") or ""),
        readiness_attestation_path=str(attestation.get("path") or ""),
    )
    if activation.get("ok"):
        rollback_production_canary(str(root), reason="full_production_activation_completed")
    return {**result, "activated": activation.get("ok") is True,
            "decision_result": decision, "activation_result": activation}
