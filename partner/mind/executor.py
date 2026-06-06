"""Mind Executor — Hermes 调度转发层。

仅保留 PROJECT / CRON_TICK / REPORT / WAKE_UP 四种事件类型。
Partner 只负责：读 state → 调 Hermes → 转发回复 → 按 UPDATE_STATE: 标记写 state。
"""

import asyncio
import glob
import hashlib
import json
import logging
import mimetypes
import os
import re
import smtplib
import time as _time
from datetime import datetime
from email.message import EmailMessage
from typing import Optional

from .event_types import MindEvent, EventType, report
from .pool import MindPool
from ..adapter import USER_FRIENDLY_PROGRESS_REPLY
from ..outbound_policy import UNAVAILABLE_NOTICE, prefix_event_notice
from ..research_memory import (
    append_strategy_memory,
    build_cross_project_context,
    build_reflection_context,
    build_research_context,
    consolidate_research_memory,
    ensure_habits,
    get_recent_growth_events,
    get_open_idea,
    mark_periodic_run,
    mark_idea_processed,
    maybe_reflection_objective,
    growth_context_for_report,
    record_growth_event,
    record_round_result,
    record_risk_event,
    scan_workspace_changes,
    should_run_periodic,
    write_reflection_artifacts,
)
from ..research_guardrails import (
    apply_round_guardrails,
    build_mind_context,
    ensure_baseline_and_metric_contracts,
    ensure_mind_files,
    improve_user_report,
    is_literature_reference_task,
    maybe_pause_after_literature_report,
    maybe_pause_project_for_quality_gate,
    should_send_user_report,
)
from ..user_text_safety import has_internal_diff, strip_internal_diff
from ..content_feed import (
    build_content_feed_context,
    build_patrol_prompt_context,
    content_patrol_enabled,
    ensure_content_sources,
    get_open_content_items,
    mark_content_processed,
    record_shared_content,
)

logger = logging.getLogger(__name__)

# ── 全局引用 ────────────────────────────────────────────────────────
_workspace: str = ""
_adapter = None  # AgentAdapter instance
_pool: Optional[MindPool] = None
_round_interval_sec: int = 60
_running_projects: set[str] = set()

# 推送回调：msg(str) -> None
_push_callback = None
_file_push_callback = None

# 规划循环检测：{project_title: consecutive_plan_only_count}
_plan_loop_counter: dict = {}

# 上一轮汇报内容缓存：{project_title: (findings_hash, next_action_hash)}
_last_report_cache: dict = {}
_stalled_repair_counter: dict = {}


def _state_dir() -> str:
    path = os.path.join(_workspace or ".", "state")
    os.makedirs(path, exist_ok=True)
    return path


async def _enqueue_visible_report(content: str, event_type: EventType | str, *,
                                  event_kind: str = "", priority: int = 3,
                                  source: str = "", parent_id: str = "",
                                  force_send: bool = True,
                                  bypass_rate_limit: bool = False) -> None:
    text = prefix_event_notice(
        content,
        event_type.value if isinstance(event_type, EventType) else str(event_type),
        event_kind=event_kind,
        workspace=_workspace,
    )
    if not text:
        return
    pool = await ensure_pool()
    await pool.put(MindEvent(
        type=EventType.REPORT,
        priority=priority,
        payload={
            "content": text,
            "force_send": bool(force_send),
            "bypass_rate_limit": bool(bypass_rate_limit),
            "visible_event_type": event_type.value if isinstance(event_type, EventType) else str(event_type),
            "visible_event_kind": event_kind,
        },
        source=source,
        parent_id=parent_id or None,
    ))


def _record_growth_event_visible(*args, notify: bool = True, **kwargs):
    record_growth_event(*args, **kwargs)
    if not notify:
        return
    try:
        project = str(args[1] if len(args) > 1 else kwargs.get("project") or "当前项目")
        learned = str(kwargs.get("learned") or (args[3] if len(args) > 3 else "") or "").strip()
        _schedule_background_report(
            prefix_event_notice(
                f"已写入一条可复用经验：{_clip(learned, 120) if learned else '后续会按这次经验调整判断和推进方式。'}",
                EventType.HABIT_UPDATE.value,
                event_kind=project,
            )
        )
    except Exception as exc:
        logger.debug(f"[GROWTH] failed to enqueue visible growth notice: {exc}")


def _schedule_background_report(content: str):
    # Growth is still recorded on disk, but background project growth notices can
    # interleave with a user's current QQ task and read like unrelated replies.
    # User-visible growth should be emitted by an explicit habit_update event or
    # the current event completion receipt, not by a global background enqueue.
    if os.getenv("PARTNER_VISIBLE_GROWTH_NOTICES", "0").strip().lower() not in {"1", "true", "yes"}:
        return
    if not content:
        return
    try:
        pool = MindPool.get_sync_instance()
        ev = MindEvent(
            type=EventType.REPORT,
            priority=3,
            payload={
                "content": content,
                "force_send": True,
                "bypass_rate_limit": True,
                "visible_event_type": EventType.HABIT_UPDATE.value,
            },
            source="growth:visible_notice",
        )
        if pool is not None:
            pool.put_threadsafe(ev)
    except Exception as exc:
        logger.debug(f"[REPORT] failed to schedule background report: {exc}")


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _dedupe_text_list(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items or []:
        text = _clip(str(item), 260)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _is_internal_fallback_text(text: str) -> bool:
    stripped = (text or "").strip()
    return any(
        token in stripped
        for token in (
            "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
            "Error: agent backend not available",
            "Reached maximum iterations",
            "tirith security scanner",
            "cannot access the image",
            "image was not provided",
        )
    )


def _is_blank_user_visible_text(text: str) -> bool:
    stripped = (text or "").strip()
    return not stripped


def _extract_content_report_from_parsed(parsed: dict) -> str:
    """Build a user-facing report from parsed structured fields only."""
    if not parsed:
        return ""
    findings = [str(x).strip() for x in (parsed.get("findings") or []) if str(x).strip()]
    next_action = str(parsed.get("next_action") or "").strip()
    step_done = str(parsed.get("step_done") or "").strip()

    lines = []
    if step_done:
        lines.append(f"本轮完成：{_clip(step_done, 90)}")
    if findings:
        lines.append(f"关键判断：{_clip('；'.join(findings[:2]), 150)}")
    if next_action:
        lines.append(f"下一步：{_clip(next_action, 120)}")
    text = _sanitize_user_report_text("\n".join(lines).strip())
    if _is_blank_user_visible_text(text):
        return ""
    return text


def _semantic_report_signature(text: str) -> str:
    """Normalize reports so same meaning is not resent after restart."""
    normalized = (text or "").strip().lower()
    normalized = re.sub(r"\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}(?::\d{2})?", "<time>", normalized)
    normalized = re.sub(r"\d{2}:\d{2}(?::\d{2})?", "<time>", normalized)
    normalized = re.sub(r"/[\w./\-\u4e00-\u9fff]+", "<path>", normalized)
    normalized = re.sub(r"\b[\w.-]+\.md\b", "<file>", normalized)
    normalized = re.sub(r"\b(step|round|task)[-_ ]?\d+\b", r"\1<n>", normalized)
    normalized = re.sub(r"\d+", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _blocker_report_allowed(title: str, issues: list[str], ttl_sec: int = 21600) -> bool:
    """Rate-limit repeated blocker reports by project and blocker category."""
    if not issues:
        return False
    categories: list[str] = []
    for issue in issues:
        text = str(issue)
        if re.search(r"api|key|预算|账号|token", text, re.I):
            categories.append("api_budget_account")
        elif re.search(r"真实数据|源目录|样本|数据集", text, re.I):
            categories.append("data_source")
        elif re.search(r"simulation|模拟|dry-run|真实 API", text, re.I):
            categories.append("simulation_real_boundary")
        else:
            categories.append(_semantic_report_signature(text)[:60])
    key = _semantic_report_signature(title + "|" + "|".join(sorted(set(categories))))
    path = os.path.join(_state_dir(), "blocker_report_history.json")
    now = _time.time()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    last = float(data.get(key) or 0)
    if last and now - last <= ttl_sec:
        return False
    data[key] = now
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return True


def _is_startup_report_step(step: int) -> bool:
    """Early project phase: keep the user visibly oriented."""
    try:
        return 0 <= int(step) <= 2
    except Exception:
        return False


def _is_startup_transition_step(step: int) -> bool:
    try:
        return int(step) == 3
    except Exception:
        return False


def _sanitize_user_report_text(text: str) -> str:
    """Remove internal agent/runtime noise before user-facing delivery."""
    if not text:
        return ""
    stripped_text = (text or "").strip()
    if stripped_text.startswith("{") and stripped_text.endswith("}"):
        try:
            data = json.loads(stripped_text)
            if isinstance(data, dict) and isinstance(data.get("message"), str):
                text = data["message"]
        except Exception:
            pass
    text = strip_internal_diff(text)
    if not text or has_internal_diff(text):
        return ""
    if re.search(r"<\s*/?\s*(tool_call|function|parameter)\b|<function=|</function>|<parameter=", text, re.I):
        return ""
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _is_internal_fallback_text(stripped):
            continue
        if stripped.startswith("⚠") and (
            "maximum iterations" in stripped.lower() or "tirith" in stripped.lower()
        ):
            continue
        if re.match(r"(?i)^session_id:", stripped):
            continue
        if re.search(r"(如果你需要|如需|请说|可以告诉我|也可以直接告诉我|你如果有).*?(继续|告诉|提供|偏好|API Key)", stripped):
            continue
        if has_internal_diff(stripped):
            continue
        lines.append(raw.rstrip())
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"(最终){4,}", "最终", cleaned)
    cleaned = re.sub(r"(_final){4,}", "_final", cleaned, flags=re.I)
    return cleaned


def _strip_tool_call_noise(text: str) -> str:
    """Remove raw tool-call transcripts from backend output before parsing/reporting."""
    if not text:
        return ""
    cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL | re.I)
    cleaned = re.sub(r"<function=[^>\n]+>.*?</function>", "", cleaned, flags=re.DOTALL | re.I)
    cleaned = re.sub(r"<parameter=[^>\n]+>.*?</parameter>", "", cleaned, flags=re.DOTALL | re.I)
    cleaned = re.sub(r"(?m)^<tool_call>.*$", "", cleaned)
    cleaned = re.sub(r"(?m)^<function=[^>\n]+>.*$", "", cleaned)
    cleaned = re.sub(r"(?m)^<parameter=[^>\n]+>.*$", "", cleaned)
    cleaned = re.sub(r"(?m)^.*</(?:tool_call|function|parameter)>.*$", "", cleaned)
    return cleaned.strip()


_ACTION_EVENT_TYPES = {
    EventType.DIRECT_TASK,
    EventType.LITERATURE_REVIEW,
    EventType.DATA_FETCH,
    EventType.DATA_ANALYSIS,
    EventType.VISUALIZATION,
    EventType.EVIDENCE_AUDIT,
    EventType.ARTIFACT_BUILD,
    EventType.PDF_REPORT,
    EventType.EMAIL_DELIVERY,
    EventType.WEB_SEARCH,
    EventType.WEB_CAPTURE,
    EventType.PROJECT_THINK,
    EventType.OBJECTIVE_REVIEW,
    EventType.CURIOSITY_EXPLORE,
    EventType.HABIT_UPDATE,
    EventType.STOP_PROJECT,
}


def _project_event_title(ev) -> str:
    try:
        if getattr(ev, "type", None) not in ({EventType.PROJECT} | _ACTION_EVENT_TYPES):
            return ""
        payload = getattr(ev, "payload", {}) or {}
        return str(payload.get("title") or "").strip()
    except Exception:
        return ""


def _has_project_event(pool, title: str = "", include_running: bool = True) -> bool:
    """Return whether the same project already has queued/waiting/running work."""
    title = (title or "").strip()
    if include_running:
        if title:
            if title in _running_projects:
                return True
        elif _running_projects:
            return True
    for ev in getattr(pool._queue, "_queue", []):
        ev_title = _project_event_title(ev)
        if ev_title and (not title or ev_title == title):
            return True
    thread_queue = getattr(pool, "_thread_queue", None)
    if thread_queue is not None:
        try:
            with thread_queue.mutex:
                thread_events = list(thread_queue.queue)
        except Exception:
            thread_events = []
        for ev in thread_events:
            ev_title = _project_event_title(ev)
            if ev_title and (not title or ev_title == title):
                return True
    if include_running:
        for ev in getattr(pool, "_inflight", {}).values():
            ev_title = _project_event_title(ev)
            if ev_title and (not title or ev_title == title):
                return True
    for _, ev in getattr(pool, "_waiting_room", {}).items():
        if isinstance(ev, tuple) and len(ev) == 2:
            ev = ev[1]
        ev_title = _project_event_title(ev)
        if ev_title and (not title or ev_title == title):
            return True
    return False


async def _enqueue_project_if_absent(pool, title: str, *, priority: int, source: str,
                                     wake_after: float | None = None, step: int = 0,
                                     parent_id: str | None = None) -> bool:
    if _has_project_event(pool, title, include_running=False):
        logger.info(f"[PROJECT] 去重跳过重复项目事件: {title}")
        return False
    await pool.put(MindEvent(
        type=EventType.PROJECT,
        priority=priority,
        payload={"title": title, "step": step},
        wake_after=wake_after,
        source=source,
        parent_id=parent_id,
    ))
    return True


