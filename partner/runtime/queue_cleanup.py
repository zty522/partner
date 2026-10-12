"""Periodic cleanup of stale queue artifacts.

Two sources of backlog accumulate in a long-running partner deployment:

1. ``instances/<NN>/state/task_queue.json`` — the legacy Harness queue.
   The current runtime (instance_host + shared worker + global job store)
   never consumes it, so entries pile up forever.  We remove terminal
   tasks (done/completed/failed/cancelled/error) older than
   ``terminal_ttl_hours`` and any task older than ``absolute_ttl_days``,
   keeping at most ``keep_newest`` tasks per instance.

2. ``<workspace>/state/application/jobs/*.json`` — the global job store.
   Terminal job records (completed/failed/cancelled/blocked) older than
   ``job_ttl_hours`` are deleted.  Running/queued jobs are never touched.

Logs a one-line summary per run for crontab visibility.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path("/mnt/e/work/partner_workspace")
INSTANCES = WORKSPACE / "instances"
JOBS_DIR = WORKSPACE / "state/application/jobs"

TERMINAL_TASK_STATUSES = {"done", "completed", "failed", "cancelled", "error", "expired"}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled", "blocked"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _ts_or_mtime(path: Path, value: str | None) -> datetime:
    parsed = _parse_ts(value)
    if parsed is not None:
        return parsed
    mtime = path.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


def clean_task_queue(path: Path, *, terminal_ttl_hours: float,
                     absolute_ttl_days: float, keep_newest: int) -> int:
    if not path.is_file():
        return 0
    try:
        tasks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(tasks, list):
        return 0
    now = _now()
    removed = 0
    kept: list[dict] = []
    for task in tasks:
        if not isinstance(task, dict):
            removed += 1
            continue
        status = str(task.get("status") or task.get("state") or "")
        created = _ts_or_mtime(path, str(task.get("created_at") or ""))
        terminal = status.lower() in TERMINAL_TASK_STATUSES
        if terminal and (now - created) > timedelta(hours=terminal_ttl_hours):
            removed += 1
            continue
        if (now - created) > timedelta(days=absolute_ttl_days):
            removed += 1
            continue
        kept.append(task)
    # keep the newest N regardless (belt and braces)
    kept.sort(key=lambda t: str(t.get("created_at") or ""), reverse=True)
    if len(kept) > keep_newest:
        removed += len(kept) - keep_newest
        kept = kept[:keep_newest]
    try:
        path.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        return 0
    return removed


def clean_job_store(*, ttl_hours: float) -> tuple[int, int]:
    removed = kept = 0
    now = _now()
    if not JOBS_DIR.is_dir():
        return 0, 0
    for path in JOBS_DIR.glob("job_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        status = str(data.get("status") or "")
        if status in TERMINAL_JOB_STATUSES:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if (now - modified) > timedelta(hours=ttl_hours):
                try:
                    path.unlink()
                    removed += 1
                    continue
                except OSError:
                    pass
        kept += 1
    return removed, kept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal-ttl-hours", type=float, default=24.0)
    parser.add_argument("--absolute-ttl-days", type=float, default=30.0)
    parser.add_argument("--keep-newest", type=int, default=50)
    parser.add_argument("--job-ttl-hours", type=float, default=72.0)
    args = parser.parse_args()

    total_removed = 0
    for instance_dir in sorted(INSTANCES.glob("0*")):
        if not instance_dir.is_dir():
            continue
        queue_path = instance_dir / "state" / "task_queue.json"
        removed = clean_task_queue(
            queue_path, terminal_ttl_hours=args.terminal_ttl_hours,
            absolute_ttl_days=args.absolute_ttl_days, keep_newest=args.keep_newest)
        if removed:
            print(f"task_queue {instance_dir.name}: removed={removed}")
        total_removed += removed
    job_removed, job_kept = clean_job_store(ttl_hours=args.job_ttl_hours)
    if job_removed:
        print(f"job_store: removed={job_removed} kept={job_kept}")
    print(f"cleanup done total_removed={total_removed + job_removed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
