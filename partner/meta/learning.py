"""Learning Manager — experience, growth, and habit tracking for Partner.

Unified SQLite backend (replaces file-based + keyword index + scattered JSONL).

After each project completes, this module records:
- experience: output type, success/failure, skills used, user feedback
- growth: milestones and capability improvements
- habits: user preferences with confidence

All data stored in ~/.partner/learning.db, shared across instances.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LEARNING_DIR = os.path.expanduser("~/.partner/learning")
GLOBAL_DB_PATH = os.path.expanduser("~/.partner/learning.db")
HABITS_PATH = os.path.expanduser("~/.partner/habits.json")  # kept for backward compat, will be phased out
_habits_cache: dict[str, Any] | None = None
_global_db_local = threading.local()

# ── SQLite schema ────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO _schema_version (version) VALUES (2);

-- New unified tables (migrate from old file-based schema)
CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_message TEXT NOT NULL,
    task_summary TEXT DEFAULT '',
    output_type TEXT DEFAULT 'text',
    file_format TEXT DEFAULT '',
    success INTEGER DEFAULT 1,
    feedback_received INTEGER DEFAULT 0,
    agent_used TEXT DEFAULT '',
    skills_used TEXT DEFAULT '[]',
    instance_id TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS habits (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS growth (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    milestone TEXT NOT NULL,
    reflection TEXT DEFAULT '',
    instance_id TEXT DEFAULT '',
    category TEXT DEFAULT 'milestone',
    created_at TEXT DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_experiences_msg ON experiences(user_message);
CREATE INDEX IF NOT EXISTS idx_experiences_output ON experiences(output_type);
CREATE INDEX IF NOT EXISTS idx_growth_user ON growth(user_id);

-- FTS5 (drop and recreate if schema changed)
DROP TABLE IF EXISTS experiences_fts;
CREATE VIRTUAL TABLE IF NOT EXISTS experiences_fts USING fts5(
    user_message, task_summary, skills_used, content=experiences, content_rowid=id
);
"""


# ── DB connection ────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    if not hasattr(_global_db_local, "conn") or _global_db_local.conn is None:
        os.makedirs(os.path.dirname(GLOBAL_DB_PATH), exist_ok=True)
        _global_db_local.conn = sqlite3.connect(GLOBAL_DB_PATH)
        _global_db_local.conn.row_factory = sqlite3.Row
        _global_db_local.conn.execute("PRAGMA journal_mode=WAL")
        _global_db_local.conn.execute("PRAGMA foreign_keys=ON")
    return _global_db_local.conn


def init_db():
    """Create tables if they don't exist."""
    db = _get_db()
    db.executescript(SCHEMA_SQL)
    db.commit()


# ═════════════════════════════════════════════════════════════════════════
#  EXPERIENCE
# ═════════════════════════════════════════════════════════════════════════

