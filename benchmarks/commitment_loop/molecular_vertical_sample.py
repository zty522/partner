#!/usr/bin/env python3
"""One bounded molecular longitudinal sample for the commitment kernel.

This is a *real* bet, not a fixture: it reads a fixed public molecule pool,
freezes an expectation against a baseline measured by the same deterministic
instrument, runs one allow-listed chemical transform through RDKit, measures the
artifact independently, and settles the result.

Honesty rules applied here:

* the pool is real public chemistry (common aromatic compounds); no result is
  invented, and the outcome is whatever the deterministic metrics say
* the baseline is measured with the *same* evaluator as the candidate, so the
  comparison proof is genuinely matched
* if the configured LLM cannot propose candidates, the failure is recorded and
  the sample falls back to the deterministic proposer, clearly labelled -- the
  LLM path is never silently impersonated
* the LLM never contributes a score, a settlement or a verdict

Boundaries respected: no Application queue, no worker, no watchdog, no channel,
no production workspace.  Everything is written under ``--workspace``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from partner.application.commitment_adapter import (  # noqa: E402
    AllowListedMoleculeExecutor, EVALUATOR_ID, EVALUATOR_VERSION,
    MolecularIdentityBaselineProvider, molecular_evaluator,
)
from partner.commitment import models as M  # noqa: E402
from partner.commitment.evaluator import ArtifactIsolation  # noqa: E402
from partner.commitment.ports import FrozenClock  # noqa: E402
from partner.commitment.proposer import LLMProposer, ProposalError, resolve_provider_config  # noqa: E402
from partner.commitment.runner import BetRunner, RunnerConfig  # noqa: E402
from partner.commitment.freezer import Freezer  # noqa: E402
from partner.commitment.selector import GuardedGainSelector  # noqa: E402
from partner.commitment.store import CommitmentStore, file_sha256  # noqa: E402

#: Real, well-known aromatic compounds.  Public chemistry, no fabricated data.
MOLECULE_POOL = (
    "c1ccccc1",            # benzene
    "Cc1ccccc1",           # toluene
    "Oc1ccccc1",           # phenol
    "Nc1ccccc1",           # aniline
    "c1ccc2ccccc2c1",      # naphthalene
    "c1ccncc1",            # pyridine
    "OC(=O)c1ccccc1",      # benzoic acid
    "Clc1ccccc1",          # chlorobenzene
)

SAMPLE_ID = "molecular_isolated_slice_v2"
LLM_MAX_CALLS = 1
#: This slice is an *isolated* workspace run: it may observe an improvement and it
#: can never be published.  The environment is declared here, frozen into the bet,
#: and therefore cannot be upgraded once the result is known.
ENVIRONMENT = "isolated_sample"
HARNESS_VERSION = "commitment-kernel-2"
ENVIRONMENT_FINGERPRINT = "envfp:molecular-isolated-v2"
BASELINE_TRANSFORM = "identity"


def _snapshot(*, baseline: dict[str, Any], threshold_delta: float,
              sa_limit: float) -> dict[str, Any]:
    return {
        "sample_id": SAMPLE_ID,
        "project_id": "molecular_generation",
        "molecule_pool": list(MOLECULE_POOL),
        "baseline_metrics": {"qed_mean": baseline["qed_mean"], "sa_mean": baseline["sa_mean"],
                             "valid_fraction": baseline["valid_fraction"]},
        "baseline_evidence": {"artifact": baseline["artifact"], "sha256": baseline["sha256"],
                              "evaluator": {"id": EVALUATOR_ID, "version": EVALUATOR_VERSION},
                              "measured_with_same_instrument": True},
        "constraints": {"sa_mean_max": sa_limit, "qed_gain_min": threshold_delta},
        "candidate_space": [
            {"candidate_id": "cand_identity", "description": "no substitution (control)",
             "params": {"transform": "identity"},
             "prior": {"expected_gain": 0.0, "risk": 0.02},
             "rationale": "control: the pool unchanged, so the measured delta must be ~0"},
            {"candidate_id": "cand_fluorine", "description": "replace one aromatic H with F",
             "params": {"transform": "add_fluorine"},
             "prior": {"expected_gain": 0.04, "risk": 0.25},
             "rationale": "small, low-cost substituent; sometimes nudges drug-likeness up"},
            {"candidate_id": "cand_hydroxyl", "description": "replace one aromatic H with OH",
             "params": {"transform": "add_hydroxyl"},
             "prior": {"expected_gain": 0.06, "risk": 0.45},
             "rationale": "polar group often raises QED, but adds synthetic-accessibility cost"},
        ],
    }


def _measure_baseline(workspace: Path, clock: FrozenClock) -> dict[str, Any]:
    """Measure the identity transform with the same instrument the candidate uses."""
    bet_id = "baseline_identity"
    root = workspace / "state" / "commitment_execution" / bet_id
    root.mkdir(parents=True, exist_ok=True)
    snapshot_path = workspace / "state" / "commitments" / "molecular_run" / bet_id / "context" / "snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps({"molecule_pool": list(MOLECULE_POOL)}, ensure_ascii=False),
                             encoding="utf-8")
    bet = _baseline_bet(bet_id, snapshot_path, clock)
    executor = AllowListedMoleculeExecutor()
    receipt = executor.execute(bet=bet, candidate=bet.candidates[0], workspace=str(workspace))
    evaluator = molecular_evaluator(allowed_roots=[root.resolve()])
    measured = evaluator.measure(bet=bet, receipt=receipt)
    if not measured.is_valid:
        raise RuntimeError(f"baseline could not be measured: {measured.missing_reason}")
    artifact = receipt.artifacts[0]
    return {"qed_mean": measured.metric_values["qed_mean"],
            "sa_mean": measured.metric_values["sa_mean"],
            "valid_fraction": measured.metric_values["valid_fraction"],
            "artifact": str(artifact), "sha256": file_sha256(Path(artifact)),
            "receipt": receipt.to_dict(), "measurement": measured.to_dict()}


def _baseline_bet(bet_id: str, snapshot_path: Path, clock: FrozenClock) -> M.BetRecord:
    candidates = (
        M.Candidate(candidate_id="cand_identity", description="no substitution (control)",
                    params={"transform": "identity"}, proposed_by="policy"),
        M.Candidate(candidate_id="cand_fluorine", description="add F",
                    params={"transform": "add_fluorine"}, proposed_by="policy"),
    )
    return M.BetRecord(
        bet_id=bet_id, partner_id="partner-molecular", project_id="molecular_generation",
        run_id="molecular_run", question="identity transform baseline",
        context_snapshot_ref=str(snapshot_path), context_snapshot_hash="baseline",
        candidates=candidates, selected_action="cand_identity",
        rejected_alternatives=(M.RejectedAlternative("cand_fluorine", "baseline uses identity"),),
        selection_reason="baseline measurement", expected_effects=(
            M.ExpectedEffect(metric="qed_mean", direction="increase", threshold=0.0),),
        falsification_conditions=(
            M.FalsificationCondition(code="baseline_missing", kind="missing_evidence",
                                     description="baseline metric unavailable",
                                     params={"metric": "qed_mean"}),),
        evaluation_protocol=M.EvaluationProtocol(
            evaluator_id=EVALUATOR_ID, evaluator_version=EVALUATOR_VERSION,
            metric_specs=({"metric": "qed_mean"}, {"metric": "sa_mean"}, {"metric": "valid_fraction"})),
        baseline_ref="snapshot:baseline_metrics", budget=M.Budget.create(
            wall_clock_seconds=600, model_calls=0, actions=1, rounds=1, started_epoch=clock.now()),
        commitment_policy=M.CommitmentPolicy(earliest_turn_round=2, max_turns=1),
        code_version="commitment-kernel-1", data_version=SAMPLE_ID,
        model_config_ref="none")


def _build(snapshot: dict[str, Any], *, bet_id: str, run_id: str, workspace: Path,
           clock: FrozenClock, proposer, threshold_delta: float, sa_limit: float,
           allowed_root: Path) -> BetRunner:
    store = CommitmentStore(workspace, run_id, bet_id)
    snapshot_path = store.path("context", "snapshot.json")
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline_qed = float(snapshot["baseline_metrics"]["qed_mean"])
    effects = (M.ExpectedEffect(metric="qed_mean", direction="increase",
                                threshold=round(baseline_qed + threshold_delta, 6),
                                unit="qed", baseline_value=baseline_qed,
                                kind="delta_over_baseline", min_delta=threshold_delta),)
    conditions = (
        M.FalsificationCondition(code="qed_gain_missing", kind="metric_violation",
                                 description=f"mean QED did not rise by {threshold_delta}",
                                 params={"metric": "qed_mean", "delta": threshold_delta}),
        M.FalsificationCondition(code="sa_ceiling", kind="guardrail_violation",
                                 description="mean synthetic accessibility exceeded the ceiling",
                                 params={"metric": "sa_mean", "limit": sa_limit, "direction": "max"}),
        M.FalsificationCondition(code="validity_loss", kind="guardrail_violation",
                                 description="some produced molecule was unparsable",
                                 params={"metric": "valid_fraction", "limit": 1.0, "direction": "min"}),
    )
    config = RunnerConfig(
        partner_id="partner-molecular", project_id="molecular_generation", run_id=run_id,
        question=(f"Can a single declared aromatic substitution raise mean QED of the fixed pool by "
                  f"at least {threshold_delta} while keeping mean SA <= {sa_limit}?"),
        baseline_ref="snapshot:baseline_metrics.qed_mean",
        expected_effects=effects, falsification_conditions=conditions,
        evaluation_protocol=M.EvaluationProtocol(
            evaluator_id=EVALUATOR_ID, evaluator_version=EVALUATOR_VERSION,
            metric_specs=({"metric": "qed_mean"}, {"metric": "sa_mean"}, {"metric": "valid_fraction"})),
        budget=M.Budget.create(wall_clock_seconds=600, model_calls=LLM_MAX_CALLS, actions=2,
                              rounds=2, started_epoch=clock.now()),
        commitment_policy=M.CommitmentPolicy(earliest_turn_round=2, max_turns=1,
                                            require_new_evidence_to_turn=True),
        max_candidates=3, code_version=HARNESS_VERSION, data_version=SAMPLE_ID,
        model_config_ref="workspace:config/agent_api_config.json",
        context_snapshot_ref="context/snapshot.json",
        scope="molecular_generation:qed_mean",
        environment=ENVIRONMENT, harness_version=HARNESS_VERSION,
        environment_fingerprint=ENVIRONMENT_FINGERPRINT,
        treatment_spec=M.TreatmentSpec(baseline_treatment=BASELINE_TRANSFORM,
                                       declared_paths=(), allows_code_change=False))
    molecule_executor = AllowListedMoleculeExecutor()
    evaluator = molecular_evaluator(allowed_roots=[workspace / "state" / "commitment_execution"])
    return BetRunner(
        store=store, config=config, clock=clock, proposer=proposer,
        executor=molecule_executor, evaluator=evaluator,
        snapshot_reader=_Reader(snapshot_path), selector=GuardedGainSelector(max_risk=0.5),
        freezer=Freezer(),
        baseline_provider=MolecularIdentityBaselineProvider(
            executor=molecule_executor, evaluator=evaluator,
            treatment_transform=BASELINE_TRANSFORM, environment=ENVIRONMENT,
            harness_version=HARNESS_VERSION,
            environment_fingerprint_value=ENVIRONMENT_FINGERPRINT),
        owner_id=f"sample-{run_id}")


class _Reader:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read_frozen_state(self):
        return json.loads(self._path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--config-workspace",
                        default=os.environ.get("PARTNER_WORKSPACE", ""),
                        help="workspace holding config/agent_api_config.json (provider routing); "
                             "defaults to $PARTNER_WORKSPACE so no private path is hardcoded. "
                             "When it is unset the LLM proposal path is skipped with a recorded "
                             "reason and the deterministic proposer is used instead.")
    parser.add_argument("--threshold-delta", type=float, default=0.03)
    parser.add_argument("--sa-limit", type=float, default=4.0)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    clock = FrozenClock(1_756_000_000.0)
    report: dict[str, Any] = {"sample_id": SAMPLE_ID, "workspace": str(workspace)}

    baseline = _measure_baseline(workspace, clock)
    report["baseline"] = baseline
    print(f"[baseline] identity transform: qed_mean={baseline['qed_mean']} "
          f"sa_mean={baseline['sa_mean']} valid={baseline['valid_fraction']}")

    snapshot = _snapshot(baseline=baseline, threshold_delta=args.threshold_delta,
                         sa_limit=args.sa_limit)
    allowed_root = workspace / "state" / "commitment_execution"

    llm_report: dict[str, Any] = {"attempted": False}
    result_payload: dict[str, Any] = {}
    proposer_label = "deterministic"
    run_id = "molecular_run_llm"

    if not args.no_llm and not str(args.config_workspace).strip():
        llm_report["attempted"] = False
        llm_report["failed_reason"] = "config_workspace_not_provided"
        llm_report["outcome"] = "llm proposal path skipped; using the deterministic proposer"
    elif not args.no_llm:
        llm_report["attempted"] = True
        try:
            provider = resolve_provider_config(args.config_workspace)
            llm_report["provider"] = provider.provider
            llm_report["model"] = provider.model
            llm_report["base_url"] = provider.base_url
            proposer = LLMProposer(provider, max_calls=LLM_MAX_CALLS)
            runner = _build(snapshot, bet_id="bet_molecular_llm", run_id=run_id,
                            workspace=workspace, clock=clock, proposer=proposer,
                            threshold_delta=args.threshold_delta, sa_limit=args.sa_limit,
                            allowed_root=allowed_root)
            try:
                result = runner.run()
            except Exception as exc:  # noqa: BLE001 -- record the real failure, then fall back
                llm_report["failed_reason"] = f"run_error: {type(exc).__name__}: {exc}"
                result = None
            llm_report["calls_used"] = proposer.calls_used
            llm_report["transcript"] = proposer.transcript
            if result is not None and result.settlement is not None:
                proposer_label = "llm"
                run_id = "molecular_run_llm"
                result_payload = result.to_dict()
            elif result is not None:
                llm_report["failed_reason"] = result.reason
                llm_report["outcome"] = "no settlement produced by the llm-backed attempt"
        except ProposalError as exc:
            llm_report["failed_reason"] = f"provider_config_unavailable: {exc}"
        except Exception as exc:  # noqa: BLE001 -- a provider failure is recorded, not hidden
            llm_report["failed_reason"] = f"{type(exc).__name__}: {exc}"

    if not result_payload:
        from partner.commitment.proposer import DeterministicProposer
        runner = _build(snapshot, bet_id="bet_molecular_det", run_id="molecular_run_det",
                        workspace=workspace, clock=clock, proposer=DeterministicProposer(),
                        threshold_delta=args.threshold_delta, sa_limit=args.sa_limit,
                        allowed_root=allowed_root)
        result = runner.run()
        proposer_label = "deterministic (fallback)"
        run_id = "molecular_run_det"
        result_payload = result.to_dict()

    report["llm"] = llm_report
    report["proposer_used"] = proposer_label
    report["run_id"] = run_id
    report["run"] = result_payload

    store = CommitmentStore(workspace, run_id, result_payload["bet_id"])
    manifest = store.write_manifest(extra={"sample_id": SAMPLE_ID, "proposer": proposer_label,
                                           "environment": ENVIRONMENT})
    report["manifest"] = manifest
    report["artifacts"] = _collect(store)
    (out / "molecular_sample_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "molecular_sample_summary.md").write_text(_summary_md(report), encoding="utf-8")

    settlement = result_payload.get("settlement") or {}
    print(f"[proposer] {proposer_label}")
    print(f"[environment] {settlement.get('environment')} -> publishable="
          f"{settlement.get('publish_eligible')} blockers={settlement.get('publish_blockers')}")
    print(f"[claim] expectations_met={settlement.get('expectations_met')} "
          f"improvement_over_baseline={settlement.get('improvement_over_baseline')} "
          f"baseline_already_satisfied={settlement.get('baseline_already_satisfied')} "
          f"supported_claim={settlement.get('supported_claim')}")

    print(f"[bet] {result_payload['bet_id']} state={result_payload['state']}")
    print(f"[settlement] {settlement.get('settlement_class')} "
          f"improvement={settlement.get('improvement_observed')} "
          f"publish={settlement.get('publish_eligible')}")
    print(f"[out] {out}")
    return 0 if settlement.get("settlement_class") else 1


def _collect(store: CommitmentStore) -> dict[str, Any]:
    return {
        "bet": str(store.path("bet.json")),
        "state": str(store.path("state.json")),
        "events": str(store.events_path),
        "manifest": str(store.path("manifest.json")),
        "receipts": [str(store.artifact_path("receipt", i)) for i in store.list_artifacts("receipt")],
        "measurements": [str(store.artifact_path("measurement", i))
                         for i in store.list_artifacts("measurement")],
        "settlements": [str(store.artifact_path("settlement", i))
                        for i in store.list_artifacts("settlement")],
        "experiences": [str(store.artifact_path("experience", i))
                        for i in store.list_artifacts("experience")],
    }


def _summary_md(report: dict[str, Any]) -> str:
    settlement = report["run"].get("settlement") or {}
    measurement = report["run"].get("measurement") or {}
    outcomes = settlement.get("expectation_outcomes") or []
    lines = [
        f"# Molecular bounded sample — {SAMPLE_ID}", "",
        f"- proposer used: **{report['proposer_used']}**",
        f"- LLM attempted: {report['llm'].get('attempted')}",
        f"- provider/model: {report['llm'].get('provider')} / {report['llm'].get('model')}",
        f"- LLM calls used: {report['llm'].get('calls_used')}",
        f"- bet: `{report['run']['bet_id']}` terminal state **{report['run']['state']}**", "",
        "## Baseline (same instrument as the candidate)", "",
        f"- qed_mean = {report['baseline']['qed_mean']}",
        f"- sa_mean = {report['baseline']['sa_mean']}",
        f"- valid_fraction = {report['baseline']['valid_fraction']}",
        f"- sha256 = `{report['baseline']['sha256']}`", "",
        "## Measurement", "",
        f"- validity: {measurement.get('validity')}",
        f"- values: {json.dumps(measurement.get('metric_values'), ensure_ascii=False)}", "",
        "## Settlement", "",
        f"- class: **{settlement.get('settlement_class')}**",
        f"- improvement_observed: {settlement.get('improvement_observed')}",
        f"- publish_eligible: {settlement.get('publish_eligible')}",
        f"- new_regressions: {settlement.get('new_regressions')}",
        f"- pre_existing_failures: {settlement.get('pre_existing_failures')}",
        f"- publish_blockers: {settlement.get('publish_blockers')}",
        f"- comparison matched: {(settlement.get('comparison') or {}).get('matched')}",
        f"- delta: {(settlement.get('comparison') or {}).get('delta')}",
        f"- environment: {settlement.get('environment')}",
        f"- supported claim: {settlement.get('supported_claim')}",
        f"- baseline provenance: {(settlement.get('comparison') or {}).get('baseline_provenance')}",
        f"- treatment (baseline -> candidate): "
        f"{(settlement.get('comparison') or {}).get('baseline_treatment')} -> "
        f"{(settlement.get('comparison') or {}).get('candidate_treatment')}", "",
        "| metric | threshold | observed | met | note |",
        "|---|---|---|---|---|",
    ]
    for outcome in outcomes:
        lines.append(f"| {outcome['metric']} | {outcome['threshold']} | {outcome['observed']} | "
                     f"{outcome['met']} | {outcome.get('note')} |")
    lines += ["", "## Machine rules", ""]
    lines += [f"- {rule}" for rule in (settlement.get("machine_rules") or [])]
    lines += ["", "## Artifacts", ""]
    for key, value in (report.get("artifacts") or {}).items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
