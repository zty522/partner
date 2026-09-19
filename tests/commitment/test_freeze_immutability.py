"""Invariant 2: after COMMITTED the meaning of the bet cannot be edited in place."""
from __future__ import annotations

import json

import pytest

from partner.commitment import models as M
from partner.commitment.freezer import Freezer, FreezeRequest
from partner.commitment.selector import GuardedGainSelector, SelectionRefused
from partner.commitment.state_machine import IllegalTransition

from conftest import default_snapshot, make_config


def _freeze(workspace, *, threshold=12.0, question="q"):
    config = make_config(threshold=threshold, question=question)
    snapshot = default_snapshot()
    proposer_candidates = tuple(
        M.Candidate(candidate_id=e["candidate_id"], description=e["description"],
                    params=e["params"], proposed_by="policy")
        for e in snapshot["candidate_space"])
    selection = GuardedGainSelector(max_risk=0.5).select(candidates=proposer_candidates,
                                                         snapshot=snapshot)
    request = FreezeRequest(
        partner_id=config.partner_id, project_id=config.project_id, run_id=config.run_id,
        question=question, context_snapshot_ref=config.context_snapshot_ref,
        context_snapshot_hash="hash-of-snapshot", candidates=proposer_candidates,
        selection=selection, expected_effects=config.expected_effects,
        falsification_conditions=config.falsification_conditions,
        evaluation_protocol=config.evaluation_protocol, baseline_ref=config.baseline_ref,
        budget=config.budget, commitment_policy=config.commitment_policy,
        code_version=config.code_version, data_version=config.data_version,
        model_config_ref=config.model_config_ref)
    return Freezer().freeze(request, bet_id="bet_freeze", now_iso="2026-09-19T00:00:00+0800")


def test_expectation_metric_must_be_measurable():
    """An expectation the frozen protocol cannot measure is unfalsifiable."""
    record = _freeze(None)
    payload = record.to_dict()
    payload.pop("freeze_hash")
    payload["evaluation_protocol"] = M.EvaluationProtocol(
        evaluator_id="e", evaluator_version="1",
        metric_specs=({"metric": "something_else"},)).to_dict()
    broken = M.BetRecord.from_dict(payload)
    with pytest.raises(M.ContractError, match="not produced by the evaluation protocol"):
        Freezer.assert_falsifiable(broken)


def test_protocol_cannot_see_agent_output():
    with pytest.raises(M.ContractError, match="agent_output_visible"):
        M.EvaluationProtocol(evaluator_id="e", evaluator_version="1",
                             metric_specs=({"metric": "mean"},), agent_output_visible=True)


def test_freeze_hash_detects_post_hoc_editing():
    record = _freeze(None)
    payload = record.to_dict()
    payload["question"] = "a different, easier question"
    with pytest.raises(M.ContractError, match="freeze_hash"):
        M.BetRecord.from_dict(payload)


def test_store_refuses_in_place_semantic_edit(workspace):
    from partner.commitment.store import CommitmentStore
    record = _freeze(workspace)
    store = CommitmentStore(workspace, "run_test", "bet_freeze")
    store.save_bet(record)
    payload = record.to_dict()
    payload.pop("freeze_hash")
    payload["expected_effects"] = [M.ExpectedEffect(
        metric="mean", direction="increase", threshold=0.001, unit="unit").to_dict()]
    tampered = M.BetRecord.from_dict(payload)
    with pytest.raises(IllegalTransition, match="frozen field"):
        store.save_bet(tampered)
    assert store.load_bet().freeze_hash() == record.freeze_hash()
    assert [row["kind"] for row in store.issues()] == ["frozen_fields_edited"]


def test_new_revision_must_reference_its_parent(workspace):
    from partner.commitment.store import CommitmentStore
    record = _freeze(workspace)
    store = CommitmentStore(workspace, "run_test", "bet_freeze")
    store.save_bet(record)
    payload = record.to_dict(); payload.pop("freeze_hash")
    payload["revision"] = 3; payload["parent_revision"] = 1
    payload["expected_effects"] = [M.ExpectedEffect(metric="mean", direction="increase",
                                                    threshold=11.0, unit="unit").to_dict()]
    forward = M.BetRecord.from_dict(payload)
    with pytest.raises(IllegalTransition):
        store.save_bet(forward)
    payload2 = record.to_dict(); payload2.pop("freeze_hash")
    payload2["revision"] = 2; payload2["parent_revision"] = 0
    payload2["expected_effects"] = [M.ExpectedEffect(metric="mean", direction="increase",
                                                     threshold=11.0, unit="unit").to_dict()]
    with pytest.raises((IllegalTransition, M.ContractError), match="parent_revision"):
        M.BetRecord.from_dict(payload2)
    payload3 = record.to_dict(); payload3.pop("freeze_hash")
    payload3["revision"] = 2; payload3["parent_revision"] = 1
    payload3["expected_effects"] = [M.ExpectedEffect(metric="mean", direction="increase",
                                                     threshold=11.0, unit="unit").to_dict()]
    linked = M.BetRecord.from_dict(payload3)
    store.save_bet(linked)
    assert store.load_bet().revision == 2
    assert (store.path("revisions", "r1.json")).exists()
    assert (store.path("revisions", "r2.json")).exists()


def test_selection_refuses_when_no_prior_exists():
    snapshot = default_snapshot()
    snapshot["candidate_space"][0].pop("prior")
    candidates = tuple(M.Candidate(candidate_id=e["candidate_id"], description=e["description"],
                                   params=e["params"]) for e in snapshot["candidate_space"])
    with pytest.raises(SelectionRefused, match="declares no|no declared prior"):
        GuardedGainSelector().select(candidates=candidates, snapshot=snapshot)


def test_selection_reports_every_abandoned_alternative():
    snapshot = default_snapshot()
    candidates = tuple(M.Candidate(candidate_id=e["candidate_id"], description=e["description"],
                                   params=e["params"]) for e in snapshot["candidate_space"])
    result = GuardedGainSelector(max_risk=0.5).select(candidates=candidates, snapshot=snapshot)
    assert result.selected.candidate_id == "cand_large_gain"
    assert {r.candidate_id for r in result.rejected} == {"cand_small_gain", "cand_risky"}
    reasons = {r.candidate_id: r.reason for r in result.rejected}
    assert "risk guardrail" in reasons["cand_risky"]
    assert "lower declared expected gain" in reasons["cand_small_gain"]
