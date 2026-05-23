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
    """QQ integration via NapCat + NoneBot."""
    
    def name(self) -> str:
        return "qq"
    
    def is_available(self) -> bool:
        try:
            import nonebot
            return True
        except ImportError:
            return False
    
    def start(self, on_message: Callable[[Message], None]):
        self._on_message = on_message
        # QQ bot runs as a separate process (NapCat)
        # Partner connects via WebSocket/HTTP
        raise NotImplementedError(
            "QQ integration requires NapCat setup.\n"
            "See: https://github.com/NapNeko/NapCatQQ\n"
            "Run 'partner setup' to configure."
        )
    
    def stop(self):
        pass
    
    def send_text(self, chat_id: str, text: str):
        pass


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
        "qq": QQPlatform,
        "telegram": TelegramPlatform,
    }
    
    cls = platforms.get(name)
    if not cls:
        raise ValueError(f"Unknown platform: {name}. Supported: {list(platforms.keys())}")
    
    return cls(cfg)
