#!/usr/bin/env python3
"""Inject one real local USER_MESSAGE and optionally wait for its TaskInstance."""
from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_id", choices=["01", "02", "03", "04", "05"])
    parser.add_argument("--workspace-root", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--text", required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    workspace = Path(args.workspace_root) / "instances" / args.instance_id
    state = workspace / "state"
    state.mkdir(parents=True, exist_ok=True)
    before = {path.name for path in (state / "tasks").glob("*")} if (state / "tasks").exists() else set()
    message_id = f"local_{args.instance_id}_{uuid.uuid4().hex[:12]}"
    row = {
        "id": message_id,
        "message_id": message_id,
        "text": args.text[:4000],
        "display_text": args.text[:4000],
        "source": "local_canary",
        "channel": "local",
        "sender_id": "local_canary",
        "sender_name": "本地验收",
        "attachments": [],
        "created_at": datetime.now().isoformat(),
    }
    with (state / "desktop_inbox.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
    print(json.dumps({"status": "injected", "message_id": message_id, "instance_id": args.instance_id}, ensure_ascii=False))
    if args.timeout <= 0:
        return 0
    deadline = time.time() + args.timeout
    task_path = None
    while time.time() < deadline:
        candidates = [path for path in (state / "tasks").glob("*") if path.name not in before]
        if candidates:
            task_path = max(candidates, key=lambda value: value.stat().st_mtime)
            task_state = task_path / "task_instance.json"
            if task_state.exists():
                try:
                    value = json.loads(task_state.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    value = {}
                status = str(value.get("completion_status") or "")
                if status in {"done", "failed"}:
                    # Harness writes an intermediate terminal state before the
                    # manual stop/Receipt gate finalizes the task. Wait for
                    # that governance result and then reload; otherwise a
                    # false-success canary exits 0 several seconds too early.
                    settle_deadline = min(deadline, time.time() + 15)
                    while time.time() < settle_deadline:
                        time.sleep(1)
                        try:
                            value = json.loads(task_state.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            continue
                        status = str(value.get("completion_status") or status)
                        governance = (value.get("metadata") or {}).get("manual_iteration_governance")
                        if isinstance(governance, dict):
                            break
                    print(json.dumps({
                        "status": status,
                        "task_id": value.get("task_id") or task_path.name,
                        "task_dir": str(task_path),
                    }, ensure_ascii=False))
                    return 0 if status == "done" else 1
        time.sleep(1)
    print(json.dumps({"status": "timeout", "message_id": message_id,
                      "task_dir": str(task_path) if task_path else ""}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
