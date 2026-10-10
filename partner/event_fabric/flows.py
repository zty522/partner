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
import hashlib


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
    soft_orchestrated: bool = False

    def node(self, node_id: str) -> FlowNode:
        for value in self.nodes:
            if value.node_id == node_id:
                return value
        raise KeyError(node_id)

    @classmethod
    def build_from_sequence(
        cls, name: str, version: str, event_types: list[str], *,
        description: str = "", parameters: dict[str, dict[str, Any]] | None = None,
        soft_orchestrated: bool = False,
    ) -> "EventFlowDefinition":
        """Assemble a linear Event Flow from an event-type sequence.

        Soft-orchestration output: the LLM proposes event types from the shared
        pool; this constructor materialises them into a legal definition with
        depends_on = previous node (no cycles, fully reachable).  Node ids are
        derived from the event name and uniquified for repeated events.
        """
        nodes: list[FlowNode] = []
        seen: dict[str, int] = {}
        for event_type in event_types:
            base = event_type.split('.')[-1].replace('_', '')
            node_id = base
            if node_id in seen:
                seen[node_id] += 1
                node_id = f"{base}_{seen[node_id]}"
            else:
                seen[node_id] = 0
            deps = (nodes[-1].node_id,) if nodes else ()
            node_params = dict((parameters or {}).get(event_type) or {})
            nodes.append(FlowNode(node_id, event_type, deps, parameters=node_params))
        return cls(name, version, tuple(nodes), description, soft_orchestrated)


@dataclass
class SuspendedFlow:
    flow_id: str
    resume_node_id: str
    reason: str
    child_flow_id: str = ""
    # A repeated controller node is a durable loop boundary.  After its child
    # terminates the same semantic Event becomes ready again and decides, from
    # the child's sealed evidence, whether another child is warranted.
    repeat_owner: bool = False


