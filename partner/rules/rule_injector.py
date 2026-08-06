"""Rule Injector — inject active rules into Planner prompts.

This module integrates the Rules layer into Partner's planning pipeline.
When the BatchPlanner generates a plan, rules are injected as a structured
block in the system prompt, giving the LLM awareness of constraints,
preferences, conventions, and prohibitions.

Integration points:
  1. batch_planner.py: _build_system_prompt() calls inject_rules_block()
  2. executor.py: _run_micro_plan() can check prohibitions before execution
  3. interaction_orchestrator.py: classify methods can use rules for routing
"""

from __future__ import annotations

import logging
from typing import Any

from .rule_loader import RuleLoader
from . import RuleCategory

logger = logging.getLogger(__name__)

# Tokens to reserve for rules in the planner prompt
MAX_RULES_CHARS = 2000


class RuleInjector:
    """Injects active rules into various Partner subsystems."""

    def __init__(self, loader: RuleLoader | None = None):
        self._loader = loader or RuleLoader()

    # ------------------------------------------------------------------
    # Prompt injection
    # ------------------------------------------------------------------

    def inject_rules_block(self, goal: str = "", max_chars: int = MAX_RULES_CHARS) -> str:
        """Build a compact rules block for the planner system prompt.

        Rules are filtered by relevance to the goal (keyword overlap)
        and formatted as a structured block.
        """
        rules = self._loader.load_all()
        if not rules:
            return ""

        # Sort: prohibitions first (most important), then constraints, then
        # preferences, then conventions
        order = {
            RuleCategory.PROHIBITION: 0,
            RuleCategory.CONSTRAINT: 1,
            RuleCategory.PREFERENCE: 2,
            RuleCategory.CONVENTION: 3,
        }
        rules.sort(key=lambda r: (order.get(r.category, 9), r.priority))

        # Relevance filter: if goal is provided, boost rules whose keywords
        # overlap with the goal
        if goal:
            goal_lower = goal.lower()
            rules.sort(key=lambda r: (
                order.get(r.category, 9),
                -(sum(1 for kw in r.statement.lower().split() if kw in goal_lower)),
                r.priority,
            ))

        # Build block
        blocks: list[str] = ["<rules>"]
        total = 0

        for r in rules:
            line = f"- {r.to_injection()}"
            if total + len(line) > max_chars:
                remaining = len(rules) - rules.index(r)
                if remaining > 0:
                    blocks.append(f"- ... ({remaining} more rules omitted)")
                break
            blocks.append(line)
            total += len(line)

        blocks.append("</rules>")
        return "\n".join(blocks)

    def inject_constraints_only(self, max_chars: int = 1000) -> str:
        """Inject only MUST/MUST-NOT constraints (for safety-critical contexts)."""
        rules = [
            r for r in self._loader.load_all()
            if r.category in (RuleCategory.CONSTRAINT, RuleCategory.PROHIBITION)
        ]
        if not rules:
            return ""

        blocks = ["<constraints>"]
        total = 0
        for r in sorted(rules, key=lambda r: r.priority):
            line = f"- {r.to_injection()}"
            if total + len(line) > max_chars:
                break
            blocks.append(line)
            total += len(line)
        blocks.append("</constraints>")
        return "\n".join(blocks)

    # ------------------------------------------------------------------
    # Execution guard
    # ------------------------------------------------------------------

    def check_prohibitions(
        self, action: str, params: dict[str, Any] | None = None
    ) -> tuple[bool, str]:
        """Check if an action violates any prohibition rules.

        Returns (allowed, reason). If allowed=False, the action should be blocked.
        """
        for r in self._loader.load_by_category(RuleCategory.PROHIBITION):
            # Check if this prohibition applies — use params for tool context
            if r.applies_to:
                tool = (params or {}).get("tool", "")
                module = (params or {}).get("module", "")
                # If we have tool context and it doesn't match, skip
                if (tool or module) and tool not in r.applies_to and module not in r.applies_to:
                    continue
            # Keyword match: extract alphanumeric tokens from statement
            statement_lower = r.statement.lower()
            action_lower = action.lower()
            import re as _re
            stmt_words = _re.findall(r"[a-zA-Z0-9_-]+", statement_lower)
            if any(kw in action_lower for kw in stmt_words[:10]):
                return False, f"Blocked by rule: {r.statement}"
        return True, ""

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return self._loader.count()
