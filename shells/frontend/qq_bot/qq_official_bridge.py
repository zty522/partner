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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from partner.application import PartnerApplicationService
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


class QQQfficialBridge:
    def __init__(self, workspace: str, config: QQQfficialBridgeConfig | None = None):
        self.workspace = str(Path(workspace).resolve())
        self.root = str(Path(self.workspace).parent.parent)
        self.instance_id = Path(self.workspace).name
        self.config = config or QQQfficialBridgeConfig(); self.config.workspace = self.workspace
        state = Path(self.workspace) / "state"; state.mkdir(parents=True, exist_ok=True)
        self._delivery_state_file = str(state / "qq_delivery_state.json")
        self._history_file = str(state / "qq_chat_history.jsonl")
        self._seen_file = state / "qq_seen_messages.json"
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
        self._write_delivery_state(True, "ready")

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

    def _handle_message(self, msg: QQMessage) -> None:
        text = str(msg.content or msg.raw_message or "").strip()
        if not text or not self._remember(msg.msg_id): return
        self._stats["messages_received"] += 1
        self._append_history("user", text, sender_id=msg.sender_id, sender_name=msg.sender_name,
                             msg_id=msg.msg_id)
        special = self._handle_special_command(text, msg)
        if special:
            self._reply(msg, special); return
        submission = PartnerApplicationService(self.root).submit(
            text, channel="qq", sender_id=msg.sender_id, sender_name=msg.sender_name or "QQ用户",
            persona_hint=self.instance_id,
        )
        self._reply(msg, submission.message if submission.accepted else "这条任务没有进入队列：" + submission.message)

    def _handle_special_command(self, text: str, _msg: QQMessage) -> str | None:
        if text.strip().lower() in {"/help", "help", "帮助", "使用帮助"}:
            return "直接告诉我你想推进什么即可。我会把任务交给统一 Event 工作流；/status 可查看当前后台工作。"
        if text.strip().lower() in {"/status", "status", "状态", "当前状态"}:
            jobs = PartnerApplicationService(self.root).list_jobs(limit=20)
            active = [x for x in jobs if x.get("status") in {"queued", "dispatched", "running"}]
            if not active: return "当前没有正在执行的后台工作。"
            return "当前后台工作：" + "；".join(f"{x.get('title')}（{x.get('status')}）" for x in active[:4])
        return None

    def _start_notification_poller(self) -> None:
        def poll() -> None:
            outbound = Path(self.root) / "state/application/outbound" / self.instance_id
            while self._running:
                self._expire_stale_delivery_startup()
                for path in sorted(outbound.glob("*.json")) if outbound.exists() else []:
                    try:
                        value = json.loads(path.read_text(encoding="utf-8"))
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
            if not image.is_file() or (asset.get('sha256') and digest(image)!=asset['sha256']): return False
            if not self.send_file_proactive(user,image.read_bytes(),file_type=1,file_name=image.name): return False
            value.setdefault('images_delivered',[]).append(identity)
            value.setdefault('component_acks',[]).append({'kind':'image','id':asset.get('id'),'sha256':identity,'at':time.time()})
            write_json(path,value)
        pdfs = list(value.get('pdf_artifacts') or [])[:1]
        if pdfs and not value.get('pdf_delivered'):
            pdf = Path(str(pdfs[0]))
            if not (pdf.is_file() and self.send_file_proactive(user, pdf.read_bytes(), file_name=pdf.name)):
                return False
            value['pdf_delivered'] = True
            value.setdefault('component_acks',[]).append({'kind':'pdf','at':time.time()})
            write_json(path, value)
        return True

    def _record_delivery_ack(self, payload: dict, path: Path) -> None:
        ledger = EventLedger(self.root)
        event = ledger.create("delivery.channel_ack", "delivery", correlation_id=str(payload.get("job_id") or ""),
                              job_id=str(payload.get("job_id") or ""), instance_id=self.instance_id, channel="qq")
        ledger.complete(event, EventSummary(event_id=event.event_id, status="completed",
                                           headline="QQ 已确认消息交付", outcome="文本、请求的图片与 PDF 均获得渠道确认",
                                           evidence_refs=[str(path)], notification_kind="routine"))
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
    bridge = QQQfficialBridge(workspace)
    if config_path: bridge.load_config_from_file(config_path)
    return bridge


__all__ = ["QQQfficialBridge", "QQQfficialBridgeConfig", "application_delivery_due",
           "record_application_delivery_failure", "create_bridge"]
