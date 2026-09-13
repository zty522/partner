"""Frontend-safe projections of structured Event summaries."""

from __future__ import annotations

import re
from typing import Any, Mapping


SERIES_LABELS = {
    "interaction": ("对话", "#8B5CF6"),
    "project": ("项目推进", "#3B82F6"),
    "active_learning": ("外部学习", "#14B8A6"),
    "self_evolution": ("系统改进", "#F59E0B"),
    "planning": ("计划判断", "#6366F1"),
    "memory": ("记忆", "#0F766E"),
    "presentation": ("内容整理", "#EC4899"),
    "delivery": ("交付", "#64748B"),
    "runtime": ("运行状态", "#78716C"),
    "selector": ("调度判断", "#A78BFA"),
    "frontend": ("交互", "#64748B"),
}


def _human_outcome(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("human_message") or "").strip()
    if explicit:
        return explicit
    raw = str(row.get("outcome") or "").strip()
    if "molecular_method_candidate_benchmark" in raw:
        def value(name: str) -> str:
            match = re.search(rf"(?:^|[;,]\s*){re.escape(name)}=([^;,]+)", raw)
            return match.group(1).strip() if match else ""
        method = {
            "maxmin_fingerprint": "MaxMin 指纹选择",
            "scaffold_round_robin": "骨架轮转选择",
            "pareto_diverse": "Pareto 多样性选择",
        }.get(value("method"), value("method") or "候选方法")
        improved = value("candidate_improved").lower() in {"true", "1", "yes"}
        metrics = [
            ("seed", value("replicate_seed")),
            ("骨架数变化", value("scaffold_count_delta")),
            ("指纹多样性变化", value("fingerprint_diversity_delta")),
            ("平均 QED 变化", value("mean_qed_delta")),
        ]
        detail = "；".join(f"{label} {metric}" for label, metric in metrics if metric)
        verdict = "候选优于本轮基线" if improved else "候选未优于基线，本轮不晋升"
        return f"同数据预算分子候选对照已完成：{method}；{detail}。{verdict}。"
    # Frontends should not expose local absolute paths in routine cards.
    return re.sub(r"(?:[A-Za-z]:[\\/]|/)[^\s;,]+", "[本地证据]", raw)


def project_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    series = str(row.get("series") or "project")
    label, color = SERIES_LABELS.get(series, (series, "#64748B"))
    return {
        "event_id": str(row.get("event_id") or ""), "series": series,
        "work_item_id": str(row.get("work_item_id") or row.get("job_id") or ""),
        "continuation_owner": str(row.get("continuation_owner") or ""),
        "series_label": label, "color": color, "status": str(row.get("status") or ""),
        "headline": str(row.get("headline") or row.get("event_type") or "Event"),
        "outcome": _human_outcome(row), "project_id": str(row.get("project_id") or ""),
        "at": str(row.get("created_at") or row.get("updated_at") or ""),
        "evidence_count": len(row.get("evidence_refs") or []),
        "artifact_count": len(row.get("artifacts") or []),
        "business_delta": bool(row.get("business_delta")),
        "learning_delta": bool(row.get("learning_delta")),
        "evolution_delta": bool(row.get("evolution_delta")),
        "requires_human": bool(row.get("requires_human")),
        "notification_kind": str(row.get("notification_kind") or "routine"),
        "completion": dict(row.get("completion") or {}),
    }
