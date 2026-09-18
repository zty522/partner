"""Real evaluator for GepaOptimizer (M2 / 6.4).

``GepaOptimizer`` accepts any callable ``(candidate) -> float``.  This
module ships a concrete evaluator built on the autonomous_evolution
compare node's ``criteria_results`` so the fitness signal is real.

Three evaluators are exposed:

* ``compare_evaluator`` — uses the matched_tests /
  regression_passed flags from a prior compare pass.
* ``terminal_evaluator`` — uses the JobRepository final_status
  (``completed`` -> 1.0, ``failed`` -> 0.0, anything else -> None).
* ``composite_evaluator`` — combines the two with weights.

All evaluators return ``None`` when the signal is not available,
which the GepaOptimizer translates to "no score yet".
"""
from __future__ import annotations

from typing import Any, Callable


def compare_evaluator(candidate, *, criteria_results: dict | None) -> float | None:
    """Return a fitness score from a compare node's ``criteria_results``.

    Score = matched_tests * weight_a + regression_passed * weight_b
    where ``matched_tests`` and ``regression_passed`` are boolean.
    """
    if not criteria_results:
        return None
    matched = bool(criteria_results.get("matched_tests"))
    regression_passed = bool(criteria_results.get("regression_passed"))
    expectations = bool(criteria_results.get("expectations_met"))
    if matched and regression_passed and expectations:
        return 1.0
    if matched and regression_passed:
        return 0.7
    if matched:
        return 0.4
    return 0.0


def terminal_evaluator(candidate, *, final_status: str | None) -> float | None:
    if final_status == "completed":
        return 1.0
    if final_status == "failed":
        return 0.0
    return None


def composite_evaluator(*, criteria_results: dict | None,
                        final_status: str | None,
                        w_compare: float = 0.6,
                        w_terminal: float = 0.4) -> float | None:
    s_c = compare_evaluator(None, criteria_results=criteria_results)
    s_t = terminal_evaluator(None, final_status=final_status)
    if s_c is None and s_t is None:
        return None
    if s_c is None:
        return w_terminal * s_t
    if s_t is None:
        return w_compare * s_c
    return w_compare * s_c + w_terminal * s_t


def make_evaluator(*, compare_provider: Callable[[Any], dict | None],
                    terminal_provider: Callable[[Any], str | None]) -> Callable[[Any], float | None]:
    """Factory that returns an evaluator closure.

    The closure calls the two providers on the candidate; this lets the
    autonomous_evolution flow plug in real data without coupling the
    adapter to the cycle's internal save() helper.
    """
    def _evaluator(candidate):
        criteria = compare_provider(candidate)
        terminal = terminal_provider(candidate)
        return composite_evaluator(
            criteria_results=criteria,
            final_status=terminal,
        )
    return _evaluator


__all__ = [
    "compare_evaluator", "terminal_evaluator", "composite_evaluator",
    "make_evaluator",
]
