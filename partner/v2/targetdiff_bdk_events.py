"""TargetDiff BDK FunctionPool stage.

This module is the BDK-integrated counterpart to
`targetdiff_continuous_events.py` (stages 9–13). It runs the SAME 5-fold
group-disjoint split but replaces sklearn's `LinearRegression` and
`HistGradientBoostingRegressor` with `bdk.function_pool.FunctionPool` via
`partner.learn.bdk_function_pool_fitter`.

Honesty contract:
    - This stage is PARALLEL to stage 13 (`targetdiff_method_decision`), not
      a replacement.  Existing 02 pipelines using stage 9–13 are untouched.
    - The BDK path is injected at subprocess invocation time (NOT relied on
      via sitecustomize).  If the BDK module is missing, this stage returns
      a clear `bdk_unavailable` status and the original 9–13 pipeline is
      unaffected.
    - The result includes kernel probs + BDK ocamms verdict so 02 / 05 can
      audit which kernels BDK actually used.
    - The script does NOT mutate the input `affinity_info.pkl`.  All reads
      are copy-mode via pickle.load.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from partner.governance.storage import latest_receipt, workspace_root


# Inline Python script for the subprocess.  It:
# 1. Reads affinity_info.pkl, builds (vina, rmsd) features and pk target.
# 2. 5-fold group-disjoint split (same hash as stage 9).
# 3. Trains BDKFunctionPoolFitter per fold.
# 4. Writes result JSON with kernel_probs, ocamms verdict, RMSE per fold.
# 5. Computes mean_delta_rmse so we can compare to stage 13 directly.
PIPELINE = r'''
import argparse, hashlib, json, math, pickle, re, statistics
import sys
from collections import defaultdict

# Inject the Partner repository for the subprocess. BDK organs are now owned
# by partner.learn; the incubator is never imported at runtime.
_PARTNER_ROOT = "/mnt/e/work/partner"
if _PARTNER_ROOT not in sys.path:
    sys.path.insert(0, _PARTNER_ROOT)

import numpy as np
from partner.learn.bdk_function_pool_fitter import BDKFunctionPoolFitter
from partner.learn.bdk_kernel_mask_optimizer import recommend_kernel_mask

p = argparse.ArgumentParser()
p.add_argument("--input")
p.add_argument("--output")
p.add_argument("--previous")
p.add_argument("--epochs", type=int, default=200)
p.add_argument("--data-driven-mask", type=int, default=0,
                help="if 1, use kernel-mask-optimizer-recommended mask")
a = p.parse_args()

raw = pickle.load(open(a.input, "rb"))
previous = json.load(open(a.previous, encoding="utf-8")) if a.previous else {}


def fold(group):
    return int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 5


def ligand_id(key):
    group, name = key.split("/", 1)
    return group + "/" + re.sub(r"_(?:min|docked)_\d+$", "", name)


rows = []
for key, value in raw.items():
    try:
        pk, vina, rmsd = float(value["pk"]), float(value["vina"]), float(value["rmsd"])
    except (KeyError, TypeError, ValueError):
        continue
    if pk > 0 and all(math.isfinite(x) for x in (pk, vina, rmsd)):
        rows.append({"group": str(key).split("/", 1)[0],
                     "ligand": ligand_id(str(key)),
                     "pk": pk, "vina": vina, "rmsd": rmsd})

buckets = defaultdict(list)
for row in rows:
    buckets[(row["group"], row["ligand"])].append(row)
agg = []
for (group, ligand), values in buckets.items():
    agg.append({"group": group, "ligand": ligand,
                 "pk": statistics.median(x["pk"] for x in values),
                 "vina": statistics.median(x["vina"] for x in values),
                 "rmsd": statistics.median(x["rmsd"] for x in values),
                 "replicates": len(values),
                 "pk_spread": max(x["pk"] for x in values) - min(x["pk"] for x in values)})


def metrics(y, pred):
    err = y - pred
    return {"count": int(len(y)),
            "rmse": float(np.sqrt(np.mean(err ** 2))),
            "mae": float(np.mean(np.abs(err))),
            "r2": float(1.0 - np.var(err) / max(np.var(y), 1e-12))}


folds = []
predictions = []
kernel_probs_per_fold = []
ocamms_per_fold = []

for held in range(5):
    tr = [r for r in agg if fold(r["group"]) != held]
    te = [r for r in agg if fold(r["group"]) == held]
    X_train = np.array([[r["vina"], r["rmsd"]] for r in tr], dtype=np.float32)
    y_train = np.array([r["pk"] for r in tr], dtype=np.float32)
    X_test = np.array([[r["vina"], r["rmsd"]] for r in te], dtype=np.float32)
    y_test = np.array([r["pk"] for r in te], dtype=np.float32)

    # Optional: data-driven mask recommendation. Opt in via CLI flag.
    use_data_mask = bool(getattr(a, "data_driven_mask", 0))
    if use_data_mask:
        rec = recommend_kernel_mask(X_train, y_train, top_k=2)
        initial_mask = rec["mask"]
    else:
        initial_mask = [True, True, True, True]
    fitter = BDKFunctionPoolFitter(in_dim=2, epochs=a.epochs, seed=42 + held,
                                     initial_kernel_mask=initial_mask)
    fitter.fit(X_train, y_train)
    pred = fitter.predict(X_test)
    bdk_m = metrics(y_test, pred)
    audit = fitter.ocamms_check()

    folds.append({"fold": held,
                  "train_ligands": len(tr),
                  "test_ligands": len(te),
                  "train_groups": len({r["group"] for r in tr}),
                  "test_groups": len({r["group"] for r in te}),
                  "group_overlap": len({r["group"] for r in tr}
                                       & {r["group"] for r in te}),
                  "bdk": bdk_m})
    predictions.extend({"fold": held, "group": r["group"], "ligand": r["ligand"],
                        "y": float(actual), "bdk": float(p)}
                       for r, actual, p in zip(te, y_test, pred))
    kernel_probs_per_fold.append(fitter.kernel_probs)
    ocamms_per_fold.append({"passed": audit["passed"],
                             "n_activated": audit["n_activated"],
                             "activated_names": audit["activated_names"]})

mean_rmse = statistics.fmean(x["bdk"]["rmse"] for x in folds)
ocamms_all_passed = all(o["passed"] for o in ocamms_per_fold)
mean_kernel_probs = [statistics.fmean(p[i] for p in kernel_probs_per_fold)
                     for i in range(4)]

result = {
    "ok": True,
    "method": "bdk_function_pool",
    "lineage": {"previous_path": a.previous or "",
                "previous_stage": previous.get("stage"),
                "previous_event": previous.get("event"),
                "consumed": bool(previous)},
    "contract": {"target_field": "pk",
                  "features": ["vina", "rmsd"],
                  "aggregation": "median by group + ligand filename without final min/docked conformer suffix",
                  "split": "sha256(group)%5",
                  "causal_claim": False},
    "row_records": len(rows),
    "aggregated_ligands": len(agg),
    "replication_factor": len(rows) / max(len(agg), 1),
    "max_replicates": max((r["replicates"] for r in agg), default=0),
    "pk_spread_nonzero_ligands": sum(r["pk_spread"] > 1e-12 for r in agg),
    "max_pk_spread": max((r["pk_spread"] for r in agg), default=0.0),
    "folds": folds,
    "mean_bdk_rmse": mean_rmse,
    "ocamms_all_passed": ocamms_all_passed,
    "mean_kernel_probs": mean_kernel_probs,
    "epochs_per_fold": a.epochs,
}
with open(a.output, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"BDK stage complete: mean_rmse={mean_rmse:.4f}, ocamms_all_passed={ocamms_all_passed}")
'''


def atomic_targetdiff_bdk_stage(ctx: Any, params: dict) -> dict:
    """Run the BDK FunctionPool 5-fold CV on TargetDiff data.

    Reads affinity_info.pkl the same way stage 9 does.  Produces a
    result.json with `method=bdk_function_pool` and `mean_bdk_rmse`
    alongside `mean_kernel_probs` and `ocamms_all_passed`.
    """
    # Verify the migrated Partner-owned implementation before subprocess use.
    try:
        from partner.learn.bdk_function_pool import FunctionPool  # noqa: F401
    except ImportError as exc:
        return {
            "ok": False,
            "status": "bdk_unavailable",
            "error": f"Partner-owned BDK module is unavailable: {exc}",
            "retryable": False,
        }

    root = workspace_root(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    working_str = str(getattr(task, "working_dir", "") if task else "") or str(getattr(ctx, "working_dir", "") or "")
    working = Path(working_str) if working_str and working_str not in ("", ".") else Path(str(root))
    out_dir = working / "molecular" / "bdk_runs"
    out_dir.mkdir(parents=True, exist_ok=True)

    source_data = root / "external" / "targetdiff" / "data" / "affinity_info.pkl"
    if not source_data.exists():
        return {
            "ok": False,
            "status": "missing_input",
            "error": f"TargetDiff data not found at {source_data}",
            "files": [],
        }

    epochs = int(params.get("epochs") or 200)
    run_id = params.get("run_id") or "bdk_default"
    source = out_dir / f"targetdiff_bdk_{run_id}.py"
    output = out_dir / f"targetdiff_bdk_{run_id}_result.json"

    source.write_text(PIPELINE, encoding="utf-8")

    receipt = latest_receipt(str(root), "molecular_generation")
    previous = None
    if receipt is not None:
        for path in reversed(receipt.artifacts or []):
            candidate = Path(str(path))
            if candidate.is_file() and candidate.suffix == ".json" and "bdk_" not in candidate.name:
                previous = candidate
                break

    command = [
        sys.executable, str(source),
        "--input", str(source_data),
        "--output", str(output),
        "--epochs", str(epochs),
    ]
    if bool(params.get("data_driven_mask")):
        command.extend(["--data-driven-mask", "1"])
    if previous is not None:
        command.extend(["--previous", str(previous)])

    proc = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not output.is_file():
        return {
            "ok": False,
            "status": "script_failed",
            "error": (proc.stderr or proc.stdout or "")[-3000:],
            "files": [str(source)],
        }
    result = json.loads(output.read_text(encoding="utf-8"))

    # Cross-check group leakage (same hard gate as stage 9).
    if any(row.get("group_overlap", 0) for row in result.get("folds", [])):
        return {
            "ok": False,
            "status": "group_leakage",
            "result": result,
            "files": [str(source), str(output)],
        }

    # Build a markdown report so 02 can render PDF / send via QQ.
    excerpt = json.dumps(result, ensure_ascii=False, indent=2)[:8000]
    title = "TargetDiff BDK FunctionPool 5-Fold CV"
    report = f"""# {title}

