"""Layered Memory System for Partner.

Per the Loop+Harness survey (Tsinghua 2026), memory has three layers:

  Representation Layer — What to store
    - Raw logs: complete execution transcripts
    - Episodic traces: structured summaries of key events
    - Semantic summaries: compressed, queryable abstractions

  Operation Layer — How to manage
    - Write: when and what to record
    - Retrieve: similarity search with recency boost
    - Compress: merge redundant entries, discard noise
    - Merge: combine related memories into higher-level abstractions
    - Update: refresh stale entries with new information

  Evolution Layer — Learning to manage better
    - Content evolution: agent writes new experiences, compresses old ones
    - Mechanism evolution: improve how memories are organized and retrieved
    - Strategy evolution: learn when to write, when to retrieve, when to forget
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MemoryEntry:
    """A single memory entry at any representation level."""

    id: str
    content: str
    level: str  # "raw" | "episodic" | "semantic"
    topic: str = ""
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5  # 0.0–1.0
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    source_session: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def age_days(self) -> float:
        return (time.time() - self.created_at) / 86400

    def staleness(self) -> float:
        """Days since last access, 0 = just accessed."""
        return (time.time() - self.last_accessed) / 86400


# ---------------------------------------------------------------------------
# Representation Layer
# ---------------------------------------------------------------------------

class MemoryRepresentation:
    """Three levels of memory representation."""

    def __init__(self, db_path: str = ""):
        self._db_path = db_path
        self._in_memory: list[MemoryEntry] = []  # fallback when no db
        self._init_db()

    def _init_db(self) -> None:
        if not self._db_path:
            return
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS layered_memory (
                id TEXT PRIMARY KEY,
                level TEXT NOT NULL,
                topic TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                importance REAL DEFAULT 0.5,
                content TEXT NOT NULL,
                created_at REAL,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                source_session TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_level
            ON layered_memory(level)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_topic
            ON layered_memory(topic)
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_raw(self, content: str, topic: str = "", **kwargs: Any) -> str:
        """Store a raw log entry."""
        return self._store(level="raw", content=content, topic=topic, **kwargs)

    def write_episodic(self, content: str, topic: str = "", **kwargs: Any) -> str:
        """Store an episodic (structured summary) entry."""
        return self._store(level="episodic", content=content, topic=topic, **kwargs)

    def write_semantic(self, content: str, topic: str = "", **kwargs: Any) -> str:
        """Store a semantic (compressed, queryable) entry."""
        return self._store(level="semantic", content=content, topic=topic, **kwargs)

    def _store(self, level: str, content: str, topic: str = "", **kwargs: Any) -> str:
        entry_id = f"{level}_{int(time.time() * 1000)}_{hash(content) & 0xFFFF:04x}"
        entry = MemoryEntry(
            id=entry_id,
            level=level,
            content=content,
            topic=topic,
            tags=kwargs.get("tags", []),
            importance=kwargs.get("importance", 0.5),
            source_session=kwargs.get("source_session", ""),
            metadata=kwargs.get("metadata", {}),
        )

        # Always store in-memory as fallback
        self._in_memory.append(entry)

        if self._db_path:
            try:
                conn = sqlite3.connect(self._db_path)
                conn.execute(
                    """INSERT OR REPLACE INTO layered_memory
                       (id, level, topic, tags, importance, content, created_at,
                        last_accessed, access_count, source_session, metadata)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        entry.id, entry.level, entry.topic,
                        json.dumps(entry.tags), entry.importance, entry.content,
                        entry.created_at, entry.last_accessed, entry.access_count,
                        entry.source_session, json.dumps(entry.metadata),
                    ),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("Failed to store memory '%s': %s", entry_id, e)

        return entry_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def retrieve(
        self,
        topic: str = "",
        level: str | None = None,
        limit: int = 20,
        min_importance: float = 0.0,
    ) -> list[MemoryEntry]:
        """Retrieve memories by topic and optional level filter."""
        if self._db_path:
            return self._retrieve_from_db(topic, level, limit, min_importance)
        return self._retrieve_from_memory(topic, level, limit, min_importance)

    def _retrieve_from_db(
        self, topic: str, level: str | None, limit: int, min_importance: float
    ) -> list[MemoryEntry]:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row

            query = "SELECT * FROM layered_memory WHERE importance >= ?"
            params: list[Any] = [min_importance]

            if topic:
                query += " AND topic LIKE ?"
                params.append(f"%{topic}%")
            if level:
                query += " AND level = ?"
                params.append(level)

            query += " ORDER BY importance DESC, last_accessed DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(query, params)
            results = [self._row_to_entry(row) for row in cur.fetchall()]

            # Update access timestamps
            for entry in results:
                conn.execute(
                    "UPDATE layered_memory SET last_accessed=?, access_count=access_count+1 WHERE id=?",
                    (time.time(), entry.id),
                )
            conn.commit()
            conn.close()

            return results
        except Exception as e:
            logger.error("Memory retrieve failed: %s", e)
            return []

    def _retrieve_from_memory(
        self, topic: str, level: str | None, limit: int, min_importance: float
    ) -> list[MemoryEntry]:
        """Retrieve from in-memory list when no database is configured."""
        results = [
            e for e in self._in_memory
            if e.importance >= min_importance
        ]
        if topic:
            results = [e for e in results if topic.lower() in e.topic.lower()]
        if level:
            results = [e for e in results if e.level == level]
        # Sort by importance desc, last_accessed desc
        results.sort(key=lambda e: (e.importance, e.last_accessed), reverse=True)
        return results[:limit]

    def retrieve_semantic(self, topic: str = "", limit: int = 10) -> list[MemoryEntry]:
        """Retrieve only semantic-level memories (fast, compressed)."""
        return self.retrieve(topic=topic, level="semantic", limit=limit)

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            level=row["level"],
            topic=row["topic"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            importance=row["importance"],
            content=row["content"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            access_count=row["access_count"],
            source_session=row["source_session"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )


# ---------------------------------------------------------------------------
# Operation Layer
# ---------------------------------------------------------------------------

class MemoryOperations:
    """CRUD + compress + merge operations on the memory store."""

    def __init__(self, store: MemoryRepresentation):
        self._store = store

    def compress(self, topic: str, max_raw: int = 50) -> int:
        """Compress raw entries for a topic into episodic summaries.

        When raw entries exceed max_raw, merge the oldest ones into a single
        episodic summary and remove the originals. Returns number compressed.
        """
        raw_entries = self._store.retrieve(topic=topic, level="raw", limit=200)
        if len(raw_entries) <= max_raw:
            return 0

        # Sort by age (oldest first)
        raw_entries.sort(key=lambda e: e.created_at)
        to_compress = raw_entries[: len(raw_entries) - max_raw]

        # Build episodic summary
        summary_parts = [f"Compressed {len(to_compress)} raw entries for '{topic}':"]
        for e in to_compress[:20]:  # Cap at 20 entries in summary
            summary_parts.append(f"  [{datetime.fromtimestamp(e.created_at).isoformat()[:19]}] {e.content[:200]}")

        self._store.write_episodic(
            content="\n".join(summary_parts),
            topic=topic,
            importance=min(0.8, max(e.importance for e in to_compress) + 0.1),
            tags=["compressed", f"n={len(to_compress)}"],
        )

        # Delete compressed raw entries
        if self._store._db_path:
            try:
                conn = sqlite3.connect(self._store._db_path)
                ids_to_delete = [(e.id,) for e in to_compress]
                conn.executemany("DELETE FROM layered_memory WHERE id=?", ids_to_delete)
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("Failed to delete compressed entries: %s", e)

        return len(to_compress)

    def merge(self, topic: str, min_similarity: float = 0.7) -> int:
        """Merge semantically similar entries for a topic.

        Simple keyword-overlap-based merge. Returns number of merges performed.
        """
        entries = self._store.retrieve(topic=topic, limit=100)
        if len(entries) < 2:
            return 0

        merged = 0
        merged_ids: set[str] = set()

        for i, e1 in enumerate(entries):
            if e1.id in merged_ids:
                continue
            for e2 in entries[i + 1 :]:
                if e2.id in merged_ids:
                    continue
                sim = self._keyword_similarity(e1.content, e2.content)
                if sim >= min_similarity:
                    # Merge e2 into e1
                    self._store.write_semantic(
                        content=f"[MERGED] {e1.content}\n\n(Similar: {e2.content[:200]})",
                        topic=topic,
                        importance=max(e1.importance, e2.importance),
                        tags=list(set(e1.tags + e2.tags + ["merged"])),
                    )
                    merged_ids.add(e1.id)
                    merged_ids.add(e2.id)
                    merged += 1

        # Delete merged entries
        if merged_ids and self._store._db_path:
            try:
                conn = sqlite3.connect(self._store._db_path)
                conn.executemany(
                    "DELETE FROM layered_memory WHERE id=?",
                    [(eid,) for eid in merged_ids],
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("Failed to delete merged entries: %s", e)

        return merged

    def update_importance(self, memory_id: str, delta: float) -> bool:
        """Adjust importance of a memory entry (positive = more important)."""
        if not self._store._db_path:
            return False
        try:
            conn = sqlite3.connect(self._store._db_path)
            conn.execute(
                "UPDATE layered_memory SET importance = MAX(0, MIN(1, importance + ?)) WHERE id=?",
                (delta, memory_id),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error("Failed to update importance: %s", e)
            return False

    @staticmethod
    def _keyword_similarity(a: str, b: str) -> float:
        """Simple keyword overlap similarity."""
        wa = set(a.lower().split())
        wb = set(b.lower().split())
        if not wa or not wb:
            return 0.0
        intersection = wa & wb
        return len(intersection) / min(len(wa), len(wb))


# ---------------------------------------------------------------------------
# Evolution Layer
# ---------------------------------------------------------------------------

class MemoryEvolution:
    """Learn to manage memory better over time.

    Tracks:
      - Write strategy: when does writing new memories add value?
      - Retrieve strategy: which retrieval pattern yields best results?
      - Forget strategy: when should old memories be pruned?
    """

    def __init__(self, store: MemoryRepresentation, ops: MemoryOperations):
        self._store = store
        self._ops = ops
        self._strategy_stats: dict[str, dict[str, float]] = defaultdict(lambda: {"writes": 0, "retrievals": 0, "forgets": 0, "hits": 0})

    def record_write(self, topic: str) -> None:
        self._strategy_stats[topic]["writes"] += 1

    def record_retrieval(self, topic: str, found: bool) -> None:
        stats = self._strategy_stats[topic]
        stats["retrievals"] += 1
        if found:
            stats["hits"] += 1

    def should_forget(self, topic: str, max_age_days: int = 90, max_count: int = 200) -> int:
        """Recommend how many entries to forget for a topic.

        Returns 0 if retention is fine, or the number of entries to prune.
        """
        entries = self._store.retrieve(topic=topic, limit=500)
        if not entries:
            return 0

        to_forget = 0
        for e in entries:
            if e.age_days() > max_age_days and e.importance < 0.3:
                to_forget += 1

        if len(entries) > max_count:
            # Sort by (low importance, old) and count excess
            excess = sorted(entries, key=lambda e: (e.importance, -e.last_accessed))
            to_forget += len(entries) - max_count

        return min(to_forget, len(entries))

    def prune(self, topic: str, max_age_days: int = 90, max_count: int = 200) -> int:
        """Execute forgetting: remove low-value entries. Returns count pruned."""
        to_forget = self.should_forget(topic, max_age_days, max_count)
        if to_forget <= 0:
            return 0

        if not self._store._db_path:
            return 0

        try:
            conn = sqlite3.connect(self._store._db_path)
            conn.execute(
                """DELETE FROM layered_memory WHERE id IN (
                    SELECT id FROM layered_memory
                    WHERE topic LIKE ? AND importance < 0.3
                    ORDER BY importance ASC, last_accessed ASC
                    LIMIT ?
                )""",
                (f"%{topic}%", to_forget),
            )
            deleted = conn.total_changes
            conn.commit()
            conn.close()
            self._strategy_stats[topic]["forgets"] += deleted
            logger.info("Pruned %d low-value memories for topic '%s'", deleted, topic)
            return deleted
        except Exception as e:
            logger.error("Memory pruning failed for '%s': %s", topic, e)
            return 0


# ---------------------------------------------------------------------------
# Unified Interface
# ---------------------------------------------------------------------------

class LayeredMemoryStore:
    """Unified three-layer memory system.

    Usage::

        store = LayeredMemoryStore(db_path="/path/to/memory.db")
        store.remember("Found that scanpy 1.10 has new API", topic="scanpy")
        results = store.recall("scanpy", level="semantic")
        store.optimize()  # Run compress + merge + prune cycle
    """

    def __init__(self, db_path: str = ""):
        self.representation = MemoryRepresentation(db_path)
        self.operations = MemoryOperations(self.representation)
        self.evolution = MemoryEvolution(self.representation, self.operations)

    def remember(
        self,
        content: str,
        topic: str = "",
        level: str = "episodic",
        importance: float = 0.5,
        **kwargs: Any,
    ) -> str:
        """Write a memory at the given representation level."""
        self.evolution.record_write(topic)
        if level == "raw":
            return self.representation.write_raw(content, topic=topic, importance=importance, **kwargs)
        elif level == "semantic":
            return self.representation.write_semantic(content, topic=topic, importance=importance, **kwargs)
        else:
            return self.representation.write_episodic(content, topic=topic, importance=importance, **kwargs)

    def recall(
        self,
        topic: str,
        level: str | None = None,
        limit: int = 20,
    ) -> list[MemoryEntry]:
        """Retrieve memories, tracking the retrieval for evolution."""
        results = self.representation.retrieve(topic=topic, level=level, limit=limit)
        self.evolution.record_retrieval(topic, len(results) > 0)
        return results

    def optimize(self, topic: str = "") -> dict[str, int]:
        """Run the full memory optimization cycle: compress, merge, prune."""
        result: dict[str, int] = {}

        if topic:
            topics = [topic]
        else:
            # Get all topics
            entries = self.representation.retrieve(limit=500)
            topics = list(set(e.topic for e in entries if e.topic))

        for t in topics:
            compressed = self.operations.compress(t)
            merged = self.operations.merge(t)
            pruned = self.evolution.prune(t)
            total = compressed + merged + pruned
            if total > 0:
                result[t] = total
                logger.info(
                    "Memory optimization for '%s': %d compressed, %d merged, %d pruned",
                    t, compressed, merged, pruned,
                )

        return result

    def stats(self) -> dict[str, Any]:
        """Return memory system statistics."""
        entries = self.representation.retrieve(limit=1000)
        by_level: dict[str, int] = defaultdict(int)
        by_topic: dict[str, int] = defaultdict(int)
        for e in entries:
            by_level[e.level] += 1
            if e.topic:
                by_topic[e.topic] += 1

        return {
            "total_entries": len(entries),
            "by_level": dict(by_level),
            "by_topic": dict(sorted(by_topic.items(), key=lambda x: -x[1])[:20]),
            "strategy_stats": dict(self.evolution._strategy_stats),
        }


# ---------------------------------------------------------------------------
# Re-export MemoryManager for backward compatibility
# ---------------------------------------------------------------------------
from .memory_manager import MemoryManager  # noqa: E402, F401
