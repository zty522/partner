"""Agent Adapter Layer - unified interface for different agent backends."""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import List, Optional


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
    def chat(self, message: str, max_tokens: int = None) -> str:
        """Have a conversation (used for the check-in interface)."""
        pass


class HermesAdapter(AgentAdapter):
    """Adapter for Hermes Agent via cronjob/subprocess."""
    
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
    
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
    
    def chat(self, message: str, max_tokens: int = None) -> str:
        """Chat via hermes subprocess."""
        import subprocess
        import shlex
        try:
            cmd = ["hermes", "chat", "--query", message, "--quiet", "--toolsets", ""]
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=60,
                cwd=self.workspace,
            )
            out = result.stdout.strip()
            if result.returncode == 0 and out:
                return out
            # Try with explicit provider fallback
            logger.warning(f"hermes chat returned {result.returncode}: {result.stderr[:200]}")
            return out or f"Error: {result.stderr[:200]}"
        except subprocess.TimeoutExpired:
            logger.warning(f"hermes chat timed out (60s) for query: {message[:60]}")
            return "timeout"
        except FileNotFoundError:
            return "Hermes agent is not available (not found in PATH)"
        except Exception as e:
            return f"Error: {e}"


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
    
    def chat(self, message: str, max_tokens: int = None) -> str:
        return "Direct mode: I can only work through scheduled tasks."


def create_adapter(backend: str, workspace_path: str) -> AgentAdapter:
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
