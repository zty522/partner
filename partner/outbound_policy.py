"""Shared policy for user-visible fallback text."""

import json
import os
import time
import re

THINKING_NOTICE = "[进度] 正在思考..."
UNAVAILABLE_NOTICE = "当前实例或 LLM 暂时没有响应，请稍后再试。"

TEMPLATES = {
    "progress": "[进度] 正在 {current}/{total}：{description}",
    "progress_done": "[进度] 已完成 {current}/{total}：{description}{summary}",
    "parallel": "[进度] 将并行执行：{items}",
    "plan_ready": "[进度] 已设计 {total} 步，即将开始执行。",
    "iteration_start": "[进度] 第 {iteration} 轮开始：{goal}",
    "iteration_end": "[进度] 第 {iteration} 轮完成，仍需补充：{missing_summary}",
    "check_passed": "[完成] 当前产物已满足交付要求。",
    "check_failed": "[进度] 仍需补充：{missing_summary}",
    "reflect": "[进度] 需要补齐：{missing_summary}。下一步：{next_focus}",
    "curiosity": "[进度] 已生成补充计划：{total} 步。方向：{focus}",
    "delivery_success": "[完成] 任务完成，报告已保存：{file_path}",
    "delivery_failure": "[错误] 任务失败：{reason}",
    # ── Phase-level summary (compact, human-readable) ────────────────
    "phase_summary": (
        "═══ 阶段概览 ═══\n"
        "轮次：第 {iteration}/{max_iterations} 轮\n"
        "阶段：{phase}  {phase_detail}\n"
        "进度：{completed}/{total} 步骤完成\n"
        "最近发现：{latest_findings}\n"
        "当前缺口：{gaps}\n"
        "下一步：{next_step}"
    ),
    "harness_plan": (
        "═══ 计划 ═══\n"
        "目标：{goal}\n"
        "步骤数：{total}\n"
        "关键步骤：{key_steps}\n"
        "期望产物：{expected}"
    ),
    "harness_check": (
        "═══ 检查 ═══\n"
        "结果：{verdict}\n"
        "已有：{found_summary}\n"
        "缺失：{missing_summary}\n"
        "下一轮重点：{next_focus}"
    ),
}

# Internal patterns that must never appear in user-facing messages
_INTERNAL_PATTERNS = (
    (r"atomic_[a-z_]+", "[内部操作]"),
    (r"smart_llm_structured_action", "[智能分析]"),
    (r"task_instance\.json", "任务配置"),
    (r"_step_\d+\.result\.json", "[步骤结果]"),
    (r"_step_[A-Za-z0-9_.-]+\.result\.json", "[步骤结果]"),
    (r"\bstep_\d+\b", "步骤"),
)


def _template_clean(value: object, max_len: int = 240) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?m)^@@.*$|^diff --git .*|^--- [ab]/.*|^\+{3} [ab]/.*", "", text)
    text = re.sub(r"[/\\][^\s，。；;]+(?:\.py|\.json|\.md|\.txt|\.pdf)(?::\d+)?", "[内部文件]", text)
    text = re.sub(r"_step_[A-Za-z0-9_.-]+\.result\.json", "[步骤结果]", text)
    # Strip Hermes model normalization warning (anywhere in the text, any encoding)
    text = re.sub(r"(?i)\s*Normalized model\s+.*?to\s+.*?for\s+.*?(?:\s|$)", "", text)
    text = re.sub(r"(?i)[\u26a0\U000026a0\ufe0f]\s*Normalized model\s+.*?to\s+.*?for\s+.*?(?:\s|$)", "", text)
    # Clean up leftover emoji/separator artifacts from stripped warnings
    text = re.sub(r"\s*[\u26a0\ufe0f\U000026a0]\s*[\u250a\u2502]\s*", "", text)
    text = re.sub(r"\s*[\u26a0\ufe0f\U000026a0]\s*(?:Normalized|┊|∥)\s*", "", text)
    # Remove bare ⚠ that's not part of actual content (followed by ASCII/common chars)
    text = re.sub(r"[\u26a0\ufe0f\U000026a0](?=\s*[a-zA-Z0-9_\-\.\,\:\;\/\\\(\)\[\]\{\}])", "", text)
    # Filter out internal event/pattern names from user-visible text
    for pattern, replacement in _INTERNAL_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len].rstrip()


def send_template(template: str, **kwargs: object) -> str:
    raw = TEMPLATES.get(str(template or ""), str(template or ""))
    cleaned = {key: _template_clean(value, max_len=500 if key in {"items", "missing_summary", "focus"} else 240) for key, value in kwargs.items()}
    try:
        return raw.format(**cleaned).strip()
    except Exception:
        return raw.strip()

