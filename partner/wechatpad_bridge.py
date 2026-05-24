"""WeChatPad Bridge - connects WeChatPad iPad protocol to Partner's conversation engine.

This module is the high-level integration layer that:
  1. Starts the WechatPadAdapter to receive messages via WebSocket
  2. Routes text messages directly to ConversationEngine
  3. Handles voice messages through the VoiceProcessor pipeline
  4. Sends text/voice/image replies back through WeChatPad REST API
  5. Maintains per-user conversation context

It acts as the glue between the messaging layer (wechatpad_adapter.py) and
the intelligence layer (conversation.py / router.py).

Architecture:
    WeChat User
        ↓ (text/voice/image message via iPad protocol)
    WeChatPad Server (Docker)
        ↓ WebSocket push
    WechatPadAdapter (wechatpad_adapter.py)
        ↓ WechatPadMessage
    WechatPadBridge (this module)
        ├── voice? → VoiceProcessor.transcribe() → text
        ↓
    ConversationEngine (conversation.py)
        ↓ response text
    WechatPadBridge
        ├── voice_reply? → VoiceProcessor.synthesize() → audio
        ↓
    WechatPadAdapter (REST API) → send back to WeChat User

Usage:
    from partner.wechatpad_bridge import WechatPadBridge

    bridge = WechatPadBridge(
        workspace="/mnt/e/work/study_room",
        config=WechatPadBridgeConfig(
            api_url="http://127.0.0.1:8080",
            ws_url="ws://127.0.0.1:8080",
            token="your_token",
        ),
    )
    bridge.start()  # Blocks, listening for messages
    # or
    bridge.start_async()  # Non-blocking, runs in background thread

Advantages over WeChatBridge (WeChatFerry):
  - Cross-platform: works on Linux/macOS/Windows
  - No WeChat desktop client required
  - No DLL injection (more stable)
  - Docker deployment supported
"""

import os
import json
import time
import re
import logging
import threading
import base64
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, Dict, List
from dataclasses import dataclass, field

from .wechatpad_adapter import WechatPadAdapter, WechatPadMessage
from .voice import VoiceProcessor, VoiceConfig
from .conversation import ConversationEngine
from .task_queue import TaskQueue
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager

logger = logging.getLogger(__name__)


@dataclass
class WechatPadBridgeConfig:
    """WeChatPad bridge configuration."""
    # WeChatPad server settings
    api_url: str = ""              # HTTP API base URL
    ws_url: str = ""               # WebSocket URL
    token: str = ""                # Auth token
    admin_key: str = ""            # Admin key (for token generation)
    wxid: str = ""                 # Bot's wxid (auto-detected if empty)

    # Voice settings
    voice_enabled: bool = True
    voice_reply: bool = False      # Send voice replies (text only by default)
    stt_engine: str = "funasr"
    tts_engine: str = "edge-tts"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    # Message settings
    max_reply_length: int = 2000
    group_at_only: bool = True     # In groups, only respond when @mentioned
    group_at_reply_prefix: bool = True  # Add @sender prefix in group replies

    # Workspace
    workspace: str = ""


