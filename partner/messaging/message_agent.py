"""MessageAgent: the instance's asynchronous inbound message thread.

Consumes the inbox, classifies each message (``chat`` / ``control`` /
``task``) with one flash model call grounded in a rich chat context, then:

- ``chat``    -> replies directly with a truthful, user-readable answer
- ``control`` -> writes a control signal for the running engine + replies
- ``task``    -> submits a job via the fast-path intent contract + replies

The thread never blocks the QQ event loop; it runs inside the instance
process alongside the bridge.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .inbox import read_pending, mark_done
from .control import write_control
from .chat_context import chat_context
from .attachments import materialise_attachments

CLASSIFY_SYSTEM = (
    "你是 Partner 的消息分拣员。用户正在与一个连续运行的自主代理对话：代理既在执行长任务，"
    "也能与你交流。请对用户消息分类（只输出 JSON）：\n"
    "字段：intent（chat|control|task）、reply（给用户的回复，1-3 句普通中文）、"
    "reason（一句话理由）、task_payload（仅 intent=task 时，见下）。\n"
    "分类规则：\n"
    "- chat：闲聊、问候、感谢、问进展/问状态/问最近做了什么、无明确新任务指令的普通交流。"
    "回复要真实：根据下方运行记录回答，计划不等于完成，未知不猜；优先说具体内容与意义。\n"
    "- control：明确要求停止、暂停、继续、切换方向、调整当前运行、要求只回复不执行等控制指令。"
    "kind 取 stop|pause|resume|direct 之一，value 写用户具体要求。\n"
    "- task：明确的执行请求——读取/分析某个链接、图片、文档，推进项目，跑自进化/主动学习等。"
    "task_payload 字段：dispatch_target（project_iteration 或已有项目名或 content_read_reply 目标）、"
    "goal（≤4 项，每项一句话）、constraints（仅原文明示约束）、success_criteria（≤4 项）、"
    "workstream_type（project_research|active_learning|self_improvement|mixed）、"
    "warm_reply（1-2 句告诉用户接下来做什么，不承诺未确定结果，不报内部ID/文件名/哈希）、"
    "report_policy（none|milestone|final，默认 milestone）。\n"
    "回复真实性铁律：不得声称已完成新实验/已读取内容，除非运行记录证明；不得编造事实。"
    "用户可读性：不要用『结算/判断/边界/event/flow/job_id』等机器词；直接说人话。"
)


class MessageAgent:
    """One asynchronous consumer of the instance inbox."""

    def __init__(
        self,
        root: str,
        *,
        instance_id: str = "01",
        workspace: str | None = None,
        adapter=None,
        reply_sender=None,
        poll_seconds: float = 2.0,
        enabled: bool = True,
    ):
        self.root = Path(root).resolve()
        # Instance workspace is where chat history lives (instances/01/state);
        # root is the global partner workspace (jobs/inbox/control/service).
        self.workspace = Path(workspace).resolve() if workspace else self.root
        self.instance_id = str(instance_id)
        self.adapter = adapter
        self.reply_sender = reply_sender  # callable(sender_id, content) -> bool
        self.poll_seconds = float(poll_seconds)
        self.enabled = enabled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stats = {"processed": 0, "chat": 0, "control": 0, "task": 0, "errors": 0}

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"message-agent-{self.instance_id}"
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            if not self.enabled:
                continue
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                self._stats["errors"] += 1
                time.sleep(1.0)

    # ---------------------------------------------------------------- processing
    def run_once(self) -> int:
        """Process all pending inbox messages; return processed count."""
        messages = read_pending(self.root, persona_hint=self.instance_id)
        if not messages:
            return 0
        for msg in messages:
            try:
                self._process(msg)
            except Exception as exc:  # noqa: BLE001
                self._stats["errors"] += 1
                mark_done(self.root, str(msg.get("msg_id") or ""),
                          outcome="error", detail=f"{type(exc).__name__}: {exc}")
        return len(messages)

    def _process(self, msg: dict) -> None:
        sender_id = str(msg.get("sender_id") or "")
        text = str(msg.get("text") or "")
        attachments = list(msg.get("attachments") or [])
        msg_id = str(msg.get("msg_id") or "")
        self._stats["processed"] += 1

        # Materialise attachments (download docs/images; keep URLs for retrieve).
        work_dir = self.root / "state/application/inbound_work" / msg_id
        materialised = materialise_attachments(self.root, attachments, work_dir=work_dir)
        for entry in materialised:
            if entry.get("material") == "text" and entry.get("text"):
                text = (text + "\n\n[附件内容]\n" + entry["text"]).strip()
            elif entry.get("material") == "image":
                text = (text + f"\n\n[附件图片：{entry.get('name')}]").strip()

        context = chat_context(
            self.root, chat_workspace=self.workspace,
            persona_hint=self.instance_id, sender_id=sender_id,
            request=text, attachments=materialised,
        )
        classification = self._classify(text, context, attachments=materialised)
        intent = str(classification.get("intent") or "chat")
        reply = str(classification.get("reply") or "").strip()

        if intent == "control":
            payload = classification.get("payload") or {}
            write_control(self.root, kind=str(payload.get("kind") or "pause"),
                value=str(payload.get("value") or text)[:2000],
                reason=str(classification.get("reason") or "")[:1000],
                user=sender_id, persona_hint=self.instance_id,
            )
            self._stats["control"] += 1
            self._send(sender_id, reply or "好的，已按你的要求调整。")
            mark_done(self.root, msg_id, outcome="control", detail=reply)
            return

        if intent == "task":
            submitted = self._submit_task(text, classification, sender_id=sender_id, msg=msg)
            self._stats["task"] += 1
            # Double-receipt fix (2026-10-12): service.submit already delivered
            # the "accepted" notice through publish_lifecycle (compose -> gate
            # -> critic -> delivery.send_text).  Re-sending `submitted` here put
            # the same acceptance on QQ twice within a second.  Only send a
            # fallback when submission outright failed or returned nothing.
            failed = (not submitted) or ("任务提交失败" in str(submitted))
            if failed:
                self._send(sender_id, submitted or reply or "已收到，正在处理。")
            mark_done(self.root, msg_id, outcome="task", detail=str(submitted or reply or "task"))
            return

        # chat
        self._stats["chat"] += 1
        self._send(sender_id, reply or "收到。")
        mark_done(self.root, msg_id, outcome="chat", detail=reply)

    def _classify(self, text: str, context: dict, *, attachments: list[dict]) -> dict:
        """One flash model call; deterministic JSON contract."""
        if self.adapter is None:
            return {"intent": "task", "reply": "", "reason": "no_adapter",
                    "payload": {"dispatch_target": "project_iteration",
                                "goal": [text[:500]], "workstream_type": "project_research",
                                "warm_reply": "收到，正在处理。"}}
        prompt = (
            CLASSIFY_SYSTEM
            + "\n\n当前实例正在运行的上下文（真实只读记录）：\n"
            + json.dumps(context, ensure_ascii=False)[:16000]
            + "\n\n用户消息原文=" + json.dumps(text, ensure_ascii=False)[:4000]
            + "\n附件=" + json.dumps(
                [{"kind": a.get("kind"), "name": a.get("name"), "material": a.get("material")}
                 for a in attachments], ensure_ascii=False)[:2000]
            + "\n只输出 JSON。"
        )
        try:
            raw = self.adapter.chat(prompt, purpose="message_classify")
        except Exception as exc:  # noqa: BLE001
            return {"intent": "task", "reply": "", "reason": f"classify_error:{exc}",
                    "payload": {"dispatch_target": "project_iteration",
                                "goal": [text[:500]], "workstream_type": "project_research",
                                "warm_reply": "收到，正在处理。"}}
        return self._parse_classification(raw, text)

    @staticmethod
    def _parse_classification(raw: str, text: str) -> dict:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            # strip code fences then retry once
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```", 2)[1] if "```" in cleaned[3:] else cleaned
            try:
                value = json.loads(cleaned)
            except (TypeError, ValueError):
                value = {}
        intent = str(value.get("intent") or "")
        if intent not in {"chat", "control", "task"}:
            intent = "task" if any(
                marker in text for marker in ("http://", "https://", "读取", "分析", "看看", "跑", "自进化", "学习")
            ) else "chat"
        payload = value.get("task_payload")
        if not isinstance(payload, dict):
            payload = {}
        return {
            "intent": intent,
            "reply": str(value.get("reply") or "").strip(),
            "reason": str(value.get("reason") or "").strip(),
            "payload": payload,
        }

    # ---------------------------------------------------------------- actions
    def _submit_task(self, text: str, classification: dict, *, sender_id: str, msg: dict) -> str:
        """Fast-path submission with the classification contract; the
        intent tri-passes are skipped inside submit() when intent_contract
        is provided."""
        payload = dict(classification.get("payload") or {})
        # Heuristic: an explicit http(s) link with a "read / look at" intent
        # routes to content_read_reply via workstream_type=active_learning
        # (service.py selects that flow when user_urls present).
        if ("http://" in text or "https://" in text) and not payload.get("dispatch_target"):
            payload["workstream_type"] = str(payload.get("workstream_type") or "active_learning")
        contract = {
            "route": "project_iteration",
            "dispatch_target": str(payload.get("dispatch_target") or "project_iteration"),
            "goal": list(payload.get("goal") or [text[:500]]),
            "constraints": list(payload.get("constraints") or []),
            "success_criteria": list(payload.get("success_criteria") or []),
            "workstream_type": str(payload.get("workstream_type") or "project_research"),
            "warm_reply": str(payload.get("warm_reply") or "收到，正在处理。"),
            "report_policy": str(payload.get("report_policy") or "milestone"),
            "original_request": text,
        }
        try:
            from partner.application.service import PartnerApplicationService
            service = PartnerApplicationService(self.root)
            submission = service.submit(
                text=text,
                channel="qq",
                sender_id=sender_id,
                sender_name=str(msg.get("sender_name") or "用户"),
                persona_hint=self.instance_id,
                attachments=[a.get("path") or a.get("url") for a in (msg.get("attachments") or []) if (a.get("path") or a.get("url"))],
                intent_contract=contract,
            )
            if getattr(submission, "accepted", False):
                return str(submission.message or contract["warm_reply"])
            return str(getattr(submission, "message", "") or contract["warm_reply"])
        except Exception as exc:  # noqa: BLE001
            return f"收到，但任务提交失败：{type(exc).__name__}: {exc}"[:300]

    def _send(self, sender_id: str, content: str) -> bool:
        if not content:
            return False
        if self.reply_sender is not None:
            try:
                return bool(self.reply_sender(sender_id, content))
            except Exception:  # noqa: BLE001
                return False
        # Fallback: enqueue outbound text so the bridge poller delivers it.
        try:
            from partner.application.service import PartnerApplicationService
            service = PartnerApplicationService(self.root)
            service._enqueue_outbound_text(
                job_id=f"msg-{int(time.time() * 1000)}",
                sender_id=sender_id, content=content,
                persona_hint=self.instance_id, project_id="",
            )
            return True
        except Exception:  # noqa: BLE001
            return False


__all__ = ["MessageAgent", "CLASSIFY_SYSTEM"]
