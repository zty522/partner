"""Deterministic benchmark fixture shared by all three arms.

Every arm sees this same episode: the same initial snapshot, the same budget, the
same evaluator, the same candidate space and the same seeds.  Parity is a
property of construction and is asserted in
``tests/commitment/test_benchmark_parity.py`` -- if the arms can differ in
budget or instrument, a comparison between them is meaningless.

The fixture is synthetic and is labelled as such.  It is an execution skeleton
for the loop, not evidence about research quality.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from partner.commitment import models as M
from partner.commitment.evaluator import ArtifactIsolation, JsonMetricEvaluator
from partner.commitment.store import file_sha256

FIXTURE_LABEL = "synthetic"
BENCHMARK_ID = "commitment_loop_skeleton_v1"
SEEDS = (11, 12, 13)


# ---------------------------------------------------------------------------
# the synthetic task: raise a mean without breaking an upper guardrail
# ---------------------------------------------------------------------------

def mean_of(payload: Mapping[str, Any]) -> float:
    values = list(payload.get("values") or ())
    if not values:
        raise ValueError("no values")
    return round(sum(float(v) for v in values) / len(values), 6)


def max_of(payload: Mapping[str, Any]) -> float:
    values = list(payload.get("values") or ())
    if not values:
        raise ValueError("no values")
    return round(max(float(v) for v in values), 6)


def evaluator(*, allowed_roots: Sequence[str | Path] = ()) -> JsonMetricEvaluator:
    """One instrument, shared by every arm and every seed."""
    return JsonMetricEvaluator(
        evaluator_id="benchmark-deterministic-evaluator", evaluator_version="1.0.0",
        artifact_key="result.json", extractors={"mean": mean_of, "max": max_of},
        direction_by_metric={"mean": "increase", "max": "increase"},
        unit_by_metric={"mean": "unit", "max": "unit"},
        isolation=ArtifactIsolation(allowed_roots=allowed_roots) if allowed_roots else ArtifactIsolation())


def episode_snapshot(*, seed: int) -> dict[str, Any]:
    """The initial snapshot.  Only the declared numbers move with the seed."""
    jitter = (int(seed) % 3) * 0.5
    return {
        "fixture": FIXTURE_LABEL,
        "benchmark_id": BENCHMARK_ID,
        "seed": int(seed),
        "project_id": "synthetic_numbers",
        # Values the *control run* produces; the comparison no longer reads this
        # field, it is kept only for readability of the fixture.
        "baseline_metrics": {"mean": 7.333333, "max": 12.0},
        "constraints": {"max_values": 6},
        "candidate_space": [
            {"candidate_id": "cand_identity", "description": "no change",
             "params": {"shift": 0.0}, "prior": {"expected_gain": 0.0, "risk": 0.05},
             "rationale": "control direction"},
            {"candidate_id": "cand_improve", "description": "declared improvement",
             "params": {"shift": 4.0}, "prior": {"expected_gain": 0.35, "risk": 0.20},
             "rationale": "expected to clear the threshold"},
            {"candidate_id": "cand_risky", "description": "large but risky change",
             "params": {"shift": 40.0}, "prior": {"expected_gain": 0.95, "risk": 0.85},
             "rationale": "violates the guardrail"},
        ],
        "strong_counter_evidence": {"cand_improve": 6.0, "cand_identity": 8.0,
                                    "cand_risky": 12.0},
    }


def shared_budget() -> dict[str, int]:
    """One budget for every arm.  Absolute deadline is created per episode."""
    return {"wall_clock_seconds": 900, "model_calls": 4, "actions": 3, "rounds": 3}


def shared_protocol() -> M.EvaluationProtocol:
    return M.EvaluationProtocol(
        evaluator_id="benchmark-deterministic-evaluator", evaluator_version="1.0.0",
        metric_specs=({"metric": "mean"}, {"metric": "max"}))


def freeze_quote() -> tuple[tuple[M.ExpectedEffect, ...], tuple[M.FalsificationCondition, ...]]:
    """The frozen expectation and failure condition, identical for every arm."""
    effects = (M.ExpectedEffect(metric="mean", direction="increase", threshold=10.0, unit="unit",
                                kind="delta_over_baseline", min_delta=2.0),)
    conditions = (
        M.FalsificationCondition(code="threshold_not_met", kind="metric_violation",
                                 description="mean stayed below the frozen threshold",
                                 params={"metric": "mean", "threshold": 10.0}),
        M.FalsificationCondition(code="guardrail_broken", kind="guardrail_violation",
                                 description="max exceeded the declared guardrail",
                                 params={"metric": "max", "limit": 30.0}),
    )
    return effects, conditions


@dataclass
class Episode:
    """One benchmark episode, identical for every arm."""

    workspace: Path
    seed: int
    snapshot: dict[str, Any]
    budget_spec: dict[str, int] = field(default_factory=shared_budget)
    max_rounds: int = 3
    turns_allowed: int = 1
    environment: str = "synthetic_fixture"
    harness_version: str = "benchmark-harness-1"
    environment_fingerprint: str = "envfp:benchmark-fixture"

    def config_kwargs(self) -> dict[str, Any]:
        effects, conditions = freeze_quote()
        return {"effects": effects, "conditions": conditions}


@dataclass
class ArmResult:
    """What every arm must report, in the same shape, so they are comparable."""

    arm: str
    seed: int
    decisions: list[dict[str, Any]]
    model_calls: int = 0
    tokens: int = 0
    wall_clock_seconds: float = 0.0
    tool_calls: int = 0
    human_interventions: int = 0
    settlement_classes: list[str] = field(default_factory=list)
    published: int = 0
    notes: tuple[str, ...] = ()
    dependency_missing: tuple[str, ...] = ()

    @property
    def rounds(self) -> int:
        return len(self.decisions)

    def to_dict(self) -> dict[str, Any]:
        return {"arm": self.arm, "seed": self.seed, "rounds": self.rounds,
                "decisions": self.decisions, "model_calls": self.model_calls,
                "tokens": self.tokens, "wall_clock_seconds": self.wall_clock_seconds,
                "tool_calls": self.tool_calls, "human_interventions": self.human_interventions,
                "settlement_classes": list(self.settlement_classes),
                "published": self.published, "notes": list(self.notes),
                "dependency_missing": list(self.dependency_missing)}


class FixtureExecutor:
    """Executes the synthetic shift and writes the artifact the evaluator reads."""

    executor_id = "benchmark-fixture-executor"
    executor_version = "1.0.0"

    def __init__(self) -> None:
        self.executions = 0

    def execute(self, *, bet: M.BetRecord, candidate: M.Candidate,
                workspace: str) -> M.ExecutionReceipt:
        import time
        self.executions += 1
        out_dir = Path(workspace) / "state" / "benchmark_execution" / bet.bet_id
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / "result.json"
        shift = float(candidate.params.get("shift") or 0.0)
        values = [2.0 + shift, 8.0 + shift, 12.0 + shift]
        artifact.write_text(json.dumps({"values": values}), encoding="utf-8")
        now = time.time()
        return M.ExecutionReceipt(
            receipt_id=f"rcpt_{bet.bet_id}_{self.executions}", bet_id=bet.bet_id,
            requested_action=bet.selected_action, executed_action=bet.selected_action,
            executor_id=self.executor_id, executor_version=self.executor_version,
            started_epoch=now, finished_epoch=now + 0.001, status="completed", exit_code=0,
            artifacts=(str(artifact),), artifact_hashes={str(artifact): file_sha256(artifact)},
            log_ref="", data_hash=file_sha256(artifact), budget_consumed={"actions": 1},
            human_intervention=False, idempotency_key=f"bench:{bet.bet_id}:{self.executions}")
