"""World Model 客户端 - 封装对世界模型的调用

支持 AETHER-backed 混合架构 (AETHER + LLM + Heuristic fallback).
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
import yaml

logger = logging.getLogger(__name__)


def load_world_model_config(workspace: str) -> dict:
    """Load world model configuration from workspace config directory.

    Searches for world_model.yaml in:
      1. <workspace>/config/world_model.yaml
      2. <workspace>/../config/world_model.yaml
      3. <workspace>/../../config/world_model.yaml

    Returns the 'world_model' section dict, with these keys:
      - enabled: bool
      - endpoint: str (main server URL)
      - aether_endpoint: str (AETHER GPU server URL, optional)
      - timeout: int
      - max_simulation_steps: int
      - fallback_to_llm: bool
      - mode: str (hybrid | aether_only | llm_only | heuristic_only)
    """
    resolved = os.path.abspath(workspace)
    candidates = [
        os.path.join(resolved, "config", "world_model.yaml"),
        os.path.join(resolved, "..", "config", "world_model.yaml"),
        os.path.join(resolved, "..", "..", "config", "world_model.yaml"),
    ]
    for path in candidates:
        normalized = os.path.normpath(path)
        if os.path.exists(normalized):
            try:
                with open(normalized, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                    wm_cfg = (cfg if isinstance(cfg, dict) else {}).get("world_model", {})
                    logger.info(
                        "[WORLD_MODEL] loaded config from %s: %s",
                        normalized,
                        wm_cfg,
                    )
                    return wm_cfg
            except Exception as exc:
                logger.debug("[WORLD_MODEL] failed to load %s: %s", normalized, exc)
                continue
    logger.info("[WORLD_MODEL] no config found, returning disabled")
    return {"enabled": False}


class WorldModelClient:
    """Client for the World Model MCP server.

    Provides simulation and optimization capabilities by calling the
    world model server's REST endpoints. The server internally uses an
    AETHER → LLM → Heuristic fallback chain.

    Config fields (from world_model.yaml):
      - enabled: enable/disable
      - endpoint: main server URL (default http://localhost:8100)
      - aether_endpoint: AETHER GPU server URL (default http://localhost:8080)
      - timeout: request timeout in seconds (default 60)
      - max_simulation_steps: max steps to simulate (default 10)
      - fallback_to_llm: whether to fall back to LLM if AETHER fails
      - mode: 'hybrid', 'aether_only', 'llm_only', 'heuristic_only'
    """

    def __init__(self, config: dict):
        self.enabled = config.get("enabled", False)
        self.endpoint = config.get("endpoint", "http://localhost:8100")
        self.aether_endpoint = config.get("aether_endpoint", "http://localhost:8080")
        self.timeout = config.get("timeout", 60)
        self.max_simulation_steps = config.get("max_simulation_steps", 10)
        self.fallback_to_llm = config.get("fallback_to_llm", True)
        self.mode = config.get("mode", "hybrid")

    async def simulate_plan(self, plan: List[Dict], state: Dict) -> Dict:
        """Simulate executing a plan and return predictions.

        Delegates to the world model server which internally tries:
          1. AETHER (GPU visual world model)
          2. LLM (language model API)
          3. Heuristic (rule-based fallback)

        Args:
            plan: List of step dictionaries with 'action', 'target', etc.
            state: Current environment state dictionary.

        Returns:
            Dict with simulation results, source info, and fallback indicators.
        """
        if not self.enabled:
            return {"status": "fallback", "reason": "world_model_disabled"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.endpoint}/simulate",
                    json={"plan": plan, "state": state},
                )
                resp.raise_for_status()
                data = resp.json()
                logger.debug(
                    "simulate_plan: _backend=%s, fallback=%s, risk=%.2f",
                    data.get("_backend", "unknown"),
                    data.get("fallback", False),
                    data.get("total_risk_score", 0),
                )
                return data
        except Exception as e:
            logger.warning(f"World model simulation failed: {e}")
            return {"status": "fallback", "reason": str(e)}

    async def optimize_plan(self, plan: List[Dict], state: Dict) -> Dict:
        """Suggest plan optimizations.

        Args:
            plan: List of step dictionaries.
            state: Current environment state dictionary.

        Returns:
            Dict with optimization suggestions, or fallback info if unavailable.
        """
        if not self.enabled:
            return {"status": "fallback", "reason": "world_model_disabled"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.endpoint}/optimize",
                    json={"plan": plan, "state": state},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"World model optimization failed: {e}")
            return {"status": "fallback", "reason": str(e)}

    async def health_check(self) -> Dict:
        """Check if the world model server is available.

        Returns the full health response including backend status:
          - aether (available, model_loaded)
          - llm (available, configured)
          - heuristic (always available)
          - current_backend
          - mode
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.endpoint}/health")
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return {"available": False}

    def is_available(self) -> bool:
        """Check if the world model client is configured and enabled."""
        return self.enabled
