from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/mnt/e/work/partner")

from partner.learn.targetdiff_bdk_sweep import (  # noqa: E402
    evaluate_config,
    fold_sha256,
    load_aggregated_ligands,
)


_HAS_REAL_DATA = Path(
    "/mnt/e/work/partner_workspace/external/targetdiff/data/affinity_info.pkl"
).exists()


def test_fold_sha256_matches_stage13():
    """Verify fold function matches Stage 13's deterministic split."""
    # Stage 13 uses int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 5
    group = "test_group"
    expected = int(__import__("hashlib").sha256(group.encode()).hexdigest()[:8], 16) % 5
    assert fold_sha256(group) == expected


def test_load_aggregated_ligands_synthetic(tmp_path):
    pkl = tmp_path / "synth.pkl"
    import pickle
    rows = {}
    for i in range(20):
        for j in range(2):
            key = f"group_{i}/ligand_{j}_min_{i}"
            rows[key] = {"pk": 0.5 * i + 0.1 * j, "vina": 1.0, "rmsd": 1.0, "smiles": "X"}
    with open(pkl, "wb") as f:
        pickle.dump(rows, f)
    agg = load_aggregated_ligands(pkl)
    assert len(agg) > 0
    assert all("group" in r and "pk" in r for r in agg)


def test_evaluate_config_runs_synthetic(tmp_path):
    pkl = tmp_path / "synth.pkl"
    import pickle, math
    import numpy as np
    rows = {}
    rng = np.random.default_rng(0)
    for i in range(30):
        for j in range(2):
            key = f"group_{i}/ligand_{j}_min_{i}"
            vina = 1.0 + i * 0.1
            rmsd = 1.0
            pk = 0.5 * vina + 0.05 * rng.standard_normal()
            rows[key] = {"pk": pk, "vina": vina, "rmsd": rmsd, "smiles": "X"}
    with open(pkl, "wb") as f:
        pickle.dump(rows, f)
    agg = load_aggregated_ligands(pkl)
    out = evaluate_config(agg, lr=0.05, weight_decay=0.001,
                           epochs=30, mask=[True, True, True, True])
    assert "mean_rmse" in out
    assert out["mean_rmse"] >= 0


@pytest.mark.skipif(not _HAS_REAL_DATA,
                    reason="TargetDiff data not present in workspace")
def test_evaluate_single_config_on_real_data(tmp_path):
    """One config evaluation to verify real-data wiring."""
    agg = load_aggregated_ligands(Path(
        "/mnt/e/work/partner_workspace/external/targetdiff/data/affinity_info.pkl"))
    # Just one config, fewer epochs to keep it fast.
    out = evaluate_config(agg, lr=0.05, weight_decay=0.001,
                           epochs=30, mask=[True, True, True, True])
    assert "mean_rmse" in out
    # Sanity: BDK on real TargetDiff should produce RMSE in 1.4-1.8 range.
    assert 1.4 < out["mean_rmse"] < 1.8
