#!/usr/bin/env python3
"""Render the per-protocol report envelope (M3 / 6.4).

This is the real report pipeline:

* reads the latest aggregate ``metrics.json``;
* emits a Markdown report (``report.md``) with the per-group metric
  table, primary / secondary metric separation, and explicit
  ``no_data`` rows where groups have insufficient runs;
* emits a JSON report envelope (``report.json``) that mirrors the
  ``Report`` schema;
* emits a JSON-lines issue list (``issues.jsonl``) aggregating every
  ``Issue`` row recorded across runs in this benchmark workspace;
* emits the data payload for **Figure 2** (per-family metric bar) and
  **Figure 3** (per-method paired-delta CI) as ``figures/figure_2.json``
  and ``figures/figure_3.json``.  The matplotlib / plotly rendering
  is left to the next verification pass; the data contracts are real.

State honesty: ``static_implemented``.
"""
from __future__ import annotations

import argparse
import sys
import csv
import json
from pathlib import Path


def _load_metrics(metrics_path: Path) -> list[dict]:
    if not metrics_path.exists():
        return []
    return json.loads(metrics_path.read_text(encoding="utf-8"))


def _aggregate_issues(runs_root: Path) -> list[dict]:
    out: list[dict] = []
    for run_json in sorted(runs_root.glob("*/run.json")):
        payload = json.loads(run_json.read_text(encoding="utf-8"))
        for issue in payload.get("issues", []) or []:
            out.append(issue)
    return out


def _per_family_table(metrics: list[dict]) -> list[dict]:
    rows = []
    for m in metrics:
        family = (m.get("group_key") or ["", "", "", ""])[3] or "unknown"
        rows.append({
            "fixture_family": family,
            "metric": m.get("metric"),
            "n": m.get("n"),
            "mean": m.get("mean"),
            "ci_lower": m.get("ci_lower"),
            "ci_upper": m.get("ci_upper"),
            "missing_reason": m.get("missing_reason"),
        })
    return rows


def _figure_2_data(metrics: list[dict]) -> dict:
    """Data contract for Figure 2 (per-family metric bar).

    Real plot: ``matplotlib.pyplot.bar(family, mean, yerr=[lo, hi])``
    with one panel per metric.  Emits ``{metric: [{family, mean,
    ci_lower, ci_upper, missing_reason}, ...]}``.
    """
    out: dict[str, list[dict]] = {}
    for row in _per_family_table(metrics):
        out.setdefault(row["metric"] or "unknown", []).append({
            "fixture_family": row["fixture_family"],
            "mean": row["mean"],
            "ci_lower": row["ci_lower"],
            "ci_upper": row["ci_upper"],
            "missing_reason": row["missing_reason"],
        })
    return {"metrics": out}


