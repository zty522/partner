"""BDK FunctionPool-based regressor for Partner 02 molecular affinity fitting.

This module wraps BDK's `FunctionPool` (4 kernels: Linear, Quadratic, Fourier,
ExponentialDecay + sparse gate + kernel mask) as a sklearn-style regressor that
02 can call when fitting molecular affinity (vina/rmsd → pk).

Honesty contract:
    - This is a sklearn-compatible wrapper, not a Partner production pipeline.
    - Training requires `torch` (BDK FunctionPool is a torch.nn.Module).
    - The fitter exposes the *kernel gate* after training so callers can see
      which kernels BDK selected for a given dataset; this is auditable.
    - We do NOT modify 02's existing baseline (LinearRegression +
      HistGradientBoostingRegressor).  This module is for 02 to *opt into* when
      the user wants a BDK-vs-sklearn comparison.

Why this remains:
    - It preserves the accepted numerical comparison organ after the
      incubator-convergence migration (ADR 0035).
    - 02 currently uses sklearn for its baseline. This fitter remains an
      additional Event option without disrupting existing pipelines.
"""
from __future__ import annotations

from typing import Any

try:
    import torch
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "BDKFunctionPoolFitter requires torch.  Install with `pip install torch`."
    ) from exc

import numpy as np
from partner.learn.bdk_function_pool import FunctionPool
from partner.learn.bdk_constraints import apply_ocamms_constraint

KERNEL_NAMES = ["linear", "quadratic", "fourier", "expdecay"]


class BDKFunctionPoolFitter:
    """Sklearn-style regressor wrapping BDK FunctionPool.

    Parameters
    ----------
    in_dim : int
        Number of input features (e.g. 2 for [vina, rmsd]).
    epochs : int
        Number of full-batch gradient steps.
    lr : float
        Learning rate for Adam.
    weight_decay : float
        L2 regularisation on the FunctionPool parameters.
    initial_kernel_mask : list[bool] or None
        If given, length must equal len(KERNEL_NAMES). True = kernel enabled,
        False = masked out (BDKFunctionPool will set gate logit to -inf for
        masked kernels). Default: all True.
    seed : int
        Torch random seed for reproducibility.
    """

    def __init__(
        self,
        in_dim: int,
        epochs: int = 200,
        lr: float = 0.05,
        weight_decay: float = 1e-3,
        initial_kernel_mask: list[bool] | None = None,
        seed: int = 0,
    ) -> None:
        if in_dim < 1:
            raise ValueError(f"in_dim must be >= 1, got {in_dim}")
        self.in_dim = in_dim
        self.epochs = epochs
        self.lr = lr
        self.weight_decay = weight_decay
        self.initial_kernel_mask = list(initial_kernel_mask) if initial_kernel_mask else [True] * len(KERNEL_NAMES)
        if len(self.initial_kernel_mask) != len(KERNEL_NAMES):
            raise ValueError(
                f"initial_kernel_mask length {len(self.initial_kernel_mask)} "
                f"must equal {len(KERNEL_NAMES)}"
            )
        self.seed = seed
        # Optional: data-driven mask recommendation.  Set via from_data().
        self._mask_recommendation: dict[str, Any] | None = None

        self._pool: Any = None
        self._trained = False
        self._final_gate: list[float] | None = None
        self._final_kernel_probs: list[float] | None = None
        self._loss_history: list[float] = []

    @classmethod
    def from_data(
        cls,
        X: np.ndarray,
        y: np.ndarray,
        *,
        top_k: int = 2,
        min_r2: float = 0.001,
        epochs: int = 200,
        lr: float = 0.05,
        weight_decay: float = 1e-3,
        seed: int = 0,
    ) -> "BDKFunctionPoolFitter":
        """Build a fitter whose initial kernel mask is data-driven.

        Uses bdk_kernel_mask_optimizer.recommend_kernel_mask to pick a mask
        based on the actual (X, y) data's per-kernel R^2.

        The returned fitter has an extra attribute `mask_recommendation`
        (dict) with the R^2 ranking and reasoning, so callers can audit
        which mask was chosen and why.
        """
        from partner.learn.bdk_kernel_mask_optimizer import recommend_kernel_mask
        rec = recommend_kernel_mask(X, y, top_k=top_k, min_r2=min_r2)
        fitter = cls(
            in_dim=X.shape[1],
            epochs=epochs, lr=lr, weight_decay=weight_decay,
            initial_kernel_mask=rec["mask"],
            seed=seed,
        )
        fitter._mask_recommendation = rec
        return fitter

    @property
    def mask_recommendation(self) -> dict[str, Any] | None:
        """The data-driven mask recommendation (only set if from_data was used)."""
        return self._mask_recommendation

    def fit(self, X: np.ndarray, y: np.ndarray) -> "BDKFunctionPoolFitter":
        """Train the FunctionPool on (X, y).

        X : (n_samples, in_dim) float array
        y : (n_samples,) float array

        Note: initial_kernel_mask is applied during BOTH training and inference
        to keep the audit consistent.  Masked kernels have gate logits forced
        to -inf, so they receive 0 probability mass after softmax.
        """
        X = np.asarray(X, dtype=np.float32)
        y = np.asarray(y, dtype=np.float32).reshape(-1)
        if X.ndim != 2 or X.shape[1] != self.in_dim:
            raise ValueError(f"X must be (n, {self.in_dim}); got {X.shape}")
        if X.shape[0] != y.shape[0]:
            raise ValueError(f"X and y row counts differ: {X.shape[0]} vs {y.shape[0]}")

        torch.manual_seed(self.seed)
        self._pool = FunctionPool(in_dim=self.in_dim, num_kernels=len(KERNEL_NAMES))
        optimizer = torch.optim.Adam(self._pool.parameters(), lr=self.lr,
                                      weight_decay=self.weight_decay)
        xt = torch.from_numpy(X)
        yt = torch.from_numpy(y).reshape(-1, 1)

        self._loss_history = []
        for epoch in range(self.epochs):
            self._pool.train()
            optimizer.zero_grad()
            pred, gate_probs = self._pool(xt, kernel_mask=self.initial_kernel_mask)
            loss = torch.nn.functional.mse_loss(pred, yt)
            loss.backward()
            optimizer.step()
            self._loss_history.append(float(loss.item()))

        # Snapshot final gate probs WITH mask applied (consistent with predict())
        self._pool.eval()
        with torch.no_grad():
            _, gate_probs = self._pool(xt, kernel_mask=self.initial_kernel_mask)
            probs_np = gate_probs.mean(dim=0).cpu().numpy().tolist()
        self._final_kernel_probs = probs_np
        # Convert to "logits" by inverting softmax for ocamms validator
        eps = 1e-9
        self._final_gate = [float(np.log(max(p, eps))) for p in probs_np]
        self._trained = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._trained:
            raise RuntimeError("predict() called before fit()")
        X = np.asarray(X, dtype=np.float32)
        if X.ndim != 2 or X.shape[1] != self.in_dim:
            raise ValueError(f"X must be (n, {self.in_dim}); got {X.shape}")
        self._pool.eval()
        with torch.no_grad():
            xt = torch.from_numpy(X)
            # Use the SAME mask as training to keep behaviour consistent
            pred, _ = self._pool(xt, kernel_mask=self.initial_kernel_mask)
        return pred.cpu().numpy().reshape(-1)

    # -- audit / inspection API ----------------------------------------------
    @property
    def kernel_probs(self) -> list[float]:
        if self._final_kernel_probs is None:
            raise RuntimeError("kernel_probs only available after fit()")
        return list(self._final_kernel_probs)

    @property
    def kernel_logits(self) -> list[float]:
        if self._final_gate is None:
            raise RuntimeError("kernel_logits only available after fit()")
        return list(self._final_gate)

    def ocamms_check(self, *, margin: float = 0.20, max_activated: int = 2) -> dict[str, Any]:
        """Run BDK ocamms validator on the trained gate logits.

        Returns dict with passed (bool), message, n_activated, activated_names.
        """
        if not self._trained:
            raise RuntimeError("ocamms_check only available after fit()")
        passed = apply_ocamms_constraint(self.kernel_probs, margin=margin, max_activated=max_activated)
        activated = [name for name, p in zip(KERNEL_NAMES, self.kernel_probs) if p > margin]
        return {
            "passed": passed,
            "margin": margin,
            "max_activated": max_activated,
            "n_activated": len(activated),
            "activated_names": activated,
            "kernel_probs": self.kernel_probs,
            "kernel_logits": self.kernel_logits,
        }

    @property
    def loss_history(self) -> list[float]:
        return list(self._loss_history)


