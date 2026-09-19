"""Run-level metadata: a run's positioning is not the settlement's verdict."""
from __future__ import annotations

import json

import pytest

from conftest import build_bet, default_snapshot, publishable_config
from partner.commitment import models as M
from partner.commitment.store import CommitmentStore, RUN_KINDS, run_metadata


def test_infrastructure_canary_can_never_claim_acceptance():
    meta = run_metadata(run_kind="infrastructure_canary", instance_executed=False,
                        project_advancement=False, replicates_independent=False,
                        executed_by="hermes", owner_instance="02")
    assert meta["production_canary_acceptance"] is False
    assert meta["instance_executed"] is False
    assert meta["project_advancement"] is False
    assert meta["replicates_independent"] is False


def test_acceptance_is_derived_from_execution_independence_and_advancement():
    full = run_metadata(run_kind="production_canary", instance_executed=True,
                        project_advancement=True, replicates_independent=True,
                        executed_by="instance:02")
    assert full["production_canary_acceptance"] is True
    for missing in ("instance_executed", "project_advancement", "replicates_independent"):
        kwargs = {"run_kind": "production_canary", "instance_executed": True,
                  "project_advancement": True, "replicates_independent": True,
                  "executed_by": "instance:02"}
        kwargs[missing] = False
        assert run_metadata(**kwargs)["production_canary_acceptance"] is False, missing
    # a non-production kind is never acceptance, even when everything else holds
    assert run_metadata(run_kind="instance_canary", instance_executed=True,
                        project_advancement=True, replicates_independent=True,
                        executed_by="instance:02")["production_canary_acceptance"] is False


def test_an_unknown_run_kind_is_refused():
    with pytest.raises(M.ContractError, match="run_kind"):
        run_metadata(run_kind="looks_great", instance_executed=False,
                     project_advancement=False, replicates_independent=False,
                     executed_by="x")
    assert "looks_great" not in RUN_KINDS


def test_the_store_records_metadata_without_touching_the_settlement(workspace):
    snapshot = default_snapshot(baseline_mean=5.0)
    runner, store, _ = build_bet(workspace, snapshot=snapshot,
                                 config=publishable_config(threshold=12.0))
    result = runner.run()
    settlement_path = store.artifact_path("settlement", result.settlement.settlement_id)
    before_bytes = settlement_path.read_bytes()
    chain_before = store.verify_chain()

    meta = run_metadata(run_kind="infrastructure_canary", instance_executed=False,
                        project_advancement=False, replicates_independent=False,
                        executed_by="hermes", owner_instance="02",
                        notes="first molecular canary: kernel-invoked, not instance-executed")
    path = store.write_run_metadata(meta)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["run_kind"] == "infrastructure_canary"
    # the original machine verdict is byte-identical
    assert settlement_path.read_bytes() == before_bytes
    assert store.load_settlement(result.settlement.settlement_id).publish_eligible         == result.settlement.publish_eligible
    # the event chain gained nothing: metadata is not an event
    assert store.verify_chain().head_hash == chain_before.head_hash
    manifest = json.loads(store.path("manifest.json").read_text(encoding="utf-8"))
    assert manifest["extra"]["run_metadata"]["run_kind"] == "infrastructure_canary"
    assert manifest["extra"]["run_metadata_ref"] == "run_metadata.json"
    assert "run_metadata.json" in manifest["artifacts"], "it must also be hashed as an artifact"


def test_asserting_acceptance_that_was_not_earned_is_refused(workspace):
    store = CommitmentStore(workspace, "run_meta_guard", "bet_meta_guard")
    bad = {"run_kind": "infrastructure_canary", "instance_executed": False,
           "project_advancement": False, "replicates_independent": False,
           "production_canary_acceptance": True}
    with pytest.raises(M.ContractError, match="derived"):
        store.write_run_metadata(bad)
    with pytest.raises(M.ContractError, match="must be a bool"):
        store.write_run_metadata({"run_kind": "infrastructure_canary",
                                  "instance_executed": "yes", "project_advancement": False,
                                  "replicates_independent": False,
                                  "production_canary_acceptance": False})