def _read_active_plan_snapshot() -> dict:
    path = os.path.join(_workspace or ".", "state", "active_plan.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_actionable_project_next(title: str) -> str:
    if not title:
        return ""
    try:
        from ..project_state import read_project_brief, read_state_md

        texts = [
            read_state_md(_workspace, title),
            read_project_brief(_workspace, title, max_chars=4000),
        ]
    except Exception:
        texts = []
    candidates: list[str] = []
    for text in texts:
        if not text:
            continue
        section = re.search(r"## 下一步最小动作\s*\n(.*?)(?=\n## |\Z)", text, flags=re.DOTALL)
        if section:
            candidates.append(section.group(1).strip())
        for match in re.finditer(r"(?:^|\n)\s*[-*]?\s*(?:下一步|NEXT)\s*[:：]\s*(.+)", text, flags=re.I):
            candidates.append(match.group(1).strip())
    for raw in candidates:
        value = re.sub(r"\s+", " ", raw).strip(" -。")
        if not value or value in {"待补充", "EMPTY", "N/A"}:
            continue
        if re.fullmatch(r"(无|暂无|没有|无需|待补充|等待.*|已完成|关闭|完成|EMPTY|N/?A)", value, re.I):
            continue
        if re.search(r"(等待用户|等用户|需要用户|等待你|收到回复后|等待.*授权码|等待.*确认)", value):
            continue
        return value[:900]
    return ""


def _active_plan_matches_project(plan: dict, title: str) -> bool:
    if not plan or not title:
        return False
    candidates = [
        str(plan.get("title") or "").strip(),
        str(plan.get("project") or "").strip(),
        str(plan.get("goal") or "").strip(),
    ]
    return any(title == item or title in item or item in title for item in candidates if item)


def _empty_chain_recovery_marker_path() -> str:
    return os.path.join(_state_dir(), "empty_chain_recovery.json")


def _allow_empty_chain_recovery(title: str, next_action: str, *, min_interval_sec: int = 600) -> bool:
    key = hashlib.sha1(f"{title}\n{next_action}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    path = _empty_chain_recovery_marker_path()
    now = _time.time()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    last = float(data.get(key) or 0)
    if now - last < min_interval_sec:
        return False
    data[key] = now
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return True


async def _maybe_recover_empty_active_chain(pool: MindPool, active_name: str, status: str, *,
                                           source: str, parent_id: str = "") -> bool:
    """Recover the common failure shape: active state promises NEXT but pool is empty."""
    if not active_name or _has_project_event(pool, active_name, include_running=True):
        return False
    plan = _read_active_plan_snapshot()
    plan_status = str(plan.get("status") or plan.get("project_status") or "").strip().lower()
    heartbeat = str(plan.get("heartbeat_summary") or "").strip()
    plan_active = _active_plan_matches_project(plan, active_name) and plan_status in {"active", "running", "in_progress"}
    next_action = _extract_actionable_project_next(active_name)
    has_promised_followup = bool(re.search(r"(selector follow-up|下一步|next|继续|env_verify|direct_task|data_analysis|artifact_build|pdf_report)", heartbeat, re.I))
    if status in {"waiting", "done"} and not plan_active:
        return False
    if not next_action and not (plan_active and has_promised_followup):
        return False
    if not next_action:
        next_action = heartbeat or "恢复 active_plan 中记录的下一步，重新选择一个最小可验证 event。"
    if not _allow_empty_chain_recovery(active_name, next_action):
        logger.info(f"[RECOVERY] skip repeated empty-chain recovery: {active_name}")
        return False
    try:
        from ..project_state import append_log, set_project_status

        set_project_status(_workspace, active_name, "active", f"{source}: empty mind pool recovery")
        append_log(
            _workspace,
            active_name,
            f"RECOVERY: active state had actionable NEXT but mind_pool was empty. source={source}; next={next_action}",
        )
    except Exception as exc:
        logger.debug(f"[RECOVERY] state mark failed for {active_name}: {exc}")
    await pool.put(MindEvent(
        type=EventType.PROJECT_THINK,
        priority=2,
        payload={
            "title": active_name,
            "step": 0,
            "delivery_mode": "research_project",
            "user_request": (
                "恢复一次中断的执行链：先核对 active_plan/state.md 中记录的下一步，"
                "然后只选择一个最小可验证 event 入队；不要重新询问用户，不要直接停止。"
            ),
            "root_user_request": str(plan.get("goal") or active_name)[:1800],
            "event_type": EventType.PROJECT_THINK.value,
            "event_kind": "empty_chain_recovery",
            "stop_after_completion": True,
            "curiosity_depth": 0,
            "previous_next_action": next_action,
            "followup_reason": f"{source}: active project had no queued/running event",
        },
        source=f"{source}:empty_chain_recovery",
        parent_id=parent_id or None,
    ))
    logger.warning(f"[RECOVERY] queued project_think empty-chain recovery for {active_name}: {next_action[:160]}")
    return True


def _cooling_down_delay_sec() -> int:
    """Optional low-frequency revisit delay for explicit cooling-down mode."""
    raw = os.getenv("PARTNER_COOLING_DOWN_DELAY_SEC")
    if raw is not None and raw.strip() != "":
        try:
            return max(0, int(raw))
        except Exception:
            pass
    return max(int(_round_interval_sec), 1800)


def _cooling_down_enabled() -> bool:
    """By default Partner keeps working; cooling-down is opt-in.

    A research partner should not stop after one phase looks complete. It may
    reflect, transfer ideas, or search for a small next breakthrough. Operators
    can enable low-frequency revisit explicitly via PARTNER_ENABLE_COOLING_DOWN.
    """
    return os.getenv("PARTNER_ENABLE_COOLING_DOWN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _active_recur_wake_after() -> float | None:
    """Active projects should keep moving; no delayed wake unless explicitly configured."""
    env_delay = os.getenv("PARTNER_ACTIVE_PROJECT_DELAY_SEC")
    if env_delay is None or env_delay.strip() == "":
        return None
    try:
        delay = max(0, int(env_delay))
    except Exception:
        return None
    if delay <= 0:
        return None
    return _time.time() + delay


def _delivery_mode(payload: dict | None) -> str:
    mode = str((payload or {}).get("delivery_mode") or "research_project").strip()
    return mode if mode in {"research_project", "reference_brief", "direct_deliverable", "audit_only"} else "research_project"


def _is_one_shot_delivery(mode: str) -> bool:
    return mode in {"reference_brief", "direct_deliverable", "audit_only"}


def _stop_after_completion(payload: dict | None) -> bool:
    payload = payload or {}
    if bool(payload.get("stop_after_completion")):
        return True
    return _is_one_shot_delivery(_delivery_mode(payload))


def _delivery_policy(mode: str, user_request: str) -> str:
    request = _clip(user_request or "", 900)
    if mode == "reference_brief":
        return (
            "\n本轮交付模式：reference_brief（参考资料/文献简报）。"
            f"\n用户原始请求：{request or '按当前任务描述整理参考资料'}"
            "\n必须围绕用户原始请求找资料、读摘要/正文/元数据、总结方法路线和引用线索。"
            "\n禁止自动进入实验、建模、调参、生成模拟数据、写 pipeline 或把“下一阶段可做”当成本轮成果。"
            "\n完成一版可读简报后应暂停，不要自动扩展成长期项目。"
        )
    if mode == "direct_deliverable":
        return (
            "\n本轮交付模式：direct_deliverable（直接交付）。"
            f"\n用户原始请求：{request or '按用户点名交付物直接完成'}"
            "\n只完成用户点名的表格/文件/翻译/整理/转换/导出等交付物。"
            "\n如果用户要 Excel/表格/文件，必须实际生成或修改该文件；不要把健壮性测试、长期项目推进或阶段汇报当作成果。"
            "\n禁止自动搜索文献、做阶段 PPT/PDF、开新实验、创建长期路线图。"
            "\n完成交付物后暂停，除非用户明确要求继续。"
        )
    if mode == "audit_only":
        return (
            "\n本轮交付模式：audit_only（证据/风险审计）。"
            f"\n用户原始风险信号：{request or '按当前风险信号审计'}"
            "\n先审计证据、数据泄露、过拟合、路径真实性或结论可信度。"
            "\n审计未通过前禁止继续调参、宣称最佳结果、进入下一阶段实验或包装成果。"
        )
    return (
        "\n本轮交付模式：research_project（长期科研项目）。"
        "\n可以持续推进，但每一步仍必须根据上下文选择一个最小动作，不要机械走固定流程。"
    )


def _delivery_objective_override(workspace: str, title: str, mode: str, user_request: str) -> tuple[str, str] | None:
    if mode == "research_project":
        return None
    from ..project_state import get_project_dir

    project_dir = get_project_dir(workspace, title)
    request = _clip(user_request or f"围绕「{title}」完成用户点名交付", 900)
    if mode == "reference_brief":
        return (
            f"只完成用户要求的参考资料/文献方法简报：{request}。"
            "必须优先找相关文献/资料并形成可读汇报，交付后暂停，不进入实验或下一阶段项目推进。",
            os.path.join(project_dir, "reference_brief.md"),
        )
    if mode == "direct_deliverable":
        return (
            f"只完成用户点名的直接交付物：{request}。"
            "如果需要改文件就直接改；如果需要整理表格/Excel/CSV，就必须生成对应实际文件并在 FILES 写明。"
            "不要只写说明；不要生成额外项目路线或阶段报告。",
            os.path.join(project_dir, "direct_deliverable.md"),
        )
    if mode == "audit_only":
        return (
            f"只做用户指出问题的证据/风险审计：{request}。"
            "审计未通过前禁止继续优化、调参或宣称新最佳。",
            os.path.join(project_dir, "audit_result.md"),
        )
    return None


def _selector_objective_override(workspace: str, title: str, payload: dict | None) -> tuple[str, str] | None:
    payload = payload or {}
    if not _stop_after_completion(payload):
        return None
    event_kind = str(payload.get("event_kind") or "one_shot_event").strip() or "one_shot_event"
    user_request = str(payload.get("user_request") or "").strip()
    from ..project_state import get_project_dir

    project_dir = get_project_dir(workspace, title)
    safe_kind = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", event_kind).strip("_") or "one_shot_event"
    return (
        f"只执行 selector 选中的这个 event：{event_kind}。用户原始请求：{user_request or title}。"
        "完成用户点名交付后停止自动续跑；不要生成阶段汇报/PPT/PDF，除非用户明确要求。",
        os.path.join(project_dir, f"{safe_kind}_result.md"),
    )


def _build_one_shot_project_prompt(workspace: str, title: str, state_md: str, step: int,
                                   event_payload: dict | None, artifact_path: str) -> str:
    """Slim executor prompt for user-directed one-shot deliverables.

    These events should behave like a direct assistant action, not a long
    research lifecycle. Keep the prompt small and require real file outputs when
    the user asked for a file.
    """
    event_payload = event_payload or {}
    user_request = _clip(str(event_payload.get("user_request") or title), 1200)
    event_kind = str(event_payload.get("event_kind") or "one_shot_event").strip() or "one_shot_event"
    artifact_hint = os.path.basename(artifact_path) if artifact_path else "one_shot_result.md"
    state_snapshot = _compact_state_snapshot(state_md)
    return (
        f"你是 Partner 的轻量执行器。本轮不是长期科研项目推进，只完成用户这一次明确交付。\n"
        f"项目/任务名：{title}\n"
        f"event_kind：{event_kind}\n"
        f"用户原始请求：{user_request}\n"
        f"当前状态摘要：\n{state_snapshot}\n\n"
        "执行规则：\n"
        "1. 只做用户点名的最小交付，不要自动做阶段汇报、PPT/PDF、长期路线图、健壮性测试或下一轮扩展。\n"
        "2. 如果用户要求 Excel/CSV/PPT/PDF/图片/文档，必须实际生成对应文件，不能只写说明。\n"
        "3. 若任务依赖地点、日期范围、文件路径等关键参数，用户没给就不要擅自补；应说明缺失参数并停止。\n"
        "4. 若需要联网获取公开数据，优先使用无需账号/API key 的公开来源；如果失败，换一个可用公开来源或说明真实限制。\n"
        "5. 不要把内部工具调用、diff、trace、代码片段原样当作给用户看的汇报。\n"
        "6. 完成后停止，不要继续排队下一步。\n\n"
        "必须按以下结构输出：\n"
        "ACTION: <一个动作类型，例如 create_file / summarize / modify_file / lookup>\n"
        "DONE: <一句话说明实际完成了什么>\n"
        "FINDINGS: <1-3 条关键结果；没有就写 EMPTY>\n"
        "NEXT: <写 已完成，等待用户查看/继续>\n"
        "STATE_DELTA: <对状态的简短更新>\n"
        f"FILES: <真实生成/修改的文件路径，多个用 ; 分隔；没有文件写 EMPTY。结果摘要可写入 {artifact_hint}>\n"
        f"ARTIFACT_CONTENT:\n<写入 {artifact_hint} 的简短摘要或说明；不要写长篇报告>\n"
    )


def _cap_project_waiting_delay(pool, title: str, max_delay_sec: int) -> bool:
    """Pull an existing delayed PROJECT closer if it was scheduled too far out."""
    now = _time.time()
    changed = False
    for eid, value in list(getattr(pool, "_waiting_room", {}).items()):
        if not isinstance(value, tuple) or len(value) != 2:
            continue
        wake_at, ev = value
        if _project_event_title(ev) != title:
            continue
        if wake_at and wake_at > now + max_delay_sec:
            pool._waiting_room[eid] = (now + max_delay_sec, ev)
            changed = True
            logger.info(f"[PROJECT] 拉近 cooling-down 回访时间: {title} -> {max_delay_sec}s")
    if changed and getattr(pool, "_auto_save", False):
        pool.save()
    return changed


def _has_reflection_event(pool, project: str) -> bool:
    project = (project or "").strip()
    if not project:
        return False
    for ev in getattr(pool._queue, "_queue", []):
        if getattr(ev, "type", None) == EventType.REFLECTION and (getattr(ev, "payload", {}) or {}).get("project") == project:
            return True
    for _, value in getattr(pool, "_waiting_room", {}).items():
        if not isinstance(value, tuple) or len(value) != 2:
            continue
        _, ev = value
        if getattr(ev, "type", None) == EventType.REFLECTION and (getattr(ev, "payload", {}) or {}).get("project") == project:
            return True
    return False


def _reflection_summary_for_report(content: str) -> str:
    lines = []
    for raw in (content or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if re.match(r"^(一|二|三|四|五|六|七|八|九|十|[0-9]+)[、.．]", line):
            continue
        if re.match(r"^[-*]\s*$", line):
            continue
        lines.append(re.sub(r"^[-*]\s*", "", line))
        if len(lines) >= 3:
            break
    return _clip(_sanitize_user_report_text("；".join(lines)), 180)


def _compact_state_snapshot(state_md: str) -> str:
    """Shrink verbose state.md into a compact snapshot for executor prompts."""
    if not state_md:
        return "（新项目，尚无状态记录）"

    lines = [line.rstrip() for line in state_md.splitlines()]
    picked = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_internal_fallback_text(stripped):
            continue
        if re.search(r"/(?:mnt|home|tmp)/|关键目录|顶层脚本|目录结构|现有文件|当前相关文件", stripped):
            continue
        if stripped.startswith("# 项目：") or stripped.startswith("最后更新:"):
            picked.append(stripped)
            continue
        if stripped.startswith("## 当前状态") or stripped.startswith("当前状态：") or stripped.startswith("当前聚焦方向："):
            picked.append(stripped)
            continue
        if stripped.startswith("- "):
            picked.append(_clip(stripped, 90))
        if len(picked) >= 4:
            break
    if not picked:
        return _clip(state_md, 260)
    return "\n".join(picked[:4])


def _project_file_hints(workspace: str, title: str) -> str:
    from ..project_state import get_project_dir

    project_dir = get_project_dir(workspace, title)
    if not os.path.isdir(project_dir):
        return "（暂无项目文件）"
    names = []
    for name in sorted(os.listdir(project_dir)):
        path = os.path.join(project_dir, name)
        if os.path.isfile(path):
            names.append(name)
    if not names:
        return "（暂无项目文件）"
    return ", ".join(names[:4])


def _breakthrough_queue_path(workspace: str, title: str) -> str:
    from ..project_state import get_project_dir

    return os.path.join(get_project_dir(workspace, title), "breakthrough_queue.md")


def _read_breakthrough_queue(workspace: str, title: str) -> str:
    return _tail_text_file(_breakthrough_queue_path(workspace, title), max_lines=90)


def _path_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _read_text(path: str, max_chars: int = 4000) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(max_chars)
    except OSError:
        return ""


def _append_breakthrough_queue(workspace: str, title: str, *, reason: str,
                               next_action: str, source_result: dict | None = None) -> bool:
    """Persist an escape hatch when a project tries to stop or wait.

    The queue is intentionally a human-readable project artifact. It gives the
    next round a concrete, durable objective instead of relying on the model to
    remember that it should not stop.
    """
    path = _breakthrough_queue_path(workspace, title)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    source_result = source_result or {}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    original_done = _clip(str(source_result.get("step_done") or "EMPTY"), 180)
    original_next = _clip(str(source_result.get("next_action") or "EMPTY"), 180)
    block = (
        f"\n## {ts} | open\n"
        f"- 触发原因：{reason}\n"
        f"- 原始完成：{original_done}\n"
        f"- 原始下一步：{original_next}\n"
        f"- 必须推进：{next_action}\n"
        "- 验收标准：必须产生一个真实文件、一个证据化结论或一个可验证的下一实验；"
        "禁止输出“已完成/等待用户/无下一步”。\n"
    )
    try:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# {title} 突破队列\n\n这个文件记录项目完成态、等待态或证据不足时自动生成的下一突破口。\n")
        with open(path, "a", encoding="utf-8") as f:
            f.write(block)
        return True
    except OSError as exc:
        logger.warning(f"[PROJECT] 写入 breakthrough_queue 失败: {exc}")
        return False


def _choose_micro_objective(workspace: str, title: str, state_md: str, step: int) -> tuple[str, str]:
    """Return a single-step objective and preferred output artifact."""
    from ..project_state import get_project_dir, load_project_guardrail, read_project_brief
    from ..stage_report import maybe_stage_report_objective

    project_dir = get_project_dir(workspace, title)
    stage_objective, stage_path = maybe_stage_report_objective(workspace, title, step)
    if stage_objective and stage_path:
        return stage_objective, stage_path
    guardrail = load_project_guardrail(workspace, title)
    mainline = (guardrail.get("current_mainline") or "").strip()
    allowed_scope = [str(x).strip() for x in (guardrail.get("allowed_scope") or []) if str(x).strip()]
    forbidden_scope = [str(x).strip() for x in (guardrail.get("forbidden_scope") or []) if str(x).strip()]
    if mainline or allowed_scope or forbidden_scope or guardrail.get("completion_criteria"):
        alignment_path = os.path.join(project_dir, "contract_alignment.md")
        boundary_path = os.path.join(project_dir, "scope_boundary_audit.md")
        deliverable_path = os.path.join(project_dir, "contract_deliverable.md")
        allowed_text = "；".join([mainline] + allowed_scope[:6]).strip("；") or "用户原始要求"
        forbidden_text = "；".join(forbidden_scope[:8]) or "用户未要求的新方向"
        criteria_text = "；".join([str(x).strip() for x in (guardrail.get("completion_criteria") or []) if str(x).strip()][:8])
        if not os.path.exists(alignment_path):
            return (
                "先做任务合同对齐，不推进新实验或新方向。"
                f"本轮只根据用户原始要求和项目合同写清：允许范围={allowed_text}；禁止范围={forbidden_text}；"
                f"完成标准={criteria_text or '直接满足用户点名交付物'}。"
                "同时列出当前已有产物哪些回答了用户问题、哪些属于越界或下一阶段建议。",
                alignment_path,
            )
        if not os.path.exists(boundary_path):
            return (
                "执行通用边界审计。只检查当前项目是否偏离用户合同："
                f"允许范围={allowed_text}；禁止范围={forbidden_text}。"
                "把每个已有主要结论标成 in_scope / next_phase / out_of_scope，并说明需要从用户汇报中降级或删除的内容。",
                boundary_path,
            )
        return (
            "只补齐合同内交付物，不自动扩展。"
            f"当前允许范围：{allowed_text}。禁止范围：{forbidden_text}。"
            "如果合同内交付物已经足够，产物应写成最终交付摘要和暂停理由；不要为了继续推进而发明下一阶段。",
            deliverable_path,
        )
    breakthrough_queue = _read_breakthrough_queue(workspace, title)
    if breakthrough_queue:
        return (
            "优先执行项目目录里的 breakthrough_queue.md 最新 open 项。"
            "先读取该文件和当前状态，只消化最新一个突破任务；必须做一个最小可验证动作并落盘，"
            "把结果写入 breakthrough_execution.md，同时在突破队列中追加本次处理结论。"
            "禁止输出项目已完成、等待新指令、NEXT 无。",
            os.path.join(project_dir, "breakthrough_execution.md"),
        )

    reflection_objective, reflection_path = maybe_reflection_objective(workspace, title, step)
    if reflection_objective:
        return reflection_objective, reflection_path

    if mainline or allowed_scope or forbidden_scope:
        boundary_path = os.path.join(project_dir, "next_experiment.md")
        allowed_text = "；".join([mainline] + allowed_scope[:5]).strip("；")
        forbidden_text = "；".join(forbidden_scope[:8])
        objective = "基于用户纠偏后的项目边界，只推进一个最小闭环步骤。"
        if allowed_text:
            objective += f" 当前允许/主线方向：{allowed_text}。"
        if forbidden_text:
            objective += f" 必须避开：{forbidden_text}。"
        objective += " 不要把边界扩展成用户没说过的新方向；优先读取本地状态和文件后产出一个可落盘的小结果。"
        return objective, boundary_path

    return (
        f"围绕项目「{title}」只推进一个最小闭环步骤。优先基于本地现有状态与文件，产出一个可记录的新结论、"
        f"小文档或明确的下一步执行结果。当前是 step {step}，不要发散。",
        "",
    )


def _detect_plan_only_response(parsed: dict, hermes_response: str) -> bool:
    """检测回复是否只是纯规划（没有实际执行代码/文件操作）。
    
    判断标准：
    1. DONE 中包含"设计"、"规划"、"方案"、"计划"等词但不含执行类动词
    2. 没有 ARTIFACT_CONTENT 或 ARTIFACT_CONTENT 为空
    3. 回复中没有 terminal/read_file/write_file 等工具调用痕迹
    """
    step_done = (parsed.get("step_done") or "").lower()
    artifact = (parsed.get("artifact_content") or "").strip()
    
    # 纯规划关键词
    plan_keywords = ["设计了", "规划了", "方案", "计划", "草稿", "框架", "大纲", "路线图",
                     "梳理了", "整理了", "定义了", "补充了", "更新了"]
    # 执行类关键词
    exec_keywords = ["运行了", "执行了", "复制了", "下载了", "分析了", "跑了", "计算了",
                     "生成了结果", "产出了数据", "训练了", "测试了", "验证了结果",
                     "terminal", "read_file", "write_file", "patch"]
    
    is_plan = any(kw in step_done for kw in plan_keywords)
    has_exec = any(kw in step_done for kw in exec_keywords)
    
    # 如果是规划且没有执行类内容，且没有产出产物
    if is_plan and not has_exec and not artifact:
        return True
    return False


def _build_project_prompt(workspace: str, title: str, state_md: str, step: int,
                          event_payload: dict | None = None) -> tuple[str, str]:
    from ..project_state import format_project_guardrail_for_prompt, load_project_guardrail, read_project_brief

    event_payload = event_payload or {}
    mode = _delivery_mode(event_payload)
    user_request = str(event_payload.get("user_request") or "").strip()
    event_kind = str(event_payload.get("event_kind") or "").strip()
    stop_after = _stop_after_completion(event_payload)
    override = _selector_objective_override(workspace, title, event_payload)
    if not override:
        override = _delivery_objective_override(workspace, title, mode, user_request)
    if override:
        objective, artifact_path = override
    else:
        objective, artifact_path = _choose_micro_objective(workspace, title, state_md, step)
    if stop_after:
        return _build_one_shot_project_prompt(
            workspace=workspace,
            title=title,
            state_md=state_md,
            step=step,
            event_payload=event_payload,
            artifact_path=artifact_path,
        ), artifact_path
    delivery_policy = _delivery_policy(mode, user_request)
    literature_task = is_literature_reference_task(workspace, title)
    artifact_hint = os.path.basename(artifact_path) if artifact_path else "（优先补充现有项目文档或状态）"
    state_snapshot = _compact_state_snapshot(state_md)
    guardrail = load_project_guardrail(workspace, title)
    guardrail_block = format_project_guardrail_for_prompt(workspace, title)
    guardrail_prompt = f"{guardrail_block}\n" if guardrail_block else ""
    research_context = build_research_context(workspace, title)
    research_prompt = f"{research_context}\n" if research_context else ""
    mind_context = build_mind_context(workspace, title)
    mind_prompt = f"{mind_context}\n" if mind_context else ""
    content_context = build_content_feed_context(workspace, project=title)
    content_prompt = f"{content_context}\n" if content_context else ""
    project_brief = read_project_brief(workspace, title, max_chars=1600)
    brief_prompt = f"项目热简报：\n{project_brief}\n" if project_brief else ""
    has_scope_contract = bool(
        guardrail.get("current_mainline")
        or guardrail.get("allowed_scope")
        or guardrail.get("forbidden_scope")
        or guardrail.get("completion_criteria")
    )
    
    # 规划循环检测：如果连续多轮都是纯规划，强制要求执行
    plan_count = _plan_loop_counter.get(title, 0)
    execution_enforcement = ""
    if plan_count >= 2 and not has_scope_contract:
        execution_enforcement = (
            "\n⚠️ 执行警告：你已经连续 {} 轮只产出计划/方案而没有实际执行。"
            "\n本轮必须用 terminal 工具实际执行一个动作（运行代码、复制文件、下载数据等），"
            "不能只写文档。如果目标需要先写方案，写完后立即执行它。"
            "\n禁止本轮输出以\"设计了\"、\"规划了\"、\"方案\"开头的完成描述。"
        ).format(plan_count)
    contract_boundary = ""
    if has_scope_contract:
        allowed_text = "；".join(
            [str(guardrail.get("current_mainline") or "").strip()]
            + [str(x).strip() for x in (guardrail.get("allowed_scope") or []) if str(x).strip()]
        ).strip("；")
        forbidden_text = "；".join(str(x).strip() for x in (guardrail.get("forbidden_scope") or []) if str(x).strip())
        criteria_text = "；".join(str(x).strip() for x in (guardrail.get("completion_criteria") or []) if str(x).strip())
        contract_boundary = (
            "\n任务合同硬边界：必须以用户原始要求为准，不得把自主下一阶段当成当前任务。"
            f"允许/主线：{allowed_text or '按用户原始要求'}。"
            f"禁止/越界：{forbidden_text or '用户没有要求的新方向'}。"
            f"完成标准：{criteria_text or '直接满足用户点名交付物'}。"
            "DONE/FINDINGS 必须说明本轮如何服务这些合同条款。"
            "如果合同内交付物已经满足，NEXT 允许写“暂停等待用户评估/确认是否进入下一阶段”；"
            "不要为了通用推进规则继续发明新任务。"
        )
    completion_rule = (
        "如果项目看起来已经完成，不要输出归档/等待/不再推进；必须转入复盘、误差分析、失败边界、跨项目迁移、外部内容消化或下一突破口中的一个实际动作；"
        if not has_scope_contract
        else "如果任务合同内交付物已经完成，不要自动寻找下一突破口；应总结交付内容、标出边界，并暂停等待用户评估是否进入下一阶段；"
    )
    next_rule = (
        "NEXT: <下一步，必须是可执行动作；禁止写 无/等待新指令/项目已完成/关闭>\n"
        if not has_scope_contract
        else "NEXT: <若合同内交付物未完成，写合同内补漏动作；若已完成，允许写 暂停等待用户评估/确认是否进入下一阶段>\n"
    )
    
    prompt = (
        f"你是项目执行器，持续推进项目「{title}」。\n"
        f"{guardrail_prompt}"
        f"{brief_prompt}"
        f"{research_prompt}"
        f"{mind_prompt}"
        f"{content_prompt}"
        f"{delivery_policy}\n"
        f"selector 选择的 event_kind：{event_kind or 'project_step'}；"
        f"本次完成后是否停止自动续跑：{'yes' if stop_after else 'no'}。\n"
        f"目标：{objective}\n"
        f"状态摘要：{state_snapshot}\n"
        f"建议产物：{artifact_hint}\n"
        f"候选行动集合：run_experiment/read_paper/inspect_result/update_report/debug_pipeline/summarize_failure/"
        f"test_transfer_method/write_ppt_section/refresh_project_brief/process_idea/direct_edit。\n"
        f"规则：每轮必须根据交付模式和上下文从候选行动集合里选一个 ACTION，只做一个最小闭环；"
        f"不要按固定顺序执行所有动作；简单交付就直接交付，长期项目才持续推进；"
        f"优先用本地内容；默认不联网；只允许 HTTPS；禁止 curl|bash / curl|python；"
        f"如果是新项目、陌生领域、资料依赖任务或用户明确要求调研，先用 read_paper/process_idea 做项目起步卡：目标、重点难点、常见做法/资料路线、最小闭环；"
        f"如果任务不需要背景核验，不要机械搜索文献，直接推进最小可验证动作；"
        f"把长期研究记忆当作启发和边界，不要机械复述；如果某方法在本项目失败，只记录边界，不要认为它在所有项目都失败；"
        f"{completion_rule}"
        f"关键数字必须来自本地文件或本轮实际输出；没有证据就标为 hypothesis；用户汇报要分清 verified/inferred/next；"
        f"如果遇到 API key/预算/账号/真实数据/源目录等外部阻塞，可以明确写出 BLOCKER，但 NEXT 必须同时给出不依赖该资源的替代推进动作；"
        f"内容巡游类项目不能因为 GitHub README 容易获取就替代用户分享内容，GitHub 只能作为证据渠道；"
        f"simulation/dry-run/proxy/synthetic/toy data 必须明确标注，不能写成真实 API、真实最佳或真实突破；"
        f"不要让用户做选择题，不要碰 /mnt/e/work/biomni*；不要输出 tool_call、function、terminal、read_file、write_file 标签；"
        f"禁止创建、修改或写入 Hermes skills/SKILL.md/system/hermes_home/skills；新方法、新框架、新习惯必须写到 Partner 项目目录或 user/partner_mind，而不是注册成 Hermes skill；"
        f"DONE/FINDINGS/NEXT 必须写内容进展和判断，不要把“更新某文件、文件数、字节数、目录结构”当作成果；"
        f"不要描述你'打算检查环境'，不要先说你要去看什么，直接给最终正文。"
        f"把状态摘要视为可信输入，除非目标明确要求，否则不要再重复检查这些文件是否存在。\n"
        f"{contract_boundary}"
        f"执行要求：你有 terminal、file、web 工具可用。"
        f"如果本轮目标涉及运行代码、复制文件、下载数据、执行分析，必须用 terminal 工具实际执行，"
        f"不能只设计方案或写计划文件。先执行，再总结结果。\n"
        f"{execution_enforcement}"
        f"严格只输出：\n"
        f"ACTION: <从行动集合中选一个>\n"
        f"DONE: <本轮完成>\n"
        f"FINDINGS: <最多两条发现，用；分隔>\n"
        f"EVIDENCE: <证据文件/输出路径；没有则写 hypothesis>\n"
        f"{next_rule}"
        f"FILES: <本轮写入或更新的文件；没有则写 EMPTY>\n"
        f"STATE_DELTA: <3-6行状态增量，纯文本，不要重写整份 state.md>\n"
        f"ARTIFACT_CONTENT: <如果建议产物不是空，就直接给这个文件的正文；若无则写 EMPTY>\n"
    )
    return prompt, artifact_path


def _looks_like_stalled_project_result(parsed: dict, *, allow_contract_pause: bool = False) -> bool:
    if not parsed:
        return False
    text = "\n".join(
        str(parsed.get(k) or "")
        for k in ("step_done", "next_action", "state_delta", "evidence", "files")
    )
    if allow_contract_pause:
        text_for_stall = re.sub(r"(暂停)?等待用户(?:评估|确认)?[^。\n]*(?:下一阶段|继续|进入|是否)[^。\n]*", "", text)
    else:
        text_for_stall = text
    if re.search(r"(项目已完成|项目已关闭|已关闭|项目关闭|归档状态|不再进行|等待新指令|等待用户|NEXT:\s*无)", text_for_stall):
        return True
    next_action = str(parsed.get("next_action") or "").strip()
    if allow_contract_pause and re.fullmatch(r"(暂停)?等待用户(?:评估|确认)?.*(?:下一阶段|继续|进入|是否).*", next_action, re.I):
        return False
    if not next_action or re.fullmatch(r"(无|暂无|没有|无需|等待.*|已完成|关闭|N/?A|EMPTY)", next_action, re.I):
        return True
    evidence = str(parsed.get("evidence") or "").strip().lower()
    files = str(parsed.get("files") or "").strip().lower()
    action = str(parsed.get("action") or "").strip()
    if action == "inspect_result" and evidence == "hypothesis" and files in {"", "empty"}:
        return True
    return False


def _breakthrough_next_action(title: str, parsed: dict) -> str:
    return (
        "做一次完成态逃逸复盘：回看用户根目标、已证实结果、未验证假设、失败边界和已有文件，"
        "选择一个仍能产生新信息的最小下一步；如果根目标已经满足，则调用 stop_project。"
    )


def _repair_stalled_project_result(title: str, parsed: dict) -> dict:
    repaired = dict(parsed or {})
    original_done = str(repaired.get("step_done") or "").strip()
    original_next = str(repaired.get("next_action") or "").strip()
    repaired["action"] = "process_idea"
    repaired["step_done"] = "检测到完成态/等待态空转，已转入下一突破口生成"
    repaired["findings"] = [
        "上一轮结果没有给出可执行下一步，不能作为长期伙伴的停止信号",
        f"原始完成描述：{_clip(original_done or 'EMPTY', 90)}；原始下一步：{_clip(original_next or 'EMPTY', 90)}",
    ]
    repaired["evidence"] = "system:stalled_result_repair"
    repaired["next_action"] = _breakthrough_next_action(title, parsed)
    repaired["files"] = "EMPTY"
    repaired["state_delta"] = (
        "完成态逃逸：本轮输出被识别为项目完成/等待/无下一步的空转信号。\n"
        "系统不会停止项目生命线，而是转入复盘、误差分析、失败边界、跨项目迁移或下一突破口。\n"
        f"下一步：{repaired['next_action']}"
    )
    repaired["artifact_content"] = "EMPTY"
    return repaired


def _extract_labeled_field(text: str, label: str) -> str:
    pattern = rf"^{re.escape(label)}:\s*(.*)$"
    match = re.search(pattern, text, re.MULTILINE)
    return (match.group(1).strip() if match else "")


def _parse_structured_project_response(response: str) -> dict:
    response = (response or "").strip()
    if not response:
        return {}
    if response == USER_FRIENDLY_PROGRESS_REPLY:
        return {}

    if "Too many requests" in response or "Error code: 429" in response:
        return {}
    if "Reached maximum iterations" in response and "DONE:" not in response:
        return {}

    step_done = _extract_labeled_field(response, "DONE")
    next_action = _extract_labeled_field(response, "NEXT")
    action = _extract_labeled_field(response, "ACTION")
    evidence = _extract_labeled_field(response, "EVIDENCE")
    files = _extract_labeled_field(response, "FILES")

    findings = []
    findings_line = _extract_labeled_field(response, "FINDINGS")
    if findings_line:
        findings = [
            item.strip(" -")
            for item in re.split(r"[；;]|\n\s*(?:[-*]|\d+[.)、])\s*", findings_line)
            if item.strip(" -")
        ][:4]

    state_match = re.search(
        r"^STATE_DELTA:\s*(?P<body>.*)\Z",
        response,
        re.MULTILINE | re.DOTALL,
    )
    tail = state_match.group("body").strip() if state_match else ""
    artifact_content = ""
    state_update = tail
    if tail:
        artifact_split = re.search(
            r"(?m)^\s*ARTIFACT_CONTENT:\s*",
            tail,
        )
        if artifact_split:
            idx = artifact_split.start()
            state_update = tail[:idx].strip()
            artifact_content = tail[artifact_split.end():].strip()
    if not artifact_content:
        artifact_content = _extract_labeled_field(response, "ARTIFACT_CONTENT")

    parsed = {
        "step_done": step_done,
        "action": action,
        "findings": findings,
        "evidence": evidence,
        "next_action": next_action,
        "files": files,
        "state_delta": state_update,
        "artifact_content": artifact_content,
    }
    if not any([parsed["step_done"], parsed["findings"], parsed["next_action"], parsed["state_delta"]]):
        return {}
    return parsed


def _merge_state_delta(existing_state: str, title: str, delta: str, step_done: str, next_action: str) -> str:
    """Append a compact delta block instead of asking the model to rewrite full state."""
    existing = (existing_state or "").strip()
    lines = [line.rstrip() for line in (delta or "").splitlines() if line.strip()]
    if not lines and not step_done and not next_action:
        return existing or f"# 项目：{title}\n"

    if not existing:
        existing = f"# 项目：{title}"
    if "# 项目：" not in existing:
        existing = f"# 项目：{title}\n\n{existing}"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = [f"最后更新: {now}", "", "## 当前状态"]
    if step_done:
        block.append(f"- 最近完成：{step_done}")
    for line in lines[:4]:
        cleaned = line.lstrip("- ").strip()
        if cleaned:
            block.append(f"- {cleaned}")
    if next_action:
        block.append(f"- 下一步：{next_action}")

    prefix = []
    seen_current = False
    for raw in existing.splitlines():
        stripped = raw.strip()
        if stripped.startswith("最后更新:"):
            continue
        if stripped == "## 当前状态":
            seen_current = True
            continue
        if seen_current and stripped.startswith("## "):
            seen_current = False
        if not seen_current:
            prefix.append(raw)
    cleaned_prefix = "\n".join(prefix).strip()
    return (cleaned_prefix + "\n\n" + "\n".join(block)).strip() + "\n"


def _normalize_artifact_content(content: str) -> str:
    text = (content or "").strip()
    if not text or text.upper() == "EMPTY":
        return ""
    text = re.sub(r"^```(?:markdown|md|text)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = _strip_internal_markup_from_artifact(text)
    text = text.strip()
    return text


def _looks_like_artifact_meta_summary(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    if len(stripped) < 1000 and re.search(r"(已在上方|write_file|完整写入|内容涵盖|源文件已|已撰写完成)", stripped):
        return True
    if len(stripped) < 800 and re.search(r"^(已|报告).*?(写入|生成|完成)", stripped) and "# " not in stripped[:160]:
        return True
    return False


def _extract_added_file_from_review_diff(response: str, target_basename: str) -> str:
    text = response or ""
    target = os.path.basename(target_basename or "")
    if not text or not target:
        return ""
    lines = text.splitlines()
    active = False
    collected: list[str] = []
    saw_target = False
    for raw in lines:
        line = raw.rstrip("\n")
        if (
            re.search(rf"(?:^|[/\\]){re.escape(target)}\s*(?:→|->)\s*(?:.+[/\\])?{re.escape(target)}", line)
            or re.search(rf"diff --git\s+a/(?:.+/)?{re.escape(target)}\s+b/(?:.+/)?{re.escape(target)}", line)
        ):
            active = True
            saw_target = True
            collected = []
            continue
        if active and re.match(r"^(ACTION|DONE|FINDINGS|EVIDENCE|NEXT|FILES|STATE_DELTA|ARTIFACT_CONTENT):\s*", line):
            break
        if active and re.match(r"^(diff --git|[ab]/.+\s+→\s+[ab]/.+)", line) and target not in line:
            break
        if active and line.startswith("+") and not line.startswith("+++"):
            collected.append(line[1:])
    body = "\n".join(collected).strip()
    if saw_target and len(body) > 200:
        return _normalize_artifact_content(body)
    return ""


def _tokenize_path_query(text: str) -> set[str]:
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text or "")
        if len(token.strip()) >= 2
    }
    stop = {
        "report", "pdf", "source", "project", "user", "reports", "png", "jpg", "jpeg", "webp",
        "包含", "报告", "重新", "生成", "图片", "图像", "刚刚", "这个", "那个",
    }
    return {token for token in tokens if token not in stop}


def _find_relevant_report_images(title: str, user_request: str, state_md: str, limit: int = 4) -> list[str]:
    if not _workspace:
        return []
    roots = [
        os.path.join(_workspace, "deliverables"),
        os.path.join(_workspace, "user"),
        os.path.join(_workspace, "20_records", "projects"),
        os.path.join(_workspace, "30_artifacts"),
    ]
    query = " ".join([title or "", user_request or "", state_md or ""])
    tokens = _tokenize_path_query(query)
    wants_image = bool(re.search(r"(图|图片|图像|可视化|架构图|plot|image|diagram|visual)", query, re.I))
    rows: list[tuple[int, float, str]] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*"), recursive=True):
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            rel = os.path.relpath(path, _workspace).replace(os.sep, "/")
            haystack = rel.lower()
            score = 0
            for token in tokens:
                if token and token in haystack:
                    score += 12
            if re.search(r"(plot|diagram|architecture|visual|可视化|架构|图)", haystack, re.I):
                score += 6
            if rel.startswith("deliverables/"):
                score += 4
            if score < 12:
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            rows.append((score, mtime, os.path.abspath(path)))
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    result: list[str] = []
    seen: set[str] = set()
    for _, _, path in rows:
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
        if len(result) >= limit:
            break
    return result


def _ensure_pdf_report_image_refs(text: str, title: str, user_request: str, state_md: str) -> str:
    body = (text or "").strip()
    if not body:
        return body
    if re.search(r"!\[[^\]]*\]\([^)]+\)", body):
        return body
    context = " ".join([body, title or "", user_request or "", state_md or ""])
    if not re.search(r"(图|图片|图像|可视化|架构图|plot|image|diagram|visual)", context, re.I):
        return body
    images = _find_relevant_report_images(title, user_request, state_md)
    if not images:
        return body
    lines = [body.rstrip(), "", "## 图像", ""]
    for idx, path in enumerate(images, start=1):
        alt = os.path.splitext(os.path.basename(path))[0]
        lines.append(f"![{alt}]({path})")
        if idx != len(images):
            lines.append("")
    return "\n".join(lines).strip()


def _repair_pdf_report_artifact_content(
    content: str,
    response: str,
    title: str,
    user_request: str,
    state_md: str,
    artifact_path: str,
) -> str:
    text = (content or "").strip()
    if _looks_like_artifact_meta_summary(text):
        recovered = _extract_added_file_from_review_diff(response, os.path.basename(artifact_path))
        if recovered:
            text = recovered
    if _looks_like_artifact_meta_summary(text):
        images = _find_relevant_report_images(title, user_request, state_md)
        lines = [
            f"# {title}",
            "",
            "## 用户请求",
            user_request or "生成 PDF 报告。",
            "",
            "## 报告说明",
            "本报告根据当前项目已有结果重新整理，重点保留用户点名要求包含的图像或可视化产物。",
        ]
        if images:
            lines.extend(["", "## 图像"])
            for path in images:
                alt = os.path.splitext(os.path.basename(path))[0]
                lines.extend(["", f"![{alt}]({path})"])
        if state_md.strip():
            lines.extend(["", "## 项目状态摘要", _clip(state_md.strip(), 1800)])
        text = "\n".join(lines).strip()
    return _ensure_pdf_report_image_refs(text, title, user_request, state_md)


def _strip_internal_markup_from_artifact(text: str) -> str:
    """Clean agent protocol/diff noise before writing project artifacts."""
    if not text:
        return ""

    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            lines.append("")
            continue
        if re.match(r"<\s*/?\s*(tool_call|invoke|function|parameter)\b", stripped, re.I):
            continue
        if re.match(r"<\s*(function|parameter)\s*=", stripped, re.I):
            continue
        if re.match(r"(diff --git|index [0-9a-f]+\.\.|--- |\+\+\+ |@@ )", stripped):
            continue
        if re.match(r"… omitted \d+ diff line", stripped):
            continue
        if re.match(
            r"^(ACTION|DONE|FINDINGS|EVIDENCE|NEXT|FILES|STATE_DELTA|ARTIFACT_CONTENT):\s*",
            stripped,
        ):
            break
        if raw.startswith("+") and not raw.startswith("+++"):
            raw = raw[1:]
        lines.append(raw.rstrip())

    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _fallback_artifact_content(response: str, artifact_path: str) -> str:
    basename = os.path.basename(artifact_path or "")
    text = (response or "").strip()
    if not basename or not text:
        return ""

    if basename == "next_experiment.md":
        lines = []
        for prefix in ("目标：", "输入：", "产出：", "验收标准："):
            match = re.search(rf"(?m)^{re.escape(prefix)}.*$", text)
            if match:
                lines.append(match.group(0).strip())
        return "\n".join(lines).strip()

    code_block = re.search(r"```(?:markdown|md|text)?\s*(?P<body>.*?)```", text, re.DOTALL)
    if code_block:
        return code_block.group("body").strip()

    named_block = re.search(
        rf"{re.escape(basename)}.*?\n(?P<body>(?:.+\n?){{3,40}})",
        text,
        re.DOTALL,
    )
    if named_block:
        return named_block.group("body").strip()
    return ""


def _structured_audit_artifact(path: str, parsed: dict, response: str = "") -> str:
    basename = os.path.basename(path or "")
    if basename not in {"data_leakage_audit.md", "progress_quality_audit.md"}:
        return ""
    title = "数据泄露审计" if basename == "data_leakage_audit.md" else "推进质量审计"
    findings = parsed.get("findings") or []
    lines = [
        f"# {title}",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 本轮结论",
        str(parsed.get("step_done") or "本轮完成审计。").strip(),
        "",
        "## 关键发现",
    ]
    if findings:
        for item in findings[:4]:
            lines.append(f"- {item}")
    else:
        lines.append("- 未提取到明确发现，需要下一轮继续核验证据。")
    lines.extend([
        "",
        "## 证据",
        str(parsed.get("evidence") or "hypothesis").strip(),
        "",
        "## 状态增量",
        str(parsed.get("state_delta") or "EMPTY").strip(),
        "",
        "## 下一步",
        str(parsed.get("next_action") or "继续补齐真实证据后再推进。").strip(),
    ])
    raw = _sanitize_user_report_text(response or "")
    if raw:
        lines.extend(["", "## 原始结构化输出摘录", _clip(raw, 1600)])
    return "\n".join(lines).strip()


def _artifact_needs_structured_fallback(path: str, content: str) -> bool:
    basename = os.path.basename(path or "")
    if basename not in {"data_leakage_audit.md", "progress_quality_audit.md"}:
        return False
    text = (content or "").strip()
    if len(text) < 180:
        return True
    if text.startswith(("python", "diff", "+", "-", "```")) and "# " not in text[:80]:
        return True
    return False


def _write_artifact_file(path: str, content: str) -> bool:
    if not path or not content:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n")
        return True
    except OSError:
        return False


def _format_user_progress_update(parsed: dict, project_title: str = "") -> str:
    if not parsed.get("step_done"):
        return ""
    if str(parsed.get("evidence") or "").startswith("system:"):
        return ""
    
    step_done = parsed["step_done"]
    findings = parsed.get("findings") or []
    next_action = parsed.get("next_action") or ""
    
    # 去重检测：如果关键发现和下一步跟上一轮完全相同，标注
    cache_key = (tuple(findings), next_action)
    last_cache = _last_report_cache.get(project_title, None) if project_title else None
    is_duplicate = (last_cache == cache_key and findings)
    
    if is_duplicate:
        return ""
    
    # 更新缓存
    if project_title:
        _last_report_cache[project_title] = cache_key
    
    lines = [f"最近完成：{step_done}"]
    if findings:
        lines.append("关键发现：")
        for finding in findings[:2]:
            lines.append(f"- {_clip(finding, 140)}")
    if next_action:
        lines.append(f"下一步：{next_action}")
    text = _sanitize_user_report_text("\n".join(lines).strip())
    if _is_blank_user_visible_text(text):
        return ""
    return text


def _tail_text_file(path: str, max_lines: int = 40) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:]).strip()
    except OSError:
        return ""


def _known_done_risk_text(workspace: str, title: str, state_md: str, hot_text: str) -> str:
    """Scan configured source/evidence files for stale false-completion signals."""
    texts = [state_md or "", hot_text or ""]
    try:
        from ..project_state import read_project_contract
        contract = read_project_contract(workspace, title)
        candidates = []
        for root in contract.get("source_roots") or []:
            if os.path.isdir(root):
                for name in ("FINAL_REPORT.md", "current_best_result.md", "summary.md", "state.md"):
                    candidates.append(os.path.join(root, name))
        for path in candidates:
            if os.path.exists(path):
                texts.append(_tail_text_file(path, max_lines=80))
    except Exception:
        pass
    return "\n".join(texts)


def _sanitize_project_log_for_report(text: str, max_chars: int = 900) -> str:
    """Drop raw tool-call garbage and stale prompt scaffolding before report LLM sees it."""
    if not text:
        return ""

    cleaned_lines = []
    skip_block = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        if stripped.startswith("<tool_call>"):
            skip_block = True
            continue
        if skip_block:
            if stripped.startswith("</tool_call>"):
                skip_block = False
            continue
        if any(
            token in stripped
            for token in (
                "<function=",
                "</function>",
                "<parameter=",
                "</parameter>",
                "Reached maximum iterations",
            )
        ):
            continue
        if stripped.startswith("```"):
            continue
        if stripped.startswith("## 环境验证") or stripped.startswith("## 数据就绪") or stripped.startswith("## 脚本与流水线") or stripped.startswith("## 模型状态") or stripped.startswith("## 结果追踪"):
            continue
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            continue
        if stripped.startswith("Let me check"):
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    return _clip(cleaned, max_chars)


def _collect_report_context(workspace: str, title: str, project_outcome: str, step: int = 0) -> dict:
    from ..project_state import get_project_dir, read_state_md

    project_dir = get_project_dir(workspace, title)
    state_md = read_state_md(workspace, title)
    log_tail = _tail_text_file(os.path.join(project_dir, "trace_detail.md"), max_lines=36)
    runtime_tail = _tail_text_file(os.path.join(workspace, "10_logs", "instance.log"), max_lines=18)
    files = []
    try:
        for name in sorted(os.listdir(project_dir)):
            if os.path.isfile(os.path.join(project_dir, name)):
                files.append(name)
    except OSError:
        pass
    return {
        "state_snapshot": _compact_state_snapshot(state_md),
        "project_log_tail": _sanitize_project_log_for_report(log_tail, max_chars=900),
        "runtime_log_tail": _clip(runtime_tail, 1200),
        "files": ", ".join(files[:8]) if files else "（暂无项目文件）",
        "project_outcome": project_outcome or "本轮已执行完成",
        "growth_context": growth_context_for_report(workspace, title),
        "step": step,
        "startup_phase": _is_startup_report_step(step),
        "startup_transition": _is_startup_transition_step(step),
    }


def _build_round_report_prompt(title: str, ctx: dict) -> str:
    runtime_context = ""
    try:
        from ..runtime_monitor import compact_runtime_context

        runtime_context = compact_runtime_context(_workspace)
    except Exception:
        runtime_context = ""
    return (
        f"你是 Partner 的用户汇报器。请根据本地状态，给用户发一条自然语言进展汇报。\n"
        f"项目：{title}\n"
        f"当前轮次：step {ctx.get('step', 0)}\n"
        f"本轮执行结果：{ctx['project_outcome']}\n"
        f"状态摘要：\n{ctx['state_snapshot']}\n"
        f"{ctx.get('growth_context') or ''}\n"
        f"{runtime_context}\n"
        f"要求：\n"
        f"- 只用“本轮执行结果”和“状态摘要”判断内容进展，不要复述长日志。\n"
        f"- 可以说明必要的交付物、文件类型或产出位置，但不要暴露内部实现噪声。\n"
        f"- 以“状态摘要”为当前事实来源；除非这里明确显示缺失，否则不要声称文件不存在或状态丢失。\n"
        f"- 如果这是 step 0-2 的起步阶段，要更主动汇报：说明你对用户指令的理解、项目目标、初始框架/当前情况、下一步切入点。\n"
        f"- 每次 event 执行后都要给用户一个可见回执，哪怕只是说明本轮没有形成新结论。\n"
        f"- 直接像对用户汇报一样说话，用中文。\n"
        f"- 说明现在在做什么、本轮发生了什么、下一步是什么；如果下一步需要用户补充信息，可以直接澄清。\n"
        f"- 如果“最近成长事件”非空，要用一句话说明我这次改变了什么判断习惯或推进习惯。\n"
        f"- 如果运行消耗摘要显示失败较多、耗时异常长或 token 很高，可以用一句话提醒；否则不要主动谈成本。\n"
        f"- 不要输出 JSON，不要用标题。\n"
        f"- 如果项目完成，要明确说明当前选择结束执行或进入反思状态。\n"
        f"- 起步阶段控制在 120-260 字；稳定阶段控制在 80-160 字。\n"
    )


def _strip_state_prefix(line: str) -> str:
    text = (line or "").strip()
    for prefix in ("- 最近完成：", "- 下一步：", "- ", "当前状态：", "当前聚焦方向："):
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _build_round_report_fallback(title: str, project_outcome: str) -> str:
    """LLM 生成报告失败时，返回空字符串，不发送硬编码 fallback。"""
    return ""


def _generate_round_report(title: str, project_outcome: str, step: int = 0) -> str:
    if not _adapter:
        return ""
    ctx = _collect_report_context(_workspace, title, project_outcome, step=step)
    prompt = _build_round_report_prompt(title, ctx)
    try:
        reply = (_adapter.chat(prompt, purpose="report") or "").strip()
    except Exception as exc:
        logger.warning(f"[REPORT] LLM report generation failed: {exc}")
        return ""
    if not reply or reply == USER_FRIENDLY_PROGRESS_REPLY:
        return ""
    reply = _sanitize_user_report_text(reply)
    if not reply:
        return ""
    if _is_blank_user_visible_text(reply):
        return ""
    if reply.startswith("{") and "partner_heartbeat" in reply:
        return ""
    if ctx.get("startup_transition"):
        reply = reply.rstrip()
    return reply


def _format_stage_report_notification(title: str, published: dict) -> str:
    return ""


def _push_stage_report_files(published: dict) -> bool:
    """Best-effort QQ file push for generated stage reports.

    User-facing reports are sent as PDF only. Markdown/PPTX stay in workspace
    unless the user explicitly asks for those formats in a direct deliverable.
    """
    if _file_push_callback is None:
        return False
    sent = False
    path = published.get("pdf")
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as f:
            data = f.read()
        ok = _file_push_callback(data, os.path.basename(path), os.path.basename(path))
        sent = bool(ok) or sent
    except Exception as exc:
        logger.warning(f"[REPORT] stage report file push failed for {path}: {exc}")
    return sent


_ONE_SHOT_FILE_EXTS = {
    ".xlsx", ".xls", ".csv", ".tsv", ".docx", ".pptx", ".pdf",
    ".png", ".jpg", ".jpeg", ".webp", ".txt",
    ".mp4", ".m4v", ".mov", ".webm",
}


def _safe_report_name(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", name or "report").strip("_") or "report"


def _resolve_pdf_report_image_path(image_path: str, source_dir: str = "") -> str:
    raw = (image_path or "").strip().strip("\"'")
    if not raw:
        return ""
    candidates = []
    if os.path.isabs(raw):
        candidates.append(raw)
    else:
        if source_dir:
            candidates.append(os.path.join(source_dir, raw))
        if _workspace:
            candidates.append(os.path.join(_workspace, raw))
            candidates.append(os.path.join(_workspace, "deliverables", raw))
    for candidate in candidates:
        if os.path.exists(candidate) and os.path.isfile(candidate):
            return os.path.abspath(candidate)

    basename = os.path.basename(raw)
    if not basename or not _workspace:
        return ""
    roots = [
        os.path.join(_workspace, "deliverables"),
        os.path.join(_workspace, "user"),
        os.path.join(_workspace, "20_records", "projects"),
        os.path.join(_workspace, "30_artifacts"),
    ]
    matches = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        matches.extend(glob.glob(os.path.join(root, "**", basename), recursive=True))
    files = [path for path in matches if os.path.isfile(path)]
    if not files:
        return ""
    files.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return os.path.abspath(files[0])


def _write_user_pdf_report(title: str, source_name: str, body: str, source_dir: str = "") -> str:
    """Render a simple text artifact to a user-facing PDF report."""
    text = (body or "").strip()
    if not text:
        return ""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Image as PlatypusImage
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except Exception as exc:
        logger.debug(f"[REPORT] reportlab unavailable for PDF report: {exc}")
        return ""

    font_name = ""
    try:
        for candidate in (
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
            "/usr/share/fonts/opentype/unifont/unifont.otf",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
        ):
            if os.path.exists(candidate):
                pdfmetrics.registerFont(TTFont("PartnerCJK", candidate))
                font_name = "PartnerCJK"
                break
    except Exception:
        font_name = ""
    if not font_name:
        logger.warning("[REPORT] no embeddable CJK font found for PDF report")
        return ""

    report_dir = os.path.join(_workspace, "user", "reports", _safe_report_name(title))
    os.makedirs(report_dir, exist_ok=True)
    base = os.path.splitext(_safe_report_name(source_name))[0] or "report"
    path = os.path.join(report_dir, f"{base}.pdf")
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PartnerTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=18,
        leading=24,
        spaceAfter=14,
    )
    body_style = ParagraphStyle(
        "PartnerBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10.5,
        leading=16,
        spaceAfter=8,
    )
    heading_style = ParagraphStyle(
        "PartnerHeading",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=13,
        leading=18,
        spaceBefore=10,
        spaceAfter=6,
    )
    page_width = A4[0] - 3.2 * cm
    max_image_height = A4[1] - 5.2 * cm
    story = [Paragraph(_escape_pdf_text(title), title_style)]
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            story.append(Spacer(1, 0.18 * cm))
            continue
        image_match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line)
        if image_match:
            alt = image_match.group(1).strip()
            image_path = image_match.group(2).strip().strip("\"'")
            resolved_image_path = _resolve_pdf_report_image_path(image_path, source_dir=source_dir)
            if resolved_image_path:
                try:
                    if alt:
                        story.append(Paragraph(_escape_pdf_text(alt), body_style))
                    img = PlatypusImage(resolved_image_path)
                    scale = min(page_width / float(img.imageWidth or 1), max_image_height / float(img.imageHeight or 1), 1.0)
                    img.drawWidth = float(img.imageWidth) * scale
                    img.drawHeight = float(img.imageHeight) * scale
                    story.append(img)
                    story.append(Spacer(1, 0.22 * cm))
                    continue
                except Exception as exc:
                    logger.warning(f"[REPORT] failed to embed image in PDF report: {resolved_image_path}: {exc}")
            story.append(Paragraph(_escape_pdf_text(f"[image unavailable] {alt or image_path}"), body_style))
            continue
        if line.startswith("#"):
            line = re.sub(r"^#+\s*", "", line).strip()
            story.append(Paragraph(_escape_pdf_text(line), heading_style))
        else:
            story.append(Paragraph(_escape_pdf_text(line), body_style))
    try:
        doc = SimpleDocTemplate(
            path,
            pagesize=A4,
            rightMargin=1.6 * cm,
            leftMargin=1.6 * cm,
            topMargin=1.6 * cm,
            bottomMargin=1.6 * cm,
        )
        doc.build(story)
        if os.path.exists(path) and os.path.getsize(path) > 1200:
            return path
    except Exception as exc:
        logger.debug(f"[REPORT] failed to render PDF report: {exc}")
    return ""


def _escape_pdf_text(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _action_event_should_publish_pdf(event_type: EventType) -> bool:
    return event_type == EventType.PDF_REPORT


def _required_output_exts(user_request: str, event_type: str = "", event_kind: str = "") -> set[str]:
    text = " ".join([user_request or "", event_type or "", event_kind or ""])
    required: set[str] = set()
    if re.search(r"(excel|xlsx|xls|工作簿)", text, re.I):
        required.update({".xlsx", ".xls"})
    if re.search(r"\bcsv\b|逗号分隔", text, re.I):
        required.add(".csv")
    if re.search(r"\bpptx?\b|幻灯片|PPT", text, re.I):
        required.add(".pptx")
    if re.search(r"\bpdf\b", text, re.I):
        required.add(".pdf")
    if event_type == EventType.PDF_REPORT.value:
        required.add(".pdf")
    if re.search(r"(图片|截图|图像|png|jpg|jpeg|webp)", text, re.I):
        required.update({".png", ".jpg", ".jpeg", ".webp"})
    if re.search(r"(word|docx)", text, re.I):
        required.add(".docx")
    return required


def _resolve_one_shot_output_files(project_dir: str, parsed: dict | None,
                                   artifact_path: str = "",
                                   since_ts: float | None = None,
                                   required_exts: set[str] | None = None) -> list[str]:
    """Resolve user-facing files created by a one-shot event.

    The primary source is structured FILES. As a fallback, scan recent files in
    the project directory so agents that generated an artifact but forgot to
    list it can still deliver it.
    """
    paths: list[str] = []
    seen: set[str] = set()

    required_exts = {x.lower() for x in (required_exts or set()) if x}

    def add_path(raw: str) -> None:
        raw = str(raw or "").strip().strip("`'\"，,；;。")
        if not raw or raw.upper() == "EMPTY":
            return
        if re.match(r"^[a-zA-Z]+://", raw):
            return
        path = raw if os.path.isabs(raw) else os.path.join(project_dir, raw)
        path = os.path.abspath(path)
        workspace_root = os.path.abspath(_workspace or project_dir)
        user_root = os.path.abspath(os.path.join(_workspace or project_dir, "user"))
        deliverables_root = os.path.abspath(os.path.join(_workspace or project_dir, "deliverables"))
        artifacts_root = os.path.abspath(os.path.join(_workspace or project_dir, "30_artifacts"))
        allowed = False
        try:
            common = os.path.commonpath([os.path.abspath(project_dir), path])
            allowed = common == os.path.abspath(project_dir)
        except ValueError:
            allowed = False
        if not allowed:
            try:
                common_user = os.path.commonpath([user_root, path])
                allowed = common_user == user_root
            except ValueError:
                allowed = False
        if not allowed:
            try:
                common_deliverables = os.path.commonpath([deliverables_root, path])
                allowed = common_deliverables == deliverables_root
            except ValueError:
                allowed = False
        if not allowed:
            try:
                common_artifacts = os.path.commonpath([artifacts_root, path])
                allowed = common_artifacts == artifacts_root
            except ValueError:
                allowed = False
        if not allowed:
            try:
                parent = os.path.dirname(path)
                allowed = os.path.commonpath([workspace_root, path]) == workspace_root and parent == workspace_root
            except ValueError:
                allowed = False
        if not allowed:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in _ONE_SHOT_FILE_EXTS:
            return
        if required_exts and ext not in required_exts:
            return
        if os.path.exists(path) and os.path.isfile(path) and path not in seen:
            seen.add(path)
            paths.append(path)

    files_text = str((parsed or {}).get("files") or "")
    for part in re.split(r"[;\n，,]+", files_text):
        add_path(part)
    if artifact_path:
        add_path(artifact_path)

    if paths:
        return paths[:6]

    cutoff = float(since_ts or 0)
    recent: list[str] = []
    skip_parts = {".git", "__pycache__", "10_logs", "state", "logs"}
    scan_roots = [project_dir]
    workspace_root = os.path.abspath(_workspace or project_dir)
    deliverables_root = os.path.join(workspace_root, "deliverables")
    if os.path.isdir(deliverables_root):
        scan_roots.append(deliverables_root)
    scan_roots.append(workspace_root)
    scanned_roots: set[str] = set()
    for root in scan_roots:
        root = os.path.abspath(root)
        if root in scanned_roots or not os.path.isdir(root):
            continue
        scanned_roots.add(root)
        if root == workspace_root:
            try:
                root_names = os.listdir(root)
            except OSError:
                root_names = []
            for name in root_names:
                path = os.path.join(root, name)
                if not os.path.isfile(path):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in _ONE_SHOT_FILE_EXTS:
                    continue
                if required_exts and ext not in required_exts:
                    continue
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if cutoff and mtime < cutoff - 5:
                    continue
                recent.append(path)
            continue
        for cur, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip_parts and not d.startswith(".")]
            for name in files:
                path = os.path.join(cur, name)
                ext = os.path.splitext(name)[1].lower()
                if ext not in _ONE_SHOT_FILE_EXTS:
                    continue
                if required_exts and ext not in required_exts:
                    continue
                if name in {"state.md", "trace_detail.md", "exploration_log.md", "project_brief.md"}:
                    continue
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if cutoff and mtime < cutoff - 5:
                    continue
                recent.append(path)
    recent.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for path in recent:
        add_path(path)
    return paths[:6]


def _push_one_shot_output_files(project_dir: str, parsed: dict | None,
                                artifact_path: str = "",
                                since_ts: float | None = None,
                                required_exts: set[str] | None = None) -> tuple[bool, list[str]]:
    """Best-effort QQ file push for direct one-shot deliverables."""
    files = _resolve_one_shot_output_files(project_dir, parsed, artifact_path, since_ts, required_exts=required_exts)
    if _file_push_callback is None or not files:
        return False, files
    sent = False
    for path in files:
        try:
            with open(path, "rb") as f:
                data = f.read()
            label = os.path.basename(path)
            ok = _file_push_callback(data, os.path.basename(path), label)
            sent = bool(ok) or sent
        except Exception as exc:
            logger.warning(f"[REPORT] one-shot file push failed for {path}: {exc}")
    return sent, files


def _latest_stage_report_outputs(title: str) -> dict:
    """Find the latest user-facing stage report outputs for a project."""
    safe_title = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", title).strip("_") or "project"
    report_dir = os.path.join(_workspace, "user", "reports", safe_title)
    outputs: dict[str, str] = {}
    if not os.path.isdir(report_dir):
        return outputs
    latest_md = os.path.join(report_dir, "latest_stage_report.md")
    if os.path.exists(latest_md):
        outputs["markdown"] = latest_md
    try:
        names = sorted(
            os.listdir(report_dir),
            key=lambda name: os.path.getmtime(os.path.join(report_dir, name)),
            reverse=True,
        )
    except OSError:
        return outputs
    for name in names:
        path = os.path.join(report_dir, name)
        if name.lower().endswith(".pdf") and "pdf" not in outputs:
            outputs["pdf"] = path
        elif name.lower().endswith(".pptx") and "pptx" not in outputs:
            outputs["pptx"] = path
        if "pdf" in outputs and "pptx" in outputs:
            break
    show_dir = os.path.join(_workspace, "user", "showcase", safe_title)
    if os.path.exists(os.path.join(show_dir, "README.md")):
        outputs["showcase"] = show_dir
    return outputs


def _stage_report_text_summary(title: str, pause_reason: str, outputs: dict) -> str:
    """Natural QQ text for a finished basic task / stage report."""
    md = outputs.get("markdown") or ""
    body = _read_text(md, 5000) if md else ""
    bullets: list[str] = []
    for raw in body.splitlines():
        line = raw.strip().lstrip("-*#0123456789.、 ").strip()
        if not line or len(line) < 8:
            continue
        if re.search(r"(阶段汇报|目录|下一步|文件|workspace|showcase)", line, re.I):
            continue
        bullets.append(line)
        if len(bullets) >= 3:
            break
    return "；".join(bullets[:2]) if bullets else ""


def _event_completion_receipt(title: str, event_type: EventType | str, parsed: dict | None,
                              *, next_event: str = "", next_reason: str = "",
                              files: list[str] | None = None) -> str:
    parsed = parsed or {}
    llm_text = _format_event_completion_receipt_with_llm(
        title,
        event_type,
        parsed,
        next_event=next_event,
        next_reason=next_reason,
        files=files,
    )
    if llm_text:
        return llm_text
    logger.warning(
        "[REPORT] LLM receipt formatter unavailable; refusing code-generated completion receipt "
        "for event_type=%s title=%s",
        event_type.value if isinstance(event_type, EventType) else str(event_type or ""),
        title,
    )
    return UNAVAILABLE_NOTICE


def _format_event_completion_receipt_with_llm(title: str, event_type: EventType | str, parsed: dict,
                                             *, next_event: str = "", next_reason: str = "",
                                             files: list[str] | None = None) -> str:
    """Let the model decide how much of an event result should be shown."""
    if not _adapter or os.getenv("PARTNER_DISABLE_LLM_RECEIPT_FORMATTER", "").lower() in {"1", "true", "on", "yes"}:
        return ""
    event_value = event_type.value if isinstance(event_type, EventType) else str(event_type or "")
    event_label = prefix_event_notice("x", event_value).splitlines()[0].strip("【】").replace("事件：", "")
    next_label = prefix_event_notice("x", next_event).splitlines()[0].strip("【】").replace("事件：", "") if next_event else ""
    artifact = _compact_artifact_for_receipt(str(parsed.get("artifact_content") or ""), limit=2600)
    user_files = [os.path.basename(p) for p in (files or []) if p]
    prompt = f"""你是 Partner 的 event 完成汇报 formatter。你只根据已完成 event 的结果组织给用户的消息，不重新执行任务，不补事实，不编造来源。

任务/项目：{title}
当前 event：{event_value} / {event_label}
DONE：{str(parsed.get('step_done') or '')[:700]}
FINDINGS：{json.dumps(parsed.get('findings') or [], ensure_ascii=False)[:1200]}
EVIDENCE：{str(parsed.get('evidence') or '')[:800]}
FILES：{', '.join(user_files) if user_files else str(parsed.get('files') or 'EMPTY')[:600]}
ARTIFACT：
{artifact or 'EMPTY'}

下一步 event：{next_event or 'none'} {('/ ' + next_label) if next_label else ''}
下一步原因：{next_reason[:500] if next_reason else ''}

输出要求：
- 中文自然回复，不要 JSON，不要代码块，不要加【事件】前缀。
- 由你根据内容判断该展开多少：如果用户要解释/讲解，就直接给核心内容；如果用户要文件/图片/视频，就说明已发送或给可访问链接；如果受限，就说清访问限制。
- 必须包含本轮做了什么、用户最关心的结果、下一步将调用哪个 event 或是否结束/等待。
- 不要暴露 workspace、内部文件路径、trace、diff、tool_call。
- 控制在 80-900 字；内容太多时优先保留标题、链接、关键结论和边界。
"""
    try:
        raw = (_adapter.chat(prompt, purpose="classify") or "").strip()
    except Exception as exc:
        logger.debug(f"[REPORT] LLM receipt formatter failed: {exc}")
        return ""
    text = _sanitize_user_report_text(raw)
    if not text or _is_internal_fallback_text(text):
        return ""
    if text.startswith("```"):
        text = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    if len(text) > 1200:
        return text[:1199].rstrip() + "…"
    return text


def _compact_artifact_for_receipt(text: str, limit: int = 1000) -> str:
    """Compact artifact content into a QQ-readable answer excerpt."""
    raw = (text or "").strip()
    if not raw or raw.upper() == "EMPTY":
        return ""
    raw = strip_internal_diff(raw)
    if not raw or has_internal_diff(raw):
        return ""
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if re.search(r"^(搜索时间|搜索来源|搜索方法说明|状态标记)[:：]?", stripped):
            continue
        if re.search(r"^(---+|#+\s*)$", stripped):
            continue
        stripped = re.sub(r"^#{1,4}\s*", "", stripped)
        stripped = stripped.replace("**", "")
        lines.append(stripped)
        if len("\n".join(lines)) >= limit:
            break
    compact = "\n".join(lines).strip()
    return _clip(compact, limit)


# ── 公开接口 ────────────────────────────────────────────────────────


def set_push_callback(callback):
    """设置推送回调函数。

    callback 签名: func(content: str) -> None
    QQ bridge 在初始化时调用此函数注册回调。
    """
    global _push_callback
    _push_callback = callback
    logger.info(f"[MIND] Push callback registered: {callback}")


def set_file_push_callback(callback):
    """设置文件推送回调。callback(file_bytes, filename, caption) -> bool"""
    global _file_push_callback
    _file_push_callback = callback
    logger.info(f"[MIND] File push callback registered: {callback}")


def init(workspace: str, adapter=None, **kwargs):
    """初始化 executor（简化版：只设置 workspace + adapter）。"""
    global _workspace, _adapter, _round_interval_sec
    _workspace = workspace
    _adapter = adapter
    try:
        env_interval = os.getenv("PARTNER_PROJECT_INTERVAL_SEC")
        if env_interval:
            _round_interval_sec = max(5, int(env_interval))
        elif "project_interval_sec" in kwargs:
            _round_interval_sec = max(5, int(kwargs["project_interval_sec"]))
        elif "round_interval_sec" in kwargs:
            # scheduler.interval_minutes is the health/report pulse. Project lifeline
            # should keep moving instead of sleeping for the whole pulse interval.
            _round_interval_sec = max(5, min(60, int(kwargs["round_interval_sec"])))
    except Exception:
        _round_interval_sec = 60
    if kwargs:
        logger.debug(f"[MIND] init 忽略废弃参数: {list(kwargs.keys())}")
    logger.info(f"[MIND] Executor initialized: workspace={workspace}")


async def ensure_pool() -> MindPool:
    """获取 MindPool 单例。"""
    global _pool
    if _pool is None:
        _pool = await MindPool.get_instance()
    return _pool


async def execute_event(event: MindEvent):
    """执行一个念头：按类型分发到对应的处理函数。"""
    logger.info(f"[执行] 开始处理 #{event.id[:8]} type={event.type.value} "
                f"pri={event.priority}")

    try:
        handler = _get_handler(event.type)
        if handler:
            await handler(event)
        else:
            logger.warning(f"[执行] 无处理函数: {event.type.value}")
        _p = await ensure_pool()
        if _p._auto_save:
            _p.save()
    except asyncio.CancelledError:
        logger.info(f"[执行] 念头 #{event.id[:8]} 被取消")
    except Exception as e:
        logger.error(f"[执行] 念头 #{event.id[:8]} 执行失败: {e}", exc_info=True)


# ── 事件分发 ────────────────────────────────────────────────────────


def _get_handler(event_type: EventType):
    """获取事件类型的处理函数（仅保留 4 种）。"""
    return {
        EventType.PROJECT: _handle_project,
        EventType.DIRECT_TASK: _handle_action_event,
        EventType.LITERATURE_REVIEW: _handle_action_event,
        EventType.DATA_FETCH: _handle_action_event,
        EventType.DATA_ANALYSIS: _handle_action_event,
        EventType.VISUALIZATION: _handle_action_event,
        EventType.EVIDENCE_AUDIT: _handle_action_event,
        EventType.ARTIFACT_BUILD: _handle_action_event,
        EventType.PDF_REPORT: _handle_action_event,
        EventType.EMAIL_DELIVERY: _handle_email_delivery,
        EventType.WEB_SEARCH: _handle_action_event,
        EventType.WEB_CAPTURE: _handle_action_event,
        EventType.PROJECT_THINK: _handle_action_event,
        EventType.OBJECTIVE_REVIEW: _handle_action_event,
        EventType.CURIOSITY_EXPLORE: _handle_action_event,
        EventType.HABIT_UPDATE: _handle_action_event,
        EventType.STOP_PROJECT: _handle_stop_project,
        EventType.CRON_TICK: _handle_cron_tick,
        EventType.REPORT: _handle_report,
        EventType.WAKE_UP: _handle_wake_up,
        EventType.REFLECTION: _handle_reflection,
        EventType.CROSS_PROJECT: _handle_cross_project,
        EventType.MEMORY_CONSOLIDATE: _handle_memory_consolidate,
        EventType.CONTENT_DIGEST: _handle_content_digest,
        EventType.CONTENT_PATROL: _handle_content_patrol,
    }.get(event_type)


# ── ACTION EVENTS ───────────────────────────────────────────────────


_ACTION_EVENT_SPECS = {
    EventType.DIRECT_TASK: {
        "name": "直接交付",
        "artifact": "direct_task_result.md",
        "rules": [
            "只完成用户点名的最小交付，不要扩展成长期项目。",
            "本 event 只能做一个动作：例如只验证环境、只生成一个文件、只读取一个输入、只发送一封邮件；不要同时安装依赖、取数、分析、绘图和写报告。",
            "如果用户要 Excel/CSV/文档/图片，必须生成真实文件并在 FILES 写路径。",
            "不要生成阶段报告、PPT/PDF 或项目路线图，除非用户明确要求。",
        ],
    },
    EventType.LITERATURE_REVIEW: {
        "name": "资料/文献整理",
        "artifact": "literature_review.md",
        "rules": [
            "围绕用户原始问题查资料、读摘要/元数据/可得正文，并形成可读简报。",
            "禁止自动进入实验、建模、调参或生成模拟数据。",
            "输出应包含代表性文献/资料、方法路线、证据强弱和可参考边界。",
        ],
    },
    EventType.DATA_FETCH: {
        "name": "数据获取",
        "artifact": "data_fetch_result.md",
        "rules": [
            "只获取、下载、读取或保存一个真实数据源，不做统计分析、绘图或报告。",
            "必须记录数据来源、访问方式、时间范围/查询参数和保存路径。",
            "如果数据源不可访问、需要账号/API key/权限或返回空数据，直接报错并说明证据，不编造替代数据。",
            "FILES 必须写真实保存的数据文件路径；没有文件则写 EMPTY 并说明阻塞。",
        ],
    },
    EventType.DATA_ANALYSIS: {
        "name": "数据分析",
        "artifact": "data_analysis_result.md",
        "rules": [
            "只做一个最小可验证分析动作：读取已有数据、统计、跑一个分析脚本或检查数据质量。",
            "不要在本 event 里获取外部数据、绘图或写报告；这些分别由 data_fetch、visualization、pdf_report 处理。",
            "必须说明输入数据来源和输出文件；没有真实数据时不要编造结果。",
            "不要自动写阶段 PPT/PDF，除非用户明确要求。",
        ],
    },
    EventType.VISUALIZATION: {
        "name": "可视化",
        "artifact": "visualization_result.md",
        "rules": [
            "只基于已有数据、分析结果或用户给定内容生成图表/图片。",
            "必须保存真实图片文件并在 FILES 写路径。",
            "图中标题、坐标轴、节点、图例和注释默认使用英文；报告正文仍按用户语言书写。",
            "不要在本 event 里重新取数、写完整报告或发送邮件；下一步需要报告时指向 pdf_report。",
        ],
    },
    EventType.EVIDENCE_AUDIT: {
        "name": "证据审计",
        "artifact": "evidence_audit.md",
        "rules": [
            "先检查证据真实性、数据泄露、过拟合、引用可靠性或结论边界。",
            "审计未通过前禁止继续调参、宣称最佳结果或包装成果。",
            "必须区分 verified / inferred / hypothesis。",
        ],
    },
    EventType.ARTIFACT_BUILD: {
        "name": "产物构建",
        "artifact": "artifact_build_result.md",
        "rules": [
            "构建用户可看的交付物：代码、表格、PPT 或整理文件。",
            "必须生成真实文件并在 FILES 写路径。",
            "如果需要 PDF 报告，应在 NEXT 中建议调用 pdf_report，不要在本 event 内顺手生成 PDF。",
            "内容质量优先于文件名和路径，不要用占位产物糊弄。",
        ],
    },
    EventType.PDF_REPORT: {
        "name": "PDF报告",
        "artifact": "pdf_report_source.md",
        "rules": [
            "把已有 event 结果、摘要、图表说明或用户要求整理成 PDF 报告正文。",
            "不要调用工具、不要写脚本、不要自己生成 .pdf；执行器会把 ARTIFACT_CONTENT 统一转换成真实 PDF 并交付。",
            "ARTIFACT_CONTENT 必须是完整 Markdown 报告正文，不能写“已写入/已生成/内容涵盖/上方 write_file”这类执行元说明。",
            "如果报告围绕已有图片、图表或可视化产物，ARTIFACT_CONTENT 必须包含可解析的 Markdown 图片引用，优先使用绝对路径。",
            "FILES 写 EMPTY；最终 PDF 路径由执行器补入。",
            "不要重新执行研究/实验/建模；只做报告整理、排版和交付。",
            "pdf_report 已经是最终 PDF 交付 event，NEXT 不要再要求 artifact_build 做 Markdown 转 PDF，除非明确发现 PDF 生成失败。",
        ],
    },
    EventType.EMAIL_DELIVERY: {
        "name": "邮件交付",
        "artifact": "email_delivery_result.md",
        "rules": [
            "只负责把已有文件或本轮明确生成的文件发送到用户邮箱，不重新分析数据、不重做项目。",
            "必须先解析收件邮箱，再解析要发送的文件线索；找不到文件就直接报错并说明需要哪个文件。",
            "缺少 SMTP 配置时直接说明缺少配置，不要改成数据分析、项目推进或其它兜底任务。",
        ],
    },
    EventType.WEB_SEARCH: {
        "name": "网络搜索",
        "artifact": "web_search_result.md",
        "rules": [
            "围绕用户问题搜索公开网页、搜索引擎、B站、小红书、知乎、博客、论文库或公开数据库等可访问来源，并整理来源链接、平台、标题和可信度边界。",
            "按目标选择来源：人物/机构优先公开网页和平台主页；B站优先账号、视频、动态、评论 API 或公开页面；小红书优先公开可访问链接/搜索页，登录受限时记录限制；文献优先 DOI/arXiv/PubMed/Crossref/Semantic Scholar/Google Scholar 可见元数据；数据库优先官方 API、下载页、schema、版本和许可。",
            "搜索结果必须保留可追溯证据：URL、发布时间/更新时间、作者/账号、平台、访问状态；不能只写模型常识。",
            "如果平台正文、评论、图片或视频需要登录/权限，不绕过限制、不编造正文；记录可见线索和不可访问原因，并给出可继续访问所需材料。",
            "如果用户要转发视频、图片或附件，优先给公开链接；如果能合法下载公开文件，必须保存到项目目录、workspace/user 或 workspace/deliverables 下，并在 FILES 写真实本地路径。",
            "不要把搜索结果包装成已证实事实；区分 verified / source-claimed / inferred。",
        ],
    },
    EventType.WEB_CAPTURE: {
        "name": "网页/图片捕获",
        "artifact": "web_capture_result.md",
        "rules": [
            "只做一个捕获动作：下载一个公开图片/附件，或对一个公开网页截图。",
            "必须保存真实本地文件并在 FILES 写路径；不能只给链接或描述。",
            "必须记录来源 URL、访问状态和捕获方式；登录/权限/反爬受限就直接报错，不绕过限制。",
            "如果捕获结果用于报告，NEXT 应指向 pdf_report 或后续整理 event。",
        ],
    },
    EventType.PROJECT_THINK: {
        "name": "项目思考",
        "artifact": "project_think.md",
        "rules": [
            "只做项目起步、目标拆解、关键难点、最小路线或下一步选择。",
            "如果来自 action 失败恢复，必须把大目标拆成可单独验证的 event 链；本 event 不直接取数、绘图或写报告。",
            "拆解结果必须写成 event 链，每个 event 只有一个动作和一个验收标准；不要把“安装依赖+取数+验证”写成同一个下一步。",
            "不要机械进入文献、实验、PPT 全流程；下一步只提出一个最小动作。",
            "应把用户要求、假设、风险和验收标准区分清楚。",
        ],
    },
    EventType.OBJECTIVE_REVIEW: {
        "name": "目标对齐",
        "artifact": "objective_review.md",
        "rules": [
            "只回看根目标、当前上下文、本轮结果和已有文件，判断原始目标是否真正完成。",
            "必须列出 completed / missing / blockers / next_event 建议；不能直接执行取数、绘图、写报告、发邮件或搜索。",
            "如果用户原始目标包含多个交付物，必须逐项核对，不要因为某一个子步骤完成就停止。",
            "如果上一轮 selector 超时、bad_json、选错 event、或 active chain 为空，本 event 负责重新对齐下一步。",
            "next_event 只能建议一个最小 event，并写明验收标准；如果可以停止，必须说明所有验收项已经满足。",
        ],
    },
    EventType.CURIOSITY_EXPLORE: {
        "name": "好奇探索",
        "artifact": "curiosity_explore.md",
        "rules": [
            "从上一轮 NEXT 中选择一个最小、可验证、能产生新信息的动作。",
            "探索动作可以是资料、推理、实验、建模、验证、原型、对比或其它能产生新信息的方式；不要只复述上一轮内容。",
            "必须说明探索问题、方法、得到的新证据/观察，以及下一步是否仍值得继续。",
            "如果缺少必要数据/环境/权限，先做无阻塞替代：搭建最小公式、伪代码、公开例子或验证计划。",
        ],
    },
    EventType.HABIT_UPDATE: {
        "name": "习惯/成长更新",
        "artifact": "habit_update.md",
        "rules": [
            "把用户经验、失败教训或新行为边界抽象成可复用习惯。",
            "不要把具体项目案例长期塞进 prompt；保留抽象行为改变。",
            "必须写明触发、学到什么、以后怎么改变。",
        ],
    },
}


def _action_event_spec(event_type: EventType) -> dict:
    return _ACTION_EVENT_SPECS.get(event_type, _ACTION_EVENT_SPECS[EventType.DIRECT_TASK])


def _extract_first_url(text: str) -> str:
    match = re.search(r"https?://[^\s<>'\"，。；;）)]+", text or "", re.I)
    return match.group(0).strip() if match else ""


def _safe_capture_name(url: str, suffix: str) -> str:
    base = re.sub(r"^https?://", "", url or "", flags=re.I)
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_")[:80] or "web_capture"
    if not suffix.startswith("."):
        suffix = "." + suffix
    return base + suffix


def _run_web_capture(event: MindEvent, title: str, project_dir: str) -> dict | None:
    if event.type != EventType.WEB_CAPTURE:
        return None
    payload = event.payload or {}
    source = "\n".join(
        str(payload.get(k) or "")
        for k in ("user_request", "root_user_request", "parent_user_request", "event_kind")
    )
    url = _extract_first_url(source)
    if not url:
        return {
            "action": "web_capture",
            "step_done": "未找到可捕获的公开 URL",
            "findings": ["web_capture 需要一个明确 URL", "没有下载或截图任何文件"],
            "evidence": "EMPTY",
            "next_action": "调用 stop_project，等待用户提供公开 URL 或先调用 web_search 找来源。",
            "state_delta": "web_capture blocked: missing URL",
            "files": "EMPTY",
            "artifact_content": "# Web capture blocked\n\nNo URL was provided.\n",
        }
    os.makedirs(project_dir, exist_ok=True)
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=25) as resp:
            content_type = str(resp.headers.get("content-type") or "").lower()
            data = resp.read(12_000_000)
        url_path = re.sub(r"[?#].*$", "", url)
        ext = os.path.splitext(url_path)[1].lower()
        if content_type.startswith("image/") or ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
            if not ext or len(ext) > 6:
                ext = mimetypes.guess_extension(content_type.split(";", 1)[0]) or ".png"
            path = os.path.join(project_dir, _safe_capture_name(url, ext))
            with open(path, "wb") as f:
                f.write(data)
            return {
                "action": "web_capture",
                "step_done": "已下载公开图片并保存为本地文件",
                "findings": [f"来源 URL 可访问，content-type={content_type or 'unknown'}", f"文件大小 {len(data)} bytes"],
                "evidence": f"url={url}; file={path}",
                "next_action": "如果该图片用于报告，调用 pdf_report 将图片嵌入最终报告。",
                "state_delta": f"web image captured: {path}",
                "files": path,
                "artifact_content": f"# Web image capture\n\n- URL: {url}\n- File: {path}\n- Content-Type: {content_type}\n",
            }
    except Exception as exc:
        # If the direct request fails, a browser screenshot may still work.
        direct_error = f"{type(exc).__name__}: {exc}"
    else:
        direct_error = "not_an_image"

    screenshot_path = os.path.join(project_dir, _safe_capture_name(url, ".png"))
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1365, "height": 900}, device_scale_factor=1)
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.screenshot(path=screenshot_path, full_page=True)
            browser.close()
        if os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 1000:
            return {
                "action": "web_capture",
                "step_done": "已对公开网页截图并保存为本地 PNG 文件",
                "findings": [f"网页截图成功", f"直接下载路径未作为图片使用：{direct_error}"],
                "evidence": f"url={url}; file={screenshot_path}",
                "next_action": "如果该截图用于报告，调用 pdf_report 将截图嵌入最终报告。",
                "state_delta": f"web page screenshot captured: {screenshot_path}",
                "files": screenshot_path,
                "artifact_content": f"# Web page screenshot\n\n- URL: {url}\n- Screenshot: {screenshot_path}\n",
            }
    except Exception as exc:
        return {
            "action": "web_capture",
            "step_done": "网页/图片捕获失败",
            "findings": [
                f"直接下载失败或不是图片：{direct_error}",
                f"浏览器截图失败：{type(exc).__name__}: {_clip(str(exc), 160)}",
            ],
            "evidence": f"url={url}",
            "next_action": "调用 stop_project，等待可访问 URL、安装浏览器截图依赖，或改用其它公开图片来源。",
            "state_delta": f"web_capture failed for {url}",
            "files": "EMPTY",
            "artifact_content": f"# Web capture failed\n\nURL: {url}\n\nDirect: {direct_error}\n\nScreenshot: {type(exc).__name__}: {exc}\n",
        }
    return None


