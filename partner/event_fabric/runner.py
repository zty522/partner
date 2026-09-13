"""Bounded executor for one ready semantic Event checkpoint."""
from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable, iscoroutinefunction
from typing import Any
import asyncio

from .catalog import EventCatalog
from .flows import EventFlowController, EventFlowDefinition, EventFlowState
from .ledger import EventLedger
from .models import EventSummary


@dataclass
class EventRunResult:
    flow_state: EventFlowState
    node_id: str
    event_id: str
    output: dict[str, Any]


class EventFlowRunner:
    """Execute one node; callers own scheduling and resource admission."""

    def __init__(self, *, catalog: EventCatalog, ledger: EventLedger,
                 controller: EventFlowController):
        self.catalog = catalog
        self.ledger = ledger
        self.controller = controller

    async def run_ready_node(self, state: EventFlowState,
                             definition: EventFlowDefinition, node_id: str,
                             ctx: Any, initial: dict[str, Any] | None = None) -> EventRunResult:
        node = definition.node(node_id)
        event_definition = self.catalog.get(node.event_type)
        if event_definition is None:
            raise KeyError(f"Event missing from pinned catalog: {node.event_type}")
        upstream = {dependency: state.node_outputs.get(dependency, {})
                    for dependency in node.depends_on}
        previous = upstream.get(node.depends_on[-1], {}) if node.depends_on else {}
        params = {**dict(initial or {}), **dict(node.parameters),
                  "upstream": upstream, "previous": previous,
                  "flow_outputs": dict(state.node_outputs),
                  "previous_semantic": (previous.get("semantic_output", {})
                                        if isinstance(previous, dict) else {}),
                  "flow_id": state.flow_id,
                  "node_id": node.node_id, "project_id": state.project_id,
                  "instance_id": state.instance_id}
        if state.current_event_id and state.waiting_task_id:
            event = self.ledger.event_index()[state.current_event_id]
            from .models import EventEnvelope
            event = EventEnvelope(**{k:v for k,v in event.items() if k in EventEnvelope.__dataclass_fields__})
        else:
            event = self.ledger.create(
                node.event_type, event_definition.series,
                root_event_id=state.root_event_id, correlation_id=state.task_id,
                project_id=state.project_id, job_id=state.task_id,
                instance_id=state.instance_id, catalog_version=state.catalog_version,
                flow_id=state.flow_id, flow_type=state.flow_type,
                node_id=node.node_id, branch_id=node.branch_id,
                concurrency_key=node.concurrency_key or f"project:{state.project_id}",
                payload={"flow_node": True, "definition_version": definition.version},
            )
        params["event_id"] = event.event_id
        state.current_event_id = event.event_id
        state.node_event_ids[node.node_id] = event.event_id
        self.controller.store.save(state)
        self.ledger.transition(event, "running")
        import time
        if hasattr(ctx, "__dict__"):
            ctx.event_deadline = time.monotonic() + max(1, int(event_definition.timeout_seconds))
        try:
            if iscoroutinefunction(event_definition.handler):
                invocation = event_definition.handler(ctx, params)
            else:
                invocation = asyncio.to_thread(event_definition.handler, ctx, params)
            value = await asyncio.wait_for(
                invocation, timeout=max(1, int(event_definition.timeout_seconds)))
            if isawaitable(value):
                value = await value
            output = dict(value or {})
        except asyncio.TimeoutError:
            output = {
                "ok": False, "status": "failed",
                "error": f"Event exceeded {event_definition.timeout_seconds}s timeout",
                "failure_class": "runtime",
                "mechanism": f"event_timeout/{node.event_type}",
            }
        except Exception as exc:
            output = {"ok": False, "status": "failed",
                      "error": f"{type(exc).__name__}: {exc}",
                      "failure_class": "event_handler",
                      "mechanism": f"event/{node.event_type}"}
        if output.get("status") == "waiting":
            import time
            state.waiting_task_id = str(output.get("background_task_id") or "")
            state.next_check_at = time.time() + 5
            self.controller.store.save(state)
            return EventRunResult(state, node_id, event.event_id, output)
        state.waiting_task_id = ""
        state.next_check_at = 0.0
        terminal = "completed" if output.get("ok") else "failed"
        files = [str(value) for value in output.get("files") or []]
        semantic = output.get("semantic_output")
        if not isinstance(semantic, dict):
            semantic = {key: value for key, value in output.items()
                        if key not in {"model_output", "content"}}
        summary = EventSummary(
            event_id=event.event_id, status=terminal,
            headline=str(output.get("summary") or output.get("error") or node.event_type)[:500],
            outcome=str(output.get("outcome") or output.get("summary") or "")[:2000],
            evidence_refs=[str(value) for value in output.get("evidence_refs") or files],
            artifacts=[{"path": value} for value in files],
            metrics=dict(output.get("metrics") or {}),
            failure_class=str(output.get("failure_class") or ""),
            mechanism=str(output.get("mechanism") or ""),
            business_delta=bool(output.get("business_delta")),
            learning_delta=bool(output.get("learning_delta")),
            evolution_delta=bool(output.get("evolution_delta")),
            production_effective=bool(output.get("production_effective")),
            requires_human=bool(output.get("requires_human")),
            next_event_candidates=list(output.get("next_event_candidates") or []),
            notification_kind=str(output.get("notification_kind") or "routine"),
            semantic_output=semantic, token_usage=dict(output.get("token_usage") or {}),
        )
        self.ledger.complete(event, summary)
        recoverable = (terminal == 'failed' and event_definition.idempotent
            and event_definition.execution_method == 'llm'
            and any(x in str(output.get('error', '')).lower() for x in ('timeout','timed out','http 429','empty result','output truncated')))
        retry_count = state.node_retry_counts.get(node_id, 0)
        local_retry = terminal == 'failed' and event_definition.idempotent and output.get('retryable') is True
        retry_budget = 2 if recoverable else max(0,event_definition.max_attempts-1)
        if (recoverable or local_retry) and retry_count < retry_budget:
            import time
            state.node_retry_counts[node_id] = retry_count + 1
            state.next_check_at = time.time() + 15 * (retry_count + 1)
            state.current_event_id = ''
            self.controller.store.save(state)
            return EventRunResult(state, node_id, event.event_id,
                {**output, 'status':'waiting', 'retry_scheduled':True})
        state.node_outputs[node.node_id] = output
        state = self.controller.mark_terminal(
            state, definition, node.node_id, event_id=event.event_id,
            summary=summary.to_dict())
        state.current_event_id = ""
        self.controller.store.save(state)
        return EventRunResult(state, node.node_id, event.event_id, output)
