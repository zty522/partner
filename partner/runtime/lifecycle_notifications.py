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
    # Semantic boundaries that carry user-visible substance.  Internal phase
    # nodes (understand_1..3, commitment, plan, inspect...) complete with
    # nothing the user can act on; publishing them yields "收到/正在调研"
    # spam.  Only nodes whose completion means real content is available
    # (a research result, a settlement, a delivered report) publish.
    "assess", "benchmark_settle", "evolution_gate", "report_ack", "final_ack",
    "execute", "settle",
    # research_explain's synthesize is the whole point of an explain task: its
    # completion always deserves a delivery decision.
    "synthesize",
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
                   is_child_flow: bool = False, flow_name: str = "") -> bool:
    if mode in {"audit", "debug"}:
        return True
    if lifecycle_phase in {"accepted", "flow_failed", "event_failed"}:
        return True
    if lifecycle_phase == "flow_planned":
        return False
    if lifecycle_phase == "flow_started":
        # The acceptance receipt (message_agent "已收到您的任务") already covers
        # the "received" acknowledgement.  Re-publishing flow_started produces
        # a second near-identical "收到，正在调研" message that users read as
        # spam.  Silent starts are safe: the heartbeat branch below still
        # publishes a progress note once the run has been quiet >600s.
        return False
    if lifecycle_phase == "flow_completed":
        # Push a wrap-up for every user-visible flow: the research spine itself
        # plus each child round (cycle/learning/evolution), so users see what
        # finished even on multi-hour runs.
        return bool(flow_name in {'project_iteration', 'project_cycle_round',
                                  'active_learning', 'autonomous_evolution',
                                  'research_explain'})
    if lifecycle_phase == "event_completed":
        # Semantic milestones always publish (execute/synthesize/settle etc.),
        # so users see phase progress instead of hours of silence.  Routine
        # event chatter ("收到，正在调研" repeated per node) is throttled
        # deterministically in publish_lifecycle (PUSH_INTERVAL), otherwise the
        # user receives 7 near-identical acknowledgements for a single task.
        return node_id in _STANDARD_MILESTONE_NODES
    return False


# Routine progress notes are throttled to one per interval; semantic
# milestones and failures bypass the window.
PUSH_INTERVAL = 300.0


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
    contract = facts.get("intent_contract")
    benchmark = (contract or {}).get("benchmark") if isinstance(contract, dict) else {}
    is_v4 = ((isinstance(benchmark, dict) and
              benchmark.get("protocol_id") == "v4_longitudinal_closed_loop_v1") or
             str(facts.get("flow_name") or facts.get("flow_type") or "") in
             {"v4_benchmark_suite", "v4_benchmark_episode"})
    if (is_v4 and
            lifecycle_phase not in {"flow_failed", "event_failed"}):
        # v4 owns its user narrative with dedicated compose/send Events.
        # Runtime lifecycle prose would expose child-flow mechanics and even
        # turn deliberately suppressed milestones into noisy QQ messages.
        return {"ok": True, "suppressed": True, "mode": "dedicated_v4_events",
                "reason": "v4 benchmark uses dedicated user-message Events"}
    mode = notification_mode(facts.get("intent_contract"))
    if not should_publish(mode=mode, lifecycle_phase=lifecycle_phase,
                          node_id=str(facts.get("node_id") or ""),
                          is_child_flow=bool(facts.get("is_child_flow")),
                          flow_name=str(facts.get('flow_name') or facts.get('flow_type') or '')):
        # Deterministic throttle for routine event chatter: non-milestone
        # event_completed updates publish at most once per PUSH_INTERVAL.
        # Semantic milestones, failures and acceptance always pass.
        from partner.messaging.publish_gate import last_push_ts
        from datetime import datetime as _dt, timezone as _tz
        if (lifecycle_phase == "event_completed"
                and str(facts.get("node_id") or "") not in _STANDARD_MILESTONE_NODES
                and (_dt.now(_tz.utc).timestamp() - last_push_ts(workspace)) <= PUSH_INTERVAL):
            return {"ok": True, "suppressed": True, "mode": mode,
                    "reason": f"routine event chatter throttled (PUSH_INTERVAL={PUSH_INTERVAL:.0f}s)"}
        # Heartbeat removed: the auto catch-up note re-produced the same
        # "已收到/正在调研" empty acknowledgement ~10min after acceptance,
        # which users read as spam.  The LLM publish_gate alone decides
        # whether a >25min silence with concrete new facts deserves a note.
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
    # LLM publish gate: grounded in the real chat history, decide whether to
    # actually send (dedupe / too-long-silent / too-frequent) and rewrite the
    # candidate into plain user-facing Chinese.  Fails open (send).
    # Acceptance receipts are mandatory user-facing acks: a user who just sent
    # a message must see "已收到你的任务" exactly once per task.  The gate only
    # rewrites their wording (rewrite_only) — the duplicate/too-frequent rules
    # target proactive milestone chatter, not the ack that answers the user's
    # own message.  Silently dropping the receipt reads as "没反应".
    from partner.messaging.publish_gate import publish_gate
    gated = publish_gate(workspace=workspace, ctx=ctx,
                         candidate=composed.get("message") or "",
                         facts=base,
                         rewrite_only=(lifecycle_phase == "accepted"))
    if not gated.get("send"):
        return {"ok": True, "suppressed": True, "mode": mode, "stage": "gate",
                "reason": f"llm publish gate: {gated.get('reason') or 'duplicate/too-frequent'}"}
    if gated.get("message"):
        composed["message"] = gated["message"]
        composed["summary"] = gated["message"][:300]
    reviewed = _run_event(
        workspace=workspace, ctx=ctx, event_type="notification.lifecycle_critic",
        params={**base, "previous": composed, "message": composed.get("message")},
        flow_id=flow_id, node_id="critic", root_event_id=root_event_id)
    if not reviewed.get("ok"):
        return {"ok": False, "stage": "critic", "flow_id": flow_id, "output": reviewed}
    contract = dict(base.get("intent_contract") or {})
    _ec = contract.get("execution_constraints")
    constraints = dict(_ec) if isinstance(_ec, dict) else {}
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
    # Audit density belongs in the Web event explorer.  Broadcasting every
    # Event start/finish to QQ produced hundreds of low-information messages
    # per run.  QQ keeps only root lifecycle, failures and explicit milestone
    # messages composed by the business Flow.  Event-level failures ("未完成：
    # 核验…") are mechanism noise for the user; only flow-level failures carry
    # enough meaning to deserve a QQ push.
    if (mode in {'audit', 'debug'} and lifecycle_phase in
            {'event_started', 'event_completed', 'flow_started', 'event_failed'}):
        channels = ['web']
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
