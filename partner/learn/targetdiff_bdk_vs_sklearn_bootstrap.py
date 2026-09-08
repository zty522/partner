"""Bootstrap CI for sklearn vs BDK comparison on TargetDiff.

For each method (sklearn HGB, BDK best from sweep):
  1. Run 5-fold CV — get per-(group, fold) RMSE.
  2. Bootstrap: resample groups (with replacement) 1000 times.
  3. Compute ΔRMSE (BDK - sklearn) distribution.
  4. Report 95% CI and p-value for "BDK is worse than sklearn".

Honesty:
    - If the CI includes 0, we report "no statistical evidence of difference"
      and DO NOT claim BDK is better/worse.
    - If the CI excludes 0, we report the direction and the magnitude
      of the gap, with the bootstrap percentile interval.
    - We never claim causation; this is descriptive.
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
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from partner.learn.bdk_function_pool_fitter import BDKFunctionPoolFitter


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


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


def per_group_errors(agg: list[dict], *, method: str, lr: float = 0.05,
                    weight_decay: float = 0.001, epochs: int = 300,
                    mask: list[bool] | None = None) -> dict[str, float]:
    """Return {group: rmse_for_that_group} for the given method.

    Re-runs the 5-fold CV.  Each held-out group contributes one RMSE.
    """
    if mask is None:
        mask = [True, True, True, True]
    by_group: dict[str, list[float]] = defaultdict(list)
    for held in range(5):
        tr = [r for r in agg if fold_sha256(r["group"]) != held]
        te = [r for r in agg if fold_sha256(r["group"]) == held]
        X_tr = np.array([[r["vina"], r["rmsd"]] for r in tr], dtype=np.float32)
        y_tr = np.array([r["pk"] for r in tr], dtype=np.float32)
        X_te = np.array([[r["vina"], r["rmsd"]] for r in te], dtype=np.float32)
        y_te = np.array([r["pk"] for r in te], dtype=np.float32)
        if len(X_tr) == 0 or len(X_te) == 0:
            continue
        if method == "sklearn_hgb":
            model = HistGradientBoostingRegressor(
                max_iter=160, max_leaf_nodes=31, learning_rate=0.08,
                l2_regularization=1.0, random_state=42,
            ).fit(X_tr, y_tr)
            pred = model.predict(X_te)
        elif method == "bdk":
            fitter = BDKFunctionPoolFitter(
                in_dim=2, lr=lr, weight_decay=weight_decay, epochs=epochs,
                initial_kernel_mask=mask, seed=42 + held,
            )
            fitter.fit(X_tr, y_tr)
            pred = fitter.predict(X_te)
        else:
            raise ValueError(f"unknown method: {method}")
        # Per-group RMSE
        te_arr = np.array(te)
        for r, p in zip(te, pred):
            # r is the row dict; p is the predicted pk
            err = float(r["pk"]) - float(p)
            by_group[r["group"]].append(err)

    # Convert to per-group RMSE
    per_group_rmse = {}
    for g, errs in by_group.items():
        per_group_rmse[g] = float(np.sqrt(np.mean(np.array(errs) ** 2)))
    return per_group_rmse


def bootstrap_delta_ci(sklearn_per_group: dict[str, float],
                        bdk_per_group: dict[str, float],
                        n_boot: int = 1000, ci: float = 0.95,
                        seed: int = 42) -> dict:
    """Bootstrap resample groups; compute ΔRMSE = bdk - sklearn per sample.

    Returns: mean_delta, ci_low, ci_high, p_value (one-sided, P[delta > 0]).
    """
    rng = np.random.default_rng(seed)
    groups = sorted(set(sklearn_per_group.keys()) & set(bdk_per_group.keys()))
    sk_arr = np.array([sklearn_per_group[g] for g in groups])
    bk_arr = np.array([bdk_per_group[g] for g in groups])
    n_groups = len(groups)

    deltas = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n_groups, size=n_groups)
        deltas[i] = bk_arr[idx].mean() - sk_arr[idx].mean()

    alpha = (1.0 - ci) / 2.0
    ci_low = float(np.quantile(deltas, alpha))
    ci_high = float(np.quantile(deltas, 1.0 - alpha))
    p_value_one_sided = float((deltas > 0).mean())
    return {
        "n_groups": n_groups,
        "n_boot": n_boot,
        "delta_mean": float(deltas.mean()),
        "delta_std": float(deltas.std()),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "ci_level": ci,
        "p_value_BDK_worse": p_value_one_sided,
        "excludes_zero": bool(ci_low > 0 or ci_high < 0),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="affinity_info.pkl")
    p.add_argument("--output", required=True, help="output report .json path")
    p.add_argument("--n-boot", type=int, default=1000)
    p.add_argument("--n-seeds", type=int, default=1,
                   help="number of seeds for CV; >1 tightens CI")
    args = p.parse_args()

    pkl = Path(args.input)
    if not pkl.exists():
        print(f"ERROR: {pkl} not found", file=sys.stderr)
        sys.exit(1)

    agg = load_aggregated_ligands(pkl)
    n_groups = len({r["group"] for r in agg})
    print(f"Loaded {len(agg)} ligands across {n_groups} groups")

    # Use the BDK config from the sweep (best: lr=0.05, wd=0.001, epochs=300, all mask)
    print(f"Computing per-group RMSE (n_seeds={args.n_seeds})...")
    if args.n_seeds == 1:
        sk_per_group = per_group_errors(agg, method="sklearn_hgb")
        bk_per_group = per_group_errors(agg, method="bdk", lr=0.05,
                                         weight_decay=0.001, epochs=300)
    else:
        # Multi-seed: per_group_rmse_mean is defined inline here for simplicity.
        from collections import defaultdict
        def per_group_mean(agg, *, method, n_seeds):
            by_group = defaultdict(list)
            for seed_offset in range(n_seeds):
                for held in range(5):
                    tr = [r for r in agg if fold_sha256(r["group"]) != held]
                    te = [r for r in agg if fold_sha256(r["group"]) == held]
                    X_tr = np.array([[r["vina"], r["rmsd"]] for r in tr], dtype=np.float32)
                    y_tr = np.array([r["pk"] for r in tr], dtype=np.float32)
                    X_te = np.array([[r["vina"], r["rmsd"]] for r in te], dtype=np.float32)
                    y_te = np.array([r["pk"] for r in te], dtype=np.float32)
                    if method == "sklearn_hgb":
                        m = HistGradientBoostingRegressor(
                            max_iter=160, max_leaf_nodes=31, learning_rate=0.08,
                            l2_regularization=1.0, random_state=42 + seed_offset,
                        ).fit(X_tr, y_tr)
                        pred = m.predict(X_te)
                    else:
                        f = BDKFunctionPoolFitter(
                            in_dim=2, lr=0.05, weight_decay=0.001, epochs=300,
                            initial_kernel_mask=[True, True, True, True],
                            seed=42 + held + seed_offset * 5,
                        )
                        f.fit(X_tr, y_tr)
                        pred = f.predict(X_te)
                    for r, p in zip(te, pred):
                        by_group[r["group"]].append((float(r["pk"]) - float(p)) ** 2)
            return {g: float(np.sqrt(np.mean(sq))) for g, sq in by_group.items()}
        sk_per_group = per_group_mean(agg, method="sklearn_hgb", n_seeds=args.n_seeds)
        bk_per_group = per_group_mean(agg, method="bdk", n_seeds=args.n_seeds)
    print(f"  sklearn mean RMSE: {np.mean(list(sk_per_group.values())):.4f}")
    print(f"  BDK mean RMSE: {np.mean(list(bk_per_group.values())):.4f}")

    print(f"Running {args.n_boot} bootstrap iterations...")
    boot = bootstrap_delta_ci(sk_per_group, bk_per_group,
                                n_boot=args.n_boot, ci=0.95, seed=42)

    if boot["excludes_zero"]:
        direction = "BDK is statistically WORSE than sklearn" if boot["delta_mean"] > 0 else "BDK is statistically BETTER than sklearn"
        verdict = direction + f" (95% CI excludes 0)"
    else:
        verdict = "No statistical evidence of difference between BDK and sklearn (95% CI includes 0)"

    report = {
        "generated_at": _now(),
        "n_ligands": len(agg),
        "n_groups": n_groups,
        "n_seeds": args.n_seeds,
        "sklearn_hgb_mean_rmse": float(np.mean(list(sk_per_group.values()))),
        "bdk_best_mean_rmse": float(np.mean(list(bk_per_group.values()))),
        "bdk_config": {"lr": 0.05, "weight_decay": 0.001, "epochs": 300, "mask": "all"},
        "bootstrap": boot,
        "verdict": verdict,
        "honesty_notes": [
            "This is a descriptive bootstrap CI, not a hypothesis test with strong guarantees.",
            "If CI includes 0, we report no statistical evidence of difference.",
            "If CI excludes 0, the magnitude of the difference is given with its 95% interval.",
            "Single-seed run; multiple seeds would tighten the interval.",
        ],
    }
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                  encoding="utf-8")

    print(f"\n=== BOOTSTRAP RESULT ===")
    print(f"  sklearn HGB mean RMSE: {report['sklearn_hgb_mean_rmse']:.4f}")
    print(f"  BDK best mean RMSE:    {report['bdk_best_mean_rmse']:.4f}")
    print(f"  delta (BDK - sklearn):  {boot['delta_mean']:+.4f}")
    print(f"  95% CI:                 [{boot['ci_low']:+.4f}, {boot['ci_high']:+.4f}]")
    print(f"  p (BDK worse):         {boot['p_value_BDK_worse']:.3f}")
    print(f"  CI excludes 0:         {boot['excludes_zero']}")
    print(f"  VERDICT: {verdict}")
    print(f"\nReport: {args.output}")


if __name__ == "__main__":
    main()
