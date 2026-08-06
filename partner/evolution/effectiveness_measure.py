from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EffectivenessMeasure:
    def measure_all(self) -> dict:
        """Measure overall self-evolution effectiveness."""
        result = {}
        try:
            result["rules_growth"] = self._measure_rules_growth()
        except Exception as e:
            result["rules_growth"] = {"error": str(e)}
        try:
            result["experience_improvement"] = self._measure_experience_improvement()
        except Exception as e:
            result["experience_improvement"] = {"error": str(e)}
        try:
            result["benchmark_trend"] = self._measure_benchmark_trend()
        except Exception as e:
            result["benchmark_trend"] = {"error": str(e)}
        return result

    def _measure_rules_growth(self) -> dict:
        from ..evolution.evolution_db import get_active_rules

        rules = get_active_rules(min_confidence=0.0, limit=200)
        cats = {}
        for r in rules:
            c = r.get("category", "other")
            cats[c] = cats.get(c, 0) + 1
        return {"total_rules": len(rules), "by_category": cats}

    def _measure_experience_improvement(self) -> dict:
        from ..meta.learning import get_experience_stats

        stats = get_experience_stats()
        return {
            "total": stats.get("total", 0),
            "success_rate": stats.get("success_rate", 0),
        }

    def _measure_benchmark_trend(self) -> dict:
        from ..evolution.evolution_db import init_evolution_db, _get_db

        init_evolution_db()
        db = _get_db()
        rows = db.execute(
            """SELECT id, reflection FROM growth
               WHERE category='benchmark_evaluation'
               ORDER BY id DESC LIMIT 5"""
        ).fetchall()
        import json

        scores = []
        for r in rows:
            try:
                ref = json.loads(str(r["reflection"] or "{}"))
                scores.append(
                    {
                        "id": r["id"],
                        "total_score": ref.get("total_score", 0),
                        "max_score": ref.get("max_score", 100),
                    }
                )
            except Exception:
                pass
        return {"benchmark_runs": len(scores), "recent_scores": scores}
