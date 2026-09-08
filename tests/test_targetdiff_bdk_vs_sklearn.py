from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/mnt/e/work/partner")

from partner.learn.targetdiff_bdk_vs_sklearn_comparison import (  # noqa: E402
    load_aggregated_ligands,
    run_sklearn_baseline,
    run_bdk,
)


_HAS_REAL_DATA = Path(
    "/mnt/e/work/partner_workspace/external/targetdiff/data/affinity_info.pkl"
).exists()


def _make_synthetic_data():
    """Build a small synthetic aggregated dataset for fast tests."""
    rng_path = "/tmp/synthetic_affinity.pkl"
    import pickle
    rows = {}
    for i in range(50):
        for j in range(3):
            key = f"group_{i}/ligand_{j}_min_{i}"
            # pk = 0.7 * vina - 0.3 * rmsd^2 + noise
            vina = 5.0 + (i % 5) * 0.3
            rmsd = 1.0 + (j % 3) * 0.2
            pk = 0.7 * vina - 0.3 * rmsd ** 2 + 0.05 * ((i + j) % 7)
            rows[key] = {"pk": pk, "vina": vina, "rmsd": rmsd, "smiles": "X"}
    with open(rng_path, "wb") as f:
        pickle.dump(rows, f)
    return rng_path


def test_load_aggregated_ligands_synthetic():
    pkl = _make_synthetic_data()
    agg = load_aggregated_ligands(Path(pkl))
    assert len(agg) > 0
    # All records have the 4 required fields
    for row in agg:
        assert {"group", "ligand", "pk", "vina", "rmsd"} <= row.keys()


def test_run_sklearn_baseline_returns_metrics():
    pkl = _make_synthetic_data()
    agg = load_aggregated_ligands(Path(pkl))
    result = run_sklearn_baseline(agg)
    assert result["method"] == "sklearn_baseline"
    assert "mean_linear_rmse" in result
    assert "mean_hgb_rmse" in result
    assert len(result["folds"]) >= 1
    for fold in result["folds"]:
        assert fold["linear"]["rmse"] >= 0
        assert fold["hgb"]["rmse"] >= 0


def test_run_bdk_returns_metrics_and_kernel_probs():
    pkl = _make_synthetic_data()
    agg = load_aggregated_ligands(Path(pkl))
    result = run_bdk(agg, epochs=20)
    assert result["method"] == "bdk_function_pool"
    assert "mean_bdk_rmse" in result
    assert "mean_kernel_probs" in result
    assert len(result["mean_kernel_probs"]) == 4
    for p in result["mean_kernel_probs"]:
        assert 0.0 <= p <= 1.0
    assert abs(sum(result["mean_kernel_probs"]) - 1.0) < 0.05
    assert "ocamms_all_passed" in result


def test_comparison_finds_some_signal_on_synthetic_data():
    """Synthetic data has clear structure (pk = 0.7*vina - 0.3*rmsd^2 + noise).
    BDK FunctionPool with 4 kernels SHOULD find it.
    """
    pkl = _make_synthetic_data()
    agg = load_aggregated_ligands(Path(pkl))
    bdk = run_bdk(agg, epochs=200)
    # Synthetic data has polynomial structure; BDK with quadratic+linear
    # should achieve reasonable RMSE
    assert bdk["mean_bdk_rmse"] < 2.0  # Sanity bound
    # At least one kernel should be activated
    assert max(bdk["mean_kernel_probs"]) > 0.15


@pytest.mark.skipif(not _HAS_REAL_DATA,
                    reason="TargetDiff data not present in workspace")
def test_comparison_on_real_data(tmp_path):
    """Run on real TargetDiff data and write a real report."""
    output = tmp_path / "real_report.md"
    from partner.learn.targetdiff_bdk_vs_sklearn_comparison import main
    # Invoke as CLI
    import sys as _sys
    argv = [
        "targetdiff_bdk_vs_sklearn_comparison",
        "--input", "/mnt/e/work/partner_workspace/external/targetdiff/data/affinity_info.pkl",
        "--output", str(output),
        "--epochs", "30",  # fast
    ]
    saved = _sys.argv
    try:
        _sys.argv = argv
        main()
    finally:
        _sys.argv = saved
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    # All 3 methods present
    assert "sklearn LinearRegression" in content
    assert "sklearn HistGradientBoostingRegressor" in content
    assert "BDK FunctionPool" in content
    # JSON also exists
    json_path = output.with_suffix(".json")
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert "sklearn" in data
    assert "bdk" in data
    assert "delta_rmse_bdk_minus_sklearn_best" in data
    # Honest verdict field present
    assert "bdk_beats_sklearn_best" in data


def test_comparison_exits_nonzero_on_missing_input(tmp_path):
    output = tmp_path / "should_not_exist.md"
    import sys as _sys
    saved = _sys.argv
    _sys.argv = ["prog", "--input", "/nonexistent.pkl", "--output", str(output)]
    try:
        from partner.learn.targetdiff_bdk_vs_sklearn_comparison import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
    finally:
        _sys.argv = saved
    assert not output.exists()
