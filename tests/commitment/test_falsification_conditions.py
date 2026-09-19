"""Frozen failure conditions must be machine-checked, not decorative."""
from __future__ import annotations

from partner.commitment import models as M
from conftest import ScriptedExecutor, build_bet, default_snapshot, make_config, publishable_config


def _config_with_guardrail(limit: float):
    config = make_config(threshold=12.0)
    conditions = tuple(config.falsification_conditions) + (
        M.FalsificationCondition(code="sa_guardrail", kind="guardrail_violation",
                                 description="a measured ceiling was breached",
                                 params={"metric": "max", "limit": limit, "direction": "max"}),)
    return M.RunnerConfig(**{**config.__dict__, "falsification_conditions": conditions}) \
        if hasattr(config, "__dict__") else config


def test_guardrail_breach_keeps_improvement_but_blocks_publication(workspace):
    from partner.commitment.runner import RunnerConfig
    base = publishable_config(threshold=12.0)
    config = RunnerConfig(
        partner_id=base.partner_id, project_id=base.project_id, run_id=base.run_id,
        question=base.question, baseline_ref=base.baseline_ref,
        expected_effects=base.expected_effects,
        falsification_conditions=tuple(base.falsification_conditions) + (
            M.FalsificationCondition(code="max_guardrail", kind="guardrail_violation",
                                     description="max must stay within the declared ceiling",
                                     params={"metric": "max", "limit": 14.0, "direction": "max"}),),
        evaluation_protocol=base.evaluation_protocol, budget=base.budget,
        commitment_policy=base.commitment_policy, max_candidates=base.max_candidates,
        code_version=base.code_version, data_version=base.data_version,
        model_config_ref=base.model_config_ref,
        context_snapshot_ref=base.context_snapshot_ref, scope=base.scope,
        environment=base.environment, treatment_spec=base.treatment_spec,
        harness_version=base.harness_version,
        environment_fingerprint=base.environment_fingerprint)
    # values [13, 13, 40] -> mean 22 (improvement) but max 40 > 14 (guardrail breach)
    runner, _, _ = build_bet(workspace, snapshot=default_snapshot(baseline_mean=5.0),
                             config=config,
                             executor=ScriptedExecutor(workspace=workspace, values=[13.0, 13.0, 40.0]))
    settlement = runner.run().settlement
    assert settlement.improvement_over_baseline is True, "the expectation did move"
    assert settlement.publish_eligible is False
    assert any("max_guardrail" in blocker for blocker in settlement.publish_blockers)
    assert any("falsification[max_guardrail] violated" in rule for rule in settlement.machine_rules)


def test_guardrail_within_limit_publishes(workspace):
    from partner.commitment.runner import RunnerConfig
    base = publishable_config(threshold=12.0)
    config = RunnerConfig(
        partner_id=base.partner_id, project_id=base.project_id, run_id=base.run_id,
        question=base.question, baseline_ref=base.baseline_ref,
        expected_effects=base.expected_effects,
        falsification_conditions=tuple(base.falsification_conditions) + (
            M.FalsificationCondition(code="max_guardrail", kind="guardrail_violation",
                                     description="max must stay within the declared ceiling",
                                     params={"metric": "max", "limit": 14.0, "direction": "max"}),),
        evaluation_protocol=base.evaluation_protocol, budget=base.budget,
        commitment_policy=base.commitment_policy, max_candidates=base.max_candidates,
        code_version=base.code_version, data_version=base.data_version,
        model_config_ref=base.model_config_ref,
        context_snapshot_ref=base.context_snapshot_ref, scope=base.scope,
        environment=base.environment, treatment_spec=base.treatment_spec,
        harness_version=base.harness_version,
        environment_fingerprint=base.environment_fingerprint)
    runner, _, _ = build_bet(workspace, snapshot=default_snapshot(baseline_mean=5.0),
                             config=config,
                             executor=ScriptedExecutor(workspace=workspace, values=[13.0, 13.0]))
    settlement = runner.run().settlement
    assert settlement.publish_eligible is True
    assert settlement.publish_blockers == ()


def test_unknown_falsification_kind_is_treated_as_violated(workspace):
    from partner.commitment.runner import RunnerConfig
    base = make_config(threshold=12.0)
    config = RunnerConfig(
        partner_id=base.partner_id, project_id=base.project_id, run_id=base.run_id,
        question=base.question, baseline_ref=base.baseline_ref,
        expected_effects=base.expected_effects,
        falsification_conditions=tuple(base.falsification_conditions) + (
            M.FalsificationCondition(code="mystery", kind="not_implemented_kind",
                                     description="a condition the kernel cannot evaluate",
                                     params={"x": 1}),),
        evaluation_protocol=base.evaluation_protocol, budget=base.budget,
        commitment_policy=base.commitment_policy, max_candidates=base.max_candidates,
        code_version=base.code_version, data_version=base.data_version,
        model_config_ref=base.model_config_ref,
        context_snapshot_ref=base.context_snapshot_ref, scope=base.scope,
        environment=base.environment, treatment_spec=base.treatment_spec,
        harness_version=base.harness_version,
        environment_fingerprint=base.environment_fingerprint)
    runner, _, _ = build_bet(workspace, snapshot=default_snapshot(baseline_mean=5.0),
                             config=config,
                             executor=ScriptedExecutor(workspace=workspace, values=[13.0]))
    settlement = runner.run().settlement
    assert settlement.publish_eligible is False
    assert any("mystery" in blocker for blocker in settlement.publish_blockers)
