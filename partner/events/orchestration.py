"""Soft-orchestration Events.

The shared Event pool is the only material an orchestrator may use.  A flow
starts from a static baseline definition; at runtime the orchestrator LLM
decides whether to keep the remaining graph, adjust its suffix, switch to
another registered flow, or redesign a new linear flow from whitelisted
events.  Every decision is deterministic materialisation (build_from_sequence
+ registry-style validation) and is recorded in definition_history /
orchestration_history.
"""
from __future__ import annotations

from typing import Any
import json

from partner.event_fabric.catalog import EventDefinition
from ._llm import call_model, json_object


def _whitelist(ctx: Any) -> list[dict[str, str]]:
    """Event pool whitelist = registered event types + one-line descriptions."""
    catalog = getattr(ctx, "catalog", None)
    if catalog is None:
        return []
    rows = []
    try:
        names = catalog.names() if hasattr(catalog, "names") else []
        for name in names:
            item = catalog.get(name)
            if item is None:
                continue
            rows.append({"event_type": str(getattr(item, "name", "") or ""),
                         "description": str(getattr(item, "description", "") or "")[:120]})
    except Exception:
        return []
    return rows


def orchestration_assess(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """LLM: judge whether the running flow needs adjustment or redesign.

    Inputs (params): goal / intent_contract, current flow nodes + completion
    status, finished node semantic summaries, whitelist, constraints.
    Output (semantic_output): {action, reason, proposed_sequence?, target_flow?,
    route_note} where action in keep | adjust | switch | redesign.
    """
    flow_name = str(params.get("flow_name") or "")
    flow_version = str(params.get("flow_version") or "")
    goal = str(params.get("goal") or "")
    contract = params.get("intent_contract") or {}
    if isinstance(contract, dict):
        goal = goal or str(contract.get("goal") or contract.get("original_request") or "")[:2000]
        contract_exec = contract.get("execution_constraints") or {}
    else:
        contract_exec = {}
    nodes = params.get("nodes") or []
    finished = params.get("finished_summaries") or {}
    whitelist = params.get("whitelist") or _whitelist(ctx)
    if not whitelist:
        return {"ok": False, "status": "failed",
                "error": "orchestration.assess: event pool whitelist unavailable",
                "semantic_output": {"action": "keep", "reason": "whitelist unavailable"}}
    whitelist_types = [str(row["event_type"]) for row in whitelist]
    node_rows = []
    for node in nodes:
        node_rows.append({
            "node_id": str(node.get("node_id") or ""),
            "event_type": str(node.get("event_type") or ""),
            "status": str(node.get("status") or "pending"),
            "summary": str(node.get("summary") or "")[:160],
        })
    finished_rows = []
    for node_id, row in (finished or {}).items():
        if isinstance(row, dict):
            finished_rows.append({"node_id": str(node_id),
                                  "status": str(row.get("status") or ""),
                                  "summary": str(row.get("summary") or "")[:200]})
    constraints_text = ""
    if isinstance(contract_exec, dict):
        keep = ("user_visible", "user_readable", "max_rounds", "report_policy",
                "stop_when", "budget", "evolution_apply")
        constraints_text = json.dumps(
            {k: v for k, v in contract_exec.items() if k in keep and v is not None},
            ensure_ascii=False)[:1200]
    prompt = (
        "你是 Partner 的运行时软编排器。当前 flow 是静态基线，你可以决定是否在运行时调整。"
        "你只能从下面的共享事件池白名单里选择事件（积木），按接口契约拼接；"
        "不允许编造不存在的 event_type。\n"
        f"运行目标（goal）：{goal}\n"
        f"当前 flow：{flow_name}@{flow_version}\n"
        f"执行约束：{constraints_text or '（无）'}\n"
        "当前图节点与状态：\n"
        + json.dumps(node_rows[:40], ensure_ascii=False)
        + "\n已完成节点摘要：\n"
        + json.dumps(finished_rows[:30], ensure_ascii=False)
        + "\n事件池白名单（event_type: description）：\n"
        + json.dumps(whitelist[:120], ensure_ascii=False)
        + "\n\n决策规则：\n"
        "1. keep：当前剩余图足以达成目标 → action=keep。\n"
        "2. adjust：剩余图有缺陷（缺关键事件、顺序不合理、某事件明显不需要）→ action=adjust，"
        "给出 proposed_sequence（从白名单选，线性顺序，从当前未完成节点开始的新完整序列，包含必要收尾）。\n"
        "3. switch：另一个已注册 flow 更合适 → action=switch，给 target_flow（必须是已注册 flow 名，如 project_cycle）。\n"
        "4. redesign：需要全新组合 → action=redesign，给 proposed_sequence（同 adjust）。\n"
        "约束：proposed_sequence 只含白名单 event_type；必须包含足以让运行可结算、可交付用户消息的收尾事件"
        "（如 delivery.send_text）；不得包含已完成的事件（已完成事件不可替换）。\n"
        "只输出一个 JSON 对象："
        '{"action": "keep|adjust|switch|redesign", "reason": "一句话理由", '
        '"proposed_sequence": ["event.type", ...], "target_flow": "flow_name_or_empty", '
        '"route_note": "对剩余执行的说明"}\n'
    )
    try:
        raw, _usage = call_model(ctx, purpose="orchestration_assess", prompt=prompt)
    except Exception as exc:
        return {"ok": False, "status": "failed",
                "error": f"orchestration.assess: {type(exc).__name__}: {exc}",
                "semantic_output": {"action": "keep", "reason": "assess failed, keep baseline"}}
    decision = {}
    try:
        decision = json_object(raw)
        if not isinstance(decision, dict):
            raise ValueError("non-dict decision")
    except Exception:
        decision = {"action": "keep", "reason": "unparseable assess output"}
    action = str(decision.get("action") or "keep")
    if action not in {"keep", "adjust", "switch", "redesign"}:
        action = "keep"
    proposed = [str(x) for x in (decision.get("proposed_sequence") or [])]
    proposed = [x for x in proposed if x in whitelist_types]
    if action in {"adjust", "redesign"} and not proposed:
        action = "keep"
        decision["reason"] = (str(decision.get("reason") or "") + "（序列为空，保持基线）")
    target_flow = str(decision.get("target_flow") or "")
    semantic = {
        "action": action,
        "reason": str(decision.get("reason") or "")[:400],
        "proposed_sequence": proposed,
        "target_flow": target_flow,
        "route_note": str(decision.get("route_note") or "")[:400],
        "whitelist_count": len(whitelist_types),
    }
    return {"ok": True, "status": "completed", "summary": f"orchestration: {action}",
            "semantic_output": semantic}


DEFINITIONS = [
    EventDefinition("orchestration.assess", "orchestration",
                    "运行时评估当前 flow 并决定 keep/adjust/switch/redesign",
                    orchestration_assess, execution_method="llm"),
]
