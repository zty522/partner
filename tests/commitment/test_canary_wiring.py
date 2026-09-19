"""Wiring contracts for the real-project canary.

The first real canary run was BLOCKED with ``PoolError: unknown selection method ''``
because the declared candidate space carried the action under a ``method`` key while
the proposers copy a candidate's action from ``params``.  The kernel was right to
refuse an empty action; the wiring was wrong.  These tests pin the contract.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "commitment_molecular_canary",
    REPO_ROOT / "scripts" / "run_commitment_molecular_canary.py")
canary = importlib.util.module_from_spec(_SPEC)
sys.modules["commitment_molecular_canary"] = canary
_SPEC.loader.exec_module(canary)

from partner.application.molecular_selection_adapter import (  # noqa: E402
    BASELINE_METHOD, PoolError, load_pool, select,
)


def test_every_declared_candidate_carries_its_action_in_params(tmp_path):
    snapshot = canary._snapshot(tmp_path, [{"group": "rule", "smiles": "c1ccccc1",
                                            "qed": 0.5, "sa": 1.0,
                                            "scaffold": "c1ccccc1"}], "fp")
    assert snapshot["candidate_space"], "a canary must declare candidates"
    for entry in snapshot["candidate_space"]:
        params = entry.get("params") or {}
        assert params.get("method"), f"{entry['candidate_id']} carries no action parameters"
        assert entry.get("prior"), f"{entry['candidate_id']} carries no declared prior"
    methods = {e["params"]["method"] for e in snapshot["candidate_space"]}
    assert canary.BASELINE_METHOD in methods or canary.BASELINE_METHOD == "rule"
    assert "scaffold_cap" in methods


def test_boundaries_and_frozen_limits_are_declared_before_any_measurement():
    spec = canary.build_spec(project_dir=Path("/tmp"),
                             pool_rows=[{"group": "rule", "smiles": "c1ccccc1",
                                         "qed": 0.5, "sa": 1.0, "scaffold": "c1ccccc1"}],
                             fingerprint="fp")
    assert spec["environment"] == "production_canary"
    assert spec["evaluation_protocol"]["replicates"] == len(canary.SEED_PROTOCOL) >= 2
    assert spec["budget"]["rounds"] == 1
    assert spec["commitment_policy"]["require_new_evidence_to_turn"] is True
    assert spec["treatment"]["allows_code_change"] is False
    kinds = {e["kind"] for e in spec["expected_effects"]}
    assert "delta_over_baseline" in kinds and "guardrail" in kinds
    delta = next(e for e in spec["expected_effects"] if e["kind"] == "delta_over_baseline")
    assert delta["min_delta"] > 0, "an improvement claim needs a minimum effect size"
    assert any(f["kind"] == "missing_evidence" for f in spec["falsification_conditions"])


def test_the_executor_refuses_to_guess_an_unknown_method():
    rows = [{"group": "rule", "smiles": "c1ccccc1", "qed": 0.5, "sa": 1.0,
             "scaffold": "c1ccccc1"}]
    with pytest.raises(PoolError, match="refusing to guess"):
        select(rows, method="", seed=1)
    with pytest.raises(PoolError, match="refusing to guess"):
        select(rows, method="not_a_project_method", seed=1)


def test_the_real_project_pool_loads_and_every_method_is_deterministic():
    project_dir = Path("/mnt/e/work/partner_workspace/share/projects/molecular_generation")
    if not project_dir.exists():
        pytest.skip("02 project not present on this machine")
    rows = load_pool(project_dir)
    assert len(rows) > 100, "the real comparison pool should be the project's own artifact"
    for method in ("rule", "scaffold_cap", "pareto_diverse", "maxmin_fingerprint",
                   "scaffold_round_robin"):
        first = select(rows, method=method, seed=canary.SEED_PROTOCOL[0])
        second = select(rows, method=method, seed=canary.SEED_PROTOCOL[0])
        assert first == second, f"{method} is not deterministic for a fixed seed"
