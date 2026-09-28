#!/usr/bin/env python3
"""Run one QQ Official Bot bridge for one Partner instance."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s qq_bridge_daemon %(levelname)s %(message)s")
logger = logging.getLogger("qq_bridge_daemon")
_stop = False


def _stop_signal(*_args):
    global _stop
    _stop = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Partner QQ Bot bridge")
    parser.add_argument("--workspace", default=os.environ.get(
        "PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace"))
    parser.add_argument("--instance", choices=("01", "02", "03", "04", "05"),
                        default=os.environ.get("PARTNER_INSTANCE_ID", "02"))
    parser.add_argument("--commands-only", action="store_true",
                        help="accept status/trace commands without draining proactive outbound")
    args = parser.parse_args(argv)
    root = Path(args.workspace).resolve()
    instance_workspace = root / "instances" / args.instance
    candidates = (instance_workspace / "qq_config.json",
                  instance_workspace / "state" / "qq_config.json")
    config = next((path for path in candidates if path.is_file()), None)
    if config is None:
        logger.error("instance %s has no qq_config.json", args.instance)
        return 1

    signal.signal(signal.SIGINT, _stop_signal)
    signal.signal(signal.SIGTERM, _stop_signal)
    from shells.frontend.qq_bot.qq_official_bridge import create_bridge
    bridge = create_bridge(str(instance_workspace), config_path=str(config))
    if args.commands_only:
        bridge.config.outbound_delivery_enabled = False
    stop_event = threading.Event()

    def run_bridge():
        try:
            bridge.start()
        except Exception:
            logger.exception("QQ bridge stopped with an error")

    thread = threading.Thread(target=run_bridge, daemon=True,
                              name=f"qq-bridge-{args.instance}")
    thread.start()
    logger.info("QQ bridge started: instance=%s workspace=%s", args.instance, root)
    while not _stop and thread.is_alive():
        stop_event.wait(timeout=5)
    bridge.stop()
    thread.join(timeout=10)
    return 0 if not thread.is_alive() else 2


if __name__ == "__main__":
    raise SystemExit(main())
