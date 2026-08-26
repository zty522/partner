"""Bounded Harness events for Campaign governance."""
from __future__ import annotations

from typing import Any

from partner.governance.campaign import (
    campaign_snapshot, cancel_campaign, create_campaign, enqueue_work_item,
    seed_default_work,
)
from partner.governance.campaign_models import CampaignBudget
from partner.governance.campaign_storage import active_campaign_id, load_campaign, save_campaign
from partner.governance.models import now_iso
from partner.governance.storage import workspace_root


def _workspace(ctx: Any) -> str:
    value = str(getattr(ctx, "workspace", "") or "")
    return str(workspace_root(value)) if value else ""


def atomic_campaign_status(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace(ctx)
    campaign_id = str(params.get("campaign_id") or active_campaign_id(workspace))
    return {"ok": bool(campaign_id), **campaign_snapshot(workspace, campaign_id)}


def atomic_create_campaign(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace(ctx)
    from partner.state.config import manual_stable_mode, runtime_capability_enabled
    if manual_stable_mode(workspace) or not runtime_capability_enabled(workspace, "automatic_campaigns"):
        return {"ok": False, "status": "disabled_in_manual_stable", "retryable": False,
                "error": "自动 Campaign 已暂停；当前默认入口是用户手动消息"}
    duration = int(params.get("duration_seconds") or 28_800)
    budget = CampaignBudget(
        max_work_items=int(params.get("max_work_items") or 40),
        max_failures=int(params.get("max_failures") or 8),
        max_retries_per_item=int(params.get("max_retries_per_item") or 2),
        max_runtime_seconds=duration,
        max_model_calls=int(params.get("max_model_calls") or 200),
        max_cost_units=float(params.get("max_cost_units") or 80),
    )
    state = create_campaign(
        workspace,
        goal=str(params.get("goal") or ""),
        allowed_instances=list(params.get("allowed_instances") or ["01", "02"]),
        duration_seconds=duration,
        max_active=int(params.get("max_active") or 2),
        report_interval_seconds=int(params.get("report_interval_seconds") or 3600),
        budget=budget,
    )
    items = seed_default_work(workspace, state.campaign_id) if params.get("seed_defaults", True) else []
    return {"ok": True, "status": "created", "campaign": state.to_dict(),
            "work_items": [item.to_dict() for item in items],
            "note": "Campaign is persisted; a runner must be active to dispatch work."}


def atomic_enqueue_campaign_work(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace(ctx)
    from partner.state.config import manual_stable_mode, runtime_capability_enabled
    if manual_stable_mode(workspace) or not runtime_capability_enabled(workspace, "automatic_campaigns"):
        return {"ok": False, "status": "disabled_in_manual_stable", "retryable": False,
                "error": "自动 Campaign 已暂停；当前默认入口是用户手动消息"}
    campaign_id = str(params.get("campaign_id") or active_campaign_id(workspace))
    item = enqueue_work_item(workspace, campaign_id, params)
    return {"ok": True, "status": item.status, "work_item": item.to_dict()}


def atomic_pause_campaign(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace(ctx)
    campaign_id = str(params.get("campaign_id") or active_campaign_id(workspace))
    state = load_campaign(workspace, campaign_id)
    if not state:
        return {"ok": False, "status": "missing_campaign", "retryable": False}
    state.status = "paused"
    state.stop_reason = str(params.get("reason") or "paused by governed event")
    state.updated_at = now_iso()
    save_campaign(workspace, state)
    return {"ok": True, "status": "paused", "campaign": state.to_dict()}


def atomic_cancel_campaign(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace(ctx)
    campaign_id = str(params.get("campaign_id") or active_campaign_id(workspace))
    state = cancel_campaign(workspace, campaign_id, str(params.get("reason") or "cancelled by governed event"))
    return {"ok": True, "status": "cancelled", "campaign": state.to_dict()}
