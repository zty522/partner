"""ReadPlan / ReadReceipt: explicit read budget tracking.

Every Event / consumer that issues a read must declare the read
plan up front (purpose, filters, caps, freshness).  After the read,
the actual bytes consumed and selection reason are reported in a
ReadReceipt so we can audit budget compliance.

Budget violations are returned as `truncated=True` so callers cannot
mistake a partial read for a complete one.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReadPlan:
    purpose: str
    scope: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    max_results: int = 100
    max_bytes: int = 60_000
    max_files: int = 5
    max_sections: int = 5
    freshness_requirement: str = "stale_ok"  # strict | loose | stale_ok
    index_generation: int = 0
    selected_resource_ids: list[str] = field(default_factory=list)
    selection_reason: str = ""


@dataclass
class ReadReceipt:
    purpose: str
    scope: str
    bytes_read: int
    files_opened: int
    cache_hit: bool
    truncated: bool
    remaining_cursor: str
    error: str = ""
    selection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BudgetExceeded(Exception):
    """Raised when a read would exceed the declared ReadPlan budget."""

    def __init__(self, plan: ReadPlan, message: str):
        self.plan = plan
        super().__init__(message)


def open_section(path: Path, *, byte_offset: int, byte_length: int,
                 plan: ReadPlan, received: dict[str, int]) -> str | None:
    """Read a section bounded by the stored byte offsets.

    Bumps `received` (in-place) with the actual bytes read.  Returns
    the text or `None` if reading would exceed `max_bytes`.
    """
    remaining = plan.max_bytes - received.get("bytes", 0)
    if remaining <= 0:
        return None
    wanted = min(byte_length, remaining)
    try:
        with open(path, "rb") as handle:
            handle.seek(byte_offset)
            data = handle.read(wanted)
    except OSError:
        return None
    received["bytes"] = received.get("bytes", 0) + len(data)
    received["files"] = received.get("files", 0) + 1
    return data.decode("utf-8", errors="replace")
