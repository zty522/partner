from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


PROVIDER_DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "xiaomi": "https://token-plan-cn.xiaomimimo.com/v1",
    "openai": "https://api.openai.com/v1",
}


def workspace_root_from_path(workspace: str) -> str:
    path = os.path.abspath(workspace or os.getcwd())
    parts = path.split(os.sep)
    if "instances" in parts:
        idx = len(parts) - 1 - list(reversed(parts)).index("instances")
        if idx > 0:
            return os.sep.join(parts[:idx]) or os.sep
    return path


def unified_agent_config_path(workspace: str) -> str:
    return os.path.join(workspace_root_from_path(workspace), "config", "agent_api_config.json")


def instance_agent_config_path(workspace: str) -> str:
    return os.path.join(os.path.abspath(workspace or os.getcwd()), "config", "agent_api_config.json")


def load_agent_api_config(workspace: str) -> dict[str, Any]:
    for path in (unified_agent_config_path(workspace), instance_agent_config_path(workspace)):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as exc:
            logger.debug("[AGENT_CONFIG] failed to load %s: %s", path, exc)
    return {}


def desired_hermes_model_config(workspace: str) -> dict[str, str]:
    data = load_agent_api_config(workspace)
    routing = data.get("_routing") if isinstance(data.get("_routing"), dict) else {}
    default_agent = str(routing.get("default_agent") or "hermes").strip().lower()
    if default_agent != "hermes":
        return {}
    section = data.get("hermes") if isinstance(data.get("hermes"), dict) else {}
    provider = str(section.get("provider") or "").strip()
    model = str(section.get("model") or "").strip()
    base_url = str(section.get("base_url") or "").strip()
    api_key = str(section.get("api_key") or "").strip()
    if not provider and not model:
        return {}
    if provider and not base_url:
        base_url = PROVIDER_DEFAULT_BASE_URLS.get(provider.lower(), "")
    result = {"provider": provider, "model": model, "base_url": base_url, "api_key": api_key}
    return {k: v for k, v in result.items() if v}


def apply_hermes_model_config(config: dict[str, str], *, hermes_home: str | None = None) -> tuple[bool, str]:
    if not config:
        return True, "no Hermes model config requested"
    home = Path(hermes_home or os.path.expanduser("~/.hermes"))
    path = home / "config.yaml"
    try:
        home.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                data = loaded
        model_block = data.setdefault("model", {})
        if not isinstance(model_block, dict):
            model_block = {}
            data["model"] = model_block
        changed = False
        mapping = {
            "model": "default",
            "provider": "provider",
            "base_url": "base_url",
            "api_key": "api_key",
        }
        for source_key, target_key in mapping.items():
            value = str(config.get(source_key) or "").strip()
            if value and str(model_block.get(target_key) or "").strip() != value:
                model_block[target_key] = value
                changed = True
        if changed or not path.exists():
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            return True, f"Hermes model config updated: {path}"
        return True, "Hermes model config already matches"
    except Exception as exc:
        return False, str(exc)


def apply_workspace_hermes_config(workspace: str) -> tuple[bool, str]:
    return apply_hermes_model_config(desired_hermes_model_config(workspace))
