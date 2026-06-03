"""Mind Executor — Hermes 调度转发层。

仅保留 PROJECT / CRON_TICK / REPORT / WAKE_UP 四种事件类型。
Partner 只负责：读 state → 调 Hermes → 转发回复 → 按 UPDATE_STATE: 标记写 state。
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time as _time
from datetime import datetime
from typing import Optional

from .event_types import MindEvent, EventType, report
from .pool import MindPool
from ..adapter import USER_FRIENDLY_PROGRESS_REPLY
from ..research_memory import (
    append_strategy_memory,
    build_cross_project_context,
    build_reflection_context,
    build_research_context,
    consolidate_research_memory,
    ensure_habits,
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
    should_send_user_report,
)
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

# 报告去重缓存：{content_hash: timestamp}，10分钟内同一内容不重复推送
_report_dedup_cache: dict = {}

# 规划循环检测：{project_title: consecutive_plan_only_count}
_plan_loop_counter: dict = {}

# 上一轮汇报内容缓存：{project_title: (findings_hash, next_action_hash)}
_last_report_cache: dict = {}
_last_user_report_sent_at: float = 0.0
_stalled_repair_counter: dict = {}


def _state_dir() -> str:
    path = os.path.join(_workspace or ".", "state")
    os.makedirs(path, exist_ok=True)
    return path


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
            "我先继续在后台处理，晚点给你汇报进展",
            "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
            "处理超时了，稍后再试吧",
            "Error: agent backend not available",
            "Reached maximum iterations",
            "tirith security scanner",
        )
    )


def _is_low_value_user_visible_text(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    low_value_patterns = [
        r"项目已完成",
        r"项目状态为已完成",
        r"归档状态",
        r"不再进行新的",
        r"仅保留项目文件",
        r"文件.*齐全",
        r"目录结构完整",
        r"稳定终态",
        r"无新变化",
        r"没有确定具体方向",
        r"还没有确定具体方向",
        r"你这边有什么想做",
        r"有什么想做的",
        r"可以直接跟我说",
        r"我来安排推进",
        r"本轮执行结束，正在依据状态文件整理下一步",
        r"本轮执行结束，正在整理",
        r"正在整理成更清楚的汇报",
        r"已继续整理证据",
        r"下一轮会优先落到可验证动作",
        r"我这轮还在继续推进",
        r"我会按现在这条线继续往下推",
        r"有结果了再.*汇报",
        r"当前项目状态清晰",
        r"项目当前进展正常",
        r"当前项目共维护",
        r"本轮没有产生新的额外产出",
        r"文件列表",
        r"^你好[，,]?",
        r"这是本轮进展汇报",
        r"当前相关文件",
        r"文件体系",
        r"目录.*完备",
        r"文件体系.*完备",
        r"文件完整",
        r"核心文档.*更新",
        r"当前状态[:：]\s*/",
        r"下一步[:：]\s*关键目录",
        r"关键目录[:：]",
        r"^\S+\.md\s*$",
    ]
    if any(re.search(pattern, stripped) for pattern in low_value_patterns):
        return True
    if re.search(r"(成长|更新了.*习惯|以后会|学到|改变了.*判断|改变了.*推进)", stripped):
        concrete_markers = (
            "验证",
            "审计",
            "实验",
            "结果",
            "发现",
            "风险",
            "修正",
            "排查",
            "定位",
            "恢复",
        )
        if not any(marker in stripped for marker in concrete_markers):
            return True
        if re.search(r"当前状态[:：]\s*<path>|下一步[:：]\s*关键目录|关键目录", stripped):
            return True
        return False
    return False


def _is_file_operation_report(text: str) -> bool:
    """Reports should describe research progress, not filesystem bookkeeping."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    file_ops = [
        r"\b[\w.-]+\.md\b",
        r"\b[\w.-]+\.py\b",
        r"/(?:mnt|home|tmp)/",
        r"\d+\s*字节",
        r"总文件数",
        r"文件数",
        r"Verified文件",
        r"Hypothesis文件",
        r"Inferred文件",
        r"目录结构",
        r"关键目录",
        r"文件完整",
        r"文件齐全",
        r"更新.*文件",
        r"创建.*文件",
        r"写入.*文件",
        r"产物已写入",
    ]
    if not any(re.search(pattern, stripped, re.I) for pattern in file_ops):
        return False
    content_markers = [
        "结论",
        "发现",
        "验证",
        "审计",
        "风险",
        "不可信",
        "泄露",
        "过拟合",
        "对比",
        "差异",
        "失败原因",
        "下一步",
    ]
    return not any(marker in stripped for marker in content_markers)


def _extract_content_report_from_parsed(parsed: dict) -> str:
    """Build a user-facing report from parsed structured fields only."""
    if not parsed:
        return ""
    findings = [str(x).strip() for x in (parsed.get("findings") or []) if str(x).strip()]
    next_action = str(parsed.get("next_action") or "").strip()
    step_done = str(parsed.get("step_done") or "").strip()

    if step_done and _is_file_operation_report(step_done):
        step_done = "完成了一轮内容核验和判断更新。"
    if step_done and re.search(r"\b[\w.-]+\.(?:md|py)\b|/mnt/|/home/|字节|关键目录|目录结构", step_done):
        step_done = "完成了一轮内容核验和判断更新。"

    lines = []
    if step_done:
        lines.append(f"本轮完成：{_clip(step_done, 90)}")
    if findings:
        lines.append(f"关键判断：{_clip('；'.join(findings[:2]), 150)}")
    if next_action:
        lines.append(f"下一步：{_clip(next_action, 120)}")
    text = _sanitize_user_report_text("\n".join(lines).strip())
    if _is_low_value_user_visible_text(text) or _is_file_operation_report(text):
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


def _recent_report_seen(sig: str, ttl_sec: int = 21600) -> bool:
    if not sig:
        return False
    path = os.path.join(_state_dir(), "report_history.json")
    now = _time.time()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    history = data.get("history") if isinstance(data, dict) else {}
    if not isinstance(history, dict):
        history = {}
    seen = float(history.get(sig) or 0)
    history = {k: v for k, v in history.items() if now - float(v or 0) <= ttl_sec}
    history[sig] = now
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"history": history}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return bool(seen and now - seen <= ttl_sec)


def _report_min_interval_sec() -> int:
    raw = os.getenv("PARTNER_REPORT_MIN_INTERVAL_SEC", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except Exception:
            pass
    return 600


def _sanitize_user_report_text(text: str) -> str:
    """Remove internal agent/runtime noise before user-facing delivery."""
    if not text:
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
        lines.append(raw.rstrip())
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"(最终){4,}", "最终", cleaned)
    cleaned = re.sub(r"(_final){4,}", "_final", cleaned, flags=re.I)
    cleaned = re.sub(
        r"(如需|如果你需要|你可以|请告知|随时告诉我|等待你|待你|待用户|有啥想继续搞|你想继续搞|你想让我|你要我|要不要|请选择|给我方向).*",
        "",
        cleaned,
    )
    return cleaned


def _project_event_title(ev) -> str:
    try:
        if getattr(ev, "type", None) != EventType.PROJECT:
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


