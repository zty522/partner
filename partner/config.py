"""Configuration management for Partner."""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class WorkspaceConfig:
    """Workspace configuration."""
    path: str = ""
    readonly_dirs: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.path:
            self.path = os.path.join(os.getcwd(), "partner_workspace")


@dataclass
class AgentConfig:
    """Agent backend configuration."""
    backend: str = "hermes"  # hermes, claude_code, codex
    model: Optional[str] = None
    provider: Optional[str] = None


@dataclass
class SchedulerConfig:
    """Scheduler configuration."""
    interval_minutes: int = 30
    max_tasks_per_cycle: int = 1
    heartbeat_timeout_minutes: int = 60


@dataclass
class PartnerConfig:
    """Main configuration."""
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    name: str = "Partner"
    
    def save(self, path: str):
        with open(path, 'w') as f:
            json.dump(asdict(self), f, indent=2)
    
    @classmethod
    def load(cls, path: str) -> 'PartnerConfig':
        with open(path) as f:
            data = json.load(f)
        return cls(
            workspace=WorkspaceConfig(**data.get('workspace', {})),
            agent=AgentConfig(**data.get('agent', {})),
            scheduler=SchedulerConfig(**data.get('scheduler', {})),
            name=data.get('name', 'Partner'),
        )
