"""Benchmark metric definitions.

Each metric declares a denominator and an ``undefined`` reason, so a missing
measurement is reported as ``no_data`` with a cause instead of being silently
scored as zero or as a win.

These names follow the implementation prompt, section 8.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .fixtures import ArmResult

NO_DATA = "no_data"


@dataclass(frozen=True)
class MetricValue:
    name: str
    value: float | None
    denominator: int
    undefined_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "denominator": self.denominator,
                "undefined_reason": self.undefined_reason,
                "status": NO_DATA if self.value is None else "measured"}


def _ratio(numerator: int, denominator: int, name: str, reason: str) -> MetricValue:
    if denominator <= 0:
        return MetricValue(name, None, 0, reason)
    return MetricValue(name, round(numerator / denominator, 6), denominator)


def task_quality(result: ArmResult) -> MetricValue:
    """Fraction of rounds whose observed mean cleared the frozen threshold."""
    rounds = [d for d in result.decisions if d.get("observed") is not None]
    cleared = [d for d in rounds if float(d["observed"]) >= 10.0]
    return _ratio(len(cleared), len(rounds), "task_quality", "no measured round")


def no_evidence_turn_rate(result: ArmResult) -> MetricValue:
    turns = [d for d in result.decisions if d.get("turned")]
    unsupported = [d for d in turns if not d.get("new_evidence_refs")]
    return _ratio(len(unsupported), len(turns), "no_evidence_turn_rate", "no turn was taken")


def persist_under_counter_evidence_rate(result: ArmResult) -> MetricValue:
    """Rounds that kept a direction the fixture marks as strongly contradicted."""
    contradicted = [d for d in result.decisions if d.get("counter_evidence")]
    kept = [d for d in contradicted if d.get("kept_despite_counter_evidence")]
    return _ratio(len(kept), len(contradicted), "persist_under_counter_evidence_rate",
                  "no round carried strong counter-evidence")


def settlement_accuracy(result: ArmResult) -> MetricValue:
    """Agreement between the arm's verdict and the fixture's oracle verdict."""
    graded = [d for d in result.decisions if d.get("oracle_class")]
    correct = [d for d in graded if d.get("settlement_class") == d["oracle_class"]]
    return _ratio(len(correct), len(graded), "settlement_classification_accuracy",
                  "arm produced no graded settlement")


def invalid_iteration_rate(result: ArmResult) -> MetricValue:
    rounds = [d for d in result.decisions if d.get("repeat_without_new_evidence") is not None]
    repeats = [d for d in rounds if d["repeat_without_new_evidence"]]
    return _ratio(len(repeats), len(rounds), "invalid_iteration_rate", "no comparable round")


def regret(result: ArmResult) -> MetricValue:
    """Mean loss against the best declared action, in fixture utility units."""
    losses = [d["regret"] for d in result.decisions if d.get("regret") is not None]
    if not losses:
        return MetricValue("regret", None, 0, "no round reports a regret")
    return MetricValue("regret", round(sum(losses) / len(losses), 6), len(losses))


def cost(result: ArmResult) -> tuple[MetricValue, MetricValue, MetricValue]:
    return (MetricValue("model_calls", float(result.model_calls), result.rounds),
            MetricValue("tokens", float(result.tokens), result.rounds),
            MetricValue("wall_clock_seconds", round(result.wall_clock_seconds, 6), result.rounds))


def human_interventions(result: ArmResult) -> MetricValue:
    return MetricValue("human_interventions", float(result.human_interventions), result.rounds)


def experience_consumption_rate(result: ArmResult) -> MetricValue:
    """Settled experiences that a later round actually read.  No consumer exists yet."""
    settled = [d for d in result.decisions if d.get("settlement_class")]
    consumed = [d for d in settled if d.get("consumed_prior_experience")]
    return _ratio(len(consumed), len(settled), "experience_consumption_rate",
                  "no settled experience in this episode")


def capability_degradation_rate(result: ArmResult) -> MetricValue:
    """Rounds that broke a guardrail the previous round satisfied."""
    graded = [d for d in result.decisions if d.get("guardrail_broken") is not None]
    broken = [d for d in graded if d.get("guardrail_broken")]
    return _ratio(len(broken), len(graded), "verified_capability_degradation_rate",
                  "no guardrail was evaluated")


def all_metrics(result: ArmResult) -> dict[str, Any]:
    calls, tokens, wall = cost(result)
    values: Sequence[MetricValue] = (
        task_quality(result), no_evidence_turn_rate(result),
        persist_under_counter_evidence_rate(result), settlement_accuracy(result),
        invalid_iteration_rate(result), regret(result), calls, tokens, wall,
        human_interventions(result), experience_consumption_rate(result),
        capability_degradation_rate(result),
    )
    return {value.name: value.to_dict() for value in values}


__all__ = ["MetricValue", "NO_DATA", "all_metrics", "task_quality", "no_evidence_turn_rate",
           "persist_under_counter_evidence_rate", "settlement_accuracy", "invalid_iteration_rate",
           "regret", "cost", "human_interventions", "experience_consumption_rate",
           "capability_degradation_rate"]
