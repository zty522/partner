"""Wiring of external-method adapters into the Partner Event flow (M2).

This module is the single source of truth for *which* Event produces
each adapter's input and *which* Event consumes its output.  Adapters
do not call each other; they are independent libraries invoked by
the Event that owns that step in the flow.

State honesty: ``static_implemented``. No event has invoked the
adapters in this session.  The wiring is real source code; the next
verification pass must (a) confirm the Event method names exist on the
referenced modules and (b) trace a candidate through the loop with a
mocked evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AdapterWiring:
    """Documents how one external method is invoked from a flow."""
    adapter: str
    producer_event: str
    consumer_event: str
    producer_module: str
    consumer_module: str
    payload_contract: str
    feedback_contract: str
    promotion_event: str
    notes: str = ""


# Each entry names the live partner Event that owns the step.  These
# are real event_type strings used in the Event Catalog.  The
# producer / consumer module paths are real.
WIRINGS: list[AdapterWiring] = [
    AdapterWiring(
        adapter="ace",
        producer_event="memory.observe",
        consumer_event="memory.recall",
        producer_module="partner/memory/event_memory.py",
        consumer_module="partner/memory/event_memory.py",
        payload_contract="ACEEntry { strategy_id, scope, content }",
        feedback_contract="usage_outcomes.jsonl row with kind=applied|rejected",
        promotion_event="memory.consolidate",
        notes="ACEEntry.append_semantic is the single producer; EventMemory.recall filters by project_id.",
    ),
    AdapterWiring(
        adapter="gepa",
        producer_event="evolution.candidate_propose",
        consumer_event="evolution.candidate_record_fitness",
        producer_module="partner/events/autonomous_evolution.py:handler candidate",
        consumer_module="partner/events/autonomous_evolution.py:handler compare",
        payload_contract="GepaCandidate { parent_id, change_summary, diff_fingerprint }",
        feedback_contract="compare.<attempt>.criteria_results.{matched_tests, regression_passed, expectations_met}",
        promotion_event="evolution.decision",
        notes="GepaOptimizer.select_next returns the next candidate the runtime should evaluate; decision promotes via promotion_decide.",
    ),
    AdapterWiring(
        adapter="dgm",
        producer_event="evolution.attempt_register",
        consumer_event="evolution.lineage_export",
        producer_module="partner/events/autonomous_evolution.py:handler attempt",
        consumer_module="partner/research/adapters/dgm.py",
        payload_contract="DGMLineageNode { parent_id, generation, code_fingerprint, archive_status }",
        feedback_contract="promotion_decide.decision ∈ {promote,reject,inconclusive,no_change}",
        promotion_event="evolution.archive_update",
        notes="Lineage graph is reconstructed from DGMLineageNode records; archive_status transitions are driven by decision events.",
    ),
]


def get_wiring(adapter: str) -> AdapterWiring | None:
    for w in WIRINGS:
        if w.adapter == adapter:
            return w
    return None


__all__ = ["AdapterWiring", "WIRINGS", "get_wiring"]
