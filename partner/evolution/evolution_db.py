"""Evolution data persistence — extends ~/.partner/learning.db with self-evolution tables.

Schema adds:
- evolution_rules: learned behavior rules with confidence tracking
- evolution_state: cycle tracking (last_run, completed_since_last_cycle, cumulative stats)
- evolution_patterns: raw extracted patterns before they become rules
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time as _time
from typing import Any

logger = logging.getLogger(__name__)

def _get_global_db_path():
    import os as _os
    env = _os.environ.get("PARTNER_DATA_DIR", "")
    if env:
        return _os.path.join(env, "learning.db")
    return _os.path.expanduser("~/.partner/learning.db")
GLOBAL_DB_PATH = _get_global_db_path()
_global_db_local = threading.local()

# ── Schema (appended to existing learning.db) ────────────────────────

EVOLUTION_SCHEMA = """
-- Evolution rules: what to do differently based on learned patterns
CREATE TABLE IF NOT EXISTS evolution_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'agent_selection',
    -- 'agent_selection', 'event_sequence', 'parameter_tweak', 'failure_avoidance', 'general'
    condition TEXT NOT NULL,       -- JSON: {"task_type": "文献综述", ...} match conditions
    action TEXT NOT NULL,          -- JSON: {"preferred_agent": "cytobridge", ...} action to take
    confidence REAL NOT NULL DEFAULT 0.5,
    success_rate REAL DEFAULT 0.0,
    observation_count INTEGER DEFAULT 0,
    source TEXT DEFAULT '',         -- how this rule was derived
    rule_text TEXT DEFAULT '',      -- human-readable description for prompt injection
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rules_category ON evolution_rules(category);
CREATE INDEX IF NOT EXISTS idx_rules_active ON evolution_rules(is_active);

-- Evolution state: per-cycle tracking
CREATE TABLE IF NOT EXISTS evolution_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Raw extracted patterns (intermediate — before rule consolidation)
CREATE TABLE IF NOT EXISTS evolution_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    pattern_data TEXT NOT NULL,     -- JSON pattern details
    confidence REAL DEFAULT 0.3,
    sample_count INTEGER DEFAULT 0,
    observed_at TEXT DEFAULT (datetime('now'))
);

-- Knowledge: external system architecture learnings
CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'unknown',
    key_insights TEXT DEFAULT '',
    architecture_mapping TEXT DEFAULT '',
    improvements_applied TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ── Default evolution state keys ────────────────────────────────────

_DEFAULT_STATE: dict[str, str] = {
    "completed_since_last_cycle": "0",
    "last_cycle_at": "0",
    "cycle_count": "0",
    "total_observations_processed": "0",
    "total_rules_generated": "0",
    "min_experiences_before_cycle": "5",  # how many completed tasks trigger a cycle
    "max_rules_in_prompt": "5",          # how many rules to inject into prompt
    "convergence_min_confidence": "0.6",  # minimum confidence for rule to be used
}


# ── DB connection (same db as learning) ─────────────────────────────

def _get_db() -> sqlite3.Connection:
    if not hasattr(_global_db_local, "conn") or _global_db_local.conn is None:
        import os
        os.makedirs(os.path.dirname(GLOBAL_DB_PATH), exist_ok=True)
        _global_db_local.conn = sqlite3.connect(GLOBAL_DB_PATH)
        _global_db_local.conn.row_factory = sqlite3.Row
        _global_db_local.conn.execute("PRAGMA journal_mode=WAL")
        _global_db_local.conn.execute("PRAGMA foreign_keys=ON")
    return _global_db_local.conn


def init_evolution_db():
    """Create evolution tables if they don't exist."""
    db = _get_db()
    db.executescript(EVOLUTION_SCHEMA)
    # Ensure default state rows
    for key, val in _DEFAULT_STATE.items():
        db.execute(
            "INSERT OR IGNORE INTO evolution_state (key, value) VALUES (?, ?)",
            (key, val),
        )
    db.commit()


# ── State accessors ─────────────────────────────────────────────────

def get_state(key: str, default: str = "0") -> str:
    init_evolution_db()
    row = _get_db().execute(
        "SELECT value FROM evolution_state WHERE key=?", (key,)
    ).fetchone()
    return row["value"] if row else default


