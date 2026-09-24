"""Composable candidate proposal, adversarial review and selection Events."""
from __future__ import annotations

from typing import Any, Mapping

from partner.event_fabric.catalog import EventDefinition
from .project import hypothesis_propose, hypothesis_critic, action_select


def _semantic(params: Mapping[str, Any], node: str) -> dict[str, Any]:
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), Mapping) else {}
    row = outputs.get(node) if isinstance(outputs.get(node), Mapping) else {}
    value = row.get("semantic_output") if isinstance(row.get("semantic_output"), Mapping) else {}
    return dict(value)


def propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    result = hypothesis_propose(ctx, params)
    semantic = dict(result.get("semantic_output") or {})
    rows = semantic.get("hypotheses") if isinstance(semantic.get("hypotheses"), list) else []
    candidates = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        candidates.append({
            "id": str(row.get("id") or f"candidate_{index}"),
            "event_type": str(row.get("event_type") or "project.agent_action"),
            "description": str(row.get("claim") or row.get("action") or ""),
            "parameters": dict(row.get("parameters") or {}),
            "expected_observation": str(row.get("expected_observation") or ""),
            "disproof": str(row.get("disproof") or ""),
            "risk": str(row.get("risk") or "unknown"),
            "success_criteria": list(row.get("required_evidence") or []),
        })
    result["semantic_output"] = {**semantic, "candidates": candidates}
    result["summary"] = f"proposed {len(candidates)} candidates"
    return result


def critic(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    result = hypothesis_critic(ctx, params)
    semantic = dict(result.get("semantic_output") or {})
    proposed = _semantic(params, "propose").get("candidates") or []
    accepted_ids = {str(v) for v in semantic.get("accepted") or []}
    accepted = [dict(row) for row in proposed if isinstance(row, Mapping)
                and (not accepted_ids or str(row.get("id")) in accepted_ids)]
    result["semantic_output"] = {**semantic, "accepted_candidates": accepted}
    result["summary"] = f"critic accepted {len(accepted)} candidates"
    return result


def select(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    result = action_select(ctx, params)
    semantic = dict(result.get("semantic_output") or {})
    candidates = _semantic(params, "propose").get("candidates") or []
    accepted = _semantic(params, "critic").get("accepted_candidates") or candidates
    selected_id = str(semantic.get("selected_id") or semantic.get("id") or "")
    selected_event = str(semantic.get("selected_event") or semantic.get("event_type") or "")
    selected = next((dict(row) for row in accepted if isinstance(row, Mapping)
                     and (str(row.get("id")) == selected_id or
                          (not selected_id and str(row.get("event_type")) == selected_event))), None)
    if selected is None and accepted:
        selected = dict(accepted[0])
    selected = selected or {"id": "abstain", "event_type": "core.abstain",
                            "description": "no candidate survived critic"}
    selected.update({key: semantic[key] for key in (
        "parameters", "reason", "success_criteria", "evidence_contract", "rollback", "goal_gaps")
        if key in semantic})
    result["semantic_output"] = {**semantic, "candidates": list(candidates),
                                 "selected": selected}
    result["summary"] = f"selected candidate {selected.get('id')}"
    return result


DEFINITIONS = [
    EventDefinition("candidate.propose", "candidate", "提出有限、可证伪的候选集合", propose,
                    execution_method="llm", evidence_contract=("candidate_set",)),
    EventDefinition("candidate.critic", "candidate", "独立攻击候选并保留可执行集合", critic,
                    execution_method="llm", evidence_contract=("critic_findings",)),
    EventDefinition("candidate.select", "candidate", "依据冻结标准选择一个候选", select,
                    execution_method="llm", evidence_contract=("selected_candidate",)),
]

