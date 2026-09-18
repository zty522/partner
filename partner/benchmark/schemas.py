"""Benchmark data contracts (M3 / Section 6.1, 8.1).

These dataclasses are the single source of truth for what a benchmark run
produces. They are versioned and the schema_version field on each instance
must be incremented whenever the shape changes; old versions are kept in
the canonical docs and parsed alongside the new ones.

Validation: each ``validate()`` method enforces required fields and rejects
unknown keys. The schemas deliberately reject mixing structural metadata
with raw LLM output: e.g. ``Score`` does not allow a free-form ``details``
field, only the typed metric table.

State honesty: static_implemented. No real benchmark has been run.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "benchmark/v1"
_SCORE_DIRS = {"higher_is_better", "lower_is_better", "binary"}
_TRAJ_STATES = {"running", "succeeded", "failed", "cancelled", "inconclusive"}
_EVO_DECISIONS = {"promote", "reject", "inconclusive", "no_change"}
_RUN_STATES = {"draft", "ready", "running", "completed", "failed", "cancelled"}




def _opt(k: str, payload: dict):
    if k == "notes":
        return payload.get("notes", "")
    if k == "schema_version":
        return payload.get("schema_version", SCHEMA_VERSION)
    return payload[k]

def _check_keys(payload: dict[str, Any], allowed: Iterable[str], required: Iterable[str], where: str) -> None:
    extra = set(payload.keys()) - set(allowed)
    missing = set(required) - set(payload.keys())
    if extra:
        raise ValueError(f"{where}: unknown field(s) {sorted(extra)}")
    if missing:
        raise ValueError(f"{where}: missing required field(s) {sorted(missing)}")


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Suite:
    suite_id: str
    title: str
    version: str
    domains: list[str]
    tracks: list[str]
    task_families: list[str]
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Suite":
        _check_keys(payload, ("suite_id", "title", "version", "domains", "tracks",
                              "task_families", "schema_version", "notes"),
                    ("suite_id", "title", "version", "domains", "tracks", "task_families"),
                    "Suite")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Suite.schema_version must be {SCHEMA_VERSION}")
        return cls(**{k: _opt(k, payload) for k in (
            "suite_id", "title", "version", "domains", "tracks", "task_families",
            "schema_version", "notes"
        )})


@dataclass(frozen=True)
class ProjectEpisode:
    episode_id: str
    project_id: str
    domain: str
    stage_count: int
    initial_state: dict[str, Any]
    expected_artifacts: list[str]
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProjectEpisode":
        _check_keys(payload, ("episode_id", "project_id", "domain", "stage_count",
                              "initial_state", "expected_artifacts",
                              "schema_version", "notes"),
                    ("episode_id", "project_id", "domain", "stage_count",
                     "initial_state", "expected_artifacts"),
                    "ProjectEpisode")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"ProjectEpisode.schema_version must be {SCHEMA_VERSION}")
        return cls(**{k: _opt(k, payload) for k in (
            "episode_id", "project_id", "domain", "stage_count",
            "initial_state", "expected_artifacts", "schema_version", "notes"
        )})


@dataclass(frozen=True)
class Task:
    task_id: str
    family: str
    domain: str
    inputs: list[str]
    success_criteria: list[str]
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Task":
        _check_keys(payload, ("task_id", "family", "domain", "inputs",
                              "success_criteria", "schema_version", "notes"),
                    ("task_id", "family", "domain", "inputs", "success_criteria"),
                    "Task")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Task.schema_version must be {SCHEMA_VERSION}")
        return cls(**{k: _opt(k, payload) for k in (
            "task_id", "family", "domain", "inputs", "success_criteria",
            "schema_version", "notes"
        )})


@dataclass(frozen=True)
class Source:
    source_id: str
    title: str
    url: str
    version: str
    license: str
    captured_at: str
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Source":
        _check_keys(payload, ("source_id", "title", "url", "version",
                              "license", "captured_at", "schema_version", "notes"),
                    ("source_id", "title", "url", "version", "license", "captured_at"),
                    "Source")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Source.schema_version must be {SCHEMA_VERSION}")
        return cls(**{k: _opt(k, payload) for k in (
            "source_id", "title", "url", "version", "license", "captured_at",
            "schema_version", "notes"
        )})


@dataclass(frozen=True)
class Annotation:
    annotation_id: str
    task_id: str
    label: str              # accepted | rejected | pending | needs_human | not_applicable
    evidence_refs: list[str]
    annotation_pending: bool
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Annotation":
        _check_keys(payload, ("annotation_id", "task_id", "label", "evidence_refs",
                              "annotation_pending", "schema_version", "notes"),
                    ("annotation_id", "task_id", "label", "evidence_refs",
                     "annotation_pending"),
                    "Annotation")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Annotation.schema_version must be {SCHEMA_VERSION}")
        if payload.get("label") not in {"accepted", "rejected", "pending",
                                        "needs_human", "not_applicable"}:
            raise ValueError(f"Annotation.label invalid: {payload.get('label')!r}")
        return cls(**{k: _opt(k, payload) for k in (
            "annotation_id", "task_id", "label", "evidence_refs",
            "annotation_pending", "schema_version", "notes"
        )})


@dataclass(frozen=True)
class Protocol:
    protocol_id: str
    title: str
    version: str
    hypotheses: list[str]
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Protocol":
        _check_keys(payload, ("protocol_id", "title", "version", "hypotheses",
                              "schema_version", "notes"),
                    ("protocol_id", "title", "version", "hypotheses"),
                    "Protocol")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Protocol.schema_version must be {SCHEMA_VERSION}")
        return cls(**{k: _opt(k, payload) for k in (
            "protocol_id", "title", "version", "hypotheses",
            "schema_version", "notes"
        )})


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    protocol_id: str
    parent_run_id: str | None
    code_sha: str
    code_dirty: bool
    model: str
    tool_permissions: list[str]
    budget: dict[str, Any]
    random_seed: int
    started_at: str
    state: str
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunManifest":
        _check_keys(payload, ("run_id", "protocol_id", "parent_run_id", "code_sha",
                              "code_dirty", "model", "tool_permissions", "budget",
                              "random_seed", "started_at", "state",
                              "schema_version", "notes"),
                    ("run_id", "protocol_id", "code_sha", "code_dirty", "model",
                     "tool_permissions", "budget", "random_seed", "started_at", "state"),
                    "RunManifest")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"RunManifest.schema_version must be {SCHEMA_VERSION}")
        if payload.get("state") not in _RUN_STATES:
            raise ValueError(f"RunManifest.state invalid: {payload.get('state')!r}")
        return cls(**{k: _opt(k, payload) for k in (
            "run_id", "protocol_id", "parent_run_id", "code_sha", "code_dirty",
            "model", "tool_permissions", "budget", "random_seed", "started_at",
            "state", "schema_version", "notes"
        )})

    def fingerprint(self) -> str:
        return _fingerprint(asdict(self))


@dataclass(frozen=True)
class Trajectory:
    trajectory_id: str
    run_id: str
    state: str
    events: list[dict[str, Any]]
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Trajectory":
        _check_keys(payload, ("trajectory_id", "run_id", "state", "events",
                              "schema_version", "notes"),
                    ("trajectory_id", "run_id", "state", "events"),
                    "Trajectory")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Trajectory.schema_version must be {SCHEMA_VERSION}")
        if payload.get("state") not in _TRAJ_STATES:
            raise ValueError(f"Trajectory.state invalid: {payload.get('state')!r}")
        return cls(**{k: _opt(k, payload) for k in (
            "trajectory_id", "run_id", "state", "events",
            "schema_version", "notes"
        )})


@dataclass(frozen=True)
class EvolutionAttempt:
    attempt_id: str
    run_id: str
    parent_attempt_id: str | None
    decision: str
    evidence_refs: list[str]
    hypothesis: str
    expected_effect_size: float
    observed_effect_size: float
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvolutionAttempt":
        _check_keys(payload, ("attempt_id", "run_id", "parent_attempt_id",
                              "decision", "evidence_refs", "hypothesis",
                              "expected_effect_size", "observed_effect_size",
                              "schema_version", "notes"),
                    ("attempt_id", "run_id", "decision", "evidence_refs",
                     "hypothesis", "expected_effect_size", "observed_effect_size"),
                    "EvolutionAttempt")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"EvolutionAttempt.schema_version must be {SCHEMA_VERSION}")
        if payload.get("decision") not in _EVO_DECISIONS:
            raise ValueError(f"EvolutionAttempt.decision invalid: {payload.get('decision')!r}")
        return cls(**{k: _opt(k, payload) for k in (
            "attempt_id", "run_id", "parent_attempt_id", "decision",
            "evidence_refs", "hypothesis", "expected_effect_size",
            "observed_effect_size", "schema_version", "notes"
        )})


@dataclass(frozen=True)
class Score:
    score_id: str
    run_id: str
    metric_name: str
    direction: str
    value: float
    confidence_interval: tuple[float, float] | None
    missing_reason: str | None
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Score":
        _check_keys(payload, ("score_id", "run_id", "metric_name", "direction",
                              "value", "confidence_interval", "missing_reason",
                              "schema_version", "notes"),
                    ("score_id", "run_id", "metric_name", "direction", "value"),
                    "Score")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Score.schema_version must be {SCHEMA_VERSION}")
        if payload.get("direction") not in _SCORE_DIRS:
            raise ValueError(f"Score.direction invalid: {payload.get('direction')!r}")
        ci = payload.get("confidence_interval")
        if ci is not None:
            if not (isinstance(ci, list) and len(ci) == 2):
                raise ValueError("Score.confidence_interval must be [lower, upper] or null")
            ci = (float(ci[0]), float(ci[1]))
        return cls(
            score_id=payload["score_id"],
            run_id=payload["run_id"],
            metric_name=payload["metric_name"],
            direction=payload["direction"],
            value=float(payload["value"]),
            confidence_interval=ci,
            missing_reason=payload.get("missing_reason"),
            schema_version=payload.get("schema_version", SCHEMA_VERSION),
            notes=payload.get("notes", ""),
        )


@dataclass(frozen=True)
class Issue:
    issue_id: str
    run_id: str
    category: str           # infrastructure | candidate | data | evaluator | protocol
    severity: str           # info | warning | error | critical
    summary: str
    evidence_refs: list[str]
    reproduction_command: str
    expected: str
    actual: str
    root_cause_confidence: float  # 0..1; >0.7 requires non-LLM evidence
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Issue":
        _check_keys(payload, ("issue_id", "run_id", "category", "severity",
                              "summary", "evidence_refs", "reproduction_command",
                              "expected", "actual", "root_cause_confidence",
                              "schema_version", "notes"),
                    ("issue_id", "run_id", "category", "severity", "summary",
                     "evidence_refs", "reproduction_command", "expected", "actual",
                     "root_cause_confidence"),
                    "Issue")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Issue.schema_version must be {SCHEMA_VERSION}")
        if payload.get("category") not in {"infrastructure", "candidate", "data",
                                           "evaluator", "protocol"}:
            raise ValueError(f"Issue.category invalid: {payload.get('category')!r}")
        if payload.get("severity") not in {"info", "warning", "error", "critical"}:
            raise ValueError(f"Issue.severity invalid: {payload.get('severity')!r}")
        rc = float(payload.get("root_cause_confidence", 0.0))
        if rc < 0.0 or rc > 1.0:
            raise ValueError("Issue.root_cause_confidence must be in [0, 1]")
        return cls(**{k: _opt(k, payload) for k in (
            "issue_id", "run_id", "category", "severity", "summary",
            "evidence_refs", "reproduction_command", "expected", "actual",
            "root_cause_confidence", "schema_version", "notes"
        )})


@dataclass(frozen=True)
class Report:
    report_id: str
    run_id: str
    title: str
    sections: list[str]
    metric_table: list[dict[str, Any]]
    paper_figure_refs: list[str]   # figure 1..5 in Partner_Nature plan
    missing_or_incomparable: list[str]
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Report":
        _check_keys(payload, ("report_id", "run_id", "title", "sections",
                              "metric_table", "paper_figure_refs",
                              "missing_or_incomparable", "schema_version", "notes"),
                    ("report_id", "run_id", "title", "sections", "metric_table",
                     "paper_figure_refs"),
                    "Report")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Report.schema_version must be {SCHEMA_VERSION}")
        return cls(**{k: _opt(k, payload) for k in (
            "report_id", "run_id", "title", "sections", "metric_table",
            "paper_figure_refs", "missing_or_incomparable",
            "schema_version", "notes"
        )})



def _pick(payload: dict, keys: tuple, *, schema_version: str = SCHEMA_VERSION) -> dict:
    """Pick required keys but tolerate optional defaults (notes, schema_version)."""
    return {k: payload.get(k, "") if k == "notes"
            else payload.get(k, schema_version) if k == "schema_version"
            else payload[k]
            for k in keys}


def dump(obj, path) -> None:
    Path(path).write_text(
        json.dumps(asdict(obj), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


__all__ = [
    "SCHEMA_VERSION",
    "Suite", "ProjectEpisode", "Task", "Source", "Annotation", "Protocol",
    "RunManifest", "Trajectory", "EvolutionAttempt", "Score", "Issue", "Report",
    "dump",
]
