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
import threading
import time as _time
import uuid
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Optional

import yaml

from .event_types import MindEvent, EventType, report
from ..core.delivery_queue import deliver, init as delivery_init, register_channel
from ..adapters.adapter import USER_FRIENDLY_PROGRESS_REPLY
from ..dialogue.outbound_policy import UNAVAILABLE_NOTICE, prefix_event_notice, send_template
from ..goal.acceptance_criteria import AcceptanceCriteriaGenerator
from ..skills.summarize_search import summarize_search_results
from ..knowledge.research_memory import (
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
from ..knowledge.research_guardrails import (
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
from ..dialogue.user_text_safety import has_internal_diff, strip_internal_diff
from ..utils.text_cleaner import clean_user_facing_text
from ..knowledge.content_feed import (
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
_pool = None  # deprecated — MindPool removed
_round_interval_sec: int = 60
_running_projects: set[str] = set()
_event_loop_instance = None  # asyncio event loop in dedicated thread

# 系统组件
_journal = None
_knowledge = None
_task_queue = None
_state_manager = None

# 推送回调：msg(str) -> None
_push_callback = None
_file_push_callback = None

# 规划循环检测：{project_title: consecutive_plan_only_count}
_plan_loop_counter: dict = {}

# 上一轮汇报内容缓存：{project_title: (findings_hash, next_action_hash)}
_last_report_cache: dict = {}
_stalled_repair_counter: dict = {}


def _append_to_chat_history(workspace: str, entry: dict):
    """Append a message entry to qq_chat_history.jsonl for GUI display."""
    import json as _json, os as _os
    history_path = _os.path.join(workspace, "state", "qq_chat_history.jsonl")
    try:
        _os.makedirs(_os.path.dirname(history_path), exist_ok=True)
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _update_active_plan_phase(workspace: str, phase_idx: int, status: str, **extra):
    """Update a single phase status in active_plan.json, plus optional extra fields."""
    import json as _json
    from pathlib import Path as _Path
    plan_path = _Path(workspace) / "state" / "active_plan.json"
    if not plan_path.exists():
        return
    try:
        plan = _json.loads(plan_path.read_text(encoding="utf-8"))
        if not isinstance(plan, dict):
            return
        phases = plan.get("phases") or []
        if 0 <= phase_idx < len(phases):
            phases[phase_idx]["status"] = status
            # Apply any extra fields (e.g. output_summary, error, elapsed)
            for k, v in extra.items():
                if v is not None:
                    phases[phase_idx][k] = v
            plan["current_phase_index"] = phase_idx + 1 if status == "completed" else phase_idx
            plan["last_heartbeat"] = __import__("datetime").datetime.now().isoformat()
            # Update overall plan status when execution starts or completes
            if status == "running" and plan.get("status") in ("active", "planning"):
                plan["status"] = "running"
            elif status == "completed":
                # Check if all phases are done
                all_done = all(p.get("status") in ("completed", "failed", "error", "skipped") for p in phases)
                if all_done:
                    plan["status"] = "completed"
                    # Save final pipeline snapshot
                    try:
                        import os as _os
                        created = str(plan.get("created_at", ""))
                        if created:
                            round_id = created.replace(":", "-").replace(".", "-")
                            snap_dir = _os.path.join(_os.path.dirname(_os.path.dirname(workspace)), "conversations", round_id)
                            snap_path = _os.path.join(snap_dir, "pipeline.json")
                            _os.makedirs(snap_dir, exist_ok=True)
                            with open(snap_path, "w", encoding="utf-8") as _sf:
                                _sf.write(_json.dumps(plan, indent=2, ensure_ascii=False))
                    except Exception:
                        pass
            plan_path.write_text(_json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _compact_title_from_request(text: str, fallback: str = "用户任务") -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    raw = re.sub(r"^(请|帮我|麻烦|能不能|可以帮我|你可以|我要|我想)\s*", "", raw, flags=re.I)
    raw = re.sub(r"[。！？!?；;，,]+", " ", raw).strip()
    raw = re.sub(r"[^\w\u4e00-\u9fff ._+-]+", "_", raw).strip(" _.-")
    if len(raw) > 48:
        raw = raw[:48].rstrip(" _.-")
    return raw or fallback


def _is_generic_project_title(title: str) -> bool:
    raw = str(title or "").strip()
    if not raw:
        return True
    normalized = raw.lower()
    generic = {"用户任务", "当前项目", "任务", "新任务", "task", "project", "report"}
    event_values = {item.value for item in EventType}
    return raw in generic or normalized in generic or raw in event_values or normalized in event_values


def _http_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Partner/0.7"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _http_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Partner/0.7"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _state_dir() -> str:
    path = os.path.join(_workspace or ".", "state")
    os.makedirs(path, exist_ok=True)
    return path


def _event_pipeline_path() -> str:
    return os.path.join(_state_dir(), "event_pipeline.jsonl")


def _append_event_pipeline(event_id: str, event_type: str, status: str, data: dict):
    """Write an event pipeline entry. TUI/GUI poll this file for real-time progress."""
    try:
        path = _event_pipeline_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            "event_id": event_id[:12],
            "type": event_type,
            "status": status,
            **data,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug(f"[PIPELINE] failed to write: {exc}")


def _dispatch_to_event_pipeline(entry):
    """Channel dispatcher: write a delivery entry to the event pipeline."""
    meta = entry.metadata or {}
    event_id = meta.get("event_id", "")
    event_type = meta.get("event_type", entry.kind)
    status = meta.get("status", "completed")
    data = {
        "ts": entry.timestamp,
        "seq": entry.sequence,
        "msg": entry.content[:200],
    }
    if meta.get("step"):
        data["step"] = meta["step"]
    _append_event_pipeline(event_id, event_type, status, data)


def _dispatch_to_dialog_history(entry):
    """Channel dispatcher: write a delivery entry to dialog history."""
    meta = entry.metadata or {}
    _append_assistant_dialog_history(
        content=entry.content,
        sender_id=meta.get("sender_id", ""),
        sender_name=meta.get("sender_name", "Partner"),
        message_id=meta.get("message_id", ""),
        source=meta.get("source", "desktop_gui"),
    )


_batch_plan_inflight: set[str] = set()  # task_ids currently being processed by _handle_batch_plan_event
_batch_plan_recently_completed: dict[str, float] = {}  # title→timestamp, 60s content-dedup window
_recent_report_hashes: dict[str, float] = {}  # md5(content) -> timestamp, for dedup in _enqueue_visible_report
_DEDUP_WINDOW_SEC = 5.0
# Message-level dedup: text → timestamp, prevents duplicate BATCH_PLAN creation
# when two USER_MESSAGE events with the same text arrive from different sources
_recent_user_messages: dict[str, float] = {}  # text_hash → timestamp
_USER_MSG_DEDUP_SEC = 30.0


async def _enqueue_visible_report(content: str, event_type: EventType | str, *,
                                  event_kind: str = "", priority: int = 3,
                                  source: str = "", parent_id: str = "",
                                  force_send: bool = True,
                                  bypass_rate_limit: bool = False,
                                  files: list[str] | None = None) -> None:
    plain = re.sub(r"^【[^】]+】", "", str(content or "")).strip()
    if plain in {"思考中.......", "思考中......", "思考中……", "Thinking..."}:
        logger.info("[REPORT] skipped thinking-only event receipt: %s/%s", event_type, event_kind)
        return
    # Also skip THINKING_NOTICE ("[进度] 正在思考...") — these come from task_queue lifeline,
    # not from progress events. The first one is already delivered as reply_to_user.
    if plain.rstrip(".") == "[进度] 正在思考":
        logger.debug("[REPORT] skipped THINKING_NOTICE duplicate")
        return
    # Message deduplication: skip if same hash was sent within DEDUP_WINDOW_SEC
    content_hash = hashlib.md5(str(content or "").encode("utf-8")).hexdigest()
    now = _time.time()
    last_sent = _recent_report_hashes.get(content_hash)
    if last_sent is not None and (now - last_sent) < _DEDUP_WINDOW_SEC:
        logger.debug("[REPORT] dedup skipped duplicate report: hash=%s age=%.2fs", content_hash, now - last_sent)
        return
    _recent_report_hashes[content_hash] = now
    # Prune stale entries every 50 reports to prevent unbounded growth
    if len(_recent_report_hashes) > 200:
        cutoff = now - _DEDUP_WINDOW_SEC
        _recent_report_hashes.clear()
    text = prefix_event_notice(
        content,
        event_type.value if isinstance(event_type, EventType) else str(event_type),
        event_kind=event_kind,
        workspace=_workspace,
    )
    if not text:
        return
    # Send progress message to user via registered push callback
    if _push_callback is not None:
        try:
            _push_callback(text)
        except Exception as exc:
            logger.debug("[REPORT] push callback failed: %s", exc)
    else:
        logger.debug("[REPORT] no push callback registered, progress not sent")


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
    # MindPool removed — visible growth notices deferred to Harness
    logger.debug("[REPORT] visible growth notice recorded (Harness handles dispatch)")


def _clip(text: str, limit: int) -> str:
    """Clip text to max length, stripping internal noise first."""
    text = (text or "").strip()
    # Strip Hermes model normalization warning before clipping
    text = re.sub(r"(?im)^.*⚠️?\s*Normalized model\s+.*to\s+.*for\s+.*$", "", text).strip()
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
            "PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE",
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
    EventType.BATCH_PLAN,
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
    EventType.FILE_INSPECTION,
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
    """Return whether the same project already has running work.
    MindPool removed — only check _running_projects."""
    title = (title or "").strip()
    if include_running:
        if title:
            if title in _running_projects:
                return True
        elif _running_projects:
            return True
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
    # MindPool removed — project events deferred to Harness
    logger.debug(f"[PROJECT] enqueue skipped (MindPool removed): {title}")
    return False


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
        from ..projects.project_state import read_project_brief, read_state_md

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


async def _maybe_recover_empty_active_chain(pool=None, active_name: str = "", status: str = "", *,
                                           source: str = "", parent_id: str = "") -> bool:
    """Recover the common failure shape: active state promises NEXT but pool is empty."""
    if os.getenv("PARTNER_ENABLE_EMPTY_CHAIN_RECOVERY", "").strip().lower() not in {"1", "true", "yes", "on"}:
        logger.info(f"[RECOVERY] empty-chain recovery disabled by default: {active_name}")
        return False
    if not active_name or _has_project_event(pool, active_name, include_running=True):
        return False
    plan = _read_active_plan_snapshot()
    plan_status = str(plan.get("status") or plan.get("project_status") or "").strip().lower()
    heartbeat = str(plan.get("heartbeat_summary") or "").strip()
    plan_active = _active_plan_matches_project(plan, active_name) and plan_status in {"active", "running", "in_progress"}
    try:
        from ..projects.project_state import get_active

        marker_active = (get_active(_workspace) or "").strip()
    except Exception:
        marker_active = ""
    if not plan_active and marker_active != active_name:
        logger.info(f"[RECOVERY] skip empty-chain recovery without active marker/plan: {active_name}")
        return False
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
        from ..projects.project_state import append_log, set_project_status

        set_project_status(_workspace, active_name, "active", f"{source}: empty mind pool recovery")
        append_log(
            _workspace,
            active_name,
            f"RECOVERY: active state had actionable NEXT but mind_pool was empty. source={source}; next={next_action}",
        )
    except Exception as exc:
        logger.debug(f"[RECOVERY] state mark failed for {active_name}: {exc}")
    # MindPool removed — empty chain recovery deferred to Harness
    logger.warning(f"[RECOVERY] empty-chain recovery for {active_name}: {next_action[:160]} (Harness handles)")
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


def _user_prefers_pdf() -> bool:
    """Check if user prefers PDF output from habits database.
    Uses load_habits() which merges SQLite with DEFAULT_HABITS.
    """
    try:
        from ..meta.learning import load_habits
        habits = load_habits("default")
        pref = habits.get("prefer_pdf")
        if pref is not None:
            if isinstance(pref, dict):
                return bool(pref.get("value", False) or pref.get("confidence", 0) > 0.5)
            return bool(pref)
        return True  # DEFAULT_HABITS has prefer_pdf=True
    except Exception:
        return True  # Default to True on error


def _user_prefers_summary() -> bool:
    """Check if user prefers final summaries from habits database."""
    try:
        from ..meta.learning import get_habit
        pref = get_habit("default", "prefer_summary")
        if pref is not None:
            if isinstance(pref, dict):
                return bool(pref.get("value", False) or pref.get("confidence", 0) > 0.5)
            return bool(pref)
        return True  # Default: prefer summary
    except Exception:
        return True


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
    from ..projects.project_state import get_project_dir

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
    from ..projects.project_state import get_project_dir

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
    """保留兼容 — MindPool 已移除，无延迟队列可调整。"""
    return False


def _has_reflection_event(pool, project: str) -> bool:
    """保留兼容 — MindPool 已移除，无队列可扫描。"""
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
    from ..projects.project_state import get_project_dir

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
    from ..projects.project_state import get_project_dir

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
    from ..projects.project_state import get_project_dir, load_project_guardrail, read_project_brief
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
    from ..projects.project_state import format_project_guardrail_for_prompt, load_project_guardrail, read_project_brief

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
    if not any([parsed["step_done"], parsed["findings"], parsed["next_action"],
                parsed["state_delta"], parsed["action"], parsed["artifact_content"]]):
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
        os.path.join(_workspace, "state", "user"),
        os.path.join(_workspace, "projects"),
        os.path.join(_workspace, "projects", "projects"),
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
        from ..projects.project_state import read_project_contract
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
    from ..projects.project_state import get_project_dir, read_state_md

    project_dir = get_project_dir(workspace, title)
    state_md = read_state_md(workspace, title)
    log_tail = _tail_text_file(os.path.join(project_dir, "trace_detail.md"), max_lines=36)
    runtime_tail = _tail_text_file(os.path.join(workspace, "state/record", "instance.log"), max_lines=18)
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
        from ..monitoring.runtime_monitor import compact_runtime_context

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
    ".png", ".jpg", ".jpeg", ".webp", ".txt", ".md",
    ".mp4", ".m4v", ".mov", ".webm",
}


def _is_diagnostic_artifact(path: str) -> bool:
    name = os.path.basename(path or "")
    lower = name.lower()
    parts = set(os.path.normpath(path or "").split(os.sep))
    if name in {"_missing_artifacts.md", "_error_report.md"}:
        return True
    if lower.startswith("_step_") and lower.endswith(".result.json"):
        return True
    if lower in {"batch_plan_status.md", "batch_plan_context.md", "batch_research_notes.md"}:
        return True
    if "fallback" in lower or "missing_artifacts" in lower or "error_report" in lower:
        # Remediation fallback files that match a delivery format extension
        # (.csv, .xlsx, .pdf, .md) should NOT be filtered — they are the
        # best available output when the primary pipeline fails.
        ext = os.path.splitext(lower)[1]
        if ext in (".csv", ".xlsx", ".xls", ".pdf", ".md", ".txt", ".pptx", ".docx"):
            return False
        return True
    if "fallbacks" in parts:
        return True
    return False


def _looks_like_diagnostic_content(text: str) -> bool:
    body = (text or "").lstrip()
    if not body:
        return False
    if re.match(r"^#\s+(Error Report|Partial Artifact Report|Missing Artifacts)", body, re.I):
        return True
    return bool(re.search(
        r"(harness_loop_guard_terminated|Manual Recovery|Missing Artifacts|Accepted Fallbacks|Batch planner fallback|BatchPlanner 输出非 JSON|PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE)",
        body[:1600],
        re.I,
    ))


def _harness_result_is_diagnostic_only(result: object) -> bool:
    parsed = getattr(result, "parsed", None)
    if not isinstance(parsed, dict) or not parsed:
        return False
    status = str(parsed.get("delivery_status") or "").strip().lower()
    if status not in {"failed", "partial"}:
        return False
    plan = getattr(result, "plan", None) or []
    step_results = getattr(result, "step_results", None) or {}
    files = _resolve_one_shot_output_files(
        os.path.dirname(str(parsed.get("files") or "")) if str(parsed.get("files") or "") else (_workspace or "."),
        parsed,
        allow_workspace_fallback=False,
    )
    if files:
        return False
    return not plan and not step_results


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
        os.path.join(_workspace, "state", "user"),
        os.path.join(_workspace, "projects", "projects"),
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


def _append_reportlab_table(story: list, table_rows: list[list[str]],
                            body_style: Any, page_width: float) -> None:
    """Render collected markdown table rows as a ReportLab Table into the story."""
    if not table_rows:
        return
    from reportlab.platypus import Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    col_count = max(len(r) for r in table_rows) if table_rows else 1
    col_width = page_width / max(col_count, 1)
    # Unify cell text: ensure first row (header) uses body_style Paragraphs
    styled_data: list = []
    for idx, row in enumerate(table_rows):
        padded = row + [""] * (col_count - len(row))
        styled_row = []
        for cell in padded:
            escaped = _escape_pdf_text(cell)
            p = Paragraph(escaped, body_style)
            styled_row.append(p)
        styled_data.append(styled_row)
    tbl = Table(styled_data, colWidths=[col_width] * col_count, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3a5c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), body_style.fontName),
        ("FONTSIZE", (0, 0), (-1, -1), body_style.fontSize),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8fc")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.18 * cm))


def _write_user_pdf_report(title: str, source_name: str, body: str, source_dir: str = "") -> str:
    """Render a simple text artifact to a user-facing PDF report."""
    import os as _pdf_os
    # Prevent OpenBLAS OOM in WSL
    _pdf_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    _pdf_os.environ.setdefault("OMP_NUM_THREADS", "1")
    text = (body or "").strip()
    if not text:
        return ""
    text = re.sub(r"\[(?:hypo|hypothesis)\]", "基于内部知识（未经实时验证）", text, flags=re.I)
    text = re.sub(r"\b(?:hypo|hypothesis)\s*[:：]", "基于内部知识（未经实时验证）：", text, flags=re.I)
    # Convert RGBA images to RGB for reportlab compatibility
    from PIL import Image as PILImage
    def _ensure_rgb(img_path):
        try:
            pi = PILImage.open(img_path)
            if pi.mode == 'RGBA':
                rgb = PILImage.new('RGB', pi.size, (255, 255, 255))
                rgb.paste(pi, mask=pi.split()[3])
                tmp = img_path + '.rgb.png'
                rgb.save(tmp, 'PNG')
                return tmp
        except Exception:
            pass
        return img_path

    # Insert synthetic data warning if content mentions synthetic/simulated data
    if re.search(r"合成数据|synthetic|simulated data", text, re.IGNORECASE):
        warning = (
            "\n\n> **注意**：本实验使用合成数据验证代码流程，结果不代表真实生物学性能，"
            "仅用于方法对比。\n\n"
        )
        text = warning + text
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Image as PlatypusImage
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from reportlab.lib import colors
    except Exception as exc:
        logger.debug(f"[REPORT] reportlab unavailable for PDF report: {exc}")
        return ""

    font_name = ""
    try:
        for candidate in (
            "C:\\Windows\\Fonts\\msyh.ttc",
            "C:\\Windows\\Fonts\\msyh.ttf",
            "C:\\Windows\\Fonts\\simsun.ttc",
            "C:\\Windows\\Fonts\\simhei.ttf",
            "C:\\Windows\\Fonts\\Deng.ttf",
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

    report_dir = os.path.join(_workspace, "state", "user", "reports", _safe_report_name(title))
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
    # ── Line-by-line processing with markdown table support ──────────
    table_rows: list[list[str]] = []  # buffer for collecting table lines
    in_table = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            # Flush any pending table
            if in_table and table_rows:
                _append_reportlab_table(story, table_rows, body_style, page_width)
                table_rows = []
                in_table = False
            story.append(Spacer(1, 0.18 * cm))
            continue

        # ── Image line ───────────────────────────────────────────────
        image_match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line)
        if image_match:
            if in_table and table_rows:
                _append_reportlab_table(story, table_rows, body_style, page_width)
                table_rows = []
                in_table = False
            alt = image_match.group(1).strip()
            image_path = image_match.group(2).strip().strip("\"'")
            resolved_image_path = _resolve_pdf_report_image_path(image_path, source_dir=source_dir)
            if resolved_image_path:
                try:
                    resolved_image_path = _ensure_rgb(resolved_image_path)
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

        # ── Table row ────────────────────────────────────────────────
        if line.startswith("|"):
            # Parse markdown table row into cells
            cells = [c.strip() for c in line.strip("|").split("|")]
            # Check if this is a separator row (|---|)
            if re.match(r"^[-:\s]+$", "".join(cells)):
                continue  # skip separator row
            table_rows.append(cells)
            in_table = True
            continue
        else:
            if in_table and table_rows:
                _append_reportlab_table(story, table_rows, body_style, page_width)
                table_rows = []
                in_table = False

        # ── Heading ──────────────────────────────────────────────────
        if line.startswith("#"):
            line = re.sub(r"^#+\s*", "", line).strip()
            story.append(Paragraph(_escape_pdf_text(line), heading_style))
        else:
            story.append(Paragraph(_escape_pdf_text(line), body_style))

    # Flush any remaining table at end of content
    if in_table and table_rows:
        _append_reportlab_table(story, table_rows, body_style, page_width)
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
    # Only the user's wording may require a concrete file type. Selector event
    # labels such as "deliver_existing_csv" are hints, not a user contract.
    text = user_request or ""
    required: set[str] = set()
    if re.search(r"(excel|xlsx|xls|工作簿)", text, re.I):
        required.update({".xlsx", ".xls"})
    if re.search(r"\bcsv\b|逗号分隔", text, re.I):
        required.add(".csv")
    if re.search(r"表格|数据表|表\b", text, re.I):
        required.update({".csv", ".xlsx", ".xls"})
    if re.search(r"\bpptx?\b|幻灯片|PPT", text, re.I):
        required.add(".pptx")
    if re.search(r"\bpdf\b", text, re.I):
        required.add(".pdf")
    elif re.search(r"(报告|report)", text, re.I) and not re.search(r"(markdown|md|\.md|仅\s*md|只要\s*md)", text, re.I):
        required.add(".pdf")
    if event_type == EventType.PDF_REPORT.value:
        required.add(".pdf")
    if re.search(r"(图片|截图|图像|png|jpg|jpeg|webp)", text, re.I):
        required.update({".png", ".jpg", ".jpeg", ".webp"})
    if re.search(r"(word|docx)", text, re.I):
        required.add(".docx")
    return required


def _align_expected_artifacts_with_required_exts(expected: object, required_exts: set[str]) -> list[dict]:
    items = [item for item in (expected or []) if isinstance(item, dict)]
    if not required_exts:
        return items[:8]
    normalized_exts = sorted({ext if str(ext).startswith(".") else f".{ext}" for ext in required_exts})
    patterns = [f"*{ext}" for ext in normalized_exts]
    aligned: list[dict] = []
    for item in items:
        kind = str(item.get("type") or "file").strip().lower()
        if kind != "file":
            aligned.append(item)
            continue
        pattern = str(item.get("pattern") or item.get("name") or "").strip()
        ext = os.path.splitext(pattern.split(",", 1)[0].strip())[1].lower()
        if ext and ext not in normalized_exts:
            continue
        aligned.append(item)
    aligned.append({
        "type": "file",
        "pattern": ", ".join(patterns),
        "description": f"当前目标格式文件（可接受扩展名：{', '.join(normalized_exts)}）",
        "required": True,
    })
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in aligned:
        key = (str(item.get("type") or ""), str(item.get("pattern") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:8]


def _has_file_expected_artifact(*groups: object) -> bool:
    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "file").strip().lower() == "file":
                return True
    return False


def _requested_output_groups(user_request: str) -> dict[str, set[str]]:
    text = user_request or ""
    groups: dict[str, set[str]] = {}
    if re.search(r"(excel|xlsx|xls|工作簿)", text, re.I):
        groups["spreadsheet"] = {".xlsx", ".xls"}
    if re.search(r"\bcsv\b|逗号分隔", text, re.I):
        groups["csv"] = {".csv"}
    if re.search(r"表格|数据表|表\b", text, re.I):
        groups["table"] = {".csv", ".xlsx", ".xls", ".md"}
    if re.search(r"(绘图|画图|图表|图片|截图|图像|可视化|plot|chart|png|jpg|jpeg|webp)", text, re.I):
        groups["image"] = {".png", ".jpg", ".jpeg", ".webp"}
    if re.search(r"\bpptx?\b|幻灯片|PPT", text, re.I):
        groups["slides"] = {".pptx"}
    if re.search(r"\bpdf\b", text, re.I):
        groups["pdf"] = {".pdf"}
    elif re.search(r"(报告|report)", text, re.I):
        if re.search(r"(markdown|md|\.md|仅\s*md|只要\s*md)", text, re.I):
            groups["report"] = {".md"}
        else:
            groups["report"] = {".pdf"}
    if re.search(r"(word|docx)", text, re.I):
        groups["doc"] = {".docx"}
    return groups


def _delivery_requirements_satisfied(user_request: str, files: list[str]) -> bool:
    groups = _requested_output_groups(user_request)
    if not groups:
        return bool(files)
    return all(
        any(_file_satisfies_output_group(path, group, wanted) for path in files if path)
        for group, wanted in groups.items()
    )


def _has_unfinished_requested_output(user_request: str, files: list[str], group: str) -> bool:
    groups = _requested_output_groups(user_request)
    if group not in groups:
        return False
    return not any(_file_satisfies_output_group(path, group, groups[group]) for path in files if path)


def _file_satisfies_output_group(path: str, group: str, wanted_exts: set[str]) -> bool:
    ext = os.path.splitext(path or "")[1].lower()
    if ext not in wanted_exts:
        return False
    if group != "report":
        return True
    name = os.path.basename(path or "").lower()
    if ext in {".pdf", ".docx"}:
        return True
    return bool(re.search(r"(report|报告)", name, re.I))


def _final_report_delivery_satisfied(user_request: str, event_type: EventType, files: list[str]) -> bool:
    groups = _requested_output_groups(user_request)
    exts = {os.path.splitext(path)[1].lower() for path in files if path}
    if event_type == EventType.PDF_REPORT and ".pdf" in exts and ({"report", "pdf"} & set(groups)):
        return True
    return _delivery_requirements_satisfied(user_request, files)


def _batch_plan_prefers_pdf(user_request: str, required_exts: set[str]) -> bool:
    """Determine if a batch plan should include PDF output.

    Checks the habit system: if prefer_pdf is set in learning habits,
    the user/system wants PDF as the default final delivery format.
    Returns False if explicit structured-data formats are required.
    """
    if any(ext in required_exts for ext in {".csv", ".xlsx", ".xls", ".pptx", ".docx", ".png", ".jpg", ".jpeg", ".webp"}):
        return False
    if _user_prefers_pdf():
        return True
    return False


def _normalize_batch_expected_artifacts_for_request(expected: object, user_request: str) -> list[dict]:
    items = [item for item in (expected or []) if isinstance(item, dict)]
    if _user_prefers_pdf():
        return items
    cleaned: list[dict] = []
    for item in items:
        pattern = str(item.get("pattern") or item.get("name") or "").lower()
        desc = str(item.get("description") or "").lower()
        if ".pdf" in pattern or "pdf" in desc:
            continue
        cleaned.append(item)
    return cleaned


def _publish_batch_plan_pdf_if_needed(title: str, user_request: str, parsed: dict | None,
                                      task_dir: str, required_exts: set[str]) -> tuple[dict, set[str]]:
    """No-op: PDF publishing has been disabled in favor of LLM-based summarization."""
    return parsed or {}, set(required_exts or set())


def _task_text_file_summaries_for_dir(root: str) -> list[dict]:
    rows: list[dict] = []
    if not root or not os.path.isdir(root):
        return rows
    skip_parts = {".git", "__pycache__", "state", "logs", "state/record"}
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_parts and not d.startswith(".")]
        for name in names:
            path = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            if ext not in {".md", ".txt", ".csv", ".json"}:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            preview = ""
            if ext in {".md", ".txt", ".csv", ".json"} and size <= 2_000_000:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        preview = f.read(1600)
                except OSError:
                    preview = ""
            rows.append({
                "path": path,
                "relative_path": os.path.relpath(path, root),
                "size": size,
                "preview": preview,
                "diagnostic": _is_diagnostic_artifact(path) or _looks_like_diagnostic_content(preview),
            })
    return rows


def _batch_delivery_failure_reasons(task: object, required_exts: set[str], delivery_dir: str) -> list[str]:
    reasons: list[str] = []
    log_path = os.path.join(str(getattr(task, "working_dir", "") or ""), "task_log.jsonl")
    log_text = ""
    try:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-240:]
            log_text = "".join(lines)
    except OSError:
        log_text = ""
    if re.search(r"external_agent_cli.*fallback|external_agent_cli.*failed|hermes CLI returned no usable output|Connection error", log_text, re.I):
        reasons.append("Hermes/外部 Agent 子调用没有拿到可用结果，部分步骤只能走 fallback")
    if re.search(r"artifact_validator|Missing Artifacts|missing_artifacts", log_text, re.I):
        reasons.append("ArtifactValidator 检查发现期望交付物缺失")
    summaries = _task_text_file_summaries_for_dir(delivery_dir)
    valid_text = [
        row for row in summaries
        if not bool(row.get("diagnostic"))
        and int(row.get("size") or 0) >= 200
        and os.path.splitext(str(row.get("path") or ""))[1].lower() in {".md", ".txt"}
    ]
    if ".pdf" in required_exts and valid_text:
        reasons.append("已有可转换正文，但最终 PDF 生成/定位/发送阶段没有产出可验证 PDF")
    elif ".pdf" in required_exts:
        reasons.append("没有发现可转换为 PDF 的有效正文文件")
    if not reasons:
        reasons.append("最终交付检查没有找到满足要求的真实文件")
    return reasons[:4]


