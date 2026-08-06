"""World Model 客户端 - 封装对世界模型的调用。

支持两种模式：
  - aether_only: 调用 AETHER GPU 后端，下载视频到本地 workspace
  - local: 读取 agent_runs.jsonl，从历史执行数据预测
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
                    logger.info("[WORLD_MODEL] loaded config from %s", normalized)
                    return wm_cfg
            except Exception as exc:
                logger.debug("[WORLD_MODEL] failed to load %s: %s", normalized, exc)
                continue
    logger.info("[WORLD_MODEL] no config found, returning disabled")
    return {"enabled": False}


class WorldModelClient:
    """Client for the World Model server.

    Provides simulation and video generation by calling the world model server's
    REST endpoints. The server internally calls AETHER (CogVideoX on GPU),
    saves generated videos, and downloads them to the local workspace.

    Config fields (from world_model.yaml):
      - enabled: enable/disable
      - endpoint: main server URL (default http://localhost:8100)
      - timeout: request timeout in seconds (default 120)
      - mode: 'aether_only' (default)
    """

    def __init__(self, config: dict):
        self.enabled = config.get("enabled", False)
        self.endpoint = config.get("endpoint", "http://localhost:8100")
        self.timeout = config.get("timeout", 120)
        self.mode = config.get("mode", "aether_only")

    async def simulate_plan(self, plan: List[Dict], state: Dict) -> Dict:
        """Simulate executing a plan via AETHER with video generation.

        The server:
          1. Calls AETHER API on remote GPU → generates 17 frames from plan
          2. Saves frames as PNGs + MP4 video on remote
          3. Downloads the video and artifacts to local workspace
          4. Returns results with local file paths

        Args:
            plan: List of step dicts with 'action', 'parameters', etc.
            state: Current environment state dict.

        Returns:
            Dict with:
              - status: "success" | "error"
              - backend: "aether"
              - session_id: unique session identifier
              - prompt: text prompt sent to AETHER
              - frames_generated: number of generated frames
              - elapsed_seconds: GPU inference time
              - local_session_dir: path to local output directory
              - video_path: local path to generated MP4
              - frames_dir: local path to frame images
              - downloaded_files: list of downloaded file paths
              - fallback: False (or True if degraded)
        """
        if not self.enabled:
            return {"status": "disabled", "reason": "world_model_disabled"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.endpoint}/simulate",
                    json={"plan": plan, "state": state},
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("status") == "success":
                    session_id = data.get("session_id", "")
                    video_path = data.get("video_path", "")
                    frames_dir = data.get("frames_dir", "")
                    local_dir = data.get("local_session_dir", "")
                    logger.info(
                        "WorldModel: session=%s, frames=%d, video=%s",
                        session_id,
                        data.get("frames_generated", 0),
                        video_path or "N/A",
                    )
                else:
                    logger.warning("WorldModel returned error: %s", data.get("error", "unknown"))

                return data

        except Exception as e:
            logger.warning("WorldModel server call failed: %s", e)
            return {"status": "error", "error": str(e), "backend": "unreachable"}

    async def health_check(self) -> Dict:
        """Check if the world model server is available."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.endpoint}/health")
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return {"available": False}

    def is_available(self) -> bool:
        return self.enabled
