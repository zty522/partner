#!/usr/bin/env python3
"""Read the log-file delivery channel: state/outbound/replies.log.

Each line is one delivered reply, written with an fsync and a receipt (byte offset +
sha256) recorded by the send node, so a line in this file is evidence of delivery
rather than a claim about it.

Usage:
  python3 scripts/read_replies.py --workspace /mnt/e/work/partner_workspace [--limit 5]
                                  [--job job_xxx] [--grep TOKEN] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--limit", type=int, default=5, help="how many of the newest lines")
    parser.add_argument("--job", default="", help="only lines for this job_id")
    parser.add_argument("--grep", default="", help="only lines whose content contains this")
    parser.add_argument("--json", action="store_true", help="print raw JSON lines")
    args = parser.parse_args()

    path = Path(args.workspace).resolve() / "state" / "outbound" / "replies.log"
    if not path.is_file():
        print(f"no delivery log yet: {path}")
        return 1
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if args.job and str(row.get("job_id")) != args.job:
            continue
        if args.grep and args.grep not in str(row.get("content") or ""):
            continue
        rows.append(row)
    if args.json:
        for row in rows[-max(0, args.limit):]:
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if not rows:
        print("no matching replies")
        return 1
    for row in rows[-max(0, args.limit):]:
        print(f"--- {row.get('ts')}  instance={row.get('instance')}  job={row.get('job_id')}")
        print(str(row.get("content") or "").strip())
    print(f"\n({len(rows)} matching line(s) in {path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
