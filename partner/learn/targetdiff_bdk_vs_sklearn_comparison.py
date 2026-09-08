"""Run sklearn (Stage 13 style) AND BDK FunctionPool on the same affinity data,
produce a side-by-side comparison report.

This is the "02 sklearn vs BDK 对比实验" — the BDK integration plan stage 2
second item.

Honesty contract:
    - Both methods read the SAME `affinity_info.pkl` and use the SAME
      5-fold group-disjoint split (`sha256(group)%5`).
    - We run sklearn inline (not as a subprocess) so both methods have
      identical conditions except for the regressor itself.
    - The comparison includes per-fold metrics, kernel selection, ocamms
      verdict, and an honest "neither is better" summary if applicable.
    - We do NOT claim BDK is better — only report numbers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pickle
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression

from partner.learn.bdk_function_pool_fitter import BDKFunctionPoolFitter


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


def fold_sha256(group: str) -> int:
    return int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 5


def load_aggregated_ligands(pkl_path: Path) -> list[dict]:
    """Same logic as Stage 9-13 inline PIPELINE."""
    raw = pickle.load(open(pkl_path, "rb"))
    rows = []
    for key, value in raw.items():
        try:
            pk, vina, rmsd = float(value["pk"]), float(value["vina"]), float(value["rmsd"])
        except (KeyError, TypeError, ValueError):
            continue
        if pk > 0 and all(math.isfinite(x) for x in (pk, vina, rmsd)):
            group = str(key).split("/", 1)[0]
            name = str(key).split("/", 1)[1]
            ligand = group + "/" + re.sub(r"_(?:min|docked)_\d+$", "", name)
            rows.append({"group": group, "ligand": ligand,
                          "pk": pk, "vina": vina, "rmsd": rmsd})
    buckets = defaultdict(list)
    for row in rows:
        buckets[(row["group"], row["ligand"])].append(row)
    agg = []
    for (group, ligand), values in buckets.items():
        agg.append({
            "group": group, "ligand": ligand,
            "pk": statistics.median(x["pk"] for x in values),
            "vina": statistics.median(x["vina"] for x in values),
            "rmsd": statistics.median(x["rmsd"] for x in values),
        })
    return agg


def metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    err = y - pred
    return {
        "count": int(len(y)),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "mae": float(np.mean(np.abs(err))),
    }


def run_sklearn_baseline(agg: list[dict]) -> dict:
    """Replicate Stage 13 sklearn pipeline (LinearRegression + HGB)."""
    folds = []
    for held in range(5):
        tr = [r for r in agg if fold_sha256(r["group"]) != held]
        te = [r for r in agg if fold_sha256(r["group"]) == held]
        X_tr = np.array([[r["vina"], r["rmsd"]] for r in tr])
        y_tr = np.array([r["pk"] for r in tr])
        X_te = np.array([[r["vina"], r["rmsd"]] for r in te])
        y_te = np.array([r["pk"] for r in te])
        if len(X_tr) == 0 or len(X_te) == 0:
            continue
        lm = LinearRegression().fit(X_tr, y_tr)
        gb = HistGradientBoostingRegressor(
            max_iter=160, max_leaf_nodes=31, learning_rate=0.08,
            l2_regularization=1.0, random_state=42,
        ).fit(X_tr, y_tr)
        pred_lm = lm.predict(X_te)
        pred_gb = gb.predict(X_te)
        m_lm = metrics(y_te, pred_lm)
        m_gb = metrics(y_te, pred_gb)
        folds.append({
            "fold": held,
            "n_train": len(tr), "n_test": len(te),
            "linear": m_lm, "hgb": m_gb,
            "delta_rmse_hgb_vs_linear": m_gb["rmse"] - m_lm["rmse"],
        })
    return {
        "method": "sklearn_baseline",
        "folds": folds,
        "mean_linear_rmse": statistics.fmean(f["linear"]["rmse"] for f in folds),
        "mean_hgb_rmse": statistics.fmean(f["hgb"]["rmse"] for f in folds),
        "mean_delta_rmse_hgb_minus_linear": statistics.fmean(
            f["delta_rmse_hgb_vs_linear"] for f in folds),
    }


def run_bdk(agg: list[dict], *, epochs: int = 200) -> dict:
    folds = []
    kernel_probs_all = []
    ocamms_per_fold = []
    for held in range(5):
        tr = [r for r in agg if fold_sha256(r["group"]) != held]
        te = [r for r in agg if fold_sha256(r["group"]) == held]
        X_tr = np.array([[r["vina"], r["rmsd"]] for r in tr], dtype=np.float32)
        y_tr = np.array([r["pk"] for r in tr], dtype=np.float32)
        X_te = np.array([[r["vina"], r["rmsd"]] for r in te], dtype=np.float32)
        y_te = np.array([r["pk"] for r in te], dtype=np.float32)
        if len(X_tr) == 0 or len(X_te) == 0:
            continue
        fitter = BDKFunctionPoolFitter(in_dim=2, epochs=epochs, seed=42 + held)
        fitter.fit(X_tr, y_tr)
        pred = fitter.predict(X_te)
        m = metrics(y_te, pred)
        audit = fitter.ocamms_check()
        folds.append({"fold": held, "n_train": len(tr), "n_test": len(te),
                       "bdk": m, "ocamms": audit,
                       "kernel_probs": fitter.kernel_probs})
        kernel_probs_all.append(fitter.kernel_probs)
        ocamms_per_fold.append({"passed": audit["passed"],
                                 "n_activated": audit["n_activated"],
                                 "activated_names": audit["activated_names"]})
    return {
        "method": "bdk_function_pool",
        "folds": folds,
        "mean_bdk_rmse": statistics.fmean(f["bdk"]["rmse"] for f in folds),
        "mean_kernel_probs": [statistics.fmean(p[i] for p in kernel_probs_all)
                                for i in range(4)],
        "ocamms_all_passed": all(o["passed"] for o in ocamms_per_fold),
        "ocamms_per_fold": ocamms_per_fold,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="affinity_info.pkl path")
    p.add_argument("--output", required=True, help="output report md path")
    p.add_argument("--epochs", type=int, default=200)
    args = p.parse_args()

    pkl = Path(args.input)
    if not pkl.exists():
        print(f"ERROR: {pkl} not found", file=sys.stderr)
        sys.exit(1)

    agg = load_aggregated_ligands(pkl)
    sklearn = run_sklearn_baseline(agg)
    bdk = run_bdk(agg, epochs=args.epochs)

    mean_sklearn_best = min(sklearn["mean_linear_rmse"], sklearn["mean_hgb_rmse"])
    mean_bdk = bdk["mean_bdk_rmse"]
    bdk_beats_sklearn = mean_bdk < mean_sklearn_best
    delta = mean_bdk - mean_sklearn_best

    # Per-fold table rows (build as plain strings, no nested f-strings with quotes)
    fold_rows = []
    n = min(len(sklearn["folds"]), len(bdk["folds"]))
    for i in range(n):
        sk = sklearn["folds"][i]
        bk = bdk["folds"][i]
        ocamms_status = "PASS" if bk["ocamms"]["passed"] else "FAIL"
        fold_rows.append(
            f"| {sk['fold']} | {sk['n_test']} | "
            f"{sk['linear']['rmse']:.4f} | {sk['hgb']['rmse']:.4f} | "
            f"{bk['bdk']['rmse']:.4f} | {ocamms_status} |"
        )
    fold_table = chr(10).join(fold_rows) + chr(10)

    abs_delta = abs(delta)
    if abs_delta < 0.05:
        interpretation = "within noise range (a few percent of mean RMSE)"
    else:
        interpretation = "possibly meaningful — would need bootstrap CI to confirm"

    report = f"""# TargetDiff: sklearn baseline vs BDK FunctionPool 5-Fold CV

