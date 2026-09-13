"""User notification policy for background project work.

The full Event stream remains visible in audit/UI detail.  Push channels only
receive changes that matter to a person, preventing project work, external
learning and self-evolution from becoming one undifferentiated message flood.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class NotificationDecision:
    notify: bool
    category: str
    reason: str


IMPORTANT = {"milestone", "result", "approval", "blocker", "candidate_promoted",
             "candidate_rolled_back", "user_query"}


def decide_notification(event: Mapping[str, Any], *, report_policy: str = "milestone") -> NotificationDecision:
    category = str(event.get("notification_kind") or event.get("kind") or "routine")
    if report_policy == "explicit" and category not in {"approval", "blocker", "user_query"}:
        return NotificationDecision(False, category, "explicit export was not requested")
    if category in IMPORTANT:
        return NotificationDecision(True, category, "meaningful user boundary")
    if bool(event.get("requires_human")):
        return NotificationDecision(True, "approval", "human decision required")
    if bool(event.get("blocked")) or str(event.get("status") or "").lower() in {"failed", "blocked"}:
        return NotificationDecision(True, "blocker", "work cannot continue safely")
    if bool(event.get("business_delta")):
        return NotificationDecision(True, "milestone", "new verified project evidence")
    return NotificationDecision(False, category, "routine Event remains in audit stream")


def report_required(event: Mapping[str, Any], *, report_policy: str = "milestone") -> bool:
    """PDF is a milestone/export product, never an automatic per-Event side effect."""
    if report_policy == "none":
        return False
    if report_policy == "explicit":
        return bool(event.get("explicit_export"))
    decision = decide_notification(event, report_policy=report_policy)
    return decision.notify and decision.category in {
        "milestone", "result", "candidate_promoted", "candidate_rolled_back",
    }

