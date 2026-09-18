"""DocumentRepository: index docs/catalog.yaml entries plus per-file sections.

Stores document metadata (id, path, title, tier, authority, tags,
deprecated, applicability) and Markdown section offsets so the consumer
can request specific sections instead of reading the entire file.

Source of truth: docs/catalog.yaml (read once, kept in sync).
Markdown sections: derived from `#`/`##`/`###` heading scans; offset
table covers line ranges so we can fetch a slice, not the whole file.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import yaml

from .runtime_storage import workspace_dir
from .sqlite_base import get_connection

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id        TEXT PRIMARY KEY,
    path          TEXT NOT NULL,
    title         TEXT,
    tier          TEXT,
    authority     TEXT,
    tags_json     TEXT,
    applicability TEXT,
    deprecated    INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT,
    content_sha   TEXT,
    content_bytes INTEGER NOT NULL DEFAULT 0,
    source_uri    TEXT,
    parsed_at     REAL
);

CREATE INDEX IF NOT EXISTS idx_docs_authority
    ON documents(authority, deprecated);
CREATE INDEX IF NOT EXISTS idx_docs_tier
    ON documents(tier);

CREATE TABLE IF NOT EXISTS sections (
    doc_id        TEXT NOT NULL,
    section_id    TEXT NOT NULL,
    heading_level INTEGER NOT NULL,
    heading       TEXT NOT NULL,
    start_line    INTEGER NOT NULL,
    end_line      INTEGER NOT NULL,
    byte_offset   INTEGER NOT NULL,
    byte_length   INTEGER NOT NULL,
    sha256        TEXT,
    keywords      TEXT,
    PRIMARY KEY (doc_id, section_id)
);

CREATE INDEX IF NOT EXISTS idx_sections_heading
    ON sections(heading);

CREATE TABLE IF NOT EXISTS doc_watermark (
    doc_id     TEXT PRIMARY KEY,
    parsed_at  REAL,
    sha256     TEXT,
    section_count INTEGER NOT NULL DEFAULT 0
);
"""


def init(repo_root: Path, catalog_path: Path) -> "DocumentRepository":
    ws_dir = workspace_dir(repo_root)
    db_path = ws_dir / "documents.db"
    repo = DocumentRepository(db_path, catalog_path, repo_root=repo_root)
    repo._ensure_schema()
    return repo


