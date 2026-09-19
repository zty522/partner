"""The three comparison arms, sharing one interface.

``fixed_pipeline``    a predetermined action order; no adaptation, no verdict
``free_llm_loop``     free next-step choice from the previous result, same hard
                      budget, no frozen expectation and no settlement
``commitment_loop``   the kernel: frozen expectation, independent measurement,
                      machine settlement, and a turn gate

Parity is the point.  All three receive the same :class:`~.fixtures.Episode`,
the same evaluator instance, the same budget and the same candidate space, and
each reports an :class:`~.fixtures.ArmResult` in the same shape.

Scope warning, stated here rather than buried: this is an **execution skeleton**.
One deterministic synthetic episode cannot show that one arm is better, and
nothing in this module supports a research claim.  ``run_benchmark.py`` stamps
that limitation into its output.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from partner.commitment import models as M
from partner.commitment.ports import FrozenClock
from partner.commitment.runner import BetRunner, RunnerConfig
from partner.commitment.freezer import Freezer
from partner.commitment.selector import GuardedGainSelector
from partner.commitment.store import CommitmentStore

from .fixtures import (
    ArmResult, Episode, FixtureExecutor, freeze_quote, shared_protocol,
)
from partner.commitment.evidence import build_baseline_evidence


class _FixtureBaselineProvider:
    """Executes the control action for real and measures it with the shared instrument."""

    def __init__(self, *, executor: FixtureExecutor, evaluator, episode: Episode,
                 control: Mapping[str, Any]) -> None:
        self.executor = executor
        self.evaluator = evaluator
        self.episode = episode
        self.control = control

    def provide(self, *, bet, store):
        import dataclasses
        control = M.Candidate(candidate_id=str(self.control["candidate_id"]),
                              description=str(self.control.get("description") or "control"),
                              params=dict(self.control.get("params") or {}),
                              proposed_by="policy", rationale="declared control arm")
        exec_bet = dataclasses.replace(bet, bet_id=f"{bet.bet_id}__baseline",
                                       context_snapshot_ref=str(store.path("context",
                                                                          "snapshot.json")))
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
            data_hash=receipt.data_hash, treatment=str(control.candidate_id),
            environment=self.episode.environment,
            environment_fingerprint_value=self.episode.environment_fingerprint,
            harness_version=self.episode.harness_version, store=store)


class Arm(Protocol):
    name: str
    requires_llm: bool

    def run(self, episode: Episode) -> ArmResult: ...


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _utility(observed: float | None, *, max_metric: float | None) -> float:
    """Fixture oracle utility: 1.0 when the direction worked and no guardrail broke."""
    if observed is None or max_metric is None:
        return 0.0
    if max_metric > 30.0:
        return 0.0
    return 1.0 if float(observed) >= 10.0 else 0.0


def _oracle_class(observed: float | None, *, max_metric: float | None) -> str:
    if observed is None:
        return "invalid"
    if max_metric is not None and max_metric > 30.0:
        return "falsified"
    return "supported" if float(observed) >= 10.0 else "falsified"


BEST_ALLOWED_UTILITY = 1.0


# ---------------------------------------------------------------------------
# arm 1: fixed pipeline
# ---------------------------------------------------------------------------

@dataclass
class FixedPipelineArm:
    """Run the declared candidate order, one per round, ignoring every result."""

    name: str = "fixed_pipeline"
    requires_llm: bool = False

    def run(self, episode: Episode) -> ArmResult:
        result = ArmResult(self.name, episode.seed, [])
        executor = FixtureExecutor()
        evaluator = _evaluator_for(episode)
        space = list(episode.snapshot["candidate_space"])
        start = time.time()
        for index, entry in enumerate(space[: episode.max_rounds], start=1):
            bet = _synthetic_bet(episode, entry, revision=index)
            receipt = executor.execute(bet=bet, candidate=_candidate(entry), workspace=str(episode.workspace))
            measured = evaluator.measure(bet=bet, receipt=receipt)
            observed = measured.metric_values.get("mean")
            maximum = measured.metric_values.get("max")
            result.tool_calls += 1
            result.decisions.append({
                "round": index, "chosen": entry["candidate_id"], "reason": "predetermined order",
                "observed": observed, "max_observed": maximum,
                "settlement_class": None, "settlement_mechanism": "none",
                "oracle_class": _oracle_class(observed, max_metric=maximum),
                "publish_eligible": None, "guardrail_broken": (maximum or 0) > 30.0,
                "turned": False, "new_evidence_refs": [], "repeat_without_new_evidence": False,
                "consumed_prior_experience": False,
                "regret": round(BEST_ALLOWED_UTILITY - _utility(observed, max_metric=maximum), 6),
                "counter_evidence": entry["candidate_id"] in episode.snapshot["strong_counter_evidence"],
                "kept_despite_counter_evidence": (
                    entry["candidate_id"] in episode.snapshot["strong_counter_evidence"]),
            })
        result.wall_clock_seconds = time.time() - start
        result.notes = ("no frozen expectation", "no independent settlement", "cannot stop early")
        result.settlement_classes = []
        return result


# ---------------------------------------------------------------------------
# arm 2: free LLM loop (no freezing, no settlement)
# ---------------------------------------------------------------------------

@dataclass
class FreeLLMLoopArm:
    """Choose the next step freely from the previous result, under the same budget.

    ``llm_client`` is optional.  When it is absent the arm still runs, but it
    records ``dependency_missing=('llm_chooser',)`` and its choices come from a
    greedy replay rule, which is *not* the arm's defining behaviour.  The result
    says so rather than pretending the LLM arm ran.
    """

    name: str = "free_llm_loop"
    requires_llm: bool = True
    llm_client: Any = None

    def run(self, episode: Episode) -> ArmResult:
        result = ArmResult(self.name, episode.seed, [])
        if self.llm_client is None:
            result.dependency_missing = ("llm_chooser",)
            result.notes = ("free choice replayed by a greedy rule; not the arm's defining behaviour",)
        executor = FixtureExecutor()
        evaluator = _evaluator_for(episode)
        space = {e["candidate_id"]: e for e in episode.snapshot["candidate_space"]}
        order = [e["candidate_id"] for e in episode.snapshot["candidate_space"]]
        last_observed: float | None = None
        chosen_history: list[str] = []
        start = time.time()
        for index in range(1, episode.max_rounds + 1):
            choice = self._choose(space, order, last_observed, chosen_history, episode)
            entry = space[choice]
            bet = _synthetic_bet(episode, entry, revision=index)
            receipt = executor.execute(bet=bet, candidate=_candidate(entry), workspace=str(episode.workspace))
            measured = evaluator.measure(bet=bet, receipt=receipt)
            observed = measured.metric_values.get("mean")
            maximum = measured.metric_values.get("max")
            repeated = choice in chosen_history
            result.tool_calls += 1
            result.model_calls += 1 if self.llm_client is not None else 0
            result.decisions.append({
                "round": index, "chosen": choice,
                "reason": "free choice from the previous observation",
                "observed": observed, "max_observed": maximum,
                "settlement_class": None, "settlement_mechanism": "self_verdict_not_settlement",
                "oracle_class": _oracle_class(observed, max_metric=maximum),
                "publish_eligible": None, "guardrail_broken": (maximum or 0) > 30.0,
                "turned": index > 1 and choice != chosen_history[-1],
                "new_evidence_refs": [f"round:{index - 1}"] if index > 1 else [],
                "repeat_without_new_evidence": repeated,
                "consumed_prior_experience": False,
                "regret": round(BEST_ALLOWED_UTILITY - _utility(observed, max_metric=maximum), 6),
                "counter_evidence": choice in episode.snapshot["strong_counter_evidence"],
                "kept_despite_counter_evidence": False,
            })
            chosen_history.append(choice)
            last_observed = observed
        result.wall_clock_seconds = time.time() - start
        result.notes = result.notes + ("no frozen expectation", "no machine settlement")
        return result

    def _choose(self, space: Mapping[str, Mapping[str, Any]], order: Sequence[str],
                last_observed: float | None, history: Sequence[str],
                episode: Episode) -> str:
        if last_observed is None:
            return order[0]
        # Greedy replay: if the last action cleared the threshold, repeat it;
        # otherwise move on.  This is deliberately not the commitment rule -- there
        # is no expectation to fail and no gate that could stop it.
        if last_observed >= 10.0:
            return history[-1]
        untried = [cid for cid in order if cid not in history]
        return untried[0] if untried else order[-1]


# ---------------------------------------------------------------------------
# arm 3: the commitment loop
# ---------------------------------------------------------------------------

@dataclass
class CommitmentLoopArm:
    """One bet, frozen expectations, machine settlement, bounded stop."""

    name: str = "commitment_loop"
    requires_llm: bool = False

    def run(self, episode: Episode) -> ArmResult:
        result = ArmResult(self.name, episode.seed, [])
        effects, conditions = freeze_quote()
        clock = FrozenClock(1_700_000_000.0)
        budget = M.Budget.create(
            wall_clock_seconds=episode.budget_spec["wall_clock_seconds"],
            model_calls=episode.budget_spec["model_calls"],
            actions=episode.budget_spec["actions"], rounds=episode.budget_spec["rounds"],
            started_epoch=clock.now())
        bet_id = f"bench_bet_s{episode.seed}"
        spec = {
            "bet_id": bet_id, "partner_id": "partner-benchmark",
            "project_id": episode.snapshot["project_id"], "run_id": f"bench_run_s{episode.seed}",
            "question": "Can a declared shift raise the mean to at least 10 without breaking the max guardrail?",
            "baseline_ref": "snapshot:baseline_metrics.mean",
            "expected_effects": [e.to_dict() for e in effects],
            "falsification_conditions": [f.to_dict() for f in conditions],
            "evaluation_protocol": shared_protocol().to_dict(),
            "budget": {"wall_clock_seconds": episode.budget_spec["wall_clock_seconds"],
                       "model_calls": episode.budget_spec["model_calls"],
                       "actions": episode.budget_spec["actions"],
                       "rounds": episode.budget_spec["rounds"]},
            "commitment_policy": {"earliest_turn_round": 2, "max_turns": 1,
                                  "require_new_evidence_to_turn": True},
            "environment": episode.environment,
            "harness_version": episode.harness_version,
            "environment_fingerprint": episode.environment_fingerprint,
            "max_candidates": 3, "code_version": "commitment-kernel-1",
            "data_version": "synthetic-fixture-1", "model_config_ref": "none",
            "context_snapshot_ref": "context/snapshot.json",
            "max_risk": 0.5,
        }
        store = CommitmentStore(episode.workspace, spec["run_id"], bet_id)
        # the episode snapshot decides the declared numbers; the runner reads it on disk
        snapshot_path = store.path("context", "snapshot.json")
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        snapshot_path.write_text(_json.dumps(episode.snapshot, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        evaluator = _evaluator_for(episode)
        control = episode.snapshot["candidate_space"][0]
        runner = BetRunner(
            store=store, config=_config(spec, budget, episode), clock=clock,
            proposer=_DeterministicProposer(), executor=_BenchExecutor(),
            evaluator=evaluator, snapshot_reader=_SnapshotReader(episode.snapshot),
            selector=_Selector(), freezer=Freezer(),
            baseline_provider=_FixtureBaselineProvider(
                executor=_BenchExecutor(), evaluator=evaluator, episode=episode,
                control=control),
            owner_id=f"bench-s{episode.seed}")
        start = time.time()
        run_result = runner.run()
        result.wall_clock_seconds = time.time() - start
        settlement = run_result.settlement
        observed = None if run_result.measurement is None else run_result.measurement.metric_values.get("mean")
        maximum = None if run_result.measurement is None else run_result.measurement.metric_values.get("max")
        result.tool_calls = 1
        result.decisions.append({
            "round": 1, "chosen": None if settlement is None else _selected(store),
            "reason": run_result.reason,
            "observed": observed, "max_observed": maximum,
            "settlement_class": None if settlement is None else settlement.settlement_class,
            "settlement_mechanism": "kernel_machine_settlement",
            "oracle_class": _oracle_class(observed, max_metric=maximum),
            "publish_eligible": None if settlement is None else settlement.publish_eligible,
            "guardrail_broken": (maximum or 0) > 30.0,
            "turned": False, "new_evidence_refs": [],
            "repeat_without_new_evidence": False, "consumed_prior_experience": False,
            "regret": round(BEST_ALLOWED_UTILITY - _utility(observed, max_metric=maximum), 6),
            "counter_evidence": False, "kept_despite_counter_evidence": False,
        })
        if settlement is not None:
            result.settlement_classes = [settlement.settlement_class]
            result.published = 1 if settlement.publish_eligible else 0
        result.notes = (f"terminal={run_result.state}",
                        "stopped by the settlement, not by a round counter")
        result.dependency_missing = ()
        return result


# ---------------------------------------------------------------------------
# small adapters used only by the benchmark
# ---------------------------------------------------------------------------

def _evaluator_for(episode: Episode):
    from .fixtures import evaluator as make_evaluator
    return make_evaluator(allowed_roots=[episode.workspace / "state" / "benchmark_execution"])


class _SnapshotReader:
    def __init__(self, snapshot: Mapping[str, Any]) -> None:
        self._snapshot = snapshot

    def read_frozen_state(self) -> Mapping[str, Any]:
        return dict(self._snapshot)


class _DeterministicProposer:
    def propose(self, *, question, snapshot, max_candidates):
        from partner.commitment.proposer import ProposalResult
        space = list(snapshot.get("candidate_space") or ())
        candidates = tuple(_candidate(entry) for entry in space[: int(max_candidates)])
        return ProposalResult(candidates, "policy", model_calls=0, provider="fixture",
                              model="deterministic", trace_ref="fixture")


class _Selector:
    def select(self, *, candidates, snapshot):
        return GuardedGainSelector(max_risk=0.5).select(candidates=candidates, snapshot=snapshot)


class _BenchExecutor:
    executor_id = "benchmark-fixture-executor"
    executor_version = "1.0.0"

    def __init__(self) -> None:
        self._inner = FixtureExecutor()

    def execute(self, *, bet, candidate, workspace):
        return self._inner.execute(bet=bet, candidate=candidate, workspace=workspace)


def _candidate(entry: Mapping[str, Any]) -> M.Candidate:
    return M.Candidate(candidate_id=str(entry["candidate_id"]),
                       description=str(entry.get("description") or entry["candidate_id"]),
                       params=dict(entry.get("params") or {}), proposed_by="policy",
                       rationale=str(entry.get("rationale") or "declared"))


def _synthetic_bet(episode: Episode, entry: Mapping[str, Any], *, revision: int) -> M.BetRecord:
    """A bet-shaped record so the shared executor signature stays uniform."""
    effects, conditions = freeze_quote()
    clock = FrozenClock(1_700_000_000.0)
    budget = M.Budget.create(wall_clock_seconds=episode.budget_spec["wall_clock_seconds"],
                             model_calls=episode.budget_spec["model_calls"],
                             actions=episode.budget_spec["actions"],
                             rounds=episode.budget_spec["rounds"], started_epoch=clock.now())
    candidates = tuple(_candidate(e) for e in episode.snapshot["candidate_space"])
    selected = str(entry["candidate_id"])
    rejected = tuple(M.RejectedAlternative(c.candidate_id, "not selected") for c in candidates
                     if c.candidate_id != selected)
    return M.BetRecord(
        bet_id=f"bench_s{episode.seed}_r{revision}", partner_id="partner-benchmark",
        project_id=episode.snapshot["project_id"], run_id=f"bench_run_s{episode.seed}",
        question=episode.snapshot["project_id"], context_snapshot_ref="fixture",
        context_snapshot_hash="fixture", candidates=candidates, selected_action=selected,
        rejected_alternatives=rejected, selection_reason="arm choice",
        expected_effects=effects, falsification_conditions=conditions,
        evaluation_protocol=shared_protocol(), baseline_ref="fixture",
        budget=budget, commitment_policy=M.CommitmentPolicy(earliest_turn_round=1, max_turns=1),
        code_version="commitment-kernel-1", data_version="synthetic-fixture-1",
        model_config_ref="none", status="EXECUTING", revision=1)


def _config(spec: Mapping[str, Any], budget: M.Budget, episode: "Episode") -> RunnerConfig:
    effects, conditions = freeze_quote()
    return RunnerConfig(
        partner_id=str(spec["partner_id"]), project_id=str(spec["project_id"]),
        run_id=str(spec["run_id"]), question=str(spec["question"]),
        baseline_ref=str(spec["baseline_ref"]), expected_effects=effects,
        falsification_conditions=conditions, evaluation_protocol=shared_protocol(),
        budget=budget,
        environment=episode.environment, harness_version=episode.harness_version,
        environment_fingerprint=episode.environment_fingerprint,
        treatment_spec=M.TreatmentSpec(baseline_treatment="control",
                                       allows_code_change=False),
        commitment_policy=M.CommitmentPolicy(earliest_turn_round=2, max_turns=1,
                                            require_new_evidence_to_turn=True),
        max_candidates=int(spec["max_candidates"]), code_version=str(spec["code_version"]),
        data_version=str(spec["data_version"]), model_config_ref=str(spec["model_config_ref"]),
        context_snapshot_ref=str(spec["context_snapshot_ref"]), scope="benchmark")


def _selected(store: CommitmentStore) -> str:
    try:
        return store.load_bet().selected_action
    except Exception:
        return ""


ARMS: tuple[type, ...] = (FixedPipelineArm, FreeLLMLoopArm, CommitmentLoopArm)


def build_arm(name: str, **kwargs: Any):
    for cls in ARMS:
        if cls.name == name:
            return cls(**kwargs)
    raise KeyError(f"unknown arm {name!r}; known: {[c.name for c in ARMS]}")


__all__ = ["Arm", "ArmResult", "FixedPipelineArm", "FreeLLMLoopArm", "CommitmentLoopArm",
           "ARMS", "build_arm", "BEST_ALLOWED_UTILITY"]
