"""Rule type definitions for Partner's Rules layer.

Per the "AI立规矩" framework, rules come in four categories:

  Constraint  — MUST / MUST NOT (hard boundaries, gated by permissions)
  Preference  — SHOULD / PREFER (soft guidance, can be overridden)
  Convention  — BY DEFAULT (team/domain conventions, the "right way")
  Prohibition — NEVER (absolute bans, enforced at harness level)

Each rule has a scope (global/project/personal) defining where it applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RuleCategory(Enum):
    CONSTRAINT = "constraint"
    PREFERENCE = "preference"
    CONVENTION = "convention"
    PROHIBITION = "prohibition"


class RuleScope(Enum):
    GLOBAL = "global"  # Applies to all Partner instances
    PROJECT = "project"  # Applies to a specific project
    PERSONAL = "personal"  # Applies to the user's style/preferences


@dataclass
class Rule:
    """A single rule entry."""

    id: str
    category: RuleCategory
    scope: RuleScope
    statement: str  # Human-readable rule text
    applies_to: list[str] = field(default_factory=list)  # Tools/modules affected
    priority: int = 5  # 1=highest, 10=lowest
    enabled: bool = True
    source: str = ""  # Where this rule came from (file path or "auto-generated")
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_injection(self) -> str:
        """Format this rule as a single line for prompt injection."""
        cat_marker = {
            RuleCategory.CONSTRAINT: "[MUST]",
            RuleCategory.PREFERENCE: "[SHOULD]",
            RuleCategory.CONVENTION: "[BY DEFAULT]",
            RuleCategory.PROHIBITION: "[NEVER]",
        }[self.category]
        return f"{cat_marker} {self.statement}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category.value,
            "scope": self.scope.value,
            "statement": self.statement,
            "applies_to": self.applies_to,
            "priority": self.priority,
            "enabled": self.enabled,
            "source": self.source,
        }
