"""Persistent audit and semantic de-duplication for user-visible text.

The transport callback remains responsible for the actual channel send.  This
module records every attempted send (including suppressed and failed attempts)
and persists acknowledged semantic fingerprints across process/QQ reconnects.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from typing import Any


_lock = threading.Lock()
_AUDIT_FILE = "user_message_delivery.jsonl"
_DEDUP_FILE = "user_message_dedup.json"


def _state_path(workspace: str, name: str) -> str:
    state = os.path.join(str(workspace or "."), "state")
    os.makedirs(state, exist_ok=True)
    return os.path.join(state, name)


def semantic_text(text: str) -> str:
    """Normalize presentation-only differences without erasing task meaning."""
    value = str(text or "").strip()
    value = re.sub(r"\s+", " ", value)
    value = value.replace("…", "...")
    return value


def fingerprint(text: str) -> str:
    return hashlib.sha256(semantic_text(text).encode("utf-8")).hexdigest()


def _load_dedup(path: str) -> dict[str, dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_dedup(path: str, data: dict[str, dict[str, Any]]) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _append_audit(path: str, row: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def begin_text_delivery(
    workspace: str,
    text: str,
    *,
    source: str = "",
    parent_id: str = "",
    dedup_ttl_sec: float = 300.0,
) -> dict[str, Any]:
    """Create an audited attempt and decide whether a prior ACK suppresses it."""
    now = time.time()
    # Ordinary receipts are scoped to their foreground Event: two distinct
    # manual tasks may legitimately have the same "1/1 started" text.  Long
    # lived blocker/timeout notices intentionally use a content-only key so a
    # reconnect or stale in-memory event cannot resend the same flood.
    signature_material = semantic_text(text)
    if parent_id and float(dedup_ttl_sec) < 3600.0:
        signature_material = f"{parent_id}|{source}|{signature_material}"
    signature = hashlib.sha256(signature_material.encode("utf-8")).hexdigest()
    attempt_id = f"text_{uuid.uuid4().hex[:16]}"
    dedup_path = _state_path(workspace, _DEDUP_FILE)
    audit_path = _state_path(workspace, _AUDIT_FILE)
    with _lock:
        recent = _load_dedup(dedup_path)
        prior = recent.get(signature) or {}
        prior_ts = float(prior.get("acknowledged_at") or 0.0)
        duplicate = prior_ts > 0 and now - prior_ts <= max(0.0, float(dedup_ttl_sec))
        _append_audit(audit_path, {
            "attempt_id": attempt_id,
            "ts": now,
            "status": "deduplicated" if duplicate else "attempting",
            "fingerprint": signature,
            "semantic_text": semantic_text(text)[:1000],
            "source": str(source or ""),
            "parent_id": str(parent_id or ""),
            "dedup_ttl_sec": float(dedup_ttl_sec),
            "prior_acknowledged_at": prior_ts or None,
        })
    return {
        "attempt_id": attempt_id,
        "fingerprint": signature,
        "duplicate": duplicate,
        "prior_acknowledged_at": prior_ts or None,
    }


def finish_text_delivery(
    workspace: str,
    attempt: dict[str, Any],
    *,
    acknowledged: bool,
    status: str,
    error: str = "",
) -> None:
    """Persist the channel outcome; only an actual ACK enters the dedup set."""
    now = time.time()
    audit_path = _state_path(workspace, _AUDIT_FILE)
    dedup_path = _state_path(workspace, _DEDUP_FILE)
    signature = str(attempt.get("fingerprint") or "")
    with _lock:
        if acknowledged and signature:
            recent = _load_dedup(dedup_path)
            recent[signature] = {
                "acknowledged_at": now,
                "attempt_id": attempt.get("attempt_id"),
            }
            cutoff = now - 7 * 24 * 3600
            recent = {
                key: value for key, value in recent.items()
                if float((value or {}).get("acknowledged_at") or 0.0) >= cutoff
            }
            _write_dedup(dedup_path, recent)
        _append_audit(audit_path, {
            "attempt_id": attempt.get("attempt_id"),
            "ts": now,
            "status": str(status or ("sent" if acknowledged else "failed")),
            "fingerprint": signature,
            "acknowledged": bool(acknowledged),
            "error": str(error or "")[:500],
        })
