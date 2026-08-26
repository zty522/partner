import asyncio
import json

from shells.frontend.qq_bot.qq_official_bot import QQQfficialBot, QQBotInfo, WS_OPCODE
from shells.frontend.qq_bot.qq_official_bridge import QQQfficialBridge


def test_resumed_session_notifies_ready_handler():
    bot = QQQfficialBot("app", "secret")
    bot._bot_info = QQBotInfo(id="bot-id", name="partner-test")
    observed = []
    bot.set_ready_handler(lambda info: observed.append(info.name))
    bot._start_heartbeat_task = lambda: None

    asyncio.run(bot._on_ws_message(
        '{"op":%d,"t":"RESUMED","s":8,"d":{}}' % WS_OPCODE.DISPATCH
    ))

    assert observed == ["partner-test"]


def test_stale_starting_state_becomes_explicit_ready_timeout(tmp_path):
    bridge = QQQfficialBridge.__new__(QQQfficialBridge)
    bridge._delivery_state_file = str(tmp_path / "qq_delivery_state.json")
    bridge._write_delivery_state(False, "starting")

    assert bridge._expire_stale_delivery_startup(timeout_sec=0) is True
    state = json.loads((tmp_path / "qq_delivery_state.json").read_text(encoding="utf-8"))
    assert state["delivery_ready"] is False
    assert state["status"] == "error"
    assert state["error_type"] == "ReadyTimeoutError"
