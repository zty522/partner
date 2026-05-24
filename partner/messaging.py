"""Messaging Platform - connect Partner to WeChat/QQ/Telegram.

Usage:
    from partner.messaging import create_platform
    
    platform = create_platform("wechat", config)
    platform.start(on_message=handle_message)
    platform.send_text(chat_id, "Hello!")
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional, List
from enum import Enum
import os


class MessageType(Enum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    FILE = "file"


@dataclass
class Message:
    platform: str           # "wechat", "qq", "telegram"
    chat_id: str            # conversation identifier
    sender: str             # who sent it
    content: str            # text content (or transcribed voice)
    type: MessageType = MessageType.TEXT
    raw_audio: Optional[str] = None  # path to audio file if voice
    metadata: dict = field(default_factory=dict)


@dataclass
class PlatformConfig:
    name: str
    enabled: bool = True
    voice_enabled: bool = False
    stt_engine: str = "funasr"    # funasr, whisper
    tts_engine: str = "edge-tts"  # edge-tts, cosyvoice
    config: dict = field(default_factory=dict)


class MessagePlatform(ABC):
    """Abstract base class for messaging platforms."""
    
    def __init__(self, config: PlatformConfig):
        self.config = config
        self._on_message: Optional[Callable] = None
    
    @abstractmethod
    def name(self) -> str:
        pass
    
    @abstractmethod
    def start(self, on_message: Callable[[Message], None]):
        """Start listening for messages. Call on_message for each received."""
        pass
    
    @abstractmethod
    def stop(self):
        """Stop listening."""
        pass
    
    @abstractmethod
    def send_text(self, chat_id: str, text: str):
        """Send a text message."""
        pass
    
    def send_voice(self, chat_id: str, text: str):
        """Send a voice message (TTS then send audio)."""
        # Default: just send as text
        self.send_text(chat_id, text)
    
    def is_available(self) -> bool:
        """Check if this platform's dependencies are installed."""
        return True


class WeChatPlatform(MessagePlatform):
    """WeChat integration via WeChatFerry (Windows) or Wechaty (cross-platform).

    Uses partner.wechat_wcf.WeChatFerryAdapter for WeChatFerry integration,
    which provides proper message normalization, voice handling, and reconnection.
    Falls back to Wechaty for cross-platform scenarios.
    """

    def name(self) -> str:
        return "wechat"

    def is_available(self) -> bool:
        """Check if any WeChat backend is available."""
        # Check WeChatFerry (Windows)
        try:
            from .wechat_wcf import WeChatFerryAdapter
            adapter = WeChatFerryAdapter()
            if adapter.is_available():
                return True
        except Exception:
            pass
        # Check Wechaty (cross-platform)
        try:
            import wechaty
            return True
        except ImportError:
            pass
        return False

    def start(self, on_message: Callable[[Message], None]):
        self._on_message = on_message
        # Try WeChatFerry first (better integration)
        try:
            self._start_wechatferry(on_message)
            return
        except (ImportError, RuntimeError) as e:
            pass
        # Fallback to Wechaty
        try:
            self._start_wechaty(on_message)
        except ImportError:
            raise RuntimeError(
                "No WeChat library found. Install one:\n"
                "  pip install wcferry      (Windows only, recommended)\n"
                "  pip install wechaty       (cross-platform)"
            )

    def _start_wechatferry(self, on_message):
        """Start via WeChatFerry adapter (DLL injection, Windows)."""
        from .wechat_wcf import WeChatFerryAdapter, WCFMsgType

        adapter = WeChatFerryAdapter({
            "msg_types": [WCFMsgType.TEXT.value, WCFMsgType.VOICE.value],
        })

        if not adapter.is_available():
            raise RuntimeError("WeChatFerry not available")

        def handle_wcf_msg(wcf_msg):
            msg_type = MessageType.VOICE if wcf_msg.msg_type == WCFMsgType.VOICE.value else MessageType.TEXT
            m = Message(
                platform="wechat",
                chat_id=wcf_msg.room_id or wcf_msg.sender,
                sender=wcf_msg.sender,
                content=wcf_msg.content,
                type=msg_type,
                metadata={"is_group": wcf_msg.is_group, "msg_id": wcf_msg.msg_id},
            )
            on_message(m)

        adapter.start(on_message=handle_wcf_msg)
        self._adapter = adapter

    def _start_wechaty(self, on_message):
        """Start via Wechaty (cross-platform)."""
        import asyncio
        from wechaty import Wechaty, Message as WechatyMessage

        async def main():
            bot = Wechaty.instance()

            @bot.on_message
            async def handle(msg: WechatyMessage):
                if msg.is_self():
                    return
                m = Message(
                    platform="wechat",
                    chat_id=msg.conversation_id,
                    sender=msg.talker().name,
                    content=msg.text(),
                    type=MessageType.TEXT if msg.type() == 7 else MessageType.VOICE,
                )
                on_message(m)

            await bot.start()

        asyncio.run(main())

    def stop(self):
        if hasattr(self, '_adapter'):
            self._adapter.stop()

    def send_text(self, chat_id: str, text: str):
        if hasattr(self, '_adapter'):
            self._adapter.send_text(chat_id, text)

    def send_voice(self, chat_id: str, text: str):
        """Send a voice message (TTS then send audio)."""
        if hasattr(self, '_adapter'):
            # Generate voice using VoiceProcessor
            try:
                from .voice import VoiceProcessor
                vp = VoiceProcessor()
                audio_path = vp.synthesize(text)
                if audio_path and not audio_path.startswith("[TTS error"):
                    self._adapter.send_voice(chat_id, audio_path)
                else:
                    # Fallback to text
                    self.send_text(chat_id, text)
            except Exception:
                self.send_text(chat_id, text)
        else:
            self.send_text(chat_id, text)


