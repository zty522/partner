#!/usr/bin/env python3
"""Run warmup→verified-memory→transfer episodes through Event/Flow."""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from partner.benchmark.wrapper import PartnerBenchmarkWrapper


PAIRS = (("warm_project_01", "transfer_project_02"),
         ("warm_learning_01", "transfer_learning_02"),
         ("warm_evolution_01", "transfer_evolution_02"))


def _run_one(wrapper, here: Path, *, protocol: str, task_id: str,
             memory_path: Path | None = None) -> dict:
    inputs = {"task_path": str(here / "tasks" / f"{task_id}.json"),
              "arm_runner_path": str(here / "real_runner.py"),
              "task_id": task_id, "benchmark_seed": 20260924}
    if memory_path is not None:
        inputs["arm_input_overrides"] = {"candidate": {"memory_path": str(memory_path)}}
    submitted = wrapper.submit(protocol_id=protocol,
        request=f"/benchmark {protocol}\nlongitudinal_task={task_id}", instance_id="01",
        project_id="partner_core_v1_1_longitudinal", inputs=inputs,
        allow_external_judges=False)
    terminal = wrapper.wait(submitted, timeout_seconds=900)
    result = wrapper.result(submitted.benchmark_run_id)
    comparison = (result.get("report") or {}).get("comparison") or {}
    return {"task_id": task_id, "job_id": submitted.job_id,
            "benchmark_run_id": submitted.benchmark_run_id,
            "job_status": terminal.get("status"),
            "settlement": (result.get("settlement") or {}).get("decision"),
            "baseline": comparison.get("baseline_value"),
            "candidate": comparison.get("candidate_value"),
            "effect": comparison.get("effect"), "run_directory": result.get("directory")}


def run(workspace: Path) -> dict:
    here = Path(__file__).resolve().parent
    wrapper = PartnerBenchmarkWrapper(workspace)
    oracle = json.loads((here / "sealed_oracles.json").read_text(encoding="utf-8"))
    lessons = dict(oracle.get("transfer_lessons") or {})
    directory = workspace / "state/benchmarks/partner_loop_bench/longitudinal"
    directory.mkdir(parents=True, exist_ok=True)
    memory_path = directory / f"verified_memory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    memory = {"schema_version": 1, "lessons": []}
    warmups, transfers = [], []
    for warm_id, transfer_id in PAIRS:
        warm = _run_one(wrapper, here, protocol="partner_loop_full_vs_single_v1", task_id=warm_id)
        warmups.append(warm)
        report_path = Path(warm["run_directory"]) / "report.json"
        verified = (warm["job_status"] == "completed" and warm["baseline"] == 1.0
                    and warm["candidate"] == 1.0 and report_path.is_file())
        if not verified:
            raise RuntimeError(f"warmup did not produce verified evidence: {warm_id}")
        memory["lessons"].append({"lesson_id": f"lesson_{warm_id}",
            "content": lessons[warm_id], "verified": True,
            "source_settlement": f"{warm['benchmark_run_id']}:both_arms_task_success=1",
            "source_artifact": str(report_path),
            "source_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "recorded_at": datetime.now(timezone.utc).isoformat()})
        memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        transfers.append(_run_one(wrapper, here, protocol="partner_loop_memory_vs_none_v1",
                                  task_id=transfer_id, memory_path=memory_path))
    valid = [row for row in transfers if isinstance(row.get("effect"), (int, float))]
    effect = mean(float(row["effect"]) for row in valid) if valid else None
    result = {"schema_version": 1, "benchmark": "Partner-LoopBench longitudinal transfer",
              "warmups": warmups, "transfers": transfers, "memory_path": str(memory_path),
              "all_terminal": all(row["job_status"] == "completed" for row in warmups + transfers),
              "no_memory_success_rate": mean(float(row["baseline"]) for row in valid) if valid else None,
              "verified_memory_success_rate": mean(float(row["candidate"]) for row in valid) if valid else None,
              "learning_to_action_gain": effect,
              "aggregate_decision": "supported" if isinstance(effect, float) and effect >= .01 else
                                    "falsified" if isinstance(effect, float) and effect <= 0 else "inconclusive",
              "completed_at": datetime.now(timezone.utc).isoformat()}
    output = directory / f"longitudinal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        x = list(range(len(transfers)))
        fig, ax = plt.subplots(figsize=(7.4, 4.4))
        ax.plot(x, [r["baseline"] for r in transfers], "o-", label="no verified memory")
        ax.plot(x, [r["candidate"] for r in transfers], "s-", label="verified memory")
        ax.set_xticks(x, [r["task_id"] for r in transfers], rotation=12, ha="right")
        ax.set_ylim(-.05, 1.05); ax.set_ylabel("Task success"); ax.grid(axis="y", alpha=.2)
        ax.set_title(f"Longitudinal learning-to-action gain={effect}"); ax.legend(); fig.tight_layout()
        figure = output.with_suffix(".png"); fig.savefig(figure, dpi=180); plt.close(fig)
        result["figure_path"] = str(figure)
    except Exception as exc:
        result["figure_error"] = f"{type(exc).__name__}: {exc}"
    result["result_path"] = str(output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    args = parser.parse_args()
    print(json.dumps(run(Path(args.workspace).resolve()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
