"""Thin adapter between the commitment kernel and the existing Partner domain.

This module is deliberately thin.  It does not re-implement Application Jobs,
Event Flows, indexing, memory or delivery; it only:

* reads a frozen project snapshot from disk (a point read of a known path)
* executes one bounded, allow-listed molecular action through RDKit and writes a
  real artifact
* measures that artifact deterministically and independently of any agent verdict
* assembles a kernel runner from a declarative bet spec

Nothing here starts a worker, an Application queue, a watchdog, or a channel.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from partner.commitment import models as M
from partner.commitment.evidence import build_baseline_evidence, environment_fingerprint
from partner.commitment.evaluator import ArtifactIsolation, JsonMetricEvaluator
from partner.commitment.freezer import Freezer
from partner.commitment.proposer import (
    DeterministicProposer, LLMProposer, ProposalError, resolve_provider_config,
)
from partner.commitment.ports import FrozenClock, NullExternalLearningRequester, SystemClock, \
    ShadowConsequenceForecaster
from partner.commitment.runner import BetRunner, RunnerConfig
from partner.commitment.selector import GuardedGainSelector
from partner.commitment.store import CommitmentStore, file_sha256

ADAPTER_VERSION = "commitment-adapter/1"
EXECUTOR_ID = "allow-listed-molecule-executor"
EXECUTOR_VERSION = "1.0.0"
EVALUATOR_ID = "deterministic-molecular-evaluator"
EVALUATOR_VERSION = "1.0.0"

#: Only these transforms may run.  An action outside the list is refused, so a
#: proposal can never smuggle arbitrary code into an execution.
ALLOWED_TRANSFORMS = ("identity", "add_methyl", "add_hydroxyl", "add_fluorine")


class SnapshotError(RuntimeError):
    """The frozen project snapshot is unusable."""


class TransformRefused(RuntimeError):
    """The candidate asked for an action outside the allow-list."""


# ---------------------------------------------------------------------------
# RDKit helpers (deterministic)
# ---------------------------------------------------------------------------

def _rdkit():
    from rdkit import Chem
    from rdkit.Chem import QED
    return Chem, QED


def sa_score(smiles: str) -> float | None:
    """Synthetic accessibility score, or None when the contrib module is absent."""
    try:
        from rdkit import Chem, RDConfig
        import sys
        contrib = os.path.join(RDConfig.RDContribDir, "SA_Score")
        if contrib not in sys.path:
            sys.path.append(contrib)
        import sascorer  # type: ignore
    except Exception:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return float(sascorer.calculateScore(mol))


def qed_value(smiles: str) -> float | None:
    Chem, QED = _rdkit()
    mol = Chem.MolFromSmiles(smiles)
    return None if mol is None else float(QED.qed(mol))


def canonical(smiles: str) -> str | None:
    Chem, _ = _rdkit()
    mol = Chem.MolFromSmiles(smiles)
    return None if mol is None else Chem.MolToSmiles(mol, canonical=True)


def _first_substitutable_aromatic_carbon(mol):
    """Lowest-index aromatic carbon carrying an explicit hydrogen.

    Deterministic by construction: index order, no randomness.
    """
    for atom in mol.GetAtoms():
        if not atom.GetIsAromatic() or atom.GetSymbol() != "C":
            continue
        if atom.GetTotalNumHs() >= 1:
            return atom.GetIdx()
    return None


def apply_transform(smiles: str, transform: str) -> tuple[str, bool]:
    """Return ``(smiles, changed)``.  An inapplicable transform is reported, not faked."""
    if transform not in ALLOWED_TRANSFORMS:
        raise TransformRefused(f"transform {transform!r} is not allow-listed")
    if transform == "identity":
        return smiles, False
    Chem, _ = _rdkit()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return smiles, False
    target = _first_substitutable_aromatic_carbon(mol)
    if target is None:
        return smiles, False
    fragment = {"add_methyl": "C", "add_hydroxyl": "O", "add_fluorine": "F"}[transform]
    try:
        editable = Chem.RWMol(mol)
        atom = Chem.Atom(fragment)
        new_index = editable.AddAtom(atom)
        editable.AddBond(int(target), int(new_index), Chem.BondType.SINGLE)
        candidate = editable.GetMol()
        Chem.SanitizeMol(candidate)
        out = Chem.MolToSmiles(candidate, canonical=True)
        return out, out != Chem.MolToSmiles(mol, canonical=True)
    except Exception:
        return smiles, False


# ---------------------------------------------------------------------------
# ports implementation
# ---------------------------------------------------------------------------

class ProjectSnapshotReader:
    """Reads one frozen snapshot file.  Point read, no tree walk."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)

    def read_frozen_state(self) -> Mapping[str, Any]:
        if not self.path.exists():
            raise SnapshotError(f"frozen snapshot not found: {self.path}")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise SnapshotError("frozen snapshot must be a JSON object")
        return payload


