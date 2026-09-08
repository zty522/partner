"""Observe → recall → propose → fit → revise → actively query."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..active_learning import gaussian_information_gain

from .events import WorldModelEventLedger
from .hypotheses import FunctionHypothesis
from .memory import AssociativeWorldMemory
from .providers import HypothesisProvider, LibraryHypothesisProvider


@dataclass
class _SessionBelief:
    posterior: dict[str, float] = field(default_factory=dict)
    top_hypothesis: str = ""


class WorldModelEngine:
    def __init__(self, *, provider: HypothesisProvider | None = None,
                 memory: AssociativeWorldMemory | None = None,
                 ledger: WorldModelEventLedger | None = None,
                 robust_evidence: bool = True) -> None:
        self.provider = provider or LibraryHypothesisProvider()
        self.memory = memory or AssociativeWorldMemory()
        self.ledger = ledger
        self.robust_evidence = bool(robust_evidence)
        self.sessions: dict[str, _SessionBelief] = {}
        self.step = 0

    @staticmethod
    def _normalize(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
        scale = {"x_min": float(np.min(x)), "x_scale": max(float(np.max(x) - np.min(x)), 1e-8),
                 "y_mean": float(np.mean(y)), "y_scale": max(float(np.std(y)), 1e-8)}
        return ((x - scale["x_min"]) / scale["x_scale"],
                (y - scale["y_mean"]) / scale["y_scale"], scale)

    @staticmethod
    def _signature(x: np.ndarray, y: np.ndarray) -> list[float]:
        ys = y[np.argsort(x)]
        diffs = np.diff(ys)
        turning = float(np.mean(np.sign(diffs[1:]) != np.sign(diffs[:-1]))) if len(diffs) > 1 else 0.0
        monotonic = float(abs(np.mean(np.sign(diffs)))) if len(diffs) else 0.0
        lag = float(np.corrcoef(ys[:-1], ys[1:])[0, 1]) if len(ys) > 2 else 0.0
        return [min(1.0, len(x) / 32.0), float(np.std(y) / (1 + np.std(y))), monotonic,
                turning, (lag + 1) / 2 if np.isfinite(lag) else 0.5]

    @staticmethod
    def _posterior(fits: list[dict[str, Any]]) -> None:
        maximum = max(row["log_score"] for row in fits)
        weights = np.asarray([math.exp(max(-700, row["log_score"] - maximum)) for row in fits])
        weights /= max(float(weights.sum()), 1e-12)
        for row, weight in zip(fits, weights):
            row["posterior"] = float(weight)

    @staticmethod
    def _active_query(hypotheses: dict[str, FunctionHypothesis], fits: list[dict[str, Any]],
                      xn: np.ndarray, scale: dict[str, float]) -> dict[str, Any]:
        grid = np.linspace(0, 1, 201)
        top = sorted(fits, key=lambda row: row["posterior"], reverse=True)[:min(8, len(fits))]
        predictions = np.stack([hypotheses[row["hypothesis_id"]].predict(grid, row["parameters"])
                                for row in top])
        weights = np.asarray([row["posterior"] for row in top]); weights /= weights.sum()
        mean = np.sum(predictions * weights[:, None], axis=0)
        disagreement = np.sum((predictions - mean) ** 2 * weights[:, None], axis=0)
        distance = np.min(abs(grid[:, None] - xn[None, :]), axis=1)
        noise_variance = max(float(top[0].get("evidence_loss") or 0.0), 1e-4)
        information_gain = np.asarray([
            gaussian_information_gain(value, noise_variance) for value in disagreement
        ])
        coverage = np.clip(distance * 12, 0, 1)
        score = information_gain * coverage
        if not np.any(score > 0):
            score = distance
        index = int(np.argmax(score))
        return {"x": scale["x_min"] + float(grid[index]) * scale["x_scale"],
                "x_normalized": float(grid[index]), "model_disagreement": float(disagreement[index]),
                "expected_information_gain": float(information_gain[index]),
                "acquisition_score": float(score[index]),
                "acquisition_strategy": "gaussian_information_gain_x_coverage_v1",
                "reason": "maximize information value while avoiding already observed points"}

    def observe(self, x: list[float] | np.ndarray, y: list[float] | np.ndarray, *,
                domain_id: str = "default") -> dict[str, Any]:
        xa, ya = np.asarray(x, dtype=float).reshape(-1), np.asarray(y, dtype=float).reshape(-1)
        if len(xa) != len(ya) or len(xa) < 3 or len(np.unique(xa)) < 3:
            raise ValueError("observations need matching x/y and at least three distinct x values")
        if not np.all(np.isfinite(xa)) or not np.all(np.isfinite(ya)):
            raise ValueError("observations must be finite")
        self.step += 1
        episode_id = f"wm_episode_{domain_id}_{self.step:06d}"
        evidence_digest = hashlib.sha256(np.column_stack((xa, ya)).tobytes()).hexdigest()
        parents: list[str] = []
        if self.ledger:
            event = self.ledger.append("observation/recorded", episode_id=episode_id,
                                       domain_id=domain_id,
                                       payload={"count": len(xa), "digest": evidence_digest},
                                       evidence_refs=[f"sha256:{evidence_digest}"])
            parents = [event["event_id"]]
        xn, yn, scale = self._normalize(xa, ya)
        signature = self._signature(xn, yn)
        retrieved = self.memory.retrieve(signature, step=self.step)
        if self.ledger:
            event = self.ledger.append("memory/retrieved", episode_id=episode_id, domain_id=domain_id,
                                       payload={"trace_ids": [r["trace_id"] for r in retrieved]}, parents=parents)
            parents = [event["event_id"]]
        simple = LibraryHypothesisProvider("basic").propose(xn[:3], yn[:3], surprise=0.0)
        surprise = min(1.0, min(row.fit(xn, yn)["rmse_normalized"] for row in simple))
        proposed = self.provider.propose(xn, yn, surprise=surprise)
        provider_diagnostics = dict(getattr(self.provider, "last_diagnostics", {}))
        hypotheses = {row.hypothesis_id: row for row in proposed}
        priors = self.memory.prior_log_weights(retrieved)
        fits = [row.fit(xn, yn, prior_log_weight=priors.get(row.hypothesis_id, 0.0),
                        robust=self.robust_evidence) for row in proposed]
        self._posterior(fits); fits.sort(key=lambda row: row["posterior"], reverse=True)
        if self.ledger:
            event = self.ledger.append("hypothesis/proposed", episode_id=episode_id, domain_id=domain_id,
                                       payload={"provider_id": self.provider.provider_id,
                                                "hypothesis_ids": list(hypotheses),
                                                "provider_diagnostics": provider_diagnostics}, parents=parents)
            parents = [event["event_id"]]
        previous, top = self.sessions.get(domain_id, _SessionBelief()), fits[0]
        if previous.top_hypothesis:
            change = ({row["hypothesis_id"]: row["posterior"] for row in fits}.get(previous.top_hypothesis, 0.0)
                      - previous.posterior.get(previous.top_hypothesis, 0.0))
            self.memory.revise(hypothesis_id=previous.top_hypothesis, domain_id=domain_id,
                               signature=signature, step=self.step, evidence_count=len(xn),
                               confirmation=max(-1, min(1, change * 2)))
        self.memory.revise(hypothesis_id=top["hypothesis_id"], domain_id=domain_id,
                           signature=signature, step=self.step, evidence_count=len(xn),
                           confirmation=float(top["posterior"]))
        posterior = {row["hypothesis_id"]: row["posterior"] for row in fits}
        self.sessions[domain_id] = _SessionBelief(posterior, top["hypothesis_id"])
        entropy = -sum(value * math.log(max(value, 1e-12)) for value in posterior.values())
        effective_count = float(math.exp(entropy))
        if len(xa) < 8:
            evidence_status = "insufficient_evidence"
            evidence_reason = "fewer than eight observations cannot support structural selection"
        elif top["posterior"] < 0.65 or effective_count > 2.5:
            evidence_status = "ambiguous"
            evidence_reason = "posterior evidence does not clearly separate alternatives"
        else:
            evidence_status = "provisional"
            evidence_reason = "best current explanation; not a statement of causal truth"
        query = self._active_query(hypotheses, fits, xn, scale)
        event_ids: list[str] = []
        if self.ledger:
            belief = self.ledger.append("belief/revised", episode_id=episode_id, domain_id=domain_id,
                                        payload={"top": top["hypothesis_id"],
                                                 "posterior": top["posterior"]}, parents=parents)
            active = self.ledger.append("active_query/proposed", episode_id=episode_id, domain_id=domain_id,
                                        payload=query, parents=[belief["event_id"]])
            memory = self.ledger.append("memory/consolidated", episode_id=episode_id, domain_id=domain_id,
                                        payload={"trace_count": len(self.memory.traces)},
                                        parents=[active["event_id"]])
            event_ids = [belief["event_id"], active["event_id"], memory["event_id"]]
        return {"schema_version": 1, "episode_id": episode_id, "domain_id": domain_id,
                "provider_id": self.provider.provider_id, "observation_count": len(xa),
                "surprise_against_basic": surprise, "hypotheses_considered": len(fits),
                "effective_hypothesis_count": effective_count,
                "evidence_status": evidence_status, "evidence_reason": evidence_reason,
                "provider_diagnostics": provider_diagnostics, "top_hypothesis": top,
                "ranked_hypotheses": fits, "retrieved_memories": retrieved,
                "next_observation": query, "normalization": scale,
                "memory_trace_count": len(self.memory.traces), "event_ids": event_ids}

    @staticmethod
    def predict(result: dict[str, Any], x: list[float] | np.ndarray) -> np.ndarray:
        row, scale = result["top_hypothesis"], result["normalization"]
        hypothesis = FunctionHypothesis(row["hypothesis_id"], row["family"], row["complexity"],
                                        tuple(row.get("structure_params") or ()), row["origin"])
        xn = (np.asarray(x, dtype=float) - scale["x_min"]) / scale["x_scale"]
        normalized = hypothesis.predict(xn, row["parameters"])
        return normalized * scale["y_scale"] + scale["y_mean"]
