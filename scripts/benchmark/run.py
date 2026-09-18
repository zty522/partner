#!/usr/bin/env python3
"""Single command to drive a benchmark run end-to-end (M3 / Section 6.3, round 6).

Delegates to ``scripts/benchmark/executor.py`` (single source of truth
for execution).  The default command for any verification pass:

    scripts/benchmark/run.py --run-id X --protocol-id PCI-H1H4-v1 \
        --fixture partner.benchmark.fixtures.<family> \
        --benchmark-workspace /tmp/pci-bench

State honesty: ``static_implemented``.  No real benchmark has run.
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--protocol-id", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--benchmark-workspace", required=True)
    ap.add_argument("--model", default="minimax/MiniMax-M3")
    ap.add_argument("--method-arm", default="candidate")
    ap.add_argument("--method-arm-label", default="candidate")
    ap.add_argument("--parent-run-id", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--wall-timeout-seconds", type=int, default=600)
    ap.add_argument("--wait-timeout-seconds", type=int, default=600)
    args = ap.parse_args()

    argv = [
        "executor.py",
        "--run-id", args.run_id,
        "--protocol-id", args.protocol_id,
        "--fixture", args.fixture,
        "--benchmark-workspace", args.benchmark_workspace,
        "--model", args.model,
        "--method-arm", args.method_arm,
        "--method-arm-label", args.method_arm_label,
    ]
    if args.parent_run_id:
        argv += ["--parent-run-id", args.parent_run_id]
    argv += [
        "--seed", str(args.seed),
        "--wall-timeout-seconds", str(args.wall_timeout_seconds),
        "--wait-timeout-seconds", str(args.wait_timeout_seconds),
    ]
    saved = sys.argv
    sys.argv = argv
    try:
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from executor import main as _executor_main
        return _executor_main()
    finally:
        sys.argv = saved


if __name__ == "__main__":
    raise SystemExit(main())
