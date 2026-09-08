from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# BDK import path injection (matches production module).
_PARTNER_ROOT = Path("/mnt/e/work/partner")
if str(_PARTNER_ROOT) not in sys.path:
    sys.path.insert(0, str(_PARTNER_ROOT))

from partner.learn.bdk_function_pool_fitter import (  # noqa: E402
    BDKFunctionPoolFitter,
    KERNEL_NAMES,
    fit_predict_evaluate,
)


def test_bdk_function_pool_actually_imported():
    """The fitter must really import BDK, not just call numpy."""
    from partner.learn.bdk_function_pool import FunctionPool
    assert FunctionPool.KERNEL_NAMES == KERNEL_NAMES
    pool = FunctionPool(in_dim=2, num_kernels=4)
    assert hasattr(pool, "forward")
    assert hasattr(pool, "gate")


def test_constants_match_function_pool():
    """Kernel name order must match BDK FunctionPool.KERNEL_NAMES."""
    from partner.learn.bdk_function_pool import FunctionPool
    assert KERNEL_NAMES == FunctionPool.KERNEL_NAMES


def test_fitter_rejects_inconsistent_kernel_mask():
    """A mask with wrong length must be rejected at construction."""
    with pytest.raises(ValueError, match="initial_kernel_mask length"):
        BDKFunctionPoolFitter(in_dim=2, initial_kernel_mask=[True, True])


def test_fit_then_predict_returns_correct_shape():
    rng = np.random.default_rng(0)
    X = rng.uniform(-2, 2, size=(50, 2)).astype(np.float32)
    y = (2 * X[:, 0] - X[:, 1]).astype(np.float32)
    fitter = BDKFunctionPoolFitter(in_dim=2, epochs=20, seed=42)
    fitter.fit(X[:40], y[:40])
    pred = fitter.predict(X[40:])
    assert pred.shape == (10,)


def test_fitter_beats_constant_baseline_on_linear_data():
    """On a pure-linear dataset, the FunctionPool should learn better than y.mean()."""
    rng = np.random.default_rng(0)
    n = 200
    X = rng.uniform(-3, 3, size=(n, 1)).astype(np.float32)
    y = (2.5 * X[:, 0] + 0.05 * rng.standard_normal(n)).astype(np.float32)
    fitter = BDKFunctionPoolFitter(in_dim=1, epochs=200, seed=42)
    fitter.fit(X[:160], y[:160])
    pred = fitter.predict(X[160:])
    rmse_fitter = float(np.sqrt(np.mean((y[160:] - pred) ** 2)))
    rmse_const = float(np.sqrt(np.mean((y[160:] - y[:160].mean()) ** 2)))
    assert rmse_fitter < rmse_const, (
        f"fitter RMSE {rmse_fitter:.3f} should beat constant baseline {rmse_const:.3f}"
    )


def test_kernel_probs_sum_to_one():
    fitter = BDKFunctionPoolFitter(in_dim=2, epochs=10, seed=1)
    X = np.random.RandomState(0).uniform(-1, 1, (30, 2)).astype(np.float32)
    y = X.sum(axis=1).astype(np.float32)
    fitter.fit(X, y)
    probs = fitter.kernel_probs
    assert len(probs) == len(KERNEL_NAMES)
    assert abs(sum(probs) - 1.0) < 1e-3


def test_ocamms_check_returns_audit_dict():
    fitter = BDKFunctionPoolFitter(in_dim=2, epochs=10, seed=1)
    X = np.random.RandomState(0).uniform(-1, 1, (30, 2)).astype(np.float32)
    y = X.sum(axis=1).astype(np.float32)
    fitter.fit(X, y)
    audit = fitter.ocamms_check()
    assert "passed" in audit
    assert "margin" in audit
    assert "max_activated" in audit
    assert "n_activated" in audit
    assert "activated_names" in audit
    assert "kernel_probs" in audit
    assert "kernel_logits" in audit
    assert isinstance(audit["passed"], bool)


