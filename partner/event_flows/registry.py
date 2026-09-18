from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Iterable
import json

from partner.event_fabric.flows import EventFlowDefinition


class EventFlowRegistry:
    def __init__(self, definitions: Iterable[EventFlowDefinition] = ()):
        self._flows: dict[str, EventFlowDefinition] = {}
        self._versions: dict[tuple[str, str], EventFlowDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: EventFlowDefinition) -> None:
        if definition.name in self._flows:
            raise ValueError(f"duplicate Event Flow: {definition.name}")
        node_ids = [node.node_id for node in definition.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError(f"duplicate node in Event Flow: {definition.name}")
        if not node_ids:
            raise ValueError(f"empty Event Flow: {definition.name}")
        known = set(node_ids)
        for node in definition.nodes:
            missing = set(node.depends_on) - known
            if missing:
                raise ValueError(f"unknown dependency in {definition.name}: {sorted(missing)}")
        # Reject cycles and unreachable nodes so a flow can never be
        # created with no legal start point (empty ready_node_ids).
        roots = [n for n in node_ids if not any(d == n for node in definition.nodes for d in node.depends_on)]
        # Actually roots = nodes with no dependents... compute as nodes
        # with no incoming edges is wrong; roots = nodes with no depends_on.
        roots = [n for n in node_ids if not dict((x.node_id, x.depends_on) for x in definition.nodes)[n]]
        if not roots:
            raise ValueError(f"cyclic or sourceless Event Flow: {definition.name} (no root node)")
        # Topological reachability check.
        deps = {node.node_id: list(node.depends_on) for node in definition.nodes}
        seen = set()
        visiting = set()
        def visit(nid):
            if nid in seen:
                return
            if nid in visiting:
                raise ValueError(f"cycle detected in {definition.name} at {nid}")
            visiting.add(nid)
            for d in deps.get(nid, []):
                visit(d)
            visiting.discard(nid)
            seen.add(nid)
        for nid in node_ids:
            visit(nid)
        unreachable = set(node_ids) - seen
        if unreachable:
            raise ValueError(f"unreachable nodes in {definition.name}: {sorted(unreachable)}")
        self._flows[definition.name] = definition
        self._versions[(definition.name, definition.version)] = definition

    def get(self, name: str, *, version: str = "") -> EventFlowDefinition:
        if version:
            return self._versions[(name, version)]
        return self._flows[name]

    def names(self) -> list[str]:
        return sorted(self._flows)

    @property
    def version(self) -> str:
        rows = [asdict(self._flows[name]) for name in self.names()]
        body = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(body.encode("utf-8")).hexdigest()[:16]


def build_flow_registry() -> EventFlowRegistry:
    from .builtins import DEFINITIONS, LEGACY_PRESENTATION_FLOWS
    registry = EventFlowRegistry(DEFINITIONS)
    from .cycle import DEFINITIONS as cycle_definitions, HISTORICAL as cycle_historical
    for definition in cycle_definitions:
        registry.register(definition)
    from .cycle import _improvement_flow_definitions
    for definition in _improvement_flow_definitions(local_learning=False):
        registry._versions[(definition.name,definition.version)] = definition
    for definition in cycle_historical:
        registry._versions[(definition.name, definition.version)] = definition
    # Preserve the previous production graph for already pinned requests.
    # New requests use 2.1; old requests retain fail-stop action semantics.
    from dataclasses import replace
    current = next(f for f in LEGACY_PRESENTATION_FLOWS if f.name == "project_iteration")
    previous_nodes = tuple(replace(node, depends_on=('route',)) if node.node_id == 'notify' else node
                           for node in current.nodes if node.node_id != 'continuation')
    previous = replace(current, version='2.1.0', nodes=previous_nodes)
    registry._versions[(previous.name, previous.version)] = previous
    old = replace(previous, version="2.0.0", nodes=tuple(
        replace(node, continue_on_failure=False) for node in previous.nodes))
    registry._versions[(old.name, old.version)] = old
    current = next(f for f in LEGACY_PRESENTATION_FLOWS if f.name == 'new_project')
    old_nodes = tuple(
        replace(node, depends_on=('init',)) if node.node_id == 'notify' else
        replace(node, node_id='route') if node.node_id == 'channel' else
        replace(node, depends_on=('route',)) if node.node_id == 'send' else node
        for node in current.nodes if node.node_id not in {'route', 'continuation'})
    old = replace(current, version='1.0.0', nodes=old_nodes)
    registry._versions[(old.name, old.version)] = old
    for name in ('xhs_authoring', 'browser_video_learning'):
        current = registry.get(name)
        old = replace(current, version='1.0.0', nodes=tuple(
            replace(node, continue_on_failure=False) for node in current.nodes
            if node.event_type.startswith('social.')))
        registry._versions[(old.name, old.version)] = old
    for historical in LEGACY_PRESENTATION_FLOWS:
        registry._versions[(historical.name,historical.version)] = historical
    return registry
