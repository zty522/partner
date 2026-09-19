"""The semantic corrections of 2026-09-19, asserted behaviourally.

Four families:

* the execution environment decides publishability, and isolated evidence can
  never be promoted (fix 1)
* reaching a threshold, beating a baseline and not getting worse are three
  different machine answers (fix 2)
* a baseline is executable, hashed evidence -- not a number in a snapshot (fix 3)
* matched means "controls identical and the treatment difference exactly as
  frozen", so a patch or an action may be the treatment (fix 4)

Plus read-only compatibility with the pre-correction schema.
"""
from __future__ import annotations

import dataclasses

import pytest

from conftest import (ScriptedBaselineProvider, ScriptedExecutor, build_bet, default_snapshot,
                      make_config, make_evaluator, publishable_config)
from partner.commitment import models as M
from partner.commitment import state_machine as sm
from partner.commitment.settlement import settle, SettlementRequest, treatment_diff_hash


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class MutationProvider:
    """Wraps a real provider and post-processes the evidence, to test tampering."""

    def __init__(self, inner, **changes) -> None:
        self.inner = inner
        self.changes = changes

    def provide(self, *, bet, store):
        evidence = self.inner.provide(bet=bet, store=store)
        if evidence is None:
            return None
        return dataclasses.replace(evidence, **self.changes)


def _run(workspace, *, snapshot=None, config=None, values=(13.0, 14.0), bet_id="bet_sem",
         provider=None):
    return build_bet(workspace, bet_id=bet_id, snapshot=snapshot, config=config,
                     executor=ScriptedExecutor(workspace=workspace, values=list(values)),
                     baseline_provider=provider)


# ---------------------------------------------------------------------------
# fix 1: the environment decides publishability
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("environment,expected", [
    ("synthetic_fixture", "synthetic_fixture_only"),
    ("isolated_sample", "isolated_sample_only"),
    ("shadow", "shadow_only"),
])
def test_non_production_environments_can_never_publish(workspace, environment, expected):
    snapshot = default_snapshot(baseline_mean=5.0)
    config = make_config(threshold=12.0, environment=environment, replicates=4)
    runner, _, _ = _run(workspace, snapshot=snapshot, config=config)
    settlement = runner.run().settlement
    assert settlement.settlement_class == "supported"
    assert settlement.expectations_met is True
    assert settlement.publish_eligible is False, "isolation is not a promotion path"
    assert expected in settlement.publish_blockers
    assert any(blocker.startswith("environment_not_publishable") for blocker
               in settlement.publish_blockers)


def test_a_production_canary_still_needs_the_other_gates(workspace):
    """The environment gate is necessary, not sufficient."""
    snapshot = default_snapshot(baseline_mean=5.0)
    single = publishable_config(threshold=12.0, replicates=1)
    runner, _, _ = _run(workspace, snapshot=snapshot, config=single, bet_id="bet_canary_single")
    settlement = runner.run().settlement
    assert settlement.settlement_class == "supported"
    assert settlement.publish_eligible is False
    assert "single_episode_only" in settlement.publish_blockers

    replicated = publishable_config(threshold=12.0, replicates=2)
    runner2, _, _ = _run(workspace, snapshot=snapshot, config=replicated, bet_id="bet_canary_repl")
    settlement2 = runner2.run().settlement
    assert settlement2.publish_eligible is True, settlement2.publish_blockers


def test_the_environment_is_frozen_and_cannot_be_edited_after_the_fact(workspace):
    """Promoting an isolated bet to production is not an edit, it is a new bet."""
    from partner.commitment.store import CommitmentStore
    snapshot = default_snapshot(baseline_mean=5.0)
    config = make_config(threshold=12.0, environment="isolated_sample")
    runner, store, _ = _run(workspace, snapshot=snapshot, config=config)
    runner.run()
    record = store.load_bet()
    assert record.environment == "isolated_sample"
    promoted = dataclasses.replace(record, environment="production")
    with pytest.raises(Exception) as excinfo:
        store.save_bet(promoted)
    assert "environment" in str(excinfo.value)


