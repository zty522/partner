"""Small dependency-free governance contracts.

The JSON Schemas under docs/contracts are the public contracts. These
dataclasses enforce the same high-value invariants at runtime without adding a
new validation dependency to Partner's core install.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


ACTION_STATES = {"proposed", "queued", "running", "completed", "blocked", "cancelled"}
PROJECT_STATES = {"active", "paused", "blocked", "completed", "cancelled"}
ISSUE_CATEGORIES = {
    "context", "planning", "event", "environment", "verification",
    "delivery", "scheduling", "data", "model", "unknown",
}
ISSUE_STATES = {"open", "investigating", "candidate", "resolved", "wont_fix"}
EXPERIMENT_STATES = {"candidate", "validating", "promoted", "rejected", "inconclusive"}
DECISIONS = {"promoted", "rejected", "inconclusive"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _required(value: str, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _string_list(values: Any, name: str, *, nonempty: bool = False) -> list[str]:
    if values is None:
        values = []
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be a list")
    result = [str(value).strip() for value in values if str(value).strip()]
    if nonempty and not result:
        raise ValueError(f"{name} must not be empty")
    return result


@dataclass
class NextAction:
    title: str
    event_type: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "proposed"
    action_id: str = field(default_factory=lambda: new_id("action"))
    task_id: str = ""
    blocked_reason: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        self.title = _required(self.title, "title")
        self.event_type = _required(self.event_type, "event_type")
        if self.status not in ACTION_STATES:
            raise ValueError(f"invalid action status: {self.status}")
        if self.status in {"queued", "running", "completed"} and not self.task_id:
            raise ValueError(f"task_id required for {self.status} action")
        if self.status == "blocked" and not self.blocked_reason:
            raise ValueError("blocked_reason required for blocked action")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "NextAction":
        obj = cls(**value)
        obj.validate()
        return obj


@dataclass
class IterationReceipt:
    project_id: str
    iteration: int
    goal: str
    inputs: list[str]
    actions_executed: list[str]
    artifacts: list[str]
    findings: list[str]
    next_actions: list[NextAction] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    stop_reason: str = ""
    delivery_confirmed: bool = False
    receipt_id: str = field(default_factory=lambda: new_id("receipt"))
    created_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        self.project_id = _required(self.project_id, "project_id")
        self.goal = _required(self.goal, "goal")
        if int(self.iteration) < 1:
            raise ValueError("iteration must be >= 1")
        self.actions_executed = _string_list(self.actions_executed, "actions_executed", nonempty=True)
        self.inputs = _string_list(self.inputs, "inputs")
        self.artifacts = _string_list(self.artifacts, "artifacts")
        self.findings = _string_list(self.findings, "findings")
        self.unresolved_questions = _string_list(self.unresolved_questions, "unresolved_questions")
        for action in self.next_actions:
            action.validate()
        if self.next_actions and self.stop_reason:
            raise ValueError("receipt cannot contain both next_actions and stop_reason")
        if not self.next_actions and not self.stop_reason:
            raise ValueError("receipt requires next_actions or stop_reason")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["next_actions"] = [action.to_dict() for action in self.next_actions]
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IterationReceipt":
        data = dict(value)
        data["next_actions"] = [NextAction.from_dict(item) for item in data.get("next_actions", [])]
        obj = cls(**data)
        obj.validate()
        return obj


@dataclass
class ProjectState:
    project_id: str
    owner_instance: str
    status: str
    goal: str
    current_iteration: int = 0
    latest_receipt_id: str = ""
    blocked_reason: str = ""
    resume_event: str = ""
    updated_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        self.project_id = _required(self.project_id, "project_id")
        self.goal = _required(self.goal, "goal")
        if self.owner_instance not in {"01", "02", "03", "04", "05"}:
            raise ValueError("owner_instance must be 01..05")
        if self.status not in PROJECT_STATES:
            raise ValueError(f"invalid project status: {self.status}")
        if self.status == "blocked" and not self.blocked_reason:
            raise ValueError("blocked project requires blocked_reason")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProjectState":
        obj = cls(**value)
        obj.validate()
        return obj


@dataclass
class IssueRecord:
    summary: str
    category: str
    severity: str
    evidence: list[str]
    instance_id: str = ""
    project_id: str = ""
    status: str = "open"
    occurrences: int = 1
    issue_id: str = field(default_factory=lambda: new_id("issue"))
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        self.summary = _required(self.summary, "summary")
        self.evidence = _string_list(self.evidence, "evidence", nonempty=True)
        if self.category not in ISSUE_CATEGORIES:
            raise ValueError(f"invalid issue category: {self.category}")
        if self.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"invalid issue severity: {self.severity}")
        if self.status not in ISSUE_STATES:
            raise ValueError(f"invalid issue status: {self.status}")
        if int(self.occurrences) < 1:
            raise ValueError("occurrences must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass
class EvolutionExperiment:
    issue_id: str
    hypothesis: str
    intervention: str
    baseline: dict[str, Any]
    success_criteria: list[str]
    project_id: str = ""
    resume_action_id: str = ""
    tests: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    status: str = "candidate"
    experiment_id: str = field(default_factory=lambda: new_id("experiment"))
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        self.issue_id = _required(self.issue_id, "issue_id")
        self.hypothesis = _required(self.hypothesis, "hypothesis")
        self.intervention = _required(self.intervention, "intervention")
        self.success_criteria = _string_list(self.success_criteria, "success_criteria", nonempty=True)
        self.tests = _string_list(self.tests, "tests")
        if self.status not in EXPERIMENT_STATES:
            raise ValueError(f"invalid experiment status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass
class PromotionDecision:
    experiment_id: str
    decision: str
    evidence: list[str]
    regression_passed: bool
    metrics_before: dict[str, Any] = field(default_factory=dict)
    metrics_after: dict[str, Any] = field(default_factory=dict)
    rollback_required: bool = False
    reason: str = ""
    decided_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        self.experiment_id = _required(self.experiment_id, "experiment_id")
        self.evidence = _string_list(self.evidence, "evidence", nonempty=True)
        if self.decision not in DECISIONS:
            raise ValueError(f"invalid decision: {self.decision}")
        if self.decision == "promoted" and not self.regression_passed:
            raise ValueError("cannot promote when regression did not pass")
        if self.decision == "rejected" and not self.rollback_required:
            raise ValueError("rejected change must require rollback/non-adoption")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass
class ContextSelection:
    query: str
    selected: list[dict[str, Any]]
    budget_chars: int
    used_chars: int
    instance_id: str = ""
    project_id: str = ""
    selection_id: str = field(default_factory=lambda: new_id("context"))
    created_at: str = field(default_factory=now_iso)

    def validate(self) -> None:
        if int(self.budget_chars) < 1:
            raise ValueError("budget_chars must be positive")
        if int(self.used_chars) > int(self.budget_chars):
            raise ValueError("context selection exceeded budget")
        for item in self.selected:
            for field_name in ("document_id", "path", "tier", "reason", "chars"):
                if field_name not in item:
                    raise ValueError(f"selected context missing {field_name}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
