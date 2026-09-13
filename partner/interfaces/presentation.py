"""Shared user-facing semantics for QQ, TUI and desktop GUI.

This module is deliberately presentation-only.  It classifies an already
selected Event; it never selects an action, changes Reward, or claims a
delivery.  Keeping that boundary here prevents each frontend from inventing a
second runtime while still giving users one stable vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ActivityKind:
    key: str
    label: str
    icon: str
    color: str
    report_label: str


PROJECT = ActivityKind("project", "项目推进", "📌", "#2F6FED", "项目推进报告")
ACTIVE_LEARNING = ActivityKind("active_learning", "主动学习", "🔎", "#9A5B13", "主动学习报告")
SELF_EVOLUTION = ActivityKind("self_evolution", "Partner 自进化", "🛠", "#7A4CC2", "自进化报告")
WAITING = ActivityKind("waiting", "受控等待", "⏸", "#667085", "等待说明")
FAILURE = ActivityKind("failure", "未通过", "❌", "#C0362C", "失败说明")

KINDS = {kind.key: kind for kind in (PROJECT, ACTIVE_LEARNING, SELF_EVOLUTION, WAITING, FAILURE)}

_LEARNING_MARKERS = (
    "external_learning", "external_knowledge", "research_learning", "research_adoption",
    "literature", "github", "paper", "主动学习", "外部知识", "源码学习", "文献学习",
)
_EVOLUTION_MARKERS = (
    "self_evolution", "evolution", "code_candidate", "candidate_autonomous",
    "policy_learning", "promotion", "rollback", "issue_repair", "partner 自进化",
    "自进化诊断", "代码 candidate", "agent_active_learning_",
)


def _haystack(*values: object) -> str:
    return " ".join(str(value or "").lower() for value in values)


def classify_activity(
    *,
    instance_id: str = "",
    event_type: str = "",
    strategy_id: str = "",
    instruction: str = "",
    result: Mapping[str, Any] | None = None,
) -> ActivityKind:
    """Classify an Event for display without changing its execution route."""
    payload = result or {}
    nested = payload.get("result") if isinstance(payload, Mapping) else {}
    nested = nested if isinstance(nested, Mapping) else {}
    text = _haystack(event_type, strategy_id, instruction, payload.get("activity_kind"),
                     nested.get("activity_kind"), nested.get("strategy_id"))
    if any(marker in text for marker in _EVOLUTION_MARKERS):
        return SELF_EVOLUTION
    if any(marker in text for marker in _LEARNING_MARKERS):
        return ACTIVE_LEARNING
    if str(instance_id) == "05" and any(marker in text for marker in ("hermes", "partner", "策略", "框架")):
        return SELF_EVOLUTION
    # Instance identity alone is not enough: 04/05 may execute ordinary project
    # work.  Only the selected Event/strategy/instruction determines the label.
    return PROJECT


def terminal_kind(activity: ActivityKind, result: Mapping[str, Any] | None) -> ActivityKind:
    payload = result or {}
    nested = payload.get("result") if isinstance(payload, Mapping) else {}
    nested = nested if isinstance(nested, Mapping) else {}
    if payload.get("blocked") or nested.get("blocked") or str(payload.get("status", "")).lower() in {
        "waiting", "blocked", "paused",
    }:
        return WAITING
    if payload and not bool(payload.get("ok")):
        return FAILURE
    return activity


def activity_heading(activity: ActivityKind, instance_id: str) -> str:
    return f"{activity.icon} {activity.label}｜{instance_id or '?'}"


def _safe_topic(value: str, fallback: str) -> str:
    value = re.sub(r"\[[^\]]+\]", " ", str(value or ""))
    value = re.sub(r"(?:strategy_id|event_type)\s*[=:]\s*[^\s]+", " ", value, flags=re.I)
    value = Path(value).stem if ("/" in value or "\\" in value) else value
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", value).strip("_- ")
    return (value[:36] or fallback).strip("_-")


def report_filename(
    *,
    instance_id: str,
    activity: ActivityKind,
    title: str = "",
    strategy_id: str = "",
) -> str:
    """Return a readable QQ display filename; the evidence file stays intact."""
    topic = _safe_topic(title or strategy_id, "本轮结果")
    return f"{instance_id or 'Partner'}_{activity.report_label}_{topic}.pdf"


def compact_message(text: str, *, limit: int = 520) -> str:
    """Remove empty template chatter while preserving meaningful line breaks."""
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or line in {"正在思考...", "思考中...", "处理中...", "本轮完成"}:
            continue
        if lines and line == lines[-1]:
            continue
        lines.append(line)
    value = "\n".join(lines)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


__all__ = [
    "ACTIVE_LEARNING", "FAILURE", "KINDS", "PROJECT", "SELF_EVOLUTION", "WAITING",
    "ActivityKind", "activity_heading", "classify_activity", "compact_message",
    "report_filename", "terminal_kind",
]
