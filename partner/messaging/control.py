"""Control signals between the message thread (instance) and the engine
(shared worker).  Written by MessageAgent on a user instruction; consumed
by EventWorker at safe node boundaries and idle scheduling.

Kinds:
- ``stop``    : finish the current step safely, do not start new work
- ``pause``   : finish the current step safely, hold the rest of the job
- ``resume``  : clear pause/stop for this user
- ``direct``  : redirect current effort (a new bounded task supersedes)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

CONTROL_REL = "state/application/control"


def _control_dir(root: str | os.PathLike) -> Path:
    return Path(root) / CONTROL_REL


def write_control(
    root: str | os.PathLike,
    *,
    kind: str,
    value: str = "",
    reason: str = "",
    user: str = "",
    persona_hint: str = "",
) -> dict:
    """Persist one control signal.  Returns the record dict."""
    directory = _control_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "control_id": f"ctl_{uuid.uuid4().hex[:12]}",
        "kind": str(kind),
        "value": str(value or "")[:2000],
        "reason": str(reason or "")[:2000],
        "user": str(user or ""),
        "persona_hint": str(persona_hint or ""),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "consumed": False,
    }
    target = directory / f"{record['control_id']}.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)
    return record


def read_controls(
    root: str | os.PathLike,
    *,
    unconsumed: bool = True,
    persona_hint: str = "",
    kinds: tuple[str, ...] | None = None,
) -> list[dict]:
    """Return control records, newest last (chronological)."""
    directory = _control_dir(root)
    if not directory.exists():
        return []
    rows: list[dict] = []
    for path in sorted(directory.glob("ctl_*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if unconsumed and row.get("consumed"):
            continue
        if persona_hint and str(row.get("persona_hint") or "") != persona_hint:
            continue
        if kinds and str(row.get("kind") or "") not in kinds:
            continue
        rows.append(row)
    return rows


def consume_control(root: str | os.PathLike, control_id: str) -> bool:
    """Mark one control consumed (audit trail, file kept)."""
    directory = _control_dir(root)
    path = directory / f"{control_id}.json"
    if not path.is_file():
        return False
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    row["consumed"] = True
    row["consumed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)
    return True


def clear_controls(root: str | os.PathLike, *, persona_hint: str = "") -> int:
    """Consume all unconsumed controls for a user; return count."""
    count = 0
    for row in read_controls(root, unconsumed=True, persona_hint=persona_hint):
        if consume_control(root, str(row.get("control_id") or "")):
            count += 1
    return count


def active_controls(root: str | os.PathLike, *, persona_hint: str = "") -> list[dict]:
    """Unconsumed controls that still apply (for engine scheduling)."""
    return read_controls(root, unconsumed=True, persona_hint=persona_hint)


__all__ = [
    "write_control", "read_controls", "consume_control", "clear_controls",
    "active_controls",
]
