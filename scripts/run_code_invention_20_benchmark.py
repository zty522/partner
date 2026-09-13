#!/usr/bin/env python3
"""Resume the 20-case hidden-test shadow code-invention benchmark."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from partner.evolution.code_invention_lab import run_shadow_code_invention


ISSUES = [
    "Retry status normalization rejects transient values with harmless case or whitespace differences.",
    "Numeric clamp returns the wrong boundary for values outside an ordered inclusive interval.",
    "Deduplication loses the first-seen ordering contract.",
    "Boolean configuration strings are interpreted by Python truthiness instead of strict parsing.",
    "Exponential retry delay violates its maximum cap.",
    "Host allowlisting accepts attacker-controlled suffix lookalikes.",
    "Bearer credentials remain visible in diagnostic text.",
    "Chunking drops a final partial chunk and does not reject a non-positive size.",
    "Dotted numeric versions use lexicographic components and sort 2.10 before 2.9.",
    "Configuration merge gives base values precedence over explicit overrides.",
    "Timeout normalization admits negative and non-finite values.",
    "Top-k returns ascending output and mishandles zero or oversized k.",
    "Mean calculation raises on an empty observation set.",
    "Filesystem containment uses a vulnerable string-prefix comparison.",
    "Status aliases are case-sensitive and unknown states leak through unclassified.",
    "Retry-After parsing returns non-positive values and raises on malformed headers.",
    "Idempotency components retain whitespace, empty segments, and unstable case.",
    "Confidence normalization does not reject NaN or infinity.",
    "Extension allowlisting is case- and leading-dot-sensitive.",
    "Pagination continues after a short terminal page.",
]


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--repo", default="benchmarks/code_invention_20/repo")
    parser.add_argument("--target-count", type=int, default=20)
    parser.add_argument("--retry-rejected", action="store_true",
                        help="Run one new Candidate for prior rejected cases; preserve old attempts")
    args = parser.parse_args()
    target_count = max(1, min(20, args.target_count))
    repo = Path(args.repo).resolve()
    root = Path(args.workspace).resolve()
    report_path = root / "state/code_invention_20_benchmark.json"
    prior = _load(report_path)
    attempts = list(prior.get("attempts") or prior.get("cases") or [])
    completed = {int(row["case"]): row for row in attempts
                 if isinstance(row, dict) and row.get("terminal")}

    for case in range(1, target_count + 1):
        if (case in completed and (not args.retry_rejected
                                   or completed[case].get("decision") == "validated_shadow")):
            continue
        result = run_shadow_code_invention(
            root, repo, issue={"benchmark_case": case, "summary": ISSUES[case - 1]},
            target_files=["partner/core/tasks.py"],
            reproducer_tests=[f"tests/test_public.py::test_{case:02d}"],
            hidden_tests=[f"tests/test_hidden.py::test_{case:02d}"],
        )
        row = {
            "case": case,
            "attempt": 1 + sum(int(item.get("case") or 0) == case for item in attempts
                               if isinstance(item, dict)),
            "terminal": True, "candidate_id": result.get("candidate_id"),
            "decision": result.get("decision"), "reason": result.get("reason"),
            "baseline_failed": (result.get("baseline") or {}).get("exit_code") != 0,
            "candidate_passed": (result.get("candidate_test") or {}).get("exit_code") == 0,
            "hidden_passed": (result.get("hidden_test") or {}).get("exit_code") == 0,
            "production_effective": False,
        }
        attempts.append(row)
        completed[case] = row
        cases = [completed[key] for key in sorted(completed)]
        validated = sum(row.get("decision") == "validated_shadow" for row in cases)
        report = {"schema_version": 1, "benchmark": "code_invention_20_hidden",
                  "target_count": target_count, "completed_count": len(cases),
                  "validated_count": validated, "cases": cases, "attempts": attempts,
                  "passed": len(cases) >= target_count and validated >= 15,
                  "production_effective": False}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(json.dumps(row, ensure_ascii=False), flush=True)

    final = _load(report_path)
    signal = "PASS" if final.get("passed") else "INSUFFICIENT"
    print(json.dumps({"completion_signal": signal, "completed": final.get("completed_count"),
                      "validated": final.get("validated_count"),
                      "report_path": str(report_path)}, ensure_ascii=False), flush=True)
    return 0 if final.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
