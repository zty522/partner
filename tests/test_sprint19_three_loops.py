import json
from pathlib import Path

from partner.governance.project_action_selector import select_project_action
from partner.governance.sprint19_acceptance import (
    audit_active_learning, audit_project_iteration, audit_self_evolution,
    run_sprint19_acceptance,
)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_learning_interruption_changes_and_records_next_action(tmp_path):
    options = [
        ("continuous_project_step", {"strategy_id": "observe"}),
        ("continuous_project_step", {"strategy_id": "experiment"}),
        ("continuous_project_step", {"strategy_id": "analyse"}),
    ]
    normal = select_project_action(
        tmp_path, instance_id="04", project_id="literature_github_learning",
        options=options, project_steps=1,
    )
    learned = select_project_action(
        tmp_path, instance_id="04", project_id="literature_github_learning",
        options=options, project_steps=1, learning_interruptions=1,
    )
    assert normal["parameters"]["strategy_id"] == "experiment"
    assert learned["parameters"]["strategy_id"] == "analyse"
    assert learned["learning_intervention_applied"] is True
    assert Path(learned["selection_id"].replace("project_action_", "") or "") is not None
    rows = list((tmp_path / "share/mind/governance/experience_guided_policy/native_action_selections").glob("*.json"))
    assert len(rows) == 2


def test_three_loop_acceptance_reports_independent_gates(tmp_path):
    project = "literature_github_learning"
    artifact_a = tmp_path / "external/a.json"
    artifact_b = tmp_path / "external/b.json"
    _write(artifact_a, {"value": 1})
    _write(artifact_b, {"value": 2})
    receipts = tmp_path / f"share/projects/{project}/governance/receipts"
    _write(receipts / "0001_r1.json", {
        "receipt_id": "r1", "actions_executed": ["web.fetch:a"],
        "artifacts": [str(artifact_a)], "next_actions": [{"event_type": "next"}],
    })
    _write(receipts / "0002_r2.json", {
        "receipt_id": "r2", "actions_executed": ["pytest:test_b"],
        "artifacts": [str(artifact_b)], "next_actions": [{"event_type": "next"}],
    })
    native = tmp_path / "state/instance_native/events.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text("\n".join(json.dumps(row) for row in [
        {"event_type": "native_learning_triggered", "instance_id": "04"},
        {"event_type": "native_learning_completed", "instance_id": "04"},
    ]) + "\n", encoding="utf-8")
    selected = select_project_action(
        tmp_path, instance_id="04", project_id=project,
        options=[("continuous_project_step", {"strategy_id": "a"}),
                 ("continuous_project_step", {"strategy_id": "b"})],
        project_steps=0, learning_interruptions=1,
    )
    receipt = json.loads((receipts / "0002_r2.json").read_text(encoding="utf-8"))
    receipt["delivery_confirmed"] = True
    receipt["findings"] = [
        f"experiment executed；动作选择={selected['selection_id']}；学习干预=True"
    ]
    _write(receipts / "0002_r2.json", receipt)
    _write(tmp_path / "share/mind/governance/active_learning/sprint18/learning_policy.json", {
        "posteriors": {"duplicate|a": {"mean": .25}}, "selection_changed": True,
    })
    _write(tmp_path / "share/mind/governance/code_candidates/c1/result.json", {
        "candidate_id": "c1", "decision": "applied", "production_effective": True,
        "target_file": "partner/policy.py", "patch": "real patch",
    })
    decisions = tmp_path / "share/mind/governance/experience_guided_policy/promotion_decisions.jsonl"
    decisions.parent.mkdir(parents=True, exist_ok=True)
    decisions.write_text(json.dumps({"candidate_id": "c1", "decision": "promoted"}) + "\n",
                         encoding="utf-8")

    assert audit_project_iteration(tmp_path, project)["passed"] is True
    assert audit_active_learning(tmp_path, "04")["passed"] is True
    assert audit_self_evolution(tmp_path)["passed"] is True
    result = run_sprint19_acceptance(tmp_path, project_id=project, instance_id="04")
    assert result["status"] == "passed"
    assert Path(result["path"]).is_file()


