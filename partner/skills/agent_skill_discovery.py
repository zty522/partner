"""Agent skill discovery — simplified.

Partner no longer discovers per-skill capabilities from agent CLIs.
Agents describe their capabilities through standard protocols (MCP, OpenAPI).
This module is retained as a minimal stub for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Any

from .base_skill import Skill

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


class AgentSkillDiscovery:
    """Stub — Partner no longer performs CLI-based skill discovery.

    Agent capabilities are discovered through standard protocols (MCP, OpenAPI)
    or configured directly in the agent's own configuration.
    """

    def __init__(self, workspace: str, config: JsonDict | None = None) -> None:
        self.workspace = workspace

    def discover(self) -> list[Skill]:
        """Return an empty list — no CLI-based discovery is performed."""
        logger.info("[SKILL_DISCOVERY] CLI skill discovery disabled (agent self-describes via MCP/OpenAPI)")
        return []
