"""Unit tests for WeChat integration: wechat_wcf, voice, wechat_bridge.

Mock strategy:
  - wcferry.Wcf is mocked at import level (not installed in WSL)
  - ffmpeg/pilk subprocess calls are mocked
  - STT/TTS engines are mocked
  - ConversationEngine is mocked for bridge tests

Coverage targets:
  - WeChatFerryAdapter: lifecycle, message normalization, send methods, contacts
  - VoiceProcessor: transcribe pipeline, synthesize pipeline, format conversion, cleanup
  - WeChatBridge: message routing, voice handling, user context, stats
"""

import json
import os
import sys
import time
import tempfile
import threading
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock, call
from dataclasses import asdict
import pytest

# Ensure partner package is importable
sys.path.insert(0, "/mnt/e/work/study_room/partner")


# ─── WeChatFerryAdapter Tests ─────────────────────────────────────────────

class TestWCFMsgType:
    """Test WCFMsgType enum values."""

    def test_text_type(self):
        from partner.wechat_wcf import WCFMsgType
        assert WCFMsgType.TEXT.value == 1

    def test_voice_type(self):
        from partner.wechat_wcf import WCFMsgType
        assert WCFMsgType.VOICE.value == 34

    def test_image_type(self):
        from partner.wechat_wcf import WCFMsgType
        assert WCFMsgType.IMAGE.value == 3

    def test_system_type(self):
        from partner.wechat_wcf import WCFMsgType
        assert WCFMsgType.SYSTEM.value == 10000


class TestWCFMessage:
    """Test WCFMessage dataclass."""

    def test_create_basic(self):
        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage(
            msg_id="12345",
            msg_type=1,
            is_group=False,
            sender="wxid_user1",
            content="Hello!",
            room_id="",
            timestamp=1000.0,
        )
        assert msg.msg_id == "12345"
        assert msg.is_group is False
        assert msg.is_at_me is False  # default
        assert msg.extra == {}  # default

    def test_create_group_message(self):
        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage(
            msg_id="99",
            msg_type=1,
            is_group=True,
            sender="wxid_sender",
            content="Hi group",
            room_id="12345@chatroom",
            timestamp=2000.0,
            is_at_me=True,
        )
        assert msg.is_group is True
        assert msg.room_id.endswith("@chatroom")
        assert msg.is_at_me is True


