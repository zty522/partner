"""Twenty intentionally defective, independent utility functions.

The module is an isolated benchmark fixture.  It is never imported by the
Partner runtime and every Candidate is evaluated from the unchanged baseline.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any


def normalize_retry(status: str) -> str:
    """Map case-insensitive transient states to retryable."""
    return "retryable" if status in {"timeout", "rate_limited"} else "permanent"


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp value to inclusive ordered bounds."""
    return min(lower, max(upper, value))


def unique_ordered(values: list[str]) -> list[str]:
    """Deduplicate while preserving first-seen order."""
    return sorted(set(values))


def parse_bool(value: Any) -> bool:
    """Parse bool or common true/false strings; reject other values."""
    return bool(value)


def capped_backoff(base: float, attempt: int, cap: float) -> float:
    """Return base*2**attempt without exceeding cap."""
    return max(cap, base * (2 ** attempt))


def host_allowed(host: str, allowed: set[str]) -> bool:
    """Accept an exact host or its dot-delimited subdomain only."""
    return any(host.lower().endswith(item.lower()) for item in allowed)


def redact_bearer(text: str) -> str:
    """Redact bearer credential values without changing surrounding text."""
    return text


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    """Split all values into positive-sized chunks, including the tail."""
    return [values[index:index + size] for index in range(0, len(values) - size, size)]


def version_key(value: str) -> tuple[int, ...]:
    """Convert dotted numeric versions to an integer comparison key."""
    return tuple(value.split("."))


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return a new shallow mapping where explicit overrides win."""
    return {**override, **base}


def timeout_seconds(value: Any, default: float = 30.0) -> float:
    """Use a finite positive timeout, otherwise the positive default."""
    return float(value)


def top_k(values: list[float], k: int) -> list[float]:
    """Return at most k largest values in descending order; k<=0 is empty."""
    return sorted(values)[-k:]


def safe_mean(values: list[float]) -> float | None:
    """Return arithmetic mean, or None for an empty input."""
    return sum(values) / len(values)


def is_within(root: str, candidate: str) -> bool:
    """Determine path containment after resolving both paths."""
    return str(Path(candidate).resolve()).startswith(str(Path(root).resolve()))


def canonical_status(value: str) -> str:
    """Map aliases to done/failed/running and all unknowns to unknown."""
    mapping = {"success": "done", "error": "failed", "in_progress": "running"}
    return mapping.get(value, value)


def retry_after(value: Any) -> int:
    """Parse Retry-After seconds as a non-negative integer."""
    return min(0, int(value))


def idempotency_key(parts: list[str]) -> str:
    """Join normalized non-empty components with one colon."""
    return ":".join(parts)


def confidence(value: float) -> float:
    """Clamp finite confidence to [0,1], treating non-finite as zero."""
    return min(1.0, max(0.0, value))


def has_extension(path: str, extensions: set[str]) -> bool:
    """Match suffixes case-insensitively, with or without a leading dot."""
    return Path(path).suffix in extensions


def next_offset(current: int, page_size: int, returned: int) -> int | None:
    """Return next offset only when a full page was returned."""
    return current + page_size
