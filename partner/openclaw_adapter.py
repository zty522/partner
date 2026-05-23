"""OpenClaw (小龙虾) Adapter for Partner.

Connects Partner to OpenClaw's gateway API for task execution.
OpenClaw config: /home/os/.openclaw/openclaw.json
"""

import json
import os
import subprocess
from typing import List, Optional

from .adapter import AgentAdapter, SearchResult, ExecutionResult


class OpenClawAdapter(AgentAdapter):
    """Adapter for OpenClaw Agent."""
    
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
            # Default gateway port
            self._gateway_url = "http://localhost:3000"
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
        """Check if OpenClaw is installed and gateway is accessible."""
        config_path = os.path.join(self.config_dir, "openclaw.json")
        if not os.path.exists(config_path):
            return False
        
        # Check if gateway is running
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.gateway_url}/health",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except:
            # Gateway might not be running, but config exists
            return os.path.exists(config_path)
    
    def search_web(self, query: str) -> List[SearchResult]:
        """Search web via OpenClaw's tools."""
        prompt = f"Search the web for: {query}. Return titles, URLs, and snippets."
        result = self.execute_task(prompt)
        return [SearchResult(title="Search result", url="", snippet=result)]
    
    def execute_task(self, prompt: str) -> str:
        """Execute a task via OpenClaw gateway API."""
        try:
            import urllib.request
            import urllib.parse
            
            data = json.dumps({
                "message": prompt,
                "agent": self.config.get("agents", {}).get("defaults", {}).get("model", "default"),
            }).encode()
            
            req = urllib.request.Request(
                f"{self.gateway_url}/api/v1/chat",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.token}",
                },
                method="POST",
            )
            
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
                return result.get("response", result.get("message", str(result)))
        
        except Exception as e:
            # Fallback: use CLI
            return self._execute_via_cli(prompt)
    
    def _execute_via_cli(self, prompt: str) -> str:
        """Fallback: execute via OpenClaw CLI."""
        try:
            # Write prompt to temp file
            prompt_file = os.path.join(self.workspace, "state", "_openclaw_task.md")
            with open(prompt_file, 'w') as f:
                f.write(prompt)
            
            result = subprocess.run(
                ["openclaw", "chat", "--message", prompt],
                capture_output=True, text=True, timeout=120,
                cwd=self.workspace,
            )
            
            if result.returncode == 0:
                return result.stdout.strip()
            return f"OpenClaw error: {result.stderr[:500]}"
        
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return f"OpenClaw not available: {e}"
    
    def chat(self, message: str) -> str:
        """Chat via OpenClaw."""
        return self.execute_task(message)
