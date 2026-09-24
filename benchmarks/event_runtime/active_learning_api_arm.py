#!/usr/bin/env python3
"""Matched downstream consumer for a source-bound sklearn learning handoff."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import time
from pathlib import Path

from sklearn.metrics import mean_squared_error, root_mean_squared_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    handoff_path = Path(args.dataset)
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    binding = ((handoff.get("candidate") or {}).get("parameters") or {}).get("source_binding") or {}
    source = Path(str(binding.get("local_path") or ""))
    source_bound = source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == binding.get("sha256")
    cases = [([0.0, 1.0], [0.0, 2.0]), ([1.0, 2.0, 3.0], [1.0, 2.5, 2.5])]
    predictions = []
    errors = []
    started = time.monotonic()
    for index, (truth, predicted) in enumerate(cases):
        oracle = math.sqrt(sum((a-b) ** 2 for a, b in zip(truth, predicted)) / len(truth))
        try:
            if args.arm == "baseline":
                observed = mean_squared_error(truth, predicted, squared=False)
            else:
                observed = root_mean_squared_error(truth, predicted)
            failed = abs(float(observed) - oracle) >= 1e-12
        except Exception as exc:
            failed = True
            errors.append(f"{type(exc).__name__}: {exc}")
        predictions.append({"sample_id": str(index), "y_true": 0.0,
                            "y_pred": 1.0 if failed else 0.0})
    elapsed = time.monotonic() - started
    rmse = math.sqrt(sum(row["y_pred"] ** 2 for row in predictions) / len(predictions))
    value = {
        "metrics": {"rmse": rmse, "failure_rate": sum(r["y_pred"] for r in predictions)/len(predictions)},
        "predictions": predictions,
        "guardrails": {"handoff_source_bound": source_bound, "same_inputs": True,
                       "numeric_oracle_independent": True, "within_budget": elapsed < 120,
                       "artifacts_complete": True},
        "run_config": {"model": "sklearn_metric_api", "folds": "not_applicable",
                       "seeds": "fixed", "budget": {"runs": 1},
                       "features": {"declared_feature": "root_mean_squared_error"
                                    if args.arm == "candidate" else None}},
        "provenance": {"code_revision": "active_learning_api_arm_v1",
                       "data_hash": "sha256:" + hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
                       "handoff_status": handoff.get("status")},
        "errors": errors,
    }
    Path(args.output).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
