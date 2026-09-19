"""Legacy (commitment/1) publish safety.

The bug this file guards against: v1 deserialisation inferred an environment from
the record's own ``publish_eligible`` flag, so an old *isolated* experiment that
had claimed ``publish_eligible=true`` was read back as ``production`` -- i.e. the
untrustworthy claim was converted into current publication authority.

The rules now enforced at the data-contract layer:

* a v1 record with no ``environment`` becomes ``legacy_unknown``, never
  ``production`` / ``production_canary``;
* ``schema_legacy`` is an unbypassable publication/promotion blocker
  (``legacy_schema_untrusted``);
* the original claim survives only as ``legacy_publish_claim`` for audit;
* a v1 comparison proof has no baseline evidence, so it is never ``matched``;
* legacy settlements cannot mint experiences;
* history is read-only: bytes, hashes and the hash chain are untouched.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conftest import build_bet, default_snapshot, publishable_config
from partner.commitment import models as M
from partner.commitment.settlement import build_experience


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _v1_proof(**overrides) -> dict:
    payload = {"schema_version": "commitment/1", "inputs_identical": True,
               "evaluator_identical": True, "protocol_identical": True,
               "budget_identical": True, "code_version_identical": True,
               "baseline_value": 5.0, "candidate_value": 13.0, "delta": 8.0, "matched": True}
    payload.update(overrides)
    return payload


def _v1_settlement(*, publish_eligible: bool, comparison: dict | None = None,
                   settlement_class: str = "supported") -> dict:
    return {
        "schema_version": "commitment/1", "settlement_id": "stl_legacy_1",
        "bet_id": "bet_legacy_1", "settlement_class": settlement_class,
        "expectation_outcomes": [], "comparison": comparison or _v1_proof(),
        "new_regressions": [], "pre_existing_failures": [],
        "improvement_observed": True, "publish_eligible": publish_eligible,
        "publish_blockers": [], "next_state": "CLOSED", "machine_rules": ["legacy rule"],
        "llm_explanation": "", "llm_explanation_authoritative": False,
        "created_at": "2026-09-18T00:00:00",
    }


# ---------------------------------------------------------------------------
# the reported bug
# ---------------------------------------------------------------------------

def test_v1_with_a_publish_claim_never_becomes_production():
    decision = M.SettlementDecision.from_dict(_v1_settlement(publish_eligible=True))
    assert decision.schema_legacy is True
    assert decision.environment == "legacy_unknown", "must not be inferred as production"
    assert decision.environment not in ("production", "production_canary")
    assert decision.publish_eligible is False, "an old claim is not current authority"
    assert decision.legacy_publish_claim is True, "the original claim stays auditable"
    assert M.BLOCKER_LEGACY_SCHEMA_UNTRUSTED in decision.publish_blockers
    body = decision.to_dict()
    assert body["schema_version"] == M.SCHEMA_VERSION, "reading must not emit v1"
    assert body["legacy_publish_claim"] is True
    assert body["publish_eligible"] is False


def test_v1_without_a_publish_claim_behaves_the_same_way():
    decision = M.SettlementDecision.from_dict(_v1_settlement(publish_eligible=False))
    assert decision.environment == "legacy_unknown"
    assert decision.publish_eligible is False
    assert decision.legacy_publish_claim is False
    assert M.BLOCKER_LEGACY_SCHEMA_UNTRUSTED in decision.publish_blockers


def test_a_legacy_record_can_never_be_constructed_as_publishable():
    """The gate is in the contract, not only in the reader."""
    with pytest.raises(M.ContractError, match="legacy"):
        M.SettlementDecision(
            settlement_id="s", bet_id="b", settlement_class="supported",
            expectation_outcomes=(), comparison=M.ComparisonProof.from_dict(_v1_proof()),
            new_regressions=(), pre_existing_failures=(), expectations_met=True,
            improvement_over_baseline=True, baseline_already_satisfied=False,
            supported_claim="improvement_over_baseline", environment="legacy_unknown",
            improvement_observed=True, publish_eligible=True, publish_blockers=(),
            next_state="CLOSED", machine_rules=("r",), schema_legacy=True)


def test_a_legacy_bet_record_is_never_reinterpreted_as_production():
    v1_bet = {
        "schema_version": "commitment/1", "bet_id": "bet_legacy_1", "partner_id": "p",
        "project_id": "proj", "run_id": "run", "question": "q",
        "context_snapshot_ref": "context/snapshot.json", "context_snapshot_hash": "h",
        "candidates": [{"schema_version": "commitment/1", "candidate_id": "c1",
                        "description": "d", "params": {}, "proposed_by": "policy",
                        "rationale": "r"}],
        "selected_action": "c1", "rejected_alternatives": [], "selection_reason": "only option",
        "expected_effects": [{"schema_version": "commitment/1", "metric": "mean",
                              "direction": "increase", "threshold": 1.0}],
        "falsification_conditions": [{"schema_version": "commitment/1", "code": "c",
                                      "kind": "metric_violation", "description": "d",
                                      "params": {"metric": "mean"}}],
        "evaluation_protocol": {"schema_version": "commitment/1", "evaluator_id": "e",
                                "evaluator_version": "1", "metric_specs": [{"metric": "mean"}]},
        "baseline_ref": "snapshot", "budget": M.Budget.create(
            wall_clock_seconds=10, model_calls=0, actions=1, rounds=1,
            started_epoch=1.0).to_dict(),
        "commitment_policy": M.CommitmentPolicy(earliest_turn_round=1, max_turns=1).to_dict(),
        "code_version": "v1", "data_version": "d1", "model_config_ref": "none",
        "status": "CLOSED", "revision": 1, "parent_revision": 0,
        "created_at": "", "updated_at": "",
    }
    record = M.BetRecord.from_dict(v1_bet)
    assert record.environment == "legacy_unknown"
    assert record.environment not in ("production", "production_canary")
    assert record.treatment is None, "no treatment contract existed in v1; none is invented"
    assert record.to_dict()["schema_version"] == M.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# a legacy proof is not a matched comparison
# ---------------------------------------------------------------------------

def test_a_v1_proof_without_baseline_evidence_is_never_matched():
    proof = M.ComparisonProof.from_dict(_v1_proof())
    assert proof.legacy_untrusted is True
    assert proof.matched is False, "no baseline evidence reference exists in v1"
    assert proof.baseline_evidence_ref == ""
    # and a legacy settlement therefore cannot be 'supported' on it
    decision = M.SettlementDecision.from_dict(_v1_settlement(publish_eligible=False))
    assert decision.comparison.matched is False


# ---------------------------------------------------------------------------
# legacy settlements cannot be promoted
# ---------------------------------------------------------------------------

def test_a_legacy_settlement_cannot_mint_an_experience():
    decision = M.SettlementDecision.from_dict(_v1_settlement(publish_eligible=True))
    bet = M.BetRecord.from_dict({
        "schema_version": "commitment/1", "bet_id": "bet_legacy_1", "partner_id": "p",
        "project_id": "proj", "run_id": "run", "question": "q",
        "context_snapshot_ref": "c", "context_snapshot_hash": "h",
        "candidates": [{"schema_version": "commitment/1", "candidate_id": "c1",
                        "description": "d", "params": {}, "proposed_by": "policy",
                        "rationale": "r"}],
        "selected_action": "c1", "rejected_alternatives": [], "selection_reason": "s",
        "expected_effects": [{"schema_version": "commitment/1", "metric": "mean",
                              "direction": "increase", "threshold": 1.0}],
        "falsification_conditions": [{"schema_version": "commitment/1", "code": "c",
                                      "kind": "metric_violation", "description": "d",
                                      "params": {"metric": "mean"}}],
        "evaluation_protocol": {"schema_version": "commitment/1", "evaluator_id": "e",
                                "evaluator_version": "1", "metric_specs": [{"metric": "mean"}]},
        "baseline_ref": "b", "budget": M.Budget.create(wall_clock_seconds=10, model_calls=0,
                                                       actions=1, rounds=1, started_epoch=1.0).to_dict(),
        "commitment_policy": M.CommitmentPolicy(earliest_turn_round=1, max_turns=1).to_dict(),
        "code_version": "v1", "data_version": "d1", "model_config_ref": "none"})
    receipt = M.ExecutionReceipt(
        receipt_id="r", bet_id="bet_legacy_1", requested_action="c1", executed_action="c1",
        executor_id="e", executor_version="1", started_epoch=1.0, finished_epoch=1.1,
        status="completed", exit_code=0, artifacts=("a.json",),
        artifact_hashes={"a.json": "0" * 64}, log_ref="", data_hash="",
        budget_consumed={"actions": 1}, human_intervention=False, idempotency_key="i")
    with pytest.raises(M.ContractError, match="legacy"):
        build_experience(settlement=decision, bet=bet, receipt=receipt)


def test_an_inconclusive_experience_is_not_promotable():
    record = M.ExperienceRecord(
        experience_id="e", bet_id="b", run_id="r", settlement_ref="s",
        settlement_class="inconclusive", state_action_outcome_chain=("a",),
        scope="x", confidence=0.5, evidence_refs=("s",))
    with pytest.raises(M.ContractError, match="not promotable"):
        record.assert_promotable()


# ---------------------------------------------------------------------------
# the v2 path is unchanged
# ---------------------------------------------------------------------------

def test_v2_canary_still_publishes_when_every_gate_is_met(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    config = publishable_config(threshold=12.0, replicates=2)
    runner, _, _ = build_bet(workspace, snapshot=snapshot, config=config)
    settlement = runner.run().settlement
    assert settlement.schema_legacy is False
    assert settlement.environment == "production_canary"
    assert settlement.publish_eligible is True, settlement.publish_blockers
    assert settlement.legacy_publish_claim is False
    assert M.BLOCKER_LEGACY_SCHEMA_UNTRUSTED not in settlement.publish_blockers
    assert settlement.expectation_outcomes[0].met is True


def test_v2_production_environment_is_still_distinct_from_legacy():
    assert "production" in M.EXECUTION_ENVIRONMENTS
    assert "legacy_unknown" in M.EXECUTION_ENVIRONMENTS
    assert "legacy_unknown" in M.NON_PUBLISHABLE_ENVIRONMENTS
    assert "production" not in M.NON_PUBLISHABLE_ENVIRONMENTS


# ---------------------------------------------------------------------------
# history is read-only
# ---------------------------------------------------------------------------

def test_reading_legacy_history_changes_no_bytes_and_no_chain(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    runner, store, _ = build_bet(workspace, snapshot=snapshot,
                                 config=publishable_config(threshold=12.0))
    runner.run()
    # plant a v1 artifact inside the real store, then read it through the store
    legacy_path = store.artifact_path("settlement", "stl_legacy_planted")
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps(_v1_settlement(publish_eligible=True), indent=2),
                           encoding="utf-8")
    before_hash, before_mtime = _sha(legacy_path), legacy_path.stat().st_mtime_ns
    chain_before = store.verify_chain()

    loaded = store.load_settlement("stl_legacy_planted")

    assert loaded.schema_legacy is True
    assert loaded.publish_eligible is False
    assert _sha(legacy_path) == before_hash, "reading history must not rewrite it"
    assert legacy_path.stat().st_mtime_ns == before_mtime
    assert store.verify_chain().head_hash == chain_before.head_hash
    assert store.verify_chain().ok is True
    # the append-only chain gained nothing from a read
    assert store.load_settlement("stl_legacy_planted").legacy_publish_claim is True