## 本轮目标

在 TargetDiff affinity 数据上跑 BDK FunctionPool 5-fold 交叉验证，
产出 `mean_bdk_rmse`、每折的 BDK RMSE、跨折平均的 kernel_probs 和
`ocamms_all_passed` 标记。本阶段与 Stage 9–13 (sklearn baseline) 平行
——**不替换**已有 pipeline，只作为附加对照。

## 真实执行

实际命令：`{' '.join(command)}`。源码写入 `{source}`，独立子进程运行，
退出码为 {proc.returncode}，机器 JSON 输出到 `{output}`。

## 机器结果

```json
{excerpt}
```

## 决策边界

BDK 结果是 **回归器对照**，不是药效因果。本阶段：

- 不替换 Stage 9–13 的 LinearRegression + HistGradientBoostingRegressor
- 不声称 BDK 在任何指标上优于 sklearn（要看 mean_bdk_rmse 与 sklearn
  baseline 的对比才能下结论）
- 若 `ocamms_all_passed=False`，说明 BDK 在某些折选择了 >2 个核，
  这本身是合法的训练结果，但 02 把它当"模型复杂度警告"展示给用户

## 下一步

由 02 决定是否把 BDK 结果并入 Stage 13 的 method_decision 摘要，或作为
独立 evidence 写到下一阶段的 previous 输入。本模块本身不修改任何
production 配置或 partner runtime。
"""
    md = out_dir / f"targetdiff_bdk_{run_id}_report.md"
    md.write_text(report, encoding="utf-8")

    # Try to render PDF like other stages do (best-effort; failures are
    # surfaced but don't fail the stage since the JSON is the canonical
    # artifact).
    pdf_result: dict[str, Any] = {"ok": False, "error": "pdf_skipped"}
    pdf = out_dir / f"targetdiff_bdk_{run_id}_report.pdf"
    try:
        from partner.v2.pdf_events import atomic_generate_detailed_pdf
        pdf_result = atomic_generate_detailed_pdf(ctx, {
            "content": report,
            "output_path": str(pdf),
            "title": title,
            "report_style": "research",
            "min_content_chars": 800,
            "min_sections": 4,
        })
    except Exception as exc:
        pdf_result = {"ok": False, "error": f"pdf generation failed: {exc}"}

    files = [str(source), str(output), str(md)]
    if pdf_result.get("ok"):
        files.append(str(pdf))

    return {
        "ok": True,
        "status": "completed",
        "method": "bdk_function_pool",
        "summary": (f"BDK FunctionPool 5-fold CV 完成: mean_bdk_rmse={result['mean_bdk_rmse']:.4f},"
                     f" ocamms_all_passed={result['ocamms_all_passed']}"),
        "result": result,
        "files": files,
        "path": str(output),
    }


def atomic_targetdiff_sklearn_baseline(ctx: Any, params: dict) -> dict[str, Any]:
    """Run the matched sklearn arm for an Event-first BDK experiment."""
    root = workspace_root(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    working_str = str(
        getattr(task, "working_dir", "") if task else ""
    ) or str(getattr(ctx, "working_dir", "") or "")
    working = Path(working_str) if working_str and working_str != "." else Path(str(root))
    out_dir = working / "molecular" / "bdk_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    source_data = root / "external" / "targetdiff" / "data" / "affinity_info.pkl"
    if not source_data.is_file():
        return {
            "ok": False,
            "status": "missing_input",
            "error": f"TargetDiff data not found at {source_data}",
            "files": [],
        }

    from partner.learn.targetdiff_bdk_vs_sklearn_comparison import (
        load_aggregated_ligands,
        run_sklearn_baseline,
    )

    aggregated = load_aggregated_ligands(source_data)
    result = run_sklearn_baseline(aggregated)
    result.update({
        "ok": True,
        "event": "targetdiff_sklearn_affinity_baseline",
        "contract": {
            "target_field": "pk",
            "features": ["vina", "rmsd"],
            "split": "sha256(group)%5",
            "group_disjoint": True,
        },
        "aggregated_ligands": len(aggregated),
    })
    run_id = str(params.get("run_id") or "sklearn_default")
    output = out_dir / f"targetdiff_sklearn_{run_id}_result.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report = f"""# TargetDiff sklearn 匹配基线

- Event: `targetdiff_sklearn_affinity_baseline`
- 数据: `{source_data}`
- 聚合 ligand: {len(aggregated)}
- Split: `sha256(group)%5`，与 BDK Candidate 完全相同
- Linear mean RMSE: {result['mean_linear_rmse']:.6f}
- HGB mean RMSE: {result['mean_hgb_rmse']:.6f}
- 本事件只建立实验基线，不修改生产策略。
"""
    md = out_dir / f"targetdiff_sklearn_{run_id}_report.md"
    md.write_text(report, encoding="utf-8")
    return {
        "ok": True,
        "status": "completed",
        "method": "sklearn_baseline",
        "summary": (
            "sklearn 5-fold baseline 完成: "
            f"linear={result['mean_linear_rmse']:.4f}, hgb={result['mean_hgb_rmse']:.4f}"
        ),
        "result": result,
        "files": [str(output), str(md)],
        "path": str(output),
    }


HANDLERS = {
    "targetdiff_bdk_function_pool": atomic_targetdiff_bdk_stage,
    "targetdiff_sklearn_affinity_baseline": atomic_targetdiff_sklearn_baseline,
}
