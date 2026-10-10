"""Unified Note Store for Partner (Mind Notes).

Every durable, LLM-usable memory lives here as one typed note in a single
append/replace JSONL ledger under ``share/mind/notes/notes.jsonl``.  Types are
registered in ``NOTE_TYPES`` and are extensible; each type carries its own
schema requirements, status vocabulary and promotion path.

Lifecycle of a note::

    record (judge)  ->  open/candidate  ->  promote (evidence)  ->  active/confirmed
                                          ->  dismiss (no value / falsified)

The store is intentionally dumb: validation and JSONL persistence only.
LLM judgement lives in ``partner/events/notes.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import fcntl
import json
import re
import time
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_tokens(text: str) -> set[str]:
    """Tokenize for recall: english words + chinese 2-grams."""
    words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    han = re.findall(r"[\u4e00-\u9fff]+", text)
    bigrams: set[str] = set()
    for chunk in han:
        for i in range(len(chunk) - 1):
            bigrams.add(chunk[i:i + 2])
    return words | bigrams


def _root(workspace: str | Path) -> Path:
    value = Path(workspace).resolve()
    return value.parent.parent if value.parent.name == "instances" else value


# ---------------------------------------------------------------------------
# Type registry
# ---------------------------------------------------------------------------

NOTE_TYPES: dict[str, dict[str, Any]] = {
    "observation": {
        "desc": "运行观察，任务终态自动投影",
        "required": ["content"],
        "statuses": ["recorded"],
        "promote_to": None,
    },
    "lesson": {
        "desc": "可迁移教训，需证据",
        "required": ["content", "evidence_refs"],
        "statuses": ["open", "candidate", "active", "dismissed"],
        "promote_to": "active",
    },
    "habit": {
        "desc": "行动习惯，需 >=2 个不同 flow 的已验证成果才激活",
        "required": ["content", "evidence_refs"],
        "statuses": ["candidate", "active", "dismissed"],
        "promote_to": "active",
    },
    "growth": {
        "desc": "持久成长，需真实改善 + 匹配实验证据",
        "required": ["content", "evidence_refs"],
        "statuses": ["confirmed", "dismissed"],
        "promote_to": "confirmed",
    },
    "belief": {
        "desc": "项目信念，需证据修订",
        "required": ["content", "evidence_refs"],
        "statuses": ["open", "confirmed", "dismissed"],
        "promote_to": "confirmed",
    },
    "preference": {
        "desc": "用户偏好",
        "required": ["content"],
        "statuses": ["active", "dismissed"],
        "promote_to": None,
    },
    "pending": {
        "desc": "待定改进/学习候补：有价值但没想清楚、资料少、风险高，先记录",
        "required": ["content", "gap", "trigger_signal"],
        "statuses": ["open", "progressing", "resolved", "dismissed"],
        "promote_to": "resolved",
    },
    "insight": {
        "desc": "外部资料提炼的想法（学习后不落地就存这里）",
        "required": ["content", "source"],
        "statuses": ["open", "candidate", "adopted", "dismissed"],
        "promote_to": "adopted",
    },
    "issue_note": {
        "desc": "运行问题记录，与 docs/temp.md 互链",
        "required": ["content"],
        "statuses": ["open", "fixed", "dismissed"],
        "promote_to": "fixed",
    },
    "user_insight": {
        "desc": "用户交流中发现（习惯、偏好之外的洞察）",
        "required": ["content"],
        "statuses": ["open", "active", "dismissed"],
        "promote_to": "active",
    },
}

VALID_STATUSES = {t: set(spec["statuses"]) for t, spec in NOTE_TYPES.items()}
VALID_TYPES = set(NOTE_TYPES)


class NoteValidationError(ValueError):
    pass


def validate_record(note_type: str, record: Mapping[str, Any]) -> None:
    """Validate one note before persistence.  Raises NoteValidationError."""
    if note_type not in NOTE_TYPES:
        raise NoteValidationError(f"unknown note_type {note_type!r}; registered: {sorted(VALID_TYPES)}")
    spec = NOTE_TYPES[note_type]
    missing = [k for k in spec["required"] if not str(record.get(k) or "").strip()]
    if missing:
        raise NoteValidationError(f"note_type {note_type} requires fields {missing}")
    status = str(record.get("status") or "open")
    if status not in VALID_STATUSES[note_type]:
        raise NoteValidationError(
            f"note_type {note_type} status {status!r} not in {sorted(VALID_STATUSES[note_type])}")
    evidence = record.get("evidence_refs") or []
    if evidence and not isinstance(evidence, list):
        raise NoteValidationError("evidence_refs must be a list")
    for ref in evidence:
        if not str(ref).strip():
            raise NoteValidationError("evidence_refs contains empty reference")


class NoteStore:
    """Append/replace typed notes in one JSONL ledger.

    The ledger is append-only: new notes and updated versions are appended as
    new rows.  A row is addressed by ``id``; readers keep the *last* row for
    each id (dedupe), so an update is O(1) write even with a large ledger.
    File locking keeps concurrent instances safe.
    """

    def __init__(self, workspace: str | Path):
        self.root = _root(workspace)
        self.directory = self.root / "share/mind/notes"
        self.path = self.directory / "notes.jsonl"

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def _lock(self):
        self._handle = self.path.open("a", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self._handle

    def _unlock(self):
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
        except Exception:
            pass

    def append(self, note_type: str, record: Mapping[str, Any], note_id: str | None = None) -> dict[str, Any]:
        note = dict(record)
        note.setdefault("type", note_type)
        note.setdefault("id", note_id or f"note_{uuid.uuid4().hex[:12]}")
        note.setdefault("status", "open")
        note.setdefault("content", "")
        note.setdefault("confidence", 0.5)
        note.setdefault("source", "")
        note.setdefault("evidence_refs", [])
        note.setdefault("created_at", _now())
        note["updated_at"] = _now()
        validate_record(note_type, note)
        self.directory.mkdir(parents=True, exist_ok=True)
        handle = self._lock()
        try:
            handle.write(json.dumps(note, ensure_ascii=False) + "\n")
            handle.flush()
        finally:
            self._unlock()
        return note

    def replace(self, note_id: str, patch: Mapping[str, Any]) -> dict[str, Any] | None:
        """Update one note by id (append new version, readers keep last)."""
        rows = self._dedupe(self._read_all())
        found = None
        for row in rows:
            if row.get("id") == note_id:
                found = row
                break
        if found is None:
            return None
        merged = {**found, **dict(patch), "id": note_id, "updated_at": _now()}
        validate_record(merged.get("type", found.get("type", "observation")), merged)
        handle = self._lock()
        try:
            handle.write(json.dumps(merged, ensure_ascii=False) + "\n")
            handle.flush()
        finally:
            self._unlock()
        return merged

    @staticmethod
    def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the last row per note id (append-only versioning)."""
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            nid = row.get("id")
            if not nid:
                continue
            latest[nid] = row
        return list(latest.values())

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                rows = []
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return rows

    def _rows_current(self) -> list[dict[str, Any]]:
        return self._dedupe(self._read_all())

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._rows_current()
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return rows

    def recall(self, query: str = "", types: list[str] | None = None,
               statuses: list[str] | None = None, limit: int = 8,
               project_id: str = "") -> list[dict[str, Any]]:
        """Semantic-ish recall: keyword/bigram overlap + status/type filters + recency."""
        rows = self._rows_current()
        tokens = _text_tokens(str(query or ""))
        hits: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            if types and row.get("type") not in types:
                continue
            if statuses and row.get("status") not in statuses:
                continue
            if project_id and row.get("project_id") and row.get("project_id") != project_id:
                continue
            content = f"{row.get('content','')} {row.get('source','')} {row.get('gap','')}"
            text_tokens = _text_tokens(content)
            overlap = len(tokens & text_tokens) if tokens else 0
            score = overlap + float(row.get("confidence") or 0.5) * 0.5
            if tokens and overlap == 0:
                continue
            hits.append((score, row))
        hits.sort(key=lambda pair: -pair[0])
        return [row for _, row in hits[:limit]]

    def pending_open(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.recall(query="", types=["pending"], statuses=["open", "progressing"],
                           limit=limit)

    def by_id(self, note_id: str) -> dict[str, Any] | None:
        for row in self._rows_current():
            if row.get("id") == note_id:
                return row
        return None

    def stats(self) -> dict[str, Any]:
        rows = self._rows_current()
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for row in rows:
            t = row.get("type") or "?"
            s = row.get("status") or "?"
            by_type[t] = by_type.get(t, 0) + 1
            by_status[s] = by_status.get(s, 0) + 1
        return {"total": len(rows), "by_type": by_type, "by_status": by_status,
                "path": str(self.path)}


def notes_injection(ctx: Any) -> str:
    """Build the 【长期笔记参考】 prompt segment for the current run context.

    Pulls ``note_injection`` from run_context (or a direct ctx attribute) and
    renders a bounded, user/LLM-readable reference block with the usage
    guidance.  Returns "" when nothing is injected.
    """
    try:
        run_context = getattr(ctx, "run_context", None) or {}
        value = run_context.get("note_injection") if isinstance(run_context, dict) else None
        if not value:
            value = getattr(ctx, "note_injection", None)
        if isinstance(value, str) and value.strip():
            return value
        if not isinstance(value, dict):
            return ""
        notes = value.get("notes") or []
        pending = value.get("pending") or []
        parts: list[str] = []
        for n in notes[:6]:
            line = f"[{n.get('type','note')}·{n.get('status','')}] {str(n.get('content',''))[:180]}"
            if n.get("source"):
                line += f"（来源={str(n.get('source'))[:60]}）"
            parts.append(line)
        for p in pending[:4]:
            line = f"[pending·{p.get('status','open')}] {str(p.get('content',''))[:180]}"
            if p.get("gap"):
                line += f"（gap={str(p.get('gap'))[:60]}）"
            if p.get("trigger_signal"):
                line += f"（trigger={str(p.get('trigger_signal'))[:60]}）"
            parts.append(line)
        if not parts:
            return ""
        return ("【长期笔记参考】\n"
                + "\n".join(parts)
                + "\n请判断：1) 哪些记录直接指导当前步骤、怎么用；"
                  "2) 哪些 open 的 pending 可被本次任务解决（是→转入落地链并更新状态）；"
                  "3) 是否有需要新增/更新的记录（按写入契约输出："
                  "{decision, note_type, content, confidence, gap, trigger_signal, evidence_refs, reason}）。"
                  "注意：笔记不等于已验证事实，使用前核对 evidence_refs。\n")
    except Exception:
        return ""
