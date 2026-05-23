"""WeChat Bridge - connects WeChatFerry to Partner's conversation engine.

This module is the high-level integration layer that:
  1. Starts the WeChatFerry adapter to receive messages
  2. Routes text messages directly to ConversationEngine
  3. Handles voice messages through the VoiceProcessor pipeline
  4. Sends text/voice replies back through WeChat
  5. Maintains per-user conversation context

It acts as the glue between the messaging layer (wechat_wcf.py) and
the intelligence layer (conversation.py / router.py).

Usage:
    from partner.wechat_bridge import WeChatBridge

    bridge = WeChatBridge(workspace="/mnt/e/work/study_room")
    bridge.start()  # Blocks, listening for messages
    # or
    bridge.start_async()  # Non-blocking, runs in background thread

Architecture:
    WeChat User
        ↓ (text/voice message)
    WeChatFerry Adapter (wechat_wcf.py)
        ↓ WCFMessage
    WeChatBridge (this module)
        ├── voice? → VoiceProcessor.transcribe() → text
        ↓
    ConversationEngine (conversation.py)
        ↓ response text
    WeChatBridge
        ├── voice_reply? → VoiceProcessor.synthesize() → audio
        ↓
    WeChatFerry Adapter → send back to WeChat User
"""

import os
import json
import time
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, List
from dataclasses import dataclass, field

from .wechat_wcf import WeChatFerryAdapter, WCFMessage
from .voice import VoiceProcessor, VoiceConfig
from .conversation import ConversationEngine
from .task_queue import TaskQueue
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager

logger = logging.getLogger(__name__)


@dataclass
class BridgeConfig:
    """WeChat bridge configuration."""
    # Voice settings
    voice_enabled: bool = True
    voice_reply: bool = False          # Send voice replies (text only by default)
    stt_engine: str = "funasr"         # funasr, whisper, whisper-api
    tts_engine: str = "edge-tts"       # edge-tts, cosyvoice
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    # Message settings
    max_reply_length: int = 2000       # Truncate long replies
    group_at_only: bool = True         # In groups, only respond when @mentioned
    typing_indicator: bool = True      # Show "typing..." status

    # WeChatFerry settings
    msg_types: List[int] = field(default_factory=lambda: [1, 34])  # Text + Voice
    audio_dir: str = ""                # Directory for voice files

    # Workspace
    workspace: str = ""


