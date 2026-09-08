"""Typed function hypotheses; neural providers propose, evidence still judges."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class FunctionHypothesis:
    hypothesis_id: str
    family: str
    complexity: float
    structure_params: tuple[float, ...] = ()
    origin: str = "known_library"

    def design(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float).reshape(-1)
        if self.family == "constant":
            columns = [np.ones_like(x)]
        elif self.family == "polynomial":
            columns = [x ** power for power in range(int(self.structure_params[0]) + 1)]
        elif self.family == "fourier":
            frequency = float(self.structure_params[0])
            columns = [np.ones_like(x), np.sin(2 * np.pi * frequency * x),
                       np.cos(2 * np.pi * frequency * x)]
        elif self.family == "exp_decay":
            columns = [np.ones_like(x), np.exp(-float(self.structure_params[0]) * x)]
        elif self.family == "hinge":
            knot = float(self.structure_params[0])
            columns = [np.ones_like(x), x, np.maximum(0.0, x - knot)]
        elif self.family == "reciprocal":
            rate = float(self.structure_params[0])
            columns = [np.ones_like(x), 1.0 / (1.0 + rate * np.maximum(x, 0.0))]
        elif self.family == "modulated":
            frequency = float(self.structure_params[0])
            columns = [np.ones_like(x), x, x * np.sin(2 * np.pi * frequency * x),
                       x * np.cos(2 * np.pi * frequency * x)]
        else:
            raise ValueError(f"unsupported hypothesis family: {self.family}")
        return np.column_stack(columns)

    def fit(self, x: np.ndarray, y: np.ndarray, *, prior_log_weight: float = 0.0,
            ridge: float = 1e-6, robust: bool = False) -> dict[str, Any]:
        design, target = self.design(x), np.asarray(y, dtype=float).reshape(-1)
        regularizer = np.eye(design.shape[1]) * float(ridge)
        regularizer[0, 0] = 0.0
        weights = np.ones(len(target), dtype=float)
        coefficients = np.zeros(design.shape[1], dtype=float)
        robust_scale = 0.0
        iterations = 8 if robust and len(target) >= 8 else 1
        for _ in range(iterations):
            coefficients = np.linalg.solve(
                design.T @ (weights[:, None] * design) + regularizer,
                design.T @ (weights * target),
            )
            residual = target - design @ coefficients
            center = float(np.median(residual))
            robust_scale = 1.4826 * float(np.median(np.abs(residual - center))) + 1e-4
            normalized = np.abs(residual - center) / (1.5 * robust_scale)
            weights = np.ones(len(target), dtype=float)
            large = normalized > 1.0
            weights[large] = 1.0 / normalized[large]
        prediction = design @ coefficients
        residual = target - prediction
        n, parameter_count = max(1, len(target)), int(design.shape[1])
        mse = max(float(np.sum(residual ** 2)) / n, 1e-12)
        evidence_loss = mse
        if robust and len(target) >= 8:
            # One gross observation must not select the mechanism for every
            # other point.  Capping affects evidence ranking only; the ordinary
            # RMSE remains visible in the returned diagnostics.
            evidence_loss = max(float(np.mean(np.minimum(
                residual ** 2, (3.0 * robust_scale) ** 2))), 1e-12)
        score = n * float(np.log(evidence_loss)) + parameter_count * float(np.log(max(n, 2)))
        score += self.complexity * float(np.log(max(n, 2)))
        return {
            "hypothesis_id": self.hypothesis_id, "family": self.family,
            "origin": self.origin, "complexity": self.complexity,
            "structure_params": list(self.structure_params),
            "parameters": coefficients.tolist(), "parameter_count": parameter_count,
            "rmse_normalized": float(np.sqrt(mse)), "bic_mdl": float(score),
            "evidence_loss": float(evidence_loss),
            "fit_mode": "huber_irls" if robust and len(target) >= 8 else "least_squares",
            "downweighted_fraction": float(np.mean(weights < 0.999)),
            "log_score": float(-0.5 * score + prior_log_weight),
            "prediction_normalized": prediction.tolist(),
        }

    def predict(self, x: np.ndarray, parameters: list[float]) -> np.ndarray:
        return self.design(x) @ np.asarray(parameters, dtype=float)


def hypothesis_catalog(*, origin: str = "known_library") -> list[FunctionHypothesis]:
    rows = [
        FunctionHypothesis("constant", "constant", 1.0, (), origin),
        FunctionHypothesis("linear", "polynomial", 2.0, (1.0,), origin),
        FunctionHypothesis("quadratic", "polynomial", 3.0, (2.0,), origin),
        FunctionHypothesis("cubic", "polynomial", 4.0, (3.0,), origin),
        FunctionHypothesis("quartic", "polynomial", 5.0, (4.0,), origin),
    ]
    # Structural parameters remain typed and auditable, but the grid is dense
    # enough that a provider is not restricted to the few synthetic training
    # values.  This is still hypothesis generation, not evidence scoring.
    rows.extend(FunctionHypothesis(f"fourier_f{v:g}", "fourier", 4.0, (v,), origin)
                for v in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0))
    rows.extend(FunctionHypothesis(f"exp_decay_r{v:g}", "exp_decay", 3.0, (v,), origin)
                for v in (0.5, 1.0, 2.0, 3.0, 4.0))
    rows.extend(FunctionHypothesis(f"hinge_k{v:g}", "hinge", 4.0, (v,), origin)
                for v in (0.25, 0.375, 0.5, 0.625, 0.75))
    rows.extend(FunctionHypothesis(f"reciprocal_r{v:g}", "reciprocal", 3.5, (v,), origin)
                for v in (0.5, 1.0, 2.0, 4.0))
    rows.extend(FunctionHypothesis(f"modulated_f{v:g}", "modulated", 5.5, (v,), origin)
                for v in (1.0, 2.0, 3.0))
    return rows