def _curiosity_depth(payload: dict | None) -> int:
    try:
        return max(0, int((payload or {}).get("curiosity_depth") or 0))
    except Exception:
        return 0


def _root_user_request(payload: dict | None) -> str:
    payload = payload or {}
    for key in ("root_user_request", "parent_user_request", "original_user_request", "user_request"):
        val = str(payload.get(key) or "").strip()
        if val:
            return val
    return ""


def _fallback_followup_after_selector_failure(event: MindEvent, title: str, parsed: dict,
                                              payload: dict, reason: str) -> dict:
    user_request = str(payload.get("user_request") or title).strip()
    root_request = _root_user_request(payload) or user_request
    event_kind = str(payload.get("event_kind") or event.type.value).strip()
    next_action = str(parsed.get("next_action") or payload.get("previous_next_action") or "").strip()
    return {
        "continue": True,
        "event_type": EventType.OBJECTIVE_REVIEW.value,
        "event_kind": re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", f"{event_kind}_recover").strip("_")[:80] or "objective_recover",
        "objective": (
            "回看根目标、本轮结果、上下文和已有文件，重新判断当前执行链缺什么、"
            "下一步应该调用哪个最小 event，或是否可以停止。"
            "本 event 不执行具体任务，只做目标/交付物对齐。"
            f"\n根目标：{root_request[:1200]}"
            f"\n失败原因：selector_{reason}"
            f"\n上一轮 event：{event.type.value}/{event_kind}"
            f"\n上一轮 NEXT：{next_action[:700]}"
        ),
        "question": "selector 失败后，当前根目标还缺什么，下一步应该调用哪个 event？",
        "reason": f"selector_{reason}_objective_review",
    }


