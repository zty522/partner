#!/usr/bin/env python3
"""Negative control: verified but irrelevant memory must not create uplift."""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from partner.benchmark.wrapper import PartnerBenchmarkWrapper
from run_longitudinal import _run_one


ROTATION = (("transfer_project_02", "warm_evolution_01"),
            ("transfer_learning_02", "warm_project_01"),
            ("transfer_evolution_02", "warm_learning_01"))


def run(workspace: Path, longitudinal_result: Path) -> dict:
    here = Path(__file__).resolve().parent
    source = json.loads(longitudinal_result.read_text(encoding="utf-8"))
    warmups = {row["task_id"]: row for row in source.get("warmups") or []}
    lessons = json.loads((here / "sealed_oracles.json").read_text(encoding="utf-8"))["transfer_lessons"]
    wrapper = PartnerBenchmarkWrapper(workspace)
    directory = workspace / "state/benchmarks/partner_loop_bench/longitudinal"
    rows = []
    for transfer_id, irrelevant_warm_id in ROTATION:
        warm = warmups[irrelevant_warm_id]
        report = Path(warm["run_directory"]) / "report.json"
        memory_path = directory / f"irrelevant_{transfer_id}_{datetime.now().strftime('%H%M%S%f')}.json"
        memory_path.write_text(json.dumps({"schema_version": 1, "lessons": [{
            "lesson_id": f"irrelevant_{irrelevant_warm_id}", "content": lessons[irrelevant_warm_id],
            "verified": True, "source_settlement": f"{warm['benchmark_run_id']}:both_arms_task_success=1",
            "source_artifact": str(report), "source_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "recorded_at": datetime.now(timezone.utc).isoformat()}]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        row = _run_one(wrapper, here, protocol="partner_loop_memory_vs_none_v1",
                       task_id=transfer_id, memory_path=memory_path)
        row["irrelevant_memory_source"] = irrelevant_warm_id
        rows.append(row)
    effects = [float(row["effect"]) for row in rows if isinstance(row.get("effect"), (int, float))]
    effect = mean(effects) if effects else None
    result = {"schema_version": 1, "benchmark": "verified-memory specificity negative control",
              "source_longitudinal_result": str(longitudinal_result), "rows": rows,
              "mean_irrelevant_memory_effect": effect,
              "specificity_pass": isinstance(effect, float) and effect <= 0,
              "all_terminal": all(row["job_status"] == "completed" for row in rows),
              "completed_at": datetime.now(timezone.utc).isoformat()}
    output = directory / f"memory_specificity_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    result["result_path"] = str(output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--longitudinal-result", default=(
        "/mnt/e/work/partner_workspace/state/benchmarks/partner_loop_bench/longitudinal/"
        "longitudinal_20260924_175920.json"))
    args = parser.parse_args()
    print(json.dumps(run(Path(args.workspace).resolve(), Path(args.longitudinal_result).resolve()),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
