"""Lesson Extractor — extract reusable patterns from execution history.

Analyzes the experiences table to discover:
1. Agent selection patterns — which agents work best for which task types
2. Event sequence patterns — what step combinations have high success rates
3. Failure avoidance patterns — what conditions correlate with failures
4. Output preference patterns — what output types are common for tasks

Each extracted rule is written to the evolution_rules table.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from ..utils.workspace import get_learning_db_path

logger = logging.getLogger(__name__)

RULE_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type   TEXT NOT NULL,
    rule_text   TEXT NOT NULL,
    confidence  REAL DEFAULT 0.5,
    source_ids  TEXT DEFAULT '[]',
    category    TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now')),
    applied_at  TEXT DEFAULT '',
    effectiveness REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_evo_rules_type ON evolution_rules(rule_type);
CREATE INDEX IF NOT EXISTS idx_evo_rules_conf ON evolution_rules(confidence DESC);
"""


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(get_learning_db_path())
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def ensure_rules_table():
    db = _get_db()
    # Check if table exists and has the right columns
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='evolution_rules'")
    exists = cursor.fetchone()
    if exists:
        # Verify column presence
        cols = {r["name"] for r in db.execute("PRAGMA table_info(evolution_rules)").fetchall()}
        expected = {"rule_type", "rule_text", "confidence", "source_ids", "category", "effectiveness"}
        missing = expected - cols
        if not missing:
            db.close()
            return
        # Drop and recreate if schema is wrong
        logger.info("[LESSON_EXTRACTOR] evolution_rules table has wrong schema, recreating...")
        db.execute("DROP TABLE IF EXISTS evolution_rules")
        db.commit()
    db.executescript(RULE_SCHEMA)
    db.commit()
    db.close()


