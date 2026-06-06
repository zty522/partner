"""Shared policy for user-visible fallback text."""

import json
import os
import time

THINKING_NOTICE = "思考中......."
UNAVAILABLE_NOTICE = "当前实例或 LLM 暂时没有响应，请稍后再试。"

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
    path = os.path.join(workspace, "logs", "agent_runs.jsonl")
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


def llm_source_label(workspace: str) -> str:
    """Return the user-visible source of the most recent real LLM call."""
    row = _last_agent_run(workspace)
    backend = str(row.get("backend") or "").strip().lower()
    provider = str(row.get("provider") or "").strip().lower()
    model = str(row.get("model") or "").strip().lower()
    if "ollama" in backend:
        return "Ollama"
    if provider == "custom" and any(name in model for name in ("qwen", "llama", "mistral", "gemma", "deepseek")):
        return "Ollama"
    return "API"


def _model_prefix(workspace: str) -> str:
    return f"模型：{llm_source_label(workspace)}" if workspace else ""


def prefix_event_notice(text: str, event_type: str, *, event_kind: str = "", workspace: str = "") -> str:
    body = (text or "").strip()
    if not body:
        return ""
    model = _model_prefix(workspace)
    if body.startswith("【模型："):
        return body
    if body.startswith("【事件："):
        if not model:
            return body
        return body.replace("【事件：", f"【{model}｜事件：", 1)
    label = event_visible_label(event_type)
    kind = str(event_kind or "").strip()
    if kind and kind not in {event_type, label}:
        event_head = f"事件：{label}｜{kind}"
    else:
        event_head = f"事件：{label}"
    if model:
        return f"【{model}｜{event_head}】\n{body}"
    return f"【{event_head}】\n{body}"
