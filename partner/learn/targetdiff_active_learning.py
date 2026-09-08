"""Offline, real-data active-learning benchmark for TargetDiff affinity labels.

This does not choose a regression function.  It freezes the official test set,
hides labels in an auditable training pool, and compares which *samples to
label next* under equal budgets for three rounds.
"""
from __future__ import annotations

import hashlib
import json
import math
import pickle
from pathlib import Path
from typing import Any

from partner.governance.models import now_iso
from partner.governance.evolution_events import append_evolution_event
from partner.governance.storage import atomic_json, workspace_root


def acquisition_scores(uncertainty: Any, distance: Any, slice_priority: Any,
                       weights: tuple[float, float, float] = (.55, .30, .15)) -> Any:
    import numpy as np
    def norm(values: Any) -> Any:
        values = np.asarray(values, dtype=float)
        width = float(values.max() - values.min()) if len(values) else 0.0
        return (values - values.min()) / width if width > 0 else np.zeros_like(values)
    if len(weights) != 3 or any(float(value) < 0 for value in weights):
        raise ValueError("acquisition weights must be three non-negative values")
    total = sum(float(value) for value in weights)
    if total <= 0:
        raise ValueError("acquisition weights must have positive sum")
    wu, wd, ws = (float(value) / total for value in weights)
    return wu * norm(uncertainty) + wd * norm(distance) + ws * norm(slice_priority)


def _load_real_rows(repo: Path) -> tuple[Any, Any, Any, Any, list[str], list[str]]:
    import numpy as np
    import torch
    split = torch.load(repo / "data/split_by_name.pt", map_location="cpu", weights_only=True)
    with (repo / "data/affinity_info.pkl").open("rb") as handle:
        raw = pickle.load(handle)
    keys = {part: {str(pair[1]).rsplit(".", 1)[0] for pair in split[part]}
            for part in ("train", "test")}
    def rows(part: str) -> list[tuple[str, float, float, float]]:
        result = []
        for key in sorted(keys[part] & set(raw)):
            value = raw[key]
            try:
                pk, vina, rmsd = float(value["pk"]), float(value["vina"]), float(value["rmsd"])
            except (KeyError, TypeError, ValueError):
                continue
            if pk > 0 and all(math.isfinite(item) for item in (pk, vina, rmsd)):
                result.append((key, vina, rmsd, pk))
        return result
    train, test = rows("train"), rows("test")
    if len(train) < 500 or len(test) < 10:
        raise ValueError(f"insufficient official real rows: train={len(train)} test={len(test)}")
    return (
        np.asarray([[row[1], row[2]] for row in train], dtype=float),
        np.asarray([row[3] for row in train], dtype=float),
        np.asarray([[row[1], row[2]] for row in test], dtype=float),
        np.asarray([row[3] for row in test], dtype=float),
        [row[0] for row in train], [row[0] for row in test],
    )


def _fit_predict(x: Any, y: Any, test_x: Any, pool_x: Any,
                 seed: int) -> tuple[Any, Any, Any]:
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor
    model = RandomForestRegressor(n_estimators=48, min_samples_leaf=3,
                                  random_state=seed, n_jobs=1, oob_score=True)
    model.fit(x, y)
    test_predictions = model.predict(test_x)
    tree_pool = np.vstack([tree.predict(pool_x) for tree in model.estimators_])
    return test_predictions, np.var(tree_pool, axis=0), np.asarray(model.oob_prediction_)


