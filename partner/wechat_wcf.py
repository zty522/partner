"""WeChatFerry Adapter - WeChat integration via DLL injection (Windows only).

WeChatFerry hooks into the WeChat desktop client process to intercept and
send messages. It requires:
  - Windows OS (not WSL)
  - WeChat desktop client running (specific version)
  - wcferry Python package: pip install wcferry

Architecture:
  WeChat Desktop ←DLL Hook→ Wcferry (Rust DLL) ←Python SDK→ wcferry ←→ This Adapter

This module provides:
  - WeChatFerryAdapter: low-level message send/receive via wcferry
  - Message callback registration
  - Contact/group info lookup
  - Voice message handling (receive SILK → save to file)

Usage:
    from partner.wechat_wcf import WeChatFerryAdapter

    adapter = WeChatFerryAdapter()
    adapter.start(on_message=lambda msg: print(msg))
    adapter.send_text("wxid_xxx", "Hello!")
    adapter.stop()

Note: Voice message transcription is handled by partner.voice.VoiceProcessor,
not by this adapter. This adapter only deals with raw message transport.
"""

import os
import time
import logging
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class WCFMsgType(Enum):
    """WeChatFerry message types (from wcferry documentation)."""
    TEXT = 1           # 文字消息
    IMAGE = 3          # 图片
    VOICE = 34         # 语音
    VIDEO = 43         # 视频
    EMOTICON = 47      # 表情
    FILE = 49          # 文件
    LINK = 49          # 链接 (same code, different sub-type)
    MINI_PROGRAM = 49  # 小程序
    SYSTEM = 10000     # 系统消息
    RECALLED = 10002   # 撤回消息


@dataclass
class WCFMessage:
    """Normalized message from WeChatFerry."""
    msg_id: str
    msg_type: int
    is_group: bool
    sender: str          # wxid of sender
    content: str         # text content or file path
    room_id: str         # "" for private chat, group wxid for group chat
    timestamp: float
    is_at_me: bool = False
    raw: Any = None      # original WxMsg object
    extra: Dict = field(default_factory=dict)


