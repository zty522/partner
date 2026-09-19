"""Real 02-project molecular selection executor, evaluator and baseline evidence.

Domain, taken from the project itself (not invented here):

* the molecular_generation project compares **molecule-selection methods** by running
  a selection over a fixed molecule pool with a ``replicate_seed`` and recording
  ``selected_count`` / ``selected_scaffold_count`` / ``selected_mean_qed`` /
  ``selected_mean_sa`` / ``mean_nearest_neighbor_tanimoto_distance``
  (see ``metrics/method_candidates.jsonl``);
* the methods that already exist in the project ledger are ``scaffold_round_robin``,
  ``pareto_diverse``, ``maxmin_fingerprint``, ``scaffold_cap`` and ``llm_greedy_dsl``;
* the frozen comparison pool is ``datasets/bootstrap/molecular_synth_comparison.csv``
  (real SMILES with group ``rule`` / ``stochastic`` and real QED / SA values).

Everything here is deterministic given (pool, method, seed): no randomness beyond
seeded sampling, no LLM, no fabricated numbers.  Metrics are computed from the
selection artifact with RDKit by an evaluator that never reads agent prose.

Scope limit stated once, here: QED, SA, scaffold counts and fingerprint distances are
**computational proxy metrics**.  They are not activity, not efficacy, not
synthesizability in the wet lab, and not clinical value.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from partner.commitment import models as M
from partner.commitment.evidence import build_baseline_evidence, environment_fingerprint
from partner.commitment.evaluator import ArtifactIsolation, JsonMetricEvaluator
from partner.commitment.store import CommitmentStore, file_sha256

DOMAIN_VERSION = "molecular-selection-canary/1"
EXECUTOR_ID = "molecular-selection-executor"
EXECUTOR_VERSION = "1.0.0"
EVALUATOR_ID = "molecular-selection-evaluator"
EVALUATOR_VERSION = "1.0.0"

#: The frozen baseline method: the project's own ``rule`` selection group.
BASELINE_METHOD = "rule"
SELECT_COUNT = 20
SEED_PROTOCOL = (20261239, 20261307)   # declared before any measurement


class PoolError(RuntimeError):
    """The project pool is unusable."""


def _rdkit():
    from rdkit import Chem
    from rdkit.Chem import AllChem, QED, DataStructs
    from rdkit.Chem.Scaffolds import MurckoScaffold
    return Chem, AllChem, QED, DataStructs, MurckoScaffold


def load_pool(project_dir: str | os.PathLike) -> list[dict[str, Any]]:
    """Read the real molecule pool from the project's frozen comparison CSV."""
    path = Path(project_dir) / "datasets" / "bootstrap" / "molecular_synth_comparison.csv"
    if not path.exists():
        raise PoolError(f"project pool not found: {path}")
    Chem, _AllChem, QED, _DS, MurckoScaffold = _rdkit()
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            smiles = (row.get("canonical_smiles") or "").strip()
            if not smiles:
                continue
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                continue
            rows.append({
                "group": (row.get("group") or "").strip(),
                "smiles": smiles,
                "qed": float(row["qed"]) if row.get("qed") else float(QED.qed(mol)),
                "sa": float(row["sa_score"]) if row.get("sa_score") else None,
                "scaffold": MurckoScaffold.MurckoScaffoldSmiles(mol=mol),
            })
    if not rows:
        raise PoolError(f"project pool is empty: {path}")
    return rows


