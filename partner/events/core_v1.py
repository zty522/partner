"""Canonical Events forming the Partner Core v1 decision spine."""
from __future__ import annotations

from typing import Any, Mapping

from partner.event_fabric.catalog import EventDefinition
from partner.core_v1.latent import LatentDynamicsModel
from partner.core_v1.models import Forecast, TypedJudgment
from partner.core_v1.policy import TriggerEvidence, route_next
from partner.core_v1.service import (
    build_state, candidate_from_dict, forecast_from_dict, freeze_decision,
    jev_client, judgment_from_dict, settle_and_learn, state_from_dict,
)
from partner.core_v1.store import CoreStore


def _outputs(params: Mapping[str, Any]) -> dict[str, Any]:
    return dict(params.get("flow_outputs") or {}) if isinstance(params.get("flow_outputs"), dict) else {}


def _semantic(params: Mapping[str, Any], node: str) -> dict[str, Any]:
    row = _outputs(params).get(node)
    if not isinstance(row, dict) or not isinstance(row.get("semantic_output"), dict):
        return {}
    return dict(row["semantic_output"])


def state_build(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    domain = str(params.get("domain") or "project")
    state, candidates, selected_id = build_state(params, domain=domain)
    return {"ok": True, "status": "completed", "summary": f"Core v1 state {state.state_id}",
            "semantic_output": {"state": state.to_dict(),
                                "candidates": [c.to_dict() for c in candidates],
                                "selected_id": selected_id}}


def latent_forecast(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    source = _semantic(params, "core_state")
    if not source:
        return {"ok": False, "status": "failed", "error": "missing core_state"}
    state = state_from_dict(source["state"])
    candidates = tuple(candidate_from_dict(v) for v in source.get("candidates") or [])
    model = LatentDynamicsModel.load(CoreStore(ctx.workspace).root / "latent_model.json")
    forecasts = tuple(model.forecast(state, candidate) for candidate in candidates)
    return {"ok": True, "status": "completed", "summary": "shadow latent forecasts recorded",
            "semantic_output": {"forecasts": [v.to_dict() for v in forecasts],
                                "authoritative": False,
                                "model_status": model.artifact.get("status", "unfitted")}}


def jev_evaluate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    source = _semantic(params, "core_state")
    if not source:
        return {"ok": False, "status": "failed", "error": "missing core_state"}
    judgment = jev_client(ctx.workspace).evaluate(state_from_dict(source["state"]))
    return {"ok": True, "status": "completed", "summary": f"Jev {judgment.status}",
            "semantic_output": {"judgment": judgment.to_dict(),
                                "advisory_only": not judgment.authoritative},
            "token_usage": dict(judgment.usage)}


def commitment_freeze(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    source = _semantic(params, "core_state")
    if not source:
        return {"ok": False, "status": "failed", "error": "missing core_state"}
    state = state_from_dict(source["state"])
    candidates = tuple(candidate_from_dict(v) for v in source.get("candidates") or [])
    forecast_rows = _semantic(params, "core_forecast").get("forecasts") or []
    forecasts = tuple(forecast_from_dict(v) for v in forecast_rows)
    judgment_row = _semantic(params, "core_jev").get("judgment") or {}
    judgment = judgment_from_dict(judgment_row) if judgment_row else TypedJudgment(
        "unavailable", "jev-latest", {}, reason="Jev event produced no judgment")
    record = freeze_decision(state=state, candidates=candidates,
                             selected_id=str(source.get("selected_id") or ""),
                             forecasts=forecasts, judgment=judgment, workspace=ctx.workspace)
    path = CoreStore(ctx.workspace).save_decision(record)
    return {"ok": True, "status": "completed", "summary": f"frozen {record.decision_id}",
            "evidence_refs": [str(path)],
            "semantic_output": {"decision": record.to_dict(), "decision_id": record.decision_id,
                                "commitment_path": str(path)}}


def settlement(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    state_row = _semantic(params, "core_state").get("state") or {}
    record = _semantic(params, "core_commit").get("decision") or {}
    evaluated_node = str(params.get("evaluated_node") or "verify")
    evaluated = _outputs(params).get(evaluated_node)
    if not state_row or not record or not isinstance(evaluated, dict):
        return {"ok": False, "status": "failed",
                "error": f"settlement missing state, commitment or evaluator node {evaluated_node}"}
    settled, trigger, learning = settle_and_learn(
        record=record, state=state_from_dict(state_row), evaluated=evaluated,
        params=params, workspace=ctx.workspace)
    return {"ok": True, "status": "completed", "summary": settled.reason,
            "evidence_refs": list(settled.evidence_refs),
            "semantic_output": {"settlement": settled.to_dict(),
                                "trigger_evidence": trigger.__dict__,
                                "recommended_route": learning["route"],
                                "route_reason": learning["reason"],
                                "model_update": learning}}


def route(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    settled = _semantic(params, "core_settlement")
    raw = settled.get("trigger_evidence") if isinstance(settled.get("trigger_evidence"), dict) else {}
    if not raw:
        return {"ok": True, "status": "completed", "summary": "missing trigger evidence; waiting",
                "semantic_output": {"primary_route": "waiting", "side_routes": [],
                                    "reason": "Core settlement supplied no trigger evidence",
                                    "next_event_candidates": []}}
    fields = TriggerEvidence.__dataclass_fields__
    evidence = TriggerEvidence(**{name: bool(raw.get(name)) for name in fields})
    judgment_row = _semantic(params, "core_jev").get("judgment") or {}
    judgment = judgment_from_dict(judgment_row) if judgment_row else None
    decision = route_next(evidence, judgment)
    outputs = _outputs(params)
    reflect = outputs.get("reflect") if isinstance(outputs.get("reflect"), dict) else {}
    sem = reflect.get("semantic_output") if isinstance(reflect.get("semantic_output"), dict) else {}
    next_candidates = []
    if sem.get("next_question"):
        next_candidates.append({"goal": str(sem["next_question"]), "source": "verified_reflection"})
    return {"ok": True, "status": "completed", "summary": decision.reason,
            "semantic_output": {"primary_route": decision.route.value, "side_routes": [],
                                "reason": decision.reason, "jev_agrees": decision.jev_agrees,
                                "next_event_candidates": next_candidates},
            "next_event_candidates": next_candidates}


DEFINITIONS = [
    EventDefinition("core.state_build", "core", "构建统一的冻结前决策状态", state_build),
    EventDefinition("core.latent_forecast", "core", "潜空间动力学影子预测候选后果", latent_forecast),
    EventDefinition("core.jev_evaluate", "core", "Jev 对有限候选进行类型化影子判断", jev_evaluate,
                    execution_method="external", external_call=True),
    EventDefinition("core.commitment_freeze", "core", "冻结选择、预期、评价器、预算和失败条件",
                    commitment_freeze, produces_artifact=True),
    EventDefinition("core.settlement", "core", "依据真实执行和独立评价裁决冻结决策",
                    settlement, produces_artifact=True, reads_existing_artifact=True),
    EventDefinition("core.route_next", "core", "按确定性触发合同选择迭代、主动学习或自进化",
                    route),
]