class WechatPadBridge:
    """High-level bridge between WeChatPad and Partner.

    Integrates WechatPadAdapter (transport) + VoiceProcessor (STT/TTS) +
    ConversationEngine (intelligence).
    """

    def __init__(self, workspace: str, config: WechatPadBridgeConfig = None):
        self.workspace = workspace
        self.config = config or WechatPadBridgeConfig()
        self.config.workspace = workspace

        state_dir = os.path.join(workspace, "state")
        os.makedirs(state_dir, exist_ok=True)

        # Initialize Partner components
        self.task_queue = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        self.knowledge = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
        self.journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        self.state_manager = StateManager(state_dir)
        self.conversation = ConversationEngine(
            self.journal, self.knowledge, self.task_queue, self.state_manager
        )

        # Initialize voice processor
        self.voice = VoiceProcessor(VoiceConfig(
            stt_engine=self.config.stt_engine,
            tts_engine=self.config.tts_engine,
            tts_voice=self.config.tts_voice,
            temp_dir=os.path.join(workspace, "state", "voice_cache"),
        ))

        # Initialize WechatPad adapter
        self.adapter = WechatPadAdapter({
            "api_url": self.config.api_url,
            "ws_url": self.config.ws_url,
            "token": self.config.token,
            "admin_key": self.config.admin_key,
            "wxid": self.config.wxid,
        })

        # Per-user conversation context
        self._user_contexts: Dict[str, List[Dict]] = {}
        self._max_context_per_user = 10

        # Group member cache (for @mention name resolution)
        self._group_members: Dict[str, Dict[str, str]] = {}  # room_id -> {wxid: nickname}

        # Statistics
        self._stats = {
            "messages_received": 0,
            "messages_sent": 0,
            "voice_transcribed": 0,
            "images_processed": 0,
            "errors": 0,
            "start_time": None,
        }

        self._running = False

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self):
        """Start the bridge (blocking).

        Connects to WeChatPad via WebSocket and listens for messages.
        This method blocks until stop() is called.
        """
        self._running = True
        self._stats["start_time"] = datetime.now().isoformat()

        logger.info("Starting WechatPad Bridge...")
        logger.info(f"  Workspace: {self.workspace}")
        logger.info(f"  API URL: {self.config.api_url}")
        logger.info(f"  WS URL: {self.config.ws_url}")
        logger.info(f"  Voice enabled: {self.config.voice_enabled}")

        # Log to Partner journal
        self.journal.log(JournalEntry(
            task_id="wechatpad_bridge",
            task_type="system",
            task_title="WeChatPad Bridge 启动",
            result_summary=(
                f"api_url={self.config.api_url}, "
                f"voice={self.config.voice_enabled}, "
                f"stt={self.config.stt_engine}"
            ),
        ))

        try:
            self.adapter.start(on_message=self._handle_message)
        except Exception as e:
            logger.error(f"WechatPad Bridge failed to start: {e}")
            self._running = False
            raise
        finally:
            self._cleanup()

    def start_async(self):
        """Start the bridge in a background thread (non-blocking)."""
        self._bridge_thread = threading.Thread(target=self.start, daemon=True)
        self._bridge_thread.start()
        logger.info("WechatPad Bridge started in background thread")

    def stop(self):
        """Stop the bridge."""
        logger.info("Stopping WechatPad Bridge...")
        self._running = False
        self.adapter.stop()

    # ── Message Handling ─────────────────────────────────────────

    def _handle_message(self, msg: WechatPadMessage):
        """Handle an incoming WeChat message.

        Core message processing pipeline:
        1. Filter: skip self messages, group non-@mentions
        2. Voice: transcribe if voice message
        3. Route: send to ConversationEngine
        4. Reply: send text or voice reply
        """
        self._stats["messages_received"] += 1

        # Skip self messages
        if msg.sender == self.adapter.get_self_wxid():
            return

        # Skip system messages
        if msg.msg_type in (10000, 10002):
            return

        # In groups, only respond when @mentioned (if configured)
        if msg.is_group and self.config.group_at_only and not msg.is_at_me:
            return

        try:
            # Process based on message type
            user_text = self._extract_text(msg)
            if not user_text or not user_text.strip():
                return

            logger.info(f"[{msg.sender}] {user_text[:100]}")

            # Get response from Partner
            reply = self._get_response(msg.sender, user_text, msg.is_group)

            # Send reply
            self._send_reply(msg, reply)

            # Log interaction
            self.journal.log(JournalEntry(
                task_id=f"wechatpad_{msg.msg_id}",
                task_type="conversation",
                task_title=f"WeChatPad对话: {msg.sender}",
                result_summary=f"Q: {user_text[:100]} → A: {reply[:100]}",
            ))

        except Exception as e:
            logger.error(f"Message handling error: {e}", exc_info=True)
            self._stats["errors"] += 1
            # Try to send error notification
            try:
                target = msg.room_id or msg.sender
                self.adapter.send_text(target, "抱歉，处理消息时出了点问题。请稍后再试。")
            except Exception:
                pass

    def _extract_text(self, msg: WechatPadMessage) -> str:
        """Extract text content from a message (transcribe voice if needed)."""
        if msg.msg_type == 1:  # Text
            # Remove @mention prefix in group messages
            content = msg.content
            if msg.is_group:
                # Remove @bot_name prefix
                content = re.sub(r"@\S{1,20}\s*", "", content).strip()
            return content

        elif msg.msg_type == 3:  # Image
            self._stats["images_processed"] += 1
            return "[图片消息]"

        elif msg.msg_type == 34:  # Voice
            if not self.config.voice_enabled:
                return "[语音消息]"
            return self._transcribe_voice(msg)

        elif msg.msg_type == 43:  # Video
            return "[视频消息]"

        elif msg.msg_type == 49:  # App message (link, file, quote, etc.)
            return self._parse_app_msg(msg)

        elif msg.msg_type == 47:  # Emoji
            return "[表情消息]"

        else:
            return f"[消息类型 {msg.msg_type}]"

    def _transcribe_voice(self, msg: WechatPadMessage) -> str:
        """Transcribe a voice message using VoiceProcessor."""
        try:
            # Try to download voice data from WeChatPad
            raw = msg.raw or {}
            content_str = msg.content

            # Parse voice XML to get bufid and length
            buf_id = ""
            voice_length = 0
            try:
                root = ET.fromstring(content_str)
                voicemsg = root.find("voicemsg")
                if voicemsg is not None:
                    buf_id = voicemsg.get("bufid", "")
                    voice_length = int(voicemsg.get("voicelength", "0"))
            except (ET.ParseError, ValueError):
                pass

            if not buf_id:
                return "[语音消息 - 无法解析]"

            # Download voice data
            voice_data = self.adapter.get_msg_voice(
                buf_id=buf_id, length=voice_length, msg_id=msg.new_msg_id,
            )
            if not voice_data or voice_data.get("Code") != 200:
                return "[语音消息 - 下载失败]"

            audio_base64 = voice_data.get("Data", {}).get("Base64", "")
            if not audio_base64:
                return "[语音消息 - 数据为空]"

            # Save to temp file
            audio_dir = os.path.join(self.workspace, "state", "voice_cache")
            os.makedirs(audio_dir, exist_ok=True)
            audio_path = os.path.join(audio_dir, f"voice_{msg.msg_id}.silk")

            with open(audio_path, "wb") as f:
                f.write(base64.b64decode(audio_base64))

            # Transcribe
            text = self.voice.transcribe(audio_path, source_format="silk")

            # Cleanup
            try:
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            except OSError:
                pass

            if text and not text.startswith("[STT error"):
                self._stats["voice_transcribed"] += 1
                return f"[语音] {text}"
            else:
                return "[语音消息 - 识别失败]"

        except Exception as e:
            logger.error(f"Voice transcription failed: {e}")
            return "[语音消息 - 处理失败]"

    def _parse_app_msg(self, msg: WechatPadMessage) -> str:
        """Parse app message (msg_type=49) sub-types."""
        try:
            content = msg.content
            # Try to parse as XML
            if "<" not in content:
                return f"[应用消息] {content[:200]}"

            xml_data = ET.fromstring(content)
            appmsg = xml_data.find(".//appmsg")
            if appmsg is None:
                return f"[应用消息] {content[:200]}"

            data_type = appmsg.findtext(".//type", "")

            if data_type == "57":  # Quote/reply
                title = appmsg.findtext(".//title", "")
                return f"[引用消息] {title}"
            elif data_type == "5":  # Link
                title = appmsg.findtext(".//title", "")
                url = appmsg.findtext(".//url", "")
                return f"[链接] {title} {url}"
            elif data_type in ("6", "74"):  # File
                title = appmsg.findtext(".//title", "")
                return f"[文件] {title}"
            elif data_type in ("33", "36"):  # Mini program
                return "[小程序]"
            elif data_type == "2000":  # Transfer
                return "[转账消息]"
            elif data_type == "2001":  # Red packet
                return "[红包消息]"
            else:
                title = appmsg.findtext(".//title", "")
                return f"[应用消息] {title}" if title else f"[应用消息 type={data_type}]"

        except (ET.ParseError, Exception) as e:
            logger.debug(f"App msg parse failed: {e}")
            return f"[应用消息] {msg.content[:200]}"

    # ── Response Generation ──────────────────────────────────────

    def _get_response(self, sender: str, text: str, is_group: bool) -> str:
        """Get a response from Partner's conversation engine.

        Maintains per-user conversation context.
        """
        # Build context-aware prompt
        context = self._get_user_context(sender)
        if context:
            context_text = "\n".join([
                f"{'用户' if c['role'] == 'user' else 'Partner'}: {c['text'][:200]}"
                for c in context[-3:]  # Last 3 exchanges
            ])
            full_text = f"[上下文]\n{context_text}\n\n[当前消息]\n{text}"
        else:
            full_text = text

        # Get response from conversation engine
        reply = self.conversation.respond(full_text)

        # Update context
        self._add_user_context(sender, "user", text)
        self._add_user_context(sender, "partner", reply)

        # Truncate if too long
        if len(reply) > self.config.max_reply_length:
            reply = reply[:self.config.max_reply_length] + "\n\n...(回复过长，已截断)"

        return reply

    def _send_reply(self, original_msg: WechatPadMessage, reply: str):
        """Send reply back to the user (text or voice)."""
        target = original_msg.room_id or original_msg.sender

        # In groups, add @sender prefix
        if original_msg.is_group and self.config.group_at_reply_prefix:
            at_list = [original_msg.sender]
        else:
            at_list = []

        # Send text
        self.adapter.send_text(target, reply, at_list=at_list)
        self._stats["messages_sent"] += 1

        # Optionally send voice reply
        if self.config.voice_reply and self.config.voice_enabled:
            self._send_voice_reply(target, reply)

    def _send_voice_reply(self, target: str, text: str):
        """Generate and send a voice reply."""
        try:
            audio_path = self.voice.synthesize(text)
            if audio_path and not audio_path.startswith("[TTS error"):
                # Read audio file and send as base64
                with open(audio_path, "rb") as f:
                    audio_b64 = base64.b64encode(f.read()).decode()

                self.adapter.send_voice(target, audio_b64)

                # Cleanup
                try:
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                except OSError:
                    pass
        except Exception as e:
            logger.error(f"Voice reply failed: {e}")

    # ── User Context ─────────────────────────────────────────────

    def _get_user_context(self, sender: str) -> List[Dict]:
        """Get conversation context for a user."""
        return self._user_contexts.get(sender, [])

    def _add_user_context(self, sender: str, role: str, text: str):
        """Add a message to user's conversation context."""
        if sender not in self._user_contexts:
            self._user_contexts[sender] = []

        self._user_contexts[sender].append({
            "role": role,
            "text": text,
            "timestamp": time.time(),
        })

        # Trim to max context length
        if len(self._user_contexts[sender]) > self._max_context_per_user:
            self._user_contexts[sender] = self._user_contexts[sender][-self._max_context_per_user:]

    # ── Cleanup & Stats ──────────────────────────────────────────

    def _cleanup(self):
        """Cleanup on shutdown."""
        # Save conversation contexts
        try:
            ctx_path = os.path.join(self.workspace, "state", "wechatpad_contexts.json")
            with open(ctx_path, "w", encoding="utf-8") as f:
                json.dump(self._user_contexts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save contexts: {e}")

        # Cleanup temp audio files
        self.voice.cleanup_temp()

        # Log stats
        logger.info(f"WechatPad Bridge stats: {json.dumps(self._stats, indent=2)}")
        self.journal.log(JournalEntry(
            task_id="wechatpad_bridge",
            task_type="system",
            task_title="WeChatPad Bridge 关闭",
            result_summary=json.dumps(self._stats, ensure_ascii=False),
        ))

    def get_stats(self) -> Dict:
        """Get bridge statistics."""
        return {
            **self._stats,
            "active_users": len(self._user_contexts),
            "adapter_stats": self.adapter.get_stats(),
            "voice_engines": self.voice.get_available_engines(),
        }