def set_state(key: str, value: str):
    init_evolution_db()
    _get_db().execute(
        "INSERT INTO evolution_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    _get_db().commit()


def increment_completed_count(amount: int = 1) -> int:
    """Increment completed_since_last_cycle, return new count."""
    count = int(get_state("completed_since_last_cycle", "0")) + amount
    set_state("completed_since_last_cycle", str(count))
    return count


def reset_cycle_counter():
    set_state("completed_since_last_cycle", "0")
    set_state("last_cycle_at", str(_time.time()))


def increment_cycle_count() -> int:
    c = int(get_state("cycle_count", "0")) + 1
    set_state("cycle_count", str(c))
    return c


# ── Rules CRUD ──────────────────────────────────────────────────────

def save_rule(
    category: str,
    condition: dict,
    action: dict,
    confidence: float = 0.5,
    success_rate: float = 0.0,
    observation_count: int = 1,
    source: str = "",
    rule_text: str = "",
) -> int:
    """Save a new evolution rule. Returns rule id."""
    init_evolution_db()
    db = _get_db()
    cur = db.execute(
        """INSERT INTO evolution_rules
           (category, condition, action, confidence, success_rate,
            observation_count, source, rule_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            category[:50],
            json.dumps(condition, ensure_ascii=False),
            json.dumps(action, ensure_ascii=False),
            round(confidence, 4),
            round(success_rate, 4),
            observation_count,
            source[:200],
            rule_text[:500],
        ),
    )
    db.commit()
    rid = cur.lastrowid or 0

    # Update total rules count for state tracking
    count = int(get_state("total_rules_generated", "0")) + 1
    set_state("total_rules_generated", str(count))

    logger.info("[EVOLUTION] rule saved: id=%d cat=%s confidence=%.2f", rid, category, confidence)
    return rid


def get_active_rules(category: str | None = None, min_confidence: float = 0.0, limit: int = 20) -> list[dict]:
    """Load active rules, optionally filtered by category and minimum confidence."""
    init_evolution_db()
    db = _get_db()

    if category:
        rows = db.execute(
            """SELECT * FROM evolution_rules
               WHERE is_active=1 AND category=? AND confidence>=?
               ORDER BY confidence DESC, observation_count DESC
               LIMIT ?""",
            (category, min_confidence, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT * FROM evolution_rules
               WHERE is_active=1 AND confidence>=?
               ORDER BY confidence DESC, observation_count DESC
               LIMIT ?""",
            (min_confidence, limit),
        ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        try:
            d["condition"] = json.loads(d["condition"])
        except Exception:
            d["condition"] = {}
        try:
            d["action"] = json.loads(d["action"])
        except Exception:
            d["action"] = {}
        result.append(d)
    return result


def update_rule_confidence(rule_id: int, delta: float):
    """Adjust a rule's confidence (bounded [0.0, 1.0])."""
    init_evolution_db()
    db = _get_db()
    row = db.execute(
        "SELECT confidence FROM evolution_rules WHERE id=?", (rule_id,)
    ).fetchone()
    if not row:
        return
    new_conf = max(0.0, min(1.0, row["confidence"] + delta))
    db.execute(
        "UPDATE evolution_rules SET confidence=?, updated_at=datetime('now') WHERE id=?",
        (new_conf, rule_id),
    )
    db.commit()


def deactivate_rule(rule_id: int):
    """Mark a rule as inactive (confidence too low, or superseded)."""
    init_evolution_db()
    _get_db().execute(
        "UPDATE evolution_rules SET is_active=0, updated_at=datetime('now') WHERE id=?",
        (rule_id,),
    )
    _get_db().commit()


def record_pattern(category: str, pattern_data: dict, confidence: float = 0.3, sample_count: int = 1):
    """Record a raw extracted pattern (intermediate before rule creation)."""
    init_evolution_db()
    _get_db().execute(
        """INSERT INTO evolution_patterns (category, pattern_data, confidence, sample_count)
           VALUES (?, ?, ?, ?)""",
        (
            category[:50],
            json.dumps(pattern_data, ensure_ascii=False),
            round(confidence, 4),
            sample_count,
        ),
    )
    _get_db().commit()


# ── Query helpers for lesson extraction ─────────────────────────────

def query_experiences_for_pattern(min_rows: int = 10) -> list[dict]:
    """Fetch recent completed experiences for pattern mining."""
    db = _get_db()
    rows = db.execute(
        """SELECT e.* FROM experiences e
           ORDER BY e.id DESC
           LIMIT ?""",
        (min_rows * 3,),  # fetch more to filter
    ).fetchall()
    return [dict(r) for r in rows]


def get_experiences_by_task_type(task_keyword: str, limit: int = 50) -> list[dict]:
    """Get experiences matching a task type keyword."""
    db = _get_db()
    rows = db.execute(
        """SELECT * FROM experiences
           WHERE user_message LIKE ? OR task_summary LIKE ?
           ORDER BY id DESC LIMIT ?""",
        (f"%{task_keyword}%", f"%{task_keyword}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_experience_stats() -> dict:
    """Return aggregate stats: total, success rate, by agent, by output type."""
    db = _get_db()
    total = db.execute("SELECT COUNT(*) AS c FROM experiences").fetchone()["c"]
    successes = db.execute("SELECT COUNT(*) AS c FROM experiences WHERE success=1").fetchone()["c"]

    # By agent
    by_agent = db.execute(
        """SELECT agent_used, COUNT(*) AS cnt, SUM(success) AS ok
           FROM experiences WHERE agent_used != ''
           GROUP BY agent_used ORDER BY cnt DESC"""
    ).fetchall()

    # By output type
    by_output = db.execute(
        """SELECT output_type, COUNT(*) AS cnt, SUM(success) AS ok
           FROM experiences GROUP BY output_type ORDER BY cnt DESC"""
    ).fetchall()

    return {
        "total": total,
        "successes": successes,
        "success_rate": round(successes / max(total, 1), 4),
        "by_agent": [dict(r) for r in by_agent],
        "by_output": [dict(r) for r in by_output],
    }
