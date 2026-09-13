"""Domain-neutral value-of-information contracts for active learning.

The same selector can rank a numeric measurement, a tool inspection, a
matched resample, or a bounded repair experiment. It never executes an option
and never promotes a policy; execution remains Event-first.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


def _entropy(probabilities: dict[str, float]) -> float:
    return -sum(value * math.log(max(value, 1e-12))
                for value in probabilities.values() if value > 0.0)


def _normalized(values: dict[str, float]) -> dict[str, float]:
    clean = {str(key): max(0.0, float(value)) for key, value in values.items()}
    total = sum(clean.values())
    if total <= 0.0:
        raise ValueError("probability mass must be positive")
    return {key: value / total for key, value in clean.items()}


def expected_information_gain(
    hypothesis_prior: dict[str, float],
    outcome_likelihoods: dict[str, dict[str, float]],
) -> float:
    """Return I(H; outcome) for one proposed experiment in nats."""
    prior = _normalized(hypothesis_prior)
    likelihoods = {hypothesis: _normalized(outcomes)
                   for hypothesis, outcomes in outcome_likelihoods.items()}
    if set(prior) - set(likelihoods):
        raise ValueError("every prior hypothesis needs outcome likelihoods")
    outcomes = sorted({outcome for values in likelihoods.values() for outcome in values})
    prior_entropy = _entropy(prior)
    expected_posterior_entropy = 0.0
    for outcome in outcomes:
        probability = sum(prior[hypothesis] * likelihoods[hypothesis].get(outcome, 0.0)
                          for hypothesis in prior)
        if probability <= 0.0:
            continue
        posterior = {
            hypothesis: (prior[hypothesis] * likelihoods[hypothesis].get(outcome, 0.0)
                         / probability)
            for hypothesis in prior
        }
        expected_posterior_entropy += probability * _entropy(posterior)
    return max(0.0, prior_entropy - expected_posterior_entropy)


def gaussian_information_gain(predictive_variance: float, noise_variance: float) -> float:
    """Information from one scalar Gaussian observation in nats."""
    return 0.5 * math.log1p(max(0.0, predictive_variance) /
                            max(float(noise_variance), 1e-9))


@dataclass(frozen=True)
class ActiveLearningOption:
    option_id: str
    event_type: str
    outcome_likelihoods: dict[str, dict[str, float]]
    task_value: float = 1.0
    novelty: float = 1.0
    cost: float = 0.0
    risk: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()


def rank_active_learning_options(
    hypothesis_prior: dict[str, float],
    options: list[ActiveLearningOption],
) -> list[dict[str, Any]]:
    """Rank options by information × task value × novelty − cost − risk."""
    if not options:
        raise ValueError("at least one active-learning option is required")
    ranked = []
    for option in options:
        information = expected_information_gain(hypothesis_prior, option.outcome_likelihoods)
        score = (information * max(0.0, option.task_value) * max(0.0, option.novelty)
                 - max(0.0, option.cost) - max(0.0, option.risk))
        row = asdict(option)
        row.update({"expected_information_gain": information, "acquisition_score": score})
        ranked.append(row)
    ranked.sort(key=lambda row: (-row["acquisition_score"], row["option_id"]))
    return ranked


@dataclass
class ActiveLearningMemory:
    """Small evidence memory for action success; raw Episodes stay external."""
    outcomes: dict[str, dict[str, int]] = field(default_factory=dict)
    observation_ids: set[str] = field(default_factory=set)

    def observe(self, context_key: str, option_id: str, *, success: bool,
                observation_id: str = "") -> bool:
        if observation_id and observation_id in self.observation_ids:
            return False
        key = f"{context_key}|{option_id}"
        counts = self.outcomes.setdefault(key, {"success": 0, "failure": 0})
        counts["success" if success else "failure"] += 1
        if observation_id:
            self.observation_ids.add(observation_id)
        return True

    def success_probability(self, context_key: str, option_id: str) -> float:
        counts = self.outcomes.get(f"{context_key}|{option_id}", {})
        return ((int(counts.get("success") or 0) + 1) /
                (int(counts.get("success") or 0) + int(counts.get("failure") or 0) + 2))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 2, "outcomes": self.outcomes,
                "observation_ids": sorted(self.observation_ids)}

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ActiveLearningMemory":
        rows = dict((value or {}).get("outcomes") or {})
        return cls(
            outcomes={str(key): {"success": int(counts.get("success") or 0),
                                 "failure": int(counts.get("failure") or 0)}
                      for key, counts in rows.items() if isinstance(counts, dict)},
            observation_ids={str(item) for item in (value or {}).get("observation_ids") or []
                             if str(item)},
        )
