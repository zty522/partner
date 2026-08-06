"""
RL-Enhanced Self-Evolution Engine.
Inspired by AgentEvolver (https://modelscope.github.io/AgentEvolver/).

Core idea: Treat Partner's own dialog history and run logs as a replay buffer
for reinforcement learning. Each task completion is a (state, action, reward)
tuple that feeds back into the planning system.

Architecture:
  ExperienceReplayBuffer — collects (state, action, reward) from learning.db
  RewardCalculator      — scores task outcomes across multiple dimensions
  PolicyOptimizer       — uses experience data to suggest planning improvements
  RLFeedbackLoop        — orchestrates the full RL cycle

The buffer grows continuously as Partner runs more tasks. The optimizer
periodically analyzes accumulated experience to improve future decisions.
"""

from __future__ import annotations

import json, logging, os, sqlite3, time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Data types ──

@dataclass
class Experience:
    """One (state, action, reward) tuple from a completed task."""
    task_id: str
    task_text: str          # user's original request (state)
    planned_steps: list[str]  # actions taken
    output_type: str        # text/file/table/chart/pdf
    file_format: str        # md/pdf/csv/png
    success: bool
    elapsed_seconds: float
    skills_used: list[str]
    agent_used: str
    user_feedback: Optional[str] = None
    created_at: str = ""

    @property
    def reward(self) -> float:
        """Composite reward score (-1 to 1)."""
        base = 1.0 if self.success else -0.5
        # Efficiency bonus: faster is better (capped)
        if self.elapsed_seconds > 0:
            efficiency = max(0, 1.0 - self.elapsed_seconds / 3600)  # 1h baseline
            base += efficiency * 0.3
        # Rich output bonus: file/table > plain text
        if self.output_type in ("file", "table", "chart", "pdf"):
            base += 0.1
        # User feedback bonus
        if self.user_feedback and any(w in (self.user_feedback or "").lower()
                                       for w in ["好", "great", "谢谢", "good"]):
            base += 0.2
        return max(-1.0, min(1.0, base))


@dataclass
class PolicySuggestion:
    """A suggested planning improvement based on experience."""
    rule: str           # e.g., "prefer tool X for task type Y"
    confidence: float   # 0-1
    evidence_count: int # how many experiences support this
    source_tasks: list[str]  # task IDs that generated this insight


# ── Experience Replay Buffer ──