class QQPlatform(MessagePlatform):
    """QQ integration via NapCat (OneBot 11 WebSocket).

    Uses partner.qq_napcat.NapCatAdapter for QQ integration via
    OneBot 11 protocol. NapCat must be running and configured.

    Config keys:
        ws_url: NapCat WebSocket URL (default: ws://127.0.0.1:3001)
        access_token: Optional OneBot access token
        group_at_only: Only respond when @mentioned in groups (default: True)
    """

    def name(self) -> str:
        return "qq"

    def is_available(self) -> bool:
        """Check if NapCat adapter can be used."""
        try:
            from .qq_napcat import NapCatAdapter
            adapter = NapCatAdapter()
            return adapter.is_available()
        except Exception:
            return False

    def start(self, on_message: Callable[[Message], None]):
        self._on_message = on_message
        self._start_napcat(on_message)

    def _start_napcat(self, on_message):
        """Start via NapCat adapter (OneBot 11 WebSocket)."""
        from .qq_napcat import NapCatAdapter

        adapter = NapCatAdapter({
            "ws_url": self.config.config.get("ws_url", "ws://127.0.0.1:3001"),
            "access_token": self.config.config.get("access_token", ""),
            "group_at_only": self.config.config.get("group_at_only", True),
        })

        if not adapter.is_available():
            raise RuntimeError(
                "websockets package not installed. Install with:\n"
                "  pip install websockets\n"
                "Also ensure NapCat is running: https://github.com/NapNeko/NapCatQQ"
            )

        def handle_qq_msg(qq_msg):
            is_group = qq_msg.message_type == "group"
            msg_type = MessageType.VOICE if "[语音]" in qq_msg.content else MessageType.TEXT
            m = Message(
                platform="qq",
                chat_id=qq_msg.group_id if is_group else qq_msg.sender_id,
                sender=f"{qq_msg.sender_name}({qq_msg.sender_id})",
                content=qq_msg.content,
                type=msg_type,
                metadata={
                    "is_group": is_group,
                    "msg_id": qq_msg.msg_id,
                    "sender_id": qq_msg.sender_id,
                    "is_at_me": qq_msg.is_at_me,
                },
            )
            on_message(m)

        adapter.start(on_message=handle_qq_msg)
        self._adapter = adapter

    def stop(self):
        if hasattr(self, '_adapter'):
            self._adapter.stop()

    def send_text(self, chat_id: str, text: str):
        if hasattr(self, '_adapter'):
            # Infer group vs private from chat_id format
            # Group IDs are typically longer numbers; private are shorter
            is_group = len(chat_id) > 10  # Heuristic
            self._adapter.send_text(chat_id, text, is_group)

    def send_voice(self, chat_id: str, text: str):
        """Send a voice message (TTS then send audio)."""
        if hasattr(self, '_adapter'):
            try:
                from .voice import VoiceProcessor
                vp = VoiceProcessor()
                audio_path = vp.synthesize(text)
                if audio_path and not audio_path.startswith("[TTS error"):
                    is_group = len(chat_id) > 10
                    self._adapter.send_voice(chat_id, audio_path, is_group)
                else:
                    self.send_text(chat_id, text)
            except Exception:
                self.send_text(chat_id, text)
        else:
            self.send_text(chat_id, text)