def _followup_event_decision_with_llm(event: MindEvent, title: str, parsed: dict, payload: dict) -> dict:
    """Ask the model selector whether a completed event should enqueue another event."""
    user_request = str(payload.get("user_request") or title).strip()
    root_request = _root_user_request(payload)
    parent_request = str(payload.get("parent_user_request") or "").strip()
    event_kind = str(payload.get("event_kind") or event.type.value).strip()
    next_action = str(parsed.get("next_action") or "").strip()
    depth = _curiosity_depth(payload)
    max_depth = max(0, int(os.getenv("PARTNER_FOLLOWUP_MAX_DEPTH", os.getenv("PARTNER_CURIOSITY_MAX_DEPTH", "6")) or "6"))
    if depth >= max_depth:
        return {"continue": False, "reason": "max_depth"}
    if not _adapter:
        return {"continue": False, "reason": "no_llm"}
    habits = ensure_habits(_workspace)
    habit_lines = [str(x) for x in (habits.get("habits") or [])[:8]]
    habit_block = "\n".join(f"- {line}" for line in habit_lines) if habit_lines else "- 暂无共享习惯。"
    growth_rows = get_recent_growth_events(_workspace, project=title, limit=4)
    if len(growth_rows) < 4:
        seen = {
            (
                str(row.get("time") or ""),
                str(row.get("project") or ""),
                str(row.get("behavior_change") or row.get("learned") or ""),
            )
            for row in growth_rows
        }
        for row in get_recent_growth_events(_workspace, project="", limit=4):
            key = (
                str(row.get("time") or ""),
                str(row.get("project") or ""),
                str(row.get("behavior_change") or row.get("learned") or ""),
            )
            if key in seen:
                continue
            growth_rows.append(row)
            seen.add(key)
            if len(growth_rows) >= 4:
                break
    growth_lines = []
    for row in growth_rows[:4]:
        changed = str(row.get("behavior_change") or row.get("learned") or "").strip()
        trigger = str(row.get("trigger") or "").strip()
        if changed:
            if trigger:
                growth_lines.append(f"- 因为“{_clip(trigger, 70)}”，以后要：{_clip(changed, 130)}")
            else:
                growth_lines.append(f"- {_clip(changed, 150)}")
    growth_block = "\n".join(growth_lines) if growth_lines else "- 暂无近期成长事件。"
    prompt = f"""你是 Partner 的 mind follow-up selector。判断一个已完成 event 是否应继续入队另一个 event。

可选 event：
- none: 当前目标已经达到，或需要等用户新指令/缺失输入。
- direct_task: 下一步是一次性直接交付。
- literature_review: 下一步是资料/文献/方法依据整理。
- data_fetch: 下一步是获取、下载或保存一个真实数据源。
- data_analysis: 下一步是读取已有数据、统计、质量检查或最小分析。
- visualization: 下一步是基于已有数据/结果绘制图表。
- evidence_audit: 下一步是证据真实性、泄露、过拟合或可靠性审计。
- artifact_build: 下一步是生成用户可看的代码、PPT、图表、表格等交付物。
- pdf_report: 下一步是把已有结果整理成 PDF 报告并交付。
- email_delivery: 下一步是把已有/本轮生成文件发送到邮箱，不重新分析数据。
- web_search: 下一步是搜索公开网页、小红书、B站、知乎等外部公开内容，整理链接、来源和可信度边界。
- web_capture: 下一步是下载一个公开图片/文件，或对公开网页截图并保存为本地图片，用于报告、转发或证据留存。
- project_think: 下一步是目标拆解、路线选择、风险识别。
- objective_review: 下一步是回看根目标、已完成内容、缺口、阻塞和下一 event；用于防止执行链跑偏、selector 失败后恢复、或停止前验收目标。
- curiosity_explore: 下一步来自 Partner 的好奇心，要产生新问题、新假设、新探索动作或新的验证线索。
- habit_update: 下一步是沉淀抽象习惯/经验。
- stop_project: 停止当前 project 执行链，保存为 waiting；只有明确判断当前目标已完成、或应等待用户/资源/缺失输入时才选择。

选择原则：
- 不要用关键词或任务类别硬编码判断，基于用户目标、本轮结果和 NEXT 判断。
- 参考 Partner 共享习惯和近期成长事件；习惯是倾向，不是绝对规则，用户明确要求优先。
- 如果用户目标已经达到，或应该等待用户/资源/缺失输入，选择 stop_project。
- none 只用于 selector 无法判断或不应改变队列；不要用 none 来停止一个正在执行的 project。
- 如果 NEXT 只是泛泛“继续/等待/看用户是否需要”，选择 stop_project。
- 如果当前 event 只是父目标/根目标中的一个子步骤，即使本轮 NEXT 写了“已完成/等待用户查看”，也要回到根目标检查是否还有未交付内容；未交付时选择下一个最小 event，不要停止。
- 如果根目标包含多个交付物或连续阶段，按“一个 event 只做一个可验证动作”的方式继续入队，直到根目标交付完成或遇到真实缺失输入/资源。
- 如果本轮结果、NEXT、历史上下文和根目标之间明显不一致，或你不确定是否已经满足用户原始目标，选择 objective_review，不要直接 stop_project。
- 禁止把多个动作塞进一个 objective，例如“安装依赖并下载数据并验证并绘图并生成报告”。这类必须选择 project_think 重新拆成 event 链。
- 执行型 event 的 objective 只能有一个动作、一个验收标准；如果无法写成一个动作，选择 project_think。
- 如果用户目标、FILES 或 NEXT 需要 PDF 报告，且已有可整理内容，选择 pdf_report；不要让 artifact_build 或其它 event 附带生成 PDF。
- 如果 NEXT 已明确写出 pdf_report/PDF 报告/报告并交付，且当前已有数据、图表、摘要或文件证据，必须选择 pdf_report，不要再选择 project_think。
- 如果下一步需要真实网页截图、股市页面截图、公开视频封面、公开图片或报告配图，选择 web_capture；web_search 只负责找来源，web_capture 负责保存真实图片文件。
- pdf_report 已经会生成并交付真实 PDF；不要因为“Markdown 转 PDF”再选择 artifact_build，除非明确有 PDF 生成失败证据。
- 如果继续能更好地完成用户原始目标，或本轮自然产生了值得探索的新问题，选择一个最合适 event。
- curiosity_explore 不是特殊通道，只有当它比 data_analysis/literature_review/evidence_audit/project_think 等更贴合时才选它。

Partner 共享习惯：
{habit_block}

近期成长事件：
{growth_block}

当前 event：{event.type.value}/{event_kind}
当前 follow-up 深度：{depth}
根目标/原始用户请求：
{(root_request or user_request)[:1200]}

当前子 event 请求：
{user_request[:1200]}

父级请求/上一阶段目标：
{parent_request[:1200] if parent_request else "EMPTY"}

本轮结构化结果：
DONE: {str(parsed.get('step_done') or '')[:600]}
FINDINGS: {json.dumps(parsed.get('findings') or [], ensure_ascii=False)[:900]}
EVIDENCE: {str(parsed.get('evidence') or '')[:600]}
NEXT: {next_action[:700]}

只输出 JSON：
{{
  "event_type": "none|direct_task|literature_review|data_fetch|data_analysis|visualization|evidence_audit|artifact_build|pdf_report|email_delivery|web_search|web_capture|project_think|objective_review|curiosity_explore|habit_update|stop_project",
  "event_kind": "自由短标签",
  "objective": "如果 event_type 不是 none，写一个具体、可执行、最小的下一步目标",
  "question": "这一步想弄清楚/验证/交付什么",
  "reason": "为什么选择这个 event；如果 none，说明为什么停止"
}}
"""
    try:
        raw = (_adapter.chat(prompt, purpose="classify") or "").strip()
    except Exception as exc:
        logger.debug(f"[FOLLOWUP] LLM selector failed: {exc}")
        return _fallback_followup_after_selector_failure(event, title, parsed, payload, "llm_failed")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return _fallback_followup_after_selector_failure(event, title, parsed, payload, "bad_json")
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return _fallback_followup_after_selector_failure(event, title, parsed, payload, "bad_json")
    if not isinstance(data, dict):
        return _fallback_followup_after_selector_failure(event, title, parsed, payload, "bad_json")
    selected = str(data.get("event_type") or "none").strip().lower()
    if selected in {"", "none"}:
        return {"continue": False, "reason": str(data.get("reason") or "selector_none")}
    if selected == EventType.STOP_PROJECT.value:
        return {
            "continue": True,
            "event_type": selected,
            "event_kind": re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", str(data.get("event_kind") or "stop_project")).strip("_")[:80] or "stop_project",
            "objective": str(data.get("objective") or "停止当前执行链并等待用户继续").strip()[:1800] or "停止当前执行链并等待用户继续",
            "question": str(data.get("question") or "").strip()[:600],
            "reason": str(data.get("reason") or "").strip()[:600],
        }
    allowed = {
        EventType.DIRECT_TASK.value,
        EventType.LITERATURE_REVIEW.value,
        EventType.DATA_FETCH.value,
        EventType.DATA_ANALYSIS.value,
        EventType.VISUALIZATION.value,
        EventType.EVIDENCE_AUDIT.value,
        EventType.ARTIFACT_BUILD.value,
        EventType.PDF_REPORT.value,
        EventType.EMAIL_DELIVERY.value,
        EventType.WEB_SEARCH.value,
        EventType.WEB_CAPTURE.value,
        EventType.PROJECT_THINK.value,
        EventType.OBJECTIVE_REVIEW.value,
        EventType.CURIOSITY_EXPLORE.value,
        EventType.HABIT_UPDATE.value,
        EventType.STOP_PROJECT.value,
    }
    if selected not in allowed:
        return {"continue": False, "reason": f"unsupported_event:{selected}"}
    objective = str(data.get("objective") or "").strip()
    if not objective:
        return {"continue": False, "reason": "empty_objective"}
    event_kind = re.sub(
        r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+",
        "_",
        str(data.get("event_kind") or selected),
    ).strip("_")[:80] or selected
    return {
        "continue": True,
        "event_type": selected,
        "event_kind": event_kind,
        "objective": objective[:1800],
        "question": str(data.get("question") or "").strip()[:600],
        "reason": str(data.get("reason") or "").strip()[:600],
    }


