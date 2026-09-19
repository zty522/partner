"""Shared worker process — runs EventFlowRunner jobs from the global queue.

ADR 0100 / migration_plan_0100 Phase 6.

Each instance is reduced to a pure QQ transport + LLM sync call.  All
business execution (EventFlowRunner flows, project scaffolding, artifact
writing) is done by an independent worker process pulled off the same
global job queue.

Workers are stateless and can be scaled horizontally up to whatever the
resource scheduler permits.  Multiple workers race for queued jobs via
file-based polling — first come, first serve.

Implementation note: this is a thin wrapper over
:class:`partner.runtime.event_worker.EventWorker` run in ``shared_mode``.
``shared_mode`` makes ``next_job()`` pick ANY queued/dispatched/running
job (ignoring ``assigned_instance`` and the retired instance_scheduler
gate), so N workers serve M instances with no cross-instance binding.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from partner.runtime.event_worker import EventWorker


def main() -> int:
    parser = argparse.ArgumentParser(description="Partner shared worker process")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--slot-index", type=int, default=0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    # Bounded diagnostic mode.  Without these flags the process is the ordinary
    # shared worker polling the global queue: default behaviour is untouched.
    parser.add_argument("--root-job-id", default="",
                        help="bounded mode: only this root Job and its own flow")
    parser.add_argument("--instance-id", default="",
                        help="bounded mode: instance identity to run as")
    parser.add_argument("--once", action="store_true",
                        help="bounded mode: one bounded pass, then exit")
    parser.add_argument("--deadline-seconds", type=float, default=0.0,
                        help="bounded mode: hard wall-clock limit (0 = none)")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    if args.root_job_id or args.once:
        if not args.root_job_id:
            parser.error("--once requires --root-job-id: a bounded run must name its Job")
        return asyncio.run(_run_bounded(workspace=workspace, root_job_id=args.root_job_id,
                                        instance_id=args.instance_id or "02",
                                        deadline_seconds=float(args.deadline_seconds)))
    worker = EventWorker(
        str(workspace),
        instance_id=f"shared-{int(args.slot_index)}",
        shared_mode=True,
    )
    # run_forever polls every poll_seconds and never returns on its own.
    asyncio.run(worker.run_forever(poll_seconds=float(args.poll_seconds)))
    return 0


async def _run_bounded(*, workspace: Path, root_job_id: str, instance_id: str,
                       deadline_seconds: float) -> int:
    """Run the named root Job on production semantics, then exit.

    The Job is driven by ``EventWorker.run_forever`` -- the exact loop a long-lived
    worker uses, including one atomic lease per Job lifecycle and the poll that
    honours the flow's own ``next_run_at``.  A watchdog stops that loop as soon as
    the named Job reaches a terminal status or the deadline passes, so the run is
    bounded without re-implementing (and mis-implementing) claim handling.
    """
    import time as _time
    worker = EventWorker(str(workspace), instance_id=instance_id, shared_mode=False,
                         root_job_id=root_job_id)
    started = _time.time()
    stop_reason: list[str] = []

    async def watchdog() -> None:
        from partner.index.job_repository import init as _init_jobs
        repo = _init_jobs(workspace)
        while not worker._stopping:
            await asyncio.sleep(1.0)
            if deadline_seconds and (_time.time() - started) > deadline_seconds:
                stop_reason.append("deadline_reached")
                worker.stop()
                return
            try:
                record = repo.get_record(root_job_id) or {}
            except Exception:  # noqa: BLE001 - a read failure must not kill the run
                continue
            status = str(record.get("status") or "")
            if status in {"completed", "failed", "cancelled", "blocked"}:
                stop_reason.append(f"root_job_terminal:{status}")
                worker.stop()
                return

    await asyncio.gather(worker.run_forever(poll_seconds=1.0), watchdog())
    print(f"[bounded] root_job_id={root_job_id} instance={instance_id} "
          f"stop={stop_reason or ['loop_exited']} seconds={round(_time.time() - started, 2)}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
