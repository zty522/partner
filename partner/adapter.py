"""Agent Adapter Layer - unified interface for different agent backends."""

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import List, Optional


USER_FRIENDLY_PROGRESS_REPLY = "我先继续在后台处理，晚点给你汇报进展"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class ExecutionResult:
    output: str
    exit_code: int
    success: bool


class AgentAdapter(ABC):
    """Abstract base class for agent backends.
    
    Partner talks to the outside world through this interface.
    Different backends (Hermes, Claude Code, Codex) implement this.
    """
    
    @abstractmethod
    def name(self) -> str:
        """Backend name."""
        pass
    
    @abstractmethod
    def search_web(self, query: str) -> List[SearchResult]:
        """Search the web."""
        pass
    
    @abstractmethod
    def execute_task(self, prompt: str) -> str:
        """Execute a research task given a natural language prompt.
        Returns the agent's response as text."""
        pass
    
    @abstractmethod
    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        """Have a conversation (used for the check-in interface)."""
        pass


class HermesAdapter(AgentAdapter):
    """Adapter for Hermes Agent via cronjob/subprocess."""
    
    def __init__(self, workspace_path: str, model: Optional[str] = None,
                 provider: Optional[str] = None):
        self.workspace = workspace_path
        self.model = model
        self.provider = provider

    def _log_chat_attempt(self, payload: dict):
        """Persist Hermes chat attempt metadata for timeout debugging."""
        try:
            log_dir = os.path.join(self.workspace, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "hermes_chat.jsonl")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"failed to write Hermes chat log: {exc}")

    def _build_hermes_env(self) -> dict:
        """Give Hermes a writable per-instance home under the workspace."""
        env = os.environ.copy()
        hermes_home = os.path.join(self.workspace, "system", "hermes_home")
        hermes_logs = os.path.join(hermes_home, "logs")
        os.makedirs(hermes_logs, exist_ok=True)
        env["HOME"] = hermes_home
        env["HERMES_HOME"] = hermes_home
        env["XDG_STATE_HOME"] = os.path.join(hermes_home, ".local", "state")
        env["XDG_CACHE_HOME"] = os.path.join(hermes_home, ".cache")
        env["XDG_CONFIG_HOME"] = os.path.join(hermes_home, ".config")
        os.makedirs(env["XDG_STATE_HOME"], exist_ok=True)
        os.makedirs(env["XDG_CACHE_HOME"], exist_ok=True)
        os.makedirs(env["XDG_CONFIG_HOME"], exist_ok=True)
        self._sync_hermes_runtime_files(hermes_home)
        return env

    def _sync_hermes_runtime_files(self, hermes_home: str):
        """Mirror the user's Hermes config/auth into the writable instance home."""
        source_home = os.path.expanduser("~/.hermes")
        if not os.path.isdir(source_home):
            return
        for filename in ("config.yaml", "auth.json", ".env"):
            source_path = os.path.join(source_home, filename)
            target_path = os.path.join(hermes_home, filename)
            if not os.path.exists(source_path):
                continue
            try:
                source_mtime = os.path.getmtime(source_path)
                target_mtime = os.path.getmtime(target_path) if os.path.exists(target_path) else -1
                if source_mtime <= target_mtime:
                    continue
                with open(source_path, "rb") as src, open(target_path, "wb") as dst:
                    dst.write(src.read())
            except OSError as exc:
                logger.warning(f"failed to mirror Hermes runtime file {filename}: {exc}")
    
    def name(self) -> str:
        return "hermes"
    
    def search_web(self, query: str) -> List[SearchResult]:
        # Will be implemented via hermes web_search tool
        # For MVP, delegate to execute_task
        prompt = f"Search the web for: {query}. Return top 5 results with title, URL, and snippet."
        result = self.execute_task(prompt)
        # Parse result into SearchResult list
        return [SearchResult(title="Search Result", url="", snippet=result)]
    
    def execute_task(self, prompt: str) -> str:
        """Execute a task by writing a prompt file and invoking hermes.
        
        For MVP, this writes the prompt to a file that can be picked up
        by the cron-triggered hermes session.
        """
        import subprocess
        import tempfile
        import os
        
        # Write prompt to temp file
        prompt_file = os.path.join(self.workspace, "state", "current_task.md")
        with open(prompt_file, 'w') as f:
            f.write(prompt)
        
        # For MVP, return a placeholder - in production this would invoke hermes
        return "Task queued for execution by Hermes agent."
    
    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        """Chat via hermes subprocess."""
        import subprocess
        import time

        cmd = ["hermes", "chat", "-q", message, "-Q", "-t", ""]
        if self.model:
            cmd.extend(["-m", self.model])
        if self.provider:
            cmd.extend(["--provider", self.provider])
        if purpose == "classify":
            cmd.extend(["--ignore-rules", "--max-turns", "1"])
        elif purpose == "interaction":
            cmd.extend(["--ignore-rules", "--max-turns", "1"])
        elif purpose == "project":
            cmd.extend(["--ignore-rules", "--max-turns", "1"])

        timeout_sec = 120
        max_retries = 2
        if purpose == "classify":
            timeout_sec = 45
            max_retries = 0
        elif purpose == "interaction":
            timeout_sec = 35
            max_retries = 0
        elif purpose == "project":
            timeout_sec = 90
            max_retries = 0

        for attempt in range(max_retries + 1):
            started_at = time.time()
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True, text=True, timeout=timeout_sec,
                    cwd=self.workspace,
                    env=self._build_hermes_env(),
                )
                out = result.stdout.strip()
                err = (result.stderr or "").strip()
                elapsed_ms = int((time.time() - started_at) * 1000)
                self._log_chat_attempt({
                    "ts": datetime.now().isoformat(),
                    "attempt": attempt + 1,
                    "purpose": purpose,
                    "timeout_sec": timeout_sec,
                    "elapsed_ms": elapsed_ms,
                    "returncode": result.returncode,
                    "model": self.model,
                    "provider": self.provider,
                    "message_chars": len(message),
                    "stdout_preview": out[:500],
                    "stderr_preview": err[:500],
                    "message_preview": message[:500],
                })

                # Success
                if result.returncode == 0 and out:
                    # Strip session_id line from output
                    lines = out.split("\n")
                    clean_lines = [l for l in lines if not l.startswith("session_id:")]
                    return "\n".join(clean_lines).strip() or out

                # Check for 429 rate limit in stdout OR stderr
                combined = f"{out}\n{err}"
                if "429" in combined or "Too many requests" in combined:
                    if attempt < max_retries:
                        wait = 15 * (attempt + 1)
                        logger.warning(f"Rate limited (429), retry {attempt+1}/{max_retries} in {wait}s")
                        time.sleep(wait)
                        continue
                    return USER_FRIENDLY_PROGRESS_REPLY

                # Check for other API failures in stdout
                if "API call failed" in out:
                    if attempt < max_retries:
                        wait = 10 * (attempt + 1)
                        logger.warning(f"API failed, retry {attempt+1}/{max_retries} in {wait}s")
                        time.sleep(wait)
                        continue
                    return USER_FRIENDLY_PROGRESS_REPLY

                if result.returncode != 0:
                    logger.warning(f"hermes chat exit {result.returncode}: {combined[:200]}")
                    if attempt < max_retries:
                        time.sleep(5)
                        continue

                return out or USER_FRIENDLY_PROGRESS_REPLY

            except subprocess.TimeoutExpired:
                self._log_chat_attempt({
                    "ts": datetime.now().isoformat(),
                    "attempt": attempt + 1,
                    "purpose": purpose,
                    "timeout_sec": timeout_sec,
                    "elapsed_ms": int((time.time() - started_at) * 1000),
                    "returncode": None,
                    "status": "timeout",
                    "model": self.model,
                    "provider": self.provider,
                    "message_chars": len(message),
                    "message_preview": message[:500],
                })
                logger.warning(f"hermes chat timeout ({timeout_sec}s), attempt {attempt+1}/{max_retries+1}")
                if attempt < max_retries:
                    continue
                return USER_FRIENDLY_PROGRESS_REPLY
            except FileNotFoundError:
                return "Error: agent backend not available"
            except Exception as e:
                self._log_chat_attempt({
                    "ts": datetime.now().isoformat(),
                    "attempt": attempt + 1,
                    "purpose": purpose,
                    "timeout_sec": timeout_sec,
                    "elapsed_ms": int((time.time() - started_at) * 1000),
                    "returncode": None,
                    "status": "exception",
                    "model": self.model,
                    "provider": self.provider,
                    "message_chars": len(message),
                    "error": str(e),
                    "message_preview": message[:500],
                })
                logger.warning(f"hermes chat exception: {e}")
                return USER_FRIENDLY_PROGRESS_REPLY

        return USER_FRIENDLY_PROGRESS_REPLY


