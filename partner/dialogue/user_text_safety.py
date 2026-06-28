"""User-facing text cleanup.

This module is deliberately small and dependency-free so every outbound path
can share the same leak filter before text reaches QQ or other user channels.
"""

from __future__ import annotations

import re


_DIFF_START_RE = re.compile(
    r"^\s*(?:┊\s*)?(?:review diff|diff --git\b|index [0-9a-f]{6,}\.\.|---\s+[ab]/|\+\+\+\s+[ab]/|@@\s+-\d+(?:,\d+)?\s+\+\d+)",
    re.I,
)


def strip_internal_diff(text: str) -> str:
    """Remove model/tool diff previews from user-visible text."""
    if not text:
        return ""
    lines: list[str] = []
    in_diff = False
    for raw in str(text).splitlines():
        stripped = raw.strip()
        if _DIFF_START_RE.search(stripped):
            in_diff = True
            continue
        if in_diff:
            if re.match(r"^\s*(ACTION|DONE|FINDINGS|EVIDENCE|NEXT|FILES|STATE_DELTA|ARTIFACT_CONTENT):\s*", stripped):
                in_diff = False
            elif (
                stripped.startswith("+")
                or stripped.startswith("-")
                or stripped.startswith("|")
                or stripped.startswith("#")
                or stripped == ""
            ):
                continue
            else:
                # Diff previews from Hermes usually end once normal prose or
                # the strict result fields resume.
                in_diff = False
        if not in_diff:
            lines.append(raw)
    return "\n".join(lines).strip()


def has_internal_diff(text: str) -> bool:
    return bool(text and _DIFF_START_RE.search(str(text)))

