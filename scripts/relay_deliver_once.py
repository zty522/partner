#!/usr/bin/env python3
"""Deliver queued outbound messages through a real QQ bot, once, with receipts.

``delivery.send_text`` on the qq channel only writes a queued payload under
``state/application/outbound/<origin>/``; nothing in the repo delivers it, so
``text_delivered`` stays False forever.  This is the bounded sender for that queue.

It talks to the platform REST API directly instead of going through the bot
wrapper, because the wrapper collapses every answer into a bool and a delivery
claim needs the platform's own acknowledgement (message id) -- or its refusal
code.  Provenance of a delivery is therefore the platform, not a local write.

The origin instance's own bot is tried first.  If the platform refuses it with the
deregistered-user code (11255 / 40011028 -- the documented 30-day bot/user
relationship window), the payload is retried through a relay instance whose bot is
live, which is the relay path this project already uses for instances that cannot
send directly.

Bounded by construction: one pass over the queue, at most --limit payloads, and a
hard --deadline-seconds wall clock.

Usage:
  python3 scripts/relay_deliver_once.py --workspace /mnt/e/work/partner_workspace \
      --relay-instance 03 --limit 5 --deadline-seconds 120 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
SEND_URL = "https://api.sgroup.qq.com/v2/users/{openid}/messages"

#: The platform's "user/group deregistered" refusal: this bot has no live
#: relationship with the recipient, so another bot has to carry the message.
RELATIONSHIP_ERRORS = {11255, 40011028}


def _post_json(url: str, payload: dict, headers: dict, *, timeout: float = 20.0):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except ValueError:
            return exc.code, {"raw": raw[:400]}
    except Exception as exc:  # noqa: BLE001 -- a transport failure is a result, not a crash
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def _config_for(workspace: Path, instance_id: str) -> dict:
    for path in (workspace / "instances" / instance_id / "state" / "qq_config.json",
                 workspace / "instances" / instance_id / "qq_config.json",
                 workspace / "instances" / instance_id / "config" / "qq_config.json"):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"no qq_config.json for instance {instance_id}")


def _token(config: dict) -> str:
    # app_id must stay a string; the platform rejects an integer appId
    status, body = _post_json(TOKEN_URL, {"appId": str(config["app_id"]),
                                          "clientSecret": str(config["app_secret"])}, {})
    token = str(body.get("access_token") or "")
    if not token:
        raise RuntimeError(f"token request failed: status={status} body={body}")
    return token


def _send(config: dict, token: str, openid: str, content: str) -> tuple[int, dict]:
    headers = {"Authorization": f"QQBot {token}", "X-Union-Appid": str(config["app_id"])}
    return _post_json(SEND_URL.format(openid=openid), {"content": content, "msg_type": 0},
                      headers)


def _pending(workspace: Path) -> list[Path]:
    root = workspace / "state" / "application" / "outbound"
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.glob("*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 -- unreadable queue entries are skipped, not guessed
            continue
        if str(payload.get("delivery_state") or "") == "delivered":
            continue
        if not str(payload.get("content") or "").strip():
            continue
        found.append(path)
    return found


def _write_receipt(workspace: Path, receipt: dict) -> None:
    path = workspace / "share" / "relay_receipts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _deliver(workspace: Path, payload_path: Path, payload: dict, *, origin: str,
             relay_instance: str, dry_run: bool) -> dict:
    content = str(payload["content"])
    openid = str(payload.get("to_user") or "")
    receipt = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "payload_path": str(payload_path), "job_id": str(payload.get("job_id") or ""),
        "origin_instance": origin, "to_user": openid,
        "content_sha256": __import__("hashlib").sha256(content.encode("utf-8")).hexdigest(),
        "content_preview": content[:120],
    }
    if dry_run:
        return {**receipt, "status": "dry_run", "via_instance": "", "api_message_id": ""}
    attempts = []
    for candidate in [origin] + [i for i in [relay_instance] if i != origin]:
        try:
            config = _config_for(workspace, candidate)
        except FileNotFoundError as exc:
            attempts.append({"instance": candidate, "error": str(exc)})
            continue
        try:
            token = _token(config)
        except RuntimeError as exc:
            attempts.append({"instance": candidate, "app_id": str(config.get("app_id")),
                             "error": str(exc)})
            continue
        status, body = _send(config, token, openid, content)
        attempt = {"instance": candidate, "app_id": str(config.get("app_id")),
                   "http_status": status, "response": body}
        attempts.append(attempt)
        if status == 200 and body.get("id"):
            return {**receipt, "status": "delivered", "via_instance": candidate,
                    "via_app_id": str(config.get("app_id")), "api_message_id": str(body["id"]),
                    "api_timestamp": str(body.get("timestamp") or ""),
                    "attempts": attempts}
        code = body.get("code") or body.get("err_code")
        if code not in RELATIONSHIP_ERRORS and status not in (0, 400):
            break
    last = attempts[-1] if attempts else {}
    return {**receipt, "status": "failed",
            "error": str(last.get("response", {}).get("message") or last.get("error") or "unknown"),
            "attempts": attempts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--relay-instance", default="03",
                        help="instance whose bot carries messages the origin cannot send")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--deadline-seconds", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    started = time.time()
    delivered = failed = 0
    print(f"[relay] queue scan in {workspace / 'state' / 'application' / 'outbound'}")
    for path in _pending(workspace)[:max(0, args.limit)]:
        if time.time() - started > args.deadline_seconds:
            print("[relay] deadline reached; stopping this pass")
            break
        payload = json.loads(path.read_text(encoding="utf-8"))
        origin = path.parent.name
        receipt = _deliver(workspace, path, payload, origin=origin,
                           relay_instance=args.relay_instance, dry_run=args.dry_run)
        _write_receipt(workspace, receipt)
        if receipt["status"] == "delivered":
            delivered += 1
            payload["delivery_state"] = "delivered"
            payload["text_delivered"] = True
            payload["delivery_receipt"] = {k: receipt[k] for k in
                                           ("status", "via_instance", "via_app_id",
                                            "api_message_id", "api_timestamp", "ts")}
            payload["delivered_at"] = receipt["ts"]
        elif receipt["status"] == "failed":
            failed += 1
            payload["delivery_state"] = "failed"
            payload["delivery_receipt"] = {"status": "failed", "error": receipt["error"],
                                           "ts": receipt["ts"]}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        print(f"[relay] {path.name}: {receipt['status']} "
              f"via={receipt.get('via_instance') or '-'} "
              f"msg_id={(receipt.get('api_message_id') or '-')[:24]} "
              f"error={receipt.get('error') or '-'}")
    print(f"[relay] done in {time.time() - started:.1f}s delivered={delivered} failed={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
