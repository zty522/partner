"""
Message Broker — persistent, prioritized message queue for cross-instance task routing.
Backed by SQLite. No external dependencies (no Redis/RabbitMQ needed).
"""
from __future__ import annotations
import json, logging, os, sqlite3, threading, time
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/.partner/queue.db")
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
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            routing_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            status TEXT DEFAULT 'pending',
            source_instance TEXT DEFAULT '',
            target_instance TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            picked_at TEXT,
            completed_at TEXT,
            error TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3
        );
        CREATE TABLE IF NOT EXISTS instances (
            instance_id TEXT PRIMARY KEY,
            capabilities TEXT DEFAULT '[]',
            load INTEGER DEFAULT 0,
            status TEXT DEFAULT 'inactive',
            last_heartbeat TEXT,
            hostname TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_msg_status ON messages(status);
        CREATE INDEX IF NOT EXISTS idx_msg_routing ON messages(routing_key);
        CREATE INDEX IF NOT EXISTS idx_msg_priority ON messages(priority);
    """)
    db.commit()

def publish(routing_key: str, payload: dict, priority: int = 5,
            source_instance: str = "", target_instance: str = "") -> str:
    """Publish a message to the queue."""
    import uuid
    init_db()
    db = _get_db()
    msg_id = str(uuid.uuid4())[:12]
    db.execute("""
        INSERT INTO messages (id, routing_key, payload, priority, source_instance, target_instance)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (msg_id, routing_key, json.dumps(payload, ensure_ascii=False),
          priority, source_instance, target_instance))
    db.commit()
    logger.info("[QUEUE] published %s routing=%s pri=%d", msg_id, routing_key, priority)
    return msg_id

def subscribe(instance_id: str, callback: Callable = None, timeout: float = 30) -> dict | None:
    """Subscribe to the next available message for an instance."""
    init_db()
    db = _get_db()
    # First, try messages addressed directly to this instance
    row = db.execute("""
        SELECT * FROM messages WHERE status='pending'
        AND (target_instance=? OR target_instance='' OR target_instance IS NULL)
        ORDER BY priority ASC, created_at ASC LIMIT 1
    """, (instance_id,)).fetchone()
    if not row:
        return None
    msg = dict(row)
    db.execute("UPDATE messages SET status='processing', picked_at=datetime('now') WHERE id=?", (msg["id"],))
    db.commit()
    if callback:
        try:
            result = callback(json.loads(msg["payload"]))
            if result:
                complete(msg["id"], result)
        except Exception as exc:
            fail(msg["id"], str(exc))
    return msg

def complete(msg_id: str, result: dict = None):
    """Mark a message as completed."""
    db = _get_db()
    db.execute("""
        UPDATE messages SET status='completed', completed_at=datetime('now'),
            payload=json_set(payload, '$.result', ?)
        WHERE id=?
    """, (json.dumps(result, ensure_ascii=False) if result else "{}", msg_id))
    db.commit()

def fail(msg_id: str, error: str = ""):
    """Mark a message as failed, retry if under max_retries."""
    db = _get_db()
    row = db.execute("SELECT retry_count, max_retries FROM messages WHERE id=?", (msg_id,)).fetchone()
    if row and row["retry_count"] < row["max_retries"]:
        db.execute("""
            UPDATE messages SET status='pending', retry_count=retry_count+1, error=?
            WHERE id=?
        """, (error[:500], msg_id))
        logger.info("[QUEUE] retrying %s (attempt %d/%d)", msg_id, row["retry_count"]+1, row["max_retries"])
    else:
        db.execute("""
            UPDATE messages SET status='failed', completed_at=datetime('now'), error=?
            WHERE id=?
        """, (error[:500], msg_id))
    db.commit()

def register_instance(instance_id: str, capabilities: list = None, hostname: str = ""):
    """Register or heartbeat an instance."""
    import socket
    init_db()
    db = _get_db()
    hostname = hostname or socket.gethostname()
    caps_json = json.dumps(capabilities or [], ensure_ascii=False)
    db.execute("""
        INSERT INTO instances (instance_id, capabilities, status, last_heartbeat, hostname)
        VALUES (?, ?, 'active', datetime('now'), ?)
        ON CONFLICT(instance_id) DO UPDATE SET
            capabilities=excluded.capabilities,
            status='active',
            last_heartbeat=datetime('now'),
            hostname=excluded.hostname
    """, (instance_id, caps_json, hostname))
    db.commit()

def heartbeat(instance_id: str, load: int = 0):
    """Instance heartbeat."""
    db = _get_db()
    db.execute("""
        UPDATE instances SET last_heartbeat=datetime('now'), status='active', load=?
        WHERE instance_id=?
    """, (load, instance_id))
    db.commit()

def mark_stale_instances(timeout_minutes: int = 2):
    """Mark instances without recent heartbeat as inactive."""
    db = _get_db()
    db.execute("""
        UPDATE instances SET status='inactive'
        WHERE status='active' AND last_heartbeat < datetime('now', ?)
    """, (f"-{timeout_minutes} minutes",))
    db.commit()
    # Re-queue messages assigned to stale instances
    stale = db.execute("""
        SELECT instance_id FROM instances WHERE status='inactive'
    """).fetchall()
    for s in stale:
        db.execute("""
            UPDATE messages SET status='pending', target_instance=''
            WHERE target_instance=? AND status='processing'
        """, (s["instance_id"],))
    db.commit()

def get_instance_loads() -> list[dict]:
    """Get current instance loads for routing decisions."""
    init_db()
    db = _get_db()
    mark_stale_instances()
    rows = db.execute("""
        SELECT instance_id, capabilities, load, status, last_heartbeat
        FROM instances WHERE status='active'
        ORDER BY load ASC
    """).fetchall()
    return [dict(r) for r in rows]

def get_queue_depth() -> dict:
    """Get queue statistics."""
    init_db()
    db = _get_db()
    stats = db.execute("""
        SELECT status, COUNT(*) as count FROM messages GROUP BY status
    """).fetchall()
    return {r["status"]: r["count"] for r in stats}
