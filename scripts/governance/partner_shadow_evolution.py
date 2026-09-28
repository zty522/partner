#!/usr/bin/env python3
"""Run one bounded, non-mutating self-evolution shadow review."""
from __future__ import annotations

import argparse
import json

from partner.governance.shadow_evolution import run_shadow_evolution


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project-id", default="")
    args = parser.parse_args()
    result = run_shadow_evolution(args.workspace, project_id=args.project_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

