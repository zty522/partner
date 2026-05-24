"""QQ Bridge - connects NapCat QQ to Partner's conversation engine.

This module is the high-level integration layer that:
  1. Starts the NapCat adapter to receive QQ messages
  2. Routes text messages directly to ConversationEngine
  3. Handles voice messages through the VoiceProcessor pipeline
  4. Sends text/voice replies back through QQ
  5. Maintains per-user conversation context

It acts as the glue between the messaging layer (qq_napcat.py) and
the intelligence layer (conversation.py / router.py).

Usage:
    from partner.qq_bridge import QQBridge

    bridge = QQBridge(workspace="/mnt/e/work/study_room")
    bridge.start()  # Blocks, listening for messages
    # or
    bridge.start_async()  # Non-blocking, runs in background thread

Architecture:
    QQ User
        ↓ (text/voice message)
    NapCat (NTQQ protocol server)
        ↓ OneBot 11 WebSocket
    NapCatAdapter (qq_napcat.py)
        ↓ QQMessage
    QQBridge (this module)
        ├── voice? → VoiceProcessor.transcribe() → text
        ↓
    ConversationEngine (conversation.py)
        ↓ response text
    QQBridge
        ├── voice_reply? → VoiceProcessor.synthesize() → audio
        ↓
    NapCatAdapter → send back to QQ User
"""

import os
import json
import time
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, List
from dataclasses import dataclass, field

from .qq_napcat import NapCatAdapter, QQMessage
from .voice import VoiceProcessor, VoiceConfig
from .conversation import ConversationEngine
from .task_queue import TaskQueue
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager

logger = logging.getLogger(__name__)


@dataclass
class QQBridgeConfig:
    """QQ bridge configuration."""
    # NapCat settings
    ws_url: str = "ws://127.0.0.1:3001"
    access_token: str = ""

    # Voice settings
    voice_enabled: bool = True
    voice_reply: bool = False          # Send voice replies (text only by default)
    stt_engine: str = "funasr"         # funasr, whisper, whisper-api
    tts_engine: str = "edge-tts"       # edge-tts, cosyvoice
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    # Message settings
    max_reply_length: int = 2000       # Truncate long replies
    group_at_only: bool = True         # In groups, only respond when @mentioned
    auto_approve_friend: bool = False  # Auto-approve friend requests

    # Workspace
    workspace: str = ""