def pool_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps([(r["group"], r["smiles"], round(float(r["qed"]), 9)) for r in rows],
                         sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pool_signature(rows: Sequence[Mapping[str, Any]], *, path: str | os.PathLike) -> str:
    """A fingerprint over the pool *and* the file it came from."""
    p = Path(path)
    return environment_fingerprint(
        name="molecular-generation-02-pool",
        dependencies={"pool_hash": pool_hash(rows), "file_sha256": file_sha256(p)},
        harness_version=DOMAIN_VERSION)


# ---------------------------------------------------------------------------
# selection methods (deterministic given the seed)
# ---------------------------------------------------------------------------

def _farthest_point_pick(rows: Sequence[Mapping[str, Any]], start: int, count: int) -> list[int]:
    """Greedy max-min Tanimoto distance over Morgan fingerprints."""
    Chem, AllChem, _QED, DataStructs, _MS = _rdkit()
    fps = []
    for row in rows:
        mol = Chem.MolFromSmiles(row["smiles"])
        fps.append(AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048))
    chosen = [start]
    remaining = set(range(len(rows))) - {start}
    while len(chosen) < min(count, len(rows)) and remaining:
        best, best_distance = None, -1.0
        for index in remaining:
            similarity = max(DataStructs.TanimotoSimilarity(fps[index], fps[c]) for c in chosen)
            if 1.0 - similarity > best_distance:
                best, best_distance = index, 1.0 - similarity
        chosen.append(best)
        remaining.discard(best)
    return chosen


def select(rows: Sequence[Mapping[str, Any]], *, method: str, seed: int,
           count: int = SELECT_COUNT) -> list[int]:
    """Return the chosen pool indices.  Raises for an unknown method (no silent fallback)."""
    Chem, _AllChem, _QED, _DS, MurckoScaffold = _rdkit()
    if method in ("rule", "stochastic"):
        pool = [i for i, r in enumerate(rows) if r["group"] == method]
        if not pool:
            raise PoolError(f"pool carries no rows for group {method!r}")
        rng = random.Random(seed)
        picked = pool[:count] if method == "rule" else rng.sample(pool, min(count, len(pool)))
        return picked
    if method == "scaffold_cap":
        cap, counts, picked = 3, {}, []
        for index in sorted(range(len(rows)), key=lambda i: (-float(rows[i]["qed"]), i)):
            scaffold = rows[index]["scaffold"]
            if counts.get(scaffold, 0) >= cap:
                continue
            counts[scaffold] = counts.get(scaffold, 0) + 1
            picked.append(index)
            if len(picked) >= count:
                break
        return picked
    if method == "pareto_diverse":
        ordered = sorted(range(len(rows)), key=lambda i: (-float(rows[i]["qed"]),
                                                          float(rows[i]["sa"] or 0.0), i))
        return ordered[:count]
    if method == "maxmin_fingerprint":
        start = min(range(len(rows)), key=lambda i: (-float(rows[i]["qed"]), i))
        return _farthest_point_pick(rows, start, count)
    if method == "scaffold_round_robin":
        by_scaffold: dict[str, list[int]] = {}
        for index in sorted(range(len(rows)), key=lambda i: (-float(rows[i]["qed"]), i)):
            by_scaffold.setdefault(rows[index]["scaffold"], []).append(index)
        picked: list[int] = []
        round_index = 0
        while len(picked) < count:
            added = False
            for scaffold in sorted(by_scaffold):
                bucket = by_scaffold[scaffold]
                if round_index < len(bucket):
                    picked.append(bucket[round_index])
                    added = True
                    if len(picked) >= count:
                        break
            if not added:
                break
            round_index += 1
        return picked
    raise PoolError(f"unknown selection method {method!r}; refusing to guess")


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------

