"""Shared policy for user-visible QQ Bot messages."""

import json, os, time, re

THINKING_NOTICE = "⏳ 正在处理..."
UNAVAILABLE_NOTICE = "当前实例或 LLM 暂时没有响应，请稍后再试。"

TEMPLATES = {
    # ── Progress (internal, not shown to user unless critical) ──
    "progress": "⏳ {current}/{total} {description}",
    "progress_done": "✅ {current}/{total} {description}",
    "plan_ready": "📋 已规划 {total} 步，开始执行",

    # ── Task acknowledgment (shown before plan) ──
    "task_ack": "📨 {title}\n🎯 {goal}\n📋 已规划 {total} 步",
    "task_ack_short": "📨 {title}\n📋 已规划 {total} 步",

    # ── Task result (visible to user) ──
    "task_complete": (
        "Partner ─ {task_title}\n"
        "📋 {description}\n"
        "🎯 {goal}\n"
        "📊 执行 {completed}/{total} 步\n"
        "✅ {result_summary}\n"
        "📎 {files}\n"
        "➡️ {next_step}"
    ),
    "task_failed": (
        "Partner ─ {task_title}\n"
        "📋 {description}\n"
        "🎯 {goal}\n"
        "📊 执行到 {failed_at}/{total} 步\n"
        "❌ {error}\n"
        "📎 {files}\n"
        "➡️ {next_step}"
    ),
    "task_empty": (
        "Partner ─ {task_title}\n"
        "📋 未生成有效产出\n"
        "❌ {reason}"
    ),

    # ── Phase summary ──
    "phase_summary": (
        "📊 第 {iteration}/{max_iterations} 轮\n"
        "{phase_detail}\n"
        "✅ {completed}/{total} 已完成"
    ),

    # ── Internal check ──
    "check_failed": "⚠️ 仍需补充：{missing_summary}",
    "reflect": "🔍 需要补齐：{missing_summary} → {next_focus}",
    "curiosity": "💡 补充计划 {total} 步：{focus}",
}

# Internal patterns stripped from user messages
_INTERNAL_PATTERNS = (
    (r"atomic_[a-z_]+", "[内部操作]"),
    (r"smart_llm_structured_action", "[智能分析]"),
    (r"task_instance\.json", "任务配置"),
    (r"_step_\d+\.result\.json", "[步骤结果]"),
    (r"\bstep_\d+\b", "步骤"),
)


def _template_clean(value: object, max_len: int = 240) -> str:
    text = str(value or "").strip()
    # Strip HTML <img> tags that may leak from web_capture results
    text = re.sub(r"<img\s+[^>]*/?>", "", text)
    text = re.sub(r"(?m)^@@.*$|^diff --git .*|^--- [ab]/.*|^\+\+\+ [ab]/.*", "", text)
    text = re.sub(r"[/\\][^\s，。；;]+(?:\.py|\.json|\.md|\.txt|\.pdf)(?::\d+)?", "[内部文件]", text)
    text = re.sub(r"_step_[A-Za-z0-9_.-]+\.result\.json", "[步骤结果]", text)
    text = re.sub(r"(?i)\s*Normalized model\s+.*?to\s+.*?for\s+.*?(?:\s|$)", "", text)
    text = re.sub(r"(?i)[\u26a0\U000026a0\ufe0f]\s*Normalized model\s+.*?to\s+.*?for\s+.*?(?:\s|$)", "", text)
    text = re.sub(r"\s*[\u26a0\ufe0f\U000026a0]\s*[\u250a\u2502]\s*", "", text)
    for pattern, replacement in _INTERNAL_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len].rstrip()


def send_template(template: str, **kwargs: object) -> str:
    raw = TEMPLATES.get(str(template or ""), str(template or ""))
    cleaned = {key: _template_clean(value, max_len=500 if key in {"files", "error", "result_summary"} else 240)
               for key, value in kwargs.items()}
    try:
        return raw.format(**cleaned).strip()
    except Exception:
        return raw.strip()


def format_task_result(*, ok: bool, task_title: str = "", description: str = "",
                       goal: str = "", completed: int = 0, total: int = 0,
                       result_summary: str = "", files: str = "",
                       next_step: str = "无", error: str = "",
                       failed_at: int = 0) -> str:
    """Generate a user-facing task result message."""
    # Clean up display noise
    for prefix in ("【任务指令】", "【任务】", "Task:"):
        task_title = task_title.replace(prefix, "").strip()
        description = description.replace(prefix, "").strip()
        goal = goal.replace(prefix, "").strip()
    task_title = re.sub(r"[，,。]\s*只做这一?件事[。.]?$", "", task_title).strip()
    description = re.sub(r"[，,。]\s*只做这一?件事[。.]?$", "", description).strip()
    goal = re.sub(r"[，,。]\s*只做这一?件事[。.]?$", "", goal).strip()
    if not task_title:
        task_title = "任务"
    if not description:
        description = task_title
    if not goal:
        goal = "-"
    if not result_summary and ok:
        result_summary = "完成"
    if not files:
        files = "无"

    if ok:
        if completed == 0 and total == 0:
            return send_template("task_empty", task_title=task_title, reason=error or "无产出")
        return send_template("task_complete",
                            task_title=task_title, description=description,
                            goal=goal, completed=completed, total=max(total, 1),
                            result_summary=result_summary, files=files,
                            next_step=next_step)
    else:
        return send_template("task_failed",
                            task_title=task_title, description=description,
                            goal=goal, failed_at=failed_at or completed, total=max(total, 1),
                            error=error, files=files, next_step=next_step)


EVENT_VISIBLE_LABELS = {
    "direct_reply": "直接回复", "direct_task": "直接交付",
    "literature_review": "搜索文献", "data_fetch": "数据获取",
    "data_analysis": "数据分析", "visualization": "可视化",
    "evidence_audit": "证据审计", "artifact_build": "构建产物",
    "pdf_report": "PDF报告", "email_delivery": "邮件交付",
    "web_search": "网络搜索", "web_capture": "网页捕获",
    "project_think": "项目思考", "objective_review": "目标对齐",
    "curiosity_explore": "好奇探索", "habit_update": "经验成长",
    "ollama_status": "Ollama状态", "project": "项目推进",
    "content_digest": "内容消化", "reflection": "反思整理",
    "memory_consolidate": "记忆整理", "report": "主动汇报",
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
    elif (kind.startswith("selector_") or kind.startswith("routing_prefer_")
          or kind.startswith("failure_")
          or kind in {"action_failure_objective_review", "one_shot_complete"}):
        kind = ""
    return body
