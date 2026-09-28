#!/usr/bin/env python3
"""Recover and drain durable experimental TaskInstances without cycle waits.

Only non-terminal TaskInstances matching ``--experiment-id`` are considered.
Stale mirrors are closed honestly, then one replacement USER_MESSAGE per
distinct unfinished request is delivered to each instance.  The two instance
workers run in parallel, while work inside one instance remains strictly FIFO.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


TERMINAL = {"done", "failed"}


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _close_stale(path: Path, value: dict[str, Any]) -> None:
    value["completion_status"] = "failed"
    value["status"] = "failed"
    metadata = value.setdefault("metadata", {})
    metadata["serial_queue_reconciliation"] = {
        "status": "superseded_for_serial_redrive",
        "reason": "pre-fix experimental message was merged or lost before execution",
        "at": datetime.now().astimezone().isoformat(),
    }
    _save(path, value)
    log_path = path.parent / "task_log.jsonl"
    row = {
        "ts": datetime.now().astimezone().isoformat(),
        "event": "completion_status_updated",
        "status": "failed",
        "failure_owner": "event_queue",
        "mechanism": "experimental_batch_plan_merge",
        "redrive": True,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def collect(root: Path, instance_id: str, experiment_id: str) -> list[str]:
    tasks_dir = root / "instances" / instance_id / "state/tasks"
    rows: list[tuple[str, Path, dict[str, Any]]] = []
    terminal_by_message: dict[str, set[str]] = {}
    for path in tasks_dir.glob("*/task_instance.json"):
        value = _load(path)
        message = str(value.get("user_message") or "")
        if f"[experiment_id={experiment_id}]" not in message:
            continue
        status = str(value.get("completion_status") or "pending")
        terminal_by_message.setdefault(message, set()).add(status)
        rows.append((str(value.get("created_at") or ""), path, value))
    selected: list[str] = []
    seen: set[str] = set()
    for _, path, value in sorted(rows):
        message = str(value.get("user_message") or "")
        status = str(value.get("completion_status") or "pending")
        if status not in TERMINAL:
            _close_stale(path, value)
        if message in seen or "done" in terminal_by_message.get(message, set()):
            continue
        seen.add(message)
        selected.append(message)
    return selected


def _inject(workspace: Path, instance_id: str, message: str) -> tuple[str, float]:
    message_id = f"serial_{instance_id}_{uuid.uuid4().hex[:12]}"
    row = {
        "id": message_id,
        "message_id": message_id,
        "text": "[execution_mode=serial_queue] " + message,
        "display_text": message,
        "source": "serial_experiment_queue",
        "channel": "local",
        "sender_id": "serial_experiment_queue",
        "sender_name": "Partner 串行实验队列",
        "attachments": [],
        "created_at": datetime.now().astimezone().isoformat(),
    }
    inbox = workspace / "state/desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return message_id, time.time()


def _wait(workspace: Path, message: str, dispatched_at: float, timeout: int) -> dict[str, Any]:
    deadline = time.time() + timeout
    prefixed = "[execution_mode=serial_queue] " + message
    while time.time() < deadline:
        matches = []
        for path in (workspace / "state/tasks").glob("*/task_instance.json"):
            try:
                if path.stat().st_mtime + 2 < dispatched_at:
                    continue
            except OSError:
                continue
            value = _load(path)
            if str(value.get("user_message") or "") != prefixed:
                continue
            matches.append((path.stat().st_mtime, path, value))
        if matches:
            _, path, value = max(matches)
            status = str(value.get("completion_status") or "pending")
            if status in TERMINAL:
                # Let manual governance append its final decision.
                settle = min(deadline, time.time() + 45)
                while time.time() < settle:
                    value = _load(path)
                    governance = (value.get("metadata") or {}).get("manual_iteration_governance")
                    if isinstance(governance, dict):
                        status = str(value.get("completion_status") or status)
                        break
                    time.sleep(1)
                return {"status": status, "task_id": value.get("task_id") or path.parent.name,
                        "task_path": str(path)}
        time.sleep(1)
    return {"status": "timeout", "task_id": "", "task_path": ""}


def drain(root: Path, instance_id: str, messages: list[str], timeout: int,
          results: dict[str, Any]) -> None:
    workspace = root / "instances" / instance_id
    output = []
    for index, message in enumerate(messages, 1):
        message_id, dispatched_at = _inject(workspace, instance_id, message)
        print(json.dumps({"event": "serial_dispatched", "instance_id": instance_id,
                          "position": index, "total": len(messages),
                          "message_id": message_id}, ensure_ascii=False), flush=True)
        result = _wait(workspace, message, dispatched_at, timeout)
        result.update({"message_id": message_id, "position": index})
        output.append(result)
        print(json.dumps({"event": "serial_completed", "instance_id": instance_id,
                          **result}, ensure_ascii=False), flush=True)
    results[instance_id] = output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--manifest", default="",
                        help="JSON object mapping instance IDs to ordered USER_MESSAGE arrays")
    parser.add_argument("--instances", nargs="+", default=["04", "05"])
    parser.add_argument("--task-timeout", type=int, default=1200)
    parser.add_argument("--result-path", default="")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.manifest:
        manifest = _load(Path(args.manifest))
        queues = {
            iid: [str(item) for item in manifest.get(iid, []) if str(item).strip()]
            for iid in args.instances
        }
    elif args.experiment_id:
        queues = {iid: collect(root, iid, args.experiment_id) for iid in args.instances}
    else:
        parser.error("one of --experiment-id or --manifest is required")
    print(json.dumps({"event": "serial_queue_prepared",
                      "counts": {key: len(value) for key, value in queues.items()}},
                     ensure_ascii=False), flush=True)
    results: dict[str, Any] = {}
    threads = [threading.Thread(target=drain,
                                args=(root, iid, queues[iid], args.task_timeout, results),
                                name=f"serial-{iid}") for iid in args.instances]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    payload = {"experiment_id": args.experiment_id,
               "manifest": str(Path(args.manifest).resolve()) if args.manifest else "",
               "completed_at": datetime.now().astimezone().isoformat(),
               "results": results}
    if args.result_path:
        _save(Path(args.result_path), payload)
    print(json.dumps({"event": "serial_queue_finished", **payload}, ensure_ascii=False), flush=True)
    bad = [row for rows in results.values() for row in rows if row.get("status") != "done"]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
