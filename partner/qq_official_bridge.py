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
import subprocess
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
        }
        self._force_run_triggered = False
        self._last_task_queued_at = 0  # timestamp of last task queue (suppress heartbeat double-report)

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
        2. Detect if message is a task request or casual chat
        3. If task → queue it, return confirmation
        4. If chat → get LLM response, send reply
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

            logger.info(f"[QQ {msg.sender_name}({msg.sender_id})] {user_text[:100]}\n")

            # Step 0: Special commands (handled directly, no LLM needed)
            special_reply = self._handle_special_command(user_text, msg)
            if special_reply:  # Has a real reply
                self._send_reply(msg, special_reply)
                return
            if special_reply == "":  # Pattern matched, go to LLM chat
                self._force_run_triggered = True
            else:
                self._force_run_triggered = False

            # Step 1: Use LLM to classify: task request or casual chat?
            # If force_run was just triggered, skip TASK (research already started)
            _task_queued = False
            if not self._force_run_triggered:
                intent = self._classify_intent(user_text, msg.sender_id)
                if intent == "TASK":
                    reply = self._queue_task(user_text, msg)
                    if reply:
                        self._send_reply(msg, reply)
                        return
                    # Empty reply → fall through to LLM chat
                    _task_queued = True
            if not _task_queued:
                self._force_run_triggered = False

            # Step 2: Normal chat — get LLM response directly (no double-reply)
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

            prompt = f"""你是 Partner，我的私人研究伙伴。你一直在后台自己研究东西，每 {self._get_interval_minutes()} 分钟醒一次。

回复规则（严格遵守）：
- 像好朋友聊天一样说话，自然口语化，像真人
- 不要用emoji开头每一句（一个👌😊✅偶尔点缀可以）
- 不说代码、diff、JSON、文件路径、配置文件内容
- 绝对不要用markdown格式：不用**加粗**、*斜体*、`代码`、#标题、-列表、>引用
- 不用"收到"、"好的"、"明白了"这类机械回复开头
- 用户让你推进项目 → 直接说"好，开始弄"然后执行，别问方向
- 用户让你继续 → 直接继续，不用确认
- 不知道就说不知道，不编造

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
        """Post-process response to be concise and conversational.

        Strips all markdown formatting so QQ receives clean text.
        """
        import re

        # Strip markdown thoroughly (both single and double asterisks)
        reply = re.sub(r'\*{1,2}(.+?)\*{1,2}', r'\1', reply)       # **bold** and *italic* → plain
        reply = re.sub(r'__(\S.*?\S)__', r'\1', reply)             # __underline__
        reply = re.sub(r'~~(.+?)~~', r'\1', reply)                 # ~~strikethrough~~
        reply = re.sub(r'`{1,3}[^`]*?`{1,3}', '', reply)           # `code` and ```code``` → remove
        reply = re.sub(r'^#{1,6}\s+', '', reply, flags=re.MULTILINE)  # # heading → remove heading marker
        reply = re.sub(r'^>\s?', '', reply, flags=re.MULTILINE)    # > blockquote → remove
        reply = re.sub(r'^(\s*[-*+])\s+', '  ', reply, flags=re.MULTILINE)  # - list → indent only
        reply = re.sub(r'\n{3,}', '\n\n', reply)                   # Collapse excessive newlines

        # Hard cap at 500 chars for QQ
        max_len = 500
        if len(reply) > max_len:
            break_at = reply.rfind("。", 0, max_len - 20)
            if break_at > max_len // 2:
                reply = reply[:break_at + 1]
            else:
                reply = reply[:max_len - 10] + "……"

        # Limit list items to at most 3
        lines = reply.split("\n")
        short_lines = []
        list_count = 0
        for line in lines:
            stripped = line.strip()
            if stripped and stripped[0] in ("•", "-", "·", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0"):
                list_count += 1
                if list_count > 3:
                    if list_count == 4:
                        short_lines.append("  ……")
                    continue
            short_lines.append(line)
        reply = "\n".join(short_lines)

        return reply.strip()

    # ── Special Commands (direct, no LLM) ─────────────────────────

    def _handle_special_command(self, text: str, msg: QQMessage) -> Optional[str]:
        """Handle special action commands directly without LLM.

        Returns a reply string if handled, None if not matched.
        If reply is empty string '', the pattern was matched but reply
        should come from LLM (fall through to normal chat, skip TASK).
        """
        t = text.strip()

        # Clear queue: 清空队列, 清空, 清除所有任务, 清除队列
        clear_patterns = ["清空队列", "清空", "清除队列", "清除所有任务",
                          "清空所有", "全部清空", "队列清空",
                          "清空之前的", "清空前面"]
        for p in clear_patterns:
            if p in t:
                return self._clear_queue(msg)

        # Force run: 立即运行, 直接开始, 现在开始, 马上开始, 立即执行,
        # 推进, 继续推进, 继续做, 继续+项目名, 跑起来
        run_patterns = ["立即运行", "直接开始", "现在开始", "马上开始",
                        "立即执行", "立刻开始", "立刻运行",
                        "开始执行", "立即开始", "不要等", "不用等",
                        "继续推进", "继续做", "推进"]
        for p in run_patterns:
            if p in t:
                self._force_run(msg)
                return ""  # Matched, but let LLM generate reply

        # Change interval: 间隔改成X, 间隔改为X, 设定间隔X, 修改间隔X
        import re
        interval_match = re.search(r'(?:间隔|心跳).*?(\d+)\s*分', t)
        if interval_match:
            minutes = int(interval_match.group(1))
            return self._change_interval(minutes, msg)

        return None

    def _clear_queue(self, msg: QQMessage) -> str:
        """Clear all tasks from task_queue.json and reset active_plan."""
        state_dir = os.path.join(self.workspace, "state")

        # Clear task queue
        queue_path = os.path.join(state_dir, "task_queue.json")
        try:
            with open(queue_path, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=2, ensure_ascii=False)
            logger.info("Task queue cleared via QQ command")
        except Exception as e:
            logger.error(f"Failed to clear queue: {e}")

        # Reset active_plan to idle
        from datetime import datetime
        plan_path = os.path.join(state_dir, "active_plan.json")
        try:
            plan = {
                "status": "idle",
                "title": "",
                "goal": "",
                "created_at": datetime.now().isoformat(),
                "current_phase_index": 0,
                "phases": [],
                "last_heartbeat": datetime.now().isoformat(),
                "heartbeat_summary": "队列已清空，等待新计划",
            }
            with open(plan_path, 'w', encoding='utf-8') as f:
                json.dump(plan, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to reset active_plan: {e}")

        # Log to journal
        try:
            self.journal.log(JournalEntry(
                task_id="clear_queue",
                task_type="system",
                task_title="QQ清空队列",
                result_summary=f"来自 {msg.sender_name or 'QQ用户'} 的指令：队列已清空",
            ))
        except Exception:
            pass

        return "好的，队列清干净了"

    def _force_run(self, msg: QQMessage) -> str:
        """Trigger immediate research cycle run."""
        state_dir = os.path.join(self.workspace, "state")
        now = datetime.now().isoformat()

        # Archive existing active plan if any
        plan_path = os.path.join(state_dir, "active_plan.json")
        try:
            with open(plan_path) as f:
                old_plan = json.load(f)
            if old_plan.get("status") == "active":
                archive_path = os.path.join(state_dir, f"plan_archive_{now[:19].replace(':','')}.json")
                with open(archive_path, 'w', encoding='utf-8') as f:
                    json.dump(old_plan, f, indent=2, ensure_ascii=False)
                logger.info(f"Archived previous plan to {archive_path}")
        except Exception:
            pass

        # Extract task title from message
        task_title = "推进研究项目"
        if msg.content and len(msg.content) > 10:
            task_title = msg.content.replace("立即开始执行", "").replace("直接开始", "").replace("马上开始", "").strip() or task_title

        # Create new plan
        plan = {
            "status": "active",
            "title": task_title,
            "goal": f"用户要求: {task_title}",
            "created_at": now,
            "current_phase_index": 0,
            "phases": [
                {
                    "name": f"文献调研 - {task_title}",
                    "type": "literature_search",
                    "status": "in_progress",
                    "current_step": "开始搜索相关文献",
                    "result": "",
                    "started_at": now
                },
                {
                    "name": "代码实现",
                    "type": "code_implementation",
                    "status": "pending",
                    "current_step": "",
                    "result": ""
                },
                {
                    "name": "实验与分析",
                    "type": "experiment",
                    "status": "pending",
                    "current_step": "",
                    "result": ""
                }
            ],
            "last_heartbeat": now,
            "heartbeat_summary": f"QQ用户要求: {task_title}"
        }
        with open(plan_path, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

        # Execute research immediately via Hermes agent (background)
        import subprocess
        try:
            subprocess.Popen(
                ["hermes", "-z",
                 f"你是自主研究助手。目标是：{task_title}。\n\n"
                 f"读取 {self.workspace}/state/active_plan.json，执行当前 in_progress 阶段。\n"
                 "规则：\n"
                 "- 不要问用户问题，直接执行\n"
                 "- 分析现状 → 搜索文献 → 修改代码 → 运行实验 → 记录结果\n"
                 "- 完成后：更新 active_plan.json，推进到下一阶段\n"
                 "- 调用 python3 send_qq_report.py 推送进度报告\n"
                 "- 如果所有阶段完成，设置 status=idle\n"
                 "- 用中文写报告\n"
                 "- 不要使用markdown格式，不要用**加粗**、*斜体*、列表符号等",
                 "--skills", "partner-research"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            logger.debug(f"Force run Hermes exec failed: {e}")

        return ""  # Let LLM generate natural reply


    def _change_interval(self, minutes: int, msg: QQMessage) -> str:
        """Change the heartbeat interval and update the cron job."""
        import subprocess
        cfg_path = os.path.join(self.workspace, "partner_config.json")
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if "scheduler" not in cfg:
                cfg["scheduler"] = {}
            cfg["scheduler"]["interval_minutes"] = minutes
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            logger.info(f"Heartbeat interval changed to {minutes}min via QQ")
        except Exception as e:
            logger.error(f"Failed to change interval: {e}")
            return "没改成功，待会儿再试试？"

        # Try to update the cron job schedule
        try:
            cron_id = cfg.get("scheduler", {}).get("cron_job_id", "")
            cron_name = cfg.get("scheduler", {}).get("cron_job_name", "partner-research-cycle")
            target = cron_id or cron_name
            if target:
                subprocess.run(
                    ["hermes", "cron", "edit", target, "--schedule", f"every {minutes}m"],
                    capture_output=True, timeout=30,
                )
        except Exception as e:
            logger.debug(f"Cron schedule update failed: {e}")

        # Log to journal
        try:
            self.journal.log(JournalEntry(
                task_id="set_interval",
                task_type="system",
                task_title=f"修改心跳间隔为{minutes}分钟",
                result_summary=f"来自 {msg.sender_name or 'QQ用户'} 的指令",
            ))
        except Exception:
            pass

        return f"改好了，以后每 {minutes} 分钟找你一次"


    # ── LLM Intent Classification & Task Queuing ───────────────────

    def _classify_intent(self, text: str, sender: str) -> str:
        """Use LLM to classify user intent: TASK or CHAT.

        Sends a lightweight classification prompt to the LLM.
        Returns "TASK" if the user wants research work done,
        "CHAT" for normal conversation (greetings, status checks, chit-chat).
        Falls back to "CHAT" on any error.
        """
        try:
            if self._adapter is None:
                cfg_path = os.path.join(self.workspace, "partner_config.json")
                if not os.path.exists(cfg_path):
                    return "CHAT"
                with open(cfg_path) as f:
                    cfg = json.load(f)
                backend = cfg.get("agent", {}).get("backend", cfg.get("backend", "hermes"))
                from .adapter import create_adapter
                self._adapter = create_adapter(backend, self.workspace)

            prompt = f"""你是 Partner 的意图分类器。判断用户的消息是"研究任务"还是"普通聊天"。

