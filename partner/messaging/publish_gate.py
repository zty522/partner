"""LLM publish gate for user-visible lifecycle messages.

The rule-based ``should_publish`` filter decides *when a candidate may
exist*; this gate decides *whether to actually send it and what to say*,
grounded in the real chat history.  The LLM sees the recent conversation,
recent jobs and runtime status and answers three questions:

1. 是否已经向用户发过类似信息（重复检测）——不发，或只发增量；
2. 距离上次回复是否太久（>30 分钟且任务有进展）——主动补一条；
3. 是否回复太频繁（短时间多条）——收敛合并。

It also rewrites the candidate into plain user-facing Chinese.  The gate
fails open (send) so a broken model call never silently drops a message.
"""
from __future__ import annotations

import json
import os
from typing import Any

_GATE_PROMPT = """你负责决定一条发给项目负责人的消息该不该发、怎么发。消息必须基于真实事实，面向用户可读。

【当前任务】（用户要求，你汇报的内容必须围绕它，不得跑题）
{goal}

【聊天记录】（最近对话，含时间戳）
{chat}

【近期任务】
{recent_jobs}

【运行状态】
{status}

【候选消息】（由内部事件生成，可能包含机器词或重复内容）
{candidate}

【判断规则】
1. 相关性：候选消息必须服务于【当前任务】。若内容与用户要求无关（例如旧项目话术、验证流程细节），不要原样发；重写成与当前任务相关的表述；实在无关则 send=false。
2. 重复检测：聊天记录里是否已经发过类似的信息（同一件事/同一个结果）？
   - 已发过 → 不发（send=false），除非候选里有用户还不知道的新信息，此时只讲增量。
   - 特别地：如果聊天记录里已经发过"已收到/收到/正在调研/正在处理/还在进行"类确认，而候选消息仍只是同样的空确认（没有具体的新事实，如读了哪些资料、找到什么、测得什么结果），一律 send=false。空确认只允许发一次。
3. 太久没回复：用户最近问过进展或任务有进展，且距上次发送已超过 25 分钟，**并且候选消息里有具体的新事实**（读了哪几份资料/找到几个候选/测试结果/发现了什么）？
   - 是 → 补发一条，把具体进展讲清楚（send=true）。
   - 否（超过 25 分钟但候选仍无具体事实）→ send=false；不要用"还在推进/还没有结果"这类空话填补沉默。
4. 太频繁：短时间内（10 分钟内）已发过 2 条以上？
   - 是 → 不重复发；如果候选包含新信息，合并成一条最精简的。
5. 用户可读性：候选里的内部词（event/flow/job/结算/边界/决策/effect=0.0/证据清单/哈希绑定/轮次 等）要改写成大白话。
   内部标记必须清除或改写为普通人能懂的话，包括但不限于：
   - 方括号内部标记：[commitment]、[流内草稿·未经裁决背书]、[路径已隐藏]、[数据量已隐藏]、[进度] 等
   - 机器字段：settlement_class=...、improvement_over_基线结果=False、publish_eligible=...、
     publish_blockers=...、bet=...、expectations_met=...、next_round_goal=... 等键值对
   - 步骤计数：完成（25/32）、第 N 步、Round N 等
   改写成"做了什么、得到什么、下一步做什么"的自然中文，语气像助理向老板汇报。
6. 具体事实优先：进展消息要写清楚具体做了什么——读了哪几份资料（可列标题/主题）、找到了几个候选、测试结果如何、发现了什么——不要用"确定方案/整理资料/推进中"这类空话；没有具体事实可讲时如实说"目前在做××，还没有可汇报的结果"。
7. 真实性：只允许使用给定信息中的事实，禁止编造；读取外部内容失败时如实说明，不虚构内容。

【输出格式】严格 JSON，不要多余文字：
{{"send": true 或 false, "message": "改写后的消息（send=false 时可留空）", "reason": "一句话说明判断理由"}}"""


def _call_gate_llm(ctx: Any, prompt: str) -> dict:
    from partner.events._llm import call_model
    raw, _ = call_model(ctx, purpose="lifecycle_publish_gate", prompt=prompt)
    text = str(raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("gate llm returned no JSON")
    parsed = json.loads(text[start:end + 1])
    return parsed


def publish_gate(
    *,
    workspace,
    ctx,
    candidate: str,
    facts: dict[str, Any],
    rewrite_only: bool = False,
) -> dict[str, Any]:
    """Decide send/no-send and produce the final user-facing message.

    ``rewrite_only=True`` keeps the LLM grounding (chat history, duplicate
    awareness) but forces send=True and only rewrites the wording — used by the
    delivery Event where deduplication was already decided upstream.

    Fails open: any model/parse failure returns send=True with the original
    candidate so a lifecycle update is never silently dropped.
    """
    try:
        from partner.messaging.chat_context import chat_context, recent_jobs
        persona = str(getattr(ctx, "instance_id", "") or "")
        sender = str(getattr(ctx, "sender_id", "") or "")
        chat_workspace = getattr(ctx, "workspace", None) or workspace
        cc = chat_context(
            workspace, chat_workspace=chat_workspace,
            persona_hint=persona, sender_id=sender,
            request=str(facts.get("request") or "")[:2000],
        )
        chat = str(cc.get("recent_chat") or "(暂无聊天记录)")[-4000:]
        jobs = str(cc.get("recent_jobs") or "")[:1500]
        status = json.dumps(cc.get("runtime_status") or {}, ensure_ascii=False)[:1500]
        # Bind the publication to the task the user actually asked for: the
        # gate must never let stale project-cycle prose leak into the reply.
        contract = facts.get("intent_contract")
        goal = ""
        if isinstance(contract, dict):
            goal = str(contract.get("original_request") or "")[:600]
        if not goal:
            goal = str(facts.get("request") or getattr(ctx, "request", "") or "")[:600]
        prompt = _GATE_PROMPT.format(
            goal=goal or "(未提供任务原文，按候选消息内容判断)",
            chat=chat, recent_jobs=jobs, status=status, candidate=str(candidate or "")[:1500])
        if rewrite_only:
            prompt += (
                "\n【模式】当前为改写模式：你只负责把候选消息改写成面向用户的自然中文。"
                "send 恒为 true（发送与否由上层决定），message 必须非空。"
                "即使内容与聊天记录重复，也必须输出改写后的 message（重复与否不在此模式判断）。"
            )
        decision = _call_gate_llm(ctx, prompt)
        send = True if rewrite_only else bool(decision.get("send", True))
        message = str(decision.get("message") or "").strip()
        reason = str(decision.get("reason") or "")[:200]
        final = message if (send and message) else candidate
        return {
            "ok": True, "send": send, "message": final,
            "reason": reason, "gate": "llm",
        }
    except Exception as exc:  # noqa: BLE001 - fail open
        return {
            "ok": True, "send": True, "message": candidate,
            "reason": f"gate_fallback: {type(exc).__name__}",
            "gate": "fallback",
        }


def last_push_ts(workspace) -> float:
    """Seconds since the newest delivered outbound message for the persona."""
    try:
        root = os.path.join(str(workspace), "state", "application", "outbound")
        newest = 0.0
        for base, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith(".sent"):
                    mtime = os.path.getmtime(os.path.join(base, name))
                    if mtime > newest:
                        newest = mtime
        return newest
    except OSError:
        return 0.0


__all__ = ["publish_gate", "last_push_ts"]
