"""Ollama probe — detect available Ollama services and check task suitability.

This module probes Ollama endpoints (local, remote, SSH tunnel) and provides
helpers to determine whether a task is suitable for lightweight Ollama models.

Ollama is an execution-layer option (not a routing type):
  - In direct_llm path: fallback when main model unavailable
  - In batch_plan path: call_agent_skill(agent="ollama", ...)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Cache for available models
_models_cache: dict[str, Any] = {"models": [], "fetched_at": 0.0}
_CACHE_TTL_SEC = 300  # 5 minutes


def load_ollama_config() -> dict:
    """Load Ollama suitability config from configs/ollama_suitability.yaml."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "configs", "ollama_suitability.yaml"),
        os.path.expanduser("~/.partner/ollama_suitability.yaml"),
    ]
    defaults = {
        "extra_endpoints": [],
        "suitable_patterns": [
            "翻译|translate", "你好|hello|hi|hey", "解释|explain|定义",
            "谢谢|thanks|thank", "sentiment|情感", "summarize|总结|摘要",
        ],
        "unsuitable_patterns": [
            "股票|stock|股价", "差异表达|RNA|基因|转录|代谢",
            "代码|生成.*代码|实现.*功能", "天气|weather|气温",
            "plot|绘图|图表|可视化", "推荐|餐厅", "报告|report|pdf",
            "表格|csv|excel",
        ],
        "constraints": {
            "suitable_max_context_tokens": 4096,
            "unsuitable_min_context_tokens": 8000,
            "model_min_size": "1B",
            "model_supports_tools": False,
        },
    }
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                if isinstance(loaded, dict):
                    # Deep merge
                    result = dict(defaults)
                    if isinstance(loaded.get("extra_endpoints"), list):
                        result["extra_endpoints"] = loaded["extra_endpoints"]
                    if isinstance(loaded.get("suitable_patterns"), list):
                        result["suitable_patterns"] = loaded["suitable_patterns"]
                    if isinstance(loaded.get("unsuitable_patterns"), list):
                        result["unsuitable_patterns"] = loaded["unsuitable_patterns"]
                    if isinstance(loaded.get("constraints"), dict):
                        result["constraints"] = {**result.get("constraints", {}), **loaded["constraints"]}
                    return result
            except Exception as exc:
                logger.debug("[OLLAMA_PROBE] failed to load config: %s", exc)
    return defaults


def check_ollama_endpoint(url: str, timeout: int = 3) -> bool:
    """Check if an Ollama endpoint is reachable.
    
    Bypasses http_proxy for localhost connections to prevent proxy
    502 errors when no_proxy is not set in the environment.
    """
    try:
        tags_url = url.rstrip("/") + "/api/tags"
        req = urllib.request.Request(tags_url, headers={"User-Agent": "Partner/0.7"})
        # Bypass proxy for localhost connections
        parsed = urllib.parse.urlparse(tags_url)
        hostname = parsed.hostname or ""
        if hostname in ("127.0.0.1", "localhost", "::1"):
            import os as _os
            orig_proxy = _os.environ.pop("http_proxy", None)
            _os.environ.pop("https_proxy", None)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                    return isinstance(data, dict) and "models" in data
            finally:
                if orig_proxy is not None:
                    _os.environ["http_proxy"] = orig_proxy
        else:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                return isinstance(data, dict) and "models" in data
    except Exception:
        return False


def probe_ollama_endpoints() -> list[dict]:
    """Probe all known endpoints and return available ones.

    Returns list of dicts: [{"name": "local", "base_url": "http://localhost:11434"}, ...]
    """
    config = load_ollama_config()
    endpoints: list[dict] = []

    # 1. Always probe localhost
    local_url = "http://127.0.0.1:11434"
    if check_ollama_endpoint(local_url):
        endpoints.append({"name": "local", "base_url": local_url})

    # 2. Probe configured extra endpoints (SSH tunnels, remote servers)
    for url in config.get("extra_endpoints", []):
        url_str = str(url).strip()
        if url_str and check_ollama_endpoint(url_str):
            endpoints.append({"name": "remote", "base_url": url_str})

    return endpoints


