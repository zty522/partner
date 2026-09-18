"""CodeRepository: index partner source tree by path, symbol, keyword,
and call relationship.

Stores one row per (path, qualified_name) for each function / method /
class / module top-level.  A separate symbol table lets callers search
by name without re-reading source files.

Index files explicitly listed in the configuration; never rglob the
repository without an explicit maintenance scan.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .runtime_storage import workspace_dir
from .sqlite_base import get_connection

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_files (
    path          TEXT PRIMARY KEY,
    repo_id       TEXT NOT NULL,
    content_sha   TEXT NOT NULL,
    language      TEXT,
    byte_size     INTEGER NOT NULL DEFAULT 0,
    parsed_at     REAL,
    mtime         REAL,
    parse_status  TEXT NOT NULL DEFAULT 'pending',
    parse_error   TEXT
);

CREATE TABLE IF NOT EXISTS workspace_files (
    path          TEXT PRIMARY KEY,
    workspace_root TEXT NOT NULL,
    content_sha   TEXT NOT NULL,
    byte_size     INTEGER NOT NULL DEFAULT 0,
    mtime         REAL NOT NULL,
    indexed_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workspace_files_mtime
    ON workspace_files(workspace_root, mtime);

CREATE INDEX IF NOT EXISTS idx_repo_files_status
    ON repo_files(repo_id, parse_status);

CREATE TABLE IF NOT EXISTS symbols (
    repo_id        TEXT NOT NULL,
    path           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind           TEXT NOT NULL,
    signature      TEXT,
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    byte_offset    INTEGER NOT NULL,
    byte_length    INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    doc_excerpt    TEXT,
    PRIMARY KEY (repo_id, path, qualified_name)
);

CREATE INDEX IF NOT EXISTS idx_symbols_qualified
    ON symbols(repo_id, qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind
    ON symbols(kind);

CREATE TABLE IF NOT EXISTS symbol_calls (
    repo_id        TEXT NOT NULL,
    source_symbol  TEXT NOT NULL,
    target_name    TEXT NOT NULL,
    target_path    TEXT,
    kind           TEXT NOT NULL DEFAULT 'unresolved'
);

CREATE INDEX IF NOT EXISTS idx_symbol_calls_target
    ON symbol_calls(repo_id, target_name);

CREATE TABLE IF NOT EXISTS file_keywords (
    repo_id     TEXT NOT NULL,
    path        TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    PRIMARY KEY (repo_id, path, keyword)
);

CREATE TABLE IF NOT EXISTS code_watermark (
    repo_id     TEXT NOT NULL,
    path        TEXT NOT NULL,
    sha256      TEXT,
    parsed_at   REAL,
    symbol_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo_id, path)
);
"""


DEFAULT_INCLUDE = (
    "partner",
    "tests",
    "scripts",
    "shells",
    "config",
)

DEFAULT_EXCLUDE = (
    "__pycache__",
    ".git",
    ".tox",
    ".venv",
    "node_modules",
    "state",
    "instances",
    "isolated",
    "site-packages",
    "external",
    "browser_ipc",
    "browser_profiles",
)


def init(repo_root: Path, *, repo_id: str = "partner") -> "CodeRepository":
    ws_dir = workspace_dir(repo_root)
    db_path = ws_dir / "code.db"
    repo = CodeRepository(db_path, repo_root, repo_id=repo_id)
    repo._ensure_schema()
    return repo


