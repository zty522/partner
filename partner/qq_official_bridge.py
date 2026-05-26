"""QQ Official Bot Bridge - connects QQ Official Bot to Partner's conversation engine.

This module is the high-level integration layer that:
  1. Starts the QQ Official Bot adapter
  2. Routes text messages to ConversationEngine
  3. Sends text replies back through QQ
  4. Maintains per-user conversation context

Usage:
    from partner.qq_official_bridge import QQQfficialBridge

    bridge = QQQfficialBridge(workspace="/path/to/workspace")
    bridge.configure(app_id="YOUR_APP_ID", app_secret="YOUR_APP_SECRET")
    bridge.start()  # Blocks, listening for messages

Architecture:
    QQ User → QQ Bot Platform → WebSocket → QQQfficialBot → QQQfficialBridge → ConversationEngine
                                                                                         ↓
    QQ User ← QQ Bot Platform ← REST API ← QQQfficialBot ← QQQfficialBridge ← ConversationEngine
"""

import os
import json
import time
import asyncio
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, List

from .qq_official_bot import QQQfficialBot, QQMessage, QQMessageType, QQBotInfo
from .conversation import ConversationEngine
from .task_queue import TaskQueue
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager

logger = logging.getLogger(__name__)


@dataclass
class QQQfficialBridgeConfig:
    """QQ Official Bridge configuration."""
    # Bot credentials (from q.qq.com)
    app_id: str = ""
    app_secret: str = ""

    # Connection settings
    is_sandbox: bool = False
    auto_reconnect: bool = True

    # Message settings
    max_reply_length: int = 2000
    group_at_only: bool = True  # In groups, only respond when @mentioned

    # Workspace
    workspace: str = ""


