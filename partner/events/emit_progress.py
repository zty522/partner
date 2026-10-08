"""User-visible lifecycle message Events.

These handlers edit and review messages. Transport is a separate
``delivery.send_text`` Event, and the runtime records all three in the ledger.
"""
from __future__ import annotations

from typing import Any
import re

from partner.event_fabric.catalog import EventDefinition


_PHASE_LABELS = {
    "accepted": "已接收", "flow_planned": "已规划", "flow_started": "开始运行",
    "event_started": "开始", "event_completed": "完成", "event_failed": "未完成",
    "event_waiting": "等待", "flow_completed": "流程完成", "flow_failed": "流程未完成",
    "flow_resumed": "恢复运行",
}


def _clean(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"\b(?:evt|flow|job)_[a-f0-9]{8,}\b", "", text)
    return text[:limit].strip(" ：:，,")


def lifecycle_compose(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Turn one verified runtime transition into a concise Chinese update."""
    phase = str(params.get("lifecycle_phase") or "event_completed")
    flow_name = _clean(params.get("flow_name") or params.get("flow_type"), 60)
    event_name = _clean(params.get("event_description") or params.get("event_type")
                        or params.get("node_id"), 90)
    summary = _clean(params.get("event_summary") or params.get("summary"), 180)
    next_event = _clean(params.get("next_event_description"), 100)
    index, total = params.get("event_index"), params.get("event_total")
    position = f"（第 {index}/{total} 步）" if index and total else ""
    if phase == "accepted":
        # The first line is the user's own task label.  Taking a raw character
        # prefix from a long body can expose half a local path or half a word.
        request = _clean(str(params.get("request") or "").splitlines()[0], 70)
        message = f"已收到你的任务：{request}。" if request else "已收到你的任务。"
        if flow_name:
            message += f"将按“{flow_name}”流程执行，后续步骤会持续同步到网页和 QQ。"
    elif phase == "flow_planned":
        nodes = [_clean(x, 45) for x in params.get("planned_events") or []]
        nodes = [x for x in nodes if x]
        preview, omitted = " → ".join(nodes[:8]), max(0, len(nodes) - 8)
        message = f"已生成“{flow_name or '任务'}”运行计划，共 {len(nodes)} 个 Event"
        if preview:
            message += f"：{preview}"
        if omitted:
            message += f"，另有 {omitted} 步"
        message += "。"
    elif phase in {"flow_started", "flow_resumed"}:
        message = f"{_PHASE_LABELS[phase]} Flow“{flow_name}”。"
    elif phase in {"flow_completed", "flow_failed"}:
        facts=params.get('milestone_facts') if isinstance(params.get('milestone_facts'),dict) else {}
        if flow_name == 'project_cycle_round' and facts:
            number=facts.get('round_number') or '?'
            hypothesis=_clean(facts.get('hypothesis') or facts.get('round_goal'),110)
            result_text=('获得可核验证据' if facts.get('verified') is True else
                         '未获得完整可核验证据' if facts.get('verified') is False else '核验状态未知')
            route=_clean(facts.get('route'),30); reason=_clean(facts.get('reason'),100)
            next_goal=_clean(facts.get('next_hypothesis') or facts.get('next_round_goal'),100)
            action=_clean(facts.get('execution_summary'),120)
            error=_clean(facts.get('execution_error'),120)
            finding=_clean(facts.get('finding') or facts.get('information_gain'),120)
            artifacts=[str(v).rsplit('/',1)[-1] for v in facts.get('artifacts') or []]
            # A round decision may request active learning, but the learning Flow
            # has not run yet. Never relay an LLM explanation that describes a
            # future handoff as already retrieved, frozen, or consumed.
            if route == 'active_learning':
                reason = '当前知识缺口需要外部来源核对；是否形成可靠 handoff 以后续主动学习 Flow 的真实终态为准'
            route_text = {
                'active_learning': '补充外部资料学习', 'continue_project': '继续下一轮研究',
                'complete': '目标达成', 'stop': '本轮结束',
            }.get(route, str(route) or '')
            goal_text = _clean(hypothesis or round_goal, 80)
            result_text = {
                True: '本轮已形成可确认的结果', False: '本轮未形成可确认的结果',
                None: '本轮结果待进一步确认',
            }.get(facts.get('verified'), result_text)
            message = f'第 {number} 轮进展'
            if goal_text:
                message += f'：围绕“{goal_text}”'
            if action:
                message += f'，实际完成：{action}'
            if error:
                message += f'；执行受阻：{error}'
            elif finding:
                message += f'；取得认识：{finding}'
            message += f'。{result_text}'
            if route_text:
                message += f'，下一步：{route_text}'
            if reason:
                message += f'（{reason}）'
            if next_goal and route in {'continue_project','active_learning'}:
                message += f'：{next_goal}'
            message += '。'
        elif flow_name == 'active_learning' and facts:
            if phase == 'flow_failed':
                message = '主动学习未完成：没有形成可供下一项目轮消费的可靠 handoff。'
                if summary:
                    message += _clean(summary, 160) + '。'
            else:
                urls=facts.get('source_urls') or []
                ideas=facts.get('source_ideas') or []
                status=_clean(facts.get('learning_status'),40)
                if ideas:
                    parts=[]
                    for item in ideas[:3]:
                        title=_clean(item.get('title'),40) or '未命名资料'
                        core=_clean('；'.join(item.get('core_ideas') or []),90)
                        parts.append(f"《{title}》({_clean(item.get('url'),60)})——{core}" if core
                                     else f"《{title}》({_clean(item.get('url'),60)})")
                    message=f"主动学习完成：查阅了 {len(ideas)} 份外部资料。{('；'.join(parts))}。"
                    message+='从中提炼出学习成果映射，供下一轮研究参考。'
                else:
                    message=f"主动学习完成：{status or '已查阅外部资料并冻结学习成果'}"
                    if urls: message+=f"；来源：{_clean(urls[0],100)}"
                    message+='。学习成果供下一轮研究参考，实际效果需后续验证。'
        else:
            message = f"{_PHASE_LABELS[phase]}：{flow_name or '当前 Flow'}。"
            if summary:
                message += summary + "。"
    else:
        label = _PHASE_LABELS.get(phase, "进展")
        subject = event_name or _clean(params.get("node_id"), 60) or "当前 Event"
        message = f"{label}{position}：{subject}。"
        if summary and phase != "event_started":
            message += summary + "。"
        if next_event and phase in {"event_completed", "event_waiting", "event_failed"}:
            message += f"下一步：{next_event}。"
    message = re.sub(r"。+", "。", message).strip()
    if len(message) > 390:
        message = message[:389].rstrip('；，。：: ') + '。'
    return {"ok": bool(message), "status": "completed" if message else "failed",
            "message": message, "summary": message[:300],
            "semantic_output": {"lifecycle_phase": phase, "message": message}}


def lifecycle_critic(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on empty, internal, misleading, or unbounded updates."""
    previous = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    message = _clean(params.get("message") or previous.get("message"), 420)
    problems: list[str] = []
    if not message:
        problems.append("empty_message")
    if re.search(r"\b(?:sha256|production_effective|exit_code|params)\b", message, re.I):
        problems.append("internal_field")
    if len(message) > 400:
        problems.append("too_long")
    phase = str(params.get("lifecycle_phase") or "")
    if phase.endswith("failed") and not any(x in message for x in ("未完成", "失败", "等待")):
        problems.append("failure_hidden")
    accepted = not problems
    return {"ok": accepted, "status": "completed" if accepted else "failed",
            "accepted": accepted, "problems": problems, "message": message,
            "summary": "生命周期消息已通过发送前审查" if accepted else "生命周期消息未通过发送前审查",
            "semantic_output": {"accepted": accepted, "problems": problems, "message": message}}


def emit_progress(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Compatibility editor for callers from the retired side-band design."""
    translated = lifecycle_compose(ctx, {
        **params, "lifecycle_phase": params.get("lifecycle_phase") or "event_completed",
        "event_summary": (params.get("node_output") or {}).get("summary"),
        "event_description": params.get("event_description") or params.get("completed_node_id"),
    })
    return {**translated, "progress_text": translated.get("message", ""),
            "source_event": params.get("completed_node_id", "")}


DEFINITIONS = [
    EventDefinition("notification.lifecycle_compose", "notification",
                    "根据真实运行状态编辑用户可读的生命周期消息", lifecycle_compose),
    EventDefinition("notification.lifecycle_critic", "notification",
                    "发送前审查生命周期消息的真实性、可读性和内部字段", lifecycle_critic),
    EventDefinition("notification.emit_progress", "notification",
                    "兼容旧调用：仅编辑 Event 进度文本，不执行投递", emit_progress),
]
