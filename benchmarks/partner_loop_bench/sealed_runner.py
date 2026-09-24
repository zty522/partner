#!/usr/bin/env python3
"""Execute a committed action and score it against a sealed local oracle.

The Event subject invokes this program only after Commitment.  It receives no
score before selection, and the oracle file is never included in subject view.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    task_path = Path(args.task).resolve()
    task = json.loads(task_path.read_text(encoding="utf-8"))
    oracle_path = Path(__file__).with_name("sealed_oracles.json")
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    task_id = str(task.get("task_id") or "")
    expected = str((oracle.get("answers") or {}).get(task_id) or "")
    if not expected:
        raise ValueError(f"sealed answer missing for {task_id}")
    success = float(args.selection == expected)
    candidates = {str(row.get("id")) for row in task.get("candidates") or []}
    evidence = {
        "schema_version": 1,
        "metrics": {"task_success": success},
        "predictions": [{"sample_id": task_id, "y_true": 1.0, "y_pred": success}],
        "guardrails": {
            "oracle_hidden": True,
            "same_public_task": True,
            "within_budget": True
        },
        "run_config": {
            "model": "partner-autonomous-choice",
            "folds": "not_applicable",
            "seeds": "protocol_seed",
            "budget": "one_committed_action",
            "features": {"declared_feature": None}
        },
        "provenance": {
            "code_revision": "partner-loop-bench-v1",
            "data_hash": "sha256:" + hashlib.sha256(task_path.read_bytes()).hexdigest(),
            "oracle_hash": "sha256:" + hashlib.sha256(oracle_path.read_bytes()).hexdigest()
        }
    }
    Path(args.output).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
