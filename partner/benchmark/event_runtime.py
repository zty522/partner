"""Contracts and persistence for benchmark runs executed by Event Flows.

The benchmark controller and the subject share the Event engine, but never the
evaluation view.  A subject receives only ``subject_view``; hidden references,
scores and expected outputs stay in the benchmark run directory.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import json
import os
import re
import uuid


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return "sha256:" + sha256(canonical(value).encode("utf-8")).hexdigest()


def root(workspace: str | Path) -> Path:
    value = Path(workspace).expanduser().resolve()
    return value.parent.parent if value.parent.name == "instances" else value


@dataclass(frozen=True)
class BenchmarkProtocolV1:
    protocol_id: str
    version: str
    subject_flow: str
    arms: tuple[str, ...]
    primary_metric: dict[str, Any]
    expected_effect: dict[str, Any]
    guardrails: tuple[dict[str, Any], ...]
    checkpoints: tuple[dict[str, Any], ...]
    budget: dict[str, Any]
    required_inputs: tuple[str, ...] = ()
    allowed_arm_differences: tuple[str, ...] = ()
    subject_view: dict[str, Any] = field(default_factory=dict)
    evaluators: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BenchmarkProtocolV1":
        required = {"protocol_id", "version", "subject_flow", "arms", "primary_metric",
                    "expected_effect", "guardrails", "checkpoints", "budget"}
        missing = required - set(raw)
        if missing:
            raise ValueError(f"benchmark protocol missing {sorted(missing)}")
        arms = tuple(str(v) for v in raw["arms"])
        if len(arms) < 2 or len(set(arms)) != len(arms):
            raise ValueError("benchmark protocol requires at least two unique arms")
        checkpoints = tuple(dict(v) for v in raw["checkpoints"])
        checkpoint_ids = [str(v.get("id") or "") for v in checkpoints]
        if not all(checkpoint_ids) or len(checkpoint_ids) != len(set(checkpoint_ids)):
            raise ValueError("checkpoint ids must be nonempty and unique")
        return cls(
            protocol_id=str(raw["protocol_id"]), version=str(raw["version"]),
            subject_flow=str(raw["subject_flow"]), arms=arms,
            primary_metric=dict(raw["primary_metric"]),
            expected_effect=dict(raw["expected_effect"]),
            guardrails=tuple(dict(v) for v in raw["guardrails"]),
            checkpoints=checkpoints, budget=dict(raw["budget"]),
            required_inputs=tuple(str(v) for v in raw.get("required_inputs") or ()),
            allowed_arm_differences=tuple(str(v) for v in raw.get("allowed_arm_differences") or ()),
            subject_view=dict(raw.get("subject_view") or {}),
            evaluators=dict(raw.get("evaluators") or {}),
            schema_version=int(raw.get("schema_version") or 1),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("arms", "guardrails", "checkpoints", "required_inputs",
                    "allowed_arm_differences"):
            value[key] = list(value[key])
        return value


class BenchmarkProtocolStore:
    def __init__(self, workspace: str | Path):
        self.workspace = root(workspace)
        self.builtin = Path(__file__).with_name("event_protocols")
        self.external = self.workspace / "config/benchmark_protocols"

    @staticmethod
    def _safe(identifier: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier):
            raise ValueError("invalid benchmark protocol id")
        return identifier

    def load(self, protocol_id: str) -> tuple[BenchmarkProtocolV1, Path]:
        identifier = self._safe(protocol_id)
        candidates = (self.external / f"{identifier}.json", self.builtin / f"{identifier}.json")
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
            raise FileNotFoundError(f"benchmark protocol not found: {identifier}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        protocol = BenchmarkProtocolV1.from_dict(raw)
        if protocol.protocol_id != identifier:
            raise ValueError("protocol id does not match filename")
        return protocol, path


class BenchmarkRunStore:
    def __init__(self, workspace: str | Path, run_id: str):
        self.workspace = root(workspace)
        self.run_id = run_id or "bench_" + uuid.uuid4().hex[:16]
        self.directory = self.workspace / "state/benchmarks/runs" / self.run_id
        self.directory.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, value: Mapping[str, Any]) -> Path:
        target = self.directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return target

    def read(self, name: str, default: Any = None) -> Any:
        path = self.directory / name
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def append_checkpoint(self, value: Mapping[str, Any]) -> Path:
        checkpoint_id = str(value.get("checkpoint_id") or "unknown")
        arm = str(value.get("arm_id") or "subject")
        return self.write(f"checkpoints/{arm}/{checkpoint_id}.json", value)


def public_subject_view(protocol: BenchmarkProtocolV1, supplied: Mapping[str, Any], arm: str) -> dict[str, Any]:
    """Return the only protocol view visible to the system under test."""
    feature = supplied.get("declared_feature") if arm == protocol.arms[1] else None
    return {
        "protocol_id": protocol.protocol_id,
        "protocol_version": protocol.version,
        "arm_id": arm,
        "task": dict(protocol.subject_view),
        "inputs": {key: supplied[key] for key in protocol.required_inputs if key in supplied},
        "budget": dict(protocol.budget),
        "allowed_arm_differences": list(protocol.allowed_arm_differences),
        "arm_configuration": {"features": {"declared_feature": feature}},
        "evaluation_contract": {
            "primary_metric": dict(protocol.primary_metric),
            "guardrails": [{"id": str(row.get("id")), "hard": bool(row.get("hard"))}
                           for row in protocol.guardrails],
            "required_artifact": "benchmark_evidence.json",
            "artifact_schema": {
                "metrics": {str(protocol.primary_metric.get("name") or "score"): "number"},
                "predictions": [{"sample_id": "string", "y_true": "number", "y_pred": "number"}],
                "guardrails": {"<guardrail_id>": "boolean"},
                "run_config": {
                    "model": "frozen model identifier", "folds": "frozen fold hash",
                    "seeds": "frozen seed hash",
                    "budget": "actual budget record",
                    "features": {"declared_feature": "null for baseline; declared value for candidate"}
                },
                "provenance": {"code_revision": "string", "data_hash": "string"}
            },
        },
    }


def numeric_metrics(value: Any) -> dict[str, float]:
    """Collect typed numeric metrics without interpreting prose as evidence."""
    result: dict[str, float] = {}
    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            metrics = node.get("metrics")
            if isinstance(metrics, Mapping):
                for key, item in metrics.items():
                    if isinstance(item, (int, float)) and not isinstance(item, bool):
                        result[str(key)] = float(item)
            semantic = node.get("semantic_output")
            if isinstance(semantic, Mapping):
                embedded = semantic.get("metrics")
                if isinstance(embedded, Mapping):
                    for key, item in embedded.items():
                        if isinstance(item, (int, float)) and not isinstance(item, bool):
                            result[str(key)] = float(item)
            for item in node.values():
                if isinstance(item, (Mapping, list, tuple)):
                    visit(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                visit(item)
    visit(value)
    return result


__all__ = ["BenchmarkProtocolV1", "BenchmarkProtocolStore", "BenchmarkRunStore",
           "canonical", "digest", "numeric_metrics", "public_subject_view", "root"]
