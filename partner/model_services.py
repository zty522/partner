"""Workspace-local configuration for auxiliary model services.

The main conversational LLM remains in ``config/api.json``.  Typed judges,
world models and future specialist models share ``config/model_services.json``
so their endpoint, model, mode and credential source are explicit and
independent from the main LLM provider.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import json
import os


CONFIG_NAME = "model_services.json"


def workspace_root(workspace: str | Path) -> Path:
    root = Path(workspace).resolve()
    return root.parent.parent if root.parent.name == "instances" else root


def config_path(workspace: str | Path) -> Path:
    return workspace_root(workspace) / "config" / CONFIG_NAME


def load_model_services(workspace: str | Path) -> dict[str, Any]:
    """Read the auxiliary-model registry, failing closed to no services."""
    try:
        value = json.loads(config_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"schema_version": 1, "services": {}}
    if not isinstance(value, dict):
        return {"schema_version": 1, "services": {}}
    services = value.get("services")
    value["services"] = dict(services) if isinstance(services, dict) else {}
    return value


def load_model_service(workspace: str | Path, name: str) -> dict[str, Any]:
    row = load_model_services(workspace).get("services", {}).get(name)
    return dict(row) if isinstance(row, Mapping) else {}


def resolve_api_key(service: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> str:
    """Resolve a credential without logging or serialising it into evidence."""
    auth = service.get("authentication")
    auth = dict(auth) if isinstance(auth, Mapping) else {}
    configured = str(auth.get("api_key") or service.get("api_key") or "").strip()
    if configured:
        return configured
    key_name = str(auth.get("api_key_env") or service.get("api_key_env") or "").strip()
    return str((environ if environ is not None else os.environ).get(key_name) or "").strip()


def update_model_service(workspace: str | Path, name: str, service: Mapping[str, Any]) -> Path:
    """Atomically update one service while preserving unrelated model entries."""
    path = config_path(workspace)
    value = load_model_services(workspace)
    value.setdefault("schema_version", 1)
    value.setdefault("services", {})[name] = dict(service)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
    return path
