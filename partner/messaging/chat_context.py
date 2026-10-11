"""Bounded chat context injected into message classification and
direct-answer LLM calls: recent conversation + runtime status + recent
jobs + open notes.  All reads are bounded and read-only; the LLM sees
real receipts, never guesses.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def recent_chat(
    root: str | os.PathLike,
    *,
    persona_hint: str = "",
    sender_id: str = "",
    limit: int = 12,
    max_chars: int = 6000,
) -> str:
    """Last N conversation turns for this user (QQ chat history +
    dialog history), oldest first, truncated.  Real content only."""
    root = Path(root)
    rows: list[dict] = []
    for name in ("qq_chat_history.jsonl", "dialog_history.jsonl"):
        path = root / "state" / name
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if sender_id and str(row.get("sender_id") or "") and str(row.get("sender_id")) != str(sender_id):
                        continue
                    if persona_hint and str(row.get("source") or "") and str(row.get("source")) != "qq":
                        continue
                    rows.append(row)
        except OSError:
            continue
    if not rows:
        return "(暂无聊天记录)"
    selected = rows[-limit:]
    parts: list[str] = []
    for row in selected:
        role = str(row.get("role") or "user")
        content = str(row.get("content") or "").replace("\n", " ")[:400]
        ts = str(row.get("timestamp") or "")[:19]
        if content:
            parts.append(f"[{ts}] {role}: {content}")
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


def recent_jobs(
    root: str | os.PathLike,
    *,
    persona_hint: str = "",
    limit: int = 3,
    max_chars: int = 4000,
) -> str:
    """Brief summary of the instance's most recent jobs (title, status,
    when, short outcome)."""
    try:
        from partner.index.job_repository import init as _init_jobs
        repo = _init_jobs(root)
        records = repo.list_recent(limit=max(20, limit * 4)) or []
    except Exception:  # noqa: BLE001
        return ""
    picked: list[dict] = []
    for row in records:
        if persona_hint and str(row.get("assigned_instance") or row.get("origin_instance") or "") != persona_hint:
            continue
        picked.append(row)
        if len(picked) >= limit:
            break
    if not picked:
        return "(暂无近期任务)"
    parts: list[str] = []
    for row in picked:
        title = str(row.get("title") or row.get("request") or "")[:120]
        status = str(row.get("status") or "")
        ts = str(row.get("created_at") or "")[:16]
        if title:
            parts.append(f"- [{ts}] {title} → {status}")
    text = "\n".join(parts)
    return text[:max_chars]


def open_notes_digest(root: str | os.PathLike, *, max_chars: int = 2500) -> str:
    """Digest of open notes (act-now / open) to ground learning and
    evolution judgments."""
    path = Path(root) / "share" / "mind" / "notes" / "notes.jsonl"
    if not path.is_file():
        return ""
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if str(row.get("status") or "") in {"open", "act_now"}:
                    rows.append(row)
    except OSError:
        return ""
    if not rows:
        return "(暂无待办笔记)"
    parts = [str(r.get("content") or "")[:220] for r in rows[-8:]]
    text = "\n".join(f"- {p}" for p in parts)
    return text[:max_chars]


def chat_context(
    root: str | os.PathLike,
    *,
    chat_workspace: str | os.PathLike | None = None,
    persona_hint: str = "",
    sender_id: str = "",
    request: str = "",
    attachments: list[dict] | None = None,
) -> dict:
    """Assemble the full bounded context block for message handling.

    ``root`` is the global partner workspace (jobs/notes/runtime receipts);
    ``chat_workspace`` is the instance workspace where chat history lives
    (defaults to ``root``)."""
    chat_root = Path(chat_workspace) if chat_workspace else Path(root)
    return {
        "source": "read_only_real_state",
        "recent_chat": recent_chat(chat_root, persona_hint=persona_hint, sender_id=sender_id),
        "recent_jobs": recent_jobs(root, persona_hint=persona_hint),
        "open_notes": open_notes_digest(root),
        "runtime_status": _runtime_status_snapshot(root, persona_hint=persona_hint),
        "current_request": str(request or "")[:2000],
        "attachments": [
            {"kind": str(a.get("kind") or ""), "name": str(a.get("name") or "")}
            for a in (attachments or [])
        ],
    }


def _runtime_status_snapshot(root: str | os.PathLike, *, persona_hint: str = "") -> dict:
    """Reuse the bounded runtime_status helper with a stub ctx."""
    class _Stub:
        pass

    ctx = _Stub()
    ctx.workspace = str(root)
    ctx.project_id = ""
    ctx.instance_id = persona_hint or ""
    ctx.job_id = ""
    try:
        from partner.runtime.status_context import runtime_status
        value = runtime_status(ctx, {"project_id": "", "instance_id": persona_hint or ""})
    except Exception:  # noqa: BLE001
        value = {}
    return value


__all__ = ["recent_chat", "recent_jobs", "open_notes_digest", "chat_context"]
