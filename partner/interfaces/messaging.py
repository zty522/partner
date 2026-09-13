"""Channel-neutral message intake and presentation helpers.

The three user interfaces used to maintain slightly different JSON shapes and
write paths.  This module is the compatibility boundary: every local UI writes
one durable inbox record, while the runtime remains the sole owner of assistant
history and delivery acknowledgements.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class InboxReceipt:
    """Proof that a local interaction was durably appended to an inbox."""

    accepted: bool
    message_id: str
    inbox_path: str
    bytes_written: int = 0
    error: str = ""


def _safe_attachment(item: str | os.PathLike | Mapping[str, object]) -> dict:
    if isinstance(item, Mapping):
        raw_path = str(item.get("path") or "")
        supplied_name = str(item.get("name") or "")
    else:
        raw_path = os.fspath(item)
        supplied_name = ""
    path = os.path.abspath(os.path.expanduser(raw_path)) if raw_path else ""
    name = supplied_name or os.path.basename(path)
    result = {"path": path, "name": name}
    if path and os.path.isfile(path):
        result["size"] = os.path.getsize(path)
        result["exists"] = True
    else:
        result["exists"] = False
    return result


def enqueue_interaction(
    instance_workspace: str | os.PathLike,
    text: str,
    *,
    source: str,
    sender_id: str,
    sender_name: str,
    attachments: Sequence[str | os.PathLike | Mapping[str, object]] = (),
    message_id: str | None = None,
) -> InboxReceipt:
    """Append exactly one normalized user request to ``desktop_inbox.jsonl``.

    ``O_APPEND`` plus a single ``os.write`` keeps concurrent GUI/TUI writers
    from interleaving records.  The function deliberately does *not* write to
    chat history; only the runtime may claim a message was observed or replied
    to.
    """

    workspace = Path(instance_workspace).expanduser().resolve()
    inbox_path = workspace / "state" / "desktop_inbox.jsonl"
    clean_text = str(text or "").replace("\x00", "").strip()
    safe_attachments = [_safe_attachment(item) for item in attachments]
    if not clean_text and not safe_attachments:
        return InboxReceipt(False, "", str(inbox_path), error="empty_message")

    channel = re.sub(r"[^a-z0-9_-]+", "_", str(source or "local").lower()).strip("_") or "local"
    msg_id = message_id or f"{channel}_{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "schema_version": 2,
        "id": msg_id,
        "message_id": msg_id,
        "text": clean_text,
        "display_text": clean_text,
        "source": channel,
        "channel": channel,
        "sender_id": str(sender_id or channel),
        "sender_name": str(sender_name or channel),
        "attachments": safe_attachments,
        "created_at": now,
    }
    payload = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    try:
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(inbox_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            written = os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        if written != len(payload):
            return InboxReceipt(False, msg_id, str(inbox_path), written, "short_write")
        return InboxReceipt(True, msg_id, str(inbox_path), written)
    except OSError as exc:
        return InboxReceipt(False, msg_id, str(inbox_path), error=f"{type(exc).__name__}: {exc}")


def is_channel_response(row: Mapping[str, object], channel: str, message_id: str = "") -> bool:
    """Return whether a history row is a real response visible to ``channel``."""

    role = str(row.get("role") or "")
    if role not in {"assistant", "partner"}:
        return False
    content = str(row.get("content") or row.get("text") or "").strip()
    if not content or content in {"思考中.......", "思考中......", "思考中……", "Thinking..."}:
        return False
    if message_id and str(row.get("reply_to") or "") == message_id:
        return True
    expected = str(channel or "").lower()
    source = str(row.get("source") or "").lower()
    target = str(row.get("target_id") or "").lower()
    if expected == "tui":
        return source in {"tui", "app_tui"} or target in {"tui", "tui_user"}
    if expected == "gui":
        return source in {"gui", "app_gui"} or target in {"gui", "desktop_gui"}
    return source == expected or target == expected


def split_outbound_text(text: str, max_chars: int = 1800) -> list[str]:
    """Split a bot reply at semantic boundaries without dropping characters."""

    clean = str(text or "").strip()
    if not clean:
        return []
    limit = max(64, int(max_chars))
    if len(clean) <= limit:
        return [clean]

    chunks: list[str] = []
    remaining = clean
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[: limit + 1]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind("。"), window.rfind("；"), window.rfind(" "))
        if cut < limit // 3:
            cut = limit
        elif window[cut:cut + 1] in {"。", "；"}:
            cut += 1
        chunk = remaining[:cut].rstrip()
        chunks.append(chunk or remaining[:limit])
        remaining = remaining[cut:].lstrip()

    if len(chunks) == 1:
        return chunks
    total = len(chunks)
    return [f"[{index}/{total}] {chunk}" for index, chunk in enumerate(chunks, 1)]