class QQBridge:
    """High-level bridge between QQ (NapCat) and Partner.

    Integrates NapCatAdapter (transport) + VoiceProcessor (STT/TTS) +
    ConversationEngine (intelligence).
    """

    def __init__(self, workspace: str, config: QQBridgeConfig = None):
        self.workspace = workspace
        self.config = config or QQBridgeConfig()
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

        # Initialize NapCat adapter
        self.adapter = NapCatAdapter({
            "ws_url": self.config.ws_url,
            "access_token": self.config.access_token,
            "group_at_only": self.config.group_at_only,
            "auto_approve_friend": self.config.auto_approve_friend,
            "audio_dir": os.path.join(workspace, "state", "voice_cache"),
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

        Connects to NapCat via WebSocket and listens for QQ messages.
        This method blocks until stop() is called.
        """
        self._running = True
        self._stats["start_time"] = datetime.now().isoformat()

        logger.info("Starting QQ Bridge...")
        logger.info(f"  Workspace: {self.workspace}")
        logger.info(f"  NapCat WS: {self.config.ws_url}")
        logger.info(f"  Voice enabled: {self.config.voice_enabled}")
        logger.info(f"  STT engine: {self.config.stt_engine}")
        logger.info(f"  TTS engine: {self.config.tts_engine}")

        # Log to Partner journal
        self.journal.log(JournalEntry(
            task_id="qq_bridge",
            task_type="system",
            task_title="QQ Bridge 启动",
            result_summary=f"ws={self.config.ws_url}, voice={self.config.voice_enabled}, stt={self.config.stt_engine}",
        ))

        try:
            self.adapter.start(on_message=self._handle_message)
        except Exception as e:
            logger.error(f"QQ Bridge failed to start: {e}")
            self._running = False
            raise
        finally:
            self._cleanup()

    def start_async(self):
        """Start the bridge in a background thread (non-blocking)."""
        self._bridge_thread = threading.Thread(target=self.start, daemon=True)
        self._bridge_thread.start()
        logger.info("QQ Bridge started in background thread")

    def stop(self):
        """Stop the bridge."""
        logger.info("Stopping QQ Bridge...")
        self._running = False
        self.adapter.stop()

    # ── Message Handling ─────────────────────────────────────────

    def _handle_message(self, msg: QQMessage):
        """Handle an incoming QQ message.

        Core pipeline:
        1. Filter: skip self messages, group non-@mentions
        2. Voice: transcribe if voice message
        3. Route: send to ConversationEngine
        4. Reply: send text or voice reply
        """
        self._stats["messages_received"] += 1

        # Skip self messages
        if msg.sender_id == self.adapter.get_self_id():
            return

        try:
            # Process based on content
            user_text = self._extract_text(msg)
            if not user_text.strip():
                return

            logger.info(f"[QQ {msg.sender_name}({msg.sender_id})] {user_text[:100]}")

            # Get response from Partner
            reply = self._get_response(msg.sender_id, user_text, bool(msg.group_id))

            # Send reply
            self._send_reply(msg, reply)

            # Log interaction
            self.journal.log(JournalEntry(
                task_id=f"qq_{msg.msg_id}",
                task_type="conversation",
                task_title=f"QQ对话: {msg.sender_name}({msg.sender_id})",
                result_summary=f"Q: {user_text[:100]} → A: {reply[:100]}",
            ))

        except Exception as e:
            logger.error(f"Message handling error: {e}", exc_info=True)
            self._stats["errors"] += 1
            # Try to send error notification
            try:
                is_group = bool(msg.group_id)
                target = msg.group_id if is_group else msg.sender_id
                self.adapter.send_text(target, "抱歉，处理消息时出了点问题。请稍后再试。", is_group)
            except Exception:
                pass

    def _extract_text(self, msg: QQMessage) -> str:
        """Extract text content from a message.

        For voice messages, transcribe using VoiceProcessor.
        """
        content = msg.content

        # Check if message contains voice segments
        if "[语音]" in content and self.config.voice_enabled:
            # Try to extract voice file from raw message
            audio_path = self._extract_voice_file(msg)
            if audio_path:
                text = self.voice.transcribe(audio_path, source_format="amr")
                # Cleanup
                try:
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                except OSError:
                    pass
                if text and not text.startswith("[STT error"):
                    self._stats["voice_transcribed"] += 1
                    return f"[语音] {text}"
                return "[语音消息 - 识别失败]"
            return "[语音消息]"

        return content

    def _extract_voice_file(self, msg: QQMessage) -> Optional[str]:
        """Extract voice file from raw message segments."""
        try:
            segments = msg.raw.get("message", [])
            for seg in segments:
                if seg.get("type") == "record":
                    file_url = seg.get("data", {}).get("file", "")
                    if file_url:
                        # Download the file
                        return self._download_file(file_url, ".amr")
        except Exception as e:
            logger.error(f"Failed to extract voice: {e}")
        return None

    def _download_file(self, url: str, ext: str = "") -> Optional[str]:
        """Download a file from URL to temp directory."""
        try:
            import urllib.request
            import hashlib
            fname = hashlib.md5(url.encode()).hexdigest() + ext
            fpath = os.path.join(self.adapter._audio_dir, fname)
            urllib.request.urlretrieve(url, fpath)
            return fpath
        except Exception as e:
            logger.error(f"Download failed: {e}")
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

    def _send_reply(self, original_msg: QQMessage, reply: str):
        """Send reply back to the user (text or voice)."""
        is_group = bool(original_msg.group_id)
        target = original_msg.group_id if is_group else original_msg.sender_id

        # Always send text
        self.adapter.send_text(target, reply, is_group)
        self._stats["messages_sent"] += 1

        # Optionally send voice reply
        if self.config.voice_reply and self.config.voice_enabled:
            self._send_voice_reply(target, reply, is_group)

    def _send_voice_reply(self, target: str, text: str, is_group: bool):
        """Generate and send a voice reply."""
        try:
            audio_path = self.voice.synthesize(text)
            if audio_path and not audio_path.startswith("[TTS error"):
                self.adapter.send_voice(target, audio_path, is_group)
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
            ctx_path = os.path.join(self.workspace, "state", "qq_contexts.json")
            with open(ctx_path, "w", encoding="utf-8") as f:
                json.dump(self._user_contexts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save contexts: {e}")

        # Cleanup temp audio files
        self.voice.cleanup_temp()

        # Log stats
        logger.info(f"QQ Bridge stats: {json.dumps(self._stats, indent=2)}")
        self.journal.log(JournalEntry(
            task_id="qq_bridge",
            task_type="system",
            task_title="QQ Bridge 关闭",
            result_summary=json.dumps(self._stats, ensure_ascii=False),
        ))

    def get_stats(self) -> Dict:
        """Get bridge statistics."""
        return {
            **self._stats,
            "active_users": len(self._user_contexts),
            "voice_engines": self.voice.get_available_engines(),
        }
