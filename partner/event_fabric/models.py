"""Typed records shared by execution, learning and every frontend.

An Event is a fact about work, not a chat message.  Every terminal Event owns
one structured summary; frontends project that summary for their medium.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


EVENT_SERIES = {
    "interaction", "project", "active_learning", "self_evolution", "evolution",
    "planning", "memory", "presentation", "delivery", "runtime",
    "selector", "frontend", "visualization", "notification", "acceptance",
    "improvement"
}
TERMINAL_STATUSES = {"completed", "failed", "blocked", "cancelled"}


@dataclass
class EventEnvelope:
    event_id: str
    event_type: str
    series: str
    status: str = "created"
    root_event_id: str = ""
    parent_event_id: str = ""
    correlation_id: str = ""
    project_id: str = ""
    job_id: str = ""
    work_item_id: str = ""
    continuation_owner: str = ""
    instance_id: str = ""
    channel: str = "local"
    priority: int = 50
    concurrency_key: str = ""
    prerequisites: list[str] = field(default_factory=list)
    input_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    schema_version: int = 1
    catalog_version: str = ""
    flow_id: str = ""
    flow_type: str = ""
    node_id: str = ""
    branch_id: str = "main"
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EventSummary:
    event_id: str
    status: str
    headline: str
    outcome: str = ""
    claims: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    failure_class: str = ""
    mechanism: str = ""
    business_delta: bool = False
    learning_delta: bool = False
    evolution_delta: bool = False
    production_effective: bool = False
    completion: dict[str, Any] = field(default_factory=dict)
    requires_human: bool = False
    next_event_candidates: list[dict[str, Any]] = field(default_factory=list)
    human_message: str = ""
    notification_kind: str = "routine"
    created_at: str = ""
    schema_version: int = 1
    semantic_output: dict[str, Any] = field(default_factory=dict)
    llm_trace_refs: list[str] = field(default_factory=list)
    token_usage: dict[str, Any] = field(default_factory=dict)
    next_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EventSelection:
    selection_id: str
    selector_event_id: str
    considered_event_ids: list[str]
    selected: list[dict[str, Any]]
    rejected: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""
    created_at: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
