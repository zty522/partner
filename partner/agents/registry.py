"""Agent Registry — central registry for discovering and managing agents.

Searches for manifests in:
1. ~/.partner/agents/ directory (user-registered agents)
2. config/agents/ under workspace
3. Built-in manifests shipped with Partner (hermes, openclaw, codex, etc.)
4. Remote URLs (future)
5. MCP discovery (future)
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from .manifest import AgentManifest


def _get_builtin_manifest_dir() -> str:
    """Return the path to the built-in manifest files shipped with Partner."""
    return os.path.join(os.path.dirname(__file__), "manifests")


def _get_user_manifest_dir() -> str:
    """Return the path to the user's ~/.partner/agents/ directory."""
    return os.path.join(os.path.expanduser("~"), ".partner", "agents")


class AgentRegistry:
    """Central registry for discovering and managing agents."""

    def __init__(self, workspace: str | None = None):
        self._workspace = workspace

    # ── Discovery ──

    def _discover_dirs(self) -> list[str]:
        """Return all directories to search for manifests, in priority order."""
        dirs: list[str] = []

        # 1. Built-in manifests
        builtin = _get_builtin_manifest_dir()
        if os.path.isdir(builtin):
            dirs.append(builtin)

        # 2. Workspace agents directory (instance level or root)
        if self._workspace:
            ws_agents = os.path.join(self._workspace, "config", "agents")
            if os.path.isdir(ws_agents):
                dirs.append(ws_agents)
            # Also check workspace root (when workspace is an instance dir)
            _parts = self._workspace.rstrip("/").split("/")
            if len(_parts) >= 2 and _parts[-2] == "instances":
                _root = "/".join(_parts[:-2])
                _root_agents = os.path.join(_root, "config", "agents")
                if os.path.isdir(_root_agents) and _root_agents not in dirs:
                    dirs.append(_root_agents)

        # 3. User-registered agents (~/.partner/agents/)
        user_dir = _get_user_manifest_dir()
        if os.path.isdir(user_dir):
            dirs.append(user_dir)

        return dirs

    def list_agents(self) -> list[AgentManifest]:
        """List all registered agents."""
        manifests: list[AgentManifest] = []
        seen_names: set[str] = set()

        for d in self._discover_dirs():
            for fname in sorted(os.listdir(d)):
                if not fname.endswith((".json", ".yaml", ".yml")):
                    continue
                fpath = os.path.join(d, fname)
                try:
                    m = AgentManifest.from_file(fpath)
                    if m.name not in seen_names:
                        seen_names.add(m.name)
                        manifests.append(m)
                except Exception as exc:
                    # Skip invalid manifests silently
                    continue

        return manifests

    def get_agent(self, name: str) -> AgentManifest | None:
        """Get agent by name."""
        for m in self.list_agents():
            if m.name == name:
                return m
        return None

    # ── Registration ──

    def register_agent(self, manifest: AgentManifest) -> bool:
        """Register a new agent manifest (saves to ~/.partner/agents/).

        Returns True on success.
        """
        user_dir = _get_user_manifest_dir()
        os.makedirs(user_dir, exist_ok=True)

        safe_name = manifest.name.replace(" ", "_").replace("/", "_")
        fpath = os.path.join(user_dir, f"{safe_name}.json")

        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2, ensure_ascii=False)

        # Non-blocking health check: log warning if agent is unavailable
        try:
            hc = self.health_check(manifest.name)
            if hc.get("status") not in ("ok", "unknown"):
                logger = __import__("logging").getLogger(__name__)
                logger.warning(
                    "[REGISTRY] agent '%s' registered but health check reports: %s — %s",
                    manifest.name, hc.get("status"), hc.get("details"),
                )
        except Exception:
            pass

        return True

    def register_from_file(self, manifest_path: str) -> bool:
        """Register an agent from a manifest file path.

        Copies the file to ~/.partner/agents/.
        """
        manifest = AgentManifest.from_file(manifest_path)
        return self.register_agent(manifest)

    def unregister_agent(self, name: str) -> bool:
        """Remove an agent registration.

        Only removes from ~/.partner/agents/ (user-registered).
        Built-in agents cannot be unregistered.
        """
        user_dir = _get_user_manifest_dir()
        if not os.path.isdir(user_dir):
            return False

        for fname in os.listdir(user_dir):
            if not fname.endswith((".json", ".yaml", ".yml")):
                continue
            fpath = os.path.join(user_dir, fname)
            try:
                m = AgentManifest.from_file(fpath)
                if m.name == name:
                    os.remove(fpath)
                    return True
            except Exception:
                continue
        return False

    # ── Query ──

    def find_by_capability(self, capability: str) -> list[AgentManifest]:
        """Find agents with a specific capability."""
        return [m for m in self.list_agents() if capability in m.capabilities]

    # ── Health ──

    def health_check(self, name: str) -> dict:
        """Check if an agent is available.

        Runs the agent's health_check_cmd or checks basic availability.
        Returns a dict with at least 'status' and 'details'.
        """
        manifest = self.get_agent(name)
        if not manifest:
            return {"status": "error", "details": f"Agent '{name}' not found"}

        # Health via command
        if manifest.health_check_cmd:
            try:
                r = subprocess.run(
                    manifest.health_check_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if r.returncode == 0:
                    return {
                        "status": "ok",
                        "details": r.stdout.strip() or "Health check passed",
                        "returncode": 0,
                    }
                else:
                    return {
                        "status": "unavailable",
                        "details": r.stderr.strip() or f"Exit code {r.returncode}",
                        "returncode": r.returncode,
                    }
            except FileNotFoundError:
                return {"status": "unavailable", "details": f"Command not found: {manifest.health_check_cmd}"}
            except subprocess.TimeoutExpired:
                return {"status": "timeout", "details": f"Health check timed out after 10s"}
            except Exception as exc:
                return {"status": "error", "details": str(exc)}

        # No health check command — basic check
        if manifest.endpoint_type == "cli":
            cmd = manifest.endpoint_config.get("command", "")
            if cmd:
                found = shutil.which(cmd.split()[0]) if cmd else None
                if found:
                    return {"status": "ok", "details": f"Binary found: {found}"}
                else:
                    return {"status": "unavailable", "details": f"Binary not found: {cmd.split()[0]}"}

        return {"status": "unknown", "details": "No health check configured"}
