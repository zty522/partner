"""Behavior Tuner — convert learned patterns into Planner behavior rules.

Uses task-relevance filtering: only injects rules whose keywords match
the current task goal, keeping prompt concise and relevant.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from ..utils.workspace import get_learning_db_path

logger = logging.getLogger(__name__)


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(get_learning_db_path())
    db.row_factory = sqlite3.Row
    return db


def load_rules(max_rules: int = 5, min_confidence: float = 0.3, category: str | None = None) -> list[dict]:
    """Load top rules by confidence, optionally filtered by category."""
    db = _get_db()
    if category:
        rows = db.execute(
            """SELECT id, rule_type, rule_text, confidence, category, effectiveness
               FROM evolution_rules
               WHERE confidence >= ? AND category = ?
               ORDER BY confidence DESC, effectiveness DESC
               LIMIT ?""",
            (min_confidence, category, max_rules),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT id, rule_type, rule_text, confidence, category, effectiveness
               FROM evolution_rules
               WHERE confidence >= ?
               ORDER BY confidence DESC, effectiveness DESC
               LIMIT ?""",
            (min_confidence, max_rules),
        ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def load_rules_by_type(rule_type: str, limit: int = 3) -> list[dict]:
    """Load best rules of a specific type."""
    db = _get_db()
    rows = db.execute(
        """SELECT id, rule_type, rule_text, confidence, category, effectiveness
           FROM evolution_rules
           WHERE rule_type = ?
           ORDER BY confidence DESC
           LIMIT ?""",
        (rule_type, limit),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text for relevance matching."""
    text = text.lower()
    # Split on common separators, keep Chinese chars and English words > 2 chars
    tokens = set()
    # English words
    for w in re.findall(r'[a-z]{3,}', text):
        tokens.add(w)
    # Chinese bi-grams
    for i in range(len(text) - 1):
        pair = text[i:i+2]
        if all('\u4e00' <= c <= '\u9fff' for c in pair):
            tokens.add(pair)
    return tokens


def _rule_relevance_score(rule_text: str, goal_keywords: set[str]) -> float:
    """Score how relevant a rule is to the current task goal. 0.0 to 1.0."""
    rule_lower = rule_text.lower()
    # Direct keyword matches
    matches = sum(1 for kw in goal_keywords if kw in rule_lower)
    if matches == 0:
        return 0.0
    return min(1.0, matches / max(len(goal_keywords), 1) * 3)


def select_relevant_rules(goal_text: str | None = None, max_rules: int = 4) -> list[dict]:
    """Select the most relevant rules for the given task goal.

    Uses keyword overlap between the goal text and rule_text.
    Falls back to top-confidence rules if no goal text is provided.
    Excludes architecture_insight rules (handled separately).
    """
    if not goal_text or not goal_text.strip():
        return load_rules(max_rules=max_rules, min_confidence=0.65)

    all_rules = load_rules(max_rules=50, min_confidence=0.65)
    if not all_rules:
        return []

    # Score and sort by relevance
    goal_kw = _extract_keywords(goal_text)
    scored = []
    for r in all_rules:
        if r.get("category") == "architecture_insight":
            continue  # architecture rules handled separately
        score = _rule_relevance_score(r.get("rule_text", ""), goal_kw)
        if score > 0:
            scored.append((score, r))

    # Sort by relevance (desc), then by confidence (desc)
    scored.sort(key=lambda x: (-x[0], -x[1].get("confidence", 0)))

    if scored:
        return [r for _, r in scored[:max_rules]]
    # Fallback to top confidence
    return load_rules(max_rules=max_rules, min_confidence=0.4)


def format_rules_for_prompt(max_rules: int = 5, goal_text: str | None = None) -> str:
    """Format evolution rules as a planner-injectable string.

    Includes both task-relevant general rules and architecture insight rules
    (only if the goal involves architecture/design/methodology keywords).
    This is the primary integration point with prompt_builder.py.
    Returns an empty string if no rules are available.
    """
    lines = []
    total_chars = 0
    MAX_CHARS = 1200  # hard cap on total prompt injection size

    # ── General rules (task-relevant) ──
    rules = select_relevant_rules(goal_text=goal_text, max_rules=max_rules)
    if rules:
        lines.append("## 自进化经验规则")
        total_chars += 20
        for r in rules:
            rt = r.get("rule_text", "")
            conf = r.get("confidence", 0.5)
            label = "高" if conf >= 0.7 else "中" if conf >= 0.5 else "低"
            line = f"- {rt[:80]} [{label}]"
            if total_chars + len(line) > MAX_CHARS:
                break
            lines.append(line)
            total_chars += len(line)

    # ── Architecture insight rules (only if goal mentions architecture/methodology) ──
    _arch_keywords = {"架构", "设计", "改进", "方法", "思路", "architecture", "design", "improve",
                      "pattern", "并行", "轨道", "track", "pipeline", "编排", "调度", "方案",
                      "对比", "借鉴", "机制", "Agent", "编排方式", "架构设计", "调度方案",
                      "错误重试", "回退", "Harness", "LangChain"}
    _needs_arch = goal_text and any(kw in goal_text.lower() or kw in goal_text for kw in _arch_keywords)
    if _needs_arch:
        arch_rules = load_rules(max_rules=2, min_confidence=0.5, category="architecture_insight")
        if arch_rules:
            lines.append("")
            total_chars += 2
            lines.append("## 架构借鉴（外部系统学习）")
            total_chars += 20
            for r in arch_rules:
                rt = r.get("rule_text", "")
                conf = r.get("confidence", 0.5)
                line = f"- {rt[:100]} [c:{conf:.2f}]"
                if total_chars + len(line) > MAX_CHARS:
                    break
                lines.append(line)
                total_chars += len(line)

    return "\n".join(lines) if lines else ""


def mark_rule_applied(rule_id: int, effectiveness: float | None = None) -> None:
    """Mark a rule as having been applied in a planning cycle."""
    from datetime import datetime

    db = _get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if effectiveness is not None:
        db.execute(
            "UPDATE evolution_rules SET applied_at = ?, effectiveness = ? WHERE id = ?",
            (now, effectiveness, rule_id),
        )
    else:
        db.execute(
            "UPDATE evolution_rules SET applied_at = ? WHERE id = ?",
            (now, rule_id),
        )
    db.commit()
    db.close()


def record_rule_effectiveness(rule_id: int, task_success: bool) -> None:
    """Update rule effectiveness based on whether the task that used it succeeded."""
    db = _get_db()
    row = db.execute(
        "SELECT effectiveness, applied_at FROM evolution_rules WHERE id = ?",
        (rule_id,),
    ).fetchone()
    if row is None:
        db.close()
        return
    current = row["effectiveness"] or 0.0
    # Exponentially weighted moving average
    alpha = 0.3
    new_eff = current * (1 - alpha) + (1.0 if task_success else 0.0) * alpha
    db.execute(
        "UPDATE evolution_rules SET effectiveness = ? WHERE id = ?",
        (round(new_eff, 3), rule_id),
    )
    db.commit()
    db.close()


def tune_parameters_from_habits() -> dict[str, Any]:
    """Generate behavior parameter adjustments from learned habits.

    Returns a dict of parameter overrides for the Planner.
    """
    db = _get_db()
    rows = db.execute(
        "SELECT key, value, confidence FROM habits WHERE confidence >= 0.4"
    ).fetchall()
    db.close()

    params: dict[str, Any] = {}
    for r in rows:
        key = r["key"]
        val = r["value"]
        try:
            parsed = json.loads(val) if val.startswith(("{", "[")) else val
        except (json.JSONDecodeError, ValueError, TypeError):
            parsed = val

        if key == "preferred_language":
            lang = parsed.strip('"')
            if lang == "zh":
                params["output_language"] = "zh"
        elif key == "prefer_pdf":
            params["prefer_pdf"] = parsed in (True, "true", "1")
        elif key == "min_citations":
            try:
                params["min_citations"] = int(parsed)
            except (ValueError, TypeError):
                pass
        elif key == "avoid_web_search":
            params["avoid_web_search"] = parsed in (True, "true", "1")
        elif key.startswith("processing_habits."):
            task_type = key.split(".", 1)[1]
            params.setdefault("processing_habits", {})[task_type] = parsed

    return params


def count_rules() -> int:
    """Total stored evolution rules."""
    db = _get_db()
    count = db.execute("SELECT COUNT(*) AS c FROM evolution_rules").fetchone()["c"]
    db.close()
    return count
