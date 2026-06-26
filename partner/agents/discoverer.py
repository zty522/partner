"""Auto-discover CLI agents from GitHub and register them in Partner.

When a user requests an agent that isn't registered yet, Partner uses Hermes
to search GitHub for the agent's repository, read its README, understand how
to install and invoke it via CLI, and create a manifest JSON automatically.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Default manifest template ──
_DEFAULT_MANIFEST_TEMPLATE = {
    "name": "",
    "version": "1.0.0",
    "description": "",
    "capabilities": [],
    "endpoint_type": "cli",
    "endpoint_config": {
        "command": "",
        "subcommand": "",
        "preamble_args": [],
        "args": [],
        "timeout": 3600,
        "inject_llm_credentials": False,
    },
    "health_check_cmd": "",
    "install_info": {
        "method": "pip",
        "package": "",
        "source": "",
        "post_install": [],
    },
}


def _get_workspace_agents_dir(workspace: str) -> str:
    """Get the workspace-level agents config directory.

    Resolves from instance workspace to workspace root:
      .../instances/03/ → .../config/agents/
    """
    # If workspace looks like an instance dir (.../instances/XX/), go up to root
    _parts = workspace.rstrip("/").split("/")
    if len(_parts) >= 2 and _parts[-2] == "instances":
        workspace_root = "/".join(_parts[:-2])
    else:
        workspace_root = workspace
    agent_dir = os.path.join(workspace_root, "config", "agents")
    os.makedirs(agent_dir, exist_ok=True)
    return agent_dir


def check_agent_registered(agent_name: str, workspace: str) -> bool:
    """Check if an agent is already registered (in any search path)."""
    try:
        from ..agents.registry import AgentRegistry

        registry = AgentRegistry(workspace=workspace)
        manifest = registry.get_agent(agent_name)
        return manifest is not None
    except Exception:
        pass
    # Also do a direct file check
    agent_dir = _get_workspace_agents_dir(workspace)
    for fname in os.listdir(agent_dir):
        if fname.startswith(agent_name) and fname.endswith(".json"):
            return True
    return False


async def discover_and_register_agent(
    agent_name: str,
    workspace: str,
    adapter: Any = None,
) -> str | None:
    """Use Hermes to discover a CLI agent from GitHub and register it.

    Steps:
    1. Check if already registered
    2. Use Hermes (via adapter.chat) to search GitHub for the agent
    3. Have Hermes analyze the README to determine CLI usage
    4. Create a manifest JSON and save to workspace config/agents/
    5. Return the path to the saved manifest, or None on failure
    """
    if check_agent_registered(agent_name, workspace):
        logger.info("[AGENT_DISCOVERY] %s already registered", agent_name)
        agent_dir = _get_workspace_agents_dir(workspace)
        for fname in os.listdir(agent_dir):
            if fname.startswith(agent_name) and fname.endswith(".json"):
                return os.path.join(agent_dir, fname)
        return None

    logger.info("[AGENT_DISCOVERY] Discovering agent: %s", agent_name)

    if adapter is None:
        from ..adapter import HermesAdapter
        adapter = HermesAdapter(workspace)

    # ── Prompt Hermes to search GitHub and analyze the agent ──
    prompt = (
        f"Your task is to discover how to use the CLI tool '{agent_name}'.\n\n"
        f"1. Search the web to find the GitHub repository for '{agent_name}'. "
        f"Look for its main repo (e.g., github.com/someone/{agent_name}).\n\n"
        f"2. Read the repository's README file (use the raw GitHub URL). "
        f"Understand:\n"
        f"   - What the tool does (description)\n"
        f"   - How to install it (pip/git/npm/go/cargo)\n"
        f"   - What CLI commands/subcommands it supports\n"
        f"   - What arguments/flags it accepts\n"
        f"   - How to run it with an input file and output directory\n\n"
        f"3. Return a JSON object (and ONLY valid JSON, no markdown) with this structure:\n"
        f"{json.dumps(_DEFAULT_MANIFEST_TEMPLATE, indent=2)}\n\n"
        f"Fill in the fields based on what you found:\n"
        f"- name: '{agent_name}'\n"
        f"- description: one-line summary\n"
        f"- endpoint_config.command: the CLI binary name\n"
        f"- endpoint_config.subcommand: if the tool has subcommands (e.g. 'run')\n"
        f"- endpoint_config.args: list of argument template strings. "
        f"Use {{input}} for input file path, {{output}} for output dir, "
        f"{{question}} for the user's question, {{device}} for cpu/cuda.\n"
        f"- install_info.method: 'pip', 'git', 'npm', 'go', 'cargo'\n"
        f"- install_info.package: package name if pip\n"
        f"- install_info.source: source URL if git\n\n"
        f"If you cannot find the agent, return: {{\"error\": \"agent not found\"}}"
    )

    try:
        reply = adapter.chat(prompt, purpose="action")
    except Exception as exc:
        logger.warning("[AGENT_DISCOVERY] Hermes chat failed: %s", exc)
        return None

    if not reply or reply.strip().startswith("__PARTNER_AGENT_"):
        logger.warning("[AGENT_DISCOVERY] Hermes returned unavailable")
        return None

    # ── Parse the JSON manifest from Hermes's reply ──
    manifest = _extract_json_from_reply(reply)
    if manifest is None:
        logger.warning("[AGENT_DISCOVERY] Failed to parse JSON from Hermes reply")
        return None

    if "error" in manifest:
        logger.warning("[AGENT_DISCOVERY] Hermes reported: %s", manifest["error"])
        return None

    # ── Fill in defaults ──
    manifest.setdefault("name", agent_name)
    manifest.setdefault("version", "1.0.0")
    manifest.setdefault("endpoint_type", "cli")
    if "endpoint_config" not in manifest:
        manifest["endpoint_config"] = {}
    manifest["endpoint_config"].setdefault("command", agent_name)
    manifest["endpoint_config"].setdefault("timeout", 3600)
    manifest["endpoint_config"].setdefault("inject_llm_credentials", False)
    if "install_info" not in manifest:
        manifest["install_info"] = {"method": "pip", "package": agent_name}
    manifest["install_info"].setdefault("method", "pip")
    manifest["install_info"].setdefault("post_install", [f"{agent_name} --help"])
    manifest.setdefault("health_check_cmd", f"{agent_name} --help")

    # ── Inject LLM credentials from Partner's config into preamble_args ──
    # If the manifest doesn't already have LLM credential placeholders, add them
    endpoint = manifest.setdefault("endpoint_config", {})
    preamble = endpoint.setdefault("preamble_args", [])
    _has_llm_base = any("{__llm_base_url__}" in str(a) for a in preamble)
    _has_llm_key = any("{__llm_api_key__}" in str(a) for a in preamble)
    _has_llm_model = any("{__llm_model__}" in str(a) for a in preamble)

    if not _has_llm_base:
        preamble.extend(["--llm-base-url", "{__llm_base_url__}"])
    if not _has_llm_key:
        preamble.extend(["--llm-api-key", "{__llm_api_key__}"])
    if not _has_llm_model:
        preamble.extend(["--llm-model", "{__llm_model__}"])
    endpoint["preamble_args"] = preamble
    endpoint["inject_llm_credentials"] = True

    # ── Save manifest to workspace ──
    agent_dir = _get_workspace_agents_dir(workspace)
    manifest_path = os.path.join(agent_dir, f"{agent_name}.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        logger.info("[AGENT_DISCOVERY] Saved manifest: %s", manifest_path)
        return manifest_path
    except OSError as exc:
        logger.error("[AGENT_DISCOVERY] Failed to save manifest: %s", exc)
        return None


def _extract_json_from_reply(reply: str) -> dict[str, Any] | None:
    """Extract a JSON object from Hermes's text reply.

    Handles markdown code fences and other common LLM output patterns.
    """
    text = reply.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # Find the first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    else:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try fixing common JSON errors
    try:
        import json5
        return json5.loads(text)
    except Exception:
        pass

    return None