async def _maybe_enqueue_followup_event(event: MindEvent, title: str, parsed: dict, payload: dict) -> dict:
    decision = _followup_event_decision_with_llm(event, title, parsed, payload)
    if not decision.get("continue"):
        logger.info(f"[FOLLOWUP] no follow-up for {title}: {decision.get('reason')}")
        return {
            "queued": False,
            "event_type": "",
            "event_kind": "",
            "reason": str(decision.get("reason") or ""),
        }
    pool = await ensure_pool()
    depth = _curiosity_depth(payload) + 1
    selected_type = EventType(decision["event_type"])
    user_request = str(payload.get("user_request") or title).strip()
    root_request = _root_user_request(payload) or user_request
    await pool.put(MindEvent(
        type=selected_type,
        priority=max(2, min(8, int(payload.get("priority") or 4) + 1)),
        payload={
            "title": title,
            "step": int(payload.get("step") or 0) + 1,
            "delivery_mode": "research_project",
            "user_request": decision["objective"],
            "root_user_request": (root_request or user_request)[:1800],
            "event_type": selected_type.value,
            "event_kind": decision["event_kind"],
            "stop_after_completion": True,
            "curiosity_depth": depth,
            "parent_user_request": str(payload.get("user_request") or "")[:1600],
            "followup_question": decision.get("question", ""),
            "followup_reason": decision.get("reason", ""),
            "previous_next_action": str(parsed.get("next_action") or "")[:900],
        },
        source=f"{event.type.value}:selector_followup",
        parent_id=event.id,
    ))
    if selected_type != EventType.STOP_PROJECT:
        try:
            from ..project_state import set_project_status
            set_project_status(_workspace, title, "active", f"selector follow-up：{selected_type.value}/{decision['event_kind']}")
        except Exception as exc:
            logger.debug(f"[FOLLOWUP] failed to mark active: {exc}")
    logger.info(f"[FOLLOWUP] queued {selected_type.value} for {title}: {decision['event_kind']} depth={depth}")
    return {
        "queued": True,
        "event_type": selected_type.value,
        "event_kind": decision["event_kind"],
        "reason": decision.get("reason", ""),
    }


async def _enqueue_stop_project_event(event: MindEvent, title: str, reason: str, payload: dict | None = None) -> bool:
    pool = await ensure_pool()
    payload = payload or {}
    await pool.put(MindEvent(
        type=EventType.STOP_PROJECT,
        priority=max(2, min(8, int(payload.get("priority") or 4) + 1)),
        payload={
            "title": title,
            "step": int(payload.get("step") or 0) + 1,
            "event_kind": "selector_stop_project",
            "reason": reason or "selector chose to stop",
            "user_request": str(payload.get("user_request") or "")[:1600],
            "previous_event_type": event.type.value,
            "previous_event_kind": str(payload.get("event_kind") or "")[:120],
        },
        source=f"{event.type.value}:selector_stop_project",
        parent_id=event.id,
    ))
    logger.info(f"[STOP_PROJECT] queued explicit stop for {title}: {reason}")
    return True


def _build_action_event_prompt(event: MindEvent, title: str, state_md: str, artifact_path: str) -> str:
    spec = _action_event_spec(event.type)
    payload = event.payload or {}
    user_request = _clip(str(payload.get("user_request") or title), 1400)
    root_request = _clip(_root_user_request(payload), 1400)
    parent_request = _clip(str(payload.get("parent_user_request") or ""), 1200)
    event_kind = str(payload.get("event_kind") or event.type.value).strip()
    source_files_block = ""
    source_files = payload.get("source_files") or []
    if isinstance(source_files, str):
        source_files = [source_files]
    if isinstance(source_files, list):
        chunks: list[str] = []
        for raw_path in source_files[:5]:
            path = str(raw_path or "").strip()
            if not path or not os.path.exists(path) or not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(6000)
            except Exception:
                continue
            if text.strip():
                chunks.append(f"--- {path} ---\n{_clip(text, 6000)}")
        if chunks:
            source_files_block = "可用来源文件摘录（已由执行器读取，禁止再调用工具读取）：\n" + "\n\n".join(chunks) + "\n\n"
    curiosity_block = ""
    if event.type == EventType.CURIOSITY_EXPLORE:
        curiosity_block = (
            "\n好奇探索上下文："
            f"\n- 想弄清楚的问题：{_clip(str(payload.get('followup_question') or payload.get('curiosity_question') or ''), 600)}"
            f"\n- 为什么值得继续：{_clip(str(payload.get('followup_reason') or payload.get('curiosity_reason') or ''), 600)}"
            f"\n- 上一轮 NEXT：{_clip(str(payload.get('previous_next_action') or ''), 700)}"
            f"\n- 原始用户问题：{_clip(str(payload.get('parent_user_request') or ''), 900)}\n"
        )
    habits = ensure_habits(_workspace)
    habit_lines = [str(x) for x in (habits.get("habits") or [])[:6]]
    habit_block = "\n".join(f"- {line}" for line in habit_lines) if habit_lines else "- 先证据，后结论；先最小动作，后扩展。"
    growth_rows = get_recent_growth_events(_workspace, project=title, limit=4)
    if len(growth_rows) < 4:
        seen = {
            (
                str(row.get("time") or ""),
                str(row.get("project") or ""),
                str(row.get("behavior_change") or row.get("learned") or ""),
            )
            for row in growth_rows
        }
        for row in get_recent_growth_events(_workspace, project="", limit=4):
            key = (
                str(row.get("time") or ""),
                str(row.get("project") or ""),
                str(row.get("behavior_change") or row.get("learned") or ""),
            )
            if key in seen:
                continue
            growth_rows.append(row)
            seen.add(key)
            if len(growth_rows) >= 4:
                break
    growth_lines = []
    for row in growth_rows[:4]:
        changed = str(row.get("behavior_change") or row.get("learned") or "").strip()
        trigger = str(row.get("trigger") or "").strip()
        if changed:
            if trigger:
                growth_lines.append(f"- 因为“{_clip(trigger, 70)}”，以后要：{_clip(changed, 130)}")
            else:
                growth_lines.append(f"- {_clip(changed, 150)}")
    growth_block = "\n".join(growth_lines) if growth_lines else "- 暂无近期成长事件；按共享习惯和当前证据执行。"
    rules = "\n".join(f"- {line}" for line in spec.get("rules", []))
    external_access_rule = (
        "本事件是 web_search：允许使用可用的 web/搜索/外部内容工具访问公开 HTTPS 来源；"
        "优先调用 `python -m partner.content_tools acquire <url> --dest <项目目录或workspace/deliverables子目录> --json` 获取完整正文、图片、视频元数据和可交付文件；"
        "如需下载公开视频且已配置权限，可加 --download-video --keyframes；"
        "禁止绕过登录、付费墙、验证码或平台权限；禁止用 curl|bash / curl|python 这类不透明管道。"
        if event.type == EventType.WEB_SEARCH
        else "默认优先使用本地内容；如需联网，必须是用户目标或本 event 边界确实需要的公开 HTTPS 来源。"
    )
    hierarchy_block = ""
    if root_request and root_request != user_request:
        hierarchy_block = (
            f"根目标/原始用户请求：{root_request}\n"
            f"父级请求/上一阶段目标：{parent_request or 'EMPTY'}\n"
            "本轮是根目标中的一个小 event。DONE 只写本轮实际完成内容；"
            "NEXT 必须回到根目标判断是否还有未交付内容，不能因为本子步骤完成就默认等待用户。\n"
        )
    next_rule = (
        "NEXT: <一个最小下一步；若根目标还有未交付内容，必须写下一步 event 目标；只有根目标已完成或缺少真实输入/资源时才写等待用户>\n"
        if hierarchy_block else
        "NEXT: <一个最小下一步；若本次直接交付已完成，写 已完成，等待用户查看/继续>\n"
    )
    objective_review_block = (
        "objective_review 专用要求：\n"
        "- ACTION 写 objective_review。\n"
        "- DONE 写本轮完成了目标/交付物对齐，不要写已执行具体任务。\n"
        "- FINDINGS 必须包含：completed=<已满足项>; missing=<缺口>; blockers=<阻塞或 EMPTY>。\n"
        "- NEXT 必须写建议的下一个 event_type/event_kind/objective，或明确写 stop_project 及停止理由。\n"
        "- FILES 写 EMPTY；ARTIFACT_CONTENT 写本轮对齐记录，便于之后追踪 selector 为什么这样选。\n\n"
        if event.type == EventType.OBJECTIVE_REVIEW else ""
    )
    return (
        "你是 Partner 的动作级执行器。本轮只执行一个小 event，不进入固定项目流水线。\n"
        f"event_type：{event.type.value}\n"
        f"event_name：{spec.get('name')}\n"
        f"event_kind：{event_kind}\n"
        f"任务/项目名：{title}\n"
        f"用户原始请求：{user_request}\n"
        f"{hierarchy_block}"
        f"{curiosity_block}"
        f"{source_files_block}"
        f"当前状态摘要：\n{_compact_state_snapshot(state_md)}\n\n"
        f"本事件边界：\n{rules}\n\n"
        f"Partner 共享习惯（抽象习惯，不是固定流程）：\n{habit_block}\n\n"
        f"近期成长事件（会影响本轮判断，但不能覆盖用户明确要求）：\n{growth_block}\n\n"
        "通用交付约束：如果用户要求具体格式文件，最终必须有该格式真实文件；"
        "如果缺少地点、文件路径、数据范围等关键参数，不要擅自补全，先说明缺失并停止。\n\n"
        "小 event 约束：本轮只能完成一个可验证动作。禁止在一个 event 内同时完成安装依赖、取数、分析、绘图、写报告、发邮件等多个阶段；"
        "如果用户根目标需要多个阶段，DONE 只写本轮动作，NEXT 写下一个最小 event 目标。\n\n"
        f"{objective_review_block}"
        f"外部访问约束：{external_access_rule}\n\n"
        "必须按以下结构输出，不要 markdown 包裹：\n"
        "ACTION: <动作类型>\n"
        "DONE: <一句话说明实际完成了什么>\n"
        "FINDINGS: <1-3 条关键结果；没有写 EMPTY>\n"
        "FINDINGS 必须写用户真正关心的可见结果和证据线索，不要只写“成功搜索/提取/生成/整理”这类执行元信息。\n"
        "EVIDENCE: <证据来源/文件/链接；没有写 hypothesis>\n"
        f"{next_rule}"
        "STATE_DELTA: <需要写入状态的简短增量>\n"
        "FILES: <真实生成/修改文件路径；没有写 EMPTY>\n"
        f"ARTIFACT_CONTENT:\n<写入 {os.path.basename(artifact_path)} 的内容；可以是简报、审计、总结或交付说明>\n"
    )


def _load_email_config() -> dict:
    candidates = [
        os.path.join(_workspace, "00_config", "email_config.json"),
        os.path.join(_workspace, "email_config.json"),
        os.path.join(_workspace, "00_config", "partner_config.json"),
        os.path.join(_workspace, "partner_config.json"),
    ]
    cfg: dict = {}
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if "email" in data and isinstance(data["email"], dict):
            cfg.update(data["email"])
        if "smtp" in data and isinstance(data["smtp"], dict):
            cfg.update(data["smtp"])
        if any(k in data for k in ("smtp_host", "host", "username", "password", "from_email")):
            cfg.update(data)
    env_map = {
        "smtp_host": "PARTNER_SMTP_HOST",
        "smtp_port": "PARTNER_SMTP_PORT",
        "username": "PARTNER_SMTP_USERNAME",
        "password": "PARTNER_SMTP_PASSWORD",
        "from_email": "PARTNER_SMTP_FROM",
    }
    for key, env_name in env_map.items():
        val = os.getenv(env_name)
        if val:
            cfg[key] = val
    if os.getenv("PARTNER_SMTP_USE_SSL"):
        cfg["use_ssl"] = os.getenv("PARTNER_SMTP_USE_SSL", "").strip().lower() not in {"0", "false", "no"}
    if os.getenv("PARTNER_SMTP_STARTTLS"):
        cfg["starttls"] = os.getenv("PARTNER_SMTP_STARTTLS", "").strip().lower() in {"1", "true", "yes"}
    return cfg


def _infer_smtp_host(email_addr: str) -> tuple[str, int, bool]:
    domain = (email_addr.rsplit("@", 1)[-1] if "@" in email_addr else "").lower()
    if domain in {"qq.com", "vip.qq.com", "foxmail.com"}:
        return "smtp.qq.com", 465, True
    if domain in {"163.com"}:
        return "smtp.163.com", 465, True
    if domain in {"126.com"}:
        return "smtp.126.com", 465, True
    if domain in {"gmail.com"}:
        return "smtp.gmail.com", 465, True
    if domain in {"outlook.com", "hotmail.com", "live.com"}:
        return "smtp.office365.com", 587, False
    return "", 465, True


