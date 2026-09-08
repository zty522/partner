"""Sprint18 §6 relay: outbox + relay daemon.

When any of the 5 instance bots fails to deliver a QQ message (because
its app_id → user-openid relation is stale), it appends an entry to
``share/relay_outbox.jsonl``. A long-running daemon reads this outbox
and forwards each entry through the *still-active* partner03 bot, so
the user receives every 5-instance message via at least one channel.

This module is intentionally side-effect-light: append_relay_outbox is
safe to call from any executor / bridge code; relay_daemon_loop uses
the 03 instance's qq_config.json (validated working channel).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKSPACE_HINT = Path("/mnt/e/work/partner_workspace")


def _outbox_path(workspace: str | Path) -> Path:
    return Path(workspace) / "share" / "relay_outbox.jsonl"


def append_relay_outbox(workspace: str | Path, *, content: str, kind: str,
                        source: str = "", parent_id: str = "") -> dict[str, Any]:
    """Append an entry to the relay outbox; safe under concurrent appenders."""
    p = _outbox_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "kind": kind,
        "content": content[:8000],  # cap payload size
        "source": source,
        "parent_id": parent_id,
    }
    with p.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return {"ok": True, "path": str(p)}


def load_bridge_for_instance(workspace: str | Path, instance_id: str) -> Any:
    """Load the QQOfficialBridge by reusing the chosen instance's qq_config.

    Falls back to instance 03 if ``instance_id`` is not specified because
    the 03 bot was verified to deliver (send_file_proactive ok=True).
    """
    chosen = instance_id or "03"
    cfg_path = Path(workspace) / "instances" / chosen / "state" / "qq_config.json"
    if not cfg_path.exists():
        cfg_path = Path(workspace) / "instances" / chosen / "qq_config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"qq_config not found for instance {chosen}: {cfg_path}")

    import sys
    sys.path.insert(0, "/mnt/e/work/partner")
    from shells.frontend.qq_bot.qq_official_bridge import create_bridge

    bridge = create_bridge(str(workspace), config_path=str(cfg_path))
    bridge.start()
    return bridge


def relay_daemon_loop(workspace: str | Path, *, instance_id: str = "03",
                       poll_interval_sec: float = 5.0,
                       openid: str = "",
                       stop_after_idle_sec: float = 3600.0) -> None:
    """Continually drain relay_outbox.jsonl through the chosen instance bot.

    Keeps the chosen bridge alive and forwards each pending entry via
    ``bridge.send_proactive``. Stops automatically after ``stop_after_idle_sec``
    of no new outbox writes to avoid burning resources overnight.
    """
    if not str(openid or "").strip():
        raise ValueError("relay openid must be explicit; cross-instance relay is disabled in production")
    workspace = Path(workspace)
    bridge = load_bridge_for_instance(workspace, instance_id)
    last_ts = datetime.now(timezone.utc)
    outbox = _outbox_path(workspace)
    drained_seen: set[str] = set()

    while True:
        if not outbox.exists():
            time.sleep(poll_interval_sec)
            continue
        pending: list[dict[str, Any]] = []
        try:
            with outbox.open("r", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    sig = f"{d.get('ts','')}|{d.get('kind','')}|{d.get('source','')}|{d.get('content','')[:80]}"
                    if sig not in drained_seen:
                        pending.append(d)
                        drained_seen.add(sig)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            pending = []
        except Exception as exc:
            logger.warning("[SPRINT18_RELAY] outbox read failed: %s", exc)
            time.sleep(poll_interval_sec)
            continue

        for entry in pending:
            content = entry.get("content", "")
            kind = entry.get("kind", "text")
            if not content:
                continue
            prefix = ""
            if kind == "file":
                prefix = "📎 来自其他实例的文件附件说明：\n"
            elif kind == "text":
                prefix = "🔁 实例转发消息：\n"
            body = (prefix + content)[:1800]
            try:
                ok = bridge.send_proactive(openid, body)
                logger.info("[SPRINT18_RELAY] %s kind=%s ts=%s sent=%s",
                            instance_id, kind, entry.get("ts",""), ok)
            except Exception as exc:
                logger.warning("[SPRINT18_RELAY] send failed: %s", exc)
            last_ts = datetime.now(timezone.utc)

        # If idle too long and stop requested (via sentinel file) → exit
        sentinel = workspace / "share" / "relay_daemon.stop"
        if sentinel.exists():
            logger.info("[SPRINT18_RELAY] sentinel stop file found, exiting")
            try:
                sentinel.unlink()
            except OSError:
                pass
            return
        if (datetime.now(timezone.utc) - last_ts).total_seconds() > stop_after_idle_sec:
            logger.info("[SPRINT18_RELAY] idle % sec, exiting", stop_after_idle_sec)
            return
        time.sleep(poll_interval_sec)
