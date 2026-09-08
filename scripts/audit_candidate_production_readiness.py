#!/usr/bin/env python3
"""Audit a learned Candidate without mutating production."""
from __future__ import annotations

import argparse
import json

from partner.governance.production_readiness import assess_production_readiness


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--llm-experiment", action="append", default=[])
    parser.add_argument("--rollback-drill-passed", action="store_true")
    parser.add_argument("--single-authorized-model", action="store_true")
    args = parser.parse_args()
    result = assess_production_readiness(
        args.workspace, candidate_id=args.candidate_id,
        llm_experiment_paths=list(args.llm_experiment),
        rollback_drill_passed=args.rollback_drill_passed,
        single_authorized_model=args.single_authorized_model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["production_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
