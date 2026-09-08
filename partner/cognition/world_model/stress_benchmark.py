"""Matched multi-seed pressure benchmark for hypothesis providers.

This benchmark deliberately includes conditions hidden by the small frozen
demonstration: five-point contexts, substantial noise, one gross outlier and
structural parameters absent from Transformer training exemplars.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .engine import WorldModelEngine
from .events import WorldModelEventLedger
from .providers import LibraryHypothesisProvider, TransformerHypothesisProvider


def stress_dataset(*, seeds: int = 10) -> tuple[list[dict[str, Any]], str]:
    functions = {
        "fourier": lambda x: 0.4 + 1.5 * np.sin(2 * np.pi * 1.5 * x + 0.2),
        "exp_decay": lambda x: 0.3 + 1.8 * np.exp(-3.2 * x),
        "hinge": lambda x: -0.2 + 0.4 * x + 2.4 * np.maximum(0, x - 0.37),
        "polynomial": lambda x: 0.2 - 1.1 * x + 2.2 * x ** 2,
    }
    rows: list[dict[str, Any]] = []
    for seed in range(seeds):
        rng = np.random.default_rng(8100 + seed)
        for expected_family, function in functions.items():
            for condition, count, noise in (("sparse", 5, 0.015),
                                             ("noise", 17, 0.18),
                                             ("outlier", 17, 0.025)):
                train_x = np.sort(np.concatenate(([0.0, 1.0], rng.uniform(0, 1, count - 2))))
                train_y = function(train_x) + rng.normal(0, noise, count)
                if condition == "outlier":
                    index = int(rng.integers(1, count - 1))
                    train_y[index] += float(rng.choice((-1, 1))) * 1.8
                test_x = np.linspace(0, 1, 201)
                rows.append({
                    "case_id": f"s{seed}_{expected_family}_{condition}",
                    "seed": seed, "condition": condition,
                    "expected_family": expected_family,
                    "train_x": train_x, "train_y": train_y,
                    "test_x": test_x, "test_y": function(test_x),
                })
    serializable = [{key: value.tolist() if isinstance(value, np.ndarray) else value
                     for key, value in row.items()} for row in rows]
    digest = hashlib.sha256(json.dumps(serializable, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    return rows, digest


def run_stress_benchmark(*, provider_kind: str, output_dir: str | Path,
                         seeds: int = 10) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    if provider_kind == "library":
        provider = LibraryHypothesisProvider("advanced")
        training = {"kind": "none", "reason": "fixed typed library baseline"}
    elif provider_kind == "transformer":
        provider = TransformerHypothesisProvider(seed=17)
        training = provider.fit_synthetic()
    else:
        raise ValueError("provider_kind must be library or transformer")
    ledger_path = target / f"{provider_kind}_stress_events.jsonl"
    if ledger_path.exists():
        ledger_path.unlink()
    engine = WorldModelEngine(provider=provider, ledger=WorldModelEventLedger(ledger_path))
    datasets, dataset_digest = stress_dataset(seeds=seeds)
    cases = []
    for row in datasets:
        result = engine.observe(row["train_x"], row["train_y"], domain_id=row["case_id"])
        prediction = engine.predict(result, row["test_x"])
        diagnostics = result.get("provider_diagnostics") or {}
        selected = diagnostics.get("selected_families") or []
        cases.append({
            "case_id": row["case_id"], "seed": row["seed"],
            "condition": row["condition"], "expected_family": row["expected_family"],
            "top_family": result["top_hypothesis"]["family"],
            "top_hypothesis": result["top_hypothesis"]["hypothesis_id"],
            "family_correct": result["top_hypothesis"]["family"] == row["expected_family"],
            "expected_family_proposed": (provider_kind == "library" or
                                         row["expected_family"] == "polynomial" or
                                         row["expected_family"] in selected),
            "holdout_rmse": float(np.sqrt(np.mean((prediction - row["test_y"]) ** 2))),
            "hypotheses_considered": result["hypotheses_considered"],
            "evidence_status": result["evidence_status"],
            "provider_diagnostics": diagnostics,
        })
    rmses = np.asarray([row["holdout_rmse"] for row in cases])
    metrics = {
        "case_count": len(cases),
        "mean_holdout_rmse": float(np.mean(rmses)),
        "p90_holdout_rmse": float(np.quantile(rmses, 0.9)),
        "family_accuracy": float(np.mean([row["family_correct"] for row in cases])),
        "expected_family_proposal_recall": float(np.mean(
            [row["expected_family_proposed"] for row in cases])),
        "sparse_insufficient_evidence_rate": float(np.mean(
            [row["evidence_status"] == "insufficient_evidence"
             for row in cases if row["condition"] == "sparse"])),
        "mean_hypotheses_considered": float(np.mean(
            [row["hypotheses_considered"] for row in cases])),
        "by_condition_mean_rmse": {
            condition: float(np.mean([row["holdout_rmse"] for row in cases
                                      if row["condition"] == condition]))
            for condition in ("sparse", "noise", "outlier")
        },
    }
    ledger = WorldModelEventLedger(ledger_path)
    payload = {
        "schema_version": 1, "provider_kind": provider_kind,
        "provider_id": provider.provider_id, "seeds": seeds,
        "evaluator_id": "robust_bic_mdl_v2",
        "dataset_digest": dataset_digest, "training": training,
        "cases": cases, "metrics": metrics,
        "event_ledger": {"path": str(ledger_path), **ledger.verify()},
        "production_mutation": False,
    }
    output = target / f"{provider_kind}_stress_benchmark.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["output_path"] = str(output)
    return payload


def compare_stress_benchmarks(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if baseline["dataset_digest"] != candidate["dataset_digest"]:
        raise ValueError("baseline/candidate stress dataset mismatch")
    if baseline.get("evaluator_id") != candidate.get("evaluator_id"):
        raise ValueError("baseline/candidate stress evaluator mismatch")
    before, after = baseline["metrics"], candidate["metrics"]
    criteria = {
        "matched_dataset": True,
        "matched_evaluator": True,
        "no_material_mean_rmse_regression": (
            after["mean_holdout_rmse"] <= before["mean_holdout_rmse"] + 0.02),
        "no_material_p90_rmse_regression": (
            after["p90_holdout_rmse"] <= before["p90_holdout_rmse"] + 0.05),
        "expected_family_proposal_recall": after["expected_family_proposal_recall"] >= 0.95,
        "sparse_cases_admit_insufficient_evidence": (
            after["sparse_insufficient_evidence_rate"] == 1.0),
        "smaller_working_set": (
            after["mean_hypotheses_considered"] < before["mean_hypotheses_considered"]),
        "event_ledgers_valid": bool(baseline["event_ledger"]["ok"] and
                                    candidate["event_ledger"]["ok"]),
    }
    return {
        "decision": "candidate_wins" if all(criteria.values()) else "inconclusive",
        "criteria": criteria, "baseline_metrics": before, "candidate_metrics": after,
        "delta_mean_rmse": after["mean_holdout_rmse"] - before["mean_holdout_rmse"],
        "dataset_digest": baseline["dataset_digest"],
    }
