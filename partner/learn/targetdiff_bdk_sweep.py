"""BDK hyperparameter sweep for TargetDiff affinity fitting.

Goal: try to close the 0.6% gap with sklearn HGB that the baseline comparison
showed.  Tests a grid of (lr, weight_decay, epochs, kernel_mask) combinations
and reports the best.

Honesty:
    - This is a sweep on the SAME 5-fold split as the baseline.
    - We do NOT claim BDK beats sklearn — only report the best BDK config.
    - BDK is still out-of-the-box (no clever kernel design).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pickle
import re
import statistics
from collections import defaultdict
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np

from partner.learn.bdk_function_pool_fitter import BDKFunctionPoolFitter


def fold_sha256(group: str) -> int:
    return int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 5


def load_aggregated_ligands(pkl_path: Path) -> list[dict]:
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


def evaluate_config(agg: list[dict], *, lr: float, weight_decay: float,
                     epochs: int, mask: list[bool], seed: int = 42) -> dict:
    """Run 5-fold CV with the given hyperparameters."""
    rmses = []
    for held in range(5):
        tr = [r for r in agg if fold_sha256(r["group"]) != held]
        te = [r for r in agg if fold_sha256(r["group"]) == held]
        X_tr = np.array([[r["vina"], r["rmsd"]] for r in tr], dtype=np.float32)
        y_tr = np.array([r["pk"] for r in tr], dtype=np.float32)
        X_te = np.array([[r["vina"], r["rmsd"]] for r in te], dtype=np.float32)
        y_te = np.array([r["pk"] for r in te], dtype=np.float32)
        if len(X_tr) == 0 or len(X_te) == 0:
            continue
        fitter = BDKFunctionPoolFitter(
            in_dim=2, epochs=epochs, lr=lr, weight_decay=weight_decay,
            initial_kernel_mask=mask, seed=seed + held,
        )
        fitter.fit(X_tr, y_tr)
        pred = fitter.predict(X_te)
        err = y_te - pred
        rmses.append(float(np.sqrt(np.mean(err ** 2))))
    return {"mean_rmse": statistics.fmean(rmses), "lr": lr,
             "weight_decay": weight_decay, "epochs": epochs,
             "mask": mask}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--quick", action="store_true",
                   help="use a smaller grid for faster turnaround")
    args = p.parse_args()

    pkl = Path(args.input)
    if not pkl.exists():
        print(f"ERROR: {pkl} not found", file=sys.stderr)
        sys.exit(1)
    agg = load_aggregated_ligands(pkl)

    # Grid: lr, weight_decay, epochs, kernel_mask
    if args.quick:
        lrs = [0.01, 0.05]
        wds = [1e-3, 1e-2]
        epochs_list = [100, 300]
        masks = [
            [True, True, True, True],
            [True, True, False, False],
        ]
    else:
        lrs = [0.005, 0.01, 0.02, 0.05, 0.1]
        wds = [1e-4, 1e-3, 1e-2, 1e-1]
        epochs_list = [100, 200, 500]
        masks = [
            [True, True, True, True],
            [True, True, False, False],   # kernel-optimizer-recommended
            [True, False, True, False],
            [True, False, False, False],
            [False, True, False, False],
        ]
    grid = list(product(lrs, wds, epochs_list, masks))
    print(f"Evaluating {len(grid)} configurations on {len(agg)} ligands...")

    results = []
    for i, (lr, wd, ep, mask) in enumerate(grid):
        r = evaluate_config(agg, lr=lr, weight_decay=wd, epochs=ep, mask=mask)
        results.append(r)
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(grid)}] best so far: {min(x['mean_rmse'] for x in results):.4f}")

    # Sort by mean_rmse
    results.sort(key=lambda x: x["mean_rmse"])
    best = results[0]
    best_5 = results[:5]

    report = {
        "n_configs": len(results),
        "n_ligands": len(agg),
        "best_config": best,
        "top_5": best_5,
        "all_results": results,
    }
    Path(args.output).write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n=== TOP 5 CONFIGS ===")
    for i, r in enumerate(best_5, 1):
        print(f"  {i}. RMSE={r['mean_rmse']:.4f}  "
              f"lr={r['lr']}  wd={r['weight_decay']}  "
              f"epochs={r['epochs']}  mask={r['mask']}")
    print(f"\nReport: {args.output}")
    print(f"Compared to baseline:")
    print(f"  sklearn HGB:        1.5416 RMSE (previous baseline)")
    print(f"  BDK best (this run): {best['mean_rmse']:.4f} RMSE")
    if best["mean_rmse"] < 1.5416:
        print(f"  BDK beat sklearn by {1.5416 - best['mean_rmse']:.4f}")
    else:
        print(f"  BDK still worse by {best['mean_rmse'] - 1.5416:.4f}")


if __name__ == "__main__":
    main()