def record_experience(
    user_message: str,
    *,
    task_summary: str = "",
    output_type: str = "text",
    file_format: str = "",
    success: bool = True,
    agent_used: str = "",
    skills_used: list[str] | None = None,
    instance_id: str = "",
) -> int | None:
    """Record a task experience with its output type in the unified DB."""
    init_db()
    db = _get_db()
    skills_json = json.dumps(skills_used or [], ensure_ascii=False)
    cur = db.execute(
        """INSERT INTO experiences
           (user_message, task_summary, output_type, file_format, success,
            agent_used, skills_used, instance_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_message[:500], task_summary[:1000],
            output_type[:20], file_format[:20],
            int(success), agent_used[:100], skills_json,
            instance_id[:64],
        ),
    )
    db.commit()
    eid = cur.lastrowid

    # Also update FTS index
    try:
        db.execute(
            "INSERT INTO experiences_fts(rowid, user_message, task_summary, skills_used) VALUES (?, ?, ?, ?)",
            (eid, user_message[:500], task_summary[:1000], skills_json),
        )
        db.commit()
    except Exception as exc:
        logger.debug("[LEARNING] FTS index update failed: %s", exc)

    logger.info(
        "[LEARNING] experience recorded: output=%s success=%s msg=%s",
        output_type, success, user_message[:60],
    )
    # Rebuild FTS sync index
    try:
        db = _get_db()
        db.execute("INSERT INTO experiences_fts(experiences_fts) VALUES('rebuild')")
        db.commit()
    except Exception as exc:
        logger.debug("[LEARNING] FTS rebuild failed: %s", exc)
    return eid


def record_feedback(
    original_message: str,
    *,
    corrected_output_type: str,
    corrected_format: str = "",
    confidence_delta: float = 0.3,
) -> None:
    """Record explicit user correction about output type.

    Called when user says things like '不要文件，直接告诉我' or '我要表格不是文字'.
    Updates the most recent matching experience and adjusts habits.
    """
    init_db()
    db = _get_db()

    # Mark the most recent matching experience as having feedback
    row = db.execute(
        """SELECT id, output_type FROM experiences
           WHERE user_message LIKE ? AND feedback_received=0
           ORDER BY id DESC LIMIT 1""",
        (f"{original_message[:200]}%",),
    ).fetchone()

    if row:
        exp_id = row["id"]
        old_type = row["output_type"]
        db.execute(
            "UPDATE experiences SET output_type=?, feedback_received=1 WHERE id=?",
            (corrected_output_type, exp_id),
        )
        db.commit()
        logger.info(
            "[LEARNING] feedback recorded: exp=%d old=%s new=%s",
            exp_id, old_type, corrected_output_type,
        )

        # Auto-update habit: learn that this kind of query prefers corrected_output_type
        key = _infer_habit_key_from_message(original_message)
        if key:
            set_habit("default", key, corrected_output_type, confidence=0.7)
            set_habit("default", f"{key}_format", corrected_format or corrected_output_type, confidence=0.6)
    else:
        # No matching experience, just record the preference as a new habit
        key = _infer_habit_key_from_message(original_message)
        if key:
            set_habit("default", key, corrected_output_type, confidence=0.6)


def _infer_habit_key_from_message(msg: str) -> str:
    """Infer a habit key from a user message.

    Pure LLM-driven: uses simple heuristic fallback, no hardcoded keywords.
    Returns a semantic category string.
    """
    msg_lower = msg.lower().strip()
    # Simple structural detection — not keyword matching, just length-based
    # For proper implementation, this should call LLM. For now, use message
    # fingerprint: first 3 meaningful chars + length bucket
    words = [w for w in re.split(r'[\s,，。！？、；;:：]+', msg_lower) if len(w) > 1]
    if not words:
        return "general_output_preference"

    # Generate a stable key from significant words (max 3)
    significant = sorted(set(w[:8] for w in words if len(w) > 2))[:3]
    if significant:
        return f"query_type_{'_'.join(significant)}"
    return "general_output_preference"


def get_relevant_experiences(
    user_message: str,
    limit: int = 5,
    min_similarity: int = 0,
) -> list[dict[str, Any]]:
    """Retrieve experiences relevant to the current user message.

    Uses FTS5 full-text search on the experiences table.
    Falls back to simple LIKE matching if FTS not available.
    """
    init_db()
    db = _get_db()

    # Try FTS5 first
    try:
        # Escape FTS5 special characters
        safe_msg = user_message.replace('"', '""').replace("'", "''")[:300]
        rows = db.execute(
            """SELECT e.* FROM experiences e
               JOIN experiences_fts fts ON e.id = fts.rowid
               WHERE experiences_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (safe_msg, limit),
        ).fetchall()
        if rows:
            result = [dict(r) for r in rows]
            logger.info(
                "[LEARNING] FTS matched %d experiences for msg=%s",
                len(result), user_message[:50],
            )
            return result
    except Exception as exc:
        logger.debug("[LEARNING] FTS search failed, falling back: %s", exc)

    # Fallback: simple LIKE matching with Chinese character segmentation
    # Split Chinese text into overlapping 2-char segments for better matching
    words = []
    for w in re.split(r'[\s,，。！？、；;:：()（）]+', user_message):
        w = w.strip()
        if not w:
            continue
        if len(w) > 2:
            # For Chinese text, generate overlapping bigrams
            chinese_chars = re.findall(r'[\u4e00-\u9fff]', w)
            if len(chinese_chars) >= len(w) * 0.5:  # mostly Chinese
                # Use the whole phrase as a search term
                if len(w) <= 20:
                    words.append(w)
                # Also add individual character pairs for fuzzy matching
                for i in range(len(chinese_chars) - 1):
                    bigram = chinese_chars[i] + chinese_chars[i + 1]
                    if bigram not in words and len(bigram) > 1:
                        words.append(bigram)
            else:
                words.append(w[:30])
    if not words:
        # Return most recent experiences if no meaningful words
        rows = db.execute(
            "SELECT * FROM experiences ORDER BY id DESC LIMIT ?",
            (min(limit, 5),),
        ).fetchall()
        return [dict(r) for r in rows]

    # Build LIKE pattern from significant words
    seen = set()
    matches = []
    for word in words:
        if word.lower() in seen:
            continue
        seen.add(word.lower())
        pattern = f"%{word[:50]}%"
        rows = db.execute(
            """SELECT DISTINCT e.* FROM experiences e
               WHERE e.user_message LIKE ? OR e.task_summary LIKE ?
               ORDER BY e.id DESC LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
        for r in rows:
            matches.append(dict(r))

    # Deduplicate and limit
    seen_ids = set()
    unique = []
    for m in matches:
        if m["id"] not in seen_ids:
            seen_ids.add(m["id"])
            unique.append(m)
            if len(unique) >= limit:
                break

    if unique:
        logger.info(
            "[LEARNING] LIKE matched %d experiences for msg=%s",
            len(unique), user_message[:50],
        )

    return unique


def format_experiences_for_prompt(
    user_message: str,
    max_experiences: int = 3,
) -> str:
    """Format relevant experiences as a natural-language block for planner prompts."""
    rows = get_relevant_experiences(user_message, limit=max_experiences + 2)
    if not rows:
        return ""

    lines = ["## 相关经验（来自类似任务）"]
    for row in rows[:max_experiences]:
        status = "成功" if row.get("success") else "失败"
        otype = row.get("output_type", "text")
        fformat = row.get("file_format", "")
        summary = str(row.get("task_summary") or row.get("user_message") or "")[:150]

        output_desc = {
            "text": "纯文本回复",
            "file": f"文件输出（{fformat or '未知格式'}）",
        }.get(otype, otype)

        lines.append(f"- [{status}] {summary} → {output_desc}")
        if row.get("feedback_received"):
            lines.append(f"  （用户对此输出形式有反馈修正）")

    if lines:
        lines.append("")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════
#  HABITS
# ═════════════════════════════════════════════════════════════════════════

_DEFAULT_HABITS: dict[str, Any] = {
    "prefer_pdf": True,
    "min_citations": 5,
    "default_search_concurrency": 2,
    "preferred_language": "zh",
    "include_mae_comparison": True,
    "report_structure": ["abstract", "methods", "comparison", "limitations", "breakthroughs"],
    "processing_habits": {
        "literature_review": {
            "parallel_queries": 3,
            "preferred_sources": ["hermes_search_papers", "pubmed_search", "literature_search"],
            "summarize_after_search": True,
        },
        "breakthrough_analysis": {
            "require_evidence": True,
            "min_breakthrough_count": 3,
        },
    },
    # Dynamic output type preferences (learned from feedback)
    "output_preferences": {},
}


def load_habits(user_id: str | None = None) -> dict[str, Any]:
    """Load habits from SQLite DB, merged with defaults.

    If user_id is None, loads 'default' user habits.
    """
    init_db()
    db = _get_db()
    habits = dict(_DEFAULT_HABITS)

    uid = user_id or "default"
    rows = db.execute(
        "SELECT key, value, confidence FROM habits WHERE user_id=? ORDER BY confidence DESC",
        (uid,),
    ).fetchall()

    for row in rows:
        key = row["key"]
        try:
            val = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            val = row["value"]

        # Route keys to appropriate nested sections
        if key.startswith("query_type_"):
            # query_type_成都天气查询 → output_preferences["成都天气查询"]
            sub_key = key[len("query_type_"):]
            if "output_preferences" not in habits:
                habits["output_preferences"] = {}
            habits["output_preferences"][sub_key] = val
        elif key.startswith("output_preferences."):
            sub_key = key[len("output_preferences."):]
            if "output_preferences" not in habits:
                habits["output_preferences"] = {}
            habits["output_preferences"][sub_key] = val
        elif key.startswith("processing_habits."):
            sub_key = key[len("processing_habits."):]
            if "processing_habits" not in habits:
                habits["processing_habits"] = {}
            habits["processing_habits"][sub_key] = val
        else:
            habits[key] = val

    return habits


def save_habits(updates: dict[str, Any] | None = None, user_id: str = "default") -> dict[str, Any]:
    """Save habits to SQLite, optionally merging updates first.

    Returns the full habits dict.
    """
    habits = load_habits(user_id)
    if updates:
        _deep_merge(habits, updates)

    db = _get_db()
    init_db()

    # Flatten nested dict and write each leaf as a separate habit row
    _flatten_and_save_habits(db, user_id, habits)
    db.commit()

    logger.info("[LEARNING] habits saved (%d top-level keys)", len(habits))
    return habits


def _flatten_and_save_habits(db: sqlite3.Connection, user_id: str, habits: dict, prefix: str = ""):
    """Recursively flatten habits dict into SQLite rows."""
    for key, value in habits.items():
        full_key = f"{prefix}{key}" if not prefix else key
        if key in ("output_preferences", "processing_habits") and isinstance(value, dict):
            for sub_key, sub_val in value.items():
                _write_habit_row(db, user_id, f"{full_key}.{sub_key}", sub_val)
        elif isinstance(value, (list, dict)):
            _write_habit_row(db, user_id, full_key, value)
        else:
            _write_habit_row(db, user_id, full_key, value)


def _write_habit_row(db, user_id, key, value, confidence=None):
    value_json = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    db.execute(
        """INSERT INTO habits (user_id, key, value, confidence, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(user_id, key) DO UPDATE SET
               value=excluded.value,
               confidence=excluded.confidence,
               updated_at=excluded.updated_at""",
        (user_id[:100], key[:100], value_json, float(confidence or 0.5)),
    )


def update_habits(habit_updates: dict[str, Any], user_id: str = "default") -> dict[str, Any]:
    """Convenience: merge habit_updates and persist."""
    return save_habits(habit_updates, user_id)


def get_habit(key: str, default: Any = None, user_id: str = "default") -> Any:
    """Get a single habit value from SQLite or JSON fallback.

    Checks SQLite first, then JSON file for backward compatibility.
    """
    init_db()
    db = _get_db()
    row = db.execute(
        "SELECT value FROM habits WHERE user_id=? AND key=?",
        (user_id, key),
    ).fetchone()
    if row:
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    # Fallback to JSON file for backward compat
    try:
        if os.path.exists(HABITS_PATH):
            with open(HABITS_PATH, "r", encoding="utf-8") as f:
                json_habits = json.load(f)
            if isinstance(json_habits, dict) and key in json_habits:
                return json_habits[key]
    except Exception:
        pass

    return default


def set_habit(user_id: str, key: str, value: Any, confidence: float = 0.5) -> None:
    """Store or update a habit for a user in SQLite."""
    init_db()
    db = _get_db()
    value_json = json.dumps(value, ensure_ascii=False)
    db.execute(
        """INSERT INTO habits (user_id, key, value, confidence, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(user_id, key) DO UPDATE SET
               value=excluded.value,
               confidence=excluded.confidence,
               updated_at=excluded.updated_at""",
        (user_id[:100], key[:100], value_json, float(confidence)),
    )
    db.commit()
    logger.info("[LEARNING] habit set: %s/%s (confidence=%.2f)", user_id[:20], key, confidence)


def get_global_habit(user_id: str, key: str) -> Any | None:
    """Alias for get_habit (kept for backward compat)."""
    return get_habit(key, default=None, user_id=user_id)


def format_habits_for_prompt(user_id: str = "default") -> str:
    """Format relevant habits as a short text block for planner prompts.

    Includes dynamic output preferences learned from feedback.
    """
    habits = load_habits(user_id)
    lines = ["## 用户习惯与偏好（来自历史经验）", ""]

    if habits.get("prefer_pdf"):
        lines.append("- 用户优先接收 PDF 格式的正式报告")
    if habits.get("include_mae_comparison"):
        lines.append("- 报告应包含 MAE/R² 等量化对比")
    if habits.get("preferred_language") == "zh":
        lines.append("- 使用中文输出")

    proc = habits.get("processing_habits", {})
    lit = proc.get("literature_review", {})
    if lit.get("parallel_queries"):
        lines.append(f"- 文献检索默认使用 {lit['parallel_queries']} 个并行查询")
    if lit.get("summarize_after_search"):
        lines.append("- 检索后自动进行结构化摘要提取")
    if proc.get("breakthrough_analysis", {}).get("require_evidence"):
        lines.append("- 突破方向建议须附带证据来源")

    # Dynamic output preferences (learned from user feedback)
    output_prefs = habits.get("output_preferences", {})
    if output_prefs:
        lines.append("")
        lines.append("### 动态输出偏好（基于用户反馈学习）")
        for query_type, pref in output_prefs.items():
            pref_label = {"text": "纯文字回复", "file": "生成文件", "table": "表格/CSV", "chart": "图表/图片"}.get(
                str(pref).lower(), str(pref)
            )
            lines.append(f"- 对于「{query_type}」类查询，用户偏好：{pref_label}")

    # Last failures reminder
    if habits.get("last_failures"):
        lines.append("")
        lines.append(f"- 注意：上次任务中以下步骤失败：{'；'.join(habits['last_failures'][:3])}")

    return "\n".join(lines)


def query_global_experiences(task_type: str, limit: int = 5) -> list[dict]:
    """Kept for backward compatibility.

    Now delegates to get_relevant_experiences if task_type is '*' or empty.
    """
    if not task_type or task_type == "*":
        rows = get_relevant_experiences("", limit=limit)
        return rows
    init_db()
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM experiences WHERE task_summary LIKE ? ORDER BY id DESC LIMIT ?",
        (f"%{task_type[:200]}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════
#  GROWTH
# ═════════════════════════════════════════════════════════════════════════

def record_growth(
    *,
    milestone: str,
    reflection: str = "",
    category: str = "milestone",
    user_id: str = "default",
    instance_id: str = "",
) -> int | None:
    """Record a growth milestone in the unified SQLite DB."""
    init_db()
    db = _get_db()
    cur = db.execute(
        "INSERT INTO growth (user_id, milestone, reflection, category, instance_id) VALUES (?, ?, ?, ?, ?)",
        (user_id[:100], milestone[:500], reflection[:2000], category[:50], instance_id[:64]),
    )
    db.commit()
    gid = cur.lastrowid
    logger.info("[LEARNING/GROWTH] milestone recorded: %s (%s)", milestone[:60], category)
    return gid


def record_growth_milestone(
    user_id: str,
    milestone: str,
    reflection: str = "",
    instance_id: str = "",
) -> int | None:
    """Alias kept for backward compat."""
    return record_growth(milestone=milestone, reflection=reflection, user_id=user_id, instance_id=instance_id)


def get_growth_timeline(user_id: str = "default", limit: int = 10) -> list[dict]:
    """Return growth milestones for a user, most recent first."""
    init_db()
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM growth WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def format_growth_for_prompt(user_id: str = "default", max_events: int = 3) -> str:
    """Format recent growth events as a natural-language block for planner prompts."""
    events = get_growth_timeline(user_id=user_id, limit=max_events + 1)
    if not events:
        return ""

    lines = ["## 成长记录（能力里程碑）"]
    for ev in events[:max_events]:
        milestone = str(ev.get("milestone") or "")[:120]
        category = str(ev.get("category") or "milestone")
        lines.append(f"- [{category}] {milestone}")

    lines.append("")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════
#  LEGACY CLASS (kept for backward compat, delegates to module functions)
# ═════════════════════════════════════════════════════════════════════════


class LearningManager:
    """Unified Learning Manager — delegates to SQLite-backed module functions.

    Kept for backward compatibility with existing callers.
    """

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or LEARNING_DIR).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        init_db()

    # ── Experience ──

    def record_experience(
        self,
        project_name: str,
        summary: str,
        *,
        successful_queries: list[str] | None = None,
        failed_queries: list[str] | None = None,
        lessons: str = "",
    ) -> str:
        """Record an experience entry (delegates to module function).

        Also writes to legacy file for backward compatibility.
        """
        # Write to SQLite
        record_experience(
            user_message=project_name,
            task_summary=summary,
            output_type="text",
            success=bool(not failed_queries),
            skills_used=successful_queries or [],
            instance_id="",
        )

        # Legacy file write (will be removed in future)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)[:120]
        exp_file = self.base_dir / f"{safe_name}_experience.md"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        entry = [
            f"## {timestamp}",
            "",
            f"**Project**: {project_name}",
            f"**Summary**: {summary}",
            "",
        ]
        if successful_queries:
            entry.append("**Successful queries**:")
            entry.extend(f"- `{q}`" for q in successful_queries[:10])
            entry.append("")
        if failed_queries:
            entry.append("**Failed queries**:")
            entry.extend(f"- `{q}`" for q in failed_queries[:10])
            entry.append("")
        if lessons:
            entry.append(f"**Lessons**: {lessons}")
            entry.append("")

        entry_text = "\n".join(entry) + "\n"
        try:
            with open(exp_file, "a", encoding="utf-8") as f:
                f.write(entry_text)
            logger.info("[LEARNING] experience recorded to %s", exp_file)
        except Exception as exc:
            logger.warning("[LEARNING] failed to write experience: %s", exc)

        return entry_text

    def record_task_output(
        self,
        user_message: str,
        output_type: str = "text",
        file_format: str = "",
        success: bool = True,
        skills_used: list[str] | None = None,
        instance_id: str = "",
    ) -> int | None:
        """Record the output type of a completed task.

        This is the new unified API for tracking output preferences.
        """
        return record_experience(
            user_message=user_message,
            task_summary="",
            output_type=output_type,
            file_format=file_format,
            success=success,
            skills_used=skills_used,
            instance_id=instance_id,
        )

    # ── Growth ──

    def record_growth(
        self,
        milestone: str,
        reflection: str,
        *,
        skills_learned: list[str] | None = None,
        difficulties: list[str] | None = None,
    ) -> str:
        """Record a growth milestone (delegates to module function + legacy file)."""
        # Write to SQLite
        record_growth(
            milestone=milestone,
            reflection=reflection,
            category="milestone",
        )

        # Legacy file write
        growth_file = self.base_dir / "growth_log.md"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        entry = [
            f"### {timestamp} — {milestone}",
            "",
            reflection.strip(),
            "",
        ]
        if skills_learned:
            entry.append("**Skills learned**:")
            entry.extend(f"- {s}" for s in skills_learned)
            entry.append("")
        if difficulties:
            entry.append("**Difficulties encountered**:")
            entry.extend(f"- {d}" for d in difficulties)
            entry.append("")
        entry.append("---\n")

        entry_text = "\n".join(entry)
        try:
            with open(growth_file, "a", encoding="utf-8") as f:
                f.write(entry_text)
            logger.info("[LEARNING] growth recorded: %s", milestone)
        except Exception as exc:
            logger.warning("[LEARNING] failed to write growth: %s", exc)

        return entry_text

    # ── Habits ──

    def update_habits(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Merge updates into habits and persist."""
        return update_habits(updates)

    def get_habits(self) -> dict[str, Any]:
        """Get current habits dict."""
        return load_habits()

    def format_habits_for_prompt(self) -> str:
        """Format relevant habits as a short text block for planner prompts."""
        return format_habits_for_prompt()

    # ── Project completion ──

    def record_project_completion(
        self,
        project_name: str,
        *,
        summary: str = "",
        successful_queries: list[str] | None = None,
        failed_queries: list[str] | None = None,
        lessons: str = "",
        milestone: str = "任务完成",
        reflection: str = "",
        skills_learned: list[str] | None = None,
        difficulties: list[str] | None = None,
        habit_updates: dict[str, Any] | None = None,
    ) -> None:
        """Record all learning artifacts at project completion.

        Extended to also record output type if available in summary.
        """
        # Detect output type from summary/lessons
        output_type = self._detect_output_type(lessons or summary)

        self.record_experience(
            project_name=project_name,
            summary=summary,
            successful_queries=successful_queries,
            failed_queries=failed_queries,
            lessons=lessons,
        )

        # Also record output type in the unified table
        record_experience(
            user_message=project_name,
            task_summary=summary,
            output_type=output_type,
            success=bool(not failed_queries),
            skills_used=skills_learned or [],
        )

        self.record_growth(
            milestone=milestone,
            reflection=reflection,
            skills_learned=skills_learned,
            difficulties=difficulties,
        )

        if habit_updates:
            self.update_habits(habit_updates)

    def _detect_output_type(self, text: str) -> str:
        """Detect output type from task text.

        No hardcoded keywords — returns 'text' by default.
        """
        # Simple heuristic: if text mentions files, probably file output
        # But we don't hardcode — just return 'text' and let the planner decide
        return "text"

    def save_code_template(self, code: str, purpose: str, dataset_id: str = "", metrics: dict | None = None) -> str:
        """Save a generated code script as a reusable template for future tasks."""
        templates_dir = self.base_dir / "code_templates"
        templates_dir.mkdir(parents=True, exist_ok=True)
        safe_purpose = "".join(c if c.isalnum() or c in "-_" else "_" for c in purpose)[:60]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_purpose}_{timestamp}.py"
        filepath = templates_dir / filename
        metadata = {
            "purpose": purpose,
            "dataset_id": dataset_id,
            "metrics": metrics or {},
            "created_at": datetime.now().isoformat(),
        }
        meta_path = templates_dir / f"{filename}.meta.json"
        try:
            filepath.write_text(code, encoding="utf-8")
            meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("[LEARNING] code template saved: %s (%s)", filepath, purpose)
        except Exception as exc:
            logger.warning("[LEARNING] failed to save code template: %s", exc)
        return str(filepath)


# ═════════════════════════════════════════════════════════════════════════
#  TASK EXTRACTION HELPERS
# ═════════════════════════════════════════════════════════════════════════

def extract_learning_from_task(task: Any) -> dict[str, Any]:
    """Extract learning signals from a TaskInstance or its metadata dict.

    Scans the task's working directory for ``_step_*.result.json`` files
    and extracts success/failure info from there.
    Falls back to ``task.metadata["step_results"]`` for backward compatibility.

    Returns a dict suitable for ``record_project_completion()`` keyword args.
    """
    meta = getattr(task, "metadata", {})
    if not isinstance(meta, dict):
        meta = {}

    step_results: dict[str, Any] = {}
    working_dir = getattr(task, "working_dir", "") or ""
    if working_dir and os.path.isdir(working_dir):
        try:
            for fname in sorted(os.listdir(working_dir)):
                if fname.startswith("_step_") and fname.endswith(".result.json"):
                    fpath = os.path.join(working_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict):
                            step_id = fname.replace(".result.json", "")
                            step_results[step_id] = data
                    except Exception:
                        pass
        except Exception:
            pass

    if not step_results:
        step_results = meta.get("step_results", {})
    if not isinstance(step_results, dict):
        step_results = {}

    success_factors = []
    failure_factors = []
    useful_queries = []
    skills_learned = set()

    for step_id, step_result in step_results.items():
        if not isinstance(step_result, dict):
            continue
        ok = step_result.get("ok", False)
        skill = str(step_result.get("skill") or step_result.get("event_type") or "")
        content = str(step_result.get("content") or "")
        if ok and skill:
            success_factors.append(f"{step_id}: {skill} 成功")
            skills_learned.add(skill)
            if content and len(content) > 20:
                snippet = content[:150].replace("\n", " ")
                if len(snippet) > 10:
                    useful_queries.append(snippet)
        elif not ok and skill:
            error = str(step_result.get("error") or "unknown")[:120]
            failure_factors.append(f"{step_id}: {skill} 失败 ({error})")
        elif ok and not skill:
            success_factors.append(f"{step_id}: 成功 (unknown skill)")
        elif not ok and not skill:
            failure_factors.append(f"{step_id}: 失败 ({step_result.get('error') or 'unknown'})")

    # Also scan file artifacts for fallback diagnostic info
    if not success_factors and not failure_factors and working_dir and os.path.isdir(working_dir):
        try:
            for fname in sorted(os.listdir(working_dir)):
                fpath = os.path.join(working_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                if fname in ("task_instance.json", "task_log.jsonl"):
                    continue
                if fname.startswith("_") or fname.startswith("."):
                    continue
                size = os.path.getsize(fpath)
                if size > 100:
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            head = f.read(2000).strip()
                        if head:
                            success_factors.append(f"产出文件 {fname} ({size}B)")
                    except Exception:
                        success_factors.append(f"产出文件 {fname} ({size}B)")
        except Exception:
            pass

    user_msg = str(getattr(task, "user_message", "") or meta.get("user_message") or meta.get("root_goal") or "")
    goal = user_msg

    working_dir = getattr(task, "working_dir", "") or ""
    artifacts_found = []
    if working_dir and os.path.isdir(working_dir):
        try:
            for fname in sorted(os.listdir(working_dir)):
                if fname.startswith("_") or fname in ("task_instance.json", "task_log.jsonl"):
                    continue
                fpath = os.path.join(working_dir, fname)
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 100:
                    artifacts_found.append(f"{fname} ({os.path.getsize(fpath)}B)")
        except Exception:
            pass

    summary_parts = []
    if goal:
        summary_parts.append(f"目标: {goal[:160]}")
    if success_factors:
        summary_parts.append(f"成功 {len(success_factors)} 步")
    if failure_factors:
        summary_parts.append(f"失败 {len(failure_factors)} 步")
    if artifacts_found:
        summary_parts.append(f"产出 {len(artifacts_found)} 个文件")

    summary = "; ".join(summary_parts) or "任务完成"
    reflection = "\n".join([
        f"目标: {goal[:200]}" if goal else "",
        f"成功步骤 ({len(success_factors)}): " + "; ".join(success_factors[:6]) if success_factors else "",
        f"失败步骤 ({len(failure_factors)}): " + "; ".join(failure_factors[:6]) if failure_factors else "",
        f"产出文件: " + "; ".join(artifacts_found[:6]) if artifacts_found else "",
    ])

    # Infer output type from artifacts
    output_type = "file" if artifacts_found else "text"
    file_format = ""
    if artifacts_found:
        ext = os.path.splitext(artifacts_found[0].split(" ")[0])[1].lower()
        if ext:
            file_format = ext.lstrip(".")

    habit_updates = {}
    if useful_queries:
        habit_updates["last_useful_queries"] = useful_queries[:5]
    # Only learn PDF preference from genuinely successful tasks — hallucinated
    # runs may produce .pdf files even when all core steps failed
    if artifacts_found and any(f.endswith(".pdf") for f in artifacts_found) and not failure_factors:
        habit_updates["prefer_pdf"] = True
    if failure_factors:
        habit_updates["last_failures"] = failure_factors[:5]

    return {
        "summary": summary,
        "output_type": output_type,
        "file_format": file_format,
        "success_factors": success_factors[:10],
        "failure_factors": failure_factors[:10],
        "useful_queries": useful_queries[:10],
        "milestone": "批处理任务周期完成",
        "reflection": reflection.strip(),
        "skills_learned": sorted(skills_learned),
        "habit_updates": habit_updates,
        "extra_notes": f"产出: {len(artifacts_found)} 个文件 / 成功: {len(success_factors)} 步 / 失败: {len(failure_factors)} 步",
    }


def generate_lessons_from_task(task: Any, adapter: Any = None) -> str:
    """Generate a brief lesson summary from step results using the LLM adapter."""
    step_results: list[dict[str, Any]] = []
    working_dir = getattr(task, "working_dir", "") or ""
    if working_dir and os.path.isdir(working_dir):
        try:
            for fname in sorted(os.listdir(working_dir)):
                if fname.startswith("_step_") and fname.endswith(".result.json"):
                    fpath = os.path.join(working_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict):
                            data["_step_id"] = fname
                            step_results.append(data)
                    except Exception:
                        pass
        except Exception:
            pass

    if not step_results:
        return ""

    summary_lines = []
    for sr in step_results:
        sid = sr.get("_step_id", "?")
        ok = sr.get("ok", False)
        event_type = str(sr.get("event_type") or sr.get("skill") or "?")
        content = str(sr.get("content") or "")[:200]
        error = str(sr.get("error") or "")[:200]
        status = "成功" if ok else "失败"
        line = f"{sid}: {event_type} -> {status}"
        if ok and content:
            line += f" | {content}"
        if not ok and error:
            line += f" | {error}"
        summary_lines.append(line)

    summary_text = "\n".join(summary_lines)

    if adapter is not None and hasattr(adapter, "chat") and callable(adapter.chat):
        prompt = (
            "你是 Partner 的成长记录员。以下是一次任务中所有步骤的执行记录（步骤ID、类型、成功/失败、内容摘要）。\n"
            "请根据这些记录总结 3-5 条最重要的教训（用中文），用于帮助未来的任务做得更好。\n"
            "要求：\n"
            "- 每条教训一行，以「教训」开头\n"
            "- 具体说明什么做得好、什么做得不好\n"
            "- 包含具体的数据或步骤名作为依据\n"
            "- 不要铺垫，直接输出教训\n\n"
            "步骤记录：\n"
            f"{summary_text[:3000]}\n\n"
            "输出格式：\n"
            "教训1: ...\n"
            "教训2: ...\n"
        )
        try:
            reply = adapter.chat(prompt, purpose="classify")
            if reply and len(reply) > 50:
                return reply.strip()[:2000]
        except Exception:
            pass

    ok_count = sum(1 for sr in step_results if sr.get("ok"))
    fail_count = sum(1 for sr in step_results if not sr.get("ok"))
    return f"共 {len(step_results)} 个步骤，成功 {ok_count}，失败 {fail_count}。"


# ── Legacy module-level functions (kept for backward compat) ────────────

def record_project_completion(
    project_name: str,
    *,
    summary: str = "",
    success_factors: list[str] | None = None,
    failure_factors: list[str] | None = None,
    useful_queries: list[str] | None = None,
    milestone: str = "任务完成",
    reflection: str = "",
    skills_learned: list[str] | None = None,
    habit_updates: dict[str, Any] | None = None,
    extra_notes: str = "",
) -> None:
    """Legacy module-level project completion. Delegates to LearningManager."""
    lm = LearningManager()
    lm.record_project_completion(
        project_name=project_name,
        summary=summary,
        successful_queries=useful_queries,
        failed_queries=failure_factors,
        lessons=reflection,
        milestone=milestone,
        reflection=reflection,
        skills_learned=skills_learned,
        difficulties=failure_factors,
        habit_updates=habit_updates,
    )


def merge_local_experiences_into_global(project_name: str = "") -> int:
    """Scan legacy local files and merge their content into unified SQLite DB.

    Returns count of entries merged.
    """
    import glob as glob_mod
    init_db()
    count = 0

    base = os.path.expanduser("~/.partner/learning")
    if not os.path.isdir(base):
        return 0

    if project_name:
        patterns = [os.path.join(base, project_name, "experience.md")]
    else:
        patterns = glob_mod.glob(os.path.join(base, "*", "experience.md"))

    for exp_path in patterns:
        if not os.path.isfile(exp_path):
            continue
        proj_name = os.path.basename(os.path.dirname(exp_path))
        try:
            with open(exp_path, "r", encoding="utf-8") as f:
                content = f.read(2000)
            summary = content.strip()[:500]
            if summary:
                record_experience(
                    user_message=proj_name,
                    task_summary=summary,
                    output_type="text",
                    success=True,
                    instance_id="legacy_migrate",
                )
                count += 1
        except Exception as exc:
            logger.debug("[LEARNING/GLOBAL] skip merge %s: %s", exp_path, exc)

    logger.info("[LEARNING/GLOBAL] merged %d local experiences into unified DB", count)
    return count


# ── Internal helpers ──────────────────────────────────────────────────────

def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
    """In-place deep merge of patch into base."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
