"""Associative memory with reinforcement, contradiction and recoverable decay."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class MemoryTrace:
    trace_id: str
    hypothesis_id: str
    domain_id: str
    signature: list[float]
    strength: float = 1.0
    confirmations: int = 0
    contradictions: int = 0
    retrievals: int = 0
    last_seen_step: int = 0
    evidence_count: int = 0


class AssociativeWorldMemory:
    def __init__(self, traces: list[MemoryTrace] | None = None, *, decay: float = 0.015) -> None:
        self.traces = list(traces or [])
        self.decay = float(decay)

    @staticmethod
    def _similarity(left: list[float], right: list[float]) -> float:
        distance = float(np.linalg.norm(np.asarray(left) - np.asarray(right)) /
                         max(1.0, np.sqrt(len(left))))
        return math.exp(-distance)

    def retrieve(self, signature: list[float], *, step: int, top_k: int = 8) -> list[dict[str, Any]]:
        scored = []
        for trace in self.traces:
            age = max(0, int(step) - trace.last_seen_step)
            score = self._similarity(signature, trace.signature) * trace.strength * math.exp(-self.decay * age)
            scored.append((score, trace))
        result = []
        for score, trace in sorted(scored, key=lambda value: value[0], reverse=True)[:top_k]:
            trace.retrievals += 1
            result.append({**asdict(trace), "retrieval_score": float(score)})
        return result

    def revise(self, *, hypothesis_id: str, domain_id: str, signature: list[float],
               step: int, evidence_count: int, confirmation: float) -> MemoryTrace:
        candidates = [row for row in self.traces
                      if row.hypothesis_id == hypothesis_id and row.domain_id == domain_id]
        trace = max(candidates, key=lambda row: self._similarity(signature, row.signature), default=None)
        if trace is None or self._similarity(signature, trace.signature) < 0.82:
            trace = MemoryTrace(f"memory_{domain_id}_{hypothesis_id}_{len(self.traces) + 1}",
                                hypothesis_id, domain_id, list(signature),
                                last_seen_step=step, evidence_count=evidence_count)
            self.traces.append(trace)
        quality = max(-1.0, min(1.0, float(confirmation)))
        if quality >= 0:
            trace.confirmations += 1
            trace.strength = min(12.0, trace.strength * 0.98 + 0.35 + 1.25 * quality)
        else:
            trace.contradictions += 1
            trace.strength = max(0.05, trace.strength * (0.72 + 0.20 * (1.0 + quality)))
        trace.signature, trace.last_seen_step = list(signature), int(step)
        trace.evidence_count = max(trace.evidence_count, int(evidence_count))
        return trace

    def prior_log_weights(self, retrieved: list[dict[str, Any]]) -> dict[str, float]:
        weights: dict[str, float] = {}
        for row in retrieved:
            key = str(row["hypothesis_id"])
            weights[key] = max(weights.get(key, -20.0),
                               math.log1p(max(float(row["retrieval_score"]), 0.0)))
        return weights

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "decay": self.decay,
                "traces": [asdict(row) for row in self.traces]}

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "AssociativeWorldMemory":
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([MemoryTrace(**row) for row in value.get("traces") or []],
                   decay=float(value.get("decay") or 0.015))