def calibrated_slice_uncertainty(labelled_x: Any, labelled_y: Any, labelled_oob: Any,
                                 pool_x: Any, raw_uncertainty: Any,
                                 reference_x: Any) -> tuple[Any, dict[str, Any]]:
    """Calibrate RF variance with label-visible OOB error by RMSD slice.

    Feature quantiles do not use hidden labels.  Per-slice error estimates use
    only currently labelled samples and are shrunk toward the global error so
    sparse slices cannot receive extreme weights.
    """
    import numpy as np
    labelled_x = np.asarray(labelled_x, dtype=float)
    pool_x = np.asarray(pool_x, dtype=float)
    residual = np.abs(np.asarray(labelled_y, dtype=float) - np.asarray(labelled_oob, dtype=float))
    boundaries = np.quantile(np.asarray(reference_x, dtype=float)[:, 1], [.25, .5, .75])
    labelled_bins = np.digitize(labelled_x[:, 1], boundaries, right=False)
    pool_bins = np.digitize(pool_x[:, 1], boundaries, right=False)
    global_error = max(1e-9, float(np.mean(residual)))
    factors: list[float] = []
    counts: list[int] = []
    for index in range(4):
        values = residual[labelled_bins == index]
        count = int(len(values))
        shrunk = ((float(values.sum()) + 10.0 * global_error) / (count + 10.0))
        factors.append(max(.5, min(2.0, shrunk / global_error)))
        counts.append(count)
    calibrated = np.asarray(raw_uncertainty, dtype=float) * np.asarray(factors)[pool_bins]
    return calibrated, {
        "method": "oob_absolute_error_by_rmsd_quartile_shrink10_v1",
        "boundaries": [float(value) for value in boundaries],
        "labelled_counts": counts, "factors": factors,
        "hidden_pool_labels_used": False,
    }


