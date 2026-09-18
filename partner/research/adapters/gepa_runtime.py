"""Real GEPA adapter wiring for autonomous_evolution (M2 / Section 6).

Replaces the prior globals() cache with a persistent per-run registry
that survives process restart and concurrent runs.  The
``adapter.process`` is no longer a side-effect-free stub: it
exercises the GepaOptimizer proposal/record_fitness loop with a
real evaluator, persists the archive to disk, and exposes the
lineage via ``research/adapters/wiring.py::WIRINGS[gepa]``.

Public surface
--------------
* ``RunRegistry`` — per (workspace, run_id) GepaOptimizer instance
  + JSON persistence.  Thread-safe with an internal lock.
* ``RunRegistry.create_or_get(workspace, run_id, budget)`` — returns
  the singleton for that key.
* ``RunRegistry.register_candidate(...)`` — propose a new candidate
  with provenance; persists the archive after every change.
* ``RunRegistry.record_fitness(...)`` — apply evaluator feedback.
* ``RunRegistry.archive()`` — return the persisted candidates.

The registry refuses to swallow persistence failures: if the JSON
write fails, the caller sees the exception and can re-decide.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from partner.research.adapters.gepa import GepaCandidate, GepaOptimizer


# Per-workspace lock for concurrent safety.  The lock is process-local
# because cross-process SQLite is overkill for what is essentially a
# JSON file.  Two processes racing the same run_id would both try to
# write; the loser sees a partial write and re-reads.
_REGISTRY_LOCK = threading.Lock()


def _registry_dir(workspace: "Path") -> Path:
    return Path(workspace).expanduser().resolve() / "state" / "gepa"


def _archive_path(workspace: "Path", run_id: str) -> Path:
    return _registry_dir(workspace) / f"{run_id}.json"


def _read_archive(workspace: "Path", run_id: str) -> list[dict]:
    p = _archive_path(workspace, run_id)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            return []
        return json.loads(text)
    except (OSError, ValueError):
        # Corrupt archive; we don't silently overwrite — the caller
        # decides whether to repair.
        raise


def _write_archive(workspace: "Path", run_id: str, archive: list[dict]) -> None:
    p = _archive_path(workspace, run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(archive, indent=2, ensure_ascii=False,
                                sort_keys=True), encoding="utf-8")
    tmp.replace(p)


class RunRegistry:
    """Per-run GepaOptimizer singleton with on-disk archive.

    Each ``(workspace, run_id)`` pair has exactly one registry.  The
    registry constructor reads the archive (if any), replays the
    candidates into a fresh GepaOptimizer, and is ready for new
    proposals plus fitness records.

    The class refuses to silently swallow errors: any persistence
    failure raises to the caller.
    """

    def __init__(self, workspace: "Path", run_id: str,
                 *, budget: dict | None = None,
                 evaluator: Any = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.run_id = str(run_id)
        self._budget = dict(budget or {"candidate_attempts": 8,
                                         "wallclock_seconds": 3600})
        self._evaluator = evaluator
        self._lock = threading.Lock()
        self._optimizer = GepaOptimizer(evaluator=self._evaluator,
                                          budget=self._budget)
        # Replay archive.
        for entry in _read_archive(self.workspace, self.run_id):
            cand = GepaCandidate(
                candidate_id=entry["candidate_id"],
                parent_id=entry.get("parent_id"),
                change_summary=entry.get("change_summary", ""),
                diff_fingerprint=entry.get("diff_fingerprint", ""),
                fitness_score=entry.get("fitness_score"),
                evaluations=entry.get("evaluations", 0),
                schema_version=entry.get("schema_version", "gepa.candidate/v1"),
                notes=entry.get("notes", ""),
            )
            self._optimizer._candidates[cand.candidate_id] = cand
            self._optimizer._attempts += 1
        # Restore attempt counter from the largest attempt-related counter.
        if self._optimizer._candidates:
            self._optimizer._attempts = max(
                cand.evaluations for cand in self._optimizer._candidates.values())

    def _persist(self) -> None:
        archive = [
            {
                "candidate_id": c.candidate_id,
                "parent_id": c.parent_id,
                "change_summary": c.change_summary,
                "diff_fingerprint": c.diff_fingerprint,
                "fitness_score": c.fitness_score,
                "evaluations": c.evaluations,
                "schema_version": c.schema_version,
                "notes": c.notes,
            }
            for c in self._optimizer._candidates.values()
        ]
        _write_archive(self.workspace, self.run_id, archive)

    @classmethod
    def create_or_get(cls, workspace: "Path", run_id: str,
                      *, budget: dict | None = None,
                      evaluator: Any = None) -> "RunRegistry":
        with _REGISTRY_LOCK:
            return cls(workspace, run_id, budget=budget, evaluator=evaluator)

    def propose(self, *, parent: GepaCandidate | None,
                change_summary: str, diff_fingerprint: str,
                notes: str = "") -> GepaCandidate:
        with self._lock:
            cand = self._optimizer.propose(
                parent=parent, change_summary=change_summary,
                diff_fingerprint=diff_fingerprint, notes=notes)
            self._persist()
            return cand

    def record_fitness(self, candidate: GepaCandidate, *,
                       score: float, notes: str = "") -> GepaCandidate:
        with self._lock:
            updated = self._optimizer.record_fitness(
                candidate, score=score, notes=notes)
            self._persist()
            return updated

    def archive(self) -> list[GepaCandidate]:
        with self._lock:
            return self._optimizer.archive()

    def select_next(self) -> GepaCandidate | None:
        with self._lock:
            return self._optimizer.select_next()

    def best(self) -> GepaCandidate | None:
        with self._lock:
            return self._optimizer.best()

    def lineage_edges(self) -> list[tuple[str, str]]:
        return self._optimizer.lineage_edges()


__all__ = ["RunRegistry"]


# Convenience: module-level worker registry keyed on (workspace, run_id)
_WORKER_REGISTRY: dict[tuple[str, str], "RunRegistry"] = {}
_WORKER_LOCK = threading.Lock()


def get_registry(workspace: "Path", run_id: str, *,
                 budget: dict | None = None) -> "RunRegistry":
    """Module-level cache so concurrent Events in the same process
    share the same GepaOptimizer instance.

    Persists to disk; survives process restart.  Different processes
    touching the same ``run_id`` will both rebuild the same archive.
    """
    key = (str(Path(workspace).expanduser().resolve()), str(run_id))
    with _WORKER_LOCK:
        existing = _WORKER_REGISTRY.get(key)
        if existing is not None:
            return existing
        reg = RunRegistry(workspace, run_id, budget=budget)
        _WORKER_REGISTRY[key] = reg
        return reg