def _extract_smtp_config_from_text(text: str) -> dict:
    """Extract a user-supplied SMTP config from a conversational reply.

    Expected user style:
      发件邮箱：xxx@qq.com
      授权码：abcdefg
    """
    text = text or ""
    emails = re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
    if not emails:
        return {}
    sender = emails[-1]
    password = ""
    patterns = [
        r"(?:SMTP\s*)?(?:授权码|授权密码|客户端授权码|应用专用密码|app password|password|密码)\s*[:：]\s*([^\s，,。；;]+)",
        r"(?:授权码|授权密码|客户端授权码|应用专用密码)\s*(?:是|为)?\s*([A-Za-z0-9_\-]{6,})",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            password = m.group(1).strip()
            break
    if not password:
        return {}
    host, port, use_ssl = _infer_smtp_host(sender)
    cfg = {
        "username": sender,
        "password": password,
        "from_email": sender,
        "use_ssl": use_ssl,
    }
    if host:
        cfg["smtp_host"] = host
        cfg["smtp_port"] = port
    return cfg


def _save_email_config_from_user(cfg: dict) -> str:
    if not cfg:
        return ""
    path = os.path.join(_workspace, "00_config", "email_config.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}
    existing.update(cfg)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


def _email_config_missing_reason(cfg: dict) -> str:
    host = cfg.get("smtp_host") or cfg.get("host")
    user = cfg.get("username") or cfg.get("user")
    password = cfg.get("password") or cfg.get("smtp_password") or cfg.get("token")
    from_email = cfg.get("from_email") or cfg.get("sender") or user
    missing = []
    if not host:
        missing.append("smtp_host")
    if not user:
        missing.append("username")
    if not password:
        missing.append("password/authorization_code")
    if not from_email:
        missing.append("from_email")
    return "、".join(missing)


def _smtp_help_text(recipient: str, files: list[str]) -> str:
    file_line = "、".join(os.path.basename(p) for p in files) if files else "要发送的附件"
    to_line = recipient or "目标收件邮箱"
    return (
        f"我已经找到了要发送的文件：{file_line}，收件邮箱是 {to_line}。\n"
        "但还不能发送，因为缺少发件邮箱的 SMTP 授权信息。\n\n"
        "SMTP 授权码不是 QQ 密码，而是邮箱给第三方程序发邮件用的专用授权码。\n"
        "如果用 QQ 邮箱作为发件邮箱，获取方式通常是：\n"
        "1. 打开 QQ 邮箱网页版。\n"
        "2. 进入 设置 -> 账号。\n"
        "3. 找到 POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务。\n"
        "4. 开启 SMTP 或 POP3/SMTP 服务。\n"
        "5. 按页面提示验证后生成授权码。\n\n"
        "然后直接发给我这两项即可：\n"
        "发件邮箱：你的QQ邮箱@qq.com\n"
        "SMTP授权码：刚生成的授权码\n\n"
        "收到后我会继续刚才的邮件发送任务。"
    )


def _extract_email_recipient(text: str) -> str:
    match = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text or "")
    return match.group(0) if match else ""


def _select_email_attachments_with_llm(request: str, candidates: list[str], limit: int = 6) -> list[str]:
    if not _adapter or not candidates:
        return []
    workspace_root = os.path.abspath(_workspace)
    rows = []
    for idx, path in enumerate(candidates[:40]):
        try:
            rel = os.path.relpath(path, workspace_root)
        except Exception:
            rel = path
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
            size = os.path.getsize(path)
        except Exception:
            mtime = ""
            size = 0
        rows.append({
            "id": idx,
            "path": path,
            "rel": rel,
            "mtime": mtime,
            "size": size,
        })
    prompt = f"""你是 Partner 的 email attachment selector。用户要发送邮件，你只从候选文件中选择应作为附件的文件。

判断原则：
- 只根据用户请求、最近文件路径/文件名/时间判断相关性。
- 不要按固定关键词模板；如果无法确定具体附件，selected_ids 输出空数组并写 reason。
- 只选择已经存在的候选文件，不编造路径。
- 如果用户要求“全部/都发”，可以选择多个；否则优先选择最相关的 1 个。

用户请求：
{request[:1200]}

候选文件：
{json.dumps(rows, ensure_ascii=False)[:5000]}

只输出 JSON：
{{"selected_ids":[],"reason":""}}
"""
    try:
        raw = (_adapter.chat(prompt, purpose="classify") or "").strip()
    except Exception as exc:
        logger.debug(f"[EMAIL] attachment selector LLM failed: {exc}")
        return []
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return []
    selected: list[str] = []
    for item in data.get("selected_ids") or []:
        try:
            idx = int(item)
        except Exception:
            continue
        if 0 <= idx < len(candidates):
            selected.append(candidates[idx])
        if len(selected) >= limit:
            break
    return selected


def _candidate_email_attachments(request: str) -> list[str]:
    text = request or ""
    explicit: list[str] = []
    for raw in re.findall(r"(?:(?:/|\.{1,2}/)[^\s，。；;]+?\.(?:xlsx|xls|csv|pdf|docx|pptx|png|jpg|jpeg|webp|txt))", text, flags=re.I):
        path = raw if os.path.isabs(raw) else os.path.join(_workspace, raw)
        if os.path.exists(path):
            explicit.append(os.path.abspath(path))
    if explicit:
        return explicit[:6]

    exts = [".xlsx", ".xls", ".csv", ".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".txt"]
    files: list[str] = []
    for ext in exts:
        files.extend(glob.glob(os.path.join(_workspace, "**", f"*{ext}"), recursive=True))
    workspace_root = os.path.abspath(_workspace)
    filtered: list[str] = []
    for path in files:
        try:
            abspath = os.path.abspath(path)
            if os.path.commonpath([workspace_root, abspath]) != workspace_root:
                continue
            rel = os.path.relpath(abspath, workspace_root)
            if rel.startswith(("system/hermes_home/", "logs/", "10_logs/", "state/")):
                continue
            if not os.path.isfile(abspath):
                continue
            filtered.append(abspath)
        except Exception:
            continue
    if not filtered:
        return []
    filtered.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    selected = _select_email_attachments_with_llm(text, filtered)
    return selected


def _send_email_with_attachments(cfg: dict, recipient: str, subject: str, body: str, files: list[str]) -> None:
    host = cfg.get("smtp_host") or cfg.get("host")
    port = int(cfg.get("smtp_port") or cfg.get("port") or (465 if cfg.get("use_ssl", True) else 587))
    username = cfg.get("username") or cfg.get("user")
    password = cfg.get("password") or cfg.get("smtp_password") or cfg.get("token")
    from_email = cfg.get("from_email") or cfg.get("sender") or username
    use_ssl = bool(cfg.get("use_ssl", port == 465))
    starttls = bool(cfg.get("starttls", not use_ssl))

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    for path in files:
        ctype, _ = mimetypes.guess_type(path)
        if not ctype:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(path))

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            if starttls:
                server.starttls()
            server.login(username, password)
            server.send_message(msg)


async def _handle_email_delivery(event: MindEvent):
    payload = event.payload or {}
    title = str(payload.get("title") or payload.get("project") or "email_delivery").strip() or "email_delivery"
    request = str(payload.get("user_request") or payload.get("objective") or title).strip()
    event_kind = str(payload.get("event_kind") or "email_delivery")
    from ..project_state import get_project_dir, read_state_md, write_state_md

    project_dir = get_project_dir(_workspace, title)
    os.makedirs(project_dir, exist_ok=True)
    artifact_path = os.path.join(project_dir, "email_delivery_result.md")
    recipient = _extract_email_recipient(request)
    files = _candidate_email_attachments(request)
    supplied_cfg = _extract_smtp_config_from_text(request)
    saved_cfg_path = ""
    if supplied_cfg:
        try:
            saved_cfg_path = _save_email_config_from_user(supplied_cfg)
        except Exception as exc:
            logger.warning(f"[EMAIL] failed to save user supplied smtp config: {exc}")
    cfg = _load_email_config()
    missing_cfg = _email_config_missing_reason(cfg)

    status = "failed"
    findings: list[str] = []
    next_action = "补齐缺失信息后重新执行 email_delivery。"
    if not recipient:
        findings.append("没有从用户消息中解析到收件邮箱地址。")
    if not files:
        findings.append("没有找到与本次请求匹配的可发送文件。")
    if missing_cfg:
        findings.append(f"SMTP 邮件配置缺失：{missing_cfg}。")
        findings.append(_smtp_help_text(recipient, files))
        next_action = "等待用户提供发件邮箱和 SMTP 授权码；收到后继续发送邮件。"
    if saved_cfg_path:
        findings.append("已保存用户提供的 SMTP 配置。")

    if recipient and files and not missing_cfg:
        try:
            subject = str(payload.get("subject") or "Partner 文件交付").strip() or "Partner 文件交付"
            body = "你好，附件是 Partner 为你整理的文件。\n\n本邮件由 Partner 自动发送。"
            _send_email_with_attachments(cfg, recipient, subject, body, files)
            status = "sent"
            findings.append(f"已发送邮件到 {recipient}。")
            findings.append("附件：" + "；".join(os.path.basename(p) for p in files))
            next_action = "已完成，等待用户查收邮箱。"
        except Exception as exc:
            findings.append(f"SMTP 发送失败：{exc}")
            next_action = "检查 SMTP 配置、授权码、网络连通性后重试。"

    artifact = (
        f"# 邮件交付结果\n\n"
        f"- 时间: {datetime.now().isoformat(timespec='seconds')}\n"
        f"- 状态: {status}\n"
        f"- 收件人: {recipient or '未解析'}\n"
        f"- 附件: {'; '.join(files) if files else '未找到'}\n"
        f"- SMTP配置: {'已保存' if saved_cfg_path else ('缺失' if missing_cfg else '可用')}\n"
        f"- 结果: {'; '.join(findings) if findings else 'EMPTY'}\n"
        f"- 下一步: {next_action}\n"
    )
    _write_artifact_file(artifact_path, artifact)
    state_md = read_state_md(_workspace, title)
    parsed = {
        "step_done": "邮件交付已执行" if status == "sent" else "邮件交付未完成",
        "findings": findings or ["EMPTY"],
        "evidence": artifact_path,
        "next_action": next_action,
        "state_delta": f"email_delivery status={status}; recipient={recipient or 'missing'}; files={'; '.join(files) if files else 'missing'}",
        "files": "; ".join(files) if files else artifact_path,
        "artifact_content": artifact,
    }
    new_state = _merge_state_delta(
        existing_state=state_md,
        title=title,
        delta=parsed["state_delta"],
        step_done=parsed["step_done"],
        next_action=next_action,
    )
    if new_state:
        write_state_md(_workspace, title, new_state)
    try:
        record_round_result(_workspace, title, parsed, artifact)
    except Exception as exc:
        logger.debug(f"[EMAIL] memory update failed: {exc}")

    await _enqueue_visible_report(
        _event_completion_receipt(
            title,
            event.type,
            parsed,
            next_event=EventType.STOP_PROJECT.value,
            next_reason=next_action,
            files=files if status == "sent" else [artifact_path],
        ),
        event.type,
        event_kind=event_kind,
        priority=2,
        source="email_delivery:completion_receipt",
        parent_id=event.id,
        bypass_rate_limit=True,
    )
    try:
        await _enqueue_stop_project_event(event, title, next_action, payload)
    except Exception as exc:
        logger.debug(f"[EMAIL] failed to enqueue stop: {exc}")
    logger.info(f"[MIND] DONE event_type=email_delivery, id={event.id[:8]}, status={status}")


async def _handle_action_event(event: MindEvent):
    """Handle small action events without entering the heavy PROJECT pipeline."""
    payload = event.payload or {}
    title = str(payload.get("title") or payload.get("project") or payload.get("event_kind") or event.type.value).strip()
    if not title:
        title = event.type.value
    logger.info(f"[ACTION] Executing {event.type.value}: '{title[:60]}'")

    from ..project_state import get_project_dir, read_state_md, write_state_md

    project_dir = get_project_dir(_workspace, title)
    os.makedirs(project_dir, exist_ok=True)
    state_md = read_state_md(_workspace, title)
    spec = _action_event_spec(event.type)
    artifact_path = os.path.join(project_dir, str(spec.get("artifact") or f"{event.type.value}_result.md"))
    started_at = _time.time()
    response = ""
    parsed = _run_web_capture(event, title, project_dir)
    if parsed:
        response = (
            f"ACTION: {parsed.get('action', event.type.value)}\n"
            f"DONE: {parsed.get('step_done', '')}\n"
            f"FINDINGS: {'；'.join(parsed.get('findings') or [])}\n"
            f"EVIDENCE: {parsed.get('evidence', '')}\n"
            f"NEXT: {parsed.get('next_action', '')}\n"
            f"FILES: {parsed.get('files', '')}\n"
            f"STATE_DELTA: {parsed.get('state_delta', '')}\n"
            f"ARTIFACT_CONTENT: {parsed.get('artifact_content', '')}"
        )
        logger.info(f"[ACTION] builtin action handled {event.type.value}/{payload.get('event_kind') or ''}: {parsed.get('action') or ''}")
    else:
        try:
            if _adapter:
                prompt = _build_action_event_prompt(event, title, state_md, artifact_path)
                purpose = "action_think" if event.type in {EventType.PROJECT_THINK, EventType.OBJECTIVE_REVIEW, EventType.HABIT_UPDATE} else "action"
                response = (_adapter.chat(prompt, purpose=purpose) or "").strip()
        except Exception as exc:
            logger.warning(f"[ACTION] backend failed for {event.type.value}: {exc}")
            response = ""

        raw_had_tool_noise = bool(re.search(r"<\s*tool_call\b|<function=|<parameter=", response, re.I))
        parse_response = _strip_tool_call_noise(response) if raw_had_tool_noise else response
        parsed = _parse_structured_project_response(parse_response)
    if not parsed:
        record_risk_event(_workspace, title, f"{event.type.value} returned no structured result", response[:260], severity="medium")
        parsed = {
            "step_done": "本轮事件没有得到可执行的结构化结果",
            "findings": [
                "LLM/API 调用超时或返回格式不可解析，执行器没有拿到可验证产物",
                "本轮不会编造数据、图表或报告",
            ],
            "evidence": "system:action_event_unstructured_or_timeout",
            "next_action": "改用更小步骤继续：先拆解目标，再只执行下一个最小可验证 event。",
            "state_delta": f"{event.type.value} failed: no structured result",
            "files": "EMPTY",
            "artifact_content": "EMPTY",
        }
        if event.type == EventType.PROJECT_THINK:
            hinted_next = str(payload.get("previous_next_action") or "").strip()
            if hinted_next:
                parsed["next_action"] = hinted_next
            followup = {"queued": False, "event_type": "", "event_kind": "", "reason": ""}
            try:
                followup = await _maybe_enqueue_followup_event(event, title, parsed, payload)
            except Exception as exc:
                logger.warning(f"[FOLLOWUP] enqueue after unstructured project_think failed for {title}: {exc}")
            try:
                await _enqueue_visible_report(
                    _event_completion_receipt(
                        title,
                        event.type,
                        parsed,
                        next_event=str(followup.get("event_type") or ""),
                        next_reason=str(followup.get("reason") or parsed["next_action"]),
                        files=[],
                    ),
                    event.type,
                    event_kind=str(payload.get("event_kind") or event.type.value),
                    priority=2,
                    source=f"{event.type.value}:unstructured_followup_receipt",
                    parent_id=event.id,
                    bypass_rate_limit=True,
                )
            except Exception as exc:
                logger.debug(f"[ACTION] failed to enqueue project_think unstructured receipt: {exc}")
            if followup.get("queued"):
                logger.info(f"[ACTION] unstructured project_think still queued next event {followup.get('event_type')} for {title}")
            else:
                logger.warning(f"[ACTION] unstructured project_think did not produce follow-up; project left active for retry: {title}")
            logger.info(f"[MIND] DONE event_type={event.type.value}, id={event.id[:8]}")
            return
        recovery_queued = False
        try:
            pool = await ensure_pool()
            if event.type not in {EventType.PROJECT_THINK, EventType.OBJECTIVE_REVIEW}:
                root_request = _root_user_request(payload) or str(payload.get("user_request") or title)
                await pool.put(MindEvent(
                    type=EventType.OBJECTIVE_REVIEW,
                    priority=max(2, min(8, int(payload.get("priority") or 4) + 1)),
                    payload={
                        "title": title,
                        "step": int(payload.get("step") or 0) + 1,
                        "delivery_mode": "research_project",
                        "user_request": (
                            "上一个 action event 超时或返回不可解析。请不要重复同一个大动作，"
                            "先回看根目标、已有结果、缺口和阻塞，再选择下一个最小可验证 event。"
                            f"\n原始用户请求：{root_request[:1400]}"
                        ),
                        "root_user_request": root_request[:1800],
                        "event_type": EventType.OBJECTIVE_REVIEW.value,
                        "event_kind": "action_failure_objective_review",
                        "stop_after_completion": True,
                        "parent_user_request": str(payload.get("user_request") or "")[:1600],
                        "previous_next_action": parsed["next_action"],
                        "failure_event_type": event.type.value,
                        "failure_event_kind": str(payload.get("event_kind") or ""),
                    },
                    source=f"{event.type.value}:failure_objective_review",
                    parent_id=event.id,
                ))
                recovery_queued = True
        except Exception as exc:
            logger.debug(f"[ACTION] failed to enqueue failure recovery event: {exc}")
        try:
            await _enqueue_visible_report(
                _event_completion_receipt(
                    title,
                    event.type,
                    parsed,
                    next_event=EventType.OBJECTIVE_REVIEW.value if recovery_queued else (EventType.STOP_PROJECT.value if _stop_after_completion(payload) else ""),
                    next_reason=parsed["next_action"],
                    files=[],
                ),
                event.type,
                event_kind=str(payload.get("event_kind") or event.type.value),
                priority=2,
                source=f"{event.type.value}:unstructured_timeout_receipt",
                parent_id=event.id,
                bypass_rate_limit=True,
            )
        except Exception as exc:
            logger.debug(f"[ACTION] failed to enqueue unstructured-result receipt: {exc}")
        if _stop_after_completion(payload) and not recovery_queued:
            try:
                await _enqueue_stop_project_event(event, title, parsed["next_action"], payload)
            except Exception as exc:
                logger.debug(f"[STOP_PROJECT] enqueue after action failure failed: {exc}")
        logger.info(f"[MIND] DONE event_type={event.type.value}, id={event.id[:8]}")
        return

    artifact_written = False
    artifact_text = _normalize_artifact_content(parsed.get("artifact_content", ""))
    if event.type == EventType.PDF_REPORT:
        artifact_text = _repair_pdf_report_artifact_content(
            artifact_text,
            response,
            title,
            str(payload.get("user_request") or title),
            state_md,
            artifact_path,
        )
    if artifact_text:
        artifact_written = _write_artifact_file(artifact_path, artifact_text)
        if artifact_written and not parsed.get("files"):
            parsed["files"] = artifact_path
        if artifact_written and event.type == EventType.PDF_REPORT:
            pdf_path = _write_user_pdf_report(
                title,
                os.path.basename(artifact_path),
                artifact_text,
                source_dir=os.path.dirname(artifact_path),
            )
            if pdf_path:
                parsed["files"] = pdf_path
    new_state = _merge_state_delta(
        existing_state=state_md,
        title=title,
        delta=parsed.get("state_delta", ""),
        step_done=parsed.get("step_done", ""),
        next_action=parsed.get("next_action", ""),
    )
    if new_state:
        write_state_md(_workspace, title, new_state)
    try:
        record_round_result(_workspace, title, parsed, response)
    except Exception as exc:
        logger.debug(f"[ACTION] memory update failed: {exc}")
    if event.type == EventType.HABIT_UPDATE:
        _record_growth_event_visible(
            _workspace,
            title,
            trigger=payload.get("user_request") or parsed.get("step_done", ""),
            learned="用户经验需要转化为抽象习惯，而不是某个实例的硬编码规则。",
            behavior_change=parsed.get("next_action") or "以后在相似场景先应用该习惯，再决定是否扩展。",
            evidence=os.path.basename(artifact_path) if artifact_written else parsed.get("evidence", ""),
            category="habit_update",
        )

    required_exts = _required_output_exts(
        str(payload.get("user_request") or ""),
        event.type.value,
        str(payload.get("event_kind") or ""),
    )
    if event.type in {EventType.PROJECT_THINK, EventType.OBJECTIVE_REVIEW}:
        required_exts = set()
    pushed, files = _push_one_shot_output_files(
        project_dir,
        parsed,
        artifact_path=artifact_path if artifact_written else "",
        since_ts=started_at,
        required_exts=required_exts,
    )
    if required_exts and not files:
        record_risk_event(
            _workspace,
            title,
            f"{event.type.value} missing required output file",
            str(payload.get("user_request") or "")[:260],
            severity="high",
        )
        parsed["step_done"] = "没有生成用户要求的目标格式文件"
        parsed["findings"] = [
            f"用户要求的文件格式是 {', '.join(sorted(required_exts))}，但本轮未发现对应真实文件",
            "不能把摘要 Markdown 当作目标交付文件发送",
        ]
        parsed["next_action"] = "补齐必要参数或重新执行文件生成，直到产生目标格式文件。"
    followup = {"queued": False, "event_type": "", "event_kind": "", "reason": ""}
    try:
        followup = await _maybe_enqueue_followup_event(event, title, parsed, payload)
    except Exception as exc:
        logger.warning(f"[FOLLOWUP] enqueue check failed for {title}: {exc}")
    if not followup.get("queued") and _stop_after_completion(payload):
        stop_reason = str(followup.get("reason") or "one-shot event completed without selected follow-up")
        try:
            await _enqueue_stop_project_event(event, title, stop_reason, payload)
            followup = {
                "queued": True,
                "event_type": EventType.STOP_PROJECT.value,
                "event_kind": "one_shot_complete",
                "reason": stop_reason,
            }
        except Exception as exc:
            logger.debug(f"[STOP_PROJECT] enqueue after one-shot failed: {exc}")
    await _enqueue_visible_report(
        _event_completion_receipt(
            title,
            event.type,
            parsed,
            next_event=str(followup.get("event_type") or ""),
            next_reason=str(followup.get("reason") or ""),
            files=files,
        ),
        event.type,
        event_kind=str(payload.get("event_kind") or event.type.value),
        priority=2,
        source=f"{event.type.value}:completion_receipt",
        parent_id=event.id,
        bypass_rate_limit=True,
    )
    if followup.get("queued"):
        logger.info(f"[ACTION] selector queued next event {followup.get('event_type')} for {title}")
    else:
        logger.info(f"[ACTION] selector did not queue a next event; project remains active for later selection: {title}")
    logger.info(f"[MIND] DONE event_type={event.type.value}, id={event.id[:8]}")


# ── PROJECT ─────────────────────────────────────────────────────────


def _append_log_summary(workspace: str, title: str, ts: str, parsed: dict, step: int = 0):
    """追加摘要到 exploration_log.md（精简版），完整内容写入 trace_detail.md。"""
    from ..project_state import get_project_dir

    project_dir = get_project_dir(workspace, title)

    # 摘要条目
    step_done = parsed.get("step_done", "")
    action = parsed.get("action", "")
    evidence = parsed.get("evidence", "")
    files = parsed.get("files", "")
    findings = parsed.get("findings", [])
    next_action = parsed.get("next_action", "")

    summary_lines = [f"### {ts}"]
    if action:
        summary_lines.append(f"动作: {action[:80]}")
    if step_done:
        summary_lines.append(f"完成: {step_done[:120]}")
    if findings:
        summary_lines.append(f"发现: {'; '.join(f[:80] for f in findings[:2])}")
    if evidence:
        summary_lines.append(f"证据: {evidence[:120]}")
    if files and files.upper() != "EMPTY":
        summary_lines.append(f"文件: {files[:120]}")
    if next_action:
        summary_lines.append(f"下一步: {next_action[:120]}")
    summary_lines.append("")

    summary_entry = "\n".join(summary_lines)

    # 写入 exploration_log.md（只保留摘要，限制大小）
    summary_path = os.path.join(project_dir, "exploration_log.md")
    try:
        # 读取现有内容
        existing = ""
        if os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                existing = f.read()

        # 如果文件过大（>50KB），截断旧条目
        if len(existing.encode("utf-8")) > 50000:
            lines = existing.split("\n")
            # 保留前5行（标题）+ 后半部分条目
            header = "\n".join(lines[:5]) + "\n\n"
            body_lines = lines[5:]
            # 保留后 2/3 的内容
            keep_start = len(body_lines) // 3
            existing = header + "\n".join(body_lines[keep_start:])

        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(summary_entry)
    except OSError as e:
        logger.warning(f"[PROJECT] 写入 exploration_log 摘要失败: {e}")

    # 完整回复写入 trace_detail.md
    trace_path = os.path.join(project_dir, "trace_detail.md")
    try:
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write(f"\n## [{ts}] step={step}\n")
            f.write(f"**DONE:** {step_done}\n")
            f.write(f"**FINDINGS:** {'; '.join(findings)}\n")
            f.write(f"**NEXT:** {next_action}\n")
            f.write(f"---\n\n")
    except OSError as e:
        logger.warning(f"[PROJECT] 写入 trace_detail 失败: {e}")


async def _handle_project(event: MindEvent):
    """项目念头：纯 Hermes 调度转发层。

    1. 读取项目状态
    2. 构造紧凑的单步执行 prompt
    3. 解析结构化回复并更新 state.md
    4. 追加执行日志
    5. 只把系统组装后的进展摘要推送到 QQ
    6. 将自身放回等待室（5分钟后继续）
    """
    title = event.payload.get("title", "")
    if not title:
        logger.warning(f"[PROJECT] No title, skipping")
        return

    logger.info(f"[PROJECT] Executing step {event.payload.get('step', 0)}: '{title[:60]}'")
    _running_projects.add(title)

    # 0. 确保活跃项目标记
    from ..project_state import (
        append_log,
        audit_project_context,
        audit_project_round,
        consolidate_project_files,
        get_project_dir,
        get_project_status,
        is_project_done_signal,
        read_state_md,
        resolve_project_name,
        set_active,
        set_project_status,
        write_state_md,
    )
    title = resolve_project_name(_workspace, title) or title
    set_active(_workspace, title)
    project_dir = get_project_dir(_workspace, title)
    ensure_mind_files(_workspace, title)
    ensure_baseline_and_metric_contracts(_workspace, title)
    one_shot_event = _stop_after_completion(event.payload)
    event_started_at = _time.time()

    # 1. 读取项目状态
    state_md = read_state_md(_workspace, title)
    try:
        from ..project_state import read_project_brief
        hot_text = f"{state_md}\n{read_project_brief(_workspace, title, max_chars=2200)}"
    except Exception:
        hot_text = state_md

    open_idea = get_open_idea(_workspace, title)
    selected_open_idea_text = str((open_idea or {}).get("content") or (open_idea or {}).get("idea") or "").strip()
    project_status = get_project_status(_workspace, title)
    risk_scan_text = _known_done_risk_text(_workspace, title, state_md, hot_text)
    context_audit_issues = audit_project_context(_workspace, title, risk_scan_text)
    if project_status == "done" and context_audit_issues:
        set_project_status(_workspace, title, "active", "历史完成态命中证据审计风险，重新核验真实项目")
        project_status = "active"
    if project_status == "done" and _cooling_down_enabled():
        set_project_status(_workspace, title, "cooling_down", "阶段完成，转入低频反思和突破口观察")
        project_status = "cooling_down"
    elif project_status == "done":
        set_project_status(_workspace, title, "active", "阶段完成后继续寻找下一步突破口")
        project_status = "active"
    event_source = str(getattr(event, "source", "") or "")
    material_wakeup = (
        str(event.payload.get("reason") or "") == "user_shared_project_material"
        or event_source.startswith("interaction:add_task")
        or event_source.startswith("interaction:switch_project")
        or event_source.startswith("interaction:correct_direction")
    )
    if project_status in {"cooling_down", "waiting"} and (open_idea or material_wakeup):
        set_project_status(_workspace, title, "active", "检测到未消化用户/老师信号，重新激活项目")
        project_status = "active"
    elif project_status == "waiting":
        logger.info(f"[PROJECT] Project is waiting for user evaluation/resource; skip execution: {title}")
        logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                    f"title='{title[:40]}'")
        return
    elif project_status == "cooling_down" and not _cooling_down_enabled():
        set_project_status(_workspace, title, "active", "默认连续推进：不进入冷却等待")
        project_status = "active"
    elif project_status == "cooling_down":
        pool = await ensure_pool()
        if not _has_reflection_event(pool, title):
            await pool.put(MindEvent(
                type=EventType.REFLECTION,
                priority=7,
                payload={"project": title, "reason": "project_cooling_down"},
                source="project:cooling_down_reflection",
                parent_id=event.id,
            ))
        else:
            logger.info(f"[PROJECT] 同项目 cooling-down reflection 已在队列中，跳过重复: {title}")
        await _enqueue_project_if_absent(
            pool,
            title,
            priority=8,
            wake_after=_time.time() + _cooling_down_delay_sec(),
            source="project:cooling_down_recur",
            step=event.payload.get("step", 0) + 1,
            parent_id=event.id,
        )
        logger.info(f"[PROJECT] Cooling-down project kept alive via reflection loop: {title}")
        return

    # 2. 调用 Hermes — 只推进一个最小闭环步骤
    response = None
    try:
        if _adapter:
            prompt, artifact_path = _build_project_prompt(
                workspace=_workspace,
                title=title,
                state_md=state_md,
                step=event.payload.get("step", 0),
                event_payload=event.payload,
            )
            try:
                response = _adapter.chat(prompt, purpose="project")
            except Exception as e:
                logger.warning(f"[PROJECT] Hermes 调用异常: {e}")
                response = None

        # 3. 处理 Hermes 回复
        hermes_response = (response or "").strip()
        raw_had_tool_noise = bool(re.search(r"<\s*tool_call\b|<function=|<parameter=", hermes_response, re.I))
        parse_response = _strip_tool_call_noise(hermes_response) if raw_had_tool_noise else hermes_response
        parsed = _parse_structured_project_response(parse_response)
        repaired_stalled_result = False
        try:
            from ..project_state import load_project_guardrail

            guardrail_for_pause = load_project_guardrail(_workspace, title)
            allow_contract_pause = bool(guardrail_for_pause.get("completion_criteria"))
        except Exception:
            allow_contract_pause = False
        if parsed and _looks_like_stalled_project_result(
            parsed,
            allow_contract_pause=allow_contract_pause,
        ):
            repaired_stalled_result = True
            record_risk_event(
                _workspace,
                title,
                "stalled/done project result repaired",
                json.dumps(parsed, ensure_ascii=False)[:500],
                severity="medium",
            )
            _stalled_repair_counter[title] = _stalled_repair_counter.get(title, 0) + 1
            next_breakthrough = _breakthrough_next_action(title, parsed)
            _append_breakthrough_queue(
                _workspace,
                title,
                reason=f"完成态/等待态空转修复，连续次数={_stalled_repair_counter[title]}",
                next_action=next_breakthrough,
                source_result=parsed,
            )
            _record_growth_event_visible(
                _workspace,
                title,
                trigger="项目输出完成/等待/无下一步",
                learned="长期科研伙伴不能把“项目完成/等待用户”当成停止信号。",
                behavior_change="以后遇到完成态或等待态，会自动转入复盘、误差分析、失败边界或下一突破口。",
                evidence="breakthrough_queue.md",
                category="anti_stall",
            )
            parsed = _repair_stalled_project_result(title, parsed)
        elif parsed:
            _stalled_repair_counter[title] = 0
        audit_issues = [] if one_shot_event else (audit_project_round(_workspace, title, parsed) if parsed else [])
        if audit_issues:
            parsed["findings"] = (parsed.get("findings") or [])[:1] + audit_issues[:2]
            parsed["next_action"] = "先补齐真实证据文件或回到真实项目源目录，不能用无证据/合成数据结论收尾。"
            parsed["state_delta"] = (parsed.get("state_delta") or "") + "\n证据审计未通过：" + "；".join(audit_issues[:2])
            _append_breakthrough_queue(
                _workspace,
                title,
                reason="证据审计未通过",
                next_action=parsed["next_action"],
                source_result=parsed,
            )
            _record_growth_event_visible(
                _workspace,
                title,
                trigger="证据审计未通过",
                learned="没有真实证据的指标、文件或结论不能直接汇报成进展。",
                behavior_change="以后先补齐证据路径或回到真实源目录，再更新最佳结果和用户汇报。",
                evidence="system/checks/risk_events.jsonl",
                category="evidence_habit",
            )
            for issue in audit_issues[:3]:
                record_risk_event(_workspace, title, "evidence audit failed", issue, severity="high")
        guardrail_result = {"issues": [], "report_type": "meaningful_progress", "progress_score": 70 if one_shot_event else 0}
        if parsed and not one_shot_event and not hermes_response.strip() == USER_FRIENDLY_PROGRESS_REPLY:
            guardrail_result = apply_round_guardrails(
                _workspace,
                title,
                parsed,
                hermes_response=hermes_response,
            )
            parsed = guardrail_result.get("parsed") or parsed
            guardrail_issues = list(guardrail_result.get("issues") or [])
            if guardrail_issues:
                audit_issues = _dedupe_text_list((audit_issues or []) + guardrail_issues)
                _append_breakthrough_queue(
                    _workspace,
                    title,
                    reason="研究守门检查触发",
                    next_action=parsed.get("next_action") or "先做 baseline/metric/evidence 审计，再继续推进。",
                    source_result=parsed,
                )
        new_state = _merge_state_delta(
            existing_state=state_md,
            title=title,
            delta=parsed.get("state_delta", ""),
            step_done=parsed.get("step_done", ""),
            next_action=parsed.get("next_action", ""),
        ) if parsed else ""
        push_text = _format_user_progress_update(parsed, project_title=title)

        timed_out_or_stalled = (
            not hermes_response
            or hermes_response.strip() == USER_FRIENDLY_PROGRESS_REPLY
        )
        invalid_structured_reply = bool(hermes_response and not timed_out_or_stalled and not parsed)
        if raw_had_tool_noise and not parsed:
            record_risk_event(_workspace, title, "raw tool-call leaked in backend output", hermes_response[:260], severity="high")

        # 规划循环检测：追踪连续纯规划轮次
        if parsed and not timed_out_or_stalled:
            is_plan_only = _detect_plan_only_response(parsed, hermes_response)
            if is_plan_only:
                _plan_loop_counter[title] = _plan_loop_counter.get(title, 0) + 1
                logger.info(f"[PROJECT] 检测到纯规划回复，连续计数: {_plan_loop_counter[title]}")
                if _plan_loop_counter[title] >= 2:
                    _append_breakthrough_queue(
                        _workspace,
                        title,
                        reason=f"连续纯规划轮次={_plan_loop_counter[title]}",
                        next_action="停止继续写方案，选择一个已有方案中的最小可执行动作并运行或验证，产出证据文件。",
                        source_result=parsed,
                    )
                    _record_growth_event_visible(
                        _workspace,
                        title,
                        trigger="连续多轮只写计划/方案",
                        learned="只写方案会让项目看起来在动、实际没有推进。",
                        behavior_change="以后连续规划超过阈值时，强制选择一个最小可执行动作并产出证据文件。",
                        evidence="breakthrough_queue.md",
                        category="execution_habit",
                    )
            else:
                _plan_loop_counter[title] = 0  # 有实际执行，重置计数

        artifact_written = False
        system_generated_result = str(parsed.get("evidence") or "").startswith("system:") if parsed else False
        if parsed and artifact_path and not timed_out_or_stalled and not system_generated_result:
            artifact_text = forced_artifact_text or _normalize_artifact_content(parsed.get("artifact_content", ""))
            if not artifact_text:
                artifact_text = _normalize_artifact_content(
                    _fallback_artifact_content(hermes_response, artifact_path)
                )
            if os.path.basename(artifact_path).startswith("stage_report_") and artifact_path.endswith(".md"):
                try:
                    from ..stage_report import _is_placeholder_report, _resolve_full_markdown_path, _read_text

                    if _is_placeholder_report(artifact_text):
                        resolved = _resolve_full_markdown_path(_workspace, title, artifact_path)
                        artifact_text = _read_text(resolved)
                except Exception as exc:
                    logger.warning(f"[PROJECT] 阶段汇报正文无效，拒绝写入占位报告: {exc}")
                    artifact_text = ""
            if _artifact_needs_structured_fallback(artifact_path, artifact_text):
                artifact_text = _structured_audit_artifact(artifact_path, parsed, hermes_response)
            if artifact_text:
                artifact_written = _write_artifact_file(artifact_path, artifact_text)

        if new_state and not timed_out_or_stalled:
            write_state_md(_workspace, title, new_state)
            logger.info(f"[PROJECT] 状态已更新（{len(new_state)} 字符）")
        stage_report_published_this_round = False
        one_shot_files_pushed = False
        one_shot_output_files: list[str] = []
        if artifact_written:
            logger.info(f"[PROJECT] 产物已写入: {os.path.basename(artifact_path)}")
            artifact_name = os.path.basename(artifact_path)
            if artifact_name.startswith("stage_report_") and artifact_name.endswith(".md"):
                try:
                    from ..stage_report import publish_stage_report

                    published = publish_stage_report(_workspace, title, artifact_path)
                    if published:
                        files_pushed = _push_stage_report_files(published)
                        parsed["next_action"] = parsed.get("next_action") or "继续执行阶段汇报中的下一步最小计划。"
                        _record_growth_event_visible(
                            _workspace,
                            title,
                            trigger="项目积累到阶段性汇报节点",
                            learned="阶段成果需要转化成用户/老师可读的汇报，而不是只堆日志。",
                            behavior_change="后续每隔一段推进轮次自动生成阶段汇报 PPT/PDF，并把风险和失败边界一起呈现。",
                            evidence=os.path.basename(published.get("pdf") or artifact_name),
                            category="communication_habit",
                        )
                        try:
                            pool = await ensure_pool()
                            message = _stage_report_text_summary(title, "", published)
                            await pool.put(MindEvent(
                                type=EventType.REPORT,
                                priority=2,
                                payload={
                                    "content": message,
                                    "force_send": True,
                                    "bypass_rate_limit": True,
                                },
                                source="project:stage_report_published",
                                parent_id=event.id,
                            ))
                        except Exception as exc:
                            logger.debug(f"[REPORT] enqueue stage report notification failed: {exc}")
                        logger.info(f"[PROJECT] 阶段汇报已发布: {published}")
                        stage_report_published_this_round = True
                except Exception as exc:
                    logger.warning(f"[PROJECT] 阶段汇报发布失败: {exc}")
            if artifact_name == "data_leakage_audit.md":
                _record_growth_event_visible(
                    _workspace,
                    title,
                    trigger="用户/审计指出结果异常好或可能泄露",
                    learned="异常好结果不能直接当突破；必须先确认验证流程没有泄露。",
                    behavior_change="以后遇到过低误差、异常提升或用户经验不匹配时，优先生成数据泄露/过拟合审计，再继续实验。",
                    evidence=artifact_name,
                    category="quality_guardrail",
                )
            elif artifact_name == "progress_quality_audit.md":
                _record_growth_event_visible(
                    _workspace,
                    title,
                    trigger="检测到机械递增、重复堆数量或文件复用",
                    learned="持续运行不等于持续推进；重复生成更多数量可能是伪进展。",
                    behavior_change="以后会先核对唯一结果、真实脚本、输出路径和可复现实验，再决定是否继续扩大规模。",
                    evidence=artifact_name,
                    category="progress_quality",
                )

        if one_shot_event and parsed and not timed_out_or_stalled and not invalid_structured_reply:
            one_shot_files_pushed, one_shot_output_files = _push_one_shot_output_files(
                project_dir,
                parsed,
                artifact_path=artifact_path if artifact_written else "",
                since_ts=event_started_at,
                required_exts=_required_output_exts(
                    str(event.payload.get("user_request") or ""),
                    str(event.payload.get("event_type") or "project"),
                    str(event.payload.get("event_kind") or ""),
                ),
            )
            if one_shot_output_files:
                logger.info(
                    "[PROJECT] one-shot output files: %s (pushed=%s)",
                    [os.path.basename(p) for p in one_shot_output_files],
                    one_shot_files_pushed,
                )
                parsed["files"] = "; ".join(one_shot_output_files)

        # 3b. 追加日志：trace_detail.md 记录完整回复，exploration_log.md/log.md 只记录摘要
        if hermes_response and not timed_out_or_stalled and parsed:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            # 完整回复写入 trace_detail.md（溯源用）
            append_log(_workspace, title, f"### {ts}\n{hermes_response}\n")
            # 摘要写入 exploration_log.md（只保留关键信息）
            _append_log_summary(_workspace, title, ts, parsed, step=event.payload.get("step", 0))
            try:
                record_round_result(_workspace, title, parsed, hermes_response)
            except Exception as exc:
                logger.debug(f"[PROJECT] research memory update failed: {exc}")
            try:
                from ..project_state import update_project_brief_from_round
                update_project_brief_from_round(_workspace, title, parsed)
            except Exception as exc:
                logger.debug(f"[PROJECT] project brief update failed: {exc}")
            try:
                consolidate_project_files(_workspace, title)
                consolidate_research_memory(_workspace, title)
                scan_workspace_changes(_workspace, title)
            except Exception as exc:
                logger.debug(f"[PROJECT] memory consolidation failed: {exc}")
            try:
                if selected_open_idea_text and artifact_written:
                    mark_idea_processed(_workspace, title, selected_open_idea_text, status="absorbed")
            except Exception as exc:
                logger.debug(f"[PROJECT] idea status update failed: {exc}")
        elif invalid_structured_reply:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            append_log(
                _workspace,
                title,
                f"### {ts}\n本轮返回了非结构化输出，已跳过落盘与状态更新。\n",
            )
            record_risk_event(_workspace, title, "非结构化结果", hermes_response[:260], severity="medium")
        elif timed_out_or_stalled:
            record_risk_event(_workspace, title, "agent backend stalled or unavailable", hermes_response[:260], severity="high")

        # 4. Event completion receipt. REPORT is now only the transport for
        # event-level receipts, not a proactive progress policy.
        pool = await ensure_pool()

        mode = _delivery_mode(event.payload)
        if _stop_after_completion(event.payload) and parsed and not timed_out_or_stalled and not invalid_structured_reply:
            followup = {"queued": False, "event_type": "", "event_kind": "", "reason": ""}
            try:
                followup = await _maybe_enqueue_followup_event(event, title, parsed, event.payload or {})
            except Exception as exc:
                logger.warning(f"[FOLLOWUP] enqueue check failed for {title}: {exc}")
            await _enqueue_visible_report(
                _event_completion_receipt(
                    title,
                    event.type,
                    parsed,
                    next_event=str(followup.get("event_type") or ""),
                    next_reason=str(followup.get("reason") or ""),
                    files=one_shot_output_files,
                ),
                event.type,
                event_kind=str(event.payload.get("event_kind") or event.type.value),
                priority=2,
                source="project:completion_receipt",
                parent_id=event.id,
                bypass_rate_limit=True,
            )
            if followup.get("queued"):
                logger.info(f"[PROJECT] selector queued next event {followup.get('event_type')} for {title}")
            else:
                logger.info(f"[PROJECT] selector did not choose continue/stop; project remains active for later selection: {title}, mode={mode}, event_kind={event.payload.get('event_kind')}")
            logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                        f"title='{title[:40]}'")
            return

        # 5. 将自身放回等待室。CRON_TICK 是恢复/健康检查；项目生命线按状态推进。
        next_step = event.payload.get("step", 0) + 1
        active_now = get_active_project_name()
        if active_now and active_now != title:
            logger.info(f"[PROJECT] Not re-queueing stale project '{title}', active is '{active_now}'")
            logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                        f"title='{title[:40]}'")
            return
        try:
            paused_lit, lit_reason = maybe_pause_after_literature_report(
                _workspace,
                title,
                published_report=stage_report_published_this_round,
            )
            if paused_lit:
                logger.info(f"[PROJECT] Literature/reference task paused after report: {title}: {lit_reason}")
                logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                            f"title='{title[:40]}'")
                return
        except Exception as exc:
            logger.debug(f"[PROJECT] literature task pause check failed: {exc}")
        paused_by_quality_gate = False
        try:
            paused_by_quality_gate, pause_reason = maybe_pause_project_for_quality_gate(
                _workspace,
                title,
                next_step=next_step,
                report_type=str(guardrail_result.get("report_type") or "meaningful_progress"),
                progress_score=int(guardrail_result.get("progress_score") or 0),
            )
            if paused_by_quality_gate:
                logger.info(f"[PROJECT] Quality gate paused project '{title}': {pause_reason}")
                if stage_report_published_this_round:
                    logger.info("[PROJECT] Skip extra quality-gate notice because stage report was just sent")
                    logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                                f"title='{title[:40]}'")
                    return
                try:
                    report_outputs = _latest_stage_report_outputs(title)
                    files_pushed = _push_stage_report_files(report_outputs) if report_outputs else False
                    message = _stage_report_text_summary(title, pause_reason, report_outputs)
                    await pool.put(MindEvent(
                        type=EventType.REPORT,
                        priority=2,
                        payload={
                            "content": message,
                            "force_send": True,
                            "bypass_rate_limit": True,
                        },
                        source="project:quality_gate_pause_notice",
                        parent_id=event.id,
                    ))
                except Exception as exc:
                    logger.debug(f"[REPORT] failed to enqueue quality-gate pause notice: {exc}")
                logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                            f"title='{title[:40]}'")
                return
        except Exception as exc:
            logger.debug(f"[PROJECT] quality gate check failed: {exc}")
        if (
            parsed
            and not audit_issues
            and is_project_done_signal(parsed)
            and not get_open_idea(_workspace, title)
            and _cooling_down_enabled()
        ):
            set_project_status(_workspace, title, "cooling_down", parsed.get("step_done", "阶段完成，进入低频反思"))
            await _enqueue_project_if_absent(
                pool,
                title,
                priority=8,
                wake_after=_time.time() + _cooling_down_delay_sec(),
                source="project:completion_cooling_down",
                step=next_step,
                parent_id=event.id,
            )
            logger.info(f"[PROJECT] Marked cooling_down and scheduled low-frequency revisit: {title}")
        else:
            if audit_issues or repaired_stalled_result:
                set_project_status(_workspace, title, "active", "检测到证据不足或完成态空转，继续执行突破队列")
            wake_after = _active_recur_wake_after()
            await _enqueue_project_if_absent(
                pool,
                title,
                priority=6,
                wake_after=wake_after,
                source="project:recur",
                step=next_step,
                parent_id=event.id,
            )
            if wake_after:
                logger.info(f"[PROJECT] Re-queued for step {next_step} (wake in {int(wake_after - _time.time())}s)")
            else:
                logger.info(f"[PROJECT] Re-queued for step {next_step} immediately")
            auto_recur_source = str(event.source or "").startswith((
                "project:recur",
                "cron_tick:resume_active",
                "wake_up:resume_active",
                "project:completion_cooling_down",
            ))
            if parsed and not timed_out_or_stalled and not invalid_structured_reply and not auto_recur_source:
                await _enqueue_visible_report(
                    _event_completion_receipt(
                        title,
                        event.type,
                        parsed,
                        next_event=EventType.PROJECT.value,
                        next_reason=f"项目仍在 active，下一轮继续 step {next_step}",
                    ),
                    event.type,
                    event_kind=str(event.payload.get("event_kind") or "project_recur"),
                    priority=3,
                    source="project:completion_receipt",
                    parent_id=event.id,
                    bypass_rate_limit=True,
                )

        logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                    f"title='{title[:40]}'")
    finally:
        _running_projects.discard(title)


