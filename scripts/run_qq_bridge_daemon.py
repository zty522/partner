#!/usr/bin/env python3
"""Standalone QQ Bridge daemon — keeps QQ online and answers pushes.

Loads qq_config.json from instances/03/state/ (any instance's state dir is OK;
the bridge is workspace-shared) and runs bridge.start() in the foreground so
its WebSocket stays connected. Push callbacks for all 5 instances must be
re-registered — the bridge is shared but the executor.py callbacks are bound at
import time and only the calling process can register them.  This daemon only
keeps the WebSocket alive; partner instances still emit messages via their own
push_text_now; this daemon's purpose is to keep the bot platform link up so
that other processes (the desktop GUI, partner-cli sessions) can send.
"""
from __future__ import annotations

import os
import sys
import json
import logging
import signal
from pathlib import Path

sys.path.insert(0, "/mnt/e/work/partner")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s qq_bridge_daemon %(levelname)s %(message)s",
)
logger = logging.getLogger("qq_bridge_daemon")

_stop = False


def _stop_signal(*_a):
    global _stop
    _stop = True
    logger.info("qq_bridge_daemon: stop signal received")


def main() -> int:
    signal.signal(signal.SIGINT, _stop_signal)
    signal.signal(signal.SIGTERM, _stop_signal)

    workspace = os.environ.get("PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace")
    candidate_paths = [
        os.path.join(workspace, "config", "qq_config.json"),
        os.path.join(workspace, "instances", "03", "qq_config.json"),
        os.path.join(workspace, "instances", "03", "state", "qq_config.json"),
    ]
    cfg = next((p for p in candidate_paths if os.path.exists(p)), "")
    if not cfg:
        logger.error("qq_bridge_daemon: no qq_config.json found under %s", workspace)
        return 1
    logger.info("qq_bridge_daemon: loading config from %s", cfg)

    try:
        from shells.frontend.qq_bot.qq_official_bridge import create_bridge
    except Exception as exc:
        logger.error("qq_bridge_daemon: import failed: %s", exc)
        return 2

    bridge = create_bridge(workspace, config_path=cfg)

    import threading
    stop_event = threading.Event()

    def _run_bridge():
        try:
            bridge.start()
        except Exception as exc:
            logger.exception("qq_bridge_daemon: start failed: %s", exc)

    t = threading.Thread(target=_run_bridge, daemon=True, name="qq-bridge")
    t.start()
    logger.info("qq_bridge_daemon: bridge thread started, pid=%d", os.getpid())

    # Outer loop — keeps the daemon alive.  bridge.start() blocks internally,
    # so this loop only responds to stop signals.
    while not _stop:
        try:
            stop_event.wait(timeout=60)
        except KeyboardInterrupt:
            break
    try:
        bridge.stop()
    except Exception:
        pass
    logger.info("qq_bridge_daemon: exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