def _resolve_one_shot_output_files(project_dir: str, parsed: dict | None,
                                   artifact_path: str = "",
                                   since_ts: float | None = None,
                                   required_exts: set[str] | None = None,
                                   allow_workspace_fallback: bool = True,
                                   extra_scan_roots: list[str] | None = None) -> list[str]:
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
        user_root = os.path.abspath(os.path.join(_workspace or project_dir, "state", "user"))
        deliverables_root = os.path.abspath(os.path.join(_workspace or project_dir, "deliverables"))
        artifacts_root = os.path.abspath(os.path.join(_workspace or project_dir, "30_artifacts"))
        allowed = False
        try:
            common = os.path.commonpath([os.path.abspath(project_dir), path])
            allowed = common == os.path.abspath(project_dir)
        except ValueError:
            allowed = False
        # Also allow files from any extra_scan_root
        if not allowed and extra_scan_roots:
            for root in extra_scan_roots:
                try:
                    root_abs = os.path.abspath(root)
                    if os.path.commonpath([root_abs, path]) == root_abs:
                        allowed = True
                        break
                except ValueError:
                    continue
        if not allow_workspace_fallback and not allowed:
            return
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
        if _is_diagnostic_artifact(path):
            return
        if os.path.exists(path) and os.path.isfile(path) and path not in seen:
            seen.add(path)
            paths.append(path)

    raw_files = (parsed or {}).get("files") or (parsed or {}).get("file_paths") or ""
    if isinstance(raw_files, (list, tuple, set)):
        for part in raw_files:
            add_path(str(part))
    else:
        files_text = str(raw_files or "")
        for part in re.split(r"[;\n，,]+", files_text):
            add_path(part)
    if artifact_path:
        add_path(artifact_path)

    if paths:
        return paths[:6]

    cutoff = float(since_ts or 0)
    recent: list[str] = []
    skip_parts = {".git", "__pycache__", "state/record", "state", "logs"}
    scan_roots = [project_dir]
    workspace_root = os.path.abspath(_workspace or project_dir)
    if extra_scan_roots:
        for root in extra_scan_roots:
            root_abs = os.path.abspath(root)
            if root_abs not in scan_roots:
                scan_roots.append(root_abs)
    if allow_workspace_fallback:
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
                if _is_diagnostic_artifact(path):
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
                if _is_diagnostic_artifact(path):
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
    recent.sort(key=lambda p: (
        # Priority: PDF=0, MD=1, HTML=2, PNG/SVG=3, CSV=4, rest=5
        # Within same priority, newer files first (negative mtime)
        0 if p.endswith(".pdf") else
        1 if p.endswith(".md") else
        2 if p.endswith(".html") else
        3 if p.endswith((".png", ".svg", ".jpg", ".jpeg")) else
        4 if p.endswith((".csv", ".tsv", ".xlsx")) else
        5,
        -os.path.getmtime(p),
    ))
    logger.debug("[DELIVERY_SCAN] recent files found: %s", [os.path.basename(p) for p in recent])
    for path in recent:
        add_path(path)
    return paths[:6]


def _postprocess_agent_content(content: str, required_exts: set[str] | None = None) -> str:
    """Clean agent output before writing to disk.

    Strips internal markers (diff markers, tool noise, timeout denial messages,
    step-result JSON, file paths).  If *required_exts* requests a tabular format
    (.csv / .xls / .xlsx) and the content resembles a markdown pipe table
    rather than actual CSV, the first pipe-table is extracted and converted to
    CSV-formatted text automatically.

    Args:
        content: Raw agent output that may contain internal machinery noise.
        required_exts: Set of file extensions the caller expects (e.g. {".csv", ".xlsx"}).

    Returns:
        Cleaned content safe for user-facing delivery.
    """
    if not content:
        return content

    # 1. Strip internal markers, diff artifacts, JSON dumps, file paths, etc.
    cleaned = clean_user_facing_text(content)

    # 2. If tabular format is requested and content looks like markdown, convert
    tabular_exts = {".csv", ".xls", ".xlsx"}
    if required_exts and (required_exts & tabular_exts):
        # Check if content is markdown tables (has pipe patterns) not actual CSV
        has_csv_commas = bool(re.search(r'^[^|]*,[^|]*$', cleaned.strip(), re.M))
        has_pipe_tables = "|" in cleaned
        if has_pipe_tables and not has_csv_commas:
            # Try extracting pipe tables and converting to CSV text
            try:
                from ..utils.format_converter import _find_md_tables
                tables = _find_md_tables(cleaned)
                if tables:
                    # Use the largest table
                    table = max(tables, key=len)
                    # Build CSV text from table rows
                    import csv, io
                    output = io.StringIO()
                    writer = csv.writer(output)
                    for row in table:
                        writer.writerow(row)
                    csv_text = output.getvalue()
                    output.close()
                    if csv_text.strip():
                        logger.info(
                            "[POSTPROCESS] converted markdown pipe-table (%d rows) to CSV text",
                            len(table),
                        )
                        return csv_text
            except Exception as exc:
                logger.debug("[POSTPROCESS] md-table-to-csv conversion failed: %s", exc)

        # 3. Fallback: if content has key-value pairs (**key**: value lines),
        #    try converting to a 2-column CSV (key, value)
        if not has_csv_commas:
            kv_lines = re.findall(r'^\s*\*\*([^*]+)\*\*\s*:\s*(.+)$', cleaned.strip(), re.M)
            if kv_lines and len(kv_lines) >= 3:
                logger.info(
                    "[POSTPROCESS] found %d key-value pairs, converting to CSV",
                    len(kv_lines),
                )
                import csv, io
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(["指标", "数值"])
                for key, val in kv_lines:
                    writer.writerow([key.strip(), val.strip()])
                csv_text = output.getvalue()
                output.close()
                if csv_text.strip():
                    return csv_text

    return cleaned


def _push_one_shot_output_files(project_dir: str, parsed: dict | None,
                                artifact_path: str = "",
                                since_ts: float | None = None,
                                required_exts: set[str] | None = None,
                                allow_workspace_fallback: bool = True,
                                extra_scan_roots: list[str] | None = None) -> tuple[bool, list[str]]:
    """Best-effort QQ file push for direct one-shot deliverables."""
    files = _resolve_one_shot_output_files(
        project_dir,
        parsed,
        artifact_path,
        since_ts,
        required_exts=required_exts,
        allow_workspace_fallback=allow_workspace_fallback,
        extra_scan_roots=extra_scan_roots,
    )
    logger.info(
        "[REPORT] one-shot output file candidates: %s callback=%s required_exts=%s allow_workspace_fallback=%s",
        [os.path.basename(p) for p in files],
        bool(_file_push_callback),
        sorted(required_exts or []),
        allow_workspace_fallback,
    )
    # Validate content format — .csv files that contain markdown need conversion
    if files and required_exts and (required_exts & {".csv", ".xlsx", ".xls"}):
        validated: list[str] = []
        for fp in files:
            ext = os.path.splitext(fp)[1].lower()
            if ext == ".csv":
                try:
                    # Fix CSV encoding: ensure utf-8-sig for Excel compatibility
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        raw = f.read()
                    # Attempt to fix encoding issues (latin1 double-encoding)
                    try:
                        fixed = raw.encode("latin1").decode("utf-8", errors="replace")
                        if fixed != raw and len(fixed) > len(raw) * 0.5:
                            raw = fixed
                    except Exception:
                        pass
                    with open(fp, "w", encoding="utf-8-sig") as f:
                        f.write(raw)
                    # Check if content is actually markdown, not CSV
                    sample = raw[:500]
                    if "|" in sample and not re.search(r'^[^|]*,[^|]*$', sample.strip(), re.M):
                        logger.info(
                            "[REPORT] %s contains markdown, not CSV; attempting conversion",
                            fp,
                        )
                        from ..utils.format_converter import try_md_table_to_csv
                        csv_path = try_md_table_to_csv(fp, os.path.dirname(fp) or project_dir)
                        if csv_path and os.path.isfile(csv_path):
                            validated.append(csv_path)
                            continue
                except Exception:
                    pass
                # Ensure utf-8-sig encoding even if no conversion needed
                try:
                    with open(fp, "r", encoding="utf-8-sig", errors="replace") as f:
                        _ = f.read()
                except Exception:
                    pass
            validated.append(fp)
        files = validated
    if not files:
        # If required_exts asks for CSV/XLSX but no files found, try converting
        # markdown tables to CSV as a fallback
        if required_exts and (required_exts & {".csv", ".xlsx", ".xls"}):
            try:
                from ..utils.format_converter import try_md_table_to_csv
                md_files = _resolve_one_shot_output_files(
                    project_dir,
                    parsed,
                    artifact_path,
                    since_ts,
                    required_exts={".md"},
                    allow_workspace_fallback=allow_workspace_fallback,
                    extra_scan_roots=extra_scan_roots,
                )
                for md_path in md_files:
                    csv_path = try_md_table_to_csv(md_path, os.path.dirname(md_path) or project_dir)
                    if csv_path and os.path.isfile(csv_path):
                        logger.info("[REPORT] md→csv conversion produced %s from %s", csv_path, md_path)
                        files.append(csv_path)
                        break
            except Exception as exc:
                logger.warning("[REPORT] md→csv conversion failed: %s", exc)
        if not files:
            return False, files
    if _file_push_callback is None:
        logger.warning("[REPORT] one-shot file push skipped: no file push callback registered")
        return False, files
    sent = False
    for path in files:
        for _retry in range(3):
            try:
                with open(path, "rb") as f:
                    data = f.read()
                label = os.path.basename(path)
                ok = _file_push_callback(data, os.path.basename(path), label)
                logger.info("[REPORT] one-shot file push result: %s ok=%s (attempt %d/3)", os.path.basename(path), ok, _retry + 1)
                if ok:
                    sent = True
                    break
                if _retry < 2:
                    import time as _t
                    _t.sleep(2)
            except Exception as exc:
                logger.warning(f"[REPORT] one-shot file push failed for {path}: {exc}")
                if _retry < 2:
                    import time as _t
                    _t.sleep(2)
    return sent, files


