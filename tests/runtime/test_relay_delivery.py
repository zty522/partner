"""Real outbound channel: the qq queue, the relay sender, and their receipts.

The platform is never contacted here; ``_post_json`` is stubbed so the fallback and
the receipt logic can be exercised deterministically.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from partner.events.delivery import send_text

RELAY_PATH = Path(__file__).resolve().parents[2] / "scripts" / "relay_deliver_once.py"


@pytest.fixture()
def relay(monkeypatch):
    spec = importlib.util.spec_from_file_location("relay_deliver_once", RELAY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["relay_deliver_once"] = module
    spec.loader.exec_module(module)
    return module


def _ctx(workspace: Path):
    return SimpleNamespace(workspace=str(workspace), job_id="job_out_1",
                           instance_id="02", channel="qq",
                           sender_id="OPENID_REAL", project_id="p")


def _configs(workspace: Path, *, origin="02", relay_instance="03"):
    for instance_id, app_id in ((origin, "1904082527"), (relay_instance, "1904095253")):
        path = workspace / "instances" / instance_id / "qq_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"app_id": app_id, "app_secret": "secret"}), encoding="utf-8")


def test_send_text_queues_a_payload_for_the_qq_channel(tmp_path):
    result = send_text(_ctx(tmp_path), {"message": "hello from the flow"})
    assert result["ok"] is True and result["delivered"] is False and result["queued"] is True
    payload_path = tmp_path / "state" / "application" / "outbound" / "02" / "job_out_1.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["delivery_state"] == "queued"
    assert payload["text_delivered"] is False
    assert payload["to_user"] == "OPENID_REAL"
    assert result["receipt"] == {"channel": "qq", "path": str(payload_path)}


def test_send_text_refuses_to_queue_without_a_recipient(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.sender_id = ""
    result = send_text(ctx, {"message": "hello"})
    assert result["ok"] is False and "recipient identity is missing" in result["error"]
    assert not (tmp_path / "state" / "application" / "outbound" / "02").exists()


def test_send_text_treats_the_local_channel_as_already_delivered(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.channel = "local"
    result = send_text(ctx, {"message": "hello"})
    assert result["delivered"] is True
    assert result["receipt"]["projection"] == "event_ledger"
    assert not (tmp_path / "state" / "application" / "outbound" / "02").exists()


def _queue_one(workspace: Path, *, content="hello from the flow"):
    send_text(_ctx(workspace), {"message": content})
    return sorted((workspace / "state" / "application" / "outbound" / "02").glob("*.json"))[-1]


def test_the_relay_falls_back_to_a_live_bot_and_records_the_platform_receipt(
        tmp_path, relay, monkeypatch):
    _configs(tmp_path)
    payload_path = _queue_one(tmp_path)
    calls = []

    def fake_post(url, payload, headers, timeout=20.0):
        calls.append({"url": url, "token_payload": payload})
        if "getAppAccessToken" in url:
            return 200, {"access_token": "tok-" + str(payload["appId"])}
        if payload["appId"] if "appId" in payload else False:
            return 200, {"access_token": "tok"}
        if headers["X-Union-Appid"] == "1904082527":
            return 400, {"message": "请求的资源不存在(用户/群已注销)", "code": 11255,
                         "err_code": 40011028, "trace_id": "t1"}
        return 200, {"id": "ROBOT1.0_TEST_MESSAGE", "timestamp": "2026-09-19T18:25:24+08:00"}

    monkeypatch.setattr(relay, "_post_json", fake_post)
    receipt = relay._deliver(tmp_path, payload_path,
                             json.loads(payload_path.read_text(encoding="utf-8")),
                             origin="02", relay_instance="03", dry_run=False)
    assert receipt["status"] == "delivered"
    assert receipt["via_instance"] == "03" and receipt["via_app_id"] == "1904095253"
    assert receipt["api_message_id"] == "ROBOT1.0_TEST_MESSAGE"
    assert [a["instance"] for a in receipt["attempts"]] == ["02", "03"]
    assert receipt["attempts"][0]["http_status"] == 400
    assert receipt["attempts"][1]["http_status"] == 200


def test_the_relay_writes_a_receipt_and_marks_the_payload_delivered(tmp_path, relay, monkeypatch,
                                                                   capsys):
    _configs(tmp_path)
    payload_path = _queue_one(tmp_path)

    def fake_post(url, payload, headers, timeout=20.0):
        if "getAppAccessToken" in url:
            return 200, {"access_token": "tok"}
        if headers["X-Union-Appid"] == "1904082527":
            return 400, {"message": "请求的资源不存在(用户/群已注销)", "code": 11255,
                         "err_code": 40011028}
        return 200, {"id": "ROBOT1.0_OK", "timestamp": "2026-09-19T18:25:24+08:00"}

    monkeypatch.setattr(relay, "_post_json", fake_post)
    monkeypatch.setattr(sys, "argv", ["relay_deliver_once.py", "--workspace", str(tmp_path),
                                      "--relay-instance", "03", "--limit", "5",
                                      "--deadline-seconds", "30"])
    assert relay.main() == 0
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["delivery_state"] == "delivered" and payload["text_delivered"] is True
    assert payload["delivery_receipt"]["api_message_id"] == "ROBOT1.0_OK"
    assert payload["delivery_receipt"]["via_instance"] == "03"
    lines = (tmp_path / "share" / "relay_receipts.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    ledger_row = json.loads(lines[0])
    assert ledger_row["status"] == "delivered" and ledger_row["origin_instance"] == "02"
    assert ledger_row["content_preview"].startswith("hello from the flow")
    # a delivered payload is never sent twice
    assert relay._pending(tmp_path) == []


def test_the_relay_marks_a_payload_failed_when_every_bot_refuses(tmp_path, relay, monkeypatch):
    _configs(tmp_path)
    payload_path = _queue_one(tmp_path)

    def fake_post(url, payload, headers, timeout=20.0):
        if "getAppAccessToken" in url:
            return 200, {"access_token": "tok"}
        return 400, {"message": "请求的资源不存在(用户/群已注销)", "code": 11255,
                     "err_code": 40011028}

    monkeypatch.setattr(relay, "_post_json", fake_post)
    receipt = relay._deliver(tmp_path, payload_path,
                             json.loads(payload_path.read_text(encoding="utf-8")),
                             origin="02", relay_instance="03", dry_run=False)
    assert receipt["status"] == "failed"
    assert "已注销" in receipt["error"]


def test_the_relay_does_not_send_on_a_dry_run(tmp_path, relay):
    _configs(tmp_path)
    payload_path = _queue_one(tmp_path)
    receipt = relay._deliver(tmp_path, payload_path,
                             json.loads(payload_path.read_text(encoding="utf-8")),
                             origin="02", relay_instance="03", dry_run=True)
    assert receipt["status"] == "dry_run" and receipt["api_message_id"] == ""
    assert json.loads(payload_path.read_text(encoding="utf-8"))["delivery_state"] == "queued"


# ---------------------------------------------------------------------------
# the log-file channel
# ---------------------------------------------------------------------------

def test_the_log_channel_appends_and_reports_a_checkable_receipt(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.channel = "log"
    result = send_text(ctx, {"message": "hello from the flow"})
    assert result["ok"] is True and result["delivered"] is True
    receipt = result["receipt"]
    assert receipt["channel"] == "log"
    log_path = Path(receipt["path"])
    raw = log_path.read_bytes()
    assert result["receipt"]["offset"] == 0
    assert result["receipt"]["bytes"] == len(raw)
    assert result["receipt"]["sha256"] == hashlib.sha256(raw).hexdigest()
    row = json.loads(raw.decode("utf-8").strip())
    assert row["content"] == "hello from the flow"
    assert row["job_id"] == "job_out_1" and row["instance"] == "02"
    assert row["channel"] == "log"
    # no queue payload is written: this channel delivers, it does not defer
    assert not (tmp_path / "state" / "application" / "outbound" / "02").exists()


def test_the_log_channel_appends_without_losing_earlier_lines(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.channel = "log"
    first = send_text(ctx, {"message": "one"})
    second = send_text(ctx, {"message": "two"})
    lines = Path(first["receipt"]["path"]).read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(line)["content"] for line in lines] == ["one", "two"]
    assert second["receipt"]["offset"] == first["receipt"]["bytes"]


def test_channel_route_accepts_the_log_channel():
    from partner.events.delivery import channel_route

    assert channel_route(_ctx(Path("/tmp")), {"channel": "log"})["ok"] is True
    assert channel_route(_ctx(Path("/tmp")), {"channel": "nonsense"})["ok"] is False
