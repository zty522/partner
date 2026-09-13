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
import sys
from pathlib import Path

from partner.runtime.event_worker import EventWorker


def main() -> int:
    parser = argparse.ArgumentParser(description="Partner shared worker process")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--slot-index", type=int, default=0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    worker = EventWorker(
        str(workspace),
        instance_id=f"shared-{int(args.slot_index)}",
        shared_mode=True,
    )
    # run_forever polls every poll_seconds and never returns on its own.
    import asyncio
    asyncio.run(worker.run_forever(poll_seconds=float(args.poll_seconds)))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
