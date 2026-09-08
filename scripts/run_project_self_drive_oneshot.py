#!/usr/bin/env python3
"""One-shot per-instance self-drive: emit one task per instance using the
project's real next-action request.

Differences from the legacy self-drive:

  * No 180s tick loop. Reads each instance's ``native_state.project_id``,
    calls the existing ``instance_native._next_project_request`` helper
    (which itself uses ``project_loop.request_next_action`` to read the
    project's real brief/state/receipts), and emits exactly ONE inbox
    row per instance — only when the project actually has a next action
    to advance on.
  * Skips BLOCKED instances, projects without a real project_state.json,
    and instances whose desktop_inbox already has a pending self-drive
    row.  This keeps the emit idempotent: a second ``run_once`` is a
    no-op until the instance consumes the prior row.
  * No "自动续跑" template. The text is whatever the project loop
    decided the next step should be, plus the standard
    ``[instance_native=true]`` markers so the executor recognises it.

Run with::

    /home/os/miniconda3/bin/python scripts/run_project_self_drive_oneshot.py
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, "/mnt/e/work/partner")

from partner.governance.instance_native import (
    _next_project_request_dict, load_state, save_state,
)

WORKSPACE = Path(os.environ.get("PARTNER_WORKSPACE",
                                "/mnt/e/work/partner_workspace"))

INSTANCES = ["01", "02", "03", "04", "05"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _desktop_inbox_has_pending(workspace: Path, instance_id: str,
                                sender_marker: str) -> bool:
    """Skip emit if the most recent inbox row is still pending for this
    sender — keeps the one-shot genuinely one-shot."""
    inbox = workspace / "instances" / instance_id / "state" / "desktop_inbox.jsonl"
    if not inbox.exists():
        return False
    last = None
    try:
        with inbox.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return False
    if not last:
        return False
    return (
        sender_marker in str(last.get("sender_id") or "")
        or sender_marker in str(last.get("text") or "")
    )


def _append_inbox(workspace: Path, instance_id: str, text: str,
                  project_id: str) -> dict[str, Any]:
    inbox = workspace / "instances" / instance_id / "state" / "desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    message_id = (
        f"selfdrive_oneshot_{instance_id}_"
        f"{datetime.now(timezone.utc).astimezone().strftime('%Y%m%d_%H%M%S')}"
    )
    row = {
        "id": message_id,
        "message_id": message_id,
        "role": "user",
        "text": text,
        "content": text,
        "source": "instance_native",
        "channel": "local",
        "kind": "project",
        "project_id": project_id,
        "sender_id": f"partner_{instance_id}_self_drive_oneshot",
        "sender_name": f"Partner{instance_id}项目oneshot",
        "created_at": _now_iso(),
        "self_drive": True,
        "self_drive_oneshot": True,
    }
    with inbox.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return {"ok": True, "message_id": message_id}


def _instance_has_pending_task(workspace: Path, instance_id: str) -> bool:
    """Skip emit if the instance's most recent task is still pending.

    The legacy self-drive reset ``pending_message_id`` so inbox poller
    re-reads inbox, but it does not cancel the in-flight task. Checking
    the latest task_instance.json is the reliable signal.
    """
    tasks_dir = workspace / "instances" / instance_id / "state" / "tasks"
    if not tasks_dir.exists():
        return False
    candidates = sorted(
        tasks_dir.glob("*/task_instance.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return False
    try:
        latest = json.loads(candidates[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(latest.get("completion_status") or "") == "pending"


def run_once(workspace: Path) -> dict[str, Any]:
    """Emit one inbox row per instance, only if the project has a real
    next action to advance on. Returns a summary dict."""
    summary = {
        "emitted": [],
        "skipped_blocked": [],
        "skipped_no_project": [],
        "skipped_no_next": [],
        "skipped_pending": [],
        "errors": [],
    }
    for instance_id in INSTANCES:
        try:
            state = load_state(workspace, instance_id)
            if state.phase == "BLOCKED":
                summary["skipped_blocked"].append(instance_id)
                continue
            project_id = state.project_id or ""
            if not project_id:
                summary["skipped_no_project"].append(instance_id)
                continue
            # Ask the project's own loop what the next step should be.
            # ADR 0065: consume the structured response and trust
            # ``is_fallback`` rather than string-sniffing the rendered
            # template. The legacy fallback template no longer needs
            # to appear literally in the request text.
            proposed = _next_project_request_dict(workspace, state)
            if proposed.get("is_fallback"):
                summary["skipped_no_next"].append(
                    f"{instance_id}:{project_id}:{proposed.get('reason','?')}")
                continue
            request = str(proposed.get("request") or "")
            sender_marker = (
                f"partner_{instance_id}_self_drive_oneshot")
            if _desktop_inbox_has_pending(
                    workspace, instance_id, sender_marker):
                summary["skipped_pending"].append(instance_id)
                continue
            if _instance_has_pending_task(workspace, instance_id):
                summary["skipped_pending"].append(
                    f"{instance_id}:has_inflight_task")
                continue
            result = _append_inbox(
                workspace, instance_id, request, project_id)
            if result.get("ok"):
                summary["emitted"].append(
                    f"{instance_id}:{project_id}:"
                    f"{result['message_id'][-12:]}")
        except Exception as exc:
            summary["errors"].append(
                f"{instance_id}:{type(exc).__name__}:{exc}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=str(WORKSPACE))
    parser.add_argument(
        "--instance", choices=INSTANCES + ["all"], default="all")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    summary = run_once(workspace)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
