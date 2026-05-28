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
        """Execute a research task via Hermes CLI chat.

        Writes prompt to state file for reference, then invokes hermes chat
        to get a response. If hermes is unavailable, returns empty string
        (caller handles this gracefully).
        """
        import subprocess
        import os

        # Write prompt to state file for reference
        prompt_file = os.path.join(self.workspace, "state", "current_task.md")
        try:
            os.makedirs(os.path.dirname(prompt_file), exist_ok=True)
            with open(prompt_file, 'w') as f:
                f.write(prompt)
        except Exception:
            pass

        # Try hermes chat to execute the task
        try:
            cmd = ["hermes", "chat", "--query", prompt, "--quiet", "--toolsets", ""]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
                cwd=self.workspace,
            )
            out = result.stdout.strip()
            if result.returncode == 0 and out:
                return out
            logger.warning(f"hermes execute_task returned {result.returncode}: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            logger.warning(f"hermes execute_task timed out (60s)")
        except FileNotFoundError:
            logger.warning(f"hermes CLI not found")
        except Exception as e:
            logger.warning(f"hermes execute_task error: {e}")

        # No result — return empty, caller handles it
        return ""
    
    def chat(self, message: str, max_tokens: int = None) -> str:
        """Chat via hermes subprocess (fast timeout, fallback on hang)."""
        import subprocess
        import shlex
        try:
            cmd = ["hermes", "chat", "--query", message, "--quiet", "--toolsets", ""]
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=30,
                cwd=self.workspace,
            )
            out = result.stdout.strip()
            if result.returncode == 0 and out:
                return out
            # Try with explicit provider fallback
            logger.warning(f"hermes chat returned {result.returncode}: {result.stderr[:200]}")
            return None  # Don't send error text to user
        except subprocess.TimeoutExpired:
            logger.warning(f"hermes chat timed out (30s) for query: {message[:60]}")
            return None  # Fallback to local response
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.warning(f"hermes chat error: {e}")
            return None


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
    
    adapter_class = adapters.get(backend, DirectAdapter)
    return adapter_class(workspace_path)
