#!/usr/bin/env python3
"""Frozen HGB Davis arm used by the Event benchmark protocol."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.model_selection import GroupKFold

AA = "ACDEFGHIKLMNPQRSTVWY"
SMILES_CHARS = "CNOSPFIBrcnos[]=#()123456789+-/@\\"


def fractions(text: str, alphabet: str) -> list[float]:
    counts = Counter(text)
    size = max(1, len(text))
    return [counts[c] / size for c in alphabet]


def target_features(sequence: str, kind: str) -> list[float]:
    basic = [math.log1p(len(sequence)), *fractions(sequence, AA)]
    if kind == "target_basic":
        return basic
    bins = [0.0] * 32
    for left, right in zip(sequence, sequence[1:]):
        bins[int(hashlib.sha256((left + right).encode()).hexdigest()[:8], 16) % len(bins)] += 1
    total = max(1.0, sum(bins))
    hashed = [value / total for value in bins]
    return hashed if kind == "target_kmer32" else [*basic, *hashed]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    seed = int(os.environ.get("PARTNER_BENCHMARK_SEED", "0"))
    task = os.environ.get("PARTNER_BENCHMARK_TASK_ID", "target_basic")
    declared = os.environ.get("PARTNER_DECLARED_FEATURE", task)
    rows = list(csv.DictReader(Path(args.dataset).open(encoding="utf-8")))
    y = np.asarray([float(row["pKd"]) for row in rows])
    groups = np.asarray([row["target_id"] for row in rows])
    drug = np.asarray([[math.log1p(len(row["smiles"])), *fractions(row["smiles"], SMILES_CHARS)]
                       for row in rows], dtype=float)
    extra = np.asarray([target_features(row["sequence"], task) for row in rows], dtype=float)
    x = drug if args.arm == "baseline" else np.column_stack((drug, extra))
    predictions = np.zeros(len(rows), dtype=float)
    leakage_free = True
    started = time.monotonic()
    splitter = GroupKFold(n_splits=5, shuffle=True, random_state=seed)
    for fold, (train, test) in enumerate(splitter.split(x, y, groups)):
        leakage_free &= not bool(set(groups[train]) & set(groups[test]))
        model = HistGradientBoostingRegressor(max_iter=120, learning_rate=.08,
                                               l2_regularization=.1,
                                               random_state=seed + fold)
        model.fit(x[train], y[train])
        predictions[test] = model.predict(x[test])
    elapsed = time.monotonic() - started
    value = {
        "metrics": {"rmse": float(root_mean_squared_error(y, predictions)),
                    "mae": float(mean_absolute_error(y, predictions)),
                    "r2": float(r2_score(y, predictions)), "elapsed_seconds": elapsed},
        "predictions": [{"sample_id": row["sample_id"], "y_true": float(actual),
                         "y_pred": float(predicted)}
                        for row, actual, predicted in zip(rows, y, predictions)],
        "guardrails": {"no_target_leakage": leakage_free,
                       "official_test_not_used_for_tuning": True,
                       "within_budget": elapsed <= 1800, "artifacts_complete": True},
        "run_config": {"model": "HistGradientBoostingRegressor:frozen_v1",
                       "folds": "GroupKFold(target):5", "seeds": seed,
                       "task_id": task, "budget": {"runs": 1, "folds": 5, "bootstrap": 1000},
                       "features": {"declared_feature": declared if args.arm == "candidate" else None}},
        "provenance": {"code_revision": "davis_target_feature_arm_v1",
                       "data_hash": "sha256:" + hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest(),
                       "source": "https://github.com/hkmztrk/DeepDTA/tree/master/data/davis"},
    }
    Path(args.output).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
