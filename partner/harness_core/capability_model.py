"""Environment Capability Model for Partner Harness.

Quantifies Partner's environment capability across three dimensions
(per Loop+Harness survey, Tsinghua 2026):

  - Action Diversity: how many distinct operations Partner can perform
  - Feedback Density: how frequently/reliably the environment provides
    attribution signals after each action
  - Task Horizon: maximum supported task duration, whether intermediate
    errors are recoverable

These three dimensions set Partner's evolution ceiling — the richer the
environment, the higher-quality the experiences Partner can collect.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CapabilityScore:
    """Three-axis capability measurement."""

    action_diversity: float  # 0.0–1.0  (number of distinct event types / reference max)
    feedback_density: float  # 0.0–1.0  (fraction of actions that produce verifiable output)
    task_horizon: float  # 0.0–1.0  (normalised max task duration support)

    # Per-axis breakdowns
    action_breakdown: dict[str, int] = field(default_factory=dict)
    feedback_sources: list[str] = field(default_factory=list)
    max_task_duration_seconds: float = 0.0
    error_recovery_supported: bool = False

    @property
    def overall(self) -> float:
        """Harmonic mean — penalises zero-value axes."""
        vals = [self.action_diversity, self.feedback_density, self.task_horizon]
        if any(v == 0 for v in vals):
            return 0.0
        return 3.0 / (1 / self.action_diversity + 1 / self.feedback_density + 1 / self.task_horizon)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_diversity": round(self.action_diversity, 3),
            "feedback_density": round(self.feedback_density, 3),
            "task_horizon": round(self.task_horizon, 3),
            "overall": round(self.overall, 3),
            "action_breakdown": self.action_breakdown,
            "feedback_sources": self.feedback_sources,
            "max_task_duration_seconds": self.max_task_duration_seconds,
            "error_recovery_supported": self.error_recovery_supported,
        }


class CapabilityModel:
    """Measures Partner's environment capability ceiling.

    Usage::

        model = CapabilityModel(event_registry, workspace_root="/mnt/e/work/partner_workspace")
        score = model.measure()
        print(f"Environment ceiling: {score.overall:.2f}")
    """

    # Reference maximums for normalisation
    REF_ACTION_COUNT = 100  # a well-equipped agent should have ~100 event types
    REF_MAX_TASK_SECONDS = 3600  # 1 hour is "full horizon"
    REF_TASK_SECONDS_PER_POINT = 300  # 5 min increments

    def __init__(
        self,
        event_registry: Any = None,
        workspace_root: str = "",
        *,
        known_tools: list[str] | None = None,
    ):
        self._registry = event_registry
        self._workspace_root = workspace_root
        self._known_tools = known_tools or []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def measure(self) -> CapabilityScore:
        """Run a full capability measurement and return a score."""
        return CapabilityScore(
            action_diversity=self._measure_action_diversity(),
            feedback_density=self._measure_feedback_density(),
            task_horizon=self._measure_task_horizon(),
            action_breakdown=self._count_action_categories(),
            feedback_sources=self._list_feedback_sources(),
            max_task_duration_seconds=self._max_task_duration(),
            error_recovery_supported=self._check_error_recovery(),
        )

    def compare(self, other: CapabilityScore) -> dict[str, Any]:
        """Compare self score against another (e.g. external system)."""
        mine = self.measure()
        return {
            "self": mine.to_dict(),
            "other": other.to_dict(),
            "gaps": {
                "action_diversity": round(other.action_diversity - mine.action_diversity, 3),
                "feedback_density": round(other.feedback_density - mine.feedback_density, 3),
                "task_horizon": round(other.task_horizon - mine.task_horizon, 3),
            },
        }

    def recommendations(self, score: CapabilityScore | None = None) -> list[str]:
        """Generate improvement recommendations based on weakest axis."""
        if score is None:
            score = self.measure()
        recs: list[str] = []
        if score.action_diversity < 0.3:
            recs.append("Low action diversity — consider adding more v2 event types or external tool manifests.")
        if score.feedback_density < 0.3:
            recs.append("Low feedback density — add output validation checks or artifact verification after each step.")
        if score.task_horizon < 0.3:
            recs.append("Short task horizon — add checkpoint/resume support for long-running tasks.")
        return recs

    # ------------------------------------------------------------------
    # Internal measurement helpers
    # ------------------------------------------------------------------

    def _measure_action_diversity(self) -> float:
        """Count distinct event types vs reference maximum."""
        count = self._count_event_types()
        return min(count / self.REF_ACTION_COUNT, 1.0)

    def _measure_feedback_density(self) -> float:
        """Estimate fraction of actions that produce verifiable output."""
        sources = self._list_feedback_sources()
        # Each feedback source contributes a small increment
        return min(len(sources) * 0.15, 1.0)

    def _measure_task_horizon(self) -> float:
        """Normalise max task duration support."""
        max_sec = self._max_task_duration()
        return min(max_sec / self.REF_MAX_TASK_SECONDS, 1.0)

    def _count_event_types(self) -> int:
        if self._registry is None:
            return len(self._known_tools)
        try:
            return len(self._registry.list_events())
        except AttributeError:
            # Try _events dict
            try:
                return len(self._registry._events)
            except AttributeError:
                return len(self._known_tools)

    def _count_action_categories(self) -> dict[str, int]:
        """Group event types by category."""
        categories: dict[str, int] = {}
        if self._registry is None:
            return categories
        try:
            events = self._registry.list_events()
        except AttributeError:
            try:
                events = list(self._registry._events.keys())
            except AttributeError:
                return categories
        for name in events:
            cat = name.split("_")[0] if "_" in name else "other"
            categories[cat] = categories.get(cat, 0) + 1
        return categories

    def _list_feedback_sources(self) -> list[str]:
        """Identify available feedback channels."""
        sources: list[str] = []
        # Check for artifact validator
        try:
            from .artifact_validator import ArtifactValidator
            sources.append("artifact_validator")
        except ImportError:
            pass
        # Check for sandbox
        if os.path.isdir(os.path.join(self._workspace_root, "instances")):
            sources.append("instance_logs")
        # Check for benchmark
        try:
            import importlib
            importlib.import_module("partner.benchmark")
            sources.append("benchmark_scorer")
        except ImportError:
            pass
        # Check for test frameworks
        for tool in self._known_tools:
            if any(t in tool.lower() for t in ("test", "validate", "check", "lint")):
                sources.append(f"tool:{tool}")
        return sources

    def _max_task_duration(self) -> float:
        """Return maximum supported task duration in seconds."""
        try:
            from .robust_executor import load_harness_config
            cfg = load_harness_config(self._workspace_root)
            timeout = cfg.get("global_timeout", 600)
            stale_ttl = cfg.get("stale_task_ttl_seconds", 3600)
            return float(max(timeout, stale_ttl))
        except Exception:
            return 600.0

    def _check_error_recovery(self) -> bool:
        """Check whether error recovery mechanisms exist."""
        try:
            from .remediation_handler import RemediationHandler
            return True
        except ImportError:
            return False
