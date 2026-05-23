"""Event System - structured, reusable research cycles for Partner.

Events are multi-phase research cycles that go beyond single tasks.
Each Event has a template (like Agent Skills), executes phases sequentially,
and can spawn follow-up Events (self-growing mechanism).
"""

import json
import uuid
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from enum import Enum


class EventStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class PhaseType(Enum):
    LITERATURE_SEARCH = "literature_search"
    KNOWLEDGE_EXTRACTION = "knowledge_extraction"
    KNOWLEDGE_SYNTHESIS = "knowledge_synthesis"
    IDEA_GENERATION = "idea_generation"
    PROJECT_SCAN = "project_scan"
    EXPLORATION = "exploration"
    PLANNING = "planning"
    EVENT_GENERATION = "event_generation"
    KNOWLEDGE_SCAN = "knowledge_scan"
    GAP_ANALYSIS = "gap_analysis"


@dataclass
class EventPhase:
    """A single phase within an Event."""
    name: str
    type: str  # PhaseType value
    description: str = ""
    prompt: str = ""
    status: str = "pending"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'EventPhase':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class EventTemplate:
    """Template defining a reusable research cycle structure."""
    name: str
    description: str
    phases: List[EventPhase] = field(default_factory=list)
    priority_base: int = 7
    ttl_hours: int = 48
    estimated_minutes: int = 10
    tags: List[str] = field(default_factory=list)
    triggers: Dict[str, Any] = field(default_factory=dict)
    inputs: Dict[str, Any] = field(default_factory=dict)

    def create(self, inputs: Dict[str, Any] = None, priority: int = None,
               triggered_by: str = "cron_auto", parent_event_id: str = None) -> 'Event':
        """Create an Event instance from this template."""
        import copy
        phases = [copy.deepcopy(p) for p in self.phases]
        now = datetime.now()
        return Event(
            id=f"evt_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            template=self.name,
            priority=priority or self.priority_base,
            ttl_hours=self.ttl_hours,
            inputs=inputs or {},
            phases=phases,
            meta={
                "triggered_by": triggered_by,
                "parent_event_id": parent_event_id,
            }
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "phases": [p.to_dict() for p in self.phases],
            "priority_base": self.priority_base,
            "ttl_hours": self.ttl_hours,
            "estimated_minutes": self.estimated_minutes,
            "tags": self.tags,
            "triggers": self.triggers,
            "inputs": self.inputs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'EventTemplate':
        phases = [EventPhase.from_dict(p) for p in d.pop("phases", [])]
        return cls(phases=phases, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Event:
    """A running instance of an Event template."""
    id: str
    template: str
    status: str = EventStatus.PENDING.value
    priority: int = 7
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    ttl_hours: int = 48
    inputs: Dict[str, Any] = field(default_factory=dict)
    phases: List[EventPhase] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "template": self.template,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "ttl_hours": self.ttl_hours,
            "inputs": self.inputs,
            "phases": [p.to_dict() for p in self.phases],
            "outputs": self.outputs,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'Event':
        phases = [EventPhase.from_dict(p) for p in d.pop("phases", [])]
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(phases=phases, **filtered)

    @property
    def current_phase(self) -> Optional[EventPhase]:
        """Get the currently running or first pending phase."""
        for p in self.phases:
            if p.status == "running":
                return p
        for p in self.phases:
            if p.status == "pending":
                return p
        return None

    @property
    def is_expired(self) -> bool:
        """Check if event has exceeded its TTL."""
        created = datetime.fromisoformat(self.created_at)
        elapsed_hours = (datetime.now() - created).total_seconds() / 3600
        return elapsed_hours > self.ttl_hours

    def summary(self) -> str:
        """Human-readable summary of this event."""
        completed_phases = sum(1 for p in self.phases if p.status == "completed")
        total_phases = len(self.phases)
        return (
            f"[{self.id}] {self.template} "
            f"(priority={self.priority}, phases={completed_phases}/{total_phases}, "
            f"status={self.status})"
        )
