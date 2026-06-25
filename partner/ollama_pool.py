"""Configurable Ollama endpoint pool.

Partner can use several local/remote Ollama servers as optional cost-saving
backends.  The pool is best-effort: every call probes endpoints in configured
order and returns None when unavailable so the caller can fall back to the API
backend without surfacing errors to users.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any


DEFAULT_MODE = "lite"
VALID_MODES = {"off", "lite", "project", "all"}
DEFAULT_LOCAL_ENDPOINTS = (
    ("auto-local", "http://127.0.0.1:11434"),
    ("auto-tunnel", "http://127.0.0.1:11435"),
)


@dataclass
class OllamaSelection:
    name: str
    base_url: str
    api_base_url: str
    model: str
    mode: str
    reason: str


def _load_agent_config(workspace: str) -> dict:
    for rel in ("00_config/partner_config.json", "partner_config.json"):
        path = os.path.join(workspace, rel)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            agent = data.get("agent") if isinstance(data, dict) else None
            if isinstance(agent, dict):
                return agent
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return {}


def _api_base(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/v1") else base + "/v1"


def _api_root(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _split_models(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def load_ollama_pool_config(workspace: str) -> dict:
    agent = _load_agent_config(workspace)
    pool = agent.get("ollama_pool") if isinstance(agent, dict) else None
    if not isinstance(pool, dict):
        pool = {}

    legacy_dynamic = agent.get("dynamic_ollama") if isinstance(agent, dict) else None
    if not pool.get("endpoints") and isinstance(legacy_dynamic, dict) and legacy_dynamic.get("base_url"):
        pool = {
            **pool,
            "enabled": bool(legacy_dynamic.get("enabled", False)),
            "mode": "project",
            "endpoints": [{
                "name": "legacy_dynamic",
                "base_url": legacy_dynamic.get("base_url", ""),
                "models": legacy_dynamic.get("models") or ["qwen3:1.7b", "qwen3:4b", "qwen2.5:14b", "llama3.3:7b", "qwen2.5:7b"],
                "enabled": True,
            }],
        }

    env_base = os.getenv("PARTNER_OLLAMA_POOL_BASE_URL") or os.getenv("PARTNER_OLLAMA_BASE_URL")
    if not pool.get("endpoints") and env_base:
        pool = {
            **pool,
            "enabled": True,
            "mode": os.getenv("PARTNER_OLLAMA_POOL_MODE", DEFAULT_MODE),
            "endpoints": [{
                "name": "env",
                "base_url": env_base,
                "models": _split_models(os.getenv("PARTNER_OLLAMA_POOL_MODELS") or os.getenv("PARTNER_OLLAMA_MODEL") or "qwen3:1.7b,qwen3:4b,qwen2.5:14b,llama3.3:7b,qwen2.5:7b"),
                "enabled": True,
            }],
        }

    mode = str(pool.get("mode") or DEFAULT_MODE).strip().lower()
    if mode not in VALID_MODES:
        mode = DEFAULT_MODE
    endpoints = pool.get("endpoints") if isinstance(pool.get("endpoints"), list) else []
    auto_discover = pool.get("auto_discover")
    if auto_discover is None:
        auto_discover = True
    if auto_discover:
        seen_urls = {
            str(endpoint.get("base_url") or "").strip().rstrip("/")
            for endpoint in endpoints
            if isinstance(endpoint, dict)
        }
        default_models = (
            os.getenv("PARTNER_OLLAMA_AUTO_MODELS")
            or os.getenv("PARTNER_OLLAMA_POOL_MODELS")
            or os.getenv("PARTNER_OLLAMA_MODEL")
            or "qwen3:1.7b,qwen3:4b,qwen2.5:14b,llama3.3:7b,qwen2.5:7b,qwen2.5:0.5b"
        )
        for name, base_url in DEFAULT_LOCAL_ENDPOINTS:
            if base_url not in seen_urls:
                endpoints.append({
                    "name": name,
                    "base_url": base_url,
                    "models": _split_models(default_models),
                    "enabled": True,
                    "auto_discovered": True,
                    "location": "local" if name == "auto-local" else "tunnel",
                })
                seen_urls.add(base_url)

    return {
        "enabled": bool(pool.get("enabled", False)),
        "mode": mode,
        "probe_timeout_sec": float(pool.get("probe_timeout_sec") or os.getenv("PARTNER_OLLAMA_PROBE_TIMEOUT_SEC") or 2.0),
        "heartbeat_probe_timeout_sec": float(pool.get("heartbeat_probe_timeout_sec") or os.getenv("PARTNER_OLLAMA_HEARTBEAT_PROBE_TIMEOUT_SEC") or 1.5),
        "chat_timeout_sec": int(float(pool.get("chat_timeout_sec") or os.getenv("PARTNER_OLLAMA_TIMEOUT_SEC") or 30)),
        "max_input_chars": int(float(pool.get("max_input_chars") or os.getenv("PARTNER_OLLAMA_MAX_INPUT_CHARS") or 4000)),
        "auto_discover": bool(auto_discover),
        "endpoints": endpoints,
    }


def purpose_allowed(mode: str, purpose: str) -> bool:
    mode = (mode or DEFAULT_MODE).lower()
    if mode == "off":
        return False
    if mode == "all":
        return purpose in {"classify", "interaction", "report", "project", "chat"}
    if mode == "project":
        return purpose == "project"
    # lite/default: cheap short LLM work only.
    return purpose in {"chat", "interaction", "classify", "short_summary", "report"}


def write_status(workspace: str, payload: dict) -> None:
    try:
        state_dir = os.path.join(workspace, "state")
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, "ollama_pool_status.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": datetime.now().isoformat(), **payload}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _available_models(base_url: str, timeout: float) -> set[str]:
    with urllib.request.urlopen(_api_root(base_url) + "/api/tags", timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return {
        item.get("name") or item.get("model")
        for item in data.get("models", [])
        if item.get("name") or item.get("model")
    }


def _probe_chat(base_url: str, model: str, timeout: float) -> tuple[bool, str]:
    if "qwen3" in (model or "").lower():
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "只回复 OK"}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 16},
        }
        req = urllib.request.Request(
            _api_root(base_url) + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            reply = data.get("message", {}).get("content", "")
            elapsed_ms = int((time.time() - start) * 1000)
            return bool(reply.strip()), f"probe_ok:{elapsed_ms}ms" if reply.strip() else "empty_probe_reply"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            return False, f"{type(exc).__name__}: {str(exc)[:160]}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "stream": False,
        "temperature": 0,
        "max_tokens": 256 if "qwen3" in (model or "").lower() else 16,
    }
    req = urllib.request.Request(
        _api_base(base_url) + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"},
        method="POST",
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        elapsed_ms = int((time.time() - start) * 1000)
        return bool(reply.strip()), f"probe_ok:{elapsed_ms}ms" if reply.strip() else "empty_probe_reply"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def select_ollama(workspace: str, purpose: str) -> OllamaSelection | None:
    """Probe configured Ollama endpoints in order.  Returns None on fallback."""
    cfg = load_ollama_pool_config(workspace)
    mode = cfg["mode"]
    if not cfg["enabled"] or not purpose_allowed(mode, purpose):
        write_status(workspace, {
            "selected": "",
            "fallback": "primary_agent",
            "purpose": purpose,
            "mode": mode,
            "reason": "disabled_or_purpose_not_allowed",
        })
        return None

    probe_results: list[dict] = []
    for endpoint in cfg["endpoints"]:
        if not isinstance(endpoint, dict) or endpoint.get("enabled") is False:
            continue
        name = str(endpoint.get("name") or endpoint.get("base_url") or "ollama").strip()
        base_url = str(endpoint.get("base_url") or "").strip().rstrip("/")
        models = _split_models(endpoint.get("models") or endpoint.get("model") or cfg.get("models") or "qwen3:1.7b,qwen3:4b,qwen2.5:14b,llama3.3:7b,qwen2.5:7b")
        if not base_url or not models:
            probe_results.append({"endpoint": name, "ok": False, "reason": "missing_base_url_or_models"})
            continue
        try:
            available = _available_models(base_url, cfg["probe_timeout_sec"])
        except Exception as exc:
            probe_results.append({"endpoint": name, "base_url": base_url, "ok": False, "reason": f"tags_failed:{str(exc)[:160]}"})
            continue
        for model in models:
            if model not in available:
                probe_results.append({"endpoint": name, "model": model, "ok": False, "reason": "model_not_installed"})
                continue
            ok, reason = _probe_chat(base_url, model, cfg["probe_timeout_sec"])
            probe_results.append({"endpoint": name, "base_url": base_url, "model": model, "ok": ok, "reason": reason})
            if ok:
                selection = OllamaSelection(
                    name=name,
                    base_url=base_url,
                    api_base_url=_api_base(base_url),
                    model=model,
                    mode=mode,
                    reason=reason,
                )
                write_status(workspace, {
                    "selected": model,
                    "endpoint": name,
                    "base_url": base_url,
                    "purpose": purpose,
                    "mode": mode,
                    "fallback": "",
                    "reason": reason,
                    "probe_results": probe_results,
                })
                return selection

    write_status(workspace, {
        "selected": "",
        "fallback": "primary_agent",
        "purpose": purpose,
        "mode": mode,
        "reason": "no_configured_endpoint_available",
        "probe_results": probe_results,
    })
    return None


def heartbeat_probe(workspace: str, purpose: str = "report") -> dict:
    """Fast heartbeat probe for configured/local/tunnel Ollama endpoints.

    This intentionally does not run a chat completion.  It keeps status fresh
    and detects when same-machine Ollama or a reverse SSH tunnel becomes
    reachable; the real call path still performs a model/chat probe before use.
    """
    cfg = load_ollama_pool_config(workspace)
    mode = cfg["mode"]
    timeout = max(0.2, min(float(cfg.get("heartbeat_probe_timeout_sec") or 1.5), float(cfg.get("probe_timeout_sec") or 2.0), 3.0))
    if not cfg["enabled"] or not purpose_allowed(mode, purpose):
        payload = {
            "selected": "",
            "fallback": "primary_agent",
            "purpose": purpose,
            "mode": mode,
            "reason": "disabled_or_purpose_not_allowed",
            "heartbeat_probe": True,
        }
        write_status(workspace, payload)
        return payload

    probe_results: list[dict] = []
    selected = None
    for endpoint in cfg["endpoints"]:
        if not isinstance(endpoint, dict) or endpoint.get("enabled") is False:
            continue
        name = str(endpoint.get("name") or endpoint.get("base_url") or "ollama").strip()
        base_url = str(endpoint.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            probe_results.append({"endpoint": name, "ok": False, "reason": "missing_base_url"})
            continue
        try:
            available = _available_models(base_url, timeout)
        except Exception as exc:
            probe_results.append({
                "endpoint": name,
                "base_url": base_url,
                "ok": False,
                "reason": f"tags_failed:{str(exc)[:160]}",
                "auto_discovered": bool(endpoint.get("auto_discovered")),
            })
            continue
        models = _split_models(endpoint.get("models") or endpoint.get("model") or "qwen3:1.7b,qwen3:4b,qwen2.5:14b,llama3.3:7b,qwen2.5:7b")
        usable = [model for model in models if model in available]
        row = {
            "endpoint": name,
            "base_url": base_url,
            "ok": bool(usable),
            "available_models": sorted(str(x) for x in available if x)[:20],
            "usable_models": usable,
            "auto_discovered": bool(endpoint.get("auto_discovered")),
        }
        if not usable:
            row["reason"] = "no_configured_model_installed"
        else:
            row["reason"] = "tags_ok"
        probe_results.append(row)
        if usable and selected is None:
            selected = {"endpoint": name, "base_url": base_url, "model": usable[0], "reason": "tags_ok"}

    payload = {
        "selected": (selected or {}).get("model", ""),
        "endpoint": (selected or {}).get("endpoint", ""),
        "base_url": (selected or {}).get("base_url", ""),
        "fallback": "" if selected else "primary_agent",
        "purpose": purpose,
        "mode": mode,
        "reason": (selected or {}).get("reason") or "no_configured_endpoint_available",
        "heartbeat_probe": True,
        "probe_results": probe_results,
    }
    write_status(workspace, payload)
    return payload


def test_pool(workspace: str, purpose: str = "report") -> dict:
    selected = select_ollama(workspace, purpose)
    cfg = load_ollama_pool_config(workspace)
    status_path = os.path.join(workspace, "state", "ollama_pool_status.json")
    status = {}
    try:
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)
    except Exception:
        pass
    return {
        "configured": cfg,
        "selected": selected.__dict__ if selected else None,
        "status": status,
    }
