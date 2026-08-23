"""Conservative runtime signal detection for actionable evolution issues."""
from __future__ import annotations

from typing import Any

from .evolution_loop import record_issue


FAILURE_STATUSES = {"failed", "error", "partial", "missing", "content_quality_failed", "login_not_verified"}


def detect_signals(
    *,
    instance_id: str,
    project_id: str = "",
    expected_outputs: bool = False,
    files: list[str] | None = None,
    event_types: list[str] | None = None,
    result: dict[str, Any] | None = None,
    prior_event_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return high-signal issues only; absence of evidence is not guessed."""
    files = list(files or [])
    events = list(event_types or [])
    prior = list(prior_event_types or [])
    result = dict(result or {})
    issues: list[dict[str, Any]] = []
    status = str(result.get("status") or "").lower()
    if result.get("ok") is False or status in FAILURE_STATUSES:
        issues.append({
            "summary": f"事件执行返回失败状态: {status or 'ok=false'}",
            "category": "event",
            "severity": "high",
            "evidence": [str(result.get("error") or result)[:800]],
        })
    if expected_outputs and not files:
        issues.append({
            "summary": "任务完成回调没有当前任务产物",
            "category": "verification",
            "severity": "high",
            "evidence": [f"events={events}"],
        })
    if result.get("delivery_required") and not result.get("delivery_confirmed"):
        issues.append({
            "summary": "任务需要用户交付但缺少渠道确认",
            "category": "delivery",
            "severity": "critical",
            "evidence": [f"status={status}", f"files={files[:5]}"],
        })
    width = len(events)
    if width and len(prior) >= width * 2:
        previous = prior[-width:]
        before_previous = prior[-2 * width:-width]
        if events == previous == before_previous:
            issues.append({
                "summary": "连续三轮执行相同事件，可能没有产生新证据",
                "category": "planning",
                "severity": "medium",
                "evidence": [f"events={events}", f"prior_tail={prior[-8:]}"],
            })
    for issue in issues:
        issue["instance_id"] = instance_id
        issue["project_id"] = project_id
    return issues


def detect_and_record(workspace: str, **kwargs: Any) -> list[dict[str, Any]]:
    recorded = []
    for issue in detect_signals(**kwargs):
        result = record_issue(workspace, issue)
        if result.get("ok"):
            recorded.append(result["issue"])
    return recorded
