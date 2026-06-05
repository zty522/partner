"""Shared policy for user-visible fallback text."""

THINKING_NOTICE = "思考中......."
UNAVAILABLE_NOTICE = "当前实例或 LLM 暂时没有响应，请稍后再试。"

EVENT_VISIBLE_LABELS = {
    "direct_reply": "直接回复",
    "direct_task": "直接交付",
    "literature_review": "搜索文献",
    "data_analysis": "数据分析",
    "evidence_audit": "证据审计",
    "artifact_build": "构建产物",
    "pdf_report": "PDF报告",
    "project_think": "项目思考",
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


def prefix_event_notice(text: str, event_type: str, *, event_kind: str = "") -> str:
    body = (text or "").strip()
    if not body:
        return ""
    if body.startswith("【事件："):
        return body
    label = event_visible_label(event_type)
    kind = str(event_kind or "").strip()
    if kind and kind not in {event_type, label}:
        return f"【事件：{label}｜{kind}】\n{body}"
    return f"【事件：{label}】\n{body}"