class AllowListedMoleculeExecutor:
    """Apply one allow-listed transform to a molecule pool and record a receipt."""

    executor_id = EXECUTOR_ID
    executor_version = EXECUTOR_VERSION

    def __init__(self, *, fail_mode: str = "", human_intervention: bool = False) -> None:
        self.fail_mode = fail_mode
        self.human_intervention = bool(human_intervention)

    def execute(self, *, bet: M.BetRecord, candidate: M.Candidate,
                workspace: str) -> M.ExecutionReceipt:
        started = time.time()
        transform = str(candidate.params.get("transform") or "")
        out_dir = Path(workspace) / "state" / "commitment_execution" / bet.bet_id
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / "result.json"
        log = out_dir / "execution.log"
        started_epoch = started

        if self.fail_mode == "execution_failure":
            log.write_text(f"transform={transform} refused by executor\n", encoding="utf-8")
            return M.ExecutionReceipt(
                receipt_id=f"rcpt_{bet.bet_id}_failed", bet_id=bet.bet_id,
                requested_action=bet.selected_action, executed_action=bet.selected_action,
                executor_id=self.executor_id, executor_version=self.executor_version,
                started_epoch=started_epoch, finished_epoch=time.time(), status="failed",
                exit_code=2, artifacts=(), artifact_hashes={}, log_ref=str(log), data_hash="",
                budget_consumed={"actions": 1}, human_intervention=self.human_intervention,
                idempotency_key=f"exec:{bet.bet_id}:r{bet.revision}",
                failure_reason="executor refused the requested transform")

        if transform not in ALLOWED_TRANSFORMS:
            raise TransformRefused(f"transform {transform!r} is not allow-listed")

        source = Path(str(bet.context_snapshot_ref))
        snapshot_path = source if source.exists() else (
            Path(workspace) / "state" / "commitments" / bet.run_id / bet.bet_id / str(bet.context_snapshot_ref))
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        pool = list(snapshot.get("molecule_pool") or ())
        if not pool:
            raise SnapshotError("snapshot carries no molecule_pool")

        lines: list[str] = [f"transform={transform}", f"pool_size={len(pool)}"]
        produced: list[dict[str, Any]] = []
        changed = 0
        for smiles in pool:
            out, did_change = apply_transform(str(smiles), transform)
            changed += 1 if did_change else 0
            produced.append({"source": str(smiles), "product": out, "changed": did_change})
        lines.append(f"changed={changed}")
        artifact.write_text(json.dumps({
            "transform": transform, "candidate_id": candidate.candidate_id,
            "products": produced,
            "smiles": [row["product"] for row in produced],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        finished = time.time()
        return M.ExecutionReceipt(
            receipt_id=f"rcpt_{bet.bet_id}_r{bet.revision}", bet_id=bet.bet_id,
            requested_action=bet.selected_action, executed_action=bet.selected_action,
            executor_id=self.executor_id, executor_version=self.executor_version,
            started_epoch=started_epoch, finished_epoch=finished, status="completed", exit_code=0,
            artifacts=(str(artifact),), artifact_hashes={str(artifact): file_sha256(artifact)},
            log_ref=str(log), data_hash=file_sha256(artifact),
            budget_consumed={"actions": 1, "wall_clock_seconds": round(finished - started_epoch, 3)},
            human_intervention=self.human_intervention,
            idempotency_key=f"exec:{bet.bet_id}:r{bet.revision}")


def _valid_fraction(payload: Mapping[str, Any]) -> float:
    smiles = list(payload.get("smiles") or ())
    if not smiles:
        raise ValueError("artifact carries no molecules")
    return round(sum(1 for s in smiles if canonical(str(s)) is not None) / len(smiles), 6)


def _qed_mean(payload: Mapping[str, Any]) -> float:
    smiles = list(payload.get("smiles") or ())
    values = [qed_value(str(s)) for s in smiles]
    if not smiles or any(v is None for v in values):
        raise ValueError("artifact contains an unparsable molecule")
    return round(sum(float(v) for v in values) / len(values), 6)


def _sa_mean(payload: Mapping[str, Any]) -> float:
    smiles = list(payload.get("smiles") or ())
    values = [sa_score(str(s)) for s in smiles]
    if not smiles or any(v is None for v in values):
        raise ValueError("synthetic accessibility score unavailable or unparsable")
    return round(sum(float(v) for v in values) / len(values), 6)


def molecular_evaluator(*, allowed_roots: Sequence[str | os.PathLike] = ()) -> JsonMetricEvaluator:
    """The deterministic measuring instrument used for both baseline and candidate."""
    return JsonMetricEvaluator(
        evaluator_id=EVALUATOR_ID, evaluator_version=EVALUATOR_VERSION,
        artifact_key="result.json",
        extractors={"valid_fraction": _valid_fraction, "qed_mean": _qed_mean, "sa_mean": _sa_mean},
        direction_by_metric={"valid_fraction": "increase", "qed_mean": "increase",
                             "sa_mean": "decrease"},
        unit_by_metric={"valid_fraction": "fraction", "qed_mean": "qed", "sa_mean": "sa"},
        isolation=ArtifactIsolation(allowed_roots=allowed_roots) if allowed_roots else ArtifactIsolation(),
    )


class MolecularIdentityBaselineProvider:
    """Produces admissible baseline evidence by *executing* the control arm.

    It reuses the same executor and the same deterministic evaluator as the
    candidate, so the control and the treatment differ in exactly one thing: the
    allow-listed transform.  A declared scalar would not be evidence.
    """

    def __init__(self, *, executor: "AllowListedMoleculeExecutor", evaluator: JsonMetricEvaluator,
                 treatment_transform: str = "identity", environment: str = "isolated_sample",
                 harness_version: str = "", environment_fingerprint_value: str = "") -> None:
        self.executor = executor
        self.evaluator = evaluator
        self.treatment_transform = treatment_transform
        self.environment = environment
        self.harness_version = harness_version
        self.environment_fingerprint = environment_fingerprint_value

    def provide(self, *, bet: M.BetRecord, store: CommitmentStore):
        import dataclasses
        control = M.Candidate(candidate_id="baseline_control",
                              description="control arm: the declared baseline transform",
                              params={"transform": self.treatment_transform},
                              proposed_by="policy",
                              rationale="declared control direction, not a proposal")
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
            data_hash=receipt.data_hash, treatment=self.treatment_transform,
            environment=self.environment,
            environment_fingerprint_value=self.environment_fingerprint,
            harness_version=self.harness_version, store=store)


# ---------------------------------------------------------------------------
# declarative runner assembly
# ---------------------------------------------------------------------------

def _declared(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise an externally declared spec object into the internal contract.

    The spec is a *declaration format* written by a caller; it should not have to
    know the kernel's internal schema version.  The adapter stamps it, and never
    invents any other field.
    """
    body = dict(payload)
    body.setdefault("schema_version", M.SCHEMA_VERSION)
    return body


def runner_config_from_spec(spec: Mapping[str, Any], *, clock) -> RunnerConfig:
    """Build a RunnerConfig from a declarative bet spec.

    Required keys: partner_id, project_id, run_id, question, baseline_ref,
    evaluation_protocol{metric_specs}, expected_effects[], falsification_conditions[],
    budget{...}, commitment_policy{...}, max_candidates, code_version,
    data_version, model_config_ref, context_snapshot_ref.
    """
    required = ("partner_id", "project_id", "run_id", "question", "baseline_ref",
                "evaluation_protocol", "expected_effects", "falsification_conditions",
                "budget", "commitment_policy", "max_candidates", "code_version",
                "data_version", "model_config_ref", "context_snapshot_ref")
    missing = [key for key in required if key not in spec]
    if missing:
        raise SnapshotError(f"bet spec missing required key(s): {missing}")
    budget_spec = dict(spec["budget"])
    budget = M.Budget.create(
        wall_clock_seconds=int(budget_spec.get("wall_clock_seconds") or 600),
        model_calls=int(budget_spec.get("model_calls") or 0),
        actions=int(budget_spec.get("actions") or 1),
        rounds=int(budget_spec.get("rounds") or 1), started_epoch=clock.now())
    return RunnerConfig(
        partner_id=str(spec["partner_id"]), project_id=str(spec["project_id"]),
        run_id=str(spec["run_id"]), question=str(spec["question"]),
        baseline_ref=str(spec["baseline_ref"]),
        expected_effects=tuple(M.ExpectedEffect.from_dict(_declared(e))
                               for e in spec["expected_effects"]),
        falsification_conditions=tuple(M.FalsificationCondition.from_dict(_declared(f))
                                       for f in spec["falsification_conditions"]),
        evaluation_protocol=M.EvaluationProtocol.from_dict(_declared(spec["evaluation_protocol"])),
        budget=budget,
        commitment_policy=M.CommitmentPolicy.from_dict(_declared(spec["commitment_policy"])),
        max_candidates=int(spec["max_candidates"]), code_version=str(spec["code_version"]),
        data_version=str(spec["data_version"]), model_config_ref=str(spec["model_config_ref"]),
        context_snapshot_ref=str(spec["context_snapshot_ref"]),
        scope=str(spec.get("scope") or ""),
        publish_gate_reasons=tuple(spec.get("publish_gate_reasons") or ()),
        environment=str(spec.get("environment") or "isolated_sample"),
        treatment_spec=(M.TreatmentSpec.from_dict(_declared(spec["treatment"]))
                        if spec.get("treatment") else None),
        harness_version=str(spec.get("harness_version") or ""),
        environment_fingerprint=str(spec.get("environment_fingerprint") or ""),
        changed_paths=tuple(spec.get("changed_paths") or ()),
        candidate_code_version=str(spec.get("candidate_code_version") or ""),
    )


def build_runner(workspace: str | os.PathLike, spec: Mapping[str, Any], *,
                 clock=None, use_llm: bool = False, llm_max_calls: int = 2,
                 executor: Any = None, owner_id: str = "") -> BetRunner:
    """Assemble a kernel runner over real on-disk storage.

    ``use_llm`` opts into the real configured provider for candidate proposal;
    the failure path is honest (no candidates + a recorded reason) and the
    measurement stays deterministic either way.
    """
    workspace = Path(workspace)
    clock = clock or SystemClock()
    config = runner_config_from_spec(spec, clock=clock)
    store = CommitmentStore(workspace, config.run_id, str(spec["bet_id"]))

    proposer: Any
    if use_llm:
        try:
            provider = resolve_provider_config(workspace)
            proposer = LLMProposer(provider, max_calls=llm_max_calls)
        except ProposalError:
            proposer = DeterministicProposer()
    else:
        proposer = DeterministicProposer()

    snapshot_path = workspace / "state" / "commitments" / config.run_id / str(spec["bet_id"]) / \
        str(spec["context_snapshot_ref"])
    if not snapshot_path.exists():
        snapshot_path = workspace / str(spec["context_snapshot_ref"])
    # one root covers both arms: <root>/<bet_id>/ (candidate) and
    # <root>/<bet_id>__baseline/ (control)
    allowed_root = workspace / "state" / "commitment_execution"
    molecule_executor = executor or AllowListedMoleculeExecutor(
        fail_mode=str(spec.get("executor_fail_mode") or ""),
        human_intervention=bool(spec.get("human_intervention") or False))
    evaluator = molecular_evaluator(allowed_roots=[allowed_root])
    provider = MolecularIdentityBaselineProvider(
        executor=molecule_executor, evaluator=evaluator,
        treatment_transform=str(spec.get("baseline_transform") or "identity"),
        environment=config.environment, harness_version=config.harness_version,
        environment_fingerprint_value=config.environment_fingerprint)
    return BetRunner(
        store=store, config=config, clock=clock, proposer=proposer,
        executor=molecule_executor, evaluator=evaluator,
        snapshot_reader=ProjectSnapshotReader(snapshot_path),
        selector=GuardedGainSelector(max_risk=float(spec.get("max_risk") or 0.5)),
        freezer=Freezer(), forecaster=ShadowConsequenceForecaster(),
        external_learning=NullExternalLearningRequester(),
        baseline_provider=provider, owner_id=owner_id)


__all__ = [
    "ADAPTER_VERSION", "EXECUTOR_ID", "EXECUTOR_VERSION", "EVALUATOR_ID", "EVALUATOR_VERSION",
    "ALLOWED_TRANSFORMS", "SnapshotError", "TransformRefused", "ProjectSnapshotReader",
    "AllowListedMoleculeExecutor", "MolecularIdentityBaselineProvider", "environment_fingerprint",
    "molecular_evaluator", "apply_transform", "qed_value",
    "sa_score", "canonical", "runner_config_from_spec", "build_runner",
]
