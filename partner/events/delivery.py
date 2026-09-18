"""Channel delivery Events.  A local queue write is never an acknowledgment."""
from __future__ import annotations

from typing import Any
from pathlib import Path
from datetime import datetime, timezone
import json
import os
from partner.event_fabric.catalog import EventDefinition


def _outbound_name(ctx, params):
    identity = str(getattr(ctx, 'job_id', 'job'))
    if ((params.get('intent_contract') or {}).get('execution_constraints') or {}).get('evolution_cycle'):
        # A cycle sends text and PDF under one Job. Each Flow/Event needs its
        # own receipt: a previous .sent file must never acknowledge new text.
        identity += '.' + str(params.get('flow_id') or 'flow') + '.' + str(params.get('node_id') or 'send')
    return identity + '.json'


def channel_route(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    channel = str(params.get("channel") or getattr(_ctx, "channel", "") or "local")
    if channel not in {"qq", "gui", "tui", "local"}:
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
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    flow_outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    composed = next((value for key, value in reversed(list(flow_outputs.items()))
                     if key in {"message_critic", "critic", "compose"} and isinstance(value, dict)), {})
    dedup = next((value for key, value in reversed(list(flow_outputs.items()))
                  if key in {"deduplicate", "dedup"} and isinstance(value, dict)), {})
    raw = str(params.get("message") or params.get("text") or prior.get("message")
              or composed.get("message") or composed.get("model_output")
              or prior.get("model_output") or prior.get("summary") or "").strip()
    status_line = _job_status_line(ctx, params, flow_outputs)
    prepend = bool(params.get("prepend_status", False))
    if status_line and prepend and raw:
        text = status_line + "\n\n" + raw
    else:
        text = raw
    channel = str(params.get("channel") or getattr(ctx, "channel", "local"))
    decision=next((v for v in flow_outputs.values() if isinstance(v,dict) and 'notify' in v),{})
    if decision.get('notify') is False:
        return {'ok':True,'status':'completed','suppressed':True,'delivered':False,'summary':'通知决策要求仅保存本地记录'}
    if ((params.get('intent_contract') or {}).get('execution_constraints') or {}).get('evolution_cycle') and not composed.get('ok'):
        return {'ok':False,'status':'failed','error':'cycle message did not pass review; no transport request made'}
    if not text:
        return {"ok": False, "status": "failed", "error": "empty user message"}
    if dedup and not bool(dedup.get("should_send", True)):
        return {"ok": True, "status": "completed", "delivered": False,
                "suppressed": True, "message": text, "delivery_kind": "text",
                "summary": "重复消息已抑制，未调用渠道"}
    if channel in {"gui", "tui", "local"}:
        return {"ok": True, "status": "completed", "delivered": True,
                "receipt": {"channel": channel, "projection": "event_ledger"},
                "message": text, "delivery_kind": "text"}
    if not str(params.get('sender_id') or getattr(ctx,'sender_id','')).strip():
        return {'ok':False,'status':'failed','error':'QQ recipient identity is missing; no transport request was made'}
    root = Path(str(getattr(ctx, "workspace", ""))).resolve()
    origin = str(params.get("origin_instance") or getattr(ctx, "instance_id", ""))
    target = root / "state/application/outbound" / origin / _outbound_name(ctx, params)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 2, "job_id": getattr(ctx, "job_id", ""),
               "to_user": str(params.get("sender_id") or getattr(ctx, "sender_id", "")),
               "content": text, "notification_identity":dedup.get("notification_identity",{}), "pdf_artifacts": [], "text_delivered": False,
               "delivery_state": "queued", "created_at": datetime.now(timezone.utc).isoformat()}
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return {"ok": True, "status": "completed", "delivered": False,
            "queued": True, "receipt": {"channel": "qq", "path": str(target)},
            "message": text, "delivery_kind": "text"}


def send_pdf(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
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
    assets=(outputs.get('visuals',{}).get('semantic_output') or {}).get('images',[])
    embedded=render.get('embedded_figure_ids') or []
    if embedded: assets=sorted(assets,key=lambda a:embedded.index(a['id']) if a['id'] in embedded else len(embedded))
    channel=str(params.get('channel') or getattr(ctx,'channel','qq'))
    if channel in {'local','gui','tui'}:
        return {'ok':True,'status':'completed','delivered':True,'pdf_path':str(path),'message':text_msg,
                'files':[str(path)],'receipt':{'channel':channel,'projection':'event_ledger'}}
    if not str(params.get('sender_id') or getattr(ctx,'sender_id','')).strip():
        return {'ok':False,'status':'failed','error':'QQ recipient identity is missing; no transport request was made'}
    payload = {"schema_version": 3, "job_id": getattr(ctx, "job_id", ""),
               "to_user": str(params.get("sender_id") or getattr(ctx, "sender_id", "")),
               "content": text_msg, "image_artifacts":[{'path':a['path'],'sha256':a['sha256'],'id':a['id']} for a in assets[:1]],
               "figure_manifest":(outputs.get('visuals') or {}).get('manifest_path'), "pdf_artifacts": [str(path)],
               "text_delivered": False, "delivery_state": "pdf_queued",
               "created_at": datetime.now(timezone.utc).isoformat()}
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return {"ok": True, "status": "completed", "delivered": False, "queued": True,
            "files": [str(path)], "pdf_path": str(path), "delivery_kind": "pdf",
            "receipt": {"channel": "qq", "path": str(target)}}


def verify(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    sem = prior.get("semantic_output") if isinstance(prior.get("semantic_output"), dict) else {}
    # crash recovery 会把 send_text 的 queued/delivered/receipt 塞进
    # semantic_output；这里同时查两层，避免误判"渠道没接受交付"。
    def _get(key: str):
        value = prior.get(key)
        return value if value is not None else sem.get(key)
    delivered = bool(_get("delivered")) or _get("status") == "sent"
    accepted = delivered or bool(_get("queued")) or bool(_get("suppressed"))
    receipt = _get("receipt") or prior.get("results") or []
    status = "completed" if accepted else "failed"
    semantic_output = {"delivered": delivered, "accepted": accepted,
                       "suppressed": bool(_get("suppressed")),
                       "receipt": receipt}
    if delivered:
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
