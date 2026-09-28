#!/usr/bin/env python3
"""Reduce a persisted Partner task into an Episode Trace v3 bundle."""
from __future__ import annotations

import argparse
import json

from partner.governance.episode_trace import reduce_task_episode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--instance", required=True, choices=["01", "02", "03", "04", "05"])
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    result = reduce_task_episode(args.workspace, instance_id=args.instance, task_id=args.task_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

