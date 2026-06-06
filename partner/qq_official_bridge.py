"""QQ Official Bot Bridge - connects QQ Official Bot to Partner's event runtime.

This module is the high-level integration layer that:
  1. Starts the QQ Official Bot adapter
  2. Routes text messages to InteractionOrchestrator
  3. Sends text replies back through QQ
  4. Maintains per-user conversation context

Usage:
    from partner.qq_official_bridge import QQQfficialBridge

    bridge = QQQfficialBridge(workspace="/path/to/workspace")
    bridge.configure(app_id="YOUR_APP_ID", app_secret="YOUR_APP_SECRET")
    bridge.start()  # Blocks, listening for messages

Architecture:
    QQ User → QQ Bot Platform → WebSocket → QQQfficialBot → QQQfficialBridge → InteractionOrchestrator
                                                                                         ↓
    QQ User ← QQ Bot Platform ← REST API ← QQQfficialBot ← QQQfficialBridge ← InteractionOrchestrator
"""

import os
import json
import time
import asyncio
import logging
import threading
import subprocess
import re
try:
    import fcntl
except ImportError:
    fcntl = None
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, List

from .qq_official_bot import QQQfficialBot, QQMessage, QQMessageType, QQBotInfo
from .task_queue import TaskQueue
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager
from .outbound_policy import THINKING_NOTICE, UNAVAILABLE_NOTICE, prefix_event_notice
from .config import (
    apply_runtime_agent_defaults,
    load_partner_config_data,
    save_partner_config_data,
)
from .interaction_orchestrator import InteractionOrchestrator
from .user_text_safety import has_internal_diff, strip_internal_diff

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
    send_thinking_hint: bool = False
    thinking_hint_text: str = ""

    # Workspace
    workspace: str = ""


