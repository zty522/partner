"""Independent measurement.

The evaluator's job is to turn *artifacts produced by an execution* into machine
numbers, without ever consulting what the agent said about them.

Three things make the measurement independent, and all three are enforced here:

1.  the evaluator reads only the artifacts the execution receipt names
2.  the evaluator refuses paths that look like agent self-reports or verdicts
3.  the artifact hashes are captured before and after, and must be identical --
    an execution that rewrites its evidence while it is being measured is not
    being measured

Anything missing, unreadable, or unparsable becomes ``validity='missing'`` /
``'invalid'`` with a stated reason.  It is never silently scored as zero, and it
is never silently scored as success.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .models import (
    BetRecord, ContractError, ExecutionReceipt, OutcomeMeasurement, sha256_of,
)
from .store import file_sha256

#: Names that indicate a self-report rather than a measurement input.
FORBIDDEN_ARTIFACT_MARKERS = (
    "verdict", "self_score", "selfscore", "agent_opinion", "llm_verdict",
    "conclusion", "success_claim",
)

MetricExtractor = Callable[[Mapping[str, Any]], float]


class EvaluationError(RuntimeError):
    """The evaluator cannot produce a measurement.  Fail closed."""


class NotIndependentError(EvaluationError):
    """The evaluator was pointed at something that is not independent evidence."""


class ArtifactIsolation:
    """Guards that the measured artifacts are execution output, not agent claims."""

    def __init__(self, *, allowed_roots: Sequence[str | Path] = ()) -> None:
        self.allowed_roots = tuple(Path(root) for root in allowed_roots)

    def check_path(self, path: str | Path) -> Path:
        candidate = Path(path)
        lowered = candidate.name.lower()
        for marker in FORBIDDEN_ARTIFACT_MARKERS:
            if marker in lowered:
                raise NotIndependentError(
                    f"refusing to measure {candidate.name!r}: the name marks it as an agent "
                    "self-report, which cannot be independent evidence")
        if self.allowed_roots and not any(self._within(candidate, root) for root in self.allowed_roots):
            raise NotIndependentError(
                f"refusing to measure {candidate}: outside the declared artifact roots "
                f"{[str(r) for r in self.allowed_roots]}")
        return candidate

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            return False

    @staticmethod
    def snapshot(paths: Sequence[str | Path]) -> dict[str, str]:
        out: dict[str, str] = {}
        for path in paths:
            candidate = Path(path)
            if candidate.exists() and candidate.is_file():
                out[str(candidate)] = file_sha256(candidate)
        return out


@dataclass(frozen=True)
class MeasurementResult:
    """All measurements for one execution, plus the independence proof."""

    measurements: tuple[OutcomeMeasurement, ...]
    metric_values: Mapping[str, float | None]
    validity: str
    missing_reason: str
    artifact_hashes_before: Mapping[str, str]
    artifact_hashes_after: Mapping[str, str]
    artifacts_read: tuple[str, ...]

    @property
    def agent_artifacts_unchanged(self) -> bool:
        return dict(self.artifact_hashes_before) == dict(self.artifact_hashes_after)

    @property
    def is_valid(self) -> bool:
        return self.validity == "valid"

    def primary(self, metric: str) -> OutcomeMeasurement:
        for measurement in self.measurements:
            if measurement.metric == metric:
                return measurement
        raise EvaluationError(f"no measurement for metric {metric!r} in {list(self.metric_values)}")

    def to_dict(self) -> dict[str, Any]:
        return {"measurements": [m.to_dict() for m in self.measurements],
                "metric_values": dict(self.metric_values), "validity": self.validity,
                "missing_reason": self.missing_reason,
                "artifact_hashes_before": dict(self.artifact_hashes_before),
                "artifact_hashes_after": dict(self.artifact_hashes_after),
                "artifacts_read": list(self.artifacts_read)}


class JsonMetricEvaluator:
    """Deterministic evaluator over a declared JSON artifact.

    ``artifact_key`` names the artifact inside ``receipt.artifacts`` whose payload
    carries the raw data; ``extractors`` maps a metric name to a pure function
    over that payload.  Both are declared in the frozen protocol, so the same
    numbers are produced for baseline and candidate.
    """

    def __init__(self, *, evaluator_id: str, evaluator_version: str,
                 artifact_key: str, extractors: Mapping[str, MetricExtractor],
                 isolation: ArtifactIsolation | None = None,
                 direction_by_metric: Mapping[str, str] | None = None,
                 unit_by_metric: Mapping[str, str] | None = None) -> None:
        if not extractors:
            raise ContractError("JsonMetricEvaluator: at least one extractor is required")
        self.evaluator_id = str(evaluator_id)
        self.evaluator_version = str(evaluator_version)
        self.artifact_key = str(artifact_key)
        self._extractors = dict(extractors)
        self._isolation = isolation or ArtifactIsolation()
        self._directions = dict(direction_by_metric or {})
        self._units = dict(unit_by_metric or {})

    def measure(self, *, bet: BetRecord, receipt: ExecutionReceipt) -> MeasurementResult:
        artifact_path = self._resolve_artifact(receipt)
        before = ArtifactIsolation.snapshot([artifact_path])
        failing = self._validity_failure(bet, receipt, artifact_path)
        if failing is not None:
            validity, reason = failing
            return MeasurementResult(
                measurements=self._blank_measurements(bet, receipt, validity, reason),
                metric_values={spec["metric"]: None for spec in bet.evaluation_protocol.metric_specs},
                validity=validity, missing_reason=reason,
                artifact_hashes_before=before, artifact_hashes_after=before,
                artifacts_read=(str(artifact_path),))

        try:
            payload = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            reason = f"artifact unreadable: {type(exc).__name__}: {exc}"
            return MeasurementResult(
                measurements=self._blank_measurements(bet, receipt, "invalid", reason),
                metric_values={spec["metric"]: None for spec in bet.evaluation_protocol.metric_specs},
                validity="invalid", missing_reason=reason,
                artifact_hashes_before=before, artifact_hashes_after=before,
                artifacts_read=(str(artifact_path),))
        if not isinstance(payload, dict):
            reason = "artifact payload is not a JSON object"
            return MeasurementResult(
                measurements=self._blank_measurements(bet, receipt, "invalid", reason),
                metric_values={spec["metric"]: None for spec in bet.evaluation_protocol.metric_specs},
                validity="invalid", missing_reason=reason,
                artifact_hashes_before=before, artifact_hashes_after=before,
                artifacts_read=(str(artifact_path),))

        values: dict[str, float | None] = {}
        problems: list[str] = []
        for spec in bet.evaluation_protocol.metric_specs:
            metric = str(spec.get("metric"))
            extractor = self._extractors.get(metric)
            if extractor is None:
                values[metric] = None
                problems.append(f"no extractor registered for {metric}")
                continue
            try:
                values[metric] = float(extractor(payload))
            except Exception as exc:  # noqa: BLE001 -- scoring failure is data
                values[metric] = None
                problems.append(f"{metric}: {type(exc).__name__}: {exc}")

        after = ArtifactIsolation.snapshot([artifact_path])
        if dict(before) != dict(after):
            reason = "artifact content changed during measurement"
            return MeasurementResult(
                measurements=self._blank_measurements(bet, receipt, "invalid", reason),
                metric_values=values, validity="invalid", missing_reason=reason,
                artifact_hashes_before=before, artifact_hashes_after=after,
                artifacts_read=(str(artifact_path),))
        if problems or any(v is None for v in values.values()):
            reason = "; ".join(problems) or "no metric value produced"
            return MeasurementResult(
                measurements=self._blank_measurements(bet, receipt, "invalid", reason),
                metric_values=values, validity="invalid", missing_reason=reason,
                artifact_hashes_before=before, artifact_hashes_after=after,
                artifacts_read=(str(artifact_path),))

        measurements = tuple(
            OutcomeMeasurement(
                measurement_id=f"meas_{sha256_of({'bet': bet.bet_id, 'metric': metric})[:12]}",
                bet_id=bet.bet_id, receipt_id=receipt.receipt_id, metric=metric,
                value=value, direction=self._directions.get(metric, "increase"),
                unit=self._units.get(metric, ""), measured_by=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                evidence_refs=(str(artifact_path),), missing_reason="", validity="valid",
                agent_artifacts_unchanged=True,
                artifact_hashes_before=before, artifact_hashes_after=after)
            for metric, value in values.items())
        return MeasurementResult(measurements=measurements, metric_values=values, validity="valid",
                                 missing_reason="", artifact_hashes_before=before,
                                 artifact_hashes_after=after, artifacts_read=(str(artifact_path),))

    # -- internals -----------------------------------------------------------

    def _resolve_artifact(self, receipt: ExecutionReceipt) -> str:
        # Screen every artifact the execution offers before choosing one, so a
        # receipt that hands over an agent self-report is refused by name rather
        # than accidentally treated as a missing file.
        for path in receipt.artifacts:
            self._isolation.check_path(path)
        for path in receipt.artifacts:
            if Path(path).name == self.artifact_key or Path(path).stem == Path(self.artifact_key).stem:
                return str(self._isolation.check_path(path))
        raise NotIndependentError(
            f"execution receipt does not name the declared artifact {self.artifact_key!r}; "
            f"got {list(receipt.artifacts)}")

    def _validity_failure(self, bet: BetRecord, receipt: ExecutionReceipt,
                          artifact_path: str) -> tuple[str, str] | None:
        if not receipt.is_valid:
            return "invalid", f"execution status={receipt.status}: {receipt.failure_reason}"
        if not Path(artifact_path).exists():
            return "missing", f"declared artifact is absent: {artifact_path}"
        # ``artifact_hashes`` is keyed by the string form of the path.  Looking it
        # up with a Path object always missed, which silently skipped the
        # tamper check -- a hash guard that never fired.  Normalise on str().
        declared = receipt.artifact_hashes.get(str(artifact_path))
        if declared and file_sha256(Path(artifact_path)) != declared:
            return "invalid", (f"artifact hash does not match the receipt for {artifact_path}; the "
                               "artifact was modified after the execution reported it")
        return None

    def _blank_measurements(self, bet: BetRecord, receipt: ExecutionReceipt,
                            validity: str, reason: str) -> tuple[OutcomeMeasurement, ...]:
        return tuple(
            OutcomeMeasurement(
                measurement_id=f"meas_{sha256_of({'bet': bet.bet_id, 'metric': spec.get('metric')})[:12]}",
                bet_id=bet.bet_id, receipt_id=receipt.receipt_id,
                metric=str(spec.get("metric")), value=None,
                direction=self._directions.get(str(spec.get("metric")), "increase"),
                unit=self._units.get(str(spec.get("metric")), ""), measured_by=self.evaluator_id,
                evaluator_version=self.evaluator_version, evidence_refs=(), missing_reason=reason,
                validity=validity, agent_artifacts_unchanged=True,
                artifact_hashes_before={}, artifact_hashes_after={})
            for spec in bet.evaluation_protocol.metric_specs)


def evaluator_fingerprint(evaluator: JsonMetricEvaluator) -> str:
    """Identity of the measuring instrument, pinned into the comparison proof."""
    return sha256_of({"id": evaluator.evaluator_id, "version": evaluator.evaluator_version,
                      "artifact_key": evaluator.artifact_key,
                      "metrics": sorted(evaluator._extractors)})


__all__ = ["ArtifactIsolation", "JsonMetricEvaluator", "MeasurementResult", "EvaluationError",
           "NotIndependentError", "MetricExtractor", "evaluator_fingerprint",
           "FORBIDDEN_ARTIFACT_MARKERS"]
