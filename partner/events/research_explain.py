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
{{"learned_summary": "已学内容总结（300-600字）", "research_summary": "调研讲解（300-600字）", "recommendations": ["建议1", "建议2", "建议3"], "message": "最终发给用户的消息（200-400字：第一句直接给出结论或最重要的发现，然后核心要点2-4条（每条一行，不要长段落），最后给出改进建议方向与‘详细分析见PDF报告’；自然口语、直接可读、无标题式铺陈）"}}"""


def _sources_text(research: dict) -> str:
    """Extract real source titles/urls from the research node output for the PDF."""
    rows = []
    sem = research.get("semantic_output") if isinstance(research, dict) else {}
    if isinstance(sem, dict):
        for s in (sem.get("sources") or [])[:15]:
            if not isinstance(s, dict):
                continue
            title = str(s.get("title") or "")
            url = str(s.get("url") or "")
            if url:
                rows.append(f"- {title[:120] if title else url}\n  {url}")
        for f_ in (sem.get("retrieval_failures") or [])[:5]:
            if isinstance(f_, dict) and str(f_.get("error") or "").strip():
                rows.append(f"- 未获取：{str(f_.get('error') or '')[:120]}")
    if not rows:
        return "（本次调研未获取到外部来源，以下分析基于已有内部笔记与运行经验。）"
    return "\n".join(rows)


_REPORT_PROMPT = """你是 Partner 的“调研与改进建议报告”Event。把下方材料整理成一份**简洁、面向用户**的中文报告 Markdown。读者只看这份报告就能明白：调研了什么、查到了什么（具体资料）、对 Partner 意味着什么、建议怎么改。

【用户原始请求】
{request}

【调研讲解】（research_explain.synthesize 输出）
{synthesize}

【本次调研的真实来源】
{sources}

【硬规则】
1. 结构：`# 标题` → `## 核心结论`（3-5 条，每条一行）→ `## 调研内容`（读了哪些资料、每条资料讲了什么核心观点，自然叙述）→ `## 改进建议`（3-5 条，可落地、有依据）→ `## 局限与下一步`。
2. 全文 600-1000 汉字，纯文本 Markdown，禁止代码块、禁止表格以外的机器符号、禁止 LaTeX。
3. 真实性：只写上方材料中出现的内容；没读到的资料不得声称已读；来源缺失时明确写“本次未获取到外部来源”。
4. 用户化：禁止机器词（event/flow/job/结算/边界/证据清单/哈希/轮次编号/内部文件名）；来源用自然语言叙述（如“根据 arXiv 论文《…》”）。
5. 不生成图表占位、不生成附录、不生成证据索引。
【输出】直接输出 Markdown 正文，不要 JSON 包裹。"""


def report_pdf(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Lightweight Chinese PDF for research/explain flows: concise, user-facing,
    built from the synthesize output + real retrieved sources."""
    flow_outputs = params.get("flow_outputs") if isinstance(
        params.get("flow_outputs"), dict) else {}
    synth = _semantic(flow_outputs, "synthesize") or _pick(flow_outputs, "synthesize")
    research = _semantic(flow_outputs, "research") or _pick(flow_outputs, "research")
    request = str(params.get("request") or getattr(ctx, "request", "") or "")[:2500]
    learned = str(synth.get("learned_summary") or "").strip()
    research_text = str(synth.get("research_summary") or "").strip()
    recs = synth.get("recommendations") or []
    if isinstance(recs, str):
        recs = [recs]
    recs = [str(r).strip() for r in recs if str(r).strip()]
    synth_block = "\n\n".join([p for p in (learned, research_text, "建议：\n" + "\n".join(f"- {r}" for r in recs)) if p])
    prompt = _REPORT_PROMPT.format(
        request=request,
        synthesize=synth_block[:6000],
        sources=_sources_text(research)[:4000],
    )
    raw, usage = call_model(ctx, purpose="research_explain.report_pdf", prompt=prompt)
    body = str(raw or "").strip()
    if len(body) < 120:
        body = ("# 调研与改进建议报告\n\n## 核心结论\n- 本次调研已完成，但报告内容生成不完整。\n\n"
                "## 调研内容\n" + (research_text[:800] or "（未获取到外部来源。）") + "\n\n"
                "## 改进建议\n" + "\n".join(f"- {r}" for r in recs[:5]))
    working = Path(str(getattr(ctx, "working_dir", "") or getattr(ctx, "project_dir", "") or "."))
    working.mkdir(parents=True, exist_ok=True)
    title = str(params.get("chinese_filename") or "调研与改进建议报告").removesuffix(".md")
    path = working / f"{title}.md"
    path.write_text(body.strip() + "\n", encoding="utf-8")
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "content": body, "summary": "调研讲解 PDF 报告草稿已形成", "token_usage": usage}


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
    EventDefinition('research_explain.synthesize', 'research_explain', '综合已学内容与调研结果生成用户讲解与建议',
                    synthesize, execution_method='llm', timeout_seconds=300),
    EventDefinition('research_explain.report_pdf', 'research_explain',
                    '生成简洁面向用户的中文调研报告 Markdown（供 PDF 渲染）',
                    report_pdf, execution_method='llm', timeout_seconds=300),
]
