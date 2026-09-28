#!/usr/bin/env python3
"""Historical diagnostic relay daemon (not part of production runtime).

Run in the background. Drains ``share/relay_outbox.jsonl`` continuously
and forwards each pending entry through an explicitly selected bridge. Exits when
``share/relay_daemon.stop`` is created.

ADR 0067 removed the implicit 03 target because QQ OpenIDs are app-scoped.
Running this diagnostic now requires an explicit ``RELAY_OPENID``; its output
must never count as delivery by the originating instance.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/e/work/partner")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s relay_daemon %(levelname)s %(message)s",
)
logger = logging.getLogger("relay_daemon")

WORKSPACE = Path(os.environ.get("PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace"))

from partner.evolution.sprint18_relay import relay_daemon_loop

if __name__ == "__main__":
    relay_openid = os.environ.get("RELAY_OPENID", "").strip()
    if not relay_openid:
        logger.error("RELAY_OPENID is required; production cross-instance relay is disabled")
        raise SystemExit(2)
    logger.info("Starting relay daemon (workspace=%s)", WORKSPACE)
    try:
        relay_daemon_loop(
            WORKSPACE,
            instance_id=os.environ.get("RELAY_INSTANCE", "03"),
            poll_interval_sec=float(os.environ.get("RELAY_POLL_SEC", "5")),
            openid=relay_openid,
        )
    except KeyboardInterrupt:
        logger.info("interrupted; bye")
    except Exception as exc:
        logger.exception("relay daemon failed: %s", exc)
        raise SystemExit(1)
