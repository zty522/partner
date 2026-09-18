#!/usr/bin/env python3
"""Aggregate per-run ``run.json`` files (M3 / Section 6.4) — round 6.

Two-layer separation enforced:

Layer 1 — ``task_quality``: per-run, per-arm single value produced by
``fixture.score()``.  Aggregated by
``(method_arm, family, model, protocol)`` with mean / stddev / 95 %
CI.

Layer 2 — ``hidden_task_quality_delta``: paired delta
``candidate - baseline`` per ``(family, model, seed, protocol)``,
computed ONLY when both arms have a numeric ``task_quality``.
Missing pairs are recorded as ``missing_reason="pair_unavailable"``
and never silently converted to 0.

State honesty: ``static_implemented``.  No real aggregate has run.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def _confidence_interval(values, z=1.96):
    if len(values) < 2:
        return None
    m = statistics.mean(values)
    s = statistics.stdev(values)
    half = z * s / math.sqrt(len(values))
    return [m - half, m + half]


def _load_runs(bench_root: Path) -> list[dict]:
    runs_dir = bench_root / "runs"
    out = []
    if not runs_dir.exists():
        return out
    for run_json in sorted(runs_dir.glob("*/run.json")):
        try:
            out.append(json.loads(run_json.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _task_quality_for(run: dict) -> tuple[float | None, str | None, str]:
    """Return ``(value, missing_reason, metric_name)`` for the run's
    primary per-arm metric (task_quality).  Returns ``(None, reason, name)``
    if the run has no numeric score.
    """
    for s in (run.get("scores") or []):
        if s.get("metric_name") == "task_quality":
            return s.get("value"), s.get("missing_reason"), s.get("metric_name") or "task_quality"
    # Fallback: first metric with a numeric value
    for s in (run.get("scores") or []):
        if s.get("value") is not None:
            return s.get("value"), s.get("missing_reason"), s.get("metric_name") or "task_quality"
    return None, "no_score", "task_quality"


def _aggregate(runs: list[dict]) -> dict:
    # Layer 1: per-arm task_quality aggregation
    groups = defaultdict(list)            # (method_arm, family, model, protocol) -> [values]
    no_data = []
    paired = defaultdict(dict)            # (family, model, seed, protocol) -> {arm: value}
    per_family = defaultdict(lambda: defaultdict(list))
    issues_by_cat = defaultdict(int)
    issues_by_sev = defaultdict(int)

    for r in runs:
        m = (r.get("manifest") or {})
        arm = m.get("method_arm") or "unknown"
        family = m.get("fixture", "").split(".")[-1] or "unknown"
        model = m.get("model") or "unknown"
        seed = m.get("seed", 0)
        protocol = m.get("protocol_id") or "unknown"
        value, missing_reason, metric_name = _task_quality_for(r)
        if value is None:
            no_data.append({
                "run_id": m.get("run_id"),
                "metric": metric_name,
                "reason": missing_reason or "no_value",
                "execution_status": m.get("execution_status"),
                "evaluation_status": m.get("evaluation_status"),
                "research_outcome": m.get("research_outcome"),
                "final_job_status": m.get("final_job_status"),
            })
        else:
            key = (arm, family, model, protocol)
            groups[key].append(value)
            per_family[family][arm].append(value)
            paired[(family, model, seed, protocol)][arm] = value
        for i in (r.get("issues") or []):
            issues_by_cat[i.get("category") or "unknown"] += 1
            issues_by_sev[i.get("severity") or "unknown"] += 1

    # Layer 1 summary
    group_summary = []
    for key, values in sorted(groups.items()):
        arm, family, model, protocol = key
        group_summary.append({
            "metric_name": "task_quality",
            "method_arm": arm, "family": family,
            "model": model, "protocol": protocol,
            "n": len(values),
            "mean": statistics.mean(values),
            "stdev": statistics.stdev(values) if len(values) >= 2 else None,
            "ci95": _confidence_interval(values),
        })

    # Layer 2 paired delta (hidden_task_quality_delta)
    paired_deltas = []
    for key, by_arm in sorted(paired.items()):
        family, model, seed, protocol = key
        baseline_v = by_arm.get("baseline")
        candidate_v = by_arm.get("candidate")
        if baseline_v is not None and candidate_v is not None:
            delta = candidate_v - baseline_v
            paired_deltas.append({
                "metric_name": "hidden_task_quality_delta",
                "family": family, "model": model, "seed": seed,
                "protocol": protocol,
                "baseline": baseline_v,
                "candidate": candidate_v,
                "delta": delta,
                "missing_reason": None,
            })
        else:
            paired_deltas.append({
                "metric_name": "hidden_task_quality_delta",
                "family": family, "model": model, "seed": seed,
                "protocol": protocol,
                "baseline": baseline_v,
                "candidate": candidate_v,
                "delta": None,
                "missing_reason": "pair_unavailable",
            })

    family_summary = {}
    for family, by_arm in sorted(per_family.items()):
        family_summary[family] = {}
        for arm, values in sorted(by_arm.items()):
            family_summary[family][arm] = {
                "metric_name": "task_quality",
                "n": len(values),
                "mean": statistics.mean(values) if values else None,
                "stdev": statistics.stdev(values) if len(values) >= 2 else None,
                "ci95": _confidence_interval(values),
            }

    return {
        "summary": {
            "n_runs": len(runs),
            "n_no_data": len(no_data),
            "n_paired": sum(1 for d in paired_deltas
                             if d["missing_reason"] is None),
            "n_pair_unavailable": sum(1 for d in paired_deltas
                                       if d["missing_reason"] == "pair_unavailable"),
        },
        "issues": {
            "by_category": dict(issues_by_cat),
            "by_severity": dict(issues_by_sev),
        },
        "no_data": no_data,
        "groups": group_summary,
        "paired_deltas": paired_deltas,
        "per_family": family_summary,
    }


def _emit_csvs(agg, bench_root: Path) -> None:
    metrics_dir = bench_root / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    with (metrics_dir / "per_group.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["metric_name", "method_arm", "family",
                                            "model", "protocol", "n", "mean",
                                            "stdev", "ci95_low", "ci95_high"])
        w.writeheader()
        for row in agg["groups"]:
            ci = row.get("ci95") or [None, None]
            w.writerow({**row, "ci95_low": ci[0], "ci95_high": ci[1]})
    with (metrics_dir / "paired_deltas.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["metric_name", "family", "model",
                                            "seed", "protocol", "baseline",
                                            "candidate", "delta",
                                            "missing_reason"])
        w.writeheader()
        for row in agg["paired_deltas"]:
            w.writerow(row)
    with (metrics_dir / "no_data.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["run_id", "metric", "reason",
                                            "execution_status", "evaluation_status",
                                            "research_outcome", "final_job_status"])
        w.writeheader()
        for row in agg["no_data"]:
            w.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark-workspace", required=True)
    args = ap.parse_args()
    bench_root = Path(args.benchmark_workspace).expanduser().resolve()
    runs = _load_runs(bench_root)
    agg = _aggregate(runs)
    (bench_root / "metrics.json").write_text(
        json.dumps(agg, indent=2, ensure_ascii=False), encoding="utf-8")
    _emit_csvs(agg, bench_root)
    print(json.dumps({"ok": True, **agg["summary"],
                       "metrics_path": str(bench_root / "metrics.json")},
                      ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
