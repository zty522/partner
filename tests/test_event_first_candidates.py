from __future__ import annotations

import json
import hashlib
from types import SimpleNamespace

from partner.governance.candidate_execution import execute_candidate
from partner.governance.candidate_skills import activate_promoted_candidate, register_candidate_skill
from partner.governance.evolution_events import (
    append_evolution_event,
    load_evolution_events,
    verify_evolution_ledger,
)
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.v2.candidate_events import ALLOWED_CANDIDATE_HANDLERS, HANDLERS
from partner.mind.harness import default_registry


def _payload(*, ready: bool, experiment_id: str = "experiment_event_first") -> dict:
    return {
        "candidate_id": "candidate_event_first",
        "title": "event-first candidate",
        "status": "candidate",
        "artifact_type": "event_strategy" if ready else "knowledge_draft",
        "project_id": "event_first_test",
        "experiment_id": experiment_id,
        "strategy_id": "candidate_event_first",
        "source_episode_ids": ["episode_test_source"],
        "success_criteria": ["handler returns ok"],
        "applicability": ["instance 04 test"],
        "intervention": json.dumps({"change": "bounded test"}),
        "execution_contract": {
            "ready": ready,
            "kind": "event",
            "event_type": "test_candidate_event" if ready else "",
            "allowed_instances": ["04"],
            "default_params": {"from_contract": True},
        },
    }


def test_registration_emits_candidate_proposed_event(tmp_path):
    result = register_candidate_skill(str(tmp_path), _payload(ready=True))
    assert result["candidate"]["execution_ready"] is True
    assert result["event"]["event_type"] == "candidate/proposed"
    events = load_evolution_events(str(tmp_path))
    assert [event["event_type"] for event in events] == ["candidate/proposed"]
    assert verify_evolution_ledger(str(tmp_path))["ok"] is True


def test_candidate_and_bdk_events_are_in_audited_registries():
    assert "execute_candidate" in HANDLERS
    assert "targetdiff_bdk_function_pool" in ALLOWED_CANDIDATE_HANDLERS
    assert default_registry().get("execute_candidate") is not None


def test_knowledge_draft_is_not_executable(tmp_path):
    register_candidate_skill(str(tmp_path), _payload(ready=False))
    result = execute_candidate(
        str(tmp_path), "candidate_event_first",
        ctx=SimpleNamespace(), handlers={}, params={}, instance_id="04",
        execution_id="exec_blocked", mode="shadow",
    )
    assert result["ok"] is False
    assert result["status"] == "candidate_execution_blocked"
    assert "not ready" in result["error"]
    assert [row["event_type"] for row in load_evolution_events(str(tmp_path))][-2:] == [
        "candidate/execution_requested", "candidate/execution_completed",
    ]


def test_executable_candidate_runs_only_through_allowlisted_event(tmp_path):
    register_candidate_skill(str(tmp_path), _payload(ready=True))

    def handler(_ctx, params):
        assert params == {"from_contract": True, "runtime": 7}
        return {"ok": True, "status": "completed", "summary": "real handler ran",
                "files": ["artifact.json"]}

    result = execute_candidate(
        str(tmp_path), "candidate_event_first",
        ctx=SimpleNamespace(), handlers={"test_candidate_event": handler},
        params={"runtime": 7}, instance_id="04",
        execution_id="exec_ok", mode="shadow",
    )
    assert result["ok"] is True
    assert result["candidate_event_type"] == "test_candidate_event"
    assert result["execution_event_id"].startswith("evoevt_")