@dataclass
class MolecularSelectionExecutor:
    """Executes one declared selection method over the real project pool."""

    project_dir: str
    seeds: tuple[int, ...] = SEED_PROTOCOL
    executor_id: str = EXECUTOR_ID
    executor_version: str = EXECUTOR_VERSION

    def execute(self, *, bet: M.BetRecord, candidate: M.Candidate,
                workspace: str) -> M.ExecutionReceipt:
        started = time.time()
        method = str(candidate.params.get("method") or "")
        out_dir = Path(workspace) / "state" / "commitment_execution" / bet.bet_id
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / "selection.json"
        log = out_dir / "execution.log"
        try:
            rows = load_pool(self.project_dir)
            repeats = []
            for seed in self.seeds:
                picked = select(rows, method=method, seed=int(seed))
                repeats.append({
                    "seed": int(seed),
                    "selected": [{"smiles": rows[i]["smiles"], "qed": float(rows[i]["qed"]),
                                  "sa": None if rows[i]["sa"] is None else float(rows[i]["sa"]),
                                  "scaffold": rows[i]["scaffold"]} for i in picked],
                })
            artifact.write_text(json.dumps({
                "domain_version": DOMAIN_VERSION, "method": method,
                "pool_size": len(rows), "pool_hash": pool_hash(rows),
                "select_count": SELECT_COUNT, "seeds": list(self.seeds), "repeats": repeats,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            log.write_text(f"method={method}\nseeds={list(self.seeds)}\npool={len(rows)}\n",
                           encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 -- a real domain failure is reported, not hidden
            log.write_text(f"method={method}\nfailed={type(exc).__name__}: {exc}\n",
                           encoding="utf-8")
            return M.ExecutionReceipt(
                receipt_id=f"rcpt_{bet.bet_id}_failed", bet_id=bet.bet_id,
                requested_action=bet.selected_action, executed_action=bet.selected_action,
                executor_id=self.executor_id, executor_version=self.executor_version,
                started_epoch=started, finished_epoch=time.time(), status="failed", exit_code=2,
                artifacts=(), artifact_hashes={}, log_ref=str(log), data_hash="",
                budget_consumed={"actions": 1}, human_intervention=False,
                idempotency_key=f"molecular-selection:{bet.bet_id}:r{bet.revision}",
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
                             "tool_calls": len(self.seeds)},
            human_intervention=False,
            idempotency_key=f"molecular-selection:{bet.bet_id}:r{bet.revision}")


# ---------------------------------------------------------------------------
# evaluator (deterministic, artifact-only)
# ---------------------------------------------------------------------------

def _selected_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    repeats = list(payload.get("repeats") or ())
    if not repeats:
        raise ValueError("artifact carries no repeats")
    rows: list[dict[str, Any]] = []
    for repeat in repeats:
        rows.extend(repeat.get("selected") or ())
    if not rows:
        raise ValueError("artifact repeats carry no selected molecules")
    return rows


def _validity(payload: Mapping[str, Any]) -> float:
    Chem, _A, _Q, _D, _M = _rdkit()
    rows = _selected_rows(payload)
    ok = sum(1 for row in rows if Chem.MolFromSmiles(str(row["smiles"])) is not None)
    return round(ok / len(rows), 6)


def _uniqueness(payload: Mapping[str, Any]) -> float:
    Chem, _A, _Q, _D, _M = _rdkit()
    rows = _selected_rows(payload)
    canonical = {Chem.MolToSmiles(Chem.MolFromSmiles(str(r["smiles"])))
                 for r in rows if Chem.MolFromSmiles(str(r["smiles"])) is not None}
    return round(len(canonical) / len(rows), 6)


def _qed_mean(payload: Mapping[str, Any]) -> float:
    values = [float(row["qed"]) for row in _selected_rows(payload) if row.get("qed") is not None]
    if not values:
        raise ValueError("no QED values in the selection artifact")
    return round(sum(values) / len(values), 6)


def _sa_mean(payload: Mapping[str, Any]) -> float:
    values = [float(row["sa"]) for row in _selected_rows(payload) if row.get("sa") is not None]
    if len(values) != len(_selected_rows(payload)):
        raise ValueError("synthetic-accessibility missing for some selected molecules")
    return round(sum(values) / len(values), 6)


def _selected_count(payload: Mapping[str, Any]) -> float:
    """Mean selected molecules per repeat: the project declares 20."""
    repeats = list(payload.get("repeats") or ())
    counts = [len(repeat.get("selected") or ()) for repeat in repeats]
    if not counts:
        raise ValueError("no repeats in the selection artifact")
    return round(sum(counts) / len(counts), 6)


def _scaffold_count(payload: Mapping[str, Any]) -> float:
    scaffolds = {str(row["scaffold"]) for row in _selected_rows(payload)}
    return float(len(scaffolds))


def _nn_tanimoto_mean(payload: Mapping[str, Any]) -> float:
    """Mean nearest-neighbour Tanimoto distance: higher means more diverse."""
    Chem, AllChem, _Q, DataStructs, _M = _rdkit()
    repeats = list(payload.get("repeats") or ())
    distances: list[float] = []
    for repeat in repeats:
        rows = list(repeat.get("selected") or ())
        fps = [AllChem.GetMorganFingerprintAsBitVect(Chem.MolFromSmiles(str(r["smiles"])), 2,
                                                     nBits=2048) for r in rows]
        for index, fp in enumerate(fps):
            others = [DataStructs.TanimotoSimilarity(fp, other)
                      for j, other in enumerate(fps) if j != index]
            if others:
                distances.append(1.0 - max(others))
    if not distances:
        raise ValueError("cannot compute nearest-neighbour distance from this artifact")
    return round(sum(distances) / len(distances), 6)


def molecular_evaluator(*, allowed_roots: Sequence[str | os.PathLike] = ()) -> JsonMetricEvaluator:
    return JsonMetricEvaluator(
        evaluator_id=EVALUATOR_ID, evaluator_version=EVALUATOR_VERSION,
        artifact_key="selection.json",
        extractors={"validity": _validity, "uniqueness": _uniqueness, "qed_mean": _qed_mean,
                    "sa_mean": _sa_mean, "scaffold_count": _scaffold_count,
                    "selected_count": _selected_count, "nn_tanimoto_mean": _nn_tanimoto_mean},
        direction_by_metric={"validity": "increase", "uniqueness": "increase",
                             "qed_mean": "increase", "sa_mean": "decrease",
                             "scaffold_count": "increase", "selected_count": "increase",
                             "nn_tanimoto_mean": "increase"},
        unit_by_metric={"validity": "fraction", "uniqueness": "fraction", "qed_mean": "qed",
                        "sa_mean": "sa", "scaffold_count": "count",
                        "selected_count": "count", "nn_tanimoto_mean": "distance"},
        isolation=(ArtifactIsolation(allowed_roots=allowed_roots) if allowed_roots
                   else ArtifactIsolation()))


# ---------------------------------------------------------------------------
# baseline evidence from the project's own frozen baseline method
# ---------------------------------------------------------------------------

@dataclass
class ProjectBaselineProvider:
    """Runs the frozen baseline method for real and measures it with the same instrument."""

    project_dir: str
    executor: MolecularSelectionExecutor
    evaluator: JsonMetricEvaluator
    environment: str
    environment_fingerprint_value: str
    harness_version: str

    def provide(self, *, bet: M.BetRecord, store: CommitmentStore):
        import dataclasses
        control = M.Candidate(candidate_id=f"baseline_{BASELINE_METHOD}",
                              description=f"frozen project baseline method ({BASELINE_METHOD})",
                              params={"method": BASELINE_METHOD}, proposed_by="policy",
                              rationale="the project's own frozen baseline selection group")
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
            data_hash=receipt.data_hash, treatment=BASELINE_METHOD,
            environment=self.environment,
            environment_fingerprint_value=self.environment_fingerprint_value,
            harness_version=self.harness_version, store=store)


__all__ = ["DOMAIN_VERSION", "EXECUTOR_ID", "EVALUATOR_ID", "BASELINE_METHOD", "SELECT_COUNT",
           "SEED_PROTOCOL", "PoolError", "load_pool", "pool_hash", "pool_signature", "select",
           "MolecularSelectionExecutor", "molecular_evaluator", "ProjectBaselineProvider"]
