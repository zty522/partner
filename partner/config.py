"""Configuration management for Partner."""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


def get_partner_config_paths(workspace: str) -> List[str]:
    """Return candidate config paths for a workspace in priority order."""
    return [
        os.path.join(workspace, "partner_config.json"),
        os.path.join(workspace, "00_config", "partner_config.json"),
    ]


def resolve_partner_config_path(workspace: str, prefer_existing: bool = True) -> str:
    """Resolve the best config path for runtime reads/writes."""
    paths = get_partner_config_paths(workspace)
    if prefer_existing:
        for path in paths:
            if os.path.exists(path):
                return path
    return paths[0]


def workspace_has_partner_config(workspace: str) -> bool:
    """Whether a workspace has config in any supported location."""
    return any(os.path.exists(path) for path in get_partner_config_paths(workspace))


def load_partner_config_data(workspace: str) -> dict:
    """Load workspace config from the best available path."""
    config_path = resolve_partner_config_path(workspace)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_partner_config_data(workspace: str, data: dict):
    """Save runtime config to the primary path and mirror into 00_config."""
    primary_path = resolve_partner_config_path(workspace, prefer_existing=False)
    os.makedirs(os.path.dirname(primary_path), exist_ok=True)
    with open(primary_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    mirror_path = os.path.join(workspace, "00_config", "partner_config.json")
    if mirror_path != primary_path:
        os.makedirs(os.path.dirname(mirror_path), exist_ok=True)
        with open(mirror_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def discover_hermes_model_defaults(config_path: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Read Hermes's current default provider/model from config.yaml.

    Keeps parsing intentionally small and dependency-free: only the top-level
    `model:` block is inspected.
    """
    config_path = config_path or os.path.expanduser("~/.hermes/config.yaml")
    if not os.path.exists(config_path):
        return {"model": None, "provider": None}

    model = None
    provider = None
    in_model_block = False

    with open(config_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line.startswith(" ") and stripped.endswith(":"):
                in_model_block = stripped == "model:"
                continue
            if not in_model_block:
                continue
            if not line.startswith("  "):
                in_model_block = False
                continue
            key, sep, value = stripped.partition(":")
            if not sep:
                continue
            value = value.strip().strip("'\"")
            if key == "default" and value:
                model = value
            elif key == "provider" and value:
                provider = value
            if model and provider:
                break

    return {"model": model, "provider": provider}


def apply_runtime_agent_defaults(agent_cfg: dict) -> dict:
    """Fill missing agent model/provider values from Hermes defaults."""
    resolved = dict(agent_cfg or {})
    backend = resolved.get("backend", "hermes")
    if backend != "hermes":
        return resolved

    hermes_defaults = discover_hermes_model_defaults()
    if not resolved.get("model"):
        resolved["model"] = hermes_defaults.get("model")
    if not resolved.get("provider"):
        resolved["provider"] = hermes_defaults.get("provider")
    if not resolved.get("classifier_backend"):
        resolved["classifier_backend"] = backend
    if not resolved.get("classifier_model"):
        resolved["classifier_model"] = resolved.get("model")
    if not resolved.get("classifier_provider"):
        resolved["classifier_provider"] = resolved.get("provider")
    return resolved


def sync_partner_agent_defaults(workspace: str, force: bool = False):
    """Persist current Hermes defaults into a workspace config."""
    data = load_partner_config_data(workspace)
    agent_cfg = data.get("agent", {})
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
    if force and agent_cfg.get("backend", "hermes") == "hermes":
        agent_cfg["model"] = None
        agent_cfg["provider"] = None
        agent_cfg["classifier_model"] = None
        agent_cfg["classifier_provider"] = None
    data["agent"] = apply_runtime_agent_defaults(agent_cfg)
    save_partner_config_data(workspace, data)


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
    classifier_backend: Optional[str] = None
    classifier_model: Optional[str] = None
    classifier_provider: Optional[str] = None


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
