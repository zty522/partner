"""Settlement classification: improvement, regression and pre-existing failure.

These four cases are the whole point of the kernel, so they are asserted against
the machine rule rather than against prose.
"""
from __future__ import annotations

import pytest

from partner.commitment import models as M
from conftest import (ScriptedExecutor, ScriptedBaselineProvider, build_bet, default_snapshot,
                      make_config, publishable_config)


def test_baseline_fail_candidate_pass_records_target_improvement(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)      # baseline misses the threshold
    runner, store, _ = build_bet(workspace, snapshot=snapshot,
                                 config=make_config(threshold=12.0),
                                 executor=ScriptedExecutor(workspace=workspace,
                                                           values=[13.0, 14.0]))
    result = runner.run()
    settlement = result.settlement
    assert settlement.settlement_class == "supported"
    assert settlement.expectations_met is True
    assert settlement.improvement_over_baseline is True
    assert settlement.baseline_already_satisfied is False
    assert settlement.supported_claim == "improvement_over_baseline"
    assert settlement.improvement_observed is settlement.improvement_over_baseline
    assert settlement.new_regressions == ()
    assert settlement.pre_existing_failures == ()
    assert settlement.expectation_outcomes[0].note.startswith("improvement_over_baseline")
    assert settlement.comparison.delta is not None and settlement.comparison.delta > 0
    # supported, and still unpublishable: the environment decides that
    assert settlement.publish_eligible is False
    assert "synthetic_fixture_only" in settlement.publish_blockers
    assert "single_episode_only" in settlement.publish_blockers


def test_baseline_fail_candidate_fail_records_pre_existing_failure(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    runner, _, _ = build_bet(workspace, snapshot=snapshot, config=make_config(threshold=12.0),
                             executor=ScriptedExecutor(workspace=workspace, values=[6.0]))
    settlement = runner.run().settlement
    assert settlement.settlement_class == "falsified"
    assert settlement.expectations_met is False
    assert settlement.improvement_over_baseline is False
    assert settlement.baseline_already_satisfied is False
    assert settlement.supported_claim == "none"
    assert settlement.pre_existing_failures == ("mean",)
    assert settlement.new_regressions == (), "both failing is a blocker, not a regression"
    assert any("pre_existing_failure[mean]" in rule for rule in settlement.machine_rules)


def test_baseline_pass_candidate_fail_records_a_new_regression(workspace):
    snapshot = default_snapshot(baseline_mean=15.0)     # baseline already satisfied
    runner, _, _ = build_bet(workspace, snapshot=snapshot, config=make_config(threshold=12.0),
                             executor=ScriptedExecutor(workspace=workspace, values=[11.0]))
    settlement = runner.run().settlement
    assert settlement.new_regressions == ("mean",)
    assert settlement.pre_existing_failures == ()
    assert settlement.settlement_class != "supported"
    assert settlement.baseline_already_satisfied is True
    assert settlement.improvement_over_baseline is False
    assert settlement.publish_eligible is False
    assert "new_regression:mean" in settlement.publish_blockers


def test_expectation_met_but_publish_gate_fails_keeps_the_two_apart(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    config = make_config(threshold=12.0, extra_publish_reasons=("requires_human_signoff",))
    runner, _, _ = build_bet(workspace, snapshot=snapshot, config=config,
                             executor=ScriptedExecutor(workspace=workspace, values=[13.0]))
    settlement = runner.run().settlement
    assert settlement.improvement_over_baseline is True, "the expectation did move"
    assert settlement.settlement_class == "supported"
    assert settlement.publish_eligible is False, "improvement and permission are different fields"
    assert "requires_human_signoff" in settlement.publish_blockers


def test_llm_explanation_can_never_be_authoritative():
    with pytest.raises(M.ContractError, match="llm_explanation"):
        M.SettlementDecision(
            settlement_id="s", bet_id="b", settlement_class="inconclusive",
            expectation_outcomes=(),
            comparison=_unmatched_proof(),
            new_regressions=(), pre_existing_failures=(),
            expectations_met=False, improvement_over_baseline=False,
            baseline_already_satisfied=False, supported_claim="none",
            environment="synthetic_fixture", improvement_observed=False,
            publish_eligible=False, publish_blockers=(), next_state="CLOSED",
            machine_rules=("r",), llm_explanation="trust me, it worked",
            llm_explanation_authoritative=True)


def _unmatched_proof():
    return M.ComparisonProof(
        inputs_identical=False, evaluator_identical=False, protocol_identical=False,
        budget_comparable=False, environment_identical=False, harness_version_identical=False,
        baseline_treatment="", candidate_treatment="", treatment_diff_hash="",
        expected_treatment_diff_hash="", baseline_code_version="", candidate_code_version="",
        baseline_value=None, candidate_value=None, delta=None)


def test_incompatible_baseline_evidence_makes_the_settlement_inconclusive(workspace):
    """A baseline measured in another environment is not a comparable baseline."""
    snapshot = default_snapshot(baseline_mean=5.0)
    config = make_config(threshold=12.0)
    provider = ScriptedBaselineProvider(
        workspace=workspace, baseline_values=[5.0, 20.0], evaluator=__import__(
            "conftest", fromlist=["make_evaluator"]).make_evaluator(),
        environment="shadow", fingerprint="envfp:other")
    runner, _, _ = build_bet(workspace, snapshot=snapshot, config=config,
                             executor=ScriptedExecutor(workspace=workspace, values=[13.0]),
                             baseline_provider=provider)
    settlement = runner.run().settlement
    assert settlement.settlement_class == "inconclusive"
    assert settlement.publish_eligible is False
    assert "baseline_evidence_incompatible" in settlement.publish_blockers
    assert any("baseline_environment" in reason for reason in settlement.publish_blockers)
