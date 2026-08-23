"""Contracts for recoverable, bounded, long-running Partner campaigns."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .models import new_id, now_iso


CAMPAIGN_STATES = {"draft", "running", "paused", "blocked", "completed", "cancelled"}
WORK_STATES = {
    "proposed", "leased", "queued", "running", "completed", "failed",
    "blocked", "cancelled",
}
WORK_KINDS = {"project_iteration", "evolution_experiment", "audit", "report"}
LEASE_STATES = {"active", "released", "expired"}
SAFE_AUTONOMY = {"safe", "human_required", "forbidden"}


def _required(value: str, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _iso(value: str, name: str) -> str:
    value = _required(value, name)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO-8601") from exc
    return value


@dataclass
class CampaignBudget:
    max_work_items: int = 100
    max_failures: int = 12
    max_retries_per_item: int = 2
    max_runtime_seconds: int = 28_800
    max_model_calls: int = 500
    max_cost_units: float = 100.0

    def validate(self) -> None:
        if int(self.max_work_items) < 1:
            raise ValueError("max_work_items must be positive")
        if int(self.max_failures) < 1:
            raise ValueError("max_failures must be positive")
        if int(self.max_retries_per_item) < 0:
            raise ValueError("max_retries_per_item must not be negative")
        if int(self.max_runtime_seconds) < 60:
            raise ValueError("max_runtime_seconds must be at least 60")
        if int(self.max_model_calls) < 0 or float(self.max_cost_units) < 0:
            raise ValueError("model/cost budgets must not be negative")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "CampaignBudget":
        obj = cls(**dict(value or {}))
        obj.validate()
        return obj


@dataclass
class CampaignUsage:
    work_items_created: int = 0
    work_items_completed: int = 0
    failures: int = 0
    retries: int = 0
    model_calls: int = 0
    cost_units: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "CampaignUsage":
        return cls(**dict(value or {}))


@dataclass
class CampaignState:
    goal: str
    allowed_instances: list[str]
    deadline_at: str
    budget: CampaignBudget
    campaign_id: str = field(default_factory=lambda: new_id("campaign"))
    status: str = "draft"
    max_active: int = 2
    active_instances: list[str] = field(default_factory=list)
    restore_instances: list[str] = field(default_factory=list)
    usage: CampaignUsage = field(default_factory=CampaignUsage)
    report_interval_seconds: int = 3600
    last_report_at: str = ""
    stop_reason: str = ""
    created_at: str = field(default_factory=now_iso)
    started_at: str = ""
    updated_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        self.goal = _required(self.goal, "goal")
        self.deadline_at = _iso(self.deadline_at, "deadline_at")
        allowed = list(dict.fromkeys(str(value) for value in self.allowed_instances))
        if not allowed or set(allowed) - {"01", "02", "03", "04", "05"}:
            raise ValueError("allowed_instances must contain only 01..05")
        self.allowed_instances = allowed
        if self.status not in CAMPAIGN_STATES:
            raise ValueError(f"invalid campaign status: {self.status}")
        if not 1 <= int(self.max_active) <= 2:
            raise ValueError("max_active must be 1 or 2")
        if set(self.active_instances) - set(self.allowed_instances):
            raise ValueError("active_instances must be allowed")
        if len(self.active_instances) > self.max_active:
            raise ValueError("active_instances exceeds max_active")
        if set(self.restore_instances) - {"01", "02", "03", "04", "05"} or len(self.restore_instances) > 2:
            raise ValueError("restore_instances must contain at most two known instances")
        if int(self.report_interval_seconds) < 60:
            raise ValueError("report_interval_seconds must be at least 60")
        self.budget.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["budget"] = self.budget.to_dict()
        data["usage"] = self.usage.to_dict()
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CampaignState":
        data = dict(value)
        data["budget"] = CampaignBudget.from_dict(data.get("budget"))
        data["usage"] = CampaignUsage.from_dict(data.get("usage"))
        obj = cls(**data)
        obj.validate()
        return obj


@dataclass
class WorkItem:
    campaign_id: str
    instance_id: str
    project_id: str
    kind: str
    title: str
    instruction: str
    work_item_id: str = field(default_factory=lambda: new_id("work"))
    status: str = "proposed"
    priority: int = 50
    attempt: int = 0
    max_attempts: int = 3
    task_id: str = ""
    lease_id: str = ""
    source_action_id: str = ""
    source_issue_id: str = ""
    requires_artifact: bool = True
    requires_delivery: bool = True
    autonomy: str = "safe"
    blocked_reason: str = ""
    evidence: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        self.campaign_id = _required(self.campaign_id, "campaign_id")
        self.project_id = _required(self.project_id, "project_id")
        self.title = _required(self.title, "title")
        self.instruction = _required(self.instruction, "instruction")
        if self.instance_id not in {"01", "02", "03", "04", "05"}:
            raise ValueError("instance_id must be 01..05")
        if self.kind not in WORK_KINDS:
            raise ValueError(f"invalid work kind: {self.kind}")
        if self.status not in WORK_STATES:
            raise ValueError(f"invalid work status: {self.status}")
        if self.autonomy not in SAFE_AUTONOMY:
            raise ValueError(f"invalid autonomy: {self.autonomy}")
        if not 0 <= int(self.priority) <= 100:
            raise ValueError("priority must be 0..100")
        if int(self.max_attempts) < 1 or int(self.attempt) < 0:
            raise ValueError("attempt bounds are invalid")
        if self.status in {"queued", "running", "completed"} and not self.task_id:
            raise ValueError(f"task_id required for {self.status} work")
        if self.status == "blocked" and not self.blocked_reason:
            raise ValueError("blocked work requires blocked_reason")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkItem":
        obj = cls(**dict(value))
        obj.validate()
        return obj


@dataclass
class InstanceLease:
    campaign_id: str
    work_item_id: str
    instance_id: str
    holder: str
    acquired_at: str
    expires_at: str
    lease_id: str = field(default_factory=lambda: new_id("lease"))
    status: str = "active"
    heartbeat_at: str = field(default_factory=now_iso)
    released_at: str = ""

    def validate(self) -> None:
        _required(self.campaign_id, "campaign_id")
        _required(self.work_item_id, "work_item_id")
        _required(self.holder, "holder")
        _iso(self.acquired_at, "acquired_at")
        _iso(self.expires_at, "expires_at")
        if self.instance_id not in {"01", "02", "03", "04", "05"}:
            raise ValueError("instance_id must be 01..05")
        if self.status not in LEASE_STATES:
            raise ValueError(f"invalid lease status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InstanceLease":
        obj = cls(**dict(value))
        obj.validate()
        return obj


@dataclass
class CampaignReport:
    campaign_id: str
    report_type: str
    status: str
    summary: str
    metrics: dict[str, Any]
    evidence: list[str]
    blocked_items: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    report_id: str = field(default_factory=lambda: new_id("campaign_report"))
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        _required(self.campaign_id, "campaign_id")
        if self.report_type not in {"checkpoint", "final"}:
            raise ValueError("report_type must be checkpoint or final")
        _required(self.summary, "summary")
        return asdict(self)
