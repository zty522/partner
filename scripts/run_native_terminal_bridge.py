#!/usr/bin/env python3
"""Completion-signal driven native terminal bridge (Sprint 37).

Watches for terminal native Jobs (sender_id prefixed ``partner_``) and bridges
them to instance_native.handle_terminal so the state machine advances — project
continuation / learning branch / YIELD_SLOT / BLOCKED — without a second user
message.  Reacts only to actual terminals; not a timer-driven poller.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/e/work/partner")

from partner.governance.terminal_bridge import watchdog_loop


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="single pass, then exit")
    args = parser.parse_args()
    watchdog_loop(Path(args.workspace), interval=args.interval, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
