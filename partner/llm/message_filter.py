"""Message filter — apply content filters to user-facing messages.

Reads filter rules from configs/message_filter.yaml and applies them to
remove internal JSON, step IDs, dependency info, and other noise from
user-facing messages.

Only these message types should reach users:
  [进度] human-readable progress
  [完成] delivery notification
  [错误] error report
  Final summary in natural language
"""

from __future__ import annotations
import logging, os, re, time
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Rate-limit cache: {(normalized_text): last_sent_timestamp}
_rate_limit_cache: dict[str, float] = {}

_FILTER_CACHE: dict[str, list[dict]] = {}
_FILTER_MTIME: float = 0.0


def load_filters() -> list[dict]:
    """Load message filter rules from config, with file-watch caching."""
    global _FILTER_CACHE, _FILTER_MTIME
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "configs", "message_filter.yaml"),
        os.path.expanduser("~/.partner/message_filter.yaml"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                mtime = os.path.getmtime(path)
                if path in _FILTER_CACHE and mtime == _FILTER_MTIME:
                    return _FILTER_CACHE[path]
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                filters = data.get("filters", []) if isinstance(data, dict) else []
                if isinstance(filters, list):
                    _FILTER_CACHE[path] = filters
                    _FILTER_MTIME = mtime
                    return filters
            except Exception as exc:
                logger.debug("[FILTER] failed to load %s: %s", path, exc)
                return []
    return []


def apply_filters(text: str) -> str:
    """Apply all loaded filters to a message.

    suppress action: removes the matched line entirely.
    replace action: replaces matched text with configured replacement.
    Returns the filtered text.
    """
    filters = load_filters()
    lines = (text or "").splitlines()
    out_lines: list[str] = []

    for line in lines:
        suppressed = False
        for rule in filters:
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("pattern", "")).strip()
            if not pattern:
                continue
            action = str(rule.get("action", "suppress")).strip().lower()
            try:
                # Apply replacement first (can modify the line)
                if action == "replace":
                    replacement = str(rule.get("replacement", "")).strip()
                    if re.search(pattern, line):
                        line = re.sub(pattern, replacement, line)
                # Then check for suppression
                elif action == "suppress":
                    if re.search(pattern, line):
                        suppressed = True
                        break
            except re.error:
                continue
        if not suppressed:
            out_lines.append(line)

    return "\n".join(out_lines).strip()


def should_suppress_message(text: str) -> bool:
    """Check if the entire message should be suppressed (e.g. it's all noise)."""
    filtered = apply_filters(text)
    if not filtered:
        return True
    return filtered.strip() == ""


def is_rate_limited(text: str, cooldown_sec: float = 1.0) -> bool:
    """Check if a similar message was sent too recently.

    Normalizes by stripping timestamps and numbers.
    """
    normalized = re.sub(r"\d{1,4}([/-]\d{1,4}){0,2}", "<t>", str(text or ""))
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    normalized = normalized.strip()

    now = time.time()
    last = _rate_limit_cache.get(normalized, 0.0)
    if last and (now - last) < cooldown_sec:
        return True
    _rate_limit_cache[normalized] = now
    return True  # always update timestamp


def sanitize_for_user(text: str) -> str:
    """Full sanitization pipeline: depends → suppress → limit → return.

    Returns empty string if message should be suppressed entirely.
    """
    # Step 1: Remove dependency info
    cleaned = re.sub(r"依赖：\[.*?\]", "", str(text or ""))
    # Step 2: Remove step IDs in brackets
    cleaned = re.sub(r"\[[a-z_]+\d*\]", "", cleaned)
    # Step 3: Remove internal JSON
    cleaned = re.sub(r"\{[^}]*\"event_type\"[^}]*\}", "[计划步骤]", cleaned)
    # Step 4: Apply YAML-based filters
    cleaned = apply_filters(cleaned)
    # Step 5: Final cleanup
    cleaned = cleaned.strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned


def format_user_message(prefix: str, content: str, files: list[str] | None = None) -> str:
    """Format a clean user-facing message.

    Allowed prefixes: [进度], [完成], [错误]
    """
    # Sanitize content
    clean_content = sanitize_for_user(content)
    if not clean_content:
        return ""

    # Build message
    parts = [f"[{prefix}] {clean_content}"]
    if files:
        file_names = [os.path.basename(f) for f in files if f]
        if file_names:
            parts.append(f"\n已生成：{', '.join(file_names)}")

    return "\n".join(parts).strip()
