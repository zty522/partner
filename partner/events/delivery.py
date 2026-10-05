"""Channel delivery Events.  A local queue write is never an acknowledgment."""
from __future__ import annotations

from typing import Any
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
from partner.event_fabric.catalog import EventDefinition


def _outbound_name(ctx, params):
    identity = str(getattr(ctx, 'job_id', 'job'))
    notification_id = str(params.get('notification_id') or '').strip()
    if notification_id:
        identity += '.notification.' + ''.join(
            c for c in notification_id if c.isalnum() or c in '_-')[:40]
    elif params.get('flow_id') and params.get('node_id'):
        # A Flow can send several text/PDF milestones even when self-evolution
        # is disabled.  Each Event needs an immutable receipt: reusing
        # ``<job>.json`` overwrote the earlier ACK and let a later delivery
        # appear to have no history.
        identity += '.' + str(params.get('flow_id') or 'flow') + '.' + str(params.get('node_id') or 'send')
    return identity + '.json'


def _delivery_channels(ctx: Any, params: dict[str, Any]) -> list[str]:
    constraints = ((params.get("intent_contract") or {}).get(
        "execution_constraints") or {})
    declared = constraints.get("delivery_channels")
    if isinstance(declared, list):
        channels = [str(value) for value in declared
                    if str(value) in {"qq", "web", "local", "log", "file"}]
        if channels:
            return list(dict.fromkeys(channels))
    return [str(params.get("channel") or getattr(ctx, "channel", "local"))]


def _web_receipt(ctx: Any, *, projection: str) -> dict[str, Any]:
    return {"channel": "web", "projection": projection,
            "job_id": str(getattr(ctx, "job_id", "")), "visible": True}


def _qq_recipient(ctx: Any, params: dict[str, Any]) -> str:
    """Return the already verified QQ recipient carried by the Job context.

    Dual-channel Web submissions resolve ``recipient_ref`` at the application
    boundary and replace the synthetic Web sender with that verified OpenID.
    Delivery must never guess from last-seen chat state.
    """
    channel = str(params.get("channel") or getattr(ctx, "channel", ""))
    sender = str(params.get("sender_id") or getattr(ctx, "sender_id", "")).strip()
    # ``channel`` was absent on older internal Event calls; those calls are
    # still safe because the QQ destination is the explicit ``sender_id``.
    # A Web request, however, carries a synthetic browser identity and must
    # never be treated as an OpenID.
    if channel not in {"", "qq", "both"} or not sender:
        return ""
    # The application boundary normally verifies the recipient.  Delivery is
    # nevertheless a security/reliability boundary of its own: a historical
    # v4 run queued a synthetic label (``codex-v4-final``), which could only be
    # rejected later by QQ.  When this is a real Partner workspace, require the
    # recipient to occur in this instance's authenticated C2C allow-list.
    workspace = Path(str(getattr(ctx, "workspace", "") or ""))
    instance_id = str(params.get("origin_instance") or
                      getattr(ctx, "instance_id", "") or "")
    binding_path = workspace / "instances" / instance_id / "state" / "record" / "bot_id.json"
    production_workspace = (workspace / ".partner_workspace_identity").is_file()
    if production_workspace or binding_path.is_file():
        try:
            binding = json.loads(binding_path.read_text(encoding="utf-8"))
            allowed = {str(value).strip() for value in
                       binding.get("allowed_user_openids") or [] if str(value).strip()}
        except (OSError, TypeError, ValueError):
            return ""
        # QQ callbacks carry the bare OpenID.  Web submissions may carry the
        # canonical instance-scoped form after application-level verification.
        candidate = sender
        prefix = f"inst{instance_id}_"
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
        if candidate not in allowed:
            return ""
        return candidate
    # Unit-sized/local contexts without a Partner workspace keep the explicit
    # sender contract; they do not claim production QQ delivery.
    return sender


