"""Static validation for benchmark parent and subject Flow graphs."""
from __future__ import annotations

from typing import Any


def _ancestors(flow: Any, node_id: str) -> set[str]:
    seen: set[str] = set()
    def visit(identifier: str) -> None:
        for dependency in flow.node(identifier).depends_on:
            if dependency not in seen:
                seen.add(dependency)
                visit(dependency)
    visit(node_id)
    return seen


def validate_benchmark_flows(*, protocol: Any, parent: Any, subject: Any,
                             catalog: Any) -> list[str]:
    errors: list[str] = []
    for flow in (parent, subject):
        for node in flow.nodes:
            if catalog.get(node.event_type) is None:
                errors.append(f"unregistered_event:{flow.name}:{node.event_type}")

    checkpoints = {str(node.parameters.get("checkpoint_id")): node
                   for node in subject.nodes if node.event_type == "checkpoint.capture"}
    for spec in protocol.checkpoints:
        identifier = str(spec.get("id") or "")
        after = str(spec.get("after") or "")
        node = checkpoints.get(identifier)
        if node is None:
            errors.append(f"missing_checkpoint:{identifier}")
        elif after not in node.depends_on or str(node.parameters.get("source_node") or "") != after:
            errors.append(f"checkpoint_boundary_mismatch:{identifier}:{after}")

    subject_ids = {node.node_id for node in subject.nodes}
    required_chain = ("inspect", "plan", "core_commit", "execute", "verify", "core_settlement")
    for earlier, later in zip(required_chain, required_chain[1:]):
        if earlier not in subject_ids or later not in subject_ids:
            errors.append(f"missing_subject_boundary:{earlier}:{later}")
        elif earlier not in _ancestors(subject, later):
            errors.append(f"subject_order_violation:{earlier}:{later}")
    for node in subject.nodes:
        if node.event_type.startswith("benchmark."):
            errors.append(f"evaluator_leaked_into_subject:{node.event_type}")

    parent_ids = {node.node_id for node in parent.nodes}
    required_parent = ("freeze", "variant_parity", "baseline_submit", "baseline_collect",
                       "candidate_submit", "candidate_collect", "paired_compare",
                       "aggregate", "benchmark_settlement", "benchmark_report", "close")
    for earlier, later in zip(required_parent, required_parent[1:]):
        if earlier not in parent_ids or later not in parent_ids:
            errors.append(f"missing_parent_boundary:{earlier}:{later}")
        elif earlier not in _ancestors(parent, later):
            errors.append(f"parent_order_violation:{earlier}:{later}")
    return sorted(set(errors))


__all__ = ["validate_benchmark_flows"]

