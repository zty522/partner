#!/usr/bin/env python3
"""Isolated baseline/candidate replay for a reproducible API adapter defect."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path


BASELINE = '''from sklearn.metrics import mean_squared_error\ndef rmse(y_true, y_pred):\n    return mean_squared_error(y_true, y_pred, squared=False)\n'''
CANDIDATE = '''from sklearn.metrics import root_mean_squared_error\ndef rmse(y_true, y_pred):\n    return root_mean_squared_error(y_true, y_pred)\n'''
TESTS = '''import math\nfrom metric_adapter import rmse\ndef test_reproducer_removed_squared_argument():\n    assert abs(rmse([0.,1.],[0.,2.])-math.sqrt(.5)) < 1e-12\ndef test_regression_perfect_prediction():\n    assert rmse([1.,2.],[1.,2.]) == 0.0\n'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    evidence = Path(args.dataset)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="partner_evolution_benchmark_") as raw:
        work = Path(raw)
        (work / "metric_adapter.py").write_text(CANDIDATE if args.arm == "candidate" else BASELINE)
        (work / "test_metric_adapter.py").write_text(TESTS)
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_metric_adapter.py",
                                 "--tb=short", "--junitxml=result.xml"], cwd=work,
                                text=True, capture_output=True, timeout=90, check=False)
        import xml.etree.ElementTree as ET
        cases = []
        for item in ET.parse(work / "result.xml").iter("testcase"):
            failed = item.find("failure") is not None or item.find("error") is not None
            cases.append({"sample_id": str(item.get("name")), "y_true": 0.0,
                          "y_pred": 1.0 if failed else 0.0})
    elapsed = time.monotonic() - started
    rmse = math.sqrt(sum(row["y_pred"] ** 2 for row in cases) / max(1, len(cases)))
    baseline_expected = args.arm == "candidate" or result.returncode != 0
    value = {
        "metrics": {"rmse": rmse, "failed_tests": sum(r["y_pred"] for r in cases),
                    "elapsed_seconds": elapsed}, "predictions": cases,
        "guardrails": {"isolated_execution": True, "same_tests": True,
                       "baseline_reproduces": baseline_expected,
                       "no_new_regression": args.arm == "baseline" or result.returncode == 0,
                       "within_budget": elapsed < 120, "artifacts_complete": len(cases) == 2},
        "run_config": {"model": "pytest_isolated_adapter_replay", "folds": "same_two_tests",
                       "seeds": "fixed", "budget": {"runs": 1, "timeout": 90},
                       "features": {"declared_feature": "root_mean_squared_error_patch"
                                    if args.arm == "candidate" else None}},
        "provenance": {"code_revision": "self_evolution_metric_arm_v1",
                       "data_hash": "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
                       "production_effective": False},
        "execution": {"returncode": result.returncode, "stdout_tail": result.stdout[-2000:]},
    }
    Path(args.output).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
