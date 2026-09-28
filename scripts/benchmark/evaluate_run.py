#!/usr/bin/env python3
"""Evaluate one completed Partner Job and persist a reviewable JSON result."""
from __future__ import annotations

from pathlib import Path
import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    from partner.web.run_trace import trace_overview
    projection = trace_overview(args.workspace, args.job_id, limit=1,
                                view="business", include_acceptance=False)
    from partner.benchmark.run_acceptance import evaluate_run
    result = evaluate_run(args.workspace, args.job_id, projection=projection)
    output = (Path(args.output) if args.output else
              Path(args.workspace) / "state" / "benchmarks" / "run_acceptance" /
              f"{args.job_id}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**result, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
