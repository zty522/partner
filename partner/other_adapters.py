"""Adapters for other open-source agent frameworks.

Supports: AutoGPT, OpenHands, CrewAI, gptme
"""

import json
import os
import subprocess
from typing import List

from .adapter import AgentAdapter, SearchResult


class AutoGPTAdapter(AgentAdapter):
    """Adapter for AutoGPT."""
    
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
    
    def name(self) -> str:
        return "autogpt"
    
    def is_available(self) -> bool:
        try:
            result = subprocess.run(["which", "autogpt"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                return True
        except:
            pass
        # Check Docker
        try:
            result = subprocess.run(["docker", "ps", "--filter", "name=autogpt"], capture_output=True, text=True, timeout=5)
            return "autogpt" in result.stdout
        except:
            return False
    
    def search_web(self, query: str) -> List[SearchResult]:
        return [SearchResult(title="AutoGPT search", url="", snippet=self.execute_task(f"Search: {query}"))]
    
    def execute_task(self, prompt: str) -> str:
        try:
            result = subprocess.run(
                ["autogpt", "--prompt", prompt],
                capture_output=True, text=True, timeout=300,
                cwd=self.workspace,
            )
            return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr[:500]}"
        except Exception as e:
            return f"AutoGPT error: {e}"
    
    def chat(self, message: str) -> str:
        return self.execute_task(message)


class OpenHandsAdapter(AgentAdapter):
    """Adapter for OpenHands (formerly Devin OSS)."""
    
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
    
    def name(self) -> str:
        return "openhands"
    
    def is_available(self) -> bool:
        try:
            result = subprocess.run(["docker", "ps", "--filter", "name=openhands"], capture_output=True, text=True, timeout=5)
            return "openhands" in result.stdout
        except:
            return False
    
    def search_web(self, query: str) -> List[SearchResult]:
        return [SearchResult(title="OpenHands search", url="", snippet=self.execute_task(f"Search the web: {query}"))]
    
    def execute_task(self, prompt: str) -> str:
        # OpenHands runs as a web service, interact via API
        try:
            import urllib.request
            data = json.dumps({"action": "run", "command": prompt}).encode()
            req = urllib.request.Request(
                "http://localhost:3000/api/agent/run",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read())
                return result.get("output", str(result))
        except Exception as e:
            return f"OpenHands error: {e}"
    
    def chat(self, message: str) -> str:
        return self.execute_task(message)


class CrewAIAdapter(AgentAdapter):
    """Adapter for CrewAI multi-agent framework."""
    
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
    
    def name(self) -> str:
        return "crewai"
    
    def is_available(self) -> bool:
        try:
            import crewai
            return True
        except ImportError:
            return False
    
    def search_web(self, query: str) -> List[SearchResult]:
        return [SearchResult(title="CrewAI search", url="", snippet=self.execute_task(f"Research: {query}"))]
    
    def execute_task(self, prompt: str) -> str:
        try:
            import crewai
            
            # Create a simple crew for the task
            agent = crewai.Agent(
                role="Research Assistant",
                goal="Complete research tasks thoroughly",
                backstory="You are an AI research companion.",
                verbose=False,
            )
            
            task = crewai.Task(
                description=prompt,
                expected_output="Detailed research findings",
                agent=agent,
            )
            
            crew = crewai.Crew(agents=[agent], tasks=[task], verbose=False)
            result = crew.kickoff()
            return str(result)
        except Exception as e:
            return f"CrewAI error: {e}"
    
    def chat(self, message: str) -> str:
        return self.execute_task(message)


class GptmeAdapter(AgentAdapter):
    """Adapter for gptme (terminal-based agent)."""
    
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
    
    def name(self) -> str:
        return "gptme"
    
    def is_available(self) -> bool:
        try:
            result = subprocess.run(["which", "gptme"], capture_output=True, text=True, timeout=3)
            return result.returncode == 0
        except:
            return False
    
    def search_web(self, query: str) -> List[SearchResult]:
        return [SearchResult(title="gptme search", url="", snippet=self.execute_task(f"Search: {query}"))]
    
    def execute_task(self, prompt: str) -> str:
        try:
            result = subprocess.run(
                ["gptme", "--non-interactive", prompt],
                capture_output=True, text=True, timeout=120,
                cwd=self.workspace,
            )
            return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr[:500]}"
        except Exception as e:
            return f"gptme error: {e}"
    
    def chat(self, message: str) -> str:
        return self.execute_task(message)
