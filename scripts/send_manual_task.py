#!/usr/bin/env python3
"""Send a manual task to one or all Partner instances.

Each call writes a single inbox entry to one instance's
``state/desktop_inbox.jsonl``. The instance's desktop_inbox poller will
consume it and run the task. The task content is whatever you put in
``--text`` (or via ``--task-file``). Use the same wording the instance
already understands (project, role, expected_artifacts, etc).

Examples
--------
    # Send one task to instance 03 (default = all 5)
    scripts/send_manual_task.py --instance 03 --text "03实例 task: ..."

    # Broadcast a single instruction to all 5 instances
    scripts/send_manual_task.py --all --text "迭代报告 + 给 partner01_xiaohongshu/posts/"

    # Send a longer task from a file
    scripts/send_manual_task.py --instance 02 --task-file /tmp/task.txt
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_WORKSPACE = "/mnt/e/work/partner_workspace"
DEFAULT_INSTANCES = ["01", "02", "03", "04", "05"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _send_one(workspace: str, instance_id: str, text: str) -> str:
    """Append one row to ``instances/<iid>/state/desktop_inbox.jsonl``.

    Returns the row id (so the caller can correlate receipt later).
    """
    inbox = Path(workspace) / "instances" / instance_id / "state" / "desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    row_id = f"manual_{instance_id}_{int(datetime.now().timestamp())}_{hashlib.sha1(text.encode()).hexdigest()[:12]}"
    row = {
        "id": row_id,
        "message_id": row_id,
        "role": "user",
        "text": text,
        "content": text,
        "source": "manual_direct",
        "channel": "desktop",
        "kind": "project",
        "sender_id": "user_zll",
        "sender_name": "用户",
        "created_at": _utc_now(),
    }
    with inbox.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return row_id


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Send a manual task to one or all Partner instances.")
    p.add_argument("--workspace", default=DEFAULT_WORKSPACE,
                   help=f"Partner workspace root (default: {DEFAULT_WORKSPACE})")
    p.add_argument("--instance", choices=DEFAULT_INSTANCES,
                   help="Target instance (e.g. 03). Mutually exclusive with --all.")
    p.add_argument("--all", action="store_true",
                   help="Broadcast to all 5 instances (default if no --instance given).")
    p.add_argument("--text", help="Task text to send.")
    p.add_argument("--task-file", help="Read task text from this file (UTF-8).")
    args = p.parse_args(argv)

    if args.task_file:
        text = Path(args.task_file).read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text.strip()
    else:
        text = sys.stdin.read().strip()
    if not text:
        print("ERROR: empty task text", file=sys.stderr)
        return 2

    if args.instance:
        targets = [args.instance]
    elif args.all:
        targets = list(DEFAULT_INSTANCES)
    else:
        targets = list(DEFAULT_INSTANCES)

    sent = []
    for iid in targets:
        rid = _send_one(args.workspace, iid, text)
        sent.append((iid, rid))
        print(f"  {iid}: {rid}  ({len(text)} chars)")
    print(f"\nSent {len(sent)} inbox row(s) under workspace {args.workspace}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
