"""Data-driven initial kernel mask for BDK FunctionPool.

Given (X, y) with X.shape = (n_samples, in_dim), recommend a kernel_mask
[True, True, True, True] biased toward kernels that have the highest
linear correlation with y on the actual data.

The mask is a *prior*; the BDK gate is still learned end-to-end and may
overrule the prior.  The point of the mask is to prune obviously-irrelevant
kernels so the gate doesn't waste capacity on noise.

Algorithm (auditable, no LLM):
  1. For each feature column x_i in X:
       - fit y ~ a*x_i + b  →  R^2_lin_i
       - fit y ~ a*x_i^2 + b*x_i + c  →  R^2_quad_i
  2. Aggregate per-kernel:
       - linear:  sum_i R^2_lin_i (i.e. linear over each feature)
       - quadratic: sum_i R^2_quad_i
       - fourier:  same with sin/cos basis
       - expdecay: same with exp(-x) basis
  3. Keep the top-K kernels with the highest aggregate R^2.
     Default K=2 (matches BDK ocamms max_activated=2).
  4. If the user passes ocamms_max_activated=1, keep top-1 instead.

Honesty:
    - This is a heuristic.  The BDK gate may still choose differently.
    - The mask is just an initialisation, not a hard constraint on the
      learned function.
    - The R^2 numbers are reported in the audit dict so callers can verify.
"""
from __future__ import annotations

from typing import Any

import numpy as np


# Kernel indices (must match bdk.function_pool.KERNEL_NAMES order)
_KERNEL_LINEAR = 0
_KERNEL_QUADRATIC = 1
_KERNEL_FOURIER = 2
_KERNEL_EXPDECAY = 3


def _per_feature_r2(X: np.ndarray, y: np.ndarray) -> dict[str, np.ndarray]:
    """For each feature x_i, compute R^2 of y ~ f(x_i) for each kernel basis."""
    n, d = X.shape
    results = {"linear": np.zeros(d), "quadratic": np.zeros(d),
                "fourier": np.zeros(d), "expdecay": np.zeros(d)}
    for i in range(d):
        xi = X[:, i]
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        if ss_tot < 1e-12:
            continue
        # Linear: y = a*x + b
        A = np.column_stack([xi, np.ones_like(xi)])
        coefs, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
        pred = A @ coefs
        ss_res = float(np.sum((y - pred) ** 2))
        results["linear"][i] = max(0.0, 1.0 - ss_res / ss_tot)
        # Quadratic: y = a*x^2 + b*x + c
        A2 = np.column_stack([xi ** 2, xi, np.ones_like(xi)])
        coefs2, _, _, _ = np.linalg.lstsq(A2, y, rcond=None)
        pred2 = A2 @ coefs2
        ss_res2 = float(np.sum((y - pred2) ** 2))
        results["quadratic"][i] = max(0.0, 1.0 - ss_res2 / ss_tot)
        # Fourier: y = a*sin(x) + b*cos(x) + c
        Af = np.column_stack([np.sin(xi), np.cos(xi), np.ones_like(xi)])
        coefsf, _, _, _ = np.linalg.lstsq(Af, y, rcond=None)
        predf = Af @ coefsf
        ss_resf = float(np.sum((y - predf) ** 2))
        results["fourier"][i] = max(0.0, 1.0 - ss_resf / ss_tot)
        # ExpDecay: y = a*exp(-x) + b
        Ae = np.column_stack([np.exp(-xi), np.ones_like(xi)])
        coefse, _, _, _ = np.linalg.lstsq(Ae, y, rcond=None)
        prede = Ae @ coefse
        ss_rese = float(np.sum((y - prede) ** 2))
        results["expdecay"][i] = max(0.0, 1.0 - ss_rese / ss_tot)
    return results


def recommend_kernel_mask(X: np.ndarray, y: np.ndarray, *,
                            top_k: int = 2,
                            min_r2: float = 0.001) -> dict[str, Any]:
    """Return data-driven initial kernel mask + audit info.

    Args:
        X: (n_samples, in_dim) feature matrix.
        y: (n_samples,) target vector.
        top_k: how many kernels to keep enabled (default 2, matches ocamms).
        min_r2: kernels with aggregate R^2 below this are turned off even
                if they are in the top-K by ranking (to avoid enabling
                noise kernels when all R^2 are tiny).

    Returns dict:
        mask: [True, True, True, True] of length 4
        kernel_r2: dict mapping kernel name to aggregate R^2
        per_feature_r2: dict mapping kernel name to per-feature R^2
        top_k: int (echoed back)
        n_features: int
        reasoning: str (human-readable summary)
    """
    if X.ndim != 2:
        raise ValueError(f"X must be 2D; got shape {X.shape}")
    if y.ndim != 1:
        raise ValueError(f"y must be 1D; got shape {y.shape}")
    if X.shape[0] != y.shape[0]:
        raise ValueError(f"X and y row counts differ: {X.shape[0]} vs {y.shape[0]}")
    if not 1 <= top_k <= 4:
        raise ValueError(f"top_k must be 1..4, got {top_k}")

    per_feat = _per_feature_r2(X, y)
    kernel_r2 = {name: float(per_feat[name].sum()) for name in per_feat}

    # Rank by R^2 desc, take top_k
    ranked = sorted(kernel_r2.items(), key=lambda kv: -kv[1])
    keep = {name for name, _ in ranked[:top_k]}

    # Apply min_r2 filter: a kernel can be in top-K but if its R^2 is
    # essentially zero, we don't trust it.
    final_keep = {name for name in keep if kernel_r2[name] >= min_r2}

    # Always keep at least one kernel (otherwise the gate has nothing to use).
    if not final_keep and ranked:
        final_keep = {ranked[0][0]}

    # Map back to mask (4 entries, in BDK FunctionPool.KERNEL_NAMES order)
    mask = [
        "linear" in final_keep,
        "quadratic" in final_keep,
        "fourier" in final_keep,
        "expdecay" in final_keep,
    ]
    reasoning = (
        f"top_k={top_k}, min_r2={min_r2}. "
        f"R^2 ranking: " + ", ".join(f"{n}={r2:.4f}" for n, r2 in ranked) + ". "
        f"Enabled: {sorted(final_keep)}."
    )
    return {
        "mask": mask,
        "kernel_r2": kernel_r2,
        "per_feature_r2": per_feat,
        "top_k": top_k,
        "n_features": X.shape[1],
        "min_r2": min_r2,
        "reasoning": reasoning,
    }


if __name__ == "__main__":  # pragma: no cover
    import json
    import sys
    rng = np.random.default_rng(0)
    X = rng.uniform(-2, 2, size=(200, 2))
    # pk = 0.5*vina^2 - 0.3*rmsd + noise
    y = 0.5 * X[:, 0] ** 2 - 0.3 * X[:, 1] + 0.1 * rng.standard_normal(200)
    out = recommend_kernel_mask(X, y, top_k=2)
    print(json.dumps({k: v for k, v in out.items() if k != "per_feature_r2"},
                      indent=2, ensure_ascii=False))
