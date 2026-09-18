"""Versioned protocol schema for Partner research experiments (M0).

Purpose
-------
Enforce a strict, versioned contract for research protocols (H1-H4 hypotheses,
primary/secondary metrics, allowed-modification objects, evaluator boundaries,
budgets, stop rules, human-intervention taxonomy).

The schema rejects unknown fields and missing critical conditions rather than
silently degrading. It is deliberately small so it can be referenced from
project_cycle, self_improvement_cycle, learning_improvement_cycle and the
benchmark runner without ambiguity.

State honesty
-------------
This module is implemented and unit-test covered, but **the runtime has not
been end-to-end verified** with this schema. All consumers should treat the
schema as ``implemented_unverified`` until a separate verification pass runs.

Status markers (M2 / M3 honesty contract)
-----------------------------------------
* ``implemented_unverified``    - code exists, static checks pass, no real run.
* ``pending_execution``         - requires a real run; do not claim success.
* ``annotation_pending``        - requires expert annotation to count.
* ``external_dependency_missing`` - needs a third-party artefact not in repo.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "research.protocol/v1"
SCHEMA_KIND = "research_protocol"

ALLOWED_TOP_KEYS: frozenset[str] = frozenset({
    "schema_version", "kind", "protocol_id", "title", "version",
    "hypotheses", "primary_metrics", "secondary_metrics",
    "allowed_modification_objects", "evaluator_boundaries",
    "budget", "stop_rules", "human_intervention_taxonomy",
    "baseline_protocols", "controls", "expected_direction",
    "minimum_meaningful_effect", "evidence_requirements",
    "tag_changes", "annotations", "metadata",
})

REQUIRED_TOP_KEYS: frozenset[str] = frozenset({
    "schema_version", "kind", "protocol_id", "version",
    "hypotheses", "primary_metrics", "allowed_modification_objects",
    "evaluator_boundaries", "budget", "stop_rules",
})

ALLOWED_HYPOTHESIS_KEYS: frozenset[str] = frozenset({
    "id", "claim", "falsification_condition", "required_evidence",
    "minimum_sample_size", "expected_effect_size",
})

ALLOWED_METRIC_KEYS: frozenset[str] = frozenset({
    "name", "kind", "direction", "aggregation",
    "minimum_meaningful_effect", "ci_method",
})

ALLOWED_MODIFICATION_KEYS: frozenset[str] = frozenset({
    "object", "scope", "must_record", "forbidden",
})

ALLOWED_EVALUATOR_KEYS: frozenset[str] = frozenset({
    "name", "isolation", "input", "output", "blind",
})

ALLOWED_BUDGET_KEYS: frozenset[str] = frozenset({
    "wallclock_seconds", "llm_calls", "candidate_attempts",
    "external_actions", "cost_usd_ceiling",
})

ALLOWED_STOP_RULE_KEYS: frozenset[str] = frozenset({
    "when", "action", "max_revisions",
})

ALLOWED_HUMAN_INTERVENTION_KEYS: frozenset[str] = frozenset({
    "label", "trigger", "expected_response", "default",
})

DEFAULT_BUDGET_CEILINGS = {
    "wallclock_seconds": 6 * 3600,
    "llm_calls": 2000,
    "candidate_attempts": 16,
    "external_actions": 100,
    "cost_usd_ceiling": 50.0,
}


class ProtocolSchemaError(ValueError):
    pass


class UnknownFieldError(ProtocolSchemaError):
    pass


class MissingFieldError(ProtocolSchemaError):
    pass


class InvalidFieldError(ProtocolSchemaError):
    pass


class BudgetCeilingExceededError(ProtocolSchemaError):
    pass


@dataclass(frozen=True)
class ResearchProtocol:
    schema_version: str
    kind: str
    protocol_id: str
    title: str
    version: str
    hypotheses: list
    primary_metrics: list
    secondary_metrics: list
    allowed_modification_objects: list
    evaluator_boundaries: list
    budget: dict
    stop_rules: list
    primary_delta_metrics: list = field(default_factory=list)
    human_intervention_taxonomy: list = field(default_factory=list)
    baseline_protocols: list = field(default_factory=list)
    controls: list = field(default_factory=list)
    expected_direction: dict = field(default_factory=dict)
    minimum_meaningful_effect: dict = field(default_factory=dict)
    evidence_requirements: list = field(default_factory=list)
    tag_changes: list = field(default_factory=list)
    annotations: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def __eq__(self, other):
        if not isinstance(other, ResearchProtocol):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __hash__(self) -> int:
        return hash(self.fingerprint())


_HYPOTHESIS_ID_RE = re.compile(r"^H[1-9][0-9]*$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.]+)?$")
_METRIC_DIRECTIONS = {"higher_is_better", "lower_is_better", "binary"}
_METRIC_AGGREGATIONS = {"mean", "median", "ci", "auc", "pass_rate"}


def _check_keys(payload, allowed, required, where):
    allowed_set = set(allowed)
    required_set = set(required)
    extra = set(payload.keys()) - allowed_set
    missing = required_set - set(payload.keys())
    if extra:
        raise UnknownFieldError(f"{where}: unknown field(s) {sorted(extra)}")
    if missing:
        raise MissingFieldError(f"{where}: missing required field(s) {sorted(missing)}")


def _check_hypothesis(idx, item):
    where = f"hypotheses[{idx}]"
    _check_keys(item, ALLOWED_HYPOTHESIS_KEYS, ("id", "claim", "falsification_condition"), where)
    if not _HYPOTHESIS_ID_RE.match(str(item.get("id") or "")):
        raise InvalidFieldError(f"{where}: id must match H1/H2/...; got {item.get('id')!r}")
    if not str(item.get("claim") or "").strip():
        raise InvalidFieldError(f"{where}: claim must be a non-empty string")
    if not str(item.get("falsification_condition") or "").strip():
        raise InvalidFieldError(f"{where}: falsification_condition must be non-empty")


def _check_metric(idx, item, kind):
    where = f"{kind}[{idx}]"
    _check_keys(item, ALLOWED_METRIC_KEYS, ("name", "direction"), where)
    if str(item.get("direction") or "") not in _METRIC_DIRECTIONS:
        raise InvalidFieldError(
            f"{where}: direction must be one of {sorted(_METRIC_DIRECTIONS)}; got {item.get('direction')!r}"
        )
    if "aggregation" in item and str(item["aggregation"]) not in _METRIC_AGGREGATIONS:
        raise InvalidFieldError(
            f"{where}: aggregation must be one of {sorted(_METRIC_AGGREGATIONS)}"
        )


def _check_modification(idx, item):
    where = f"allowed_modification_objects[{idx}]"
    _check_keys(item, ALLOWED_MODIFICATION_KEYS, ("object", "scope"), where)
    if not str(item.get("object") or "").strip():
        raise InvalidFieldError(f"{where}: object must be non-empty")
    if not str(item.get("scope") or "").strip():
        raise InvalidFieldError(f"{where}: scope must be non-empty")


def _check_evaluator(idx, item):
    where = f"evaluator_boundaries[{idx}]"
    _check_keys(item, ALLOWED_EVALUATOR_KEYS, ("name", "isolation"), where)
    if not str(item.get("name") or "").strip():
        raise InvalidFieldError(f"{where}: name must be non-empty")
    iso = str(item.get("isolation") or "")
    if iso not in {"process", "filesystem", "network", "memory", "model"}:
        raise InvalidFieldError(
            f"{where}: isolation must be process/filesystem/network/memory/model; got {iso!r}"
        )


def _check_stop_rule(idx, item):
    where = f"stop_rules[{idx}]"
    _check_keys(item, ALLOWED_STOP_RULE_KEYS, ("when", "action"), where)


def _check_human(idx, item):
    where = f"human_intervention_taxonomy[{idx}]"
    _check_keys(item, ALLOWED_HUMAN_INTERVENTION_KEYS, ("label", "default"), where)
    if str(item.get("default") or "") not in {"allow", "block", "defer"}:
        raise InvalidFieldError(
            f"{where}: default must be allow/block/defer; got {item.get('default')!r}"
        )


def _check_budget_ceiling(budget):
    for key, ceiling in DEFAULT_BUDGET_CEILINGS.items():
        if key in budget:
            try:
                value = float(budget[key])
            except (TypeError, ValueError) as exc:
                raise InvalidFieldError(f"budget.{key}: not numeric ({exc})")
            if value > ceiling:
                raise BudgetCeilingExceededError(
                    f"budget.{key}={value} exceeds hard ceiling {ceiling}"
                )


def validate(payload):
    if not isinstance(payload, dict):
        raise ProtocolSchemaError(f"payload must be a dict; got {type(payload).__name__}")
    _check_keys(payload, ALLOWED_TOP_KEYS, REQUIRED_TOP_KEYS, "protocol")

    if payload["schema_version"] != SCHEMA_VERSION:
        raise InvalidFieldError(f"schema_version must be {SCHEMA_VERSION!r}")
    if payload["kind"] != SCHEMA_KIND:
        raise InvalidFieldError(f"kind must be {SCHEMA_KIND!r}")
    if not re.match(r"^[A-Za-z][A-Za-z0-9_.\-]*$", str(payload["protocol_id"])):
        raise InvalidFieldError(f"protocol_id must match identifier pattern")
    if not _VERSION_RE.match(str(payload["version"])):
        raise InvalidFieldError(f"version must be semver; got {payload['version']!r}")

    hypotheses = payload["hypotheses"]
    if not isinstance(hypotheses, list) or not hypotheses:
        raise InvalidFieldError("hypotheses must be a non-empty list")
    for idx, h in enumerate(hypotheses):
        _check_hypothesis(idx, h)

    primary = payload["primary_metrics"]
    if not isinstance(primary, list) or not primary:
        raise InvalidFieldError("primary_metrics must be a non-empty list")
    for idx, m in enumerate(primary):
        _check_metric(idx, m, "primary_metrics")

    secondary = payload.get("secondary_metrics", []) or []
    for idx, m in enumerate(secondary):
        _check_metric(idx, m, "secondary_metrics")

    mods = payload["allowed_modification_objects"]
    if not isinstance(mods, list) or not mods:
        raise InvalidFieldError("allowed_modification_objects must be a non-empty list")
    for idx, m in enumerate(mods):
        _check_modification(idx, m)

    evaluators = payload["evaluator_boundaries"]
    if not isinstance(evaluators, list) or not evaluators:
        raise InvalidFieldError("evaluator_boundaries must be a non-empty list")
    for idx, e in enumerate(evaluators):
        _check_evaluator(idx, e)

    budget = payload["budget"]
    _check_keys(budget, ALLOWED_BUDGET_KEYS, ("wallclock_seconds",), "budget")
    _check_budget_ceiling(budget)

    stop_rules = payload["stop_rules"]
    if not isinstance(stop_rules, list) or not stop_rules:
        raise InvalidFieldError("stop_rules must be a non-empty list")
    for idx, r in enumerate(stop_rules):
        _check_stop_rule(idx, r)

    humans = payload.get("human_intervention_taxonomy", []) or []
    for idx, h in enumerate(humans):
        _check_human(idx, h)

    return ResearchProtocol(
        schema_version=payload["schema_version"],
        kind=payload["kind"],
        protocol_id=payload["protocol_id"],
        title=str(payload.get("title") or ""),
        version=payload["version"],
        hypotheses=list(hypotheses),
        primary_metrics=list(primary),
        secondary_metrics=list(secondary),
        allowed_modification_objects=list(mods),
        evaluator_boundaries=list(evaluators),
        budget=dict(budget),
        stop_rules=list(stop_rules),
        human_intervention_taxonomy=list(humans),
        baseline_protocols=list(payload.get("baseline_protocols", []) or []),
        controls=list(payload.get("controls", []) or []),
        expected_direction=dict(payload.get("expected_direction", {}) or {}),
        minimum_meaningful_effect=dict(payload.get("minimum_meaningful_effect", {}) or {}),
        evidence_requirements=list(payload.get("evidence_requirements", []) or []),
        tag_changes=list(payload.get("tag_changes", []) or []),
        annotations=list(payload.get("annotations", []) or []),
        metadata=dict(payload.get("metadata", {}) or {}),
    )


def load_protocol(path):
    text = Path(path).read_text(encoding="utf-8")
    payload = json.loads(text)
    return validate(payload)


def dump_protocol(protocol, path):
    payload = protocol.to_dict()
    Path(path).write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


__all__ = [
    "ALLOWED_TOP_KEYS",
    "REQUIRED_TOP_KEYS",
    "DEFAULT_BUDGET_CEILINGS",
    "ProtocolSchemaError",
    "UnknownFieldError",
    "MissingFieldError",
    "InvalidFieldError",
    "BudgetCeilingExceededError",
    "ResearchProtocol",
    "SCHEMA_VERSION",
    "SCHEMA_KIND",
    "validate",
    "load_protocol",
    "dump_protocol",
]