class WeChatFerryAdapter:
    """WeChatFerry adapter for WeChat desktop integration.

    This adapter wraps the wcferry Python SDK to provide:
    - Message receiving via callback
    - Text/voice message sending
    - Contact and group info lookup
    - Auto-reconnection on failure

    Prerequisites:
    - Windows OS
    - WeChat desktop running (version must match wcferry DLL)
    - pip install wcferry
    """

    def __init__(self, config: Dict = None):
        """Initialize the adapter.

        Args:
            config: Optional configuration dict with keys:
                - msg_types: list of message type ints to receive (default: [1, 34])
                - reconnect_interval: seconds between reconnection attempts (default: 30)
                - audio_dir: directory to save voice files (default: temp dir)
                - log_level: logging level (default: INFO)
        """
        self.config = config or {}
        self._wcf = None
        self._on_message: Optional[Callable[[WCFMessage], None]] = None
        self._running = False
        self._audio_dir = self.config.get("audio_dir") or os.path.join(
            tempfile.gettempdir(), "partner_wechat_audio"
        )
        os.makedirs(self._audio_dir, exist_ok=True)

        # Message types to subscribe to
        self._msg_types = self.config.get("msg_types", [1, 34])

        self._reconnect_interval = self.config.get("reconnect_interval", 30)
        self._contacts_cache: Dict[str, Dict] = {}

    # ── Lifecycle ────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check if wcferry package is installed."""
        try:
            import wcferry
            return True
        except ImportError:
            return False

    def start(self, on_message: Callable[[WCFMessage], None]):
        """Start listening for WeChat messages.

        Args:
            on_message: Callback invoked for each received message.

        Raises:
            RuntimeError: If wcferry is not available or WeChat is not running.
        """
        if not self.is_available():
            raise RuntimeError(
                "wcferry package not installed. Install with:\n"
                "  pip install wcferry\n"
                "Note: WeChatFerry requires Windows and a running WeChat desktop client."
            )

        self._on_message = on_message
        self._running = True

        try:
            from wcferry import Wcf
            self._wcf = Wcf()
            logger.info("WeChatFerry initialized successfully")

            # Enable message receiving
            self._wcf.enable_receiving_msg()
            logger.info("Message receiving enabled")

            # Subscribe to specific message types if supported
            # (wcferry may receive all types by default; we filter in callback)
            self._start_message_loop()
        except Exception as e:
            self._running = False
            raise RuntimeError(f"Failed to start WeChatFerry: {e}")

    def stop(self):
        """Stop listening and disconnect from WeChat."""
        self._running = False
        if self._wcf:
            try:
                self._wcf.disable_recv_msg()
                logger.info("Message receiving disabled")
            except Exception as e:
                logger.warning(f"Error disabling message receiving: {e}")
            try:
                self._wcf.cleanup()
                logger.info("WeChatFerry cleaned up")
            except Exception as e:
                logger.warning(f"Error during cleanup: {e}")
            self._wcf = None

    # ── Message Sending ──────────────────────────────────────────

    def send_text(self, wxid: str, text: str) -> bool:
        """Send a text message to a user or group.

        Args:
            wxid: Recipient wxid (user or group)
            text: Text content to send

        Returns:
            True if sent successfully
        """
        if not self._wcf:
            logger.error("WeChatFerry not initialized")
            return False

        try:
            ret = self._wcf.send_text(text, wxid)
            if ret == 0:
                logger.debug(f"Sent text to {wxid}: {text[:50]}...")
                return True
            else:
                logger.warning(f"send_text returned non-zero: {ret}")
                return False
        except Exception as e:
            logger.error(f"Failed to send text to {wxid}: {e}")
            return False

    def send_voice(self, wxid: str, audio_path: str, voice_format: str = "silk") -> bool:
        """Send a voice message.

        Args:
            wxid: Recipient wxid
            audio_path: Path to audio file (WAV/MP3/SILK)
            voice_format: Target format ("silk" for WeChat voice)

        Returns:
            True if sent successfully
        """
        if not self._wcf:
            logger.error("WeChatFerry not initialized")
            return False

        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found: {audio_path}")
            return False

        try:
            # If the file is not in SILK format, convert it first
            silk_path = audio_path
            if not audio_path.endswith(".silk") and voice_format == "silk":
                silk_path = self._convert_to_silk(audio_path)
                if not silk_path:
                    return False

            ret = self._wcf.send_file(silk_path, wxid)
            if ret == 0:
                logger.debug(f"Sent voice to {wxid}: {silk_path}")
                return True
            else:
                # Fallback: try sending as regular file
                logger.warning(f"send_file for voice returned {ret}, trying as file")
                ret = self._wcf.send_file(audio_path, wxid)
                return ret == 0
        except Exception as e:
            logger.error(f"Failed to send voice to {wxid}: {e}")
            return False

    def send_image(self, wxid: str, image_path: str) -> bool:
        """Send an image message."""
        if not self._wcf:
            return False
        try:
            ret = self._wcf.send_image(image_path, wxid)
            return ret == 0
        except Exception as e:
            logger.error(f"Failed to send image to {wxid}: {e}")
            return False

    def send_file(self, wxid: str, file_path: str) -> bool:
        """Send a file message."""
        if not self._wcf:
            return False
        try:
            ret = self._wcf.send_file(file_path, wxid)
            return ret == 0
        except Exception as e:
            logger.error(f"Failed to send file to {wxid}: {e}")
            return False

    # ── Contact & Group Info ─────────────────────────────────────

    def get_self_wxid(self) -> str:
        """Get the current user's wxid."""
        if self._wcf:
            return self._wcf.get_self_wxid()
        return ""

    def get_contacts(self) -> list:
        """Get all contacts."""
        if self._wcf:
            try:
                contacts = self._wcf.get_contacts()
                # Cache contacts
                for c in contacts:
                    wxid = c.get("wxid", "")
                    if wxid:
                        self._contacts_cache[wxid] = c
                return contacts
            except Exception as e:
                logger.error(f"Failed to get contacts: {e}")
        return []

    def get_contact_name(self, wxid: str) -> str:
        """Get display name for a contact."""
        if wxid not in self._contacts_cache:
            self.get_contacts()
        contact = self._contacts_cache.get(wxid, {})
        return contact.get("name") or contact.get("wxid", wxid)

    def get_chatroom_members(self, room_id: str) -> list:
        """Get members of a group chat."""
        if self._wcf:
            try:
                return self._wcf.get_chatroom_members(room_id)
            except Exception as e:
                logger.error(f"Failed to get members of {room_id}: {e}")
        return []

    # ── Internal ─────────────────────────────────────────────────

    def _start_message_loop(self):
        """Start the message receiving loop in a background thread."""
        import threading

        def _loop():
            logger.info("Message loop started")
            while self._running:
                try:
                    if not self._wcf or not self._wcf.is_receiving_msg():
                        logger.warning("Message receiving stopped unexpectedly")
                        if self._running:
                            self._try_reconnect()
                        continue

                    # Poll for messages (blocking with timeout)
                    try:
                        msg = self._wcf.get_msg()
                    except Exception:
                        # No message available or timeout
                        time.sleep(0.1)
                        continue

                    if msg is None:
                        time.sleep(0.1)
                        continue

                    # Normalize and dispatch
                    wcf_msg = self._normalize_message(msg)
                    if wcf_msg and self._should_process(wcf_msg):
                        try:
                            self._on_message(wcf_msg)
                        except Exception as e:
                            logger.error(f"Error in message handler: {e}")

                except Exception as e:
                    logger.error(f"Message loop error: {e}")
                    if self._running:
                        time.sleep(1)

            logger.info("Message loop ended")

        self._msg_thread = threading.Thread(target=_loop, daemon=True)
        self._msg_thread.start()

    def _normalize_message(self, msg) -> Optional[WCFMessage]:
        """Convert a wcferry WxMsg to our WCFMessage format."""
        try:
            msg_id = str(getattr(msg, "id", ""))
            msg_type = getattr(msg, "type", 0)
            content = getattr(msg, "content", "")
            sender = getattr(msg, "sender", "")
            room_id = getattr(msg, "roomid", "")
            is_group = bool(room_id and room_id.endswith("@chatroom"))

            # For group messages, the actual sender is in the content
            # Format: "actual_sender_wxid:\nactual_content"
            if is_group and ":\n" in content:
                parts = content.split(":\n", 1)
                actual_sender = parts[0]
                content = parts[1] if len(parts) > 1 else content
                sender = actual_sender

            return WCFMessage(
                msg_id=msg_id,
                msg_type=msg_type,
                is_group=is_group,
                sender=sender,
                content=content,
                room_id=room_id,
                timestamp=time.time(),
                is_at_me=self._check_at_me(msg, is_group),
                raw=msg,
            )
        except Exception as e:
            logger.error(f"Failed to normalize message: {e}")
            return None

    def _should_process(self, msg: WCFMessage) -> bool:
        """Filter messages: only process subscribed types."""
        return msg.msg_type in self._msg_types

    def _check_at_me(self, msg, is_group: bool) -> bool:
        """Check if the bot was @mentioned in a group message."""
        if not is_group:
            return False
        try:
            # wcferry may have an at_list or similar attribute
            at_list = getattr(msg, "at_list", [])
            if at_list and self._wcf:
                self_wxid = self._wcf.get_self_wxid()
                return self_wxid in at_list
        except Exception:
            pass
        return False

    def _convert_to_silk(self, audio_path: str) -> Optional[str]:
        """Convert audio file to SILK format for WeChat voice messages.

        Uses pilk library if available, otherwise tries ffmpeg + silk encoder.
        """
        try:
            import pilk

            # Ensure input is PCM
            pcm_path = audio_path.rsplit(".", 1)[0] + ".pcm"
            silk_path = audio_path.rsplit(".", 1)[0] + ".silk"

            # Convert to PCM first using ffmpeg
            import subprocess
            cmd = [
                "ffmpeg", "-y", "-i", audio_path,
                "-f", "s16le", "-ar", "24000", "-ac", "1",
                pcm_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"ffmpeg PCM conversion failed: {result.stderr.decode()}")
                return None

            # Convert PCM to SILK
            pilk.encode(pcm_path, silk_path, pcm_rate=24000, tencent=True)

            # Cleanup PCM
            if os.path.exists(pcm_path):
                os.remove(pcm_path)

            return silk_path
        except ImportError:
            logger.warning(
                "pilk not installed. Install with: pip install pilk\n"
                "Falling back to sending audio as file."
            )
            return None
        except Exception as e:
            logger.error(f"SILK conversion failed: {e}")
            return None

    def _try_reconnect(self):
        """Attempt to reconnect to WeChatFerry."""
        logger.info(f"Attempting reconnect in {self._reconnect_interval}s...")
        time.sleep(self._reconnect_interval)
        try:
            if self._wcf:
                self._wcf.enable_receiving_msg()
                logger.info("Reconnected to WeChatFerry")
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
