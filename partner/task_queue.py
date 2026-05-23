"""Task Queue Manager - manages the lifecycle of research tasks."""

import json
import uuid
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskType(Enum):
    LITERATURE_SEARCH = "literature_search"
    PROJECT_SCAN = "project_scan"
    KNOWLEDGE_SYNTHESIS = "knowledge_synthesis"
    IDEA_GENERATION = "idea_generation"
    SKILL_LEARNING = "skill_learning"
    CROSS_PROJECT = "cross_project_analysis"
    DEEP_DIVE = "deep_dive"
    SELF_IMPROVEMENT = "self_improvement"


@dataclass
class Task:
    id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    type: str = "deep_dive"
    title: str = ""
    description: str = ""
    priority: int = 5
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    ttl_hours: int = 48
    status: str = TaskStatus.PENDING.value
    tags: List[str] = field(default_factory=list)
    result_summary: str = ""
    completed_at: Optional[str] = None


class TaskQueue:
    """Manages task lifecycle with priority-based selection."""
    
    def __init__(self, path: str):
        self.path = path
        self.tasks: List[Task] = []
        self._load()
    
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            valid_fields = {f.name for f in Task.__dataclass_fields__.values()}
            self.tasks = []
            for t in data:
                filtered = {k: v for k, v in t.items() if k in valid_fields}
                self.tasks.append(Task(**filtered))
        except (FileNotFoundError, json.JSONDecodeError):
            self.tasks = []
    
    def save(self):
        with open(self.path, 'w') as f:
            json.dump([asdict(t) for t in self.tasks], f, indent=2, ensure_ascii=False)
    
    def add_task(self, task: Task) -> str:
        self.tasks.append(task)
        self.save()
        return task.id
    
    def get_next(self) -> Optional[Task]:
        """Get highest priority pending task."""
        pending = [t for t in self.tasks if t.status == TaskStatus.PENDING.value]
        if not pending:
            return None
        # Sort by priority (descending), then by created_at (ascending)
        pending.sort(key=lambda t: (-t.priority, t.created_at))
        return pending[0]
    
    def complete(self, task_id: str, result_summary: str = ""):
        for t in self.tasks:
            if t.id == task_id:
                t.status = TaskStatus.COMPLETED.value
                t.result_summary = result_summary
                t.completed_at = datetime.now().isoformat()
                break
        self.save()
    
    def fail(self, task_id: str, reason: str = ""):
        for t in self.tasks:
            if t.id == task_id:
                t.status = TaskStatus.FAILED.value
                t.result_summary = reason
                break
        self.save()
    
    def stats(self) -> dict:
        total = len(self.tasks)
        by_status = {}
        for t in self.tasks:
            by_status[t.status] = by_status.get(t.status, 0) + 1
        return {"total": total, "by_status": by_status}
