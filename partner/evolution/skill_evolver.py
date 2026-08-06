"""Skill Auto-Evolution Engine.

Implements the complete create→use→evolve closed loop for Partner skills,
per the Loop+Harness survey (Tsinghua 2026) five-path evolution framework.

Three-phase lifecycle:
  1. Creation  — extract new skills from successful execution trajectories
  2. Detection  — identify stale/unused skills for pruning
  3. Improvement — refine existing skills from failure patterns

Tracks skill health via composite scoring: usage frequency × success rate ×
recency × dependency count.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SkillHealth:
    """Quantitative health score for a skill."""

    name: str
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used: float = 0.0  # epoch seconds
    created_at: float = 0.0
    dependency_count: int = 0  # how many other skills depend on this one

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total

    @property
    def recency_score(self) -> float:
        """1.0 if used within last day, decaying to 0.0 after 30 days."""
        if self.last_used == 0:
            return 0.0
        days = (time.time() - self.last_used) / 86400
        return max(0.0, 1.0 - days / 30.0)

    @property
    def health(self) -> float:
        """Composite health score 0.0–1.0.

        Weighted: 40% recency + 35% success rate + 15% usage + 10% dependencies.
        """
        usage_norm = min(self.usage_count / 50.0, 1.0)  # 50 uses = "fully adopted"
        dep_norm = min(self.dependency_count / 5.0, 1.0)
        return (
            0.40 * self.recency_score
            + 0.35 * self.success_rate
            + 0.15 * usage_norm
            + 0.10 * dep_norm
        )


@dataclass
class TrajectorySummary:
    """Extracted summary of an execution trajectory for skill creation."""

    task_goal: str
    steps: list[str]
    tools_used: list[str]
    success: bool
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    key_insights: list[str] = field(default_factory=list)


class SkillEvolver:
    """Manages the skill auto-evolution lifecycle.

    Usage::

        evolver = SkillEvolver(skill_registry, db_path="/path/to/learning.db")
        evolver.ingest_trajectory(trajectory)  # After each task execution
        stale = evolver.detect_stale(health_threshold=0.2)
        for skill in stale:
            evolver.prune(skill)
    """

    STALE_THRESHOLD = 0.2  # Health below this → candidate for pruning
    STALE_AGE_DAYS = 30  # Unused for this long → stale regardless of health
    MIN_TRAJECTORIES_FOR_EXTRACTION = 3  # Need at least 3 successes to extract a skill

    def __init__(
        self,
        skill_registry: Any = None,
        db_path: str = "",
        *,
        workspace_root: str = "",
    ):
        self._registry = skill_registry
        self._db_path = db_path or self._resolve_db(workspace_root)
        self._trajectory_buffer: list[TrajectorySummary] = []
        self._health_cache: dict[str, SkillHealth] = {}

    # ------------------------------------------------------------------
    # Phase 1: Creation — extract skills from trajectories
    # ------------------------------------------------------------------

    def ingest_trajectory(self, trajectory: TrajectorySummary) -> None:
        """Record an execution trajectory for later skill extraction."""
        self._trajectory_buffer.append(trajectory)
        # Keep buffer bounded
        if len(self._trajectory_buffer) > 100:
            self._trajectory_buffer = self._trajectory_buffer[-100:]

    def extract_candidates(self) -> list[dict[str, Any]]:
        """Analyze buffered trajectories and propose new skills.

        Returns list of candidate skill definitions ready for review/creation.
        Only proposes skills when 3+ successful trajectories share the same pattern.
        """
        if len(self._trajectory_buffer) < self.MIN_TRAJECTORIES_FOR_EXTRACTION:
            return []

        # Group successful trajectories by tool sequence pattern
        successes = [t for t in self._trajectory_buffer if t.success]
        if len(successes) < self.MIN_TRAJECTORIES_FOR_EXTRACTION:
            return []

        # Find common tool sequences
        pattern_groups: dict[str, list[TrajectorySummary]] = {}
        for t in successes:
            key = "→".join(t.tools_used[:5])  # First 5 tools as pattern signature
            if key:
                pattern_groups.setdefault(key, []).append(t)

        candidates: list[dict[str, Any]] = []
        for pattern, trajectories in pattern_groups.items():
            if len(trajectories) < self.MIN_TRAJECTORIES_FOR_EXTRACTION:
                continue
            # Extract common goal theme
            goals = [t.task_goal for t in trajectories]
            common_goal = self._longest_common_prefix(goals)
            # Collect insights
            all_insights: list[str] = []
            for t in trajectories:
                all_insights.extend(t.key_insights)
            unique_insights = list(dict.fromkeys(all_insights))[:5]

            candidates.append({
                "proposed_name": f"skill_auto_{pattern.replace('→', '_')[:60]}",
                "pattern": pattern,
                "trajectory_count": len(trajectories),
                "common_goal": common_goal,
                "key_insights": unique_insights,
                "avg_duration": sum(t.duration_seconds for t in trajectories) / len(trajectories),
                "tools": list(dict.fromkeys(t for traj in trajectories for t in traj.tools_used)),
            })

        return candidates

    # ------------------------------------------------------------------
    # Phase 2: Detection — identify stale / unhealthy skills
    # ------------------------------------------------------------------

    def compute_health(self, skill_name: str) -> SkillHealth:
        """Compute health score for a single skill."""
        # Check cache
        if skill_name in self._health_cache:
            return self._health_cache[skill_name]

        health = SkillHealth(name=skill_name)

        if self._db_path and os.path.exists(self._db_path):
            try:
                conn = sqlite3.connect(self._db_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                # Usage from growth/evolution tables
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM growth WHERE category='skill_use' AND milestone LIKE ?",
                    (f"%{skill_name}%",),
                )
                row = cur.fetchone()
                if row:
                    health.usage_count = row["cnt"]

                # Success/failure from experiences
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM experiences WHERE skill_name=? AND outcome='success'",
                    (skill_name,),
                )
                row = cur.fetchone()
                if row:
                    health.success_count = row["cnt"]

                cur.execute(
                    "SELECT COUNT(*) as cnt FROM experiences WHERE skill_name=? AND outcome='failure'",
                    (skill_name,),
                )
                row = cur.fetchone()
                if row:
                    health.failure_count = row["cnt"]

                # Last used timestamp
                cur.execute(
                    "SELECT MAX(created_at) as ts FROM growth WHERE category='skill_use' AND milestone LIKE ?",
                    (f"%{skill_name}%",),
                )
                row = cur.fetchone()
                if row and row["ts"]:
                    try:
                        health.last_used = datetime.fromisoformat(str(row["ts"])).timestamp()
                    except (ValueError, TypeError):
                        pass

                # Created at
                cur.execute(
                    "SELECT MIN(created_at) as ts FROM growth WHERE category='skill_create' AND milestone LIKE ?",
                    (f"%{skill_name}%",),
                )
                row = cur.fetchone()
                if row and row["ts"]:
                    try:
                        health.created_at = datetime.fromisoformat(str(row["ts"])).timestamp()
                    except (ValueError, TypeError):
                        pass

                conn.close()
            except Exception as e:
                logger.warning("Failed to query learning DB for skill '%s': %s", skill_name, e)

        self._health_cache[skill_name] = health
        return health

    def detect_stale(
        self,
        health_threshold: float | None = None,
        age_days: int | None = None,
    ) -> list[SkillHealth]:
        """Identify skills that are candidates for pruning.

        A skill is stale if:
          - Health score is below threshold (default 0.2), OR
          - Not used for more than age_days (default 30)
        """
        threshold = health_threshold if health_threshold is not None else self.STALE_THRESHOLD
        max_age = age_days if age_days is not None else self.STALE_AGE_DAYS

        skill_names = self._list_skills()
        stale: list[SkillHealth] = []

        for name in skill_names:
            health = self.compute_health(name)
            is_stale = False

            if health.health < threshold:
                is_stale = True
            elif health.last_used > 0:
                days_unused = (time.time() - health.last_used) / 86400
                if days_unused > max_age:
                    is_stale = True
            elif health.created_at > 0:
                days_since_create = (time.time() - health.created_at) / 86400
                if days_since_create > max_age and health.usage_count == 0:
                    is_stale = True

            if is_stale:
                stale.append(health)

        return sorted(stale, key=lambda h: h.health)

    def detect_degraded(self, min_success_rate: float = 0.5) -> list[SkillHealth]:
        """Identify skills with degraded performance (low success rate)."""
        skill_names = self._list_skills()
        degraded: list[SkillHealth] = []
        for name in skill_names:
            health = self.compute_health(name)
            if health.usage_count >= 5 and health.success_rate < min_success_rate:
                degraded.append(health)
        return sorted(degraded, key=lambda h: h.success_rate)

    # ------------------------------------------------------------------
    # Phase 3: Improvement — refine from failure patterns
    # ------------------------------------------------------------------

    def analyze_failures(self, skill_name: str) -> list[str]:
        """Extract common failure patterns for a skill from the database."""
        patterns: list[str] = []
        if not self._db_path or not os.path.exists(self._db_path):
            return patterns

        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT error_message, COUNT(*) as cnt FROM experiences "
                "WHERE skill_name=? AND outcome='failure' "
                "GROUP BY error_message ORDER BY cnt DESC LIMIT 5",
                (skill_name,),
            )
            for row in cur.fetchall():
                patterns.append(row["error_message"] or "unknown error")
            conn.close()
        except Exception as e:
            logger.warning("Failed to analyze failures for '%s': %s", skill_name, e)

        return patterns

    def propose_fixes(self, skill_name: str) -> list[dict[str, Any]]:
        """Generate fix proposals for a degraded skill based on failure analysis."""
        failures = self.analyze_failures(skill_name)
        if not failures:
            return []

        proposals: list[dict[str, Any]] = []
        common_errors = {
            "timeout": "Add timeout parameter and retry logic",
            "not found": "Add pre-check for resource existence",
            "permission": "Add permission check before operation",
            "import": "Add dependency availability check",
            "connection": "Add connection health check and retry",
            "memory": "Add resource cleanup and batch processing",
        }

        for err in failures:
            err_lower = err.lower()
            for keyword, fix in common_errors.items():
                if keyword in err_lower:
                    proposals.append({
                        "skill": skill_name,
                        "error_pattern": err[:100],
                        "proposed_fix": fix,
                        "confidence": 0.7,
                    })

        return proposals

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    def prune(self, skill_name: str) -> bool:
        """Remove a stale skill."""
        if self._registry is None:
            logger.warning("No skill registry available for pruning '%s'", skill_name)
            return False

        try:
            if hasattr(self._registry, "remove_skill"):
                self._registry.remove_skill(skill_name)
            elif hasattr(self._registry, "unregister"):
                self._registry.unregister(skill_name)
            else:
                logger.warning("Skill registry has no remove/unregister method")
                return False

            # Clear cache
            self._health_cache.pop(skill_name, None)
            logger.info("Pruned stale skill: %s", skill_name)
            return True
        except Exception as e:
            logger.error("Failed to prune skill '%s': %s", skill_name, e)
            return False

    # ------------------------------------------------------------------
    # Full cycle
    # ------------------------------------------------------------------

    def run_cycle(self) -> dict[str, Any]:
        """Run a complete skill evolution cycle.

        Returns a summary of actions taken.
        """
        result: dict[str, Any] = {
            "candidates_extracted": 0,
            "stale_detected": 0,
            "stale_pruned": 0,
            "degraded_detected": 0,
            "fixes_proposed": 0,
        }

        # 1. Extract new skill candidates from trajectories
        candidates = self.extract_candidates()
        result["candidates_extracted"] = len(candidates)
        result["candidates"] = candidates[:5]  # Top 5

        # 2. Detect stale skills
        stale = self.detect_stale()
        result["stale_detected"] = len(stale)
        result["stale_skills"] = [s.name for s in stale]

        # 3. Prune deeply stale (health < 0.1 AND old)
        for s in stale:
            if s.health < 0.1:
                if self.prune(s.name):
                    result["stale_pruned"] += 1

        # 4. Detect degraded skills
        degraded = self.detect_degraded()
        result["degraded_detected"] = len(degraded)
        result["degraded_skills"] = [s.name for s in degraded]

        # 5. Propose fixes
        for s in degraded[:3]:
            fixes = self.propose_fixes(s.name)
            if fixes:
                result.setdefault("fix_proposals", []).extend(fixes)
                result["fixes_proposed"] += len(fixes)

        logger.info(
            "Skill evolution cycle complete: %d candidates, %d stale (%d pruned), %d degraded",
            result["candidates_extracted"],
            result["stale_detected"],
            result["stale_pruned"],
            result["degraded_detected"],
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _list_skills(self) -> list[str]:
        if self._registry is None:
            return []
        try:
            return list(self._registry.list_skills())
        except AttributeError:
            try:
                return list(self._registry._skills.keys())
            except AttributeError:
                return []

    @staticmethod
    def _resolve_db(workspace_root: str) -> str:
        """Resolve learning.db path from workspace root."""
        if not workspace_root:
            return ""
        # Try PARTNER_DATA_DIR first
        data_dir = os.environ.get("PARTNER_DATA_DIR", "")
        if data_dir and os.path.isdir(data_dir):
            return os.path.join(data_dir, "learning.db")
        # Fallback: workspace_root/partner_data/
        candidate = os.path.join(workspace_root, "partner_data", "learning.db")
        if os.path.exists(candidate):
            return candidate
        # Last resort: workspace_root parent/partner_data/
        parent = os.path.join(os.path.dirname(workspace_root), "partner_data", "learning.db")
        if os.path.exists(parent):
            return parent
        return candidate

    @staticmethod
    def _longest_common_prefix(strings: list[str]) -> str:
        """Extract the longest common prefix from a list of goal strings."""
        if not strings:
            return ""
        # Use first string as base
        base = strings[0]
        for s in strings[1:]:
            while not s.startswith(base) and base:
                base = base[:-1]
        return base.strip()
