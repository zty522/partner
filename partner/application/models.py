"""Small durable contracts for the user-facing Partner application layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class JobRecord:
    schema_version: int = 1
    job_id: str = ""
    project_id: str = ""
    title: str = ""
    request: str = ""
    route: str = "enqueue_work"
    channel: str = "local"
    sender_id: str = ""
    persona_hint: str = ""
    origin_instance: str = ""
    assigned_instance: str = ""
    intake_instance_id: str = ""
    status: str = "queued"
    message_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    report_policy: str = "milestone"
    error: str = ""
    root_event_id: str = ""
    intent_contract_path: str = ""
    intent_contract: dict[str, Any] = field(default_factory=dict)
    intent_model_calls: int = 0
    event_catalog_version: str = ""
    flow_id: str = ""
    flow_type: str = ""
    current_event_id: str = ""
    ready_event_ids: list[str] = field(default_factory=list)
    completed_event_ids: list[str] = field(default_factory=list)
    suspended_flows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Submission:
    accepted: bool
    job_id: str
    project_id: str
    assigned_instance: str
    status: str
    route: str
    message: str
    message_id: str = ""
