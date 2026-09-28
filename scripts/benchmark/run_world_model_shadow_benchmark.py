#!/usr/bin/env python3
"""Run the accepted three-domain benchmark entirely inside Partner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from partner.cognition.world_model.benchmark import compare_benchmarks, run_shadow_benchmark


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    baseline = run_shadow_benchmark(provider_kind="library", output_dir=args.output_dir)
    candidate = run_shadow_benchmark(provider_kind="transformer", output_dir=args.output_dir)
    comparison = compare_benchmarks(baseline, candidate)
    comparison_path = Path(args.output_dir) / "matched_comparison.json"
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps({"baseline": baseline["metrics"], "candidate": candidate["metrics"],
                      "comparison": comparison}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