class TestWeChatFerryAdapter:
    """Test WeChatFerryAdapter with mocked wcferry."""

    def _make_adapter(self, config=None):
        """Create adapter with mocked audio dir."""
        from partner.wechat_wcf import WeChatFerryAdapter
        with patch("os.makedirs"):
            adapter = WeChatFerryAdapter(config or {})
        return adapter

    def test_init_defaults(self):
        adapter = self._make_adapter()
        assert adapter._wcf is None
        assert adapter._running is False
        assert adapter._on_message is None
        assert adapter._msg_types == [1, 34]  # default: text + voice

    def test_init_custom_config(self):
        adapter = self._make_adapter({
            "msg_types": [1, 3, 34],
            "reconnect_interval": 60,
        })
        assert adapter._msg_types == [1, 3, 34]
        assert adapter._reconnect_interval == 60

    def test_is_available_false(self):
        """When wcferry is not installed, is_available returns False."""
        adapter = self._make_adapter()
        # wcferry is not installed in WSL
        assert adapter.is_available() is False

    def test_is_available_true(self):
        """When wcferry is importable, is_available returns True."""
        adapter = self._make_adapter()
        mock_wcferry = MagicMock()
        with patch.dict("sys.modules", {"wcferry": mock_wcferry}):
            assert adapter.is_available() is True

    def test_start_raises_without_wcferry(self):
        """start() raises RuntimeError when wcferry not installed."""
        adapter = self._make_adapter()
        with pytest.raises(RuntimeError, match="wcferry package not installed"):
            adapter.start(on_message=lambda m: None)

    def test_start_success(self):
        """start() initializes Wcf and starts message loop."""
        adapter = self._make_adapter()
        mock_wcf = MagicMock()
        mock_wcferry = MagicMock()
        mock_wcferry.Wcf.return_value = mock_wcf

        with patch.dict("sys.modules", {"wcferry": mock_wcferry}):
            with patch.object(adapter, "_start_message_loop"):
                adapter.start(on_message=lambda m: None)

        assert adapter._running is True
        assert adapter._wcf is mock_wcf
        mock_wcf.enable_receiving_msg.assert_called_once()

    def test_stop_cleans_up(self):
        """stop() disables receiving and cleans up wcf."""
        adapter = self._make_adapter()
        mock_wcf = MagicMock()
        adapter._wcf = mock_wcf
        adapter._running = True

        adapter.stop()

        assert adapter._running is False
        mock_wcf.disable_recv_msg.assert_called_once()
        mock_wcf.cleanup.assert_called_once()
        assert adapter._wcf is None

    def test_stop_handles_cleanup_error(self):
        """stop() handles exceptions in cleanup gracefully."""
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.disable_recv_msg.side_effect = Exception("disconnect error")
        adapter._wcf.cleanup.side_effect = Exception("cleanup error")
        adapter._running = True

        adapter.stop()  # should not raise
        assert adapter._running is False

    def test_send_text_success(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.send_text.return_value = 0

        result = adapter.send_text("wxid_test", "Hello!")
        assert result is True
        adapter._wcf.send_text.assert_called_once_with("Hello!", "wxid_test")

    def test_send_text_failure_nonzero(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.send_text.return_value = -1

        result = adapter.send_text("wxid_test", "Hello!")
        assert result is False

    def test_send_text_no_wcf(self):
        adapter = self._make_adapter()
        result = adapter.send_text("wxid_test", "Hello!")
        assert result is False

    def test_send_text_exception(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.send_text.side_effect = Exception("send error")

        result = adapter.send_text("wxid_test", "Hello!")
        assert result is False

    def test_send_voice_success_silk(self):
        """send_voice with .silk file sends directly."""
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.send_file.return_value = 0

        with patch("os.path.exists", return_value=True):
            result = adapter.send_voice("wxid_test", "/tmp/voice.silk")

        assert result is True
        adapter._wcf.send_file.assert_called_once_with("/tmp/voice.silk", "wxid_test")

    def test_send_voice_converts_non_silk(self):
        """send_voice converts non-silk files before sending."""
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.send_file.return_value = 0

        with patch("os.path.exists", return_value=True):
            with patch.object(adapter, "_convert_to_silk", return_value="/tmp/voice.silk"):
                result = adapter.send_voice("wxid_test", "/tmp/voice.wav")

        assert result is True
        adapter._wcf.send_file.assert_called_once_with("/tmp/voice.silk", "wxid_test")

    def test_send_voice_convert_fails(self):
        """send_voice returns False when conversion fails."""
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()

        with patch("os.path.exists", return_value=True):
            with patch.object(adapter, "_convert_to_silk", return_value=None):
                result = adapter.send_voice("wxid_test", "/tmp/voice.wav")

        assert result is False

    def test_send_voice_file_not_found(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()

        with patch("os.path.exists", return_value=False):
            result = adapter.send_voice("wxid_test", "/tmp/nonexistent.silk")

        assert result is False

    def test_send_image(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.send_image.return_value = 0

        result = adapter.send_image("wxid_test", "/tmp/photo.jpg")
        assert result is True

    def test_send_file(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.send_file.return_value = 0

        result = adapter.send_file("wxid_test", "/tmp/doc.pdf")
        assert result is True

    def test_get_self_wxid(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.get_self_wxid.return_value = "wxid_bot"

        assert adapter.get_self_wxid() == "wxid_bot"

    def test_get_self_wxid_no_wcf(self):
        adapter = self._make_adapter()
        assert adapter.get_self_wxid() == ""

    def test_get_contacts(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        contacts = [{"wxid": "u1", "name": "Alice"}, {"wxid": "u2", "name": "Bob"}]
        adapter._wcf.get_contacts.return_value = contacts

        result = adapter.get_contacts()
        assert len(result) == 2
        assert adapter._contacts_cache["u1"]["name"] == "Alice"

    def test_get_contact_name_from_cache(self):
        adapter = self._make_adapter()
        adapter._contacts_cache["u1"] = {"name": "Alice"}

        assert adapter.get_contact_name("u1") == "Alice"

    def test_get_contact_name_fallback(self):
        adapter = self._make_adapter()
        # No cache, no wcf
        assert adapter.get_contact_name("unknown") == "unknown"

    def test_get_chatroom_members(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.get_chatroom_members.return_value = ["u1", "u2"]

        result = adapter.get_chatroom_members("room@chatroom")
        assert result == ["u1", "u2"]

    def test_normalize_private_message(self):
        """_normalize_message correctly parses private messages."""
        adapter = self._make_adapter()

        raw_msg = MagicMock()
        raw_msg.id = "msg_001"
        raw_msg.type = 1
        raw_msg.content = "Hello!"
        raw_msg.sender = "wxid_user"
        raw_msg.roomid = ""

        result = adapter._normalize_message(raw_msg)

        assert result is not None
        assert result.msg_id == "msg_001"
        assert result.is_group is False
        assert result.sender == "wxid_user"
        assert result.content == "Hello!"

    def test_normalize_group_message(self):
        """_normalize_message parses group messages with sender prefix."""
        adapter = self._make_adapter()

        raw_msg = MagicMock()
        raw_msg.id = "msg_002"
        raw_msg.type = 1
        raw_msg.content = "wxid_sender:\nHi everyone!"
        raw_msg.sender = "wxid_sender"
        raw_msg.roomid = "12345@chatroom"

        result = adapter._normalize_message(raw_msg)

        assert result is not None
        assert result.is_group is True
        assert result.sender == "wxid_sender"
        assert result.content == "Hi everyone!"

    def test_normalize_group_no_sender_prefix(self):
        """Group message without sender prefix keeps original content."""
        adapter = self._make_adapter()

        raw_msg = MagicMock()
        raw_msg.id = "msg_003"
        raw_msg.type = 1
        raw_msg.content = "System notification"
        raw_msg.sender = "system"
        raw_msg.roomid = "12345@chatroom"

        result = adapter._normalize_message(raw_msg)

        assert result is not None
        assert result.is_group is True
        assert result.content == "System notification"

    def test_normalize_handles_exception(self):
        adapter = self._make_adapter()
        # Use an object whose attributes raise when accessed
        bad_msg = type("BadMsg", (), {"id": property(lambda self: (_ for _ in ()).throw(ValueError("bad")))})()
        result = adapter._normalize_message(bad_msg)
        assert result is None

    def test_should_process_filters(self):
        adapter = self._make_adapter({"msg_types": [1, 34]})
        from partner.wechat_wcf import WCFMessage

        text_msg = WCFMessage("1", 1, False, "u", "hi", "", time.time())
        voice_msg = WCFMessage("2", 34, False, "u", "", "", time.time())
        image_msg = WCFMessage("3", 3, False, "u", "", "", time.time())

        assert adapter._should_process(text_msg) is True
        assert adapter._should_process(voice_msg) is True
        assert adapter._should_process(image_msg) is False

    def test_check_at_me_private_chat(self):
        adapter = self._make_adapter()
        assert adapter._check_at_me(MagicMock(), is_group=False) is False

    def test_check_at_me_with_at_list(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.get_self_wxid.return_value = "wxid_bot"

        raw_msg = MagicMock()
        raw_msg.at_list = ["wxid_bot"]

        assert adapter._check_at_me(raw_msg, is_group=True) is True

    def test_check_at_me_not_in_at_list(self):
        adapter = self._make_adapter()
        adapter._wcf = MagicMock()
        adapter._wcf.get_self_wxid.return_value = "wxid_bot"

        raw_msg = MagicMock()
        raw_msg.at_list = ["wxid_other"]

        assert adapter._check_at_me(raw_msg, is_group=True) is False

    def test_convert_to_silk_with_pilk(self):
        """_convert_to_silk uses pilk + ffmpeg."""
        adapter = self._make_adapter()

        mock_pilk = MagicMock()
        mock_subprocess = MagicMock()
        mock_subprocess.return_value = MagicMock(returncode=0)

        with patch.dict("sys.modules", {"pilk": mock_pilk}):
            with patch("subprocess.run", mock_subprocess):
                with patch("os.path.exists", side_effect=lambda p: p.endswith(".silk")):
                    with patch("os.remove"):
                        result = adapter._convert_to_silk("/tmp/voice.wav")

        assert result == "/tmp/voice.silk"

    def test_convert_to_silk_no_pilk(self):
        """_convert_to_silk returns None when pilk not installed."""
        adapter = self._make_adapter()

        # Make pilk import fail
        with patch.dict("sys.modules", {"pilk": None}):
            result = adapter._convert_to_silk("/tmp/voice.wav")

        assert result is None


# ─── VoiceProcessor Tests ─────────────────────────────────────────────────

class TestVoiceConfig:
    """Test VoiceConfig defaults."""

    def test_defaults(self):
        from partner.voice import VoiceConfig
        cfg = VoiceConfig()
        assert cfg.stt_engine == "funasr"
        assert cfg.tts_engine == "edge-tts"
        assert cfg.sample_rate == 16000
        assert cfg.tts_voice == "zh-CN-XiaoxiaoNeural"


class TestVoiceProcessor:
    """Test VoiceProcessor with mocked engines."""

    def _make_processor(self, config=None):
        from partner.voice import VoiceProcessor, VoiceConfig
        cfg = config or VoiceConfig()
        with patch("os.makedirs"):
            vp = VoiceProcessor(cfg)
        return vp

    def test_init_creates_temp_dir(self):
        from partner.voice import VoiceProcessor, VoiceConfig
        cfg = VoiceConfig(temp_dir="/tmp/test_voice")
        with patch("partner.voice.os.makedirs") as mock_mkdir:
            vp = VoiceProcessor(cfg)
        mock_mkdir.assert_called_with("/tmp/test_voice", exist_ok=True)

    def test_transcribe_file_not_found(self):
        vp = self._make_processor()
        result = vp.transcribe("/nonexistent/file.silk")
        assert "[STT error" in result
        assert "file not found" in result

    def test_transcribe_wav_passthrough(self):
        """WAV files skip conversion and go directly to STT."""
        vp = self._make_processor()

        with patch("os.path.exists", return_value=True):
            with patch.object(vp, "_ensure_wav", return_value="/tmp/test.wav"):
                with patch.object(vp, "_stt_funasr", return_value="测试文字"):
                    result = vp.transcribe("/tmp/test.wav", source_format="auto")

        assert result == "测试文字"

    def test_transcribe_silk_pipeline(self):
        """SILK → WAV → STT pipeline."""
        vp = self._make_processor()

        with patch("os.path.exists", return_value=True):
            with patch.object(vp, "_ensure_wav", return_value="/tmp/test.wav"):
                with patch.object(vp, "_stt_funasr", return_value="语音识别结果"):
                    with patch("os.remove"):
                        result = vp.transcribe("/tmp/test.silk", source_format="silk")

        assert result == "语音识别结果"

    def test_transcribe_unknown_engine(self):
        from partner.voice import VoiceConfig
        vp = self._make_processor(VoiceConfig(stt_engine="nonexistent"))

        with patch("os.path.exists", return_value=True):
            with patch.object(vp, "_ensure_wav", return_value="/tmp/test.wav"):
                result = vp.transcribe("/tmp/test.wav")

        assert "unknown engine" in result

    def test_synthesize_empty_text(self):
        vp = self._make_processor()
        result = vp.synthesize("")
        assert "[TTS error" in result

    def test_synthesize_edge_tts(self):
        vp = self._make_processor()

        mock_edge_tts = MagicMock()
        mock_communicate = MagicMock()
        mock_edge_tts.Communicate.return_value = mock_communicate

        async def mock_save(path):
            # Simulate file creation
            with open(path, "w") as f:
                f.write("fake audio")

        mock_communicate.save = mock_save

        with patch.dict("sys.modules", {"edge_tts": mock_edge_tts}):
            with patch("os.path.exists", return_value=True):
                with patch("os.path.getsize", return_value=1024):
                    result = vp.synthesize("你好", "/tmp/output.mp3")

        assert result == "/tmp/output.mp3"

    def test_synthesize_unknown_engine(self):
        from partner.voice import VoiceConfig
        vp = self._make_processor(VoiceConfig(tts_engine="nonexistent"))

        result = vp.synthesize("Hello")
        assert "unknown engine" in result

    def test_ensure_wav_already_wav(self):
        vp = self._make_processor()
        result = vp._ensure_wav("/tmp/test.wav", source_format="wav")
        assert result == "/tmp/test.wav"

    def test_ensure_wav_auto_detect_wav(self):
        vp = self._make_processor()
        result = vp._ensure_wav("/tmp/test.wav", source_format="auto")
        assert result == "/tmp/test.wav"

    def test_ensure_wav_silk_format(self):
        vp = self._make_processor()
        with patch.object(vp, "_silk_to_wav", return_value="/tmp/test.wav") as mock_silk:
            result = vp._ensure_wav("/tmp/test.silk", source_format="silk")
        assert result == "/tmp/test.wav"
        mock_silk.assert_called_once_with("/tmp/test.silk")

    def test_ensure_wav_amr_format(self):
        vp = self._make_processor()
        with patch.object(vp, "_amr_to_wav", return_value="/tmp/test.wav") as mock_amr:
            result = vp._ensure_wav("/tmp/test.amr", source_format="amr")
        assert result == "/tmp/test.wav"
        mock_amr.assert_called_once_with("/tmp/test.amr")

    def test_ensure_wav_other_format(self):
        vp = self._make_processor()
        with patch.object(vp, "_ffmpeg_to_wav", return_value="/tmp/test.wav") as mock_ff:
            result = vp._ensure_wav("/tmp/test.mp3", source_format="mp3")
        assert result == "/tmp/test.wav"

    def test_silk_to_wav_with_pilk(self):
        """SILK→WAV conversion via pilk + ffmpeg."""
        vp = self._make_processor()

        mock_pilk = MagicMock()
        mock_result = MagicMock(returncode=0)

        with patch.dict("sys.modules", {"pilk": mock_pilk}):
            with patch("subprocess.run", return_value=mock_result):
                with patch("os.path.exists", side_effect=lambda p: p.endswith(".wav")):
                    with patch("os.remove"):
                        result = vp._silk_to_wav("/tmp/test.silk")

        assert result == "/tmp/test.wav"
        mock_pilk.decode.assert_called_once()

    def test_silk_to_wav_no_pilk_no_silk_decoder(self):
        """SILK→WAV returns None when no decoder available."""
        vp = self._make_processor()

        with patch.dict("sys.modules", {"pilk": None}):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                with patch("os.path.exists", return_value=False):
                    result = vp._silk_to_wav("/tmp/test.silk")

        assert result is None

    def test_amr_to_wav_success(self):
        vp = self._make_processor()
        mock_result = MagicMock(returncode=0)

        with patch("subprocess.run", return_value=mock_result):
            result = vp._amr_to_wav("/tmp/test.amr")

        assert result == "/tmp/test.wav"

    def test_amr_to_wav_failure(self):
        vp = self._make_processor()
        mock_result = MagicMock(returncode=1, stderr=b"error")

        with patch("subprocess.run", return_value=mock_result):
            result = vp._amr_to_wav("/tmp/test.amr")

        assert result is None

    def test_ffmpeg_to_wav_success(self):
        vp = self._make_processor()
        mock_result = MagicMock(returncode=0)

        with patch("subprocess.run", return_value=mock_result):
            result = vp._ffmpeg_to_wav("/tmp/test.mp3")

        assert result == "/tmp/test.wav"

    def test_cleanup_temp(self):
        """cleanup_temp removes old files."""
        vp = self._make_processor()

        old_time = time.time() - 48 * 3600  # 48 hours ago
        new_time = time.time()

        with patch("os.listdir", return_value=["old.wav", "new.wav"]):
            with patch("os.path.isfile", return_value=True):
                with patch("os.path.getmtime", side_effect=lambda p: old_time if "old" in p else new_time):
                    with patch("os.path.join", side_effect=os.path.join):
                        with patch("os.remove") as mock_remove:
                            vp.cleanup_temp(max_age_hours=24)

        # Only old file should be removed
        mock_remove.assert_called_once()

    def test_get_available_engines(self):
        vp = self._make_processor()
        result = vp.get_available_engines()
        assert "stt" in result
        assert "tts" in result
        # In WSL test env, edge-tts might or might not be installed
        assert isinstance(result["stt"], list)
        assert isinstance(result["tts"], list)


# ─── WeChatBridge Tests ───────────────────────────────────────────────────

class TestBridgeConfig:
    """Test BridgeConfig defaults."""

    def test_defaults(self):
        from partner.wechat_bridge import BridgeConfig
        cfg = BridgeConfig()
        assert cfg.voice_enabled is True
        assert cfg.voice_reply is False
        assert cfg.group_at_only is True
        assert cfg.max_reply_length == 2000
        assert cfg.msg_types == [1, 34]


class TestWeChatBridge:
    """Test WeChatBridge with fully mocked dependencies."""

    def _make_bridge(self, config=None):
        """Create bridge with all dependencies mocked."""
        from partner.wechat_bridge import WeChatBridge, BridgeConfig

        cfg = config or BridgeConfig()

        with patch("partner.wechat_bridge.TaskQueue") as MockTQ, \
             patch("partner.wechat_bridge.KnowledgeBase") as MockKB, \
             patch("partner.wechat_bridge.Journal") as MockJournal, \
             patch("partner.wechat_bridge.StateManager") as MockSM, \
             patch("partner.wechat_bridge.ConversationEngine") as MockConv, \
             patch("partner.wechat_bridge.VoiceProcessor") as MockVP, \
             patch("partner.wechat_bridge.WeChatFerryAdapter") as MockAdapter, \
             patch("os.makedirs"):

            bridge = WeChatBridge(workspace="/tmp/test_ws", config=cfg)

            # Store mocks for assertions
            bridge._mock_adapter = MockAdapter.return_value
            bridge._mock_voice = MockVP.return_value
            bridge._mock_conversation = MockConv.return_value
            bridge._mock_journal = MockJournal.return_value

        return bridge

    def test_init_creates_components(self):
        bridge = self._make_bridge()
        assert bridge.adapter is not None
        assert bridge.voice is not None
        assert bridge.conversation is not None
        assert bridge._running is False
        assert bridge._stats["messages_received"] == 0

    def test_handle_text_message(self):
        """Bridge routes text messages to conversation engine."""
        bridge = self._make_bridge()
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"
        bridge._mock_conversation.respond.return_value = "你好！"
        bridge._mock_adapter.send_text.return_value = True

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage(
            msg_id="msg_001",
            msg_type=1,
            is_group=False,
            sender="wxid_user",
            content="你好",
            room_id="",
            timestamp=time.time(),
        )

        bridge._handle_message(msg)

        assert bridge._stats["messages_received"] == 1
        bridge._mock_conversation.respond.assert_called_once()
        bridge._mock_adapter.send_text.assert_called_once()

    def test_handle_skips_self_messages(self):
        """Bridge ignores messages from itself."""
        bridge = self._make_bridge()
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage(
            msg_id="msg_002",
            msg_type=1,
            is_group=False,
            sender="wxid_bot",  # same as self
            content="echo",
            room_id="",
            timestamp=time.time(),
        )

        bridge._handle_message(msg)

        assert bridge._stats["messages_received"] == 1
        bridge._mock_conversation.respond.assert_not_called()

    def test_handle_skips_group_non_at(self):
        """In groups with group_at_only=True, ignores non-@ messages."""
        bridge = self._make_bridge()
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage(
            msg_id="msg_003",
            msg_type=1,
            is_group=True,
            sender="wxid_user",
            content="random chat",
            room_id="room@chatroom",
            timestamp=time.time(),
            is_at_me=False,  # not @mentioned
        )

        bridge._handle_message(msg)

        bridge._mock_conversation.respond.assert_not_called()

    def test_handle_group_at_message(self):
        """Responds to @mentioned messages in groups."""
        bridge = self._make_bridge()
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"
        bridge._mock_conversation.respond.return_value = "收到！"

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage(
            msg_id="msg_004",
            msg_type=1,
            is_group=True,
            sender="wxid_user",
            content="@bot 你好",
            room_id="room@chatroom",
            timestamp=time.time(),
            is_at_me=True,
        )

        bridge._handle_message(msg)

        bridge._mock_conversation.respond.assert_called_once()
        # Reply should go to room_id, not sender
        bridge._mock_adapter.send_text.assert_called_once()
        call_args = bridge._mock_adapter.send_text.call_args
        assert call_args[0][0] == "room@chatroom"

    def test_handle_voice_message(self):
        """Voice messages are transcribed before routing."""
        bridge = self._make_bridge()
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"
        bridge._mock_voice.transcribe.return_value = "语音内容"
        bridge._mock_conversation.respond.return_value = "回复"

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage(
            msg_id="msg_005",
            msg_type=34,  # voice
            is_group=False,
            sender="wxid_user",
            content="/tmp/voice.silk",
            room_id="",
            timestamp=time.time(),
        )

        with patch("os.path.exists", return_value=True):
            with patch("os.remove"):
                bridge._handle_message(msg)

        bridge._mock_voice.transcribe.assert_called_once()
        assert bridge._stats["voice_transcribed"] == 1

    def test_handle_voice_disabled(self):
        """When voice_enabled=False, voice messages get placeholder text."""
        from partner.wechat_bridge import BridgeConfig
        bridge = self._make_bridge(BridgeConfig(voice_enabled=False))
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"
        bridge._mock_conversation.respond.return_value = "收到"

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage(
            msg_id="msg_006",
            msg_type=34,
            is_group=False,
            sender="wxid_user",
            content="/tmp/voice.silk",
            room_id="",
            timestamp=time.time(),
        )

        bridge._handle_message(msg)

        # Should call respond with placeholder
        call_args = bridge._mock_conversation.respond.call_args[0][0]
        assert "[语音消息]" in call_args

    def test_handle_image_message(self):
        """Image messages get placeholder text."""
        bridge = self._make_bridge()
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"
        bridge._mock_conversation.respond.return_value = "收到图片"

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage(
            msg_id="msg_007",
            msg_type=3,  # image
            is_group=False,
            sender="wxid_user",
            content="",
            room_id="",
            timestamp=time.time(),
        )

        bridge._handle_message(msg)

        call_args = bridge._mock_conversation.respond.call_args[0][0]
        assert "[图片消息]" in call_args

    def test_extract_text_types(self):
        """_extract_text handles different message types."""
        bridge = self._make_bridge()
        from partner.wechat_wcf import WCFMessage

        # Text
        text_msg = WCFMessage("1", 1, False, "u", "Hello", "", time.time())
        assert bridge._extract_text(text_msg) == "Hello"

        # Image
        img_msg = WCFMessage("2", 3, False, "u", "", "", time.time())
        assert bridge._extract_text(img_msg) == "[图片消息]"

        # Video
        vid_msg = WCFMessage("3", 43, False, "u", "", "", time.time())
        assert bridge._extract_text(vid_msg) == "[视频消息]"

        # File/Link
        file_msg = WCFMessage("4", 49, False, "u", "http://example.com", "", time.time())
        assert "[文件/链接]" in bridge._extract_text(file_msg)

        # Unknown type
        unknown_msg = WCFMessage("5", 9999, False, "u", "", "", time.time())
        assert "消息类型 9999" in bridge._extract_text(unknown_msg)

    def test_user_context_management(self):
        """Bridge maintains per-user conversation context."""
        bridge = self._make_bridge()

        # Initially empty
        assert bridge._get_user_context("user1") == []

        # Add context
        bridge._add_user_context("user1", "user", "Hello")
        bridge._add_user_context("user1", "partner", "Hi there!")

        ctx = bridge._get_user_context("user1")
        assert len(ctx) == 2
        assert ctx[0]["role"] == "user"
        assert ctx[1]["role"] == "partner"

    def test_user_context_trim(self):
        """Context is trimmed to max length."""
        bridge = self._make_bridge()
        bridge._max_context_per_user = 3

        for i in range(5):
            bridge._add_user_context("user1", "user", f"msg_{i}")

        ctx = bridge._get_user_context("user1")
        assert len(ctx) == 3
        assert ctx[0]["text"] == "msg_2"  # oldest kept

    def test_reply_truncation(self):
        """Long replies are truncated."""
        bridge = self._make_bridge()
        bridge.config.max_reply_length = 50
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"
        bridge._mock_conversation.respond.return_value = "A" * 100

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage("1", 1, False, "u", "test", "", time.time())

        bridge._handle_message(msg)

        sent_text = bridge._mock_adapter.send_text.call_args[0][1]
        assert len(sent_text) < 100
        assert "截断" in sent_text

    def test_voice_reply_enabled(self):
        """When voice_reply=True, sends voice reply after text."""
        from partner.wechat_bridge import BridgeConfig
        bridge = self._make_bridge(BridgeConfig(voice_reply=True, voice_enabled=True))
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"
        bridge._mock_conversation.respond.return_value = "回复内容"
        bridge._mock_voice.synthesize.return_value = "/tmp/reply.mp3"

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage("1", 1, False, "u", "test", "", time.time())

        with patch("os.path.exists", return_value=True):
            with patch("os.remove"):
                bridge._handle_message(msg)

        bridge._mock_voice.synthesize.assert_called_once_with("回复内容")
        bridge._mock_adapter.send_voice.assert_called_once()

    def test_error_handling(self):
        """Bridge handles exceptions gracefully and sends error message."""
        bridge = self._make_bridge()
        bridge._mock_adapter.get_self_wxid.return_value = "wxid_bot"
        bridge._mock_conversation.respond.side_effect = Exception("engine crash")

        from partner.wechat_wcf import WCFMessage
        msg = WCFMessage("1", 1, False, "u", "test", "", time.time())

        bridge._handle_message(msg)

        assert bridge._stats["errors"] == 1
        # Should send error message
        bridge._mock_adapter.send_text.assert_called()
        error_text = bridge._mock_adapter.send_text.call_args[0][1]
        assert "抱歉" in error_text

    def test_stop(self):
        bridge = self._make_bridge()
        bridge._running = True
        bridge.stop()
        assert bridge._running is False
        bridge._mock_adapter.stop.assert_called_once()

    def test_get_stats(self):
        bridge = self._make_bridge()
        bridge._user_contexts = {"u1": [], "u2": []}
        bridge._mock_voice.get_available_engines.return_value = {"stt": ["funasr"], "tts": ["edge-tts"]}

        stats = bridge.get_stats()
        assert stats["active_users"] == 2
        assert "voice_engines" in stats
        assert stats["messages_received"] == 0

    def test_save_voice_to_file_existing_path(self):
        """_save_voice_to_file returns content if it's a valid file path."""
        bridge = self._make_bridge()
        from partner.wechat_wcf import WCFMessage

        msg = WCFMessage("1", 34, False, "u", "/tmp/existing.silk", "", time.time())

        with patch("os.path.exists", return_value=True):
            result = bridge._save_voice_to_file(msg)

        assert result == "/tmp/existing.silk"

    def test_save_voice_to_file_raw_extra(self):
        """_save_voice_to_file extracts path from raw.extra."""
        bridge = self._make_bridge()
        from partner.wechat_wcf import WCFMessage

        raw = MagicMock()
        raw.extra = {"file": "/tmp/voice_from_extra.silk"}

        msg = WCFMessage("1", 34, False, "u", "some content", "", time.time(), raw=raw)

        with patch("os.path.exists", side_effect=lambda p: p == "/tmp/voice_from_extra.silk"):
            result = bridge._save_voice_to_file(msg)

        assert result == "/tmp/voice_from_extra.silk"


# ─── Integration-style Tests ──────────────────────────────────────────────

class TestEndToEndMessageFlow:
    """Test the full message flow from adapter to bridge."""

    def test_text_message_flow(self):
        """Simulate: user sends text → bridge processes → reply sent."""
        from partner.wechat_bridge import WeChatBridge, BridgeConfig
        from partner.wechat_wcf import WCFMessage

        with patch("partner.wechat_bridge.TaskQueue"), \
             patch("partner.wechat_bridge.KnowledgeBase"), \
             patch("partner.wechat_bridge.Journal") as MockJournal, \
             patch("partner.wechat_bridge.StateManager"), \
             patch("partner.wechat_bridge.ConversationEngine") as MockConv, \
             patch("partner.wechat_bridge.VoiceProcessor"), \
             patch("partner.wechat_bridge.WeChatFerryAdapter") as MockAdapter, \
             patch("os.makedirs"):

            bridge = WeChatBridge(workspace="/tmp/test")
            adapter = MockAdapter.return_value
            conv = MockConv.return_value

            adapter.get_self_wxid.return_value = "wxid_bot"
            adapter.send_text.return_value = True
            conv.respond.return_value = "我是 Partner，你好！"

            msg = WCFMessage(
                msg_id="e2e_001",
                msg_type=1,
                is_group=False,
                sender="wxid_alice",
                content="你是谁？",
                room_id="",
                timestamp=time.time(),
            )

            bridge._handle_message(msg)

            # Verify full pipeline
            assert bridge._stats["messages_received"] == 1
            assert bridge._stats["messages_sent"] == 1
            conv.respond.assert_called_once()
            adapter.send_text.assert_called_once_with("wxid_alice", "我是 Partner，你好！")

            # Verify journal logged
            journal = MockJournal.return_value
            journal.log.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