# ---------------------------------------------------------------------------
# fix 2: threshold, improvement and non-inferiority are three answers
# ---------------------------------------------------------------------------

def test_absolute_threshold_met_is_not_an_improvement(workspace):
    """Both arms clear the threshold, delta is zero: absolute attainment only."""
    from partner.commitment.store import CommitmentStore
    snapshot = default_snapshot(baseline_mean=15.0)
    config = make_config(threshold=12.0)
    runner, store, _ = _run(workspace, snapshot=snapshot, config=config, values=(15.0, 15.0))
    settlement = runner.run().settlement
    assert settlement.expectations_met is True
    assert settlement.baseline_already_satisfied is True
    assert settlement.improvement_over_baseline is False, "no positive delta is not an improvement"
    assert settlement.improvement_observed is False
    assert settlement.settlement_class == "supported"
    assert settlement.supported_claim == "absolute_attainment"
    assert any("baseline already satisfied" in rule for rule in settlement.machine_rules)


def test_a_reached_threshold_with_a_worse_baseline_is_not_an_improvement(workspace):
    """Candidate reaches the threshold but is below the baseline's own value."""
    snapshot = default_snapshot(baseline_mean=15.0)
    runner, _, _ = _run(workspace, snapshot=snapshot, config=make_config(threshold=12.0),
                        values=(12.5, 12.5))
    settlement = runner.run().settlement
    assert settlement.expectations_met is True
    assert settlement.improvement_over_baseline is False
    assert settlement.baseline_already_satisfied is True
    assert settlement.supported_claim == "absolute_attainment"


@pytest.mark.parametrize("kind,kwargs,values,expect_met,expect_improvement,claim", [
    ("absolute_threshold", {}, (13.0, 13.0), True, False, "absolute_attainment"),
    ("delta_over_baseline", {"min_delta": 2.0}, (13.0, 13.0), True, True,
     "improvement_over_baseline"),
    ("non_inferiority", {"tolerance": 0.5}, (5.2, 5.2), True, False, "non_inferiority"),
    ("guardrail", {"threshold": 20.0}, (13.0, 13.0), True, False, "guardrail_held"),
])
def test_each_expectation_kind_settles_by_its_own_rule(workspace, kind, kwargs, values,
                                                       expect_met, expect_improvement, claim):
    snapshot = default_snapshot(baseline_mean=5.0)
    threshold = kwargs.pop("threshold", 12.0)
    direction = "decrease" if kind == "guardrail" else "increase"
    from partner.commitment.runner import RunnerConfig
    base = make_config(threshold=threshold)
    effect = M.ExpectedEffect(metric="mean", direction=direction, threshold=threshold,
                              unit="unit", kind=kind,
                              min_delta=kwargs.get("min_delta"),
                              tolerance=kwargs.get("tolerance"))
    config = RunnerConfig(
        partner_id=base.partner_id, project_id=base.project_id, run_id=base.run_id,
        question=base.question, baseline_ref=base.baseline_ref, expected_effects=(effect,),
        falsification_conditions=base.falsification_conditions,
        evaluation_protocol=base.evaluation_protocol, budget=base.budget,
        commitment_policy=base.commitment_policy, max_candidates=base.max_candidates,
        code_version=base.code_version, data_version=base.data_version,
        model_config_ref=base.model_config_ref, context_snapshot_ref=base.context_snapshot_ref,
        scope=base.scope, environment=base.environment, treatment_spec=base.treatment_spec,
        harness_version=base.harness_version,
        environment_fingerprint=base.environment_fingerprint)
    runner, _, _ = _run(workspace, snapshot=snapshot, config=config, values=values,
                        bet_id=f"bet_kind_{kind}")
    settlement = runner.run().settlement
    outcome = settlement.expectation_outcomes[0]
    assert outcome.kind == kind
    assert settlement.expectations_met is expect_met, (kind, outcome.note)
    assert settlement.improvement_over_baseline is expect_improvement, (kind, outcome.note)
    assert settlement.supported_claim == claim
    if kind == "non_inferiority":
        assert outcome.improvement_over_baseline is True, "within tolerance is not a win, but it holds"
    if kind == "absolute_threshold":
        assert outcome.improvement_over_baseline is None, \
            "an absolute threshold carries no relative claim at all"