def fit_predict_evaluate(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    *,
    epochs: int = 200,
    initial_kernel_mask: list[bool] | None = None,
) -> dict[str, Any]:
    """Convenience: fit BDK FunctionPool, predict on test, return metrics + audit.

    Returns dict with y_pred, rmse, mae, kernel_probs, ocamms result, loss_history.
    Does NOT depend on sklearn — uses numpy only for metrics.
    """
    fitter = BDKFunctionPoolFitter(
        in_dim=X_train.shape[1],
        epochs=epochs,
        initial_kernel_mask=initial_kernel_mask,
    )
    fitter.fit(X_train, y_train)
    y_pred = fitter.predict(X_test)
    err = y_test.reshape(-1) - y_pred
    rmse = float(np.sqrt(np.mean(err ** 2)))
    mae = float(np.mean(np.abs(err)))
    return {
        "y_pred": y_pred.tolist(),
        "rmse": rmse,
        "mae": mae,
        "kernel_probs": fitter.kernel_probs,
        "kernel_logits": fitter.kernel_logits,
        "ocamms": fitter.ocamms_check(),
        "loss_history": fitter.loss_history,
        "kernel_mask_used": fitter.initial_kernel_mask,
    }


if __name__ == "__main__":  # pragma: no cover
    # Quick demo: synthetic data.
    rng = np.random.default_rng(42)
    n = 200
    X = rng.uniform(-3, 3, size=(n, 2)).astype(np.float32)
    y = (0.7 * X[:, 0] - 0.3 * X[:, 1] ** 2 + 0.1 * np.random.randn(n)).astype(np.float32)
    n_tr = int(n * 0.8)
    out = fit_predict_evaluate(X[:n_tr], y[:n_tr], X[n_tr:], y[n_tr:], epochs=200)
    print(f"RMSE: {out['rmse']:.4f}")
    print(f"MAE:  {out['mae']:.4f}")
    print(f"Kernel probs: {out['kernel_probs']}")
    print(f"Ocamms: {out['ocamms']}")