def _claimed_missing_paths(base_dir: str, text: str, limit: int = 8) -> list[str]:
    """Find relative paths mentioned as evidence but absent on disk."""
    if not base_dir or not text:
        return []
    candidates: set[str] = set()
    patterns = [
        r"`([^`\n]+)`",
        r"(?m)(?:路径|文件|脚本|目录|训练集|结果|环境)[:：]\s*([^\s，,；;]+)",
        r"(?m)(\b(?:data|results|scripts|models|model|outputs|reinvent_env|venv)/[^\s，,；;]+)",
        r"(?m)(\b(?:data|results|scripts|models|model|outputs|reinvent_env|venv)\b)",
        r"(?m)([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.(?:py|csv|json|txt|md|pkl|joblib|smi|sdf))",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = match[0] if isinstance(match, tuple) else match
            value = str(value).strip().strip("'\"。；;,，")
            if not value or value.startswith(("/", "http://", "https://")):
                continue
            if ".." in value.split("/"):
                continue
            if len(value) > 160:
                continue
            candidates.add(value)
    missing = []
    for rel in sorted(candidates):
        if not os.path.exists(os.path.join(base_dir, rel)):
            missing.append(rel)
        if len(missing) >= limit:
            break
    return missing


def _candidate_paths_from_text(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    patterns = [
        r"`([^`\n]+)`",
        r"(?m)(?:路径|文件|脚本|目录|训练集|结果|环境)[:：]\s*([^\s，,；;]+)",
        r"(?m)(\b(?:data|results|scripts|models|model|outputs|reinvent_env|venv)(?:/[^\s，,；;]+)?)",
        r"(?m)([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.(?:py|csv|json|txt|md|pkl|joblib|smi|sdf))",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text or ""):
            value = match[0] if isinstance(match, tuple) else match
            value = str(value).strip().strip("'\"。；;,，")
            if not value or value.startswith(("/", "http://", "https://")):
                continue
            if ".." in value.split("/") or len(value) > 160:
                continue
            if value not in seen:
                seen.add(value)
                candidates.append(value)
    return candidates


def _build_path_reality_audit(project_dir: str) -> tuple[str, list[str], list[str]]:
    recovery_text = _read_text(os.path.join(project_dir, "generation_pipeline_recovery.md"), 8000)
    candidates = _candidate_paths_from_text(recovery_text)
    defaults = [
        "data",
        "data/consolidated_smiles_final.csv",
        "reinvent_env",
        "reinvent_env/bin/activate",
        "results",
        "scripts",
        "scripts/01_seed_evaluation.py",
        "scripts/08_pharmacophore_screening.py",
        "scripts/09_admet_assessment.py",
    ]
    for item in defaults:
        if item not in candidates:
            candidates.append(item)

    verified = []
    missing = []
    for rel in candidates[:40]:
        if os.path.exists(os.path.join(project_dir, rel)):
            verified.append(rel)
        else:
            missing.append(rel)

    lines = [
        "# 路径真实性审计报告",
        "",
        "本文件由 Partner 代码根据文件系统真实扫描生成，不采用 LLM 对路径是否存在的判断。",
        "",
        "## Verified",
    ]
    if verified:
        lines.extend([f"- {item}" for item in verified])
    else:
        lines.append("- EMPTY")
    lines.extend(["", "## Missing"])
    if missing:
        lines.extend([f"- {item}" for item in missing])
    else:
        lines.append("- EMPTY")
    lines.extend(["", "## 结论"])
    if missing:
        lines.append("上一轮流水线恢复报告包含不存在的路径，不能据此声称流水线可运行。")
        lines.append("下一步应先从真实源项目或备份中恢复数据、脚本和运行环境，再执行分子生成或对接。")
    else:
        lines.append("候选路径均存在，可以进入最小命令试运行。")
    return "\n".join(lines).strip(), verified, missing


def _build_source_recovery_plan(project_dir: str) -> tuple[str, list[str]]:
    path_audit_text = _read_text(os.path.join(project_dir, "path_reality_check.md"), 8000)
    missing = []
    in_missing = False
    for raw in path_audit_text.splitlines():
        line = raw.strip()
        if line == "## Missing":
            in_missing = True
            continue
        if in_missing and line.startswith("## "):
            break
        if in_missing and line.startswith("- "):
            item = line[2:].strip()
            if item and item != "EMPTY":
                missing.append(item)

    source_candidates = []
    for name in ("project_contract.json", "project_brief.md", "exploration_log.md", "state.md"):
        text = _read_text(os.path.join(project_dir, name), 6000)
        for match in re.findall(r"(/mnt/[^\s，,；;`]+|/home/[^\s，,；;`]+)", text):
            if match not in source_candidates:
                source_candidates.append(match.strip().strip("'\"。"))

    lines = [
        "# 源恢复计划",
        "",
        "本文件由 Partner 代码根据 path_reality_check.md 生成。缺失项以文件系统扫描结果为准，不采用 LLM 自行判断。",
        "",
        "## Missing",
    ]
    if missing:
        lines.extend([f"- {item}" for item in missing])
    else:
        lines.append("- EMPTY")
    lines.extend(["", "## 可能源路径"])
    if source_candidates:
        lines.extend([f"- {item}" for item in source_candidates[:12]])
    else:
        lines.append("- unknown")
    lines.extend(["", "## 最小恢复动作"])
    if missing:
        lines.append("1. 先定位真实源项目或备份，优先查找包含 data、scripts、reinvent_env、results 的目录。")
        lines.append("2. 只复制缺失项，不继续生成或筛选分子。")
        lines.append("3. 复制后重新运行路径真实性审计，再决定是否启动最小 REINVENT/VAE 命令。")
    else:
        lines.append("1. 缺失项为空，可进入最小命令试运行。")
    return "\n".join(lines).strip(), missing


def _code_generated_missing_paths(project_dir: str) -> list[str]:
    """Return code-audited missing paths from source_recovery_plan/path audit."""
    for filename, marker in (
        ("source_recovery_plan.md", "本文件由 Partner 代码根据 path_reality_check.md 生成"),
        ("path_reality_check.md", "本文件由 Partner 代码根据文件系统真实扫描生成"),
    ):
        text = _read_text(os.path.join(project_dir, filename), 8000)
        if marker not in text:
            continue
        missing: list[str] = []
        in_missing = False
        for raw in text.splitlines():
            line = raw.strip()
            if line == "## Missing":
                in_missing = True
                continue
            if in_missing and line.startswith("## "):
                break
            if in_missing and line.startswith("- "):
                item = line[2:].strip()
                if item and item != "EMPTY":
                    missing.append(item)
        if missing:
            return missing
    return []


def _build_source_lookup_attempt(project_dir: str) -> tuple[str, list[str]]:
    """Search nearby filesystem roots for missing source files without LLM guessing."""
    missing = _code_generated_missing_paths(project_dir)
    roots = []
    for candidate in (
        os.path.dirname(project_dir),
        os.path.dirname(os.path.dirname(project_dir)),
        os.path.expanduser("~"),
        "/mnt/e/work",
    ):
        if candidate and os.path.isdir(candidate) and candidate not in roots:
            roots.append(candidate)

    basenames = [os.path.basename(item.rstrip("/")) for item in missing if os.path.basename(item.rstrip("/"))]
    basenames = [item for item in basenames if item not in {"data", "results", "scripts", "reinvent_env", "*.json"}]
    found: dict[str, list[str]] = {name: [] for name in basenames}
    scanned = 0
    max_scan = 12000
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "site-packages", ".cache"}
    for root in roots:
        for cur, dirs, files in os.walk(root):
            scanned += 1
            if scanned > max_scan:
                break
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            names = set(dirs) | set(files)
            for name in basenames:
                if name in names and len(found[name]) < 5:
                    found[name].append(os.path.join(cur, name))
        if scanned > max_scan:
            break

    lines = [
        "# 源路径查找尝试",
        "",
        "本文件由 Partner 代码根据 source_recovery_plan.md 的缺失项扫描生成。候选路径只来自本机文件系统，不采用 LLM 猜测。",
        "",
        "## Missing Inputs",
    ]
    if missing:
        lines.extend([f"- {item}" for item in missing])
    else:
        lines.append("- EMPTY")
    lines.extend(["", "## Candidate Sources"])
    any_found = False
    for name, paths in found.items():
        if paths:
            any_found = True
            lines.append(f"- {name}")
            lines.extend([f"  - {path}" for path in paths])
    if not any_found:
        lines.append("- unknown")
    lines.extend(["", "## Next"])
    if any_found:
        lines.append("先人工或代码校验候选源是否属于当前项目，再只复制缺失项并重新运行路径审计。")
    else:
        lines.append("未在可扫描范围内找到候选源。下一步应要求用户提供真实项目目录或备份位置，而不是继续分子生成。")
    return "\n".join(lines).strip(), [path for paths in found.values() for path in paths]


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
    title_lc = title.lower()
    state_lc = (state_md or "").lower()
    guardrail = load_project_guardrail(workspace, title)
    mainline = (guardrail.get("current_mainline") or "").strip()
    allowed_scope = [str(x).strip() for x in (guardrail.get("allowed_scope") or []) if str(x).strip()]
    forbidden_scope = [str(x).strip() for x in (guardrail.get("forbidden_scope") or []) if str(x).strip()]

    try:
        brief_text = read_project_brief(workspace, title, max_chars=3000)
    except Exception:
        brief_text = ""

    hot_text = brief_text + "\n" + state_md
    path_audit_path = os.path.join(project_dir, "path_reality_check.md")
    path_audit_text = _read_text(path_audit_path, 5000) if os.path.exists(path_audit_path) else ""
    code_missing_paths = _code_generated_missing_paths(project_dir)
    if code_missing_paths:
        return (
            "代码生成的路径审计仍显示关键源缺失。"
            f"缺失示例：{'；'.join(code_missing_paths[:6])}。"
            "本轮禁止继续突破队列、禁止继续声称流水线可运行、禁止生成/筛选分子。"
            "必须只做一个源恢复动作：定位真实源项目/备份/可复制路径，或把找不到源写成 unknown；"
            "如果找到候选源，只列出可复制项和复制前验证标准。",
            os.path.join(project_dir, "source_lookup_attempt.md"),
        )
    if (
        "本文件由 Partner 代码根据文件系统真实扫描生成" in path_audit_text
        and re.search(r"(?m)^## Missing\s*\n(?!- EMPTY)", path_audit_text)
    ):
        recovery_plan_path = os.path.join(project_dir, "source_recovery_plan.md")
        recovery_plan_text = _read_text(recovery_plan_path, 2000) if os.path.exists(recovery_plan_path) else ""
        if (
            _path_mtime(recovery_plan_path) < _path_mtime(path_audit_path)
            or "本文件由 Partner 代码根据 path_reality_check.md 生成" not in recovery_plan_text
        ):
            return (
                "path_reality_check.md 已由代码扫描确认关键数据、脚本或环境缺失。"
                "本轮禁止继续声称流水线可运行，禁止继续生成/筛选分子。"
                "必须读取 path_reality_check.md，列出缺失项，尝试定位真实源项目/备份/可复制路径，"
                "并写出一个最小恢复计划；如果找不到源，明确写 unknown，不要编造路径。",
                recovery_plan_path,
            )
    breakthrough_queue = _read_breakthrough_queue(workspace, title)
    if breakthrough_queue:
        latest_queue = _clip(breakthrough_queue[-1200:], 1200)
        if re.search(r"(数据泄露|泄露|leakage|data leak|不可信|太好|异常好|过拟合)", latest_queue, re.I):
            audit_path = os.path.join(project_dir, "data_leakage_audit.md")
            queue_path = _breakthrough_queue_path(workspace, title)
            if os.path.exists(audit_path) and _path_mtime(audit_path) >= _path_mtime(queue_path):
                logger.info("[PROJECT] 数据泄露队列已有更新后的审计文件，跳过重复审计目标")
            else:
                return (
                    "优先执行 breakthrough_queue.md 最新用户风险信号：当前结果可能存在数据泄露/过拟合/异常好。"
                    "本轮必须做证据审计，不继续调参或宣布新最佳。检查数据划分、特征工程是否使用全量数据、"
                    "bootstrap/交叉验证是否泄露测试信息、结果是否符合用户经验基线；输出 verified/hypothesis/suspicious，"
                    "并写出修正后的下一实验。",
                    audit_path,
                )
    audit_scan_text = hot_text + "\n" + breakthrough_queue[-6000:]
    recent_task_ids = re.findall(r"\bTask[-_ ]?(\d{2,})\b", audit_scan_text, re.I)
    repeated_quality_claims = len(re.findall(r"(优质分子|综合得分|完成率|当前最佳|最终报告|文件完整|核心文档|新增突破任务)", audit_scan_text))
    progress_audit_path = os.path.join(project_dir, "progress_quality_audit.md")
    progress_audit_text = _read_text(progress_audit_path, 4000) if os.path.exists(progress_audit_path) else ""
    if os.path.exists(progress_audit_path):
        if "鲍曼" in title or "分子" in title:
            followup_path = os.path.join(project_dir, "unique_result_verification.md")
            if _path_mtime(followup_path) < _path_mtime(progress_audit_path):
                return (
                    "progress_quality_audit.md 已指出项目存在机械递增/重复堆数量风险。"
                    "本轮禁止新增 Task 编号、禁止继续生成更多变体。必须读取最近结果，"
                    "统计唯一 SMILES、去重后有效分子数、骨架多样性和重复率，"
                    "并判断上一轮所谓优质分子是否是真实增量。",
                    followup_path,
                )
            unique_text = _read_text(followup_path, 4000) if os.path.exists(followup_path) else ""
            recovery_path = os.path.join(project_dir, "generation_pipeline_recovery.md")
            if (
                re.search(r"(无真实增量|生成分子[:：]\s*0|声称生成的分子文件.*不存在|文件均不存在)", unique_text)
                and _path_mtime(recovery_path) < _path_mtime(followup_path)
            ):
                return (
                    "unique_result_verification.md 已确认上一轮没有真实生成分子。"
                    "本轮禁止再声称生成新分子，先恢复真实分子生成流水线：定位 VAE/VQ-VAE、REINVENT、AutoDock 或 reward 脚本，"
                    "列出真实输入、输出路径、最小可运行命令；如果不能运行，要写明缺哪个文件/依赖。"
                    "产物必须让下一轮能直接按命令执行。",
                    recovery_path,
                )
            recovery_text = _read_text(recovery_path, 5000) if os.path.exists(recovery_path) else ""
            missing_claimed = _claimed_missing_paths(project_dir, recovery_text)
            path_audit_path = os.path.join(project_dir, "path_reality_check.md")
            path_audit_text = _read_text(path_audit_path, 2000) if os.path.exists(path_audit_path) else ""
            if (
                missing_claimed
                and re.search(r"(verified|存在|可运行|无缺失|已就位)", recovery_text, re.I)
                and (
                    _path_mtime(path_audit_path) < _path_mtime(recovery_path)
                    or "本文件由 Partner 代码根据文件系统真实扫描生成" not in path_audit_text
                )
            ):
                return (
                    "generation_pipeline_recovery.md 声称若干路径存在/可运行，但系统检测到这些相对路径实际缺失："
                    f"{'；'.join(missing_claimed[:6])}。本轮必须做路径真实性审计："
                    "不要继续实验，不要声称流水线可运行；逐项核对项目目录下真实存在的脚本、数据和输出，"
                    "把 verified / missing / unknown 分开，并给出恢复真实流水线的一个最小动作。",
                    path_audit_path,
                )
        elif "内容巡游" in title or "agent" in title.lower():
            if re.search(r"(verified_signal_index.*冗余|重新索引.*无新信息|实际新增信息为0|无新信息)", progress_audit_text):
                fresh_path = os.path.join(project_dir, "fresh_source_verification.md")
                if _path_mtime(fresh_path) < _path_mtime(progress_audit_path):
                    return (
                        "progress_quality_audit.md 已确认继续整理旧文件没有新增信息。"
                        "本轮不要新建索引、不要复述已有文件。必须选择一个尚未验证的 hypothesis 或外部内容点，"
                        "做一次最小来源验证：给出来源 URL/文件、可验证事实、无法验证的部分和下一步动作。",
                        fresh_path,
                    )
            followup_path = os.path.join(project_dir, "verified_signal_index.md")
            if _path_mtime(followup_path) < _path_mtime(progress_audit_path):
                return (
                    "progress_quality_audit.md 已指出项目可能偏文件清单/假设堆积。"
                    "本轮不要继续新增总结文档。必须把现有内容分成 verified / inferred / hypothesis，"
                    "列出每条 verified 信号的证据来源，并给出一个下一步可验证动作。",
                    followup_path,
                )
        else:
            followup_path = os.path.join(project_dir, "quality_followup.md")
            if _path_mtime(followup_path) < _path_mtime(progress_audit_path):
                return (
                    "progress_quality_audit.md 已指出推进质量风险。"
                    "本轮不要继续扩展项目，先执行审计后的最小验证动作并落盘。",
                    followup_path,
                )
    if len(set(recent_task_ids[-8:])) >= 4 or repeated_quality_claims >= 10:
        return (
            "检测到项目近期可能在机械递增任务编号、重复堆数量或反复宣称文件完整/当前最佳。"
            "本轮不要继续新增 Task 编号、不要继续扩大数量、不要写最终报告。"
            "只做推进质量审计：核对最近 3-5 轮的真实输入文件、脚本、输出路径、唯一结果和可复现实验，"
            "区分 verified / hypothesis / suspicious，并给出一个去重后的下一步最小验证动作。",
            os.path.join(project_dir, "progress_quality_audit.md"),
        )

    if breakthrough_queue:
        return (
            "优先执行项目目录里的 breakthrough_queue.md 最新 open 项。"
            "先读取该文件和当前状态，只消化最新一个突破任务；必须做一个最小可验证动作并落盘，"
            "把结果写入 breakthrough_execution.md，同时在突破队列中追加本次处理结论。"
            "禁止输出项目已完成、等待新指令、NEXT 无。",
            os.path.join(project_dir, "breakthrough_execution.md"),
        )

    repeated_download = hot_text.count("下载更多化合物")
    repeated_final = len(re.findall(r"(?:最终|_final)", hot_text, re.I))
    if repeated_download >= 3 or repeated_final >= 8:
        return (
            "检测到当前路线出现重复动作/命名污染。本轮不要再新建“final/最终/下载更多”脚本，"
            "只做去重和转向：统计已有化合物/脚本的唯一有效结果，写清为什么重复下载没有形成真实增量，"
            "并给出下一步非重复最小动作（例如合并唯一SMILES、训练集质量审计、VAE训练前置条件）。",
            os.path.join(project_dir, "repetition_break.md"),
        )

    open_idea = get_open_idea(workspace, title)
    if open_idea:
        idea_text = str(open_idea.get("content") or open_idea.get("idea") or "").strip()
        if re.search(r"(发展图景|未来趋势|roadmap|技术发展|agent)", idea_text, re.I):
            return (
                "优先处理用户未消化想法：基于当前 workspace 已有文档，不大范围重新搜索，"
                "总结“当前 Agent 技术发展图景与未来趋势”。需要先读取本地评测框架/gap/roadmap，"
                "可写一个小脚本辅助整理关键词；最后把结论落盘成一份可读文档。",
                os.path.join(project_dir, "agent_tech_landscape.md"),
            )
        return (
            f"优先处理用户未消化想法：{_clip(idea_text, 260)}。只做一个最小闭环，并把处理结果落盘。",
            os.path.join(project_dir, "idea_response.md"),
        )

    content_items = get_open_content_items(workspace, project=title, limit=1)
    content_items = [item for item in content_items if bool(item.get("should_nudge_project", False))]
    if content_items:
        item = content_items[0]
        return (
            "优先消化用户/自巡游分享的外部内容素材。不要把它当成事实结论；先提炼它可能启发的研究假设、"
            "和当前项目的关系、一个可验证的最小动作，并把结果落盘。",
            os.path.join(project_dir, "external_content_digest.md"),
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

    if "agent" in title_lc and "文献" in title:
        framework_path = os.path.join(project_dir, "evaluation_framework_outline.md")
        gap_matrix_path = os.path.join(project_dir, "benchmark_gap_matrix.md")
        memory_gap_path = os.path.join(project_dir, "long_memory_gap_note.md")
        roadmap_path = os.path.join(project_dir, "next_benchmark_roadmap.md")
        if not os.path.exists(framework_path):
            return (
                "只写一份评测框架提纲，先补 3-5 条核心评测维度，不扩展到新检索。",
                framework_path,
            )
        if not os.path.exists(gap_matrix_path):
            return (
                "只整理一张 benchmark gap matrix，写 4-6 行“已覆盖/未覆盖/影响”的对照。",
                gap_matrix_path,
            )
        if not os.path.exists(memory_gap_path):
            return (
                "只补一张长期记忆评测缺口 note，写清为什么它仍是空白，以及后续怎么补 benchmark。",
                memory_gap_path,
            )
        if not os.path.exists(roadmap_path):
            return (
                "只写一个 2-3 项的后续阅读路线图，每项一句理由。",
                roadmap_path,
            )
        return (
            "只补现有评测框架里最薄弱的一节，最多新增 5 行，不开新主题。",
            framework_path if step % 2 == 0 else memory_gap_path,
        )

    source_roots = [str(x).strip() for x in guardrail.get("source_roots", []) if str(x).strip()]
    if source_roots:
        recovery_path = os.path.join(project_dir, "recovery_checklist.md")
        entry_path = os.path.join(project_dir, "main_entrypoint.md")
        result_path = os.path.join(project_dir, "current_best_result.md")
        seed_path = os.path.join(project_dir, "bootstrap_plan.md")
        source_hint = f"已配置真实源目录：{'；'.join(source_roots[:3])}。"
        if not os.path.exists(recovery_path):
            return (
                f"{source_hint}只写一个恢复清单，围绕 source_roots 列出先复制什么、先验证什么，不要去别处搜索。",
                recovery_path,
            )
        if not os.path.exists(entry_path):
            return (
                f"{source_hint}只确认唯一主入口脚本，"
                "写明脚本路径、它负责什么、运行前依赖什么，控制在 5 行内，不讨论别的脚本。",
                entry_path,
            )
        if not os.path.exists(result_path):
            return (
                f"{source_hint}只确认当前最可信的一组结果，"
                "写明结果目录、关键指标、为什么它是当前基线，控制在 5 行内，不展开新实验。",
                result_path,
            )
        if not os.path.exists(seed_path):
            return (
                f"{source_hint}只写一个启动计划，"
                "以前面确定的主入口脚本和当前最可信结果为基础，列 3-5 个最小步骤，不要去别处搜索。",
                seed_path,
            )
        return (
            f"{source_hint}只补一个最小执行动作，必须围绕已确认的主入口脚本或当前最可信结果展开。"
            "如果 source_roots 不可访问，就如实记录“真实源目录缺失/不可访问”，不要宣称项目完成。",
            seed_path if step % 2 == 0 else result_path,
        )

    if "benchmark" in state_lc or "agentbench" in state_lc or "swe-bench" in state_lc:
        return (
            "把当前 benchmark 相关结论整理成一个可复用的小产物，并明确下一步只推进一个最小子问题。",
            "",
        )

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


def _build_project_prompt(workspace: str, title: str, state_md: str, step: int) -> tuple[str, str]:
    from ..project_state import format_project_guardrail_for_prompt, read_project_brief

    objective, artifact_path = _choose_micro_objective(workspace, title, state_md, step)
    artifact_hint = os.path.basename(artifact_path) if artifact_path else "（优先补充现有项目文档或状态）"
    state_snapshot = _compact_state_snapshot(state_md)
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
    
    # 规划循环检测：如果连续多轮都是纯规划，强制要求执行
    plan_count = _plan_loop_counter.get(title, 0)
    execution_enforcement = ""
    if plan_count >= 2:
        execution_enforcement = (
            "\n⚠️ 执行警告：你已经连续 {} 轮只产出计划/方案而没有实际执行。"
            "\n本轮必须用 terminal 工具实际执行一个动作（运行代码、复制文件、下载数据等），"
            "不能只写文档。如果目标需要先写方案，写完后立即执行它。"
            "\n禁止本轮输出以\"设计了\"、\"规划了\"、\"方案\"开头的完成描述。"
        ).format(plan_count)
    
    prompt = (
        f"你是项目执行器，持续推进项目「{title}」。\n"
        f"{guardrail_prompt}"
        f"{brief_prompt}"
        f"{research_prompt}"
        f"{mind_prompt}"
        f"{content_prompt}"
        f"目标：{objective}\n"
        f"状态摘要：{state_snapshot}\n"
        f"建议产物：{artifact_hint}\n"
        f"行动集合：run_experiment/read_paper/inspect_result/update_report/debug_pipeline/summarize_failure/"
        f"test_transfer_method/write_ppt_section/refresh_project_brief/process_idea。\n"
        f"规则：每轮必须从行动集合里选一个 ACTION，只做一个最小闭环；优先用本地内容；默认不联网；只允许 HTTPS；禁止 curl|bash / curl|python；"
        f"把长期研究记忆当作启发和边界，不要机械复述；如果某方法在本项目失败，只记录边界，不要认为它在所有项目都失败；"
        f"如果项目看起来已经完成，不要输出归档/等待/不再推进；必须转入复盘、误差分析、失败边界、跨项目迁移、外部内容消化或下一突破口中的一个实际动作；"
        f"关键数字必须来自本地文件或本轮实际输出；没有证据就标为 hypothesis；用户汇报要分清 verified/inferred/next；"
        f"如果遇到 API key/预算/账号/真实数据/源目录等外部阻塞，可以明确写出 BLOCKER，但 NEXT 必须同时给出不依赖该资源的替代推进动作；"
        f"内容巡游类项目不能因为 GitHub README 容易获取就替代用户分享内容，GitHub 只能作为证据渠道；"
        f"simulation/dry-run/proxy/synthetic/toy data 必须明确标注，不能写成真实 API、真实最佳或真实突破；"
        f"不要让用户做选择题，不要碰 /mnt/e/work/biomni*；不要输出 tool_call、function、terminal、read_file、write_file 标签；"
        f"DONE/FINDINGS/NEXT 必须写内容进展和判断，不要把“更新某文件、文件数、字节数、目录结构”当作成果；"
        f"不要描述你'打算检查环境'，不要先说你要去看什么，直接给最终正文。"
        f"把状态摘要视为可信输入，除非目标明确要求，否则不要再重复检查这些文件是否存在。\n"
        f"执行要求：你有 terminal、file、web 工具可用。"
        f"如果本轮目标涉及运行代码、复制文件、下载数据、执行分析，必须用 terminal 工具实际执行，"
        f"不能只设计方案或写计划文件。先执行，再总结结果。\n"
        f"{execution_enforcement}"
        f"严格只输出：\n"
        f"ACTION: <从行动集合中选一个>\n"
        f"DONE: <本轮完成>\n"
        f"FINDINGS: <最多两条发现，用；分隔>\n"
        f"EVIDENCE: <证据文件/输出路径；没有则写 hypothesis>\n"
        f"NEXT: <下一步，必须是可执行动作；禁止写 无/等待新指令/项目已完成/关闭>\n"
        f"FILES: <本轮写入或更新的文件；没有则写 EMPTY>\n"
        f"STATE_DELTA: <3-6行状态增量，纯文本，不要重写整份 state.md>\n"
        f"ARTIFACT_CONTENT: <如果建议产物不是空，就直接给这个文件的正文；若无则写 EMPTY>\n"
    )
    return prompt, artifact_path


def _looks_like_stalled_project_result(parsed: dict) -> bool:
    if not parsed:
        return False
    text = "\n".join(
        str(parsed.get(k) or "")
        for k in ("step_done", "next_action", "state_delta", "evidence", "files")
    )
    if re.search(r"(项目已完成|项目已关闭|已关闭|项目关闭|归档状态|不再进行|等待新指令|等待用户|NEXT:\s*无)", text):
        return True
    next_action = str(parsed.get("next_action") or "").strip()
    if not next_action or re.fullmatch(r"(无|暂无|没有|无需|等待.*|已完成|关闭|N/?A|EMPTY)", next_action, re.I):
        return True
    evidence = str(parsed.get("evidence") or "").strip().lower()
    files = str(parsed.get("files") or "").strip().lower()
    action = str(parsed.get("action") or "").strip()
    if action == "inspect_result" and evidence == "hypothesis" and files in {"", "empty"}:
        return True
    return False


def _breakthrough_next_action(title: str, parsed: dict) -> str:
    lower = title.lower()
    if "年龄" in title or "age" in lower:
        return (
            "基于当前最佳结果做 post-completion 误差复盘：读取 FINAL_REPORT/current_best_result，"
            "拆分误差来源、极端年龄段表现和跨数据源偏差，写出一个可验证的下一实验。"
        )
    if "鲍曼" in title or "分子" in title:
        return (
            "从计算侧继续突破：对候选分子做 scaffold hopping/类似物风险排序/扩散或GNN生成可行性设计，"
            "形成一个无需等待湿实验的最小计算实验。"
        )
    if "agent" in lower or "前沿" in title or "内容巡游" in title:
        return (
            "回到已有材料做证据化推进：把 hypothesis 与真实文件/公开可验证来源分开，"
            "选择一个假设做交叉验证或写成可复用分析框架。"
        )
    return "做一次完成态逃逸复盘：列出已证实结果、未验证假设、失败边界，并生成一个可执行的下一步最小实验。"


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
            for item in re.split(r"[；;]", findings_line)
            if item.strip(" -")
        ][:2]

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
    
    def _content_done_text(raw: str) -> str:
        text = (raw or "").strip()
        if re.search(r"\b[\w.-]+\.(?:md|py)\b|写入|更新|创建|产出|文件|字节|路径|目录|关键目录", text):
            if findings:
                return "完成了一轮内容核验和判断更新。"
            return "完成了一轮项目推进。"
        return text

    lines = [f"最近完成：{_content_done_text(step_done)}"]
    if findings:
        lines.append("关键发现：")
        for finding in findings[:2]:
            lines.append(f"- {_clip(finding, 140)}")
    if next_action:
        lines.append(f"下一步：{next_action}")
    text = _sanitize_user_report_text("\n".join(lines).strip())
    if _is_low_value_user_visible_text(text) or _is_file_operation_report(text):
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


def _collect_report_context(workspace: str, title: str, project_outcome: str) -> dict:
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
    }


