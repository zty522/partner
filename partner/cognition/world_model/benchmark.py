"""Frozen three-domain shadow benchmark for hypothesis providers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .engine import WorldModelEngine
from .events import WorldModelEventLedger
from .providers import LibraryHypothesisProvider, TransformerHypothesisProvider


def _domains() -> list[dict[str, Any]]:
    return [
        {"domain_id": "cyclic_sensor", "expected_family": "fourier",
         "function": lambda x: 0.4 + 1.5 * np.sin(2 * np.pi * 2 * x + 0.2)},
        {"domain_id": "physical_decay", "expected_family": "exp_decay",
         "function": lambda x: 0.3 + 1.8 * np.exp(-2.0 * x)},
        {"domain_id": "threshold_response", "expected_family": "hinge",
         "function": lambda x: -0.2 + 0.4 * x + 2.4 * np.maximum(0, x - 0.5)},
    ]


def frozen_dataset(seed: int = 20260830) -> tuple[list[dict[str, Any]], str]:
    rng, rows = np.random.default_rng(seed), []
    for domain in _domains():
        # Freeze both domain endpoints so normalization cannot silently alter
        # Fourier frequency, decay rate or hinge position between arms.
        train_x = np.sort(np.concatenate(([0.0, 1.0], rng.uniform(0.0, 1.0, 15))))
        train_y = domain["function"](train_x) + rng.normal(0, 0.015, len(train_x))
        test_x = np.linspace(0, 1, 121)
        rows.append({"domain_id": domain["domain_id"], "expected_family": domain["expected_family"],
                     "train_x": train_x, "train_y": train_y, "test_x": test_x,
                     "test_y": domain["function"](test_x)})
    serializable = [{key: value.tolist() if isinstance(value, np.ndarray) else value
                     for key, value in row.items()} for row in rows]
    digest = hashlib.sha256(json.dumps(serializable, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    return rows, digest


def run_shadow_benchmark(*, provider_kind: str, output_dir: str | Path,
                         seed: int = 20260830) -> dict[str, Any]:
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
    ledger_path = target / f"{provider_kind}_events.jsonl"
    if ledger_path.exists():
        ledger_path.unlink()
    ledger = WorldModelEventLedger(ledger_path)
    engine = WorldModelEngine(provider=provider, ledger=ledger)
    datasets, dataset_digest = frozen_dataset(seed)
    domains = []
    for row in datasets:
        result = engine.observe(row["train_x"], row["train_y"], domain_id=row["domain_id"])
        prediction = engine.predict(result, row["test_x"])
        rmse = float(np.sqrt(np.mean((prediction - row["test_y"]) ** 2)))
        domains.append({
            "domain_id": row["domain_id"], "expected_family": row["expected_family"],
            "top_hypothesis": result["top_hypothesis"]["hypothesis_id"],
            "top_family": result["top_hypothesis"]["family"],
            "family_correct": result["top_hypothesis"]["family"] == row["expected_family"],
            "holdout_rmse": rmse, "hypotheses_considered": result["hypotheses_considered"],
            "effective_hypothesis_count": result["effective_hypothesis_count"],
            "next_observation": result["next_observation"],
        })
    payload = {
        "schema_version": 1, "provider_kind": provider_kind, "provider_id": provider.provider_id,
        "evaluator_id": "robust_bic_mdl_v2",
        "seed": seed, "dataset_digest": dataset_digest, "training": training, "domains": domains,
        "metrics": {
            "mean_holdout_rmse": float(np.mean([row["holdout_rmse"] for row in domains])),
            "family_accuracy": float(np.mean([row["family_correct"] for row in domains])),
            "mean_hypotheses_considered": float(np.mean([row["hypotheses_considered"] for row in domains])),
        },
        "event_ledger": {"path": str(ledger_path), **ledger.verify()},
        "production_mutation": False,
    }
    output = target / f"{provider_kind}_benchmark.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["output_path"] = str(output)
    return payload


def compare_benchmarks(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if baseline["dataset_digest"] != candidate["dataset_digest"]:
        raise ValueError("baseline/candidate dataset mismatch")
    if baseline.get("evaluator_id") != candidate.get("evaluator_id"):
        raise ValueError("baseline/candidate evaluator mismatch")
    before, after = baseline["metrics"], candidate["metrics"]
    criteria = {
        "matched_dataset": True,
        "matched_evaluator": True,
        "no_material_rmse_regression": after["mean_holdout_rmse"] <= before["mean_holdout_rmse"] + 0.02,
        "family_accuracy_not_worse": after["family_accuracy"] >= before["family_accuracy"],
        "candidate_identifies_at_least_two_domains": after["family_accuracy"] >= (2.0 / 3.0),
        "smaller_working_set": after["mean_hypotheses_considered"] < before["mean_hypotheses_considered"],
        "event_ledgers_valid": bool(baseline["event_ledger"]["ok"] and candidate["event_ledger"]["ok"]),
    }
    hard = all(criteria.values())
    return {"decision": "candidate_wins" if hard else "inconclusive",
            "criteria": criteria, "baseline_metrics": before, "candidate_metrics": after,
            "delta_mean_rmse": after["mean_holdout_rmse"] - before["mean_holdout_rmse"],
            "dataset_digest": baseline["dataset_digest"]}
