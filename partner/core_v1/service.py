"""Pure orchestration helpers used by the Core v1 Event handlers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
import json

from .jev import JevClient, JevConfig
from .latent import LatentDynamicsModel, TransitionSample
from .models import (
    CandidateAction, CoreDecisionRecord, CoreMode, CoreSettlement, DecisionState,
    Forecast, TypedJudgment, digest,
)
from .policy import TriggerEvidence, route_next
from .store import CoreStore


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def workspace_root(workspace: str | Path) -> Path:
    root = Path(workspace).resolve()
    return root.parent.parent if root.parent.name == "instances" else root


def load_config(workspace: str | Path) -> dict[str, Any]:
    path = workspace_root(workspace) / "config/partner_config.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    core = value.get("core_v1")
    return dict(core) if isinstance(core, dict) else {}


def _semantic(outputs: Mapping[str, Any], node: str) -> dict[str, Any]:
    row = outputs.get(node) if isinstance(outputs.get(node), dict) else {}
    value = row.get("semantic_output") if isinstance(row.get("semantic_output"), dict) else {}
    return dict(value)


def _candidate(row: Mapping[str, Any], index: int, *, domain: str) -> CandidateAction:
    candidate_id = str(row.get("id") or row.get("candidate_id") or f"{domain}_{index}")
    event_type = str(row.get("event_type") or row.get("selected_event") or
                     ("self_evolution.candidate_execute" if domain == "self_evolution" else
                      "project.agent_action"))
    criteria = row.get("success_criteria") or ()
    if isinstance(criteria, str):
        criteria = (criteria,)
    description = str(row.get("description") or row.get("change") or row.get("hypothesis") or
                      row.get("reason") or event_type)
    return CandidateAction(
        candidate_id=candidate_id, event_type=event_type, description=description,
        parameters=dict(row.get("parameters") or {}),
        expected_observation=str(row.get("expected_observation") or row.get("hypothesis") or ""),
        disproof=str(row.get("disproof") or ""), risk=str(row.get("risk") or "unknown"),
        success_criteria=tuple(str(v) for v in criteria),
        evidence_contract=dict(row.get("evidence_contract") or {}),
        rollback=str(row.get("rollback") or ""))


def candidates_from_outputs(outputs: Mapping[str, Any], domain: str) -> tuple[tuple[CandidateAction, ...], str]:
    if domain == "project":
        source = _semantic(outputs, "plan")
        rows = source.get("candidates") if isinstance(source.get("candidates"), list) else []
        selected = source.get("selected") if isinstance(source.get("selected"), dict) else {}
        if selected:
            selected_id = str(selected.get("id") or selected.get("candidate_id") or "selected")
            selected = {**selected, "id": selected_id}
            if not any(str(r.get("id") or r.get("candidate_id")) == selected_id for r in rows
                       if isinstance(r, dict)):
                rows = [*rows, selected]
        selected_id = str(selected.get("id") or selected.get("candidate_id") or "")
    elif domain == "active_learning":
        source = _semantic(outputs, "adoption")
        rows, selected_id = ([{"id": "adoption", **source}] if source else []), "adoption"
    else:
        source = _semantic(outputs, "candidate")
        selected_id = str(source.get("candidate_id") or "candidate")
        rows = ([{"id": selected_id, **source}] if source else [])
    candidates = tuple(_candidate(row, index, domain=domain)
                       for index, row in enumerate(rows) if isinstance(row, dict))
    if not candidates:
        candidates = (_candidate({"id": f"{domain}_abstain", "event_type": "core.abstain",
                                  "description": "No executable candidate was produced",
                                  "risk": "high"}, 0, domain=domain),)
        selected_id = candidates[0].candidate_id
    if selected_id not in {c.candidate_id for c in candidates}:
        selected_id = candidates[0].candidate_id
    return candidates, selected_id


def build_state(params: Mapping[str, Any], *, domain: str) -> tuple[DecisionState, tuple[CandidateAction, ...], str]:
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    candidates, selected_id = candidates_from_outputs(outputs, domain)
    completed = [row for row in outputs.values() if isinstance(row, dict)]
    evidence = []
    failures = 0
    for row in completed:
        evidence.extend(str(v) for v in row.get("evidence_refs") or row.get("files") or [])
        if row.get("ok") is False or row.get("status") in {"failed", "blocked"}:
            failures += 1
    facts = {
        "request": str(params.get("request") or "")[:4000],
        "intent_contract": dict(params.get("intent_contract") or {}),
        "candidate_ids": [c.candidate_id for c in candidates],
        "selected_by_domain_llm": selected_id,
        "completed_nodes": sorted(outputs),
        "failure_count": failures,
    }
    numeric = {
        "candidate_count": float(len(candidates)),
        "completed_node_count": float(len(completed)),
        "output_success_ratio": (sum(row.get("ok") is not False for row in completed) /
                                 max(1, len(completed))),
        "evidence_count": float(len(set(evidence))),
        "failure_count": float(failures),
        "objective_length": float(min(len(facts["request"]), 4000)) / 4000.0,
    }
    state = DecisionState.create(
        flow_id=str(params.get("flow_id") or ""), project_id=str(params.get("project_id") or ""),
        instance_id=str(params.get("instance_id") or ""), domain=domain,
        objective=facts["request"], facts=facts, numeric_features=numeric,
        evidence_refs=evidence)
    return state, candidates, selected_id


def state_from_dict(value: Mapping[str, Any]) -> DecisionState:
    return DecisionState(
        state_id=str(value["state_id"]), flow_id=str(value.get("flow_id") or ""),
        project_id=str(value.get("project_id") or ""), instance_id=str(value.get("instance_id") or ""),
        domain=str(value.get("domain") or "project"), objective=str(value.get("objective") or ""),
        facts=dict(value.get("facts") or {}), numeric_features=dict(value.get("numeric_features") or {}),
        evidence_refs=tuple(value.get("evidence_refs") or ()),
        schema_version=int(value.get("schema_version") or 1))


def candidate_from_dict(value: Mapping[str, Any]) -> CandidateAction:
    return CandidateAction(
        candidate_id=str(value["candidate_id"]), event_type=str(value["event_type"]),
        description=str(value.get("description") or ""), parameters=dict(value.get("parameters") or {}),
        expected_observation=str(value.get("expected_observation") or ""),
        disproof=str(value.get("disproof") or ""), risk=str(value.get("risk") or "unknown"),
        success_criteria=tuple(value.get("success_criteria") or ()),
        evidence_contract=dict(value.get("evidence_contract") or {}),
        rollback=str(value.get("rollback") or ""), proposed_by=str(value.get("proposed_by") or "domain_llm"))


def judgment_from_dict(value: Mapping[str, Any]) -> TypedJudgment:
    return TypedJudgment(
        status=str(value.get("status") or "unavailable"), model=str(value.get("model") or ""),
        answers=dict(value.get("answers") or {}), confidence=float(value.get("confidence") or 0),
        usage=dict(value.get("usage") or {}), latency_ms=float(value.get("latency_ms") or 0),
        reason=str(value.get("reason") or ""), authoritative=bool(value.get("authoritative")))


def forecast_from_dict(value: Mapping[str, Any]) -> Forecast:
    return Forecast(
        candidate_id=str(value["candidate_id"]), status=str(value.get("status") or "abstained"),
        expected_gain=value.get("expected_gain"), success_probability=value.get("success_probability"),
        uncertainty=float(value.get("uncertainty") or 0), ood_distance=value.get("ood_distance"),
        predicted_next_features=dict(value.get("predicted_next_features") or {}),
        model_version=str(value.get("model_version") or ""), reason=str(value.get("reason") or ""),
        authoritative=bool(value.get("authoritative")))


def jev_client(workspace: str | Path, *, transport=None, environ=None) -> JevClient:
    cfg = load_config(workspace).get("jev")
    cfg = dict(cfg) if isinstance(cfg, dict) else {}
    try:
        mode = CoreMode(str(cfg.get("mode") or "shadow"))
    except ValueError:
        mode = CoreMode.SHADOW
    return JevClient(JevConfig(
        mode=mode, endpoint=str(cfg.get("endpoint") or "https://api.typesafe.ai/v1/systemone"),
        model=str(cfg.get("model") or "jev-latest"),
        api_key_env=str(cfg.get("api_key_env") or "TYPESAFE_API_KEY"),
        timeout_seconds=float(cfg.get("timeout_seconds") or 10)), transport=transport, environ=environ)


def freeze_decision(*, state: DecisionState, candidates: tuple[CandidateAction, ...],
                    selected_id: str, forecasts: tuple[Forecast, ...], judgment: TypedJudgment,
                    workspace: str | Path) -> CoreDecisionRecord:
    selected = next((c for c in candidates if c.candidate_id == selected_id), candidates[0])
    cfg = load_config(workspace)
    budgets = cfg.get("budget") if isinstance(cfg.get("budget"), dict) else {}
    evaluator = {"project": "project.outcome_verify", "active_learning": "active_learning.matched_verify",
                 "self_evolution": "self_evolution.matched_compare"}.get(state.domain, "evidence_terminal")
    failures = tuple(v for v in (selected.disproof, "missing_or_invalid_evidence",
                                 "budget_exhausted", "guardrail_violation") if v)
    return CoreDecisionRecord.freeze(
        state=state, selected=selected, candidates=candidates, forecasts=forecasts,
        judgment=judgment,
        expected_effect={"observation": selected.expected_observation,
                         "success_criteria": list(selected.success_criteria)},
        evaluator={"event_type": evaluator, "independent_from_proposer": True},
        failure_conditions=failures,
        budget={"max_actions": int(budgets.get("max_actions") or 1),
                "max_model_calls": int(budgets.get("max_model_calls") or 4),
                "max_child_flows": int(budgets.get("max_child_flows") or 1)},
        selection_rule="domain_llm_selection_frozen; shadow advisors recorded but non-authoritative",
        created_at=now_iso())


def _observed(domain: str, output: Mapping[str, Any]) -> tuple[str, str, bool]:
    sem = output.get("semantic_output") if isinstance(output.get("semantic_output"), dict) else {}
    if output.get("ok") is False or output.get("status") in {"failed", "blocked"}:
        return "blocked", str(output.get("error") or "evaluator failed"), False
    if domain == "active_learning":
        decision = str(sem.get("decision") or "inconclusive")
        return ({"promote": "supported", "reject": "falsified"}.get(decision, "inconclusive"),
                str(sem.get("reason") or decision), decision == "promote")
    if domain == "self_evolution":
        decision = str(sem.get("decision") or output.get("status") or "inconclusive")
        return ({"promoted": "supported", "promote": "supported", "rejected": "falsified",
                 "reject": "falsified"}.get(decision, "inconclusive"), decision,
                decision in {"promoted", "promote"})
    verified = bool(output.get("business_delta") or sem.get("business_delta") or sem.get("verified"))
    return ("supported" if verified else "falsified",
            "independent project verification passed" if verified else
            "project action produced no verified business delta", verified)


def trigger_evidence(domain: str, output: Mapping[str, Any], *, supported: bool,
                     params: Mapping[str, Any]) -> TriggerEvidence:
    sem = output.get("semantic_output") if isinstance(output.get("semantic_output"), dict) else {}
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    reflect_row = outputs.get("reflect") if isinstance(outputs.get("reflect"), dict) else {}
    reflect = (reflect_row.get("semantic_output")
               if isinstance(reflect_row.get("semantic_output"), dict) else {})
    combined = {**sem, **reflect}
    failure_class = str(output.get("failure_class") or combined.get("failure_class") or "")
    next_action = (combined.get("next_question") or combined.get("next_action") or
                   combined.get("next_goal"))
    normalized_action = str(next_action or "").strip().lower()
    actionable = bool(normalized_action and normalized_action not in {
        "无", "没有", "none", "null", "n/a", "完成", "已完成", "complete", "done",
    })
    defect = domain == "self_evolution" or failure_class in {"mechanism", "system", "runtime"}
    epistemic = bool(combined.get("epistemic_gap") or combined.get("missing_external_evidence"))
    return TriggerEvidence(
        user_authorized=bool(params.get("request") or params.get("intent_contract")),
        objective_complete=bool(combined.get("objective_complete")), settled=True,
        new_evidence=bool(output.get("evidence_refs") or supported),
        executable_next_action=actionable, budget_remaining=True,
        repeated_falsified_route=bool(combined.get("repeated_falsified_route")),
        epistemic_gap=epistemic,
        external_evidence_can_resolve=bool(combined.get("source_query") or epistemic),
        mechanism_defect=defect,
        reproducible_defect=bool(combined.get("reproducer_passed") or domain == "self_evolution"),
        independent_evaluator=True,
        scientific_negative_result=bool(combined.get("scientific_negative_result")),
        data_scarcity=bool(combined.get("data_scarcity")))


def settle_and_learn(*, record: Mapping[str, Any], state: DecisionState,
                     evaluated: Mapping[str, Any], params: Mapping[str, Any],
                     workspace: str | Path) -> tuple[CoreSettlement, TriggerEvidence, dict[str, Any]]:
    outcome, reason, supported = _observed(state.domain, evaluated)
    evidence = list(evaluated.get("evidence_refs") or evaluated.get("files") or [])
    trigger = trigger_evidence(state.domain, evaluated, supported=supported, params=params)
    route = route_next(trigger)
    observed = {"evaluator_output_hash": digest(evaluated), "supported": supported,
                "status": evaluated.get("status"), "ok": evaluated.get("ok")}
    settlement = CoreSettlement.create(
        decision_id=str(record["decision_id"]), outcome=outcome, reason=reason,
        observed=observed, evidence_refs=evidence, trigger_route=route.route.value,
        settled_at=now_iso())
    store = CoreStore(workspace)
    store.save_settlement(settlement)
    next_features = dict(state.numeric_features)
    next_features["evidence_count"] = float(next_features.get("evidence_count", 0)) + len(evidence)
    next_features["output_success_ratio"] = 1.0 if supported else 0.0
    gain = 1.0 if outcome == "supported" else -1.0 if outcome in {"falsified", "invalid"} else 0.0
    selected = record.get("selected") if isinstance(record.get("selected"), dict) else {}
    transition = {"decision_id": record["decision_id"], "state": dict(state.numeric_features),
                  "action": selected, "next_state": next_features, "gain": gain,
                  "outcome": outcome, "settlement_id": settlement.settlement_id}
    store.append_transition(transition)
    rows = store.recent_transitions()
    samples = []
    for row in rows:
        try:
            samples.append(TransitionSample(dict(row["state"]), candidate_from_dict(row["action"]),
                                            dict(row["next_state"]), float(row["gain"])))
        except (KeyError, TypeError, ValueError):
            continue
    model = LatentDynamicsModel.fit(samples)
    model_path = store.root / "latent_model.json"
    model.save(model_path)
    return settlement, trigger, {"route": route.route.value, "reason": route.reason,
                                 "jev_agrees": route.jev_agrees,
                                 "model_status": model.artifact.get("status"),
                                 "training_count": model.artifact.get("training_count", 0),
                                 "model_path": str(model_path)}