class WechatPadPlatform(MessagePlatform):
    """WeChat integration via WeChatPad iPad protocol (cross-platform).

    Uses partner.wechatpad_adapter.WechatPadAdapter + partner.wechatpad_bridge.WechatPadBridge.
    Unlike WeChatFerry, this works on Linux/macOS/Windows without WeChat desktop.

    Config keys:
        api_url: WeChatPad HTTP API URL (e.g., "http://127.0.0.1:8080")
        ws_url: WeChatPad WebSocket URL (e.g., "ws://127.0.0.1:8080")
        token: Auth token (optional if admin_key provided)
        admin_key: Admin key for token generation (optional if token provided)
        wxid: Bot's wxid (optional, auto-detected)
        group_at_only: Only respond when @mentioned in groups (default: True)
        voice_enabled: Enable voice processing (default: True)
    """

    def name(self) -> str:
        return "wechatpad"

    def is_available(self) -> bool:
        """Check if WechatPad adapter can be used."""
        try:
            from .wechatpad_adapter import WechatPadAdapter
            adapter = WechatPadAdapter()
            return adapter.is_available()
        except Exception:
            return False

    def start(self, on_message: Callable[[Message], None]):
        self._on_message = on_message
        self._start_wechatpad(on_message)

    def _start_wechatpad(self, on_message):
        """Start via WechatPad adapter."""
        from .wechatpad_adapter import WechatPadAdapter

        adapter = WechatPadAdapter({
            "api_url": self.config.config.get("api_url", ""),
            "ws_url": self.config.config.get("ws_url", ""),
            "token": self.config.config.get("token", ""),
            "admin_key": self.config.config.get("admin_key", ""),
            "wxid": self.config.config.get("wxid", ""),
        })

        if not adapter.is_available():
            raise RuntimeError(
                "requests package not installed. Install with:\n"
                "  pip install requests websocket-client\n"
                "Also ensure WeChatPad server is running."
            )

        def handle_wechatpad_msg(wpad_msg):
            is_group = wpad_msg.is_group
            msg_type = MessageType.VOICE if wpad_msg.msg_type == 34 else MessageType.TEXT
            m = Message(
                platform="wechatpad",
                chat_id=wpad_msg.room_id if is_group else wpad_msg.sender,
                sender=wpad_msg.sender,
                content=wpad_msg.content,
                type=msg_type,
                metadata={
                    "is_group": is_group,
                    "msg_id": wpad_msg.msg_id,
                    "sender": wpad_msg.sender,
                    "is_at_me": wpad_msg.is_at_me,
                },
            )
            on_message(m)

        adapter.start(on_message=handle_wechatpad_msg)
        self._adapter = adapter

    def stop(self):
        if hasattr(self, '_adapter'):
            self._adapter.stop()

    def send_text(self, chat_id: str, text: str):
        if hasattr(self, '_adapter'):
            self._adapter.send_text(chat_id, text)

    def send_voice(self, chat_id: str, text: str):
        """Send a voice message (TTS then send audio)."""
        if hasattr(self, '_adapter'):
            try:
                from .voice import VoiceProcessor
                import base64
                vp = VoiceProcessor()
                audio_path = vp.synthesize(text)
                if audio_path and not audio_path.startswith("[TTS error"):
                    with open(audio_path, "rb") as f:
                        audio_b64 = base64.b64encode(f.read()).decode()
                    self._adapter.send_voice(chat_id, audio_b64)
                else:
                    self.send_text(chat_id, text)
            except Exception:
                self.send_text(chat_id, text)
        else:
            self.send_text(chat_id, text)


