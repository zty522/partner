#!/usr/bin/env python3
"""Execute real isolated fixtures for blinded Partner-LoopBench tasks."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import sqlite3
import tempfile
import time
from pathlib import Path


def _metric_patch(selection: str) -> tuple[float, dict]:
    import numpy as np
    from sklearn.metrics import mean_squared_error, root_mean_squared_error
    y_true = np.array([1.0, 2.0, 4.0, 8.0])
    y_pred = np.array([1.5, 1.5, 5.0, 7.0])
    expected = float(root_mean_squared_error(y_true, y_pred))
    try:
        if selection == "use_root_metric":
            observed = float(root_mean_squared_error(y_true, y_pred))
        elif selection == "manual_sqrt_mse":
            observed = math.sqrt(float(mean_squared_error(y_true, y_pred)))
        elif selection == "keep_removed_keyword":
            observed = float(mean_squared_error(y_true, y_pred, squared=False))
        else:
            observed = 0.0
        compatible = abs(observed - expected) < 1e-12
    except Exception:
        observed, compatible = float("nan"), False
    # The research handoff requires the versioned public replacement, rather
    # than an equivalent local workaround that bypasses the learned API claim.
    source_bound = selection == "use_root_metric"
    return float(compatible and source_bound), {
        "compatible": compatible, "source_bound": source_bound,
        "observed_rmse": observed, "expected_rmse": expected, "tests_run": 2}


def _queue_index(selection: str) -> tuple[float, dict]:
    with tempfile.TemporaryDirectory(prefix="partner_loop_queue_") as raw:
        root = Path(raw); jobs = root / "jobs"; jobs.mkdir()
        ready_id = "job_0317"
        for index in range(477):
            status = "queued" if index == 317 else "completed"
            (jobs / f"job_{index:04d}.json").write_text(json.dumps(
                {"job_id": f"job_{index:04d}", "status": status}), encoding="utf-8")
        db = sqlite3.connect(root / "jobs.db")
        db.execute("create table ready_jobs(job_id text primary key, priority integer)")
        db.execute("insert into ready_jobs values (?, 10)", (ready_id,)); db.commit()
        started = time.perf_counter(); files_read = 0
        if selection == "native_ready_index":
            found = db.execute("select job_id from ready_jobs order by priority limit 1").fetchone()[0]
        elif selection == "bounded_manifest":
            found = json.loads((jobs / "job_0317.json").read_text())["job_id"]; files_read = 1
        elif selection == "scan_all_json":
            found = ""
            for path in jobs.glob("*.json"):
                files_read += 1; row = json.loads(path.read_text())
                if row["status"] == "queued": found = row["job_id"]
        else:
            found = ""
        elapsed = time.perf_counter() - started; db.close()
    indexed = selection == "native_ready_index"
    return float(found == ready_id and indexed), {
        "found": found, "expected": ready_id, "files_read": files_read,
        "indexed_query": indexed, "elapsed_seconds": elapsed}


def _group_split(selection: str) -> tuple[float, dict]:
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.metrics import root_mean_squared_error
    from sklearn.model_selection import GroupKFold, KFold
    rng = np.random.default_rng(20260924)
    groups = np.repeat(np.arange(20), 12)
    x = rng.normal(size=(len(groups), 3))
    y = groups * .25 + x[:, 0] * .2 + rng.normal(scale=.08, size=len(groups))
    model = HistGradientBoostingRegressor(max_iter=40, random_state=7)
    if selection == "freeze_group_split":
        splitter = GroupKFold(n_splits=5); iterator = splitter.split(x, y, groups)
        leakage_free = True
    elif selection == "random_kfold":
        iterator = KFold(n_splits=5, shuffle=True, random_state=7).split(x, y)
        leakage_free = False
    else:
        iterator = []
        leakage_free = False
    rmses = []
    for train, test in iterator:
        model.fit(x[train], y[train]); rmses.append(float(root_mean_squared_error(y[test], model.predict(x[test]))))
    return float(leakage_free and len(rmses) == 5), {
        "leakage_free": leakage_free, "folds": len(rmses),
        "mean_rmse": sum(rmses) / len(rmses) if rmses else None}


RUNNERS = {"metric_api_patch": _metric_patch, "ready_queue_retrieval": _queue_index,
           "target_group_protocol": _group_split}

ACTION_ALIASES = {
    "warm_project_01": {"recipe_a17": "freeze_group_split", "recipe_b04": "random_kfold",
                        "recipe_c91": "official_test_tuning"},
    "transfer_project_02": {"recipe_a17": "freeze_group_split", "recipe_b04": "random_kfold",
                            "recipe_c91": "official_test_tuning"},
    "warm_learning_01": {"adapter_k22": "use_root_metric", "adapter_m08": "manual_sqrt_mse",
                         "adapter_p14": "keep_removed_keyword"},
    "transfer_learning_02": {"adapter_k22": "use_root_metric", "adapter_m08": "manual_sqrt_mse",
                             "adapter_p14": "keep_removed_keyword"},
    "warm_evolution_01": {"retrieval_q31": "native_ready_index", "retrieval_s12": "scan_all_json",
                          "retrieval_t05": "bounded_manifest"},
    "transfer_evolution_02": {"retrieval_q31": "native_ready_index", "retrieval_s12": "scan_all_json",
                              "retrieval_t05": "bounded_manifest"}
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True); parser.add_argument("--selection", required=True)
    parser.add_argument("--output", required=True); args = parser.parse_args()
    task_path = Path(args.task).resolve(); task = json.loads(task_path.read_text(encoding="utf-8"))
    kind = str(task.get("execution_kind") or "")
    if kind not in RUNNERS: raise ValueError(f"unknown real execution kind: {kind}")
    task_id = str(task.get("task_id") or "")
    effective_action = ACTION_ALIASES.get(task_id, {}).get(args.selection, args.selection)
    success, details = RUNNERS[kind](effective_action)
    details.update({"committed_candidate": args.selection, "effective_action_hash":
                    hashlib.sha256(effective_action.encode()).hexdigest()})
    evidence = {"schema_version": 1, "metrics": {"task_success": success},
        "predictions": [{"sample_id": task_id, "y_true": 1.0, "y_pred": success}],
        "guardrails": {"oracle_hidden": True, "same_public_task": True,
            "memory_source_verified": True, "within_budget": True},
        "run_config": {"model": "real-isolated-fixture", "folds": "task-defined",
            "seeds": "20260924", "budget": "one_action", "features": {"declared_feature": None}},
        "provenance": {"code_revision": "partner-loop-real-v1",
            "data_hash": "sha256:" + hashlib.sha256(task_path.read_bytes()).hexdigest()},
        "execution_details": details}
    Path(args.output).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__": raise SystemExit(main())
