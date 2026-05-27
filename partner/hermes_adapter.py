"""Hermes Adapter - integrates Partner with Hermes Agent.

This adapter lets Partner use Hermes's tools (web search, file ops,
code execution, etc.) to actually execute research tasks.
"""

import json
import os
import subprocess
import tempfile
from typing import List

from .adapter import AgentAdapter, SearchResult, ExecutionResult


class HermesAdapter(AgentAdapter):
    """Adapter for Hermes Agent.
    
    Execution strategy (in order of preference):
    1. Direct API: if hermes_tools is available in the current Python env
    2. CLI subprocess: invoke `hermes` CLI tool
    3. Fallback: write task to file for cron-picked execution
    """
    
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
        self._hermes_available = None
    
    def name(self) -> str:
        return "hermes"
    
    def _check_hermes(self) -> str:
        """Check how Hermes is available."""
        if self._hermes_available is not None:
            return self._hermes_available
        
        # Check 1: hermes_tools importable
        try:
            import hermes_tools
            self._hermes_available = "api"
            return "api"
        except ImportError:
            pass
        
        # Check 2: hermes CLI available
        try:
            result = subprocess.run(
                ["hermes", "--version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self._hermes_available = "cli"
                return "cli"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        self._hermes_available = "none"
        return "none"
    
    def search_web(self, query: str) -> List[SearchResult]:
        mode = self._check_hermes()
        
        if mode == "api":
            return self._search_web_api(query)
        elif mode == "cli":
            return self._search_web_cli(query)
        else:
            return [SearchResult(title="Hermes not available", url="", snippet="")]
    
    def _search_web_api(self, query: str) -> List[SearchResult]:
        """Search via hermes_tools API."""
        try:
            from hermes_tools import web_search
            result = web_search(query)
            # Parse results
            if isinstance(result, dict) and "results" in result:
                return [
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("snippet", r.get("content", "")),
                    )
                    for r in result["results"][:5]
                ]
            return [SearchResult(title="Search completed", url="", snippet=str(result)[:500])]
        except Exception as e:
            return [SearchResult(title="Search error", url="", snippet=str(e))]
    
    def _search_web_cli(self, query: str) -> List[SearchResult]:
        """Search via hermes CLI."""
        try:
            result = subprocess.run(
                ["hermes", "chat", "--message", f"Search the web for: {query}"],
                capture_output=True, text=True, timeout=60,
                cwd=self.workspace,
            )
            return [SearchResult(title="Search result", url="", snippet=result.stdout[:1000])]
        except Exception as e:
            return [SearchResult(title="Search error", url="", snippet=str(e))]
    
    def execute_task(self, prompt: str) -> str:
        """Execute a research task via Hermes."""
        mode = self._check_hermes()
        
        if mode == "api":
            return self._execute_via_api(prompt)
        elif mode == "cli":
            return self._execute_via_cli(prompt)
        else:
            return self._execute_via_file(prompt)
    
    def _execute_via_api(self, prompt: str) -> str:
        """Execute using hermes_tools directly."""
        try:
            from hermes_tools import terminal, web_search, read_file, write_file
            
            # Build a comprehensive research prompt
            full_prompt = f"""You are executing a research task for Partner.

TASK: {prompt}

Instructions:
1. If this is a literature search, use web_search to find relevant papers
2. If this is a project analysis, use read_file to examine the project
3. Synthesize your findings into a clear summary
4. Include specific details: paper titles, years, methods, metrics
5. Respond in the same language as the task description

Execute this task now."""
            
            # Use hermes terminal to run a Python script that does the research
            script = f'''
import json
import sys
sys.path.insert(0, "{self.workspace}")

# The prompt for the research
prompt = """{full_prompt}"""

print(prompt)
print("\\n---\\nExecuting via Hermes Agent session...")
'''
            
            # Write script to temp file
            script_path = os.path.join(self.workspace, "state", "_current_task.py")
            with open(script_path, 'w') as f:
                f.write(script)
            
            # For API mode, we return the prompt for the cron job to handle
            # The actual execution happens in the Hermes cron cycle
            return f"[Task queued for Hermes execution]\n\nPrompt:\n{full_prompt}"
            
        except Exception as e:
            return f"Error executing via API: {e}"
    
    def _execute_via_cli(self, prompt: str) -> str:
        """Execute using hermes CLI."""
        try:
            # Write prompt to file
            prompt_file = os.path.join(self.workspace, "state", "_task_prompt.md")
            with open(prompt_file, 'w') as f:
                f.write(prompt)
            
            result = subprocess.run(
                ["hermes", "chat", "--message", prompt],
                capture_output=True, text=True, timeout=300,
                cwd=self.workspace,
            )
            
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                return f"Hermes error (exit {result.returncode}): {result.stderr[:500]}"
                
        except subprocess.TimeoutExpired:
            return "Hermes execution timed out (5 minutes)"
        except Exception as e:
            return f"Error executing via CLI: {e}"
    
    def _execute_via_file(self, prompt: str) -> str:
        """Fallback: write task to file for later execution."""
        task_file = os.path.join(self.workspace, "state", "_pending_task.md")
        with open(task_file, 'w') as f:
            f.write(f"# Pending Task\n\n{prompt}\n\n---\nWaiting for Hermes agent to pick up.\n")
        return "Task written to file. Waiting for Hermes agent to execute."
    
    def chat(self, message: str, max_tokens: int = None) -> str:
        """Chat via Hermes."""
        mode = self._check_hermes()
        
        if mode == "api":
            # In API mode, we can't really "chat" - just return the message
            return f"[Partner via Hermes API]: {message}"
        elif mode == "cli":
            try:
                result = subprocess.run(
                    ["hermes", "chat", "--message", message],
                    capture_output=True, text=True, timeout=300,
                    cwd=self.workspace,
                )
                return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr}"
            except subprocess.TimeoutExpired:
                return "请求超时，请稍后再试"
            except Exception as e:
                return f"Hermes chat error: {e}"
        else:
            return "Hermes is not available. Please install hermes-agent or set up the CLI."
