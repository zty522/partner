"""NapCat Bridge - connects NapCat OneBot to Partner's conversation engine.

This module wraps NapCatOneBot and wires it to ConversationEngine,
so Partner can receive QQ messages via NapCat (local DLL injection).

Architecture:
    QQ User → QQ Desktop (Windows) → NapCat DLL → WebSocket → NapCatOneBot
                                                                     ↓
    QQ User ← NapCat response         ← send_message ← NapCatBridge → ConversationEngine

Usage:
    from partner.napcat_bridge import NapCatBridge

    bridge = NapCatBridge(workspace="/path/to/workspace")
    bridge.configure(ws_url="ws://localhost:3001")
    bridge.start()
"""

import os
import json
import logging
import threading
from datetime import datetime
from typing import Optional

from .napcat_onebot import NapCatOneBot, NapCatBotConfig, NapCatMessage
from .conversation import ConversationEngine
from .task_queue import TaskQueue
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager

logger = logging.getLogger(__name__)


class NapCatBridge:
    """Bridge between NapCat OneBot and Partner ConversationEngine."""

    def __init__(self, workspace: str, ws_url: str = "ws://localhost:3001"):
        self.workspace = workspace
        self.ws_url = ws_url
        self._running = False

        # Partner components
        state_dir = os.path.join(workspace, "state")
        os.makedirs(state_dir, exist_ok=True)

        self.task_queue = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        self.knowledge = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
        self.journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        self.state_manager = StateManager(state_dir)
        self.conversation = ConversationEngine(
            self.journal, self.knowledge, self.task_queue, self.state_manager,
            workspace=workspace,
        )

        # NapCat bot
        config = NapCatBotConfig(ws_url=ws_url)
        self._bot = NapCatOneBot(config=config)
        self._bot.set_message_handler(self._handle_message)
        self._bot.set_ready_handler(self._handle_ready)
        self._bot.set_error_handler(self._handle_error)

    def configure(self, ws_url: str):
        self.ws_url = ws_url
        self._bot.config.ws_url = ws_url

    def load_config_from_file(self, config_path: str) -> bool:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ws_url = data.get("ws_url", self.ws_url)
            if not ws_url.startswith(("ws://", "wss://")):
                ws_url = f"ws://{ws_url}"
            self.configure(ws_url)
            logger.info(f"NapCat config loaded from: {config_path} (ws_url={ws_url})")
            return True
        except Exception as e:
            logger.error(f"Failed to load NapCat config: {e}")
            return False

    def start(self):
        self._running = True
        logger.info(f"Starting NapCat Bridge... ws_url={self.ws_url}")
        print(f"🤖 NapCat 机器人正在连接 {self.ws_url}...")
        try:
            self._bot.start()
        except Exception as e:
            logger.error(f"NapCat Bridge failed: {e}")
            print(f"  ❌ 启动失败: {e}")
            self._running = False

    def start_async(self):
        thread = threading.Thread(target=self.start, daemon=True)
        thread.start()

    def stop(self):
        self._running = False
        self._bot.stop()
        logger.info("NapCat Bridge stopped")

    def _handle_ready(self, bot_info):
        logger.info(f"NapCat bot ready: {bot_info}")
        print(f"  ✅ NapCat 连接成功")
        self.journal.log(JournalEntry(
            task_id="napcat_bridge",
            task_type="system",
            task_title="NapCat 机器人就绪",
            result_summary=f"WebSocket: {self.ws_url}",
        ))

    def _handle_error(self, error: Exception):
        logger.error(f"NapCat bot error: {error}")
        print(f"  ❌ NapCat 错误: {error}", file=__import__('sys').stderr)

    def _handle_message(self, msg: NapCatMessage):
        """Handle incoming NapCat message."""
        logger.info(f"[NapCat {msg.sender_name}({msg.sender_id})] {msg.content[:100]}")

        # Get response from conversation engine
        reply = self.conversation.respond(msg.content)

        # Send reply
        target_id = msg.group_id if msg.group_id else msg.sender_id
        if msg.group_id:
            self._bot.send_group_msg(target_id, reply)
        else:
            self._bot.send_private_msg(target_id, reply)

        # Log
        self.journal.log(JournalEntry(
            task_id=f"napcat_{msg.msg_id}",
            task_type="conversation",
            task_title=f"NapCat对话: {msg.sender_name}({msg.sender_id})",
            result_summary=f"Q: {msg.content[:100]} → A: {reply[:100]}",
        ))