def test_execution_id_is_idempotent_and_does_not_repeat_side_effect(tmp_path):
    register_candidate_skill(str(tmp_path), _payload(ready=True))
    calls = []

    def handler(_ctx, _params):
        calls.append("called")
        return {"ok": True, "status": "completed", "summary": "ran once"}

    first = execute_candidate(
        str(tmp_path), "candidate_event_first", ctx=SimpleNamespace(),
        handlers={"test_candidate_event": handler}, params={}, instance_id="04",
        execution_id="exec_idempotent", mode="shadow",
    )
    second = execute_candidate(
        str(tmp_path), "candidate_event_first", ctx=SimpleNamespace(),
        handlers={"test_candidate_event": handler}, params={}, instance_id="04",
        execution_id="exec_idempotent", mode="shadow",
    )
    assert first["status"] == "completed"
    assert second["status"] == "idempotent_replay"
    assert second["original_status"] == "completed"
    assert second["execution_event_id"] == first["execution_event_id"]
    assert calls == ["called"]


def test_execution_id_cannot_be_reused_for_another_candidate(tmp_path):
    register_candidate_skill(str(tmp_path), _payload(ready=True))
    execute_candidate(
        str(tmp_path), "candidate_event_first", ctx=SimpleNamespace(),
        handlers={"test_candidate_event": lambda _ctx, _params: {"ok": True}},
        params={}, instance_id="04", execution_id="exec_bound", mode="shadow",
    )
    result = execute_candidate(
        str(tmp_path), "candidate_other", ctx=SimpleNamespace(), handlers={},
        params={}, instance_id="04", execution_id="exec_bound", mode="shadow",
    )
    assert result["ok"] is False
    assert "different candidate" in result["error"]


def test_candidate_cannot_execute_on_wrong_instance(tmp_path):
    register_candidate_skill(str(tmp_path), _payload(ready=True))
    result = execute_candidate(
        str(tmp_path), "candidate_event_first", ctx=SimpleNamespace(),
        handlers={"test_candidate_event": lambda _ctx, _params: {"ok": True}},
        params={}, instance_id="02", execution_id="exec_wrong_instance", mode="shadow",
    )
    assert result["ok"] is False
    assert "not allowed" in result["error"]


def test_non_executable_candidate_cannot_be_promoted(tmp_path):
    register_candidate_skill(str(tmp_path), _payload(ready=False))
    result = decide_experiment(str(tmp_path), {
        "experiment_id": "experiment_event_first",
        "candidate_id": "candidate_event_first",
        "decision": "promoted",
        "evidence": ["pytest"],
        "regression_passed": True,
        "criteria_results": {"handler returns ok": True},
    })
    assert result["ok"] is False
    assert result["status"] == "candidate_not_executable"


def test_policy_event_refreshes_candidate_shadow_projection(tmp_path):
    register_candidate_skill(str(tmp_path), _payload(ready=True))
    result = decide_experiment(str(tmp_path), {
        "experiment_id": "experiment_event_first",
        "candidate_id": "candidate_event_first",
        "decision": "inconclusive",
        "evidence": ["matched-run.json"],
        "regression_passed": True,
        "criteria_results": {"handler returns ok": True, "repeat count": False},
        "metrics_before": {"rmse": 1.0},
        "metrics_after": {"rmse": 0.9},
    })
    assert result["ok"] is True
    projection = result["candidate_projection"]
    assert projection["status"] == "candidate"
    assert projection["shadow_evidence"]["decision"] == "inconclusive"
    assert projection["shadow_evidence"]["metrics_after"]["rmse"] == 0.9


def test_promoted_candidate_requires_separate_event_first_activation(tmp_path):
    register_candidate_skill(str(tmp_path), _payload(ready=True))
    decision = decide_experiment(str(tmp_path), {
        "experiment_id": "experiment_event_first", "candidate_id": "candidate_event_first",
        "decision": "promoted", "evidence": ["matched-run.json"],
        "regression_passed": True, "criteria_results": {"handler returns ok": True},
    })
    projection = decision["candidate_projection"]
    assert projection["production_effective"] is False
    policy_event_id = decision["events"][-1]["event_id"]
    activated = activate_promoted_candidate(
        str(tmp_path), candidate_id="candidate_event_first",
        decision_key="event_first_test:planning", policy_event_id=policy_event_id)
    assert activated["ok"] is True
    assert activated["candidate"]["status"] == "promoted"
    assert activated["candidate"]["production_effective"] is True
    control = json.loads((tmp_path / "share/mind/governance/experience_guided_policy/control_policy.json").read_text())
    assert control["promoted"]["event_first_test:planning"] == "candidate_event_first"
    assert load_evolution_events(str(tmp_path))[-1]["event_type"] == "policy/activated"


