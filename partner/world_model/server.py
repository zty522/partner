"""World Model Server — AETHER-backed with video download and recording.

Two modes:
  - aether_only: Call AETHER GPU backend, download video, return with local paths
  - local: Read from agent_runs.jsonl, predict outcomes from historical data

REST endpoints:
  POST /simulate   — simulate plan execution (AETHER → generate video → download)
  GET  /health     — server status
"""
import asyncio
import json
import logging
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG: Dict[str, Any] = {
    "HOST": os.environ.get("WORLD_MODEL_HOST", "0.0.0.0"),
    "PORT": int(os.environ.get("WORLD_MODEL_PORT", "8100")),
    "LOG_DIR": os.environ.get("WORLD_MODEL_LOG_DIR", "/tmp/workspace_world_model"),
    "MODE": os.environ.get("WORLD_MODEL_MODE", "aether_only"),
    # AETHER backend
    "AETHER_ENDPOINT": os.environ.get("AETHER_ENDPOINT", "http://localhost:8080"),
    "AETHER_TIMEOUT": int(os.environ.get("AETHER_TIMEOUT", "120")),
    # Local workspace for saving videos
    "WORKSPACE": os.environ.get("WORKSPACE", "/mnt/e/work/partner_workspace"),
    "OUTPUT_DIR": os.environ.get("WORLD_MODEL_OUTPUT_DIR", ""),  # defaults to {WORKSPACE}/world_model_outputs
}

