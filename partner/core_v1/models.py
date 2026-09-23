"""Versioned, JSON-safe contracts shared by every Core v1 Event."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping
import json


SCHEMA_VERSION = 1


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


class CoreMode(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    ADVISORY = "advisory"
    GATED = "gated"


class Route(str, Enum):
    CONTINUE_PROJECT = "continue_project"
    ACTIVE_LEARNING = "active_learning"
    SELF_EVOLUTION = "self_evolution"
    WAITING = "waiting"
    COMPLETE = "complete"


@dataclass(frozen=True)
class DecisionState:
    state_id: str
    flow_id: str
    project_id: str
    instance_id: str
    domain: str
    objective: str
    facts: Mapping[str, Any]
    numeric_features: Mapping[str, float]
    evidence_refs: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def create(cls, *, flow_id: str, project_id: str, instance_id: str,
               domain: str, objective: str, facts: Mapping[str, Any],
               numeric_features: Mapping[str, float], evidence_refs=()) -> "DecisionState":
        body = {
            "flow_id": flow_id, "project_id": project_id, "instance_id": instance_id,
            "domain": domain, "objective": objective, "facts": dict(facts),
            "numeric_features": {str(k): float(v) for k, v in numeric_features.items()},
            "evidence_refs": sorted({str(v) for v in evidence_refs if v}),
        }
        return cls(state_id=f"state_{digest(body)[:16]}", **body)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAction:
    candidate_id: str
    event_type: str
    description: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    expected_observation: str = ""
    disproof: str = ""
    risk: str = "unknown"
    success_criteria: tuple[str, ...] = ()
    evidence_contract: Mapping[str, Any] = field(default_factory=dict)
    rollback: str = ""
    proposed_by: str = "domain_llm"

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.event_type:
            raise ValueError("candidate_id and event_type are required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Forecast:
    candidate_id: str
    status: str
    expected_gain: float | None = None
    success_probability: float | None = None
    uncertainty: float = 1.0
    ood_distance: float | None = None
    predicted_next_features: Mapping[str, float] = field(default_factory=dict)
    model_version: str = ""
    reason: str = ""
    authoritative: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TypedJudgment:
    status: str
    model: str
    answers: Mapping[str, Any]
    confidence: float = 0.0
    usage: Mapping[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    reason: str = ""
    authoritative: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoreDecisionRecord:
    decision_id: str
    state_id: str
    flow_id: str
    domain: str
    selected: CandidateAction
    rejected: tuple[Mapping[str, str], ...]
    expected_effect: Mapping[str, Any]
    evaluator: Mapping[str, Any]
    failure_conditions: tuple[str, ...]
    budget: Mapping[str, Any]
    forecasts: tuple[Forecast, ...]
    judgment: TypedJudgment
    selection_rule: str
    created_at: str
    content_hash: str
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def freeze(cls, *, state: DecisionState, selected: CandidateAction,
               candidates: tuple[CandidateAction, ...], forecasts: tuple[Forecast, ...],
               judgment: TypedJudgment, expected_effect: Mapping[str, Any],
               evaluator: Mapping[str, Any], failure_conditions: tuple[str, ...],
               budget: Mapping[str, Any], selection_rule: str, created_at: str) -> "CoreDecisionRecord":
        rejected = tuple({"candidate_id": c.candidate_id,
                          "reason": "not selected by frozen selection rule"}
                         for c in candidates if c.candidate_id != selected.candidate_id)
        body = {
            "state_id": state.state_id, "flow_id": state.flow_id, "domain": state.domain,
            "selected": selected.to_dict(), "rejected": rejected,
            "expected_effect": dict(expected_effect), "evaluator": dict(evaluator),
            "failure_conditions": tuple(failure_conditions), "budget": dict(budget),
            "forecasts": tuple(f.to_dict() for f in forecasts),
            "judgment": judgment.to_dict(), "selection_rule": selection_rule,
            "created_at": created_at, "schema_version": SCHEMA_VERSION,
        }
        content_hash = digest(body)
        return cls(decision_id=f"decision_{content_hash[:16]}", content_hash=content_hash,
                   state_id=state.state_id, flow_id=state.flow_id, domain=state.domain,
                   selected=selected, rejected=rejected,
                   expected_effect=dict(expected_effect), evaluator=dict(evaluator),
                   failure_conditions=tuple(failure_conditions), budget=dict(budget),
                   forecasts=tuple(forecasts), judgment=judgment,
                   selection_rule=selection_rule, created_at=created_at)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoreSettlement:
    settlement_id: str
    decision_id: str
    outcome: str
    reason: str
    observed: Mapping[str, Any]
    evidence_refs: tuple[str, ...]
    trigger_route: str
    settled_at: str
    content_hash: str
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def create(cls, *, decision_id: str, outcome: str, reason: str,
               observed: Mapping[str, Any], evidence_refs, trigger_route: str,
               settled_at: str) -> "CoreSettlement":
        if outcome not in {"supported", "falsified", "inconclusive", "invalid", "blocked"}:
            raise ValueError(f"invalid settlement outcome: {outcome}")
        body = {"decision_id": decision_id, "outcome": outcome, "reason": reason,
                "observed": dict(observed),
                "evidence_refs": sorted({str(v) for v in evidence_refs if v}),
                "trigger_route": trigger_route, "settled_at": settled_at,
                "schema_version": SCHEMA_VERSION}
        content_hash = digest(body)
        return cls(settlement_id=f"settlement_{content_hash[:16]}", content_hash=content_hash,
                   decision_id=decision_id, outcome=outcome, reason=reason,
                   observed=dict(observed), evidence_refs=tuple(body["evidence_refs"]),
                   trigger_route=trigger_route, settled_at=settled_at)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