def test_active_learning_does_not_pass_on_trigger_alone(tmp_path):
    events = tmp_path / "state/instance_native/events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({
        "event_type": "native_learning_triggered", "instance_id": "05",
    }) + "\n", encoding="utf-8")
    result = audit_active_learning(tmp_path, "05")
    assert result["passed"] is False
    assert result["gates"]["failure_or_gap_observed"] is True
    assert result["gates"]["learning_changed_next_action"] is False
    assert result["gates"]["changed_action_executed_successfully"] is False


def test_action_selection_survives_a_rolling_event_registry_cache(monkeypatch, tmp_path):
    import partner.governance.project_action_selector as selector

    calls = []
    real = selector.append_evolution_event

    def stale_registry(workspace, event_type, **kwargs):
        calls.append(event_type)
        if event_type == "active_learning/project_action_selected":
            raise ValueError("unsupported evolution event_type: active_learning/project_action_selected")
        return real(workspace, event_type, **kwargs)

    monkeypatch.setattr(selector, "append_evolution_event", stale_registry)
    result = selector.select_project_action(
        tmp_path, instance_id="03", project_id="molecular_dynamics_study",
        options=[("continuous_project_step", {"strategy_id": "a"}),
                 ("continuous_project_step", {"strategy_id": "b"})],
        project_steps=3, learning_interruptions=1,
    )
    assert result["learning_intervention_applied"] is True
    assert calls == ["active_learning/project_action_selected", "active_learning/strategy_revised"]


def test_native_action_bandit_prefers_verified_higher_reward(tmp_path):
    path = tmp_path / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"project_id": "p", "action": {"native_action_id": "weak"}, "reward": -0.1},
        {"project_id": "p", "action": {"native_action_id": "strong"}, "reward": 0.85},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    selected = select_project_action(
        tmp_path, instance_id="04", project_id="p",
        options=[("continuous_project_step", {"strategy_id": "weak"}),
                 ("continuous_project_step", {"strategy_id": "strong"})],
        project_steps=1,
    )
    assert selected["arm_id"] == "strong"
    assert selected["selection_algorithm"] == "ucb_contextual_bandit"
    assert selected["selection_reason"] == "egpl_ucb_verified_terminal_reward"


def test_production_policy_requires_llm_deliberation_each_action(tmp_path, monkeypatch):
    import partner.governance.project_action_selector as selector
    config = tmp_path / "config/partner_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"experience_guided_policy": {
        "llm_deliberation": "every_project_action"}}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(selector, "_llm_deliberate", lambda project, candidates: (
        calls.append((project, candidates)) or {"arm_id": "a", "reason": "bounded"}))
    selected = selector.select_project_action(
        tmp_path, instance_id="01", project_id="p",
        options=[("continuous_project_step", {"strategy_id": "a"})], project_steps=0)
    assert len(calls) == 1
    assert selected["llm_participation"] == {
        "required": True, "completed": True, "calls": 0,
        "role": "bounded_deliberation_no_promotion_authority"}


def test_project_deliberation_parses_nested_terminal_json(monkeypatch):
    import partner.governance.project_action_selector as selector
    monkeypatch.setattr("partner.adapters.direct_api.chat", lambda *_args, **_kwargs: (
        '<think>checked alternatives</think>{"arm_id":"a",'
        '"observed_facts":[{"fact":"measured"}],"loop_kind":"business_progress"}'))
    result = selector._llm_deliberate("p", [{"arm_id": "a"}])
    assert result["arm_id"] == "a"
    assert result["observed_facts"] == [{"fact": "measured"}]
    assert result["_llm_calls"] == 1


def test_project_reasoning_contract_keeps_three_loops_distinct():
    from partner.governance.project_reasoning import project_reasoning_contract
    value = project_reasoning_contract()
    for stage in ("PERCEIVE", "ASSOCIATE", "CONSTRAIN", "DIALECTIC", "ACCOMMODATE", "ACT-REFLECT"):
        assert stage in value
    for ledger in ("business_progress", "external_active_learning", "partner_self_evolution"):
        assert ledger in value
    assert "not progress by themselves" in value


