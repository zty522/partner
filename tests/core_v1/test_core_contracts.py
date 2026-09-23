from types import SimpleNamespace
import json

import pytest

from partner.core_v1.jev import JevClient, JevConfig
from partner.core_v1.latent import LatentDynamicsModel, TransitionSample
from partner.core_v1.models import CandidateAction, CoreMode, DecisionState
from partner.core_v1.policy import Route, TriggerEvidence, route_next
from partner.core_v1.store import CoreStore


def candidate(identifier="c1"):
    return CandidateAction(identifier, "project.agent_action", "run matched experiment",
                           expected_observation="metric improves", disproof="metric does not improve",
                           success_criteria=("verified delta",))


def state(value=0.2):
    return DecisionState.create(flow_id="f", project_id="p", instance_id="01", domain="project",
                                objective="improve result", facts={"source": "fixture"},
                                numeric_features={"progress": value, "risk": 0.1})


def test_trigger_policy_separates_science_learning_and_mechanism():
    self_evolve = route_next(TriggerEvidence(
        user_authorized=True, settled=True, mechanism_defect=True,
        reproducible_defect=True, independent_evaluator=True))
    assert self_evolve.route == Route.SELF_EVOLUTION
    scientific = route_next(TriggerEvidence(
        user_authorized=True, settled=True, mechanism_defect=True,
        reproducible_defect=True, independent_evaluator=True,
        scientific_negative_result=True, new_evidence=True,
        executable_next_action=True, budget_remaining=True))
    assert scientific.route == Route.CONTINUE_PROJECT
    learning = route_next(TriggerEvidence(
        user_authorized=True, settled=True, epistemic_gap=True,
        external_evidence_can_resolve=True))
    assert learning.route == Route.ACTIVE_LEARNING


def test_jev_uses_typed_protocol_and_remains_advisory():
    seen = {}
    def transport(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return {"model": "jev-2026-09-15", "answers": {
            "route": {"type": "choice", "choice": "continue_project", "confidence": 0.91,
                      "probabilities": {"continue_project": 0.91, "waiting": 0.09}},
            "readiness": {"type": "score", "score": 2.8, "confidence": 0.85,
                          "legend": {"0": "no", "3": "yes"},
                          "probabilities": {"0": 0.05, "3": 0.95}},
        }, "usage": {"input_tokens": 50, "output_tokens": 8}}
    client = JevClient(JevConfig(mode=CoreMode.SHADOW), transport=transport,
                       environ={"TYPESAFE_API_KEY": "test-key"})
    result = client.evaluate(state())
    assert result.status == "completed" and not result.authoritative
    assert result.answers["route"]["choice"] == "continue_project"
    assert seen["url"].endswith("/v1/systemone")
    assert seen["payload"]["questions"]["route"]["type"] == "choice"


def test_jev_missing_key_fails_open_for_execution_but_records_unavailable():
    result = JevClient(environ={}).evaluate(state())
    assert result.status == "unavailable"
    assert "TYPESAFE_API_KEY" in result.reason


def test_latent_model_abstains_then_learns_and_detects_ood(tmp_path):
    action = candidate()
    assert LatentDynamicsModel.fit([]).forecast(state(), action).status == "abstained"
    rows = []
    for index in range(12):
        x = index / 20
        rows.append(TransitionSample({"progress": x, "risk": 0.1}, action,
                                     {"progress": x + 0.05, "risk": 0.08}, 0.2 + x))
    model = LatentDynamicsModel.fit(rows, latent_dim=2)
    path = model.save(tmp_path / "model.json")
    predicted = LatentDynamicsModel.load(path).forecast(state(0.25), action)
    assert predicted.status == "predicted"
    assert predicted.expected_gain is not None and predicted.success_probability is not None
    far = state(100.0)
    assert model.forecast(far, action).status == "abstained"


def test_frozen_store_is_idempotent_and_rejects_mutation(tmp_path):
    from partner.core_v1.models import CoreDecisionRecord, Forecast, TypedJudgment
    record = CoreDecisionRecord.freeze(
        state=state(), selected=candidate(), candidates=(candidate(),),
        forecasts=(Forecast("c1", "abstained"),),
        judgment=TypedJudgment("unavailable", "jev", {}),
        expected_effect={"delta": 1}, evaluator={"event_type": "verify"},
        failure_conditions=("no delta",), budget={"max_actions": 1},
        selection_rule="fixture", created_at="2026-09-23T00:00:00+00:00")
    store = CoreStore(tmp_path)
    path = store.save_decision(record)
    assert store.save_decision(record) == path
    changed = json.loads(path.read_text())
    changed["selection_rule"] = "tampered"
    path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuntimeError):
        store.save_decision(record)
