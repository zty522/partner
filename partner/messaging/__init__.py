"""Messaging subsystem: asynchronous inbound handling for the engine.

The QQ bridge receives a message and immediately writes it to the inbox
(fast path, never blocks the event loop on an LLM call).  A separate
MessageAgent thread consumes the inbox, classifies each message with one
flash model call using a rich chat context (recent conversation + runtime
status + recent jobs), and then:

- ``chat``    -> replies directly (truthful, user-readable)
- ``control`` -> writes a control signal for the running engine + replies
- ``task``    -> submits a job via the fast-path intent contract + replies

Control signals cross the process boundary (instance -> shared worker) so
the engine can pause / stop / redirect at safe node boundaries.
"""
from .inbox import (
    write_message,
    read_pending,
    mark_done,
    pending_count,
)
from .control import (
    write_control,
    read_controls,
    consume_control,
    clear_controls,
)
from .chat_context import (
    recent_chat,
    chat_context,
)
from .attachments import (
    materialise_attachments,
)

__all__ = [
    "write_message", "read_pending", "mark_done", "pending_count",
    "write_control", "read_controls", "consume_control", "clear_controls",
    "recent_chat", "chat_context",
    "materialise_attachments",
]