class WeChatBridge:
    """High-level bridge between WeChat and Partner.

    Integrates WeChatFerry (transport) + VoiceProcessor (STT/TTS) +
    ConversationEngine (intelligence).
    """

    def __init__(self, workspace: str, config: BridgeConfig = None):
        self.workspace = workspace
        self.config = config or BridgeConfig()
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
            temp_dir=self.config.audio_dir or os.path.join(workspace, "state", "voice_cache"),
        ))

        # Initialize WeChatFerry adapter
        self.adapter = WeChatFerryAdapter({
            "msg_types": self.config.msg_types,
            "audio_dir": self.config.audio_dir or os.path.join(workspace, "state", "voice_cache"),
        })

        # Per-user conversation context
        self._user_contexts: Dict[str, List[Dict]] = {}
        self._max_context_per_user = 10

        # Statistics
        self._stats = {
            "messages_received": 0,
            "messages_sent": 0,
            "voice_transcribed": 0,
            "errors": 0,
            "start_time": None,
        }

        self._running = False

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self):
        """Start the bridge (blocking).

        Connects to WeChat via WeChatFerry and listens for messages.
        This method blocks until stop() is called.
        """
        self._running = True
        self._stats["start_time"] = datetime.now().isoformat()

        logger.info("Starting WeChat Bridge...")
        logger.info(f"  Workspace: {self.workspace}")
        logger.info(f"  Voice enabled: {self.config.voice_enabled}")
        logger.info(f"  STT engine: {self.config.stt_engine}")
        logger.info(f"  TTS engine: {self.config.tts_engine}")

        # Log to Partner journal
        self.journal.log(JournalEntry(
            task_id="wechat_bridge",
            task_type="system",
            task_title="WeChat Bridge 启动",
            result_summary=f"voice={self.config.voice_enabled}, stt={self.config.stt_engine}",
        ))

        try:
            self.adapter.start(on_message=self._handle_message)
        except Exception as e:
            logger.error(f"WeChat Bridge failed to start: {e}")
            self._running = False
            raise
        finally:
            self._cleanup()

    def start_async(self):
        """Start the bridge in a background thread (non-blocking)."""
        self._bridge_thread = threading.Thread(target=self.start, daemon=True)
        self._bridge_thread.start()
        logger.info("WeChat Bridge started in background thread")

    def stop(self):
        """Stop the bridge."""
        logger.info("Stopping WeChat Bridge...")
        self._running = False
        self.adapter.stop()

    # ── Message Handling ─────────────────────────────────────────

    def _handle_message(self, msg: WCFMessage):
        """Handle an incoming WeChat message.

        This is the core message processing pipeline:
        1. Filter: skip self messages, group non-@mentions
        2. Voice: transcribe if voice message
        3. Route: send to ConversationEngine
        4. Reply: send text or voice reply
        """
        self._stats["messages_received"] += 1

        # Skip self messages
        self_wxid = self.adapter.get_self_wxid()
        if msg.sender == self_wxid:
            return

        # In groups, only respond when @mentioned (if configured)
        if msg.is_group and self.config.group_at_only and not msg.is_at_me:
            return

        try:
            # Process based on message type
            user_text = self._extract_text(msg)
            if not user_text.strip():
                return

            logger.info(f"[{msg.sender}] {user_text[:100]}")

            # Get response from Partner
            reply = self._get_response(msg.sender, user_text, msg.is_group)

            # Send reply
            self._send_reply(msg, reply)

            # Log interaction
            self.journal.log(JournalEntry(
                task_id=f"wechat_{msg.msg_id}",
                task_type="conversation",
                task_title=f"微信对话: {msg.sender}",
                result_summary=f"Q: {user_text[:100]} → A: {reply[:100]}",
            ))

        except Exception as e:
            logger.error(f"Message handling error: {e}", exc_info=True)
            self._stats["errors"] += 1
            # Try to send error notification
            try:
                self.adapter.send_text(
                    msg.room_id or msg.sender,
                    "抱歉，处理消息时出了点问题。请稍后再试。"
                )
            except Exception:
                pass

    def _extract_text(self, msg: WCFMessage) -> str:
        """Extract text content from a message (transcribe voice if needed)."""
        if msg.msg_type == 1:  # Text
            return msg.content

        elif msg.msg_type == 34:  # Voice
            if not self.config.voice_enabled:
                return "[语音消息]"

            # Save voice to file and transcribe
            audio_path = self._save_voice_to_file(msg)
            if not audio_path:
                return "[语音消息 - 无法保存]"

            # Detect format (WeChat typically uses SILK)
            source_format = "silk"
            if audio_path.endswith(".amr"):
                source_format = "amr"

            text = self.voice.transcribe(audio_path, source_format=source_format)

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

        elif msg.msg_type == 3:  # Image
            return "[图片消息]"
        elif msg.msg_type == 43:  # Video
            return "[视频消息]"
        elif msg.msg_type == 49:  # File/Link
            return f"[文件/链接] {msg.content[:200]}"
        else:
            return f"[消息类型 {msg.msg_type}]"

    def _save_voice_to_file(self, msg: WCFMessage) -> Optional[str]:
        """Save a voice message to a temporary file.

        WeChatFerry stores voice files in a specific directory.
        The content field may contain the file path.
        """
        try:
            # content might be a file path already
            if os.path.exists(msg.content):
                return msg.content

            # Try to get the voice file from wcferry
            # wcferry saves voice messages to a temp directory
            if hasattr(msg.raw, "extra") and msg.raw.extra:
                extra = msg.raw.extra
                if isinstance(extra, dict) and "file" in extra:
                    if os.path.exists(extra["file"]):
                        return extra["file"]

            # Try common wcferry voice paths
            voice_dir = os.path.join(
                self.config.audio_dir or tempfile.gettempdir(),
                "wechat_voice"
            )
            os.makedirs(voice_dir, exist_ok=True)

            # Save with msg_id as filename
            voice_path = os.path.join(voice_dir, f"{msg.msg_id}.silk")

            # If content is raw audio data
            if isinstance(msg.content, bytes):
                with open(voice_path, "wb") as f:
                    f.write(msg.content)
                return voice_path

            logger.warning(f"Could not locate voice file for message {msg.msg_id}")
            return None

        except Exception as e:
            logger.error(f"Failed to save voice: {e}")
            return None

    # ── Response Generation ──────────────────────────────────────

    def _get_response(self, sender: str, text: str, is_group: bool) -> str:
        """Get a response from Partner's conversation engine.

        Maintains per-user conversation context.
        """
        # Build context-aware prompt
        context = self._get_user_context(sender)
        if context:
            # Prepend recent conversation context
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

    def _send_reply(self, original_msg: WCFMessage, reply: str):
        """Send reply back to the user (text or voice)."""
        target = original_msg.room_id or original_msg.sender

        # Always send text
        self.adapter.send_text(target, reply)
        self._stats["messages_sent"] += 1

        # Optionally send voice reply
        if self.config.voice_reply and self.config.voice_enabled:
            self._send_voice_reply(target, reply)

    def _send_voice_reply(self, target: str, text: str):
        """Generate and send a voice reply."""
        try:
            audio_path = self.voice.synthesize(text)
            if audio_path and not audio_path.startswith("[TTS error"):
                self.adapter.send_voice(target, audio_path)
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
            ctx_path = os.path.join(self.workspace, "state", "wechat_contexts.json")
            with open(ctx_path, "w", encoding="utf-8") as f:
                json.dump(self._user_contexts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save contexts: {e}")

        # Cleanup temp audio files
        self.voice.cleanup_temp()

        # Log stats
        logger.info(f"WeChat Bridge stats: {json.dumps(self._stats, indent=2)}")
        self.journal.log(JournalEntry(
            task_id="wechat_bridge",
            task_type="system",
            task_title="WeChat Bridge 关闭",
            result_summary=json.dumps(self._stats, ensure_ascii=False),
        ))

    def get_stats(self) -> Dict:
        """Get bridge statistics."""
        return {
            **self._stats,
            "active_users": len(self._user_contexts),
            "voice_engines": self.voice.get_available_engines(),
        }
