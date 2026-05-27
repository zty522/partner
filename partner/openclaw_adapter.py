"""OpenClaw (小龙虾) Adapter for Partner.

Connects Partner to OpenClaw's agent CLI for task execution.
OpenClaw config: /home/os/.openclaw/openclaw.json
Gateway port: 18789 (WebSocket)
CLI command: openclaw agent --agent main --message "..." --json

Requires Node.js v22+ (installed via n at ~/.n/bin/node).
"""

import json
import os
import subprocess
from typing import List, Optional

from .adapter import AgentAdapter, SearchResult, ExecutionResult

# Ensure Node.js 22+ is on PATH (installed via `n`)
_N_BIN = os.path.expanduser("~/.n/bin")
if _N_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_N_BIN}:{os.environ.get('PATH', '')}"


class OpenClawAdapter(AgentAdapter):
    """Adapter for OpenClaw Agent via CLI."""
    
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
        self.config_dir = os.path.expanduser("~/.openclaw")
        self.config = self._load_config()
        self._gateway_url = None
        self._token = None
    
    def _load_config(self) -> dict:
        config_path = os.path.join(self.config_dir, "openclaw.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                return json.load(f)
        return {}
    
    @property
    def gateway_url(self) -> str:
        if not self._gateway_url:
            port = self.config.get("gateway", {}).get("port", 18789)
            self._gateway_url = f"http://localhost:{port}"
        return self._gateway_url
    
    @property
    def token(self) -> str:
        if not self._token:
            auth = self.config.get("gateway", {}).get("auth", {})
            self._token = auth.get("token", "")
        return self._token
    
    def name(self) -> str:
        return "openclaw"
    
    def is_available(self) -> bool:
        """Check if OpenClaw CLI is installed and gateway is running."""
        # Check 1: openclaw binary exists
        try:
            result = subprocess.run(
                ["openclaw", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        
        # Check 2: gateway health
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.gateway_url}/health",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False
    
    def search_web(self, query: str) -> List[SearchResult]:
        """Search web via OpenClaw's web_search tool."""
        prompt = (
            f"Use the web_search tool to search for: {query}. "
            f"Return the top 5 results with title, URL, and a brief snippet for each."
        )
        result = self.execute_task(prompt)
        return [SearchResult(title="OpenClaw web search", url="", snippet=result)]
    
    def execute_task(self, prompt: str, timeout: int = 300) -> str:
        """Execute a task via OpenClaw agent CLI.
        
        Uses `openclaw agent --agent main --message "..." --json`.
        """
        try:
            result = subprocess.run(
                [
                    "openclaw", "agent",
                    "--agent", "main",
                    "--message", prompt,
                    "--json",
                    "--timeout", str(timeout),
                ],
                capture_output=True, text=True, timeout=timeout + 30,
                cwd=self.workspace,
                env=os.environ.copy(),
            )
            
            if result.returncode != 0:
                stderr = result.stderr.strip()
                stdout = result.stdout.strip()
                # Try to parse JSON error from stdout
                if stdout:
                    try:
                        data = json.loads(stdout)
                        err = data.get("result", {}).get("error", "")
                        if err:
                            return f"OpenClaw error: {err}"
                    except json.JSONDecodeError:
                        pass
                return f"OpenClaw error (rc={result.returncode}): {stderr[:500] or stdout[:500]}"
            
            # Parse JSON response
            data = json.loads(result.stdout)
            payloads = data.get("result", {}).get("payloads", [])
            texts = [p.get("text", "") for p in payloads if p.get("text")]
            return "\n".join(texts) if texts else str(data)
        
        except subprocess.TimeoutExpired:
            return f"OpenClaw timeout after {timeout}s"
        except FileNotFoundError:
            return "OpenClaw CLI not found. Install with: npm install -g openclaw"
        except json.JSONDecodeError:
            return f"OpenClaw returned non-JSON: {result.stdout[:300]}"
        except Exception as e:
            return f"OpenClaw adapter error: {e}"
    
    def chat(self, message: str, max_tokens: int = None) -> str:
        """Chat via OpenClaw."""
        return self.execute_task(message, timeout=120)