class QQQfficialBridge:
    """High-level bridge between QQ Official Bot and Partner.

    Integrates QQQfficialBot (transport) + InteractionOrchestrator (event selector).
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
        self._recent_message_keys: Dict[str, float] = {}
        self._message_dedup_ttl = 300.0
        self._proactive_quiet_until = 0.0
        self._proactive_quiet_reason = ""
        self._singleton_lock_fd = None
        self._singleton_lock_path = os.path.join(state_dir, "qq_bridge.lock")
        self._recent_message_file = os.path.join(state_dir, "qq_recent_messages.json")
        self._qq_chat_history_file = os.path.join(state_dir, "qq_chat_history.jsonl")
        self._recent_reply_keys: Dict[str, float] = {}
        self._interaction_orchestrator: Optional[InteractionOrchestrator] = None

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
            self.config.send_thinking_hint = data.get("send_thinking_hint", self.config.send_thinking_hint)
            self.config.thinking_hint_text = str(data.get("thinking_hint_text", self.config.thinking_hint_text) or "")
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

        if not self._acquire_singleton_lock():
            logger.warning(f"QQ bridge already running for workspace: {self.workspace}")
            print("⚠️ QQ bridge 已在这个工作区运行，当前进程退出")
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

            if self._is_recent_duplicate_message(msg):
                logger.info(
                    f"[QQ Bridge DEDUP] Skipping duplicate bridge handling for {msg.msg_id or msg.sender_id}"
                )
                return

            logger.info(f"[QQ {msg.sender_name}({msg.sender_id})] {user_text[:100]}\n")
            self._append_qq_chat_history(
                {
                    "role": "user",
                    "content": user_text,
                    "timestamp": datetime.now().isoformat(),
                    "source": "qq",
                    "channel": msg.message_type.value,
                    "sender_id": msg.sender_id,
                    "sender_name": msg.sender_name or msg.sender_id,
                    "msg_id": msg.msg_id,
                    "group_id": msg.group_id,
                }
            )

            self._send_reply(msg, THINKING_NOTICE)

            # Step 0: Special commands (handled directly, no LLM needed)
            special_reply = self._handle_special_command(user_text, msg)
            if special_reply == "__PARTNER_NO_USER_REPLY__":
                return
            if special_reply:  # Has a real reply
                self._send_reply_once(msg, special_reply)
                return
            if special_reply == "":  # Pattern matched, go to LLM chat
                self._force_run_triggered = True
            else:
                self._force_run_triggered = False

            self._force_run_triggered = False

            # Fast path for rapid external-content sharing: record and queue
            # content before any slow interaction LLM call, but do not send a
            # hard-coded content reply. The interaction orchestrator still owns
            # the user-facing response.
            if self._looks_like_external_content_share(user_text) and self._infer_focus_project():
                focus_project = self._infer_focus_project() or ""
                shared_content = self._record_shared_content_signal(
                    msg,
                    user_text,
                    project_override=focus_project,
                )
                if shared_content:
                    self._nudge_content_digest(shared_content)

            decision = self._get_interaction_orchestrator().handle_message(
                sender_id=msg.sender_id,
                sender_name=msg.sender_name or "QQ用户",
                text=user_text,
            )
            if self._looks_like_external_content_share(user_text):
                content_project = (
                    decision.target_project
                    if decision.need_lifeline_update and decision.target_project
                    else self._infer_focus_project()
                ) or ""
                shared_content = self._record_shared_content_signal(
                    msg,
                    user_text,
                    project_override=content_project,
                )
                if shared_content:
                    self._nudge_content_digest(shared_content)
            reply = self._simplify_response(decision.reply_to_user)
            reply = prefix_event_notice(
                reply,
                decision.event_type,
                event_kind=decision.event_kind,
                workspace=self.workspace,
            )
            if not (reply or "").strip():
                backend_error = self._recent_backend_failure_notice()
                if backend_error:
                    logger.info("QQ message has no agent reply because backend failed; sending configuration notice.")
                    self._send_reply_once(msg, backend_error)
                    return
                logger.info("QQ message produced no user-facing reply; keeping it in history only.")
                return
            self._mark_proactive_quiet("user_interaction", seconds=300)
            self._add_user_context(msg.sender_id, "user", user_text)
            self._add_user_context(msg.sender_id, "partner", reply)

            # Save dialogue to workspace
            try:
                from .workspace_manager import append_dialogue
                append_dialogue(self.workspace, msg.sender_name, user_text, reply, platform="qq")
            except Exception:
                pass

            # Send reply
            self._send_reply_once(msg, reply)

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
                error_text = self._unavailable_notice()
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

    @staticmethod
    def _looks_like_external_content_share(text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        if re.search(r"https?://|www\.|mp\.weixin\.qq\.com|xiaohongshu\.com|bilibili\.com|zhihu\.com|jump_url|卡片消息|图文H5|附件素材", raw, re.I):
            return True
        return False

    @staticmethod
    def _quick_content_share_reply(item: Optional[dict]) -> str:
        # Deprecated: user-facing content replies must be generated by the LLM
        # orchestrator. Keep this stub only for old call sites.
        return ""

    def _build_status_snapshot(self) -> Optional[Dict[str, str]]:
        focus_project = self._infer_focus_project()
        if not focus_project:
            return None
        display_project = focus_project
        try:
            from .project_state import simplify_project_query
            display_project = simplify_project_query(focus_project)
        except Exception:
            pass
        state_summary, next_step, blockers = self._summarize_project_state(focus_project)
        active_plan_line = self._summarize_active_plan()
        stats_line = self._summarize_stats()
        current_line = self._extract_current_progress_line(focus_project)
        recent_line = self._summarize_recent_project_log(focus_project)
        signature = " | ".join(
            part for part in (display_project, state_summary, current_line, blockers, next_step, recent_line) if part
        )
        return {
            "focus_project": focus_project,
            "display_project": display_project,
            "summary": state_summary,
            "current": current_line,
            "recent": recent_line,
            "blockers": blockers,
            "next_step": next_step,
            "active_plan": active_plan_line,
            "stats": stats_line,
            "signature": signature[:1000],
        }

    def _is_recent_duplicate_message(self, msg: QQMessage) -> bool:
        if not msg.msg_id:
            return False
        key = msg.msg_id
        now = time.time()
        cutoff = now - self._message_dedup_ttl
        try:
            if os.path.exists(self._recent_message_file):
                with open(self._recent_message_file, "r", encoding="utf-8") as f:
                    persisted = json.load(f)
                if isinstance(persisted, dict):
                    for persisted_key, ts in persisted.items():
                        try:
                            ts_float = float(ts)
                        except (TypeError, ValueError):
                            continue
                        if ts_float >= cutoff:
                            self._recent_message_keys.setdefault(persisted_key, ts_float)
        except Exception:
            pass
        stale = [k for k, ts in self._recent_message_keys.items() if ts < cutoff]
        for stale_key in stale:
            del self._recent_message_keys[stale_key]
        if key in self._recent_message_keys:
            return True
        self._recent_message_keys[key] = now
        try:
            os.makedirs(os.path.dirname(self._recent_message_file), exist_ok=True)
            with open(self._recent_message_file, "w", encoding="utf-8") as f:
                json.dump(self._recent_message_keys, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return False

    def _mark_proactive_quiet(self, reason: str, seconds: int = 180):
        self._proactive_quiet_until = max(self._proactive_quiet_until, time.time() + float(seconds))
        self._proactive_quiet_reason = reason

    def _should_suppress_proactive(self) -> bool:
        return time.time() < self._proactive_quiet_until

    def _summarize_project_state(self, focus_project: str) -> tuple[str, str, str]:
        if not focus_project:
            return "", "", ""
        try:
            from .project_state import read_state_md
            state_md = read_state_md(self.workspace, focus_project)
        except Exception:
            state_md = ""
        if not state_md:
            return "", "", ""

        plain_lines = []
        for raw in state_md.splitlines():
            line = raw.strip()
            if not line:
                continue
            line = re.sub(r"^[#*\-\s]+", "", line)
            line = re.sub(r"\[.*?\]\s*", "", line)
            if line.startswith("最后更新"):
                continue
            if "状态由 Partner 自动更新" in line:
                continue
            if line.startswith("项目："):
                continue
            plain_lines.append(line)

        summary = ""
        next_step = ""
        blockers = ""
        for line in plain_lines:
            if any(key in line for key in ("下一步", "待决策推进方向", "下一步建议")):
                next_step = re.split(r"[：:]", line, maxsplit=1)[-1].strip()
                next_step = next_step.strip("。")
                continue
            if not blockers and any(key in line for key in ("卡点", "问题", "瓶颈", "风险", "阻塞", "断档", "没动静", "未开始", "没启动")):
                blockers = re.split(r"[：:]", line, maxsplit=1)[-1].strip().strip("。")
                continue
            if not summary and len(line) >= 10:
                summary = line.strip("。")
            if summary and next_step and blockers:
                break

        if not summary and plain_lines:
            summary = plain_lines[0].strip("。")
        return summary, next_step, blockers

    def _extract_current_progress_line(self, focus_project: str) -> str:
        plan_line = self._summarize_active_plan()
        if plan_line:
            return plan_line
        try:
            from .project_state import read_state_md
            state_md = read_state_md(self.workspace, focus_project)
        except Exception:
            return ""
        for raw in state_md.splitlines():
            line = raw.strip()
            if not line:
                continue
            if any(key in line for key in ("当前推进", "当前状态", "最后动作", "进度", "已完成")):
                return re.sub(r"^[#*\-\s]+", "", line)
        return ""

    def _summarize_recent_project_log(self, focus_project: str) -> str:
        try:
            from .project_state import get_project_dir
            log_path = os.path.join(get_project_dir(self.workspace, focus_project), "trace_detail.md")
            if not os.path.exists(log_path):
                return ""
            with open(log_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
        except Exception:
            return ""
        cleaned = []
        for line in reversed(lines):
            line = re.sub(r"^[#*\-\s]+", "", line)
            if len(line) < 10:
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}", line):
                continue
            if "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__" in line:
                continue
            if "Hermes 正在处理中，下一轮再汇报进展" in line:
                continue
            cleaned.append(line.strip("。"))
            if len(cleaned) >= 2:
                break
        if not cleaned:
            return ""
        cleaned.reverse()
        return "；".join(cleaned)

    def _summarize_active_plan(self) -> str:
        plan_path = os.path.join(self.workspace, "state", "active_plan.json")
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except Exception:
            return ""

        status = plan.get("status", "")
        if status not in ("planning", "active", "completed"):
            return ""

        phases = plan.get("phases") or []
        idx = int(plan.get("current_phase_index", 0) or 0)
        current = phases[idx] if 0 <= idx < len(phases) else {}
        phase_name = (current or {}).get("name", "").strip()
        current_step = (current or {}).get("current_step", "").strip()
        heartbeat = (plan.get("heartbeat_summary") or "").strip()

        parts = [part for part in (phase_name, current_step, heartbeat) if part]
        if not parts:
            return ""
        return "；".join(parts[:2]).strip("；。")

    def _summarize_stats(self) -> str:
        try:
            stats = self.state_manager.load_stats()
        except Exception:
            return ""
        total_cycles = stats.get("total_cycles", 0)
        total_tasks = stats.get("total_tasks_completed", 0)
        if not total_cycles and not total_tasks:
            return ""
        return ""

    def _resolve_agent_config(self) -> Dict:
        """Load agent config with backward-compatible fallbacks."""
        cfg = load_partner_config_data(self.workspace)
        agent_cfg = cfg.get("agent", {})
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}
        return apply_runtime_agent_defaults(agent_cfg)

    def _infer_focus_project(self) -> str:
        """Infer the single project the reply should stay anchored to."""
        try:
            from .project_state import get_active, resolve_project_name, simplify_project_query
        except Exception:
            return ""

        active = (get_active(self.workspace) or "").strip()
        return resolve_project_name(self.workspace, simplify_project_query(active)) or simplify_project_query(active)

    def _get_main_adapter(self):
        """Get or create the main chat adapter."""
        if self._adapter is None:
            agent_cfg = self._resolve_agent_config()
            backend = agent_cfg.get("backend", "hermes")
            model = agent_cfg.get("model")
            provider = agent_cfg.get("provider")
            from .adapter import create_adapter
            self._adapter = create_adapter(
                backend, self.workspace, model=model, provider=provider
            )
        return self._adapter

    def _get_interaction_orchestrator(self) -> InteractionOrchestrator:
        if self._interaction_orchestrator is None:
            self._interaction_orchestrator = InteractionOrchestrator(
                workspace=self.workspace,
                journal=self.journal,
                knowledge=self.knowledge,
                task_queue=self.task_queue,
                state_manager=self.state_manager,
                get_adapter=self._get_main_adapter,
                get_context=self._get_user_context,
                snapshot_builder=self._build_status_snapshot,
            )
        return self._interaction_orchestrator


    @staticmethod
    def _simplify_response(reply: str) -> str:
        """Post-process response to be concise and conversational.

        Strips all markdown formatting so QQ receives clean text.
        Filters out internal leaks: tracebacks, Hermes references, session IDs.
        """
        import re

        if not reply:
            return reply

        stripped_reply = reply.strip()
        stripped_reply = re.sub(
            r"(?im)^\s*⚠️?\s*Reached maximum iterations.*(?:\n|$)",
            "",
            stripped_reply,
        ).strip()
        if stripped_reply != reply.strip():
            reply = stripped_reply
        if not stripped_reply:
            return ""

        if has_internal_diff(stripped_reply) or re.search(r"(?im)^\s*(┊\s*)?review diff\b", stripped_reply) or re.search(
            r"(?m)^@@\s+-\d+,\d+\s+\+\d+,\d+\s+@@|^diff --git |^--- a/|^\+\+\+ b/",
            stripped_reply,
        ):
            stripped_reply = strip_internal_diff(stripped_reply)
            if not stripped_reply or has_internal_diff(stripped_reply):
                return ""
            reply = stripped_reply

        if stripped_reply == "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__":
            return ""

        # ── Safety filter: catch internal leaks before any formatting ──
        # Traceback / error dumps → replace with friendly message
        _traceback_patterns = [
            r'Traceback \(most recent call last\)',
            r'^\s*File ".*", line \d+',
            r'^\s*(?:raise|except)\s+\w+',
            r'^\s*\w+Error:.*$',
            r'^\s*\w+Exception:.*$',
            r'^\s*sys\.exit',
            r'^\s*File "/home/os/\.hermes/',
            r'^\s*File "/home/os/\.partner/',
            r'^\s*File "/home/os/\.local/',
        ]
        for pat in _traceback_patterns:
            if re.search(pat, reply, re.MULTILINE):
                return ""

        # DANGEROUS COMMAND / heredoc warnings
        if re.search(r'DANGEROUS COMMAND|script execution via heredoc|PYEOF', reply):
            return ""

        # Expose "Hermes" as internal architecture name
        reply = re.sub(r'Hermes\s*(正在|正在处理|在处理|正在处理中)', r'我\1', reply)
        reply = re.sub(r'Hermes\s*(agent|Agent|会话|子进程|CLI)', r'后台任务', reply)
        reply = re.sub(r'通过\s*Hermes', '通过后台', reply)
        reply = re.sub(r'调用\s*Hermes', '在后台', reply)
        # If "Hermes" still appears as standalone word, scrub it
        reply = re.sub(r'\bHermes\b', '我', reply)

        # Session/proc IDs: proc_xxxxx, session: xxxxx
        reply = re.sub(r'session:\s*\w+', '', reply)
        reply = re.sub(r'proc_[a-f0-9]{8,}', '', reply)
        reply = re.sub(r'\(session[^)]*\)', '', reply)

        # Internal file paths
        reply = re.sub(r'/home/os/\.\w+[\w/]*', '', reply)
        reply = re.sub(r'/mnt/[a-z]/[^\s]*', '', reply)

        # "Hermes 正在处理中，下一轮再汇报进展。" — generic catch
        if re.match(r'^.*正在处理中.*汇报进展.*$', reply.strip()):
            return ""

        # ── Markdown formatting ──

        # Markdown tables: | col1 | col2 | → strip pipes, keep content
        reply = re.sub(r'^[|]\s*[-:]+[-| :]*$', '', reply, flags=re.MULTILINE)  # separator row
        reply = re.sub(r'^\|(.+)\|$', lambda m: '  '.join(c.strip() for c in m.group(1).split('|') if c.strip()), reply, flags=re.MULTILINE)

        # Strip markdown thoroughly (both single and double asterisks)
        reply = re.sub(r'\*{1,2}(.+?)\*{1,2}', r'\1', reply)       # **bold** and *italic* → plain
        reply = re.sub(r'__(\S.*?)__', r'\1', reply)             # __underline__
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

        return "__PARTNER_NO_USER_REPLY__"

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
                 "- 通过主动汇报通道推送进度报告\n"
                 "- 如果所有阶段完成，设置 status=idle\n"
                 "- 用中文写报告\n"
                 "- 不要使用markdown格式，不要用**加粗**、*斜体*、列表符号等",
                 "--skills", "partner-research"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            logger.debug(f"Force run Hermes exec failed: {e}")

        return ""  # Let LLM generate natural reply


    def _change_interval(self, minutes: int, msg: QQMessage) -> str:
        """Change the heartbeat interval and update the cron job."""
        import subprocess
        try:
            cfg = load_partner_config_data(self.workspace)
            if "scheduler" not in cfg:
                cfg["scheduler"] = {}
            cfg["scheduler"]["interval_minutes"] = minutes
            save_partner_config_data(self.workspace, cfg)
            logger.info(f"Heartbeat interval changed to {minutes}min via QQ")
        except Exception as e:
            logger.error(f"Failed to change interval: {e}")
            return self._unavailable_notice()

        # Try to update the cron job schedule
        try:
            cron_id = cfg.get("scheduler", {}).get("cron_job_id", "")
            cron_name = cfg.get("scheduler", {}).get("cron_job_name", "partner-research-cycle")
            target = cron_id or cron_name
            if target:
                subprocess.run(
                    ["hermes", "cron", "edit", target, "--schedule", f"every {minutes}m"],
                    capture_output=True, timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
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

        return "__PARTNER_NO_USER_REPLY__"

    def _record_shared_content_signal(
        self,
        msg: QQMessage,
        user_text: str,
        project_override: str = "",
    ) -> Optional[dict]:
        """Record links/social/video/article shares into the content feed."""
        try:
            from .content_feed import record_shared_content
            project = project_override or self._infer_focus_project() or ""
            return record_shared_content(
                self.workspace,
                text=user_text,
                project=project,
                sender=msg.sender_name or msg.sender_id,
                source="qq_user_share",
                raw=msg.raw,
            )
        except Exception as exc:
            logger.debug(f"failed to record shared content: {exc}")
            return None

    def _nudge_content_digest(self, item: dict):
        """Wake the mind loop to digest a newly shared external content item."""
        try:
            from .mind.event_types import EventType, MindEvent
            from .mind.pool import MindPool
            pool = MindPool.get_sync_instance()
            if not pool:
                return
            pool.put_threadsafe(MindEvent(
                type=EventType.CONTENT_DIGEST,
                priority=1,
                payload={
                    "content_id": item.get("id", ""),
                    "project": item.get("project", ""),
                },
                source="qq:shared_content",
            ))
        except Exception as exc:
            logger.debug(f"failed to nudge content digest: {exc}")

    def _get_interval_minutes(self) -> int:
        """Read configured research interval from partner_config.json."""
        try:
            cfg = load_partner_config_data(self.workspace)
            return cfg.get("scheduler", {}).get("interval_minutes", 30)
        except Exception:
            return 30

    def _recent_backend_failure_notice(self) -> str:
        """Return a concise notice when the latest agent call failed."""
        log_names = ("hermes_chat.jsonl", "openclaw_chat.jsonl", "codex_chat.jsonl", "agent_runs.jsonl")
        latest = None
        latest_ts = ""
        for name in log_names:
            path = os.path.join(self.workspace, "logs", name)
            try:
                if not os.path.exists(path):
                    continue
                with open(path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                for line in lines[-6:]:
                    row = json.loads(line)
                    ts = str(row.get("ts") or "")
                    if ts >= latest_ts:
                        latest_ts = ts
                        latest = row
            except Exception:
                continue
        if not isinstance(latest, dict):
            return ""
        status = str(latest.get("status") or "").lower()
        returncode = latest.get("returncode")
        error_text = "\n".join(
            str(latest.get(k) or "")
            for k in ("error", "stderr_preview", "stdout_preview")
            if latest.get(k)
        ).strip()
        if status not in {"failed", "timeout", "exception", "backend_not_available"} and returncode in (0, None):
            return ""
        if not error_text and status not in {"timeout", "backend_not_available"}:
            return ""
        return self._unavailable_notice()

    @staticmethod
    def _unavailable_notice() -> str:
        return UNAVAILABLE_NOTICE

    def _send_reply(self, original_msg: QQMessage, reply: str):
        """Send reply back to the user."""
        if not self._bot:
            logger.error("Bot not initialized")
            return
        sanitized = self._sanitize_outbound_text(reply)
        if sanitized and sanitized.strip() not in {THINKING_NOTICE, "思考中......", "思考中……", "Thinking..."}:
            self._append_qq_chat_history(
                {
                    "role": "assistant",
                    "content": sanitized,
                    "timestamp": datetime.now().isoformat(),
                    "source": "qq",
                    "channel": original_msg.message_type.value,
                    "sender_id": "partner",
                    "sender_name": "Partner",
                    "reply_to": original_msg.msg_id,
                    "target_id": original_msg.sender_id,
                    "group_id": original_msg.group_id,
                }
            )

        # Schedule async send
        if self._bot.get_event_loop() and self._bot.get_event_loop().is_running():
            asyncio.run_coroutine_threadsafe(
                self._bot.reply_message(original_msg, sanitized),
                self._bot.get_event_loop(),
            )
            self._stats["messages_sent"] += 1

    def _append_qq_chat_history(self, row: Dict):
        try:
            os.makedirs(os.path.dirname(self._qq_chat_history_file), exist_ok=True)
            with open(self._qq_chat_history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug(f"Failed to append QQ chat history: {exc}")

    def _send_reply_once(self, original_msg: QQMessage, reply: str):
        if not original_msg.msg_id:
            self._send_reply(original_msg, reply)
            return
        key = original_msg.msg_id
        now = time.time()
        cutoff = now - self._message_dedup_ttl
        stale = [k for k, ts in self._recent_reply_keys.items() if ts < cutoff]
        for stale_key in stale:
            del self._recent_reply_keys[stale_key]
        if key in self._recent_reply_keys:
            logger.info(f"Skipping duplicate reply for message key: {key}")
            return
        self._recent_reply_keys[key] = now
        self._send_reply(original_msg, reply)

    # ── User Context Management ───────────────────────────────────

    def _get_user_context(self, sender: str) -> List[Dict]:
        memory_context = list(self._user_contexts.get(sender, []))
        if len(memory_context) >= self._max_context_per_user:
            return memory_context[-self._max_context_per_user:]
        file_context = self._load_recent_chat_context(sender, limit=self._max_context_per_user)
        if not file_context:
            return memory_context[-self._max_context_per_user:]
        merged = file_context + memory_context
        deduped = []
        seen = set()
        for item in merged:
            key = (
                item.get("role"),
                item.get("text"),
                int(float(item.get("timestamp") or 0)) if item.get("timestamp") else 0,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[-self._max_context_per_user:]

    def _load_recent_chat_context(self, sender: str, limit: int = 10) -> List[Dict]:
        path = self._qq_chat_history_file
        if not os.path.exists(path):
            return []
        rows = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    role = row.get("role")
                    if role == "user" and str(row.get("sender_id") or "") != str(sender or ""):
                        continue
                    if role == "assistant" and str(row.get("target_id") or "") != str(sender or ""):
                        continue
                    content = str(row.get("content") or "").strip()
                    if not content:
                        continue
                    ts_text = str(row.get("timestamp") or "")
                    ts_value = 0.0
                    if ts_text:
                        try:
                            ts_value = datetime.fromisoformat(ts_text).timestamp()
                        except Exception:
                            ts_value = 0.0
                    rows.append({
                        "role": "user" if role == "user" else "partner",
                        "text": content,
                        "timestamp": ts_value,
                    })
        except Exception as exc:
            logger.debug(f"Failed to load QQ chat context: {exc}")
            return []
        return rows[-limit:]

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
        self._release_singleton_lock()

    def _acquire_singleton_lock(self) -> bool:
        try:
            lock_fd = open(self._singleton_lock_path, "a+", encoding="utf-8")
            if fcntl is not None:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    existing_pid = None
                    try:
                        lock_fd.seek(0)
                        existing_pid = int((lock_fd.read() or "0").strip() or "0")
                    except (ValueError, OSError):
                        existing_pid = None
                    if existing_pid:
                        try:
                            os.kill(existing_pid, 0)
                            lock_fd.close()
                            return False
                        except OSError:
                            pass
            else:
                existing_pid = None
                try:
                    lock_fd.seek(0)
                    existing_pid = int((lock_fd.read() or "0").strip() or "0")
                except (ValueError, OSError):
                    existing_pid = None
                if existing_pid:
                    try:
                        os.kill(existing_pid, 0)
                        lock_fd.close()
                        return False
                    except OSError:
                        pass
            lock_fd.seek(0)
            lock_fd.truncate()
            lock_fd.write(str(os.getpid()))
            lock_fd.flush()
            self._singleton_lock_fd = lock_fd
            return True
        except OSError:
            return False

    def _release_singleton_lock(self):
        if self._singleton_lock_fd is None:
            return
        if fcntl is not None:
            try:
                fcntl.flock(self._singleton_lock_fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            self._singleton_lock_fd.close()
        except OSError:
            pass
        try:
            os.remove(self._singleton_lock_path)
        except OSError:
            pass
        self._singleton_lock_fd = None

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

    def send_proactive(self, to_user: str, content: str, msg_type: QQMessageType = QQMessageType.PRIVATE,
                       bypass_quiet: bool = False) -> bool:
        """Send a proactive message to a QQ user (not in reply to a message)."""
        if not bypass_quiet and self._should_suppress_proactive():
            logger.info(
                f"Suppressing proactive QQ push during quiet window: {self._proactive_quiet_reason}"
            )
            return False
        if self._bot:
            sanitized = self._sanitize_outbound_text(content)
            if not sanitized:
                logger.info("Suppressing empty proactive QQ push")
                return False
            return self._bot.send_proactive(to_user, sanitized, msg_type)
        return False

    @staticmethod
    def _sanitize_outbound_text(content: str) -> str:
        text = (content or "").strip()
        if not text:
            return text
        text = strip_internal_diff(text)
        if not text or has_internal_diff(text):
            return ""
        if text == "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__":
            return ""
        if text.startswith("{") and '"type": "partner_heartbeat"' in text:
            return ""
        return text

    def send_file_proactive(self, to_user: str, file_data: bytes,
                             file_type: int = 4,
                             msg_type: QQMessageType = QQMessageType.PRIVATE,
                             text_content: str = "",
                             file_name: str = "") -> bool:
        """Send a file to a QQ user proactively (not in reply to a message).

        Two-step upload+sends via passive quota-friendly method.
        """
        if not self._bot or not self._bot.get_event_loop() or not self._bot.get_event_loop().is_running():
            logger.error("Bot event loop not running, cannot send file")
            return False
        import asyncio
        future = asyncio.run_coroutine_threadsafe(
            self._bot.send_file(
                to_user, file_data, file_type, msg_type,
                text_content=text_content,
                file_name=file_name,
            ),
            self._bot.get_event_loop(),
        )
        try:
            return future.result(timeout=30)
        except Exception as e:
            logger.error(f"Proactive file send failed: {e}")
            return False

    def reply_with_file(self, msg: QQMessage, file_data: bytes,
                         file_type: int = 4, text_content: str = "",
                         file_name: str = "") -> bool:
        """Reply to a QQ message with a file attachment.

        Uses msg_id + msg_type=7 for passive-reply file sending.
        """
        if not self._bot or not self._bot.get_event_loop() or not self._bot.get_event_loop().is_running():
            logger.error("Bot event loop not running, cannot reply with file")
            return False
        import asyncio
        future = asyncio.run_coroutine_threadsafe(
            self._bot.reply_with_file(msg, file_data, file_type, text_content, file_name=file_name),
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