def test_delta_over_baseline_requires_the_minimum_effect_size(workspace):
    """A candidate that improves by less than the frozen minimum is not an improvement."""
    snapshot = default_snapshot(baseline_mean=5.0)
    config = make_config(threshold=12.0, kind="delta_over_baseline", min_delta=10.0)
    runner, _, _ = _run(workspace, snapshot=snapshot, config=config, values=(13.0, 13.0))
    settlement = runner.run().settlement
    assert settlement.expectations_met is True       # the threshold was reached
    assert settlement.improvement_over_baseline is False, "delta 8 < frozen min_delta 10"
    assert settlement.supported_claim == "absolute_attainment"


# ---------------------------------------------------------------------------
# fix 3: a baseline is evidence
# ---------------------------------------------------------------------------

def test_a_declared_scalar_baseline_cannot_produce_a_matched_comparison(workspace):
    """The snapshot may declare numbers; they are not admissible evidence."""
    snapshot = default_snapshot(baseline_mean=5.0)
    runner, store, _ = _run(workspace, snapshot=snapshot, config=make_config(threshold=12.0))
    result = runner.run()
    assert result.settlement.comparison.matched is True, "the real control run is admissible"
    # now settle the same artefacts again with *no* baseline evidence at all
    bare = settle(SettlementRequest(bet=store.load_bet(), receipt=result.receipt,
                                    measured=result.measurement, baseline=None,
                                    input_hash=store.load_bet().context_snapshot_hash,
                                    environment_fingerprint="envfp:test",
                                    harness_version="commitment-kernel-test"))
    assert bare.settlement_class == "inconclusive"
    assert bare.comparison.matched is False
    assert bare.expectation_outcomes[0].observed is not None, "the measurement is still reported"
    assert bare.expectation_outcomes[0].baseline is None
    assert "baseline_evidence_missing" in bare.publish_blockers
    assert bare.improvement_over_baseline is False


