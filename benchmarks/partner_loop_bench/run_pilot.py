#!/usr/bin/env python3
"""Run the frozen three-domain Partner-LoopBench pilot through Event/Flow."""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from partner.benchmark.wrapper import PartnerBenchmarkWrapper


DEFAULT_TASKS = ("project_01", "learning_01", "evolution_01")


def run(workspace: Path, task_ids: tuple[str, ...]) -> dict:
    here = Path(__file__).resolve().parent
    wrapper = PartnerBenchmarkWrapper(workspace)
    rows = []
    for task_id in task_ids:
        task_path = here / "tasks" / f"{task_id}.json"
        submitted = wrapper.submit(
            protocol_id="partner_loop_full_vs_single_v1",
            request=f"/benchmark partner_loop_full_vs_single_v1\nblind_task={task_id}",
            instance_id="01", project_id="partner_core_v1_1",
            inputs={"task_path": str(task_path),
                    "arm_runner_path": str(here / "sealed_runner.py"),
                    "task_id": task_id, "benchmark_seed": 20260924},
            allow_external_judges=False)
        terminal = wrapper.wait(submitted, timeout_seconds=900)
        result = wrapper.result(submitted.benchmark_run_id)
        report = result.get("report") or {}
        comparison = report.get("comparison") or {}
        diagnostics = {}
        for arm in ("baseline", "candidate"):
            child_path = Path(result["directory"]) / "arms" / arm / "child_flow.json"
            child = json.loads(child_path.read_text(encoding="utf-8")) if child_path.is_file() else {}
            outputs = child.get("node_outputs") or {}
            usages = [row.get("token_usage") for row in outputs.values()
                      if isinstance(row, dict) and isinstance(row.get("token_usage"), dict)]
            diagnostics[arm] = {"terminal_events": len(outputs), "model_calls": len(usages),
                                "total_tokens": sum(int(u.get("total_tokens") or 0) for u in usages),
                                "checkpoint_count": len(child.get("checkpoints") or [])}
        rows.append({"task_id": task_id, "job_id": submitted.job_id,
                     "benchmark_run_id": submitted.benchmark_run_id,
                     "job_status": terminal.get("status"),
                     "settlement": (result.get("settlement") or {}).get("decision"),
                     "baseline": comparison.get("baseline_value"),
                     "candidate": comparison.get("candidate_value"),
                     "effect": comparison.get("effect"), "diagnostics": diagnostics,
                     "run_directory": result.get("directory")})
    effects = [float(row["effect"]) for row in rows if isinstance(row.get("effect"), (int, float))]
    complete = [row for row in rows if isinstance(row.get("baseline"), (int, float))
                and isinstance(row.get("candidate"), (int, float))]
    baseline_success = mean(float(row["baseline"]) for row in complete) if complete else None
    candidate_success = mean(float(row["candidate"]) for row in complete) if complete else None
    uplift = mean(effects) if effects else None
    false_evolution_rate = (sum(float(row["candidate"]) < float(row["baseline"])
                                for row in complete) / len(complete)) if complete else None
    aggregate_decision = ("supported" if isinstance(uplift, float) and uplift >= .01 else
                          "falsified" if isinstance(uplift, float) and uplift <= 0 else
                          "inconclusive")
    output = {"schema_version": 1, "benchmark": "Partner-LoopBench pilot",
              "protocol_id": "partner_loop_full_vs_single_v1",
              "task_count": len(rows), "all_terminal": all(row["job_status"] == "completed" for row in rows),
              "baseline_success_rate": baseline_success,
              "full_partner_success_rate": candidate_success,
              "mean_autonomous_uplift": uplift,
              "false_evolution_rate": false_evolution_rate,
              "event_efficiency": {arm: {
                  "terminal_events": sum(row.get("diagnostics", {}).get(arm, {}).get("terminal_events", 0) for row in rows),
                  "model_calls": sum(row.get("diagnostics", {}).get(arm, {}).get("model_calls", 0) for row in rows),
                  "total_tokens": sum(row.get("diagnostics", {}).get(arm, {}).get("total_tokens", 0) for row in rows)}
                  for arm in ("baseline", "candidate")},
              "predeclared_minimum_uplift": 0.01,
              "aggregate_decision": aggregate_decision,
              "rows": rows, "completed_at": datetime.now(timezone.utc).isoformat()}
    directory = workspace / "state/benchmarks/partner_loop_bench"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"pilot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        xs = list(range(len(rows)))
        ax.plot(xs, [row.get("baseline") for row in rows], "o-", label="single-turn LLM")
        ax.plot(xs, [row.get("candidate") for row in rows], "s-", label="full Partner loop")
        ax.set_xticks(xs, [row["task_id"] for row in rows], rotation=12, ha="right")
        ax.set_ylim(-.05, 1.05); ax.set_ylabel("Task success"); ax.grid(axis="y", alpha=.2)
        ax.set_title(f"Partner-LoopBench pilot: uplift={uplift}")
        ax.legend(); fig.tight_layout()
        figure = path.with_suffix(".png"); fig.savefig(figure, dpi=180); plt.close(fig)
        output["figure_path"] = str(figure)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        output["figure_error"] = f"{type(exc).__name__}: {exc}"
    output["result_path"] = str(path)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--tasks", nargs="*", default=list(DEFAULT_TASKS))
    args = parser.parse_args()
    print(json.dumps(run(Path(args.workspace).resolve(), tuple(args.tasks)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
