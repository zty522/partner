"""Active Plan (Event) Manager - multi-phase research plans spanning multiple cycles.

Replaces the old isolation-task model with continuous, multi-step plans.
A Plan is a complete "push a project forward" event:
  plan → literature_search → code_modification → experiment → analysis → next_plan

Key design:
- 30-min heartbeat minimum: every cycle checks if plan is active
- If active → check phase progress, advance if done, let continue if not
- If idle → create new plan and start executing
- Plans can run across unlimited cycles (no TTL bound, only "idle → active" transition)
"""

import json
import os
import copy
from datetime import datetime
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict


@dataclass
class PlanPhase:
    """A single phase within an active research plan."""
    name: str
    type: str  # "literature_search", "code_implementation", "experiment", "analysis", "planning", etc.
    status: str = "pending"  # pending | in_progress | completed | failed
    description: str = ""
    current_step: str = ""  # e.g., "searching papers on Combat", "modifying data_loader.py"
    result: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'PlanPhase':
        valid = {f.name for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class ActivePlan:
    """The current active research plan."""
    status: str = "idle"  # idle | planning | active | completed
    title: str = ""
    goal: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    current_phase_index: int = 0
    phases: List[PlanPhase] = field(default_factory=list)
    last_heartbeat: str = field(default_factory=lambda: datetime.now().isoformat())
    heartbeat_summary: str = ""  # brief summary for QQ notification

    @property
    def current_phase(self) -> Optional[PlanPhase]:
        if 0 <= self.current_phase_index < len(self.phases):
            return self.phases[self.current_phase_index]
        return None

    @property
    def all_done(self) -> bool:
        return all(p.status == "completed" for p in self.phases)

    @property
    def progress_text(self) -> str:
        done = sum(1 for p in self.phases if p.status == "completed")
        total = len(self.phases)
        if self.current_phase:
            cur = self.current_phase
            step = f" → {cur.current_step}" if cur.current_step else ""
            return f"[{done}/{total}] {cur.name}{step}"
        return f"[{done}/{total}]"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "title": self.title,
            "goal": self.goal,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_phase_index": self.current_phase_index,
            "phases": [p.to_dict() for p in self.phases],
            "last_heartbeat": self.last_heartbeat,
            "heartbeat_summary": self.heartbeat_summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'ActivePlan':
        phases = [PlanPhase.from_dict(p) for p in d.pop("phases", [])]
        valid = {f.name for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid}
        plan = cls(phases=phases, **filtered)
        return plan


class ActivePlanManager:
    """Manages the active plan lifecycle via JSON file."""

    def __init__(self, state_dir: str):
        self.path = os.path.join(state_dir, "active_plan.json")
        self.plan = self._load()

    def _load(self) -> ActivePlan:
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return ActivePlan.from_dict(data)
        except (FileNotFoundError, json.JSONDecodeError):
            return ActivePlan()

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.plan.to_dict(), f, indent=2, ensure_ascii=False)

    def is_active(self) -> bool:
        """Whether there's an active plan with running phases."""
        return self.plan.status in ("planning", "active")

    def start_plan(self, title: str, goal: str, phases: List[PlanPhase]):
        """Start a new plan, replacing any existing one."""
        now = datetime.now().isoformat()
        self.plan = ActivePlan(
            status="active",
            title=title,
            goal=goal,
            created_at=now,
            started_at=now,
            current_phase_index=0,
            phases=phases,
            last_heartbeat=now,
            heartbeat_summary=f"开始新计划: {title}",
        )
        self.save()

    def advance_phase(self):
        """Mark current phase as completed and move to next."""
        current = self.plan.current_phase
        if current:
            current.status = "completed"
            current.completed_at = datetime.now().isoformat()

        next_idx = self.plan.current_phase_index + 1
        if next_idx >= len(self.plan.phases):
            self.plan.status = "completed"
            self.plan.completed_at = datetime.now().isoformat()
            self.plan.heartbeat_summary = f"✅ 计划完成: {self.plan.title}"
        else:
            self.plan.current_phase_index = next_idx
            next_phase = self.plan.phases[next_idx]
            next_phase.status = "in_progress"
            next_phase.started_at = datetime.now().isoformat()
            self.plan.heartbeat_summary = f"进入新阶段: {next_phase.name}"
        self.plan.last_heartbeat = datetime.now().isoformat()
        self.save()

    def fail_phase(self, reason: str):
        """Mark current phase as failed."""
        current = self.plan.current_phase
        if current:
            current.status = "failed"
            current.completed_at = datetime.now().isoformat()
            current.result = f"失败: {reason}"
        self.plan.heartbeat_summary = f"❌ {current.name if current else ''}: {reason}"
        self.plan.last_heartbeat = datetime.now().isoformat()
        self.save()

    def heartbeat(self, summary: str):
        """Update heartbeat timestamp and summary (called every cycle)."""
        self.plan.last_heartbeat = datetime.now().isoformat()
        self.plan.heartbeat_summary = summary
        self.save()

    def reset_to_idle(self):
        """Reset plan to idle (completed or user-requested reset)."""
        self.plan = ActivePlan(
            status="idle",
            last_heartbeat=datetime.now().isoformat(),
            heartbeat_summary="已重置为空闲状态",
        )
        self.save()

    def get_status_text(self) -> str:
        """Human-readable status line for QQ notification."""
        p = self.plan
        if p.status == "idle":
            return "🟢 空闲中，等待新计划"
        if p.status == "completed":
            return f"✅ 计划完成: {p.title}"
        if p.status in ("planning", "active"):
            progress = p.progress_text
            return f"🔵 {p.title} | {progress}"
        return f"⚪ 未知状态: {p.status}"