def test_kernel_mask_blocks_specific_kernels():
    """When we mask out fourier + expdecay, only linear/quadratic can be active."""
    fitter = BDKFunctionPoolFitter(
        in_dim=2, epochs=50, seed=0,
        initial_kernel_mask=[True, True, False, False],  # linear/quadratic only
    )
    X = np.random.RandomState(0).uniform(-1, 1, (50, 2)).astype(np.float32)
    y = (X[:, 0] + X[:, 1] ** 2).astype(np.float32)
    fitter.fit(X, y)
    audit = fitter.ocamms_check()
    # With mask, fourier and expdecay must have ~0 probability
    assert audit["kernel_probs"][2] < 0.05, "fourier should be masked out"
    assert audit["kernel_probs"][3] < 0.05, "expdecay should be masked out"


def test_predict_before_fit_raises():
    fitter = BDKFunctionPoolFitter(in_dim=2)
    with pytest.raises(RuntimeError, match="fit"):
        fitter.predict(np.zeros((1, 2)))


def test_fit_predict_evaluate_returns_full_audit():
    rng = np.random.default_rng(0)
    n = 80
    X = rng.uniform(-1, 1, size=(n, 2)).astype(np.float32)
    y = (X[:, 0] - 0.5 * X[:, 1]).astype(np.float32)
    out = fit_predict_evaluate(
        X[:60], y[:60], X[60:], y[60:],
        epochs=100,
    )
    assert "y_pred" in out
    assert "rmse" in out
    assert "mae" in out
    assert "kernel_probs" in out
    assert "ocamms" in out
    assert "loss_history" in out
    assert len(out["y_pred"]) == 20
    assert out["rmse"] >= 0
    assert out["mae"] >= 0
    assert len(out["loss_history"]) == 100


def test_loss_decreases_during_training():
    """Training should reduce loss over epochs (sanity check)."""
    rng = np.random.default_rng(0)
    n = 100
    X = rng.uniform(-1, 1, size=(n, 1)).astype(np.float32)
    y = (3 * X[:, 0]).astype(np.float32) + 0.01 * rng.standard_normal(n).astype(np.float32)
    fitter = BDKFunctionPoolFitter(in_dim=1, epochs=100, seed=42)
    fitter.fit(X, y)
    losses = fitter.loss_history
    assert losses[-1] < losses[0], (
        f"final loss {losses[-1]:.4f} should be < initial loss {losses[0]:.4f}"
    )


def test_from_data_classmethod_uses_recommended_mask():
    """BDKFunctionPoolFitter.from_data() should pick a data-driven mask."""
    rng = np.random.default_rng(0)
    X = rng.uniform(-1, 1, size=(50, 2))
    y = X[:, 0] ** 2 + 0.1 * rng.standard_normal(50)
    fitter = BDKFunctionPoolFitter.from_data(X, y, top_k=2, epochs=10)
    # Should have mask_recommendation populated
    assert fitter.mask_recommendation is not None
    # mask should be a 4-bool list with exactly 2 True (top_k=2)
    assert len(fitter.initial_kernel_mask) == 4
    assert sum(fitter.initial_kernel_mask) == 2
    # Reasoning field should be populated
    assert "R^2 ranking" in fitter.mask_recommendation["reasoning"]


def test_from_data_with_explicit_mask_overrides_optimizer():
    """If initial_kernel_mask is passed to __init__, from_data is bypassed."""
    fitter = BDKFunctionPoolFitter(
        in_dim=2, initial_kernel_mask=[True, True, True, True], epochs=10,
    )
    # mask_recommendation stays None because from_data was not used
    assert fitter.mask_recommendation is None
    assert fitter.initial_kernel_mask == [True, True, True, True]


def test_ocamms_passed_or_documented_failure():
    """On a simple linear dataset, ocamms may pass or fail (kernel could be
    spread) — but the audit must be truthful.  We just check that 'passed'
    is one of True/False and that the count of activated kernels equals
    the number of names listed.
    """
    rng = np.random.default_rng(0)
    n = 80
    X = rng.uniform(-2, 2, size=(n, 2)).astype(np.float32)
    y = (X[:, 0] + X[:, 1]).astype(np.float32)
    fitter = BDKFunctionPoolFitter(in_dim=2, epochs=100, seed=42)
    fitter.fit(X, y)
    audit = fitter.ocamms_check(margin=0.20, max_activated=2)
    assert len(audit["activated_names"]) == audit["n_activated"]
    for name in audit["activated_names"]:
        assert name in KERNEL_NAMES
