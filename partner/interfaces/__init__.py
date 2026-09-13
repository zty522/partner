"""Stable interaction contracts shared by Partner's GUI, TUI and chat bots."""

from .messaging import (
    InboxReceipt,
    enqueue_interaction,
    is_channel_response,
    split_outbound_text,
)

__all__ = [
    "InboxReceipt",
    "enqueue_interaction",
    "is_channel_response",
    "split_outbound_text",
]