def _build_round_report_prompt(title: str, ctx: dict) -> str:
    return (
        f"你是 Partner 的用户汇报器。请根据本地状态，给用户发一条自然语言进展汇报。\n"
        f"项目：{title}\n"
        f"本轮执行结果：{ctx['project_outcome']}\n"
        f"状态摘要：\n{ctx['state_snapshot']}\n"
        f"{ctx.get('growth_context') or ''}\n"
        f"要求：\n"
        f"- 只用“本轮执行结果”和“状态摘要”判断内容进展，不要复述长日志。\n"
        f"- 不要提具体文件名、路径、写入/更新/创建了哪个 .md、字节数或目录结构；用户只关心完成了什么内容判断、发现了什么、下一步做什么。\n"
        f"- 以“状态摘要”为当前事实来源；除非这里明确显示缺失，否则不要声称文件不存在或状态丢失。\n"
        f"- 如果本轮没有形成明确新结果，不要生成汇报；返回空字符串。\n"
        f"- 直接像对用户汇报一样说话，用中文。\n"
        f"- 只有在有实质发现、风险、阻塞、突破或用户需要知道的习惯变化时才汇报。\n"
        f"- 有内容时说明现在在做什么、本轮发生了什么、下一步是什么。\n"
        f"- 如果“最近成长事件”非空，要用一句话说明我这次改变了什么判断习惯或推进习惯。\n"
        f"- 不要输出 JSON，不要用标题，不要问用户下一步，不要给选项。\n"
        f"- 不要说“待你指示/等待用户/请告知/随时告诉我”；如果项目完成，就说已进入归档或反思状态。\n"
        f"- 控制在 80-160 字。\n"
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


def _generate_round_report(title: str, project_outcome: str) -> str:
    if not _adapter:
        return ""
    ctx = _collect_report_context(_workspace, title, project_outcome)
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
    if _is_low_value_user_visible_text(reply) or _is_file_operation_report(reply):
        return ""
    if reply.startswith("{") and "partner_heartbeat" in reply:
        return ""
    return reply


# ── 公开接口 ────────────────────────────────────────────────────────


def set_push_callback(callback):
    """设置推送回调函数。

    callback 签名: func(content: str) -> None
    QQ bridge 在初始化时调用此函数注册回调。
    """
    global _push_callback
    _push_callback = callback
    logger.info(f"[MIND] Push callback registered: {callback}")


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
        EventType.CRON_TICK: _handle_cron_tick,
        EventType.REPORT: _handle_report,
        EventType.WAKE_UP: _handle_wake_up,
        EventType.REFLECTION: _handle_reflection,
        EventType.CROSS_PROJECT: _handle_cross_project,
        EventType.MEMORY_CONSOLIDATE: _handle_memory_consolidate,
        EventType.CONTENT_DIGEST: _handle_content_digest,
        EventType.CONTENT_PATROL: _handle_content_patrol,
    }.get(event_type)


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
    ensure_mind_files(_workspace, title)
    ensure_baseline_and_metric_contracts(_workspace, title)

    # 1. 读取项目状态
    state_md = read_state_md(_workspace, title)
    try:
        from ..project_state import read_project_brief
        hot_text = f"{state_md}\n{read_project_brief(_workspace, title, max_chars=2200)}"
    except Exception:
        hot_text = state_md

    open_idea = get_open_idea(_workspace, title)
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
    if project_status in {"cooling_down", "waiting"} and open_idea:
        set_project_status(_workspace, title, "active", "检测到未消化用户/老师信号，重新激活项目")
        project_status = "active"
    elif project_status in {"cooling_down", "waiting"} and not _cooling_down_enabled():
        set_project_status(_workspace, title, "active", "默认连续推进：不进入冷却等待")
        project_status = "active"
    elif project_status in {"cooling_down", "waiting"}:
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
            )
            try:
                response = _adapter.chat(prompt, purpose="project")
            except Exception as e:
                logger.warning(f"[PROJECT] Hermes 调用异常: {e}")
                response = None

        # 3. 处理 Hermes 回复
        hermes_response = (response or "").strip()
        parsed = _parse_structured_project_response(hermes_response)
        repaired_stalled_result = False
        if parsed and _looks_like_stalled_project_result(parsed):
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
            record_growth_event(
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
        audit_issues = audit_project_round(_workspace, title, parsed) if parsed else []
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
            record_growth_event(
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
        forced_artifact_text = ""
        if parsed and artifact_path and os.path.basename(artifact_path) == "path_reality_check.md":
            project_dir_for_audit = os.path.dirname(artifact_path)
            forced_artifact_text, verified_paths, missing_paths = _build_path_reality_audit(project_dir_for_audit)
            parsed["action"] = parsed.get("action") or "inspect_result"
            parsed["step_done"] = "完成路径真实性审计"
            if missing_paths:
                parsed["findings"] = [
                    f"检测到 {len(missing_paths)} 个声称存在但实际缺失的路径",
                    "不能继续声称流水线可运行，必须先恢复真实数据、脚本或环境",
                ]
                parsed["next_action"] = "先从真实源项目或备份恢复缺失路径，再运行最小分子生成命令。"
                parsed["state_delta"] = (
                    "路径真实性审计未通过：上一轮流水线恢复报告包含不存在的路径。\n"
                    f"缺失示例：{'；'.join(missing_paths[:6])}\n"
                    "当前不能把分子生成流水线视为可运行。"
                )
            else:
                parsed["findings"] = [
                    "候选数据、脚本和环境路径均通过文件系统扫描",
                    "下一步可以进入最小命令试运行",
                ]
                parsed["next_action"] = "执行最小分子生成命令并记录真实输出。"
                parsed["state_delta"] = "路径真实性审计通过：候选路径均存在，下一步进入最小命令试运行。"
            parsed["evidence"] = "path_reality_check.md"
            parsed["files"] = "path_reality_check.md"
            parsed["artifact_content"] = forced_artifact_text
        elif parsed and artifact_path and os.path.basename(artifact_path) == "source_recovery_plan.md":
            project_dir_for_audit = os.path.dirname(artifact_path)
            forced_artifact_text, missing_paths = _build_source_recovery_plan(project_dir_for_audit)
            parsed["action"] = parsed.get("action") or "inspect_result"
            parsed["step_done"] = "完成源恢复计划"
            if missing_paths:
                parsed["findings"] = [
                    f"仍有 {len(missing_paths)} 个关键路径缺失",
                    "不能继续生成或筛选分子，必须先恢复真实源文件",
                ]
                parsed["next_action"] = "定位真实源项目或备份，只复制缺失的数据、脚本和环境后再复查。"
                parsed["state_delta"] = (
                    "源恢复计划已生成：路径审计显示关键数据、脚本或环境仍缺失。\n"
                    f"缺失示例：{'；'.join(missing_paths[:6])}\n"
                    "当前禁止继续声称分子生成流水线可运行。"
                )
            else:
                parsed["findings"] = ["路径审计未发现缺失项", "可以进入最小命令试运行"]
                parsed["next_action"] = "执行最小分子生成命令并记录真实输出。"
                parsed["state_delta"] = "源恢复计划确认缺失项为空，下一步进入最小命令试运行。"
            parsed["evidence"] = "source_recovery_plan.md"
            parsed["files"] = "source_recovery_plan.md"
            parsed["artifact_content"] = forced_artifact_text
        elif parsed and artifact_path and os.path.basename(artifact_path) == "source_lookup_attempt.md":
            project_dir_for_audit = os.path.dirname(artifact_path)
            forced_artifact_text, candidate_paths = _build_source_lookup_attempt(project_dir_for_audit)
            missing_paths = _code_generated_missing_paths(project_dir_for_audit)
            parsed["action"] = parsed.get("action") or "inspect_result"
            parsed["step_done"] = "完成真实源路径查找"
            if candidate_paths:
                parsed["findings"] = [
                    f"找到 {len(candidate_paths)} 个候选源路径，需要先校验是否属于当前项目",
                    "复制前仍不能把分子生成流水线视为可运行",
                ]
                parsed["next_action"] = "校验候选源后只复制缺失项，并重新运行路径真实性审计。"
                parsed["state_delta"] = (
                    "真实源路径查找已完成：发现候选源，但尚未校验归属。\n"
                    "当前仍禁止直接继续分子生成，必须先复制缺失项并复查。"
                )
            else:
                parsed["findings"] = [
                    f"仍有 {len(missing_paths)} 个关键路径缺失",
                    "可扫描范围内没有找到候选源，不能编造恢复路径",
                ]
                parsed["next_action"] = "等待用户提供真实项目目录或备份位置；在此之前只做方案复核，不运行生成流水线。"
                parsed["state_delta"] = (
                    "真实源路径查找未找到候选源。\n"
                    f"仍缺失：{'；'.join(missing_paths[:6])}\n"
                    "当前不能继续声称分子生成流水线可运行。"
                )
            parsed["evidence"] = "source_lookup_attempt.md"
            parsed["files"] = "source_lookup_attempt.md"
            parsed["artifact_content"] = forced_artifact_text
        guardrail_result = {"issues": [], "report_type": "low_value", "progress_score": 0}
        if parsed and not hermes_response.strip() == USER_FRIENDLY_PROGRESS_REPLY:
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
                    record_growth_event(
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
            if _artifact_needs_structured_fallback(artifact_path, artifact_text):
                artifact_text = _structured_audit_artifact(artifact_path, parsed, hermes_response)
            artifact_written = _write_artifact_file(artifact_path, artifact_text)

        if new_state and not timed_out_or_stalled:
            write_state_md(_workspace, title, new_state)
            logger.info(f"[PROJECT] 状态已更新（{len(new_state)} 字符）")
        if artifact_written:
            logger.info(f"[PROJECT] 产物已写入: {os.path.basename(artifact_path)}")
            artifact_name = os.path.basename(artifact_path)
            if artifact_name.startswith("stage_report_") and artifact_name.endswith(".md"):
                try:
                    from ..stage_report import publish_stage_report

                    published = publish_stage_report(_workspace, title, artifact_path)
                    if published:
                        parsed.setdefault("findings", [])
                        parsed["findings"] = list(parsed.get("findings") or [])[:2] + [
                            "阶段汇报 PPT/PDF 已生成，可在用户目录 reports 中查看。"
                        ]
                        parsed["next_action"] = parsed.get("next_action") or "继续执行阶段汇报中的下一步最小计划。"
                        record_growth_event(
                            _workspace,
                            title,
                            trigger="项目积累到阶段性汇报节点",
                            learned="阶段成果需要转化成用户/老师可读的汇报，而不是只堆日志。",
                            behavior_change="后续每隔一段推进轮次自动生成阶段汇报 PPT/PDF，并把风险和失败边界一起呈现。",
                            evidence=os.path.basename(published.get("pdf") or artifact_name),
                            category="communication_habit",
                        )
                        logger.info(f"[PROJECT] 阶段汇报已发布: {published}")
                except Exception as exc:
                    logger.warning(f"[PROJECT] 阶段汇报发布失败: {exc}")
            if artifact_name == "data_leakage_audit.md":
                record_growth_event(
                    _workspace,
                    title,
                    trigger="用户/审计指出结果异常好或可能泄露",
                    learned="异常好结果不能直接当突破；必须先确认验证流程没有泄露。",
                    behavior_change="以后遇到过低误差、异常提升或用户经验不匹配时，优先生成数据泄露/过拟合审计，再继续实验。",
                    evidence=artifact_name,
                    category="quality_guardrail",
                )
            elif artifact_name == "progress_quality_audit.md":
                record_growth_event(
                    _workspace,
                    title,
                    trigger="检测到机械递增、重复堆数量或文件复用",
                    learned="持续运行不等于持续推进；重复生成更多数量可能是伪进展。",
                    behavior_change="以后会先核对唯一结果、真实脚本、输出路径和可复现实验，再决定是否继续扩大规模。",
                    evidence=artifact_name,
                    category="progress_quality",
                )

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
                current_open_idea = get_open_idea(_workspace, title)
                if current_open_idea and artifact_written:
                    idea_text = str(current_open_idea.get("content") or current_open_idea.get("idea") or "")
                    mark_idea_processed(_workspace, title, idea_text, status="absorbed")
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

        # 4. 推送至 QQ
        pool = await ensure_pool()
        report_type = str(guardrail_result.get("report_type") or "low_value")
        round_outcome = push_text
        if parsed and (
            not round_outcome
            or _is_low_value_user_visible_text(round_outcome)
            or _is_file_operation_report(round_outcome)
        ):
            round_outcome = _extract_content_report_from_parsed(parsed)
        round_outcome = improve_user_report(round_outcome, report_type)
        if repaired_stalled_result:
            logger.info("[PROJECT] Skip user-facing report: internal stalled-result repair")
            round_outcome = ""
        elif not round_outcome:
            if invalid_structured_reply:
                logger.info("[PROJECT] Skip user-facing report: invalid structured reply")
                round_outcome = ""
            elif timed_out_or_stalled:
                logger.info("[PROJECT] Skip user-facing report: model stalled or backend unavailable")
                round_outcome = ""
        allow_artifact_report = bool(
            parsed
            and artifact_written
            and not timed_out_or_stalled
            and not repaired_stalled_result
            and round_outcome
            and report_type == "low_value"
        )
        if parsed and not should_send_user_report(report_type) and not allow_artifact_report:
            logger.info(f"[REPORT] Skip proactive report by priority gate: {report_type}")
            round_outcome = ""
        elif allow_artifact_report:
            logger.info("[REPORT] Allow artifact-backed startup report despite low_value gate")
        report_text = _generate_round_report(title, round_outcome) if round_outcome else ""
        if (timed_out_or_stalled or repaired_stalled_result) and not round_outcome:
            report_text = ""
        used_fallback_report = False
        if not report_text:
            report_text = _build_round_report_fallback(title, round_outcome) if round_outcome else ""
            used_fallback_report = bool(report_text)
        report_text = improve_user_report(report_text, report_type)
        if report_text:
            await pool.put(MindEvent(
                type=EventType.REPORT,
                priority=4,
                payload={"content": report_text, "force_send": True},
                source="project:round_report_fallback" if used_fallback_report else "project:round_report",
                parent_id=event.id,
            ))

        # 5. 将自身放回等待室。CRON_TICK 是恢复/健康检查；项目生命线按状态推进。
        next_step = event.payload.get("step", 0) + 1
        active_now = get_active_project_name()
        if active_now and active_now != title:
            logger.info(f"[PROJECT] Not re-queueing stale project '{title}', active is '{active_now}'")
            logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                        f"title='{title[:40]}'")
            return
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

        logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                    f"title='{title[:40]}'")
    finally:
        _running_projects.discard(title)


# ── REPORT ──────────────────────────────────────────────────────────


async def _handle_report(event: MindEvent):
    """汇报念头：直接推送到 QQ（如果有活跃的 bot 连接），含去重。
    移除旧版 JSON 降级写入逻辑，仅保留 push_callback。
    """
    content = event.payload.get("content", "")
    content = _sanitize_user_report_text(content)
    content = improve_user_report(content, "meaningful_progress")
    if not content:
        logger.warning(f"[REPORT] Empty content, skipping {event.id[:8]}")
        return
    if _is_low_value_user_visible_text(content):
        logger.info(f"[REPORT] Skip low-value user-visible report: {content[:80]}...")
        return

    # ── 去重：同一内容在 10 分钟内不重复推送 ──
    global _report_dedup_cache, _last_user_report_sent_at
    content_stripped = content.strip()
    h = hashlib.md5(content_stripped.encode()).hexdigest()
    semantic_sig = _semantic_report_signature(content_stripped)
    now_ts = _time.time()
    if not event.payload.get("bypass_rate_limit"):
        min_interval = _report_min_interval_sec()
        if min_interval and _last_user_report_sent_at and now_ts - _last_user_report_sent_at < min_interval:
            logger.info(
                f"[REPORT] Rate-limited proactive report "
                f"({int(now_ts - _last_user_report_sent_at)}s < {min_interval}s): {content[:80]}..."
            )
            return
    stale = [k for k, v in _report_dedup_cache.items() if now_ts - v > 600]
    for k in stale:
        del _report_dedup_cache[k]
    if not event.payload.get("force_send") and h in _report_dedup_cache:
        logger.debug(f"[REPORT] 去重跳过重复推送: {content_stripped[:60]}...")
        return
    if _recent_report_seen(semantic_sig):
        logger.info(f"[REPORT] Skip semantically duplicate report: {content_stripped[:80]}...")
        return
    _report_dedup_cache[h] = now_ts

    logger.info(f"[REPORT] Sending: {content[:80]}...")

    if _push_callback is not None:
        try:
            ok = _push_callback(content)
            if ok is False:
                logger.warning(f"[REPORT] Callback did not send message ({len(content)} chars)")
            else:
                _last_user_report_sent_at = now_ts
                logger.info(f"[REPORT] Sent via callback ({len(content)} chars)")
        except Exception as e:
            logger.warning(f"[REPORT] Callback push failed: {e}")
    else:
        logger.info(f"[REPORT] No push callback registered, content dropped")

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
            await pool.put(MindEvent(
                type=EventType.CONTENT_DIGEST,
                priority=1,
                payload={
                    "content_id": item.get("id", ""),
                    "project": item.get("project") or active_project or "",
                },
                source=source,
            ))
            count += 1
    except Exception as exc:
        logger.debug(f"[CONTENT] open content enqueue failed: {exc}")
    return count


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
            from ..project_state import get_project_status
            if get_project_status(_workspace, active_name) in {"cooling_down", "waiting"}:
                _cap_project_waiting_delay(pool, active_name, _cooling_down_delay_sec())
        except Exception as exc:
            logger.debug(f"[CRON] cooling-down delay cap failed: {exc}")
        if not _has_project_event(pool, active_name, include_running=True):
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
        content = "本轮反思未形成可靠输出；已保留现有长期记忆，等待下一次周期反思。"
    path = write_reflection_artifacts(_workspace, content, kind="daily_reflection")
    append_strategy_memory(_workspace, content)
    logger.info(f"[REFLECTION] wrote {path}")
    project = str(event.payload.get("project") or "").strip()
    reason = str(event.payload.get("reason") or "").strip()
    if project and reason == "project_cooling_down":
        summary = _reflection_summary_for_report(content)
        if not summary:
            summary = "阶段完成后的反思已更新，我会继续低频检查是否有新突破口。"
        pool = await ensure_pool()
        await pool.put(MindEvent(
            type=EventType.REPORT,
            priority=5,
            payload={
                "content": f"「{project}」现在处于阶段完成后的反思观察。我刚做了一轮复盘：{summary}",
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
        content = "本轮跨项目思考未形成可靠输出；现有经验库中暂无足够迁移证据。"
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
        items = [item for item in items if item.get("id") == content_id] or items[:1]
    else:
        items = items[:1]
    if not items:
        logger.info(f"[CONTENT] no open content item, id={content_id}")
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
    content = ""
    try:
        if _adapter:
            content = (_adapter.chat(prompt, purpose="report") or "").strip()
    except Exception as exc:
        logger.warning(f"[CONTENT] digest LLM failed: {exc}")
    content = _sanitize_user_report_text(content)
    if re.search(r"<\s*tool_call\b|<\s*function=", content or "", re.I):
        logger.warning("[CONTENT] digest returned tool-call text; using safe fallback")
        content = ""
    if not content:
        if access_status in {"access_limited", "link_only", "metadata_only"}:
            content = (
                "1. 内容要点：当前只能看到链接或少量元数据，不能确认正文观点。\n"
                "2. 学习定位：先作为访问受限材料记录，不改动项目主线。\n"
                "3. 风险与不确定性：正文不可读，不能把标题或平台卡片当证据。\n"
                "4. 下一步最小动作：把限制写入学习日志，并从公开可访问来源寻找相近主题补充。"
            )
        else:
            content = (
                "1. 内容要点：内容已记录，但本轮没有形成可靠摘要。\n"
                "2. 学习定位：先作为普通学习材料保留，不改动项目主线。\n"
                "3. 风险与不确定性：缺少足够证据，不能作为事实结论。\n"
                "4. 下一步最小动作：后续只在明确相关时再转成项目 hypothesis。"
            )
    try:
        from ..research_memory import record_user_signal, record_episode
        signal_kind = "user_idea" if intent in {"project_instruction", "project_reference"} else "external_learning"
        signal_project = project if signal_kind == "user_idea" else ""
        record_user_signal(_workspace, signal_project, f"外部内容学习：{item.get('text','')}", kind=signal_kind)
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
    if project and bool(item.get("should_nudge_project", False)):
        pool = await ensure_pool()
        await pool.put(MindEvent(
            type=EventType.PROJECT,
            priority=4,
            payload={"title": project, "step": 0},
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
            from ..project_state import get_project_status
            if get_project_status(_workspace, active_name) in {"cooling_down", "waiting"}:
                _cap_project_waiting_delay(pool, active_name, _cooling_down_delay_sec())
        except Exception as exc:
            logger.debug(f"[WAKE_UP] cooling-down delay cap failed: {exc}")
        if not _has_project_event(pool, active_name, include_running=True):
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
