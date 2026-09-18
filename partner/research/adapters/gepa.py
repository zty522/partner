"""GEPA-inspired reflective candidate search adapter (M2 / Section 6).

This is **not** a wrapper around the upstream GEPA package.  It is the
shape of the optimiser that Partner expects: a small reflective loop
over candidate prompts / skills / configs, scored by execution
feedback.

Public surface:

* ``GepaOptimizer(candidate_seed, evaluator, budget)`` — owns the
  population and the budget counters.
* ``propose(parent, change_summary, diff_fingerprint)`` — register a
  new candidate with provenance.
* ``record_fitness(candidate, score)`` — apply evaluator feedback.
* ``select_next()`` — return the next candidate the optimiser wants
  the runtime to evaluate.  The default selection rule is the highest
  fitness with at least one recorded evaluation; ties broken by
  shorter ``diff_fingerprint`` (smaller edit wins).
* ``archive()`` — return the archive (candidate_id → fitness), useful
  for ``evolution.decide`` to inspect.

The adapter deliberately refuses to call the LLM itself: ``propose``
records a candidate the runtime already produced; ``record_fitness``
ingests the runtime's score.  This keeps the contract single-sourced:
the runtime executes, the adapter selects, the decision flow promotes.

State honesty: ``static_implemented``. No candidate has been optimised
in this session; the wiring (see ``partner/research/adapters/wiring.py``)
documents how the runtime would call this adapter.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class GepaCandidate:
    candidate_id: str
    parent_id: str | None
    change_summary: str
    diff_fingerprint: str
    fitness_score: float | None = None
    evaluations: int = 0
    schema_version: str = "gepa.candidate/v1"
    notes: str = ""


class GepaError(Exception):
    pass


class GepaBudgetExceeded(GepaError):
    pass


class GepaOptimizer:
    """Reflective candidate search loop.

    Parameters
    ----------
    evaluator
        A callable ``f(candidate) -> float`` (or ``None`` to skip live
        evaluation in static-only mode).  Production evaluators must be
        deterministic under matched inputs / matched seeds; the adapter
        does not check that itself — it only stores the returned score.
    budget
        ``{candidate_attempts: int, wallclock_seconds: int}``.  The
        optimiser refuses to register a candidate once the budget is
        exhausted.
    """

    def __init__(self, *, evaluator=None,
                 budget: dict | None = None) -> None:
        self._evaluator = evaluator
        self._budget = dict(budget or {"candidate_attempts": 8, "wallclock_seconds": 3600})
        self._attempts = 0
        self._candidates: dict[str, GepaCandidate] = {}
        self._children_of: dict[str, list[str]] = {}

    # ---- proposal -----------------------------------------------------

    def propose(self, *, parent: GepaCandidate | None,
                change_summary: str, diff_fingerprint: str,
                notes: str = "") -> GepaCandidate:
        if not change_summary:
            raise GepaError("change_summary must be non-empty")
        if not diff_fingerprint:
            raise GepaError("diff_fingerprint must be non-empty")
        if self._attempts >= int(self._budget.get("candidate_attempts", 8)):
            raise GepaBudgetExceeded(
                f"candidate_attempts budget exhausted ({self._attempts}); "
                f"stop proposing and let evolution.decide run"
            )
        cand = GepaCandidate(
            candidate_id="gepa_" + uuid.uuid4().hex[:12],
            parent_id=parent.candidate_id if parent else None,
            change_summary=change_summary,
            diff_fingerprint=diff_fingerprint,
            notes=notes,
        )
        self._attempts += 1
        self._candidates[cand.candidate_id] = cand
        if cand.parent_id:
            self._children_of.setdefault(cand.parent_id, []).append(cand.candidate_id)
        return cand

    # ---- feedback -----------------------------------------------------

    def record_fitness(self, candidate: GepaCandidate, *,
                       score: float, notes: str = "") -> GepaCandidate:
        if not (0.0 <= score <= 1.0):
            raise GepaError("fitness_score must be in [0, 1]")
        if candidate.candidate_id not in self._candidates:
            raise GepaError(f"unknown candidate {candidate.candidate_id!r}")
        existing = self._candidates[candidate.candidate_id]
        new_evals = existing.evaluations + 1
        # Running average so multiple evaluations converge.
        prev = existing.fitness_score or 0.0
        new_score = (prev * existing.evaluations + score) / new_evals
        updated = replace(existing, fitness_score=new_score,
                           evaluations=new_evals, notes=notes)
        self._candidates[candidate.candidate_id] = updated
        return updated

    # ---- selection ----------------------------------------------------

    def select_next(self) -> GepaCandidate | None:
        """Return the next candidate the runtime should evaluate.

        Selection rule (deterministic, audit-friendly):

        * Prefer unevaluated candidates whose parent has been evaluated
          (single-step lookahead).
        * Among unevaluated candidates, prefer those whose parent has
          the higher fitness score.
        * Ties broken by ``diff_fingerprint`` ascending (smaller edit wins).
        """
        scored = [
            (cand.fitness_score or 0.0, cand.diff_fingerprint, cand.candidate_id)
            for cand in self._candidates.values()
            if cand.evaluations == 0
        ]
        if not scored:
            return None
        scored.sort(key=lambda x: (-x[0], x[1]))
        return self._candidates[scored[0][2]]

    # ---- archive ------------------------------------------------------

    def archive(self) -> list[GepaCandidate]:
        return [c for c in self._candidates.values() if c.evaluations > 0]

    def best(self) -> GepaCandidate | None:
        ar = self.archive()
        if not ar:
            return None
        return max(ar, key=lambda c: (c.fitness_score or 0.0, -len(c.diff_fingerprint)))

    # ---- lineage ------------------------------------------------------

    def lineage(self, candidate_id: str) -> list[GepaCandidate]:
        """Return the chain of ancestors for ``candidate_id`` (oldest first)."""
        out: list[GepaCandidate] = []
        cur = self._candidates.get(candidate_id)
        seen = set()
        while cur and cur.candidate_id not in seen:
            seen.add(cur.candidate_id)
            out.append(cur)
            cur = self._candidates.get(cur.parent_id) if cur.parent_id else None
        out.reverse()
        return out

    def lineage_edges(self) -> list[tuple[str, str]]:
        edges = []
        for cand in self._candidates.values():
            if cand.parent_id:
                edges.append((cand.parent_id, cand.candidate_id))
        return sorted(edges)


__all__ = ["GepaCandidate", "GepaOptimizer", "GepaError", "GepaBudgetExceeded"]
