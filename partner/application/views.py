"""Channel-neutral product views derived from durable Partner truth."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ApplicationCommand:
    command_id: str
    channel: str
    conversation_id: str
    user_text: str
    project_hint: str = ""
    reply_to: str = ""
    attachments: tuple[dict[str, Any], ...] = ()
    requested_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactView:
    artifact_id: str
    job_id: str
    title: str
    media_type: str
    source_event_id: str
    verified: bool
    local_path: str = ""
    preview: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    delivery_state: str = "not_requested"


@dataclass(frozen=True)
class UserUpdate:
    update_id: str
    job_id: str
    project_id: str
    line: str
    kind: str
    subject: str
    action: str
    finding: str
    meaning: str
    next: str
    created_at: str
    evidence_refs: tuple[str, ...] = ()
    delivery_policy: str = "local"
    channel_ack: bool = False

    def validate(self) -> None:
        if self.line not in {"project", "active_learning", "self_evolution", "conversation"}:
            raise ValueError(f"invalid user update line: {self.line}")
        if self.kind not in {"accepted", "progress", "milestone", "blocked", "completed", "correction"}:
            raise ValueError(f"invalid user update kind: {self.kind}")
        if self.kind in {"milestone", "blocked", "completed", "correction"}:
            missing = [name for name in ("action", "finding", "meaning", "next")
                       if not str(getattr(self, name)).strip()]
            if missing:
                raise ValueError(f"meaningful update missing fields: {missing}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class JobView:
    job_id: str
    project_id: str
    title: str
    state: str
    current_activity: str = ""
    meaningful_progress: str = ""
    blocker: str = ""
    latest_result: str = ""
    next_committed_action: str = ""
    started_at: str = ""
    updated_at: str = ""
    artifacts: tuple[ArtifactView, ...] = ()
    unread: bool = False


@dataclass(frozen=True)
class EventDetail:
    event_id: str
    flow_id: str
    node: str
    series: str
    status: str
    semantic_summary: str
    duration_seconds: float = 0.0
    claims: tuple[dict[str, Any], ...] = ()
    evidence: tuple[str, ...] = ()
    token_usage: dict[str, Any] = field(default_factory=dict)
    failure: dict[str, Any] = field(default_factory=dict)
    next_candidates: tuple[dict[str, Any], ...] = ()


__all__ = ["ApplicationCommand", "ArtifactView", "EventDetail", "JobView", "UserUpdate"]
