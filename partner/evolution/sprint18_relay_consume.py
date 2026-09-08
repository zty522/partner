"""Sprint18 §6 relay consumer thread — runs inside the *03* instance.

03 is the only instance whose bot app_id still has a working user
relation, so the other 4 instances forward their failed QQ pushes via
``share/relay_outbox.jsonl``.  This thread reads that outbox and re-sends
each entry through 03's bridge, so the user receives every 5-instance
message via at least one channel.

Started from ``__main__._run_instance_mode`` only when args.instance_id == "03".
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _workspace_root(workspace: str) -> Path:
    """Return the partner workspace root, given an instance workspace."""
    p = Path(workspace).expanduser()
    if p.parent.name == "instances":
        return Path(p.parent.parent)
    return Path(p)


def _outbox_path(workspace: str) -> Path:
    return _workspace_root(workspace) / "share" / "relay_outbox.jsonl"


def _read_drained(seen: set[str], path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                sig = f"{d.get('ts','')}|{d.get('source','')}|{d.get('content','')[:60]}"
                if sig in seen:
                    continue
                seen.add(sig)
                out.append(d)
    except Exception as exc:
        logger.warning("[SPRINT18_RELAY] outbox read failed: %s", exc)
    return out


def _compose_body(entry: dict[str, Any], *, max_len: int = 1800) -> str:
    """Build a short, attributed QQ body for the relayed entry."""
    iid = entry.get("source", "??")
    kind = entry.get("kind", "text")
    content = entry.get("content", "")
    if not content:
        return ""
    if kind == "file":
        prefix = f"📎 [{iid} file] "
    else:
        prefix = f"🔁 [{iid} relay] "
    return (prefix + content)[:max_len]


def _consume_loop(workspace: str, *, openid: str, bridge: Any,
                  poll_interval_sec: float = 4.0) -> None:
    """Background thread loop: drain the outbox and re-send each entry."""
    path = _outbox_path(workspace)
    seen: set[str] = set()
    last_activity = time.time()
    logger.info("[SPRINT18_RELAY] 03 relay thread started; outbox=%s openid=%s", path, openid[:12])
    while True:
        try:
            pending = _read_drained(seen, path)
        except Exception:
            pending = []
        for entry in pending:
            body = _compose_body(entry)
            if not body:
                continue
            try:
                ok = bridge.send_proactive(
                    openid, body, kind=2, bypass_quiet=True,
                )
            except Exception as exc:
                logger.warning("[SPRINT18_RELAY] bridge.send_proactive failed: %s", exc)
                ok = False
            logger.info(
                "[SPRINT18_RELAY] relayed %s from %s ok=%s",
                entry.get("ts", ""), entry.get("source", ""), ok,
            )
            last_activity = time.time()
        if time.time() - last_activity > 7200:  # 2h idle
            logger.info("[SPRINT18_RELAY] idle 2h, exiting")
            return
        time.sleep(poll_interval_sec)


def start_relay_consumer(workspace: str, *, openid: str, bridge: Any) -> threading.Thread | None:
    """Start a background thread that forwards the relay outbox to the
    given openid via the provided 03 bridge. Returns the thread handle."""
    if not str(openid or "").strip() or bridge is None:
        logger.warning("[SPRINT18_RELAY] no bridge available; consumer not started")
        return None
    th = threading.Thread(
        target=_consume_loop,
        args=(workspace,),
        kwargs={"openid": openid, "bridge": bridge},
        name="sprint18-relay-consumer",
        daemon=True,
    )
    th.start()
    logger.info("[SPRINT18_RELAY] consumer thread started: %s", th.name)
    return th
