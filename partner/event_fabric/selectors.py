"""Selector Event: chooses all non-conflicting next Events, not one global step."""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Mapping

from .ledger import EventLedger, _now
from .models import EventSelection


class NextEventSelector:
    def __init__(self, ledger: EventLedger):
        self.ledger = ledger

    def select(self, summaries: Iterable[Mapping[str, Any]]) -> EventSelection:
        rows = list(summaries)
        selected: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        used_keys: set[str] = set()
        for row in rows:
            for candidate in row.get("next_event_candidates") or []:
                if not isinstance(candidate, Mapping) or not candidate.get("event_type"):
                    continue
                item = dict(candidate)
                key = str(item.get("concurrency_key") or item.get("project_id") or item["event_type"])
                if item.get("requires_human"):
                    rejected.append({**item, "reason": "awaiting_human_decision"})
                elif any(
                    (not str(ref).startswith("summary:")) or not self.ledger.has_summary(str(ref))
                    for ref in item.get("prerequisites") or []
                ):
                    rejected.append({**item, "reason": "unresolved_prerequisite"})
                elif key in used_keys:
                    rejected.append({**item, "reason": "concurrency_key_busy"})
                else:
                    used_keys.add(key)
                    selected.append(item)
        selector = self.ledger.create(
            "selector.next_events", "selector",
            correlation_id=str(rows[0].get("correlation_id") or "") if rows else "",
            payload={"considered": [str(row.get("event_id") or "") for row in rows]},
        )
        selection = EventSelection(
            selection_id=f"sel_{uuid.uuid4().hex[:16]}", selector_event_id=selector.event_id,
            considered_event_ids=[str(row.get("event_id") or "") for row in rows],
            selected=selected, rejected=rejected,
            rationale="selected every ready Event with a distinct concurrency key", created_at=_now(),
        )
        self.ledger.record_selection(selection)
        from .models import EventSummary
        self.ledger.complete(selector, EventSummary(
            event_id=selector.event_id, status="completed", headline="下一组 Event 已选择",
            outcome=f"{len(selected)} selected, {len(rejected)} deferred",
            metrics={"selected": len(selected), "deferred": len(rejected)},
            notification_kind="routine",
        ))
        return selection
