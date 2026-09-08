"""User-callable Event-first acceptance probes for Sprint 19."""
from __future__ import annotations

from typing import Any

from partner.governance.sprint19_acceptance import (
    audit_active_learning, audit_project_iteration, audit_self_evolution,
    run_sprint19_acceptance,
)
from partner.governance.storage import workspace_root


def _root(ctx: Any) -> str:
    return str(workspace_root(str(getattr(ctx, "workspace", ""))))


def atomic_project_iteration_audit(ctx: Any, params: dict) -> dict:
    return {"ok": True, **audit_project_iteration(_root(ctx), str(params.get("project_id") or ""))}


def atomic_active_learning_effect_audit(ctx: Any, params: dict) -> dict:
    return {"ok": True, **audit_active_learning(_root(ctx), str(params.get("instance_id") or ""))}


def atomic_self_evolution_effect_audit(ctx: Any, params: dict) -> dict:
    return {"ok": True, **audit_self_evolution(_root(ctx))}


def atomic_sprint19_acceptance(ctx: Any, params: dict) -> dict:
    return run_sprint19_acceptance(
        _root(ctx), project_id=str(params.get("project_id") or ""),
        instance_id=str(params.get("instance_id") or ""),
    )