class ExperienceReplayBuffer:
    """Collect experiences from learning.db and Partner's run logs."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self._buffer: list[Experience] = []
        self._loaded = False

    def load_from_db(self, db_path: str | None = None) -> int:
        """Load all experiences from the global learning.db."""
        if db_path is None:
            from ..meta.learning import GLOBAL_DB_PATH
            db_path = GLOBAL_DB_PATH

        if not os.path.exists(db_path):
            logger.warning("[RL] learning.db not found at %s", db_path)
            return 0

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM experiences ORDER BY created_at DESC LIMIT 10000"
            ).fetchall()
            for row in rows:
                exp = Experience(
                    task_id=str(row["id"]),
                    task_text=row["user_message"] or "",
                    planned_steps=[],
                    output_type=row["output_type"] or "text",
                    file_format=row["file_format"] or "",
                    success=bool(row["success"]),
                    elapsed_seconds=0,
                    skills_used=json.loads(row["skills_used"] or "[]"),
                    agent_used=row["agent_used"] or "",
                    created_at=row["created_at"] or "",
                )
                self._buffer.append(exp)
        finally:
            conn.close()

        self._loaded = True
        logger.info("[RL] loaded %d experiences from learning.db", len(self._buffer))
        return len(self._buffer)

    def add_from_task(self, task_instance, success: bool, elapsed: float):
        """Add a new experience from a just-completed task."""
        if not hasattr(task_instance, 'original_message'):
            return
        exp = Experience(
            task_id=str(getattr(task_instance, 'id', '')),
            task_text=str(getattr(task_instance, 'original_message', '')),
            planned_steps=[],
            output_type="text",
            file_format="",
            success=success,
            elapsed_seconds=elapsed,
            skills_used=[],
            agent_used="",
            created_at=datetime.now().isoformat(),
        )
        self._buffer.append(exp)
        logger.debug("[RL] added experience: %s (success=%s)", exp.task_id[:12], success)

    def sample(self, n: int = 100, recent_first: bool = True) -> list[Experience]:
        """Sample experiences from the buffer."""
        if not self._loaded:
            self.load_from_db()
        buf = list(self._buffer)
        if recent_first:
            buf.sort(key=lambda e: e.created_at, reverse=True)
        return buf[:n]

    def stats(self) -> dict:
        if not self._loaded:
            self.load_from_db()
        total = len(self._buffer)
        succeeded = sum(1 for e in self._buffer if e.success)
        failed = total - succeeded
        output_types = defaultdict(int)
        for e in self._buffer:
            output_types[e.output_type] += 1
        avg_reward = sum(e.reward for e in self._buffer) / max(total, 1)
        return {
            "total": total, "succeeded": succeeded, "failed": failed,
            "success_rate": succeeded / max(total, 1),
            "output_types": dict(output_types),
            "avg_reward": round(avg_reward, 3),
        }

    def format_for_prompt(self, max_examples: int = 10) -> str:
        """Format recent experiences as prompt context for the planner."""
        samples = self.sample(n=max_examples, recent_first=True)
        if not samples:
            return ""

        lines = ["## RL Experience Replay (from past tasks)", ""]
        lines.append(f"Total: {len(self._buffer)} tasks, "
                     f"Success rate: {self.stats()['success_rate']:.0%}")
        lines.append("")

        # Show best and worst examples
        succeeded = [e for e in samples if e.success][:3]
        failed = [e for e in samples if not e.success][:3]

        if succeeded:
            lines.append("### ✅ Recent Successes (to replicate)")
            for e in succeeded:
                lines.append(f"- {e.task_text[:80]}...")
            lines.append("")

        if failed:
            lines.append("### ❌ Recent Failures (to avoid)")
            for e in failed:
                lines.append(f"- {e.task_text[:80]}...")
            lines.append("")

        lines.append(f"Average reward: {self.stats()['avg_reward']:.3f}")
        return '\n'.join(lines)


# ── Reward Calculator ──

class RewardCalculator:
    """Calculate composite reward scores for task outcomes."""

    @staticmethod
    def calculate(experience: Experience) -> float:
        return experience.reward

    @staticmethod
    def batch_stats(experiences: list[Experience]) -> dict:
        """Compute aggregate statistics over a batch of experiences."""
        if not experiences:
            return {"count": 0, "avg_reward": 0, "success_rate": 0}
        rewards = [e.reward for e in experiences]
        return {
            "count": len(experiences),
            "avg_reward": sum(rewards) / len(rewards),
            "max_reward": max(rewards),
            "min_reward": min(rewards),
            "success_rate": sum(1 for e in experiences if e.success) / len(experiences),
        }


# ── Policy Optimizer ──

class PolicyOptimizer:
    """Analyze experience buffer to extract policy improvement suggestions."""

    def __init__(self, buffer: ExperienceReplayBuffer):
        self.buffer = buffer

    def extract_patterns(self) -> list[PolicySuggestion]:
        """Analyze experiences and extract actionable patterns."""
        experiences = self.buffer.sample(n=500)
        if len(experiences) < 10:
            return []

        suggestions = []

        # Pattern 1: Output type success rates
        output_stats = defaultdict(lambda: {"success": 0, "total": 0})
        for e in experiences:
            ot = e.output_type
            output_stats[ot]["total"] += 1
            if e.success:
                output_stats[ot]["success"] += 1

        for ot, stats in output_stats.items():
            if stats["total"] >= 3:
                rate = stats["success"] / stats["total"]
                if rate >= 0.8:
                    suggestions.append(PolicySuggestion(
                        rule=f"Output type '{ot}' has {rate:.0%} success rate — prefer this format",
                        confidence=rate,
                        evidence_count=stats["total"],
                        source_tasks=[],
                    ))
                elif rate <= 0.3 and stats["total"] >= 3:
                    suggestions.append(PolicySuggestion(
                        rule=f"Output type '{ot}' has low {rate:.0%} success — avoid or improve",
                        confidence=1.0 - rate,
                        evidence_count=stats["total"],
                        source_tasks=[],
                    ))

        # Pattern 2: Success rate by task category
        task_categories = {
            "molecule": ["分子", "pocketflow", "生成", "docking"],
            "trajectory": ["轨迹", "单细胞", "paga", "dpt", "rna"],
            "literature": ["文献", "paper", "检索", "review"],
            "code": ["代码", "script", "编写", "python"],
            "data": ["数据", "分析", "统计", "图表"],
        }
        cat_stats = defaultdict(lambda: {"success": 0, "total": 0, "rewards": []})
        for e in experiences:
            matched = "general"
            for cat, keywords in task_categories.items():
                if any(kw in e.task_text.lower() for kw in keywords):
                    matched = cat
                    break
            cat_stats[matched]["total"] += 1
            if e.success:
                cat_stats[matched]["success"] += 1
            cat_stats[matched]["rewards"].append(e.reward)

        for cat, stats in cat_stats.items():
            if stats["total"] >= 5:
                rate = stats["success"] / stats["total"]
                avg_r = sum(stats["rewards"]) / len(stats["rewards"])
                suggestions.append(PolicySuggestion(
                    rule=f"Category '{cat}': {rate:.0%} success, avg reward {avg_r:.2f} ({stats['total']} tasks)",
                    confidence=min(rate, 0.9),
                    evidence_count=stats["total"],
                    source_tasks=[],
                ))

        # Pattern 3: Recent trend (last 50 vs all)
        recent = experiences[:50]
        older = experiences[50:100] if len(experiences) > 50 else []
        if recent and older:
            recent_rate = sum(1 for e in recent if e.success) / len(recent)
            older_rate = sum(1 for e in older if e.success) / len(older) if older else 0
            if recent_rate > older_rate + 0.1:
                suggestions.append(PolicySuggestion(
                    rule=f"Improving trend: recent success {recent_rate:.0%} vs older {older_rate:.0%} — current approach is working",
                    confidence=0.8,
                    evidence_count=len(recent),
                    source_tasks=[],
                ))
            elif recent_rate < older_rate - 0.1:
                suggestions.append(PolicySuggestion(
                    rule=f"Declining trend: recent success {recent_rate:.0%} vs older {older_rate:.0%} — may need strategy adjustment",
                    confidence=0.8,
                    evidence_count=len(recent),
                    source_tasks=[],
                ))

        logger.info("[RL] extracted %d policy suggestions from %d experiences",
                     len(suggestions), len(experiences))
        return suggestions

    def format_suggestions(self, suggestions: list[PolicySuggestion]) -> str:
        """Format policy suggestions for planner prompt."""
        if not suggestions:
            return ""
        lines = ["## RL Policy Suggestions (from experience)", ""]
        for i, s in enumerate(suggestions[:8], 1):
            lines.append(f"{i}. [{s.confidence:.0%} confidence, {s.evidence_count} samples] {s.rule}")
        lines.append("")
        lines.append("Consider these patterns when planning new tasks.")
        return '\n'.join(lines)


# ── RL Feedback Loop ──

class RLFeedbackLoop:
    """Orchestrates the full RL cycle: collect → analyze → suggest → apply."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.buffer = ExperienceReplayBuffer(workspace)
        self.optimizer = PolicyOptimizer(self.buffer)
        self._last_analysis_time = 0
        self._analysis_interval = 300  # re-analyze every 5 minutes

    def record_task(self, task_instance, success: bool, elapsed: float):
        """Record a completed task into the experience buffer."""
        self.buffer.add_from_task(task_instance, success, elapsed)

    def get_suggestions(self, force: bool = False) -> list[PolicySuggestion]:
        """Get current policy suggestions, re-analyzing if needed."""
        now = time.time()
        if force or (now - self._last_analysis_time) > self._analysis_interval:
            self._last_analysis_time = now
            return self.optimizer.extract_patterns()
        return []

    def format_context(self) -> str:
        """Format full RL context for planner prompt."""
        parts = []
        exp_text = self.buffer.format_for_prompt(max_examples=8)
        if exp_text:
            parts.append(exp_text)
        suggestions = self.get_suggestions()
        if suggestions:
            parts.append(self.optimizer.format_suggestions(suggestions))
        return '\n'.join(parts)

    def stats(self) -> dict:
        return self.buffer.stats()


# ── Singleton ──

_rl_loop: RLFeedbackLoop | None = None

def get_rl_loop(workspace: str) -> RLFeedbackLoop:
    global _rl_loop
    if _rl_loop is None:
        _rl_loop = RLFeedbackLoop(workspace)
        _rl_loop.buffer.load_from_db()
    return _rl_loop
