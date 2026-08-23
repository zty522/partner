"""Recoverable long-running campaign orchestration.

The controller deliberately executes one bounded WorkItem at a time per
instance.  Partner tasks remain event driven; this layer persists what should
run next, survives process restarts, and prevents an in-memory research loop
from pretending to be an overnight campaign.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .campaign_models import (
    CampaignBudget, CampaignReport, CampaignState, InstanceLease, WorkItem,
)
from .campaign_storage import (
    active_campaign_id, append_campaign_event, campaign_dir, campaign_lock,
    list_leases, list_work_items, load_campaign, load_lease, load_work_item,
    save_campaign, save_lease, save_report, save_work_item, set_active_campaign,
)
from .evolution_loop import record_issue
from .models import NextAction, now_iso
from .project_loop import record_action_state, record_iteration, request_next_action
from .scheduler import ROLES, load_scheduler
from .storage import governance_log, latest_receipt, load_project_state, workspace_root


CAMPAIGN_MARKER = re.compile(
    r"\[PARTNER_CAMPAIGN\s+campaign_id=(?P<campaign>[^\s\]]+)\s+work_item_id=(?P<work>[^\s\]]+)\]"
)
TERMINAL_WORK = {"completed", "blocked", "cancelled"}
BUSY_WORK = {"leased", "queued", "running"}
NAMED_ARTIFACT_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.(?:md|pdf|csv|json|png|jpe?g|webp|xlsx)", re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event(workspace: str, campaign_id: str, event: str, **details: Any) -> None:
    append_campaign_event(workspace, campaign_id, {"ts": now_iso(), "event": event, **details})


def create_campaign(
    workspace: str,
    *,
    goal: str,
    allowed_instances: list[str],
    duration_seconds: int,
    max_active: int = 2,
    report_interval_seconds: int = 3600,
    budget: CampaignBudget | None = None,
) -> CampaignState:
    existing_id = active_campaign_id(workspace)
    existing = load_campaign(workspace, existing_id) if existing_id else None
    if existing and existing.status not in {"completed", "cancelled"}:
        raise ValueError(f"unfinished campaign already active: {existing_id} ({existing.status})")
    started = _now()
    effective_budget = budget or CampaignBudget(max_runtime_seconds=max(60, int(duration_seconds)))
    effective_budget.max_runtime_seconds = min(effective_budget.max_runtime_seconds, max(60, int(duration_seconds)))
    state = CampaignState(
        goal=goal,
        allowed_instances=allowed_instances,
        deadline_at=(started + timedelta(seconds=int(duration_seconds))).isoformat(timespec="seconds"),
        budget=effective_budget,
        status="running",
        max_active=max_active,
        restore_instances=list(load_scheduler(str(workspace_root(workspace))).get("active_slots") or [])[:2],
        report_interval_seconds=report_interval_seconds,
        last_report_at=started.isoformat(timespec="seconds"),
        started_at=started.isoformat(timespec="seconds"),
    )
    save_campaign(workspace, state)
    set_active_campaign(workspace, state.campaign_id)
    _event(workspace, state.campaign_id, "campaign_created", goal=goal, allowed_instances=allowed_instances)
    return state


def enqueue_work_item(workspace: str, campaign_id: str, params: dict[str, Any]) -> WorkItem:
    with campaign_lock(workspace, campaign_id):
        state = load_campaign(workspace, campaign_id)
        if not state:
            raise ValueError("campaign not found")
        if (state.usage.work_items_created >= state.budget.max_work_items
                and str(params.get("kind") or "project_iteration") != "report"):
            raise ValueError("campaign work-item budget exhausted")
        autonomy = str(params.get("autonomy") or "").strip()
        instruction_text = str(params.get("instruction") or "")
        if not autonomy:
            sensitive = ("真实发布", "付款", "支付", "购买", "输入密码", "使用凭证", "删除生产")
            negated = ("不得真实发布", "不会真实发布", "不真实发布", "禁止真实发布")
            autonomy = "human_required" if any(word in instruction_text for word in sensitive) and not any(
                word in instruction_text for word in negated
            ) else "safe"
        item = WorkItem(
            campaign_id=campaign_id,
            instance_id=str(params.get("instance_id") or ""),
            project_id=str(params.get("project_id") or ""),
            kind=str(params.get("kind") or "project_iteration"),
            title=str(params.get("title") or ""),
            instruction=instruction_text,
            priority=int(params.get("priority", 50)),
            max_attempts=int(params.get("max_attempts", state.budget.max_retries_per_item + 1)),
            source_action_id=str(params.get("source_action_id") or ""),
            source_issue_id=str(params.get("source_issue_id") or ""),
            requires_artifact=bool(params.get("requires_artifact", True)),
            requires_delivery=bool(params.get("requires_delivery", True)),
            autonomy=autonomy,
        )
        if item.instance_id not in state.allowed_instances:
            raise ValueError(f"instance {item.instance_id} is outside campaign scope")
        item.validate()
        save_work_item(workspace, item)
        state.usage.work_items_created += 1
        state.updated_at = now_iso()
        save_campaign(workspace, state)
        _event(workspace, campaign_id, "work_item_created", work_item_id=item.work_item_id,
               instance_id=item.instance_id, project_id=item.project_id, kind=item.kind)
        return item


DEFAULT_SEEDS = {
    "01": (
        "小红书流程安全审计",
        "执行确定性事件 xiaohongshu_inspect_upload_requirements。读取最新小红书 ProjectState、Receipt "
        "和操作手册；执行一个不会真实发布的最小验证。"
        "如果操作浏览器，每个关键步骤必须截图、调用视觉模型描述，并通过真实 QQ callback 发送说明。"
        "产出一份包含证据、发现、限制和下一项可执行动作的 Markdown。不得发布内容。",
    ),
    "02": (
        "分子项目证据边界与数据接入准备",
        "执行确定性事件 molecular_data_readiness_audit。读取分子项目最新 Receipt。"
        "不得重复前四轮 QED/SA 排序；检查恢复第五轮需要的真实目标、"
        "活性、对接或实验数据，完成一个可验证的数据接入准备动作并产出报告。如果仍缺数据，明确 blocked。",
    ),
    "03": (
        "Partner 框架最小改进候选",
        "读取当前架构与测试，找出一个有代码证据的问题。先建立 Issue 和 candidate Experiment，"
        "只实施可回滚的最小候选并运行针对性测试；未通过 promotion gate 不得宣称生产改进。产出报告。",
    ),
    "04": (
        "文献与 GitHub 真实学习切片",
        "选择一个与 Partner 当前问题直接相关的公开论文或 GitHub 项目，真实拉取或读取原文/代码，"
        "执行一个最小复现或代码核查，记录命令、来源、文件、发现以及怎样用于当前项目。产出报告。",
    ),
    "05": (
        "自进化机制证据审计",
        "读取当前高置信 Issue、Experiment 和 promotion 规则，选择一个未解决问题，提出可证伪假设和"
        "候选实验并执行安全的针对性验证。回归或标准不全时只能标记 inconclusive/rejected。产出报告。",
    ),
}


def seed_default_work(workspace: str, campaign_id: str) -> list[WorkItem]:
    state = load_campaign(workspace, campaign_id)
    if not state:
        raise ValueError("campaign not found")
    created: list[WorkItem] = []
    existing = {(item.instance_id, item.kind) for item in list_work_items(workspace, campaign_id)}
    for instance in state.allowed_instances:
        if (instance, "project_iteration") in existing:
            continue
        title, instruction = DEFAULT_SEEDS[instance]
        created.append(enqueue_work_item(workspace, campaign_id, {
            "instance_id": instance,
            "project_id": ROLES[instance],
            "kind": "project_iteration",
            "title": title,
            "instruction": f"Campaign 总目标：{state.goal}\n\n当前实例职责：{instruction}",
            "priority": 70 if instance in {"01", "02"} else 60,
            "requires_artifact": True,
            "requires_delivery": True,
        }))
    return created


def _read_latest_issue_rows(workspace: str) -> list[dict[str, Any]]:
    path = governance_log(workspace, "issues")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-1000:]
    except OSError:
        return []
    latest: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("issue_id"):
            latest[str(row["issue_id"])] = row
    return list(latest.values())


def materialize_evolution_work(workspace: str, campaign_id: str) -> list[WorkItem]:
    state = load_campaign(workspace, campaign_id)
    if not state or "05" not in state.allowed_instances:
        return []
    existing = {item.source_issue_id for item in list_work_items(workspace, campaign_id) if item.source_issue_id}
    candidates = [row for row in _read_latest_issue_rows(workspace)
                  if row.get("status") in {"open", "investigating", "candidate"}
                  and row.get("issue_id") not in existing
                  and (row.get("severity") in {"high", "critical"} or int(row.get("occurrences", 1)) >= 2)]
    created: list[WorkItem] = []
    for issue in candidates[:2]:
        try:
            created.append(enqueue_work_item(workspace, campaign_id, {
            "instance_id": "05",
            "project_id": str(issue.get("project_id") or ROLES["05"]),
            "kind": "evolution_experiment",
            "title": f"验证 Issue {issue['issue_id']}",
            "instruction": (
                f"基于 Issue {issue['issue_id']} 的真实证据进行诊断：{issue.get('summary','')}。"
                "建立带 baseline、可证伪假设、成功标准和回滚策略的 candidate Experiment；"
                "执行聚焦测试。只有全部标准和回归通过才可 promoted，否则 rejected/inconclusive。"
                "完成后回到 Issue 所属项目，不得让自进化替代项目。产出实验报告。"
            ),
            "priority": 80 if issue.get("severity") == "critical" else 65,
            "source_issue_id": issue["issue_id"],
            "requires_artifact": True,
            "requires_delivery": True,
            }))
        except ValueError as exc:
            if "budget exhausted" in str(exc):
                break
            raise
    return created


def _effective_action_is_open(workspace: str, project_id: str, action_id: str) -> bool:
    result = request_next_action(workspace, {"project_id": project_id})
    return bool(result.get("ok") and result.get("action", {}).get("action_id") == action_id)


def materialize_project_actions(workspace: str, campaign_id: str) -> list[WorkItem]:
    state = load_campaign(workspace, campaign_id)
    if not state:
        return []
    items = list_work_items(workspace, campaign_id)
    source_ids = {item.source_action_id for item in items if item.source_action_id}
    created: list[WorkItem] = []
    for instance in state.allowed_instances:
        project_id = ROLES[instance]
        prepared = request_next_action(workspace, {"project_id": project_id})
        action = prepared.get("action") if prepared.get("ok") else None
        if not action or action.get("action_id") in source_ids:
            continue
        instruction = str(action.get("params", {}).get("user_request") or (
            f"执行事件 {action.get('event_type')}，承接最新 Receipt 的全部真实产物。"
        ))
        try:
            created.append(enqueue_work_item(workspace, campaign_id, {
            "instance_id": instance,
            "project_id": project_id,
            "kind": "project_iteration",
            "title": str(action.get("title") or "项目下一轮"),
            "instruction": instruction,
            "source_action_id": str(action.get("action_id") or ""),
            "priority": 75,
            "requires_artifact": True,
            "requires_delivery": True,
            }))
        except ValueError as exc:
            if "budget exhausted" in str(exc):
                break
            raise
    return created


def _marker(item: WorkItem) -> str:
    return f"[PARTNER_CAMPAIGN campaign_id={item.campaign_id} work_item_id={item.work_item_id}]"


def campaign_instruction(item: WorkItem) -> str:
    return (
        f"{_marker(item)}\n"
        "这是长期 Campaign 中的一个有边界 WorkItem，只执行这一轮；不要自行启动旧 Research Loop。\n"
        f"项目：{item.project_id}\n任务：{item.instruction}\n\n"
        "强制要求：先读取最新 ProjectState/IterationReceipt 和相关上下文；承接上一轮产物；"
        "实际执行并验证；产出文件；调用 send_user_text 通过真实 QQ callback 发送本轮摘要。"
        "如果需要文件交付则调用 push_files 并检查 delivered。遇到登录、发布、付费、凭证、"
        "不可恢复操作或缺失数据时明确 blocked，不得猜测执行。不要在本任务内部创建无限循环。"
    )


def _lease_expiry(seconds: int, deadline_at: str = "", grace_seconds: int = 120) -> str:
    """Bound a WorkItem lease by the Campaign deadline plus a short drain grace."""
    expiry = _now() + timedelta(seconds=seconds)
    if deadline_at:
        expiry = min(expiry, _parse_time(deadline_at) + timedelta(seconds=max(0, grace_seconds)))
    return expiry.isoformat(timespec="seconds")


def _release_lease(workspace: str, campaign_id: str, lease_id: str, status: str = "released") -> None:
    lease = load_lease(workspace, campaign_id, lease_id)
    if not lease:
        return
    lease.status = status
    lease.released_at = now_iso()
    lease.heartbeat_at = now_iso()
    save_lease(workspace, lease)


def _expire_stale(workspace: str, state: CampaignState, now: datetime) -> None:
    for lease in list_leases(workspace, state.campaign_id):
        if lease.status != "active" or _parse_time(lease.expires_at) > now:
            continue
        lease.status = "expired"
        lease.released_at = now_iso()
        save_lease(workspace, lease)
        item = load_work_item(workspace, state.campaign_id, lease.work_item_id)
        if not item or item.status not in BUSY_WORK:
            continue
        if item.attempt < item.max_attempts:
            item.status = "proposed"
            item.lease_id = ""
            item.task_id = ""
            item.updated_at = now_iso()
            state.usage.retries += 1
            _event(workspace, state.campaign_id, "work_item_retry", work_item_id=item.work_item_id,
                   reason="lease expired")
        else:
            item.status = "blocked"
            item.blocked_reason = "watchdog: lease expired and retry budget exhausted"
            state.usage.failures += 1
            _event(workspace, state.campaign_id, "work_item_blocked", work_item_id=item.work_item_id,
                   reason=item.blocked_reason)
        save_work_item(workspace, item)


def _budget_stop_reason(state: CampaignState, now: datetime) -> str:
    if now >= _parse_time(state.deadline_at):
        return "campaign deadline reached"
    if state.usage.work_items_created >= state.budget.max_work_items:
        return "work-item budget exhausted"
    if state.usage.failures >= state.budget.max_failures:
        return "failure budget exhausted"
    if state.usage.model_calls >= state.budget.max_model_calls:
        return "model-call budget exhausted"
    if state.usage.cost_units >= state.budget.max_cost_units:
        return "cost budget exhausted"
    return ""


def build_campaign_report(workspace: str, campaign_id: str, report_type: str = "checkpoint") -> tuple[CampaignReport, Path]:
    state = load_campaign(workspace, campaign_id)
    if not state:
        raise ValueError("campaign not found")
    items = list_work_items(workspace, campaign_id)
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    primary = [item for item in items if item.kind != "report"]
    reports = [item for item in items if item.kind == "report"]
    primary_counts: dict[str, int] = {}
    for item in primary:
        primary_counts[item.status] = primary_counts.get(item.status, 0) + 1
    primary_closed = sum(primary_counts.get(value, 0) for value in ("completed", "blocked", "cancelled"))
    report_delivered = sum(item.status == "completed" for item in reports)
    report_issues = sum(item.status in {"failed", "blocked", "cancelled"} for item in reports)
    blocked = [f"{item.instance_id}:{item.title} — {item.blocked_reason}" for item in primary if item.status == "blocked"]
    pending = [f"{item.instance_id}:{item.title}" for item in items if item.status in {"proposed", "leased", "queued", "running"}]
    summary = (
        f"Campaign {campaign_id} 状态={state.status}；业务轮次已收口 {primary_closed}/{len(primary)}"
        f"（成功 {primary_counts.get('completed', 0)}，受控阻塞 {primary_counts.get('blocked', 0)}，"
        f"失败 {primary_counts.get('failed', 0)}）；报告送达 {report_delivered}，报告链问题 {report_issues}；"
        f"当前实例={','.join(state.active_instances) or '无'}。"
    )
    report = CampaignReport(
        campaign_id=campaign_id,
        report_type=report_type,
        status=state.status,
        summary=summary,
        metrics={"work_items": counts, "primary_work_items": primary_counts,
                 "report_delivery": {"delivered": report_delivered, "issues": report_issues},
                 "usage": state.usage.to_dict(), "budget": state.budget.to_dict()},
        evidence=[str(campaign_dir(workspace, campaign_id) / "events.jsonl")],
        blocked_items=blocked,
        next_actions=pending,
    )
    json_path = save_report(workspace, report)
    md_path = json_path.with_suffix(".md")
    lines = [f"# Partner Campaign {'最终报告' if report_type == 'final' else '阶段报告'}", "", summary, "",
             "## 预算与使用", "", "```json", json.dumps(report.metrics, ensure_ascii=False, indent=2), "```", "",
             "## 阻塞项", ""]
    lines.extend(f"- {value}" for value in blocked or ["无"])
    lines.extend(["", "## 待执行", ""])
    lines.extend(f"- {value}" for value in pending or ["无"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report, md_path


def _schedule_report_if_due(workspace: str, state: CampaignState, now: datetime) -> None:
    if state.last_report_at:
        due = _parse_time(state.last_report_at) + timedelta(seconds=state.report_interval_seconds)
        if now < due:
            return
    items = list_work_items(workspace, state.campaign_id)
    if any(item.kind == "report" and item.status not in TERMINAL_WORK for item in items):
        return
    report, path = build_campaign_report(workspace, state.campaign_id)
    target = next((value for value in state.active_instances if value in state.allowed_instances), state.allowed_instances[0])
    enqueue_work_item(workspace, state.campaign_id, {
        "instance_id": target,
        "project_id": ROLES[target],
        "kind": "report",
        "title": "Campaign 定时进度摘要",
        "instruction": f"读取并通过 send_user_text 真实发送阶段摘要文件 {path} 的核心内容；消息必须包含 campaign_id。",
        "priority": 90,
        "requires_artifact": False,
        "requires_delivery": True,
    })
    state = load_campaign(workspace, state.campaign_id) or state
    state.last_report_at = now.isoformat(timespec="seconds")
    save_campaign(workspace, state)


def _ensure_final_report_work(workspace: str, state: CampaignState, stop_reason: str) -> WorkItem:
    items = list_work_items(workspace, state.campaign_id)
    existing = next((item for item in items if item.kind == "report" and item.title == "Campaign 最终日报"), None)
    if existing:
        return existing
    _report, path = build_campaign_report(workspace, state.campaign_id, "final")
    target = next((value for value in state.active_instances if value in state.allowed_instances), state.allowed_instances[0])
    return enqueue_work_item(workspace, state.campaign_id, {
        "instance_id": target,
        "project_id": ROLES[target],
        "kind": "report",
        "title": "Campaign 最终日报",
        "instruction": (
            f"Campaign 已到停止边界：{stop_reason}。读取最终报告 {path}，通过 send_user_text "
            "向用户真实发送简明总结；必须说明完成/失败/阻塞/预算和恢复条件。"
        ),
        "priority": 100,
        "requires_artifact": False,
        "requires_delivery": True,
    })


def _unfinished_primary_work(items: list[WorkItem]) -> list[WorkItem]:
    """Return created non-report work which still needs a terminal outcome."""
    return [item for item in items if item.kind != "report" and item.status not in TERMINAL_WORK]


def _effective_stop_reason(state: CampaignState, now: datetime, items: list[WorkItem]) -> str:
    """A creation cap must not pre-empt work already admitted by that cap."""
    reason = _budget_stop_reason(state, now)
    if reason == "work-item budget exhausted" and _unfinished_primary_work(items):
        return ""
    return reason


def tick_campaign(
    workspace: str,
    campaign_id: str,
    *,
    dispatch: Callable[[WorkItem, str], str],
    switch_slots: Callable[[list[str]], None] | None = None,
    lease_seconds: int = 1800,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Advance a campaign once. Safe to call repeatedly after restarts."""
    now = now or _now()
    # Materialization functions use the same lock internally when creating
    # work, so run them before the state-transition critical section.
    reconcile_campaign_tasks(workspace, campaign_id)
    pre_state = load_campaign(workspace, campaign_id)
    pre_items = list_work_items(workspace, campaign_id) if pre_state else []
    pre_stop_reason = _effective_stop_reason(pre_state, now, pre_items) if pre_state else ""
    creation_stop_reason = _budget_stop_reason(pre_state, now) if pre_state else ""
    if pre_state and not creation_stop_reason:
        materialize_project_actions(workspace, campaign_id)
        materialize_evolution_work(workspace, campaign_id)
        pre_state = load_campaign(workspace, campaign_id)
        pre_items = list_work_items(workspace, campaign_id) if pre_state else []
        pre_stop_reason = _effective_stop_reason(pre_state, now, pre_items) if pre_state else ""
    if pre_state and pre_stop_reason:
        _ensure_final_report_work(workspace, pre_state, pre_stop_reason)
    with campaign_lock(workspace, campaign_id):
        state = load_campaign(workspace, campaign_id)
        if not state:
            return {"ok": False, "status": "missing_campaign"}
        if state.status in {"completed", "cancelled"}:
            if switch_slots and state.restore_instances:
                switch_slots(state.restore_instances)
            return {"ok": True, "status": state.status, "dispatched": []}
        if state.status == "paused":
            return {"ok": True, "status": state.status, "dispatched": []}
        _expire_stale(workspace, state, now)
        all_items = list_work_items(workspace, campaign_id)
        stop_reason = _effective_stop_reason(state, now, all_items)
        if stop_reason:
            nonfinal_busy = [
                item for item in all_items
                if item.status in BUSY_WORK and not (item.kind == "report" and item.title == "Campaign 最终日报")
            ]
            if nonfinal_busy:
                state.status = "running"
                state.stop_reason = f"draining before final report: {stop_reason}"
                state.active_instances = list(dict.fromkeys(item.instance_id for item in nonfinal_busy))[:state.max_active]
                state.updated_at = now_iso()
                save_campaign(workspace, state)
                return {"ok": True, "status": "running", "phase": "draining",
                        "stop_reason": stop_reason, "active_instances": state.active_instances,
                        "dispatched": []}
            final_item = next(
                (item for item in all_items
                 if item.kind == "report" and item.title == "Campaign 最终日报"),
                None,
            )
            if final_item and final_item.status in {"completed", "blocked", "cancelled"}:
                state.status = "completed"
                delivery_note = "" if final_item.status == "completed" else f"; final report {final_item.status}"
                state.stop_reason = stop_reason + delivery_note
                state.active_instances = []
                state.updated_at = now_iso()
                save_campaign(workspace, state)
                report, path = build_campaign_report(workspace, campaign_id, "final")
                _event(workspace, campaign_id, "campaign_completed", reason=state.stop_reason, report_path=str(path))
                if switch_slots and state.restore_instances:
                    switch_slots(state.restore_instances)
                return {"ok": True, "status": "completed", "stop_reason": state.stop_reason,
                        "report": report.to_dict()}
            state.status = "running"
            state.stop_reason = f"finalizing: {stop_reason}"
        items = list_work_items(workspace, campaign_id)
        busy = [item for item in items if item.status in BUSY_WORK]
        busy_instances = {item.instance_id for item in busy}
        runnable = sorted(
            (item for item in items if item.status in {"proposed", "failed"}
             and item.autonomy == "safe" and item.attempt < item.max_attempts),
            key=lambda item: (-item.priority, item.created_at, item.work_item_id),
        )
        if stop_reason:
            runnable = [item for item in runnable if item.kind == "report" and item.title == "Campaign 最终日报"]
            busy = [item for item in busy if item.kind == "report" and item.title == "Campaign 最终日报"]
            busy_instances = {item.instance_id for item in busy}
        selected = list(busy_instances)
        for item in runnable:
            if item.instance_id not in selected and len(selected) < state.max_active:
                selected.append(item.instance_id)
        selected = [value for value in state.allowed_instances if value in selected][:state.max_active]
        state.active_instances = selected
        state.status = "running" if runnable or busy else "blocked"
        if state.status == "blocked":
            state.stop_reason = "no runnable work; waiting for resume event or new evidence"
        else:
            state.stop_reason = ""
        state.updated_at = now_iso()
        save_campaign(workspace, state)

    if switch_slots and selected:
        switch_slots(selected)

    dispatched: list[dict[str, str]] = []
    # Dispatch outside the lock because callbacks may touch the filesystem or
    # systemd. Each item is first leased under a short critical section.
    for candidate in runnable:
        if candidate.instance_id not in selected or candidate.instance_id in busy_instances:
            continue
        with campaign_lock(workspace, campaign_id):
            item = load_work_item(workspace, campaign_id, candidate.work_item_id)
            state = load_campaign(workspace, campaign_id)
            if not item or not state or item.status not in {"proposed", "failed"}:
                continue
            item.attempt += 1
            lease = InstanceLease(
                campaign_id=campaign_id,
                work_item_id=item.work_item_id,
                instance_id=item.instance_id,
                holder=f"campaign-controller:{os.getpid()}",
                acquired_at=now_iso(),
                expires_at=_lease_expiry(lease_seconds, state.deadline_at),
            )
            item.status = "leased"
            item.lease_id = lease.lease_id
            item.updated_at = now_iso()
            save_lease(workspace, lease)
            save_work_item(workspace, item)
        try:
            task_id = str(dispatch(item, campaign_instruction(item)) or "").strip()
            if not task_id:
                raise RuntimeError("dispatch did not return a task/message id")
            with campaign_lock(workspace, campaign_id):
                item = load_work_item(workspace, campaign_id, item.work_item_id) or item
                item.status = "queued"
                item.task_id = task_id
                item.updated_at = now_iso()
                save_work_item(workspace, item)
                if item.source_action_id:
                    record_action_state(workspace, item.project_id, item.source_action_id, "queued", task_id=task_id)
                _event(workspace, campaign_id, "work_item_dispatched", work_item_id=item.work_item_id,
                       instance_id=item.instance_id, task_id=task_id)
            dispatched.append({"work_item_id": item.work_item_id, "instance_id": item.instance_id, "task_id": task_id})
            busy_instances.add(item.instance_id)
        except Exception as exc:
            with campaign_lock(workspace, campaign_id):
                item = load_work_item(workspace, campaign_id, item.work_item_id) or item
                item.status = "failed"
                item.evidence.append(f"dispatch_error:{exc}")
                item.updated_at = now_iso()
                save_work_item(workspace, item)
                _release_lease(workspace, campaign_id, item.lease_id)
                state = load_campaign(workspace, campaign_id)
                if state:
                    state.usage.failures += 1
                    state.updated_at = now_iso()
                    save_campaign(workspace, state)
                _event(workspace, campaign_id, "work_item_dispatch_failed", work_item_id=item.work_item_id,
                       error=str(exc))

    state = load_campaign(workspace, campaign_id)
    # A Campaign waiting on an external resume event still owes the operator
    # periodic visibility.  `blocked` pauses business work, not reporting.
    if state and state.status in {"running", "blocked"} and not pre_stop_reason:
        _schedule_report_if_due(workspace, state, now)
    return {"ok": True, "status": state.status if state else "unknown", "active_instances": selected,
            "dispatched": dispatched}