def _latest_stage_report_outputs(title: str) -> dict:
    """Find the latest user-facing stage report outputs for a project."""
    safe_title = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", title).strip("_") or "project"
    report_dir = os.path.join(_workspace, "state", "user", "reports", safe_title)
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
    show_dir = os.path.join(_workspace, "state", "user", "showcase", safe_title)
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
                              files: list[str] | None = None,
                              files_pushed: bool | None = None) -> str:
    parsed = parsed or {}
    llm_text = _format_event_completion_receipt_with_llm(
        title,
        event_type,
        parsed,
        next_event=next_event,
        next_reason=next_reason,
        files=files,
        files_pushed=files_pushed,
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


def _event_completion_receipt_local(title: str, event_type: EventType | str, parsed: dict | None,
                                    *, next_event: str = "", next_reason: str = "",
                                    files: list[str] | None = None,
                                    files_pushed: bool | None = None) -> str:
    """Deterministic receipt used by Harness to avoid an extra formatter LLM call."""
    parsed = parsed or {}
    done = _clip(str(parsed.get("step_done") or "本轮事件已完成"), 220)
    findings = parsed.get("findings") or []
    if isinstance(findings, str):
        findings = [findings]
    finding_text = "；".join(_clip(str(x), 140) for x in findings[:2] if str(x).strip())
    file_names = [os.path.basename(p) for p in (files or []) if p]
    lines = [done]
    if finding_text:
        lines.append(f"关键结果：{finding_text}")
    if file_names:
        status = "已发送" if files_pushed else "已生成"
        lines.append(f"文件{status}：{'; '.join(file_names[:4])}")
    if next_event:
        event_label = next_event or "none"
        lines.append(f"下一步：{event_label}")
    return "\n".join(line for line in lines if line).strip() or UNAVAILABLE_NOTICE


async def _event_completion_receipt_async(title: str, event_type: EventType | str, parsed: dict | None,
                                          *, next_event: str = "", next_reason: str = "",
                                          files: list[str] | None = None,
                                          files_pushed: bool | None = None) -> str:
    return await asyncio.to_thread(
        _event_completion_receipt,
        title,
        event_type,
        parsed,
        next_event=next_event,
        next_reason=next_reason,
        files=files,
        files_pushed=files_pushed,
    )


def _format_event_completion_receipt_with_llm(title: str, event_type: EventType | str, parsed: dict,
                                             *, next_event: str = "", next_reason: str = "",
                                             files: list[str] | None = None,
                                             files_pushed: bool | None = None) -> str:
    """Let the model decide how much of an event result should be shown."""
    if not _adapter or os.getenv("PARTNER_DISABLE_LLM_RECEIPT_FORMATTER", "").lower() in {"1", "true", "on", "yes"}:
        return ""
    event_value = event_type.value if isinstance(event_type, EventType) else str(event_type or "")
    event_label = prefix_event_notice("x", event_value).splitlines()[0].strip("【】").replace("事件：", "")
    next_label = prefix_event_notice("x", next_event).splitlines()[0].strip("【】").replace("事件：", "") if next_event else ""
    artifact = _compact_artifact_for_receipt(str(parsed.get("artifact_content") or ""), limit=2600)
    user_files = [os.path.basename(p) for p in (files or []) if p]
    if files_pushed is True:
        file_delivery_status = "已通过 QQ 文件接口发送。"
    elif files:
        file_delivery_status = "文件已生成/定位，但 QQ 文件接口未确认发送成功；只能说明文件在工作区就绪，不能说已经发给用户。"
    else:
        file_delivery_status = "本轮没有可发送文件。"
    prompt = f"""你是 Partner 的 event 完成汇报 formatter。你只根据已完成 event 的结果组织给用户的消息，不重新执行任务，不补事实，不编造来源。

任务/项目：{title}
当前 event：{event_value} / {event_label}
DONE：{str(parsed.get('step_done') or '')[:700]}
FINDINGS：{json.dumps(parsed.get('findings') or [], ensure_ascii=False)[:1200]}
EVIDENCE：{str(parsed.get('evidence') or '')[:800]}
FILES：{', '.join(user_files) if user_files else str(parsed.get('files') or 'EMPTY')[:600]}
文件发送状态：{file_delivery_status}
ARTIFACT：
{artifact or 'EMPTY'}

下一步 event：{next_event or 'none'} {('/ ' + next_label) if next_label else ''}
下一步原因：{next_reason[:500] if next_reason else ''}

输出要求：
- 中文自然回复，不要 JSON，不要代码块，不要加【事件】前缀。
- 由你根据内容判断该展开多少：如果用户要解释/讲解，就直接给核心内容；如果用户要文件/图片/视频，就说明已发送或给可访问链接；如果受限，就说清访问限制。
- 只有“文件发送状态”明确写已通过 QQ 文件接口发送时，才可以说“已发送/已发给你”；否则必须说“文件已生成但发送未确认/发送失败/需要重新发送”。
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


def _load_execution_policy() -> dict:
    """Load execution_policy.yaml from workspace 00_config/.

    Returns a dict with keys like ``stale_task_ttl_hours``.
    Defaults are returned when the file is missing or unparseable.
    """
    candidates = [
        os.path.join(_workspace, "config", "execution_policy.yaml"),
        os.path.join(_workspace, "execution_policy.yaml"),
    ]
    defaults = {"stale_task_ttl_hours": 24}
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                defaults.update(loaded)
                defaults["_config_path"] = path
                break
        except Exception as exc:
            logger.debug("[EXEC_POLICY] failed to load %s: %s", path, exc)
    return defaults


def _cleanup_stale_tasks(*, ttl_hours: int | None = None) -> None:
    """Remove task directories older than *ttl_hours* from all instances.

    Scans ``_workspace/instances/*/tasks/`` directories, checks modification
    time of the directory itself, and deletes any whose mtime is older than
    the configured TTL.

    Args:
        ttl_hours: Override TTL in hours.  Falls back to execution_policy.yaml
                   ``stale_task_ttl_hours`` if not provided (default: 24).
    """
    if not _workspace:
        return
    if ttl_hours is None:
        policy = _load_execution_policy()
        ttl_hours = int(policy.get("stale_task_ttl_hours", 24))
    if ttl_hours <= 0:
        return
    cutoff = _time.time() - ttl_hours * 3600
    instances_dir = os.path.join(_workspace, "instances")
    if not os.path.isdir(instances_dir):
        return
    removed = 0
    for inst in os.listdir(instances_dir):
        tasks_dir = os.path.join(instances_dir, inst, "tasks")
        if not os.path.isdir(tasks_dir):
            continue
        for task_name in os.listdir(tasks_dir):
            task_path = os.path.join(tasks_dir, task_name)
            if not os.path.isdir(task_path):
                continue
            try:
                mtime = os.path.getmtime(task_path)
                if mtime < cutoff:
                    import shutil
                    shutil.rmtree(task_path, ignore_errors=True)
                    removed += 1
                    logger.info(
                        "[CLEANUP] removed stale task dir %s (mtime=%s cutoff=%s)",
                        task_path,
                        datetime.fromtimestamp(mtime).isoformat(),
                        datetime.fromtimestamp(cutoff).isoformat(),
                    )
            except OSError:
                continue
    if removed:
        logger.info("[CLEANUP] removed %d stale task directories (TTL=%dh)", removed, ttl_hours)


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
    global _workspace, _adapter, _round_interval_sec, _journal, _knowledge, _task_queue, _state_manager
    _workspace = workspace
    _adapter = adapter
    _journal = kwargs.get("journal")
    _knowledge = kwargs.get("knowledge")
    _task_queue = kwargs.get("task_queue")
    _state_manager = kwargs.get("state") or kwargs.get("state_manager")
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
    try:
        _cleanup_stale_tasks()
    except Exception as exc:
        logger.warning("[MIND] stale task cleanup failed: %s", exc)

    # Initialize unified delivery queue
    try:
        delivery_init(workspace)
        # Register channel dispatchers for backward compatibility
        register_channel("event_pipeline", _dispatch_to_event_pipeline)
        register_channel("dialog_history", _dispatch_to_dialog_history)
        logger.info("[DELIVERY] unified delivery queue initialized")
    except Exception as exc:
        logger.warning("[DELIVERY] failed to initialize: %s", exc)

    logger.info(f"[MIND] Executor initialized: workspace={workspace}")


# ── Event Queue ────────────────────────────────────────────────────────

_event_queue: asyncio.Queue[MindEvent] = asyncio.Queue()


async def _event_loop():
    """Consume events from the queue and execute them.
    
    Tracks the currently running task so that STOP_PROJECT can cancel
    an in-flight BATCH_PLAN, allowing new user messages to interrupt
    a running task rather than waiting for it to complete.
    """
    current_task: asyncio.Task | None = None
    while True:
        try:
            event = await _event_queue.get()
            
            # STOP_PROJECT should cancel the currently running task
            if event.type == EventType.STOP_PROJECT and current_task and not current_task.done():
                title = str(event.payload.get("title", "") if event.payload else "")
                logger.info("[EVENT_LOOP] STOP_PROJECT cancelling running task for: %s", title[:60])
                current_task.cancel()
                try:
                    await current_task
                except (asyncio.CancelledError, Exception):
                    pass
                current_task = None
                # Also handle this STOP_PROJECT for cleanup
                handler = _get_handler(event.type)
                if handler:
                    await handler(event)
                continue

            # NEW USER_MESSAGE while a task is running → cancel current, let it handle
            if event.type == EventType.USER_MESSAGE and current_task and not current_task.done():
                logger.info("[EVENT_LOOP] new USER_MESSAGE while task running — cancelling current task")
                current_task.cancel()
                try:
                    await current_task
                except (asyncio.CancelledError, Exception):
                    pass
                current_task = None

            handler = _get_handler(event.type)
            if handler:
                # Wrap handler in a Task so it can be cancelled
                current_task = asyncio.create_task(handler(event))
                try:
                    await current_task
                except asyncio.CancelledError:
                    logger.info("[EVENT_LOOP] task %s was cancelled (%s)", event.id[:8], event.type.value)
                    # Event was interrupted — don't treat as error
                finally:
                    if current_task and current_task.done():
                        current_task = None
            else:
                logger.warning("[EVENT_LOOP] no handler for event type: %s", event.type.value)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"[EVENT_LOOP] error: {exc}")


def start_event_loop():
    """Start the background event processing loop in a dedicated thread."""
    loop = asyncio.new_event_loop()
    th = threading.Thread(target=_run_event_loop, args=(loop,), daemon=True, name="event-loop")
    th.start()
    # Store loop for threadsafe enqueue
    global _event_loop_instance
    _event_loop_instance = loop
    # Start desktop inbox poller AFTER the event loop is ready
    if _workspace:
        _start_desktop_inbox_poller(_workspace)
    logger.info("[EVENT_LOOP] started in dedicated thread")


def _get_event_loop():
    """Get the mind's event loop, falling back to get_event_loop()."""
    if _event_loop_instance is not None and not _event_loop_instance.is_closed():
        return _event_loop_instance
    return asyncio.get_event_loop()


def _run_event_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_event_loop())
    except Exception as exc:
        logger.error(f"[EVENT_LOOP] thread exited: {exc}")


_global_desktop_seen_ids: set[str] = set()


def _load_seen_ids(workspace: str) -> set[str]:
    """Load persisted seen message IDs from disk."""
    path = os.path.join(workspace, "state", "desktop_inbox_seen_ids.json")
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
    except Exception as exc:
        logger.warning("[DESKTOP_INBOX] failed to load seen_ids: %s", exc)
    return set()


def _save_seen_ids(workspace: str, seen: set[str]):
    """Persist seen message IDs to disk atomically."""
    path = os.path.join(workspace, "state", "desktop_inbox_seen_ids.json")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(seen), f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("[DESKTOP_INBOX] failed to save seen_ids: %s", exc)


def _start_desktop_inbox_poller(workspace: str):
    """Start a background thread that polls desktop_inbox.jsonl and enqueues USER_MESSAGE events.

    The TUI and GUI write user messages to desktop_inbox.jsonl. This poller reads
    new entries and feeds them into the mind's event queue via enqueue_user_message(),
    so they get processed through the same pipeline as QQ messages.
    Runs independently of the QQ bridge.

    Seen message IDs are persisted to disk so surviving a restart does not
    re-process already-consumed desktop inbox entries.
    """
    global _global_desktop_seen_ids
    _global_desktop_seen_ids = _load_seen_ids(workspace)

    inbox_path = os.path.join(workspace, "state", "desktop_inbox.jsonl")

    def poll():
        while True:
            try:
                if not os.path.exists(inbox_path):
                    _time.sleep(2)
                    continue
                with open(inbox_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                dirty = False
                processed_count = 0
                for line in lines:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        entry = json.loads(stripped)
                        msg_id = entry.get("id") or entry.get("message_id") or ""
                        if not msg_id or msg_id in _global_desktop_seen_ids:
                            continue
                        text = entry.get("text") or entry.get("content") or ""
                        if not text.strip():
                            continue
                        sender_id = entry.get("sender_id", "desktop_gui")
                        sender_name = entry.get("sender_name", "用户")
                        source = entry.get("source", "desktop_gui")

                        # Dedup: skip if the same text from the same source is
                        # already pending in the event queue
                        if _is_same_message_pending(text, source):
                            logger.info("[DESKTOP_INBOX] skipping duplicate: %s (already queued)", msg_id)
                            _global_desktop_seen_ids.add(msg_id)
                            dirty = True
                            continue

                        enqueue_user_message(
                            text=text,
                            sender_id=sender_id,
                            sender_name=sender_name,
                            source=source,
                            message_id=msg_id,
                        )
                        # Persist the user message to qq_chat_history.jsonl so the GUI
                        # can display it in the dialogue history alongside bot replies.
                        try:
                            _append_to_chat_history(workspace, {
                                "role": "user",
                                "content": text,
                                "timestamp": datetime.now().isoformat(),
                                "source": source,
                                "message_id": msg_id,
                                "sender_id": sender_id,
                                "sender_name": sender_name,
                            })
                        except Exception as exc:
                            logger.debug("[DESKTOP_INBOX] failed to persist user message: %s", exc)
                        _global_desktop_seen_ids.add(msg_id)
                        dirty = True
                        processed_count += 1
                    except json.JSONDecodeError:
                        continue
                    except Exception as exc:
                        logger.warning("[DESKTOP_INBOX] error processing entry: %s", exc)
                if dirty:
                    _save_seen_ids(workspace, _global_desktop_seen_ids)
                # Clear the inbox file after processing — removes stale entries
                # that are already in seen_ids, preventing re-processing on restart.
                if processed_count > 0 or dirty:
                    try:
                        with open(inbox_path, "w", encoding="utf-8") as f:
                            f.write("")
                    except Exception as exc:
                        logger.warning("[DESKTOP_INBOX] failed to clear inbox: %s", exc)
            except Exception as exc:
                logger.warning("[DESKTOP_INBOX] poll error: %s", exc)
            _time.sleep(2)

    t = threading.Thread(target=poll, daemon=True, name="desktop-inbox-poller")
    t.start()
    logger.info("[DESKTOP_INBOX] poller started for %s", inbox_path)


def enqueue_user_message(text: str, *, sender_id: str = "desktop_gui", sender_name: str = "用户",
                        source: str = "desktop_gui", message_id: str = ""):
    """Enqueue a user message as a USER_MESSAGE event."""
    event = MindEvent(
        type=EventType.USER_MESSAGE,
        priority=3,
        payload={
            "text": text[:4000],
            "sender_id": sender_id,
            "sender_name": sender_name,
            "message_id": message_id or f"msg_{uuid.uuid4().hex[:8]}",
            "source": source,
        },
        source=source,
    )
    _get_event_loop().call_soon_threadsafe(
        lambda: asyncio.create_task(_event_queue.put(event))
    )


# ── Message dedup ────────────────────────────────────────────────────


def _is_same_message_pending(text: str, source: str) -> bool:
    """Check if a USER_MESSAGE with the same text+source is already queued.
    
    Scans the pending event queue for duplicate USER_MESSAGE events.
    This prevents the same message being processed twice when the user
    double-clicks or the network retransmits.
    """
    try:
        q = _event_queue
        if q is None:
            return False
        # Peek into the asyncio queue (qsize + iterate pending items)
        # The queue stores asyncio.Event objects; we iterate pending
        # events from the internal _queue
        import asyncio
        if hasattr(q, '_queue'):
            import collections
            items = list(q._queue) if isinstance(q._queue, (list, collections.deque)) else []
            for item in items:
                if isinstance(item, MindEvent):
                    if (item.type == EventType.USER_MESSAGE
                            and item.payload.get("text", "") == text
                            and item.source == source):
                        return True
    except Exception:
        pass
    return False


async def ensure_pool():
    """保留兼容 — 返回事件队列（替代旧的 MindPool）。"""
    return _event_queue

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
    except asyncio.CancelledError:
        logger.info(f"[执行] 念头 #{event.id[:8]} 被取消")
    except Exception as e:
        logger.error(f"[执行] 念头 #{event.id[:8]} 执行失败: {e}", exc_info=True)


# ── 事件分发 ────────────────────────────────────────────────────────


def _get_handler(event_type: EventType):
    """获取事件类型的处理函数（统一走 batch_plan 框架）。"""
    return {
        EventType.USER_MESSAGE: _handle_user_message,
        EventType.DIRECT_REPLY: _handle_direct_reply,
        EventType.PROJECT: _handle_project,
        EventType.BATCH_PLAN: _handle_batch_plan_event,
        EventType.DIRECT_TASK: _handle_batch_plan_event,
        EventType.LITERATURE_REVIEW: _handle_batch_plan_event,
        EventType.DATA_FETCH: _handle_batch_plan_event,
        EventType.DATA_ANALYSIS: _handle_batch_plan_event,
        EventType.VISUALIZATION: _handle_batch_plan_event,
        EventType.EVIDENCE_AUDIT: _handle_batch_plan_event,
        EventType.ARTIFACT_BUILD: _handle_batch_plan_event,
        EventType.PDF_REPORT: _handle_batch_plan_event,
        EventType.EMAIL_DELIVERY: _handle_email_delivery,
        EventType.WEB_SEARCH: _handle_batch_plan_event,
        EventType.WEB_CAPTURE: _handle_batch_plan_event,
        EventType.PROJECT_THINK: _handle_batch_plan_event,
        EventType.OBJECTIVE_REVIEW: _handle_batch_plan_event,
        EventType.CHECK: _handle_action_event,
        EventType.REFLECT: _handle_action_event,
        EventType.CURIOSITY: _handle_action_event,
        EventType.CURIOSITY_EXPLORE: _handle_batch_plan_event,
        EventType.HABIT_UPDATE: _handle_action_event,
        EventType.OLLAMA_STATUS: _handle_ollama_status,
        EventType.STOP_PROJECT: _handle_stop_project,
        EventType.TASK_FAILED: _handle_task_failed,
        EventType.CRON_TICK: _handle_cron_tick,
        EventType.REPORT: _handle_report,
        EventType.WAKE_UP: _handle_wake_up,
        EventType.REFLECTION: _handle_reflection,
        EventType.CROSS_PROJECT: _handle_cross_project,
        EventType.MEMORY_CONSOLIDATE: _handle_memory_consolidate,
        EventType.CONTENT_DIGEST: _handle_content_digest,
        EventType.CONTENT_PATROL: _handle_content_patrol,
    }.get(event_type)


# ── TASK FAILED ──────────────────────────────────────────────────────


async def _handle_task_failed(event: MindEvent):
    """Handle a TASK_FAILED event: send a structured failure notification to the user."""
    payload = event.payload or {}
    task_title = payload.get("task_title", "未知任务")
    failed_at_step = payload.get("failed_at_step", "?")
    error = payload.get("error", "未知错误")
    skipped_steps = payload.get("skipped_steps", 0)
    total_steps = payload.get("total_steps", 0)
    acceptance_report = payload.get("acceptance_report", "")

    lines = [f"❌ 任务失败：{task_title}"]
    lines.append(f"   失败步骤：{failed_at_step}/{total_steps}")
    if error:
        lines.append(f"   错误原因：{error}")
    if skipped_steps > 0:
        lines.append(f"   跳过步骤：{skipped_steps}/{total_steps}")
    if acceptance_report:
        lines.append(f"   验收诊断：{acceptance_report}")

    summary = "\n".join(lines)
    logger.info("[TASK_FAILED] %s", summary)

    # Deliver via the unified queue
    deliver(
        kind="error",
        content=summary,
        channels=["event_pipeline", "dialog_history"],
        metadata={
            "sender_id": payload.get("sender_id", ""),
            "message_id": payload.get("message_id", ""),
            "event_id": event.id,
            "event_type": "task_failed",
            "status": "error",
        },
    )


# ── USER MESSAGE ─────────────────────────────────────────────────────


def _append_assistant_dialog_history(content: str, *, sender_id: str = "", sender_name: str = "Partner",
                                     message_id: str = "", source: str = "desktop_gui") -> None:
    text = str(content or "").strip()
    if not text:
        return
    if text in {"思考中.......", "思考中......", "思考中……", "Thinking..."}:
        return
    try:
        from ..workspace.workspace_layout import append_history

        row = {
            "role": "assistant",
            "content": text,
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "channel": "desktop" if source == "desktop_gui" else source,
            "sender_id": "partner",
            "sender_name": sender_name or "Partner",
            "reply_to": message_id,
            "target_id": sender_id,
        }
        append_history(_workspace, row, ("dialog_history.jsonl", "qq_chat_history.jsonl"))
    except Exception as exc:
        logger.debug("[USER_MESSAGE] failed to append assistant dialog history: %s", exc)


async def _handle_user_message(event: MindEvent):
    """Handle a USER_MESSAGE event.

    All messages enter through this single handler. It decides:
    - DIRECT_REPLY: for simple messages that just need an LLM reply
    - BATCH_PLAN: for complex tasks needing multi-step execution
    """
    payload = event.payload or {}
    text = str(payload.get("text") or payload.get("user_request") or "").strip()
    sender_id = str(payload.get("sender_id") or "desktop_gui").strip()
    sender_name = str(payload.get("sender_name") or "用户").strip()
    message_id = str(payload.get("message_id") or "").strip()
    source = str(payload.get("source") or "desktop_gui").strip()

    if not text:
        logger.info("[USER_MESSAGE] empty text; skipped %s", event.id[:8])
        return

    # Message-level dedup: skip if same text was processed within the last 30s
    _text_hash = str(hash(text))
    _now = _time.time()
    if _text_hash in _recent_user_messages:
        _age = _now - _recent_user_messages[_text_hash]
        if _age < _USER_MSG_DEDUP_SEC:
            logger.info("[USER_MESSAGE] dedup: skipping '%s' (processed %.0fs ago)", text[:60], _age)
            _append_event_pipeline(event.id, "user_message", "dedup", {
                "reason": f"same text processed {_age:.0f}s ago",
            })
            return
    _recent_user_messages[_text_hash] = _now
    # Prune entries older than dedup window to prevent unbounded growth
    _stale = [k for k, v in _recent_user_messages.items() if _now - v > _USER_MSG_DEDUP_SEC]
    for _k in _stale:
        _recent_user_messages.pop(_k, None)

    _append_event_pipeline(event.id, "user_message", "received", {
        "text": text[:100],
        "sender_id": sender_id,
    })

    # Call orchestrator to decide routing
    try:
        from ..core.interaction_orchestrator import InteractionOrchestrator

        orchestrator = InteractionOrchestrator(
            workspace=_workspace,
            journal=_journal,
            knowledge=_knowledge,
            task_queue=_task_queue,
            state_manager=_state_manager,
            get_adapter=lambda: _adapter,
            get_context=_get_context_for_message,
            snapshot_builder=_snapshot_state,
        )
        decision = orchestrator.handle_message(sender_id=sender_id, sender_name=sender_name, text=text)

        if decision.event_kind == "direct_reply":
            # Create DIRECT_REPLY event
            reply_event = MindEvent(
                type=EventType.DIRECT_REPLY,
                priority=1,
                payload={
                    "text": text,
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "message_id": message_id,
                    "source": source,
                },
                source="user_message_handler",
            )
            await _event_queue.put(reply_event)
            _append_event_pipeline(event.id, "user_message", "routed", {
                "to": "direct_reply",
            })
            return

        if decision.need_lifeline_update or decision.event_type in ("batch_plan", "project"):
            logger.debug("[USER_MESSAGE] queuing BATCH_PLAN for event_type=%s need_lifeline=%s", decision.event_type, decision.need_lifeline_update)
            # Complex task — create BATCH_PLAN event
            # Before queuing, check if there's already a pending BATCH_PLAN from
            # a previously queued USER_MESSAGE. If so, update its payload with
            # the latest message so only the most recent request is processed.
            # This handles rapid-fire messages where multiple USER_MESSAGE events
            # are queued before any BATCH_PLAN starts running.
            try:
                pending_batch_plans = []
                q = getattr(_event_queue, '_queue', None)
                if q is not None:
                    import collections
                    items = list(q) if isinstance(q, (list, collections.deque)) else []
                    for existing in items:
                        if isinstance(existing, MindEvent) and existing.type == EventType.BATCH_PLAN:
                            pending_batch_plans.append(existing)
                if pending_batch_plans:
                    # Update the LAST pending BATCH_PLAN with the latest message
                    target = pending_batch_plans[-1]
                    target.payload["user_request"] = text
                    target.payload["title"] = decision.task_title or text[:80]
                    logger.info("[USER_MESSAGE] updated existing pending BATCH_PLAN with latest message: %s", text[:60])
                    _append_event_pipeline(event.id, "user_message", "merged", {
                        "merged_into_existing_batch_plan": True,
                    })
                    return
            except Exception as exc:
                logger.debug("[USER_MESSAGE] failed to check pending BATCH_PLANs: %s", exc)

            plan_event = MindEvent(
                type=EventType.BATCH_PLAN,
                priority=decision.priority or 5,
                payload={
                    "title": decision.task_title or text[:80],
                    "user_request": text,
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "source": source,
                    "event_type": decision.event_type,
                    "event_kind": decision.event_kind,
                    "stop_after_completion": decision.stop_after_completion,
                },
                source="user_message_handler",
            )
            await _event_queue.put(plan_event)

        # Direct reply without separate event (orchestrator already handled it)
        reply = str(decision.reply_to_user or "").strip()
        if reply and reply not in {"思考中.......", "思考中......", "思考中……", "Thinking..."}:
            _append_assistant_dialog_history(reply, sender_id=sender_id, sender_name="Partner", message_id=message_id, source=source)

        _append_event_pipeline(event.id, "user_message", "completed", {
            "reply": (reply or "")[:200],
            "event_kind": decision.event_kind,
        })

    except Exception as exc:
        logger.warning("[USER_MESSAGE] failed to route message %s: %s", event.id[:8], exc, exc_info=True)
        _append_assistant_dialog_history(
            "抱歉，处理消息时出现错误",
            sender_id=sender_id, sender_name="Partner", message_id=message_id, source=source,
        )
        _append_event_pipeline(event.id, "user_message", "failed", {
            "error": str(exc)[:200],
        })


async def _handle_direct_reply(event: MindEvent):
    """Handle a DIRECT_REPLY event: call LLM directly, write reply to history, record pipeline."""
    payload = event.payload or {}
    text = str(payload.get("text") or "").strip()
    sender_id = str(payload.get("sender_id") or "desktop_gui").strip()
    sender_name = str(payload.get("sender_name") or "用户").strip()
    message_id = str(payload.get("message_id") or "").strip()
    source = str(payload.get("source") or "desktop_gui").strip()
    pre_generated_reply = str(payload.get("reply") or "").strip()

    if not text:
        return

    # Record pipeline: running
    _append_event_pipeline(event.id, "direct_reply", "running", {
        "text": text[:100],
        "sender_id": sender_id,
        "started_at": datetime.now().isoformat(),
    })

    try:
        # Use pre-generated reply from the classification if available, otherwise call LLM
        if pre_generated_reply:
            reply = pre_generated_reply
        else:
            reply = _adapter.chat(text, purpose="direct_reply")

        if reply and reply.strip() and "PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE" not in reply:
            sanitized = _sanitize_reply(reply)
            if sanitized:
                annotated = f"[快速回复] {sanitized}"
                # Use unified delivery queue
                deliver(
                    kind="reply",
                    content=annotated,
                    channels=["dialog_history", "event_pipeline"],
                    metadata={
                        "sender_id": sender_id,
                        "sender_name": sender_name or "Partner",
                        "message_id": message_id,
                        "source": source,
                        "event_id": event.id,
                        "event_type": "direct_reply",
                        "status": "completed",
                    },
                )
                _append_event_pipeline(event.id, "direct_reply", "completed", {
                    "reply": sanitized[:200],
                    "completed_at": datetime.now().isoformat(),
                })
                return
    except Exception as exc:
        logger.warning("[DIRECT_REPLY] LLM call failed: %s", exc)

    # Failed
    _append_event_pipeline(event.id, "direct_reply", "failed", {
        "error": "LLM call failed or returned empty response",
        "completed_at": datetime.now().isoformat(),
    })


def _sanitize_reply(reply: str) -> str:
    """Clean up an LLM reply string."""
    if not reply:
        return ""
    sanitized = reply.strip()
    # Remove common problematic prefixes
    for prefix in ["Assistant:", "AI:", "Bot:", "Partner:"]:
        if sanitized.startswith(prefix):
            sanitized = sanitized[len(prefix):].strip()
    return sanitized


def _get_context_for_message(sender_id: str) -> list[dict]:
    """Get recent conversation context for a user."""
    try:
        from ..workspace.workspace_layout import history_paths
        rows: list[dict] = []
        seen: set[str] = set()
        paths: list[str] = []
        for name in ("dialog_history.jsonl", "qq_chat_history.jsonl"):
            for path in history_paths(_workspace, name):
                if path not in paths:
                    paths.append(path)
        for path in paths:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-12:]
            for line in lines:
                try:
                    row = json.loads(line.strip())
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                key = "|".join(str(row.get(k) or "") for k in ("timestamp", "role", "content", "message_id"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
        rows.sort(key=lambda item: str(item.get("timestamp") or item.get("created_at") or ""))
        ctx = []
        for row in rows[-6:]:
            role = "user" if str(row.get("role") or "").lower() == "user" else "partner"
            content = str(row.get("content") or "").strip()
            if content:
                ctx.append({"role": role, "text": content[:300]})
        return ctx
    except Exception:
        return []


def _snapshot_state() -> dict:
    try:
        from ..projects.project_state import get_active
        active = get_active(_workspace) or ""
    except Exception:
        active = ""
    return {"focus_project": active, "display_project": active, "summary": ""}


# ── ACTION EVENTS ───────────────────────────────────────────────────


_ACTION_EVENT_SPECS = {
    EventType.BATCH_PLAN: {
        "name": "批量规划",
        "artifact": "batch_plan_result.md",
        "rules": [
            "复杂任务默认入口：一次生成 Harness MicroPlan，再执行依赖和并行步骤。",
            "不进入旧的逐次 follow-up selector。",
            "好奇探索由 CuriosityEngine 根据交付物缺口统一触发。",
        ],
    },
    EventType.DIRECT_TASK: {
        "name": "直接交付",
        "artifact": "",
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
    EventType.FILE_INSPECTION: {
        "name": "附件格式识别",
        "artifact": "file_inspection_result.md",
        "rules": [
            "只检查用户上传/QQ 收到的一个或多个本地文件，不推测无法验证的内容。",
            "必须读取前 64 字节，输出 magic、hex dump、MIME/扩展名推断，并保存 inspection.json。",
            "已知文本/表格/PDF/图片可建议后续读取、OCR、ASR 或分析；未知/音频格式必须如实说明边界。",
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
            "对于开放研究/综述/效果比较/突破方案类目标，如果没有可追溯证据、对比表、文件或最终交付物，不能建议 stop_project；应建议 literature_review 或 curiosity_explore 继续最小探索。",
        ],
    },
    EventType.CHECK: {
        "name": "产物检查",
        "artifact": "check_result.md",
        "rules": [
            "只检查当前 TaskInstance 产物是否满足配置规则，不重新执行任务。",
            "必须输出 satisfied / missing / files / field_hits。",
        ],
    },
    EventType.REFLECT: {
        "name": "缺口反思",
        "artifact": "reflect_result.md",
        "rules": [
            "只根据 Check 结果分析缺口，输出 missing_info、weak_evidence、suggested_queries。",
            "不要写最终报告，不要重复执行搜索。",
        ],
    },
    EventType.CURIOSITY: {
        "name": "好奇补充",
        "artifact": "curiosity_result.md",
        "rules": [
            "只根据 Reflect 缺口规划或执行一个小补充动作。",
            "每次只补一个细粒度信息缺口，避免无限探索。",
        ],
    },
    EventType.CURIOSITY_EXPLORE: {
        "name": "好奇探索",
        "artifact": "curiosity_explore.md",
        "rules": [
            "从上一轮缺口中选择一个最小、可验证、能产生新信息的探索动作；不要只复述上一轮内容。",
            "若用户目标包含“效果比较/最佳方案/突破方法/创新方向”，必须输出结构化突破探索：候选方案、证据强弱、瓶颈、突破假设、最小验证实验、失败边界。",
            "每个突破假设必须包含：为什么可能有效、需要什么数据/工具、如何最小验证、预期指标、可能失败原因。",
            "必须区分 verified / source-claimed / inferred / hypothesis；不能把常识或模型记忆写成已验证结论。",
            "如果缺少必要数据/环境/权限，先做无阻塞替代：列出公开可查证来源、最小实验设计、伪代码或验证计划，并明确下一步最小 event。",
            "NEXT 必须继续指向 literature_review、data_analysis、evidence_audit、artifact_build 或 pdf_report 中的一个具体最小动作；只有已形成最终交付物时才等待用户。",
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
            m = re.search(r"用户消息[:：]\s*(.+?)(?:\n|$)", val)
            if m and m.group(1).strip():
                return m.group(1).strip()
            return val
    return ""


def _is_open_research_goal(text: str) -> bool:
    text = str(text or "")
    return bool(text.strip())


def _has_substantive_research_output(parsed: dict | None) -> bool:
    if not isinstance(parsed, dict):
        return False
    blob = "\n".join(
        str(x or "")
        for x in (
            parsed.get("step_done"),
            "；".join(str(v) for v in (parsed.get("findings") or [])),
            parsed.get("evidence"),
            parsed.get("files"),
            parsed.get("artifact_content"),
        )
    )
    if not blob.strip() or re.search(r"(未能|没有|暂无|超时|不可解析|无结构化|EMPTY|no structured|timeout)", blob, re.I):
        return False
    evidence = str(parsed.get("evidence") or "").strip()
    files = str(parsed.get("files") or "").strip()
    if evidence and not evidence.startswith("system:") and evidence.lower() not in {"empty", "hypothesis"}:
        return True
    if files and files.upper() != "EMPTY":
        return True
    findings = [str(x).strip() for x in (parsed.get("findings") or []) if str(x).strip()]
    return len(findings) >= 3


def _research_recovery_decision(event: MindEvent, title: str, parsed: dict | None,
                                payload: dict, reason: str) -> dict:
    root_request = _root_user_request(payload) or str(payload.get("user_request") or title)
    depth = _curiosity_depth(payload)
    missing = str((parsed or {}).get("next_action") or payload.get("previous_next_action") or "").strip()
    if event.type in {EventType.LITERATURE_REVIEW, EventType.WEB_SEARCH} and depth >= 1:
        next_type = EventType.CURIOSITY_EXPLORE
        event_kind = "research_gap_curiosity_explore"
        objective = (
            "上一轮资料/搜索没有形成足够可验证的结论。请不要停止；基于已知缺口继续做深入探索："
            "列出候选最佳方案、证据强弱、关键瓶颈，并提出可验证突破假设。"
        )
        question = "当前证据不足时，哪些候选方案和突破假设最值得继续验证？"
    else:
        next_type = EventType.LITERATURE_REVIEW
        event_kind = "research_min_literature_slice"
        objective = (
            "把开放调研目标缩小成一个最小资料整理动作：只收集并对比 3-5 个可追溯来源/方法，"
            "提取方法名、数据类型、评估指标、效果边界和来源链接/文献信息。"
        )
        question = "先用最小资料切片建立可验证的方法与效果对比基础。"
    return {
        "continue": True,
        "event_type": next_type.value,
        "event_kind": event_kind,
        "objective": (
            f"{objective}\n"
            f"根目标：{root_request[:1400]}\n"
            f"上一轮缺口/NEXT：{missing[:700] or '上一轮没有形成可验证产物'}\n"
            f"恢复原因：{reason}\n"
            "验收标准：必须输出可追溯证据、候选对比表或突破假设表；如果外部访问失败，也要保存失败原因和下一步最小替代方案。"
        ),
        "question": question,
        "reason": f"open_research_continue_after_{reason}",
    }


def _fallback_followup_after_selector_failure(event: MindEvent, title: str, parsed: dict,
                                              payload: dict, reason: str) -> dict:
    user_request = str(payload.get("user_request") or title).strip()
    root_request = _root_user_request(payload) or user_request
    event_kind = str(payload.get("event_kind") or event.type.value).strip()
    next_action = str(parsed.get("next_action") or payload.get("previous_next_action") or "").strip()
    if _is_open_research_goal(root_request) and not _has_substantive_research_output(parsed):
        return _research_recovery_decision(event, title, parsed, payload, f"selector_{reason}")
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


def _event_payload_files(payload: dict) -> list[str]:
    files: list[str] = []
    for key in ("files", "file_paths", "attachments"):
        value = payload.get(key)
        if isinstance(value, str):
            parts = re.split(r"[;\n，,]+", value)
        elif isinstance(value, list):
            parts = value
        else:
            parts = []
        for item in parts:
            if isinstance(item, dict):
                item = item.get("server_path") or item.get("path") or item.get("file_path") or item.get("rel_path") or ""
            path = str(item or "").strip()
            if not path:
                continue
            if not os.path.isabs(path):
                path = os.path.join(_workspace, path)
            if path not in files:
                files.append(path)
    text = str(payload.get("user_request") or payload.get("objective") or payload.get("description") or "")
    for match in re.findall(r"(?<![\w.])(?:/|[A-Za-z]:[\\/])[^ \n\r\t;，,]+", text):
        path = match.strip().strip("'\"`。")
        if path and path not in files:
            files.append(path)
    return files


def _run_file_inspection(event: MindEvent, title: str, project_dir: str) -> dict | None:
    if event.type != EventType.FILE_INSPECTION:
        return None
    payload = event.payload or {}
    paths = _event_payload_files(payload)
    if not paths:
        return {
            "action": "file_inspection",
            "step_done": "没有找到可检查的附件路径",
            "findings": ["未收到本地文件路径", "没有读取任何文件"],
            "evidence": "",
            "next_action": "请重新上传文件，或提供服务器上可访问的绝对路径。",
            "state_delta": "file_inspection blocked: missing file path",
            "files": "EMPTY",
            "artifact_content": "## 附件格式识别\n\n未找到可访问的附件路径。\n",
        }
    from ..file_tools import inspect_file

    rows = []
    output_files = []
    findings = []
    for path in paths:
        if not os.path.exists(path) or not os.path.isfile(path):
            findings.append(f"不可访问：{path}")
            continue
        try:
            inspection = inspect_file(path, output_dir=project_dir)
            output_files.append(str(inspection.get("inspection_path") or ""))
            rows.append(inspection)
            findings.append(
                f"{os.path.basename(path)}: magic={inspection.get('magic')}, ext={inspection.get('extension') or 'none'}, size={inspection.get('size')}"
            )
        except Exception as exc:
            findings.append(f"{os.path.basename(path)} 检查失败：{exc}")
    if not rows:
        artifact = "## 附件格式识别\n\n没有任何文件完成检查。\n\n" + "\n".join(f"- {x}" for x in findings)
        return {
            "action": "file_inspection",
            "step_done": "附件检查失败",
            "findings": findings or ["没有任何文件完成检查"],
            "evidence": "",
            "next_action": "请确认文件路径和读取权限后重试。",
            "state_delta": "file_inspection failed: no inspected files",
            "files": "EMPTY",
            "artifact_content": artifact,
        }
    table = [
        "| 文件 | magic | MIME | 扩展名 | 大小 | 前 64 字节 hex |",
        "|---|---|---|---|---:|---|",
    ]
    for item in rows:
        table.append(
            "| {name} | {magic} | {mime} | {ext} | {size} | `{hex}` |".format(
                name=os.path.basename(str(item.get("path") or "")),
                magic=str(item.get("magic") or "unknown"),
                mime=str(item.get("mime_guess") or ""),
                ext=str(item.get("extension") or ""),
                size=str(item.get("size") or 0),
                hex=str(item.get("first_64_bytes_hex") or "")[:128],
            )
        )
    artifact = "## 附件格式识别\n\n" + "\n".join(table) + "\n\n"
    artifact += "## 处理建议\n\n"
    for item in rows:
        magic = str(item.get("magic") or "unknown")
        if magic in {"silk_audio", "wav_audio"}:
            artifact += f"- {os.path.basename(str(item.get('path') or ''))}: 这是音频类文件；如果没有 ASR/Whisper，只能先报告无法转写。\n"
        elif item.get("known_extension") or magic != "unknown":
            artifact += f"- {os.path.basename(str(item.get('path') or ''))}: 格式可识别，可进入读取/OCR/解析/分析事件。\n"
        else:
            artifact += f"- {os.path.basename(str(item.get('path') or ''))}: 未知格式，先询问用户来源或指定处理方式。\n"
    return {
        "action": "file_inspection",
        "step_done": f"已检查 {len(rows)} 个附件并保存 inspection.json",
        "findings": findings,
        "evidence": "; ".join(output_files),
        "next_action": "根据 magic/MIME 选择读取、OCR、ASR 或询问用户格式来源；目标已满足则 stop_project。",
        "state_delta": f"file_inspection inspected={len(rows)}",
        "files": "; ".join(output_files),
        "artifact_content": artifact,
    }


def _build_pdf_report_from_existing_files(title: str, payload: dict, project_dir: str) -> str:
    """Build a conservative report body from files already produced in a project."""
    if not os.path.isdir(project_dir):
        return ""
    files: list[str] = []
    for cur, dirs, names in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "state", "logs"} and not d.startswith(".")]
        for name in names:
            if name in {"trace_detail.md", "project_contract.json"}:
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in {".md", ".csv", ".tsv", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".webp", ".txt"}:
                continue
            path = os.path.join(cur, name)
            try:
                if os.path.getsize(path) <= 0:
                    continue
            except OSError:
                continue
            files.append(path)
    if not files:
        return ""
    files.sort(key=lambda p: (os.path.splitext(p)[1].lower() not in {".md", ".txt"}, os.path.getmtime(p) if os.path.exists(p) else 0))
    root_request = _root_user_request(payload) or str(payload.get("user_request") or title)
    lines = [
        f"# {title} 报告",
        "",
        "## 用户目标",
        root_request or title,
        "",
        "## 已使用的真实文件",
    ]
    for path in files[:20]:
        rel = os.path.relpath(path, project_dir)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        lines.append(f"- `{rel}` ({size} bytes)")

    text_sections = []
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        if ext not in {".md", ".txt", ".csv", ".tsv"}:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except OSError:
            continue
        if not text:
            continue
        if ext in {".csv", ".tsv"}:
            text = "\n".join(text.splitlines()[:12])
        text_sections.append((os.path.basename(path), text[:2200]))
        if len(text_sections) >= 4:
            break
    if text_sections:
        lines.extend(["", "## 数据与分析摘要"])
        for name, text in text_sections:
            lines.extend(["", f"### {name}", "", text])

    image_files = [p for p in files if os.path.splitext(p)[1].lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    if image_files:
        lines.extend(["", "## 图表"])
        for path in image_files[:6]:
            lines.append(f"![{os.path.basename(path)}]({path})")
    lines.extend([
        "",
        "## 边界说明",
        "本报告由当前项目目录中已经存在的真实文件整理生成；若某项数据或图表没有对应文件，报告不会补写为已完成。",
    ])
    return "\n".join(lines).strip()


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
- ollama_status: 下一步是探测 Ollama 是否可用，以及轻量问题是否会走 Ollama。
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
  "event_type": "none|direct_task|literature_review|data_fetch|data_analysis|visualization|evidence_audit|artifact_build|pdf_report|email_delivery|web_search|web_capture|project_think|objective_review|curiosity_explore|habit_update|ollama_status|stop_project",
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
    event_aliases = {
        "file_read": EventType.DATA_ANALYSIS.value,
        "read_file": EventType.DATA_ANALYSIS.value,
        "directory_listing": EventType.DATA_ANALYSIS.value,
        "list_directory": EventType.DATA_ANALYSIS.value,
        "file_write": EventType.ARTIFACT_BUILD.value,
        "write_file": EventType.ARTIFACT_BUILD.value,
    }
    selected = event_aliases.get(selected, selected)
    if selected in {"", "none"}:
        if _is_open_research_goal(root_request or user_request) and not _has_substantive_research_output(parsed):
            return _research_recovery_decision(event, title, parsed, payload, "selector_none_without_research_output")
        if payload.get("failure_event_type") and (root_request or user_request):
            return {
                "continue": True,
                "event_type": EventType.PROJECT_THINK.value,
                "event_kind": "failure_replan_min_step",
                "objective": (
                    "上一个事件失败后 selector 未给出下一步。不要停止当前根目标；"
                    "基于已完成内容和失败边界，重新拆出一个更小、可验证、可交付的下一步。"
                    f"\n根目标：{(root_request or user_request)[:1200]}"
                ),
                "question": "失败后最小可继续执行的下一步是什么？",
                "reason": "failure_review_selector_none_replan",
            }
        next_action = str(parsed.get("next_action") or payload.get("previous_next_action") or "").strip()
        active_depth = _curiosity_depth(payload)
        if next_action and not _stop_after_completion(payload) and active_depth < 4:
            if re.search(r"(artifact_build|final_report|最终报告|生成.*报告|整理.*报告)", next_action, re.I):
                return {
                    "continue": True,
                    "event_type": EventType.ARTIFACT_BUILD.value,
                    "event_kind": "selector_none_artifact_build",
                    "objective": (
                        "selector 返回 none，但当前项目仍有明确下一步。请基于已有可验证文件和上一轮 NEXT "
                        "生成用户可见的最终交付物；如果证据不足，必须在产物中如实说明缺口，不能编造已读取的文件或指标。"
                        f"\n根目标：{(root_request or user_request)[:1200]}"
                        f"\n上一轮 NEXT：{next_action[:700]}"
                    ),
                    "question": "基于现有可验证结果生成最终交付物；证据不足则明确边界。",
                    "reason": "selector_none_but_artifact_next_action",
                }
            return {
                "continue": True,
                "event_type": EventType.PROJECT_THINK.value,
                "event_kind": "selector_none_replan_next_action",
                "objective": (
                    "selector 返回 none，但当前项目仍有明确下一步。不要让队列空转；"
                    "请把上一轮 NEXT 拆成一个可执行、可验证、能产生结论或文件的最小 event。"
                    f"\n根目标：{(root_request or user_request)[:1200]}"
                    f"\n上一轮 NEXT：{next_action[:700]}"
                ),
                "question": "把上一轮 NEXT 拆成下一个最小可执行 event。",
                "reason": "selector_none_replan_next_action",
            }
        return {"continue": False, "reason": str(data.get("reason") or "selector_none")}
    if selected == EventType.STOP_PROJECT.value:
        if _is_open_research_goal(root_request or user_request) and not _has_substantive_research_output(parsed):
            return _research_recovery_decision(event, title, parsed, payload, "selector_stop_without_research_output")
        return {
            "continue": True,
            "event_type": selected,
            "event_kind": re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", str(data.get("event_kind") or "stop_project")).strip("_")[:80] or "stop_project",
            "objective": str(data.get("objective") or "停止当前执行链并等待用户继续").strip()[:1800] or "停止当前执行链并等待用户继续",
            "question": str(data.get("question") or "").strip()[:600],
            "reason": str(data.get("reason") or "").strip()[:600],
        }
    allowed = {
        EventType.BATCH_PLAN.value,
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
        EventType.OLLAMA_STATUS.value,
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
    try:
        from ..projects.project_state import get_project_status
        status = get_project_status(_workspace, title)
        if status in {"waiting", "done"}:
            return {"queued": False, "event_type": "", "event_kind": "", "reason": f"project_status_{status}"}
    except Exception:
        pass
    root_hint = "\n".join(
        str(x or "")
        for x in (
            _root_user_request(payload),
            payload.get("user_request"),
            payload.get("parent_user_request"),
            parsed.get("next_action") if isinstance(parsed, dict) else "",
        )
    )
    parsed_files = _resolve_one_shot_output_files(
        os.path.dirname(str((parsed or {}).get("files") or "")) if str((parsed or {}).get("files") or "") else os.path.join(_workspace or "", "projects", "projects", title),
        parsed,
        required_exts=None,
    ) if isinstance(parsed, dict) else []
    root_request_for_outputs = _root_user_request(payload) or str(payload.get("user_request") or title)
    delivery_status = str((parsed or {}).get("delivery_status") or "").strip().lower() if isinstance(parsed, dict) else ""
    if (
        event.type not in {EventType.PROJECT_THINK, EventType.OBJECTIVE_REVIEW, EventType.HABIT_UPDATE, EventType.CURIOSITY_EXPLORE}
        and parsed_files
        and delivery_status not in {"partial", "failed"}
        and _delivery_requirements_satisfied(root_request_for_outputs, parsed_files)
    ):
        return {
            "continue": True,
            "event_type": EventType.STOP_PROJECT.value,
            "event_kind": "root_delivery_satisfied",
            "objective": "根目标要求的交付物已由真实文件满足，停止当前执行链。",
            "question": "",
            "reason": "root_delivery_requirements_satisfied",
        }
    if event.type == EventType.DATA_FETCH:
        if _has_unfinished_requested_output(root_request_for_outputs, parsed_files, "image"):
            decision = {
                "continue": True,
                "event_type": EventType.VISUALIZATION.value,
                "event_kind": "requested_visualization",
                "objective": (
                    "基于已经获取的真实数据文件生成用户要求的图表图片；只做绘图，"
                    "输出 PNG/JPG 等真实图片文件，不能用文字说明替代。"
                    f"\n根目标：{root_request_for_outputs[:1200]}"
                    f"\n已有文件：{'; '.join(parsed_files)[:1200] or str((parsed or {}).get('files') or '')[:1200]}"
                ),
                "question": "把已有数据绘制成用户要求的图表图片。",
                "reason": "requested_image_not_delivered_after_data_fetch",
            }
        elif _has_unfinished_requested_output(root_request_for_outputs, parsed_files, "pdf") or _has_unfinished_requested_output(root_request_for_outputs, parsed_files, "report"):
            decision = {
                "continue": True,
                "event_type": EventType.PDF_REPORT.value,
                "event_kind": "requested_report",
                "objective": (
                    "基于已经获取的真实数据和已有中间产物生成用户要求的报告文件；"
                    "报告必须引用真实文件路径和可验证数据，不能编造图表或结论。"
                    f"\n根目标：{root_request_for_outputs[:1200]}"
                    f"\n已有文件：{'; '.join(parsed_files)[:1200] or str((parsed or {}).get('files') or '')[:1200]}"
                ),
                "question": "把已有结果整理成用户要求的报告文件。",
                "reason": "requested_report_not_delivered_after_data_fetch",
            }
        else:
            decision = await asyncio.to_thread(_followup_event_decision_with_llm, event, title, parsed, payload)
    elif event.type == EventType.LITERATURE_REVIEW and not _stop_after_completion(payload):
        decision = {
            "continue": True,
            "event_type": EventType.CURIOSITY_EXPLORE.value,
            "event_kind": "curiosity_followup_explore",
            "objective": (
                "基于上一轮文献/方法综述继续做深入探索："
                "1) 明确候选方案及证据强弱；"
                "2) 找出至少 3 个性能瓶颈或泛化风险；"
                "3) 提出 3 个可执行假设，每个包含为什么可能有效、最小验证实验、失败边界；"
                "4) 输出可复用的方案表，不要只复述上一轮结论。"
                f"\n根目标：{root_request_for_outputs[:1200]}"
                f"\n已有文件：{'; '.join(parsed_files)[:1200] or str((parsed or {}).get('files') or '')[:1200]}"
            ),
            "question": "从已有方法综述中深挖可验证突破口。",
            "reason": "literature_review_requires_curiosity_followup",
        }
    elif event.type == EventType.CURIOSITY_EXPLORE and not _stop_after_completion(payload):
        decision = {
            "continue": True,
            "event_type": EventType.ARTIFACT_BUILD.value,
            "event_kind": "curiosity_summary_artifact",
            "objective": (
                "把已有综述和深入探索结果整理成用户可读的最终交付物；"
                "必须包含方案对比、证据边界、可执行假设、最小验证实验和失败风险。"
                f"\n根目标：{root_request_for_outputs[:1200]}"
            ),
            "question": "整理最终突破方案交付物。",
            "reason": "curiosity_exploration_ready_for_final_artifact",
        }
    elif event.type == EventType.VISUALIZATION and (
        _has_unfinished_requested_output(root_request_for_outputs, parsed_files, "pdf")
        or _has_unfinished_requested_output(root_request_for_outputs, parsed_files, "report")
    ):
        decision = {
            "continue": True,
            "event_type": EventType.PDF_REPORT.value,
            "event_kind": "requested_report_after_visualization",
            "objective": (
                "基于已经获取的数据和本轮/历史图表文件生成用户要求的报告文件；"
                "报告必须包含真实数据、分析结论和图表路径，不能声称不存在的附件已生成。"
                f"\n根目标：{root_request_for_outputs[:1200]}"
            ),
            "question": "把数据分析和图表整理成最终报告。",
            "reason": "requested_report_not_delivered_after_visualization",
        }
    elif event.type in {EventType.PROJECT_THINK, EventType.OBJECTIVE_REVIEW}:
        decision = await asyncio.to_thread(_followup_event_decision_with_llm, event, title, parsed, payload)
    else:
        decision = await asyncio.to_thread(_followup_event_decision_with_llm, event, title, parsed, payload)
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
    followup_title = title
    if _is_generic_project_title(followup_title) and root_request:
        followup_title = _compact_title_from_request(root_request, fallback=followup_title)
    final_expected_artifacts = payload.get("root_expected_artifacts") or payload.get("expected_artifacts") or []
    if selected_type in {EventType.PROJECT_THINK, EventType.OBJECTIVE_REVIEW, EventType.HABIT_UPDATE, EventType.CURIOSITY_EXPLORE}:
        current_expected_artifacts = [{"type": "message", "pattern": "text", "description": "下一步 event 计划", "required": True}]
        root_expected_artifacts = final_expected_artifacts
    else:
        current_expected_artifacts = final_expected_artifacts
        root_expected_artifacts = []
    await pool.put(MindEvent(
        type=selected_type,
        priority=max(2, min(8, int(payload.get("priority") or 4) + 1)),
        payload={
            "title": followup_title,
            "step": int(payload.get("step") or 0) + 1,
            "delivery_mode": "research_project",
            "user_request": decision["objective"],
            "root_user_request": (root_request or user_request)[:1800],
            "event_type": selected_type.value,
            "event_kind": decision["event_kind"],
            "stop_after_completion": selected_type == EventType.STOP_PROJECT,
            "curiosity_depth": depth,
            "parent_user_request": str(payload.get("user_request") or "")[:1600],
            "followup_question": decision.get("question", ""),
            "followup_reason": decision.get("reason", ""),
            "previous_next_action": str(parsed.get("next_action") or "")[:900],
            "task_id": str(payload.get("task_id") or "")[:80],
            "task_working_dir": str(payload.get("task_working_dir") or "")[:500],
            "continue_from_project": str(payload.get("continue_from_project") or "")[:200],
            "delivery_required": bool(payload.get("delivery_required") or final_expected_artifacts),
            "expected_artifacts": current_expected_artifacts,
            "root_expected_artifacts": root_expected_artifacts,
            "artifact_freshness_policy": str(payload.get("artifact_freshness_policy") or "new")[:40],
            "reuse_existing_artifact": bool(payload.get("reuse_existing_artifact")),
            "reuse_reason": str(payload.get("reuse_reason") or "")[:500],
            "source_files": parsed_files[:8],
        },
        source=f"{event.type.value}:selector_followup",
        parent_id=event.id,
    ))
    if selected_type != EventType.STOP_PROJECT:
        try:
            from ..projects.project_state import set_project_status
            set_project_status(_workspace, followup_title, "active", f"selector follow-up：{selected_type.value}/{decision['event_kind']}")
        except Exception as exc:
            logger.debug(f"[FOLLOWUP] failed to mark active: {exc}")
    logger.info(f"[FOLLOWUP] queued {selected_type.value} for {followup_title}: {decision['event_kind']} depth={depth}")
    return {
        "queued": True,
        "event_type": selected_type.value,
        "event_kind": decision["event_kind"],
        "reason": decision.get("reason", ""),
    }


async def _enqueue_minimal_research_event_after_planning_failure(
    event: MindEvent,
    title: str,
    parsed: dict,
    payload: dict,
) -> dict:
    root_request = _root_user_request(payload) or str(payload.get("user_request") or title)
    if not root_request.strip() or not _is_open_research_goal(root_request):
        return {"queued": False, "event_type": "", "event_kind": "", "reason": "root goal is not an open research task"}
    pool = await ensure_pool()
    final_expected_artifacts = payload.get("root_expected_artifacts") or payload.get("expected_artifacts") or []
    await pool.put(MindEvent(
        type=EventType.LITERATURE_REVIEW,
        priority=max(2, min(8, int(payload.get("priority") or 4) + 1)),
        payload={
            "title": title,
            "step": int(payload.get("step") or 0) + 1,
            "delivery_mode": "research_project",
            "user_request": (
                "上游规划事件没有产出可解析的下一步计划。不要重复规划，直接执行一个最小可验证的资料整理切片："
                "围绕根目标收集并整理方法、证据、指标、效果边界和可推进的突破方向；"
                "必须生成当前任务要求的文件型交付物或明确的部分草案。"
                f"\n根目标：{root_request[:1400]}"
            ),
            "root_user_request": root_request[:1800],
            "event_type": EventType.LITERATURE_REVIEW.value,
            "event_kind": "planning_failure_min_research_slice",
            "stop_after_completion": False,
            "parent_user_request": str(payload.get("user_request") or "")[:1600],
            "previous_next_action": str((parsed or {}).get("next_action") or "")[:900],
            "task_id": str(payload.get("task_id") or "")[:80],
            "task_working_dir": str(payload.get("task_working_dir") or "")[:500],
            "continue_from_project": str(payload.get("continue_from_project") or "")[:200],
            "delivery_required": bool(payload.get("delivery_required") or final_expected_artifacts),
            "expected_artifacts": final_expected_artifacts,
            "root_expected_artifacts": [],
            "artifact_freshness_policy": str(payload.get("artifact_freshness_policy") or "new")[:40],
            "reuse_existing_artifact": bool(payload.get("reuse_existing_artifact")),
            "reuse_reason": str(payload.get("reuse_reason") or "")[:500],
        },
        source=f"{event.type.value}:planning_failure_min_research_slice",
        parent_id=event.id,
    ))
    try:
        from ..projects.project_state import set_project_status
        set_project_status(_workspace, title, "active", "planning failure fallback：literature_review/planning_failure_min_research_slice")
    except Exception as exc:
        logger.debug(f"[FOLLOWUP] failed to mark minimal research fallback active: {exc}")
    logger.warning("[FOLLOWUP] queued minimal literature_review after planning failure for %s", title)
    return {
        "queued": True,
        "event_type": EventType.LITERATURE_REVIEW.value,
        "event_kind": "planning_failure_min_research_slice",
        "reason": "project_think failed to produce a parseable plan; queued minimal research slice",
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
            "\n好奇探索输出模板要求："
            "\n1. 证据矩阵：列出候选方案/方法、证据来源或证据缺口、已知指标、适用场景、可信度等级。"
            "\n2. 瓶颈诊断：列出至少 3 个限制效果或泛化的关键瓶颈。"
            "\n3. 突破假设表：至少 3 条，每条包含 why / data_or_tool / minimal_test / expected_metric / failure_boundary。"
            "\n4. 下一步最小 event：写清楚应该继续 literature_review、data_analysis、evidence_audit、artifact_build 或 pdf_report 中哪一个，并给出验收标准。\n"
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
        "如果缺少地点、文件路径、数据范围等关键参数，不要擅自补全，先说明缺失并停止。\n"
        "【关键规则】如果任务涉及的文件（如 .h5ad、.csv、.json 等）不存在：\n"
        "  1. 必须明确报告哪个文件找不到，不能编造文件存在或编造数据\n"
        "  2. 不能自己假设文件大小、内容、包含哪些列或细胞类型\n"
        "  3. 如果找不到文件，ACTION 写 file_not_found，DONE 写缺少什么，NEXT 写请用户提供正确路径\n"
        "  4. 绝对不能输出其他无关任务（如天气查询）的产物或缓存文件\n"
        "  5. REPORT 中引用的所有文件，必须是本轮任务实际生成的真实文件\n"
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
        os.path.join(_workspace, "config", "email_config.json"),
        os.path.join(_workspace, "email_config.json"),
        os.path.join(_workspace, "config", "partner_config.json"),
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
    path = os.path.join(_workspace, "config", "email_config.json")
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
            if rel.startswith(("system/hermes_home/", "logs/", "state/record/", "state/")):
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
    from ..projects.project_state import get_project_dir, get_project_status, read_state_md, write_state_md

    event_source = str(getattr(event, "source", "") or "")
    if event.type != EventType.STOP_PROJECT and not event_source.startswith("interaction:"):
        try:
            status = get_project_status(_workspace, title)
        except Exception:
            status = ""
        if status in {"waiting", "done"}:
            logger.info(
                "[ACTION] skip stale event for stopped project: %s/%s title=%s status=%s",
                event.type.value,
                str(payload.get("event_kind") or ""),
                title,
                status,
            )
            logger.info(f"[MIND] DONE event_type={event.type.value}, id={event.id[:8]}")
            return

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
        await _event_completion_receipt_async(
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


def _batch_plan_step_label(step: Any) -> str:
    step_id = str(getattr(step, "id", "") or "").strip()
    event_type = str(getattr(step, "event_type", "") or "").strip()
    params = getattr(step, "parameters", {}) or {}
    if not isinstance(params, dict):
        params = {}
    depends = ", ".join(str(x) for x in (getattr(step, "depends_on", None) or [])) or "无依赖"
    label = event_type
    if event_type in {"atomic_execute_skill", "call_agent_skill"}:
        skill = str(params.get("skill") or params.get("skill_name") or "").strip()
        inputs = params.get("inputs") if isinstance(params.get("inputs"), dict) else {}
        query = str(
            inputs.get("query")
            or inputs.get("keywords")
            or inputs.get("question")
            or inputs.get("topic")
            or ""
        ).strip()
        label = skill or event_type
        if query:
            label += f"：{_clip(query, 56)}"
    elif event_type == "smart_llm_structured_action":
        instruction = str(params.get("instruction") or params.get("task") or params.get("objective") or "").strip()
        label = "结构化整理"
        if instruction:
            label += f"：{_clip(instruction, 56)}"
    elif event_type in {"atomic_write_artifact", "atomic_json_table_artifact"}:
        filename = str(params.get("filename") or params.get("path") or params.get("output_file") or "").strip()
        label = f"生成文件：{filename or event_type}"
    elif event_type == "pubmed_search":
        query = str(params.get("query") or params.get("term") or "").strip()
        label = "PubMed 检索" + (f"：{_clip(query, 56)}" if query else "")
    return f"{step_id}: {label}（依赖：{depends}）"


def _should_run_experiment(root_request: str, payload: dict) -> bool:
    """Check if experiment trigger keywords are in root_request and experiment phase not yet run."""
    if payload.get("_experiment_phase") or payload.get("metadata", {}).get("_experiment_phase"):
        return False
    if not root_request:
        return False
    try:
        exp_cfg = _load_yaml_named_config("experiment.yaml", {"enabled": False, "trigger_keywords": []})
        if not exp_cfg.get("enabled"):
            return False
        keywords = exp_cfg.get("trigger_keywords", [])
        if not isinstance(keywords, list):
            return False
        request_lower = root_request.lower()
        for keyword in keywords:
            if isinstance(keyword, str) and keyword.lower() in request_lower:
                logger.info("[EXPERIMENT] trigger keyword '%s' found in root_request", keyword)
                return True
    except Exception as exc:
        logger.debug("[EXPERIMENT] config load failed: %s", exc)
    return False


async def _handle_batch_plan_event(event: MindEvent):
    """Top-level batch planner: plan once, execute Harness, then optional curiosity fills."""
    # Dedup guard: if this event_id is already in-flight, skip this event
    if event.id in _batch_plan_inflight:
        logger.info("[BATCH_PLANNER] dedup: skipping duplicate event %s", event.id[:12])
        return
    _batch_plan_inflight.add(event.id)
    # Content-based dedup: if a batch_plan with the same title was completed
    # within the last 60 seconds, skip this event. Prevents duplicate execution
    # from dual USER_MESSAGE sources (QQ + desktop_inbox).
    root_request = _root_user_request(event.payload or {}) or str((event.payload or {}).get("user_request") or "")
    title = str((event.payload or {}).get("title") or (event.payload or {}).get("project") or "").strip()
    dedup_key = title or root_request[:80]
    if dedup_key and dedup_key in _batch_plan_recently_completed:
        elapsed = _time.time() - _batch_plan_recently_completed[dedup_key]
        if elapsed < 60:
            logger.info("[BATCH_PLANNER] content-based dedup: '%s' completed %.0fs ago, skipping", dedup_key[:60], elapsed)
            return
    # Clean Hermes working directory to prevent stale artifacts from previous
    # tasks leaking into the new batch plan (e.g., weather files appearing in
    # a cytobridge analysis task).
    try:
        hermes_work = os.path.join(_workspace, "system", "hermes_work")
        if os.path.isdir(hermes_work):
            for fname in os.listdir(hermes_work):
                fpath = os.path.join(hermes_work, fname)
                if os.path.isfile(fpath) and not fname.startswith("."):
                    os.remove(fpath)
            logger.info("[BATCH_PLANNER] cleaned hermes_work directory")
    except Exception as exc:
        logger.debug("[BATCH_PLANNER] hermes_work cleanup failed: %s", exc)

    payload = event.payload or {}
    root_request = _root_user_request(payload) or str(payload.get("user_request") or "")
    title = str(payload.get("title") or payload.get("project") or payload.get("event_kind") or event.type.value).strip()
    if _is_generic_project_title(title) and root_request:
        title = _compact_title_from_request(root_request, fallback=title)
        payload["title"] = title
    if not title:
        title = event.type.value
    visible_kind = str(payload.get("event_kind") or "").strip()
    if not visible_kind or visible_kind.startswith("routing_prefer_"):
        visible_kind = title if event.type != EventType.DIRECT_REPLY else "direct"
        payload["event_kind"] = visible_kind
    logger.info("[BATCH_PLANNER] executing batch_plan title=%s kind=%s", title[:80], payload.get("event_kind") or "")

    from ..harness_core import ArtifactValidator, TaskInstance, load_harness_config
    from ..planner import BatchPlanner
    from ..projects.project_state import get_project_dir, read_state_md, _write_active_plan
    from .harness import default_registry, run_harness_plan

    project_dir = get_project_dir(_workspace, title)
    os.makedirs(project_dir, exist_ok=True)
    state_md = read_state_md(_workspace, title)
    started_at = _time.time()
    spec = _action_event_spec(event.type)
    artifact_path = os.path.join(project_dir, str(spec.get("artifact") or "batch_plan_result.md"))
    required_exts = _required_output_exts(root_request, event.type.value, str(payload.get("event_kind") or ""))
    # For literature_review and similar research events, always require delivery
    if event.type in {EventType.LITERATURE_REVIEW} and not required_exts:
        required_exts.add(".md")
    if required_exts:
        payload["expected_artifacts"] = _align_expected_artifacts_with_required_exts(
            payload.get("expected_artifacts"),
            required_exts,
        )
        payload["delivery_required"] = True
    else:
        payload["expected_artifacts"] = _normalize_batch_expected_artifacts_for_request(
            payload.get("expected_artifacts"),
            root_request,
        )
    task = TaskInstance.load_or_create(
        _workspace,
        task_id=str(payload.get("task_id") or ""),
        user_message=root_request or title,
        continue_from_project=str(payload.get("continue_from_project") or ""),
        metadata={
            "title": title,
            "event_type": event.type.value,
            "event_kind": visible_kind,
            "source": "batch_plan",
        },
    )
    payload["task_id"] = task.task_id
    payload["task_working_dir"] = task.working_dir
    payload["artifact_freshness_policy"] = str(payload.get("artifact_freshness_policy") or "new")
    if isinstance(payload.get("expected_artifacts"), list):
        task.update_expected_artifacts(payload.get("expected_artifacts") or [])
    registry = default_registry()
    parsed: dict = {}
    result = None
    total_llm_calls = 0
    planned_steps = 0
    aggregate_planned_steps = 0
    aggregate_completed_steps = 0
    check_result: dict = {}
    core_step_failed_across_iterations = False  # Tracks if ANY iteration had a core agent failure
    try:
        iteration_cfg = _load_yaml_named_config("iteration.yaml", _DEFAULT_ITERATION_CONFIG)
        configured_expected = iteration_cfg.get("expected_artifacts") if isinstance(iteration_cfg.get("expected_artifacts"), list) else []
        if configured_expected:
            merged_expected = list(getattr(task, "expected_artifacts", []) or [])
            seen_expected = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in merged_expected if isinstance(item, dict)}
            for item in configured_expected:
                if not isinstance(item, dict):
                    continue
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if key not in seen_expected:
                    merged_expected.append(item)
                    seen_expected.add(key)
            task.update_expected_artifacts(merged_expected)
        progress_updates = bool(iteration_cfg.get("progress_updates", True))
        max_iterations = max(1, int(iteration_cfg.get("max_iterations") or 3))
        batch_planner = BatchPlanner.from_workspace(_workspace)
        # ── Clean up old deliverable/output dirs that might contain cached results ──
        try:
            for old_dir in ("output", "deliverables", "cytobridge_output"):
                old_path = os.path.join(_workspace, old_dir)
                if os.path.isdir(old_path):
                    import shutil
                    shutil.rmtree(old_path, ignore_errors=True)
                    logger.info("[BATCH_PLANNER] cleaned old output dir: %s", old_path)
        except Exception as exc:
            logger.debug("[BATCH_PLANNER] output dir cleanup failed: %s", exc)
        # Also clean stale task dirs older than 1 hour
        try:
            now_ts = _time.time()
            tasks_dir = os.path.join(_workspace, "state", "tasks")
            if os.path.isdir(tasks_dir):
                for tname in os.listdir(tasks_dir):
                    tpath = os.path.join(tasks_dir, tname)
                    if os.path.isdir(tpath):
                        try:
                            mtime = os.path.getmtime(tpath)
                            if now_ts - mtime > 3600:  # older than 1 hour
                                import shutil
                                shutil.rmtree(tpath, ignore_errors=True)
                        except Exception:
                            pass
        except Exception as exc:
            logger.debug("[BATCH_PLANNER] stale task cleanup failed: %s", exc)
        # ── Retrieve relevant historical experiences (unified SQLite) ─────
        relevant_experiences = ""
        try:
            from ..meta.learning import format_experiences_for_prompt
            relevant_experiences = format_experiences_for_prompt(
                root_request or str(payload.get("user_request") or title),
                max_experiences=3,
            )
            if relevant_experiences:
                task.append_log("experience_match_found", {
                    "source": "sqlite_fts",
                    "length": len(relevant_experiences),
                })
        except Exception as exc:
            logger.debug("[EXPERIENCE] matching failed: %s", exc)

        # ── Retrieve growth milestones ──────────────────────────────
        growth_context = ""
        try:
            from ..meta.learning import format_growth_for_prompt
            growth_context = format_growth_for_prompt(max_events=2)
        except Exception as exc:
            logger.debug("[GROWTH] loading failed: %s", exc)
        micro_plan, planner_calls = await batch_planner.plan(
            adapter=_adapter,
            user_message=root_request or str(payload.get("user_request") or title),
            task_instance=task,
            registry=registry,
            state_md=state_md if task.continue_from_project else "",
            relevant_experiences=relevant_experiences,
            growth_context=growth_context,
            event_type=event.type.value,
        )
        if not _user_prefers_pdf():
            micro_plan.expected_artifacts = _normalize_batch_expected_artifacts_for_request(
                micro_plan.expected_artifacts,
                root_request or title,
            )
            # Also filter out PDF-conversion plan steps when user doesn't prefer PDF
            micro_plan.plan = [s for s in micro_plan.plan if s.event_type not in ("atomic_convert_md_to_pdf",)]
        pending_plan = micro_plan
        pending_plan_calls = planner_calls
        planned_steps = len(micro_plan.plan)
        aggregate_planned_steps = planned_steps
        # Write plan phases to active_plan.json for external monitoring
        try:
            phases = []
            for step in micro_plan.plan:
                phase = {
                    "id": step.id,
                    "event_type": step.event_type,
                    "summary": _batch_plan_step_label(step),
                    "status": "pending",
                    "parameters": dict(getattr(step, "parameters", {})),
                }
                phases.append(phase)
            now_ts = datetime.now().isoformat()
            plan_data = {
                "status": "active",
                "title": title,
                "goal": root_request or title,
                "created_at": now_ts,
                "current_phase_index": 0,
                "phases": phases,
                "last_heartbeat": now_ts,
                "heartbeat_summary": f"批规划完成：{title}，共 {planned_steps} 步",
            }
            _write_active_plan(_workspace, plan_data)
            # Save pipeline snapshot to conversations/<round_id>/pipeline.json
            try:
                round_id = now_ts.replace(":", "-").replace(".", "-")
                conv_dir = os.path.join(os.path.dirname(os.path.dirname(_workspace)), "conversations", round_id)
                os.makedirs(conv_dir, exist_ok=True)
                pipeline_path = os.path.join(conv_dir, "pipeline.json")
                with open(pipeline_path, "w", encoding="utf-8") as pf:
                    pf.write(__import__("json").dumps(plan_data, indent=2, ensure_ascii=False))
            except Exception as exc:
                logger.debug("[BATCH_PLANNER] failed to save pipeline snapshot: %s", exc)
        except Exception as exc:
            logger.debug("[BATCH_PLANNER] failed to write active_plan: %s", exc)
        plan_lines = []
        for step in micro_plan.plan[:6]:
            plan_lines.append(_batch_plan_step_label(step))
        if planned_steps > 6:
            plan_lines.append(f"... 其余 {planned_steps - 6} 步")
        next_event_name = getattr(micro_plan.plan[0], "event_type", "none") if micro_plan.plan else "none"
        if progress_updates:
            wm_note = ""
            if isinstance(getattr(task, "metadata", None), dict):
                wm_note = task.metadata.get("world_model_status", "")
            # Dedup: skip if we already announced this plan for this task
            _plan_announced = getattr(task, "_plan_announced", False)
            if not _plan_announced:
                task._plan_announced = True
                plan_msg = send_template("plan_ready", total=planned_steps, next_event=next_event_name)
                if wm_note:
                    plan_msg += "\n" + wm_note
                plan_msg += ("\n" + ";".join(plan_lines[:4]) if plan_lines else "")
                await _enqueue_visible_report(
                    plan_msg,
                EventType.BATCH_PLAN,
                event_kind=visible_kind,
                priority=2,
                source="batch_plan:plan_ready",
                parent_id=event.id,
                bypass_rate_limit=True,
            )
        for iteration in range(max_iterations):
            # Generate acceptance criteria on first iteration
            if iteration == 0:
                ac_cfg = iteration_cfg.get("acceptance_criteria") if isinstance(iteration_cfg.get("acceptance_criteria"), dict) else {}
                if ac_cfg.get("enabled", True) and _adapter:
                    try:
                        generator = AcceptanceCriteriaGenerator(
                            workspace=_workspace,
                            adapter=_adapter,
                            config=ac_cfg,
                        )
                        criteria = await generator.generate(
                            user_message=root_request or title,
                            task_instance=task,
                        )
                        # Store in task metadata for persistence
                        if criteria and isinstance(task.metadata, dict):
                            task.metadata["acceptance_criteria"] = criteria
                            task.save()
                            await _enqueue_visible_report(
                                f"已根据用户目标生成动态验收标准（{len(criteria.split(chr(10)))} 条）",
                                EventType.BATCH_PLAN,
                                event_kind=visible_kind,
                                priority=2,
                                source="batch_plan:acceptance_criteria",
                                parent_id=event.id,
                                bypass_rate_limit=True,
                            )
                            logger.info("[ACCEPTANCE] criteria stored for task_id=%s", task.task_id)
                    except Exception as exc:
                        logger.warning("[ACCEPTANCE] generation failed: %s", exc)
                        task.append_log("acceptance_criteria_failed", {"error": str(exc)})

            logger.info("[ITERATION] batch_plan iteration=%s/%s task_id=%s", iteration + 1, max_iterations, task.task_id)
            # Store current plan in metadata before execution (for reflect to reference)
            if isinstance(task.metadata, dict):
                task.metadata["last_plan"] = [step.__dict__ for step in pending_plan.plan]
                task.metadata["last_expected_artifacts"] = list(pending_plan.expected_artifacts or [])
                task.save()
            task.append_log("iteration_started", {
                "iteration": iteration,
                "max_iterations": max_iterations,
                "plan_steps": [step.__dict__ for step in pending_plan.plan],
            })
            async def progress_callback(update: dict) -> None:
                if not progress_updates:
                    return
                phase = str(update.get("phase") or "")
                if phase == "parallel_start":
                    descriptions = update.get("descriptions") if isinstance(update.get("descriptions"), list) else []
                    if descriptions:
                        lines = []
                        for item in descriptions[:6]:
                            if not isinstance(item, dict):
                                continue
                            step_id = str(item.get("id") or "").strip()
                            desc = str(item.get("description") or item.get("event_type") or "").strip()
                            deps = item.get("depends_on") if isinstance(item.get("depends_on"), list) else []
                            dep_text = "无依赖，可并行" if not deps else "等待 " + ", ".join(str(x) for x in deps)
                            lines.append(f"{step_id}: {desc}（{dep_text}）")
                        if len(descriptions) > 6:
                            lines.append(f"... 其余 {len(descriptions) - 6} 步")
                        message = send_template("parallel", items="；".join(lines))
                    else:
                        message = send_template("parallel", items=", ".join(str(x) for x in update.get("step_ids") or []))
                    await _enqueue_visible_report(
                        message,
                        EventType.BATCH_PLAN,
                        event_kind=visible_kind,
                        priority=2,
                        source="batch_plan:parallel_progress",
                        parent_id=event.id,
                        bypass_rate_limit=True,
                    )
                    return
                if phase == "step_start":
                    deps = update.get("depends_on") or []
                    dep_text = "无依赖，可直接执行" if not deps else "等待：" + ", ".join(str(x) for x in deps)
                    await _enqueue_visible_report(
                        send_template(
                            "progress",
                            current=update.get("ordinal"),
                            total=update.get("total_steps"),
                            description=f"{update.get('description')}；{dep_text}",
                        ),
                        EventType.BATCH_PLAN,
                        event_kind=visible_kind,
                        priority=2,
                        source="batch_plan:step_start",
                        parent_id=event.id,
                        bypass_rate_limit=True,
                    )
                    # Update active_plan.json phase status to running
                    try:
                        _update_active_plan_phase(_workspace, update.get("ordinal", 1) - 1, "running")
                    except Exception:
                        pass
                    return
                if phase == "step_complete":
                    files_payload = update.get("files") if isinstance(update.get("files"), list) else []
                    files_text = ""
                    if files_payload:
                        cleaned_files = []
                        for x in files_payload[:3]:
                            base = os.path.basename(str(x))
                            if base in ("task_instance.json",):
                                continue
                            cleaned_files.append(base)
                        if cleaned_files:
                            files_text = "；产出：" + ", ".join(cleaned_files)
                    summary = str(update.get("summary") or "").strip()
                    await _enqueue_visible_report(
                        send_template(
                            "progress_done",
                            current=update.get("ordinal"),
                            total=update.get("total_steps"),
                            description=update.get("description"),
                            summary=(f"；{summary}" if summary else "") + files_text,
                        ),
                        EventType.BATCH_PLAN,
                        event_kind=visible_kind,
                        priority=2,
                        source="batch_plan:step_complete",
                        parent_id=event.id,
                        bypass_rate_limit=True,
                    )
                    # Update active_plan.json phase status to completed/failed
                    try:
                        step_ok = update.get("ok", True)
                        phase_status = "completed" if step_ok else "failed"
                        _update_active_plan_phase(
                            _workspace,
                            update.get("ordinal", 1) - 1,
                            phase_status,
                            output_summary=update.get("summary"),
                            error=None if step_ok else update.get("summary", ""),
                            elapsed=str(round(update.get("elapsed_sec", 0), 1)) + "s" if update.get("elapsed_sec") else None,
                        )
                    except Exception:
                        pass

            result = await run_harness_plan(
                workspace=_workspace,
                event=event,
                title=title,
                project_dir=project_dir,
                state_md=state_md,
                artifact_path=artifact_path,
                adapter=_adapter,
                build_action_prompt=_build_action_event_prompt,
                parse_structured_response=_parse_structured_project_response,
                micro_plan=pending_plan,
                planner_llm_calls=pending_plan_calls,
                progress_callback=progress_callback,
            )
            total_llm_calls += int(result.llm_calls or 0)
            aggregate_completed_steps += len(getattr(result, "step_results", {}) or {})
            # Track core agent failures across ALL iterations (not just the last)
            if not core_step_failed_across_iterations and getattr(result, "step_results", None):
                for _sid, _sr in result.step_results.items():
                    if isinstance(_sr, dict):
                        _et = str(_sr.get("event_type") or "")
                        if _et == "call_agent_skill" and not bool(_sr.get("ok", True)):
                            core_step_failed_across_iterations = True
                            break
            # Persist step_results into task.metadata so the learning pipeline
            # can find them via extract_learning_from_task()
            if result.step_results and isinstance(task.metadata, dict):
                task.metadata["step_results"] = dict(result.step_results)
                task.save()
            if result.parsed:
                parsed = dict(result.parsed or parsed or {})
            check_result = await _run_comprehensive_evaluation(task, root_request or title, iteration_cfg)
            if progress_updates:
                await _enqueue_visible_report(
                    _format_iteration_execution_summary(iteration + 1, result, check_result),
                    EventType.BATCH_PLAN,
                    event_kind=visible_kind,
                    priority=2,
                    source="batch_plan:iteration_summary",
                    parent_id=event.id,
                    bypass_rate_limit=True,
                )
            if core_step_failed_across_iterations:
                logger.info("[ITERATION] core agent step failed; stopping iterations task_id=%s iteration=%s", task.task_id, iteration)
                if progress_updates:
                    await _enqueue_visible_report(
                        "核心 Agent 步骤失败，不再重新规划。请检查环境和配置。",
                        EventType.CHECK,
                        event_kind=visible_kind,
                        priority=3,
                        source="batch_plan:core_failed",
                        parent_id=event.id,
                        bypass_rate_limit=True,
                    )
                break
            if check_result.get("satisfied"):
                logger.info("[CHECK] satisfied; stopping iteration task_id=%s iteration=%s", task.task_id, iteration)
                if progress_updates:
                    await _enqueue_visible_report(
                        send_template("check_passed"),
                        EventType.CHECK,
                        event_kind=visible_kind,
                        priority=2,
                        source="batch_plan:check_passed",
                        parent_id=event.id,
                        bypass_rate_limit=True,
                    )
                break
            if progress_updates:
                await _enqueue_visible_report(
                    send_template(
                        "check_failed",
                        missing_summary="、".join(str(x) for x in (check_result.get("missing") or [])[:6]) or "仍有信息缺口",
                    ),
                    EventType.CHECK,
                    event_kind=visible_kind,
                    priority=2,
                    source="batch_plan:check_failed",
                    parent_id=event.id,
                    bypass_rate_limit=True,
                )
            if iteration >= max_iterations - 1:
                logger.info("[CHECK] not satisfied but max_iterations reached task_id=%s", task.task_id)
                if progress_updates:
                    await _enqueue_visible_report(
                        "检查未通过且已达到最大迭代次数，任务已停止。核心步骤可能失败了，需要你确认环境和配置。",
                        EventType.CHECK,
                        event_kind=visible_kind,
                        priority=3,
                        source="batch_plan:max_iterations",
                        parent_id=event.id,
                        bypass_rate_limit=True,
                    )
                break
            gap = await _run_root_cause_diagnosis(task, root_request or title, check_result, iteration_cfg)
            patches = gap.get("patches") if isinstance(gap, dict) and isinstance(gap.get("patches"), list) else []
            missing_items = gap.get("missing_items") if isinstance(gap, dict) and isinstance(gap.get("missing_items"), list) else []
            # If diagnosis says to ask the user, stop iteration and notify
            reflection_text = (gap.get("reflection") or "") if isinstance(gap, dict) else ""
            if "【需询问用户】" in reflection_text:
                logger.info("[REFLECT] diagnosis indicates user input needed; stopping iteration task_id=%s", task.task_id)
                if progress_updates:
                    # Send the ask-user message to the user
                    user_msg = reflection_text.replace("【需询问用户】", "").strip() or "任务执行遇到问题，需要你帮助确认。"
                    await _enqueue_visible_report(
                        f"需要你的帮助：{user_msg}",
                        EventType.CHECK,
                        event_kind=visible_kind,
                        priority=2,
                        source="batch_plan:ask_user",
                        parent_id=event.id,
                        bypass_rate_limit=True,
                    )
                break
            if not gap or (not patches and not missing_items):
                logger.info("[REFLECT] no actionable gap; stopping iteration task_id=%s", task.task_id)
                break
            if progress_updates:
                await _enqueue_visible_report(
                    send_template(
                        "reflect",
                    missing_summary="、".join(str(x) for x in missing_items[:5]) or (f"生成 {len(patches)} 个补丁步骤" if patches else "证据和章节完整性不足"),
                    next_focus=gap.get("reflection") or "补齐检查发现的缺口",
                ),
                EventType.REFLECT,
                event_kind=visible_kind,
                priority=2,
                source="batch_plan:reflect_gap",
                parent_id=event.id,
                bypass_rate_limit=True,
            )
            try:
                pending_plan, pending_plan_calls = await _run_plan_redesign(
                    task,
                    root_request or title,
                    gap,
                    registry,
                    iteration_cfg,
                )
                if not _user_prefers_pdf():
                    pending_plan.expected_artifacts = _normalize_batch_expected_artifacts_for_request(
                        pending_plan.expected_artifacts,
                        root_request or title,
                    )
                    pending_plan.plan = [s for s in pending_plan.plan if s.event_type not in ("atomic_convert_md_to_pdf",)]
                planned_steps = len(pending_plan.plan)
                aggregate_planned_steps += planned_steps
                if progress_updates:
                    await _enqueue_visible_report(
                        send_template(
                            "curiosity",
                        total=len(pending_plan.plan),
                        focus=gap.get("reflection") or "补齐上一轮检查发现的缺口",
                    ),
                    EventType.CURIOSITY,
                    event_kind=visible_kind,
                    priority=2,
                    source="batch_plan:curiosity_plan_ready",
                    parent_id=event.id,
                    bypass_rate_limit=True,
                )
            except Exception as exc:
                logger.warning("[CURIOSITY] supplement planning failed task_id=%s: %s", task.task_id, exc)
                task.append_log("iteration_curiosity_failed", {"error": str(exc)})
                break
    except asyncio.CancelledError:
        logger.info("[BATCH_PLANNER] batch_plan %s cancelled (interrupted by new user message)", title[:60])
        try:
            from ..projects.project_state import clear_active, set_project_status
            set_project_status(_workspace, title, "interrupted", "new user message arrived")
            clear_active(_workspace, title)
        except Exception:
            pass
        await _enqueue_visible_report(
            f"已停止当前任务，开始处理新的请求",
            event.type,
            event_kind=visible_kind,
            priority=3,
            source="batch_plan:interrupted",
            parent_id=event.id,
        )
        return
    except Exception as exc:
        logger.warning("[BATCH_PLANNER] batch_plan failed for %s: %s", title, exc)
        task.append_log("batch_plan_handler_failed", {"error": str(exc)})
        parsed = {
            "action": "batch_plan",
            "action": "batch_plan",
            "step_done": "批量规划执行失败",
            "findings": [f"错误：{exc}"],
            "evidence": "system:batch_plan",
            "next_action": "等待用户重新发起或补充外部条件。",
            "state_delta": f"batch_plan failed task_id={task.task_id}",
            "files": "EMPTY",
            "artifact_content": "EMPTY",
            "delivery_status": "failed",
        }
        # Emit TASK_FAILED event with diagnostic payload
        try:
            failed_acceptance = None
            if isinstance(task.metadata, dict):
                ac = task.metadata.get("acceptance_criteria")
                if ac:
                    failed_acceptance = str(ac)[:500]
            task_failed_event = MindEvent(
                type=EventType.TASK_FAILED,
                priority=3,
                payload={
                    "task_title": title,
                    "failed_at_step": "batch_plan_handler",
                    "error": str(exc),
                    "total_steps": aggregate_planned_steps,
                    "skipped_steps": max(0, aggregate_planned_steps - aggregate_completed_steps),
                    "acceptance_report": failed_acceptance,
                    "source": f"{event.type.value}:handler_exception",
                    "sender_id": event.id,
                },
                source="batch_plan:handler_exception",
                parent_id=event.id,
            )
            await _event_queue.put(task_failed_event)
        except Exception as put_exc:
            logger.debug("[TASK_FAILED] enqueue failed: %s", put_exc)

    payload["harness_managed"] = True
    payload["harness_llm_calls"] = total_llm_calls
    payload["harness_ok"] = bool(result.ok) if result is not None else False

    # If the harness run failed (e.g., file_not_found from an action step),
    # extract the specific error and report it to the user immediately,
    # instead of continuing to try PDF generation on empty results.
    if not payload["harness_ok"] and result is not None:
        error_reason = str(getattr(result, "reason", "") or getattr(result, "error", "") or "execution failed")[:300]
        logger.info("[BATCH_PLANNER] harness failed: %s", error_reason)
        parsed = {
            "action": "batch_plan",
            "step_done": f"任务执行失败：{error_reason}",
            "findings": [f"失败原因：{error_reason}"],
            "evidence": "system:batch_plan",
            "next_action": "等待用户提供正确的文件路径后重新发起任务。",
            "state_delta": f"batch_plan failed: {error_reason[:120]}",
            "files": "EMPTY",
            "artifact_content": "EMPTY",
            "delivery_status": "failed",
        }
        # Emit TASK_FAILED event with diagnostic payload
        try:
            failed_acceptance = None
            if isinstance(task.metadata, dict):
                ac = task.metadata.get("acceptance_criteria")
                if ac:
                    failed_acceptance = str(ac)[:500]
            task_failed_event = MindEvent(
                type=EventType.TASK_FAILED,
                priority=3,
                payload={
                    "task_title": title,
                    "failed_at_step": "harness",
                    "error": error_reason,
                    "total_steps": aggregate_planned_steps,
                    "skipped_steps": max(0, aggregate_planned_steps - aggregate_completed_steps),
                    "acceptance_report": failed_acceptance,
                    "source": f"{event.type.value}:harness_failed",
                    "sender_id": event.id,
                },
                source="batch_plan:harness_failed",
                parent_id=event.id,
            )
            await _event_queue.put(task_failed_event)
        except Exception as exc:
            logger.debug("[TASK_FAILED] enqueue failed: %s", exc)
        delivery_dir = task.working_dir if os.path.isdir(task.working_dir) else project_dir
        pushed, files = _push_one_shot_output_files(delivery_dir, parsed, artifact_path="", required_exts=set(), allow_workspace_fallback=False)
        await _enqueue_visible_report(
            f"任务执行失败：{error_reason}",
            event.type,
            event_kind=visible_kind,
            priority=3,
            source="batch_plan:failed",
            parent_id=event.id,
        )
        # Still go through stop_project to clean up
        await _enqueue_stop_project_event(event, title, f"等待用户提供正确文件路径：{error_reason}", payload)
        logger.info("[MIND] DONE event_type=%s, id=%s", event.type.value, event.id[:8])
        return

    delivery_dir = task.working_dir if os.path.isdir(task.working_dir) else project_dir

    # ── Report file discovery: inject .md/.pdf report files into parsed
    # BEFORE _push_one_shot_output_files so they get delivered ──
    if delivery_dir:
        _report_files = []
        for _root, _dirs, _names in os.walk(delivery_dir):
            for _n in _names:
                if not _n.endswith((".md", ".pdf")):
                    continue
                if _n.startswith("_step_") or _n in ("batch_plan_result.md", "_inline_artifact.md"):
                    continue
                _fp = os.path.join(_root, _n)
                if os.path.getsize(_fp) < 1000:
                    continue
                if "report" in _n.lower() or "trajectory" in _n.lower() or "分化" in _n or "分析" in _n:
                    _report_files.append(_fp)
        if _report_files:
            _report_files.sort(key=lambda p: -os.path.getsize(p))
            parsed["files"] = _report_files
            logger.info("[DELIVERY] injecting %d report files into parsed.files: %s",
                        len(_report_files), [os.path.basename(p) for p in _report_files])

    # ── Check if the CORE task step failed ──
    # When a call_agent_skill step (core task) failed but other steps "succeeded",
    # the LLM sees error logs and hallucinates results from training data.
    core_step_failed = False
    if result is not None:
        step_results = getattr(result, "step_results", None) or {}
        for step_id, step_result in step_results.items():
            if isinstance(step_result, dict):
                et = str(step_result.get("event_type") or "")
                ok = bool(step_result.get("ok", True))
                content = str(step_result.get("content") or "")
                if et == "call_agent_skill" and not ok:
                    core_step_failed = True
                    break
                # Also check if content contains an error message
                if not ok and any(s in content.lower() for s in ("error code:", "error occurred", "401", "incorrect api key")):
                    core_step_failed = True
                    break
    core_step_failed = core_step_failed or core_step_failed_across_iterations

    # LLM-based natural summarization of execution results (replaces PDF generation)
    # Only run summarize when core steps actually succeeded — otherwise the LLM
    # hallucinates results from agent error logs
    if _adapter and result is not None and getattr(result, "ok", False) and not core_step_failed:
        try:
            summary_prompt = (
                f"请用中文简要总结以下批量规划执行的结果。\n\n任务标题：{title}"
                f"\n用户请求：{str(root_request)[:600] if root_request else '（无）'}"
                f"\n迭代次数：{iteration}"
                f"\n规划步骤数：{aggregate_planned_steps}"
                f"\n已完成步骤数：{aggregate_completed_steps}"
                f"\n交付状态：{parsed.get('delivery_status', 'unknown')}"
            )
            if parsed.get("findings"):
                summary_prompt += f"\n关键发现：{'；'.join(str(f)[:200] for f in parsed['findings'] if str(f).strip())[:800]}"
            if parsed.get("artifact_content"):
                artifact_preview = str(parsed["artifact_content"])[:800]
                summary_prompt += f"\n\n产出内容预览：\n{artifact_preview}"
            summary = (_adapter.chat(summary_prompt, purpose="summarize") or "").strip()
            if summary and summary != USER_FRIENDLY_PROGRESS_REPLY:
                await _enqueue_visible_report(
                    f"📋 执行总结\n\n{summary}",
                    event.type,
                    event_kind=visible_kind,
                    priority=2,
                    source="batch_plan:summary",
                    parent_id=event.id,
                    bypass_rate_limit=True,
                )
        except Exception as exc:
            logger.debug("[BATCH_PLANNER] LLM summarization failed: %s", exc)
    pushed, files = _push_one_shot_output_files(
        delivery_dir,
        parsed,
        artifact_path="",
        since_ts=started_at,
        required_exts=required_exts,
        allow_workspace_fallback=False,
    )
    missing_required_output = bool(required_exts and not files)

    # ── Ensure report files (.md, .pdf) from steps 3-4 are in files list ──
    # The scan may miss report files if other files fill the 6-slot limit.
    if delivery_dir and not core_step_failed and not missing_required_output:
        _existing_names = {os.path.basename(f) for f in (files or [])}
        # Collect .md and .pdf candidates separately, then dedup: if both .md
        # and .pdf exist for the same base name, only keep the .pdf
        _md_candidates = {}
        _pdf_candidates = {}
        for _root, _dirs, _names in os.walk(delivery_dir):
            for _n in _names:
                if not _n.endswith(('.md', '.pdf')):
                    continue
                if _n in _existing_names or _n.startswith('_step_'):
                    continue
                _fp = os.path.join(_root, _n)
                if 'report' in _n.lower() or 'trajectory' in _n.lower() or '分析' in _n or '分化' in _n:
                    _base = os.path.splitext(_n)[0]
                    if _n.endswith('.md'):
                        _md_candidates[_base] = (_fp, os.path.getsize(_fp))
                    else:
                        _pdf_candidates[_base] = (_fp, os.path.getsize(_fp))
        # When both .md and .pdf exist for the same base, drop .md — user wants PDF only
        for _base in list(_md_candidates.keys()):
            if _base in _pdf_candidates:
                del _md_candidates[_base]
        _found_report = sorted(
            list(_md_candidates.values()) + list(_pdf_candidates.values()),
            key=lambda x: -x[1],
        )
        for _fp, _sz in _found_report:
            if _fp not in (files or []):
                if files is None:
                    files = []
                files.insert(0, _fp)
                logger.info("[DELIVERY] added report file: %s (%dB)", os.path.basename(_fp), _sz)

    # ── Cytobridge output discovery: copy report files from the agent's
    # output directory to the delivery directory so they're discoverable ──
    if not files:
        _cytobridge_dirs = []
        # Primary: the current task's cytobridge_output (planner's output path)
        if delivery_dir:
            _task_cb = os.path.join(delivery_dir, "cytobridge_output")
            if os.path.isdir(_task_cb):
                _cytobridge_dirs.append(_task_cb)
        # Fallback: system hermes_work path (old behavior)
        if _workspace:
            _sys_cb = os.path.join(_workspace, "system", "hermes_work", "cytobridge_output")
            if os.path.isdir(_sys_cb) and _sys_cb not in _cytobridge_dirs:
                _cytobridge_dirs.append(_sys_cb)
        for _cytobridge_output in _cytobridge_dirs:
            try:
                _found = []
                for f in os.listdir(_cytobridge_output):
                    fpath = os.path.join(_cytobridge_output, f)
                    if not os.path.isfile(fpath):
                        continue
                    # Priority: PDF > HTML > MD > rest
                    if f.endswith(".pdf"):
                        _found.append((0, fpath))
                    elif f.endswith(".html"):
                        _found.append((1, fpath))
                    elif f.endswith(".md"):
                        _found.append((2, fpath))
                    elif f.endswith((".png", ".jpg", ".csv", ".json", ".npy")):
                        _found.append((3, fpath))
                if _found:
                    _found.sort(key=lambda x: (x[0], -os.path.getctime(x[1])))
                    _best = _found[0][1]
                    _target = os.path.join(delivery_dir, os.path.basename(_best))
                    import shutil
                    shutil.copy2(_best, _target)
                    files = [_target]
                    pushed = True
                    # If we have HTML but no PDF, auto-convert to PDF
                    if os.path.basename(_best).endswith(".html"):
                        _pdf_target = os.path.join(delivery_dir, os.path.basename(_best).replace(".html", ".pdf"))
                        import subprocess as _sp2
                        _sp2.run(
                            ["wkhtmltopdf", "--enable-local-file-access", "--page-size", "A4", _target, _pdf_target],
                            capture_output=True, text=True, timeout=30,
                        )
                        if os.path.isfile(_pdf_target) and os.path.getsize(_pdf_target) > 1000:
                            files.append(_pdf_target)
                            logger.info("[DELIVERY] auto-converted HTML→PDF: %s", os.path.basename(_pdf_target))
                    # Also copy the HTML report for reference
                    _html_report = os.path.join(_cytobridge_output, "report.html")
                    if os.path.isfile(_html_report) and os.path.basename(_best) != "report.html":
                        _html_target = os.path.join(delivery_dir, "report.html")
                        shutil.copy2(_html_report, _html_target)
                    logger.info("[DELIVERY] cytobridge report %s delivered", os.path.basename(_best))
                    break  # Use first directory with files
            except Exception as _exc:
                logger.debug("[DELIVERY] cytobridge output copy failed: %s", _exc)

    if core_step_failed:
        # A core agent step failed — don't push files, send natural message instead
        files = []
        pushed = False
        # Determine specific failure reason from step results
        _core_error_msg = "核心步骤（调用 agent）执行失败，没有生成有效结果。"
        if result is not None:
            step_results = getattr(result, "step_results", None) or {}
            for _sid, _sr in step_results.items():
                if isinstance(_sr, dict):
                    _et = str(_sr.get("_error_type") or "")
                    if _et == "timeout":
                        _core_error_msg = (
                            "分析超时：cytobridge agent 执行耗时超过设定上限（7200秒）。"
                            "可能原因：数据（pancreas.h5ad 65MB）较大，CPU 模式处理速度有限。"
                            "建议：① 等待下次重试（自动增加超时） ② 确认 DeepSeek API 无限流"
                        )
                        break
                    elif _et == "file_not_found":
                        _core_error_msg = "输入文件未找到，请检查文件路径是否正确。"
                        break
                    elif _et == "api_key":
                        _core_error_msg = "API 密钥无效，请检查 DeepSeek API 配置。"
                        break
                    elif _et == "agent_error":
                        _agent_err = str(_sr.get("error") or "")[:200]
                        if _agent_err:
                            _core_error_msg = f"Agent 执行错误：{_agent_err}。请检查环境和配置后重新尝试。"
                        break
        if _adapter:
            await _enqueue_visible_report(
                _core_error_msg,
                EventType.CHECK,
                event_kind=visible_kind,
                priority=3,
                source="batch_plan:core_step_failed",
                parent_id=event.id,
                bypass_rate_limit=True,
            )

    # ── Text-only delivery: if no file output expected but task succeeded ──
    is_text_only_delivery = False
    if not required_exts and result is not None and getattr(result, "ok", False) and not files:
        is_text_only_delivery = True
        step_results = getattr(result, "step_results", {}) or {}
        for step_id, step_result in sorted(step_results.items()):
            if isinstance(step_result, dict):
                content = str(step_result.get("content") or step_result.get("output") or "").strip()
                if content and len(content) > 10:
                    parsed["artifact_content"] = content
                    break

    # ── Ensure report files (.md, .pdf) from steps 3-4 are in files list ──
    # The scan may miss report files if other files fill the 6-slot limit.
    if delivery_dir and not core_step_failed:
        _report_md = [f for f in (files or []) if f.endswith(".md") and "pancreas" in os.path.basename(f)]
        _report_pdf = [f for f in (files or []) if f.endswith(".pdf") and "pancreas" in os.path.basename(f)]
        if not _report_pdf or not _report_md:
            # Scan delivery_dir for report files
            for _root, _dirs, _names in os.walk(delivery_dir):
                for _n in _names:
                    if not _n.endswith((".md", ".pdf")):
                        continue
                    if _n.startswith("_") and _n.endswith(".json"):
                        continue
                    _fp = os.path.join(_root, _n)
                    if _fp in (files or []):
                        continue
                    # Prioritize reports over cytobridge auxiliary files
                    if "report" in _n.lower() or "trajectory" in _n.lower() or "分析" in _n or "分化" in _n:
                        if files is None:
                            files = []
                        files.insert(0, _fp)
                        logger.info("[DELIVERY] added missing report file: %s (%dB)", _n, os.path.getsize(_fp))

    # ── Generic format conversion: if tabular format required but only MD exists, try extracting tables ──
    if missing_required_output and files is not None and not files and any(ext in (required_exts or set()) for ext in (".csv", ".xlsx", ".xls")):
        try:
            md_candidates = [
                f for f in _resolve_one_shot_output_files(
                    delivery_dir, parsed, artifact_path="",
                    since_ts=started_at, required_exts={".md"},
                    allow_workspace_fallback=True,
                ) if f.endswith(".md")
            ]
            # Also check inline artifact_content for markdown tables
            inline_md = str((parsed or {}).get("artifact_content") or "").strip()
            if not md_candidates and inline_md:
                # Write inline content to a temp file for conversion
                temp_md_path = os.path.join(delivery_dir, "_inline_artifact.md")
                try:
                    with open(temp_md_path, "w", encoding="utf-8") as f:
                        f.write(inline_md)
                    md_candidates.append(temp_md_path)
                except Exception:
                    pass
            if md_candidates:
                from ..utils.format_converter import try_md_table_to_csv as _try_md2csv
                for md_path in md_candidates:
                    csv_path = _try_md2csv(md_path, delivery_dir)
                    if csv_path:
                        logger.info("[DELIVERY] converted markdown table to CSV: %s", csv_path)
                        files.append(csv_path)
                        break
        except Exception as exc:
            logger.debug("[DELIVERY] markdown-to-CSV conversion failed: %s", exc)
        missing_required_output = bool(required_exts and not files)
    delivery_status = str((parsed or {}).get("delivery_status") or "").strip().lower()
    if missing_required_output:
        reasons = _batch_delivery_failure_reasons(task, required_exts, delivery_dir)
        parsed["step_done"] = "最终交付文件未生成"
        parsed["findings"] = ["原因：" + item for item in reasons]
        parsed["next_action"] = "修复上游失败后重新生成最终交付文件。"
    completed_with_delivery = bool(
        pushed
        and files
        and not missing_required_output
        and delivery_status not in {"partial", "failed"}
        and _final_report_delivery_satisfied(root_request, event.type, files)
    ) or is_text_only_delivery

    # ── For text-only delivery, set artifact_content in parsed for receipt ──
    if is_text_only_delivery and not parsed.get("artifact_content"):
        artifact_text = _compact_artifact_for_receipt(str(parsed.get("artifact_content") or parsed.get("step_done") or ""), limit=800)
        if artifact_text:
            parsed["artifact_content"] = artifact_text

    # ── Experiment validation phase ──────────────────────────────────────
    if _should_run_experiment(root_request, payload) and result is not None and getattr(result, "ok", False):
        try:
            logger.info("[EXPERIMENT] starting experiment phase for task_id=%s", task.task_id)
            # Mark experiment phase to prevent re-runs
            if isinstance(task.metadata, dict):
                task.metadata["_experiment_phase"] = True
                task.save()
            payload["_experiment_phase"] = True

            exp_cfg = _load_yaml_named_config("experiment.yaml", {})
            max_steps = max(1, int(exp_cfg.get("max_steps", 6)))

            # Generate experiment sub-plan
            experiment_planner = BatchPlanner.from_workspace(_workspace)
            # Override max_steps from experiment config
            if hasattr(experiment_planner, "config") and isinstance(experiment_planner.config, dict):
                experiment_planner.config["max_steps"] = max_steps
            exp_plan, exp_planner_calls = await experiment_planner.plan(
                adapter=_adapter,
                user_message=root_request or title,
                task_instance=task,
                registry=registry,
                state_md=state_md if task.continue_from_project else "",
            )
            aggregate_planned_steps += len(exp_plan.plan)

            # Execute experiment sub-plan
            exp_result = await run_harness_plan(
                workspace=_workspace,
                event=event,
                title=title,
                project_dir=project_dir,
                state_md=state_md,
                artifact_path=artifact_path,
                adapter=_adapter,
                build_action_prompt=_build_action_event_prompt,
                parse_structured_response=_parse_structured_project_response,
                micro_plan=exp_plan,
                planner_llm_calls=exp_planner_calls,
                progress_callback=None,
            )
            total_llm_calls += int(exp_result.llm_calls or 0)
            aggregate_completed_steps += len(getattr(exp_result, "step_results", {}) or {})

            # Merge experiment results into parsed output
            if exp_result.parsed and isinstance(exp_result.parsed, dict):
                exp_findings = exp_result.parsed.get("findings")
                if exp_findings:
                    existing = parsed.get("findings") if isinstance(parsed, dict) else []
                    if isinstance(existing, list):
                        parsed["findings"] = existing + (exp_findings if isinstance(exp_findings, list) else [exp_findings])
                exp_evidence = exp_result.parsed.get("evidence")
                if exp_evidence:
                    existing_ev = parsed.get("evidence") if isinstance(parsed, dict) else ""
                    if existing_ev:
                        parsed["evidence"] = f"{existing_ev}; {exp_evidence}"
                    else:
                        parsed["evidence"] = exp_evidence

            # Append experiment results to expected_artifacts
            experiment_artifacts = list(getattr(exp_plan, "expected_artifacts", []) or [])
            if experiment_artifacts:
                current_expected = list(getattr(task, "expected_artifacts", []) or [])
                seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in current_expected if isinstance(item, dict)}
                for item in experiment_artifacts:
                    if isinstance(item, dict):
                        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                        if key not in seen:
                            current_expected.append(item)
                            seen.add(key)
                task.update_expected_artifacts(current_expected)
                payload["expected_artifacts"] = current_expected

            logger.info("[EXPERIMENT] phase completed for task_id=%s", task.task_id)

            # Re-push output files to include any experiment artifacts
            pushed, files = _push_one_shot_output_files(
                delivery_dir,
                parsed,
                artifact_path="",
                since_ts=started_at,
                required_exts=required_exts,
                allow_workspace_fallback=bool(".pdf" in required_exts),
            )
        except Exception as exc:
            logger.warning("[EXPERIMENT] phase failed for task_id=%s: %s", task.task_id, exc)
            task.append_log("experiment_phase_failed", {"error": str(exc)})

    progress_text, next_step_text = _batch_plan_progress_summary(
        result,
        aggregate_planned_steps or planned_steps,
        completed_with_delivery=completed_with_delivery,
        completed_steps=aggregate_completed_steps or None,
    )
    if not completed_with_delivery:
        current_done = str(parsed.get("step_done") or "")
        if "最终交付文件未生成" in current_done:
            parsed["step_done"] = f"{progress_text} 最终交付文件未生成。"
        elif parsed.get("delivery_status") != "failed":
            parsed["step_done"] = progress_text
        parsed["next_action"] = next_step_text
    next_event = EventType.STOP_PROJECT.value if completed_with_delivery else ""
    next_reason = (
        "deliverable file sent successfully"
        if completed_with_delivery
        else f"{progress_text} 下一步：{next_step_text}"
    )
    # Generate final summary for delivery message (config-driven via llm_check.yaml)
    final_summary_text = ""
    if completed_with_delivery and task:
        try:
            final_summary_text = await _generate_final_summary(
                task_instance=task,
                check_result=check_result or {},
                file_list=files or [],
                user_message=root_request or title,
            )
        except Exception as exc:
            logger.debug("[FINAL_SUMMARY] generation failed (non-fatal): %s", exc)
    receipt_text = _event_completion_receipt_local(
        title,
        event.type,
        parsed,
        next_event=next_event,
        next_reason=next_reason,
        files=files,
        files_pushed=pushed,
    )
    if final_summary_text:
        receipt_text = f"{receipt_text}\n\n{final_summary_text}"
    await _enqueue_visible_report(
        receipt_text,
        event.type,
        event_kind=visible_kind,
        priority=2,
        source=f"{event.type.value}:completion_receipt",
        parent_id=event.id,
        bypass_rate_limit=True,
        files=[] if pushed else files,
    )
    if next_event == EventType.STOP_PROJECT.value:
        try:
            await _enqueue_stop_project_event(event, title, next_reason, payload)
        except Exception as exc:
            logger.debug("[STOP_PROJECT] enqueue after batch_plan failed: %s", exc)
    # Record completion for content-based dedup and clean up inflight tracking
    _batch_plan_inflight.discard(event.id)
    dedup_key = title or (root_request or "")[:80]
    if dedup_key:
        _batch_plan_recently_completed[dedup_key] = _time.time()
        # Prune entries older than 5 minutes to prevent unbounded growth
        _stale_keys = [k for k, v in _batch_plan_recently_completed.items() if _time.time() - v > 300]
        for _k in _stale_keys:
            _batch_plan_recently_completed.pop(_k, None)
    logger.info("[MIND] DONE event_type=%s, id=%s", event.type.value, event.id[:8])


async def _handle_action_event(event: MindEvent):
    """Handle small action events without entering the heavy PROJECT pipeline."""
    payload = event.payload or {}
    title = str(payload.get("title") or payload.get("project") or payload.get("event_kind") or event.type.value).strip()
    root_for_title = _root_user_request(payload) or str(payload.get("user_request") or "")
    if _is_generic_project_title(title) and root_for_title:
        title = _compact_title_from_request(root_for_title, fallback=title)
        payload["title"] = title
    if not title:
        title = event.type.value
    logger.info(f"[ACTION] Executing {event.type.value}: '{title[:60]}'")

    from ..projects.project_state import get_project_dir, read_state_md, write_state_md

    project_dir = get_project_dir(_workspace, title)
    os.makedirs(project_dir, exist_ok=True)
    state_md = read_state_md(_workspace, title)
    spec = _action_event_spec(event.type)
    artifact_path = os.path.join(project_dir, str(spec.get("artifact") or f"{event.type.value}_result.md"))
    started_at = _time.time()
    root_contract_for_harness = _root_user_request(payload) or str(payload.get("user_request") or title)
    harness_required_exts = _required_output_exts(
        root_contract_for_harness,
        event.type.value,
        str(payload.get("event_kind") or ""),
    )
    planning_event_types_for_harness = {
        EventType.PROJECT_THINK,
        EventType.OBJECTIVE_REVIEW,
        EventType.HABIT_UPDATE,
        EventType.CURIOSITY_EXPLORE,
    }
    if harness_required_exts and event.type in planning_event_types_for_harness:
        if not payload.get("root_expected_artifacts"):
            payload["root_expected_artifacts"] = _align_expected_artifacts_with_required_exts(
                payload.get("expected_artifacts"),
                harness_required_exts,
            )
        payload["expected_artifacts"] = [{"type": "message", "pattern": "text", "description": "下一步 event 计划", "required": True}]
        logger.info(
            "[HARNESS] kept planning expected_artifacts as message; root_expected_artifacts=%s",
            payload.get("root_expected_artifacts"),
        )
    elif harness_required_exts:
        payload["expected_artifacts"] = _align_expected_artifacts_with_required_exts(
            payload.get("expected_artifacts"),
            harness_required_exts,
        )
        logger.info(
            "[HARNESS] aligned expected_artifacts with required_exts=%s: %s",
            sorted(harness_required_exts),
            payload.get("expected_artifacts"),
        )
    elif (
        event.type not in planning_event_types_for_harness
        and not payload.get("expected_artifacts")
        and str(spec.get("artifact") or "").strip()
    ):
        payload["expected_artifacts"] = [{
            "type": "file",
            "pattern": os.path.basename(str(spec.get("artifact") or "")),
            "description": f"{event.type.value} 默认可验证产物",
            "required": True,
        }]
        payload["delivery_required"] = bool(payload.get("delivery_required") or payload.get("root_expected_artifacts"))
        logger.info(
            "[HARNESS] inferred expected_artifacts from event metadata for %s: %s",
            event.type.value,
            payload.get("expected_artifacts"),
        )
    response = ""
    parsed = _run_file_inspection(event, title, project_dir)
    if parsed is None:
        parsed = _run_web_capture(event, title, project_dir)
    if parsed is None and os.getenv("PARTNER_DISABLE_HARNESS", "").strip().lower() not in {"1", "true", "on", "yes"}:
        # ── Simple task fast path: DIRECT_TASK / DATA_FETCH calls agent directly ──
        if event.type in {EventType.DIRECT_TASK, EventType.DATA_FETCH, EventType.DATA_ANALYSIS, EventType.VISUALIZATION, EventType.ARTIFACT_BUILD, EventType.PDF_REPORT, EventType.WEB_SEARCH} and not payload.get("force_harness"):
            user_task = _root_user_request(payload) or str(payload.get("user_request") or title)
            try:
                from ..skills.external_agent_skills import execute_agent_task
                result = await execute_agent_task(
                    workspace=_workspace,
                    agent="hermes",
                    task=user_task,
                    allow_web=True,
                )
                if result.ok:
                    output = result.output or {}
                    content = str(output.get("content") or "")
                    # If expected artifacts require a file, write content to disk
                    expected = payload.get("expected_artifacts") or []
                    file_patterns = [e.get("pattern","") for e in expected if isinstance(e,dict) and e.get("type")=="file"]
                    if file_patterns and content:
                        # Parse the FIRST extension from comma-separated patterns
                        # e.g. "*.csv, *.xls, *.xlsx" → ".csv" (was previously picking ".xlsx")
                        pattern_str = file_patterns[0]
                        first_ext = ""
                        for pat in pattern_str.replace(",", " ").split():
                            pat = pat.strip()
                            if pat.startswith("*"):
                                e = os.path.splitext(pat)[1] or ""
                                if e:
                                    if not first_ext:
                                        first_ext = e
                                        break
                        ext = first_ext or ".csv"
                        # Clean content before writing — strip internal markers,
                        # Hermes timeout messages, diff markers, etc.
                        required_exts_set = set()
                        for pat in pattern_str.replace(",", " ").split():
                            pat = pat.strip()
                            if pat.startswith("."):
                                required_exts_set.add(pat.lower())
                            elif pat.startswith("*"):
                                e = os.path.splitext(pat)[1] or ""
                                if e:
                                    required_exts_set.add(e.lower())
                        content = _postprocess_agent_content(content, required_exts_set)
                        fname = f"result{ext}"
                        fpath = os.path.join(project_dir, fname)
                        os.makedirs(project_dir, exist_ok=True)
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.write(content + "\n" if not content.endswith("\n") else content)
                        files_str = fpath
                    else:
                        files_str = "EMPTY"
                    parsed = {
                        "action": event.type.value,
                        "step_done": "Agent 已直接完成任务",
                        "findings": [f"直接调用 Hermes 完成任务"],
                        "evidence": "system:direct_agent_call",
                        "next_action": "如目标已满足则停止；否则继续新事件。",
                        "state_delta": f"direct_call event={event.type.value}",
                        "files": files_str,
                        "artifact_content": content[:2000] or "EMPTY",
                        "delivery_status": "delivered",
                    }
                    payload["harness_managed"] = True
                    payload["direct_call_ok"] = True
                    # Also write to artifact_path so delivery logic finds it
                    if content and artifact_path:
                        os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
                        with open(artifact_path, "w", encoding="utf-8") as f:
                            f.write(content + "\n" if not content.endswith("\n") else content)
                    response = (
                        f"ACTION: {parsed['action']}\n"
                        f"DONE: {parsed['step_done']}\n"
                        f"FINDINGS: {'；'.join(parsed['findings'])}\n"
                        f"EVIDENCE: {parsed['evidence']}\n"
                        f"NEXT: {parsed['next_action']}\n"
                        f"FILES: {parsed['files']}\n"
                        f"STATE_DELTA: {parsed['state_delta']}\n"
                        f"ARTIFACT_CONTENT: {parsed['artifact_content']}"
                    )
                    payload["harness_managed"] = True
                    payload["direct_call_ok"] = True
                    logger.info("[DIRECT_CALL] %s completed: content=%dB files=%s", event.type.value, len(content), files_str)
                else:
                    # ── Agent 调用失败：发送错误消息，不生成任何文件，直接结束 ──
                    error_msg = f"任务执行失败: {result.error}"
                    await _enqueue_visible_report(
                        error_msg,
                        event.type,
                        event_kind=str(payload.get("event_kind") or event.type.value),
                        priority=2,
                        source=f"{event.type.value}:direct_call_failed",
                        parent_id=event.id,
                        bypass_rate_limit=True,
                    )
                    # 记录失败日志
                    logger.warning("[DIRECT_CALL] agent failed for %s: %s", title, result.error)
                    # 直接返回，不继续执行 harness 或后续步骤
                    return
            except Exception as exc:
                logger.warning("[DIRECT_CALL] exception: %s", exc)
                # 异常也发送错误消息并返回
                await _enqueue_visible_report(
                    f"任务执行时发生内部错误: {exc}",
                    event.type,
                    event_kind=str(payload.get("event_kind") or event.type.value),
                    priority=2,
                    source=f"{event.type.value}:direct_call_exception",
                    parent_id=event.id,
                    bypass_rate_limit=True,
                )
                return
        # ── Normal harness path ──
        if not payload.get("direct_call_ok"):
            try:
                from .harness import run_harness

                harness_result = await run_harness(
                    workspace=_workspace,
                    event=event,
                    title=title,
                    project_dir=project_dir,
                    state_md=state_md,
                    artifact_path=artifact_path,
                    adapter=_adapter,
                    build_action_prompt=_build_action_event_prompt,
                    parse_structured_response=_parse_structured_project_response,
                    max_replans=1,
                )
                if _harness_result_is_diagnostic_only(harness_result):
                    logger.warning(
                        "[HARNESS] diagnostic-only failure for %s/%s; falling back to legacy action handler: %s",
                        event.type.value,
                        payload.get("event_kind") or "",
                        harness_result.reason,
                    )
                elif harness_result.parsed:
                    payload["harness_managed"] = True
                    payload["harness_llm_calls"] = harness_result.llm_calls
                    payload["harness_ok"] = bool(harness_result.ok)
                    parsed = harness_result.parsed
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
                    logger.info(
                        "[HARNESS] %s %s/%s with %d planner+smart LLM call(s), %d step(s)",
                        "completed" if harness_result.ok else "handled_partial_or_failed",
                        event.type.value,
                        payload.get("event_kind") or "",
                        harness_result.llm_calls,
                        len(harness_result.plan),
                    )
                else:
                    logger.info(
                        "[HARNESS] skipped/fell back for %s/%s: %s",
                        event.type.value,
                        payload.get("event_kind") or "",
                        harness_result.reason,
                    )
            except Exception as exc:
                logger.warning(f"[HARNESS] failed for {event.type.value}, falling back to legacy action handler: {exc}")
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
                response = (
                    await asyncio.to_thread(_adapter.chat, prompt, purpose=purpose) or ""
                ).strip()
        except Exception as exc:
            logger.warning(f"[ACTION] backend failed for {event.type.value}: {exc}")
            response = ""

        raw_had_tool_noise = bool(re.search(r"<\s*tool_call\b|<function=|<parameter=", response, re.I))
        parse_response = _strip_tool_call_noise(response) if raw_had_tool_noise else response
        parsed = _parse_structured_project_response(parse_response)
    if not parsed and event.type == EventType.PDF_REPORT:
        report_body = _build_pdf_report_from_existing_files(title, payload, project_dir)
        if report_body:
            parsed = {
                "action": "pdf_report",
                "step_done": "已根据已有真实文件整理报告正文",
                "findings": [
                    "PDF 报告生成所需的 LLM 结构化输出不可用，已改用已有数据、图表和阶段记录生成报告",
                    "报告内容只引用当前项目目录中的真实文件，不编造额外数据",
                ],
                "evidence": f"project_dir={project_dir}",
                "next_action": "报告文件已生成后即可停止当前执行链。",
                "state_delta": "pdf_report generated from existing project files",
                "files": "EMPTY",
                "artifact_content": report_body,
            }
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
            if not followup.get("queued"):
                step_val = int(payload.get("step") or 0)
                if step_val >= 2:
                    # Escalate to DIRECT_TASK — the planning-only cycle failed repeatedly
                    try:
                        pool = await ensure_pool()
                        root_request_pt = _root_user_request(payload) or str(payload.get("user_request") or title)
                        user_request_for_next = f"规划阶段已失败多次，直接执行：{root_request_pt[:1400]}"
                        await pool.put(MindEvent(
                            type=EventType.DIRECT_TASK,
                            priority=2,
                            payload={
                                "title": title,
                                "step": step_val + 1,
                                "delivery_mode": "research_project",
                                "user_request": user_request_for_next,
                                "root_user_request": root_request_pt[:1800],
                                "event_type": EventType.DIRECT_TASK.value,
                                "event_kind": f"{event.type.value}_escalated_to_direct",
                                "stop_after_completion": False,
                                "parent_user_request": str(payload.get("user_request") or "")[:1600],
                                "previous_next_action": parsed["next_action"],
                            },
                            source=f"{event.type.value}:planning_failure_escalation_to_direct",
                            parent_id=event.id,
                        ))
                        followup = {
                            "queued": True,
                            "event_type": EventType.DIRECT_TASK.value,
                            "event_kind": f"{event.type.value}_escalated_to_direct",
                            "reason": "Planning-only cycle failed repeatedly; escalated to DIRECT_TASK",
                        }
                        logger.warning("[FOLLOWUP] escalated %s (step=%d) to DIRECT_TASK for %s", event.type.value, step_val, title)
                    except Exception as exc:
                        logger.warning(f"[FOLLOWUP] escape hatch to DIRECT_TASK failed for {title}: {exc}")
                if not followup.get("queued"):
                    try:
                        followup = await _enqueue_minimal_research_event_after_planning_failure(event, title, parsed, payload)
                    except Exception as exc:
                        logger.warning(f"[FOLLOWUP] minimal research fallback after project_think failed for {title}: {exc}")
            try:
                await _enqueue_visible_report(
                    await _event_completion_receipt_async(
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
        if event.type == EventType.OBJECTIVE_REVIEW and payload.get("failure_event_type"):
            replan_queued = False
            root_request = _root_user_request(payload) or str(payload.get("user_request") or title)
            step_val = int(payload.get("step") or 0)
            if step_val >= 2:
                # Escalate to DIRECT_TASK — the planning-only cycle failed repeatedly
                try:
                    pool = await ensure_pool()
                    next_type = EventType.DIRECT_TASK
                    event_kind = f"{event.type.value}_escalated_to_direct"
                    user_request_for_next = f"规划阶段已失败多次，直接执行：{root_request[:1400]}"
                    await pool.put(MindEvent(
                        type=next_type,
                        priority=2,
                        payload={
                            "title": title,
                            "step": step_val + 1,
                            "delivery_mode": "research_project",
                            "user_request": user_request_for_next,
                            "root_user_request": root_request[:1800],
                            "event_type": next_type.value,
                            "event_kind": event_kind,
                            "stop_after_completion": False,
                            "parent_user_request": str(payload.get("user_request") or "")[:1600],
                            "previous_next_action": parsed["next_action"],
                            "failure_event_type": str(payload.get("failure_event_type") or ""),
                            "failure_event_kind": str(payload.get("failure_event_kind") or ""),
                        },
                        source=f"{event.type.value}:failure_escalation_to_direct",
                        parent_id=event.id,
                    ))
                    replan_queued = True
                    logger.warning("[FOLLOWUP] escalated %s (step=%d) to DIRECT_TASK for %s", event.type.value, step_val, title)
                except Exception as exc:
                    logger.debug(f"[ACTION] failed to enqueue objective_review escalation to DIRECT_TASK: {exc}")
            else:
                try:
                    pool = await ensure_pool()
                    if _is_open_research_goal(root_request):
                        next_type = EventType.LITERATURE_REVIEW
                        event_kind = "objective_review_failure_research_slice"
                        user_request_for_next = (
                            "目标对齐事件超时或返回不可解析，但这是开放研究/综述类目标，不能停止。"
                            "请直接执行一个最小资料整理切片：收集并对比 3-5 个可追溯来源/方法，"
                            "输出方法、指标、证据强弱、适用边界和下一步突破探索入口。"
                            f"\\n原始用户请求：{root_request[:1400]}"
                        )
                    else:
                        next_type = EventType.PROJECT_THINK
                        event_kind = "objective_review_failure_replan"
                        user_request_for_next = (
                            "失败复盘事件本身也超时或返回不可解析。不要停止当前根目标；"
                            "请基于已有产出、失败边界和用户原始目标，拆出一个更小、"
                            "更容易验证、能产生文件或结论的下一步 event。"
                            f"\\n原始用户请求：{root_request[:1400]}"
                        )
                    await pool.put(MindEvent(
                        type=next_type,
                        priority=2,
                        payload={
                            "title": title,
                            "step": step_val + 1,
                            "delivery_mode": "research_project",
                            "user_request": user_request_for_next,
                            "root_user_request": root_request[:1800],
                            "event_type": next_type.value,
                            "event_kind": event_kind,
                            "stop_after_completion": False,
                            "parent_user_request": str(payload.get("user_request") or "")[:1600],
                            "previous_next_action": parsed["next_action"],
                            "failure_event_type": str(payload.get("failure_event_type") or ""),
                            "failure_event_kind": str(payload.get("failure_event_kind") or ""),
                        },
                        source=f"{event.type.value}:failure_replan",
                        parent_id=event.id,
                    ))
                    replan_queued = True
                except Exception as exc:
                    logger.debug(f"[ACTION] failed to enqueue objective_review fallback replan: {exc}")
            try:
                await _enqueue_visible_report(
                    await _event_completion_receipt_async(
                        title,
                        event.type,
                        parsed,
                        next_event=(next_type.value if replan_queued else ""),
                        next_reason=parsed["next_action"],
                        files=[],
                    ),
                    event.type,
                    event_kind=str(payload.get("event_kind") or event.type.value),
                    priority=2,
                    source=f"{event.type.value}:unstructured_replan_receipt",
                    parent_id=event.id,
                    bypass_rate_limit=True,
                )
            except Exception as exc:
                logger.debug(f"[ACTION] failed to enqueue objective_review fallback receipt: {exc}")
            logger.info(f"[ACTION] unstructured objective_review queued project_think={replan_queued} for {title}")
            logger.info(f"[MIND] DONE event_type={event.type.value}, id={event.id[:8]}")
            return
        recovery_queued = False
        try:
            pool = await ensure_pool()
            if event.type not in {EventType.PROJECT_THINK, EventType.OBJECTIVE_REVIEW} and not _stop_after_completion(payload):
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
                        "stop_after_completion": False,
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
                await _event_completion_receipt_async(
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
    delivery_status_before_artifact = str((parsed or {}).get("delivery_status") or "").strip().lower()
    if artifact_text and (
        delivery_status_before_artifact in {"partial", "failed"}
        or _looks_like_diagnostic_content(artifact_text)
    ):
        logger.warning(
            "[ARTIFACT] skip writing diagnostic/remediation content as user artifact for %s/%s",
            event.type.value,
            payload.get("event_kind") or "",
        )
        artifact_text = ""
    if artifact_text:
        artifact_written = _write_artifact_file(artifact_path, artifact_text)
        if artifact_written and not parsed.get("files"):
            parsed["files"] = artifact_path
        if artifact_written and payload.get("task_working_dir"):
            try:
                task_dir = os.path.abspath(str(payload.get("task_working_dir") or ""))
                workspace_root = os.path.abspath(_workspace or project_dir)
                if os.path.isdir(task_dir) and os.path.commonpath([workspace_root, task_dir]) == workspace_root:
                    task_artifact_path = os.path.join(task_dir, os.path.basename(artifact_path))
                    if os.path.abspath(task_artifact_path) != os.path.abspath(artifact_path):
                        if _write_artifact_file(task_artifact_path, artifact_text):
                            existing_files = str(parsed.get("files") or "").strip()
                            parsed["files"] = (
                                f"{existing_files}; {task_artifact_path}"
                                if existing_files and existing_files.upper() != "EMPTY"
                                else task_artifact_path
                            )
                            logger.info("[TASK_INSTANCE] mirrored action artifact to %s", task_artifact_path)
            except Exception as exc:
                logger.debug(f"[TASK_INSTANCE] failed to mirror action artifact: {exc}")
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

    root_contract = _root_user_request(payload) or str(payload.get("user_request") or "")
    required_exts = _required_output_exts(
        root_contract,
        event.type.value,
        str(payload.get("event_kind") or ""),
    )
    if event.type in {EventType.ARTIFACT_BUILD, EventType.PDF_REPORT}:
        parsed, required_exts = _publish_batch_plan_pdf_if_needed(
            title,
            root_contract,
            parsed,
            payload.get("task_working_dir") or project_dir,
            required_exts,
        )
    planning_event_types = {
        EventType.PROJECT_THINK,
        EventType.OBJECTIVE_REVIEW,
        EventType.HABIT_UPDATE,
        EventType.CURIOSITY_EXPLORE,
    }
    harness_has_files = bool(
        payload.get("harness_managed")
        and str((parsed or {}).get("files") or "").strip()
        and str((parsed or {}).get("files") or "").strip().upper() != "EMPTY"
    )
    expected_file_delivery = _has_file_expected_artifact(
        payload.get("expected_artifacts"),
        payload.get("root_expected_artifacts"),
    )
    if event.type in planning_event_types:
        required_exts = set()
        pushed, files = False, []
        logger.info("[REPORT] planning event output kept internal: %s/%s", event.type.value, payload.get("event_kind") or "")
    elif (
        payload.get("harness_managed")
        and delivery_status_before_artifact in {"partial", "failed"}
        and event.type not in {
            EventType.ARTIFACT_BUILD,
            EventType.PDF_REPORT,
            EventType.VISUALIZATION,
            EventType.WEB_CAPTURE,
            EventType.FILE_INSPECTION,
        }
    ):
        pushed, files = False, []
        logger.info(
            "[REPORT] partial/intermediate harness output kept internal: %s/%s",
            event.type.value,
            payload.get("event_kind") or "",
        )
    elif not harness_has_files and not expected_file_delivery and not artifact_written and not required_exts and event.type not in {
        EventType.ARTIFACT_BUILD,
        EventType.PDF_REPORT,
        EventType.VISUALIZATION,
        EventType.WEB_CAPTURE,
        EventType.FILE_INSPECTION,
    }:
        pushed, files = False, []
        logger.info("[REPORT] intermediate event output kept internal: %s/%s", event.type.value, payload.get("event_kind") or "")
    else:
        delivery_project_dir = project_dir
        allow_workspace_fallback = True
        if payload.get("task_working_dir"):
            candidate_dir = os.path.abspath(str(payload.get("task_working_dir") or ""))
            workspace_root = os.path.abspath(_workspace or project_dir)
            try:
                if os.path.isdir(candidate_dir) and os.path.commonpath([workspace_root, candidate_dir]) == workspace_root:
                    delivery_project_dir = candidate_dir
                    allow_workspace_fallback = False
            except ValueError:
                pass
        pushed, files = _push_one_shot_output_files(
            delivery_project_dir,
            parsed,
            artifact_path=artifact_path if artifact_written else "",
            since_ts=started_at,
            required_exts=required_exts,
            allow_workspace_fallback=allow_workspace_fallback,
            extra_scan_roots=[project_dir] if delivery_project_dir != project_dir else None,
        )
    missing_required_output = bool(required_exts and not files)
    delivery_status = str((parsed or {}).get("delivery_status") or "").strip().lower()
    if missing_required_output:
        record_risk_event(
            _workspace,
            title,
            f"{event.type.value} missing required output file",
            str(payload.get("user_request") or "")[:260],
            severity="high",
        )
        parsed["step_done"] = "没有生成当前目标格式文件"
        parsed["findings"] = [
            f"当前目标格式是 {', '.join(sorted(required_exts))}，但本轮未发现对应真实文件",
            "不能把摘要 Markdown 当作目标交付文件发送",
        ]
        parsed["next_action"] = "本轮停止并如实告知用户；需要重新执行时必须直接生成目标格式文件。"
    completed_with_delivery = bool(
        pushed
        and files
        and not missing_required_output
        and delivery_status not in {"partial", "failed"}
        and _final_report_delivery_satisfied(root_contract, event.type, files)
    )
    followup = {"queued": False, "event_type": "", "event_kind": "", "reason": ""}
    stop_after_report_reason = ""
    if completed_with_delivery:
        stop_after_report_reason = "deliverable file sent successfully"
        followup = {
            "queued": True,
            "event_type": EventType.STOP_PROJECT.value,
            "event_kind": "delivered_file_stop",
            "reason": stop_after_report_reason,
        }
    elif not (missing_required_output and _stop_after_completion(payload)):
        if payload.get("harness_managed") and (event.type in planning_event_types or delivery_status in {"partial", "failed"}):
            try:
                followup = await _maybe_enqueue_followup_event(event, title, parsed, payload)
            except Exception as exc:
                logger.warning(f"[FOLLOWUP] enqueue after harness planning event failed for {title}: {exc}")
            if not followup.get("queued"):
                logger.warning("[HARNESS] planning event produced no follow-up for %s: %s", title, followup.get("reason") or "")
        elif payload.get("harness_managed"):
            if bool(payload.get("delivery_required") or payload.get("root_expected_artifacts") or payload.get("expected_artifacts")) and not completed_with_delivery:
                try:
                    followup = await _maybe_enqueue_followup_event(event, title, parsed, payload)
                except Exception as exc:
                    logger.warning(f"[FOLLOWUP] enqueue after harness delivery gap failed for {title}: {exc}")
                if not followup.get("queued"):
                    followup = {
                        "queued": False,
                        "event_type": "",
                        "event_kind": "",
                        "reason": "Harness 执行完成但目标交付物未确认生成",
                    }
            else:
                followup = {
                    "queued": False,
                    "event_type": "",
                    "event_kind": "",
                    "reason": "Harness 执行完成，等待下一步任务或用户新指令",
                }
                logger.info("[HARNESS] skip legacy follow-up selector for %s", title)
        else:
            try:
                followup = await _maybe_enqueue_followup_event(event, title, parsed, payload)
            except Exception as exc:
                logger.warning(f"[FOLLOWUP] enqueue check failed for {title}: {exc}")
    if not followup.get("queued") and _stop_after_completion(payload):
        stop_reason = str(followup.get("reason") or "one-shot event completed without selected follow-up")
        if event.type == EventType.DIRECT_TASK and str(payload.get("event_kind") or "").strip() == "desktop_gui_message":
            stop_reason = "direct_reply_completed"
        stop_after_report_reason = stop_reason
        followup = {
            "queued": True,
            "event_type": EventType.STOP_PROJECT.value,
            "event_kind": "one_shot_complete",
            "reason": stop_reason,
        }
    if (
        payload.get("harness_managed")
        and event.type in planning_event_types
        and delivery_status in {"partial", "failed"}
        and followup.get("queued")
        and str(followup.get("event_type") or "") != EventType.STOP_PROJECT.value
    ):
        parsed["step_done"] = "规划阶段未稳定产出结构化结果，已按根目标转入下一步 event"
        parsed["findings"] = [
            "当前 planning event 只负责拆解和路由，不把补救报告当作最终交付物",
            f"已排入下一步：{followup.get('event_type')}/{followup.get('event_kind')}",
        ]
        parsed["next_action"] = str(followup.get("reason") or "继续执行已排入的下游 event")
        parsed["delivery_status"] = "routing_continued"
    if payload.get("harness_managed"):
        receipt_text = _event_completion_receipt_local(
            title,
            event.type,
            parsed,
            next_event=str(followup.get("event_type") or ""),
            next_reason=str(followup.get("reason") or ""),
            files=files,
            files_pushed=pushed,
        )
    else:
        receipt_text = await _event_completion_receipt_async(
            title,
            event.type,
            parsed,
            next_event=str(followup.get("event_type") or ""),
            next_reason=str(followup.get("reason") or ""),
            files=files,
            files_pushed=pushed,
        )
    await _enqueue_visible_report(
        receipt_text,
        event.type,
        event_kind=str(payload.get("event_kind") or event.type.value),
        priority=2,
        source=f"{event.type.value}:completion_receipt",
        parent_id=event.id,
        bypass_rate_limit=True,
        files=[] if pushed else files,
    )
    if stop_after_report_reason:
        try:
            await _enqueue_stop_project_event(event, title, stop_after_report_reason, payload)
        except Exception as exc:
            logger.debug(f"[STOP_PROJECT] enqueue after completion report failed: {exc}")
    if followup.get("queued"):
        logger.info(f"[ACTION] selector queued next event {followup.get('event_type')} for {title}")
    else:
        logger.info(f"[ACTION] selector did not queue a next event; project remains active for later selection: {title}")
    logger.info(f"[MIND] DONE event_type={event.type.value}, id={event.id[:8]}")


# ── PROJECT ─────────────────────────────────────────────────────────


def _append_log_summary(workspace: str, title: str, ts: str, parsed: dict, step: int = 0):
    """追加摘要到 exploration_log.md（精简版），完整内容写入 trace_detail.md。"""
    from ..projects.project_state import get_project_dir

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
    from ..projects.project_state import (
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
    payload_event_kind = str((event.payload or {}).get("event_kind") or "").strip()
    source_text = str(getattr(event, "source", "") or "")
    if source_text.startswith("desktop_gui:") or payload_event_kind in {"desktop_gui_message", "queue_backfill"}:
        # Desktop-originated messages already carry the user's current target.
        # Fuzzy project-name recovery can incorrectly map short messages such
        # as greetings onto an old waiting project, which makes the event look
        # consumed while the user receives no answer.
        title = title
    else:
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
        from ..projects.project_state import read_project_brief
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
                response = await asyncio.to_thread(_adapter.chat, prompt, purpose="project")
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
            from ..projects.project_state import load_project_guardrail

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
            artifact_text = _normalize_artifact_content(parsed.get("artifact_content", ""))
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
                    _root_user_request(event.payload) or str(event.payload.get("user_request") or ""),
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
        elif one_shot_event and parsed and timed_out_or_stalled:
            # ── Fallback: even if the event was marked stalled/timeout, scan for any
            # files that match the required extensions — a prior step may have
            # created them before the stall occurred.
            logger.info("[PROJECT] one-shot stalled, attempting fallback file scan")
            _, stalled_output_files = _push_one_shot_output_files(
                project_dir,
                parsed,
                artifact_path=artifact_path if artifact_written else "",
                since_ts=event_started_at,
                required_exts=_required_output_exts(
                    _root_user_request(event.payload) or str(event.payload.get("user_request") or ""),
                    str(event.payload.get("event_type") or "project"),
                    str(event.payload.get("event_kind") or ""),
                ),
            )
            if stalled_output_files:
                logger.info(
                    "[PROJECT] fallback file scan found: %s",
                    [os.path.basename(p) for p in stalled_output_files],
                )
                parsed["files"] = "; ".join(stalled_output_files)
                parsed["delivery_status"] = "partial"
                # Override the stalled state — we have files to deliver
                timed_out_or_stalled = False

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
                from ..projects.project_state import update_project_brief_from_round
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
            try:
                await _enqueue_visible_report(
                    (
                        f"「{title}」这一轮后台执行超过单步时间限制，已停止等待当前子步骤。\n\n"
                        "本轮不会编造结果；接下来会把目标拆成更小的可验证动作继续推进。"
                        "如果已经生成了中间文件，会在下一轮先检查文件并汇报。"
                    ),
                    event.type,
                    event_kind=str(event.payload.get("event_kind") or event.type.value),
                    priority=2,
                    source="project:backend_timeout_notice",
                    parent_id=event.id,
                    bypass_rate_limit=True,
                )
            except Exception as exc:
                logger.debug(f"[PROJECT] failed to enqueue timeout notice: {exc}")

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
                await _event_completion_receipt_async(
                    title,
                    event.type,
                    parsed,
                    next_event=str(followup.get("event_type") or ""),
                    next_reason=str(followup.get("reason") or ""),
                    files=one_shot_output_files,
                    files_pushed=one_shot_files_pushed,
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
                    await _event_completion_receipt_async(
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
            from ..projects.project_state import get_active
            title = get_active(_workspace) or ""
        except Exception:
            title = ""
    if not title:
        logger.info(f"[STOP_PROJECT] No project title; nothing to stop for {event.id[:8]}")
        logger.info(f"[MIND] DONE event_type=stop_project, id={event.id[:8]}")
        return
    try:
        from ..projects.project_state import append_log, clear_active, set_project_status

        set_project_status(_workspace, title, "waiting", reason)
        append_log(_workspace, title, f"STOP_PROJECT: {reason}")
        clear_active(_workspace, title)
        try:
            from ..tasks.task_queue import TaskQueue

            queue_path = os.path.join(_workspace, "state", "task_queue.json")
            completed = TaskQueue(queue_path).complete_matching_title(
                title,
                result_summary=f"stopped: {_clip(reason, 180)}",
            )
            if completed:
                append_log(_workspace, title, f"STOP_PROJECT_COMPLETED_TASKS: {completed}")
        except Exception as exc:
            logger.debug(f"[STOP_PROJECT] failed to complete task queue entries for {title}: {exc}")
        try:
            pool = await ensure_pool()
            # NoopPool removed — drop_events_for_title was a no-op stub
            removed = 0
            if removed:
                append_log(_workspace, title, f"STOP_PROJECT_CLEARED_PENDING_EVENTS: {removed}")
        except Exception as exc:
            logger.debug(f"[STOP_PROJECT] failed to clear pending events for {title}: {exc}")
        logger.info(f"[STOP_PROJECT] Project waiting and active cleared: {title} reason={reason[:160]}")
        # ── Record learning / experience at project end ──────────────────
        try:
            from ..meta.learning import LearningManager, extract_learning_from_task

            lm = LearningManager()

            # Try to find the last active task for this project to extract rich metadata
            task_instance = None
            try:
                from ..harness_core import TaskInstance
                task_queue_path = os.path.join(_workspace, "state", "task_queue.json")
                if os.path.exists(task_queue_path):
                    import json as _json
                    with open(task_queue_path, "r") as f:
                        task_queue = _json.load(f)
                    if isinstance(task_queue, list):
                        for tq_entry in task_queue:
                            if isinstance(tq_entry, dict) and tq_entry.get("title") == title:
                                tasks_dir = os.path.join(_workspace, "state", "tasks")
                                if os.path.isdir(tasks_dir):
                                    for td in os.listdir(tasks_dir):
                                        ti_path = os.path.join(tasks_dir, td, "task_instance.json")
                                        if os.path.exists(ti_path):
                                            with open(ti_path, "r") as tf:
                                                ti_data = _json.load(tf)
                                            task_instance = TaskInstance(**ti_data)
                                            break
                                break
            except Exception:
                pass

            if task_instance:
                learning_data = extract_learning_from_task(task_instance)
                # Generate LLM-powered lessons if adapter is available
                try:
                    from ..meta.learning import generate_lessons_from_task

                    llm_lessons = generate_lessons_from_task(task_instance, adapter=_adapter)
                    if llm_lessons:
                        learning_data["lessons"] = llm_lessons
                        logger.info("[LEARNING] LLM-generated lessons: %s", llm_lessons[:200])
                except Exception as exc:
                    logger.debug("[LEARNING] LLM lesson generation failed: %s", exc)
            else:
                learning_data = {}

            lm.record_project_completion(
                project_name=title,
                summary=learning_data.get("summary", f"项目停止: {_clip(reason, 200)}"),
                successful_queries=learning_data.get("useful_queries"),
                failed_queries=[f.split("(")[0].strip() for f in (learning_data.get("failure_factors") or [])[:5]],
                lessons=learning_data.get("lessons") or learning_data.get("reflection", reason)[:500],
                milestone=learning_data.get("milestone", "项目阶段结束"),
                reflection=learning_data.get("reflection", reason),
                skills_learned=learning_data.get("skills_learned"),
                difficulties=learning_data.get("failure_factors"),
                habit_updates=learning_data.get("habit_updates"),
            )

            # ── Record output type in unified experience DB ──
            try:
                from ..meta.learning import record_experience as _rec_exp
                output_type = learning_data.get("output_type", "text")
                file_format = learning_data.get("file_format", "")
                _rec_exp(
                    user_message=title,
                    task_summary=learning_data.get("summary", "")[:500],
                    output_type=output_type,
                    file_format=file_format,
                    success=bool(not learning_data.get("failure_factors")),
                    skills_used=learning_data.get("skills_learned"),
                    instance_id="",
                )
            except Exception as exc:
                logger.debug("[LEARNING] failed to record output type: %s", exc)

            # Archive project working directory to projects/completed/
            try:
                import json as _json
                import shutil as _shutil
                from datetime import datetime as _dt

                projects_dir = os.path.join(_workspace, "projects")
                completed_dir = os.path.join(projects_dir, "completed")
                os.makedirs(completed_dir, exist_ok=True)

                # Find the project dir
                project_dir = os.path.join(projects_dir, title)
                if os.path.isdir(project_dir):
                    timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
                    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:80]
                    archive_name = f"{safe_name}_{timestamp}"
                    archive_path = os.path.join(completed_dir, archive_name)

                    # Copy project directory to completed/
                    if not os.path.exists(archive_path):
                        _shutil.copytree(project_dir, archive_path, symlinks=False)
                        # Write state.json summary
                        state = {
                            "project_name": title,
                            "archived_at": _dt.now().isoformat(),
                            "reason": reason,
                            "learning_files": str(lm.base_dir),
                        }
                        with open(os.path.join(archive_path, "state.json"), "w", encoding="utf-8") as sf:
                            _json.dump(state, sf, ensure_ascii=False, indent=2)
                        logger.info("[STOP_PROJECT] project workspace archived to %s", archive_path)
            except Exception as exc:
                logger.debug("[STOP_PROJECT] failed to archive project: %s", exc)
        except Exception as exc:
            logger.debug("[LEARNING] record at stop_project failed (non-fatal): %s", exc)
        # ──────────────────────────────────────────────────────────────────
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
    # Apply config-driven message filters
    try:
        from ..llm.message_filter import sanitize_for_user
        filtered = sanitize_for_user(content)
        if filtered:
            content = filtered
    except Exception as filter_exc:
        logger.debug("[REPORT] message filter failed (non-fatal): %s", filter_exc)
    report_plain = re.sub(r"^【[^】]+】", "", str(content or "")).strip()
    if report_plain in {"思考中.......", "思考中......", "思考中……", "Thinking..."}:
        logger.info("[REPORT] skipped prefixed thinking-only report: %s/%s", visible_type, visible_kind)
        return
    if not content:
        logger.warning(f"[REPORT] Empty content, skipping {event.id[:8]}")
        return

    payload_files = event.payload.get("files") or event.payload.get("file_paths") or []
    if isinstance(payload_files, str):
        payload_files = [payload_files]
    file_send_ok = False
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
                        ok = _file_push_callback(data, os.path.basename(path), os.path.basename(path))
                        file_send_ok = bool(ok) or file_send_ok
                    except Exception as exc:
                        logger.warning(f"[REPORT] payload file push failed for {path}: {exc}")
            elif files:
                logger.warning("[REPORT] payload file push skipped: no file push callback registered")
        except Exception as exc:
            logger.warning(f"[REPORT] failed to resolve payload files: {exc}")

    if payload_files and not file_send_ok:
        content = re.sub(r"(已通过\s*QQ\s*发送|已发送给你|已发送|发给你了|发给你)", "文件已生成，但本轮文件接口未确认发送成功", content)

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

    logger.info(f"[MIND] DONE event_type=report, id={event.id[:8]}")


# ── CRON_TICK ───────────────────────────────────────────────────────


async def _enqueue_open_content_digests(pool=None, *, source: str = "", active_project: str = "") -> int:
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
        os.path.join(_workspace, "state", "user", "projects", safe, "literature_task_pause.md"),
        os.path.join(_workspace, "state", "user", "reports", safe, "latest_stage_report.md"),
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
        from ..projects.project_state import get_project_status

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
        from ..projects.project_state import get_project_status

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


async def _handle_ollama_status(event: MindEvent):
    """Probe Ollama as an explicit user-selectable event."""
    try:
        from ..ollama_pool import heartbeat_probe, load_ollama_pool_config

        cfg = load_ollama_pool_config(_workspace)
        status = heartbeat_probe(_workspace, purpose=str((event.payload or {}).get("purpose") or "report"))
        mode = str(status.get("mode") or cfg.get("mode") or "").strip() or "unknown"
        selected = str(status.get("selected") or "").strip()
        endpoint = str(status.get("endpoint") or "").strip()
        base_url = str(status.get("base_url") or "").strip()
        fallback = str(status.get("fallback") or "").strip()
        reason = str(status.get("reason") or "").strip()
        enabled = bool(cfg.get("enabled"))
        endpoints = cfg.get("endpoints") if isinstance(cfg.get("endpoints"), list) else []

        if selected:
            content = (
                f"Ollama 可用：{endpoint or base_url} / {selected}\n"
                f"模式：{mode}；轻量问题会优先尝试 Ollama，失败再回退主 LLM。"
            )
        else:
            content = (
                "Ollama 当前不可用，轻量问题会直接回退主 LLM。\n"
                f"配置：{'已启用' if enabled else '未启用'}；模式：{mode}；端点数：{len(endpoints)}"
                + (f"\n原因：{reason or fallback}" if (reason or fallback) else "")
            )
        await _enqueue_visible_report(
            content,
            EventType.OLLAMA_STATUS,
            event_kind=str((event.payload or {}).get("event_kind") or "probe"),
            priority=2,
            source="ollama_status",
            parent_id=event.id,
            force_send=True,
            bypass_rate_limit=True,
        )
    except Exception as exc:
        logger.warning("[OLLAMA] explicit status probe failed: %s", exc, exc_info=True)
        await _enqueue_visible_report(
            UNAVAILABLE_NOTICE,
            EventType.OLLAMA_STATUS,
            event_kind="probe_failed",
            priority=2,
            source="ollama_status",
            parent_id=event.id,
            force_send=True,
            bypass_rate_limit=True,
        )


_DEFAULT_ITERATION_CONFIG = {
    "max_iterations": 3,
    "progress_updates": True,
    "acceptance_criteria": {
        "enabled": True,
        "model": "deepseek-v4-flash",
        "prompt_template": "prompts/acceptance_criteria.txt",
    },
    "llm_check": {
        "enabled": True,
        "model": "deepseek-v4-flash",
        "prompt_template": "prompts/check_with_criteria.txt",
        "fallback_to_rule_check": True,
        "max_content_chars_per_file": 2000,
        "max_total_content_chars": 6000,
    },
    "check": {
        "min_file_size": 100,
        "min_file_count": 1,
        "min_expected_artifacts": 1,
        "min_citations": 3,
        "citation_trigger_terms": ["文献", "论文", "研究", "综述", "方法", "效果", "对比", "突破", "literature", "paper", "review"],
        "required_fields": [],
        "required_fields_trigger_terms": [],
        "min_required_field_hits": 1,
    },
    "reflect": {
        "model": "deepseek-v4-flash",
        "prompt_template": "prompts/reflect_patch.txt",
        "memory_top_k": 3,
    },
    "curiosity": {
        "max_steps": 3,
    },
}


def _merge_config_dict(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_config_dict(out[key], value)
        else:
            out[key] = value
    return out


def _load_yaml_named_config(filename: str, defaults: dict) -> dict:
    candidates = [
        os.path.join(_workspace, "config", filename),
        os.path.join(_workspace, filename),
    ]
    config = dict(defaults)
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                config = _merge_config_dict(config, loaded)
                config["_config_path"] = path
                break
        except Exception as exc:
            logger.debug("[ITERATION] failed to load %s: %s", path, exc)
    return config


def _read_repo_prompt(rel_path: str, fallback: str) -> str:
    for path in (os.path.join(_workspace, rel_path), os.path.join(os.path.dirname(__file__), "..", rel_path)):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                continue
    return fallback


def _task_text_file_summaries(task) -> list[dict]:
    rows: list[dict] = []
    root = getattr(task, "working_dir", "") or ""
    if not root or not os.path.isdir(root):
        return rows
    for dirpath, _, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if rel in {"task_instance.json", "task_log.jsonl"}:
                continue
            try:
                size = os.path.getsize(path)
            except Exception:
                size = 0
            preview = ""
            ext = os.path.splitext(name)[1].lower()
            if ext in {".md", ".txt", ".csv", ".json"} and size <= 2_000_000:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        preview = f.read(1200)
                except Exception:
                    preview = ""
            elif ext == ".pdf" and size <= 5_000_000:
                try:
                    from PyPDF2 import PdfReader  # type: ignore

                    reader = PdfReader(path)
                    preview = "\n".join((page.extract_text() or "") for page in reader.pages[:3])[:3000]
                except Exception:
                    preview = ""
            rows.append({
                "path": path,
                "relative_path": rel,
                "size": size,
                "preview": preview,
                "diagnostic": _is_diagnostic_artifact(path) or _looks_like_diagnostic_content(preview),
            })
    return rows


def _extract_markdown_section(text: str, title_pattern: str) -> str:
    if not text or not title_pattern:
        return ""
    pattern = re.escape(str(title_pattern).strip())
    match = re.search(rf"(?ims)^#+\s*.*{pattern}.*?\n(.*?)(?=^#+\s+|\Z)", text)
    if match:
        return match.group(1).strip()
    # PDF text extraction often loses markdown hashes; fall back to a heading
    # line containing the title and stop at the next short heading-like line.
    lines = str(text).splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if re.search(pattern, line, re.I):
            start = idx + 1
            break
    if start < 0:
        return ""
    collected: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if collected and 0 < len(stripped) <= 30 and re.search(r"(摘要|方法|局限|验证|参考|结论|来源|突破|创新)", stripped):
            break
        collected.append(line)
    return "\n".join(collected).strip()


async def _run_comprehensive_evaluation(task, root_goal: str, config: dict) -> dict:
    """LLM-driven check: evaluates current task artifacts against acceptance criteria.

    Before calling the LLM, this method performs a hard file-existence check on
    expected_artifacts.  If any required artifact pattern resolves to zero files
    on disk, the check immediately returns unsatisfied — no LLM hallucination
    can paper over a missing deliverable.

    If llm_check is enabled, it calls the LLM with the acceptance criteria + current
    file summaries. Falls back to rule-based check if LLM is unavailable and
    fallback_to_rule_check is set.
    """
    # Load llm_check.yaml as the primary config source (config-driven)
    llm_check_yaml = _load_yaml_named_config("llm_check.yaml", {})
    llm_check_file_cfg = llm_check_yaml.get("check", {}) if isinstance(llm_check_yaml.get("check"), dict) else {}
    # Merge with in-code config, file config takes precedence
    llm_check_cfg_inline = (config.get("llm_check") or {}) if isinstance(config.get("llm_check"), dict) else {}
    llm_check_cfg = {}
    llm_check_cfg.update(llm_check_cfg_inline)
    llm_check_cfg.update(llm_check_file_cfg)
    llm_enabled = bool(llm_check_cfg.get("enabled", True))
    task_meta = getattr(task, "metadata", None)
    if isinstance(task_meta, dict):
        acceptance_criteria = str(task_meta.get("acceptance_criteria") or "").strip()
    else:
        acceptance_criteria = ""
    if not acceptance_criteria:
        llm_enabled = False
    # Also read root_goal / user_message from task metadata or task attributes
    user_message = str(root_goal or "")
    if not user_message:
        if isinstance(task_meta, dict):
            user_message = str(task_meta.get("root_goal") or task_meta.get("original_goal") or "").strip()
    if not user_message:
        user_msg = getattr(task, "user_message", "") or ""
        user_message = str(user_msg or "").strip()

    # ── Hard check: expected_artifacts must exist on disk ────────────────
    try:
        from ..harness_core import ArtifactValidator, load_harness_config

        validation = ArtifactValidator(load_harness_config(_workspace)).validate(task)
        if not validation.ok:
            missing_desc = [
                f'{item.get("pattern","?")} ({item.get("description","artifact")})'
                for item in validation.missing
            ]
            logger.warning(
                "[CHECK] expected_artifacts missing: %s",
                ", ".join(missing_desc),
            )
            task.append_log("iteration_llm_check", {
                "satisfied": False,
                "missing": [f"expected_artifact_missing:{d}" for d in missing_desc],
                "reason": "required artifacts not found on disk",
                "_source": "hard_validation",
                "valid_file_count": 0,
                "file_count": 0,
            })
            return {
                "satisfied": False,
                "missing": [f"expected_artifact_missing:{d}" for d in missing_desc],
                "reason": "required artifacts not found on disk",
                "_source": "hard_validation",
                "valid_file_count": 0,
                "file_count": 0,
            }
    except Exception as exc:
        logger.warning("[CHECK] artifact validation error (non-fatal): %s", exc)

    # ── Text-only task: if expected_artifacts is empty, check step results first ──
    try:
        task_expected = getattr(task, "expected_artifacts", None) or []
        if isinstance(task_expected, list) and len(task_expected) == 0:
            # Before auto-passing, check if any steps actually completed successfully
            # If ALL steps failed, this is not a successful text-only delivery
            step_success = False
            try:
                plan_path = os.path.join(_workspace, "state", "tasks", task.task_id, "_step_*.result.json")
                import glob
                result_files = glob.glob(plan_path)
                for rf in result_files:
                    with open(rf, "r") as f:
                        step_result = json.load(f)
                    if step_result.get("ok") is True:
                        step_success = True
                        break
            except Exception:
                pass

            if step_success or not result_files:
                logger.info("[CHECK] text-only task (empty expected_artifacts) — auto-pass")
                task.append_log("iteration_llm_check", {
                    "satisfied": True,
                    "missing": [],
                    "reason": "text-only delivery: no file artifacts expected",
                    "_source": "text_only_bypass",
                    "valid_file_count": 0,
                    "file_count": 0,
                })
                return {
                    "satisfied": True,
                    "missing": [],
                    "reason": "text-only delivery: no file artifacts expected",
                    "_source": "text_only_bypass",
                    "valid_file_count": 0,
                    "file_count": 0,
                }
            else:
                # All steps failed — report as unsatisfied so TASK_FAILED fires
                logger.info("[CHECK] text-only task but all steps failed — reporting unsatisfied")
                task.append_log("iteration_llm_check", {
                    "satisfied": False,
                    "missing": ["all_steps_failed"],
                    "reason": "text-only delivery but all steps failed",
                    "_source": "text_only_bypass_step_failure",
                    "valid_file_count": 0,
                    "file_count": 0,
                })
                return {
                    "satisfied": False,
                    "missing": ["all_steps_failed"],
                    "reason": "text-only delivery but all steps failed",
                    "_source": "text_only_bypass_step_failure",
                    "valid_file_count": 0,
                    "file_count": 0,
                }
    except Exception as exc:
        logger.debug("[CHECK] text-only check failed: %s", exc)

    if llm_enabled and _adapter:
        try:
            files = _task_text_file_summaries(task)
            valid_files = [
                row for row in files
                if int(row.get("size") or 0) >= max(0, int((config.get("check") or {}).get("min_file_size") or 0))
                and not bool(row.get("diagnostic"))
            ]

            max_per_file = max(100, int(llm_check_cfg.get("max_content_chars_per_file", 2000)))
            max_total = max(500, int(llm_check_cfg.get("max_total_content_chars", 6000)))
            file_list_lines = []
            content_parts = []
            total_chars = 0
            for row in valid_files[:12]:
                rel = str(row.get("relative_path") or "")
                size = int(row.get("size") or 0)
                prev = str(row.get("preview") or "")[:max_per_file]
                file_list_lines.append(f"- {rel} ({size}B)")
                if total_chars < max_total and prev:
                    take = min(len(prev), max_total - total_chars)
                    content_parts.append(f"--- {rel} ---\n{prev[:take]}")
                    total_chars += take

            prompt = _read_repo_prompt(
                str(llm_check_cfg.get("prompt_template") or "prompts/llm_check_prompt.txt"),
                "用户原始请求：{user_message}\n\n预期交付物标准：\n{acceptance_criteria}\n\n当前产物摘要：\n{content_sample}\n\n判断是否满足验收标准。输出 JSON。",
            )
            # Build content_sample: combine file list and content preview
            content_sample_lines = []
            if file_list_lines:
                content_sample_lines.append("文件列表：")
                content_sample_lines.extend(file_list_lines)
            if content_parts:
                content_sample_lines.append("\n内容样本：")
                content_sample_lines.extend(content_parts)
            content_sample_str = "\n".join(content_sample_lines) or "（无有效文件）"
            replacements = {
                "{{user_message}}": str(user_message or "")[:2400],
                "{{acceptance_criteria}}": (acceptance_criteria or "")[:3000],
                "{{content_sample}}": content_sample_str,
            }
            for key, value in replacements.items():
                prompt = prompt.replace(key, value)
            # Fallback: also try single-brace form
            single_brace_fixups = {
                "{user_message}": str(user_message or "")[:2400],
                "{acceptance_criteria}": (acceptance_criteria or "")[:3000],
                "{content_sample}": content_sample_str,
            }
            if "{user_message}" in prompt or "{acceptance_criteria}" in prompt:
                for key, value in single_brace_fixups.items():
                    prompt = prompt.replace(key, value)

            from ..harness_core import RobustExecutor, load_harness_config
            from .harness import _json_from_llm

            robust = await RobustExecutor(load_harness_config(_workspace)).execute(
                event_name="llm_check",
                task_instance=task,
                operation=lambda: _adapter.chat(prompt, purpose="classify"),
                on_timeout="fail_fast",
                on_failure="fail_fast",
                metadata={"model": llm_check_cfg.get("model", "gpt-4o-mini")},
            )
            if robust.ok:
                raw = str(robust.value or "{}")
                try:
                    parsed = _json_from_llm(raw)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    satisfied = bool(parsed.get("satisfied", False))
                    missing_raw = parsed.get("missing") or parsed.get("missing_items") or []
                    missing = [str(x) for x in (missing_raw if isinstance(missing_raw, list) else []) if str(x).strip()]
                    reason = str(parsed.get("reason") or "")
                    result = {
                        "satisfied": satisfied,
                        "missing": missing,
                        "reason": reason,
                        "_source": "llm_check",
                        "valid_file_count": len(valid_files),
                        "file_count": len(files),
                    }
                    task.append_log("iteration_llm_check", result)
                    # Store in task.metadata for downstream consumers (final summary, etc.)
                    if isinstance(task.metadata, dict):
                        task.metadata["check_feedback"] = result
                        try:
                            task.save()
                        except Exception:
                            pass
                    logger.info("[LLM_CHECK] task_id=%s satisfied=%s missing=%s", getattr(task, "task_id", ""), satisfied, missing)
                    return result
        except Exception as exc:
            logger.warning("[LLM_CHECK] exception during LLM check: %s", exc)
            task.append_log("iteration_llm_check_failed", {"error": str(exc)})

    # Fallback: rule-based check
    if not llm_check_cfg.get("fallback_to_rule_check", True):
        logger.info("[LLM_CHECK] no fallback; returning unsatisfied with empty missing")
        return {"satisfied": False, "missing": ["LLM check unavailable"], "_source": "llm_unavailable"}

    return _run_batch_check_rule(task, root_goal, config)


def _run_batch_check_rule(task, root_goal: str, config: dict) -> dict:
    """Rule-based check fallback (original logic). Used when LLM check is unavailable."""
    check_cfg = config.get("check") or {}
    files = _task_text_file_summaries(task)
    min_file_size = max(0, int(check_cfg.get("min_file_size") or 0))
    min_file_count = max(0, int(check_cfg.get("min_file_count") or 0))
    valid_files = [
        row for row in files
        if int(row.get("size") or 0) >= min_file_size and not bool(row.get("diagnostic"))
    ]
    corpus = "\n".join(str(row.get("preview") or "") for row in files if not row.get("diagnostic"))
    pmid_pairs = re.findall(r"\bPMID[:：\s]*([0-9]{5,10})\b|pubmed\.ncbi\.nlm\.nih\.gov/([0-9]{5,10})", corpus, re.I)
    pmid_values = {item for pair in pmid_pairs for item in pair if item}
    doi_values = set(re.findall(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", corpus, re.I))
    citation_count = len(pmid_values | doi_values)
    expected = getattr(task, "expected_artifacts", []) or []
    missing: list[str] = []
    if min_file_count and len(valid_files) < min_file_count:
        missing.append(f"valid_file_count<{min_file_count}")
    if expected:
        try:
            from ..harness_core import ArtifactValidator, load_harness_config

            validation = ArtifactValidator(load_harness_config(_workspace)).validate(task)
            if not validation.ok:
                missing.extend(str(item) for item in validation.missing)
        except Exception as exc:
            missing.append(f"artifact_validator_error:{exc}")
        found_exts = {os.path.splitext(str(row.get("relative_path") or ""))[1].lower() for row in valid_files}
        for item in expected:
            if not isinstance(item, dict):
                continue
            pattern = str(item.get("pattern") or "").strip().lower()
            if pattern in {"pdf_or_md", "*.pdf_or_md"} and not ({".pdf", ".md"} & found_exts):
                missing.append("artifact_type:pdf_or_md")
            for needle in item.get("must_contain") or []:
                text = str(needle).strip()
                if text and text.lower() not in corpus.lower():
                    missing.append(f"must_contain:{text}")
            try:
                expected_min_citations = int(item.get("min_citations") or 0)
            except Exception:
                expected_min_citations = 0
            if expected_min_citations and citation_count < expected_min_citations:
                missing.append(f"citations<{expected_min_citations}")
            if str(item.get("type") or "").strip().lower() == "section":
                title = str(item.get("title") or "").strip()
                try:
                    min_length = int(item.get("min_length") or 0)
                except Exception:
                    min_length = 0
                section_text = _extract_markdown_section(corpus, title)
                if not section_text or (min_length and len(section_text) < min_length):
                    missing.append("breakthrough_directions" if re.search(r"突破|创新", title) else f"section:{title or 'missing'}")
    field_hits: dict[str, int] = {}
    required_fields = [str(x).strip() for x in (check_cfg.get("required_fields") or []) if str(x).strip()]
    trigger_terms = [str(x).strip().lower() for x in (check_cfg.get("required_fields_trigger_terms") or []) if str(x).strip()]
    field_check_enabled = bool(required_fields) and (
        not trigger_terms or any(term in str(root_goal or "").lower() for term in trigger_terms)
    )
    if field_check_enabled:
        lower_corpus = corpus.lower()
        field_hits = {field: lower_corpus.count(field.lower()) for field in required_fields}
        min_hits = max(1, int(check_cfg.get("min_required_field_hits") or 1))
        if sum(1 for value in field_hits.values() if value >= min_hits) < min_hits:
            missing.append("required_fields")
    min_citations = max(0, int(check_cfg.get("min_citations") or 0))
    for needle in check_cfg.get("must_contain") or []:
        text = str(needle).strip()
        if text and text.lower() not in corpus.lower():
            missing.append(f"must_contain:{text}")
    breakthrough_cfg = check_cfg.get("breakthrough") if isinstance(check_cfg.get("breakthrough"), dict) else {}
    if breakthrough_cfg.get("enabled", False):
        patterns = [str(x).strip() for x in (breakthrough_cfg.get("title_patterns") or []) if str(x).strip()]
        min_len = max(0, int(breakthrough_cfg.get("min_length") or 0))
        sections = [_extract_markdown_section(corpus, pattern) for pattern in patterns]
        best = max((len(text) for text in sections if text), default=0)
        if best < min_len:
            missing.append("breakthrough_directions")
    citation_terms = [
        str(x).strip().lower()
        for x in (check_cfg.get("citation_trigger_terms") or ["文献", "论文", "研究", "综述", "方法", "效果", "对比", "突破", "literature", "paper", "review"])
        if str(x).strip()
    ]
    if min_citations and any(term in str(root_goal or "").lower() for term in citation_terms) and citation_count < min_citations:
        missing.append(f"citations<{min_citations}")
    if files and not valid_files:
        missing.append("diagnostic_or_fallback_only")
    satisfied = not missing
    result = {
        "satisfied": satisfied,
        "missing": missing,
        "valid_file_count": len(valid_files),
        "file_count": len(files),
        "field_hits": field_hits,
        "citation_count": citation_count,
        "pmids": sorted(pmid_values)[:20],
        "dois": sorted(doi_values)[:20],
        "files": [{"relative_path": row["relative_path"], "size": row["size"], "diagnostic": bool(row.get("diagnostic"))} for row in files[:20]],
    }
    logger.info("[CHECK] task_id=%s satisfied=%s missing=%s files=%s", getattr(task, "task_id", ""), satisfied, missing, len(files))
    task.append_log("iteration_check", result)
    return result


async def _generate_final_summary(
    task_instance,
    check_result: dict,
    file_list: list[str],
    user_message: str = "",
) -> str:
    """Generate a final summary using LLM after batch plan execution completes.

    Loads prompts/final_summary_prompt.txt, fills template variables, and
    calls the LLM adapter. Falls back to a plain-text summary if LLM is
    unavailable.
    """
    if not _adapter:
        logger.info("[FINAL_SUMMARY] adapter unavailable; skipping LLM summary")
        return _build_plain_final_summary(check_result, file_list)

    # Load final_summary config from llm_check.yaml
    llm_check_yaml = _load_yaml_named_config("llm_check.yaml", {})
    summary_cfg = llm_check_yaml.get("check", {}) if isinstance(llm_check_yaml.get("check"), dict) else {}
    model_name = str(summary_cfg.get("model") or "deepseek-flash")
    timeout_sec = int(summary_cfg.get("timeout", 45))

    prompt = _read_repo_prompt(
        "prompts/final_summary_prompt.txt",
        (
            "用户原始请求：{user_message}\n\n"
            "任务执行步骤：\n{step_summary}\n\n"
            "最终交付文件：\n{file_list}\n\n"
            "请用一段友好、简洁的中文总结任务完成情况。输出纯文本，不要JSON。"
        ),
    )

    # Build step summary from task log
    step_summary = _build_step_summary(task_instance)
    check_result_str = json.dumps(check_result, ensure_ascii=False, indent=2)
    file_list_str = "\n".join(f"- {os.path.basename(p)}" for p in (file_list or []))

    replacements = {
        "{{user_message}}": str(user_message or getattr(task_instance, "user_message", "") or "")[:2000],
        "{{step_summary}}": step_summary[:3000],
        "{{check_result}}": check_result_str[:2000],
        "{{file_list}}": file_list_str[:2000],
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)

    # Single-brace fallback
    single_brace_fixups = {k.strip("{}"): v for k, v in replacements.items()}
    for key, value in single_brace_fixups.items():
        prompt = prompt.replace("{" + key + "}", value)

    try:
        from ..harness_core import RobustExecutor, load_harness_config
        robust = await RobustExecutor(load_harness_config(_workspace)).execute(
            event_name="final_summary",
            task_instance=task_instance,
            operation=lambda: _adapter.chat(prompt, purpose="classify"),
            on_timeout="fail_fast",
            on_failure="fail_fast",
            metadata={"model": model_name},
        )
        if robust.ok:
            summary = str(robust.value or "").strip()
            if summary:
                logger.info("[FINAL_SUMMARY] generated for task_id=%s len=%d",
                            getattr(task_instance, "task_id", ""), len(summary))
                return summary
    except Exception as exc:
        logger.warning("[FINAL_SUMMARY] LLM call failed: %s", exc)

    return _build_plain_final_summary(check_result, file_list)


def _build_plain_final_summary(check_result: dict, file_list: list[str]) -> str:
    """Build a plain-text final summary without calling the LLM."""
    lines = []
    satisfied = bool(check_result.get("satisfied"))
    if satisfied:
        lines.append("✅ 任务完成，所有验收标准均已满足。")
    else:
        missing = check_result.get("missing") or []
        reason = str(check_result.get("reason") or "")
        if reason:
            lines.append(f"⚠️ 任务基本完成，但存在以下问题：{reason}")
        else:
            lines.append("⚠️ 任务完成，但仍有改进空间。")
        if missing:
            lines.append("需关注：" + "、".join(str(x) for x in missing[:5]))
    if file_list:
        fnames = [os.path.basename(p) for p in file_list if p]
        if fnames:
            lines.append("交付文件：" + "、".join(fnames[:6]))
    return "\n".join(lines)


def _build_step_summary(task_instance) -> str:
    """Extract step execution summary from task log."""
    log_entries = getattr(task_instance, "log_entries", None)
    if not log_entries or not isinstance(log_entries, list):
        return ""
    steps = []
    for entry in log_entries:
        if not isinstance(entry, dict):
            continue
        event = entry.get("event", "")
        data = entry.get("data", {})
        if event == "iteration_started":
            iteration = data.get("iteration", 0)
            plan = data.get("plan_steps", [])
            step_descs = []
            for step in (plan or [])[:5]:
                desc = step.get("description", step.get("event_type", ""))
                if desc:
                    step_descs.append(str(desc)[:80])
            if step_descs:
                steps.append(f"第{iteration + 1}轮计划：{' → '.join(step_descs)}")
    return "\n".join(steps[:8])


def _build_retry_history(task_instance) -> str:
    """Extract retry/failure history from task log."""
    log_entries = getattr(task_instance, "log_entries", None)
    if not log_entries or not isinstance(log_entries, list):
        return ""
    retries = []
    for entry in log_entries:
        if not isinstance(entry, dict):
            continue
        event = entry.get("event", "")
        data = entry.get("data", {})
        if event in ("iteration_llm_check_failed", "iteration_curiosity_failed", "iteration_check", "iteration_llm_check"):
            if isinstance(data, dict):
                if "error" in data:
                    retries.append(f"[失败] {data['error']}")
                elif event == "iteration_llm_check" and data.get("satisfied") is False:
                    missing = data.get("missing", [])
                    retries.append(f"[检查未通过] 缺失：{'、'.join(str(x) for x in missing[:3])}")
    return "\n".join(retries[:5])


def _batch_plan_progress_summary(
    result: object,
    planned_steps: int,
    *,
    completed_with_delivery: bool = False,
    completed_steps: int | None = None,
) -> tuple[str, str]:
    plan = list(getattr(result, "plan", None) or [])
    step_results = getattr(result, "step_results", None) or {}
    total = int(planned_steps or 0) or len(plan)
    done = int(completed_steps) if completed_steps is not None else (len(step_results) if isinstance(step_results, dict) else 0)
    next_event = ""
    if plan and isinstance(step_results, dict):
        completed_ids = set(step_results)
        for step in plan:
            if getattr(step, "id", "") not in completed_ids:
                next_event = getattr(step, "event_type", "") or ""
                break
    if completed_with_delivery:
        return (f"执行完成 {done}/{total} 步，已交付目标文件。", "stop_project")
    if total and done < total:
        return (f"执行完成 {done}/{total} 步。", next_event or "继续执行未完成步骤")
    if total:
        return (f"执行完成 {done}/{total} 步。", next_event or "检查交付物并补齐缺口")
    return ("设计阶段未形成可执行计划。", "重新生成可执行计划")


def _format_iteration_execution_summary(iteration: int, result: object, check_result: dict) -> str:
    step_results = getattr(result, "step_results", None) or {}
    lines = [f"第 {iteration} 轮执行总结：完成 {len(step_results)} 个步骤。"]
    highlights: list[str] = []
    for step_id, item in list(step_results.items())[:8]:
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip()
        skill = str(item.get("skill") or "").strip()
        json_payload = item.get("json") if isinstance(item.get("json"), dict) else {}
        count = json_payload.get("count") if isinstance(json_payload, dict) else None
        sources = json_payload.get("sources") if isinstance(json_payload, dict) else []
        if count is not None:
            source_text = "，来源：" + "、".join(str(x) for x in sources[:4]) if isinstance(sources, list) and sources else ""
            highlights.append(f"{step_id}：{skill or event_type} 得到 {count} 条结果{source_text}")
            continue
        files = item.get("files") if isinstance(item.get("files"), list) else []
        if files:
            highlights.append(f"{step_id}：生成文件 " + "、".join(os.path.basename(str(x)) for x in files[:3]))
            continue
        content = str(item.get("content") or item.get("error") or "").strip()
        if content:
            highlights.append(f"{step_id}：{_clip(content, 80)}")
    if highlights:
        lines.append("本轮主要产出：")
        lines.extend("- " + item for item in highlights[:6])
    lines.append(
        "当前检查："
        + ("通过" if check_result.get("satisfied") else "未通过")
        + f"；有效文件 {check_result.get('valid_file_count', 0)} 个"
        + (f"，真实引用 {check_result.get('citation_count', 0)} 条。" if "citation_count" in check_result else "。")
    )
    return "\n".join(lines)


def _format_check_summary(check_result: dict, *, satisfied: bool) -> str:
    files = check_result.get("files") if isinstance(check_result.get("files"), list) else []
    pmids = check_result.get("pmids") if isinstance(check_result.get("pmids"), list) else []
    dois = check_result.get("dois") if isinstance(check_result.get("dois"), list) else []
    if satisfied:
        head = "检查通过：当前产物已经满足本轮交付要求。"
    else:
        missing = [str(x) for x in (check_result.get("missing") or []) if str(x).strip()]
        head = "检查未通过：" + ("、".join(missing) if missing else "仍有信息缺口")
    lines = [
        head,
        f"证据状态：有效文件 {check_result.get('valid_file_count', 0)} 个"
        + (f"；真实引用 {check_result.get('citation_count', 0)} 条。" if "citation_count" in check_result else "。"),
    ]
    if pmids or dois:
        lines.append("已识别引用：" + "；".join((["PMID " + ", ".join(pmids[:5])] if pmids else []) + (["DOI " + ", ".join(dois[:3])] if dois else [])))
    if files:
        visible = [f"{row.get('relative_path')}({row.get('size')}B)" for row in files[:5] if isinstance(row, dict)]
        if visible:
            lines.append("已检查文件：" + "；".join(visible))
    return "\n".join(lines)


def _format_reflect_summary(gap: dict, root_goal: str) -> str:
    missing = [str(x) for x in (gap.get("missing_info") or []) if str(x).strip()]
    weak = [str(x) for x in (gap.get("weak_evidence") or []) if str(x).strip()]
    queries = [str(x) for x in (gap.get("suggested_queries") or []) if str(x).strip()]
    fields = [str(x) for x in (gap.get("required_fields") or []) if str(x).strip()]
    lines = [
        "反思结果：当前产物还没有完全支撑用户的初始目标。",
        f"初始目标：{_clip(root_goal, 120)}",
    ]
    if missing:
        lines.append("缺失信息：" + "；".join(missing[:5]))
    if weak:
        lines.append("证据薄弱：" + "；".join(weak[:5]))
    if fields:
        lines.append("后续必须补齐字段：" + "、".join(fields[:8]))
    if queries:
        lines.append("建议探索查询：" + "；".join(queries[:4]))
    return "\n".join(lines)


def _format_curiosity_plan_summary(plan: object, gap: dict, root_goal: str) -> str:
    steps = list(getattr(plan, "plan", []) or [])
    queries = [str(x) for x in (gap.get("suggested_queries") or []) if str(x).strip()]
    lines = [
        f"已生成补充探索计划：{len(steps)} 步",
        "探索依据：结合初始目标和上一轮反思，优先补齐真实引用、效果指标和突破方向证据。",
    ]
    if queries:
        lines.append("本轮探索方向：" + "；".join(queries[:4]))
    for step in steps[:5]:
        lines.append("- " + _batch_plan_step_label(step))
    if len(steps) > 5:
        lines.append(f"... 其余 {len(steps) - 5} 步")
    lines.append(f"下一步：{steps[0].event_type if steps else 'none'}")
    return "\n".join(lines)


async def _run_root_cause_diagnosis(task, root_goal: str, check_result: dict, config: dict) -> dict:
    """Reflect phase: LLM generates a step patch array based on missing items.

    Outputs a dict with:
      - "patches": list of step patch operations
      - "missing_items": original missing items
      - "reflection": natural language reflection summary
    """
    if not _adapter:
        return {"patches": [], "missing_items": check_result.get("missing", []), "reflection": "no adapter available"}

    from ..harness_core import RobustExecutor, load_harness_config
    from ..memory import MemoryManager
    from .harness import _json_from_llm, HarnessStep, MicroPlan

    reflect_cfg = config.get("reflect") or {}
    memory = MemoryManager.from_workspace(_workspace)
    memories = memory.retrieve(root_goal, top_k=int(reflect_cfg.get("memory_top_k") or 3))
    workspace_summary = _task_text_file_summaries(task)[:12]

    prompt = _read_repo_prompt(
        str(reflect_cfg.get("prompt_template") or "prompts/reflect_patch.txt"),
        "原始目标：{root_goal}\n缺失项：{missing_items}\n当前计划步骤列表：{current_plan_steps}\n已完成步骤历史：{history}\n生成步骤补丁。",
    )

    # ── Early-exit: check step results for needs_user_input errors ──
    # If a step failed with file_not_found or api_key error, skip LLM reflect and ask user directly
    try:
        if task and task.working_dir:
            import glob as _glob
            for rf in sorted(_glob.glob(os.path.join(task.working_dir, "_step_*.result.json"))):
                with open(rf, "r") as f:
                    sr = json.load(f)
                # _error_type is nested inside sr["result"] due to _write_step_result_json
                err_type = sr.get("result", {}).get("_error_type", "")
                error_text = str(sr.get("result", {}).get("error", ""))
                if err_type == "file_not_found":
                    # Auto-search for the missing file in common locations
                    missing_file = ""
                    for signal in ["no such file", "file not found", "No such file or directory", "cannot open", "not found:", "does not exist"]:
                        idx = error_text.lower().find(signal)
                        if idx >= 0:
                            # Extract the filename after the signal
                            rest = error_text[idx + len(signal):].strip().split("\n")[0].strip().strip("':\"")
                            if rest:
                                missing_file = rest
                            break
                    found_paths = []
                    if missing_file and os.path.exists(missing_file):
                        found_paths = [missing_file]
                    elif missing_file:
                        fname = os.path.basename(missing_file)
                        for search_root in ("/data", "/mnt/e/work/data", "/mnt/e/work"):
                            if os.path.isdir(search_root):
                                for root, dirs, files in os.walk(search_root):
                                    if fname in files:
                                        found_paths.append(os.path.join(root, fname))
                                    if len(found_paths) >= 3:
                                        break
                                if found_paths:
                                    break
                    if found_paths:
                        suggestion = f"文件 {missing_file} 不存在，但在以下位置找到了：{found_paths[0]}"
                    else:
                        suggestion = f"文件 {missing_file} 不存在，且在文件系统中未找到"
                    return {
                        "patches": [],
                        "missing_items": [suggestion],
                        "reflection": f"【需询问用户】{suggestion}\n请确认正确的文件路径",
                    }
                elif err_type == "api_key":
                    return {
                        "patches": [],
                        "missing_items": ["DeepSeek API key 认证失败，需要你检查 API key 配置"],
                        "reflection": "【需询问用户】DeepSeek API key 无效或已过期，请检查 ~/.bashrc 中的 DEEPSEEK_API_KEY 配置",
                    }
                elif err_type == "agent_error":
                    return {
                        "patches": [],
                        "missing_items": [f"调用 agent 失败：{error_text[:200]}"],
                        "reflection": f"【需询问用户】Agent 命令执行失败，需要你确认相关工具是否安装。错误：{error_text[:200]}",
                    }
    except Exception:
        pass

    # Build current plan steps and history from task state
    current_plan_steps = []
    history = []
    attempt_count = 0
    try:
        if hasattr(task, "metadata") and isinstance(task.metadata, dict):
            if "last_plan" in task.metadata:
                current_plan_steps = task.metadata["last_plan"]
            if "completed_steps" in task.metadata:
                history = task.metadata["completed_steps"]
        # Count previous iterations from task logs
        try:
            log_path = os.path.join(task.working_dir, "task_log.jsonl") if task.working_dir else ""
            if log_path and os.path.exists(log_path):
                with open(log_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if entry.get("event") == "iteration_started":
                                attempt_count += 1
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        # Also build history from step failures in task log
        if not history and attempt_count > 0:
            try:
                log_path = os.path.join(task.working_dir, "task_log.jsonl") if task.working_dir else ""
                if log_path and os.path.exists(log_path):
                    step_entries = []
                    with open(log_path, "r") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = json.loads(line)
                                if entry.get("event") == "plan_executor_step_completed":
                                    step_entries.append(entry)
                            except json.JSONDecodeError:
                                continue
                    if step_entries:
                        history = [e.get("summary", "") for e in step_entries[-10:]]
            except Exception:
                pass
    except Exception:
        pass

    missing_items = [str(x) for x in (check_result.get("missing") or []) if str(x).strip()]
    max_items = max(1, int(reflect_cfg.get("max_missing_items", 10)))
    missing_items = missing_items[:max_items]

    replacements = {
        "{{root_goal}}": str(root_goal or "")[:2400],
        "{{missing_items}}": json.dumps(missing_items, ensure_ascii=False)[:2000],
        "{{current_plan_steps}}": json.dumps(current_plan_steps[:20], ensure_ascii=False)[:2000],
        "{{history}}": json.dumps(history[:20], ensure_ascii=False)[:2000],
        "{{attempt_count}}": str(attempt_count),
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)

    # Fallback: also try single-brace form for templates using {var} instead of {{var}}
    if any(k in prompt for k in ("{root_goal}", "{missing_items}", "{current_plan_steps}", "{history}", "{attempt_count}")):
        for key, value in {
            "{root_goal}": str(root_goal or "")[:2400],
            "{missing_items}": json.dumps(missing_items, ensure_ascii=False)[:2000],
            "{current_plan_steps}": json.dumps(current_plan_steps[:20], ensure_ascii=False)[:2000],
            "{history}": json.dumps(history[:20], ensure_ascii=False)[:2000],
            "{attempt_count}": str(attempt_count),
        }.items():
            prompt = prompt.replace(key, value)

    robust = await RobustExecutor(load_harness_config(_workspace)).execute(
        event_name="reflect_patch",
        task_instance=task,
        operation=lambda: _adapter.chat(prompt, purpose="classify"),
        on_timeout="fail_fast",
        on_failure="fail_fast",
        metadata={"model": reflect_cfg.get("model")},
    )

    result: dict = {
        "patches": [],
        "missing_items": missing_items,
        "reflection": "",
    }

    if not robust.ok:
        task.append_log("iteration_reflect_failed", {"error": robust.error})
        logger.warning("[REFLECT_PATCH] failed task_id=%s error=%s", task.task_id, robust.error)
        return result

    try:
        patches = _json_from_llm(str(robust.value or "[]"))
    except Exception as exc:
        task.append_log("iteration_reflect_failed", {"error": str(exc), "raw": str(robust.value or "")[:800]})
        logger.warning("[REFLECT_PATCH] bad JSON task_id=%s error=%s", task.task_id, exc)
        return result

    # Check if the response is an ask_user or report_failure dict (from reflect_patch.txt)
    if isinstance(patches, dict) and patches.get("action") in ("ask_user", "report_failure"):
        action = patches["action"]
        reflection = ""
        if action == "ask_user":
            reflection = f"【需询问用户】{patches.get('question', '')}"
            if patches.get("suggestions"):
                reflection += f"\n建议方案：{', '.join(patches['suggestions'])}"
        elif action == "report_failure":
            reflection = f"【报告失败】{patches.get('summary', '')}"
            if patches.get("root_causes"):
                reflection += f"\n根因：{', '.join(patches['root_causes'])}"
        logger.info("[REFLECT_PATCH] %s: %s", action, reflection[:200])
        task.append_log("iteration_reflect_result", {"action": action, "reflection": reflection[:500]})
        return {
            "patches": [],
            "missing_items": [reflection[:200]],
            "reflection": reflection,
        }

    if not isinstance(patches, list):
        patches = []

    # Validate patch format
    valid_actions = {"insert", "delete", "modify"}
    validated: list[dict] = []
    for p in patches:
        if not isinstance(p, dict):
            continue
        action = str(p.get("action") or "").strip().lower()
        if action not in valid_actions:
            continue
        p["action"] = action
        if action in ("insert", "modify") and not isinstance(p.get("new_step"), dict):
            continue
        validated.append(p)

    result["patches"] = validated
    reflection_text = f"Reflect identified {len(missing_items)} gaps, generated {len(validated)} step patches."
    result["reflection"] = reflection_text

    if validated:
        exp = f"Reflect: {len(missing_items)} gaps → {len(validated)} patches"
        memory.store(exp, tags=["reflect", "batch_plan", "patch"])

    task.append_log("iteration_reflect_patch", {
        "missing_items": missing_items,
        "patches": validated,
        "patch_count": len(validated),
    })
    logger.info("[REFLECT_PATCH] task_id=%s patches=%s missing=%s", task.task_id, len(validated), missing_items)
    return result


async def _run_plan_redesign(task, root_goal: str, gap: dict, registry, config: dict):
    """Curiosity phase: apply step patches from Reflect to the current MicroPlan.

    Takes the patches array from reflect output and applies insert/delete/modify
    operations to build a new MicroPlan. Falls back to old BatchPlanner-based
    generation if no patches are provided.
    """
    from .harness import HarnessStep, MicroPlan

    patches = gap.get("patches") if isinstance(gap.get("patches"), list) else []

    if patches:
        logger.info("[CURIOSITY_PATCH] applying %s patches task_id=%s", len(patches), getattr(task, "task_id", ""))
        plan_steps: list[HarnessStep] = []

        # Rebuild current plan from task metadata or empty
        try:
            if hasattr(task, "metadata") and isinstance(task.metadata, dict):
                last_plan_raw = task.metadata.get("last_plan", [])
                if isinstance(last_plan_raw, list):
                    plan_steps = [
                        HarnessStep(**step) if isinstance(step, dict) else step
                        for step in last_plan_raw
                    ]
        except Exception:
            plan_steps = []

        if not plan_steps:
            # No existing plan to patch; generate a minimal one from patches
            for p in patches:
                action = str(p.get("action") or "").strip().lower()
                ns = p.get("new_step")
                if action in ("insert", "modify") and isinstance(ns, dict):
                    plan_steps.append(HarnessStep(**ns))
            return MicroPlan(plan=plan_steps, expected_artifacts=[]), 0

        # Apply patches to the plan
        step_map = {s.id: s for s in plan_steps}
        used_ids = set(step_map.keys())

        for p in patches:
            action = str(p.get("action") or "").strip().lower()
            target_id = str(p.get("target_step_id") or "")
            ns = p.get("new_step")
            position = str(p.get("position") or "after").strip().lower()

            if action == "delete":
                if target_id in step_map:
                    plan_steps = [s for s in plan_steps if s.id != target_id]
                    step_map.pop(target_id, None)
                continue

            if action == "modify":
                if target_id in step_map:
                    if isinstance(ns, dict):
                        # Sanity check: extract depends_on from parameters if misplaced
                        params = dict(ns.get("parameters") or {})
                        deps_raw = ns.get("depends_on")
                        if deps_raw is None and "depends_on" in params:
                            deps_raw = params.pop("depends_on")
                        new_step = HarnessStep(
                            id=ns.get("id", target_id),
                            event_type=str(ns.get("event_type") or step_map[target_id].event_type),
                            parameters=params,
                            depends_on=list(deps_raw or step_map[target_id].depends_on or []),
                        )
                        for i, s in enumerate(plan_steps):
                            if s.id == target_id:
                                if position == "replace":
                                    plan_steps[i] = new_step
                                    step_map[target_id] = new_step
                                else:
                                    # modify: keep id, update other fields
                                    updated = HarnessStep(
                                        id=target_id,
                                        event_type=str(ns.get("event_type") or s.event_type),
                                        parameters=dict(ns.get("parameters") or s.parameters or {}),
                                        depends_on=list(ns.get("depends_on") or s.depends_on or []),
                                    )
                                    plan_steps[i] = updated
                                    step_map[target_id] = updated
                                break
                continue

            if action == "insert":
                if isinstance(ns, dict):
                    # Sanity check: depends_on may have been placed inside parameters
                    # by the LLM patch generator. Extract it to step level.
                    params = dict(ns.get("parameters") or {})
                    deps_raw = ns.get("depends_on")
                    if deps_raw is None and "depends_on" in params:
                        deps_raw = params.pop("depends_on")
                    new_step = HarnessStep(
                        id=ns.get("id", ""),
                        event_type=str(ns.get("event_type", "")),
                        parameters=params,
                        depends_on=list(deps_raw or []),
                    )
                    if new_step.id in used_ids:
                        idx = 2
                        while f"{new_step.id}_{idx}" in used_ids:
                            idx += 1
                        new_step.id = f"{new_step.id}_{idx}"
                    if target_id and target_id in step_map:
                        for i, s in enumerate(plan_steps):
                            if s.id == target_id:
                                insert_at = i + 1 if position == "after" else i
                                plan_steps.insert(insert_at, new_step)
                                break
                    else:
                        plan_steps.append(new_step)
                    step_map[new_step.id] = new_step
                    used_ids.add(new_step.id)

        # Preserve original expected_artifacts
        expected = []
        try:
            if hasattr(task, "metadata") and isinstance(task.metadata, dict):
                last_expected = task.metadata.get("last_expected_artifacts", [])
                if isinstance(last_expected, list):
                    expected = last_expected
        except Exception:
            pass

        micro_plan = MicroPlan(plan=plan_steps, expected_artifacts=expected)
        task.append_log("iteration_curiosity_patches_applied", {
            "patches_applied": len(patches),
            "resulting_steps": len(plan_steps),
            "step_ids": [s.id for s in plan_steps],
        })
        logger.info("[CURIOSITY_PATCH] applied %s patches → %s steps task_id=%s", len(patches), len(plan_steps), getattr(task, "task_id", ""))
        return micro_plan, 1

    # Fallback: old BatchPlanner-based generation
    from ..planner import BatchPlanner

    max_steps = max(1, int(((config.get("curiosity") or {}).get("max_steps")) or 3))
    planner = BatchPlanner.from_workspace(_workspace)
    planner.config = dict(planner.config)
    planner.config.update({
        "max_steps": max_steps,
        "min_steps": 1,
        "research_min_steps": 1,
        "allow_min_steps_below_three": True,
    })
    missing_items = [str(x) for x in (gap.get("missing_items") or []) if str(x).strip()]
    query = (
        "补充计划。原始目标：\n"
        + str(root_goal or "")[:1600]
        + "\n\nReflect 缺口：\n"
        + json.dumps(missing_items, ensure_ascii=False)[:1800]
        + "\n\n只规划补齐缺口的最小步骤。"
    )
    plan, calls = await planner.plan(
        adapter=_adapter,
        user_message=query,
        task_instance=task,
        registry=registry,
        state_md="",
    )
    task.append_log("iteration_curiosity_fallback_plan", {
        "steps": [step.__dict__ for step in plan.plan],
        "planner_calls": calls,
    })
    logger.info("[CURIOSITY] fallback plan task_id=%s steps=%s", task.task_id, len(plan.plan))
    return plan, calls


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

    from ..projects.project_state import recover_active_from_plan
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
            from ..projects.project_state import get_project_status, set_project_status
            from ..projects.project_registry import maybe_release_inactive_active_project
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
        from ..projects.project_state import consolidate_project_files
        seen = set()
        for projects_dir in (
            os.path.join(_workspace, "projects"),
            os.path.join(_workspace, "projects", "projects"),
        ):
            if os.path.isdir(projects_dir):
                for name in os.listdir(projects_dir):
                    path = os.path.join(projects_dir, name)
                    norm = os.path.abspath(path)
                    if os.path.isdir(path) and norm not in seen:
                        seen.add(norm)
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
                from ..projects.project_state import clear_active

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
            from ..knowledge.content_tools import split_image_for_vision

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
                from ..knowledge.content_tools import ocr_image_path

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
        from ..knowledge.research_memory import record_user_signal, record_episode
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
        user_mind_dir = os.path.join(_workspace, "state", "user", "partner_mind")
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
            from ..projects.project_state import get_project_status, set_project_status

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
        from ..projects.project_state import get_active
        return get_active(_workspace) or ""
    except Exception:
        return ""


# ── WAKE_UP ─────────────────────────────────────────────────────────


async def _handle_wake_up(event: MindEvent):
    """唤醒脉冲：检查 active_project.txt → 如有则创建 PROJECT 事件。

    没有活跃项目则什么都不做（不提示用户、不搜索）。
    """
    pool = await ensure_pool()
    logger.info(f"[WAKE_UP] 唤醒脉冲开始执行")
    _heartbeat_probe_ollama("wake_up")

    from ..projects.project_state import recover_active_from_plan
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
            from ..projects.project_state import get_project_status, set_project_status
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
