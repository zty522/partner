"""research_explain Events: summarize learned content + research a direction +
produce a plain-language user explanation + improvement recommendations.

This flow family is the answer to "总结/讲解/调研/建议" requests.  It is a
bounded single pass: it consumes what was already learned/notes, optionally
fetches external sources, and has one LLM synthesize the explanation.  It
never enters the project execution loop and never schedules a continuation.
"""
from __future__ import annotations

from typing import Any
import json

from partner.event_fabric.catalog import EventDefinition
from ._llm import call_model, json_object


def _semantic(flow_outputs: dict, node: str) -> dict:
    row = flow_outputs.get(node)
    return (row.get("semantic_output") if isinstance(row, dict)
            and isinstance(row.get("semantic_output"), dict) else {})


def _pick(flow_outputs: dict, node: str) -> dict:
    row = flow_outputs.get(node)
    return row if isinstance(row, dict) else {}


_PROMPT = """你是 Partner 的“学习总结与调研讲解”Event。用户要求：总结已经学到的内容、调研某个方向、并给出改进建议。你必须基于下方真实材料，产出面向用户的讲解。

【用户原始请求】
{request}

【已学内容/笔记召回】（memory.context_recall 输出，真实只读）
{recall}

【外部调研结果】（active_learning.source_retrieve 输出：检索到的来源与抓取正文摘要）
{research}

【规则】
1. 真实性：所有讲解必须基于上方材料中真实出现的内容；未读到的资料不得声称已读。来源缺失时如实说明“暂未找到/证据不足”。
2. 讲解结构（面向用户，非机器报告）：
   - 已学内容总结：读了/学了什么（列具体资料或主题）、核心观点是什么、有哪些关键方法或结论；
   - 调研讲解：调研了哪个方向、找到了什么（列具体来源标题）、这些来源讲了什么、与 Partner 的相关性；
   - 改进建议：针对 Partner 自进化/主动学习/迭代/消息/报告，给出具体、可落地、有依据的建议；建议必须关联上述材料中的观点，不能空谈。
3. 语言：全部用通俗中文，像助理向负责人汇报；禁止机器词（event/flow/job/结算/边界/证据清单/哈希/轮次编号等）。
4. 不编造：对不确定的内容明确说“尚不确定/需进一步核实”。

【输出格式】严格 JSON：
{{"learned_summary": "已学内容总结（300-600字）", "research_summary": "调研讲解（300-600字）", "recommendations": ["建议1", "建议2", "建议3"], "message": "最终发给用户的消息（400-800字，包含以上三部分精华，自然连贯、直接可读）"}}"""


def synthesize(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    flow_outputs = params.get("flow_outputs") if isinstance(
        params.get("flow_outputs"), dict) else {}
    recall = _semantic(flow_outputs, "recall") or _pick(flow_outputs, "recall")
    research = _semantic(flow_outputs, "research") or _pick(flow_outputs, "research")
    request = str(params.get("request") or getattr(ctx, "request", "") or "")[:4000]
    prompt = _PROMPT.format(
        request=request,
        recall=json.dumps(recall, ensure_ascii=False)[:9000],
        research=json.dumps(research, ensure_ascii=False)[:9000],
    )
    raw, usage = call_model(ctx, purpose="research_explain.synthesize", prompt=prompt)
    value = json_object(raw)
    learned = str(value.get("learned_summary") or "").strip()
    research_text = str(value.get("research_summary") or "").strip()
    recs = value.get("recommendations") or []
    if isinstance(recs, str):
        recs = [recs]
    recs = [str(r).strip() for r in recs if str(r).strip()]
    message = str(value.get("message") or "").strip()
    if not message:
        parts = [p for p in (learned, research_text) if p]
        message = "\n\n".join(parts)[:1500]
    return {
        "ok": True, "status": "completed",
        "semantic_output": {
            "learned_summary": learned, "research_summary": research_text,
            "recommendations": recs, "message": message,
        },
        "message": message, "summary": "学习总结与调研讲解已完成",
        "token_usage": usage, "model_output": raw, "model_calls": 1,
    }


DEFINITIONS = [
    EventDefinition('research_explain.synthesize', 'knowledge', '综合已学内容与调研结果生成用户讲解与建议',
                    synthesize, execution_method='llm', timeout_seconds=300),
]
