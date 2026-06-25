"""Unified Delivery Queue — all outputs go through this single ordered layer.

Every output (progress, reply, notification, file) gets:
1. A monotonically increasing sequence_number (persisted across restarts)
2. A timestamp from a single clock source
3. Written to a unified delivery_queue.jsonl as the authoritative ordered log
4. Dispatched to the appropriate channel writers for backward compatibility

The sequence_number + timestamp ensures that any downstream consumer
(GUI, TUI, QQ bridge) can reconstruct the exact chronological order
of all outputs, regardless of which channel they were delivered on.
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_SEQUENCE_COUNTER_FILE = "delivery_seq_counter.txt"
_DELIVERY_LOG_FILE = "delivery_queue.jsonl"
_lock = threading.Lock()

# Globals (set once at startup)
_workspace: str = ""


def init(workspace: str):
    """Initialize the delivery queue for a workspace. Call once at startup."""
    global _workspace
    _workspace = workspace
    _init_sequence_counter()


def _state_dir() -> str:
    return os.path.join(_workspace, "state")


def _init_sequence_counter():
    """Ensure the sequence counter file exists."""
    state = _state_dir()
    os.makedirs(state, exist_ok=True)
    path = os.path.join(state, _SEQUENCE_COUNTER_FILE)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("0")


def _next_sequence() -> int:
    """Atomically increment and return the next sequence number."""
    with _lock:
        state = _state_dir()
        path = os.path.join(state, _SEQUENCE_COUNTER_FILE)
        try:
            with open(path, "r") as f:
                seq = int(f.read().strip() or "0")
        except (FileNotFoundError, ValueError):
            seq = 0
        seq += 1
        with open(path, "w") as f:
            f.write(str(seq))
        return seq


class DeliveryEntry:
    """A single ordered delivery entry."""
    __slots__ = ("sequence", "timestamp", "kind", "content", "channels", "metadata")

    def __init__(self, kind: str, content: str, channels: Optional[list[str]] = None,
                 metadata: Optional[dict] = None):
        self.sequence = _next_sequence()
        self.timestamp = datetime.now().isoformat(timespec="milliseconds")
        self.kind = kind
        self.content = content
        self.channels = channels or ["event_pipeline", "dialog_history"]
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "seq": self.sequence,
            "ts": self.timestamp,
            "kind": self.kind,
            "content": self.content[:500],
            "channels": self.channels,
            "meta": self.metadata,
        }


def _append_delivery_log(entry: DeliveryEntry):
    """Write to the unified delivery log (authoritative ordered source)."""
    try:
        state = _state_dir()
        path = os.path.join(state, _DELIVERY_LOG_FILE)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug(f"[DELIVERY] failed to write log: {exc}")


# ── Channel dispatchers ──

_channel_dispatchers = {}


def register_channel(name: str, dispatcher_fn):
    """Register a channel dispatcher function.
    The function receives (entry: DeliveryEntry) and handles delivery to that channel.
    """
    _channel_dispatchers[name] = dispatcher_fn


def deliver(kind: str, content: str, *,
            channels: Optional[list[str]] = None,
            metadata: Optional[dict] = None) -> DeliveryEntry:
    """Main delivery function — ALL output goes through this.

    Args:
        kind: One of 'progress', 'reply', 'notification', 'error', 'file'
        content: The text/content to deliver
        channels: Which channels to deliver to (default: event_pipeline, dialog_history)
        metadata: Additional structured data

    Returns:
        The DeliveryEntry with assigned sequence number and timestamp.
    """
    entry = DeliveryEntry(kind, content, channels=channels, metadata=metadata)

    # Always write to the unified log first
    _append_delivery_log(entry)

    # Dispatch to registered channels
    for ch in (channels or ["event_pipeline", "dialog_history"]):
        fn = _channel_dispatchers.get(ch)
        if fn:
            try:
                fn(entry)
            except Exception as exc:
                logger.debug(f"[DELIVERY] channel '{ch}' dispatch failed: {exc}")

    return entry


def read_queue(limit: int = 50, offset: int = 0) -> list[dict]:
    """Read entries from the delivery queue, ordered by sequence number.

    Args:
        limit: Max entries to return
        offset: Skip first N entries

    Returns:
        List of dicts ordered by seq ascending.
    """
    try:
        state = _state_dir()
        path = os.path.join(state, _DELIVERY_LOG_FILE)
        if not os.path.exists(path):
            return []
        entries = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        entries.sort(key=lambda e: e.get("seq", 0))
        return entries[offset:offset + limit]
    except Exception as exc:
        logger.debug(f"[DELIVERY] failed to read queue: {exc}")
        return []