def test_hypothesis_grammar_accepts_exact_strategy_identifier():
    from partner.governance.project_hypothesis_engine import GRAMMARS, _grammar_index
    grammar = GRAMMARS["xiaohongshu_operations"]
    assert _grammar_index("01_claim_evidence_matrix", grammar) == 0
    shared = GRAMMARS["literature_github_learning"]
    assert _grammar_index("continuous_project_step", shared, {
        "grammar_index": "continuous_project_step",
        "rationale": "bounded use of 04_adapter_contract",
    }) == 1


def test_positive_candidate_cannot_promote_itself(tmp_path):
    from partner.governance.project_hypothesis_engine import _evaluate
    candidate = {
        "candidate_id": "hypothesis_safe_gate",
        "measurement_contract": ["stable_simulations"],
        "baseline_mean_reward": 0.0,
        "decision": "proposed_canary",
        "production_effective": False,
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    rows = [{"action": {"native_action_id": "hypothesis_safe_gate"},
             "outcome": {"evidence": ["stable_simulations=4"]}, "reward": 0.8}
            for _ in range(3)]
    result = _evaluate(path, candidate, rows)
    assert result["decision"] == "ready_for_matched_experiment"
    assert result["production_effective"] is False


def test_llm_hypothesis_candidate_is_bounded_and_selected_after_plateau(monkeypatch, tmp_path):
    import partner.governance.project_hypothesis_engine as engine
    import partner.governance.project_action_selector as selector_module
    monkeypatch.setattr(selector_module, "_llm_deliberate", lambda *_args: {})
    path = tmp_path / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    path.parent.mkdir(parents=True)
    rows = [{"project_id": "molecular_dynamics_study",
             "action": {"native_action_id": arm, "event_types": ["continuous_project_step"]},
             "outcome": {"duplicate_outcome": True}, "reward": -0.1}
            for arm in ("03_md_integrator_smoke", "03_md_timestep_stability",
                        "03_md_temperature_sweep")]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(engine, "_llm", lambda _prompt: {
        "grammar_index": 1, "variant": 4,
        "hypothesis": "temperature changes reveal a hidden stability boundary",
        "opposing_hypothesis": "the apparent boundary is numerical noise",
        "falsifier": "matched runs show no drift-distribution change",
        "expected_observation": "a reproducible drift change against baseline",
        "rationale": "tests a new causal contrast", "intervention_kind": "business_progress",
    })
    selected = select_project_action(
        tmp_path, instance_id="03", project_id="molecular_dynamics_study",
        options=[("continuous_project_step", {"strategy_id": "03_md_integrator_smoke"}),
                 ("continuous_project_step", {"strategy_id": "03_md_timestep_stability"}),
                 ("continuous_project_step", {"strategy_id": "03_md_temperature_sweep"})],
        project_steps=3,
    )
    assert selected["arm_id"].startswith("hypothesis_")
    assert selected["parameters"]["candidate_variant"] == 4
    assert selected["parameters"]["strategy_id"] == "03_md_temperature_sweep"
    assert selected["hypothesis_candidate"]["production_effective"] is False
    assert selected["hypothesis_candidate"]["schema_version"] == 2
    assert selected["hypothesis_candidate"]["measurement_contract"] == [
        "worst_relative_energy_drift", "stable_simulations"]
    assert "potential energy" not in selected["hypothesis_candidate"]["hypothesis"]
    candidates = list((tmp_path / "share/mind/governance/experience_guided_policy/project_candidates").glob("*.json"))
    assert candidates
    rows.append({"project_id": "molecular_dynamics_study",
                 "action": {"native_action_id": selected["arm_id"],
                            "event_types": ["continuous_project_step"]},
                 "outcome": {"duplicate_outcome": True}, "reward": -0.1})
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    select_project_action(
        tmp_path, instance_id="03", project_id="molecular_dynamics_study",
        options=[("continuous_project_step", {"strategy_id": "03_md_integrator_smoke"}),
                 ("continuous_project_step", {"strategy_id": "03_md_timestep_stability"}),
                 ("continuous_project_step", {"strategy_id": "03_md_temperature_sweep"})],
        project_steps=4,
    )
    decision = json.loads(candidates[0].read_text(encoding="utf-8"))
    assert decision["decision"] == "rejected"
    assert decision["production_effective"] is False
    assert decision["rejection_reason"] == "measurement_contract_not_observed"
