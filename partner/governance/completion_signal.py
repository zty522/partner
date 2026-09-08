"""Durable task-terminal notification for event-driven campaign controllers.

The JSONL ledger is authoritative and survives controller restarts.  The Unix
datagram is only a low-latency wake-up hint: losing it is harmless because the
next watchdog reconciliation reads the durable task and campaign state.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import select
from datetime import datetime
from pathlib import Path
from typing import Any


TERMINAL_TASK_STATES = {"done", "failed"}


def workspace_root_from_task_dir(working_dir: str | Path) -> Path | None:
    path = Path(working_dir).resolve()
    # <root>/instances/<id>/state/tasks/<task-id>
    try:
        if path.parent.name != "tasks" or path.parent.parent.name != "state":
            return None
        if path.parent.parent.parent.parent.name != "instances":
            return None
        return path.parents[4]
    except IndexError:
        return None


def socket_path(workspace: str | Path) -> Path:
    digest = hashlib.sha256(str(Path(workspace).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path("/tmp") / f"partner-task-terminal-{digest}.fifo"


def ledger_path(workspace: str | Path) -> Path:
    return Path(workspace).resolve() / "state/campaigns/task_terminal_events.jsonl"


def emit_task_terminal(working_dir: str | Path, *, task_id: str, status: str,
                       data: dict[str, Any] | None = None) -> dict[str, Any]:
    if status not in TERMINAL_TASK_STATES:
        return {"ok": False, "status": "not_terminal"}
    root = workspace_root_from_task_dir(working_dir)
    if root is None:
        return {"ok": False, "status": "workspace_not_resolved"}
    row = {
        "schema_version": 1,
        "event": "task_terminal",
        "task_id": str(task_id),
        "status": status,
        "instance_id": Path(working_dir).resolve().parents[2].name,
        "working_dir": str(Path(working_dir).resolve()),
        "at": datetime.now().astimezone().isoformat(),
        "data": dict(data or {}),
    }
    ledger = ledger_path(root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with ledger.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    notified = False
    try:
        fd = os.open(socket_path(root), os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, raw.encode("utf-8"))
        finally:
            os.close(fd)
        notified = True
    except OSError:
        # No active controller is normal for manual_stable tasks.
        pass
    return {"ok": True, "status": "recorded", "notified": notified,
            "ledger_path": str(ledger), "event": row}


class TaskTerminalReceiver:
    """Block until a task-terminal datagram arrives or watchdog expires."""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()
        self.path = socket_path(self.workspace)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        os.mkfifo(self.path, 0o600)
        # O_RDWR prevents an EOF spin while there are temporarily no writers.
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)

    def wait(self, timeout: float) -> dict[str, Any] | None:
        ready, _, _ = select.select([self.fd], [], [], max(0.05, float(timeout)))
        if not ready:
            return None
        raw = os.read(self.fd, 65536)
        try:
            line = raw.decode("utf-8").splitlines()[0]
            value = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def close(self) -> None:
        try:
            os.close(self.fd)
        finally:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self) -> "TaskTerminalReceiver":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