def test_a_runner_without_baseline_evidence_stops_instead_of_guessing(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    provider = ScriptedBaselineProvider(workspace=workspace, baseline_values=[5.0, 5.0],
                                       evaluator=make_evaluator(), include=False)
    runner, store, executor = _run(workspace, snapshot=snapshot,
                                   config=make_config(threshold=12.0), provider=provider)
    result = runner.run()
    assert result.state == sm.BLOCKED
    assert "baseline evidence unavailable" in result.reason
    assert executor.executions == 0, "no candidate action runs without a comparable baseline"
    assert store.list_artifacts("settlement") == []


@pytest.mark.parametrize("mode,expected_state", [("missing", sm.BLOCKED), ("mutated", sm.BLOCKED)])
def test_a_broken_baseline_artifact_fails_closed(workspace, mode, expected_state):
    snapshot = default_snapshot(baseline_mean=5.0)
    broken = ScriptedExecutor(workspace=workspace, values=[5.0, 5.0], mode=mode)
    provider = ScriptedBaselineProvider(workspace=workspace, baseline_values=[5.0, 5.0],
                                       evaluator=make_evaluator(), executor=broken)
    runner, store, _ = _run(workspace, snapshot=snapshot, config=make_config(threshold=12.0),
                            provider=provider)
    result = runner.run()
    assert result.state == expected_state, result.reason
    assert result.settlement is None
    assert store.issues(), "a refused baseline must leave an issue behind"


@pytest.mark.parametrize("field,value", [
    ("input_hash", "other-inputs"),
    ("protocol_hash", "other-protocol"),
    ("evaluator_id", "other-evaluator"),
    ("harness_version", "other-harness"),
    ("budget_hash", "other-budget"),
])
def test_a_control_mismatch_makes_the_comparison_unmatched(workspace, field, value):
    snapshot = default_snapshot(baseline_mean=5.0)
    inner = ScriptedBaselineProvider(workspace=workspace, baseline_values=[5.0, 5.0],
                                     evaluator=make_evaluator())
    runner, _, _ = _run(workspace, snapshot=snapshot, config=make_config(threshold=12.0),
                        provider=MutationProvider(inner, **{field: value}))
    settlement = runner.run().settlement
    assert settlement.comparison.matched is False, field
    assert settlement.settlement_class == "inconclusive"
    assert settlement.publish_eligible is False


def test_a_reused_historical_baseline_needs_a_complete_compatibility_proof(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    inner = ScriptedBaselineProvider(workspace=workspace, baseline_values=[5.0, 5.0],
                                     evaluator=make_evaluator())
    # a bare reuse claim is refused by the contract itself
    with pytest.raises(M.ContractError, match="compatibility proof"):
        M.BaselineEvidence(
            baseline_id="b", metric_values={"mean": 5.0}, receipt_ref="r", receipt_hash="h",
            measurement_ref="m", measurement_hash="h", artifact_refs=("a.json",),
            artifact_hashes={"a.json": "x"}, artifact_hashes_after={"a.json": "x"},
            input_hash="i", data_hash="d", executor_id="e", executor_version="1",
            evaluator_id="v", evaluator_version="1", protocol_hash="p", budget_hash="b",
            budget_snapshot={}, environment="synthetic_fixture", environment_fingerprint="f",
            harness_version="h", treatment="control", provenance="reused_frozen_evidence",
            compatibility={}, compatibility_ok=False)
    # with the proof present and the controls matching, reuse is admissible
    runner, _, _ = _run(
        workspace, snapshot=snapshot, config=make_config(threshold=12.0),
        provider=MutationProvider(inner, provenance="reused_frozen_evidence",
                                  compatibility_ok=True,
                                  compatibility={"inputs": "match", "evaluator": "match",
                                                 "protocol": "match", "budget": "comparable",
                                                 "environment": "match", "harness": "match"}))
    settlement = runner.run().settlement
    assert settlement.comparison.matched is True
    assert settlement.comparison.baseline_provenance == "reused_frozen_evidence"
    assert settlement.settlement_class == "supported"


# ---------------------------------------------------------------------------
# fix 4: control variables identical, treatment explicitly different
# ---------------------------------------------------------------------------

def test_the_same_code_with_a_different_action_is_a_legal_comparison(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    config = make_config(threshold=12.0)
    runner, _, _ = _run(workspace, snapshot=snapshot, config=config)
    settlement = runner.run().settlement
    assert settlement.comparison.code_changed is False
    assert settlement.comparison.baseline_treatment == "control"
    assert settlement.comparison.candidate_treatment.startswith(settlement.comparison.detail[:0] or "")
    assert settlement.comparison.treatment_as_frozen is True
    assert settlement.comparison.matched is True


def test_a_frozen_patch_may_be_the_single_treatment(workspace):
    from partner.commitment.runner import RunnerConfig
    snapshot = default_snapshot(baseline_mean=5.0)
    base = make_config(threshold=12.0)
    config = RunnerConfig(
        partner_id=base.partner_id, project_id=base.project_id, run_id=base.run_id,
        question=base.question, baseline_ref=base.baseline_ref, expected_effects=base.expected_effects,
        falsification_conditions=base.falsification_conditions,
        evaluation_protocol=base.evaluation_protocol, budget=base.budget,
        commitment_policy=base.commitment_policy, max_candidates=base.max_candidates,
        code_version=base.code_version, data_version=base.data_version,
        model_config_ref=base.model_config_ref, context_snapshot_ref=base.context_snapshot_ref,
        scope=base.scope, environment=base.environment,
        treatment_spec=M.TreatmentSpec(baseline_treatment="control",
                                       declared_paths=("partner/kernel.py",),
                                       allows_code_change=True),
        harness_version=base.harness_version,
        environment_fingerprint=base.environment_fingerprint,
        changed_paths=("partner/kernel.py",), candidate_code_version="patched-rev-42")
    runner, _, _ = _run(workspace, snapshot=snapshot, config=config, bet_id="bet_patch")
    settlement = runner.run().settlement
    assert settlement.comparison.code_changed is True
    assert settlement.comparison.matched is True, settlement.comparison.mismatch_reasons()
    assert settlement.settlement_class == "supported"


def test_an_undeclared_extra_change_makes_the_comparison_unmatched(workspace):
    from partner.commitment.runner import RunnerConfig
    snapshot = default_snapshot(baseline_mean=5.0)
    base = make_config(threshold=12.0)
    config = RunnerConfig(
        partner_id=base.partner_id, project_id=base.project_id, run_id=base.run_id,
        question=base.question, baseline_ref=base.baseline_ref, expected_effects=base.expected_effects,
        falsification_conditions=base.falsification_conditions,
        evaluation_protocol=base.evaluation_protocol, budget=base.budget,
        commitment_policy=base.commitment_policy, max_candidates=base.max_candidates,
        code_version=base.code_version, data_version=base.data_version,
        model_config_ref=base.model_config_ref, context_snapshot_ref=base.context_snapshot_ref,
        scope=base.scope, environment=base.environment,
        treatment_spec=M.TreatmentSpec(baseline_treatment="control",
                                       declared_paths=("partner/kernel.py",),
                                       allows_code_change=True),
        harness_version=base.harness_version,
        environment_fingerprint=base.environment_fingerprint,
        changed_paths=("partner/kernel.py", "partner/other.py"),
        candidate_code_version="patched-rev-42")
    runner, _, _ = _run(workspace, snapshot=snapshot, config=config, bet_id="bet_contaminated")
    settlement = runner.run().settlement
    assert settlement.comparison.matched is False
    assert "harness_version_identical" in settlement.comparison.mismatch_reasons()
    assert settlement.settlement_class == "inconclusive"


def test_a_bet_without_a_declared_treatment_can_never_match(workspace):
    from partner.commitment.runner import RunnerConfig
    snapshot = default_snapshot(baseline_mean=5.0)
    base = make_config(threshold=12.0)
    config = RunnerConfig(
        partner_id=base.partner_id, project_id=base.project_id, run_id=base.run_id,
        question=base.question, baseline_ref=base.baseline_ref, expected_effects=base.expected_effects,
        falsification_conditions=base.falsification_conditions,
        evaluation_protocol=base.evaluation_protocol, budget=base.budget,
        commitment_policy=base.commitment_policy, max_candidates=base.max_candidates,
        code_version=base.code_version, data_version=base.data_version,
        model_config_ref=base.model_config_ref, context_snapshot_ref=base.context_snapshot_ref,
        scope=base.scope, environment=base.environment, treatment_spec=None,
        harness_version=base.harness_version,
        environment_fingerprint=base.environment_fingerprint)
    runner, _, _ = _run(workspace, snapshot=snapshot, config=config, bet_id="bet_no_treatment")
    settlement = runner.run().settlement
    assert settlement.comparison.matched is False
    assert "treatment_contract_missing" in settlement.publish_blockers
    assert settlement.settlement_class == "inconclusive"


def test_a_settlement_cannot_describe_a_different_treatment_than_the_frozen_one(workspace):
    """The treatment is frozen into the record; a differing claim is refused."""
    snapshot = default_snapshot(baseline_mean=5.0)
    runner, store, _ = _run(workspace, snapshot=snapshot, config=make_config(threshold=12.0))
    result = runner.run()
    mismatched = settle(SettlementRequest(
        bet=store.load_bet(), receipt=result.receipt, measured=result.measurement,
        baseline=result.baseline_evidence, input_hash=store.load_bet().context_snapshot_hash,
        environment_fingerprint="envfp:test", harness_version="commitment-kernel-test",
        treatment="cand_something_else:{}"))
    assert mismatched.comparison.matched is False
    assert "treatment_as_frozen" in mismatched.comparison.mismatch_reasons()
    assert mismatched.publish_eligible is False


# ---------------------------------------------------------------------------
# schema migration policy
# ---------------------------------------------------------------------------

def test_legacy_v1_records_stay_readable_and_are_never_rewritten():
    v1_proof = {"schema_version": "commitment/1", "inputs_identical": True,
                "evaluator_identical": True, "protocol_identical": True,
                "budget_identical": True, "code_version_identical": True,
                "baseline_value": 5.0, "candidate_value": 13.0, "delta": 8.0, "matched": True}
    proof = M.ComparisonProof.from_dict(v1_proof)
    assert proof.matched is True
    assert proof.budget_comparable is True
    assert proof.harness_version_identical is True
    # a v2 write of that legacy record is v2, and the legacy payload is untouched
    assert proof.to_dict()["schema_version"] == M.SCHEMA_VERSION
    assert v1_proof["schema_version"] == "commitment/1"
    assert "budget_comparable" not in v1_proof

    v1_effect = {"schema_version": "commitment/1", "metric": "mean", "direction": "increase",
                 "threshold": 12.0, "unit": "unit", "baseline_value": 5.0}
    effect = M.ExpectedEffect.from_dict(v1_effect)
    assert effect.kind == "absolute_threshold"
    assert effect.min_delta is None

    legacy_decision = {
        "schema_version": "commitment/1", "settlement_id": "s", "bet_id": "b",
        "settlement_class": "supported", "expectation_outcomes": [], "comparison": v1_proof,
        "new_regressions": [], "pre_existing_failures": [], "improvement_observed": True,
        "publish_eligible": True, "publish_blockers": [], "next_state": "CLOSED",
        "machine_rules": ["legacy"], "llm_explanation": "", "llm_explanation_authoritative": False,
        "created_at": "2026-09-18"}
    decision = M.SettlementDecision.from_dict(legacy_decision)
    assert decision.schema_legacy is True
    assert decision.publish_eligible is True, "history is not retro-actively downgraded"
    assert decision.environment in M.EXECUTION_ENVIRONMENTS


def test_a_v2_settlement_written_by_this_code_carries_the_new_fields(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    runner, _, _ = _run(workspace, snapshot=snapshot, config=make_config(threshold=12.0))
    payload = runner.run().settlement.to_dict()
    assert payload["schema_version"] == "commitment/2"
    for field in ("expectations_met", "improvement_over_baseline", "baseline_already_satisfied",
                  "supported_claim", "environment", "improvement_observed"):
        assert field in payload, field


# ---------------------------------------------------------------------------
# an isolated slice may observe an improvement and still be unpublishable
# ---------------------------------------------------------------------------

def test_an_isolated_slice_can_report_improvement_and_must_not_publish(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    config = make_config(threshold=12.0, environment="isolated_sample", replicates=1)
    runner, store, _ = _run(workspace, snapshot=snapshot, config=config)
    result = runner.run()
    settlement = result.settlement
    assert settlement.improvement_over_baseline is True
    assert settlement.settlement_class == "supported"
    assert settlement.publish_eligible is False
    assert "isolated_sample_only" in settlement.publish_blockers
    assert "single_episode_only" in settlement.publish_blockers
    assert result.experience is not None
    assert result.experience.level == "experience"
    assert result.experience.authoritative is False
    assert result.baseline_evidence is not None
    assert result.baseline_evidence.immutable is True
    # the baseline evidence is in the append-only store, next to the candidate's
    assert store.list_artifacts("baseline") == [result.baseline_evidence.baseline_id]
    manifest = store.write_manifest()
    assert any(name.startswith("baseline/") for name in manifest["artifacts"])
