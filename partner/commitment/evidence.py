"""Baseline evidence: what makes a comparison admissible.

Two failure modes this module exists to prevent:

1. **A number is not evidence.**  Reading ``baseline_metrics`` out of a snapshot
   and stamping the candidate's conditions onto it produces a "matched"
   comparison that matches only because it was declared to.  A baseline is
   admissible when a receipt and an independent measurement exist, are hashed,
   and their artifacts can be shown not to have moved.
2. **Silent incompatibility.**  A baseline produced by a different evaluator
   version, on different inputs, under a different protocol or budget口径 is not a
   comparability problem to be smoothed over; it makes the comparison
   inconclusive.

A historical baseline may be reused, but reuse is a *proof*, not a copy: the
caller must supply the compatibility record and the checks must pass.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import (
    BaselineEvidence, BetRecord, ContractError, ExecutionReceipt, OutcomeMeasurement,
    canonical_json, hash_text, sha256_of,
)


class EvidenceError(RuntimeError):
    """The baseline evidence is missing, unreadable or not immutable."""


# ---------------------------------------------------------------------------
# building evidence from a real execution
# ---------------------------------------------------------------------------

def environment_fingerprint(*, name: str, dependencies: Mapping[str, str] | None = None,
                            harness_version: str = "") -> str:
    """A stable hash of the declared execution environment.

    Deliberately a declaration, not a probe: if it were auto-detected from the
    live machine, a re-run on a patched box would silently claim the old
    environment was identical.
    """
    return sha256_of({"environment": name, "harness_version": harness_version,
                      "dependencies": dict(sorted((dependencies or {}).items()))})


def artifact_hashes(paths: Sequence[str | Path]) -> dict[str, str]:
    """sha256 of each artifact, keyed by the path as given."""
    out: dict[str, str] = {}
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise EvidenceError(f"artifact missing, cannot hash it: {path}")
        out[str(raw)] = sha256_of_text(path.read_text(encoding="utf-8", errors="replace"))
    return out


def sha256_of_text(text: str) -> str:
    return hash_text(text)


def build_baseline_evidence(*, baseline_id: str, receipt: ExecutionReceipt,
                            measurement: OutcomeMeasurement, metric_values: Mapping[str, float],
                            bet: BetRecord, input_hash: str, data_hash: str,
                            treatment: str, environment: str,
                            environment_fingerprint_value: str, harness_version: str,
                            store=None, now_iso: str = "",
                            provenance: str = "fresh_execution",
                            compatibility: Mapping[str, Any] | None = None,
                            compatibility_ok: bool = False) -> BaselineEvidence:
    """Assemble evidence for a baseline that was actually executed and measured.

    ``artifact_hashes_after`` is taken from the same files at the end of this
    call, so ``immutable`` asserts that the baseline artifacts were stable across
    the measurement rather than merely that they exist.
    """
    from .settlement import budget_hash, protocol_hash

    if not measurement.is_valid:
        raise EvidenceError(
            f"baseline measurement is not valid ({measurement.validity}: "
            f"{measurement.missing_reason}); a failed baseline is not evidence of anything")
    before = dict(measurement.artifact_hashes_before or artifact_hashes(receipt.artifacts))
    after = dict(measurement.artifact_hashes_after or artifact_hashes(receipt.artifacts))
    evidence = BaselineEvidence(
        baseline_id=baseline_id,
        metric_values={k: float(v) for k, v in dict(metric_values).items()},
        receipt_ref=str(receipt.receipt_id),
        receipt_hash=sha256_of(receipt.to_dict()),
        measurement_ref=str(measurement.measurement_id),
        measurement_hash=sha256_of(measurement.to_dict()),
        artifact_refs=tuple(str(p) for p in receipt.artifacts),
        artifact_hashes=before,
        artifact_hashes_after=after,
        input_hash=input_hash,
        data_hash=data_hash,
        executor_id=receipt.executor_id,
        executor_version=receipt.executor_version,
        evaluator_id=measurement.measured_by,
        evaluator_version=measurement.evaluator_version,
        protocol_hash=protocol_hash(bet),
        budget_hash=budget_hash(bet),
        budget_snapshot={"wall_clock_seconds": bet.budget.wall_clock_seconds,
                         "model_calls": bet.budget.model_calls,
                         "actions": bet.budget.actions, "rounds": bet.budget.rounds},
        environment=environment,
        environment_fingerprint=environment_fingerprint_value,
        harness_version=harness_version,
        treatment=treatment,
        provenance=provenance,
        compatibility=dict(compatibility or {}),
        compatibility_ok=bool(compatibility_ok),
        created_at=now_iso or time.strftime("%Y-%m-%dT%H:%M:%S"))
    if not evidence.immutable:
        raise EvidenceError(
            "baseline artifacts changed between the two hashing passes; the measurement does not "
            "describe a stable artifact")
    return evidence


# ---------------------------------------------------------------------------
# admissibility
# ---------------------------------------------------------------------------

#: The control conditions the baseline must share with the candidate.
CONTROL_CHECKS = ("inputs_identical", "evaluator_identical", "protocol_identical",
                  "budget_comparable", "environment_identical", "harness_version_identical")


def compatibility_report(evidence: BaselineEvidence | None, *, bet: BetRecord,
                         candidate_receipt: ExecutionReceipt | None = None,
                         candidate_evaluator_id: str = "",
                         candidate_evaluator_version: str = "",
                         candidate_input_hash: str = "",
                         candidate_environment: str = "",
                         candidate_environment_fingerprint: str = "",
                         candidate_harness_version: str = "",
                         candidate_protocol_hash: str = "",
                         candidate_budget_hash: str = "") -> dict[str, bool]:
    """Per-control comparison of the baseline against the candidate.

    Returns a dict of check-name -> bool.  A check whose inputs are unavailable is
    ``False``: absence of proof is not evidence of compatibility.
    """
    if evidence is None:
        return {name: False for name in CONTROL_CHECKS}
    from .settlement import budget_hash, protocol_hash
    checks = {
        "inputs_identical": bool(candidate_input_hash) and evidence.input_hash == candidate_input_hash,
        "evaluator_identical": (bool(candidate_evaluator_id)
                                and evidence.evaluator_id == candidate_evaluator_id
                                and evidence.evaluator_version == candidate_evaluator_version),
        "protocol_identical": (bool(candidate_protocol_hash)
                               and evidence.protocol_hash == candidate_protocol_hash
                               and evidence.protocol_hash == protocol_hash(bet)),
        "budget_comparable": (bool(candidate_budget_hash)
                              and evidence.budget_hash == candidate_budget_hash),
        "environment_identical": (bool(candidate_environment)
                                  and evidence.environment == candidate_environment
                                  and evidence.environment_fingerprint
                                  == candidate_environment_fingerprint),
        "harness_version_identical": (bool(candidate_harness_version)
                                      and evidence.harness_version == candidate_harness_version),
    }
    return checks


def admissibility(evidence: BaselineEvidence | None, *, bet: BetRecord,
                  **candidate: Any) -> tuple[bool, list[str], dict[str, bool]]:
    """Is this baseline usable at all?  Returns (ok, reasons, checks)."""
    if evidence is None:
        return False, ["baseline_evidence_missing"], {name: False for name in CONTROL_CHECKS}
    reasons: list[str] = []
    if not evidence.immutable:
        reasons.append("baseline_artifacts_not_immutable")
    if evidence.provenance == "reused_frozen_evidence" and not evidence.compatibility_ok:
        reasons.append("baseline_reuse_without_compatibility_proof")
    if evidence.environment != bet.environment:
        reasons.append(
            f"baseline_environment={evidence.environment!r} != bet_environment={bet.environment!r}")
    checks = compatibility_report(evidence, bet=bet, **candidate)
    failed = [name for name, ok in checks.items() if not ok]
    reasons.extend(f"control_mismatch:{name}" for name in failed)
    return (not reasons), reasons, checks


__all__ = ["EvidenceError", "environment_fingerprint", "artifact_hashes", "sha256_of_text",
           "build_baseline_evidence", "compatibility_report", "admissibility", "CONTROL_CHECKS"]
