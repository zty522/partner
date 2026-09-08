"""Demonstrate BDK FunctionPool on a non-molecular dataset: literature sources.

04 (literature_github_learning) maintains `external_catalog_snapshot.json` with
each paper having:
  - size_bytes: file size in bytes (continuous target, 100s of KB to MBs)
  - n_use_for_tags: number of "use_for" tags (discrete-ish, 1-5)
  - adoption: "design_reference" | "active" | ... (categorical, but only
              two values appear in this dataset)

This script is a **demonstration** that BDK FunctionPool is not bound to
TargetDiff and can fit any 2-feature regression task.  It is NOT a production
pipeline — 04 doesn't actually use these predictions.

Honesty:
    - This is a script you run manually; it does NOT modify partner runtime.
    - It writes its output under /tmp/ (not in the real workspace).
    - BDK's kernel mask is recommended by the optimizer (ADR 0027) but
      sweep confirms all-4-mask is best on TargetDiff (ADR 0028) — for
      literature we report both.
    - The reported R^2 is a **demonstration of capability**, not evidence
      that 04 should adopt BDK in production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from partner.learn.bdk_function_pool_fitter import BDKFunctionPoolFitter
from partner.learn.bdk_kernel_mask_optimizer import recommend_kernel_mask


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


def load_literature_catalog(snapshot_path: Path) -> list[dict]:
    """Read external_catalog_snapshot.json and return per-source records."""
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    sources = data.get("sources") or []
    out = []
    for s in sources:
        size = int(s.get("size_bytes") or 0)
        if size <= 0:
            continue
        out.append({
            "source_id": str(s.get("source_id") or ""),
            "size_bytes": float(size),
            "n_use_for_tags": float(len(s.get("use_for") or [])),
            "adoption": str(s.get("adoption") or "unknown"),
            "log_size": float(np.log10(size)),
        })
    return out


def _5fold(group_key: str) -> int:
    return int(hashlib.sha256(group_key.encode()).hexdigest()[:8], 16) % 5


def run_bdk_on_literature(catalog: list[dict], *, mask: list[bool],
                          lr: float = 0.05, weight_decay: float = 1e-3,
                          epochs: int = 200) -> dict:
    """Fit BDK FunctionPool on (n_use_for_tags, log_size) → log_size_actual.

    The regression target IS log_size_actual (we predict it from itself
    plus tags) — this is a self-prediction sanity check, not a useful
    predictor.  We do it to verify the FunctionPool converges on a
    non-molecular dataset.
    """
    if len(catalog) < 5:
        return {"ok": False, "reason": f"only {len(catalog)} sources, need >=5"}
    X = np.array([[r["n_use_for_tags"], r["log_size"]] for r in catalog],
                  dtype=np.float32)
    # Predict log_size from n_use_for_tags + log_size (a "memorize" task)
    y = np.array([r["log_size"] for r in catalog], dtype=np.float32)
    keys = [r["source_id"] or str(i) for i, r in enumerate(catalog)]

    # 5-fold by source_id hash
    fold_idx = [_5fold(k) for k in keys]
    rmses = []
    for held in range(5):
        tr = [i for i, f in enumerate(fold_idx) if f != held]
        te = [i for i, f in enumerate(fold_idx) if f == held]
        if not tr or not te:
            continue
        fitter = BDKFunctionPoolFitter(
            in_dim=2, lr=lr, weight_decay=weight_decay, epochs=epochs,
            initial_kernel_mask=mask, seed=42 + held,
        )
        fitter.fit(X[tr], y[tr])
        pred = fitter.predict(X[te])
        err = y[te] - pred
        rmses.append(float(np.sqrt(np.mean(err ** 2))))
    return {
        "ok": True,
        "n_sources": len(catalog),
        "n_folds": len(rmses),
        "mean_rmse_log10": float(np.mean(rmses)),
        "std_rmse_log10": float(np.std(rmses)),
        "mask": mask, "lr": lr, "epochs": epochs,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", required=True,
                   help="path to external_catalog_snapshot.json")
    p.add_argument("--output", default="/tmp/literature_bdk_demo.json",
                   help="output JSON report path")
    args = p.parse_args()

    snap = Path(args.snapshot)
    if not snap.exists():
        print(f"ERROR: {snap} not found", file=sys.stderr)
        sys.exit(1)

    catalog = load_literature_catalog(snap)
    print(f"Loaded {len(catalog)} literature sources from {snap}")

    if len(catalog) < 5:
        print(f"Need at least 5 sources; got {len(catalog)}", file=sys.stderr)
        sys.exit(2)

    # Get kernel mask recommendation
    X = np.array([[r["n_use_for_tags"], r["log_size"]] for r in catalog],
                  dtype=np.float32)
    y = np.array([r["log_size"] for r in catalog], dtype=np.float32)
    rec = recommend_kernel_mask(X, y, top_k=2)
    print(f"Kernel mask recommendation: {rec['mask']}")
    # kernel_r2 in the optimizer API is "sum of per-feature R^2 per kernel"
    # (a ranking score, not a single R^2 value).  Show both for clarity.
    print(f"  Kernel score (sum of per-feature R^2): {rec['kernel_r2']}")
    for kernel, vals in rec["per_feature_r2"].items():
        per_feat_str = ", ".join(f"{v:.3f}" for v in vals)
        print(f"    {kernel}: [{per_feat_str}]")

    # Compare masks
    results = []
    for name, mask in [
        ("all-4-mask", [True, True, True, True]),
        ("recommended", rec["mask"]),
    ]:
        r = run_bdk_on_literature(catalog, mask=mask)
        r["strategy"] = name
        if r.get("ok"):
            print(f"  {name:<20s} mean_rmse_log10 = {r['mean_rmse_log10']:.4f}")
        else:
            print(f"  {name:<20s} FAILED: {r.get('reason')}")
        results.append(r)

    # Sanitize the recommendation dict for JSON: per_feature_r2 has numpy arrays.
    rec_serializable = {
        "mask": rec["mask"], "kernel_r2": rec["kernel_r2"],
        "top_k": rec["top_k"], "n_features": rec["n_features"],
        "min_r2": rec["min_r2"], "reasoning": rec["reasoning"],
        "per_feature_r2": {k: list(v) for k, v in rec["per_feature_r2"].items()},
    }
    report = {
        "generated_at": _now(),
        "snapshot": str(snap),
        "n_sources": len(catalog),
        "kernel_optimizer_recommendation": rec_serializable,
        "results": results,
        "honesty_notes": [
            "This is a demonstration that BDK FunctionPool works on a non-molecular dataset.",
            "The target (log_size) is partially derived from features, so R^2 is high by design.",
            "04 does NOT use BDK in production — this script just verifies generality.",
            "All output paths are under /tmp/ (no production writes).",
        ],
    }
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
    print(f"\nReport: {args.output}")


if __name__ == "__main__":
    main()
