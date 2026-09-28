"""Audited compose -> critic -> delivery pipeline for lifecycle updates."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import hashlib
import json
import uuid

from partner.event_fabric import EventLedger, EventSummary, build_catalog
from partner.runtime.event_run_log import EventRunLog


_STANDARD_MILESTONE_NODES = {
    "assess", "report_ack", "seal", "final_ack",
}


def notification_mode(intent_contract: dict[str, Any] | None) -> str:
    """Return the user-facing notification density for one Job.

    ``standard`` is the product default, ``audit`` exposes every business
    Event transition, and ``debug`` is reserved for transport/runtime work.
    Direct low-level callers without a contract keep the historical audit
    behavior so diagnostic tools and explicit tests remain unsurprising.
    """
    if not isinstance(intent_contract, dict):
        return "audit"
    constraints = intent_contract.get("execution_constraints")
    constraints = constraints if isinstance(constraints, dict) else {}
    value = str(constraints.get("notification_mode") or
                intent_contract.get("notification_mode") or "standard").lower()
    return value if value in {"standard", "audit", "debug"} else "standard"


def should_publish(*, mode: str, lifecycle_phase: str, node_id: str = "",
                   is_child_flow: bool = False) -> bool:
    if mode in {"audit", "debug"}:
        return True
    if lifecycle_phase in {"accepted", "flow_failed", "event_failed"}:
        return True
    if lifecycle_phase == "flow_planned":
        return not is_child_flow
    if lifecycle_phase == "flow_started":
        return False
    if lifecycle_phase == "flow_completed":
        return True
    return lifecycle_phase == "event_completed" and node_id in _STANDARD_MILESTONE_NODES


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str,
        separators=(",", ":")).encode()).hexdigest()


def _run_event(*, workspace, ctx, event_type: str, params: dict[str, Any],
               flow_id: str, node_id: str, root_event_id: str = "") -> dict[str, Any]:
    catalog = build_catalog(workspace=workspace)
    definition = catalog.get(event_type)
    if definition is None:
        raise KeyError(event_type)
    ledger = EventLedger(workspace)
    event = ledger.create(
        event_type, definition.series, root_event_id=root_event_id,
        correlation_id=str(getattr(ctx, "job_id", "")),
        project_id=str(getattr(ctx, "project_id", "")),
        job_id=str(getattr(ctx, "job_id", "")),
        instance_id=str(getattr(ctx, "instance_id", "")),
        channel=str(getattr(ctx, "channel", "")), catalog_version=catalog.version,
        flow_id=flow_id, flow_type="lifecycle_notification", node_id=node_id,
        concurrency_key=f"notification:{getattr(ctx, 'job_id', '')}",
        payload={"lifecycle_notification": True,
                 "phase": params.get("lifecycle_phase", "")},
    )
    ledger.transition(event, "running")
    trace = EventRunLog(workspace, str(getattr(ctx, "job_id", "")))
    started = datetime.now(timezone.utc).isoformat()
    trace.event(phase="started", flow_id=flow_id, flow_type="lifecycle_notification",
                node_id=node_id, event_id=event.event_id, event_type=event_type,
                inputs=params, status="running")
    try:
        output = dict(definition.handler(ctx, {**params, "event_id": event.event_id,
                                               "flow_id": flow_id, "node_id": node_id}) or {})
    except Exception as exc:
        output = {"ok": False, "status": "failed",
                  "error": f"{type(exc).__name__}: {exc}"}
    terminal = "completed" if output.get("ok") else "failed"
    summary = EventSummary(
        event_id=event.event_id, status=terminal,
        headline=str(output.get("summary") or output.get("error") or event_type)[:500],
        outcome=str(output.get("message") or output.get("summary") or "")[:2000],
        semantic_output=(output.get("semantic_output") if isinstance(
            output.get("semantic_output"), dict) else dict(output)),
        notification_kind="routine", started_at=started,
        finished_at=datetime.now(timezone.utc).isoformat(),
        input_hash=_hash(params), output_hash=_hash(output),
    )
    ledger.complete(event, summary)
    trace.event(phase="finished", flow_id=flow_id, flow_type="lifecycle_notification",
                node_id=node_id, event_id=event.event_id, event_type=event_type,
                outputs=output, status=terminal)
    return output


def publish_lifecycle(*, workspace, ctx, lifecycle_phase: str,
                      root_event_id: str = "", **facts: Any) -> dict[str, Any]:
    """Publish one update through three independently recorded Events."""
    mode = notification_mode(facts.get("intent_contract"))
    if not should_publish(mode=mode, lifecycle_phase=lifecycle_phase,
                          node_id=str(facts.get("node_id") or ""),
                          is_child_flow=bool(facts.get("is_child_flow"))):
        return {"ok": True, "suppressed": True, "mode": mode,
                "reason": "notification density policy"}
    token = uuid.uuid4().hex[:12]
    flow_id = f"notify_{getattr(ctx, 'job_id', 'job')}_{token}"
    base = {**facts, "lifecycle_phase": lifecycle_phase,
            "notification_mode": mode,
            "notification_id": token,
            "intent_contract": facts.get("intent_contract") or {}}
    composed = _run_event(workspace=workspace, ctx=ctx,
                          event_type="notification.lifecycle_compose", params=base,
                          flow_id=flow_id, node_id="compose", root_event_id=root_event_id)
    if not composed.get("ok"):
        return {"ok": False, "stage": "compose", "flow_id": flow_id, "output": composed}
    reviewed = _run_event(
        workspace=workspace, ctx=ctx, event_type="notification.lifecycle_critic",
        params={**base, "previous": composed, "message": composed.get("message")},
        flow_id=flow_id, node_id="critic", root_event_id=root_event_id)
    if not reviewed.get("ok"):
        return {"ok": False, "stage": "critic", "flow_id": flow_id, "output": reviewed}
    contract = dict(base.get("intent_contract") or {})
    constraints = dict(contract.get("execution_constraints") or {})
    # Preserve the delivery contract frozen at intake.  A Web-originated Job
    # may explicitly request both Web and QQ; replacing that list with
    # ``[web]`` silently dropped QQ from every lifecycle update.
    declared = constraints.get("delivery_channels")
    channels = ([str(value) for value in declared if str(value) in {"web", "qq"}]
                if isinstance(declared, list) else [])
    if "web" not in channels:
        channels.insert(0, "web")
    if (str(getattr(ctx, "sender_id", ""))
            and str(getattr(ctx, "channel", "")) in {"qq", "both"}
            and "qq" not in channels):
        channels.append("qq")
    constraints["delivery_channels"] = channels
    contract["execution_constraints"] = constraints
    sent = _run_event(
        workspace=workspace, ctx=ctx, event_type="delivery.send_text",
        params={**base, "message": reviewed.get("message"), "previous": reviewed,
                "intent_contract": contract, "notification_id": token,
                "flow_outputs": {"compose": composed, "critic": reviewed}},
        flow_id=flow_id, node_id="send", root_event_id=root_event_id)
    return {"ok": bool(sent.get("ok")), "flow_id": flow_id,
            "message": reviewed.get("message", ""), "delivery": sent}
