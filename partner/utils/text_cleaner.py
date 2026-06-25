"""Text cleaning utilities — strip internal markers, file paths, and diff artifacts
from user-facing content (Markdown, PDF content, reports)."""

from __future__ import annotations

import re
from typing import Any

# Patterns that should never appear in user-facing output
_INTERNAL_PATTERNS: list[tuple[str, str]] = [
    # ── Diff markers ──────────────────────────────────────────────────
    # Diff headers — no ^ anchor, because they can appear after "- " or other prefixes
    (r"(?m)^.*?@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@.*$", ""),  # @@ -1,7 +1,6 @@
    (r"(?m)^.*?---\s+a/.*$", ""),   # --- a/file.py
    (r"(?m)^.*?\+\+\+\s+b/.*$", ""), # +++ b/file.py
    (r"(?m)^.*?review\s+diff\s+.*$", ""),  # - review diff a → b

    # Diff table marker lines that start with + (diff addition)
    (r"(?m)^\+[-]+\+[-]+\+.*$", ""),  # +---+---+---+
    (r"(?m)^\+[-| ]+\+[-| ]+\+.*$", ""),  # +---+---+---+ (border variants)

    # Diff column separator character
    (r"┊", ""),

    # Lines starting with "+" that look like diff additions (not markdown list items)
    # "+# heading", "+## heading", "+ # heading" — diff-added heading
    (r"(?m)^\+[ \t]*#+.*$", ""),
    # "+> blockquote" or "+ > blockquote" — diff-added blockquote
    (r"(?m)^\+[ \t]*>.*$", ""),
    # "+| table" or "+ | table" — diff-added table row
    (r"(?m)^\+[ \t]*\|.*$", ""),

    # Lines starting with "-" that look like diff removals
    # "-# heading", "-## heading" — diff-removed heading
    (r"(?m)^-[ \t]*#+.*$", ""),
    # "-> blockquote" — diff-removed blockquote
    (r"(?m)^-[ \t]*>.*$", ""),
    # "-| table" — diff-removed table row
    (r"(?m)^-[ \t]*\|.*$", ""),
    # Bare "+" or "-" lines (remnants after diff line removal)
    (r"(?m)^\+\s*$", ""),
    (r"(?m)^-\s*$", ""),

    # ── Step result JSON embedded in text ─────────────────────────────
    # Full-step result JSON block (multi-line, starts with {"ok": ... or {"parsed": ...)
    (r"(?ms)\{\s*\"ok\"\s*:\s*(?:true|false)[^}]*\"event_type\"\s*:\s*\"[^\"]+\"[^}]*\}", ""),
    # Shorthand: any inline {"ok": ...} or {"parsed": ...} that is clearly step output
    (r"(?ms)\{\s*\"parsed\"\s*:\s*\{[^}]+}[^}]*\"event_type\"\s*:\s*\"[^\"]+\"[^}]*\}", ""),
    # Standalone {"ok": true, "path": "...", "files": [...]} artifact result
    (r"(?ms)\{\s*\"ok\"\s*:\s*(?:true|false)\s*,\s*\"path\"\s*:.*?\"files\"\s*:\s*\[.*?\].*?\}", ""),

    # ── Internal JSON metadata keys (anywhere in text) ────────────────
    (r'"event_type"\s*:\s*"[^"]+"', ""),
    (r'"result_json_path"\s*:\s*"[^"]+"', ""),
    (r'"ok"\s*:\s*(?:true|false)', ""),  # "ok": true/false — step result marker
    (r'"action"\s*:\s*"[^"]+"', ""),  # "action": "write_artifact" etc.
    (r'"next_action"\s*:\s*"[^"]+"', ""),
    (r'"state_delta"\s*:\s*"[^"]+"', ""),
    (r'"step_done"\s*:\s*"[^"]*"', ""),  # "step_done": "..." — step completion status
    (r'"artifact_content"\s*:\s*"[^"]*"', ""),  # "artifact_content": "..."
    (r'"files"\s*:\s*\[[^\]]*\]', ""),  # "files": [...] — file list
    (r'"files"\s*:\s*"[^"]*"', ""),  # "files": "path1, path2"
    (r'"findings"\s*:\s*\[[^\]]*\]', ""),  # "findings": [...] — step results
    (r'"evidence"\s*:\s*"[^"]*"', ""),  # "evidence": "..." — evidence string
    (r'"content"\s*:\s*"[^"]{50,}"', ""),  # "content": "long text..." — internal content
    (r'"parsed"\s*:\s*\{', ""),  # "parsed": { — start of parsed block

    # ── Variable references that shouldn't leak ───────────────────────
    # $step_X.Y or $step_X_Y notation (unresolved template vars)
    (r"\$step_\w+(?:\.\w+)*", "[步骤引用]"),

    # ── Internal file paths ───────────────────────────────────────────
    (r"_step_\w+\.result\.json", "[步骤结果]"),
    (r"task_instance\.json", ""),
    (r"task_log\.jsonl", ""),
    (r"_error_report\.md", ""),
    (r"_missing_artifacts\.md", ""),
    (r"fallbacks?/[\w.-]+", ""),
    (r"/home/\w+/partner_workspace/instances/\d+/tasks/[\w-]+/", ""),
    (r"/mnt/[a-z]/work/partner_workspace[\w/-]*", ""),

    # ── Internal step IDs and event names ─────────────────────────────
    (r"step_\d+(?:_result)?\.json", ""),
    (r"atomic_[a-z_]+", ""),
    (r"smart_llm_structured_action", ""),

    # ── LaTeX math — convert to plain text approximation ──────────────
    (r"\$\$\s*(.*?)\s*\$\$", r"\1"),  # $$...$$ display math → keep content, drop delimiters
    (r"\$([^$]+)\$", r"\1"),          # $...$ inline math → keep content, drop delimiters

    # ── Plan template leakage ─────────────────────────────────────────
    # Batch planner structure headings (Chinese + English)
    (r"(?m)^#\s*批量规划结果.*$", ""),
    (r"(?m)^#\s*Harness\s+MicroPlan.*$", ""),
    (r"(?m)^#\s*Harness\s+Plan.*$", ""),
    (r"(?m)^##\s*Phase\s+\d+:.*$", ""),
    (r"(?m)^###?\s*Step\s+\d+\.\d+:.*$", ""),

    # ── Tool noise / CLI denial messages ──────────────────────────────
    # Hermes CLI timeout / denial (e.g. "⏱ Timeout — denying command")
    (r"⏱\s*Timeout\s*[—\-]\s*denying\s+command", ""),
    (r"⏱\s*.*?denying\s+command", ""),
    (r"(?m)^.*?用户已阻止.*?$\n?", ""),
    (r"(?m)^.*?needs your approval.*?$\n?", ""),

    # ── Diff "+-" prefix (diff addition of list item) ───────────────────
    (r"(?m)^\+-\s+", ""),
    # Diff "+=" and "+-" border lines (e.g. "+========", "+------")
    (r"(?m)^\+[=\-]+\s*$", ""),
    # Bare "+" prefix on any content line (diff addition remnant)
    (r"(?m)^\+。?\s*", ""),

    # ── Hermes startup/model noise ──────────────────────────────────────
    (r"⚠️.*?Normalized model.*?for\s+\w+\.?\s*", ""),
    # Any ⚠️ warning line
    (r"(?m)^⚠️.*$\n?", ""),

    # Bare denial remnants after cleaning
    (r"(?m)^\s*⏱.*$\n?", ""),

    # ── Collapse multiple blank lines ─────────────────────────────────
    (r"\n{3,}", "\n\n"),

    # ── Clean up JSON artifact remnants ───────────────────────────────
    # Empty braces left after key removal (with possible commas inside)
    (r"\{\s*,?\s*,?\s*\}|\{\s*\}", ""),
    # Trailing comma before closing brace
    (r",\s*\}", ""),
    # Multiple consecutive commas from removed keys
    (r",\s*,+", ", "),
]


