"""Durable phase reducer for meaningful long-running Partner campaigns."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .campaign_models import WorkItem
from .models import now_iso


@dataclass
class LongHorizonAssessment:
    schema_version: int
    phase: str
    reason: str
    business_since_learning: int
    business_progress_since_learning: int
    no_change_since_learning: int
    governance_since_business: int
    evidence_fingerprint: str
    learning_allowed: bool
    candidate_validation_allowed: bool
    next_review_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_business(item: WorkItem) -> bool:
    return (
        item.kind == "project_iteration"
        and item.instance_id in {"01", "02", "03", "04"}
        and "[portfolio_scout=true]" not in item.instruction
    )


def _is_learning(item: WorkItem) -> bool:
    return item.instance_id == "05" or item.kind == "evolution_experiment"


def _evidence_flag(item: WorkItem, name: str) -> bool:
    needle = f"{name}=true"
    return needle in {str(value).lower() for value in item.evidence or []}


def _fingerprint(items: list[WorkItem]) -> str:
    digest = hashlib.sha256()
    for item in items:
        digest.update(json.dumps({
            "work_item_id": item.work_item_id,
            "status": item.status,
            "artifacts": item.artifacts,
            "event_types": item.event_types,
            "evidence": item.evidence,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest() if items else ""


def _same_tick_order(item: WorkItem) -> int:
    """Deterministic causal order when persisted timestamps share one second.

    ``now_iso`` is intentionally human-readable and second-granular.  Random
    WorkItem IDs must therefore never decide whether a business result happened
    before its learning checkpoint or bounded Candidate evaluation.
    """
    if item.kind == "evolution_experiment":
        return 2
    if item.instance_id == "05":
        return 1
    return 0


def assess_long_horizon(
    items: list[WorkItem],
    *,
    has_project_continuation: bool,
    has_executable_candidate: bool,
    minimum_business_per_learning: int = 2,
    now: datetime | None = None,
) -> LongHorizonAssessment:
    """Reduce durable WorkItems into the next cognitive-control phase.

    This is deliberately not an LLM reflection. It prevents governance work
    from outnumbering project evidence and makes WAIT a recoverable state.
    """
    ordered = sorted(items, key=lambda item: (
        item.updated_at, _same_tick_order(item), item.created_at, item.work_item_id,
    ))
    latest_learning_index = max(
        (index for index, item in enumerate(ordered)
         if _is_learning(item) and item.status == "completed"),
        default=-1,
    )
    since_learning = ordered[latest_learning_index + 1:]
    business = [item for item in since_learning if _is_business(item)
                and item.status in {"completed", "blocked"}]
    progress = [item for item in business if _evidence_flag(item, "business_progress")]
    no_change = [item for item in since_learning
                 if "[portfolio_scout=true]" in item.instruction
                 and item.status in {"completed", "blocked"}]
    latest_business_index = max(
        (index for index, item in enumerate(ordered)
         if _is_business(item) and item.status in {"completed", "blocked"}),
        default=-1,
    )
    governance_since_business = sum(
        1 for item in ordered[latest_business_index + 1:]
        if (_is_learning(item) or item.kind in {"audit", "report"})
        and item.status in {"completed", "blocked"}
    )
    active = [item for item in ordered if item.status in {"leased", "queued", "running", "proposed", "failed"}]
    learning_allowed = len(business) >= int(minimum_business_per_learning)
    candidate_allowed = (
        has_executable_candidate
        and governance_since_business <= 1
        and (learning_allowed or latest_learning_index >= 0)
    )

    if active:
        phase, reason = "WAIT_WORK", "durable work is still active"
    elif has_project_continuation:
        phase, reason = "ADVANCE_PROJECT", "a declared Receipt NextAction must run before learning"
    elif candidate_allowed:
        phase, reason = "VALIDATE_CANDIDATE", "new business evidence can support one bounded Candidate experiment"
    elif learning_allowed:
        phase, reason = "CONSOLIDATE_EVIDENCE", "enough new business outcomes exist for one learning pass"
    elif no_change:
        phase, reason = "WAIT_EVIDENCE", "no-change monitoring is not project progress"
    else:
        phase, reason = "WAIT_EVIDENCE", "insufficient new business evidence; do not manufacture reflection work"

    current = now or datetime.now(timezone.utc).astimezone()
    wait_minutes = 60 if no_change or governance_since_business >= 2 else 15
    return LongHorizonAssessment(
        schema_version=1,
        phase=phase,
        reason=reason,
        business_since_learning=len(business),
        business_progress_since_learning=len(progress),
        no_change_since_learning=len(no_change),
        governance_since_business=governance_since_business,
        evidence_fingerprint=_fingerprint(business),
        learning_allowed=learning_allowed,
        candidate_validation_allowed=candidate_allowed,
        next_review_at=(current + timedelta(minutes=wait_minutes)).isoformat(timespec="seconds"),
        updated_at=now_iso(),
    )