def _fetch_experiences(db: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """Fetch recent experiences for pattern mining."""
    rows = db.execute(
        """SELECT id, user_message, task_summary, output_type, success,
                  agent_used, skills_used, created_at
           FROM experiences
           ORDER BY id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _count_by(db: sqlite3.Connection, column: str, *, success_filter: bool | None = None) -> dict[str, int]:
    """Count rows grouped by a column, optionally filtered by success."""
    w = "WHERE success = 1" if success_filter is True else "WHERE success = 0" if success_filter is False else ""
    rows = db.execute(f"SELECT {column} AS k, COUNT(*) AS c FROM experiences {w} GROUP BY k ORDER BY c DESC").fetchall()
    return {r["k"]: r["c"] for r in rows if r["k"]}


# ── Rule extractors ──────────────────────────────────────────────────────


def _extract_agent_selection_rules(exps: list[dict]) -> list[dict]:
    """Discover which task keywords map to which agents with high success.

    Analyzes task_summary / user_message keywords against agent_used + success.
    Returns list of rule dicts.
    
    Only generates rules when there are 2+ distinct agents in use — otherwise
    every keyword maps to the same single agent and the rules are meaningless.
    """
    from collections import defaultdict

    # Count distinct agents first — skip if only one
    distinct_agents = set()
    for e in exps:
        agent = e.get("agent_used", "") or "default"
        distinct_agents.add(agent)
    if len(distinct_agents) < 2:
        return []  # Only one agent in use, keyword→agent mapping is meaningless

    keyword_agent_stats: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for e in exps:
        text = (e.get("task_summary", "") or "") + " " + (e.get("user_message", "") or "")
        agent = e.get("agent_used", "") or "default"
        success = e.get("success", 1)
        words = [w for w in text.lower().split()[:10] if len(w) > 3 and w not in ("目标", "调用", "成功", "失败")]
        for w in words[:5]:
            keyword_agent_stats[w][agent].append(success)

    rules = []
    for keyword, agent_stats in keyword_agent_stats.items():
        for agent, outcomes in agent_stats.items():
            if len(outcomes) < 3:  # Need at least 3 samples
                continue
            success_rate = sum(outcomes) / len(outcomes)
            if success_rate >= 0.8 and len(outcomes) >= 3:
                confidence = min(0.9, 0.4 + 0.1 * len(outcomes))
                rules.append({
                    "rule_type": "agent_selection",
                    "rule_text": f"任务包含关键词「{keyword}」时，Agent「{agent}」的成功率为 {success_rate:.0%}（{len(outcomes)} 次样本）",
                    "confidence": round(confidence, 2),
                    "category": "agent_selection",
                    "source_ids": json.dumps([e["id"] for e in exps if keyword in ((e.get("task_summary", "") or "") + (e.get("user_message", "") or "")).lower()][:10]),
                })
    return rules


def _extract_output_preference_rules(exps: list[dict]) -> list[dict]:
    """Discover which output_type are common for which task kinds.
    
    Only generates rules when there's a meaningful, non-trivial distribution.
    Skips: "text" type (universal default), near-100% dominance, minor variations.
    """
    from collections import Counter

    type_counter: Counter = Counter(e.get("output_type", "text") for e in exps if e.get("success"))
    total_success = sum(type_counter.values())
    if total_success < 20:  # Need substantial data
        return []

    rules = []
    # Only report when there are 2+ distinct, non-trivial types
    meaningful_types = {t: c for t, c in type_counter.items() 
                       if t not in ("text",) and c >= 5}
    if len(meaningful_types) < 1:
        return []
    
    for otype, count in sorted(meaningful_types.items(), key=lambda x: -x[1])[:3]:
        pct = count / total_success * 100
        rules.append({
            "rule_type": "output_preference",
            "rule_text": f"成功任务中 {pct:.0f}% 使用了「{otype}」类型输出（{count}/{total_success} 次）",
            "confidence": min(0.85, 0.3 + pct / 200),
            "category": otype,
            "source_ids": json.dumps([e["id"] for e in exps if e.get("output_type") == otype][:10]),
        })
    return rules


def _extract_failure_patterns(exps: list[dict]) -> list[dict]:
    """Analyze failure reasons and extract avoidance rules.

    Looks for common failure patterns in task_summary and user_message.
    """
    from collections import Counter

    failures = [e for e in exps if not e.get("success")]
    if len(failures) < 3:
        return []

    # Look for keywords common in failures
    fail_words: Counter = Counter()
    for e in failures:
        text = (e.get("task_summary", "") or "") + " " + (e.get("user_message", "") or "")
        words = [w for w in text.lower().split() if len(w) > 4]
        fail_words.update(words)

    rules = []
    top_fail_words = fail_words.most_common(5)
    for word, count in top_fail_words:
        if count >= 2:
            rules.append({
                "rule_type": "failure_avoidance",
                "rule_text": f"关键词「{word}」关联 {count} 次失败，需特别关注前置条件和参数验证",
                "confidence": min(0.85, 0.2 + 0.15 * count),
                "category": word,
                "source_ids": json.dumps([e["id"] for e in failures][:10]),
            })
    return rules


def _extract_skill_combo_rules(exps: list[dict]) -> list[dict]:
    """Discover successful skill combinations used in tasks."""
    from collections import Counter

    combo_counter: Counter = Counter()
    for e in exps:
        if e.get("success"):
            skills = json.loads(e.get("skills_used", "[]"))
            if len(skills) >= 2:
                combo_counter[tuple(sorted(skills))] += 1

    rules = []
    for combo, count in combo_counter.most_common(5):
        if count >= 2:
            rules.append({
                "rule_type": "event_sequence",
                "rule_text": f"技能组合 {list(combo)} 成功执行 {count} 次，可参考该组合的步骤编排方式",
                "confidence": min(0.9, 0.3 + 0.15 * count),
                "category": "+".join(combo)[:50],
                "source_ids": "[]",
            })
    return rules


# ── Public API ────────────────────────────────────────────────────────────


def extract_lessons(limit: int = 200) -> list[dict]:
    """Main extraction workflow — runs all extractors and writes to DB.

    Returns the list of newly created rules as dicts.
    """
    ensure_rules_table()
    db = _get_db()
    exps = _fetch_experiences(db, limit=limit)
    db.close()

    if not exps:
        logger.info("[LESSON_EXTRACTOR] no experiences to analyze")
        return []

    rules: list[dict] = []
    rules.extend(_extract_agent_selection_rules(exps))
    rules.extend(_extract_output_preference_rules(exps))
    rules.extend(_extract_failure_patterns(exps))
    rules.extend(_extract_skill_combo_rules(exps))

    if not rules:
        logger.info("[LESSON_EXTRACTOR] no patterns found in %d experiences", len(exps))
        return []

    # Deduplicate against existing rules
    db2 = _get_db()
    existing = {r["rule_text"]
                for r in db2.execute("SELECT rule_text FROM evolution_rules").fetchall()}
    new_rules = [r for r in rules if r["rule_text"] not in existing]

    if new_rules:
        db2.executemany(
            """INSERT INTO evolution_rules
               (rule_type, rule_text, confidence, source_ids, category)
               VALUES (:rule_type, :rule_text, :confidence, :source_ids, :category)""",
            new_rules,
        )
        db2.commit()
        logger.info("[LESSON_EXTRACTOR] added %d new rules from %d experiences",
                     len(new_rules), len(exps))
    else:
        logger.info("[LESSON_EXTRACTOR] all %d candidate rules already exist, skipped", len(rules))

    db2.close()
    return new_rules


def get_rule_stats() -> dict[str, Any]:
    """Get aggregate statistics about stored evolution rules."""
    ensure_rules_table()
    db = _get_db()
    total = db.execute("SELECT COUNT(*) AS c FROM evolution_rules").fetchone()["c"]
    by_type = {r["rule_type"]: r["c"]
               for r in db.execute(
                   "SELECT rule_type, COUNT(*) AS c FROM evolution_rules GROUP BY rule_type"
               ).fetchall()}
    avg_conf = db.execute("SELECT AVG(confidence) AS c FROM evolution_rules").fetchone()["c"] or 0
    db.close()
    return {
        "total_rules": total,
        "by_type": by_type,
        "avg_confidence": round(avg_conf, 3),
    }