def channel_route(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    channel = str(params.get("channel") or getattr(_ctx, "channel", "") or "local")
    if channel not in {"qq", "web", "both", "gui", "tui", "local", "log", "file"}:
        return {"ok": False, "status": "failed", "error": "unsupported delivery channel"}
    return {"ok": True, "status": "completed", "semantic_output": {"channel": channel},
            "summary": f"交付路由：{channel}"}


def _job_status_line(ctx: Any, params: dict[str, Any], flow_outputs: dict[str, Any]) -> str:
    """Build a deterministic one-line status header so the user can see
    *what is happening* at a glance even on the first "job accepted" ack.

    Pulls ``job_view_snapshot`` from params if the upstream notification
    Event already attached one; otherwise walks ``ctx`` (instance_id,
    job_id, workspace) and the live ``outbound/<inst>/<job>.json`` file so
    the first acknowledgement always carries enough context.
    """
    snapshot = params.get("job_view_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    instance_id = str(snapshot.get("instance_id") or getattr(ctx, "instance_id", "") or "")
    job_id = str(snapshot.get("job_id") or getattr(ctx, "job_id", "") or "")
    project_id = str(snapshot.get("project_id") or "")
    queue_position = snapshot.get("queue_position")
    queue_total = snapshot.get("queue_total")
    status = str(snapshot.get("status") or "queued")
    current_activity = str(snapshot.get("current_activity") or "")
    next_action = str(snapshot.get("next_committed_action") or "")

    if not project_id or not instance_id:
        workspace = str(getattr(ctx, "workspace", "") or "")
        origin = str(params.get("origin_instance") or instance_id or "")
        if workspace and origin and job_id:
            try:
                job_path = (Path(workspace).resolve() / "state" / "application"
                            / "outbound" / origin / f"{job_id}.json")
                if job_path.is_file():
                    payload = json.loads(job_path.read_text(encoding="utf-8"))
                    project_id = project_id or str(payload.get("project_id") or "")
                    if not status or status == "queued":
                        status = str(payload.get("delivery_state") or status or "queued")
            except Exception:
                pass
        if not project_id:
            job_record = params.get("job_record")
            if isinstance(job_record, dict):
                project_id = project_id or str(job_record.get("project_id") or "")

    if not instance_id and not project_id and not job_id:
        return ""
    parts = []
    if instance_id:
        parts.append(f"实例={instance_id}")
    if project_id:
        parts.append(f"项目={project_id}")
    if queue_position is not None and queue_total is not None:
        parts.append(f"队列={queue_position}/{queue_total}")
    elif status:
        parts.append(f"状态={status}")
    if job_id:
        parts.append(f"Job={job_id}")
    header = "[" + " | ".join(parts) + "]"
    extras = []
    if current_activity:
        extras.append(f"正在做：{current_activity}")
    if next_action:
        extras.append(f"下一步：{next_action}")
    if extras:
        header = header + "\n" + "\n".join(extras)
    return header


def send_text(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    if (((params.get('intent_contract') or {}).get('execution_constraints') or {})
            .get('suppress_user_delivery') is True):
        return {'ok': True, 'status': 'completed', 'suppressed': True,
                'delivered': False, 'summary': '父协调 Flow 统一负责最终用户消息'}
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    flow_outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    composed = next((value for key, value in reversed(list(flow_outputs.items()))
                     if key in {"final_critic", "message_critic", "critic",
                                "final_compose", "compose", "narrative"}
                     and isinstance(value, dict)), {})
    dedup = next((value for key, value in reversed(list(flow_outputs.items()))
                  if key in {"final_deduplicate", "deduplicate", "dedup"}
                  and isinstance(value, dict)), {})
    raw = str(params.get("message") or params.get("text") or prior.get("message")
              or composed.get("message") or composed.get("model_output")
              or prior.get("model_output") or prior.get("summary") or "").strip()
    # The commitment settlement outranks the draft: when the reconcile Event produced a
    # settlement-derived body, that body IS the message.  A draft that contradicted the
    # settlement was already dropped there (and the contradiction recorded).
    def _settlement_message(value):
        """The reconcile Event's body, from either shape the flow can hand us.

        The controller stores a node's whole handler result under its node id, so the
        body sits inside ``semantic_output``; a flattened dict is accepted too.  Reading
        only the top level silently missed it and sent the raw draft.
        """
        if not isinstance(value, dict):
            return ""
        direct = str(value.get("settlement_message") or "").strip()
        if direct:
            return direct
        nested = value.get("semantic_output")
        if isinstance(nested, dict) and nested.get("settlement_present"):
            return str(nested.get("settlement_message") or "").strip()
        return ""

    reconcile = next((v for v in reversed(list(flow_outputs.values()))
                      if isinstance(v, dict) and _settlement_message(v)), {})
    settlement_body = _settlement_message(reconcile)
    if settlement_body:
        # The reconcile body is already complete: it carries the verdict, and the draft
        # only when the draft did not contradict it.  Appending the draft again would
        # duplicate it and break the body digest the reconcile Event recorded.
        raw = settlement_body
        prepend = False
    status_line = _job_status_line(ctx, params, flow_outputs)
    prepend = bool(params.get("prepend_status", False))
    if status_line and prepend and raw:
        text = status_line + "\n\n" + raw
    else:
        text = raw
    channel = str(params.get("channel") or getattr(ctx, "channel", "local"))
    channels = _delivery_channels(ctx, params)
    decision=next((v for v in flow_outputs.values() if isinstance(v,dict) and 'notify' in v),{})
    if decision.get('notify') is False:
        return {'ok':True,'status':'completed','suppressed':True,'delivered':False,'summary':'通知决策要求仅保存本地记录'}
    if (((params.get('intent_contract') or {}).get('execution_constraints') or {}).get('evolution_cycle')
            and not composed.get('ok') and not bool(params.get('message_reviewed'))):
        return {'ok':False,'status':'failed','error':'cycle message did not pass review; no transport request made'}
    if not text:
        return {"ok": False, "status": "failed", "error": "empty user message"}
    if dedup and not bool(dedup.get("should_send", True)):
        return {"ok": True, "status": "completed", "delivered": False,
                "suppressed": True, "message": text, "delivery_kind": "text",
                "summary": "重复消息已抑制，未调用渠道"}
    if channel == "web" and "qq" not in channels:
        # The Web console is a pull channel.  The durable Event/run-trace
        # projection is its channel receipt; do not enqueue a fake QQ message
        # for the browser's local operator identity.
        return {"ok": True, "status": "completed", "delivered": False,
                "web_visible": True,
                "receipt": _web_receipt(ctx, projection="run_trace"),
                "message": text, "delivery_kind": "text",
                "summary": "消息已写入 Web 运行详情"}
    if channel in {"gui", "tui", "local"}:
        return {"ok": True, "status": "completed", "delivered": True,
                "receipt": {"channel": channel, "projection": "event_ledger"},
                "message": text, "delivery_kind": "text"}
    if channel in {"log", "file"}:
        # A log-file channel: delivery IS the durable append.  The receipt carries the
        # exact byte offset the line starts at and the line's digest, so the claim is
        # checkable against the file instead of being taken on trust.
        root = Path(str(getattr(ctx, "workspace", ""))).resolve()
        origin = str(params.get("origin_instance") or getattr(ctx, "instance_id", "") or "shared")
        log_path = root / "state" / "outbound" / "replies.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "ts": datetime.now(timezone.utc).astimezone().isoformat(),
            "channel": channel, "instance": origin,
            "job_id": str(getattr(ctx, "job_id", "")),
            "project_id": str(getattr(ctx, "project_id", "")),
            "flow_id": str(params.get("flow_id") or ""),
            "node_id": str(params.get("node_id") or ""),
            "content": text,
        }, ensure_ascii=False)
        encoded = (line + "\n").encode("utf-8")
        offset = log_path.stat().st_size if log_path.exists() else 0
        with log_path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return {"ok": True, "status": "completed", "delivered": True,
                "receipt": {"channel": channel, "path": str(log_path), "offset": offset,
                            "bytes": len(encoded),
                            "sha256": hashlib.sha256(encoded).hexdigest()},
                "message": text, "delivery_kind": "text"}
    qq_recipient = _qq_recipient(ctx, params)
    if not qq_recipient:
        return {'ok':False,'status':'failed','error':'QQ recipient identity is missing; no transport request was made'}
    root = Path(str(getattr(ctx, "workspace", ""))).resolve()
    origin = str(params.get("origin_instance") or getattr(ctx, "instance_id", ""))
    target = root / "state/application/outbound" / origin / _outbound_name(ctx, params)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 2, "job_id": getattr(ctx, "job_id", ""),
               "to_user": qq_recipient,
               "content": text, "notification_identity":dedup.get("notification_identity",{}), "pdf_artifacts": [], "text_delivered": False,
               "delivery_state": "queued", "created_at": datetime.now(timezone.utc).isoformat()}
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    web_receipt = (_web_receipt(ctx, projection="run_trace")
                   if "web" in channels else None)
    return {"ok": True, "status": "completed", "delivered": False,
            "queued": True, "receipt": {"channel": "qq", "path": str(target)},
            "web_receipt": web_receipt,
            "channel_receipts": ([web_receipt] if web_receipt else [])
                                + [{"channel": "qq", "path": str(target),
                                    "delivery_state": "queued"}],
            "message": text, "delivery_kind": "text"}


