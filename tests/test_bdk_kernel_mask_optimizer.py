from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, "/mnt/e/work/partner")

from partner.learn.bdk_kernel_mask_optimizer import (  # noqa: E402
    recommend_kernel_mask,
)


def test_recommend_returns_four_kernel_mask():
    rng = np.random.default_rng(0)
    X = rng.uniform(-1, 1, size=(100, 2))
    y = X[:, 0] + 0.1 * rng.standard_normal(100)
    out = recommend_kernel_mask(X, y, top_k=2)
    mask = out["mask"]
    assert len(mask) == 4
    assert all(isinstance(m, bool) for m in mask)
    # Exactly top_k enabled (when min_r2 is satisfied)
    n_true = sum(mask)
    assert n_true == 2


def test_recommend_picks_quadratic_for_quadratic_data():
    """When the true relationship is quadratic, the optimizer should prefer quadratic."""
    rng = np.random.default_rng(0)
    X = rng.uniform(-2, 2, size=(500, 1))
    y = X[:, 0] ** 2 + 0.05 * rng.standard_normal(500)
    out = recommend_kernel_mask(X, y, top_k=2)
    # Quadratic should be in the top
    kernel_r2 = out["kernel_r2"]
    assert kernel_r2["quadratic"] > kernel_r2["linear"]


def test_recommend_picks_linear_for_linear_data():
    """For linear data, linear+quadratic both have high R^2; fourier+expdecay low.

    (Quadratic R^2 >= linear R^2 always, because the quadratic basis
    includes the linear basis as a special case.  We check the contrast
    between the high-R^2 family and the low-R^2 family instead.)"""
    rng = np.random.default_rng(0)
    X = rng.uniform(-3, 3, size=(500, 1))
    y = 2.0 * X[:, 0] + 0.1 * rng.standard_normal(500)
    out = recommend_kernel_mask(X, y, top_k=2)
    kernel_r2 = out["kernel_r2"]
    # Linear and quadratic should have HIGH R^2; fourier+expdecay LOWER.
    # (Fourier basis can capture linear-ish structure too, so we check the
    # relative gap rather than an absolute threshold.)
    assert kernel_r2["linear"] > 0.9
    assert kernel_r2["quadratic"] > 0.9
    # Recommendation should NOT include fourier or expdecay
    mask = out["mask"]
    assert mask[0] is True   # linear
    assert mask[1] is True   # quadratic
    assert mask[2] is False  # fourier
    assert mask[3] is False  # expdecay


def test_recommend_keeps_at_least_one_kernel():
    """When all R^2 are below min_r2, we must still keep at least one kernel."""
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(50, 2))
    y = rng.standard_normal(50)  # pure noise
    out = recommend_kernel_mask(X, y, top_k=2, min_r2=0.5)
    n_true = sum(out["mask"])
    assert n_true >= 1, "must keep at least one kernel even when all R^2 are tiny"


def test_recommend_respects_top_k():
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(200, 2))
    y = X[:, 0] + X[:, 1] + 0.1 * rng.standard_normal(200)
    out1 = recommend_kernel_mask(X, y, top_k=1)
    out3 = recommend_kernel_mask(X, y, top_k=3)
    assert sum(out1["mask"]) <= 1
    assert sum(out3["mask"]) <= 3


def test_recommend_audit_includes_per_feature_r2():
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(50, 3))
    y = X[:, 0] ** 2 + 0.1 * rng.standard_normal(50)
    out = recommend_kernel_mask(X, y, top_k=2)
    assert "per_feature_r2" in out
    assert out["n_features"] == 3
    assert "reasoning" in out
    # per_feature_r2 should have per-kernel arrays of length 3
    for kernel_name, r2_array in out["per_feature_r2"].items():
        assert len(r2_array) == 3


def test_recommend_validates_input_shape():
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(50,))  # 1D
    y = rng.standard_normal(50)
    with pytest.raises(ValueError, match="2D"):
        recommend_kernel_mask(X, y, top_k=2)


def test_recommend_rejects_top_k_out_of_range():
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(50, 2))
    y = rng.standard_normal(50)
    with pytest.raises(ValueError, match="top_k"):
        recommend_kernel_mask(X, y, top_k=0)
    with pytest.raises(ValueError, match="top_k"):
        recommend_kernel_mask(X, y, top_k=5)


def test_recommend_rejects_mismatched_lengths():
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(50, 2))
    y = rng.standard_normal(49)  # wrong length
    with pytest.raises(ValueError, match="row counts"):
        recommend_kernel_mask(X, y, top_k=2)


@pytest.mark.skipif(not Path(
        "/mnt/e/work/partner_workspace/external/targetdiff/data/affinity_info.pkl"
    ).exists(), reason="TargetDiff data not present in workspace")
def test_recommend_on_real_targetdiff():
    """On real TargetDiff data, the optimizer should pick linear+quadratic
    (consistent with our earlier R^2 analysis)."""
    import pickle, math, statistics, re
    from collections import defaultdict
    pkl = pickle.load(open(
        "/mnt/e/work/partner_workspace/external/targetdiff/data/affinity_info.pkl", "rb"))
    rows = []
    for key, value in pkl.items():
        try:
            pk, vina, rmsd = float(value["pk"]), float(value["vina"]), float(value["rmsd"])
        except (KeyError, TypeError, ValueError):
            continue
        if pk > 0 and all(math.isfinite(x) for x in (pk, vina, rmsd)):
            group = str(key).split("/", 1)[0]
            name = str(key).split("/", 1)[1]
            ligand = group + "/" + re.sub(r"_(?:min|docked)_\d+$", "", name)
            rows.append({"pk": pk, "vina": vina, "rmsd": rmsd})

    buckets = defaultdict(list)
    for r in rows:
        key = (r["vina"], r["rmsd"])
        buckets[key].append(r["pk"])
    agg = []
    for (v, rmsd), pks in buckets.items():
        agg.append({"vina": v, "rmsd": rmsd, "pk": statistics.median(pks)})
    X = np.array([[r["vina"], r["rmsd"]] for r in agg[:5000]], dtype=np.float32)
    y = np.array([r["pk"] for r in agg[:5000]], dtype=np.float32)
    out = recommend_kernel_mask(X, y, top_k=2)
    # Linear + quadratic should be the recommended (matches our analysis)
    assert out["mask"][0] is True  # linear
    assert out["mask"][1] is True  # quadratic
    assert sum(out["mask"]) == 2
