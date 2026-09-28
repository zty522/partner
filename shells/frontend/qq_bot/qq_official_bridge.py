"""QQ transport adapter for the shared Partner Application.

This module performs transport only: intake becomes an Application Command,
and outbound files are removed from the hot queue only after a QQ ACK.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import threading
import time
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from partner.application import PartnerApplicationService
from partner.application.orchestrator import (
    orchestrate_submit, IdempotencyConflict,
    _IdempotencyInProgress as OrchestratorInProgress,
)
from partner.event_fabric import EventLedger, EventSummary
from partner.interfaces.messaging import split_outbound_text
from partner.workspace.workspace_layout import append_history
from shells.frontend.qq_bot.qq_official_bot import QQQfficialBot, QQBotInfo, QQMessage, QQMessageType

logger = logging.getLogger(__name__)
_BACKOFF = (30, 120, 600, 1800, 3600)


def application_delivery_due(outbound: dict, now_epoch: float) -> bool:
    return float(outbound.get("next_attempt_epoch") or 0) <= float(now_epoch)


def record_application_delivery_failure(outbound: dict, now_epoch: float) -> dict:
    value = dict(outbound); attempts = int(value.get("delivery_attempts") or 0) + 1
    value["delivery_attempts"] = attempts
    value["last_delivery_failure_at"] = datetime.fromtimestamp(now_epoch, timezone.utc).isoformat()
    value["next_attempt_epoch"] = now_epoch + _BACKOFF[min(attempts - 1, len(_BACKOFF) - 1)]
    value["delivery_state"] = "blocked" if attempts >= len(_BACKOFF) else "retry_wait"
    return value


@dataclass
class QQQfficialBridgeConfig:
    app_id: str = ""
    app_secret: str = ""
    is_sandbox: bool = False
    auto_reconnect: bool = True
    max_reply_length: int = 1800
    workspace: str = ""
    outbound_delivery_enabled: bool = True


class QQQfficialBridge:
    def __init__(self, workspace: str, config: QQQfficialBridgeConfig | None = None):
        supplied = Path(workspace).resolve()
        if supplied.parent.name == "instances" and re.fullmatch(r"0[1-5]", supplied.name):
            instance_workspace = supplied
            root = supplied.parent.parent
            instance_id = supplied.name
        else:
            root = supplied
            instance_id = str(os.environ.get("PARTNER_INSTANCE_ID") or "02")
            if not re.fullmatch(r"0[1-5]", instance_id):
                raise ValueError("PARTNER_INSTANCE_ID must be one of 01..05")
            instance_workspace = root / "instances" / instance_id
        self.workspace = str(instance_workspace)
        self.root = str(root)
        self.instance_id = instance_id
        self.config = config or QQQfficialBridgeConfig(); self.config.workspace = self.workspace
        state = Path(self.workspace) / "state"; state.mkdir(parents=True, exist_ok=True)
        self._delivery_state_file = str(state / "qq_delivery_state.json")
        self._history_file = str(state / "qq_chat_history.jsonl")
        self._seen_file = state / "qq_seen_messages.json"
        self._binding_file = state / "record" / "bot_id.json"
        self._lock_path = state / "qq_bridge.lock"; self._lock_handle = None
        self._bot: QQQfficialBot | None = None; self._running = False
        self._stats = {"messages_received": 0, "messages_sent": 0, "errors": 0}
        self._seen = self._load_seen()

    def configure(self, app_id: str, app_secret: str, is_sandbox: bool = False) -> None:
        self.config.app_id, self.config.app_secret = app_id, app_secret
        self.config.is_sandbox = is_sandbox

    def load_config_from_file(self, config_path: str) -> bool:
        try:
            value = json.loads(Path(config_path).read_text(encoding="utf-8"))
            self.config.app_id = str(value.get("app_id") or "")
            self.config.app_secret = str(value.get("app_secret") or "")
            self.config.is_sandbox = bool(value.get("is_sandbox"))
            self.config.auto_reconnect = bool(value.get("auto_reconnect", True))
            self.config.max_reply_length = int(value.get("max_reply_length") or 1800)
            return bool(self.config.app_id and self.config.app_secret)
        except (OSError, TypeError, ValueError):
            return False

    def _load_seen(self) -> dict[str, float]:
        try:
            value = json.loads(self._seen_file.read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in value.items()}
        except (OSError, TypeError, ValueError):
            return {}

    def _remember(self, message_id: str) -> bool:
        now = time.time(); self._seen = {k: v for k, v in self._seen.items() if now - v < 86400}
        if message_id and message_id in self._seen: return False
        if message_id: self._seen[message_id] = now
        temporary = self._seen_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._seen), encoding="utf-8"); os.replace(temporary, self._seen_file)
        return True

    def _acquire_lock(self) -> bool:
        self._lock_handle = self._lock_path.open("a+")
        try: fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); return True
        except BlockingIOError: return False

    def start(self) -> None:
        if not self.config.app_id or not self.config.app_secret:
            raise RuntimeError("QQ Bot credentials are not configured")
        if not self._acquire_lock():
            raise RuntimeError("QQ bridge already owns this instance")
        self._running = True; self._write_delivery_state(False, "starting")
        self._bot = QQQfficialBot(
            self.config.app_id, self.config.app_secret,
            is_sandbox=self.config.is_sandbox,
            auto_reconnect=self.config.auto_reconnect,
        )
        self._bot.set_message_handler(self._handle_message)
        self._bot.set_ready_handler(self._handle_ready)
        self._bot.set_error_handler(self._handle_error)
        if self.config.outbound_delivery_enabled:
            self._start_notification_poller()
        try: self._bot.start()
        finally: self._running = False; self._write_delivery_state(False, "stopped")

    def start_async(self) -> threading.Thread:
        thread = threading.Thread(target=self.start, daemon=True, name=f"qq-{self.instance_id}")
        thread.start(); return thread

    def stop(self) -> None:
        self._running = False
        if self._bot: self._bot.stop()

    def _write_delivery_state(self, ready: bool, status: str, error: str = "") -> None:
        value = {"schema_version": 2, "delivery_ready": ready, "status": status,
                 "error_type": error, "updated_at": datetime.now(timezone.utc).isoformat()}
        path = Path(self._delivery_state_file); temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"); os.replace(temporary, path)

    def _persist_identity_binding(self, info: QQBotInfo | None = None,
                                  user_openid: str = "") -> None:
        """Persist the identity contract consumed by submission routing.

        Readiness proves the bot identity; inbound messages add scoped user
        OpenIDs.  Credentials are deliberately excluded.  The replace is
        atomic so the CLI cannot observe a half-written binding.
        """
        current: dict[str, Any] = {}
        try:
            current = json.loads(self._binding_file.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            pass
        allowed = [str(value) for value in current.get("allowed_user_openids") or []
                   if str(value)]
        # Migrate the pre-binding-format record only when it proves a real
        # inbound C2C event.  This is not a "last seen" routing guess: the
        # OpenID, platform message id and message type were written by this
        # instance's authenticated QQ callback.  Once migrated, all Web/QQ
        # submissions use the normal strict recipient_ref verification.
        if not allowed:
            legacy_path = Path(self.workspace) / "state" / "qq_user_context.json"
            try:
                legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                legacy = {}
            legacy_openid = str(legacy.get("openid") or "").strip()
            if (legacy_openid and str(legacy.get("message_type") or "") == "c2c"
                    and str(legacy.get("last_msg_id") or "").strip()):
                allowed.append(legacy_openid)
        if user_openid and user_openid not in allowed:
            allowed.append(user_openid)
        value = {
            "schema_version": 1,
            "instance_id": self.instance_id,
            "bot_id": str(getattr(info, "id", "") or current.get("bot_id") or self.config.app_id),
            "bot_name": str(getattr(info, "name", "") or current.get("bot_name") or ""),
            "allowed_user_openids": allowed,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._binding_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._binding_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self._binding_file)

    def _expire_stale_delivery_startup(self, timeout_sec: float | None = None) -> bool:
        timeout = float(os.environ.get("PARTNER_QQ_READY_TIMEOUT_SEC", "90")) if timeout_sec is None else timeout_sec
        try:
            value = json.loads(Path(self._delivery_state_file).read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(str(value.get("updated_at"))).timestamp()
            if value.get("delivery_ready") or value.get("status") != "starting" or time.time() - updated < timeout:
                return False
        except (OSError, TypeError, ValueError): return False
        self._write_delivery_state(False, "error", "ReadyTimeoutError"); return True

    def _handle_ready(self, _info: QQBotInfo) -> None:
        self._persist_identity_binding(_info)
        status = "ready" if self.config.outbound_delivery_enabled else "ready_commands_only"
        self._write_delivery_state(True, status)

    def _handle_error(self, exc: Exception) -> None:
        self._stats["errors"] += 1; self._write_delivery_state(False, "error", type(exc).__name__)

    def _append_history(self, role: str, content: str, **extra: Any) -> None:
        row = {"role": role, "content": content, "timestamp": datetime.now(timezone.utc).isoformat(),
               "source": "qq", **extra}
        append_history(self.workspace, row, ("qq_chat_history.jsonl", "dialog_history.jsonl"))

    def _reply(self, msg: QQMessage, text: str) -> bool:
        if not self._bot or not self._bot.get_event_loop(): return False
        chunks = split_outbound_text(text, self.config.max_reply_length)
        for chunk in chunks:
            future = asyncio.run_coroutine_threadsafe(self._bot.reply_message(msg, chunk), self._bot.get_event_loop())
            if not future.result(timeout=20): return False
            self._append_history("assistant", chunk, reply_to=msg.msg_id, target_id=msg.sender_id,
                                 delivery_acknowledged=True)
        return True

    def _normalise_attachments(self, msg: QQMessage) -> list[dict]:
        """Extract attachment content refs from the production QQMessage
        contract: attachments live in ``msg.extra["attachments"]``, never on
        a top-level ``msg.attachments`` field."""
        raw = (msg.extra or {}).get("attachments") if isinstance(msg.extra, dict) else None
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or item.get("type") or item.get("file_type") or "attachment")
            name = str(item.get("name") or item.get("filename") or item.get("file_name") or "")
            url = str(item.get("url") or item.get("file_url") or item.get("download_url") or "")
            local_path = str(item.get("path") or "")
            sha = str(item.get("sha256") or item.get("content_sha256") or "")
            entry = {"kind": kind, "name": name}
            if local_path:
                entry["path"] = local_path
                entry["download_state"] = "present"
            elif url:
                entry["url"] = url
                entry["download_state"] = "pending"
            else:
                entry["download_state"] = "missing"
            if sha:
                entry["content_sha256"] = sha
            out.append(entry)
        return out

    @staticmethod
    def _attachment_signature(attachments: list[dict]) -> list[dict]:
        """Stable content/version reference for idempotency."""
        sig: list[dict] = []
        for a in attachments:
            s = {"kind": a.get("kind", ""), "name": a.get("name", "")}
            if a.get("content_sha256"):
                s["content_sha256"] = a["content_sha256"]
            elif a.get("path"):
                s["path"] = a["path"]
            elif a.get("url"):
                s["url"] = a["url"]
            else:
                s["download_state"] = "missing"
            sig.append(s)
        return sig

    def _handle_message(self, msg: QQMessage) -> None:
        text = str(msg.content or msg.raw_message or "").strip()
        attachments = self._normalise_attachments(msg)
        if not text and not attachments:
            return
        if not self._remember(msg.msg_id):
            return
        self._persist_identity_binding(user_openid=str(msg.sender_id or ""))
        self._stats["messages_received"] += 1
        self._append_history("user", text or "(attachment-only)",
                             sender_id=msg.sender_id, sender_name=msg.sender_name,
                             msg_id=msg.msg_id, attachment_count=len(attachments))
        special = self._handle_special_command(text, msg)
        if special:
            if not self._reply(msg, special):
                self._record_reply_failure(msg.msg_id, "special_reply")
            return
        try:
            sub_result = orchestrate_submit(
                workspace_root=self.root,
                text=text,
                channel="qq",
                sender_id=msg.sender_id,
                sender_name=msg.sender_name or "QQ用户",
                persona_hint=self.instance_id,
                request_id="qq:" + str(msg.msg_id or msg.sender_id),
                attachments=attachments,
                attachments_signature=self._attachment_signature(attachments),
                subject_allowed_instances=[self.instance_id],
                subject_id=msg.sender_id,
            )
        except IdempotencyConflict as exc:
            if not self._reply(msg, "请求内容与上一次不同（idempotency_conflict）：" + str(exc)):
                self._record_reply_failure(msg.msg_id, "idempotency_conflict")
            return
        except OrchestratorInProgress as exc:
            if not self._reply(msg, "请求正在处理中，请稍候查看结果：" + str(exc)):
                self._record_reply_failure(msg.msg_id, "in_progress")
            return

        if sub_result.job_id:
            self._record_submission_receipt(msg.msg_id, sub_result.job_id,
                                              sub_result.assigned_instance)
            # Acceptance and Flow-plan messages are queued by the dedicated
            # notification compose -> critic -> delivery Events.  Sending a
            # second hard-coded reply here made QQ disagree with Web and hid
            # the actual Event plan.
        else:
            if not self._reply(msg, sub_result.message or "已收到"):
                self._record_reply_failure(msg.msg_id, "direct_answer_reply")

    def _handle_special_command(self, text: str, _msg: QQMessage) -> str | None:
        command = text.strip()
        lowered = command.lower()
        if lowered in {"/help", "help", "帮助", "使用帮助"}:
            return ("直接发送项目任务即可。\n/status：查看本实例在途任务\n"
                    "/last：查看最近任务\n/job <job_id>：查看终态和四条结果链\n"
                    "/flow <job_id>：查看实际 Flow 节点状态\n"
                    "/events <job_id> [页码]：查看 Event 列表\n"
                    "/event <job_id> <event_id或node_id>：查看输入输出\n"
                    "/result <job_id>：查看最终结算；/log <job_id>：网页链接")
        if lowered in {"/status", "status", "状态", "当前状态"}:
            jobs = [row for row in PartnerApplicationService(self.root).list_jobs(limit=50)
                    if str(row.get("assigned_instance") or row.get("origin_instance") or "") == self.instance_id]
            active = [row for row in jobs if row.get("status") in {"queued", "dispatched", "running", "paused"}]
            if not active:
                return "当前没有在途任务。发送 /last 可查看最近一次运行。"
            return "当前在途：\n" + "\n\n".join(self._job_status_text(row) for row in active[:3])
        if lowered in {"/last", "last", "最近任务"}:
            jobs = [row for row in PartnerApplicationService(self.root).list_jobs(limit=50)
                    if str(row.get("assigned_instance") or row.get("origin_instance") or "") == self.instance_id]
            return self._job_status_text(jobs[0]) if jobs else "暂无任务记录。"
        match = re.fullmatch(r"/(job|log|flow|result)\s+([A-Za-z0-9_.-]+)", command, re.I)
        if match:
            kind, needle = match.group(1).lower(), match.group(2)
            row, error = self._find_job(needle)
            if error:
                return error
            if kind == "flow":
                return self._flow_status_text(row)
            if kind == "result":
                return self._result_text(row)
            if kind == "log":
                return f"运行日志：{self._web_url(str(row.get('job_id') or ''))}"
            return self._job_status_text(row)
        match = re.fullmatch(r"/events\s+([A-Za-z0-9_.-]+)(?:\s+(\d+))?", command, re.I)
        if match:
            row, error = self._find_job(match.group(1))
            return error or self._events_text(row, max(1, int(match.group(2) or 1)))
        match = re.fullmatch(r"/event\s+([A-Za-z0-9_.-]+)\s+([A-Za-z0-9_.-]+)", command, re.I)
        if match:
            row, error = self._find_job(match.group(1))
            return error or self._event_detail_text(row, match.group(2))
        return None

    def _instance_jobs(self, limit: int = 200) -> list[dict]:
        return [row for row in PartnerApplicationService(self.root).list_jobs(limit=limit)
                if str(row.get("assigned_instance") or row.get("origin_instance") or "") == self.instance_id]

    def _find_job(self, needle: str) -> tuple[dict, str]:
        matches = [row for row in self._instance_jobs()
                   if str(row.get("job_id") or "") == needle
                   or str(row.get("job_id") or "").startswith(needle)]
        if not matches:
            return {}, "未找到本实例的该任务。"
        exact = [row for row in matches if str(row.get("job_id") or "") == needle]
        if len(matches) > 1 and not exact:
            return {}, "Job 前缀不唯一，请输入更长的 ID。"
        return (exact or matches)[0], ""

    def _trace(self, row: dict) -> dict:
        from partner.web.run_trace import trace_overview
        return trace_overview(self.root, str(row.get("job_id") or ""), limit=500)

    def _web_url(self, job_id: str) -> str:
        base = os.environ.get("PARTNER_WEB_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
        return f"{base}/?job={job_id}"

    def _job_status_text(self, row: dict) -> str:
        job_id = str(row.get("job_id") or "")
        current = str(row.get("current_event_id") or "")
        flow_type = str(row.get("flow_type") or "")
        try:
            trace = self._trace(row)
            last = (trace.get("events") or [])[-1] if trace.get("events") else {}
            if last:
                current = str(last.get("node_id") or current)
                flow_type = str(last.get("flow_type") or flow_type)
            count = int((trace.get("counts") or {}).get("events") or 0)
        except Exception:
            count = 0
        title = str(row.get("title") or row.get("request") or job_id)[:48]
        result = [f"{title}\nJob：{job_id}\n状态：{row.get('status')}｜Flow：{flow_type or '-'}｜"
                  f"当前：{current or '已结束'}｜Events：{count}"]
        try:
            for item in (trace.get("chains") or {}).values():
                result.append(f"{item.get('name')}：{item.get('events', 0)} Events｜"
                              f"失败 {item.get('failed', 0)}｜{item.get('last_summary') or '未触发'}")
            completion = trace.get("completion") or {}
            result.append(f"终态记录：{'已写入' if completion.get('recorded') else '尚未写入'}")
        except Exception:
            pass
        result.append(self._web_url(job_id))
        return "\n".join(result)

    def _flow_status_text(self, row: dict) -> str:
        trace = self._trace(row)
        lines = [f"Job {row.get('job_id')} 的实际 Flow："]
        for flow in trace.get("flows") or []:
            lines.append(f"\n{flow.get('flow_type')}｜{flow.get('status')}｜{flow.get('flow_id')}")
            for node in flow.get("nodes") or []:
                lines.append(f"- {node.get('node_id')} [{node.get('runtime_status')}] {node.get('event_type')}")
        lines.append(self._web_url(str(row.get("job_id") or "")))
        return "\n".join(lines)

    def _events_text(self, row: dict, page: int) -> str:
        trace = self._trace(row)
        lifecycle = trace.get("events") or []
        final_by_event: dict[str, dict] = {}
        for item in lifecycle:
            final_by_event[str(item.get("event_id") or "")] = item
        events = list(final_by_event.values())
        page_size = 10
        start = (page - 1) * page_size
        selected = events[start:start + page_size]
        pages = max(1, (len(events) + page_size - 1) // page_size)
        if not selected:
            return f"页码超出范围；共 {len(events)} Events，{pages} 页。"
        lines = [f"Job {row.get('job_id')} Events 第 {page}/{pages} 页："]
        for item in selected:
            lines.append(f"- {item.get('node_id')} [{item.get('status')}]\n  {item.get('event_id')}｜{item.get('event_type')}")
        lines.append(f"输入输出：/event {row.get('job_id')} <event_id或node_id>")
        return "\n".join(lines)

    def _event_detail_text(self, row: dict, needle: str) -> str:
        trace = self._trace(row)
        candidates = [item for item in trace.get("events") or []
                      if str(item.get("event_id") or "") == needle
                      or str(item.get("event_id") or "").startswith(needle)
                      or str(item.get("node_id") or "") == needle]
        ids = list(dict.fromkeys(str(item.get("event_id") or "") for item in candidates))
        if not ids:
            return "没有找到该 Event 或节点。先用 /events <job_id> 查看列表。"
        if len(ids) > 1:
            return "匹配到多个 Event，请使用完整 event_id。"
        from partner.web.run_trace import event_detail
        detail = event_detail(self.root, str(row.get("job_id") or ""), ids[0])
        text = json.dumps(detail.get("lifecycle") or [], ensure_ascii=False, indent=2)
        if len(text) > 9000:
            text = text[:9000] + "\n…内容过长，完整内容请看网页。"
        return f"Event {ids[0]} 的脱敏生命周期：\n{text}\n{self._web_url(str(row.get('job_id') or ''))}"

    def _result_text(self, row: dict) -> str:
        trace = self._trace(row)
        completion = trace.get("completion") or {}
        lines = [f"Job {row.get('job_id')} 最终结算：{completion.get('status')}",
                 f"终态日志：{'已写入' if completion.get('recorded') else '尚未写入'}",
                 f"节点：完成 {completion.get('completed_nodes', 0)}｜失败 {completion.get('failed_nodes', 0)}｜跳过 {completion.get('skipped_nodes', 0)}"]
        if completion.get("error"):
            lines.append(f"错误：{completion.get('error')}")
        outputs = completion.get("final_outputs") or {}
        if outputs:
            body = json.dumps(outputs, ensure_ascii=False, indent=2)
            lines.append("最终节点输出：\n" + body[:6000])
        lines.append(self._web_url(str(row.get("job_id") or "")))
        return "\n".join(lines)

    def _record_submission_receipt(self, msg_id: str, job_id: str,
                                     assigned_instance: str) -> None:
        """Record the inbound request -> Job association as a *submission
        receipt*, NOT a channel ACK.  A sent/delivered state may only be
        produced by the channel returning send-success evidence (see
        ``_record_delivery_ack``).  This file lives in ``inbound/``, is
        named ``*.accepted``, and carries no ``delivered_at``."""
        try:
            target = (self.root / "state" / "application" / "inbound"
                      / ("qq_" + str(msg_id) + ".accepted"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({
                "schema_version": 2,
                "kind": "submission_receipt",
                "msg_id": msg_id,
                "job_id": job_id,
                "assigned_instance": assigned_instance,
                "accepted_at": time.time(),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.exception("submission receipt write failed")

    def _record_reply_failure(self, msg_id: str, stage: str) -> None:
        """Persist an undelivered reply so recovery can retry later.
        Never marks sent/delivered — only records that the send attempt
        failed at the given stage."""
        try:
            target = (self.root / "state" / "application" / "inbound"
                      / ("qq_" + str(msg_id) + ".reply_failed"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({
                "schema_version": 2,
                "kind": "reply_failure",
                "msg_id": msg_id,
                "stage": stage,
                "failed_at": time.time(),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.exception("reply failure write failed")

    def _start_notification_poller(self) -> None:
        def poll() -> None:
            outbound = Path(self.root) / "state/application/outbound" / self.instance_id
            while self._running:
                self._expire_stale_delivery_startup()
                paths = list(outbound.glob("*.json")) if outbound.exists() else []
                def queued_order(path: Path):
                    try:
                        row = json.loads(path.read_text(encoding="utf-8"))
                        # Terminal business text and PDF are the reason the
                        # user ran the job.  Let them pass a lifecycle backlog;
                        # lifecycle messages keep chronological order within
                        # their own class and are still all delivered.
                        priority = 1 if '.notification.' in path.name else 0
                        return (priority, str(row.get("created_at") or ""), path.name)
                    except (OSError, ValueError, TypeError):
                        return (2, "", path.name)
                # Notification filenames are unique rather than sequential.
                # Preserve the Event creation order using the durable payload
                # timestamp; lexical UUID order can send completion before start.
                for path in sorted(paths, key=queued_order):
                    try:
                        value = json.loads(path.read_text(encoding="utf-8"))
                        terminal_state = str(value.get("delivery_state") or "")
                        if terminal_state in {"sent", "delivered", "superseded", "blocked"}:
                            suffix = ".sent" if terminal_state in {"sent", "delivered"} else f".{terminal_state}"
                            os.replace(path, path.with_suffix(suffix))
                            continue
                        if not application_delivery_due(value, time.time()): continue
                        from partner.presentation.notifications import stale_progress
                        if stale_progress(path,value):
                            value['delivery_state']='superseded';value['suppression_reason']='newer pending progress for same request'
                            from partner.runtime.action_execution import write_json
                            write_json(path,value);os.replace(path,path.with_suffix('.superseded'))
                            continue
                        delivered = self._deliver_application_payload(path, value)
                        if delivered:
                            self._record_delivery_ack(value, path)
                            value['delivery_state'] = 'sent'
                            from partner.runtime.action_execution import write_json
                            write_json(path, value)
                            os.replace(path, path.with_suffix(".sent"))
                        else:
                            failed = record_application_delivery_failure(value, time.time())
                            temporary = path.with_suffix(".tmp"); temporary.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8"); os.replace(temporary, path)
                            if failed["delivery_state"] == "blocked": os.replace(path, path.with_suffix(".blocked"))
                    except Exception: logger.exception("QQ outbound delivery failed")
                time.sleep(2)
        threading.Thread(target=poll, daemon=True, name=f"qq-outbound-{self.instance_id}").start()

    def _deliver_application_payload(self, path: Path, value: dict) -> bool:
        """Persist successful components so a PDF retry does not repeat text."""
        from partner.runtime.action_execution import write_json
        user = str(value.get('to_user') or '')
        if not user.strip(): return False
        if not value.get('text_delivered'):
            chunks=split_outbound_text(str(value.get('content') or ''),getattr(getattr(self,'config',None),'max_reply_length',1800))
            for index,chunk in enumerate(chunks):
                if index < int(value.get('text_chunks_delivered',0)): continue
                if not self.send_proactive(user,chunk,bypass_quiet=True): return False
                bot = getattr(self, "_bot", None)
                receipt = (bot.get_last_api_post_receipt()
                           if bot and hasattr(bot, "get_last_api_post_receipt") else {})
                value.setdefault('platform_acks', []).append({
                    'kind': 'text', 'chunk': index + 1,
                    'http_status': receipt.get('http_status'),
                    'platform_message_id': receipt.get('platform_message_id') or '',
                    'timestamp': receipt.get('timestamp') or '',
                })
                value['text_chunks_delivered']=index+1
                write_json(path,value)
            if not chunks: return False
            value['text_delivered'] = True
            value.setdefault('component_acks',[]).append({'kind':'text','at':time.time()})
            write_json(path, value)
        for asset in value.get('image_artifacts') or []:
            identity=asset.get('sha256') or asset['path']
            if identity in value.get('images_delivered',[]): continue
            image=Path(asset['path'])
            from partner.presentation.figures import digest
            # Image is optional: degrade gracefully on missing/invalid/transport failures.
            # Text and PDF still ship so the user always receives a usable result.
            if not image.is_file():
                value.setdefault('images_skipped',[]).append({'id':asset.get('id'),'reason':'file_missing','path':str(image),'at':time.time()})
                write_json(path,value); continue
            if asset.get('sha256') and digest(image)!=asset['sha256']:
                value.setdefault('images_skipped',[]).append({'id':asset.get('id'),'reason':'sha_mismatch','expected':asset['sha256'],'actual':digest(image),'at':time.time()})
                write_json(path,value); continue
            if not self.send_file_proactive(user,image.read_bytes(),file_type=1,file_name=image.name):
                value.setdefault('images_skipped',[]).append({'id':asset.get('id'),'reason':'transport_failed','at':time.time()})
                write_json(path,value); continue
            value.setdefault('images_delivered',[]).append(identity)
            value.setdefault('component_acks',[]).append({'kind':'image','id':asset.get('id'),'sha256':identity,'at':time.time()})
            write_json(path,value)
        pdfs = list(value.get('pdf_artifacts') or [])[:1]
        if pdfs and not value.get('pdf_delivered'):
            pdf = Path(str(pdfs[0]))
            if not pdf.is_file():
                return False
            pdf_bytes = pdf.read_bytes()
            import hashlib
            actual_sha = hashlib.sha256(pdf_bytes).hexdigest()
            expected_sha = str(value.get('pdf_sha256') or '')
            if expected_sha and actual_sha != expected_sha:
                value['pdf_version_error'] = {
                    'reason': 'sha_mismatch', 'expected': expected_sha,
                    'actual': actual_sha, 'path': str(pdf), 'at': time.time()}
                write_json(path, value)
                return False
            if not self.send_file_proactive(user, pdf_bytes, file_name=pdf.name):
                return False
            bot = getattr(self, "_bot", None)
            receipt = (bot.get_last_api_post_receipt()
                       if bot and hasattr(bot, "get_last_api_post_receipt") else {})
            value['pdf_delivered'] = True
            value.setdefault('component_acks',[]).append({
                'kind':'pdf', 'filename':pdf.name, 'sha256':actual_sha,
                'size_bytes':len(pdf_bytes), 'at':time.time(),
                'http_status':receipt.get('http_status'),
                'platform_message_id':receipt.get('platform_message_id') or ''})
            write_json(path, value)
        return True

    def _record_delivery_ack(self, payload: dict, path: Path) -> None:
        ledger = EventLedger(self.root)
        event = ledger.create("delivery.channel_ack", "delivery", correlation_id=str(payload.get("job_id") or ""),
                              job_id=str(payload.get("job_id") or ""), instance_id=self.instance_id, channel="qq")
        summary = EventSummary(event_id=event.event_id, status="completed",
                               headline="QQ 已确认消息交付", outcome="文本、请求的图片与 PDF 均获得渠道确认",
                               evidence_refs=[str(path)], notification_kind="routine")
        ledger.complete(event, summary)
        try:
            from partner.index.job_repository import init as _init_jobs
            from partner.runtime.event_run_log import EventRunLog
            job = _init_jobs(self.root).get(str(payload.get("job_id") or "")) or {}
            EventRunLog(self.root, str(payload.get("job_id") or "")).event(
                phase="finished", flow_id=str(job.get("flow_id") or ""),
                flow_type=str(job.get("flow_type") or "message_delivery"),
                node_id="channel_ack", event_id=event.event_id,
                event_type="delivery.channel_ack",
                inputs={"channel": "qq", "outbound_receipt": str(path)},
                outputs={"ok": True, "status": "completed",
                         "summary": summary.headline,
                         "component_acks": list(payload.get("component_acks") or []),
                         "text_delivered": bool(payload.get("text_delivered")),
                         "pdf_delivered": bool(payload.get("pdf_delivered"))},
                status="completed")
        except Exception:
            logger.exception("QQ ACK saved; run-trace projection failed")
        try:
            from partner.runtime.iteration_receipts import write_request_receipts
            job_path = Path(self.root) / "state/application/jobs" / f"{payload.get('job_id')}.json"
            job = json.loads(job_path.read_text(encoding="utf-8"))
            write_request_receipts(self.root, job["root_event_id"])
        except Exception:
            logger.exception("Delivery ACK saved; iteration projection refresh failed")


    def send_proactive(self, to_user: str, content: str, msg_type: QQMessageType = QQMessageType.PRIVATE,
                       bypass_quiet: bool = False) -> bool:
        if not self._bot or not content.strip(): return False
        for chunk in split_outbound_text(content, self.config.max_reply_length):
            if not self._bot.send_proactive(to_user, chunk, msg_type): return False
            self._append_history("assistant", chunk, target_id=to_user, channel="proactive",
                                 delivery_acknowledged=True)
        return True

    def send_file_proactive(self, to_user: str, file_data: bytes, file_type: int = 4,
                            msg_type: QQMessageType = QQMessageType.PRIVATE,
                            text_content: str = "", file_name: str = "") -> bool:
        if not self._bot or not self._bot.get_event_loop(): return False
        future = asyncio.run_coroutine_threadsafe(
            self._bot.send_file(to_user, file_data, file_type, msg_type,
                                text_content=text_content, file_name=file_name), self._bot.get_event_loop())
        return bool(future.result(timeout=40))

    def get_stats(self) -> dict: return dict(self._stats)
    def get_config_dict(self) -> dict: return {"app_id": self.config.app_id, "is_sandbox": self.config.is_sandbox}


def create_bridge(workspace: str, config_path: str | None = None) -> QQQfficialBridge:
    target = Path(workspace).resolve()
    if config_path:
        config = Path(config_path).resolve()
        parts = config.parts
        if "instances" in parts:
            index = parts.index("instances")
            if index + 1 < len(parts) and re.fullmatch(r"0[1-5]", parts[index + 1]):
                target = Path(*parts[:index + 2])
    bridge = QQQfficialBridge(str(target))
    if config_path: bridge.load_config_from_file(config_path)
    return bridge


__all__ = ["QQQfficialBridge", "QQQfficialBridgeConfig", "application_delivery_due",
           "record_application_delivery_failure", "create_bridge"]
