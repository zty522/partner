"""
Active Monitor — proactive self-evolution tracking (Sprint 7).

Replaces passive "wait for failure → react" with:
1. Periodic health checks (every 5 min)
2. Execution trace recording
3. Trend analysis (success rate, common failures)
4. Auto-optimization suggestions

Reads docs/evolution_journal.md for self-awareness.
"""

from __future__ import annotations
import json, logging, os, time, sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealthSnapshot:
    """One health check snapshot."""
    timestamp: str
    skill_bank_size: int = 0
    recent_success_rate: float = 0.0
    recent_failures: list[str] = field(default_factory=list)
    ooda_rounds: int = 0
    active: bool = True


class ActiveMonitor:
    """Proactive health checker and trend analyzer."""

    CHECK_INTERVAL = 300  # 5 minutes

    def __init__(self, workspace: str, instance_id: str = ""):
        self.workspace = workspace
        self.instance_id = instance_id
        self.snapshots: list[HealthSnapshot] = []
        self._load_history()

    def _load_history(self):
        path = os.path.join(self.workspace, "state", "health_snapshots.jsonl")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    for line in f:
                        if line.strip():
                            d = json.loads(line)
                            self.snapshots.append(HealthSnapshot(**d))
            except:
                pass

    def _save_snapshot(self, snap: HealthSnapshot):
        path = os.path.join(self.workspace, "state", "health_snapshots.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps({
                "timestamp": snap.timestamp,
                "skill_bank_size": snap.skill_bank_size,
                "recent_success_rate": snap.recent_success_rate,
                "recent_failures": snap.recent_failures,
                "ooda_rounds": snap.ooda_rounds,
                "active": snap.active,
            }, ensure_ascii=False) + "\n")

    def check(self) -> HealthSnapshot:
        """Run one health check. Returns a snapshot."""
        snap = HealthSnapshot(timestamp=datetime.now().isoformat())

        # 1. Skill bank health
        db_path = os.path.join(self.workspace, "state", "skill_bank.db")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                snap.skill_bank_size = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
                conn.close()
            except:
                pass

        # 2. Recent success rate from heal log
        heal_log = os.path.join(self.workspace, "state", "self_heal_log.jsonl")
        recent_heals = []
        if os.path.exists(heal_log):
            try:
                with open(heal_log) as f:
                    for line in f:
                        if line.strip():
                            recent_heals.append(json.loads(line))
            except:
                pass
        
        if recent_heals:
            successes = sum(1 for h in recent_heals[-20:] if h.get("applied"))
            snap.recent_success_rate = successes / min(20, len(recent_heals))
            snap.recent_failures = [
                h.get("root_cause", "")[:100]
                for h in recent_heals[-5:]
                if not h.get("applied")
            ]

        # 3. Read evolution journal for trends
        journal = self._read_journal()
        if journal:
            import re
            ooda_matches = re.findall(r'OODA|round_(\d+)', journal)
            snap.ooda_rounds = len(ooda_matches)

        # 4. Check if process is responsive
        snap.active = True

        self.snapshots.append(snap)
        self._save_snapshot(snap)

        # Log issues
        if snap.recent_success_rate < 0.3 and len(recent_heals) >= 5:
            logger.warning("[ACTIVE_MONITOR] Low success rate: %.0f%%", snap.recent_success_rate * 100)
        
        if snap.skill_bank_size == 0:
            logger.info("[ACTIVE_MONITOR] Skill bank empty — no self-heal skills yet")

        return snap

    def get_trends(self) -> dict:
        """Analyze trends from snapshots."""
        if len(self.snapshots) < 3:
            return {"status": "insufficient_data", "snapshots": len(self.snapshots)}

        recent = self.snapshots[-10:]
        rates = [s.recent_success_rate for s in recent if s.recent_success_rate > 0]
        sizes = [s.skill_bank_size for s in recent]

        trend = "stable"
        if len(rates) >= 3:
            if rates[-1] > rates[0] * 1.2:
                trend = "improving"
            elif rates[-1] < rates[0] * 0.8:
                trend = "declining"

        return {
            "status": "ok",
            "trend": trend,
            "avg_success_rate": sum(rates) / len(rates) if rates else 0,
            "skill_bank_growth": sizes[-1] - sizes[0] if sizes else 0,
            "total_snapshots": len(self.snapshots),
        }

    def suggest_optimizations(self) -> list[str]:
        """Generate auto-optimization suggestions."""
        suggestions = []
        trends = self.get_trends()

        if trends.get("trend") == "declining":
            suggestions.append("成功率下降 — 考虑增加 prompt 中 execute_code 示例")
        
        if trends.get("skill_bank_growth", 0) == 0 and len(self.snapshots) > 5:
            suggestions.append("技能库无增长 — 自愈可能未触发，检查 core_step_failed 路径")

        snap = self.snapshots[-1] if self.snapshots else None
        if snap and snap.recent_failures:
            top_failure = snap.recent_failures[0]
            if "SMILES" in top_failure or "性质" in top_failure:
                suggestions.append("分子性质计算持续失败 — 考虑直接写 RDKit 脚本而非调用 agent")

        return suggestions

    def _read_journal(self) -> str:
        for candidate in [
            os.path.join(self.workspace, "..", "..", "partner", "docs", "evolution_journal.md"),
        ]:
            p = os.path.normpath(candidate)
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        return f.read()
                except:
                    pass
        return ""

    def run_loop(self):
        """Run the monitoring loop (blocks). Call in a daemon thread."""
        logger.info("[ACTIVE_MONITOR] Started for instance %s, interval=%ds",
                     self.instance_id, self.CHECK_INTERVAL)
        while True:
            try:
                snap = self.check()
                suggestions = self.suggest_optimizations()
                
                if snap.recent_failures:
                    logger.info("[ACTIVE_MONITOR] %d recent failures, skill_bank=%d, rate=%.0f%%",
                               len(snap.recent_failures), snap.skill_bank_size,
                               snap.recent_success_rate * 100)
                
                for s in suggestions[:3]:
                    logger.info("[ACTIVE_MONITOR] Suggestion: %s", s)
                
            except Exception as e:
                logger.debug("[ACTIVE_MONITOR] check error: %s", e)
            
            time.sleep(self.CHECK_INTERVAL)
