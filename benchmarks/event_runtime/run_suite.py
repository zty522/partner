#!/usr/bin/env python3
"""Run the Davis multi-task/multi-seed suite through Partner Event Flows."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from partner.benchmark.suite import BenchmarkSuiteCase, PartnerBenchmarkSuite


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--instance", default="01")
    parser.add_argument("--seeds", default="17,29,43")
    parser.add_argument("--reuse-run", action="append", default=[],
                        metavar="TASK:SEED:JOB_ID:RUN_ID")
    args = parser.parse_args()
    seeds = [int(v) for v in args.seeds.split(",") if v.strip()]
    tasks = ("target_basic", "target_kmer32", "target_combined")
    cases = [BenchmarkSuiteCase(task, task, seed) for task in tasks for seed in seeds]
    reused = {}
    for raw in args.reuse_run:
        task, seed, job_id, run_id = raw.split(":", 3)
        reused[(task, int(seed))] = {"job_id": job_id, "run_id": run_id}
    result = PartnerBenchmarkSuite(args.workspace).run(
        protocol_id="pk_target_feature_v1", instance_id=args.instance,
        project_id="molecular_generation", dataset_path=args.dataset,
        arm_runner_path=Path(__file__).with_name("davis_target_feature_arm.py"), cases=cases,
        existing_runs=reused)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_runs_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