def run_targetdiff_active_learning(workspace: str, *, run_id: str = "",
                                   rounds: int = 3, batch_size: int = 40,
                                   active_weights: tuple[float, float, float] = (.55, .30, .15),
                                   seed: int = 20260901,
                                   active_mode: str = "raw") -> dict[str, Any]:
    import numpy as np
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    root = workspace_root(workspace)
    repo = root / "external/targetdiff"
    required = [repo / "data/affinity_info.pkl", repo / "data/split_by_name.pt"]
    if not all(path.is_file() for path in required):
        return {"ok": False, "status": "blocked_missing_real_data",
                "missing": [str(path) for path in required if not path.is_file()]}
    train_x, train_y, test_x, test_y, train_keys, test_keys = _load_real_rows(repo)
    seed = int(seed)
    if seed < 0 or seed > 2**32 - 1:
        raise ValueError("seed must fit the NumPy uint32 range")
    if active_mode not in {"raw", "calibrated_slice"}:
        raise ValueError("active_mode must be raw or calibrated_slice")
    rng = np.random.default_rng(seed)
    # A bounded, frozen pool avoids turning compute volume into an advantage.
    pool_size = min(2400, len(train_x))
    pool_indices = np.sort(rng.choice(len(train_x), pool_size, replace=False))
    x, y = train_x[pool_indices], train_y[pool_indices]
    keys = [train_keys[int(index)] for index in pool_indices]
    initial = sorted(rng.choice(pool_size, min(120, pool_size // 4), replace=False).tolist())
    arms: dict[str, Any] = {}
    for arm_index, arm in enumerate(("random", "maxvar", "active")):
        labelled = list(initial)
        arm_rng = np.random.default_rng(seed + 1000 + arm_index)
        history = []
        for round_index in range(max(1, min(6, int(rounds))) + 1):
            remaining = np.asarray(sorted(set(range(pool_size)) - set(labelled)), dtype=int)
            test_pred, uncertainty, labelled_oob = _fit_predict(
                x[labelled], y[labelled], test_x, x[remaining], seed + round_index)
            rmse = float(mean_squared_error(test_y, test_pred) ** .5)
            mae = float(mean_absolute_error(test_y, test_pred))
            record = {"round": round_index, "labelled": len(labelled),
                      "test_rmse": rmse, "test_mae": mae,
                      "mean_pool_uncertainty": float(np.mean(uncertainty)),
                      "selected_keys": [], "calibration": {}}
            if round_index == max(1, min(6, int(rounds))) or not len(remaining):
                history.append(record)
                break
            take = min(int(batch_size), len(remaining))
            if arm == "random":
                local = arm_rng.choice(len(remaining), take, replace=False)
            elif arm == "maxvar":
                local = np.argsort(-uncertainty, kind="stable")[:take]
            else:
                labelled_x = x[labelled]
                distance = np.min(np.linalg.norm(
                    (x[remaining, None, :] - labelled_x[None, :, :]) /
                    (np.std(x, axis=0) + 1e-9), axis=2), axis=1)
                # Error-slice prior: scarce/high-RMSD regions receive a small,
                # explicit boost, never access to hidden pK labels.
                rmsd = x[remaining, 1]
                slice_priority = (rmsd >= np.quantile(x[:, 1], .75)).astype(float)
                scored_uncertainty = uncertainty
                if active_mode == "calibrated_slice":
                    scored_uncertainty, record["calibration"] = calibrated_slice_uncertainty(
                        x[labelled], y[labelled], labelled_oob, x[remaining], uncertainty, x)
                local = np.argsort(-acquisition_scores(
                    scored_uncertainty, distance, slice_priority, active_weights),
                                   kind="stable")[:take]
            selected = remaining[local].tolist()
            record["selected_keys"] = [keys[index] for index in selected]
            history.append(record)
            labelled.extend(selected)
        arms[arm] = {"history": history,
                     "rmse_change": history[-1]["test_rmse"] - history[0]["test_rmse"],
                     "uncertainty_change": (history[-1]["mean_pool_uncertainty"]
                                            - history[0]["mean_pool_uncertainty"])}
    active_final = arms["active"]["history"][-1]["test_rmse"]
    comparator = min(arms["random"]["history"][-1]["test_rmse"],
                     arms["maxvar"]["history"][-1]["test_rmse"])
    decision = "candidate_better" if active_final < comparator else "candidate_not_better"
    source_digest = hashlib.sha256(b"".join(path.read_bytes() for path in required)).hexdigest()
    run_id = run_id or f"targetdiff_active_{source_digest[:10]}"
    result = {
        "schema_version": 1, "run_id": run_id, "status": "completed", "ok": True,
        "question": "which real training samples should be labelled next under equal budgets",
        "not_function_selection": True, "official_split": True,
        "source_digest": source_digest, "train_rows": len(train_x), "test_rows": len(test_x),
        "test_keys_digest": hashlib.sha256("\n".join(test_keys).encode()).hexdigest(),
        "pool_size": pool_size, "initial_labels": len(initial), "rounds": rounds,
        "batch_size": batch_size, "seed": seed, "arms": arms, "decision": decision,
        "active_mode": active_mode,
        "active_weights": [float(value) for value in active_weights],
        "causal_claim": False, "production_effective": False, "created_at": now_iso(),
    }
    output = root / "share/mind/governance/active_learning/targetdiff" / f"{run_id}.json"
    atomic_json(output, result)
    return {**result, "path": str(output), "files": [str(output)]}


def evaluate_targetdiff_robustness(workspace: str, *, run_ids: list[str],
                                   evaluation_id: str = "sprint18_targetdiff_seed_robustness_v1") -> dict[str, Any]:
    """Aggregate independent real-pool seeds and reject fragile selectors.

    A single favourable pool is insufficient.  The Candidate must beat both
    equal-budget comparators in at least two of three seeds and improve the
    mean final RMSE.  This record is append-only Event evidence and never a
    production-policy mutation.
    """
    root = workspace_root(workspace)
    directory = root / "share/mind/governance/active_learning/targetdiff"
    rows: list[dict[str, Any]] = []
    paths: list[str] = []
    for run_id in run_ids:
        path = directory / f"{run_id}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"ok": False, "status": "missing_robustness_run", "run_id": run_id,
                    "path": str(path), "production_effective": False}
        if value.get("official_split") is not True or value.get("status") != "completed":
            return {"ok": False, "status": "invalid_robustness_run", "run_id": run_id,
                    "path": str(path), "production_effective": False}
        arms = dict(value.get("arms") or {})
        finals = {arm: float(dict(arms.get(arm) or {}).get("history", [{}])[-1]["test_rmse"])
                  for arm in ("random", "maxvar", "active")}
        rows.append({"run_id": run_id, "seed": value.get("seed"),
                     "active_mode": str(value.get("active_mode") or "raw"), "final_rmse": finals,
                     "candidate_win": finals["active"] < min(finals["random"], finals["maxvar"])})
        paths.append(str(path))
    if len(rows) < 3:
        return {"ok": False, "status": "insufficient_robustness_seeds",
                "required": 3, "observed": len(rows), "production_effective": False}
    means = {arm: sum(row["final_rmse"][arm] for row in rows) / len(rows)
             for arm in ("random", "maxvar", "active")}
    wins = sum(bool(row["candidate_win"]) for row in rows)
    gates = {
        "three_independent_pool_seeds": len({row["seed"] for row in rows}) >= 3,
        "candidate_wins_two_of_three": wins >= 2,
        "candidate_mean_beats_both": means["active"] < min(means["random"], means["maxvar"]),
    }
    decision = "accept_for_extended_shadow" if all(gates.values()) else "rejected_not_robust"
    attempted_calibration = all(row.get("active_mode") == "calibrated_slice" for row in rows)
    result = {
        "schema_version": 1, "evaluation_id": evaluation_id, "status": "completed",
        "question": "does the active acquisition advantage reproduce across frozen real-data pool seeds",
        "runs": rows, "metrics": {"candidate_wins": wins, "mean_final_rmse": means},
        "gates": gates, "decision": decision,
        "next_hypothesis": (
            ("measure uncertainty reliability/calibration curves by frozen slice before proposing another acquisition rule"
             if attempted_calibration else
             "calibrate uncertainty and test density/error-slice conditioning before retuning weights")
            if decision.startswith("rejected") else
            "extend to held-out temporal/source windows before any production consideration"
        ),
        "production_mutation": False, "production_effective": False,
        "promotion": False, "created_at": now_iso(),
    }
    output = directory / f"{evaluation_id}.json"
    atomic_json(output, result)
    event = append_evolution_event(
        str(root), "active_learning/robustness_evaluated",
        subject_id=evaluation_id, project_id="molecular_generation",
        payload={"decision": decision, "candidate_wins": wins, "gates": gates,
                 "production_effective": False},
        evidence_refs=[*paths, str(output)],
        idempotency_key=f"targetdiff-robustness:{evaluation_id}",
    )
    return {"ok": True, **result, "path": str(output), "files": [str(output)], "event": event}


def diagnose_targetdiff_uncertainty(
    workspace: str, *, seeds: list[int] | None = None,
    evaluation_id: str = "sprint18_targetdiff_uncertainty_diagnostic_v1",
    labelled_budget: int = 240,
) -> dict[str, Any]:
    """Measure whether RF variance predicts held-out error before retuning.

    Official-test labels are used only to evaluate reliability after fitting;
    they never enter training or sample selection.  This is a diagnostic
    Event, not an acquisition Candidate or a production promotion decision.
    """
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor

    root = workspace_root(workspace)
    repo = root / "external/targetdiff"
    required = [repo / "data/affinity_info.pkl", repo / "data/split_by_name.pt"]
    if not all(path.is_file() for path in required):
        return {"ok": False, "status": "blocked_missing_real_data",
                "missing": [str(path) for path in required if not path.is_file()]}
    train_x, train_y, test_x, test_y, _, test_keys = _load_real_rows(repo)
    seeds = [int(value) for value in (seeds or [20260901, 20260902, 20260903])]
    if len(set(seeds)) < 3:
        return {"ok": False, "status": "three_distinct_seeds_required",
                "production_effective": False}

    def ranks(values: Any) -> Any:
        values = np.asarray(values, dtype=float)
        order = np.argsort(values, kind="stable")
        ranked = np.empty(len(values), dtype=float)
        ranked[order] = np.arange(len(values), dtype=float)
        # Average tied ranks so constant variance cannot fake correlation.
        for value in np.unique(values):
            mask = values == value
            ranked[mask] = float(np.mean(ranked[mask]))
        return ranked

    def correlation(left: Any, right: Any) -> float:
        left_rank, right_rank = ranks(left), ranks(right)
        if float(np.std(left_rank)) == 0.0 or float(np.std(right_rank)) == 0.0:
            return 0.0
        return float(np.corrcoef(left_rank, right_rank)[0, 1])

    boundaries = np.quantile(test_x[:, 1], [.25, .5, .75])
    slice_ids = np.digitize(test_x[:, 1], boundaries, right=False)
    rows: list[dict[str, Any]] = []
    budget = max(60, min(int(labelled_budget), len(train_x)))
    for seed in seeds:
        rng = np.random.default_rng(seed)
        selected = np.sort(rng.choice(len(train_x), budget, replace=False))
        model = RandomForestRegressor(
            n_estimators=96, min_samples_leaf=3, random_state=seed, n_jobs=1,
        )
        model.fit(train_x[selected], train_y[selected])
        tree_predictions = np.vstack([tree.predict(test_x) for tree in model.estimators_])
        prediction = np.mean(tree_predictions, axis=0)
        variance = np.var(tree_predictions, axis=0)
        error = np.abs(test_y - prediction)
        uncertainty_bins = np.digitize(
            variance, np.quantile(variance, [.25, .5, .75]), right=False,
        )
        bin_rows = []
        for index in range(4):
            mask = uncertainty_bins == index
            bin_rows.append({
                "quartile": index + 1, "count": int(mask.sum()),
                "mean_variance": float(np.mean(variance[mask])) if mask.any() else 0.0,
                "mean_absolute_error": float(np.mean(error[mask])) if mask.any() else 0.0,
            })
        high_uncertainty = uncertainty_bins == 3
        high_error = error >= np.quantile(error, .75)
        slice_rows = []
        for index in range(4):
            mask = slice_ids == index
            slice_rows.append({
                "rmsd_quartile": index + 1, "count": int(mask.sum()),
                "spearman_variance_abs_error": correlation(variance[mask], error[mask]),
                "mean_absolute_error": float(np.mean(error[mask])),
            })
        rows.append({
            "seed": seed, "labelled_budget": budget,
            "spearman_variance_abs_error": correlation(variance, error),
            "top_uncertainty_error_lift": float(np.mean(error[high_uncertainty]) / max(np.mean(error), 1e-12)),
            "top_error_quartile_recall": float(np.sum(high_uncertainty & high_error) / max(1, np.sum(high_error))),
            "monotonic_error_bin_transitions": sum(
                bin_rows[index + 1]["mean_absolute_error"] >= bin_rows[index]["mean_absolute_error"]
                for index in range(3)
            ),
            "uncertainty_bins": bin_rows, "rmsd_slices": slice_rows,
        })
    metrics = {
        "mean_spearman_variance_abs_error": float(np.mean([
            row["spearman_variance_abs_error"] for row in rows])),
        "mean_top_uncertainty_error_lift": float(np.mean([
            row["top_uncertainty_error_lift"] for row in rows])),
        "mean_top_error_quartile_recall": float(np.mean([
            row["top_error_quartile_recall"] for row in rows])),
        "mean_monotonic_error_bin_transitions": float(np.mean([
            row["monotonic_error_bin_transitions"] for row in rows])),
    }
    gates = {
        "positive_rank_signal": metrics["mean_spearman_variance_abs_error"] >= .15,
        "high_uncertainty_enriches_error": metrics["mean_top_uncertainty_error_lift"] >= 1.10,
        "three_seed_consistency": sum(
            row["spearman_variance_abs_error"] > 0 for row in rows
        ) >= 2,
    }
    decision = "uncertainty_informative" if all(gates.values()) else "uncertainty_weak_or_miscalibrated"
    source_digest = hashlib.sha256(b"".join(path.read_bytes() for path in required)).hexdigest()
    result = {
        "schema_version": 1, "evaluation_id": evaluation_id, "status": "completed",
        "question": "does labelled-only RF variance reliably identify larger official-test errors",
        "official_split": True, "source_digest": source_digest,
        "test_keys_digest": hashlib.sha256("\n".join(test_keys).encode()).hexdigest(),
        "test_labels_used_for_selection": False,
        "test_labels_used_for_posthoc_evaluation_only": True,
        "seeds": seeds, "labelled_budget": budget, "runs": rows,
        "metrics": metrics, "gates": gates, "decision": decision,
        "next_hypothesis": (
            "test uncertainty-first acquisition with frozen weights and the same three seeds"
            if decision == "uncertainty_informative" else
            "replace raw tree variance with cross-fitted residual or conformal uncertainty before another acquisition candidate"
        ),
        "candidate_tested": False, "promotion": False,
        "production_mutation": False, "production_effective": False,
        "created_at": now_iso(),
    }
    directory = root / "share/mind/governance/active_learning/targetdiff"
    output = directory / f"{evaluation_id}.json"
    atomic_json(output, result)
    event = append_evolution_event(
        str(root), "active_learning/uncertainty_diagnostic",
        subject_id=evaluation_id, project_id="molecular_generation",
        payload={"decision": decision, "metrics": metrics, "gates": gates,
                 "production_effective": False},
        evidence_refs=[str(output)],
        idempotency_key=f"targetdiff-uncertainty:{evaluation_id}",
    )
    return {"ok": True, **result, "path": str(output), "files": [str(output)], "event": event}


def evaluate_targetdiff_uncertainty_candidate(
    workspace: str, *, seeds: list[int] | None = None,
    evaluation_id: str = "sprint18_targetdiff_crossfit_uncertainty_candidate_v1",
    labelled_budget: int = 240,
) -> dict[str, Any]:
    """Matched comparison of raw variance and labelled-only residual uncertainty."""
    import time
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.model_selection import KFold, cross_val_predict

    root = workspace_root(workspace)
    repo = root / "external/targetdiff"
    required = [repo / "data/affinity_info.pkl", repo / "data/split_by_name.pt"]
    if not all(path.is_file() for path in required):
        return {"ok": False, "status": "blocked_missing_real_data",
                "missing": [str(path) for path in required if not path.is_file()]}
    train_x, train_y, test_x, test_y, _, test_keys = _load_real_rows(repo)
    seeds = [int(value) for value in (seeds or [20260901, 20260902, 20260903])]
    if len(set(seeds)) < 3:
        return {"ok": False, "status": "three_distinct_seeds_required",
                "production_effective": False}

    def ranks(values: Any) -> Any:
        values = np.asarray(values, dtype=float)
        order = np.argsort(values, kind="stable")
        ranked = np.empty(len(values), dtype=float)
        ranked[order] = np.arange(len(values), dtype=float)
        for value in np.unique(values):
            mask = values == value
            ranked[mask] = float(np.mean(ranked[mask]))
        return ranked

    def metrics(uncertainty: Any, error: Any) -> dict[str, float]:
        left, right = ranks(uncertainty), ranks(error)
        spearman = (0.0 if float(np.std(left)) == 0.0 else
                    float(np.corrcoef(left, right)[0, 1]))
        high_u = np.asarray(uncertainty) >= np.quantile(uncertainty, .75)
        high_e = np.asarray(error) >= np.quantile(error, .75)
        return {
            "spearman_variance_abs_error": spearman,
            "top_uncertainty_error_lift": float(np.mean(error[high_u]) / max(np.mean(error), 1e-12)),
            "top_error_quartile_recall": float(np.sum(high_u & high_e) / max(1, np.sum(high_e))),
        }

    budget = max(60, min(int(labelled_budget), len(train_x)))
    runs: list[dict[str, Any]] = []
    for seed in seeds:
        started = time.monotonic()
        rng = np.random.default_rng(seed)
        selected = np.sort(rng.choice(len(train_x), budget, replace=False))
        labelled_x, labelled_y = train_x[selected], train_y[selected]
        baseline = RandomForestRegressor(
            n_estimators=48, min_samples_leaf=3, random_state=seed, n_jobs=1,
        )
        baseline.fit(labelled_x, labelled_y)
        tree_predictions = np.vstack([tree.predict(test_x) for tree in baseline.estimators_])
        prediction = np.mean(tree_predictions, axis=0)
        raw_uncertainty = np.var(tree_predictions, axis=0)
        error = np.abs(test_y - prediction)

        folds = KFold(n_splits=4, shuffle=True, random_state=seed)
        oof = cross_val_predict(
            RandomForestRegressor(n_estimators=32, min_samples_leaf=3,
                                  random_state=seed + 101, n_jobs=1),
            labelled_x, labelled_y, cv=folds, n_jobs=1,
        )
        residual_model = RandomForestRegressor(
            n_estimators=48, min_samples_leaf=5, random_state=seed + 202, n_jobs=1,
        )
        residual_model.fit(labelled_x, np.abs(labelled_y - oof))
        candidate_uncertainty = np.maximum(0.0, residual_model.predict(test_x))
        baseline_metrics = metrics(raw_uncertainty, error)
        candidate_metrics = metrics(candidate_uncertainty, error)
        runs.append({
            "seed": seed, "labelled_budget": budget,
            "baseline": baseline_metrics, "candidate": candidate_metrics,
            "candidate_win": (
                candidate_metrics["spearman_variance_abs_error"] > baseline_metrics["spearman_variance_abs_error"]
                and candidate_metrics["top_error_quartile_recall"] >= baseline_metrics["top_error_quartile_recall"]
            ),
            "elapsed_seconds": round(time.monotonic() - started, 4),
        })
    mean_metrics = {
        arm: {key: float(np.mean([row[arm][key] for row in runs]))
              for key in runs[0][arm]}
        for arm in ("baseline", "candidate")
    }
    wins = sum(bool(row["candidate_win"]) for row in runs)
    gates = {
        "three_distinct_seeds": len(set(seeds)) >= 3,
        "candidate_wins_two_of_three": wins >= 2,
        "mean_rank_signal_improves": (
            mean_metrics["candidate"]["spearman_variance_abs_error"]
            > mean_metrics["baseline"]["spearman_variance_abs_error"]
        ),
        "mean_top_error_recall_not_worse": (
            mean_metrics["candidate"]["top_error_quartile_recall"]
            >= mean_metrics["baseline"]["top_error_quartile_recall"]
        ),
    }
    decision = "accept_for_acquisition_shadow" if all(gates.values()) else "rejected_candidate_not_better"
    result = {
        "schema_version": 1, "evaluation_id": evaluation_id, "status": "completed",
        "experiment_type": "matched_baseline_candidate",
        "question": "does labelled-only cross-fitted residual uncertainty improve error ranking",
        "baseline": "random_forest_tree_variance",
        "candidate": "cross_fitted_absolute_residual_random_forest",
        "frozen_conditions": {"official_test": True, "seeds": seeds,
                              "labelled_budget": budget, "prediction_model": "rf_48_leaf3"},
        "test_labels_used_for_training_or_selection": False,
        "test_labels_used_for_posthoc_evaluation_only": True,
        "runs": runs, "mean_metrics": mean_metrics,
        "candidate_wins": wins, "gates": gates, "decision": decision,
        "truth": True, "safety": True, "business_progress": False,
        "rollback": "discard estimator Candidate; raw production path was never changed",
        "promotion": False, "production_mutation": False, "production_effective": False,
        "test_keys_digest": hashlib.sha256("\n".join(test_keys).encode()).hexdigest(),
        "created_at": now_iso(),
    }
    directory = root / "share/mind/governance/active_learning/targetdiff"
    output = directory / f"{evaluation_id}.json"
    atomic_json(output, result)
    event = append_evolution_event(
        str(root), "active_learning/matched_experiment_completed",
        subject_id=evaluation_id, project_id="molecular_generation",
        payload={"decision": decision, "candidate_wins": wins, "gates": gates,
                 "production_effective": False},
        evidence_refs=[str(output)], idempotency_key=f"targetdiff-crossfit:{evaluation_id}",
    )
    return {"ok": True, **result, "path": str(output), "files": [str(output)], "event": event}