# ── REPORT ──────────────────────────────────────────────────────────


async def _handle_stop_project(event: MindEvent):
    payload = event.payload or {}
    title = str(payload.get("title") or "").strip()
    reason = str(payload.get("reason") or "selector chose to stop project execution").strip()
    if not title:
        try:
            from ..project_state import get_active
            title = get_active(_workspace) or ""
        except Exception:
            title = ""
    if not title:
        logger.info(f"[STOP_PROJECT] No project title; nothing to stop for {event.id[:8]}")
        logger.info(f"[MIND] DONE event_type=stop_project, id={event.id[:8]}")
        return
    try:
        from ..project_state import append_log, clear_active, set_project_status

        set_project_status(_workspace, title, "waiting", reason)
        append_log(_workspace, title, f"STOP_PROJECT: {reason}")
        clear_active(_workspace, title)
        logger.info(f"[STOP_PROJECT] Project waiting and active cleared: {title} reason={reason[:160]}")
        await _enqueue_visible_report(
            f"已停止「{title}」的当前执行链，原因：{_clip(reason, 160)}",
            EventType.STOP_PROJECT,
            event_kind=str(payload.get("event_kind") or ""),
            priority=2,
            source="stop_project:notice",
            parent_id=event.id,
            bypass_rate_limit=True,
        )
    except Exception as exc:
        logger.warning(f"[STOP_PROJECT] failed to stop project {title}: {exc}")
    logger.info(f"[MIND] DONE event_type=stop_project, id={event.id[:8]}")


async def _handle_report(event: MindEvent):
    """汇报念头：直接推送到 QQ（如果有活跃的 bot 连接），含去重。
    移除旧版 JSON 降级写入逻辑，仅保留 push_callback。
    """
    content = event.payload.get("content", "")
    visible_type = str(event.payload.get("visible_event_type") or "").strip()
    visible_kind = str(event.payload.get("visible_event_kind") or "").strip()
    if not visible_type:
        source = str(getattr(event, "source", "") or "")
        if "stop_project" in source:
            visible_type = EventType.STOP_PROJECT.value
        elif "habit" in source or "growth" in source:
            visible_type = EventType.HABIT_UPDATE.value
        elif ":" in source:
            visible_type = source.split(":", 1)[0]
        else:
            visible_type = EventType.REPORT.value
    content = prefix_event_notice(content, visible_type, event_kind=visible_kind, workspace=_workspace)
    content = _sanitize_user_report_text(content)
    content = improve_user_report(content, "meaningful_progress")
    if not content:
        logger.warning(f"[REPORT] Empty content, skipping {event.id[:8]}")
        return

    logger.info(f"[REPORT] Sending: {content[:80]}...")

    if _push_callback is not None:
        try:
            ok = _push_callback(content)
            if ok is False:
                logger.warning(f"[REPORT] Callback did not send message ({len(content)} chars)")
            else:
                logger.info(f"[REPORT] Sent via callback ({len(content)} chars)")
        except Exception as e:
            logger.warning(f"[REPORT] Callback push failed: {e}")
    else:
        logger.info(f"[REPORT] No push callback registered, content dropped")

    payload_files = event.payload.get("files") or event.payload.get("file_paths") or []
    if isinstance(payload_files, str):
        payload_files = [payload_files]
    if payload_files:
        try:
            files = _resolve_one_shot_output_files(
                _workspace,
                {"files": "; ".join(str(x) for x in payload_files)},
                artifact_path="",
                since_ts=None,
                required_exts=set(),
            )
            if _file_push_callback is not None:
                for path in files:
                    try:
                        with open(path, "rb") as f:
                            data = f.read()
                        _file_push_callback(data, os.path.basename(path), os.path.basename(path))
                    except Exception as exc:
                        logger.warning(f"[REPORT] payload file push failed for {path}: {exc}")
        except Exception as exc:
            logger.warning(f"[REPORT] failed to resolve payload files: {exc}")

    logger.info(f"[MIND] DONE event_type=report, id={event.id[:8]}")


# ── CRON_TICK ───────────────────────────────────────────────────────


async def _enqueue_open_content_digests(pool: MindPool, *, source: str, active_project: str = "") -> int:
    """Ensure unprocessed external content is eventually digested.

    QQ/message callbacks should nudge CONTENT_DIGEST immediately, but this
    wake/cron fallback makes the pipeline robust after restarts or missed
    cross-thread nudges.
    """
    count = 0
    try:
        items = get_open_content_items(_workspace, project=active_project, limit=3)
        if not items and active_project:
            items = get_open_content_items(_workspace, limit=3)
        for item in items:
            project = item.get("project") or active_project or ""
            if project and not _should_wake_waiting_literature_project(project, item):
                logger.info(
                    f"[CONTENT] Skip stale pre-pause material for waiting literature task: {project} {item.get('id','')}"
                )
                mark_content_processed(
                    _workspace,
                    item.get("id", ""),
                    digest="已生成文献阶段汇报后遗留的旧素材，不再唤醒项目。",
                    status="archived_after_report",
                )
                continue
            await pool.put(MindEvent(
                type=EventType.CONTENT_DIGEST,
                priority=1,
                payload={
                    "content_id": item.get("id", ""),
                    "project": project,
                },
                source=source,
            ))
            count += 1
    except Exception as exc:
        logger.debug(f"[CONTENT] open content enqueue failed: {exc}")
    return count


def _safe_project_name(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name or "project").strip("_") or "project"


def _literature_pause_cutoff_ts(project: str) -> float:
    """Return timestamp after which user material should wake a paused lit task."""
    safe = _safe_project_name(project)
    candidates = [
        os.path.join(_workspace, "user", "projects", safe, "literature_task_pause.md"),
        os.path.join(_workspace, "user", "reports", safe, "latest_stage_report.md"),
    ]
    times = []
    for path in candidates:
        try:
            if os.path.exists(path):
                times.append(os.path.getmtime(path))
        except OSError:
            pass
    return max(times) if times else 0.0


