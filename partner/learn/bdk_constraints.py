"""Numpy-only sparsity audit retained for legacy FunctionPool Candidates."""
from __future__ import annotations

from typing import Any
import numpy as np


def _probabilities(values) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64).flatten()
    if not len(array): return array
    if array.min() < 0 or array.max() > 1 or not np.isclose(array.sum(), 1.0, atol=0.05):
        exponent = np.exp(array - array.max()); array = exponent / exponent.sum()
    return array


def apply_ocamms_constraint(values, margin: float = 0.2, max_activated: int = 2) -> bool:
    if values is None or len(values) == 0: return True
    return int(np.sum(_probabilities(values) > margin)) <= max_activated


def apply_ocamms_constraint_with_advice(values, margin: float = 0.2,
                                         max_activated: int = 2):
    if values is None or len(values) == 0: return True, None, "empty_logits_passed"
    probabilities = _probabilities(values); active = int(np.sum(probabilities > margin))
    if active <= max_activated:
        return True, None, f"passed: {active} activated <= {max_activated}"
    indices = np.argsort(probabilities)[::-1][:max_activated]
    suggestion = np.zeros_like(probabilities); suggestion[indices] = probabilities[indices]
    suggestion /= max(float(suggestion.sum()), 1e-12)
    return False, suggestion, (f"failed: {active} activated > {max_activated}; "
                               f"suggested keep kernels {indices.tolist()}")


def apply_to_dict(values: dict[str, float], margin: float = 0.2,
                  max_activated: int = 2) -> dict[str, Any]:
    names = list(values); probabilities = [values[name] for name in names]
    passed, suggestion, message = apply_ocamms_constraint_with_advice(probabilities, margin, max_activated)
    return {"passed": passed, "kernel_names": names,
            "activated_names": [name for name, value in values.items() if value > margin],
            "suggested_names": [] if suggestion is None else
            [names[index] for index, value in enumerate(suggestion) if value > margin],
            "message": message}
