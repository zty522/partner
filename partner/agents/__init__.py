"""Partner Agent Interface — standardized agent registry, dispatcher, and manifest."""

from .registry import AgentRegistry
from .dispatcher import AgentDispatcher, AgentTask, AgentResult
from .manifest import AgentManifest

__all__ = ["AgentRegistry", "AgentDispatcher", "AgentTask", "AgentResult", "AgentManifest"]