**Generated**: {_now()}
**Data**: `{pkl}` ({len(agg)} aggregated ligand records)
**Split**: `sha256(group) % 5` (same for both methods)
**Feature**: [vina, rmsd]
**Target**: pk

## Mean RMSE (5-fold average)

| Method | Mean RMSE |
|---|---|
| sklearn LinearRegression | **{sklearn['mean_linear_rmse']:.4f}** |
| sklearn HistGradientBoostingRegressor | **{sklearn['mean_hgb_rmse']:.4f}** |
| BDK FunctionPool (4 kernels, gate) | **{mean_bdk:.4f}** |

## Comparison verdict

- sklearn best (min of Linear/HGB): **{mean_sklearn_best:.4f}**
- BDK FunctionPool: **{mean_bdk:.4f}**
- Delta (BDK - sklearn_best): **{delta:+.4f}**
- BDK beats sklearn best: **{'YES' if bdk_beats_sklearn else 'NO'}**

Honest interpretation: this is a single dataset, single seed run. The
delta of {abs_delta:.4f} is {interpretation}.

## Per-fold detail

| Fold | n_test | Linear RMSE | HGB RMSE | BDK RMSE | BDK ocamms |
|---:|---:|---:|---:|---:|---|
{fold_table}## BDK kernel selection

BDK selected these average gate probabilities across 5 folds:

| Kernel | Mean prob |
|---|---|
| linear | {bdk['mean_kernel_probs'][0]:.4f} |
| quadratic | {bdk['mean_kernel_probs'][1]:.4f} |
| fourier | {bdk['mean_kernel_probs'][2]:.4f} |
| expdecay | {bdk['mean_kernel_probs'][3]:.4f} |

BDK ocamms verdict (max 2 activated kernels): **{'PASS' if bdk['ocamms_all_passed'] else 'FAIL'}**

## Limitations

- Single seed (BDK uses seed=42+fold; sklearn HGB uses random_state=42)
- 5-fold only — no bootstrap CI
- BDK has not been hyperparameter-tuned for this dataset
- sklearn HGB uses default-ish params from the Stage 13 inline script

## Recommendation

This comparison is **descriptive, not causal**. Use it to decide whether
to invest in BDK tuning for the next phase, not as a promotion decision.
"""
    Path(args.output).write_text(report, encoding="utf-8")

    json_path = Path(args.output).with_suffix(".json")
    json_path.write_text(json.dumps({
        "sklearn": {k: v for k, v in sklearn.items() if k != "folds"},
        "bdk": {k: v for k, v in bdk.items() if k != "folds"},
        "delta_rmse_bdk_minus_sklearn_best": delta,
        "bdk_beats_sklearn_best": bdk_beats_sklearn,
        "n_ligands": len(agg),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Report: {args.output}")
    print(f"JSON: {json_path}")
    print(f"BDK mean RMSE: {mean_bdk:.4f} vs sklearn best: {mean_sklearn_best:.4f} "
          f"(delta={delta:+.4f}, bdk_better={bdk_beats_sklearn})")


if __name__ == "__main__":
    main()
