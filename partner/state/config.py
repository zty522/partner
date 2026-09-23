"""Configuration management for Partner."""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


def _config_root(workspace: str) -> str:
    """Resolve the shared config directory — always workspace_root/config/.

    Works when workspace is either an instance dir (instances/03/) or
    the workspace root itself.
    """
    from ..workspace.workspace_layout import workspace_root_from_instance
    root = workspace_root_from_instance(workspace)
    return os.path.join(root, "config")


def get_partner_config_paths(workspace: str) -> List[str]:
    """Return config paths — unified under workspace_root/config/."""
    return [
        os.path.join(_config_root(workspace), "partner_config.json"),
    ]


def resolve_partner_config_path(workspace: str, prefer_existing: bool = True) -> str:
    paths = get_partner_config_paths(workspace)
    if prefer_existing:
        for path in paths:
            if os.path.exists(path):
                return path
    return paths[0]


def workspace_has_partner_config(workspace: str) -> bool:
    return any(os.path.exists(path) for path in get_partner_config_paths(workspace))


def load_partner_config_data(workspace: str) -> dict:
    config_path = resolve_partner_config_path(workspace)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_partner_config_data(workspace: str, data: dict):
    primary_path = resolve_partner_config_path(workspace, prefer_existing=False)
    os.makedirs(os.path.dirname(primary_path), exist_ok=True)
    with open(primary_path, "w", encoding="utf-8") as f:
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
    classify_timeout_sec: Optional[int] = None
    dynamic_ollama: Dict = field(default_factory=dict)
    ollama_pool: Dict = field(default_factory=dict)
    failover: Dict = field(default_factory=dict)
    project_timeout_sec: Optional[int] = 1200


@dataclass
class SchedulerConfig:
    """Scheduler configuration."""
    interval_minutes: int = 30
    max_tasks_per_cycle: int = 1
    heartbeat_timeout_minutes: int = 60


@dataclass
class RuntimeConfig:
    """Top-level operating mode. Experimental autonomy is opt-in."""
    mode: str = "manual_stable"
    automatic_campaigns: bool = False
    automatic_iteration: bool = False
    automatic_self_heal: bool = False
    autonomous_cron: bool = False
    step_messages: bool = True
    cognition_shadow_mirror: bool = False
    # Exactly one production component may decide whether a finished
    # WorkItem continues.  Legacy loops remain readable for audit/benchmarks
    # but must not own production continuation.
    continuation_owner: str = "work_item_runtime_v1"
    legacy_continuation_events_enabled: bool = False
    instance_native_autonomy: bool = False
    instance_native_auto_continue: bool = True
    instance_native_enabled_instances: List[str] = field(default_factory=list)
    instance_native_max_active: int = 2
    instance_native_max_learning_interruptions_per_failure: int = 1
    instance_native_slot_quantum_project_steps: int = 2
    # ADR 0061: real-action contract.  An instance-native project step is
    # only counted as progress when it ships a verifiable external action
    # and its findings are not a near-duplicate of the most recent receipt.
    # These knobs make the guard tunable per workspace without code edits.
    instance_native_max_repeat_findings: int = 2
    instance_native_require_external_artifact: bool = True


@dataclass
class JevRuntimeConfig:
    """Jev starts in shadow; credentials remain in the environment."""
    mode: str = "shadow"
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    model: str = "jev-latest"
    api_key_env: str = "TYPESAFE_API_KEY"
    timeout_seconds: float = 10.0


@dataclass
class CoreV1Config:
    enabled: bool = True
    jev: JevRuntimeConfig = field(default_factory=JevRuntimeConfig)
    budget: Dict = field(default_factory=lambda: {
        "max_actions": 1, "max_model_calls": 4, "max_child_flows": 1,
    })


@dataclass
class PartnerConfig:
    """Main configuration."""
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    core_v1: CoreV1Config = field(default_factory=CoreV1Config)
    name: str = "Partner"

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: str) -> 'PartnerConfig':
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            workspace=WorkspaceConfig(**data.get('workspace', {})),
            agent=AgentConfig(**data.get('agent', {})),
            scheduler=SchedulerConfig(**data.get('scheduler', {})),
            runtime=RuntimeConfig(**data.get('runtime', {})),
            core_v1=CoreV1Config(
                enabled=bool((data.get('core_v1') or {}).get('enabled', True)),
                jev=JevRuntimeConfig(**((data.get('core_v1') or {}).get('jev') or {})),
                budget=dict((data.get('core_v1') or {}).get('budget') or {
                    "max_actions": 1, "max_model_calls": 4, "max_child_flows": 1,
                }),
            ),
            name=data.get('name', 'Partner'),
        )


def runtime_mode(workspace: str) -> str:
    """Return the persisted operating mode, failing closed to manual_stable."""
    try:
        data = load_partner_config_data(workspace)
        runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
        return str(runtime.get("mode") or "manual_stable").strip().lower()
    except (OSError, ValueError, TypeError):
        return "manual_stable"


def manual_stable_mode(workspace: str) -> bool:
    return runtime_mode(workspace) == "manual_stable"


def runtime_capability_enabled(workspace: str, capability: str) -> bool:
    """Experimental autonomous capabilities are disabled unless explicitly enabled."""
    try:
        data = load_partner_config_data(workspace)
        runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
    except (OSError, ValueError, TypeError):
        runtime = {}
    defaults = {
        "automatic_campaigns": False,
        "automatic_iteration": False,
        "automatic_self_heal": False,
        "autonomous_cron": False,
        "step_messages": True,
        "cognition_shadow_mirror": False,
        "legacy_continuation_events_enabled": False,
        "instance_native_autonomy": False,
    }
    return bool(runtime.get(capability, defaults.get(capability, False)))