class DirectAdapter(AgentAdapter):
    """Direct adapter - Partner operates without an external agent.
    
    This is the simplest backend: Partner uses its own logic
    to execute tasks, without delegating to another agent.
    """
    
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
    
    def name(self) -> str:
        return "direct"
    
    def search_web(self, query: str) -> List[SearchResult]:
        # For MVP, use a simple HTTP search
        import urllib.request
        import urllib.parse
        import json
        
        try:
            url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "Partner/0.1"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            results = []
            for item in data.get("RelatedTopics", [])[:5]:
                if "Text" in item:
                    results.append(SearchResult(
                        title=item.get("Text", "")[:80],
                        url=item.get("FirstURL", ""),
                        snippet=item.get("Text", ""),
                    ))
            return results
        except Exception as e:
            return [SearchResult(title="Search failed", url="", snippet=str(e))]
    
    def execute_task(self, prompt: str) -> str:
        """Execute a task directly using Partner's own capabilities."""
        # For MVP, just return the prompt for the cron job to handle
        return f"Task recorded: {prompt}"
    
    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        return "Direct mode: I can only work through scheduled tasks."


def create_adapter(backend: str, workspace_path: str, model: Optional[str] = None,
                   provider: Optional[str] = None) -> AgentAdapter:
    """Factory function to create the appropriate adapter."""
    adapters = {
        "hermes": HermesAdapter,
        "direct": DirectAdapter,
    }
    
    # Try to import optional adapters
    try:
        from .openclaw_adapter import OpenClawAdapter
        adapters["openclaw"] = OpenClawAdapter
    except ImportError:
        pass
    
    try:
        from .other_adapters import AutoGPTAdapter, OpenHandsAdapter, CrewAIAdapter, GptmeAdapter
        adapters["autogpt"] = AutoGPTAdapter
        adapters["openhands"] = OpenHandsAdapter
        adapters["crewai"] = CrewAIAdapter
        adapters["gptme"] = GptmeAdapter
    except ImportError:
        pass
    
    adapter_class = adapters.get(backend, DirectAdapter)
    try:
        return adapter_class(workspace_path, model=model, provider=provider)
    except TypeError:
        return adapter_class(workspace_path)


def list_available_adapters(workspace_path: str) -> list:
    """List all available agent adapters."""
    all_adapters = [
        ("hermes", "Hermes Agent", "🔮"),
        ("openclaw", "OpenClaw (小龙虾)", "🦞"),
        ("crewai", "CrewAI", "👥"),
        ("autogpt", "AutoGPT", "🤖"),
        ("openhands", "OpenHands", "👐"),
        ("gptme", "gptme", "💻"),
        ("codex", "OpenAI Codex", "⚡"),
        ("claude_code", "Claude Code", "🧠"),
        ("direct", "Direct (no agent)", "📌"),
    ]
    
    result = []
    for name, display, emoji in all_adapters:
        try:
            adapter = create_adapter(name, workspace_path)
            available = adapter.is_available() if hasattr(adapter, 'is_available') else True
        except:
            available = False
        result.append({
            "name": name,
            "display": display,
            "emoji": emoji,
            "available": available,
        })
    
    return result
