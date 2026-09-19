"""Stable data contracts for the commitment kernel.

This module holds *only* data contracts and their serialisation validation.
No state-transition truth lives here (see ``state_machine.py``), and no policy
decision either (see ``policy.py``).

Design rules
------------
* Every contract rejects unknown fields and missing required semantics on load.
  A silently-defaulted expectation is worse than a loud failure: an unfrozen
  expectation cannot be falsified, so the whole bet would be meaningless.
* Semantic distinctions that the kernel must never blur are separate fields, not
  separate prose:

  - ``proposed_by`` records who *judged* (LLM / policy / human).
  - ``measured_by`` records who *measured* (a machine evaluator, never an LLM
    self-score).
  - ``improvement_observed`` and ``publish_eligible`` are separate fields.
  - ``new_regressions`` and ``pre_existing_failures`` are separate lists.
  - ``project_result`` and ``partner_evolution`` never merge into one status.

* All contracts are JSON-serialisable through ``to_dict`` and re-validated on
  ``from_dict`` so a stored record cannot drift from the contract.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: Current schema.  Bumped from ``commitment/1`` by the semantic corrections of
#: 2026-09-19: execution environment, expectation kinds, baseline evidence and
#: control/treatment comparison.  A v1 payload is still *readable* -- see
#: ``LEGACY_SCHEMA_VERSIONS`` -- but it is never rewritten as v1 and the new code
#: never invents the v2 evidence fields a v1 artifact never carried.
SCHEMA_VERSION = "commitment/2"
LEGACY_SCHEMA_VERSIONS = ("commitment/1",)
READABLE_SCHEMA_VERSIONS = (SCHEMA_VERSION, *LEGACY_SCHEMA_VERSIONS)

DIRECTIONS = ("increase", "decrease", "equal")
CANDIDATE_SOURCES = ("llm", "policy", "human")
#: The terminal verdicts a settlement can carry.  ``abstained`` is a first-class
#: terminal state, not a flavour of ``blocked`` or ``inconclusive``: the bet was
#: deliberately not run, so there is nothing measured and nothing to explain away.
SETTLEMENT_CLASSES = ("supported", "falsified", "inconclusive", "invalid", "blocked", "abstained")
EXECUTION_STATUSES = ("completed", "failed", "blocked", "cancelled", "timeout")

#: Where the evidence was produced.  Publication eligibility is a function of
#: this field, and the field is frozen into the bet, so neither an LLM nor a
#: candidate can upgrade it after seeing the result.
EXECUTION_ENVIRONMENTS = ("synthetic_fixture", "isolated_sample", "shadow",
                          "production_canary", "production", "legacy_unknown")
#: ``legacy_unknown`` exists because a pre-correction record carries no environment
#: field at all.  Absence of proof is not proof of production, so a legacy record
#: can never be treated as publishable -- and must never be *inferred* into
#: ``production`` from its own (untrustworthy) publish claim.
NON_PUBLISHABLE_ENVIRONMENTS = ("synthetic_fixture", "isolated_sample", "shadow",
                                "legacy_unknown")

#: What meeting an expectation would actually mean.
EXPECTATION_KINDS = ("absolute_threshold", "delta_over_baseline", "non_inferiority", "guardrail")
CLAIM_BY_KIND = {
    "absolute_threshold": "absolute_attainment",
    "delta_over_baseline": "improvement_over_baseline",
    "non_inferiority": "non_inferiority",
    "guardrail": "guardrail_held",
}
CLAIMS = ("absolute_attainment", "improvement_over_baseline", "non_inferiority",
          "guardrail_held", "none")

#: How a baseline came to exist.
BASELINE_PROVENANCE = ("fresh_execution", "reused_frozen_evidence")

#: Stable publish-blocker codes.  Callers may match on these.
BLOCKER_NOT_PUBLISHABLE_ENVIRONMENT = "environment_not_publishable"
BLOCKER_ISOLATED_SAMPLE = "isolated_sample_only"
BLOCKER_SYNTHETIC = "synthetic_fixture_only"
BLOCKER_SHADOW = "shadow_only"
BLOCKER_SINGLE_EPISODE = "single_episode_only"
#: Levels an experience may carry.  Both are single episodes; neither is a habit.
LEVEL_ABSTENTION = "abstention"
EXPERIENCE_LEVELS = ("experience", LEVEL_ABSTENTION)

#: An abstention is unpublishable by construction: no action ran, so no evidence exists.
BLOCKER_ABSTAINED = "abstained"
BLOCKER_BASELINE_INCOMPATIBLE = "baseline_evidence_incompatible"
BLOCKER_TREATMENT_UNDECLARED = "treatment_contract_missing"
#: A pre-correction (commitment/1) record has no trusted environment, no baseline
#: evidence and no control/treatment proof.  It stays readable for audit and is
#: permanently ineligible for publication or promotion.
BLOCKER_LEGACY_SCHEMA_UNTRUSTED = "legacy_schema_untrusted"


class ContractError(ValueError):
    """A record violates its data contract."""


# ---------------------------------------------------------------------------
# serialisation helpers
# ---------------------------------------------------------------------------

def _require(payload: Mapping[str, Any], key: str, kind: str) -> Any:
    if key not in payload:
        raise ContractError(f"{kind}: missing required field {key!r}")
    return payload[key]


def _reject_unknown(payload: Mapping[str, Any], allowed: Iterable[str], kind: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ContractError(f"{kind}: unknown field(s) {unknown}")


def _check_choice(value: Any, allowed: Sequence[str], kind: str, name: str) -> str:
    if value not in allowed:
        raise ContractError(f"{kind}: {name}={value!r} not in {tuple(allowed)}")
    return str(value)


def _check_non_empty(value: Any, kind: str, name: str) -> str:
    text = str(value or "")
    if not text.strip():
        raise ContractError(f"{kind}: {name} must be non-empty")
    return text


def _check_number(value: Any, kind: str, name: str, *, minimum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{kind}: {name} must be a number, got {value!r}") from exc
    if minimum is not None and number < minimum:
        raise ContractError(f"{kind}: {name}={number} below minimum {minimum}")
    return number


def schema_version_of(payload: Mapping[str, Any]) -> str:
    version = payload.get("schema_version")
    if version is None:
        raise ContractError("payload is missing schema_version")
    return str(version)


def is_legacy_payload(payload: Mapping[str, Any]) -> bool:
    """True for artifacts written before the v2 semantic corrections."""
    return schema_version_of(payload) in LEGACY_SCHEMA_VERSIONS


def _check_schema(payload: Mapping[str, Any], kind: str) -> None:
    """Accept the current schema plus any strictly read-only legacy schema.

    Legacy payloads must stay readable so that history is never rewritten; every
    record this code writes carries ``SCHEMA_VERSION``.  Missing v2 fields are
    filled with safe defaults, never with invented evidence.
    """
    version = payload.get("schema_version")
    if version not in READABLE_SCHEMA_VERSIONS:
        raise ContractError(
            f"{kind}: schema_version={version!r} not in {READABLE_SCHEMA_VERSIONS}")


def _payload(obj: Any) -> dict[str, Any]:
    out = dataclasses.asdict(obj)
    out["schema_version"] = SCHEMA_VERSION
    return out


def canonical_json(payload: Any) -> str:
    """Stable serialisation used for hashing.  Order and separators are fixed."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_of(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# budget / policy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Budget:
    """Absolute budget for one bet.

    ``deadline_epoch`` is an absolute wall-clock deadline, not a per-phase
    timeout: phase transitions must never reset the budget (invariant 9).
    """

    wall_clock_seconds: int
    model_calls: int
    actions: int
    rounds: int
    deadline_epoch: float
    started_epoch: float

    def __post_init__(self) -> None:
        if int(self.wall_clock_seconds) <= 0:
            raise ContractError("Budget: wall_clock_seconds must be > 0")
        if int(self.model_calls) < 0 or int(self.actions) <= 0 or int(self.rounds) <= 0:
            raise ContractError("Budget: model_calls>=0, actions>0, rounds>0 required")
        if float(self.deadline_epoch) <= float(self.started_epoch):
            raise ContractError("Budget: deadline_epoch must be after started_epoch")

    @classmethod
    def create(cls, *, wall_clock_seconds: int, model_calls: int, actions: int,
               rounds: int, started_epoch: float) -> "Budget":
        return cls(wall_clock_seconds=int(wall_clock_seconds), model_calls=int(model_calls),
                   actions=int(actions), rounds=int(rounds),
                   deadline_epoch=float(started_epoch) + float(wall_clock_seconds),
                   started_epoch=float(started_epoch))

    def to_dict(self) -> dict[str, Any]:
        return _payload(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Budget":
        _check_schema(payload, "Budget")
        allowed = {f.name for f in dataclasses.fields(cls)} | {"schema_version"}
        _reject_unknown(payload, allowed, "Budget")
        return cls(wall_clock_seconds=int(_require(payload, "wall_clock_seconds", "Budget")),
                   model_calls=int(_require(payload, "model_calls", "Budget")),
                   actions=int(_require(payload, "actions", "Budget")),
                   rounds=int(_require(payload, "rounds", "Budget")),
                   deadline_epoch=_check_number(_require(payload, "deadline_epoch", "Budget"),
                                                "Budget", "deadline_epoch"),
                   started_epoch=_check_number(_require(payload, "started_epoch", "Budget"),
                                               "Budget", "started_epoch"))


@dataclass
class BudgetUsage:
    """Cumulative consumption.  Mutable counters, persisted with the state."""

    model_calls: int = 0
    actions: int = 0
    rounds: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"model_calls": int(self.model_calls), "actions": int(self.actions),
                "rounds": int(self.rounds)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "BudgetUsage":
        payload = dict(payload or {})
        _reject_unknown(payload, ("model_calls", "actions", "rounds"), "BudgetUsage")
        return cls(model_calls=int(payload.get("model_calls") or 0),
                   actions=int(payload.get("actions") or 0),
                   rounds=int(payload.get("rounds") or 0))


@dataclass(frozen=True)
class CommitmentPolicy:
    """When a change of direction is allowed, and when to stop early."""

    earliest_turn_round: int
    early_stop_conditions: tuple[str, ...] = ()
    require_new_evidence_to_turn: bool = True
    max_turns: int = 1
    note: str = ""

    def __post_init__(self) -> None:
        if int(self.earliest_turn_round) < 1:
            raise ContractError("CommitmentPolicy: earliest_turn_round must be >= 1")
        if int(self.max_turns) < 1:
            raise ContractError("CommitmentPolicy: max_turns must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["early_stop_conditions"] = list(self.early_stop_conditions)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CommitmentPolicy":
        _check_schema(payload, "CommitmentPolicy")
        allowed = {f.name for f in dataclasses.fields(cls)} | {"schema_version"}
        _reject_unknown(payload, allowed, "CommitmentPolicy")
        return cls(earliest_turn_round=int(_require(payload, "earliest_turn_round", "CommitmentPolicy")),
                   early_stop_conditions=tuple(_require(payload, "early_stop_conditions", "CommitmentPolicy") or ()),
                   require_new_evidence_to_turn=bool(payload.get("require_new_evidence_to_turn", True)),
                   max_turns=int(payload.get("max_turns") or 1),
                   note=str(payload.get("note") or ""))


# ---------------------------------------------------------------------------
# the bet
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpectedEffect:
    """A frozen, machine-checkable expectation, typed by what it claims.

    ``kind`` exists because "reached an absolute threshold", "beat the baseline"
    and "did not get worse" are different statements, and a single boolean cannot
    carry all three:

    ``absolute_threshold``   reached ``threshold`` in ``direction``
    ``delta_over_baseline``  reached ``threshold`` **and** beat the baseline by at
                             least ``min_delta`` in ``direction``
    ``non_inferiority``      stayed within ``tolerance`` of the baseline
                             (a guard, not a win)
    ``guardrail``            stayed inside ``threshold`` (a declared ceiling/floor)

    ``met_by`` answers the candidate-side question only; ``improvement_over``
    answers the baseline-relative question.  Settlement computes both by kind and
    never by field name.
    """

    metric: str
    direction: str
    threshold: float
    unit: str = ""
    baseline_value: float | None = None
    kind: str = "absolute_threshold"
    min_delta: float | None = None
    tolerance: float | None = None

    def __post_init__(self) -> None:
        _check_non_empty(self.metric, "ExpectedEffect", "metric")
        _check_choice(self.direction, DIRECTIONS, "ExpectedEffect", "direction")
        _check_number(self.threshold, "ExpectedEffect", "threshold")
        _check_choice(self.kind, EXPECTATION_KINDS, "ExpectedEffect", "kind")
        if self.kind == "delta_over_baseline":
            if self.min_delta is None:
                raise ContractError(
                    "ExpectedEffect: kind=delta_over_baseline requires min_delta so that "
                    "'beat the baseline' is a machine rule, not an adjective")
            _check_number(self.min_delta, "ExpectedEffect", "min_delta", minimum=0.0)
        if self.kind == "non_inferiority" and self.tolerance is None:
            raise ContractError(
                "ExpectedEffect: kind=non_inferiority requires tolerance (the allowed shortfall)")

    @property
    def claim(self) -> str:
        """What meeting this expectation would actually support."""
        return CLAIM_BY_KIND[self.kind]

    def met_by(self, value: float) -> bool:
        """Machine rule on the candidate value.  Never delegated to an LLM."""
        got = float(value)
        if self.direction == "increase":
            return got >= self.threshold
        if self.direction == "decrease":
            return got <= self.threshold
        return abs(got - self.threshold) <= 1e-9

    def improvement_over(self, baseline: float, candidate: float) -> bool | None:
        """Machine rule for the baseline-relative question.

        Returns ``None`` when the expectation kind carries no relative claim, so
        an absolute-threshold expectation can never be reported as an improvement.
        """
        if self.kind not in ("delta_over_baseline", "non_inferiority"):
            return None
        delta = float(candidate) - float(baseline)
        signed = -delta if self.direction == "decrease" else delta
        if self.kind == "delta_over_baseline":
            required = float(self.min_delta or 0.0)
            # a zero delta is *not* an improvement, whatever the tolerance says
            return signed > 1e-12 and signed >= required
        return signed >= -float(self.tolerance or 0.0)

    def to_dict(self) -> dict[str, Any]:
        return _payload(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExpectedEffect":
        _check_schema(payload, "ExpectedEffect")
        allowed = {f.name for f in dataclasses.fields(cls)} | {"schema_version"}
        _reject_unknown(payload, allowed, "ExpectedEffect")
        baseline = payload.get("baseline_value")
        min_delta = payload.get("min_delta")
        tolerance = payload.get("tolerance")
        kind = str(payload.get("kind") or "absolute_threshold")
        if kind == "delta_over_baseline" and min_delta is None and baseline is None:
            # a v1 payload cannot carry the relative contract; keep it readable by
            # treating the frozen threshold as the acceptance rule and recording no
            # min_delta, rather than inventing one.
            kind = "absolute_threshold"
        return cls(metric=_check_non_empty(_require(payload, "metric", "ExpectedEffect"), "ExpectedEffect", "metric"),
                   direction=_check_choice(_require(payload, "direction", "ExpectedEffect"),
                                           DIRECTIONS, "ExpectedEffect", "direction"),
                   threshold=_check_number(_require(payload, "threshold", "ExpectedEffect"),
                                           "ExpectedEffect", "threshold"),
                   unit=str(payload.get("unit") or ""),
                   baseline_value=None if baseline is None else float(baseline),
                   kind=kind,
                   min_delta=None if min_delta is None else float(min_delta),
                   tolerance=None if tolerance is None else float(tolerance))


@dataclass(frozen=True)
class FalsificationCondition:
    """A named, machine-checkable condition that would falsify the bet."""

    code: str
    description: str
    kind: str  # "metric_violation" | "guardrail_violation" | "missing_evidence"
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_non_empty(self.code, "FalsificationCondition", "code")
        _check_non_empty(self.description, "FalsificationCondition", "description")
        if not dict(self.params):
            raise ContractError("FalsificationCondition: params must not be empty")

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["params"] = dict(self.params)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FalsificationCondition":
        _check_schema(payload, "FalsificationCondition")
        allowed = {f.name for f in dataclasses.fields(cls)} | {"schema_version"}
        _reject_unknown(payload, allowed, "FalsificationCondition")
        return cls(code=_check_non_empty(_require(payload, "code", "FalsificationCondition"),
                                         "FalsificationCondition", "code"),
                   description=_check_non_empty(_require(payload, "description", "FalsificationCondition"),
                                                "FalsificationCondition", "description"),
                   kind=str(payload.get("kind") or "metric_violation"),
                   params=dict(_require(payload, "params", "FalsificationCondition")))


@dataclass(frozen=True)
class EvaluationProtocol:
    """How the outcome is measured, and by whom.

    ``evaluator_id`` must name a machine evaluator.  ``agent_output_visible``
    exists so a protocol that would let the evaluator read agent verdicts can be
    rejected at freeze time instead of quietly contaminating the measurement.
    """

    evaluator_id: str
    evaluator_version: str
    metric_specs: tuple[Mapping[str, Any], ...]
    independence: str = "artifact_only"
    agent_output_visible: bool = False
    tolerance: float = 0.0
    #: How many independent episodes the evidence covers.  Publication requires
    #: replication, so ``replicates < 2`` is itself a publish blocker.
    replicates: int = 1

    def __post_init__(self) -> None:
        _check_non_empty(self.evaluator_id, "EvaluationProtocol", "evaluator_id")
        _check_non_empty(self.evaluator_version, "EvaluationProtocol", "evaluator_version")
        if not tuple(self.metric_specs):
            raise ContractError("EvaluationProtocol: metric_specs must not be empty")
        if self.agent_output_visible:
            raise ContractError(
                "EvaluationProtocol: agent_output_visible must be False; an evaluator that can "
                "read agent verdicts cannot produce independent evidence")
        if self.independence != "artifact_only":
            raise ContractError("EvaluationProtocol: independence must be 'artifact_only'")
        if int(self.replicates) < 1:
            raise ContractError("EvaluationProtocol: replicates must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["metric_specs"] = [dict(s) for s in self.metric_specs]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvaluationProtocol":
        _check_schema(payload, "EvaluationProtocol")
        allowed = {f.name for f in dataclasses.fields(cls)} | {"schema_version"}
        _reject_unknown(payload, allowed, "EvaluationProtocol")
        return cls(evaluator_id=_check_non_empty(_require(payload, "evaluator_id", "EvaluationProtocol"),
                                                 "EvaluationProtocol", "evaluator_id"),
                   evaluator_version=_check_non_empty(_require(payload, "evaluator_version", "EvaluationProtocol"),
                                                      "EvaluationProtocol", "evaluator_version"),
                   metric_specs=tuple(dict(s) for s in _require(payload, "metric_specs", "EvaluationProtocol")),
                   independence=str(payload.get("independence") or "artifact_only"),
                   agent_output_visible=bool(payload.get("agent_output_visible", False)),
                   tolerance=float(payload.get("tolerance") or 0.0),
                   replicates=int(payload.get("replicates") or 1))


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    description: str
    params: Mapping[str, Any]
    rationale: str = ""
    proposed_by: str = "policy"
    llm_trace_ref: str = ""

    def __post_init__(self) -> None:
        _check_non_empty(self.candidate_id, "Candidate", "candidate_id")
        _check_non_empty(self.description, "Candidate", "description")
        _check_choice(self.proposed_by, CANDIDATE_SOURCES, "Candidate", "proposed_by")

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["params"] = dict(self.params)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Candidate":
        _check_schema(payload, "Candidate")
        allowed = {f.name for f in dataclasses.fields(cls)} | {"schema_version"}
        _reject_unknown(payload, allowed, "Candidate")
        return cls(candidate_id=_check_non_empty(_require(payload, "candidate_id", "Candidate"),
                                                 "Candidate", "candidate_id"),
                   description=_check_non_empty(_require(payload, "description", "Candidate"),
                                                "Candidate", "description"),
                   params=dict(_require(payload, "params", "Candidate")),
                   rationale=str(payload.get("rationale") or ""),
                   proposed_by=_check_choice(payload.get("proposed_by") or "policy",
                                             CANDIDATE_SOURCES, "Candidate", "proposed_by"),
                   llm_trace_ref=str(payload.get("llm_trace_ref") or ""))


@dataclass(frozen=True)
class RejectedAlternative:
    candidate_id: str
    reason: str

    def __post_init__(self) -> None:
        _check_non_empty(self.candidate_id, "RejectedAlternative", "candidate_id")
        _check_non_empty(self.reason, "RejectedAlternative", "reason")

    def to_dict(self) -> dict[str, Any]:
        return _payload(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RejectedAlternative":
        _check_schema(payload, "RejectedAlternative")
        _reject_unknown(payload, {f.name for f in dataclasses.fields(cls)} | {"schema_version"},
                        "RejectedAlternative")
        return cls(candidate_id=_check_non_empty(_require(payload, "candidate_id", "RejectedAlternative"),
                                                 "RejectedAlternative", "candidate_id"),
                   reason=_check_non_empty(_require(payload, "reason", "RejectedAlternative"),
                                           "RejectedAlternative", "reason"))


# ---------------------------------------------------------------------------
# the bet record
# ---------------------------------------------------------------------------

#: Fields that carry the *meaning* of the bet.  Once the bet reaches COMMITTED
#: these may never be edited in place: the store rejects any write whose semantic
#: hash differs from the committed revision, and the only legal evolution is a
#: new revision that references its parent (invariant 2).
SEMANTIC_FIELDS = (
    "question",
    "context_snapshot_ref",
    "context_snapshot_hash",
    "candidates",
    "selected_action",
    "rejected_alternatives",
    "selection_reason",
    "expected_effects",
    "falsification_conditions",
    "evaluation_protocol",
    "baseline_ref",
    "budget",
    "commitment_policy",
    "environment",
    "treatment",
    "code_version",
    "data_version",
    "model_config_ref",
)


@dataclass(frozen=True)
class BetRecord:
    """One bounded wager: a single falsifiable question with frozen expectations."""

    bet_id: str
    partner_id: str
    project_id: str
    run_id: str
    question: str
    context_snapshot_ref: str
    context_snapshot_hash: str
    candidates: tuple[Candidate, ...]
    selected_action: str
    rejected_alternatives: tuple[RejectedAlternative, ...]
    selection_reason: str
    expected_effects: tuple[ExpectedEffect, ...]
    falsification_conditions: tuple[FalsificationCondition, ...]
    evaluation_protocol: EvaluationProtocol
    baseline_ref: str
    budget: Budget
    commitment_policy: CommitmentPolicy
    code_version: str
    data_version: str
    model_config_ref: str
    #: Where the evidence will be produced.  Frozen into the semantic hash, so
    #: neither an LLM nor a candidate can promote an isolated run after the fact.
    #: The default is the *least* publishable environment on purpose.
    environment: str = "synthetic_fixture"
    #: The single variable the arms are allowed to differ in.  ``None`` means the
    #: bet never declared one, and settlement then refuses to call it matched.
    treatment: TreatmentContract | None = None
    status: str = "DRAFT"
    revision: int = 1
    parent_revision: int = 0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        for name in ("bet_id", "partner_id", "project_id", "run_id", "question",
                     "context_snapshot_ref", "context_snapshot_hash",
                     "selected_action", "selection_reason", "baseline_ref",
                     "code_version", "data_version", "model_config_ref"):
            _check_non_empty(getattr(self, name), "BetRecord", name)
        if not tuple(self.candidates):
            raise ContractError("BetRecord: candidates must not be empty")
        ids = [c.candidate_id for c in self.candidates]
        if len(set(ids)) != len(ids):
            raise ContractError("BetRecord: candidate_id values must be unique")
        if self.selected_action not in ids:
            raise ContractError(
                f"BetRecord: selected_action {self.selected_action!r} is not one of the candidates")
        rejected = {r.candidate_id for r in self.rejected_alternatives}
        expected_rejected = set(ids) - {self.selected_action}
        if rejected != expected_rejected:
            raise ContractError(
                "BetRecord: rejected_alternatives must name exactly the non-selected candidates; "
                f"expected {sorted(expected_rejected)}, got {sorted(rejected)}")
        if not tuple(self.expected_effects):
            raise ContractError("BetRecord: expected_effects must not be empty")
        if not tuple(self.falsification_conditions):
            raise ContractError("BetRecord: falsification_conditions must not be empty")
        _check_choice(self.environment, EXECUTION_ENVIRONMENTS, "BetRecord", "environment")
        if self.treatment is not None and not isinstance(self.treatment, TreatmentContract):
            raise ContractError("BetRecord: treatment must be a TreatmentContract or None")
        if int(self.revision) < 1:
            raise ContractError("BetRecord: revision must be >= 1")
        if int(self.revision) > 1 and int(self.parent_revision) < 1:
            raise ContractError("BetRecord: a revision > 1 must reference parent_revision")

    # -- freeze identity -----------------------------------------------------

    def semantic_payload(self) -> dict[str, Any]:
        """The semantic fields only, built directly.

        Deliberately does not go through ``to_dict``: that would include
        ``freeze_hash``, which is derived from this payload, and the cycle would
        recurse forever.
        """
        payload: dict[str, Any] = {
            "question": self.question,
            "context_snapshot_ref": self.context_snapshot_ref,
            "context_snapshot_hash": self.context_snapshot_hash,
            "candidates": [c.to_dict() for c in self.candidates],
            "selected_action": self.selected_action,
            "rejected_alternatives": [r.to_dict() for r in self.rejected_alternatives],
            "selection_reason": self.selection_reason,
            "expected_effects": [e.to_dict() for e in self.expected_effects],
            "falsification_conditions": [f.to_dict() for f in self.falsification_conditions],
            "evaluation_protocol": self.evaluation_protocol.to_dict(),
            "baseline_ref": self.baseline_ref,
            "budget": self.budget.to_dict(),
            "commitment_policy": self.commitment_policy.to_dict(),
            "environment": self.environment,
            "treatment": None if self.treatment is None else self.treatment.to_dict(),
            "code_version": self.code_version,
            "data_version": self.data_version,
            "model_config_ref": self.model_config_ref,
        }
        return {key: payload[key] for key in SEMANTIC_FIELDS}

    def freeze_hash(self) -> str:
        return sha256_of({"schema_version": SCHEMA_VERSION,
                          "semantic": self.semantic_payload()})

    def tamper_evidence(self, other: "BetRecord") -> list[str]:
        """Names of semantic fields that differ.  Empty means identical meaning."""
        mine, theirs = self.semantic_payload(), other.semantic_payload()
        return sorted(k for k in SEMANTIC_FIELDS if mine.get(k) != theirs.get(k))

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "bet_id": self.bet_id, "schema_version": SCHEMA_VERSION,
            "partner_id": self.partner_id, "project_id": self.project_id,
            "run_id": self.run_id, "question": self.question,
            "context_snapshot_ref": self.context_snapshot_ref,
            "context_snapshot_hash": self.context_snapshot_hash,
            "candidates": [c.to_dict() for c in self.candidates],
            "selected_action": self.selected_action,
            "rejected_alternatives": [r.to_dict() for r in self.rejected_alternatives],
            "selection_reason": self.selection_reason,
            "expected_effects": [e.to_dict() for e in self.expected_effects],
            "falsification_conditions": [f.to_dict() for f in self.falsification_conditions],
            "evaluation_protocol": self.evaluation_protocol.to_dict(),
            "baseline_ref": self.baseline_ref, "budget": self.budget.to_dict(),
            "commitment_policy": self.commitment_policy.to_dict(),
            "environment": self.environment,
            "treatment": None if self.treatment is None else self.treatment.to_dict(),
            "code_version": self.code_version, "data_version": self.data_version,
            "model_config_ref": self.model_config_ref,
            "status": self.status, "revision": int(self.revision),
            "parent_revision": int(self.parent_revision),
            "created_at": self.created_at, "updated_at": self.updated_at,
            "freeze_hash": self.freeze_hash(),
        }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BetRecord":
        _check_schema(payload, "BetRecord")
        allowed = {f.name for f in dataclasses.fields(cls)} | {"schema_version", "freeze_hash"}
        _reject_unknown(payload, allowed, "BetRecord")
        record = cls(
            bet_id=_check_non_empty(_require(payload, "bet_id", "BetRecord"), "BetRecord", "bet_id"),
            partner_id=str(_require(payload, "partner_id", "BetRecord")),
            project_id=str(_require(payload, "project_id", "BetRecord")),
            run_id=str(_require(payload, "run_id", "BetRecord")),
            question=_check_non_empty(_require(payload, "question", "BetRecord"), "BetRecord", "question"),
            context_snapshot_ref=str(_require(payload, "context_snapshot_ref", "BetRecord")),
            context_snapshot_hash=str(_require(payload, "context_snapshot_hash", "BetRecord")),
            candidates=tuple(Candidate.from_dict(c) for c in _require(payload, "candidates", "BetRecord")),
            selected_action=str(_require(payload, "selected_action", "BetRecord")),
            rejected_alternatives=tuple(RejectedAlternative.from_dict(r)
                                        for r in _require(payload, "rejected_alternatives", "BetRecord")),
            selection_reason=str(_require(payload, "selection_reason", "BetRecord")),
            expected_effects=tuple(ExpectedEffect.from_dict(e)
                                   for e in _require(payload, "expected_effects", "BetRecord")),
            falsification_conditions=tuple(FalsificationCondition.from_dict(f)
                                           for f in _require(payload, "falsification_conditions", "BetRecord")),
            evaluation_protocol=EvaluationProtocol.from_dict(_require(payload, "evaluation_protocol", "BetRecord")),
            baseline_ref=str(_require(payload, "baseline_ref", "BetRecord")),
            budget=Budget.from_dict(_require(payload, "budget", "BetRecord")),
            commitment_policy=CommitmentPolicy.from_dict(_require(payload, "commitment_policy", "BetRecord")),
            code_version=str(_require(payload, "code_version", "BetRecord")),
            data_version=str(_require(payload, "data_version", "BetRecord")),
            model_config_ref=str(_require(payload, "model_config_ref", "BetRecord")),
            # A v1 payload has no trusted environment.  It is never inferred from
            # context or from a publish claim; it becomes ``legacy_unknown``.
            environment=str(payload.get("environment") or (
                "legacy_unknown" if is_legacy_payload(payload) else "synthetic_fixture")),
            treatment=(None if payload.get("treatment") is None
                       else TreatmentContract.from_dict(dict(payload["treatment"]))),
            status=str(payload.get("status") or "DRAFT"),
            revision=int(payload.get("revision") or 1),
            parent_revision=int(payload.get("parent_revision") or 0),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )
        declared = payload.get("freeze_hash")
        if declared is not None and declared != record.freeze_hash():
            raise ContractError(
                "BetRecord: freeze_hash does not match the semantic content; the record was "
                "edited after it was frozen")
        return record


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionReceipt:
    """Proof that a bounded action really ran.  No receipt, no MEASURED."""

    receipt_id: str
    bet_id: str
    requested_action: str
    executed_action: str
    executor_id: str
    executor_version: str
    started_epoch: float
    finished_epoch: float
    status: str
    exit_code: int
    artifacts: tuple[str, ...]
    artifact_hashes: Mapping[str, str]
    log_ref: str
    data_hash: str
    budget_consumed: Mapping[str, Any]
    human_intervention: bool
    idempotency_key: str
    failure_reason: str = ""

    def __post_init__(self) -> None:
        for name in ("receipt_id", "bet_id", "requested_action", "executed_action",
                     "executor_id", "executor_version", "idempotency_key"):
            _check_non_empty(getattr(self, name), "ExecutionReceipt", name)
        _check_choice(self.status, EXECUTION_STATUSES, "ExecutionReceipt", "status")
        if float(self.finished_epoch) < float(self.started_epoch):
            raise ContractError("ExecutionReceipt: finished_epoch before started_epoch")
        if self.status == "completed" and not tuple(self.artifacts):
            raise ContractError(
                "ExecutionReceipt: a completed execution must name at least one artifact; "
                "file existence is not evidence, but an execution with no output is not progress")
        if self.status == "completed" and not self.artifact_hashes:
            raise ContractError("ExecutionReceipt: completed execution requires artifact hashes")
        if self.status != "completed" and not str(self.failure_reason).strip():
            raise ContractError("ExecutionReceipt: a non-completed execution must state failure_reason")
        if self.executed_action != self.requested_action:
            raise ContractError(
                "ExecutionReceipt: executed_action differs from requested_action; the executor may "
                "not silently substitute a different action")

    @property
    def is_valid(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["artifacts"] = list(self.artifacts)
        payload["artifact_hashes"] = dict(self.artifact_hashes)
        payload["budget_consumed"] = dict(self.budget_consumed)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecutionReceipt":
        _check_schema(payload, "ExecutionReceipt")
        allowed = {f.name for f in dataclasses.fields(cls)} | {"schema_version"}
        _reject_unknown(payload, allowed, "ExecutionReceipt")
        return cls(
            receipt_id=str(_require(payload, "receipt_id", "ExecutionReceipt")),
            bet_id=str(_require(payload, "bet_id", "ExecutionReceipt")),
            requested_action=str(_require(payload, "requested_action", "ExecutionReceipt")),
            executed_action=str(_require(payload, "executed_action", "ExecutionReceipt")),
            executor_id=str(_require(payload, "executor_id", "ExecutionReceipt")),
            executor_version=str(_require(payload, "executor_version", "ExecutionReceipt")),
            started_epoch=_check_number(_require(payload, "started_epoch", "ExecutionReceipt"),
                                        "ExecutionReceipt", "started_epoch"),
            finished_epoch=_check_number(_require(payload, "finished_epoch", "ExecutionReceipt"),
                                         "ExecutionReceipt", "finished_epoch"),
            status=str(_require(payload, "status", "ExecutionReceipt")),
            exit_code=int(payload.get("exit_code") or 0),
            artifacts=tuple(payload.get("artifacts") or ()),
            artifact_hashes=dict(payload.get("artifact_hashes") or {}),
            log_ref=str(payload.get("log_ref") or ""),
            data_hash=str(payload.get("data_hash") or ""),
            budget_consumed=dict(payload.get("budget_consumed") or {}),
            human_intervention=bool(payload.get("human_intervention", False)),
            idempotency_key=str(_require(payload, "idempotency_key", "ExecutionReceipt")),
            failure_reason=str(payload.get("failure_reason") or ""),
        )


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutcomeMeasurement:
    """An independent, machine-produced measurement.

    ``agent_artifacts_unchanged`` must be true: the measurement may not be the
    byproduct of the agent rewriting its own evidence during measurement.
    """

    measurement_id: str
    bet_id: str
    receipt_id: str
    metric: str
    value: float | None
    direction: str
    unit: str
    measured_by: str
    evaluator_version: str
    evidence_refs: tuple[str, ...]
    missing_reason: str
    validity: str  # "valid" | "invalid" | "missing"
    agent_artifacts_unchanged: bool
    artifact_hashes_before: Mapping[str, str]
    artifact_hashes_after: Mapping[str, str]

    def __post_init__(self) -> None:
        for name in ("measurement_id", "bet_id", "receipt_id", "metric", "measured_by",
                     "evaluator_version"):
            _check_non_empty(getattr(self, name), "OutcomeMeasurement", name)
        _check_choice(self.direction, DIRECTIONS, "OutcomeMeasurement", "direction")
        if self.validity not in ("valid", "invalid", "missing"):
            raise ContractError("OutcomeMeasurement: validity must be valid/invalid/missing")
        if self.validity == "valid":
            if self.value is None:
                raise ContractError("OutcomeMeasurement: a valid measurement needs a value")
            if not tuple(self.evidence_refs):
                raise ContractError("OutcomeMeasurement: a valid measurement needs evidence_refs")
            if not self.agent_artifacts_unchanged:
                raise ContractError(
                    "OutcomeMeasurement: agent artifacts changed during measurement; the "
                    "measurement is not independent of the agent")
            if dict(self.artifact_hashes_before) != dict(self.artifact_hashes_after):
                raise ContractError(
                    "OutcomeMeasurement: artifact hashes differ before/after measurement")
        else:
            if not str(self.missing_reason).strip():
                raise ContractError(
                    "OutcomeMeasurement: an invalid/missing measurement must state missing_reason")

    @property
    def is_valid(self) -> bool:
        return self.validity == "valid"

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["evidence_refs"] = list(self.evidence_refs)
        payload["artifact_hashes_before"] = dict(self.artifact_hashes_before)
        payload["artifact_hashes_after"] = dict(self.artifact_hashes_after)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OutcomeMeasurement":
        _check_schema(payload, "OutcomeMeasurement")
        allowed = {f.name for f in dataclasses.fields(cls)} | {"schema_version"}
        _reject_unknown(payload, allowed, "OutcomeMeasurement")
        value = payload.get("value")
        return cls(measurement_id=str(_require(payload, "measurement_id", "OutcomeMeasurement")),
                   bet_id=str(_require(payload, "bet_id", "OutcomeMeasurement")),
                   receipt_id=str(_require(payload, "receipt_id", "OutcomeMeasurement")),
                   metric=str(_require(payload, "metric", "OutcomeMeasurement")),
                   value=None if value is None else float(value),
                   direction=str(_require(payload, "direction", "OutcomeMeasurement")),
                   unit=str(payload.get("unit") or ""),
                   measured_by=str(_require(payload, "measured_by", "OutcomeMeasurement")),
                   evaluator_version=str(_require(payload, "evaluator_version", "OutcomeMeasurement")),
                   evidence_refs=tuple(payload.get("evidence_refs") or ()),
                   missing_reason=str(payload.get("missing_reason") or ""),
                   validity=str(payload.get("validity") or "invalid"),
                   agent_artifacts_unchanged=bool(payload.get("agent_artifacts_unchanged", False)),
                   artifact_hashes_before=dict(payload.get("artifact_hashes_before") or {}),
                   artifact_hashes_after=dict(payload.get("artifact_hashes_after") or {}))


# ---------------------------------------------------------------------------
# settlement
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpectationOutcome:
    """One expectation's machine result.  LLM opinion has no field here."""

    metric: str
    direction: str
    threshold: float
    observed: float | None
    met: bool
    note: str = ""
    kind: str = "absolute_threshold"
    baseline: float | None = None
    improvement_over_baseline: bool | None = None
    baseline_satisfied: bool | None = None

    def __post_init__(self) -> None:
        _check_non_empty(self.metric, "ExpectationOutcome", "metric")
        _check_choice(self.direction, DIRECTIONS, "ExpectationOutcome", "direction")
        _check_number(self.threshold, "ExpectationOutcome", "threshold")
        _check_choice(self.kind, EXPECTATION_KINDS, "ExpectationOutcome", "kind")
        if self.observed is None and self.met:
            raise ContractError("ExpectationOutcome: an unmet observation cannot be met")
        if self.kind not in ("delta_over_baseline", "non_inferiority") \
                and self.improvement_over_baseline is not None:
            raise ContractError(
                f"ExpectationOutcome: kind={self.kind} carries no improvement claim, so "
                "improvement_over_baseline must be None")
        # No cross-check between ``met`` and ``improvement_over_baseline``: a candidate
        # can clear the absolute threshold while still being worse than a baseline that
        # already cleared it, and can improve on the baseline while still missing the
        # threshold.  Conflating the two is exactly the bug this field pair fixes.

    def to_dict(self) -> dict[str, Any]:
        return _payload(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExpectationOutcome":
        _check_schema(payload, "ExpectationOutcome")
        _reject_unknown(payload, {f.name for f in dataclasses.fields(cls)} | {"schema_version"},
                        "ExpectationOutcome")
        observed = payload.get("observed")
        baseline = payload.get("baseline")
        improvement = payload.get("improvement_over_baseline")
        baseline_satisfied = payload.get("baseline_satisfied")
        return cls(metric=str(_require(payload, "metric", "ExpectationOutcome")),
                   direction=str(_require(payload, "direction", "ExpectationOutcome")),
                   threshold=float(_require(payload, "threshold", "ExpectationOutcome")),
                   observed=None if observed is None else float(observed),
                   met=bool(payload.get("met")),
                   note=str(payload.get("note") or ""),
                   kind=str(payload.get("kind") or "absolute_threshold"),
                   baseline=None if baseline is None else float(baseline),
                   improvement_over_baseline=None if improvement is None else bool(improvement),
                   baseline_satisfied=None if baseline_satisfied is None else bool(baseline_satisfied))


@dataclass(frozen=True)
class TreatmentContract:
    """The single variable the two arms are allowed to differ in.

    A matched comparison means: everything that must be held constant *is*
    constant, and the one thing that must differ differs exactly as frozen.
    "The whole code version must be identical" is therefore not a valid gate for
    every experiment -- for a code-evolution experiment the candidate patch *is*
    the treatment, and demanding identical code would make the experiment
    impossible by construction.
    """

    baseline_treatment: str
    candidate_treatment: str
    expected_diff_hash: str
    #: Files the frozen treatment is allowed to touch.  A change outside this set
    #: is contamination, not treatment, and makes the comparison unmatched.
    declared_paths: tuple[str, ...] = ()
    #: Whether the treatment may change the code version at all.  False means the
    #: two arms must run the same harness; True means the frozen patch *is* the
    #: treatment (a code-evolution experiment).
    allows_code_change: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        _check_non_empty(self.baseline_treatment, "TreatmentContract", "baseline_treatment")
        _check_non_empty(self.candidate_treatment, "TreatmentContract", "candidate_treatment")
        _check_non_empty(self.expected_diff_hash, "TreatmentContract", "expected_diff_hash")

    def to_dict(self) -> dict[str, Any]:
        return _payload(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TreatmentContract":
        _check_schema(payload, "TreatmentContract")
        _reject_unknown(payload, {f.name for f in dataclasses.fields(cls)} | {"schema_version"},
                        "TreatmentContract")
        return cls(baseline_treatment=str(_require(payload, "baseline_treatment", "TreatmentContract")),
                   candidate_treatment=str(_require(payload, "candidate_treatment", "TreatmentContract")),
                   expected_diff_hash=str(_require(payload, "expected_diff_hash", "TreatmentContract")),
                   declared_paths=tuple(payload.get("declared_paths") or ()),
                   allows_code_change=bool(payload.get("allows_code_change", False)),
                   note=str(payload.get("note") or ""))


@dataclass(frozen=True)
class TreatmentSpec:
    """What the experiment declares about its treatment *before* selection.

    The concrete candidate is chosen inside the run, so the spec carries the
    control arm, the files the treatment may touch, and whether the code version
    is allowed to move.  The freezer turns this into the full
    :class:`TreatmentContract` once the direction is frozen.
    """

    baseline_treatment: str
    declared_paths: tuple[str, ...] = ()
    allows_code_change: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        _check_non_empty(self.baseline_treatment, "TreatmentSpec", "baseline_treatment")

    def to_dict(self) -> dict[str, Any]:
        return _payload(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TreatmentSpec":
        _check_schema(payload, "TreatmentSpec")
        _reject_unknown(payload, {f.name for f in dataclasses.fields(cls)} | {"schema_version"},
                        "TreatmentSpec")
        return cls(baseline_treatment=str(_require(payload, "baseline_treatment", "TreatmentSpec")),
                   declared_paths=tuple(payload.get("declared_paths") or ()),
                   allows_code_change=bool(payload.get("allows_code_change", False)),
                   note=str(payload.get("note") or ""))


@dataclass(frozen=True)
class BaselineEvidence:
    """A verifiable baseline: executed, measured, hashed, immutable.

    A scalar copied into a snapshot is not evidence.  This record carries the
    provenance needed to decide whether the baseline is *compatible* with the
    candidate: where it was produced, by which executor and evaluator, under
    which protocol, budget, environment and treatment, and proof that its
    artifacts did not change between the two hashing passes.
    """

    baseline_id: str
    metric_values: Mapping[str, float]
    receipt_ref: str
    receipt_hash: str
    measurement_ref: str
    measurement_hash: str
    artifact_refs: tuple[str, ...]
    artifact_hashes: Mapping[str, str]
    artifact_hashes_after: Mapping[str, str]
    input_hash: str
    data_hash: str
    executor_id: str
    executor_version: str
    evaluator_id: str
    evaluator_version: str
    protocol_hash: str
    budget_hash: str
    budget_snapshot: Mapping[str, Any]
    environment: str
    environment_fingerprint: str
    harness_version: str
    treatment: str
    provenance: str = "fresh_execution"
    compatibility: Mapping[str, Any] = field(default_factory=dict)
    compatibility_ok: bool = False
    created_at: str = ""

    def __post_init__(self) -> None:
        for name in ("baseline_id", "receipt_ref", "receipt_hash", "measurement_ref",
                     "measurement_hash", "input_hash", "executor_id", "executor_version",
                     "evaluator_id", "evaluator_version", "protocol_hash", "budget_hash",
                     "environment_fingerprint", "harness_version", "treatment"):
            _check_non_empty(getattr(self, name), "BaselineEvidence", name)
        _check_choice(self.provenance, BASELINE_PROVENANCE, "BaselineEvidence", "provenance")
        _check_choice(self.environment, EXECUTION_ENVIRONMENTS, "BaselineEvidence", "environment")
        if not dict(self.metric_values):
            raise ContractError("BaselineEvidence: metric_values must not be empty")
        if not tuple(self.artifact_refs):
            raise ContractError("BaselineEvidence: at least one artifact must be referenced")
        if self.provenance == "reused_frozen_evidence":
            if not self.compatibility_ok:
                raise ContractError(
                    "BaselineEvidence: a reused baseline requires a passing compatibility proof; "
                    "copying metric values is not reuse")
            if not dict(self.compatibility):
                raise ContractError(
                    "BaselineEvidence: a reused baseline must record which compatibility checks passed")

    @property
    def immutable(self) -> bool:
        """Both hashing passes agree, so the measured artifacts did not move."""
        before, after = dict(self.artifact_hashes), dict(self.artifact_hashes_after)
        return bool(before) and before == after

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["metric_values"] = dict(self.metric_values)
        payload["artifact_refs"] = list(self.artifact_refs)
        payload["artifact_hashes"] = dict(self.artifact_hashes)
        payload["artifact_hashes_after"] = dict(self.artifact_hashes_after)
        payload["budget_snapshot"] = dict(self.budget_snapshot)
        payload["compatibility"] = dict(self.compatibility)
        payload["immutable"] = self.immutable
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BaselineEvidence":
        _check_schema(payload, "BaselineEvidence")
        _reject_unknown(payload, {f.name for f in dataclasses.fields(cls)}
                        | {"schema_version", "immutable", "subject_kind", "subject_id"}, "BaselineEvidence")
        return cls(
            baseline_id=str(_require(payload, "baseline_id", "BaselineEvidence")),
            metric_values={k: float(v) for k, v in dict(payload.get("metric_values") or {}).items()},
            receipt_ref=str(_require(payload, "receipt_ref", "BaselineEvidence")),
            receipt_hash=str(_require(payload, "receipt_hash", "BaselineEvidence")),
            measurement_ref=str(_require(payload, "measurement_ref", "BaselineEvidence")),
            measurement_hash=str(_require(payload, "measurement_hash", "BaselineEvidence")),
            artifact_refs=tuple(payload.get("artifact_refs") or ()),
            artifact_hashes={k: str(v) for k, v in dict(payload.get("artifact_hashes") or {}).items()},
            artifact_hashes_after={k: str(v)
                                   for k, v in dict(payload.get("artifact_hashes_after") or {}).items()},
            input_hash=str(_require(payload, "input_hash", "BaselineEvidence")),
            data_hash=str(payload.get("data_hash") or ""),
            executor_id=str(_require(payload, "executor_id", "BaselineEvidence")),
            executor_version=str(_require(payload, "executor_version", "BaselineEvidence")),
            evaluator_id=str(_require(payload, "evaluator_id", "BaselineEvidence")),
            evaluator_version=str(_require(payload, "evaluator_version", "BaselineEvidence")),
            protocol_hash=str(_require(payload, "protocol_hash", "BaselineEvidence")),
            budget_hash=str(_require(payload, "budget_hash", "BaselineEvidence")),
            budget_snapshot=dict(payload.get("budget_snapshot") or {}),
            environment=str(_require(payload, "environment", "BaselineEvidence")),
            environment_fingerprint=str(_require(payload, "environment_fingerprint", "BaselineEvidence")),
            harness_version=str(_require(payload, "harness_version", "BaselineEvidence")),
            treatment=str(_require(payload, "treatment", "BaselineEvidence")),
            provenance=str(payload.get("provenance") or "fresh_execution"),
            compatibility=dict(payload.get("compatibility") or {}),
            compatibility_ok=bool(payload.get("compatibility_ok")),
            created_at=str(payload.get("created_at") or ""))


@dataclass(frozen=True)
class ComparisonProof:
    """Control variables identical; the treatment variable explicitly different.

    ``matched`` therefore no longer asks "is the whole code version the same?".
    It asks "are the control variables the same, and is the single treatment
    difference exactly the frozen one?" -- which is what makes a code-evolution
    experiment comparable at all.
    """

    inputs_identical: bool
    evaluator_identical: bool
    protocol_identical: bool
    budget_comparable: bool
    environment_identical: bool
    harness_version_identical: bool
    baseline_treatment: str
    candidate_treatment: str
    treatment_diff_hash: str
    expected_treatment_diff_hash: str
    baseline_code_version: str
    candidate_code_version: str
    baseline_value: float | None
    candidate_value: float | None
    delta: float | None
    baseline_evidence_ref: str = ""
    baseline_provenance: str = ""
    detail: str = ""
    #: Set on records read from a pre-correction schema: they carry no baseline
    #: evidence reference and no treatment contract, so they cannot be trusted as
    #: a matched comparison.  Read-only history, never a current proof.
    legacy_untrusted: bool = False

    @property
    def controls_identical(self) -> bool:
        return all((self.inputs_identical, self.evaluator_identical, self.protocol_identical,
                    self.budget_comparable, self.environment_identical,
                    self.harness_version_identical))

    @property
    def treatment_as_frozen(self) -> bool:
        return (bool(self.treatment_diff_hash) and bool(self.expected_treatment_diff_hash)
                and self.treatment_diff_hash == self.expected_treatment_diff_hash)

    @property
    def code_changed(self) -> bool:
        """The code version differs -- legal when the patch *is* the treatment."""
        return bool(self.baseline_code_version) and bool(self.candidate_code_version) \
            and self.baseline_code_version != self.candidate_code_version

    @property
    def matched(self) -> bool:
        if self.legacy_untrusted:
            return False
        return (self.controls_identical and self.treatment_as_frozen
                and self.baseline_value is not None and self.candidate_value is not None)

    def mismatch_reasons(self) -> list[str]:
        pairs = (("inputs_identical", self.inputs_identical),
                 ("evaluator_identical", self.evaluator_identical),
                 ("protocol_identical", self.protocol_identical),
                 ("budget_comparable", self.budget_comparable),
                 ("environment_identical", self.environment_identical),
                 ("harness_version_identical", self.harness_version_identical),
                 ("treatment_as_frozen", self.treatment_as_frozen))
        return [name for name, ok in pairs if not ok]

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["matched"] = self.matched
        payload["controls_identical"] = self.controls_identical
        payload["treatment_as_frozen"] = self.treatment_as_frozen
        payload["code_changed"] = self.code_changed
        payload["mismatch_reasons"] = self.mismatch_reasons()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ComparisonProof":
        _check_schema(payload, "ComparisonProof")
        legacy = is_legacy_payload(payload)
        allowed = {f.name for f in dataclasses.fields(cls)} | {
            "schema_version", "matched", "controls_identical", "treatment_as_frozen",
            "code_changed", "mismatch_reasons"}
        if legacy:
            # the v1 names are accepted on read only; nothing writes them any more
            allowed |= {"budget_identical", "code_version_identical"}
        _reject_unknown(payload, allowed, "ComparisonProof")

        def _opt(name):
            raw = payload.get(name)
            return None if raw is None else float(raw)

        if legacy:
            # v1 named two of the controls differently.  Map them read-only; the v1
            # record keeps its own on-disk shape and is never rewritten.  The result
            # is marked untrusted: a legacy proof has no baseline evidence reference
            # and no treatment contract, so ``matched`` must never come out True.
            budget_comparable = bool(payload.get("budget_comparable",
                                                 payload.get("budget_identical")))
            harness_identical = bool(payload.get("harness_version_identical",
                                                 payload.get("code_version_identical")))
            matched_v1 = bool(payload.get("matched"))
            treatment_ok = bool(payload.get("treatment_as_frozen", matched_v1))
            return cls(legacy_untrusted=True,
                       inputs_identical=bool(payload.get("inputs_identical")),
                       evaluator_identical=bool(payload.get("evaluator_identical")),
                       protocol_identical=bool(payload.get("protocol_identical")),
                       budget_comparable=budget_comparable,
                       environment_identical=bool(payload.get("environment_identical", True)),
                       harness_version_identical=harness_identical,
                       baseline_treatment=str(payload.get("baseline_treatment") or "legacy:unspecified"),
                       candidate_treatment=str(payload.get("candidate_treatment") or "legacy:unspecified"),
                       treatment_diff_hash=str(payload.get("treatment_diff_hash")
                                               or ("legacy" if treatment_ok else "")),
                       expected_treatment_diff_hash=str(
                           payload.get("expected_treatment_diff_hash")
                           or ("legacy" if treatment_ok else "")),
                       baseline_code_version=str(payload.get("baseline_code_version") or ""),
                       candidate_code_version=str(payload.get("candidate_code_version") or ""),
                       baseline_value=_opt("baseline_value"), candidate_value=_opt("candidate_value"),
                       delta=_opt("delta"),
                       baseline_evidence_ref=str(payload.get("baseline_evidence_ref") or ""),
                       baseline_provenance=str(payload.get("baseline_provenance") or ""),
                       detail=str(payload.get("detail") or ""))
        return cls(inputs_identical=bool(payload.get("inputs_identical")),
                   evaluator_identical=bool(payload.get("evaluator_identical")),
                   protocol_identical=bool(payload.get("protocol_identical")),
                   budget_comparable=bool(payload.get("budget_comparable")),
                   environment_identical=bool(payload.get("environment_identical")),
                   harness_version_identical=bool(payload.get("harness_version_identical")),
                   baseline_treatment=str(payload.get("baseline_treatment") or ""),
                   candidate_treatment=str(payload.get("candidate_treatment") or ""),
                   treatment_diff_hash=str(payload.get("treatment_diff_hash") or ""),
                   expected_treatment_diff_hash=str(payload.get("expected_treatment_diff_hash") or ""),
                   baseline_code_version=str(payload.get("baseline_code_version") or ""),
                   candidate_code_version=str(payload.get("candidate_code_version") or ""),
                   baseline_value=_opt("baseline_value"), candidate_value=_opt("candidate_value"),
                   delta=_opt("delta"),
                   baseline_evidence_ref=str(payload.get("baseline_evidence_ref") or ""),
                   baseline_provenance=str(payload.get("baseline_provenance") or ""),
                   detail=str(payload.get("detail") or ""))


@dataclass(frozen=True)
class SettlementDecision:
    """The machine verdict, kept strictly apart from any LLM explanation.

    ``improvement_observed`` answers "did the expectation move?".
    ``publish_eligible`` answers "may this be promoted?".
    They are different questions and are stored as different fields.
    """

    settlement_id: str
    bet_id: str
    settlement_class: str
    expectation_outcomes: tuple[ExpectationOutcome, ...]
    comparison: ComparisonProof
    new_regressions: tuple[str, ...]
    pre_existing_failures: tuple[str, ...]
    expectations_met: bool
    improvement_over_baseline: bool
    baseline_already_satisfied: bool
    supported_claim: str
    environment: str
    improvement_observed: bool
    publish_eligible: bool
    publish_blockers: tuple[str, ...]
    next_state: str
    machine_rules: tuple[str, ...]
    llm_explanation: str = ""
    llm_explanation_authoritative: bool = False
    created_at: str = ""
    #: True when this record was read from a pre-correction schema.
    schema_legacy: bool = False
    #: The publish claim the *old* record carried.  Kept for audit only: it is
    #: history, never current publication eligibility.
    legacy_publish_claim: bool = False

    def __post_init__(self) -> None:
        _check_non_empty(self.settlement_id, "SettlementDecision", "settlement_id")
        _check_non_empty(self.bet_id, "SettlementDecision", "bet_id")
        _check_choice(self.settlement_class, SETTLEMENT_CLASSES, "SettlementDecision", "settlement_class")
        if self.llm_explanation_authoritative:
            raise ContractError(
                "SettlementDecision: llm_explanation must never be authoritative; the settlement "
                "is a machine rule")
        if not self.schema_legacy:
            _check_choice(self.environment, EXECUTION_ENVIRONMENTS, "SettlementDecision", "environment")
            _check_choice(self.supported_claim, CLAIMS, "SettlementDecision", "supported_claim")
            if bool(self.improvement_observed) != bool(self.improvement_over_baseline):
                raise ContractError(
                    "SettlementDecision: improvement_observed is a strict alias of "
                    "improvement_over_baseline (evidence-grounded improvement over the baseline); "
                    "they must not disagree")
            if self.publish_eligible and self.environment in NON_PUBLISHABLE_ENVIRONMENTS:
                raise ContractError(
                    f"SettlementDecision: environment={self.environment!r} can never be publishable; "
                    "isolation, synthetic and shadow evidence are blocked by contract")
        if self.settlement_class == "supported" and not self.schema_legacy:
            if not self.expectation_outcomes:
                raise ContractError("SettlementDecision: 'supported' requires expectation outcomes")
            if not self.expectation_outcomes or not all(o.met for o in self.expectation_outcomes):
                raise ContractError(
                    "SettlementDecision: 'supported' requires every expectation to be met")
            if not self.expectations_met:
                raise ContractError("SettlementDecision: 'supported' requires expectations_met")
            if self.supported_claim == "none":
                raise ContractError(
                    "SettlementDecision: 'supported' must state which claim it supports; "
                    "supported_claim='none' is contradictory")
            if self.supported_claim == "improvement_over_baseline" and not self.improvement_over_baseline:
                raise ContractError(
                    "SettlementDecision: a supported improvement claim requires "
                    "improvement_over_baseline")
            if not self.comparison.matched:
                raise ContractError(
                    f"SettlementDecision: 'supported' requires a matched comparison; mismatched: "
                    f"{self.comparison.mismatch_reasons()}")
            if self.new_regressions:
                raise ContractError("SettlementDecision: a supported settlement cannot add regressions")
        if self.settlement_class == "falsified" and self.expectations_met and not self.schema_legacy:
            raise ContractError(
                "SettlementDecision: 'falsified' means the expectation was not met; it cannot also "
                "report expectations_met")
        # NOTE: ``falsified`` *may* carry improvement_over_baseline=True.  The two
        # statements are about different things now: the relative delta can have
        # moved in the declared direction while a guardrail expectation failed, in
        # which case the direction is falsified even though the metric improved.
        # Forbidding the combination would force the kernel to hide a real result.
        if self.settlement_class == "abstained" and not self.schema_legacy:
            # An abstention is not a measurement outcome.  It must not smuggle in any of
            # the fields a measured settlement carries, or a reader could mistake "we did
            # not run" for "we ran and this is what happened".
            if self.expectation_outcomes:
                raise ContractError(
                    "SettlementDecision: 'abstained' carries no expectation outcomes; nothing was "
                    "measured, so there is nothing to report about the expectations")
            if self.expectations_met or self.improvement_over_baseline or self.baseline_already_satisfied:
                raise ContractError(
                    "SettlementDecision: 'abstained' must not report expectations_met, "
                    "improvement_over_baseline or baseline_already_satisfied; the bet deliberately "
                    "did not run")
            if self.supported_claim != "none":
                raise ContractError(
                    "SettlementDecision: 'abstained' claims nothing; supported_claim must be 'none'")
            if self.publish_eligible:
                raise ContractError("SettlementDecision: 'abstained' can never be publishable")
        if self.settlement_class == "falsified" and not self.schema_legacy:
            if not (self.expectations_met or self.improvement_over_baseline
                    or self.pre_existing_failures or self.new_regressions
                    or self.comparison.matched):
                raise ContractError(
                    "SettlementDecision: 'falsified' requires a machine-checkable ground "
                    "(an unmet expectation, a regression, or a matched comparison)")
        if self.schema_legacy and self.publish_eligible:
            raise ContractError(
                "SettlementDecision: a legacy (pre-correction) record can never be publishable; "
                "it carries no trusted environment and no baseline evidence")
        if self.legacy_publish_claim and not self.schema_legacy:
            raise ContractError(
                "SettlementDecision: legacy_publish_claim is only meaningful on a legacy record")
        if self.settlement_class in ("invalid", "blocked") and self.publish_eligible:
            raise ContractError(
                f"SettlementDecision: settlement_class={self.settlement_class} cannot be publishable")
        if self.publish_eligible and self.publish_blockers:
            raise ContractError(
                "SettlementDecision: publish_eligible=True cannot carry publish_blockers")
        if not tuple(self.machine_rules):
            raise ContractError("SettlementDecision: machine_rules must not be empty")

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["expectation_outcomes"] = [o.to_dict() for o in self.expectation_outcomes]
        payload["comparison"] = self.comparison.to_dict()
        payload["new_regressions"] = list(self.new_regressions)
        payload["pre_existing_failures"] = list(self.pre_existing_failures)
        payload["publish_blockers"] = list(self.publish_blockers)
        payload["machine_rules"] = list(self.machine_rules)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SettlementDecision":
        _check_schema(payload, "SettlementDecision")
        _reject_unknown(payload, {f.name for f in dataclasses.fields(cls)} | {"schema_version"},
                        "SettlementDecision")
        legacy = is_legacy_payload(payload)
        outcomes = tuple(ExpectationOutcome.from_dict(o)
                         for o in payload.get("expectation_outcomes") or ())
        improvement = payload.get("improvement_over_baseline")
        if improvement is None:
            # a v1 record only carried improvement_observed
            improvement = bool(payload.get("improvement_observed"))
        expectations_met = payload.get("expectations_met")
        if expectations_met is None:
            expectations_met = bool(outcomes) and all(o.met for o in outcomes)
        supported_claim = payload.get("supported_claim")
        if supported_claim is None:
            if str(payload.get("settlement_class")) == "supported":
                supported_claim = "absolute_attainment"
            else:
                supported_claim = "none"
        environment = payload.get("environment")
        if environment is None:
            # A v1 record has no environment field.  It is NEVER inferred to be
            # production from its own publish claim -- doing so was the security
            # bug this change removes.  Legacy records land in an explicit,
            # non-publishable, clearly-labelled environment instead.
            environment = "legacy_unknown" if legacy else "synthetic_fixture"
        legacy_claim = bool(payload.get("publish_eligible"))
        if legacy:
            # The old claim is preserved for audit and stripped of any current
            # authority, and the blocker names the reason.
            blockers = tuple(dict.fromkeys(
                (*tuple(payload.get("publish_blockers") or ()),
                 BLOCKER_LEGACY_SCHEMA_UNTRUSTED)))
        else:
            blockers = tuple(payload.get("publish_blockers") or ())
        return cls(
            settlement_id=str(_require(payload, "settlement_id", "SettlementDecision")),
            bet_id=str(_require(payload, "bet_id", "SettlementDecision")),
            settlement_class=str(_require(payload, "settlement_class", "SettlementDecision")),
            expectation_outcomes=outcomes,
            comparison=ComparisonProof.from_dict(_require(payload, "comparison", "SettlementDecision")),
            new_regressions=tuple(payload.get("new_regressions") or ()),
            pre_existing_failures=tuple(payload.get("pre_existing_failures") or ()),
            expectations_met=bool(expectations_met),
            improvement_over_baseline=bool(improvement),
            baseline_already_satisfied=bool(payload.get("baseline_already_satisfied")),
            supported_claim=str(supported_claim),
            environment=str(environment),
            improvement_observed=bool(payload.get("improvement_observed")),
            publish_eligible=(False if legacy else legacy_claim),
            publish_blockers=blockers,
            next_state=str(payload.get("next_state") or "CLOSED"),
            machine_rules=tuple(payload.get("machine_rules") or ()),
            llm_explanation=str(payload.get("llm_explanation") or ""),
            llm_explanation_authoritative=bool(payload.get("llm_explanation_authoritative", False)),
            created_at=str(payload.get("created_at") or ""),
            schema_legacy=legacy,
            legacy_publish_claim=(legacy_claim if legacy else False))


# ---------------------------------------------------------------------------
# experience
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExperienceRecord:
    """A single settled experience.

    ``level`` is pinned to ``experience``: one settled bet is one experience.
    Promotion to a cross-episode habit or to a growth claim requires separate,
    repeated evidence and is deliberately not expressible here (invariant:
    one run does not become a habit).
    """

    experience_id: str
    bet_id: str
    run_id: str
    settlement_ref: str
    settlement_class: str
    state_action_outcome_chain: tuple[str, ...]
    scope: str
    confidence: float
    evidence_refs: tuple[str, ...]
    #: ``experience`` = a bet was run and settled.  ``abstention`` = a bet was deliberately
    #: not run (the harness declined to wager).  Still exactly one episode either way, and
    #: still never a habit or a growth claim.
    level: str = "experience"
    authoritative: bool = False

    def __post_init__(self) -> None:
        for name in ("experience_id", "bet_id", "run_id", "settlement_ref", "scope"):
            _check_non_empty(getattr(self, name), "ExperienceRecord", name)
        _check_choice(self.settlement_class, SETTLEMENT_CLASSES, "ExperienceRecord", "settlement_class")
        if self.level not in EXPERIENCE_LEVELS:
            raise ContractError(
                "ExperienceRecord: level must be 'experience' or 'abstention'; a single settled "
                "bet is one experience and can never be recorded as habit or growth")
        if not tuple(self.state_action_outcome_chain):
            raise ContractError(
                "ExperienceRecord: state_action_outcome_chain must not be empty; an experience "
                "without the full chain is not traceable")
        if not tuple(self.evidence_refs):
            raise ContractError("ExperienceRecord: evidence_refs must not be empty")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ContractError("ExperienceRecord: confidence must be within [0, 1]")
        if self.authoritative:
            raise ContractError(
                "ExperienceRecord: authoritative must stay False; authority requires cross-episode "
                "verification outside this kernel")

    def assert_promotable(self) -> None:
        """The single gate every promotion consumer must call.

        Promotion (habit, growth, production policy, code promotion) is a
        different act from settling a bet.  This raises unless the experience is
        a settled, trusted, supported episode -- so a legacy or merely
        ``inconclusive`` record cannot be promoted by accident.
        """
        if self.authoritative:
            raise ContractError("ExperienceRecord: not promotable while non-authoritative")
        if self.settlement_class != "supported":
            raise ContractError(
                f"ExperienceRecord: settlement_class={self.settlement_class!r} is not promotable; "
                "only a supported settlement can start a promotion review")
        if self.level != "experience":
            raise ContractError(
                "ExperienceRecord: promotion requires a separate review; this record is one "
                "experience, not a habit or a growth claim")

    def to_dict(self) -> dict[str, Any]:
        payload = _payload(self)
        payload["state_action_outcome_chain"] = list(self.state_action_outcome_chain)
        payload["evidence_refs"] = list(self.evidence_refs)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperienceRecord":
        _check_schema(payload, "ExperienceRecord")
        _reject_unknown(payload, {f.name for f in dataclasses.fields(cls)} | {"schema_version"},
                        "ExperienceRecord")
        return cls(
            experience_id=str(_require(payload, "experience_id", "ExperienceRecord")),
            bet_id=str(_require(payload, "bet_id", "ExperienceRecord")),
            run_id=str(_require(payload, "run_id", "ExperienceRecord")),
            settlement_ref=str(_require(payload, "settlement_ref", "ExperienceRecord")),
            settlement_class=str(_require(payload, "settlement_class", "ExperienceRecord")),
            state_action_outcome_chain=tuple(payload.get("state_action_outcome_chain") or ()),
            scope=str(_require(payload, "scope", "ExperienceRecord")),
            confidence=float(payload.get("confidence") or 0.0),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
            level=str(payload.get("level") or "experience"),
            authoritative=bool(payload.get("authoritative", False)))


__all__ = [
    "SCHEMA_VERSION", "LEGACY_SCHEMA_VERSIONS", "READABLE_SCHEMA_VERSIONS",
    "schema_version_of", "is_legacy_payload",
    "EXECUTION_ENVIRONMENTS", "NON_PUBLISHABLE_ENVIRONMENTS", "EXPECTATION_KINDS",
    "CLAIM_BY_KIND", "CLAIMS", "BASELINE_PROVENANCE",
    "BLOCKER_ABSTAINED", "BLOCKER_NOT_PUBLISHABLE_ENVIRONMENT", "BLOCKER_ISOLATED_SAMPLE", "BLOCKER_SYNTHETIC",
    "BLOCKER_SHADOW", "BLOCKER_SINGLE_EPISODE", "BLOCKER_BASELINE_INCOMPATIBLE",
    "BLOCKER_TREATMENT_UNDECLARED", "BLOCKER_LEGACY_SCHEMA_UNTRUSTED",
    "TreatmentContract", "TreatmentSpec", "BaselineEvidence",
    "DIRECTIONS", "CANDIDATE_SOURCES", "SETTLEMENT_CLASSES",
    "EXPERIENCE_LEVELS", "LEVEL_ABSTENTION",
    "EXECUTION_STATUSES", "SEMANTIC_FIELDS", "ContractError", "Budget", "BudgetUsage",
    "CommitmentPolicy", "ExpectedEffect", "FalsificationCondition", "EvaluationProtocol",
    "Candidate", "RejectedAlternative", "BetRecord", "ExecutionReceipt", "OutcomeMeasurement",
    "ExpectationOutcome", "ComparisonProof", "SettlementDecision", "ExperienceRecord",
    "canonical_json", "sha256_of", "hash_text",
]
