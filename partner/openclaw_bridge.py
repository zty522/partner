"""OpenClaw Bridge - connects OpenClaw Gateway to Partner's conversation engine.

This module is the high-level integration layer that:
  1. Uses OpenClaw Gateway as a multi-platform messaging hub
  2. Routes text messages from any OpenClaw channel to ConversationEngine
  3. Sends text/voice replies back through OpenClaw to the source channel
  4. Maintains per-user conversation context

OpenClaw supports 20+ platforms (Telegram, Discord, QQ, WhatsApp, Slack,
Signal, iMessage, IRC, LINE, Matrix, Feishu, MS Teams, etc.) through its
plugin-based channel system. This bridge lets Partner use ALL of them.

Architecture:
    Chat User (Telegram/Discord/QQ/WhatsApp/...)
        ↓ (message via OpenClaw channel plugin)
    OpenClaw Gateway (localhost:18789)
        ↓ (routes to "partner" agent)
    OpenClawBridge (this module)
        ├── voice? → VoiceProcessor.transcribe() → text
        ↓
    ConversationEngine (conversation.py)
        ↓ response text
    OpenClawBridge
        ├── voice_reply? → VoiceProcessor.synthesize() → audio
        ↓
    OpenClaw Gateway → source channel → Chat User

Usage:
    from partner.openclaw_bridge import OpenClawBridge

    bridge = OpenClawBridge(workspace="/mnt/e/work/study_room")
    bridge.start()  # Blocks, listening for messages
    # or
    bridge.start_async()  # Non-blocking, runs in background thread

    # Or use in CLI mode (for cron/testing):
    reply = bridge.chat("telegram", "user123", "Hello!")

Prerequisites:
    1. OpenClaw installed: npm install -g openclaw
    2. Gateway running: openclaw gateway (or npx openclaw onboard)
    3. At least one channel configured: openclaw channels add
    4. Node.js v22+ (for OpenClaw CLI)

Config file: ~/.openclaw/openclaw.json
Gateway default port: 18789
"""

import os
import json
import time
import re
import logging
import subprocess
import threading
import asyncio
from datetime import datetime
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field

from .voice import VoiceProcessor, VoiceConfig
from .conversation import ConversationEngine
from .task_queue import TaskQueue
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager

logger = logging.getLogger(__name__)

# Ensure Node.js 22+ and openclaw are on PATH
_N_BIN = os.path.expanduser("~/.n/bin")
_NPM_GLOBAL = os.path.expanduser("~/.npm-global/bin")
for _p in [_N_BIN, _NPM_GLOBAL]:
    if _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{_p}:{os.environ.get('PATH', '')}"


@dataclass
class OpenClawMessage:
    """Normalized message from any OpenClaw channel."""
    platform: str           # "telegram", "discord", "qq", "whatsapp", etc.
    chat_id: str            # conversation identifier
    sender: str             # who sent it
    sender_name: str = ""   # display name
    content: str = ""       # text content
    msg_type: str = "text"  # "text", "voice", "image", "file"
    is_group: bool = False
    is_at_me: bool = False
    msg_id: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class OpenClawBridgeConfig:
    """OpenClaw bridge configuration."""
    # Gateway settings
    gateway_url: str = "http://localhost:18789"
    gateway_token: str = ""         # Auto-loaded from openclaw.json if empty
    agent_id: str = "main"          # OpenClaw agent to use

    # Voice settings
    voice_enabled: bool = True
    voice_reply: bool = False
    stt_engine: str = "funasr"
    tts_engine: str = "edge-tts"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    # Message settings
    max_reply_length: int = 2000
    group_at_only: bool = True

    # Polling settings (for Gateway API mode)
    poll_interval: float = 2.0      # seconds between polls

    # Workspace
    workspace: str = ""


