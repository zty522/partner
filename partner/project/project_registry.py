"""
Project Registry — global project management across instances.
"""
from __future__ import annotations
import json, logging, os, re, sqlite3, shutil, threading
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/.partner/projects.db")
PROJECTS_DIR = os.path.expanduser("~/.partner/projects")
_local = threading.local()

def _get_db() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn

def init_db():
    db = _get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id TEXT DEFAULT '',
            description TEXT DEFAULT '',
            is_public INTEGER DEFAULT 0,
            tags TEXT DEFAULT '[]',
            workspace_path TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS project_shares (
            project_id TEXT NOT NULL,
            shared_with TEXT NOT NULL,
            permission TEXT DEFAULT 'read',
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (project_id, shared_with)
        );
        CREATE TABLE IF NOT EXISTS project_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT DEFAULT '',
            size_bytes INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_proj_owner ON projects(owner_id);
        CREATE INDEX IF NOT EXISTS idx_proj_public ON projects(is_public);
    """)
    db.commit()

def create_project(name: str, owner_id: str = "", description: str = "",
                   is_public: bool = False, tags: list = None) -> str:
    """Create a new project and return its ID."""
    import uuid
    init_db()
    db = _get_db()
    proj_id = str(uuid.uuid4())[:12]
    safe_name = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff.-]+", "_", name).strip("_") or "project"
    proj_dir = os.path.join(PROJECTS_DIR, f"{safe_name}_{proj_id}")
    os.makedirs(proj_dir, exist_ok=True)
    db.execute("""
        INSERT INTO projects (id, name, owner_id, description, is_public, tags, workspace_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (proj_id, name[:200], owner_id[:100], description[:500],
          int(is_public), json.dumps(tags or [], ensure_ascii=False), proj_dir))
    db.commit()
    logger.info("[PROJECT] created %s (%s) public=%s", proj_id, name[:40], is_public)
    return proj_id

def get_project(project_id: str) -> dict | None:
    init_db()
    db = _get_db()
    row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return dict(row) if row else None

def list_public_projects(tag: str = None) -> list[dict]:
    init_db()
    db = _get_db()
    if tag:
        rows = db.execute("""
            SELECT * FROM projects WHERE is_public=1 AND tags LIKE ?
            ORDER BY updated_at DESC
        """, (f'%"{tag}"%',)).fetchall()
    else:
        rows = db.execute("""
            SELECT * FROM projects WHERE is_public=1 ORDER BY updated_at DESC
        """).fetchall()
    return [dict(r) for r in rows]

def share_project(project_id: str, user_id: str, permission: str = "read") -> bool:
    init_db()
    db = _get_db()
    try:
        db.execute("""
            INSERT INTO project_shares (project_id, shared_with, permission)
            VALUES (?, ?, ?) ON CONFLICT(project_id, shared_with) DO UPDATE SET permission=excluded.permission
        """, (project_id, user_id, permission))
        db.commit()
        return True
    except Exception as exc:
        logger.warning("[PROJECT] share failed: %s", exc)
        return False

def get_project_workspace(project_id: str, instance_workspace: str = "") -> str | None:
    """Get a project's workspace path. If instance_workspace is given, create a symlink."""
    proj = get_project(project_id)
    if not proj:
        return None
    ws = proj["workspace_path"]
    if instance_workspace and ws:
        link = os.path.join(instance_workspace, "linked_projects", proj["name"])
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if not os.path.exists(link):
            try:
                os.symlink(ws, link)
            except (OSError, AttributeError):
                pass
    return ws