class TelegramPlatform(MessagePlatform):
    """Telegram integration via Bot API."""
    
    def name(self) -> str:
        return "telegram"
    
    def is_available(self) -> bool:
        try:
            import telegram
            return True
        except ImportError:
            return False
    
    def start(self, on_message: Callable[[Message], None]):
        self._on_message = on_message
        import asyncio
        from telegram import Update
        from telegram.ext import ApplicationBuilder, MessageHandler, filters
        
        token = self.config.config.get("bot_token", "")
        if not token:
            raise ValueError("Telegram bot_token not configured")
        
        app = ApplicationBuilder().token(token).build()
        
        async def handle(update, context):
            m = Message(
                platform="telegram",
                chat_id=str(update.effective_chat.id),
                sender=update.effective_user.first_name,
                content=update.message.text or "",
                type=MessageType.TEXT,
            )
            on_message(m)
        
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
        app.run_polling()
    
    def stop(self):
        pass
    
    def send_text(self, chat_id: str, text: str):
        import asyncio
        from telegram import Bot
        token = self.config.config.get("bot_token", "")
        bot = Bot(token)
        asyncio.run(bot.send_message(chat_id=int(chat_id), text=text))


class OpenClawPlatform(MessagePlatform):
    """OpenClaw multi-platform integration via Gateway.

    Uses partner.openclaw_bridge.OpenClawBridge to connect Partner to
    20+ messaging platforms through OpenClaw's channel plugin system.

    Supported platforms: Telegram, Discord, QQ, WhatsApp, Slack, Signal,
    iMessage, IRC, LINE, Matrix, Feishu, MS Teams, Nostr, Twitch, etc.

    Config keys:
        gateway_url: OpenClaw Gateway URL (default: http://localhost:18789)
        gateway_token: Gateway auth token (auto-loaded from openclaw.json)
        agent_id: OpenClaw agent id (default: "main")
        voice_enabled: Enable voice processing (default: True)
        group_at_only: Only respond when @mentioned in groups (default: True)
    """

    def name(self) -> str:
        return "openclaw"

    def is_available(self) -> bool:
        """Check if OpenClaw CLI is installed and Gateway is running."""
        try:
            from .openclaw_bridge import OpenClawBridge
            bridge = OpenClawBridge(
                workspace=self.config.config.get("workspace", "/mnt/e/work/study_room")
            )
            return bridge.is_available()
        except Exception:
            return False

    def start(self, on_message: Callable[[Message], None]):
        self._on_message = on_message
        self._start_bridge(on_message)

    def _start_bridge(self, on_message):
        """Start via OpenClaw Bridge."""
        from .openclaw_bridge import OpenClawBridge, OpenClawBridgeConfig

        workspace = self.config.config.get("workspace", "/mnt/e/work/study_room")
        bridge_config = OpenClawBridgeConfig(
            gateway_url=self.config.config.get("gateway_url", "http://localhost:18789"),
            gateway_token=self.config.config.get("gateway_token", ""),
            agent_id=self.config.config.get("agent_id", "main"),
            voice_enabled=self.config.config.get("voice_enabled", True),
            group_at_only=self.config.config.get("group_at_only", True),
        )

        bridge = OpenClawBridge(workspace=workspace, config=bridge_config)

        if not bridge.is_available():
            raise RuntimeError(
                "OpenClaw not available. Ensure:\n"
                "  1. OpenClaw installed: npm install -g openclaw\n"
                "  2. Gateway running: openclaw gateway\n"
                "  3. Channels configured: openclaw channels add"
            )

        # Store bridge for send operations
        self._bridge = bridge

        # The bridge handles messages via its own callback system.
        # For the MessagePlatform interface, we expose the bridge's
        # handle_agent_message as the message handler.
        def handle_openclaw_msg(channel, sender, text, **kwargs):
            m = Message(
                platform="openclaw",
                chat_id=kwargs.get("target", sender),
                sender=sender,
                content=text,
                type=MessageType.TEXT,
                metadata={
                    "channel": channel,
                    "is_group": kwargs.get("is_group", False),
                    "sender_name": kwargs.get("sender_name", ""),
                    "msg_id": kwargs.get("msg_id", ""),
                },
            )
            on_message(m)

        self._handle_msg = handle_openclaw_msg
        bridge.start_async()

    def stop(self):
        if hasattr(self, '_bridge'):
            self._bridge.stop()

    def send_text(self, chat_id: str, text: str):
        """Send text via OpenClaw. chat_id format: 'channel:target'."""
        if hasattr(self, '_bridge'):
            parts = chat_id.split(":", 1)
            if len(parts) == 2:
                channel, target = parts
            else:
                # Default: try to send via the last used channel
                channel = "telegram"
                target = chat_id
            self._bridge.send_message(channel, target, text)

    def send_voice(self, chat_id: str, text: str):
        """Send voice via OpenClaw (TTS then send audio)."""
        if hasattr(self, '_bridge'):
            try:
                from .voice import VoiceProcessor
                vp = VoiceProcessor()
                audio_path = vp.synthesize(text)
                if audio_path and not audio_path.startswith("[TTS error"):
                    # OpenClaw handles media through its channel plugins
                    # For now, fall back to text
                    self.send_text(chat_id, text)
                else:
                    self.send_text(chat_id, text)
            except Exception:
                self.send_text(chat_id, text)
        else:
            self.send_text(chat_id, text)


