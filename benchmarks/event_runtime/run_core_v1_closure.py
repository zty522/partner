#!/usr/bin/env python3
"""One command for project, active-learning and self-evolution benchmarks."""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from partner.benchmark.suite import BenchmarkSuiteCase, PartnerBenchmarkSuite
from partner.benchmark.wrapper import PartnerBenchmarkWrapper


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--learning-handoff", required=True)
    parser.add_argument("--project-instance", default="01")
    parser.add_argument("--learning-instance", default="02")
    parser.add_argument("--seeds", default="17,29")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    seeds = [int(v) for v in args.seeds.split(",") if v.strip()]
    tasks = ("target_basic", "target_kmer32", "target_combined")
    project = PartnerBenchmarkSuite(workspace).run(
        protocol_id="pk_target_feature_v1", instance_id=args.project_instance,
        project_id="molecular_generation", dataset_path=args.dataset,
        arm_runner_path=Path(__file__).with_name("davis_target_feature_arm.py"),
        cases=[BenchmarkSuiteCase(task, task, seed) for task in tasks for seed in seeds])
    wrapper = PartnerBenchmarkWrapper(workspace)
    closures = {}
    for name, protocol, feature, runner in (
        ("active_learning", "active_learning_api_adoption_v1", "root_mean_squared_error",
         "active_learning_api_arm.py"),
        ("self_evolution", "self_evolution_metric_repair_v1", "root_mean_squared_error_patch",
         "self_evolution_metric_arm.py"),
    ):
        submitted = wrapper.submit(
            protocol_id=protocol, request=f"/benchmark {protocol}\nCore v1 closure",
            instance_id=args.learning_instance, project_id="partner_core_v1",
            inputs={"dataset_path": str(Path(args.learning_handoff).resolve()),
                    "declared_feature": feature,
                    "arm_runner_path": str(Path(__file__).with_name(runner).resolve())})
        terminal = wrapper.wait(submitted)
        result = wrapper.result(submitted.benchmark_run_id)
        closures[name] = {"job_id": submitted.job_id, "run_id": submitted.benchmark_run_id,
                          "status": terminal.get("status"),
                          "settlement": result.get("settlement"),
                          "directory": result.get("directory")}
    value = {"schema_version": 1, "project": project, **closures,
             "all_confirmed": bool(project.get("all_runs_valid") and
                 all(row.get("status") == "completed" and
                     (row.get("settlement") or {}).get("decision") == "confirmed"
                     for row in closures.values())),
             "completed_at": datetime.now(timezone.utc).isoformat()}
    directory = workspace / "state/benchmarks/closures"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("closure_" + value["completed_at"].replace(":", "-") + ".json")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**value, "artifact": str(path)}, ensure_ascii=False, indent=2))
    return 0 if value["all_confirmed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