研究任务（回复 TASK）：
- 用户明确要求做研究类工作：读文献、分析数据、改代码、跑实验、查资料
- 任务管理指令：清空队列、清除、只做X、以后只做X、停止做X、取消X
- 执行指令：开始运行、立即执行、直接开始、推进、跑起来、继续
- 方向调整：不要做X了、换方向、转向X、专注X

普通聊天（回复 CHAT）：
- 打招呼：你好、在吗、hi、早上好
- 问状态：在做什么、进展如何、最近在忙什么、汇报
- 闲聊：好的、哈哈、嗯、知道了、谢谢、ok

只回复 TASK 或 CHAT，不要其他内容。

用户消息: {text[:300]}
分类:"""

            result = self._adapter.chat(prompt, max_tokens=10)
            result_clean = result.strip().upper() if result else ""
            if "TASK" in result_clean:
                logger.info(f"Intent classified as TASK: {text[:60]}...")
                return "TASK"
            logger.info(f"Intent classified as CHAT: {text[:60]}...")
            return "CHAT"
        except Exception as e:
            logger.warning(f"Intent classification failed: {e}, defaulting to CHAT")
            return "CHAT"

    def _queue_task(self, text: str, msg: QQMessage) -> str:
        """Queue a research task from QQ chat to task_queue.json.

        Returns a confirmation message to send back to the user.
        """
        import uuid
        state_dir = os.path.join(self.workspace, "state")
        queue_path = os.path.join(state_dir, "task_queue.json")

        # Build task
        task = {
            "id": f"task_{uuid.uuid4().hex[:8]}",
            "type": "deep_dive",
            "title": text[:60] + ("..." if len(text) > 60 else ""),
            "description": text,
            "priority": 7,
            "created_at": datetime.now().isoformat(),
            "ttl_hours": 48,
            "status": "pending",
            "tags": ["qq_task"],
            "source": "qq",
            "sender_name": msg.sender_name or "QQ用户",
        }

        # Load existing tasks, append, save
        tasks = []
        try:
            with open(queue_path, 'r', encoding='utf-8') as f:
                tasks = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            tasks = []
        tasks.append(task)
        with open(queue_path, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)

        # Also kick active_plan to "planning" so next cron cycle picks it up
        plan_path = os.path.join(state_dir, "active_plan.json")
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                plan = json.load(f)
            if plan.get("status") in ("idle", "completed"):
                plan["status"] = "planning"
                plan["last_heartbeat"] = datetime.now().isoformat()
                plan["heartbeat_summary"] = f"QQ用户下达了新任务: {text[:40]}..."
                with open(plan_path, 'w', encoding='utf-8') as f:
                    json.dump(plan, f, indent=2, ensure_ascii=False)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # Mark task queued timestamp to suppress heartbeat double-report
        self._last_task_queued_at = time.time()
        # Also write flag file so external scripts (send_qq_report.py) can check
        try:
            flag_path = os.path.join(state_dir, "suppress_heartbeat.flag")
            with open(flag_path, "w") as f:
                f.write(str(self._last_task_queued_at))
        except Exception:
            pass

        # Immediately trigger the cron job so the task starts processing now
        try:
            cfg_path = os.path.join(self.workspace, "partner_config.json")
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            scheduler = cfg.get("scheduler", {})
            job_id = scheduler.get("cron_job_id")
            job_name = scheduler.get("cron_job_name")

            if job_id:
                try:
                    subprocess.run(
                        ["hermes", "cron", "run", job_id, "--accept-hooks"],
                        capture_output=True, timeout=120
                    )
                except Exception:
                    if job_name:
                        subprocess.run(
                            ["hermes", "cron", "run", job_name, "--accept-hooks"],
                            capture_output=True, timeout=120
                        )
            elif job_name:
                subprocess.run(
                    ["hermes", "cron", "run", job_name, "--accept-hooks"],
                    capture_output=True, timeout=120
                )
        except Exception as e:
            logger.debug(f"Immediate cron trigger failed (non-blocking): {e}")

        logger.info(f"Task queued from QQ: {task['id']} — {text[:80]}")

        # Also log to journal
        try:
            self.journal.log(JournalEntry(
                task_id=task["id"],
                task_type="deep_dive",
                task_title=f"QQ任务: {text[:50]}",
                result_summary=f"来自 {msg.sender_name or 'QQ用户'} 的任务已加入队列",
            ))
        except Exception:
            pass

        # Build natural conversational confirmation — let LLM handle it
        return ""

    def _get_interval_minutes(self) -> int:
        """Read configured research interval from partner_config.json."""
        try:
            cfg_path = os.path.join(self.workspace, "partner_config.json")
            with open(cfg_path) as f:
                cfg = json.load(f)
            return cfg.get("scheduler", {}).get("interval_minutes", 30)
        except Exception:
            return 30

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
