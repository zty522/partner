"""Durable Event-flow graph state.

An Event never invokes another Event.  It returns candidates; this controller
resolves graph edges, persists ready nodes, and supports suspend/child/resume.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class FlowNode:
    node_id: str
    event_type: str
    depends_on: tuple[str, ...] = ()
    branch_id: str = "main"
    concurrency_key: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    when_route: str = ""
    optional: bool = False
    continue_on_failure: bool = False
    when_output: str = ""  # node.field boolean gate; missing values fail closed


@dataclass(frozen=True)
class EventFlowDefinition:
    name: str
    version: str
    nodes: tuple[FlowNode, ...]
    description: str = ""

    def node(self, node_id: str) -> FlowNode:
        for value in self.nodes:
            if value.node_id == node_id:
                return value
        raise KeyError(node_id)


@dataclass
class SuspendedFlow:
    flow_id: str
    resume_node_id: str
    reason: str
    child_flow_id: str = ""


@dataclass
class EventFlowState:
    flow_id: str
    flow_type: str
    definition_version: str
    catalog_version: str
    task_id: str
    project_id: str
    instance_id: str
    root_event_id: str = ""
    status: str = "running"
    current_event_id: str = ""
    waiting_task_id: str = ""
    next_check_at: float = 0.0
    node_retry_counts: dict[str, int] = field(default_factory=dict)
    recovery_history: list[dict[str, Any]] = field(default_factory=list)
    ready_node_ids: list[str] = field(default_factory=list)
    completed_node_ids: list[str] = field(default_factory=list)
    failed_node_ids: list[str] = field(default_factory=list)
    skipped_node_ids: list[str] = field(default_factory=list)
    node_event_ids: dict[str, str] = field(default_factory=dict)
    node_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    node_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    suspended: list[SuspendedFlow] = field(default_factory=list)
    active_child_flow_id: str = ""
    selected_route: str = ""
    definition_history: list[dict[str, Any]] = field(default_factory=list)
    run_context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value


class EventFlowStore:
    def __init__(self, workspace: str | Path):
        root = Path(workspace).resolve()
        if root.parent.name == "instances":
            root = root.parent.parent
        self.directory = root / "state/event_flows"
        self.directory.mkdir(parents=True, exist_ok=True)

    def path(self, flow_id: str) -> Path:
        return self.directory / f"{flow_id}.json"

    def save(self, state: EventFlowState) -> Path:
        state.updated_at = _now()
        target = self.path(state.flow_id)
        temporary = target.with_suffix(f".tmp.{os.getpid()}")
        temporary.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        from partner.index.resource_catalog import register_runtime
        register_runtime(target, state.to_dict())
        return target

    def load(self, flow_id: str) -> EventFlowState:
        value = json.loads(self.path(flow_id).read_text(encoding="utf-8"))
        value["suspended"] = [SuspendedFlow(**row) for row in value.get("suspended") or []]
        return EventFlowState(**value)


class EventFlowController:
    """Pure state transitions; execution remains owned by the instance runtime."""

    def __init__(self, store: EventFlowStore):
        self.store = store

    def start(self, definition: EventFlowDefinition, *, catalog_version: str,
              task_id: str, project_id: str, instance_id: str,
              run_context: dict[str, Any] | None = None) -> EventFlowState:
        roots = [node.node_id for node in definition.nodes if not node.depends_on]
        state = EventFlowState(
            flow_id=f"flow_{uuid.uuid4().hex[:16]}", flow_type=definition.name,
            definition_version=definition.version, catalog_version=catalog_version,
            task_id=task_id, project_id=project_id, instance_id=instance_id,
            ready_node_ids=roots,
            run_context=dict(run_context or {}),
        )
        self.store.save(state)
        return state

    def route_after_intent(self, state: EventFlowState, source: EventFlowDefinition,
                           target: EventFlowDefinition) -> EventFlowState:
        """Keep the three real intent Events, select the unexecuted suffix.

        This is a controller transition, never an Event calling another Event.
        Persisting the source definition makes the provisional intake auditable.
        """
        prefix = ('understand_1', 'understand_2', 'understand_3')
        if (set(state.completed_node_ids) != set(prefix) or state.current_event_id
                or state.waiting_task_id or state.failed_node_ids or state.skipped_node_ids):
            raise ValueError('intent routing requires only the completed intent prefix')
        if any(source.node(n) != target.node(n) for n in prefix):
            raise ValueError('intent routing cannot replace already executed Events')
        ready = [n.node_id for n in target.nodes if n.node_id not in prefix
                 and set(n.depends_on).issubset(prefix)]
        if not ready:
            raise ValueError('target has no ready continuation after intent')
        state.definition_history.append({'flow_type':source.name, 'version':source.version,
            'routed_to':target.name, 'target_version':target.version,
            'evidence_event_id':state.node_event_ids.get('understand_3'), 'at':_now()})
        state.flow_type, state.definition_version = target.name, target.version
        state.ready_node_ids = ready
        state.status = 'running'
        self.store.save(state)
        return state

    def mark_terminal(self, state: EventFlowState, definition: EventFlowDefinition,
                      node_id: str, *, event_id: str, summary: dict[str, Any]) -> EventFlowState:
        if node_id not in state.ready_node_ids:
            raise ValueError(f"node is not ready: {node_id}")
        state.ready_node_ids.remove(node_id)
        state.node_event_ids[node_id] = event_id
        state.node_summaries[node_id] = dict(summary)
        status = str(summary.get("status") or "failed")
        current = definition.node(node_id)
        if status != "completed" and current.optional:
            target = state.skipped_node_ids
            status = "completed"
        else:
            target = state.completed_node_ids if status == "completed" else state.failed_node_ids
        if node_id not in target:
            target.append(node_id)
        semantic = summary.get("semantic_output") or {}
        if isinstance(semantic, dict) and semantic.get("primary_route"):
            state.selected_route = str(semantic["primary_route"])
        if (status == "completed" or current.continue_on_failure) and not state.active_child_flow_id:
            completed = set(state.completed_node_ids) | set(state.skipped_node_ids)
            completed.update(n for n in state.failed_node_ids if definition.node(n).continue_on_failure)
            for node in definition.nodes:
                if (node.node_id not in completed and node.node_id not in state.ready_node_ids
                        and node.node_id not in state.failed_node_ids
                        and node.node_id not in state.skipped_node_ids
                        and set(node.depends_on).issubset(completed)):
                    gate_ok = True
                    if node.when_output:
                        owner, field = node.when_output.split('.', 1)
                        gate_ok = state.node_outputs.get(owner, {}).get(field) is True
                    if not gate_ok or (node.when_route and node.when_route != state.selected_route):
                        state.skipped_node_ids.append(node.node_id)
                        completed.add(node.node_id)
                    else:
                        state.ready_node_ids.append(node.node_id)
        if not state.ready_node_ids and not state.active_child_flow_id:
            unhandled = [n for n in state.failed_node_ids if not definition.node(n).continue_on_failure]
            state.status = "failed" if unhandled else "completed"
        self.store.save(state)
        return state

    def retry_failed_node(self, state: EventFlowState, definition: EventFlowDefinition,
                          node_id: str, *, reason: str, idempotent: bool) -> EventFlowState:
        """Explicit operator recovery after a fix; retain failed outputs/Events.

        This is not automatic unlimited retry. The caller must verify the
        catalog's idempotence and provide the concrete recovery reason.
        """
        if not idempotent or not reason or state.status!='failed' or node_id not in state.failed_node_ids:
            raise ValueError('recovery requires an idempotent failed node and explicit reason')
        if len(state.failed_node_ids)!=1:raise ValueError('resolve multiple failures explicitly before recovery')
        definition.node(node_id)
        state.recovery_history.append({'node_id':node_id,'event_id':state.node_event_ids.get(node_id),
            'output':state.node_outputs.get(node_id),'reason':reason,'at':_now()})
        state.failed_node_ids.remove(node_id);state.node_outputs.pop(node_id,None)
        state.current_event_id='';state.waiting_task_id='';state.next_check_at=0
        state.ready_node_ids=[node_id];state.status='running'
        self.store.save(state)
        return state

    def insert_child(self, state: EventFlowState, *, resume_node_id: str,
                     child_flow_id: str, reason: str) -> EventFlowState:
        """Suspend the parent at a semantic boundary; handlers never recurse."""
        return self.suspend_for_child(
            state, resume_node_id=resume_node_id,
            child_flow_id=child_flow_id, reason=reason,
        )

    def suspend_for_child(self, state: EventFlowState, *, resume_node_id: str,
                          child_flow_id: str, reason: str) -> EventFlowState:
        state.suspended.append(SuspendedFlow(
            flow_id=state.flow_id, resume_node_id=resume_node_id,
            child_flow_id=child_flow_id, reason=reason,
        ))
        state.active_child_flow_id = child_flow_id
        state.ready_node_ids = []
        state.status = "suspended"
        self.store.save(state)
        return state

    def resume(self, state: EventFlowState, *, child_flow_id: str) -> EventFlowState:
        matches = [row for row in state.suspended if row.child_flow_id == child_flow_id]
        if not matches:
            raise ValueError("child flow does not own this resume")
        row = matches[-1]
        state.active_child_flow_id = ""
        state.status = "running"
        state.ready_node_ids = [row.resume_node_id]
        self.store.save(state)
        return state
