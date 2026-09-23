from types import SimpleNamespace

from partner.events import core_v1


def _params():
    return {
        "flow_id": "flow_test", "project_id": "project_test", "instance_id": "01",
        "request": "run one verified project experiment",
        "intent_contract": {"original_request": "run one verified project experiment"},
        "domain": "project",
        "flow_outputs": {
            "plan": {"ok": True, "semantic_output": {
                "candidates": [
                    {"id": "safe", "event_type": "project.agent_action",
                     "description": "small matched run", "expected_observation": "new metric",
                     "disproof": "no metric", "risk": "low"},
                    {"id": "large", "event_type": "project.agent_action",
                     "description": "large run", "risk": "high"},
                ],
                "selected": {"id": "safe", "event_type": "project.agent_action",
                             "reason": "bounded", "success_criteria": ["verified artifact"]},
            }}
        },
    }


def test_core_events_freeze_execute_evidence_settle_and_route(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/partner_config.json").write_text("{}", encoding="utf-8")
    ctx = SimpleNamespace(workspace=str(tmp_path))
    params = _params()
    state = core_v1.state_build(ctx, params)
    params["flow_outputs"]["core_state"] = state
    forecast = core_v1.latent_forecast(ctx, params)
    params["flow_outputs"]["core_forecast"] = forecast
    jev = core_v1.jev_evaluate(ctx, params)
    params["flow_outputs"]["core_jev"] = jev
    commit = core_v1.commitment_freeze(ctx, params)
    params["flow_outputs"]["core_commit"] = commit
    assert commit["semantic_output"]["decision"]["selected"]["candidate_id"] == "safe"
    assert commit["semantic_output"]["decision"]["failure_conditions"]

    artifact = tmp_path / "measured.json"
    artifact.write_text('{"metric": 1}', encoding="utf-8")
    params["evaluated_node"] = "verify"
    params["flow_outputs"]["verify"] = {
        "ok": True, "status": "completed", "business_delta": True,
        "evidence_refs": [str(artifact)], "semantic_output": {"verified": True},
    }
    params["flow_outputs"]["reflect"] = {
        "ok": True, "semantic_output": {
            "next_question": "run the declared follow-up", "objective_complete": False,
            "failure_class": "project",
        },
    }
    settled = core_v1.settlement(ctx, params)
    params["flow_outputs"]["core_settlement"] = settled
    assert settled["semantic_output"]["settlement"]["outcome"] == "supported"
    routed = core_v1.route(ctx, params)
    assert routed["semantic_output"]["primary_route"] == "continue_project"
    assert routed["next_event_candidates"][0]["goal"] == "run the declared follow-up"


def test_flow_registry_places_commitment_before_real_execution():
    from partner.event_flows import build_flow_registry
    registry = build_flow_registry()
    for name, execute_node in (("project_iteration", "execute"),
                               ("active_learning", "matched"),
                               ("self_evolution", "isolate")):
        flow = registry.get(name)
        assert "core_commit" in flow.node(execute_node).depends_on
        assert flow.node("core_settlement").event_type == "core.settlement"


def test_instance_worker_claims_authoritative_lease_before_returning_job(tmp_path):
    from partner.application import PartnerApplicationService
    from partner.runtime.event_worker import EventWorker
    (tmp_path / "config").mkdir()
    (tmp_path / "config/partner_config.json").write_text('{"agent":{"backend":"direct"}}')
    for identifier in ("01", "02", "03", "04", "05"):
        (tmp_path / "instances" / identifier).mkdir(parents=True)
    result = PartnerApplicationService(tmp_path).submit(
        "推进04项目", channel="local", sender_id="test", persona_hint="04")
    assert result.accepted
    worker = EventWorker(tmp_path, "04")
    job = worker.next_job()
    assert job is not None
    assert worker._db_lease_job_id == job.job_id
    assert worker._db_lease_owner
    worker._release_claim()