EVENT_VISIBLE_LABELS = {
    "direct_reply": "直接回复",
    "direct_task": "直接交付",
    "literature_review": "搜索文献",
    "data_fetch": "数据获取",
    "data_analysis": "数据分析",
    "visualization": "可视化",
    "evidence_audit": "证据审计",
    "artifact_build": "构建产物",
    "pdf_report": "PDF报告",
    "email_delivery": "邮件交付",
    "web_search": "网络搜索",
    "web_capture": "网页捕获",
    "project_think": "项目思考",
    "objective_review": "目标对齐",
    "curiosity_explore": "好奇探索",
    "habit_update": "经验成长",
    "ollama_status": "Ollama状态",
    "project": "项目推进",
    "content_digest": "内容消化",
    "reflection": "反思整理",
    "memory_consolidate": "记忆整理",
    "report": "主动汇报",
    "stop_project": "结束执行",
}


def event_visible_label(event_type: str) -> str:
    key = str(event_type or "").strip()
    return EVENT_VISIBLE_LABELS.get(key, key or "事件")


def _last_agent_run(workspace: str, max_age_sec: int = 3600) -> dict:
    if not workspace:
        return {}
    path = os.path.join(workspace, "state", "logs", "agent_runs.jsonl")
    try:
        if not os.path.exists(path):
            return {}
        if max_age_sec > 0 and time.time() - os.path.getmtime(path) > max_age_sec:
            return {}
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536), os.SEEK_SET)
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                return row
    except Exception:
        return {}
    return {}


def _configured_backend(workspace: str) -> str:
    if not workspace:
        return ""
    candidates = [
        os.path.join(workspace, "config", "partner_config.json"),
        os.path.join(workspace, "partner_config.json"),
    ]
    for path in candidates:
        try:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
            backend = str(agent.get("backend") or data.get("backend") or "").strip().lower()
            if backend:
                return backend
        except Exception:
            continue
    return ""


def llm_source_label(workspace: str) -> str:
    """Return the user-visible agent/model source of the most recent real LLM call."""
    row = _last_agent_run(workspace)
    backend = str(row.get("backend") or "").strip().lower() or _configured_backend(workspace)
    provider = str(row.get("provider") or "").strip().lower()
    model = str(row.get("model") or "").strip().lower()
    if "ollama" in backend:
        return "Ollama"
    if provider == "custom" and any(name in model for name in ("qwen", "llama", "mistral", "gemma", "deepseek")):
        return "Ollama"
    if backend == "hermes":
        return "Hermes"
    if backend == "openclaw":
        return "OpenClaw"
    if backend == "codex":
        return "Codex"
    if backend in {"direct", "direct_reply"}:
        return "Partner"
    if backend:
        return backend
    return "Agent"


def _model_prefix(workspace: str) -> str:
    return llm_source_label(workspace) if workspace else ""


def _clean_header_part(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.startswith("routing_prefer_"):
        return ""
    raw = raw.replace("事件：", "").replace("模型：", "").replace("内容", "")
    raw = raw.replace("｜", "_")
    return raw[:80]


def prefix_event_notice(text: str, event_type: str, *, event_kind: str = "", workspace: str = "") -> str:
    body = (text or "").strip()
    if not body:
        return ""
    model = _model_prefix(workspace)
    # Normalize any existing Partner headers before adding the current one.
    # Reports can pass through both _enqueue_visible_report and _handle_report;
    # stripping leading headers here makes prefixing idempotent and prevents
    # duplicated lines such as 【Hermes｜x｜y】\n【Hermes｜x｜y】.
    while body.startswith("【") and "】" in body.splitlines()[0]:
        body = body.partition("】")[2].lstrip()
    if not body:
        return ""
    if body.startswith("【模型："):
        first, sep, rest = body.partition("】")
        first = first.replace("模型：", "").replace("事件：", "").replace("｜内容", "")
        return first + sep + rest
    if body.startswith("【事件："):
        first, sep, rest = body.partition("】")
        first = first.replace("事件：", "").replace("｜内容", "")
        body = first + sep + rest
        if not model:
            return body
        return body.replace("【", f"【{model}｜", 1)
    label = str(event_type or "").strip() or event_visible_label(event_type)
    kind = str(event_kind or "").strip()
    if str(event_type or "") in {"direct_reply", "interaction_reply"}:
        kind = "direct"
    elif (
        kind.startswith("selector_")
        or kind.startswith("routing_prefer_")
        or kind.startswith("failure_")
        or kind in {"action_failure_objective_review", "one_shot_complete"}
    ):
        kind = ""
    project = _clean_header_part(kind)
    event_head = _clean_header_part(label) or event_visible_label(event_type)
    return body
