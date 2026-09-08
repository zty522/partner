#!/usr/bin/env python3
"""Sprint18 overnight canary entrypoint.

Runs the self-evolution loop with a fixed cadence. Each iteration:
  * dry_run=False — real apply_pipeline
  * records its snapshot to evolution_events.jsonl as overnight/canary_run
  * escalates if many_failures

Designed for SIGINT-clean shutdown.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/e/work/partner")
from partner.evolution.overnight_canary import run_once  # noqa: E402

WORKSPACE = Path(os.environ.get("OVERNIGHT_WORKSPACE", "/mnt/e/work/partner_workspace"))
INTERVAL_SECONDS = int(os.environ.get("OVERNIGHT_INTERVAL", "120"))
DRY_RUN = os.environ.get("OVERNIGHT_DRY_RUN", "0") == "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s overnight_canary %(levelname)s %(message)s",
)
logger = logging.getLogger("overnight_canary")

_stop = False


def _stop_signal(*_a):
    global _stop
    _stop = True
    logger.info("overnight_canary: stop signal received, finishing current iteration")


def main():
    signal.signal(signal.SIGINT, _stop_signal)
    signal.signal(signal.SIGTERM, _stop_signal)
    iteration = 0
    logger.info("overnight_canary started: workspace=%s interval=%ss dry_run=%s",
                WORKSPACE, INTERVAL_SECONDS, DRY_RUN)
    while not _stop:
        try:
            snap = run_once(WORKSPACE, dry_run=DRY_RUN, budget_seconds=30)
            logger.info(
                "iter=%d examined=%d applied=%d skipped=%d failed=%d elapsed=%.2fs",
                iteration, snap.get("examined", 0), snap.get("applied", 0),
                snap.get("skipped", 0), snap.get("failed", 0),
                snap.get("elapsed_seconds", 0),
            )
        except Exception as exc:
            logger.exception("iter=%d crashed: %s", iteration, exc)
        iteration += 1
        for _ in range(int(INTERVAL_SECONDS)):
            if _stop:
                break
            time.sleep(1)
    logger.info("overnight_canary stopped after %d iterations", iteration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
