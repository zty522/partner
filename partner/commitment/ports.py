"""Ports the kernel depends on, plus deliberately inert implementations.

The kernel talks to the outside world only through these interfaces:

``StateReader``               read the frozen context snapshot
``CandidateProposer``         propose a *finite* set of candidate directions
``ActionExecutor``            run one bounded action and return a receipt
``IndependentEvaluator``      measure the artifacts, independently of the agent
``ExternalLearningRequester`` reserve only -- no active-learning loop here
``ConsequenceForecaster``     reserve only -- no world model here

The last two are ``Null``/``Shadow`` by design.  They exist so the boundary is
declared and typed, not so that a world model or an active-learning loop can be
smuggled into this round.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .models import (
    BetRecord, Candidate, ExecutionReceipt, OutcomeMeasurement, sha256_of,
)


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


@dataclass
class FrozenClock:
    """Deterministic clock for tests and fixtures."""

    current: float = 1_700_000_000.0

    def now(self) -> float:
        return float(self.current)

    def advance(self, seconds: float) -> float:
        self.current += float(seconds)
        return self.current


# ---------------------------------------------------------------------------
# proposal
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProposalResult:
    """Outcome of asking for candidates.

    ``failure`` is kept separate from ``candidates``: a proposer that produced
    nothing because the model was unreachable is a dependency problem, not a
    judgement that no direction exists.
    """

    candidates: tuple[Candidate, ...]
    proposed_by: str
    model_calls: int = 0
    provider: str = ""
    model: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    trace_ref: str = ""
    failure: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.candidates) and not self.failure

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "proposed_by": self.proposed_by, "model_calls": int(self.model_calls),
            "provider": self.provider, "model": self.model, "usage": dict(self.usage),
            "latency_ms": float(self.latency_ms), "trace_ref": self.trace_ref,
            "failure": self.failure,
        }


@runtime_checkable
class StateReader(Protocol):
    def read_frozen_state(self) -> Mapping[str, Any]: ...


@runtime_checkable
class CandidateProposer(Protocol):
    def propose(self, *, question: str, snapshot: Mapping[str, Any],
                max_candidates: int) -> ProposalResult: ...


@runtime_checkable
class ActionExecutor(Protocol):
    def execute(self, *, bet: BetRecord, candidate: Candidate,
                workspace: str) -> ExecutionReceipt: ...


@runtime_checkable
class IndependentEvaluator(Protocol):
    """Returns the full measurement set for one execution.

    The prompt's ``OutcomeMeasurement`` is the per-metric record; an execution
    normally produces one per declared metric, so the port returns the set and
    the caller selects the primary one.  ``evaluator.MeasurementResult`` is the
    concrete shape.
    """

    def measure(self, *, bet: BetRecord, receipt: ExecutionReceipt) -> Any: ...


@runtime_checkable
class ExternalLearningRequester(Protocol):
    def request(self, *, question: str, missing: Sequence[str]) -> Mapping[str, Any]: ...


@runtime_checkable
class ConsequenceForecaster(Protocol):
    def forecast(self, *, snapshot: Mapping[str, Any],
                 candidate: Candidate) -> Mapping[str, Any]: ...


# ---------------------------------------------------------------------------
# deliberately inert implementations
# ---------------------------------------------------------------------------

class NullExternalLearningRequester:
    """Declared but inert.  This round implements no active-learning loop."""

    mode = "null"

    def request(self, *, question: str, missing: Sequence[str]) -> Mapping[str, Any]:
        return {"mode": self.mode, "granted": False,
                "reason": "external learning is reserved in this round and never granted",
                "question_hash": sha256_of({"q": question, "missing": list(missing)})}


class ShadowConsequenceForecaster:
    """Declared but inert.  This round implements no world model.

    It returns a shadow record that is *never* allowed to influence selection or
    settlement; callers must treat ``authoritative`` as False.
    """

    mode = "shadow"

    def forecast(self, *, snapshot: Mapping[str, Any],
                 candidate: Candidate) -> Mapping[str, Any]:
        return {"mode": self.mode, "authoritative": False, "prediction": None,
                "reason": "shadow forecaster: no prediction is produced or consumed",
                "input_hash": sha256_of({"snapshot_keys": sorted(snapshot),
                                         "candidate": candidate.candidate_id})}


__all__ = [
    "BaselineEvidenceProvider",
    "Clock", "SystemClock", "FrozenClock", "ProposalResult", "StateReader",
    "CandidateProposer", "ActionExecutor", "IndependentEvaluator",
    "ExternalLearningRequester", "ConsequenceForecaster",
    "NullExternalLearningRequester", "ShadowConsequenceForecaster",
]


@runtime_checkable
class BaselineEvidenceProvider(Protocol):
    """Produces admissible baseline evidence for a bet.

    Kept as a port so the kernel never has to know how a baseline is produced.
    The production adapter runs the control arm through the same executor and
    evaluator; a surrogate that only *declares* a baseline is not an
    implementation of this port.
    """

    def provide(self, *, bet: BetRecord, store: Any) -> "BaselineEvidence | None": ...


