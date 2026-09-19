"""Shared harness for the commitment-kernel behaviour tests.

The tests are behavioural: they build a real store on disk, run a real bet
through the real state machine, and assert on artifacts and decisions rather than
on source strings.

The domain is deliberately arithmetic and deterministic ("raise the mean of a
number list") so that a failure is always a kernel failure, never a domain
artefact.  The molecular longitudinal sample lives outside this suite.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from partner.commitment import models as M
from partner.commitment import state_machine as sm
from partner.commitment.evaluator import ArtifactIsolation, JsonMetricEvaluator
from partner.commitment.freezer import Freezer
from partner.commitment.ports import FrozenClock, ProposalResult, SystemClock
from partner.commitment.proposer import DeterministicProposer
from partner.commitment.runner import BetRunner, RunnerConfig
from partner.commitment.selector import GuardedGainSelector
from partner.commitment.evidence import build_baseline_evidence, environment_fingerprint
from partner.commitment.store import CommitmentStore


# ---------------------------------------------------------------------------
# domain: raise the mean of a number list, deterministically
# ---------------------------------------------------------------------------

def mean_of(payload: Mapping[str, Any]) -> float:
    values = list(payload.get("values") or ())
    if not values:
        raise ValueError("no values to average")
    return round(sum(float(v) for v in values) / len(values), 6)


def max_of(payload: Mapping[str, Any]) -> float:
    values = list(payload.get("values") or ())
    if not values:
        raise ValueError("no values to max")
    return round(max(float(v) for v in values), 6)


def make_evaluator(**kwargs) -> JsonMetricEvaluator:
    return JsonMetricEvaluator(
        evaluator_id="deterministic-number-evaluator",
        evaluator_version="1.0.0",
        artifact_key="result.json",
        extractors={"mean": mean_of, "max": max_of},
        direction_by_metric={"mean": "increase", "max": "increase"},
        unit_by_metric={"mean": "unit", "max": "unit"},
        isolation=kwargs.pop("isolation", None),
    )


class FakeStateReader:
    """Reads a snapshot the test controls."""

    def __init__(self, snapshot: Mapping[str, Any]) -> None:
        self._snapshot = json.loads(json.dumps(dict(snapshot)))

    def read_frozen_state(self) -> Mapping[str, Any]:
        return json.loads(json.dumps(self._snapshot))


class ScriptedExecutor:
    """Writes a real artifact on disk, so measurement is genuinely end-to-end.

    ``mode`` selects the failure being tested: a successful run, an execution
    failure, a missing artifact, or an artifact mutated after the receipt was
    issued.
    """

    def __init__(self, *, workspace: Path, values: Sequence[float], mode: str = "ok",
                 human_intervention: bool = False) -> None:
        self.workspace = Path(workspace)
        self.values = [float(v) for v in values]
        self.mode = mode
        self.human_intervention = human_intervention
        self.executions = 0

    def execute(self, *, bet: M.BetRecord, candidate: M.Candidate, workspace: str) -> M.ExecutionReceipt:
        self.executions += 1
        out_dir = Path(workspace) / "state" / "execution" / bet.bet_id
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / "result.json"
        now = 1_700_000_000.0 + self.executions
        if self.mode == "fail":
            return M.ExecutionReceipt(
                receipt_id=f"rcpt_fail_{self.executions}", bet_id=bet.bet_id,
                requested_action=bet.selected_action, executed_action=bet.selected_action,
                executor_id="scripted", executor_version="1.0.0",
                started_epoch=now, finished_epoch=now + 0.1, status="failed", exit_code=1,
                artifacts=(), artifact_hashes={}, log_ref="", data_hash="",
                budget_consumed={"actions": 1}, human_intervention=self.human_intervention,
                idempotency_key=f"idem-{bet.bet_id}-{self.executions}",
                failure_reason="scripted_execution_failure")
        artifact.write_text(json.dumps({"values": self.values}), encoding="utf-8")
        from partner.commitment.store import file_sha256
        digest = file_sha256(artifact)
        if self.mode == "missing":
            artifact.unlink()
        receipt_artifacts: tuple[str, ...] = (str(artifact),)
        hashes = {str(artifact): digest}
        if self.mode == "mutated":
            artifact.write_text(json.dumps({"values": [999.0] * len(self.values)}), encoding="utf-8")
        if self.mode == "empty":
            artifact.write_text(json.dumps({"values": []}), encoding="utf-8")
            hashes = {str(artifact): file_sha256(artifact)}
        return M.ExecutionReceipt(
            receipt_id=f"rcpt_ok_{self.executions}", bet_id=bet.bet_id,
            requested_action=bet.selected_action, executed_action=bet.selected_action,
            executor_id="scripted", executor_version="1.0.0",
            started_epoch=now, finished_epoch=now + 0.1, status="completed", exit_code=0,
            artifacts=receipt_artifacts, artifact_hashes=hashes, log_ref="", data_hash="",
            budget_consumed={"actions": 1}, human_intervention=self.human_intervention,
            idempotency_key=f"idem-{bet.bet_id}-{self.executions}")


class ScriptedBaselineProvider:
    """Produces *real* baseline evidence for the tests.

    It executes a control run through a real executor, writes a real artifact,
    measures it with the same evaluator the candidate will use, and returns an
    admissible :class:`BaselineEvidence`.  Tests that need to see the
    "no admissible baseline" path pass ``include=False`` (or a broken executor).
    """

    def __init__(self, *, workspace: Path, baseline_values: Sequence[float],
                 evaluator: JsonMetricEvaluator, harness_version: str = "commitment-kernel-test",
                 environment: str = "synthetic_fixture", fingerprint: str = "envfp:test",
                 include: bool = True, executor: Any = None) -> None:
        self.workspace = Path(workspace)
        self.evaluator = evaluator
        self.harness_version = harness_version
        self.environment = environment
        self.fingerprint = fingerprint
        self.include = include
        self.executor = executor or ScriptedExecutor(workspace=workspace,
                                                     values=list(baseline_values))

    def provide(self, *, bet, store):
        if not self.include:
            return None
        control = bet.candidates[0]
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
            data_hash=receipt.data_hash, treatment=str(control.params.get("transform")
                                                      or control.candidate_id),
            environment=self.environment, environment_fingerprint_value=self.fingerprint,
            harness_version=self.harness_version, store=store)


# ---------------------------------------------------------------------------
# snapshots and configs
# ---------------------------------------------------------------------------

def default_snapshot(*, baseline_mean: float = 10.0) -> dict[str, Any]:
    return {
        "project_id": "deterministic_numbers",
        "constraints": {"max_values": 8},
        "baseline_metrics": {"mean": baseline_mean, "max": 20.0},
        "candidate_space": [
            {"candidate_id": "cand_small_gain", "description": "small deterministic gain",
             "params": {"shift": 1.0}, "prior": {"expected_gain": 0.05, "risk": 0.1},
             "rationale": "cheap, low variance"},
            {"candidate_id": "cand_large_gain", "description": "large deterministic gain",
             "params": {"shift": 5.0}, "prior": {"expected_gain": 0.40, "risk": 0.2},
             "rationale": "bigger declared gain, still within risk guardrail"},
            {"candidate_id": "cand_risky", "description": "high risk direction",
             "params": {"shift": 50.0}, "prior": {"expected_gain": 0.90, "risk": 0.9},
             "rationale": "highest gain but violates the guardrail"},
        ],
    }


def make_config(*, question: str = "Can a declared shift raise the mean by >= 2.0?",
                threshold: float = 12.0, rounds: int = 1, model_calls: int = 2,
                actions: int = 2, wall_clock_seconds: int = 3600,
                extra_publish_reasons: Sequence[str] = (),
                started_epoch: float = 1_700_000_000.0,
                environment: str = "synthetic_fixture", replicates: int = 1,
                kind: str = "delta_over_baseline", min_delta: float | None = 0.0,
                tolerance: float | None = None,
                treatment_spec: M.TreatmentSpec | None = None,
                harness_version: str = "commitment-kernel-test",
                environment_fingerprint: str = "envfp:test") -> RunnerConfig:
    """The default config is a *synthetic fixture*: it can never publish."""
    return RunnerConfig(
        partner_id="partner-test", project_id="deterministic_numbers", run_id="run_test",
        question=question, baseline_ref="snapshot:baseline_metrics.mean",
        environment=environment,
        treatment_spec=treatment_spec if treatment_spec is not None else M.TreatmentSpec(
            baseline_treatment="control", allows_code_change=False),
        harness_version=harness_version, environment_fingerprint=environment_fingerprint,
        expected_effects=(M.ExpectedEffect(metric="mean", direction="increase",
                                           threshold=threshold, unit="unit",
                                           kind=kind, min_delta=min_delta,
                                           tolerance=tolerance),),
        falsification_conditions=(
            M.FalsificationCondition(code="no_gain", kind="metric_violation",
                                     description="mean did not reach the frozen threshold",
                                     params={"metric": "mean", "threshold": threshold}),),
        evaluation_protocol=M.EvaluationProtocol(
            evaluator_id="deterministic-number-evaluator", evaluator_version="1.0.0",
            metric_specs=({"metric": "mean"}, {"metric": "max"}), replicates=replicates),
        budget=M.Budget.create(wall_clock_seconds=wall_clock_seconds, model_calls=model_calls,
                              actions=actions, rounds=rounds, started_epoch=started_epoch),
        commitment_policy=M.CommitmentPolicy(earliest_turn_round=1, max_turns=1,
                                            require_new_evidence_to_turn=True),
        max_candidates=3, code_version="commitment-kernel-test",
        data_version="fixture-1", model_config_ref="none",
        publish_gate_reasons=tuple(extra_publish_reasons))


def publishable_config(**kwargs) -> RunnerConfig:
    """A config that is *allowed* to publish: canary environment, replicated.

    Used by the tests that assert what the other gates do once the environment
    gate is out of the way.
    """
    kwargs.setdefault("environment", "production_canary")
    kwargs.setdefault("replicates", 2)
    return make_config(**kwargs)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(1_700_000_000.0)


def build_bet(workspace: Path, *, bet_id: str = "bet_test_1", snapshot=None,
              config: RunnerConfig | None = None, executor=None, clock=None,
              proposer=None, owner_id: str = "",
              baseline_provider=None) -> tuple[BetRunner, CommitmentStore, ScriptedExecutor]:
    """Assemble a fully wired runner over real on-disk storage."""
    snapshot = snapshot if snapshot is not None else default_snapshot()
    config = config or make_config()
    clock = clock or FrozenClock(1_700_000_000.0)
    store = CommitmentStore(workspace, config.run_id, bet_id)
    executor = executor or ScriptedExecutor(workspace=workspace, values=[12.0, 13.0, 14.0])
    evaluator = make_evaluator()
    # the control artifact must actually average to the declared baseline mean
    baseline_mean = float(snapshot["baseline_metrics"]["mean"])
    baseline_values = [baseline_mean, baseline_mean]
    provider = baseline_provider if baseline_provider is not None else ScriptedBaselineProvider(
        workspace=workspace, baseline_values=baseline_values, evaluator=evaluator,
        harness_version=config.harness_version, environment=config.environment,
        fingerprint=config.environment_fingerprint)
    runner = BetRunner(
        store=store, config=config, clock=clock,
        proposer=proposer or DeterministicProposer(),
        executor=executor, evaluator=evaluator,
        snapshot_reader=FakeStateReader(snapshot),
        selector=GuardedGainSelector(max_risk=0.5), freezer=Freezer(),
        baseline_provider=provider, owner_id=owner_id)
    return runner, store, executor

class ChargingProposer:
    """Deterministic proposer that reports a real model call, for budget tests."""

    def __init__(self, *, model_calls: int = 1) -> None:
        self._inner = DeterministicProposer()
        self._model_calls = int(model_calls)
        self.calls = 0

    def propose(self, *, question, snapshot, max_candidates):
        self.calls += 1
        base = self._inner.propose(question=question, snapshot=snapshot,
                                   max_candidates=max_candidates)
        return ProposalResult(base.candidates, base.proposed_by, model_calls=self._model_calls,
                              provider="fixture", model="deterministic",
                              usage={"prompt_tokens": 0, "completion_tokens": 0},
                              latency_ms=0.0, trace_ref=base.trace_ref, failure=base.failure)

