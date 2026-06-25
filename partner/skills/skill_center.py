"""
Skill Center — centralized skill registry with version management.
All instances share the same SQLite database for consistent skill definitions.
"""
from __future__ import annotations
import json, logging, os, re, time, sqlite3, threading
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/.partner/skills_registry.db")
_local = threading.local()

def _get_db() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn

def init_db():
    """Create tables if they don't exist."""
    db = _get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS skills (
            name TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT '1.0.0',
            description TEXT DEFAULT '',
            command_template TEXT DEFAULT '',
            parameters TEXT DEFAULT '{}',
            allowed_agents TEXT DEFAULT '["hermes"]',
            category TEXT DEFAULT 'general',
            dependencies TEXT DEFAULT '[]',
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (name, version)
        );
        CREATE TABLE IF NOT EXISTS skill_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL,
            instance_id TEXT NOT NULL,
            user_message TEXT DEFAULT '',
            success INTEGER DEFAULT 1,
            duration_ms INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            timestamp TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS instance_skills (
            instance_id TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            version TEXT NOT NULL,
            synced_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (instance_id, skill_name)
        );
        CREATE INDEX IF NOT EXISTS idx_skill_usage_skill ON skill_usage(skill_name);
        CREATE INDEX IF NOT EXISTS idx_skill_usage_instance ON skill_usage(instance_id);
    """)
    db.commit()

def register_skill(name: str, version: str = "1.0.0", description: str = "",
                   command_template: str = "", parameters: dict = None,
                   allowed_agents: list = None, category: str = "general",
                   dependencies: list = None) -> bool:
    """Register or update a skill definition."""
    init_db()
    db = _get_db()
    params_json = json.dumps(parameters or {}, ensure_ascii=False)
    agents_json = json.dumps(allowed_agents or ["hermes"], ensure_ascii=False)
    deps_json = json.dumps(dependencies or [], ensure_ascii=False)
    try:
        db.execute("""
            INSERT INTO skills (name, version, description, command_template,
                parameters, allowed_agents, category, dependencies, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(name, version) DO UPDATE SET
                description=excluded.description,
                command_template=excluded.command_template,
                parameters=excluded.parameters,
                allowed_agents=excluded.allowed_agents,
                category=excluded.category,
                dependencies=excluded.dependencies,
                updated_at=excluded.updated_at
        """, (name, version, description, command_template,
              params_json, agents_json, category, deps_json))
        db.commit()
        logger.info("[SKILL_CENTER] registered %s v%s (%s)", name, version, category)
        return True
    except Exception as exc:
        logger.error("[SKILL_CENTER] register failed: %s", exc)
        return False

def get_skill(name: str, version: str = None) -> dict | None:
    """Get skill definition. Returns latest if version not specified."""
    init_db()
    db = _get_db()
    if version:
        row = db.execute("SELECT * FROM skills WHERE name=? AND version=?", (name, version)).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM skills WHERE name=? ORDER BY version DESC LIMIT 1", (name,)
        ).fetchone()
    if not row:
        return None
    return dict(row)

def list_skills(category: str = None, enabled_only: bool = True) -> list[dict]:
    """List all skills, optionally filtered by category."""
    init_db()
    db = _get_db()
    query = "SELECT * FROM skills WHERE 1=1"
    params = []
    if enabled_only:
        query += " AND enabled=1"
    if category:
        query += " AND category=?"
        params.append(category)
    query += " ORDER BY name, version DESC"
    rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]

def sync_skills_to_instance(instance_id: str, skills: list[str] = None) -> int:
    """Sync skills to an instance. Returns count of synced skills."""
    init_db()
    db = _get_db()
    all_skills = list_skills()
    count = 0
    for sk in all_skills:
        if skills and sk["name"] not in skills:
            continue
        db.execute("""
            INSERT INTO instance_skills (instance_id, skill_name, version, synced_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(instance_id, skill_name) DO UPDATE SET
                version=excluded.version, synced_at=excluded.synced_at
        """, (instance_id, sk["name"], sk["version"]))
        count += 1
    db.commit()
    logger.info("[SKILL_CENTER] synced %d skills to instance %s", count, instance_id)
    return count

def get_instance_skills(instance_id: str) -> list[dict]:
    """Get skills synced to a specific instance."""
    init_db()
    db = _get_db()
    rows = db.execute("""
        SELECT s.* FROM skills s
        JOIN instance_skills i ON s.name=i.skill_name AND s.version=i.version
        WHERE i.instance_id=? AND s.enabled=1
        ORDER BY s.name
    """, (instance_id,)).fetchall()
    return [dict(r) for r in rows]

def check_dependencies(skill_name: str) -> list[str]:
    """Check if a skill's dependencies are met. Returns missing deps."""
    sk = get_skill(skill_name)
    if not sk:
        return ["skill not found"]
    deps = json.loads(sk.get("dependencies", "[]"))
    missing = []
    for dep in deps:
        dep_name = dep if isinstance(dep, str) else dep.get("name", "")
        dep_ver = "" if isinstance(dep, str) else dep.get("version")
        if not get_skill(dep_name, dep_ver):
            missing.append(f"{dep_name} (required by {skill_name})")
    return missing

def record_usage(skill_name: str, instance_id: str, user_message: str = "",
                 success: bool = True, duration_ms: int = 0, error: str = ""):
    """Record skill usage for analytics."""
    init_db()
    db = _get_db()
    db.execute("""
        INSERT INTO skill_usage (skill_name, instance_id, user_message, success, duration_ms, error)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (skill_name, instance_id, user_message[:500], int(success), duration_ms, error[:500]))
    db.commit()

def get_skill_stats(skill_name: str = None) -> list[dict]:
    """Get usage statistics for skills."""
    init_db()
    db = _get_db()
    if skill_name:
        rows = db.execute("""
            SELECT skill_name, COUNT(*) as calls, SUM(success) as successes,
                   AVG(duration_ms) as avg_ms
            FROM skill_usage WHERE skill_name=? GROUP BY skill_name
        """, (skill_name,)).fetchall()
    else:
        rows = db.execute("""
            SELECT skill_name, COUNT(*) as calls, SUM(success) as successes,
                   AVG(duration_ms) as avg_ms
            FROM skill_usage GROUP BY skill_name ORDER BY calls DESC
        """).fetchall()
    return [dict(r) for r in rows]

def export_registry(path: str = None) -> str:
    """Export full registry as JSON."""
    init_db()
    db = _get_db()
    rows = db.execute("SELECT * FROM skills ORDER BY name, version").fetchall()
    data = {"skills": [dict(r) for r in rows], "exported_at": datetime.now().isoformat()}
    out = json.dumps(data, ensure_ascii=False, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
    return out

def init_default_skills():
    """Register the built-in default skills."""
    skills = [
        {
            "name": "call_agent_skill",
            "version": "1.0.0",
            "description": "Primary external agent call — invokes another agent instance for task execution or delegation.",
            "command_template": "call_agent(agent={agent_name}, task={task})",
            "parameters": {"agent_name": "str", "task": "str"},
            "allowed_agents": ["hermes", "coder", "researcher"],
            "category": "agent",
            "dependencies": [],
        },
        {
            "name": "atomic_write_artifact",
            "version": "1.0.0",
            "description": "Atomic file writing — writes content to a file path with safe overwrite semantics.",
            "command_template": "write_file(path={path}, content={content})",
            "parameters": {"path": "str", "content": "str"},
            "allowed_agents": ["hermes", "coder"],
            "category": "utility",
            "dependencies": [],
        },
        {
            "name": "smart_llm_structured_action",
            "version": "1.0.0",
            "description": "LLM reasoning and structured action planning — uses the model to decompose tasks and emit structured actions.",
            "command_template": "llm_reason(prompt={prompt}, schema={schema})",
            "parameters": {"prompt": "str", "schema": "dict"},
            "allowed_agents": ["hermes"],
            "category": "reasoning",
            "dependencies": [],
        },
        {
            "name": "web_search",
            "version": "1.0.0",
            "description": "Web search capability — queries a search engine and returns results.",
            "command_template": "web_search(query={query}, max_results={max_results})",
            "parameters": {"query": "str", "max_results": "int"},
            "allowed_agents": ["hermes", "researcher"],
            "category": "search",
            "dependencies": [],
        },
        {
            "name": "stock_analysis",
            "version": "1.0.0",
            "description": "Stock market analysis — retrieves and analyzes stock data for given tickers.",
            "command_template": "stock_analysis(ticker={ticker}, period={period})",
            "parameters": {"ticker": "str", "period": "str"},
            "allowed_agents": ["hermes", "analyst"],
            "category": "finance",
            "dependencies": ["web_search"],
        },
        {
            "name": "weather_query",
            "version": "1.0.0",
            "description": "Weather data query — retrieves current weather and forecasts for a location.",
            "command_template": "weather_query(location={location}, days={days})",
            "parameters": {"location": "str", "days": "int"},
            "allowed_agents": ["hermes"],
            "category": "data",
            "dependencies": ["web_search"],
        },
        {
            "name": "literature_review",
            "version": "1.0.0",
            "description": "Academic literature review — searches and summarizes scholarly articles on a topic.",
            "command_template": "literature_review(topic={topic}, max_sources={max_sources})",
            "parameters": {"topic": "str", "max_sources": "int"},
            "allowed_agents": ["hermes", "researcher"],
            "category": "research",
            "dependencies": ["web_search"],
        },
        {
            "name": "code_generation",
            "version": "1.0.0",
            "description": "Python/R code generation — generates executable code from a natural language specification.",
            "command_template": "generate_code(language={language}, specification={specification})",
            "parameters": {"language": "str", "specification": "str"},
            "allowed_agents": ["hermes", "coder"],
            "category": "development",
            "dependencies": [],
        },
        {
            "name": "data_visualization",
            "version": "1.0.0",
            "description": "Chart/plot generation — creates data visualizations from provided datasets.",
            "command_template": "visualize(data={data}, chart_type={chart_type}, title={title})",
            "parameters": {"data": "list", "chart_type": "str", "title": "str"},
            "allowed_agents": ["hermes", "analyst"],
            "category": "visualization",
            "dependencies": ["code_generation"],
        },
    ]
    count = 0
    for sk in skills:
        if register_skill(**sk):
            count += 1
    logger.info("[SKILL_CENTER] registered %d default skills", count)
    return count
