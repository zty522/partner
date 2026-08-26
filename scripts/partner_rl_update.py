#!/usr/bin/env python3
"""Build offline RL trajectories and a non-production candidate policy."""
from __future__ import annotations

import argparse
import json

from partner.governance.external_catalog import build_external_catalog
from partner.governance.rl_evolution import run_offline_rl_update


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--campaign-id", required=True)
    args = parser.parse_args()
    result = run_offline_rl_update(args.workspace, args.campaign_id)
    result["external_catalog"] = build_external_catalog(args.workspace)["path"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
