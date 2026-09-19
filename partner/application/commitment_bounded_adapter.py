"""A minimal, non-molecular bounded action for the commitment kernel.

The action is deliberately trivial and fully deterministic: it computes text
statistics over the triggering message and writes them as the artifact.  The
candidate differs from the control by exactly one declared transform (case
folding), so the kernel gets a real, matched, falsifiable comparison it can
execute, measure and settle -- with no molecular work, no LLM call, no project
data and no external tool.

Scope note: this exists to exercise the loop (frozen expectation -> bounded action
-> independent measurement -> machine settlement -> experience).  It is not
evidence about anything scientific, and it freezes ``isolated_sample`` so a result
can never be published.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from partner.commitment import models as M
from partner.commitment.evidence import build_baseline_evidence, environment_fingerprint
from partner.commitment.evaluator import ArtifactIsolation, JsonMetricEvaluator
from partner.commitment.freezer import Freezer
from partner.commitment.ports import FrozenClock, SystemClock
from partner.commitment.proposer import DeterministicProposer
from partner.commitment.runner import BetRunner
from partner.commitment.selector import GuardedGainSelector
from partner.commitment.store import CommitmentStore, file_sha256

DOMAIN_VERSION = "bounded-text-stats/1"
EXECUTOR_ID = "bounded-text-stats-executor"
EXECUTOR_VERSION = "1.0.0"
EVALUATOR_ID = "bounded-text-stats-evaluator"
EVALUATOR_VERSION = "1.0.0"
ARTIFACT_KEY = "text_stats.json"

#: The declared control, and the one allowed treatment difference.
CONTROL_TRANSFORM = "identity"
CANDIDATE_TRANSFORM = "lowercase"
TRANSFORMS = (CONTROL_TRANSFORM, CANDIDATE_TRANSFORM)

TRACE_RE = re.compile(r"runtime_trace[A-Za-z0-9_\-]*")


class BoundedActionError(RuntimeError):
    """The action could not be carried out."""


def apply_transform(text: str, transform: str) -> str:
    if transform == CONTROL_TRANSFORM:
        return text
    if transform == CANDIDATE_TRANSFORM:
        return text.lower()
    raise BoundedActionError(f"unknown transform {transform!r}; refusing to guess")


def text_statistics(text: str, *, transform: str) -> dict[str, Any]:
    """Pure, deterministic statistics over the transformed input."""
    transformed = apply_transform(text, transform)
    words = [word for word in re.split(r"\s+", transformed.strip()) if word]
    unique = {word for word in words}
    return {
        "transform": transform,
        "text_sha256": hashlib.sha256(transformed.encode("utf-8")).hexdigest(),
        "text_len": float(len(transformed)),
        "word_count": float(len(words)),
        "unique_words": float(len(unique)),
        "unique_word_ratio": (round(len(unique) / len(words), 6) if words else 0.0),
    }


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------

@dataclass
class BoundedTextExecutor:
    """Runs one declared transform over the bet's frozen message."""

    executor_id: str = EXECUTOR_ID
    executor_version: str = EXECUTOR_VERSION

    def _message(self, *, bet: M.BetRecord, workspace: str) -> str:
        # ``context_snapshot_ref`` may be an absolute path (the baseline arm rewrites
        # its bet_id, so an id-derived path would no longer resolve).  Fall back to the
        # bet's own store location only when no usable ref is present.
        ref = str(bet.context_snapshot_ref or "")
        candidates = []
        if os.path.isabs(ref):
            candidates.append(Path(ref))
        candidates.append(Path(workspace) / "state" / "commitments" / bet.run_id /
                          bet.bet_id / (ref or "context/snapshot.json"))
        candidates.append(Path(workspace) / "state" / "commitments" / bet.run_id /
                          bet.bet_id / "context" / "snapshot.json")
        snapshot = next((path for path in candidates if path.is_file()), None)
        if snapshot is None:
            raise BoundedActionError(
                f"no frozen context snapshot for {bet.bet_id}: tried "
                f"{[str(p) for p in candidates]}")
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        message = str(payload.get("message") or "")
        if not message:
            raise BoundedActionError("the frozen context carries no message to act on")
        return message

    def execute(self, *, bet: M.BetRecord, candidate: M.Candidate,
                workspace: str) -> M.ExecutionReceipt:
        started = time.time()
        transform = str(candidate.params.get("transform") or "")
        out_dir = Path(workspace) / "state" / "commitment_execution" / bet.bet_id
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / ARTIFACT_KEY
        log = out_dir / "execution.log"
        try:
            message = self._message(bet=bet, workspace=workspace)
            stats = text_statistics(message, transform=transform)
            artifact.write_text(json.dumps(
                {"domain_version": DOMAIN_VERSION, "candidate_id": candidate.candidate_id,
                 "stats": stats, "executed_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                ensure_ascii=False, indent=2), encoding="utf-8")
            log.write_text(f"transform={transform}\ntext_len={stats['text_len']}\n",
                           encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 -- a real failure is reported, not hidden
            log.write_text(f"transform={transform}\nfailed={type(exc).__name__}: {exc}\n",
                           encoding="utf-8")
            return M.ExecutionReceipt(
                receipt_id=f"rcpt_{bet.bet_id}_failed", bet_id=bet.bet_id,
                requested_action=bet.selected_action, executed_action=bet.selected_action,
                executor_id=self.executor_id, executor_version=self.executor_version,
                started_epoch=started, finished_epoch=time.time(), status="failed", exit_code=2,
                artifacts=(), artifact_hashes={}, log_ref=str(log), data_hash="",
                budget_consumed={"actions": 1}, human_intervention=False,
                idempotency_key=f"bounded-text:{bet.bet_id}:r{bet.revision}",
                failure_reason=f"{type(exc).__name__}: {exc}")
        finished = time.time()
        return M.ExecutionReceipt(
            receipt_id=f"rcpt_{bet.bet_id}_r{bet.revision}", bet_id=bet.bet_id,
            requested_action=bet.selected_action, executed_action=bet.selected_action,
            executor_id=self.executor_id, executor_version=self.executor_version,
            started_epoch=started, finished_epoch=finished, status="completed", exit_code=0,
            artifacts=(str(artifact),), artifact_hashes={str(artifact): file_sha256(artifact)},
            log_ref=str(log), data_hash=file_sha256(artifact),
            budget_consumed={"actions": 1, "wall_clock_seconds": round(finished - started, 3),
                             "tool_calls": 1},
            human_intervention=False,
            idempotency_key=f"bounded-text:{bet.bet_id}:r{bet.revision}")


# ---------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------

def _stats(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    stats = payload.get("stats")
    if not isinstance(stats, Mapping):
        raise ValueError("artifact carries no statistics")
    return stats


def _metric(name: str):
    def extract(payload: Mapping[str, Any]) -> float:
        value = _stats(payload).get(name)
        if value is None:
            raise ValueError(f"{name} missing from the artifact")
        return float(value)
    extract.__name__ = f"_{name}"
    return extract


def bounded_text_evaluator(*, allowed_roots: Sequence[str | os.PathLike] = ()) -> JsonMetricEvaluator:
    return JsonMetricEvaluator(
        evaluator_id=EVALUATOR_ID, evaluator_version=EVALUATOR_VERSION,
        artifact_key=ARTIFACT_KEY,
        extractors={"text_len": _metric("text_len"), "word_count": _metric("word_count"),
                    "unique_words": _metric("unique_words"),
                    "unique_word_ratio": _metric("unique_word_ratio")},
        direction_by_metric={"text_len": "increase", "word_count": "increase",
                             "unique_words": "increase", "unique_word_ratio": "increase"},
        unit_by_metric={"text_len": "chars", "word_count": "words", "unique_words": "words",
                        "unique_word_ratio": "ratio"},
        isolation=(ArtifactIsolation(allowed_roots=allowed_roots) if allowed_roots
                   else ArtifactIsolation()))


# ---------------------------------------------------------------------------
# baseline evidence: the control transform, really executed and measured
# ---------------------------------------------------------------------------

@dataclass
class TextBaselineProvider:
    executor: BoundedTextExecutor
    evaluator: JsonMetricEvaluator
    environment: str
    environment_fingerprint_value: str
    harness_version: str

    def provide(self, *, bet: M.BetRecord, store: CommitmentStore):
        import dataclasses
        control = M.Candidate(candidate_id=f"baseline_{CONTROL_TRANSFORM}",
                              description=f"declared control transform ({CONTROL_TRANSFORM})",
                              params={"transform": CONTROL_TRANSFORM}, proposed_by="policy",
                              rationale="the frozen control arm of this bet")
        exec_bet = dataclasses.replace(
            bet, bet_id=f"{bet.bet_id}__baseline",
            context_snapshot_ref=str(store.path("context", "snapshot.json")))
        receipt = self.executor.execute(bet=exec_bet, candidate=control,
                                        workspace=str(store.workspace))
        store.save_receipt(receipt)
        measured = self.evaluator.measure(bet=exec_bet, receipt=receipt)
        for measurement in measured.measurements:
            store.save_measurement(measurement)
        primary = measured.primary(bet.expected_effects[0].metric)
        values = {k: float(v) for k, v in measured.metric_values.items() if v is not None}
        return build_baseline_evidence(
            baseline_id=f"base_{bet.bet_id}", receipt=receipt, measurement=primary,
            metric_values=values, bet=bet, input_hash=bet.context_snapshot_hash,
            data_hash=receipt.data_hash, treatment=CONTROL_TRANSFORM,
            environment=self.environment,
            environment_fingerprint_value=self.environment_fingerprint_value,
            harness_version=self.harness_version, store=store)


# ---------------------------------------------------------------------------
# spec + runner assembly
# ---------------------------------------------------------------------------

def bounded_spec(*, job_id: str, trace_token: str, message: str,
                 instance_id: str = "02", project_id: str = "unassigned") -> dict[str, Any]:
    """The declarative spec both commitment Events use.  Same inputs -> same bet."""
    text_len = float(len(message))
    return {
        "partner_id": f"partner-{instance_id or 'unknown'}",
        "project_id": project_id or "unassigned",
        "run_id": f"event_flow_{job_id}",
        "bet_id": f"bet_{job_id}",
        "question": (f"does case folding change the unique-word ratio of the message "
                     f"carrying {trace_token}?"),
        "context_snapshot_ref": "context/snapshot.json",
        "baseline_ref": f"control:{CONTROL_TRANSFORM}",
        "environment": "isolated_sample",
        # the bet and its baseline evidence must declare the same environment
        # fingerprint, or the comparison is inadmissible and settles inconclusive
        "environment_fingerprint": environment_fingerprint(
            name="bounded-text-stats",
            dependencies={"domain": DOMAIN_VERSION, "scope": "event_flow:record_and_execute"},
            harness_version=DOMAIN_VERSION),
        "harness_version": DOMAIN_VERSION,
        "treatment": {"baseline_treatment": CONTROL_TRANSFORM, "declared_paths": [],
                      "allows_code_change": False,
                      "note": "one declared transform is the only difference"},
        "evaluation_protocol": {
            "evaluator_id": EVALUATOR_ID, "evaluator_version": EVALUATOR_VERSION,
            "independence": "artifact_only", "agent_output_visible": False, "replicates": 1,
            "metric_specs": [{"metric": m} for m in
                             ("text_len", "word_count", "unique_words", "unique_word_ratio")],
        },
        "expected_effects": [
            {"metric": "unique_word_ratio", "direction": "increase", "unit": "ratio",
             "kind": "delta_over_baseline", "min_delta": 0.0, "threshold": 0.0},
            {"metric": "text_len", "direction": "increase", "unit": "chars",
             "kind": "guardrail", "threshold": text_len},
        ],
        "falsification_conditions": [
            {"code": "no_change", "kind": "metric_violation",
             "description": "the unique-word ratio did not move at all",
             "params": {"metric": "unique_word_ratio", "delta": 0.0}},
            {"code": "content_lost", "kind": "guardrail_violation",
             "description": "the action changed the amount of content",
             "params": {"metric": "text_len", "limit": text_len, "direction": "min"}},
            {"code": "missing_measurement", "kind": "missing_evidence",
             "description": "a required metric could not be measured from the artifact",
             "params": {"metric": "unique_word_ratio"}},
        ],
        # the proposer is deterministic (0 model calls), but the walk still asks the
        # ledger whether it *may* spend one, so the budget must carry headroom for
        # the check as well as for the two real actions (control + candidate)
        "budget": {"wall_clock_seconds": 120, "model_calls": 1, "actions": 4, "rounds": 1},
        "commitment_policy": {"earliest_turn_round": 2, "max_turns": 1,
                              "require_new_evidence_to_turn": True,
                              "early_stop_conditions": ["settled", "budget_exhausted",
                                                        "blocked"]},
        "max_candidates": 1,
        "code_version": "commitment-kernel",
        "data_version": f"message:{trace_token}",
        "model_config_ref": "none",
        "max_risk": 1.0,
        "scope": "event_flow:record_and_execute",
        "trace_token": trace_token,
    }


class _SnapshotFileReader:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read_frozen_state(self) -> Mapping[str, Any]:
        return json.loads(self._path.read_text(encoding="utf-8"))


def write_bounded_snapshot(*, store: CommitmentStore, spec: Mapping[str, Any],
                           job_id: str, trace_token: str, message: str,
                           instance_id: str = "",
                           prior: Mapping[str, Any] | None = None,
                           prior_audit: Mapping[str, Any] | None = None,
                           abstention: Mapping[str, Any] | None = None) -> Path:
    path = store.path("context", "snapshot.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "02_event_flow", "trace_token": trace_token, "message": message[:8000],
        "job_id": job_id, "instance_id": instance_id, "project_id": spec["project_id"],
        "recorded_by": "commitment.bet_record",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "candidate_space": [{
            "candidate_id": f"cand_{CANDIDATE_TRANSFORM}",
            "description": f"apply the declared {CANDIDATE_TRANSFORM} transform",
            "params": {"transform": CANDIDATE_TRANSFORM},
            "prior": {"expected_gain": 0.0, "risk": 0.0},
            "rationale": "the single declared treatment difference of this bet",
        }],
        # the prior this bet was frozen with, and the before/after of every decision
        # variable it moved (the snapshot digest is frozen into the bet)
        "prior": dict(prior or {}),
        "prior_adjusted_parameter": dict(prior_audit or {}),
        "abstention": dict(abstention or {}),
        "task_class_key": (prior or {}).get("class_key") or "",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_bounded_runner(workspace: str | os.PathLike, spec: Mapping[str, Any], *,
                         snapshot_path: Path, clock=None) -> BetRunner:
    from partner.application.commitment_adapter import runner_config_from_spec
    workspace = Path(workspace)
    clock = clock or SystemClock()
    config = runner_config_from_spec(spec, clock=clock)
    store = CommitmentStore(workspace, config.run_id, str(spec["bet_id"]))
    executor = BoundedTextExecutor()
    evaluator = bounded_text_evaluator(
        allowed_roots=[workspace / "state" / "commitment_execution"])
    provider = TextBaselineProvider(
        executor=executor, evaluator=evaluator, environment=str(spec["environment"]),
        environment_fingerprint_value=str(spec["environment_fingerprint"]),
        harness_version=DOMAIN_VERSION)
    return BetRunner(store=store, config=config, clock=clock, proposer=DeterministicProposer(),
                     executor=executor, evaluator=evaluator,
                     snapshot_reader=_SnapshotFileReader(snapshot_path),
                     selector=GuardedGainSelector(max_risk=float(spec.get("max_risk") or 1.0)),
                     freezer=Freezer(), baseline_provider=provider,
                     owner_id=f"event-flow-{spec['bet_id']}")


__all__ = ["DOMAIN_VERSION", "EXECUTOR_ID", "EVALUATOR_ID", "ARTIFACT_KEY",
           "CONTROL_TRANSFORM", "CANDIDATE_TRANSFORM", "TRANSFORMS", "TRACE_RE",
           "BoundedActionError", "apply_transform", "text_statistics",
           "BoundedTextExecutor", "bounded_text_evaluator", "TextBaselineProvider",
           "bounded_spec", "write_bounded_snapshot", "build_bounded_runner"]