def _content_item_time_ts(item: dict) -> float:
    raw = str(item.get("time") or item.get("created_at") or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return 0.0


def _idea_time_ts(idea: dict | None) -> float:
    if not idea:
        return 0.0
    raw = str(idea.get("time") or idea.get("ts") or idea.get("created_at") or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return 0.0


def _is_new_idea_for_waiting_literature_project(project: str, idea: dict | None) -> bool:
    if not idea:
        return False
    try:
        from ..project_state import get_project_status

        status = get_project_status(_workspace, project)
    except Exception:
        status = ""
    if status not in {"waiting", "done"}:
        return True
    if not is_literature_reference_task(_workspace, project):
        return True
    cutoff = _literature_pause_cutoff_ts(project)
    if cutoff <= 0:
        return True
    idea_ts = _idea_time_ts(idea)
    return bool(idea_ts and idea_ts > cutoff + 1)


def _should_wake_waiting_literature_project(project: str, item: dict | None = None) -> bool:
    """Only post-pause user material should wake a completed literature task."""
    if not project:
        return True
    try:
        from ..project_state import get_project_status

        status = get_project_status(_workspace, project)
    except Exception:
        status = ""
    if status not in {"waiting", "done"}:
        return True
    if not is_literature_reference_task(_workspace, project):
        return True
    cutoff = _literature_pause_cutoff_ts(project)
    if cutoff <= 0:
        return True
    if not item:
        return False
    item_ts = _content_item_time_ts(item)
    return bool(item_ts and item_ts > cutoff + 1)


def _heartbeat_probe_ollama(source: str) -> None:
    try:
        from ..ollama_pool import heartbeat_probe

        status = heartbeat_probe(_workspace, purpose="report")
        selected = status.get("selected") or ""
        endpoint = status.get("endpoint") or ""
        if selected:
            logger.info(f"[OLLAMA] heartbeat {source}: available {endpoint}/{selected}")
        else:
            logger.info(f"[OLLAMA] heartbeat {source}: fallback primary_agent ({status.get('reason')})")
    except Exception as exc:
        logger.debug(f"[OLLAMA] heartbeat probe failed during {source}: {exc}")


async def _handle_cron_tick(event: MindEvent):
    """心跳念头：检查 active_project.txt → 如有则创建 PROJECT 事件。

    不提示用户、不搜索、只检查持久化的活跃项目标记。
    """
    pool = await ensure_pool()
    habits = ensure_habits(_workspace)
    try:
        scan_workspace_changes(_workspace)
    except Exception as exc:
        logger.debug(f"[CRON] file perception scan failed: {exc}")
    _heartbeat_probe_ollama("cron_tick")

    if should_run_periodic(_workspace, "memory_consolidation", float(habits.get("memory_consolidation_interval_hours", 6))):
        await pool.put(MindEvent(
            type=EventType.MEMORY_CONSOLIDATE,
            priority=9,
            payload={},
            source="cron_tick:memory_consolidate",
        ))
        mark_periodic_run(_workspace, "memory_consolidation")

    if should_run_periodic(_workspace, "daily_reflection", float(habits.get("daily_reflection_interval_hours", 24))):
        await pool.put(MindEvent(
            type=EventType.REFLECTION,
            priority=7,
            payload={},
            source="cron_tick:reflection",
        ))
        mark_periodic_run(_workspace, "daily_reflection")

    if should_run_periodic(_workspace, "cross_project", float(habits.get("cross_project_interval_hours", 24))):
        await pool.put(MindEvent(
            type=EventType.CROSS_PROJECT,
            priority=8,
            payload={},
            source="cron_tick:cross_project",
        ))
        mark_periodic_run(_workspace, "cross_project")

    try:
        source_cfg = ensure_content_sources(_workspace)
        patrol_interval = float(source_cfg.get("interval_hours", 6))
        if content_patrol_enabled(_workspace) and should_run_periodic(_workspace, "content_patrol", patrol_interval):
            await pool.put(MindEvent(
                type=EventType.CONTENT_PATROL,
                priority=8,
                payload={},
                source="cron_tick:content_patrol",
            ))
            mark_periodic_run(_workspace, "content_patrol")
    except Exception as exc:
        logger.debug(f"[CRON] content patrol check failed: {exc}")

    from ..project_state import recover_active_from_plan
    active_name = recover_active_from_plan(_workspace)
    digests = await _enqueue_open_content_digests(
        pool,
        source="cron_tick:open_content_digest",
        active_project=active_name or "",
    )
    if digests:
        logger.info(f"[CRON] queued {digests} open content digest event(s)")
    if active_name:
        try:
            from ..project_state import get_project_status, set_project_status
            from ..project_registry import maybe_release_inactive_active_project
            status = get_project_status(_workspace, active_name)
            inactive_hours = int(os.getenv("PARTNER_PUBLIC_RELEASE_AFTER_HOURS", "24") or "24")
            if maybe_release_inactive_active_project(_workspace, active_name, inactive_hours=inactive_hours):
                logger.info(f"[WAKE_UP] inactive project released to public pool: {active_name}")
                status = "waiting"
            open_idea = get_open_idea(_workspace, active_name)
            if status in {"waiting", "done"}:
                if open_idea and _is_new_idea_for_waiting_literature_project(active_name, open_idea):
                    set_project_status(
                        _workspace,
                        active_name,
                        "active",
                        "检测到未消化用户/老师信号，定时脉冲重新激活项目",
                    )
                    logger.info(f"[CRON] 活跃项目处于 {status}，但存在未消化用户信号，重新激活: {active_name}")
                elif open_idea:
                    logger.info(f"[CRON] 活跃项目处于 {status}，但 open_idea 早于阶段汇报暂停，跳过自动恢复: {active_name}")
                    logger.info(f"[MIND] DONE event_type=cron_tick, id={event.id[:8]}")
                    return
                else:
                    if await _maybe_recover_empty_active_chain(
                        pool,
                        active_name,
                        status,
                        source="cron_tick",
                        parent_id=event.id,
                    ):
                        logger.info(f"[MIND] DONE event_type=cron_tick, id={event.id[:8]}")
                        return
                    logger.info(f"[CRON] 活跃项目处于 {status}，跳过自动恢复: {active_name}")
                    logger.info(f"[MIND] DONE event_type=cron_tick, id={event.id[:8]}")
                    return
            if status in {"cooling_down"}:
                _cap_project_waiting_delay(pool, active_name, _cooling_down_delay_sec())
        except Exception as exc:
            logger.debug(f"[CRON] cooling-down delay cap failed: {exc}")
        if not _has_project_event(pool, active_name, include_running=True):
            if await _maybe_recover_empty_active_chain(
                pool,
                active_name,
                status,
                source="cron_tick",
                parent_id=event.id,
            ):
                logger.info(f"[MIND] DONE event_type=cron_tick, id={event.id[:8]}")
                return
            logger.info(f"[CRON] 检测到活跃项目: {active_name}")
            await _enqueue_project_if_absent(
                pool,
                active_name,
                priority=2,
                source="cron_tick:resume_active",
                step=0,
            )
        else:
            logger.info(f"[CRON] 活跃项目已有运行/排队事件，跳过重复恢复: {active_name}")
    else:
        logger.info(f"[CRON] 无活跃项目，什么都不做")

    logger.info(f"[MIND] DONE event_type=cron_tick, id={event.id[:8]}")


async def _handle_memory_consolidate(event: MindEvent):
    """MemoryConsolidator: compact all project hot files and long-term memory."""
    try:
        from ..project_state import consolidate_project_files
        projects_dir = os.path.join(_workspace, "20_records", "projects")
        if os.path.isdir(projects_dir):
            for name in os.listdir(projects_dir):
                path = os.path.join(projects_dir, name)
                if os.path.isdir(path):
                    consolidate_project_files(_workspace, name)
        consolidate_research_memory(_workspace)
        ensure_habits(_workspace)
        logger.info("[MEMORY] consolidation completed")
    except Exception as exc:
        logger.warning(f"[MEMORY] consolidation failed: {exc}")
    logger.info(f"[MIND] DONE event_type=memory_consolidate, id={event.id[:8]}")


async def _handle_reflection(event: MindEvent):
    """Independent ReflectionLoop: daily/periodic researcher reflection."""
    context = build_reflection_context(_workspace)
    prompt = (
        "你是 Partner 的独立科研反思模块，不执行新实验，只做策略性反思。\n"
        "基于下面长期记忆，输出中文，严格分为四段：\n"
        "1. 今日/近期最重要的有效进展\n"
        "2. 失败经验与方法边界\n"
        "3. 可迁移到其他项目的方法或假设\n"
        "4. 下一步策略调整，必须具体、短小、可执行\n"
        "不要问用户，不要编造不存在的结果；不确定就标 hypothesis。\n\n"
        f"{context}\n"
    )
    content = ""
    try:
        if _adapter:
            content = (_adapter.chat(prompt, purpose="report") or "").strip()
    except Exception as exc:
        logger.warning(f"[REFLECTION] LLM failed: {exc}")
    content = _sanitize_user_report_text(content)
    if not content:
        logger.info(f"[REFLECTION] skipped empty LLM output for event {event.id[:8]}")
        return
    path = write_reflection_artifacts(_workspace, content, kind="daily_reflection")
    append_strategy_memory(_workspace, content)
    logger.info(f"[REFLECTION] wrote {path}")
    project = str(event.payload.get("project") or "").strip()
    reason = str(event.payload.get("reason") or "").strip()
    if project and reason == "project_cooling_down":
        summary = _reflection_summary_for_report(content)
        if summary:
            pool = await ensure_pool()
            await pool.put(MindEvent(
                type=EventType.REPORT,
                priority=5,
                payload={
                    "content": summary,
                    "force_send": True,
                },
                source="reflection:cooling_down_report",
                parent_id=event.id,
            ))
    logger.info(f"[MIND] DONE event_type=reflection, id={event.id[:8]}")


async def _handle_cross_project(event: MindEvent):
    """Default Mode Network: cross-project transfer and old-failure reinterpretation."""
    context = build_cross_project_context(_workspace)
    prompt = (
        "你是 Partner 的默认模式网络/跨项目思考模块。\n"
        "只基于长期经验库，提出跨项目迁移假设，不执行实验。\n"
        "输出中文，严格包含：\n"
        "- 可迁移方法：方法名 | 来自项目 | 可能适用项目 | 适用条件 | 风险\n"
        "- 旧失败重解释：哪个失败不应被视为全局失败\n"
        "- 下一轮最小验证动作：最多 3 条\n"
        "没有证据就写 hypothesis，不要问用户。\n\n"
        f"{context}\n"
    )
    content = ""
    try:
        if _adapter:
            content = (_adapter.chat(prompt, purpose="report") or "").strip()
    except Exception as exc:
        logger.warning(f"[CROSS_PROJECT] LLM failed: {exc}")
    content = _sanitize_user_report_text(content)
    if not content:
        logger.info(f"[CROSS_PROJECT] skipped empty LLM output for event {event.id[:8]}")
        return
    path = write_reflection_artifacts(_workspace, content, kind="cross_project_thinking")
    append_strategy_memory(_workspace, content)
    logger.info(f"[CROSS_PROJECT] wrote {path}")
    logger.info(f"[MIND] DONE event_type=cross_project, id={event.id[:8]}")


async def _handle_content_digest(event: MindEvent):
    """Digest user-shared/self-collected social/article/video content."""
    project = str(event.payload.get("project") or "").strip()
    content_id = str(event.payload.get("content_id") or "").strip()
    items = get_open_content_items(_workspace, project=project, limit=5)
    if content_id:
        items = [item for item in items if item.get("id") == content_id]
    else:
        items = items[:1]
    if not items:
        logger.info(f"[CONTENT] no open content item, id={content_id}")
        visible_request = str(event.payload.get("user_request") or "").strip()
        visible_title = str(event.payload.get("title") or event.payload.get("target_project") or "").strip()
        visible_kind = str(event.payload.get("event_kind") or "").strip()
        if visible_request or visible_title or visible_kind:
            prompt = (
                "你是 Partner 的内容消化 event 汇报器。现在没有可读取的完整正文或 content_feed item，"
                "只能基于 event payload 中的可见线索做边界明确的消化结果。\n"
                "不要编造合并转发里的聊天记录正文，不要假装已经读到完整聊天记录。\n"
                "请用中文输出 80-260 字，说明：本轮能确认什么、不能确认什么、与当前任务/项目是否有明确关系、下一步最小动作。\n\n"
                f"event_kind: {visible_kind}\n"
                f"title: {visible_title}\n"
                f"user_request: {visible_request}\n"
            )
            try:
                content = (_adapter.chat(prompt, purpose="report") if _adapter else "") or ""
            except Exception as exc:
                logger.warning(f"[CONTENT] payload-only digest formatter failed: {exc}")
                content = UNAVAILABLE_NOTICE
            content = _sanitize_user_report_text(content) or UNAVAILABLE_NOTICE
            await _enqueue_visible_report(
                content,
                EventType.CONTENT_DIGEST,
                event_kind=visible_kind or "payload_only",
                priority=2,
                source="content_digest:payload_only_receipt",
                parent_id=event.id,
                force_send=True,
                bypass_rate_limit=True,
            )
        if bool(event.payload.get("stop_after_completion")):
            try:
                from ..project_state import clear_active

                clear_active(_workspace, visible_title or project)
            except Exception as exc:
                logger.debug(f"[CONTENT] failed to clear active project after empty digest: {exc}")
        logger.info(f"[MIND] DONE event_type=content_digest, id={event.id[:8]}")
        return
    item = items[0]
    project_label = project or "通用研究"
    urls = " ".join(item.get("urls") or [])
    intent = str(item.get("intent") or "general_learning")
    access_status = str(item.get("access_status") or "unknown")
    scope = str(item.get("scope") or ("project" if project else "general"))
    prompt = (
        "你是 Partner 的外部内容消化模块。用户可能分享了小红书、B站、公众号、知乎或其他内容。\n"
        "你的任务不是相信它，也不是强行把它并入当前项目，而是先判断它应如何被学习和记录。\n"
        "内容分四类：project_instruction=用户明确要求用于项目；project_reference=和当前项目明显相关；"
        "general_learning=用户随手分享的科普/长文/视频，只作为通用学习；access_limited=正文不可读。\n"
        "如果是 general_learning：不要写“对当前项目的启发”，改写为“可选的远距离启发”，不要触发项目主线变化。\n"
        "如果是 access_limited/link_only/metadata_only：不能编造正文观点，只能说明可见线索和需要用户补截图/正文。\n"
        "请输出中文，严格包含四段：\n"
        "1. 内容要点：只基于给定文本/链接线索，不能编造平台原文\n"
        "2. 学习定位：说明这是项目指令、项目参考、普通学习，还是访问受限材料\n"
        "3. 风险与不确定性：比如营销内容、断章取义、链接不可读、缺少证据\n"
        "4. 下一步最小动作：一条可执行、可落盘的动作；若正文不可读，动作应是记录限制并转向公开替代来源\n"
        "不要问用户，不要把分享内容当事实结论。\n\n"
        f"当前项目：{project_label}\n"
        f"平台：{item.get('platform', 'unknown')}\n"
        f"内容意图：{intent}\n"
        f"访问状态：{access_status}\n"
        f"学习范围：{scope}\n"
        f"来源：{item.get('source', '')}\n"
        f"文本：{item.get('text', '')}\n"
        f"链接：{urls or '无'}\n"
        f"原始附件提示：{item.get('raw_hint', '') or '无'}\n"
    )
    media_paths: list[str] = []
    try:
        for row in item.get("media_files") or []:
            if isinstance(row, dict):
                path = str(row.get("text_preview") or "")
                if path and os.path.exists(path):
                    media_paths.append(path)
        acq = item.get("acquisition") if isinstance(item.get("acquisition"), dict) else {}
        for row in acq.get("media_files") or []:
            if isinstance(row, dict):
                path = str(row.get("text_preview") or "")
                if path and os.path.exists(path) and path not in media_paths:
                    media_paths.append(path)
    except Exception:
        media_paths = []

    if media_paths:
        vision_paths = list(media_paths)
        try:
            from ..content_tools import split_image_for_vision

            segment_dir = os.path.join(_workspace, "system", "media", "vision_segments")
            segmented: list[str] = []
            for path in media_paths[:4]:
                parts = split_image_for_vision(path, segment_dir)
                segmented.extend(parts or [path])
            if segmented:
                vision_paths = segmented[:8]
                if len(vision_paths) != len(media_paths):
                    logger.info(
                        f"[CONTENT] split {len(media_paths)} image(s) into {len(vision_paths)} vision segment(s)"
                    )
        except Exception as exc:
            logger.debug(f"[CONTENT] image split skipped: {exc}")
        vision_prompt = (
            "请读取用户分享的截图/图片内容，输出中文，严格包含：\n"
            "1. 图片可见正文：尽量完整转写图片中的文字；不确定处标 [看不清]\n"
            "2. 核心健康主张：逐条列出\n"
            "3. 初步分类：事实/推测/营销话术/需要验证的假设/非健康建议\n"
            "4. 证据风险：哪些只是图片说法，哪些需要查权威来源\n"
            "如果收到的是长截图切片，请按切片顺序合并理解，不要把每个切片当成独立文章。\n"
            "不要编造图片外的信息。\n\n"
            f"当前项目：{project_label}\n"
            f"原始文本提示：{item.get('text','')}\n"
        )
        content = ""
        try:
            if _adapter and hasattr(_adapter, "chat_with_images"):
                content = (_adapter.chat_with_images(vision_prompt, vision_paths, purpose="vision") or "").strip()
        except Exception as exc:
            logger.warning(f"[CONTENT] vision backend failed: {exc}")
            content = ""
        if not content or _is_internal_fallback_text(content):
            try:
                from ..content_tools import ocr_image_path

                parts = []
                for path in vision_paths[:8]:
                    result = ocr_image_path(path)
                    if result.status == "text_available" and result.text_preview:
                        parts.append(f"## {os.path.basename(path)}\n{result.text_preview}")
                if parts:
                    content = (
                        "1. 图片可见正文：\n"
                        + "\n\n".join(parts)
                        + "\n2. 学习定位：这是用户直接提供的截图内容，已作为可读外部材料记录。\n"
                        + "3. 风险与不确定性：OCR 可能有错字，后续健康主张仍需权威来源核验。\n"
                        + "4. 下一步最小动作：提取核心健康主张并纳入项目验证队列。"
                    )
            except Exception as exc:
                logger.debug(f"[CONTENT] OCR fallback failed: {exc}")
        if content and not _is_internal_fallback_text(content):
            access_status = "text_available"
            intent = "project_reference" if project else intent
            scope = "project" if project else scope
        else:
            content = (
                "1. 内容要点：已检测到图片/附件，但当前后端没有成功读取图片文字。\n"
                "2. 学习定位：先作为图片材料记录，不改动项目主线。\n"
                "3. 风险与不确定性：不能把图片提示当作正文；需要可读 OCR、复制正文或支持视觉的模型。\n"
                "4. 下一步最小动作：记录图片读取失败原因，并等待用户提供正文或配置可用视觉后端。"
            )
    elif access_status in {"access_limited", "link_only", "metadata_only"}:
        content = (
            "1. 内容要点：当前只能看到链接、卡片、附件提示或少量元数据，不能确认正文观点。\n"
            "2. 学习定位：先作为访问受限材料记录，不改动项目主线。\n"
            "3. 风险与不确定性：正文不可读，不能把标题、附件提示或平台卡片当证据，也不能用模型常识补成原文。\n"
            "4. 下一步最小动作：把限制写入学习日志，并等待用户补正文/截图/公开链接；同时只用公开替代来源做背景核验。"
        )
    else:
        visible_text = str(item.get("visible_body") or item.get("text") or "").strip()
        compact = re.sub(r"\s+", " ", visible_text)
        excerpt = compact[:420] + ("..." if len(compact) > 420 else "")
        keywords = []
        for pattern in (
            r"TMEM41B", r"CLCC1", r"APOB", r"VLDL", r"MASH", r"脂肪肝",
            r"内质网", r"磷脂翻转", r"健康建议", r"证据", r"机制",
        ):
            if re.search(pattern, compact, re.I):
                keywords.append(pattern.strip("\\r"))
        key_line = "、".join(list(dict.fromkeys(keywords))[:8]) or "未提取到稳定关键词"
        positioning = "项目参考材料" if intent in {"project_instruction", "project_reference"} else "普通学习材料"
        content = (
            f"1. 内容要点：已收到可读正文，初步关键词为：{key_line}。正文片段：{excerpt}\n"
            f"2. 学习定位：先标记为{positioning}；这一步只做收纳和初判，不把内容直接当事实结论。\n"
            "3. 风险与不确定性：用户转发正文可能是科普改写或二次传播，仍需查原始论文/权威来源核验；不能只凭推文语气判断可靠性。\n"
            "4. 下一步最小动作：把它转入项目验证队列，后续提取核心主张、证据类型、可核验来源和可能夸大点。"
        )
    try:
        from ..research_memory import record_user_signal, record_episode
        signal_kind = "user_idea" if intent in {"project_instruction", "project_reference"} else "external_learning"
        signal_project = project if signal_kind == "user_idea" else ""
        signal_text = content
        if signal_kind == "user_idea":
            signal_text = (
                f"用户新分享材料已消化，需纳入项目「{signal_project}」继续处理。\n"
                f"来源线索：{item.get('platform', 'unknown')} / {urls or item.get('source', '')}\n"
                f"消化结果：{content[:1800]}"
            )
        else:
            signal_text = f"外部内容学习：{item.get('text','')}\n\n消化结果：{content[:1200]}"
        record_user_signal(_workspace, signal_project, signal_text, kind=signal_kind)
        record_episode(
            _workspace,
            signal_project,
            "外部内容已消化",
            evidence=f"{item.get('platform', 'unknown')} / {intent} / {access_status} / {urls}",
            lesson=content[:260],
            risk="external_content_uncertain",
            links=item.get("urls") or [],
        )
    except Exception as exc:
        logger.debug(f"[CONTENT] memory record failed: {exc}")
    mark_content_processed(_workspace, item.get("id", ""), digest=content)
    path = os.path.join(_workspace, "system", "content_feed", "digests.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"## {datetime.now().isoformat(timespec='seconds')} {project_label} [{intent}/{access_status}]\n")
        f.write(content.strip() + "\n\n")
    if intent in {"general_learning", "access_limited"} or scope == "general":
        user_mind_dir = os.path.join(_workspace, "user", "partner_mind")
        os.makedirs(user_mind_dir, exist_ok=True)
        learning_path = os.path.join(user_mind_dir, "general_learning_journal.md")
        with open(learning_path, "a", encoding="utf-8") as f:
            f.write(f"## {datetime.now().isoformat(timespec='seconds')} [{item.get('platform','unknown')}] {intent}/{access_status}\n")
            f.write(content.strip() + "\n\n")
    if access_status in {"access_limited", "link_only", "metadata_only"}:
        logger.info(f"[CONTENT] access-limited content recorded without direct user notice: {item.get('id', '')}")
    if project and bool(item.get("should_nudge_project", False)):
        pool = await ensure_pool()
        should_wake_project = _should_wake_waiting_literature_project(project, item)
        try:
            from ..project_state import get_project_status, set_project_status

            current_status = get_project_status(_workspace, project)
            if current_status in {"waiting", "done", "cooling_down"} and should_wake_project:
                set_project_status(
                    _workspace,
                    project,
                    "active",
                    "用户新分享项目相关材料已消化，临时唤醒项目做材料吸收和汇报",
                )
                logger.info(
                    f"[CONTENT] reactivated project from {current_status} after user-shared content: {project}"
                )
        except Exception as exc:
            logger.debug(f"[CONTENT] failed to reactivate project after content digest: {exc}")
        try:
            compact_summary = content.strip()
            if len(compact_summary) > 900:
                compact_summary = compact_summary[:900].rstrip() + "..."
            await pool.put(MindEvent(
                type=EventType.REPORT,
                priority=2,
                payload={
                    "content": compact_summary,
                    "force_send": True,
                    "bypass_rate_limit": True,
                },
                source="content_digest:project_material_absorbed",
                parent_id=event.id,
            ))
        except Exception as exc:
            logger.debug(f"[CONTENT] failed to enqueue project material report: {exc}")
        if should_wake_project:
            await pool.put(MindEvent(
                type=EventType.PROJECT,
                priority=2,
                payload={"title": project, "step": 0, "reason": "user_shared_project_material"},
                source="content_digest:nudge_project",
                parent_id=event.id,
            ))
    logger.info(f"[CONTENT] digested {item.get('id')} for project={project_label}")
    logger.info(f"[MIND] DONE event_type=content_digest, id={event.id[:8]}")


async def _handle_content_patrol(event: MindEvent):
    """Controlled public-content patrol for instance 05 or enabled workspaces."""
    context = build_patrol_prompt_context(_workspace)
    prompt = (
        "你是 Partner 的受控内容巡游模块。你的任务是从公开入口获取少量 Agent 相关内容信号，"
        "用于后续学习和反思。\n"
        "严格限制：不绕过登录、不破解反爬、不批量抓取；如果页面不可访问就记录不可访问；"
        "不要把平台内容当事实结论，只输出可验证 hypothesis。\n\n"
        f"{context}\n\n"
        "如果可以访问公开页面，请最多提炼 3 条信号。严格输出 JSON 数组，每项字段：\n"
        "[{\"platform\":\"\", \"url\":\"\", \"title\":\"\", \"summary\":\"\", "
        "\"hypothesis\":\"\", \"risk\":\"\"}]\n"
        "如果无法访问，输出一项说明不可访问原因。不要输出 markdown。"
    )
    raw = ""
    try:
        if _adapter:
            raw = (_adapter.chat(prompt, purpose="project") or "").strip()
    except Exception as exc:
        logger.warning(f"[CONTENT] patrol LLM failed: {exc}")
    items = _parse_patrol_items(raw)
    if not items:
        items = [{
            "platform": "unknown",
            "url": "",
            "title": "内容巡游未形成可靠结果",
            "summary": "本轮巡游没有得到可用公开内容信号。",
            "hypothesis": "需要用户提供具体链接，或调整 sources.json 为可访问公开入口。",
            "risk": "no_reliable_content",
        }]
    recorded = []
    for row in items[:3]:
        text = (
            f"自主巡游内容：{row.get('title','')}\n"
            f"摘要：{row.get('summary','')}\n"
            f"假设：{row.get('hypothesis','')}\n"
            f"风险：{row.get('risk','')}\n"
            f"链接：{row.get('url','')}"
        )
        item = record_shared_content(
            _workspace,
            text=text,
            project=str(event.payload.get("project") or get_active_project_name()),
            sender="partner_content_patrol",
            source="autonomous_patrol",
            raw={"patrol": row},
        )
        if item:
            recorded.append(item)
    path = os.path.join(_workspace, "system", "content_feed", "patrol_runs.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"## {datetime.now().isoformat(timespec='seconds')}\n")
        f.write(_sanitize_user_report_text(raw)[:3000] if raw else "本轮无可靠输出")
        f.write("\n\n")
    pool = await ensure_pool()
    for item in recorded[:3]:
        await pool.put(MindEvent(
            type=EventType.CONTENT_DIGEST,
            priority=5,
            payload={"content_id": item.get("id", ""), "project": item.get("project", "")},
            source="content_patrol:digest",
            parent_id=event.id,
        ))
    logger.info(f"[CONTENT] patrol recorded {len(recorded)} items")
    logger.info(f"[MIND] DONE event_type=content_patrol, id={event.id[:8]}")


def _parse_patrol_items(raw: str) -> list[dict]:
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            out.append({k: str(v) for k, v in item.items()})
    return out


def get_active_project_name() -> str:
    try:
        from ..project_state import get_active
        return get_active(_workspace) or ""
    except Exception:
        return ""


# ── WAKE_UP ─────────────────────────────────────────────────────────


async def _handle_wake_up(event: MindEvent):
    """唤醒脉冲：检查 active_project.txt → 如有则创建 PROJECT 事件。

    没有活跃项目则什么都不做（不提示用户、不搜索）。
    """
    pool = await ensure_pool()
    logger.info(f"[WAKE_UP] 唤醒脉冲开始执行，池大小: {pool.qsize()}")
    _heartbeat_probe_ollama("wake_up")

    from ..project_state import recover_active_from_plan
    active_name = recover_active_from_plan(_workspace)
    digests = await _enqueue_open_content_digests(
        pool,
        source="wake_up:open_content_digest",
        active_project=active_name or "",
    )
    if digests:
        logger.info(f"[WAKE_UP] queued {digests} open content digest event(s)")
    if active_name:
        try:
            from ..project_state import get_project_status, set_project_status
            status = get_project_status(_workspace, active_name)
            open_idea = get_open_idea(_workspace, active_name)
            if status in {"waiting", "done"}:
                if open_idea and _is_new_idea_for_waiting_literature_project(active_name, open_idea):
                    set_project_status(
                        _workspace,
                        active_name,
                        "active",
                        "检测到未消化用户/老师信号，唤醒脉冲重新激活项目",
                    )
                    logger.info(f"[WAKE_UP] 活跃项目处于 {status}，但存在未消化用户信号，重新激活: {active_name}")
                elif open_idea:
                    logger.info(f"[WAKE_UP] 活跃项目处于 {status}，但 open_idea 早于阶段汇报暂停，跳过自动恢复: {active_name}")
                    logger.info(f"[MIND] DONE event_type=wake_up, id={event.id[:8]}")
                    return
                else:
                    if await _maybe_recover_empty_active_chain(
                        pool,
                        active_name,
                        status,
                        source="wake_up",
                        parent_id=event.id,
                    ):
                        logger.info(f"[MIND] DONE event_type=wake_up, id={event.id[:8]}")
                        return
                    logger.info(f"[WAKE_UP] 活跃项目处于 {status}，跳过自动恢复: {active_name}")
                    logger.info(f"[MIND] DONE event_type=wake_up, id={event.id[:8]}")
                    return
            if status in {"cooling_down"}:
                _cap_project_waiting_delay(pool, active_name, _cooling_down_delay_sec())
        except Exception as exc:
            logger.debug(f"[WAKE_UP] cooling-down delay cap failed: {exc}")
        if not _has_project_event(pool, active_name, include_running=True):
            if await _maybe_recover_empty_active_chain(
                pool,
                active_name,
                status,
                source="wake_up",
                parent_id=event.id,
            ):
                logger.info(f"[MIND] DONE event_type=wake_up, id={event.id[:8]}")
                return
            logger.info(f"[WAKE_UP] 从 active_project.txt 恢复项目: {active_name}")
            await _enqueue_project_if_absent(
                pool,
                active_name,
                priority=2,
                source="wake_up:resume_active",
                step=0,
            )
        else:
            logger.info(f"[WAKE_UP] 活跃项目已有运行/排队事件，跳过重复恢复: {active_name}")
    else:
        logger.info(f"[WAKE_UP] 无活跃项目，什么都不做")

    logger.info(f"[MIND] DONE event_type=wake_up, id={event.id[:8]}")