_log_path = os.path.join(CONFIG["LOG_DIR"], "world_model.log")
os.makedirs(CONFIG["LOG_DIR"], exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(_log_path), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("world_model")

_start_time: float = time.time()


def _get_output_dir() -> str:
    """Get the output directory for saving world model artifacts."""
    if CONFIG["OUTPUT_DIR"]:
        out = CONFIG["OUTPUT_DIR"]
    else:
        out = os.path.join(CONFIG["WORKSPACE"], "world_model_outputs")
    os.makedirs(out, exist_ok=True)
    return out


# ===================================================================
# AETHER backend
# ===================================================================

async def _call_aether(plan: List[Dict], state: Dict) -> Optional[Dict]:
    """Call the AETHER API server on remote GPU. Returns parsed response or None."""
    endpoint = CONFIG["AETHER_ENDPOINT"].rstrip("/") + "/simulate"
    payload = {"plan": plan, "state": state, "max_steps": CONFIG.get("MAX_SIMULATION_STEPS", 10)}

    try:
        async with httpx.AsyncClient(timeout=CONFIG["AETHER_TIMEOUT"]) as client:
            resp = await client.post(endpoint, json=payload, headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "AETHER simulation succeeded: session=%s, frames=%s, elapsed=%.1fs",
                data.get("session_id", "?"), data.get("frames_generated", 0),
                data.get("elapsed_seconds", 0),
            )
            data["_backend"] = "aether"
            return data
    except httpx.TimeoutException:
        logger.warning("AETHER backend timed out after %ds", CONFIG["AETHER_TIMEOUT"])
    except httpx.HTTPStatusError as exc:
        logger.warning("AETHER backend returned HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
    except httpx.RequestError as exc:
        logger.warning("AETHER backend unreachable: %s", exc)
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("AETHER backend returned malformed response: %s", exc)
    return None


async def _download_aether_outputs(session_id: str, remote_out_dir: str, local_output_dir: str) -> Dict:
    """Download all files from a completed AETHER session to local workspace.

    Returns: {
        "local_session_dir": str,
        "video_path": str | None,
        "frames_dir": str,
        "downloaded_files": [str],
    }
    """
    base_url = CONFIG["AETHER_ENDPOINT"].rstrip("/")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    local_session_dir = os.path.join(local_output_dir, f"{ts}_{session_id}")

    # List remote files
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            list_resp = await client.get(f"{base_url}/download/list/{session_id}")
            if list_resp.status_code != 200:
                logger.warning("Failed to list remote files for session %s: HTTP %s", session_id, list_resp.status_code)
                return {"local_session_dir": local_session_dir, "video_path": None, "frames_dir": "", "downloaded_files": []}
            remote_files = list_resp.json().get("files", [])
    except Exception as e:
        logger.warning("Failed to list remote files for session %s: %s", session_id, e)
        return {"local_session_dir": local_session_dir, "video_path": None, "frames_dir": "", "downloaded_files": []}

    # Download each file
    os.makedirs(local_session_dir, exist_ok=True)
    downloaded = []
    video_path = None
    frames_dir = ""

    for rf in remote_files:
        rel_path = rf["path"]
        local_path = os.path.join(local_session_dir, rel_path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        url = f"{base_url}/download/{session_id}/{rel_path}"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                dl_resp = await client.get(url)
                if dl_resp.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(dl_resp.content)
                    downloaded.append(rel_path)
                    logger.debug("Downloaded %s (%d bytes)", rel_path, len(dl_resp.content))

                    if rel_path == "video.mp4":
                        video_path = local_path
                    elif rel_path.startswith("frames/"):
                        if not frames_dir:
                            frames_dir = os.path.dirname(local_path)
        except Exception as e:
            logger.warning("Failed to download %s: %s", rel_path, e)

    # Also download generation_log.json and README.md for the record
    for log_file in ["generation_log.json", "README.md", "input_prompt.txt"]:
        url = f"{base_url}/download/{session_id}/{log_file}"
        local_path = os.path.join(local_session_dir, log_file)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                dl_resp = await client.get(url)
                if dl_resp.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(dl_resp.content)
                    if log_file not in downloaded:
                        downloaded.append(log_file)
        except Exception:
            pass

    logger.info("Downloaded %d files for session %s to %s", len(downloaded), session_id, local_session_dir)
    return {
        "local_session_dir": local_session_dir,
        "video_path": video_path,
        "frames_dir": frames_dir,
        "downloaded_files": downloaded,
    }


async def _check_aether_health() -> Optional[Dict]:
    """Check if AETHER backend is healthy."""
    endpoint = CONFIG["AETHER_ENDPOINT"].rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(endpoint)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return None


# ===================================================================
# Main simulation handler
# ===================================================================

async def simulate_plan(plan: List[Dict], state: Dict) -> Dict:
    """Simulate plan execution via AETHER with video download."""
    mode = CONFIG["MODE"]
    logger.info("simulate_plan: %d steps, mode=%s", len(plan), mode)

    output_dir = _get_output_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    task_id = state.get("task_id", state.get("user_message", "unknown"))[:50]

    if mode == "aether_only":
        # Tier 1: AETHER
        logger.info("Calling AETHER backend...")
        aether_result = await _call_aether(plan, state)

        if aether_result is not None and aether_result.get("status") == "success":
            session_id = aether_result.get("session_id", "")
            remote_out_dir = aether_result.get("remote_output_dir", "")

            # Download video and artifacts from remote
            dl_result = await _download_aether_outputs(session_id, remote_out_dir, output_dir)

            # Build enriched result
            result = {
                "status": "success",
                "backend": "aether",
                "session_id": session_id,
                "prompt": aether_result.get("prompt", ""),
                "frames_generated": aether_result.get("frames_generated", 0),
                "elapsed_seconds": aether_result.get("elapsed_seconds", 0),
                "local_session_dir": dl_result["local_session_dir"],
                "video_path": dl_result["video_path"],
                "frames_dir": dl_result["frames_dir"],
                "downloaded_files": dl_result["downloaded_files"],
                "fallback": False,
                "fallback_chain": ["aether"],
            }
            logger.info("Simulation complete: video=%s, files=%d",
                        dl_result["video_path"], len(dl_result["downloaded_files"]))
            return result

        elif mode == "aether_only":
            # AETHER-only mode, no fallback
            logger.error("AETHER unavailable in aether_only mode")
            return {
                "status": "error",
                "backend": "none",
                "error": "AETHER backend unavailable (mode=aether_only)",
                "fallback": True,
                "fallback_chain": ["aether"],
            }

    return {"status": "error", "error": f"Unknown mode: {mode}"}


async def health_check() -> Dict:
    """Health check."""
    aether_health = await _check_aether_health()
    aether_available = aether_health is not None and aether_health.get("status") in ("ok",)

    return {
        "status": "healthy",
        "server": "World Model Server (AETHER)",
        "version": "3.0.0",
        "mode": CONFIG["MODE"],
        "current_backend": "aether" if aether_available else "none",
        "backends": {
            "aether": {
                "available": aether_available,
                "endpoint": CONFIG["AETHER_ENDPOINT"],
                "model_loaded": aether_health.get("model_loaded", False) if aether_health else False,
                "vram_gb": aether_health.get("vram_used_gb", 0) if aether_health else 0,
            },
        },
        "output_dir": _get_output_dir(),
        "uptime": time.time() - _start_time,
    }


# ===================================================================
# FastAPI Server
# ===================================================================
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

_fastapi_app = FastAPI(title="World Model Server (AETHER)", version="3.0.0")


class _SimulateRequestBody(BaseModel):
    plan: List[Dict[str, Any]]
    state: Dict[str, Any]


@_fastapi_app.post("/simulate")
async def _fastapi_simulate(body: _SimulateRequestBody):
    return await simulate_plan(body.plan, body.state)


@_fastapi_app.get("/health")
async def _fastapi_health():
    return await health_check()


@_fastapi_app.get("/outputs/{session_id:path}")
async def _fastapi_get_output(session_id: str):
    """Get the local path for a session's output."""
    output_dir = _get_output_dir()
    for entry in os.listdir(output_dir):
        if session_id in entry:
            session_dir = os.path.join(output_dir, entry)
            files = []
            for root, dirs, filenames in os.walk(session_dir):
                for fn in filenames:
                    rel = os.path.relpath(os.path.join(root, fn), session_dir)
                    files.append({"path": rel, "size": os.path.getsize(os.path.join(root, fn))})
            return {"session_dir": session_dir, "files": files}
    raise HTTPException(404, f"Session {session_id} not found locally")


# ===================================================================
# Main
# ===================================================================

def main():
    """Start the world model server."""
    output_dir = _get_output_dir()
    logger.info("=" * 60)
    logger.info("World Model Server (AETHER) v3.0.0")
    logger.info("=" * 60)
    logger.info("Host: %s:%s", CONFIG["HOST"], CONFIG["PORT"])
    logger.info("Mode: %s", CONFIG["MODE"])
    logger.info("AETHER endpoint: %s", CONFIG["AETHER_ENDPOINT"])
    logger.info("Output directory: %s", output_dir)
    logger.info("Logging to: %s", _log_path)
    logger.info("REST endpoints: POST /simulate, GET /health, GET /outputs/{session_id}")

    uvicorn.run(_fastapi_app, host=CONFIG["HOST"], port=CONFIG["PORT"])


if __name__ == "__main__":
    main()
