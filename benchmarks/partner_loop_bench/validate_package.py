#!/usr/bin/env python3
"""Validate public/sealed separation and task/oracle coverage."""
from __future__ import annotations
import json
from pathlib import Path


FORBIDDEN_PUBLIC_KEYS = {"answer", "correct", "oracle", "score", "label"}


def validate(directory: str | Path) -> dict:
    root = Path(directory).resolve()
    oracle_path = root / "sealed_oracles.json"
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    answers = dict(oracle.get("answers") or {})
    errors = []
    task_ids = []
    domains = {}
    for path in sorted((root / "tasks").glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        task_id = str(task.get("task_id") or "")
        task_ids.append(task_id)
        domains[str(task.get("domain") or "unknown")] = domains.get(str(task.get("domain") or "unknown"), 0) + 1
        def keys(value):
            if isinstance(value, dict):
                return ({str(key).lower() for key in value} |
                        set().union(*(keys(item) for item in value.values()), set()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value), set())
            return set()
        leaked = FORBIDDEN_PUBLIC_KEYS & keys(task)
        if leaked: errors.append(f"{task_id}:leaked_keys:{sorted(leaked)}")
        ids = [str(row.get("id") or "") for row in task.get("candidates") or []]
        if len(ids) < 2 or len(ids) != len(set(ids)): errors.append(f"{task_id}:invalid_candidates")
        if answers.get(task_id) not in ids: errors.append(f"{task_id}:oracle_not_in_candidates")
    for orphan in sorted(set(answers) - set(task_ids)):
        errors.append(f"{orphan}:orphan_oracle")
    return {"valid": not errors, "task_count": len(task_ids), "domains": domains,
            "errors": errors, "public_task_ids": task_ids}


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    print(json.dumps(validate(here), ensure_ascii=False, indent=2))