class DocumentRepository:
    def __init__(self, db_path: Path, catalog_path: Path, *, repo_root: Path | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_path = Path(catalog_path)
        self.repo_root = Path(repo_root) if repo_root else None
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        conn = get_connection(self.db_path)
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self._schema_ready = True

    def ingest_catalog(self) -> int:
        self._ensure_schema()
        if not self.catalog_path.exists():
            return 0
        with self.catalog_path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        documents = data.get("documents") or []
        now = time.time()
        conn = get_connection(self.db_path)
        count = 0
        for entry in documents:
            doc_id = str(entry.get("id") or "")
            if not doc_id:
                continue
            conn.execute(
                """INSERT INTO documents
                (doc_id, path, title, tier, authority, tags_json, applicability,
                 deprecated, updated_at, source_uri, parsed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    path = excluded.path,
                    title = excluded.title,
                    tier = excluded.tier,
                    authority = excluded.authority,
                    tags_json = excluded.tags_json,
                    applicability = excluded.applicability,
                    deprecated = excluded.deprecated,
                    updated_at = excluded.updated_at,
                    source_uri = excluded.source_uri,
                    parsed_at = excluded.parsed_at""",
                (
                    doc_id,
                    str(entry.get("path") or ""),
                    str(entry.get("title") or ""),
                    str(entry.get("tier") or ""),
                    str(entry.get("authority") or ""),
                    json.dumps(entry.get("tags") or [], ensure_ascii=False),
                    str(entry.get("applicability") or ""),
                    1 if entry.get("deprecated") else 0,
                    str(entry.get("updated_at") or ""),
                    str(entry.get("source_uri") or ""),
                    now,
                ),
            )
            count += 1
        return count

    def ingest_markdown(self, doc_id: str, path: Path, *,
                        force: bool = False) -> dict[str, Any]:
        self._ensure_schema()
        if not path.exists():
            return {"sections": 0, "skipped": "missing"}
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        now = time.time()
        conn = get_connection(self.db_path)
        row = conn.execute(
            "SELECT sha256, section_count FROM doc_watermark WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if not force and row is not None and row["sha256"] == sha and int(row["section_count"]) > 0:
            return {"sections": int(row["section_count"]), "skipped": "unchanged"}
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        seen_ids: set[str] = set()
        for idx, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if not stripped.startswith("#"):
                continue
            level = 0
            while level < len(stripped) and stripped[level] == "#":
                level += 1
            if level > 6:
                continue
            if current is not None:
                current["end_line"] = idx - 1
                sections.append(current)
            heading_text = stripped[level:].strip()
            base_slug = _slug(heading_text) or f"section-{len(sections)+1}"
            slug = base_slug
            n = 2
            while slug in seen_ids:
                slug = f"{base_slug}-{n}"
                n += 1
            seen_ids.add(slug)
            current = {
                "section_id": slug,
                "heading_level": level,
                "heading": heading_text,
                "start_line": idx,
            }
        if current is not None:
            current["end_line"] = len(lines)
            sections.append(current)
        conn.execute("DELETE FROM sections WHERE doc_id = ?", (doc_id,))
        for sec in sections:
            byte_offset = sum(
                len(line.encode("utf-8")) + 1
                for line in lines[: sec["start_line"] - 1]
            )
            byte_length = sum(
                len(line.encode("utf-8")) + 1
                for line in lines[sec["start_line"] - 1: sec["end_line"]]
            )
            keywords = _extract_keywords(sec["heading"])
            conn.execute(
                """INSERT INTO sections
                (doc_id, section_id, heading_level, heading, start_line, end_line,
                 byte_offset, byte_length, sha256, keywords)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id, sec["section_id"], sec["heading_level"], sec["heading"],
                    sec["start_line"], sec["end_line"],
                    byte_offset, byte_length,
                    sha, keywords,
                ),
            )
        conn.execute(
            """INSERT INTO documents
            (doc_id, path, title, tier, authority, tags_json, applicability,
             deprecated, updated_at, source_uri, parsed_at, content_sha, content_bytes)
            VALUES (?, ?, '', '', '', '[]', '', 0, '', '', ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                parsed_at = excluded.parsed_at,
                content_sha = excluded.content_sha,
                content_bytes = excluded.content_bytes""",
            (doc_id, str(path), now, sha, len(raw)),
        )
        conn.execute(
            """INSERT INTO doc_watermark(doc_id, parsed_at, sha256, section_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                parsed_at = excluded.parsed_at,
                sha256 = excluded.sha256,
                section_count = excluded.section_count""",
            (doc_id, now, sha, len(sections)),
        )
        return {"sections": len(sections), "skipped": None, "sha256": sha}

    def list_documents(self, *, tier: str = "", authority: str = "",
                       include_deprecated: bool = False) -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses = []
        args: list[Any] = []
        if tier:
            clauses.append("tier = ?"); args.append(tier)
        if authority:
            clauses.append("authority = ?"); args.append(authority)
        if not include_deprecated:
            clauses.append("deprecated = 0")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = get_connection(self.db_path).execute(
            f"""SELECT doc_id, path, title, tier, authority, tags_json, applicability,
            deprecated, updated_at, content_sha, content_bytes, parsed_at
            FROM documents {where} ORDER BY authority, tier, doc_id""",
            args,
        ).fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            try:
                entry["tags"] = json.loads(entry.pop("tags_json") or "[]")
            except Exception:
                entry["tags"] = []
            out.append(entry)
        return out

    def find_sections(self, *, query: str = "", tier: str = "",
                      authority: str = "", limit: int = 25) -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses = []
        args: list[Any] = []
        if query:
            clauses.append("(LOWER(heading) LIKE ? OR LOWER(keywords) LIKE ?)")
            args.extend([f"%{query.lower()}%", f"%{query.lower()}%"])
        if tier:
            clauses.append("d.tier = ?"); args.append(tier)
        if authority:
            clauses.append("d.authority = ?"); args.append(authority)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""SELECT s.doc_id, s.section_id, s.heading_level, s.heading,
        s.start_line, s.end_line, s.byte_offset, s.byte_length, s.keywords,
        d.path, d.tier, d.authority
        FROM sections s JOIN documents d ON d.doc_id = s.doc_id {where}
        ORDER BY d.authority, d.tier, s.heading LIMIT ?"""
        args.append(int(limit))
        rows = get_connection(self.db_path).execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def read_section(self, doc_id: str, section_id: str, *,
                     max_bytes: int = 6000) -> str | None:
        self._ensure_schema()
        row = get_connection(self.db_path).execute(
            """SELECT s.byte_offset, s.byte_length, d.path
            FROM sections s JOIN documents d ON d.doc_id = s.doc_id
            WHERE s.doc_id = ? AND s.section_id = ?""",
            (doc_id, section_id),
        ).fetchone()
        if row is None:
            return None
        path = Path(row["path"])
        if not path.is_absolute() and self.repo_root:
            path = self.repo_root / path
        try:
            with open(path, "rb") as handle:
                handle.seek(int(row["byte_offset"]))
                data = handle.read(min(int(row["byte_length"]), max_bytes))
        except OSError:
            return None
        return data.decode("utf-8", errors="replace")


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:80]


def _extract_keywords(heading: str) -> str:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]+|[一-鿿]+", heading)
    return " ".join(t.lower() for t in tokens)