def test_learned_candidate_activation_requires_readiness_attestation(tmp_path):
    payload = _payload(ready=True)
    payload["production_readiness_contract"] = {"required": True, "version": "v1"}
    register_candidate_skill(str(tmp_path), payload)
    decision = decide_experiment(str(tmp_path), {
        "experiment_id": "experiment_event_first", "candidate_id": "candidate_event_first",
        "decision": "promoted", "evidence": ["matched-run.json"],
        "regression_passed": True, "criteria_results": {"handler returns ok": True},
    })
    blocked = activate_promoted_candidate(
        str(tmp_path), candidate_id="candidate_event_first",
        decision_key="event_first_test:planning",
        policy_event_id=decision["events"][-1]["event_id"])
    assert blocked["ok"] is False
    assert blocked["status"] == "production_readiness_blocked"
    control = tmp_path / "share/mind/governance/experience_guided_policy/control_policy.json"
    assert not control.exists()


def test_issue_experiment_and_decision_are_events(tmp_path):
    issue = record_issue(str(tmp_path), {
        "summary": "candidate route is not observable", "category": "planning",
        "severity": "medium", "evidence": ["episode_test_source"],
        "instance_id": "04", "project_id": "event_first_test",
    })
    experiment = start_experiment(str(tmp_path), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "an explicit event marker makes the route observable",
        "intervention": "emit a route marker",
        "baseline": {"strategy_id": "baseline"},
        "success_criteria": ["marker present"],
        "project_id": "event_first_test",
        "tests": ["pytest"],
    })
    decision = decide_experiment(str(tmp_path), {
        "experiment_id": experiment["experiment"]["experiment_id"],
        "decision": "inconclusive", "evidence": ["pytest"],
        "regression_passed": True, "criteria_results": {"marker present": False},
        "project_id": "event_first_test",
    })
    assert decision["ok"] is True
    types = [row["event_type"] for row in load_evolution_events(str(tmp_path))]
    assert types == ["issue/recorded", "experiment/started",
                     "experiment/completed", "policy/inconclusive"]
    projected_experiment = json.loads(
        (tmp_path / "share/mind/governance/experiments"
         / f"{experiment['experiment']['experiment_id']}.json").read_text(encoding="utf-8")
    )
    assert projected_experiment["status"] == "inconclusive"
    assert projected_experiment["result"]["policy_event_id"].startswith("evoevt_")


def test_ledger_tamper_is_detected(tmp_path):
    append_evolution_event(
        str(tmp_path), "issue/recorded", subject_id="issue_x",
        payload={"status": "open"}, evidence_refs=["evidence"],
        idempotency_key="issue-x",
    )
    path = tmp_path / "share/mind/governance/evolution_events.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["payload"]["status"] = "tampered"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    result = verify_evolution_ledger(str(tmp_path))
    assert result["ok"] is False
    assert result["reason"] == "event_hash_mismatch"


def test_ledger_reports_but_preserves_hash_valid_historical_writer(tmp_path):
    path = tmp_path / "share/mind/governance/evolution_events.jsonl"
    path.parent.mkdir(parents=True)
    legacy = {
        "schema_version": 2, "seq": 1, "event_type": "overnight/canary_run",
        "occurred_at": "2026-09-04T01:00:00+08:00", "actor": "retired_writer",
        "subject_id": "legacy", "project_id": "agent_self_evolution",
        "parents": [], "payload": {}, "evidence_refs": [], "prev_hash": "",
    }
    body = json.dumps(legacy, ensure_ascii=False, sort_keys=True)
    legacy["event_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    result = verify_evolution_ledger(str(tmp_path))
    assert result["ok"] is True
    assert result["legacy_non_idempotent_count"] == 1
    assert result["legacy_fork_count"] == 1