def get_ollama_models(endpoints: list[dict] | None = None) -> list[dict]:
    """Get available Ollama models from the first reachable endpoint.

    Results cached for _CACHE_TTL_SEC seconds.
    Returns list of dicts with model info: [{"name": "llama3.2:1b", "size": ..., ...}]
    """
    global _models_cache
    now = time.time()
    if _models_cache["models"] and (now - _models_cache["fetched_at"]) < _CACHE_TTL_SEC:
        return _models_cache["models"]

    if endpoints is None:
        endpoints = probe_ollama_endpoints()

    for ep in endpoints:
        base_url = ep.get("base_url", "")
        try:
            tags_url = base_url.rstrip("/") + "/api/tags"
            req = urllib.request.Request(tags_url, headers={"User-Agent": "Partner/0.7"})
            # Bypass proxy for localhost
            parsed = urllib.parse.urlparse(tags_url)
            if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
                orig = os.environ.pop("http_proxy", None)
                os.environ.pop("https_proxy", None)
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode("utf-8", "replace"))
                finally:
                    if orig is not None:
                        os.environ["http_proxy"] = orig
            else:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                models = data.get("models", [])
                if isinstance(models, list) and models:
                    # Normalize model entries
                    normalized = []
                    for m in models:
                        if isinstance(m, dict):
                            normalized.append({
                                "name": str(m.get("name", "unknown")),
                                "size": int(m.get("size", 0)),
                                "modified_at": str(m.get("modified_at", "")),
                            })
                        elif isinstance(m, str):
                            normalized.append({"name": m, "size": 0, "modified_at": ""})
                    _models_cache = {"models": normalized, "fetched_at": now}
                    logger.info("[OLLAMA_PROBE] found %d models at %s", len(normalized), base_url)
                    return normalized
        except Exception as exc:
            logger.debug("[OLLAMA_PROBE] failed to fetch models from %s: %s", base_url, exc)
            continue

    return []


def is_ollama_available() -> bool:
    """Quick check if any Ollama endpoint is available."""
    return len(probe_ollama_endpoints()) > 0


def is_task_suitable_for_ollama(task: str) -> bool:
    """Determine whether a task is suitable for lightweight Ollama models.

    Returns True if the task matches suitable patterns and does NOT match
    any unsuitable patterns.
    """
    config = load_ollama_config()
    text = str(task or "").strip()
    if not text:
        return False

    suitable = config.get("suitable_patterns", [])
    unsuitable = config.get("unsuitable_patterns", [])

    # Check unsuitable patterns first (fast reject)
    for pattern in unsuitable:
        try:
            if re.search(pattern, text, re.I):
                logger.debug("[OLLAMA_PROBE] unsuitable pattern matched: %s", pattern)
                return False
        except re.error:
            continue

    # Check suitable patterns
    for pattern in suitable:
        try:
            if re.search(pattern, text, re.I):
                logger.debug("[OLLAMA_PROBE] suitable pattern matched: %s", pattern)
                return True
        except re.error:
            continue

    # Default: not suitable (conservative)
    return False


def ollama_chat(message: str, model: str = "", endpoint_base: str = "") -> str:
    """Send a chat request to Ollama and return the response text.

    Args:
        message: The prompt/task to send.
        model: Model name (e.g. "llama3.2:1b"). If empty, uses first available.
        endpoint_base: Base URL of Ollama endpoint. If empty, auto-probes.

    Returns:
        Response text from Ollama model.
    """
    endpoints = probe_ollama_endpoints()
    if not endpoints:
        raise RuntimeError("No Ollama endpoints available")

    base = endpoint_base or endpoints[0]["base_url"]

    # If no model specified, use first available
    if not model:
        models = get_ollama_models(endpoints)
        if models:
            model = models[0]["name"]
        else:
            model = "llama3.2:1b"  # fallback default

    chat_url = base.rstrip("/") + "/api/chat"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        chat_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Partner/0.7",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            return str(data.get("message", {}).get("content", ""))
    except Exception as exc:
        raise RuntimeError(f"Ollama chat failed: {exc}") from exc
