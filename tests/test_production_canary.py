import json
from pathlib import Path

from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.production_canary import (
    CANARY_KEY,
    authorize_production_canary,
    record_production_canary_outcome,
    resolve_production_canary,
    rollback_production_canary,
    run_rollback_drill,
)
from partner.governance.production_readiness import _digest
from partner.mind.harness import EventRegistry, HarnessEventSpec
from partner.planner import batch_planner


def _setup(tmp_path: Path):
    root = tmp_path / "workspace"
    candidate_id = "candidate_research_test"
    register_candidate_skill(str(root), {
        "candidate_id": candidate_id,
        "title": "research candidate", "status": "candidate",
        "artifact_type": "event_context_policy", "project_id": "agent_self_evolution",
        "strategy_id": "candidate_evidence_trajectory_context_v1",
        "source_episode_ids": ["episode_real"],
        "applicability": ["04 bounded research"],
        "success_criteria": ["truth gate passes"],
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "research_adoption_context_shadow",
                               "allowed_instances": ["04", "05"],
                               "default_params": {"research_project_id": "research_v1"}},
    })
    attestation = {
        "schema_version": 1, "candidate_id": candidate_id,
        "general_llm": {"ok": True}, "evolution_ledger": {"ok": True},
        "sustained_business": {"ok": False}, "longitudinal_policy_learning": {"ok": False},
        "production_ready": False,
    }
    attestation["attestation_digest"] = _digest(attestation)
    path = root / "share/mind/governance/experience_guided_policy/production_readiness" / f"{candidate_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(attestation), encoding="utf-8")
    return root, candidate_id, path


def test_canary_is_real_route_but_not_promotion(tmp_path):
    root, candidate_id, attestation = _setup(tmp_path)
    result = authorize_production_canary(
        str(root), candidate_id=candidate_id,
        readiness_attestation_path=str(attestation), authorization_ref="user:test",
        max_accepted_tasks=3, allowed_instances=["04"])
    assert result["ok"] is True
    selected = resolve_production_canary(
        str(root), instance_id="04", user_message="请研究 GitHub 源码和文献证据")
    assert selected["active"] is True
    control = json.loads((root / "share/mind/governance/experience_guided_policy/control_policy.json").read_text())
    assert CANARY_KEY in control["canaries"]
    assert candidate_id not in (control.get("promoted") or {}).values()
    candidate = json.loads((root / "share/mind/governance/experience_guided_policy/candidate_skills"
                            / f"{candidate_id}.json").read_text())
    assert candidate["production_effective"] is False


def test_canary_scope_and_automatic_rollback(tmp_path):
    root, candidate_id, attestation = _setup(tmp_path)
    activated = authorize_production_canary(
        str(root), candidate_id=candidate_id,
        readiness_attestation_path=str(attestation), authorization_ref="user:test",
        max_accepted_tasks=3, allowed_instances=["04"])
    canary_id = activated["canary"]["canary_id"]
    assert resolve_production_canary(
        str(root), instance_id="05", user_message="研究 github 文献")["active"] is False
    assert resolve_production_canary(
        str(root), instance_id="04", user_message="写一封普通消息")["active"] is False
    record_production_canary_outcome(
        str(root), canary_id=canary_id, task_id="task_fail",
        accepted=False, truth_gate_failed=True)
    assert resolve_production_canary(
        str(root), instance_id="04", user_message="研究 github 文献")["active"] is False


def test_explicit_rollback_and_drill(tmp_path):
    root, candidate_id, attestation = _setup(tmp_path)
    drill = run_rollback_drill(
        str(root), candidate_id=candidate_id,
        readiness_attestation_path=str(attestation))
    assert drill["ok"] is True
    assert drill["route_before_rollback"]["active"] is True
    assert drill["route_after_rollback"]["active"] is False
    assert rollback_production_canary(
        str(root), reason="repeat")["status"] in {"canary_rolled_back", "already_inactive"}


def test_production_canary_plan_keeps_canary_execution_mode(tmp_path):
    root, candidate_id, attestation = _setup(tmp_path)
    authorize_production_canary(
        str(root), candidate_id=candidate_id,
        readiness_attestation_path=str(attestation), authorization_ref="user:test",
        allowed_instances=["04"])
    instance = root / "instances/04"
    working = instance / "state/tasks/probe"
    sources = []
    for relative in ("external/code/source.py", "external/literature/Paper With Spaces.pdf"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("verified source", encoding="utf-8")
        sources.append(str(path))
    message = f"请研究 GitHub 源码和文献证据 source_paths=[{','.join(sources)}]"
    plan = batch_planner._deterministic_research_candidate_plan(
        message, str(working), str(instance))
    assert plan is not None
    registry = EventRegistry()
    noop = lambda ctx, params: {"ok": True}
    for name in ("execute_candidate", "generate_text", "create_file", "push_files"):
        registry.register(HarnessEventSpec(
            name, "atomic", name, noop,
            external_call=name == "push_files",
            produces_artifact=name == "create_file",
            execution_method="llm" if name == "generate_text" else "local"))
    checked = batch_planner._manual_preflight_plan(
        plan, registry=registry, workspace=str(instance),
        working_dir=str(working), user_message=message)
    assert checked.plan[0].event_type == "execute_candidate"
    assert checked.plan[0].parameters["mode"] == "canary"
    assert all(step.event_type != "push_files" for step in checked.plan)