def send_pdf(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    if (((params.get('intent_contract') or {}).get('execution_constraints') or {})
            .get('suppress_user_delivery') is True):
        return {'ok': True, 'status': 'completed', 'suppressed': True,
                'delivered': False, 'summary': '父协调 Flow 统一负责最终 PDF 交付'}
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    upstream = params.get("upstream") if isinstance(params.get("upstream"), dict) else {}
    # PDF_REPORT 链：send 的直接 previous 可能是 quality（无 path），
    # 要从上游 render 节点拿 PDF 路径。
    render = upstream.get("render") or (params.get("flow_outputs") or {}).get("render") or {}
    if not isinstance(render, dict):
        render = {}
    outputs = params.get('flow_outputs') or {}
    draft = upstream.get("claims") or upstream.get("draft") or outputs.get('claims') or outputs.get('draft') or {}
    if not isinstance(draft, dict):
        draft = {}
    # 用 draft 第一段标题 + "报告请查收 PDF" 作为发送文本（QQ bridge 的
    # send_proactive 会因空 content 返回 False，导致持续 backoff）。
    draft_path = str(draft.get("path") or "")
    lead_text = ""
    if draft_path and Path(draft_path).is_file():
        try:
            head = Path(draft_path).read_text(encoding="utf-8", errors="replace").splitlines()
            for line in head[:6]:
                line = line.strip().lstrip("#").strip()
                if line and len(line) >= 4:
                    lead_text = line + "\n\n"
                    break
        except Exception:
            pass
    source = str(params.get("pdf_path") or params.get("source")
                 or prior.get("path") or render.get("path") or render.get("pdf_path") or "")
    if not source.lower().endswith(".pdf"):
        return {"ok": False, "status": "failed", "error": "user report delivery accepts PDF only"}
    path = Path(source)
    if not path.is_file():
        return {"ok": False, "status": "failed", "error": f"PDF does not exist: {source}"}
    # Freeze the exact report version at the delivery boundary.  A pathname is
    # mutable; the digest lets the QQ bridge reject a file that changed while
    # queued and lets settlement prove that Web and QQ refer to the same PDF.
    pdf_bytes = path.read_bytes()
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    pdf_size = len(pdf_bytes)
    # 把 PDF 路径写到 outbound payload（QQ bridge 轮询时会一起发）
    root = Path(str(getattr(ctx, "workspace", ""))).resolve()
    origin = str(params.get("origin_instance") or getattr(ctx, "instance_id", ""))
    target = root / "state/application/outbound" / origin / _outbound_name(ctx, params)
    target.parent.mkdir(parents=True, exist_ok=True)
    text_msg = (lead_text + "【PDF 报告已生成】请查收附件。").strip()
    if (params.get('intent_contract') or {}).get('supersedes_report'):
        text_msg = (lead_text + "这是更正版报告，修正了上一版的表述与引用问题，请以本附件为准。").strip()
    if (params.get('intent_contract') or {}).get('reissue_source'):
        text_msg = (lead_text + "报告已重新排版并通过检查，请查收附件；原始计算数据未变。").strip()
    reviewed=outputs.get('message_critic') or {}
    if reviewed:
        if not reviewed.get('ok') or not reviewed.get('message'): return {'ok':False,'status':'failed','error':'report message review failed'}
        text_msg=reviewed['message']
    summaries=(outputs.get('summaries') or {}).get('semantic_output') or {}
    summary_message=str(summaries.get('delivery_message') or '').strip()
    if summary_message and not ('结果总结' in text_msg and '运行总结' in text_msg):
        # The deterministic summary Event is the factual source of truth.  A
        # prose critic may shorten it, but may not remove either user-required
        # section from the delivered message.
        text_msg=summary_message
    assets=(outputs.get('visuals',{}).get('semantic_output') or {}).get('images',[])
    embedded=render.get('embedded_figure_ids') or []
    if embedded: assets=sorted(assets,key=lambda a:embedded.index(a['id']) if a['id'] in embedded else len(embedded))
    channel=str(params.get('channel') or getattr(ctx,'channel','qq'))
    channels = _delivery_channels(ctx, params)
    if channel == 'web' and 'qq' not in channels:
        return {'ok':True,'status':'completed','delivered':False,'web_visible':True,
                'pdf_path':str(path),'message':text_msg,'files':[str(path)],
                'receipt':_web_receipt(ctx, projection='artifact_index')}
    if channel in {'local','gui','tui'}:
        return {'ok':True,'status':'completed','delivered':True,'pdf_path':str(path),'message':text_msg,
                'files':[str(path)],'receipt':{'channel':channel,'projection':'event_ledger'}}
    qq_recipient = _qq_recipient(ctx, params)
    if not qq_recipient:
        return {'ok':False,'status':'failed','error':'QQ recipient identity is missing; no transport request was made'}
    payload = {"schema_version": 4, "job_id": getattr(ctx, "job_id", ""),
               "to_user": qq_recipient,
               "content": text_msg, "image_artifacts":[{'path':a['path'],'sha256':a['sha256'],'id':a['id']} for a in assets[:1]],
               "figure_manifest":(outputs.get('visuals') or {}).get('manifest_path'), "pdf_artifacts": [str(path)],
               "pdf_sha256": pdf_sha256, "pdf_size_bytes": pdf_size,
               "text_delivered": False, "delivery_state": "pdf_queued",
               "created_at": datetime.now(timezone.utc).isoformat()}
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    web_receipt = (_web_receipt(ctx, projection='artifact_index')
                   if 'web' in channels else None)
    return {"ok": True, "status": "completed", "delivered": False, "queued": True,
            "files": [str(path)], "pdf_path": str(path), "delivery_kind": "pdf",
            "pdf_sha256": pdf_sha256, "pdf_size_bytes": pdf_size,
            "receipt": {"channel": "qq", "path": str(target)},
            "web_receipt": web_receipt,
            "channel_receipts": ([web_receipt] if web_receipt else [])
                                + [{"channel": "qq", "path": str(target),
                                    "delivery_state": "queued"}]}


def verify(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    sem = prior.get("semantic_output") if isinstance(prior.get("semantic_output"), dict) else {}
    # crash recovery 会把 send_text 的 queued/delivered/receipt 塞进
    # semantic_output；这里同时查两层，避免误判"渠道没接受交付"。
    def _get(key: str):
        value = prior.get(key)
        return value if value is not None else sem.get(key)
    web_visible = bool(_get('web_visible'))
    delivered = bool(_get("delivered")) or _get("status") == "sent"
    accepted = delivered or web_visible or bool(_get("queued")) or bool(_get("suppressed"))
    receipt = _get("receipt") or prior.get("results") or []
    status = "completed" if accepted else "failed"
    semantic_output = {"delivered": delivered, "web_visible":web_visible, "accepted": accepted,
                       "suppressed": bool(_get("suppressed")),
                       "receipt": receipt}
    if delivered or web_visible:
        summary = "渠道已确认交付"
    elif prior.get("suppressed"):
        summary = "重复消息已抑制，无需渠道交付"
    elif accepted:
        summary = "已进入渠道队列，等待独立 ACK Event"
    else:
        summary = "渠道没有接受交付"
    return {"ok": accepted, "status": status, "semantic_output": semantic_output,
            "summary": summary}


DEFINITIONS = [
    EventDefinition("delivery.channel_route", "delivery", "将消息路由到指定渠道", channel_route),
    EventDefinition("delivery.send_text", "delivery", "将文本投递到本地队列或渠道", send_text),
    EventDefinition("delivery.send_pdf", "delivery", "投递 PDF 报告到本地队列或渠道", send_pdf),
    EventDefinition("delivery.verify", "delivery", "校验上一步交付结果是否被渠道接受", verify),
]