def _mtime_for(path):
    """Module-level helper to get mtime or None on error."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


class CodeRepository:
    def __init__(self, db_path: Path, repo_root: Path, *, repo_id: str = "partner"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.repo_root = Path(repo_root)
        self.repo_id = repo_id
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

    # ----- ingest -----
    def incremental_ingest(self, *, force: bool = False,
                           include: Iterable[str] = DEFAULT_INCLUDE,
                           exclude: Iterable[str] = DEFAULT_EXCLUDE,
                           max_files: int = 50000) -> dict[str, int]:
        """Walk the configured directory and (re-)parse files whose sha
        has changed since the last ingest.
        """
        self._ensure_schema()
        include = list(include)
        exclude = list(exclude)
        conn = get_connection(self.db_path)
        seen_paths: set[str] = set()
        scanned = 0
        parsed = 0
        skipped = 0
        errors = 0
        # Build the candidate list.  No recursion into excluded prefixes.
        candidates: list[Path] = []
        for include_root in include:
            root = self.repo_root / include_root
            if not root.exists():
                continue
            if root.is_file():
                candidates.append(root)
                continue
            for path in root.rglob("*"):
                rel = path.relative_to(self.repo_root).as_posix()
                if any(part in exclude for part in rel.split("/")):
                    continue
                if path.is_file() and path.suffix == ".py":
                    candidates.append(path)
        if len(candidates) > max_files:
            candidates = candidates[:max_files]
        for path in candidates:
            try:
                rel = path.relative_to(self.repo_root).as_posix()
            except ValueError:
                continue
            seen_paths.add(rel)
            scanned += 1
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            row = conn.execute(
                "SELECT content_sha FROM repo_files WHERE path = ?",
                (rel,),
            ).fetchone()
            if not force and row is not None and row["content_sha"] == sha:
                skipped += 1
                continue
            try:
                self._ingest_file(rel, raw, sha)
                parsed += 1
            except SyntaxError as exc:
                conn.execute(
                    """UPDATE repo_files SET content_sha = ?, byte_size = ?,
                    parse_status = ?, parse_error = ?, parsed_at = ?
                    WHERE path = ?""",
                    (sha, len(raw), "syntax_error", str(exc)[:500], time.time(), rel),
                )
                errors += 1
            except Exception as exc:
                conn.execute(
                    """UPDATE repo_files SET content_sha = ?, byte_size = ?,
                    parse_status = ?, parse_error = ?, parsed_at = ?
                    WHERE path = ?""",
                    (sha, len(raw), "error", str(exc)[:500], time.time(), rel),
                )
                errors += 1
        # Drop rows for files that are no longer present.
        existing = [r[0] for r in conn.execute(
            "SELECT path FROM repo_files WHERE repo_id = ?", (self.repo_id,)
        ).fetchall()]
        removed = 0
        for path in existing:
            if path not in seen_paths:
                conn.execute(
                    "DELETE FROM symbols WHERE repo_id = ? AND path = ?",
                    (self.repo_id, path),
                )
                conn.execute("DELETE FROM repo_files WHERE repo_id = ? AND path = ?",
                             (self.repo_id, path))
                removed += 1
        return {
            "scanned": scanned, "parsed": parsed, "skipped": skipped,
            "errors": errors, "removed": removed,
        }

    def _ingest_file(self, rel: str, raw: bytes, sha: str) -> None:
        text = raw.decode("utf-8", errors="replace")
        tree = ast.parse(text, filename=rel)
        lines = text.splitlines()
        conn = get_connection(self.db_path)
        now = time.time()
        # Clear previous symbols for this file.
        conn.execute(
            "DELETE FROM symbols WHERE repo_id = ? AND path = ?",
            (self.repo_id, rel),
        )
        conn.execute("DELETE FROM file_keywords WHERE repo_id = ? AND path = ?",
                     (self.repo_id, rel))
        symbols: list[dict[str, Any]] = []
        keywords: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = self._qualified(node, rel, tree)
                start, end = node.lineno, int(node.end_lineno or node.lineno)
                offset = self._byte_offset(lines, start)
                length = self._byte_length(lines, start, end)
                excerpt = ast.get_docstring(node) or ""
                symbols.append({
                    "qualified_name": qualified, "kind": "function",
                    "signature": _signature(node), "start_line": start,
                    "end_line": end, "byte_offset": offset, "byte_length": length,
                    "sha256": sha, "doc_excerpt": excerpt[:600],
                })
                keywords.update(_keywords_for(excerpt))
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                        conn.execute(
                            """INSERT INTO symbol_calls
                            (repo_id, source_symbol, target_name, kind)
                            VALUES (?, ?, ?, 'unresolved')""",
                            (self.repo_id, qualified, sub.func.id),
                        )
            elif isinstance(node, ast.ClassDef):
                qualified = self._qualified(node, rel, tree)
                start, end = node.lineno, int(node.end_lineno or node.lineno)
                offset = self._byte_offset(lines, start)
                length = self._byte_length(lines, start, end)
                excerpt = ast.get_docstring(node) or ""
                symbols.append({
                    "qualified_name": qualified, "kind": "class",
                    "signature": f"class {node.name}",
                    "start_line": start, "end_line": end,
                    "byte_offset": offset, "byte_length": length,
                    "sha256": sha, "doc_excerpt": excerpt[:600],
                })
                keywords.update(_keywords_for(excerpt))
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_qn = f"{qualified}.{sub.name}"
                        symbols.append({
                            "qualified_name": method_qn, "kind": "method",
                            "signature": _signature(sub),
                            "start_line": int(sub.lineno),
                            "end_line": int(sub.end_lineno or sub.lineno),
                            "byte_offset": self._byte_offset(lines, int(sub.lineno)),
                            "byte_length": self._byte_length(lines, int(sub.lineno), int(sub.end_lineno or sub.lineno)),
                            "sha256": sha, "doc_excerpt": (ast.get_docstring(sub) or "")[:600],
                        })
        for sym in symbols:
            conn.execute(
                """INSERT INTO symbols
                (repo_id, path, qualified_name, kind, signature, start_line, end_line,
                 byte_offset, byte_length, sha256, doc_excerpt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.repo_id, rel, sym["qualified_name"], sym["kind"],
                    sym["signature"], sym["start_line"], sym["end_line"],
                    sym["byte_offset"], sym["byte_length"], sym["sha256"],
                    sym["doc_excerpt"],
                ),
            )
        for kw in keywords:
            conn.execute(
                "INSERT OR IGNORE INTO file_keywords(repo_id, path, keyword) VALUES (?, ?, ?)",
                (self.repo_id, rel, kw),
            )
        conn.execute(
            """INSERT INTO repo_files
            (path, repo_id, content_sha, language, byte_size, parsed_at, mtime, parse_status)
            VALUES (?, ?, ?, 'python', ?, ?, ?, 'ok')
            ON CONFLICT(path) DO UPDATE SET
                content_sha = excluded.content_sha,
                byte_size = excluded.byte_size,
                parsed_at = excluded.parsed_at,
                parse_status = 'ok',
                parse_error = NULL""",
            (rel, self.repo_id, sha, len(raw), now, _mtime_for(self.repo_root / rel)),
        )
        conn.execute(
            """INSERT INTO code_watermark(repo_id, path, sha256, parsed_at, symbol_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo_id, path) DO UPDATE SET
                sha256 = excluded.sha256,
                parsed_at = excluded.parsed_at,
                symbol_count = excluded.symbol_count""",
            (self.repo_id, rel, sha, now, len(symbols)),
        )

    @staticmethod
    def _qualified(node: ast.AST, rel: str, tree: ast.Module) -> str:
        # build module:rel dotted path
        module = rel.replace("/", ".").removesuffix(".py")
        return f"{module}.{node.name}"

    @staticmethod
    def _byte_offset(lines: list[str], start_line: int) -> int:
        if start_line <= 1:
            return 0
        offset = 0
        for line in lines[: start_line - 1]:
            offset += len(line.encode("utf-8")) + 1
        return offset

    @staticmethod
    def _byte_length(lines: list[str], start_line: int, end_line: int) -> int:
        total = 0
        for line in lines[start_line - 1: end_line]:
            total += len(line.encode("utf-8")) + 1
        return total

    # ----- queries -----
    def find_symbol(self, name: str, *, kind: str = "",
                    limit: int = 25) -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses = ["qualified_name LIKE ?"]
        args: list[Any] = [f"%{name}%"]
        if kind:
            clauses.append("kind = ?"); args.append(kind)
        sql = f"""SELECT qualified_name, kind, signature, path, start_line, end_line,
        byte_offset, byte_length, sha256, doc_excerpt
        FROM symbols WHERE {' AND '.join(clauses)}
        ORDER BY (CASE WHEN qualified_name = ? THEN 0 ELSE 1 END),
                 length(qualified_name) ASC LIMIT ?"""
        args.extend([name, int(limit)])
        rows = get_connection(self.db_path).execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def find_files_with_keyword(self, keyword: str, *, limit: int = 25) -> list[str]:
        self._ensure_schema()
        rows = get_connection(self.db_path).execute(
            """SELECT DISTINCT path FROM file_keywords
            WHERE keyword = ? OR keyword LIKE ?
            ORDER BY path LIMIT ?""",
            (keyword.lower(), f"%{keyword.lower()}%", int(limit)),
        ).fetchall()
        return [r["path"] for r in rows]

    def read_symbol(self, repo_path: str, qualified_name: str, *,
                    max_bytes: int = 6000) -> str | None:
        self._ensure_schema()
        row = get_connection(self.db_path).execute(
            """SELECT byte_offset, byte_length FROM symbols
            WHERE repo_id = ? AND path = ? AND qualified_name = ?""",
            (self.repo_id, repo_path, qualified_name),
        ).fetchone()
        if row is None:
            return None
        try:
            full = self.repo_root / repo_path
            with open(full, "rb") as handle:
                handle.seek(int(row["byte_offset"]))
                data = handle.read(min(int(row["byte_length"]), max_bytes))
        except OSError:
            return None
        return data.decode("utf-8", errors="replace")

    def callers_of(self, name: str, *, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_schema()
        rows = get_connection(self.db_path).execute(
            """SELECT source_symbol, target_name, kind FROM symbol_calls
            WHERE target_name = ? ORDER BY source_symbol LIMIT ?""",
            (name, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [a.arg for a in node.args.args]
    return f"({', '.join(args)})"


def _keywords_for(text: str) -> set[str]:
    if not text:
        return set()
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text.lower())
    return set(t for t in tokens if len(t) <= 40)
