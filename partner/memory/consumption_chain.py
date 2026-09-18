"""Memory consumption evidence chain (M1 / Section 4).

The verification pass needs to prove that a lesson written by
``memory_consolidate`` is later **used** by ``recall`` and the
**usage_outcomes.jsonl** row records the consumption.  This module
provides the helpers:

* ``usage_recorded(entry_id)`` — returns True iff a usage row exists
  for ``entry_id`` in ``usage_outcomes.jsonl``.
* ``consumption_chain(workspace, entry_id)`` — returns the ordered
  list of events that touched ``entry_id`` (write, recall, outcome).
* ``unconsumed_lessons(workspace)`` — returns lessons with no usage row.

These helpers read the JSONL directly (the indexed memory layer caches
it).  They are intended for unit tests, not hot-path code.
"""
from __future__ import annotations

import json
from pathlib import Path


def _memory_root(workspace: "Path") -> Path:
    return Path(workspace) / "share" / "mind" / "memory"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def usage_recorded(workspace, entry_id: str) -> bool:
    """Return True iff a usage row references ``entry_id``."""
    p = _memory_root(workspace) / "usage_outcomes.jsonl"
    rows = _read_jsonl(p)
    return any(r.get("memory_id") == entry_id or entry_id in (r.get("memory_ids") or [])
               for r in rows)


def consumption_chain(workspace, entry_id: str) -> list[dict]:
    """Return the ordered list of events that touched ``entry_id``.

    Events considered: lessons.jsonl (write), usage_outcomes.jsonl
    (recall), usage.jsonl (applied / rejected).  Sorted by
    ``recorded_at`` if present, otherwise by file order.
    """
    root = _memory_root(workspace)
    chain: list[dict] = []
    for row in _read_jsonl(root / "lessons.jsonl"):
        if row.get("record_id") == entry_id:
            chain.append({"kind": "write", "row": row})
    for row in _read_jsonl(root / "usage_outcomes.jsonl"):
        if row.get("memory_id") == entry_id:
            chain.append({"kind": "recall", "row": row})
    for row in _read_jsonl(root / "usage.jsonl"):
        if entry_id in (row.get("memory_ids") or []):
            chain.append({"kind": "apply", "row": row})
    chain.sort(key=lambda e: (e["row"].get("recorded_at") or ""))
    return chain


def unconsumed_lessons(workspace) -> list[dict]:
    """Return lessons with NO usage row."""
    root = _memory_root(workspace)
    lessons = _read_jsonl(root / "lessons.jsonl")
    used_ids = {r.get("memory_id") for r in _read_jsonl(root / "usage_outcomes.jsonl")}
    used_ids.update(*[set(r.get("memory_ids") or []) for r in _read_jsonl(root / "usage.jsonl")])
    return [l for l in lessons if l.get("record_id") not in used_ids]


__all__ = ["usage_recorded", "consumption_chain", "unconsumed_lessons"]
