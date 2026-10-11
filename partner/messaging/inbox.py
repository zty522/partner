"""Inbound message queue (file-backed, cross-process safe).

The QQ bridge writes each inbound message here on the fast path and the
MessageAgent thread consumes it asynchronously, so the QQ event loop is
never blocked by LLM calls.  Files are the transport because the instance
host and the message thread share the same process, while the engine
worker may also read the queue for engine scheduling.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

INBOX_REL = "state/application/inbox"
SIGNAL_NAME = ".signal"


def _inbox_dir(root: str | os.PathLike) -> Path:
    return Path(root) / INBOX_REL


def _touch_signal(root: str | os.PathLike) -> None:
    try:
        signal = _inbox_dir(root) / SIGNAL_NAME
        signal.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def write_message(
    root: str | os.PathLike,
    *,
    msg_id: str,
    channel: str,
    sender_id: str,
    sender_name: str,
    text: str,
    attachments: list[dict],
    persona_hint: str,
    received_at: str = "",
) -> Path:
    """Atomically persist one inbound message and touch the wake signal."""
    directory = _inbox_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    safe_id = str(msg_id or uuid.uuid4().hex)
    row = {
        "msg_id": safe_id,
        "channel": channel,
        "sender_id": str(sender_id or ""),
        "sender_name": str(sender_name or "用户"),
        "text": str(text or ""),
        "attachments": list(attachments or []),
        "persona_hint": str(persona_hint or ""),
        "received_at": received_at or time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
    }
    target = directory / f"msg_{safe_id}.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)
    _touch_signal(root)
    return target


def read_pending(root: str | os.PathLike, *, persona_hint: str = "") -> list[dict]:
    """Return unconsumed inbox messages (msg_*.json with no .done marker),
    oldest first.  ``persona_hint`` filters when given."""
    directory = _inbox_dir(root)
    if not directory.exists():
        return []
    done = {p.name[:-len(".done.json")] for p in directory.glob("msg_*.done.json")}
    rows: list[dict] = []
    for path in sorted(directory.glob("msg_*.json")):
        if path.stem in done:
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if persona_hint and str(row.get("persona_hint") or "") != persona_hint:
            continue
        row["_path"] = str(path)
        rows.append(row)
    return rows


def mark_done(root: str | os.PathLike, msg_id: str, *, outcome: str, detail: str = "") -> None:
    """Record the processing outcome next to the message (kept for audit)."""
    directory = _inbox_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    safe_id = str(msg_id or "")
    if not safe_id:
        return
    row = {
        "msg_id": safe_id,
        "outcome": outcome,
        "detail": str(detail or "")[:4000],
        "processed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
    }
    target = directory / f"msg_{safe_id}.done.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)


def pending_count(root: str | os.PathLike) -> int:
    return len(read_pending(root))


def drain_all(root: str | os.PathLike) -> int:
    """Remove consumed done markers and stale signal; return pending count."""
    directory = _inbox_dir(root)
    if not directory.exists():
        return 0
    try:
        (directory / SIGNAL_NAME).unlink(missing_ok=True)
    except OSError:
        pass
    return pending_count(root)


__all__ = ["write_message", "read_pending", "mark_done", "pending_count", "drain_all"]
