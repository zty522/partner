"""Deterministic Event handler for executing governed Candidate artifacts."""
from __future__ import annotations

from typing import Any

from partner.governance.candidate_execution import execute_candidate
from partner.governance.storage import instance_id, workspace_root
from partner.v2.targetdiff_bdk_events import HANDLERS as targetdiff_bdk_handlers
from partner.v2.world_model_events import CANDIDATE_HANDLERS as world_model_handlers
from partner.v2.active_learning_events import CANDIDATE_HANDLERS as active_learning_handlers
from partner.v2.research_learning_events import CANDIDATE_HANDLERS as research_learning_handlers


ALLOWED_CANDIDATE_HANDLERS = {
    **targetdiff_bdk_handlers,
    **world_model_handlers,
    **active_learning_handlers,
    **research_learning_handlers,
}


def atomic_execute_candidate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(params.get("candidate_id") or "").strip()
    execution_id = str(params.get("execution_id") or "").strip()
    if not candidate_id or not execution_id:
        return {"ok": False, "status": "invalid_candidate_execution",
                "error": "candidate_id and execution_id are required", "retryable": False}
    workspace = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    iid = str(params.get("instance_id") or instance_id(str(getattr(ctx, "workspace", ""))))
    forwarded = dict(params.get("event_params") or {})
    return execute_candidate(
        workspace,
        candidate_id,
        ctx=ctx,
        handlers=ALLOWED_CANDIDATE_HANDLERS,
        params=forwarded,
        instance_id=iid,
        execution_id=execution_id,
        mode=str(params.get("mode") or "shadow"),
    )


HANDLERS = {"execute_candidate": atomic_execute_candidate}
