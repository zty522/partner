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


# User-facing rewrite of internal execution summaries: strip artifact paths,
# byte counts and internal jargon so round messages read like plain progress
# updates rather than developer logs.
def _userify(text, limit=140):
    text = str(text or '')
    # Remove artifact-path blocks entirely instead of replacing them with the
    # empty phrase "已完成相关数据文件记录": that phrase leaked into round
    # messages (repeated 3x) and told the user nothing about what was made.
    text = re.sub(r'【业务产物】[^【]*?(?:json|md|txt|py|pdf)?\s*\[bytes=\d+\]', ' ', text)
    text = re.sub(r'【执行动作】', '本轮动作：', text)
    text = re.sub(r'【真实发现】', '发现：', text)
    text = re.sub(r'【未解决】', '未解决：', text)
    text = re.sub(r'/mnt/[^ ]+|E:\\[^ ]+', '', text)
    text = re.sub(r'\[bytes=\d+\]', '', text)
    text = re.sub(r'sha256|SHA256|physical hash|物理哈希', '内容指纹', text, flags=re.I)
    text = re.sub(r'handoff|Handoff|HANDOFF', '交接记录', text)
    text = re.sub(r'转移映射', '学习成果', text)
    text = re.sub(r'机制性空转|空转', '无效重复', text)
    text = re.sub(r'准入', '允许范围', text)
    text = re.sub(r'知识注入', '外部知识借鉴', text)
    text = re.sub(r'可信下载器', '可靠下载组件', text)
    text = re.sub(r'consumed|consume|消费', '使用', text, flags=re.I)
    text = re.sub(r'benchmark|Benchmark', '基准测试', text)
    text = re.sub(r'证据链', '项目记录', text)
    text = re.sub(r'结算|裁决|判定', '确定', text)
    text = re.sub(r'终态', '最终状态', text)
    text = re.sub(r'里程碑', '阶段', text)
    text = re.sub(r'项目周期', '项目流程', text)
    text = re.sub(r'子Flow|子流程|子流程', '子流程', text)
    text = re.sub(r'基线结果|baseline', '初始结果', text)
    text = re.sub(r'\bFlow\b|\bEvent\b|flow_|event_', '', text)
    # Strip only filename-like tokens (snake_case with an underscore, or
    # alphanumeric names with a file extension).  Proper nouns such as
    # arXiv / DYNOSAUR / PDF / QQ must survive user-facing messages.
    text = re.sub(r'`?[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]*`?(?:\.[a-z]{2,4})?', '', text)
    text = re.sub(r'`?[A-Za-z][A-Za-z0-9_]*\.[a-z]{2,4}`?', '', text)
    text = re.sub(r'\b[\w-]+\.(?:md|py|json|txt|pdf|log)\b', '', text)
    text = re.sub(r'累计动作签名\([0-9a-f]{8,}\)', '', text)
    text = re.sub(r'无失败签名|失败签名[:：]?无', '未发现失败原因', text)
    text = re.sub(r'(?:verifier|allowlist)\s*[：:]?[^，。；]*', '', text, flags=re.I)
    text = re.sub(r'反思证据被限制[^。；]*', '', text)
    text = re.sub(r'ADR\s*\d+|Expected\s+Effect[^，。；]*', '内部机制文档', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return _clean(text, limit)


def lifecycle_compose(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Turn one verified runtime transition into a concise Chinese update."""
    phase = str(params.get("lifecycle_phase") or "event_completed")
    flow_name = _clean(params.get("flow_name") or params.get("flow_type"), 60)
    event_name = _userify(params.get("event_description") or params.get("event_type")
                           or params.get("node_id"), 90)
    summary = _userify(params.get("event_summary") or params.get("summary"), 180)
    next_event = _userify(params.get("next_event_description"), 100)
    index, total = params.get("event_index"), params.get("event_total")
    position = ""
    if phase == "accepted":
        # Keep the user's own task label whole: the request may be multi-line
        # and the intent label is its first segment, not a raw first line
        # that can cut mid-word.
        request = _clean(str(params.get("request") or "").splitlines()[0], 150)
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
            hypothesis=_userify(facts.get('hypothesis') or facts.get('round_goal'), 70)
            result_text=('获得可核验证据' if facts.get('verified') is True else
                         '未获得完整可核验证据' if facts.get('verified') is False else '核验状态未知')
            route=_clean(facts.get('route'),30); reason=_userify(facts.get('reason'),60)
            next_goal=_userify(facts.get('next_hypothesis') or facts.get('next_round_goal'),60)
            action=_userify(facts.get('execution_summary'))
            error=_userify(facts.get('execution_error'), 100)
            finding=_userify(facts.get('finding') or facts.get('information_gain'))
            artifacts=[str(v).rsplit('/',1)[-1] for v in facts.get('artifacts') or []]
            # A round decision may request active learning, but the learning Flow
            # has not run yet. Never relay an LLM explanation that describes a
            # future handoff as already retrieved, frozen, or consumed.
            if route == 'active_learning':
                reason = '当前知识缺口需要外部来源核对；是否形成可靠的学习成果，以后续主动学习流程的真实结果为准'
            route_text = {
                'active_learning': '补充外部资料学习', 'continue_project': '继续下一轮研究',
                'complete': '目标达成', 'stop': '本轮结束',
            }.get(route, str(route) or '')
            goal_text = _userify(hypothesis or round_goal, 70)
            result_text = {
                True: '本轮已形成可确认的结果', False: '本轮未形成可确认的结果',
                None: '本轮结果待进一步确认',
            }.get(facts.get('verified'), result_text)
            message = None
            try:
                import json as _json
                from ._llm import call_model
                facts_view = {
                    'round': number, 'goal': hypothesis or round_goal,
                    'action': facts.get('execution_summary'),
                    'finding': facts.get('finding') or facts.get('information_gain'),
                    'blocked': facts.get('execution_error'),
                    'artifacts': [str(a).rsplit('/', 1)[-1] for a in (facts.get('artifacts') or [])][:5],
                    'verified': facts.get('verified'), 'next': route,
                }
                _raw, _ = call_model(ctx, purpose='round_progress_user_message', prompt=(
                    '把一次项目轮次的执行记录改写成发给项目负责人的中文进展消息（3句以内）：'
                    '先说本轮围绕什么目标做了什么；若查阅了资料，说明看了哪些来源、它们讲什么；'
                    '再说本轮形成了什么结论或发现了什么；最后说当前卡在哪、下一步准备做什么。'
                    '只允许使用给定记录中的事实，禁止编造。'
                    '禁止出现：文件路径与文件名、哈希/签名、allowlist/verifier/审计、函数名、'
                    '“已完成相关数据文件记录”等空话、“累计动作签名”等机制术语、内部文档代号。'
                    '消息读起来是给用户看的进展，不是开发日志。\n\n轮次记录：'
                    + _json.dumps(facts_view, ensure_ascii=False)))
                _narr = _userify(_raw, 280)
                if _narr:
                    message = f'第 {number} 轮进展：{_narr}'
            except Exception:
                message = None
            if not message:
                message = f'第 {number} 轮进展'
                if goal_text:
                    message += f'：围绕“{goal_text}”'
            if action and not re.search(r'已完成相关数据文件记录|数据文件记录', action):
                message += f'，实际完成：{action}'
            if error:
                message += f'；执行受阻：{error}'
            elif finding and not re.search(r'已完成相关数据文件记录|数据文件记录', finding):
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
                    shown = ideas[:3]
                    parts=[]
                    for item in shown:
                        title=_clean(item.get('title'),40) or '未命名资料'
                        core=_clean('；'.join(item.get('core_ideas') or []),90)
                        parts.append(f"《{title}》({_clean(item.get('url'),60)})——{core}" if core
                                     else f"《{title}》({_clean(item.get('url'),60)})")
                    tail = f"；另有 {len(ideas)-len(shown)} 份" if len(ideas) > len(shown) else ''
                    message=f"主动学习完成：查阅了 {len(ideas)} 份外部资料。{('；'.join(parts))}{tail}。"
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
    # Pure internal-status updates are noise for the user: ACK/token/record
    # writes/audit boundaries/empty flow-done lines carry no project meaning.
    _status_noise = ("ack", "回执", "token", "汇总", "写入项目记录",
                     "审计", "verifier", "allowlist", "语义蕴含边界",
                     "已取得本次渠道", "等待本次真实渠道", "流程完成：",
                     "最终状态确定写入", "已记录；全部")
    if (phase in {"event_completed", "flow_completed", "flow_failed"}
            and any(x in message.lower() for x in _status_noise)):
        problems.append("internal_status_noise")
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
