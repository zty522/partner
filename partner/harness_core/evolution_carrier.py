"""Evolution Carrier — fast-path vs slow-path separation.

Per the Loop+Harness survey (Tsinghua 2026), agent evolution operates on two tracks:

  Fast Path (Harness-level):
    - Update skills, memory, prompts, configs
    - Cheap, fast, reversible — seconds to minutes
    - Applied every evolution cycle

  Slow Path (Model-level):
    - Fine-tune model weights from accumulated experience
    - Expensive, slow, near-irreversible — days, millions of dollars
    - Applied only when harness changes saturate

This module formalises the split so Partner can make explicit decisions
about which path to use for each improvement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EvolutionPath(Enum):
    FAST = "fast"  # Harness-level: skills, memory, prompts, configs
    SLOW = "slow"  # Model-level: fine-tuning from experience


class ImprovementTarget(Enum):
    SKILL = "skill"
    MEMORY = "memory"
    PROMPT = "prompt"
    CONFIG = "config"
    RULE = "rule"
    MODEL = "model"


@dataclass
class EvolutionDecision:
    """Result of deciding which path to take for an improvement."""

    target: ImprovementTarget
    path: EvolutionPath
    reason: str
    estimated_cost: str = ""  # human-readable cost estimate
    reversible: bool = True


@dataclass
class FastPathStats:
    """Track fast-path evolution activity."""

    skills_created: int = 0
    skills_updated: int = 0
    skills_pruned: int = 0
    memories_written: int = 0
    memories_pruned: int = 0
    prompts_updated: int = 0
    configs_updated: int = 0
    rules_updated: int = 0

    def total(self) -> int:
        return (
            self.skills_created
            + self.skills_updated
            + self.skills_pruned
            + self.memories_written
            + self.memories_pruned
            + self.prompts_updated
            + self.configs_updated
            + self.rules_updated
        )


class EvolutionCarrier:
    """Manages fast/slow path decisions for Partner evolution.

    Rules of thumb:
      - Skills, memory, prompts, configs → always fast path
      - Model fine-tuning → slow path, gated by saturation check
      - Fast path is the default — only escalate to slow when harness
        changes stop yielding measurable improvement.
    """

    # Targets that are always fast-path
    FAST_PATH_TARGETS = frozenset({
        ImprovementTarget.SKILL,
        ImprovementTarget.MEMORY,
        ImprovementTarget.PROMPT,
        ImprovementTarget.CONFIG,
        ImprovementTarget.RULE,
    })

    def __init__(self):
        self._fast_stats = FastPathStats()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decide(self, target: ImprovementTarget, context: dict[str, Any] | None = None) -> EvolutionDecision:
        """Decide fast vs slow path for a given improvement target."""
        if target in self.FAST_PATH_TARGETS:
            return EvolutionDecision(
                target=target,
                path=EvolutionPath.FAST,
                reason=f"{target.value} changes are always harness-level (fast path)",
                estimated_cost="seconds to minutes, CPU only",
                reversible=True,
            )
        # Model changes
        return EvolutionDecision(
            target=target,
            path=EvolutionPath.SLOW,
            reason="Model weight changes require GPU fine-tuning (slow path)",
            estimated_cost="hours to days, GPU required, $10-$10K",
            reversible=False,
        )

    def should_escalate_to_slow(self, stagnation_cycles: int = 5) -> bool:
        """Check if fast-path evolution has saturated and escalation is warranted.

        Escalation criteria:
          - Fast path has been active for at least `stagnation_cycles`
          - Recent fast-path changes show diminishing returns
        """
        if self._fast_stats.total() < 10:
            return False
        if stagnation_cycles < 5:
            return False
        logger.info(
            "Fast-path evolution saturated after %d cycles (%d total changes) — "
            "consider slow-path escalation",
            stagnation_cycles,
            self._fast_stats.total(),
        )
        return True

    def record_fast_path(self, target: ImprovementTarget) -> None:
        """Record a fast-path change for tracking."""
        if target == ImprovementTarget.SKILL:
            self._fast_stats.skills_updated += 1
        elif target == ImprovementTarget.MEMORY:
            self._fast_stats.memories_written += 1
        elif target == ImprovementTarget.PROMPT:
            self._fast_stats.prompts_updated += 1
        elif target == ImprovementTarget.CONFIG:
            self._fast_stats.configs_updated += 1
        elif target == ImprovementTarget.RULE:
            self._fast_stats.rules_updated += 1

    @property
    def stats(self) -> FastPathStats:
        return self._fast_stats

    def stats_dict(self) -> dict[str, Any]:
        s = self._fast_stats
        return {
            "skills_created": s.skills_created,
            "skills_updated": s.skills_updated,
            "skills_pruned": s.skills_pruned,
            "memories_written": s.memories_written,
            "memories_pruned": s.memories_pruned,
            "prompts_updated": s.prompts_updated,
            "configs_updated": s.configs_updated,
            "rules_updated": s.rules_updated,
            "total": s.total(),
        }

    def reset(self) -> None:
        self._fast_stats = FastPathStats()
