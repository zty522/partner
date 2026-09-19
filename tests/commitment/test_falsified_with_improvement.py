"""A direction can improve its primary metric and still be refuted by a guardrail."""
from __future__ import annotations

from conftest import ScriptedExecutor, build_bet, default_snapshot, make_config
from partner.commitment import models as M
from partner.commitment.runner import RunnerConfig


def _config_with_max_guardrail(base, limit):
    """The ceiling is a frozen *expectation* of kind guardrail, so breaching it
    refutes the direction; a ``guardrail_violation`` falsification condition would
    only block publication."""
    effects = tuple(base.expected_effects) + (
        M.ExpectedEffect(metric="max", direction="decrease", threshold=float(limit),
                         unit="unit", kind="guardrail"),)
    return RunnerConfig(
        partner_id=base.partner_id, project_id=base.project_id, run_id=base.run_id,
        question=base.question, baseline_ref=base.baseline_ref,
        expected_effects=effects,
        falsification_conditions=base.falsification_conditions,
        evaluation_protocol=base.evaluation_protocol, budget=base.budget,
        commitment_policy=base.commitment_policy, max_candidates=base.max_candidates,
        code_version=base.code_version, data_version=base.data_version,
        model_config_ref=base.model_config_ref, context_snapshot_ref=base.context_snapshot_ref,
        scope=base.scope, environment=base.environment, treatment_spec=base.treatment_spec,
        harness_version=base.harness_version,
        environment_fingerprint=base.environment_fingerprint)


def test_primary_delta_met_but_guardrail_broken_is_falsified_not_hidden(workspace):
    """mean improves past the frozen delta, but the max guardrail is breached."""
    snapshot = default_snapshot(baseline_mean=5.0)
    base = make_config(threshold=12.0, kind="delta_over_baseline", min_delta=2.0)
    config = _config_with_max_guardrail(base, limit=14.0)
    runner, _, _ = build_bet(workspace, snapshot=snapshot, config=config,
                             executor=ScriptedExecutor(workspace=workspace, values=[13.0, 40.0]))
    settlement = runner.run().settlement
    assert settlement.improvement_over_baseline is True, "the delta did move"
    assert settlement.expectations_met is False, "the max guardrail expectation failed"
    assert settlement.expectation_outcomes[-1].kind == "guardrail"
    assert settlement.expectation_outcomes[-1].met is False
    assert settlement.settlement_class == "falsified"
    assert settlement.improvement_observed is True
    assert any("refuted" in rule for rule in settlement.machine_rules)
    assert settlement.publish_eligible is False


def test_falsified_still_requires_a_machine_ground():
    with pytest_raises() as excinfo:
        M.SettlementDecision(
            settlement_id="s", bet_id="b", settlement_class="falsified",
            expectation_outcomes=(), comparison=M.ComparisonProof(
                inputs_identical=False, evaluator_identical=False, protocol_identical=False,
                budget_comparable=False, environment_identical=False,
                harness_version_identical=False, baseline_treatment="", candidate_treatment="",
                treatment_diff_hash="", expected_treatment_diff_hash="",
                baseline_code_version="", candidate_code_version="", baseline_value=None,
                candidate_value=None, delta=None),
            new_regressions=(), pre_existing_failures=(), expectations_met=False,
            improvement_over_baseline=False, baseline_already_satisfied=False,
            supported_claim="none", environment="production_canary",
            improvement_observed=False, publish_eligible=False, publish_blockers=(),
            next_state="CLOSED", machine_rules=("r",))
    assert "machine-checkable ground" in str(excinfo.value)


class pytest_raises:
    def __init__(self):
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        assert exc_type is M.ContractError, f"expected ContractError, got {exc_type}"
        self.value = exc
        return True