def clean_user_facing_text(text: str, *, preserve_newlines: bool = True) -> str:
    """Remove internal markers, file paths, and diff artifacts from user-facing text.

    Args:
        text: Raw text that may contain internal markers.
        preserve_newlines: If True, keeps paragraph structure; otherwise collapses.

    Returns:
        Cleaned text safe for user-facing output.
    """
    if not text:
        return ""

    cleaned = str(text)

    for pattern, replacement in _INTERNAL_PATTERNS:
        try:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.MULTILINE | re.IGNORECASE)
        except re.error:
            continue

    # Clean up lines that are now empty due to removals
    lines = cleaned.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if preserve_newlines:
                cleaned_lines.append("")
            continue
        # Skip lines that consist only of removed content
        cleaned_lines.append(stripped)

    if preserve_newlines:
        cleaned = "\n".join(cleaned_lines)
    else:
        cleaned = " ".join(line for line in cleaned_lines if line)

    # Final collapse of multiple blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def has_internal_markers(content: str) -> bool:
    """Check if content contains internal markers that should never reach a user.

    Returns True if any diff marker, internal path, or step ID is detected.
    Used as a last-line defense before PDF generation.
    """
    if not content:
        return False
    # Multi-line patterns that indicate internal artifact leakage
    internal_markers = [
        r"review\s+diff\s+",
        r"@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@",
        r"---\s+a/",
        r"\+\+\+\s+b/",
        r"_step_\w+\.result\.json",
        r"task_instance\.json",
        r"_error_report\.md",
        r"\+-+\+-+\+",  # diff table borders like +---+---+---+
        r'"event_type"\s*:\s*"',        # internal JSON dump
        r'"result_json_path"\s*:\s*"',  # internal JSON dump
        r'\$step_\w+',                  # unresolved variable reference
        r'\{\s*"ok"\s*:\s*(?:true|false)',  # JSON result block
    ]
    for pattern in internal_markers:
        try:
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def is_fallback_or_placeholder(content: str) -> bool:
    """Check if content looks like a fallback/placeholder rather than real output."""
    if not content or len(content) < 100:
        return True
    first_200 = content.lstrip()[:200].lower()
    markers = [
        "# fallback draft",
        "# partial artifact",
        "_error_report",
        "timeout after",
        "dependency failed",
        "placeholder content",
    ]
    return any(m in first_200 for m in markers)
