#!/usr/bin/env python3
"""Bulk-reduce manual history and run the first matched shadow replay."""
from __future__ import annotations

import argparse
import json

from partner.governance.episode_trace import reduce_manual_history
from partner.governance.shadow_replay import evaluate_preflight_shadow
from partner.governance.strategy_space import write_strategy_catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--instance", default="04")
    parser.add_argument("--project-id", default="literature_github_learning")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    history = reduce_manual_history(args.workspace, instance_id=args.instance,
                                    project_id=args.project_id, limit=args.limit)
    catalog = write_strategy_catalog(args.workspace)
    shadow = evaluate_preflight_shadow(args.workspace, project_id=args.project_id,
                                       experiment_id=args.experiment_id)
    result = {"ok": history.get("ok") and shadow.get("ok"), "history": history,
              "strategy_catalog": {"path": catalog["path"], "count": len(catalog["strategies"])},
              "shadow": shadow}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
