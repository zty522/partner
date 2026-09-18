"""Memory outcome promotion (M1 / Section 4 / round 5).

When ``EventMemory.record_usage(..., status="referenced")`` is called
inside ``_deliberate``, the row is recorded as a **reference**, not an
application.  Only after the downstream Event succeeds does the
memory become truly applied.

``promote_usage`` takes a list of ``memory_id`` + ``status`` ("applied"
or "rejected") and writes a row to ``usage_outcomes.jsonl``.  The
EventMemory layer reads this file in ``recall`` to filter out lessons
that were rejected more than once, so genuine failures don't keep
getting re-cited.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def promote_usage(*, workspace_root: str | Path, memory_ids: list[str],
                   status: str, effect: dict | None = None,
                   event_id: str = "", flow_id: str = "") -> Path:
    """Append a promotion row to ``usage_outcomes.jsonl``.

    ``status`` must be one of ``"applied"``, ``"rejected"``, or
    ``"irrelevant"``.  Raises ``ValueError`` otherwise.
    """
    if status not in ("applied", "rejected", "irrelevant"):
        raise ValueError(f"invalid promotion status: {status!r}")
    root = Path(workspace_root).expanduser().resolve()
    target = root / "share" / "mind" / "memory" / "usage_outcomes.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": "usage_outcome/v1",
        "recorded_at": time.time(),
        "memory_ids": list(memory_ids),
        "status": status,
        "effect": dict(effect or {}),
        "event_id": event_id,
        "flow_id": flow_id,
    }
    with target.open("a", encoding="utf-8") as fh:
        import fcntl as _fcntl
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
    return target


__all__ = ["promote_usage"]
