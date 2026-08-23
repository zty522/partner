#!/usr/bin/env python3
"""Render a single-page Partner health dashboard from CLI.

Reads systemd, heartbeat, ProjectState, IterationReceipt and pytest summary
files and prints either a fixed-column plain-text panel or JSON to stdout.

Designed to be safe for cost-constrained environments: no LLM calls, no
filesystem writes, deterministic output.

Usage:
    python scripts/partner_status.py            # plain-text panel (default)
    python scripts/partner_status.py --json     # machine-readable JSON
    python scripts/partner_status.py --active-only
    python scripts/partner_status.py --workspace /path/to/workspace
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.monitoring.partner_dashboard import render_text, snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace",
                        help="Workspace root (default: /mnt/e/work/partner_workspace)")
    parser.add_argument("--code-root", default="/mnt/e/work/partner",
                        help="Code root (default: /mnt/e/work/partner)")
    parser.add_argument("--pytest-summary",
                        default="/mnt/e/work/partner/docs/testing/last_pytest.txt",
                        help="Optional pytest summary log (default: docs/testing/last_pytest.txt)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of plain text")
    parser.add_argument("--active-only", action="store_true",
                        help="Hide inactive instances")
    args = parser.parse_args()

    data = snapshot(
        workspace_root=args.workspace,
        code_root=args.code_root,
        pytest_summary_path=args.pytest_summary,
        include_inactive=not args.active_only,
    )

    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
