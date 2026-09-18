"""One semantic gateway for mainline and interrupt branches."""
from __future__ import annotations

from typing import Any
import json
import os
from partner.event_fabric.catalog import EventDefinition
from ._llm import call_model, json_object, event_facts


def assess_next(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    facts = dict(params.get("facts") or {})
    if not facts:
        facts = {
            "request": params.get("request"),
            "project_id": params.get("project_id"),
            "event_outputs": params.get("flow_outputs") or params.get("parent_flow_outputs") or {},
        }
    prompt = """你是 Partner 下一 Event 仲裁者。根据真实终态和记忆选择：
continue_project / active_learning / self_evolution / waiting / report / complete。
项目目录和任务简报创建成功，只证明建档完成；原文还要求实现或运行时，选择带具体动作的 continue_project。若用户明确只要求建档，则在该边界完成，不自行扩展实验。
后续通知与交付已由当前图的消息 Event 负责，不得仅因当前还没有发出消息就另开项目迭代。init 的文件回读与哈希是实际本地执行回执，不能因为 recent_execution 没有 shell 命令就否认它。
若业务计算和必要验证已完成，仅剩中文 PDF 整理与交付，选择 report，系统会启动独立 PDF Event Flow；
不要为了报告再让项目执行器重复试验、安装排版环境或自写 PDF。report 表示等待报告交付，不是原始任务已全部完成。
知识或来源不足才选 active_learning；Partner 的规划、Event、执行、交付机制缺陷才选 self_evolution；
项目可继续就优先推进。一次失败或未产生新数据本身不要求等待：若有具体可执行的纠错步骤，可选择 continue_project 并显式列出 next_event_candidates，说明本轮没有业务推进。单个动作完成不等于用户原始目标完成；complete 必须逐项对应原始目标的真实证据，仍缺算法实现、运行结果或验证时应选择有具体下一步的 continue_project。不得用报告生成代替项目进展。只输出 JSON：
{"primary_route":"","side_routes":[],"reason":"","resume_event":"","next_event_candidates":[]}。
事实=""" + event_facts({**params, "flow_outputs":facts.get("event_outputs") or params.get("flow_outputs")}, max_chars=24000)
    raw, usage = call_model(ctx, purpose="decision_assess_next", prompt=prompt)
    value = json_object(raw)
    allowed = {"continue_project", "active_learning", "self_evolution", "waiting", "report", "complete"}
    if value.get("primary_route") not in allowed:
        return {"ok": False, "status": "failed", "error": "invalid_primary_route",
                "model_output": raw, "token_usage": usage}
    # A failed experiment may legitimately lead to a distinct repair action.
    # Preserve no-progress truth, but do not confuse lack of success with a
    # requirement to stop. The worker enforces the original request's budget.
    if value.get("primary_route") == "continue_project":
        outputs = facts.get("event_outputs") or params.get("flow_outputs") or {}
        verified = bool((outputs.get("verify") or {}).get("business_delta"))
        reflect_out = outputs.get("reflect") or {}
        reflect_weight = (reflect_out.get("semantic_output") or {}).get("weight")
        if not verified:
            value["continuation_kind"] = "remediation"
            value["business_delta"] = False
            if not value.get("next_event_candidates") and not value.get("resume_event"):
                value.update(primary_route="waiting", reason="本轮未推进且没有具体修复动作，保留阻塞证据")
        if reflect_weight is not None and reflect_weight < 0.25 and value.get("primary_route") != "waiting":
            value["weight_at_route"] = reflect_weight
            value.update(primary_route="waiting", reason=f"reflect 权重 {reflect_weight} 偏低，先保留证据")
    return {"ok": True, "status": "completed", "semantic_output": value,
            "next_event_candidates": value.get("next_event_candidates") or [],
            "summary": str(value.get("reason") or value.get("primary_route")), "token_usage": usage}


DEFINITIONS = [EventDefinition(
    "selector.assess_next", "selector", "在项目、外部学习、自进化、等待和完成之间选择下一语义步骤",
    assess_next, execution_method="llm",
)]