@dataclass
class EventFlowState:
    flow_id: str
    flow_type: str
    definition_version: str
    catalog_version: str
    task_id: str
    project_id: str
    instance_id: str
    builtin_catalog_version: str = ""
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
    orchestration_attempts: int = 0
    orchestration_history: list[dict[str, Any]] = field(default_factory=list)
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
              builtin_catalog_version: str = "",
              run_context: dict[str, Any] | None = None) -> EventFlowState:
        roots = [node.node_id for node in definition.nodes if not node.depends_on]
        state = EventFlowState(
            flow_id=f"flow_{uuid.uuid4().hex[:16]}", flow_type=definition.name,
            definition_version=definition.version, catalog_version=catalog_version,
            builtin_catalog_version=builtin_catalog_version,
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
        # A project-round blueprint is an executable contract, not prose.  The
        # registered definition is a safe superset; after the independent
        # critic freezes the sequence, nodes omitted by that contract are
        # durably skipped.  Required execution/evaluation nodes are enforced
        # by cycle.round_blueprint_critic before this transition.
        if (state.flow_type == "project_cycle_round" and node_id == "design_critic"
                and status == "completed" and isinstance(semantic, dict)):
            sequence = [str(value) for value in semantic.get("event_sequence") or []]
            selected = set(sequence)
            event_to_nodes: dict[str, list[str]] = {}
            for planned in definition.nodes:
                event_to_nodes.setdefault(planned.event_type, []).append(planned.node_id)
            selected_nodes = {
                candidate
                for event_type in selected
                for candidate in event_to_nodes.get(event_type, [])
            }
            selected_nodes.update({"input_resolve", "design", "design_critic"})
            for planned in definition.nodes:
                if (planned.node_id not in selected_nodes
                        and planned.node_id not in state.completed_node_ids
                        and planned.node_id not in state.skipped_node_ids
                        and planned.node_id not in state.failed_node_ids):
                    state.skipped_node_ids.append(planned.node_id)
            prefix_ids = {"input_resolve", "input_eligibility",
                          "design", "design_critic"}
            materialized = [node.event_type for node in definition.nodes
                            if node.node_id in prefix_ids
                            and node.node_id in state.completed_node_ids]
            materialized = materialized + [
                event_type for event_type in sequence
                if event_type not in materialized]
            plan_material = json.dumps(materialized, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8")
            state.run_context.update({
                "materialized_event_sequence": sequence,
                "planned_flow_hash": "sha256:" + hashlib.sha256(plan_material).hexdigest(),
                "flow_materialized": True,
            })
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
            if state.run_context.get("flow_materialized"):
                executed = set(state.completed_node_ids) | set(state.failed_node_ids)
                actual = [node.event_type for node in definition.nodes
                          if node.node_id in executed]
                state.run_context["actual_event_sequence"] = actual
                state.run_context["actual_flow_hash"] = "sha256:" + hashlib.sha256(
                    json.dumps(actual, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")).hexdigest()
        self.store.save(state)
        return state

    def validate_definition(self, definition: EventFlowDefinition) -> None:
        """Registry-style structural validation for LLM-assembled flows.

        Mirrors EventFlowRegistry.register checks so a dynamic definition can be
        validated before it replaces the running graph.
        """
        node_ids = [node.node_id for node in definition.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError(f"duplicate node in dynamic flow: {definition.name}")
        if not node_ids:
            raise ValueError(f"empty dynamic flow: {definition.name}")
        deps: dict[str, list[str]] = {}
        for node in definition.nodes:
            missing = set(node.depends_on) - set(node_ids)
            if missing:
                raise ValueError(f"unknown dependency in {definition.name}: {sorted(missing)}")
            deps[node.node_id] = list(node.depends_on)
        roots = [nid for nid in node_ids if not deps[nid]]
        if not roots:
            raise ValueError(f"sourceless dynamic flow: {definition.name}")
        seen, visiting = set(), set()
        def visit(nid: str) -> None:
            if nid in seen:
                return
            if nid in visiting:
                raise ValueError(f"cycle detected in {definition.name} at {nid}")
            visiting.add(nid)
            for dep in deps.get(nid, []):
                visit(dep)
            visiting.discard(nid)
            seen.add(nid)
        for nid in node_ids:
            visit(nid)
        unreachable = set(node_ids) - seen
        if unreachable:
            raise ValueError(f"unreachable nodes in {definition.name}: {sorted(unreachable)}")

    def apply_orchestration(self, state: EventFlowState, current: EventFlowDefinition,
                            proposed: EventFlowDefinition, *, evidence_event_id: str = "",
                            reason: str = "soft_orchestration",
                            decision: dict[str, Any] | None = None) -> EventFlowState:
        """Replace the running graph suffix with an LLM-approved definition.

        Pure state transition (same discipline as route_after_intent):
        already-completed nodes must exist unchanged in the proposed graph;
        ready is recomputed from the proposed dependencies; history is
        appended so every dynamic adjustment is auditable.
        """
        self.validate_definition(proposed)
        proposed_ordered = list(proposed.nodes)
        # Completed nodes are aligned by event_type, in baseline order, against
        # the proposed prefix.  An LLM-assembled sequence names nodes by event
        # short name, so node ids differ from the baseline; the event type is
        # the stable contract.  A proposed graph that drops a completed event
        # type is rejected.
        current_order = [n.node_id for n in current.nodes]
        completed_ordered = [nid for nid in current_order
                             if nid in state.completed_node_ids]
        skipped_ordered = [nid for nid in current_order
                           if nid in state.skipped_node_ids]
        p_idx = 0
        for nid in completed_ordered + skipped_ordered:
            ev = current.node(nid).event_type
            found = False
            while p_idx < len(proposed_ordered):
                if proposed_ordered[p_idx].event_type == ev:
                    found = True
                    p_idx += 1
                    break
                p_idx += 1
            if not found:
                raise ValueError(f"orchestration drops completed event: {nid} ({ev})")
        # The proposed nodes matching completed events are inherited as done;
        # the remaining suffix is recomputed into ready.
        done = set(state.completed_node_ids) | set(state.skipped_node_ids)
        matched_done: set[str] = set()
        p_idx = 0
        for nid in completed_ordered + skipped_ordered:
            ev = current.node(nid).event_type
            while p_idx < len(proposed_ordered):
                candidate = proposed_ordered[p_idx]
                if candidate.event_type == ev and candidate.node_id not in done:
                    matched_done.add(candidate.node_id)
                    p_idx += 1
                    break
                p_idx += 1
        effective_done = done | matched_done
        ready = [node.node_id for node in proposed.nodes
                 if node.node_id not in effective_done
                 and node.node_id not in state.ready_node_ids
                 and node.node_id not in state.failed_node_ids
                 and set(node.depends_on).issubset(effective_done | set(state.ready_node_ids))]
        state.definition_history.append({
            'flow_type': current.name, 'version': current.version,
            'routed_to': proposed.name, 'target_version': proposed.version,
            'evidence_event_id': evidence_event_id, 'at': _now(),
            'orchestrated': True, 'reason': reason,
            'decision': dict(decision or {}),
        })
        state.flow_type, state.definition_version = proposed.name, proposed.version
        state.ready_node_ids = ready
        state.status = 'running'
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
                          child_flow_id: str, reason: str,
                          repeat_owner: bool = False) -> EventFlowState:
        state.suspended.append(SuspendedFlow(
            flow_id=state.flow_id, resume_node_id=resume_node_id,
            child_flow_id=child_flow_id, reason=reason,
            repeat_owner=repeat_owner,
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
        if row.repeat_owner:
            # The owner completed before requesting the child.  Re-open only
            # that node; all prior Event IDs remain in the durable run log and
            # child records, while the next execution receives a fresh ID.
            state.completed_node_ids = [n for n in state.completed_node_ids
                                        if n != row.resume_node_id]
            state.failed_node_ids = [n for n in state.failed_node_ids
                                     if n != row.resume_node_id]
            state.skipped_node_ids = [n for n in state.skipped_node_ids
                                      if n != row.resume_node_id]
            state.node_event_ids.pop(row.resume_node_id, None)
            state.node_summaries.pop(row.resume_node_id, None)
            state.node_outputs.pop(row.resume_node_id, None)
        state.ready_node_ids = [row.resume_node_id]
        self.store.save(state)
        return state
