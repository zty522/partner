"""Bounded macro Harness strategies eligible for shadow evaluation."""
from __future__ import annotations

from typing import Any

from .storage import atomic_json, workspace_root


STRATEGIES: tuple[dict[str, Any], ...] = (
    {"decision_key": "planner.preflight", "baseline": "llm_plan_then_repair",
     "candidate": "candidate_preflight_aware_planning_v1", "risk": "low",
     "metrics": ["preflight_failure_rate", "planner_model_calls", "truth", "observability"]},
    {"decision_key": "context.selection", "baseline": "full_default_context",
     "candidate": "candidate_budgeted_selected_context_v1", "risk": "low",
     "metrics": ["context_chars", "missing_required_context", "truth", "business_progress"]},
    {"decision_key": "evidence.synthesis", "baseline": "direct_report",
     "candidate": "candidate_extract_then_report_v1", "risk": "low",
     "metrics": ["source_coverage", "false_success", "model_calls", "truth"]},
    {"decision_key": "failure.recovery", "baseline": "fixed_retry",
     "candidate": "candidate_failure_class_recovery_v1", "risk": "medium",
     "metrics": ["recovery_rate", "retry_count", "latency", "truth", "safety"]},
    {"decision_key": "artifact.validation", "baseline": "existence_and_format",
     "candidate": "candidate_readback_validation_v1", "risk": "low",
     "metrics": ["false_success", "content_quality", "delivery", "truth"]},
    {"decision_key": "browser.verification", "baseline": "vision_only",
     "candidate": "candidate_vision_dom_dual_evidence_v1", "risk": "medium",
     "metrics": ["dom_confirmation", "vision_confirmation", "user_observability", "safety"]},
)


def write_strategy_catalog(workspace: str) -> dict[str, Any]:
    payload = {"schema_version": 1, "mode": "shadow_first", "automatic_production_promotion": False,
               "strategies": list(STRATEGIES)}
    path = workspace_root(workspace) / "share/mind/governance/rl/strategy_space.json"
    atomic_json(path, payload)
    return {"ok": True, "path": str(path), **payload}

