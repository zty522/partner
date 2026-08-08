"""Partner workspace utilities — centralized path resolution.

All ~/.partner hardcoded paths should be replaced with calls to these functions.
Data is stored under {workspace}/partner_data/ for unified management.
"""
from __future__ import annotations

import os
from typing import Optional

# ── Workspace resolution ──────────────────────────────────────────

_WORKSPACE_CACHE: dict[str, str] = {}


def resolve_workspace(workspace: str | None = None) -> str:
    """Resolve the effective workspace path.
    
    Priority:
    1. Explicit workspace parameter
    2. PARTNER_WORKSPACE env var
    3. Instance workspace from ~/.partner_workspace pointer
    4. Fallback to current directory
    """
    if workspace:
        return workspace
    
    cached = _WORKSPACE_CACHE.get("workspace")
    if cached:
        return cached
    
    env_ws = os.environ.get("PARTNER_WORKSPACE", "")
    if env_ws:
        _WORKSPACE_CACHE["workspace"] = env_ws
        return env_ws
    
    # Check pointer file
    pointer = os.path.expanduser("~/.partner_workspace")
    if os.path.isfile(pointer):
        try:
            with open(pointer) as f:
                ws = f.read().strip()
            if ws and os.path.isdir(ws):
                _WORKSPACE_CACHE["workspace"] = ws
                return ws
        except Exception:
            pass
    
    # Fallback to cwd
    cwd = os.getcwd()
    _WORKSPACE_CACHE["workspace"] = cwd
    return cwd


# ── Partner data directory ────────────────────────────────────────

_DEFAULT_PARTNER_DATA: str | None = None


def get_partner_data_dir(workspace: str | None = None) -> str:
    """Get the data directory under workspace for Partner data.
    
    All databases, configs, agents, and reports should be stored here.
    """
    # First check env var
    env_dir = os.environ.get("PARTNER_DATA_DIR", "")
    if env_dir:
        return env_dir
    
    # Fallback to ~/.partner for backward compatibility
    fallback = os.path.expanduser("~/.partner")
    if workspace:
        ws = workspace
    else:
        ws = resolve_workspace(None)
    
    # Use workspace-based path
    partner_data = os.path.join(ws, "partner_data")
    return partner_data


def ensure_partner_data_dir(workspace: str | None = None) -> str:
    """Ensure the partner data directory exists and return its path."""
    path = get_partner_data_dir(workspace)
    os.makedirs(path, exist_ok=True)
    return path


# ── Database paths ────────────────────────────────────────────────

def get_learning_db_path(workspace: str | None = None) -> str:
    """Path to the shared learning/evolution database."""
    return os.path.join(get_partner_data_dir(workspace), "learning.db")

def get_skills_db_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "skills_registry.db")

def get_queue_db_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "queue.db")

def get_projects_db_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "projects.db")

def get_habits_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "habits.json")

def get_memory_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "long_term_memory.json")


# ── Subdirectory paths ────────────────────────────────────────────

def get_agents_dir(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "agents")

def get_reports_dir(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "reports")

def get_config_dir(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "config")

def get_growth_dir(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "growth")

def get_learning_dir(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "learning")

def get_screenshots_dir(workspace: str | None = None) -> str:
    """Canonical screenshots directory under partner_data."""
    path = os.path.join(get_partner_data_dir(workspace), "screenshots")
    os.makedirs(path, exist_ok=True)
    return path


# ── Config file paths ─────────────────────────────────────────────

def get_config_path(workspace: str | None = None) -> str:
    """Path to main config.json."""
    return os.path.join(get_partner_data_dir(workspace), "config.json")

def get_routing_rules_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "routing_rules.yaml")

def get_message_filter_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "message_filter.yaml")

def get_ollama_config_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "ollama_suitability.yaml")

def get_event_execution_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "event_execution.yaml")

def get_evolution_external_path(workspace: str | None = None) -> str:
    return os.path.join(get_partner_data_dir(workspace), "config", "evolution_external.yaml")