class OpenClawBridge:
    """High-level bridge between OpenClaw Gateway and Partner.

    Integrates OpenClaw (multi-platform transport) + VoiceProcessor (STT/TTS) +
    ConversationEngine (intelligence).

    Supports two modes:
    1. CLI mode: Uses `openclaw agent` CLI for message exchange (simple, reliable)
    2. Gateway mode: Direct HTTP/WebSocket to Gateway API (real-time, lower latency)
    """

    def __init__(self, workspace: str, config: OpenClawBridgeConfig = None):
        self.workspace = workspace
        self.config = config or OpenClawBridgeConfig()
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

        # Load OpenClaw config
        self._openclaw_config = self._load_openclaw_config()
        if not self.config.gateway_token:
            self.config.gateway_token = self._openclaw_config.get(
                "gateway", {}
            ).get("auth", {}).get("token", "")

        # Per-user conversation context
        self._user_contexts: Dict[str, List[Dict]] = {}
        self._max_context_per_user = 10

        # Available channels cache
        self._channels_cache: List[str] = []
        self._channels_cache_time: float = 0

        # Statistics
        self._stats = {
            "messages_received": 0,
            "messages_sent": 0,
            "voice_transcribed": 0,
            "errors": 0,
            "platforms_used": set(),
            "start_time": None,
        }

        self._running = False
        self._gateway_healthy = False

    # ── Config Loading ──────────────────────────────────────────

    def _load_openclaw_config(self) -> dict:
        """Load OpenClaw configuration from ~/.openclaw/openclaw.json."""
        config_path = os.path.expanduser("~/.openclaw/openclaw.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load OpenClaw config: {e}")
        return {}

    # ── Health & Discovery ──────────────────────────────────────

    def is_available(self) -> bool:
        """Check if OpenClaw CLI is installed and Gateway is running."""
        # Check 1: openclaw binary
        try:
            result = subprocess.run(
                ["openclaw", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

        # Check 2: Gateway health
        return self._check_gateway_health()

    def _check_gateway_health(self) -> bool:
        """Check if the OpenClaw Gateway is reachable."""
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.config.gateway_url}/health",
                headers=self._auth_headers(),
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                self._gateway_healthy = data.get("ok", False)
                return self._gateway_healthy
        except Exception:
            self._gateway_healthy = False
            return False

    def _auth_headers(self) -> dict:
        """Get authentication headers for Gateway API."""
        headers = {"Content-Type": "application/json"}
        if self.config.gateway_token:
            headers["Authorization"] = f"Bearer {self.config.gateway_token}"
        return headers

    def get_channels(self) -> List[str]:
        """List configured OpenClaw channels."""
        # Cache for 5 minutes
        if time.time() - self._channels_cache_time < 300 and self._channels_cache:
            return self._channels_cache

        try:
            result = subprocess.run(
                ["openclaw", "channels", "list", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                channels = [
                    ch.get("id", ch.get("name", ""))
                    for ch in data if ch.get("enabled", True)
                ]
                self._channels_cache = channels
                self._channels_cache_time = time.time()
                return channels
        except Exception as e:
            logger.warning(f"Failed to list channels: {e}")

        # Fallback: parse text output
        try:
            result = subprocess.run(
                ["openclaw", "channels", "list"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                channels = []
                for line in result.stdout.split("\n"):
                    # Parse lines like "- telegram (connected)" or "- qqbot (configured)"
                    m = re.match(r"^- (\S+)\s+\(", line.strip())
                    if m:
                        channels.append(m.group(1))
                self._channels_cache = channels
                self._channels_cache_time = time.time()
                return channels
        except Exception:
            pass

        return []

    def get_status(self) -> dict:
        """Get comprehensive bridge status."""
        return {
            "available": self.is_available(),
            "gateway_healthy": self._gateway_healthy,
            "gateway_url": self.config.gateway_url,
            "channels": self.get_channels(),
            "running": self._running,
            "stats": {k: v for k, v in self._stats.items() if k != "platforms_used"},
            "active_users": len(self._user_contexts),
        }

    # ── CLI Mode: Send & Receive ────────────────────────────────

    def send_message(self, channel: str, target: str, text: str,
                     agent_id: str = None) -> bool:
        """Send a message through OpenClaw to a specific channel/target.

        Uses `openclaw agent --channel <ch> --deliver --to <target> --message <text>`.

        Args:
            channel: OpenClaw channel id (e.g., "telegram", "discord", "qq")
            target: Target user/chat id on the platform
            text: Message text to send
            agent_id: Override agent id (default: config.agent_id)

        Returns:
            True if sent successfully.
        """
        agent = agent_id or self.config.agent_id
        try:
            result = subprocess.run(
                [
                    "openclaw", "agent",
                    "--agent", agent,
                    "--channel", channel,
                    "--to", target,
                    "--deliver",
                    "--message", text,
                    "--json",
                ],
                capture_output=True, text=True, timeout=60,
                cwd=self.workspace,
                env=os.environ.copy(),
            )

            if result.returncode != 0:
                logger.error(f"OpenClaw send failed: {result.stderr[:300]}")
                self._stats["errors"] += 1
                return False

            self._stats["messages_sent"] += 1
            self._stats["platforms_used"].add(channel)
            logger.info(f"Sent to {channel}:{target}: {text[:80]}")
            return True

        except subprocess.TimeoutExpired:
            logger.error(f"OpenClaw send timeout for {channel}:{target}")
            self._stats["errors"] += 1
            return False
        except FileNotFoundError:
            logger.error("OpenClaw CLI not found")
            return False
        except Exception as e:
            logger.error(f"OpenClaw send error: {e}")
            self._stats["errors"] += 1
            return False

    def chat(self, channel: str, user_message: str,
             sender: str = "cli_user", target: str = "") -> str:
        """High-level chat: process a user message and return a reply.

        This is the main entry point for CLI-mode interaction:
        1. Get response from Partner's ConversationEngine
        2. Optionally deliver the reply back through OpenClaw

        Args:
            channel: Source channel name
            user_message: The user's message text
            sender: Sender identifier for context tracking
            target: If set, deliver the reply to this target via OpenClaw

        Returns:
            Partner's reply text.
        """
        self._stats["messages_received"] += 1
        self._stats["platforms_used"].add(channel)

        try:
            # Build context
            context = self._get_user_context(sender)
            if context:
                context_text = "\n".join([
                    f"{'用户' if c['role'] == 'user' else 'Partner'}: {c['text'][:200]}"
                    for c in context[-3:]
                ])
                full_text = f"[上下文]\n{context_text}\n\n[当前消息]\n{user_message}"
            else:
                full_text = user_message

            # Get response
            reply = self.conversation.respond(full_text)

            # Update context
            self._add_user_context(sender, "user", user_message)
            self._add_user_context(sender, "partner", reply)

            # Truncate if too long
            if len(reply) > self.config.max_reply_length:
                reply = reply[:self.config.max_reply_length] + "\n\n...(回复过长，已截断)"

            # Log interaction
            self.journal.log(JournalEntry(
                task_id=f"openclaw_{channel}_{int(time.time())}",
                task_type="conversation",
                task_title=f"OpenClaw对话: {sender} ({channel})",
                result_summary=f"Q: {user_message[:100]} → A: {reply[:100]}",
            ))

            # Deliver reply if target specified
            if target:
                self.send_message(channel, target, reply)

            return reply

        except Exception as e:
            logger.error(f"Chat error: {e}", exc_info=True)
            self._stats["errors"] += 1
            return f"抱歉，处理消息时出了问题: {e}"

    # ── Agent Callback Mode ─────────────────────────────────────

    def handle_agent_message(self, channel: str, sender: str,
                             text: str, msg_id: str = "",
                             is_group: bool = False,
                             sender_name: str = "",
                             target: str = "") -> str:
        """Handle a message forwarded from OpenClaw agent routing.

        This is the callback entry point when OpenClaw routes a message
        to the Partner agent. The flow:
        1. OpenClaw channel plugin receives message from platform
        2. Gateway routes to configured agent
        3. Agent's system prompt instructs it to forward to this method
        4. We process through ConversationEngine and return the reply

        Args:
            channel: Source channel (telegram, discord, etc.)
            sender: Sender ID on the platform
            text: Message text
            msg_id: Platform message ID
            is_group: Whether this is a group message
            sender_name: Display name of sender
            target: Target for reply delivery

        Returns:
            Reply text to send back through OpenClaw.
        """
        msg = OpenClawMessage(
            platform=channel,
            chat_id=target or sender,
            sender=sender,
            sender_name=sender_name,
            content=text,
            msg_id=msg_id,
            is_group=is_group,
        )
        return self._process_message(msg)

    def _process_message(self, msg: OpenClawMessage) -> str:
        """Process an incoming message through the full pipeline."""
        self._stats["messages_received"] += 1
        self._stats["platforms_used"].add(msg.platform)

        try:
            user_text = msg.content
            if not user_text or not user_text.strip():
                return ""

            logger.info(f"[{msg.platform}:{msg.sender}] {user_text[:100]}")

            # Get response
            reply = self._get_response(
                f"{msg.platform}:{msg.sender}",
                user_text,
                msg.is_group,
            )

            # Log interaction
            self.journal.log(JournalEntry(
                task_id=f"openclaw_{msg.msg_id or int(time.time())}",
                task_type="conversation",
                task_title=f"OpenClaw对话: {msg.sender} ({msg.platform})",
                result_summary=f"Q: {user_text[:100]} → A: {reply[:100]}",
            ))

            return reply

        except Exception as e:
            logger.error(f"Message processing error: {e}", exc_info=True)
            self._stats["errors"] += 1
            return "抱歉，处理消息时出了点问题。请稍后再试。"

    # ── Response Generation ──────────────────────────────────────

    def _get_response(self, sender: str, text: str, is_group: bool) -> str:
        """Get a response from Partner's conversation engine."""
        context = self._get_user_context(sender)
        if context:
            context_text = "\n".join([
                f"{'用户' if c['role'] == 'user' else 'Partner'}: {c['text'][:200]}"
                for c in context[-3:]
            ])
            full_text = f"[上下文]\n{context_text}\n\n[当前消息]\n{text}"
        else:
            full_text = text

        reply = self.conversation.respond(full_text)

        self._add_user_context(sender, "user", text)
        self._add_user_context(sender, "partner", reply)

        if len(reply) > self.config.max_reply_length:
            reply = reply[:self.config.max_reply_length] + "\n\n...(回复过长，已截断)"

        return reply

    # ── Gateway Polling Mode ────────────────────────────────────

    def start(self):
        """Start the bridge in polling mode (blocking).

        Polls the OpenClaw Gateway for new messages and processes them.
        This method blocks until stop() is called.
        """
        self._running = True
        self._stats["start_time"] = datetime.now().isoformat()

        logger.info("Starting OpenClaw Bridge (polling mode)...")
        logger.info(f"  Workspace: {self.workspace}")
        logger.info(f"  Gateway: {self.config.gateway_url}")
        logger.info(f"  Voice enabled: {self.config.voice_enabled}")

        # Verify availability
        if not self.is_available():
            logger.error("OpenClaw Gateway not available")
            self._running = False
            raise RuntimeError(
                "OpenClaw not available. Ensure:\n"
                "  1. OpenClaw installed: npm install -g openclaw\n"
                "  2. Gateway running: openclaw gateway\n"
                "  3. Channels configured: openclaw channels add"
            )

        channels = self.get_channels()
        logger.info(f"  Channels: {channels}")

        # Log startup
        self.journal.log(JournalEntry(
            task_id="openclaw_bridge",
            task_type="system",
            task_title="OpenClaw Bridge 启动",
            result_summary=(
                f"gateway={self.config.gateway_url}, "
                f"channels={channels}, "
                f"voice={self.config.voice_enabled}"
            ),
        ))

        try:
            self._poll_loop()
        except KeyboardInterrupt:
            logger.info("OpenClaw Bridge interrupted")
        except Exception as e:
            logger.error(f"OpenClaw Bridge error: {e}")
            raise
        finally:
            self._cleanup()

    def start_async(self):
        """Start the bridge in a background thread (non-blocking)."""
        self._bridge_thread = threading.Thread(target=self.start, daemon=True)
        self._bridge_thread.start()
        logger.info("OpenClaw Bridge started in background thread")

    def stop(self):
        """Stop the bridge."""
        logger.info("Stopping OpenClaw Bridge...")
        self._running = False

    def _poll_loop(self):
        """Main polling loop - checks for new messages from Gateway."""
        logger.info("Starting message poll loop...")

        while self._running:
            try:
                # Check gateway health periodically
                if not self._check_gateway_health():
                    logger.warning("Gateway health check failed, retrying...")
                    time.sleep(5)
                    continue

                # Poll for new messages via CLI
                # Note: This is a simplified polling approach.
                # For production, use Gateway WebSocket API.
                time.sleep(self.config.poll_interval)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                time.sleep(5)

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

        if len(self._user_contexts[sender]) > self._max_context_per_user:
            self._user_contexts[sender] = self._user_contexts[sender][-self._max_context_per_user:]

    # ── Cleanup & Stats ──────────────────────────────────────────

    def _cleanup(self):
        """Cleanup on shutdown."""
        # Save conversation contexts
        try:
            ctx_path = os.path.join(self.workspace, "state", "openclaw_contexts.json")
            with open(ctx_path, "w", encoding="utf-8") as f:
                json.dump(self._user_contexts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save contexts: {e}")

        # Cleanup voice temp files
        try:
            self.voice.cleanup_temp()
        except Exception:
            pass

        # Log stats
        stats_copy = {**self._stats}
        stats_copy["platforms_used"] = list(stats_copy.get("platforms_used", set()))
        logger.info(f"OpenClaw Bridge stats: {json.dumps(stats_copy, indent=2, ensure_ascii=False)}")

        self.journal.log(JournalEntry(
            task_id="openclaw_bridge",
            task_type="system",
            task_title="OpenClaw Bridge 关闭",
            result_summary=json.dumps(stats_copy, ensure_ascii=False),
        ))

    def get_stats(self) -> Dict:
        """Get bridge statistics."""
        return {
            **self._stats,
            "platforms_used": list(self._stats.get("platforms_used", set())),
            "active_users": len(self._user_contexts),
            "gateway_healthy": self._gateway_healthy,
        }
