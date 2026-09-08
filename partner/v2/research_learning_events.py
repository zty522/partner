"""Event-first wrappers for evidence-grounded project research."""
from __future__ import annotations

from typing import Any

from partner.governance.research_learning import (
    investigate_selected_query,
    observe_research_project,
    run_research_selector_matched_experiment,
    select_research_query,
)
from partner.governance.research_adoption import (
    select_research_adoption_context,
    write_candidate_context_artifact,
)
from partner.governance.storage import workspace_root
from pathlib import Path


def _root(ctx: Any) -> str:
    return str(workspace_root(str(getattr(ctx, "workspace", ""))))


def atomic_research_active_learning_observe(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return observe_research_project(
        _root(ctx), project_id=str(params.get("project_id") or ""),
        goal=str(params.get("goal") or ""),
        questions=[str(value) for value in params.get("questions") or []],
        source_paths=[str(value) for value in params.get("source_paths") or []],
    )


def atomic_research_active_learning_select(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return select_research_query(_root(ctx), project_id=str(params.get("project_id") or ""))


def atomic_research_active_learning_investigate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return investigate_selected_query(_root(ctx), project_id=str(params.get("project_id") or ""))


def atomic_research_active_learning_matched(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return run_research_selector_matched_experiment(
        _root(ctx), project_id=str(params.get("project_id") or ""))


def atomic_research_adoption_context_shadow(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Execute the audited adoption Candidate in an isolated shadow path."""
    query = str(params.get("query") or "").strip()
    project_id = str(params.get("project_id") or "").strip()
    research_project_id = str(params.get("research_project_id") or "").strip()
    if not query or not project_id or not research_project_id:
        return {"ok": False, "status": "research_adoption_params_missing",
                "error": "query, project_id, and research_project_id are required",
                "production_effective": False}
    result = select_research_adoption_context(
        _root(ctx), query=query, project_id=project_id,
        research_project_id=research_project_id,
        instance_id=str(params.get("instance_id") or "04"),
        budget_chars=int(params.get("budget_chars") or 9000),
    )
    if not result.get("ok"):
        return result
    task = getattr(ctx, "task_instance", None)
    output = Path(str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "")
                      or (_root(ctx) + "/share/mind/governance/research_learning/shadow")))
    return write_candidate_context_artifact(result, output)


CANDIDATE_HANDLERS = {
    "research_adoption_context_shadow": atomic_research_adoption_context_shadow,
}
