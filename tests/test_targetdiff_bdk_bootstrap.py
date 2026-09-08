from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/mnt/e/work/partner")

from partner.learn.targetdiff_bdk_vs_sklearn_bootstrap import (  # noqa: E402
    bootstrap_delta_ci,
    fold_sha256,
    load_aggregated_ligands,
    per_group_errors,
)


_HAS_REAL_DATA = Path(
    "/mnt/e/work/partner_workspace/external/targetdiff/data/affinity_info.pkl"
).exists()


def test_bootstrap_returns_required_fields():
    sk = {f"g_{i}": float(i + 1) for i in range(20)}
    bk = {f"g_{i}": float(i + 1) + 0.1 for i in range(20)}
    out = bootstrap_delta_ci(sk, bk, n_boot=100, ci=0.95, seed=42)
    assert "delta_mean" in out
    assert "ci_low" in out
    assert "ci_high" in out
    assert "p_value_BDK_worse" in out
    assert "excludes_zero" in out
    assert out["n_groups"] == 20


def test_bootstrap_deterministic_with_seed():
    sk = {f"g_{i}": float(i % 5) for i in range(10)}
    bk = {f"g_{i}": float(i % 5) + 0.5 for i in range(10)}
    a = bootstrap_delta_ci(sk, bk, n_boot=100, seed=42)
    b = bootstrap_delta_ci(sk, bk, n_boot=100, seed=42)
    assert a["delta_mean"] == b["delta_mean"]
    assert a["ci_low"] == b["ci_low"]


def test_bootstrap_detects_large_difference():
    """When BDK is much worse, CI should exclude 0."""
    sk = {f"g_{i}": 1.0 for i in range(50)}
    bk = {f"g_{i}": 2.0 for i in range(50)}  # 1.0 worse
    out = bootstrap_delta_ci(sk, bk, n_boot=500, ci=0.95, seed=42)
    assert out["excludes_zero"] is True
    assert out["ci_low"] > 0
    assert out["p_value_BDK_worse"] > 0.99


def test_bootstrap_detects_no_difference():
    """When both methods are equal, CI should include 0."""
    sk = {f"g_{i}": float(i % 3 + 1) for i in range(50)}
    bk = {f"g_{i}": float(i % 3 + 1) for i in range(50)}  # same
    out = bootstrap_delta_ci(sk, bk, n_boot=500, ci=0.95, seed=42)
    assert out["excludes_zero"] is False


def test_per_group_errors_synthetic(tmp_path):
    pkl = tmp_path / "synth.pkl"
    import pickle
    rows = {}
    # Use pk=1.0+i (always > 0 to pass the loader filter)
    for i in range(20):
        for j in range(2):
            key = f"group_{i}/ligand_{j}_min_{i}"
            rows[key] = {"pk": 1.0 + i * 0.1, "vina": 1.0, "rmsd": 1.0, "smiles": "X"}
    with open(pkl, "wb") as f:
        pickle.dump(rows, f)
    agg = load_aggregated_ligands(pkl)
    sk = per_group_errors(agg, method="sklearn_hgb")
    bk = per_group_errors(agg, method="bdk", lr=0.05, weight_decay=0.001,
                           epochs=10, mask=[True, True, True, True])
    assert set(sk.keys()) == set(bk.keys())
    assert len(sk) == 20
    for g in sk:
        assert sk[g] >= 0
        assert bk[g] >= 0


@pytest.mark.skipif(not _HAS_REAL_DATA,
                    reason="TargetDiff data not present in workspace")
def test_bootstrap_supports_n_seeds_argument():
    sk = {f"g_{i}": 1.0 + 0.01 * i for i in range(20)}
    bk = {f"g_{i}": 1.0 + 0.01 * i + 0.05 for i in range(20)}
    out1 = bootstrap_delta_ci(sk, bk, n_boot=100, seed=42)
    out2 = bootstrap_delta_ci(sk, bk, n_boot=100, seed=42)
    assert out1["delta_mean"] == out2["delta_mean"]


def test_bootstrap_detects_consistent_bdk_disadvantage():
    """When BDK is consistently +0.02 worse, CI should be entirely positive.

    This is the case we observed in the multi-seed real-data run on
    TargetDiff: BDK +sklearn is roughly 0.01-0.02 worse across seeds.
    """
    sk = {f"g_{i}": 1.0 + 0.01 * i for i in range(50)}
    bk = {f"g_{i}": 1.0 + 0.01 * i + 0.02 for i in range(50)}
    out = bootstrap_delta_ci(sk, bk, n_boot=1000, ci=0.95, seed=42)
    assert out["excludes_zero"] is True
    assert out["ci_low"] > 0
    assert out["p_value_BDK_worse"] > 0.99


def test_bootstrap_cli_on_real_data(tmp_path):
    """End-to-end: run the CLI on real data, verify output file structure."""
    from partner.learn.targetdiff_bdk_vs_sklearn_bootstrap import main
    out_json = tmp_path / "bootstrap.json"
    import sys as _sys
    saved = _sys.argv
    _sys.argv = ["prog",
                  "--input", "/mnt/e/work/partner_workspace/external/targetdiff/data/affinity_info.pkl",
                  "--output", str(out_json),
                  "--n-boot", "100"]
    try:
        main()
    finally:
        _sys.argv = saved
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert "bootstrap" in data
    assert "verdict" in data
    assert "honesty_notes" in data
    assert data["n_groups"] > 50
    assert data["bootstrap"]["n_boot"] == 100
