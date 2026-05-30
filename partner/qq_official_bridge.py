"""QQ Official Bot Bridge — 最小消息桥接器。

只做三件事：
1. 收到消息 → 保存用户 openid → 更新活跃项目 → 回复简洁确认
2. 定时检查 mind pool 的报告 → 推送到 QQ
3. 发送 QQ 消息

Usage:
    from partner.qq_official_bridge import QQQfficialBridge

    bridge = QQQfficialBridge(workspace="/path/to/workspace")
    bridge.configure(app_id="YOUR_APP_ID", app_secret="YOUR_APP_SECRET")
    bridge.start()  # Blocks, listening for messages
"""

import os
import json
import time
import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, List

from .qq_official_bot import QQQfficialBot, QQMessage, QQMessageType, QQBotInfo
from .journal import Journal, JournalEntry

logger = logging.getLogger(__name__)


class QQQfficialBridge:
    """Minimal bridge between QQ Official Bot and Partner's Mind system."""

    def __init__(self, workspace: str):
        self.workspace = workspace

        # State directory
        state_dir = os.path.join(workspace, "state")
        os.makedirs(state_dir, exist_ok=True)

        # Journal for logging
        self.journal = Journal(os.path.join(state_dir, "journal.jsonl"))

        # Bot reference
        self._bot: Optional[QQOfficialBot] = None
        self._running = False

        # Stats
        self._stats = {
            "messages_received": 0,
            "messages_sent": 0,
            "errors": 0,
        }

        # 去重缓存：{content_hash: timestamp}，避免同一内容在5分钟内重复发送
        self._recent_sent: dict = {}

        # 应用资源限制
        from .resource_limiter import apply_limits
        apply_limits()

    # ── Configuration ──────────────────────────────────────────────

    def configure(self, app_id: str, app_secret: str, is_sandbox: bool = False):
        """Configure or update bot credentials."""
        self._app_id = app_id
        self._app_secret = app_secret
        self._is_sandbox = is_sandbox
        logger.info(f"QQ Official Bridge configured: app_id={app_id}, sandbox={is_sandbox}")

    def load_config_from_file(self, config_path: str) -> bool:
        """Load QQ configuration from a JSON file."""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._app_id = data.get("app_id", getattr(self, '_app_id', ''))
            self._app_secret = data.get("app_secret", getattr(self, '_app_secret', ''))
            self._is_sandbox = data.get("is_sandbox", getattr(self, '_is_sandbox', False))
            logger.info(f"QQ config loaded from: {config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load QQ config: {e}")
            return False

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self):
        """Start the bridge (blocking)."""
        app_id = getattr(self, '_app_id', '')
        app_secret = getattr(self, '_app_secret', '')
        if not app_id or not app_secret:
            logger.error("QQ Official Bot not configured. Call configure() first.")
            print("❌ QQ官方机器人未配置，请先设置 AppID 和 AppSecret")
            return

        self._running = True
        self._stats["start_time"] = datetime.now().isoformat()

        logger.info("Starting QQ Official Bridge...")
        logger.info(f"  Workspace: {self.workspace}")
        logger.info(f"  AppID: {app_id}")
        logger.info(f"  Sandbox: {getattr(self, '_is_sandbox', False)}")

        # Log to Partner journal
        self.journal.log(JournalEntry(
            task_id="qq_bridge",
            task_type="system",
            task_title="QQ Official Bridge 启动",
            result_summary=f"app_id={app_id}",
        ))

        # Initialize bot
        self._bot = QQQfficialBot(
            app_id=app_id,
            app_secret=app_secret,
            is_sandbox=getattr(self, '_is_sandbox', False),
            auto_reconnect=True,
        )
        self._bot.set_message_handler(self._handle_message)
        self._bot.set_ready_handler(self._handle_ready)
        self._bot.set_error_handler(self._handle_error)

        # 启动 Mind 自主念头系统（后台线程）
        self._start_mind()

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
        """Start a background thread that proactively pushes notifications."""
        def poll():
            notif_dir = os.path.join(self.workspace, "state", "notifications")
            pending_file = os.path.join(self.workspace, "state", "pending_notifications.json")
            user_ctx_path = os.path.join(self.workspace, "state", "qq_user_context.json")
            while self._running:
                try:
                    pending_notifs = []
                    if os.path.exists(pending_file):
                        try:
                            with open(pending_file) as f:
                                pending_notifs = json.load(f)
                        except Exception:
                            pending_notifs = []

                    fresh_notifs = []
                    if os.path.exists(notif_dir):
                        for fname in sorted(os.listdir(notif_dir)):
                            if fname.endswith(".json"):
                                fpath = os.path.join(notif_dir, fname)
                                try:
                                    with open(fpath) as f:
                                        notif = json.load(f)
                                    entry = {
                                        "timestamp": datetime.now().isoformat(),
                                        "type": notif.get("type", "daily"),
                                        "summary": notif.get("summary", ""),
                                        "details": notif.get("details", []),
                                        "next_task": notif.get("next_task", ""),
                                        "pending_count": notif.get("pending_count", 0),
                                    }
                                    pending_notifs.append(entry)
                                    fresh_notifs.append(entry)
                                except Exception:
                                    pass
                                try:
                                    os.remove(fpath)
                                except Exception:
                                    pass

                    if pending_notifs:
                        pending_notifs = pending_notifs[-10:]
                        os.makedirs(os.path.dirname(pending_file), exist_ok=True)
                        with open(pending_file, "w") as f:
                            json.dump(pending_notifs, f, indent=2, ensure_ascii=False)

                    if fresh_notifs and self._bot and self._bot.get_event_loop():
                        openid = ""
                        try:
                            if os.path.exists(user_ctx_path):
                                with open(user_ctx_path) as f:
                                    ctx = json.load(f)
                                openid = ctx.get("openid", "")
                            if not openid:
                                fallback = os.path.join(os.path.dirname(user_ctx_path), "proactive_openid.txt")
                                if os.path.exists(fallback):
                                    with open(fallback) as f:
                                        openid = f.read().strip()
                        except Exception:
                            pass

                        if openid:
                            for n in fresh_notifs:
                                summary = n.get("summary", "").strip()
                                if not summary:
                                    continue
                                import hashlib
                                h = hashlib.md5(summary.encode()).hexdigest()
                                now_ts = time.time()
                                stale = [k for k, v in self._recent_sent.items() if now_ts - v > 300]
                                for k in stale:
                                    del self._recent_sent[k]
                                if h in self._recent_sent:
                                    continue
                                self._recent_sent[h] = now_ts
                                if len(summary) > 500:
                                    summary = summary[:497] + "..."
                                asyncio.run_coroutine_threadsafe(
                                    self._bot.send_message(openid, summary, QQMessageType.PRIVATE),
                                    self._bot.get_event_loop(),
                                )
                                logger.info(f"Proactive push sent to {openid}: {summary[:80]}")
                except Exception:
                    pass
                time.sleep(60)
        t = threading.Thread(target=poll, daemon=True)
        t.start()

    def _start_mind(self):
        """启动 Mind 自主念头系统（后台线程）。"""
        try:
            from .core import Partner
            from .config import PartnerConfig, WorkspaceConfig
            cfg = PartnerConfig(workspace=WorkspaceConfig(path=self.workspace))
            self._partner = Partner(cfg)
            self._partner.start()
            self._partner.start_mind()

            from .mind.executor import set_push_callback

            def _push_to_qq(content: str):
                """将 Report 内容推送到 QQ 用户（含去重和内容过滤）。"""
                try:
                    content_stripped = content.strip()
                    if content_stripped.startswith('{') and content_stripped.endswith('}'):
                        logger.warning(f"[Mind] Skipping JSON push: {content[:60]}...")
                        return
                    if not content_stripped:
                        return
                    if not self._should_send(content_stripped):
                        return

                    import hashlib as _hl
                    h = _hl.md5(content_stripped.encode()).hexdigest()
                    now_ts = time.time()
                    stale = [k for k, v in self._recent_sent.items() if now_ts - v > 300]
                    for k in stale:
                        del self._recent_sent[k]
                    if h in self._recent_sent:
                        return
                    self._recent_sent[h] = now_ts

                    user_ctx_path = os.path.join(self.workspace, "state", "qq_user_context.json")
                    openid = ""
                    if os.path.exists(user_ctx_path):
                        with open(user_ctx_path) as f:
                            ctx = json.load(f)
                        openid = ctx.get("openid", "")
                    if not openid:
                        fallback = os.path.join(os.path.dirname(user_ctx_path), "proactive_openid.txt")
                        if os.path.exists(fallback):
                            with open(fallback) as f:
                                openid = f.read().strip()

                    if openid and self._bot and self._bot.get_event_loop():
                        import re
                        clean = re.sub(r'\*{1,2}(.+?)\*{1,2}', r'\1', content)
                        clean = re.sub(r'`([^`]+)`', r'\1', clean)
                        text = clean[:500] if len(clean) > 500 else clean
                        asyncio.run_coroutine_threadsafe(
                            self._bot.send_message(openid, text, QQMessageType.PRIVATE),
                            self._bot.get_event_loop(),
                        )
                        logger.info(f"[Mind] 推送报告到 {openid}: {text[:60]}...")
                except Exception as e:
                    logger.warning(f"[Mind] 推送回调异常: {e}")

            set_push_callback(_push_to_qq)
            logger.info("🧠 Mind 系统已自动启动")
        except Exception as e:
            logger.warning(f"Mind 系统启动失败（不影响 QQ 机器人）: {e}")

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
        import time as _time
        self._startup_time = _time.time()

    def _handle_error(self, error: Exception):
        """Called when an error occurs."""
        logger.error(f"QQ Bot error: {error}")

    def _handle_message(self, msg: QQMessage):
        """Handle an incoming QQ message."""
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

            # Process in background thread
            import threading
            thread = threading.Thread(
                target=self._process_message_async,
                args=(msg, user_text),
                daemon=True,
            )
            thread.start()

        except Exception as e:
            logger.error(f"Message handling error: {e}", exc_info=True)
            self._stats["errors"] += 1
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

    def _process_message_async(self, msg: QQMessage, user_text: str):
        """只做三件事：保存项目、确认回复、放入念头池。"""
        try:
            # 1. 更新活跃项目（自然语言 .txt）
            try:
                from .project_state import set_active
                set_active(self.workspace, user_text[:60])
                logger.info(f"[QQ] 活跃项目已设置: {user_text[:40]}")
            except Exception:
                pass

            # 2. 放入 PROJECT 事件到念头池
            try:
                from .mind import MindPool, MindEvent, EventType, cron_tick
                pool = MindPool.get_sync_instance()
                if pool is not None:
                    from .project_state import get_active
                    proj_name = get_active(self.workspace) or user_text[:60]
                    ev = MindEvent(
                        type=EventType.PROJECT,
                        priority=2,
                        payload={"title": proj_name, "goal": user_text, "step": 0},
                        source="qq_user",
                    )
                    pool.put_threadsafe(ev)
                    pool.put_threadsafe(cron_tick(source="qq_user:force"))
                    logger.info(f"[QQ] Project event queued to Mind Pool: '{proj_name}'")
            except Exception:
                pass

            # 3. 用 LLM 生成自然回复（绝不硬编码）
            reply = f"收到，开始推进「{user_text[:40]}」"  # 紧急 fallback
            try:
                from .adapter import create_adapter as _ca
                _backend = "hermes"
                _cfg_p = os.path.join(self.workspace, "partner_config.json")
                if os.path.exists(_cfg_p):
                    with open(_cfg_p) as _f:
                        _cfg = __import__("json").load(_f)
                    _backend = _cfg.get("agent", {}).get("backend", _cfg.get("backend", "hermes"))
                _adapter_instance = _ca(_backend, self.workspace)
                if _adapter_instance:
                    _prompt = (
                        f"用户刚刚指定了研究方向：{user_text[:80]}。\\n"
                        f"用一句话简短确认，语气像研究伙伴一样自然。\\n"
                        f"不要说「好的」「收到」「我来推进」「开始推进」这类机械回复。"
                    )
                    _r = _adapter_instance.chat(_prompt)
                    if _r and len(_r.strip()) > 5:
                        reply = _r.strip()
            except Exception:
                pass
            self._send_reply(msg, reply)

            # Log interaction
            try:
                self.journal.log(JournalEntry(
                    task_id=f"qq_{msg.msg_id}",
                    task_type="conversation",
                    task_title=f"QQ对话: {msg.sender_name}({msg.sender_id})",
                    result_summary=f"Q: {user_text[:100]}",
                ))
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Background message processing error: {e}", exc_info=True)
            self._stats["errors"] += 1
            try:
                error_text = "抱歉，处理消息时出了点问题。再跟我说一次？"
                self._send_reply(msg, error_text)
            except Exception:
                pass

    def _should_send(self, text: str) -> bool:
        """Check if message content should be sent (filters out templates and noise)."""
        if not text or not text.strip():
            return False

        blocked_keywords = [
            "系统已重启",
            "研究进展",
            "已完成",
            "下一轮将在",
            "循环中",
            "思考中",
            "有进展了跟你说",
            "刚重启完",
            "当前没有进行中的项目",
            "当前没有活跃的项目",
            "请等待",
        ]
        text_lower = text.strip().lower()
        for kw in blocked_keywords:
            if kw.lower() in text_lower:
                return False

        template_patterns = ["📊", "⏳", "🔄", "📈"]
        has_template = any(p in text for p in template_patterns)
        has_substance = any(c in text for c in ("=", ":", "：", "MAE", "mse", "loss", "acc", "f1", "auc", "准确率", "指标", "结果", "发现"))
        if has_template and not has_substance:
            return False

        return True

    def _send_reply(self, original_msg: QQMessage, reply: str):
        """Send reply back to the user with dedup."""
        if not self._bot:
            logger.error("Bot not initialized")
            return

        reply_stripped = reply.strip()
        if not reply_stripped:
            return

        # Dedup
        import hashlib as _hl
        h = _hl.md5(reply_stripped.encode()).hexdigest()
        now_ts = time.time()
        stale = [k for k, v in self._recent_sent.items() if now_ts - v > 300]
        for k in stale:
            del self._recent_sent[k]
        if h in self._recent_sent:
            logger.debug(f"[去重] 跳过重复回复: {reply_stripped[:60]}...")
            return
        self._recent_sent[h] = now_ts

        if self._bot.get_event_loop() and self._bot.get_event_loop().is_running():
            asyncio.run_coroutine_threadsafe(
                self._bot.reply_message(original_msg, reply),
                self._bot.get_event_loop(),
            )
            self._stats["messages_sent"] += 1

    # ── Cleanup & Stats ───────────────────────────────────────────

    def _cleanup(self):
        """Cleanup on shutdown."""
        logger.info(f"QQ Bridge stats: {json.dumps(self._stats, indent=2)}")
        try:
            self.journal.log(JournalEntry(
                task_id="qq_bridge",
                task_type="system",
                task_title="QQ Bridge 关闭",
                result_summary=json.dumps(self._stats, ensure_ascii=False),
            ))
        except Exception:
            pass

    def get_stats(self) -> Dict:
        """Get bridge statistics."""
        return {**self._stats}

    def send_proactive(self, to_user: str, content: str, msg_type: QQMessageType = QQMessageType.PRIVATE) -> bool:
        """Send a proactive message to a QQ user (not in reply to a message)."""
        if self._bot:
            return self._bot.send_proactive(to_user, content, msg_type)
        return False


# Helper: create bridge from config file
def create_bridge(workspace: str, config_path: str = None) -> QQQfficialBridge:
    """Create and configure a QQQfficialBridge."""
    bridge = QQQfficialBridge(workspace)
    if config_path and os.path.exists(config_path):
        bridge.load_config_from_file(config_path)
    elif not getattr(bridge, '_app_id', None):
        ws_config = os.path.join(workspace, "qq_config.json")
        if os.path.exists(ws_config):
            bridge.load_config_from_file(ws_config)
    return bridge