class QQQfficialBridge:
    """High-level bridge between QQ Official Bot and Partner.

    Integrates QQQfficialBot (transport) + ConversationEngine (intelligence).
    """

    def __init__(self, workspace: str, config: QQQfficialBridgeConfig = None):
        self.workspace = workspace
        self.config = config or QQQfficialBridgeConfig()
        self.config.workspace = workspace

        # State
        state_dir = os.path.join(workspace, "state")
        os.makedirs(state_dir, exist_ok=True)

        # Initialize Partner components
        self.task_queue = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        self.knowledge = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
        self.journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        self.state_manager = StateManager(state_dir)
        self.conversation = ConversationEngine(
            self.journal, self.knowledge, self.task_queue, self.state_manager,
            workspace=workspace,
        )

        # Agent adapter for LLM-powered conversation
        self._adapter = None

        # Initialize QQ Bot
        self._bot: Optional[QQOfficialBot] = None
        self._running = False

        # Per-user conversation context
        self._user_contexts: Dict[str, List[Dict]] = {}
        self._max_context_per_user = 10

        # Stats
        self._stats = {
            "messages_received": 0,
            "messages_sent": 0,
            "errors": 0,
            "start_time": None,
        }

    # ── Configuration ──────────────────────────────────────────────

    def configure(self, app_id: str, app_secret: str, is_sandbox: bool = False):
        """Configure or update bot credentials.

        Args:
            app_id: Bot AppID from q.qq.com developer console
            app_secret: Bot AppSecret from q.qq.com developer console
            is_sandbox: Use sandbox API (for testing)
        """
        self.config.app_id = app_id
        self.config.app_secret = app_secret
        self.config.is_sandbox = is_sandbox
        logger.info(f"QQ Official Bridge configured: app_id={app_id}, sandbox={is_sandbox}")

    def load_config_from_file(self, config_path: str) -> bool:
        """Load QQ configuration from a JSON file.

        Expected format:
        {
            "app_id": "...",
            "app_secret": "...",
            "is_sandbox": false
        }
        """
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.config.app_id = data.get("app_id", self.config.app_id)
            self.config.app_secret = data.get("app_secret", self.config.app_secret)
            self.config.is_sandbox = data.get("is_sandbox", self.config.is_sandbox)
            self.config.auto_reconnect = data.get("auto_reconnect", self.config.auto_reconnect)
            logger.info(f"QQ config loaded from: {config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load QQ config: {e}")
            return False

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self):
        """Start the bridge (blocking).

        Connects to QQ Bot Platform and listens for messages.
        """
        if not self.config.app_id or not self.config.app_secret:
            logger.error("QQ Official Bot not configured. Call configure() first.")
            print("❌ QQ官方机器人未配置，请先设置 AppID 和 AppSecret")
            return

        self._running = True
        self._stats["start_time"] = datetime.now().isoformat()

        logger.info("Starting QQ Official Bridge...")
        logger.info(f"  Workspace: {self.workspace}")
        logger.info(f"  AppID: {self.config.app_id}")
        logger.info(f"  Sandbox: {self.config.is_sandbox}")

        # Log to Partner journal
        self.journal.log(JournalEntry(
            task_id="qq_bridge",
            task_type="system",
            task_title="QQ Official Bridge 启动",
            result_summary=f"app_id={self.config.app_id}, sandbox={self.config.is_sandbox}",
        ))

        # Initialize bot
        self._bot = QQQfficialBot(
            app_id=self.config.app_id,
            app_secret=self.config.app_secret,
            is_sandbox=self.config.is_sandbox,
            auto_reconnect=self.config.auto_reconnect,
        )
        self._bot.set_message_handler(self._handle_message)
        self._bot.set_ready_handler(self._handle_ready)
        self._bot.set_error_handler(self._handle_error)

        # Start notification poller
        self._start_notification_poller()

        try:
            print("🤖 QQ 机器人正在连接...")
            self._bot.start()
        except Exception as e:
            logger.error(f"QQ Bridge failed: {e}")
            print(f"  ❌ 启动失败: {e}")
            self._running = False
            raise
        finally:
            self._cleanup()

    def start_async(self):
        """Start the bridge in a background thread (non-blocking)."""
        thread = threading.Thread(target=self.start, daemon=True)
        thread.start()
        logger.info("QQ Official Bridge started in background thread")

    def stop(self):
        """Stop the bridge."""
        logger.info("Stopping QQ Official Bridge...")
        self._running = False
        if self._bot:
            self._bot.stop()

    def _start_notification_poller(self):
        """Start a background thread that checks for pending notifications."""
        import threading
        def poll():
            notif_dir = os.path.join(self.workspace, "state", "notifications")
            pending_file = os.path.join(self.workspace, "state", "pending_notifications.json")
            while self._running:
                try:
                    # Load existing pending notifications
                    pending_notifs = []
                    if os.path.exists(pending_file):
                        try:
                            with open(pending_file) as f:
                                pending_notifs = json.load(f)
                        except Exception:
                            pending_notifs = []

                    # Check new notifications
                    if os.path.exists(notif_dir):
                        for fname in sorted(os.listdir(notif_dir)):
                            if fname.endswith(".json"):
                                fpath = os.path.join(notif_dir, fname)
                                try:
                                    with open(fpath) as f:
                                        notif = json.load(f)
                                    # Add to pending queue with timestamp
                                    pending_notifs.append({
                                        "timestamp": datetime.now().isoformat(),
                                        "type": notif.get("type", "daily"),
                                        "summary": notif.get("summary", ""),
                                        "details": notif.get("details", []),
                                        "next_task": notif.get("next_task", ""),
                                        "pending_count": notif.get("pending_count", 0),
                                    })
                                    os.remove(fpath)
                                except Exception:
                                    try:
                                        os.remove(fpath)
                                    except Exception:
                                        pass

                    # Save pending notifications (max 10, keep newest)
                    if pending_notifs:
                        pending_notifs = pending_notifs[-10:]
                        os.makedirs(os.path.dirname(pending_file), exist_ok=True)
                        with open(pending_file, "w") as f:
                            json.dump(pending_notifs, f, indent=2, ensure_ascii=False)

                except Exception:
                    pass
                import time
                time.sleep(60)
        t = threading.Thread(target=poll, daemon=True)
        t.start()

    # ── Handlers ──────────────────────────────────────────────────

    def _handle_ready(self, bot_info: QQBotInfo):
        """Called when bot successfully connects and is ready."""
        logger.info(f"Bot ready: {bot_info.name} ({bot_info.id})")
        self.journal.log(JournalEntry(
            task_id="qq_bridge",
            task_type="system",
            task_title="QQ 机器人就绪",
            result_summary=f"机器人: {bot_info.name} ({bot_info.id})",
        ))

    def _handle_error(self, error: Exception):
        """Called when an error occurs."""
        logger.error(f"QQ Bot error: {error}")

    def _handle_message(self, msg: QQMessage):
        """Handle an incoming QQ message.

        Pipeline:
        1. Save user context (so cron can send reports)
        2. Extract text content
        3. Get response from ConversationEngine
        4. Send reply
        5. Log interaction
        """
        self._stats["messages_received"] += 1

        try:
            # Save user context for cron report delivery
            user_ctx_path = os.path.join(self.workspace, "state", "qq_user_context.json")
            try:
                with open(user_ctx_path, "w") as f:
                    json.dump({
                        "openid": msg.sender_id,
                        "name": msg.sender_name,
                        "last_message_at": datetime.now().isoformat(),
                        "last_msg_id": msg.msg_id,
                        "message_type": msg.message_type.value,
                    }, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            user_text = msg.content
            if not user_text.strip():
                return

            logger.info(f"[QQ {msg.sender_name}({msg.sender_id})] {user_text[:100]}")

            # Get response from Partner
            reply = self._get_response(msg.sender_id, user_text, msg.message_type)

            # Save dialogue to workspace
            try:
                from .workspace_manager import append_dialogue
                append_dialogue(self.workspace, msg.sender_name, user_text, reply, platform="qq")
            except Exception:
                pass

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
                error_text = "抱歉，处理消息时出了点问题。请稍后再试。"
                asyncio.run_coroutine_threadsafe(
                    self._bot.send_message(
                        msg.sender_id if msg.message_type == QQMessageType.PRIVATE else msg.group_id,
                        error_text,
                        msg.message_type,
                    ),
                    self._bot._loop if self._bot else None,
                )
            except Exception:
                pass

    # ── Response Generation ───────────────────────────────────────

    def _get_response(self, sender: str, text: str, msg_type: QQMessageType) -> str:
        """Get a response using LLM via agent adapter.

        Uses Hermes (or configured backend) for natural conversation.
        Falls back to ConversationEngine if adapter unavailable.
        """
        # Try LLM-powered response first
        llm_reply = self._llm_chat(sender, text)
        if llm_reply:
            self._add_user_context(sender, "user", text)
            reply = self._simplify_response(llm_reply)
            self._add_user_context(sender, "partner", reply)
            return reply

        # Fallback: ConversationEngine
        style_prompt = "用简短自然的口语回复"
        context = self._get_user_context(sender)
        if context:
            ctx = "\n".join(f"{'用户' if c['role']=='user' else 'Partner'}: {c['text'][:200]}" for c in context[-3:])
            full_text = f"[上下文]\n{ctx}\n\n[当前消息]\n{text}\n\n[{style_prompt}]"
        else:
            full_text = f"{text}\n\n[{style_prompt}]"
        reply = self.conversation.respond(full_text)
        self._add_user_context(sender, "user", text)
        reply = self._simplify_response(reply)
        self._add_user_context(sender, "partner", reply)
        return reply

    def _llm_chat(self, sender: str, text: str) -> Optional[str]:
        """Use agent adapter for LLM-powered natural conversation."""
        try:
            if self._adapter is None:
                # Read backend from config
                cfg_path = os.path.join(self.workspace, "partner_config.json")
                if not os.path.exists(cfg_path):
                    return None
                with open(cfg_path) as f:
                    cfg = json.load(f)
                backend = cfg.get("agent", {}).get("backend", cfg.get("backend", "hermes"))
                from .adapter import create_adapter
                self._adapter = create_adapter(backend, self.workspace)

            context = self._get_user_context(sender)
            ctx_str = ""
            if context:
                ctx_str = "\n".join(
                    f"用户: {c['text'][:200]}" if c['role'] == 'user' else f"你: {c['text'][:200]}"
                    for c in context[-5:]
                )
                ctx_str = f"\n历史对话:\n{ctx_str}\n"

            # Check for pending notifications
            notif_str = ""
            pending_file = os.path.join(self.workspace, "state", "pending_notifications.json")
            if os.path.exists(pending_file):
                try:
                    with open(pending_file) as f:
                        notifs = json.load(f)
                    if notifs:
                        items = []
                        for n in notifs[-3:]:  # Last 3 notifications
                            summary = n.get("summary", "")
                            if summary:
                                items.append(f"- {summary}")
                        if items:
                            notif_str = f"\n\n⚠️ 你不在的时候有这些研究进展:\n" + "\n".join(items)
                        # Clear after showing
                        os.remove(pending_file)
                except Exception:
                    pass

            prompt = f"""你是 Partner 本人，不是 Hermes，不是 AI 助手。你就是用户的私人研究伙伴。

关于你自己的信息：
- 你的代码在 /mnt/e/work/partner
- 刚刚完成了大版本升级：增加了 QQ 官方机器人支持（partner/qq_official_bot.py），重构了 setup 流程，优化了 CLI 为 partner setup/partner status/partner bot 三条命令
- 你通过 QQ 官方机器人（partner01）和用户聊天
- 你有一个知识库、任务队列、研究循环
- 你持续在后台自主研究

规则：
1. 用朋友间聊天的口吻，简短自然
2. 不知道的事就说不知道，不要编造
3. 用户不问细节就不要主动详细展开
4. 不要查看代码文件——凭你已有的记忆回答
5. 控制在 50 字以内第一句先回应

{ctx_str}
{notif_str}
用户说: {text}"""

            result = self._adapter.chat(prompt)
            if result and not result.startswith("Error"):
                return result
        except Exception as e:
            logger.warning(f"LLM chat failed: {e}")
        return None

    @staticmethod
    def _simplify_response(reply: str) -> str:
        """Post-process response to be concise and conversational."""
        # Hard cap at 500 chars for QQ
        max_len = 500
        if len(reply) > max_len:
            # Try to find a natural break point
            break_at = reply.rfind("。", 0, max_len - 20)
            if break_at > max_len // 2:
                reply = reply[:break_at + 1]
            else:
                reply = reply[:max_len - 10] + "……"

        # Remove very long markdown-style list items (more than 3 items)
        lines = reply.split("\n")
        short_lines = []
        list_count = 0
        for line in lines:
            if line.strip().startswith(("•", "-", "  ", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
                list_count += 1
                if list_count > 3:
                    if list_count == 4:
                        short_lines.append("  ……还有其他内容")
                    continue
            short_lines.append(line)
        reply = "\n".join(short_lines)

        return reply.strip()

    def _send_reply(self, original_msg: QQMessage, reply: str):
        """Send reply back to the user."""
        if not self._bot:
            logger.error("Bot not initialized")
            return

        # Schedule async send
        if self._bot.get_event_loop() and self._bot.get_event_loop().is_running():
            asyncio.run_coroutine_threadsafe(
                self._bot.reply_message(original_msg, reply),
                self._bot.get_event_loop(),
            )
            self._stats["messages_sent"] += 1

    # ── User Context Management ───────────────────────────────────

    def _get_user_context(self, sender: str) -> List[Dict]:
        return self._user_contexts.get(sender, [])

    def _add_user_context(self, sender: str, role: str, text: str):
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

    # ── Cleanup & Stats ───────────────────────────────────────────

    def _cleanup(self):
        """Cleanup on shutdown."""
        # Save conversation contexts
        try:
            ctx_path = os.path.join(self.workspace, "state", "qq_contexts.json")
            with open(ctx_path, "w", encoding="utf-8") as f:
                json.dump(self._user_contexts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save contexts: {e}")

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
            "configured": bool(self.config.app_id),
            "bot_info": str(self._bot.get_bot_info()) if self._bot else None,
        }

    def get_config_dict(self) -> Dict:
        """Get current config as dict (without secret)."""
        return {
            "app_id": self.config.app_id,
            "is_sandbox": self.config.is_sandbox,
            "auto_reconnect": self.config.auto_reconnect,
            "max_reply_length": self.config.max_reply_length,
        }

    def send_proactive(self, to_user: str, content: str, msg_type: QQMessageType = QQMessageType.PRIVATE) -> bool:
        """Send a proactive message to a QQ user (not in reply to a message)."""
        if self._bot:
            return self._bot.send_proactive(to_user, content, msg_type)
        return False

    def send_file_proactive(self, to_user: str, file_data: bytes,
                             file_type: int = 4,
                             msg_type: QQMessageType = QQMessageType.PRIVATE,
                             text_content: str = "") -> bool:
        """Send a file to a QQ user proactively (not in reply to a message).

        Two-step upload+sends via passive quota-friendly method.
        """
        if not self._bot or not self._bot.get_event_loop() or not self._bot.get_event_loop().is_running():
            logger.error("Bot event loop not running, cannot send file")
            return False
        import asyncio
        future = asyncio.run_coroutine_threadsafe(
            self._bot.send_file(to_user, file_data, file_type, msg_type, text_content=text_content),
            self._bot.get_event_loop(),
        )
        try:
            return future.result(timeout=30)
        except Exception as e:
            logger.error(f"Proactive file send failed: {e}")
            return False

    def reply_with_file(self, msg: QQMessage, file_data: bytes,
                         file_type: int = 4, text_content: str = "") -> bool:
        """Reply to a QQ message with a file attachment.

        Uses msg_id + msg_type=7 for passive-reply file sending.
        """
        if not self._bot or not self._bot.get_event_loop() or not self._bot.get_event_loop().is_running():
            logger.error("Bot event loop not running, cannot reply with file")
            return False
        import asyncio
        future = asyncio.run_coroutine_threadsafe(
            self._bot.reply_with_file(msg, file_data, file_type, text_content),
            self._bot.get_event_loop(),
        )
        try:
            return future.result(timeout=30)
        except Exception as e:
            logger.error(f"Reply with file failed: {e}")
            return False


# Helper: create bridge from config file
def create_bridge(workspace: str, config_path: str = None) -> QQQfficialBridge:
    """Create and configure a QQQfficialBridge.

    Args:
        workspace: Partner workspace path
        config_path: Optional path to QQ config JSON file

    Returns:
        Configured QQQfficialBridge instance
    """
    bridge = QQQfficialBridge(workspace)

    if config_path and os.path.exists(config_path):
        bridge.load_config_from_file(config_path)
    elif not bridge.config.app_id:
        # Try workspace-level config
        ws_config = os.path.join(workspace, "qq_config.json")
        if os.path.exists(ws_config):
            bridge.load_config_from_file(ws_config)

    return bridge