def parse_campaign_marker(user_request: str) -> tuple[str, str] | None:
    match = CAMPAIGN_MARKER.search(str(user_request or ""))
    return (match.group("campaign"), match.group("work")) if match else None


def _task_runtime_evidence(workspace: str, marker: str, instance_id: str = "") -> dict[str, Any]:
    candidate_workspace = Path(workspace)
    if instance_id and candidate_workspace.name != instance_id:
        candidate_workspace = workspace_root(workspace) / "instances" / instance_id
    tasks = candidate_workspace / "state" / "tasks"
    candidates = sorted(tasks.glob("*/task_instance.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:12]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if marker not in str(data.get("user_message") or ""):
            continue
        delivered = False
        stack: list[Any] = [data.get("metadata", {}).get("step_results", {})]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if value.get("delivered") is True or value.get("delivery_confirmed") is True:
                    delivered = True
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        task_log = path.with_name("task_log.jsonl")
        complete = False
        execution_done = False
        failed = False
        blocked_reason = ""
        resume_event = ""
        event_types: list[str] = []
        planner_model_calls = 0
        step_model_calls = 0
        reported_total_model_calls = 0
        try:
            for line in task_log.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("event") == "completion_status_updated" and row.get("status") == "done":
                    execution_done = True
                if row.get("event") == "completion_status_updated" and row.get("status") == "failed":
                    execution_done = True
                    failed = True
                if row.get("event") in {"batch_plan_handler_failed", "task_failed"}:
                    execution_done = True
                    failed = True
                if row.get("event") == "iteration_llm_check" and row.get("satisfied") is True:
                    # completion_status=done is written before LLM_CHECK and is
                    # only an iteration boundary. Recovery may call the final
                    # completion hook only after acceptance actually passed.
                    complete = True
                if row.get("event") == "campaign_work_blocked":
                    blocked_reason = str(row.get("reason") or "external evidence unavailable")
                    resume_event = str(row.get("resume_event") or "")
                if row.get("event") == "plan_executor_step_completed" and row.get("event_type"):
                    event_types.append(str(row["event_type"]))
                try:
                    planner_model_calls = max(
                        planner_model_calls, int(row.get("planner_llm_calls") or 0),
                    )
                    row_llm_calls = int(row.get("llm_calls") or 0)
                    if row.get("event") == "plan_executor_step_completed":
                        step_model_calls += row_llm_calls
                    else:
                        reported_total_model_calls = max(reported_total_model_calls, row_llm_calls)
                except (TypeError, ValueError):
                    pass
        except (OSError, ValueError, TypeError):
            pass
        task_dir = path.parent
        artifacts = [str(value) for value in task_dir.iterdir()
                     if value.is_file() and not value.name.startswith("_")
                     and value.name not in {"task_instance.json", "task_log.jsonl", "active_plan.json"}]
        model_calls = max(reported_total_model_calls, planner_model_calls + step_model_calls)
        return {"found": True, "complete": complete, "execution_done": execution_done,
                "failed": failed, "blocked_reason": blocked_reason, "resume_event": resume_event,
                "delivered": delivered,
                "model_calls": model_calls, "event_types": event_types, "artifacts": artifacts,
                "task_id": str(data.get("task_id") or task_dir.name)}
    return {"found": False, "complete": False, "execution_done": False, "failed": False,
            "blocked_reason": "", "resume_event": "",
            "delivered": False, "model_calls": 0,
            "event_types": [], "artifacts": [], "task_id": ""}


def _delivery_ack_from_latest_task(workspace: str, marker: str, instance_id: str = "") -> bool:
    return bool(_task_runtime_evidence(workspace, marker, instance_id).get("delivered"))


def reconcile_campaign_tasks(workspace: str, campaign_id: str) -> list[str]:
    """Recover queued/running work after either process has restarted."""
    reconciled: list[str] = []
    for item in list_work_items(workspace, campaign_id):
        if item.status not in {"queued", "running"}:
            continue
        marker = f"campaign_id={campaign_id} work_item_id={item.work_item_id}"
        runtime = _task_runtime_evidence(workspace, marker, item.instance_id)
        if not runtime.get("found"):
            continue
        if runtime.get("complete") or runtime.get("failed"):
            complete_campaign_work(
                workspace,
                campaign_instruction(item),
                files=list(runtime.get("artifacts") or []),
                event_types=list(runtime.get("event_types") or []),
                success=not bool(runtime.get("failed")),
                evidence=["reconciled from persisted task log"],
            )
            reconciled.append(item.work_item_id)
        elif item.status == "queued":
            with campaign_lock(workspace, campaign_id):
                current = load_work_item(workspace, campaign_id, item.work_item_id)
                if current and current.status == "queued":
                    current.status = "running"
                    current.updated_at = now_iso()
                    save_work_item(workspace, current)
                    if current.source_action_id:
                        record_action_state(workspace, current.project_id, current.source_action_id,
                                            "running", task_id=current.task_id)
                    _event(workspace, campaign_id, "work_item_running", work_item_id=current.work_item_id)
                    reconciled.append(item.work_item_id)
    return reconciled


def _progress_signature(event_types: list[str], artifacts: list[str]) -> str:
    hashes: list[str] = []
    for value in artifacts[:5]:
        try:
            hashes.append(hashlib.sha256(Path(value).read_bytes()).hexdigest()[:16])
        except OSError:
            continue
    raw = json.dumps({"events": event_types, "artifacts": hashes}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _requested_named_artifacts(instruction: str) -> set[str]:
    """Extract explicit filenames; globs and generic extensions are ignored."""
    return {
        Path(match).name.lower() for match in NAMED_ARTIFACT_RE.findall(str(instruction or ""))
        if "*" not in match
    }


def complete_campaign_work(
    workspace: str,
    user_request: str,
    *,
    files: list[str] | None = None,
    event_types: list[str] | None = None,
    success: bool = True,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    marker_ids = parse_campaign_marker(user_request)
    if not marker_ids:
        return {"handled": False}
    campaign_id, work_item_id = marker_ids
    marker = f"campaign_id={campaign_id} work_item_id={work_item_id}"
    with campaign_lock(workspace, campaign_id):
        item = load_work_item(workspace, campaign_id, work_item_id)
        state = load_campaign(workspace, campaign_id)
        if not item or not state:
            return {"handled": True, "ok": False, "status": "missing_campaign_work"}
        if item.status in TERMINAL_WORK:
            return {
                "handled": True,
                "ok": item.status in {"completed", "blocked"},
                "status": f"already_{item.status}",
                "work_item": item.to_dict(),
            }
        item.artifacts = [str(value) for value in files or [] if Path(str(value)).is_file()]
        item.event_types = [str(value) for value in event_types or []]
        item.evidence.extend(str(value) for value in evidence or [])
        runtime = _task_runtime_evidence(workspace, marker, item.instance_id)
        item.artifacts = list(dict.fromkeys([
            *item.artifacts,
            *(str(value) for value in runtime.get("artifacts") or [] if Path(str(value)).is_file()),
        ]))
        delivered = bool(runtime.get("delivered"))
        runtime_blocked_reason = str(runtime.get("blocked_reason") or "")
        runtime_resume_event = str(runtime.get("resume_event") or "")
        state.usage.model_calls += int(runtime.get("model_calls") or 0)
        state.usage.cost_units += float(runtime.get("model_calls") or 0)
        problems = []
        if not success:
            problems.append("task completion reported failure")
        if item.kind != "report" and runtime.get("found") and not runtime.get("complete"):
            problems.append("final LLM acceptance not found")
        if item.requires_artifact and not item.artifacts:
            problems.append("required artifact missing")
        requested_names = _requested_named_artifacts(item.instruction)
        artifact_names = {Path(value).name.lower() for value in item.artifacts}
        missing_names = sorted(requested_names - artifact_names)
        if item.requires_artifact and missing_names:
            problems.append(f"explicitly requested artifact missing: {', '.join(missing_names)}")
        if item.requires_delivery and not delivered:
            problems.append("real delivery callback not found")
        signature = _progress_signature(item.event_types, item.artifacts)
        completed = [value for value in list_work_items(workspace, campaign_id)
                     if value.status == "completed" and value.instance_id == item.instance_id
                     and value.project_id == item.project_id]
        previous_signatures = []
        for value in sorted(completed, key=lambda row: row.updated_at)[-2:]:
            previous_signatures.extend(
                evidence_value.split("=", 1)[1] for evidence_value in value.evidence
                if evidence_value.startswith("progress_signature=")
            )
        if len(previous_signatures) >= 2 and previous_signatures[-2:] == [signature, signature]:
            problems.append("three consecutive rounds produced the same event/artifact signature")
        if problems:
            item.status = "failed" if item.attempt < item.max_attempts else "blocked"
            item.blocked_reason = "; ".join(problems) if item.status == "blocked" else ""
            item.evidence.extend(problems)
            state.usage.failures += 1
            if item.status == "failed":
                state.usage.retries += 1
            record_issue(workspace, {
                "summary": f"Campaign WorkItem 验收失败: {item.title}",
                "category": "delivery" if not delivered and item.requires_delivery else "verification",
                "severity": "high",
                "evidence": [f"work_item={item.work_item_id}", *problems],
                "instance_id": item.instance_id,
                "project_id": item.project_id,
            })
        elif runtime_blocked_reason:
            item.status = "blocked"
            item.blocked_reason = runtime_blocked_reason
            item.evidence.append(f"delivery_confirmed={delivered}")
            item.evidence.append(f"resume_event={runtime_resume_event}")
            item.evidence.append(f"progress_signature={signature}")
            state.usage.work_items_completed += 1
            if item.source_action_id:
                record_action_state(
                    workspace, item.project_id, item.source_action_id, "blocked",
                    task_id=item.task_id, blocked_reason=runtime_blocked_reason,
                )
        else:
            item.status = "completed"
            item.evidence.append(f"delivery_confirmed={delivered}")
            item.evidence.append(f"progress_signature={signature}")
            state.usage.work_items_completed += 1
            if item.source_action_id:
                record_action_state(workspace, item.project_id, item.source_action_id, "completed", task_id=item.task_id)
        item.updated_at = now_iso()
        save_work_item(workspace, item)
        _release_lease(workspace, campaign_id, item.lease_id)
        state.active_instances = [value for value in state.active_instances if value != item.instance_id]
        state.updated_at = now_iso()
        save_campaign(workspace, state)
        terminal_event = "work_item_completed" if item.status == "completed" else (
            "work_item_blocked" if item.status == "blocked" and not problems else "work_item_failed"
        )
        _event(workspace, campaign_id, terminal_event,
               work_item_id=item.work_item_id, status=item.status, artifacts=item.artifacts,
               delivery_confirmed=delivered, problems=problems)

    receipt_result: dict[str, Any] = {}
    if item.status in {"completed", "blocked"} and item.kind == "project_iteration":
        previous = latest_receipt(workspace, item.project_id)
        followup_instruction = (
            "读取最新 Receipt 并承接其中的真实产物。选择一个能够产生新证据、且不重复最近三轮事件的"
            "最小下一步，实际执行、验证、产出文件并通过真实 QQ callback 汇报。若缺数据、权限或新假设，"
            "明确记录 blocked 和 resume_event，不得机械续跑。"
        )
        can_continue = item.status == "completed" and state.usage.work_items_created < state.budget.max_work_items
        next_actions = [NextAction(
            title=f"{item.title} · 证据驱动下一轮",
            event_type="batch_plan",
            params={"user_request": followup_instruction, "campaign_id": campaign_id},
        ).to_dict()] if can_continue else []
        receipt_result = record_iteration(workspace, {
            "project_id": item.project_id,
            "owner_instance": item.instance_id,
            "project_goal": (load_project_state(workspace, item.project_id).goal
                             if load_project_state(workspace, item.project_id) else item.title),
            "goal": item.title,
            "inputs": list(previous.artifacts) if previous else [],
            "actions_executed": item.event_types or ["batch_plan"],
            "artifacts": item.artifacts,
            "findings": item.evidence[-5:] or ["Campaign bounded iteration completed"],
            "next_actions": next_actions,
            "stop_reason": (runtime_blocked_reason or "campaign work-item budget reached") if not next_actions else "",
            "delivery_confirmed": delivered,
            "requires_delivery": item.requires_delivery,
            "project_status": "blocked" if item.status == "blocked" else "completed",
            "resume_event": runtime_resume_event,
        })
        if not receipt_result.get("ok"):
            record_issue(workspace, {
                "summary": f"Campaign 完成但 IterationReceipt 写入失败: {item.title}",
                "category": "verification", "severity": "critical",
                "evidence": [str(receipt_result)], "instance_id": item.instance_id,
                "project_id": item.project_id,
            })
    return {"handled": True, "ok": item.status in {"completed", "blocked"}, "status": item.status,
            "work_item": item.to_dict(), "receipt": receipt_result, "delivery_confirmed": delivered}


def cancel_campaign(workspace: str, campaign_id: str, reason: str) -> CampaignState:
    with campaign_lock(workspace, campaign_id):
        state = load_campaign(workspace, campaign_id)
        if not state:
            raise ValueError("campaign not found")
        state.status = "cancelled"
        state.stop_reason = str(reason or "cancelled by operator")
        for item in list_work_items(workspace, campaign_id):
            if item.lease_id:
                _release_lease(workspace, campaign_id, item.lease_id, status="released")
            if item.status in TERMINAL_WORK:
                continue
            item.status = "cancelled"
            item.blocked_reason = state.stop_reason
            item.updated_at = now_iso()
            save_work_item(workspace, item)
        state.active_instances = []
        state.updated_at = now_iso()
        save_campaign(workspace, state)
        closed_runtime_tasks = 0
        try:
            from ..tasks.task_queue import TaskQueue

            root = workspace_root(workspace)
            for instance in state.allowed_instances:
                queue_path = root / "instances" / instance / "state" / "task_queue.json"
                if queue_path.is_file():
                    closed_runtime_tasks += TaskQueue(str(queue_path)).fail_matching_description_fragment(
                        f"campaign_id={campaign_id}", state.stop_reason,
                    )
        except (OSError, ValueError) as exc:
            governance_log(workspace, "campaign_cancel_runtime_queue_cleanup_failed", {
                "campaign_id": campaign_id, "error": str(exc),
            })
        _event(workspace, campaign_id, "campaign_cancelled", reason=state.stop_reason,
               runtime_tasks_closed=closed_runtime_tasks)
        return state


def campaign_snapshot(workspace: str, campaign_id: str = "") -> dict[str, Any]:
    campaign_id = campaign_id or active_campaign_id(workspace)
    state = load_campaign(workspace, campaign_id) if campaign_id else None
    if not state:
        return {"campaign_id": campaign_id, "status": "missing"}
    items = list_work_items(workspace, campaign_id)
    return {"campaign": state.to_dict(), "work_items": [item.to_dict() for item in items],
            "leases": [lease.to_dict() for lease in list_leases(workspace, campaign_id)]}