def _figure_3_data(metrics: list[dict]) -> dict:
    """Data contract for Figure 3 (per-method paired-delta CI)."""
    methods: dict[str, dict] = {}
    for m in metrics:
        method = json.loads(m["group_key"][5]).get("method", "?") if isinstance(m.get("group_key"), list) else "?"
        family = (m.get("group_key") or [""] * 6)[3] or "unknown"
        methods.setdefault(method, {}).setdefault(family, []).append({
            "n": m.get("n"),
            "ci_lower": m.get("ci_lower"),
            "ci_upper": m.get("ci_upper"),
            "missing_reason": m.get("missing_reason"),
        })
    return {"methods": methods}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark-workspace", required=True)
    ap.add_argument("--report-id", required=True)
    ap.add_argument("--paper-figure-refs", default="figure_2,figure_3")
    ap.add_argument("--title", default="Benchmark aggregate report")
    args = ap.parse_args()

    bench_root = Path(args.benchmark_workspace)
    runs_root = bench_root / "runs"
    metrics_path = bench_root / "aggregate" / "metrics.json"
    metrics = _load_metrics(metrics_path)
    if not metrics:
        print(json.dumps({"ok": False, "error": f"missing: {metrics_path}"}))
        return 1

    per_family = _per_family_table(metrics)
    issues = _aggregate_issues(runs_root)

    md_lines = [f"# {args.title}", "",
                f"Report id: `{args.report_id}`",
                f"Generated from `{metrics_path}` (group_count={len(set(json.dumps(m.get('group_key')) for m in metrics))}).",
                "",
                "## Figure 2 — per-family metric table",
                "",
                "| family | metric | n | mean | CI (95%) | missing_reason |",
                "|---|---|---|---|---|---|"]
    for row in per_family:
        ci = (f"{row['ci_lower']:.3f}..{row['ci_upper']:.3f}"
              if row["ci_lower"] is not None and row["ci_upper"] is not None
              else "-")
        mean = f"{row['mean']:.3f}" if row["mean"] is not None else "-"
        md_lines.append(f"| {row['fixture_family']} | {row['metric']} | {row['n']} | {mean} | {ci} | {row['missing_reason'] or '-'} |")

    md_lines += ["", "## Figure 3 — per-method paired-delta CI",
                 "",
                 "(see figures/figure_3.json for the structured payload)",
                 ""]

    md_lines += ["", "## Missing / incomparable groups",
                 ""]
    missing = [m for m in metrics if m.get("missing_reason")]
    if missing:
        for m in missing:
            md_lines.append(f"- {json.dumps(m['group_key'], ensure_ascii=False)[:120]}: {m['missing_reason']}")
    else:
        md_lines.append("(none)")

    md_lines += ["", f"## Issues ({len(issues)})", ""]
    for i in issues:
        md_lines.append(f"- [{i.get('severity', 'info')}/{i.get('category', '?')}] {i.get('summary', '')[:120]}")

    out_dir = bench_root / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.report_id}.md").write_text("\n".join(md_lines), encoding="utf-8")

    figure_2 = _figure_2_data(metrics)
    figure_3 = _figure_3_data(metrics)
    figures_dir = out_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    (figures_dir / "figure_2.json").write_text(json.dumps(figure_2, indent=2), encoding="utf-8")
    (figures_dir / "figure_3.json").write_text(json.dumps(figure_3, indent=2), encoding="utf-8")

    # Figure 1 + 4 data + best-effort PNG render
    try:
        sys.path.insert(0, str(Path("/mnt/e/work/partner")))
        from partner.research.figures.figure_1 import aggregate_family_arm_table
        from partner.research.figures.figure_4 import (
            aggregate_issues_by_method, render_figure_4,
        )
        runs_root = bench_root / "runs"
        figure_1 = {"families": aggregate_family_arm_table(runs_root)}
        issues_by_method = aggregate_issues_by_method(runs_root)
        figure_4 = {"methods": issues_by_method}
        (figures_dir / "figure_1.json").write_text(
            json.dumps(figure_1, indent=2), encoding="utf-8")
        (figures_dir / "figure_4.json").write_text(
            json.dumps(figure_4, indent=2), encoding="utf-8")
        render_figure_4(issues_by_method, figures_dir / "figure_4.png",
                         title=f"{args.title} — Figure 4")
        from partner.research.figures import render_figure_1
        render_figure_1(runs_root, figures_dir / "figure_1.png",
                         title=f"{args.title} — Figure 1")
    except Exception:
        pass

    # Best-effort PNG render: only if matplotlib is installed.  The
    # JSON payload is the canonical record; the PNG is for human reading.
    try:
        sys.path.insert(0, str(Path("/mnt/e/work/partner")))
        from partner.research.figures import render_figure_2, render_figure_3
        render_figure_2(figure_2, figures_dir / "figure_2.png",
                        title=f"{args.title} — Figure 2")
        render_figure_3(figure_3, figures_dir / "figure_3.png",
                        title=f"{args.title} — Figure 3")
    except Exception as exc:
        # Non-fatal: no matplotlib, or figures dir not writable.
        pass

    issues_path = out_dir / f"{args.report_id}.issues.jsonl"
    with issues_path.open("w", encoding="utf-8") as f:
        for i in issues:
            f.write(json.dumps(i, ensure_ascii=False) + "\n")

    report_envelope = {
        "report_id": args.report_id,
        "run_id": metrics[0]["group_key"][0] if metrics else "",
        "title": args.title,
        "sections": ["Figure 2 table", "Figure 3 payload", "Missing groups", "Issues"],
        "metric_table": per_family,
        "paper_figure_refs": [s for s in args.paper_figure_refs.split(",") if s.strip()],
        "missing_or_incomparable": [m.get("missing_reason") for m in metrics if m.get("missing_reason")],
        "schema_version": "benchmark/v1",
        "notes": "Static-implemented; figures/figure_*.json contains data; rendering to PNG/PNG-plot deferred to next pass.",
    }
    (out_dir / f"{args.report_id}.json").write_text(json.dumps(report_envelope, indent=2), encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "out_dir": str(out_dir),
        "report_id": args.report_id,
        "issues": len(issues),
        "figure_2_path": str(figures_dir / "figure_2.json"),
        "figure_3_path": str(figures_dir / "figure_3.json"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