# ── Voice Processing ─────────────────────────────────────────

class VoiceProcessor:
    """Handle STT (speech-to-text) and TTS (text-to-speech).
    
    Delegates to partner.voice.VoiceProcessor for actual processing.
    This class exists for backward compatibility with the messaging API.
    """
    
    def __init__(self, stt_engine: str = "funasr", tts_engine: str = "edge-tts"):
        self.stt_engine = stt_engine
        self.tts_engine = tts_engine
        self._delegate = None
    
    def _get_delegate(self):
        if self._delegate is None:
            from .voice import VoiceProcessor as _VP, VoiceConfig
            self._delegate = _VP(VoiceConfig(
                stt_engine=self.stt_engine,
                tts_engine=self.tts_engine,
            ))
        return self._delegate
    
    def transcribe(self, audio_path: str) -> str:
        """Convert speech to text."""
        return self._get_delegate().transcribe(audio_path)
    
    def synthesize(self, text: str, output_path: str = "") -> str:
        """Convert text to speech."""
        return self._get_delegate().synthesize(text, output_path or None)


# ── Factory ──────────────────────────────────────────────────

def create_platform(name: str, config: dict = None) -> MessagePlatform:
    """Create a messaging platform instance."""
    cfg = PlatformConfig(name=name, config=config or {})
    
    platforms = {
        "wechat": WeChatPlatform,
        "wechatpad": WechatPadPlatform,
        "qq": QQPlatform,
        "telegram": TelegramPlatform,
        "openclaw": OpenClawPlatform,
    }
    
    cls = platforms.get(name)
    if not cls:
        raise ValueError(f"Unknown platform: {name}. Supported: {list(platforms.keys())}")
    
    return cls(cfg)
