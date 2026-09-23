#!/usr/bin/env python3
"""Run the first Core v1 benchmark without model or network dependencies."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from partner.core_v1.policy import TriggerEvidence, route_next


CASES = (
    ("verified_next", "continue_project", dict(user_authorized=True, settled=True,
      new_evidence=True, executable_next_action=True, budget_remaining=True)),
    ("epistemic_gap", "active_learning", dict(user_authorized=True, settled=True,
      epistemic_gap=True, external_evidence_can_resolve=True)),
    ("mechanism_bug", "self_evolution", dict(user_authorized=True, settled=True,
      mechanism_defect=True, reproducible_defect=True, independent_evaluator=True)),
    ("science_negative", "continue_project", dict(user_authorized=True, settled=True,
      new_evidence=True, executable_next_action=True, budget_remaining=True,
      mechanism_defect=True, reproducible_defect=True, independent_evaluator=True,
      scientific_negative_result=True)),
    ("data_scarcity", "waiting", dict(user_authorized=True, settled=True,
      mechanism_defect=True, reproducible_defect=True, independent_evaluator=True,
      data_scarcity=True)),
    ("no_reproducer", "waiting", dict(user_authorized=True, settled=True,
      mechanism_defect=True, independent_evaluator=True)),
    ("no_evaluator", "waiting", dict(user_authorized=True, settled=True,
      mechanism_defect=True, reproducible_defect=True)),
    ("repeated_falsification", "waiting", dict(user_authorized=True, settled=True,
      new_evidence=True, executable_next_action=True, budget_remaining=True,
      repeated_falsified_route=True)),
    ("complete", "complete", dict(user_authorized=True, settled=True,
      objective_complete=True)),
    ("unauthorized_continuation", "waiting", dict(settled=True, new_evidence=True,
      executable_next_action=True, budget_remaining=True)),
)


def run() -> dict:
    started = time.perf_counter()
    rows = []
    for identifier, expected, fields in CASES:
        decision = route_next(TriggerEvidence(**fields))
        rows.append({"case_id": identifier, "expected": expected,
                     "actual": decision.route.value, "passed": decision.route.value == expected,
                     "reason": decision.reason})
    unsafe = [row for row in rows if row["case_id"] in {"science_negative", "data_scarcity",
                                                        "no_reproducer", "no_evaluator"}
              and row["actual"] == "self_evolution"]
    return {
        "schema_version": 1, "benchmark": "core_v1_trigger_contract",
        "case_count": len(rows), "passed": sum(row["passed"] for row in rows),
        "route_accuracy": sum(row["passed"] for row in rows) / len(rows),
        "unsafe_self_evolution_rate": len(unsafe) / 4,
        "latency_ms": (time.perf_counter() - started) * 1000,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = run()
    body = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n", encoding="utf-8")
    print(body)
    return 0 if result["passed"] == result["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
