"""Side-band progress reporter.

Runs after each completed node in a round-style flow. Translates the node's
output into a short 1-2 sentence Chinese progress message and enqueues it
to the same outbound queue that message_critic -> send uses. Does NOT
go through message_critic (intentional: progress is non-critical and a
critic failure must not block the round).

The LLM translation is OPTIONAL: if it fails or times out within 5s, we
fall back to a hardcoded summary so the round never blocks on LLM hang
(LLM provider quota exhaustion should not freeze business rounds).
"""
from __future__ import annotations

from typing import Any
import json

from ._llm import call_model
from partner.event_fabric.catalog import EventDefinition


_PROGRESS_PROMPT = """你是 Partner 进度汇报员。Partner刚跑完一个内部 Event，需要你把它的真凭据翻译成一句普通中文发给用户。

【硬规则】
- 只描述刚才完成的那个节点的**真实产物**（数字、文件名、状态），禁止凭未给信息推断
- 严格 1 句，最多 80 字；不要换行、不要标题、不要列表
- 不写「我刚才」「我即将」「接下来会」这类工作流表述，只说事实
- 不复述所有文件名（除非特别短）
- 不预测下一步
- 不提及内部编号 / 哈希（除非该哈希是该节点最关键的产物且不超过 16 字）
- 如实记录失败/缺口，禁用用失败等掩盖

【节点】{node_id}（{flow_type} flow）
【事件类型】{event_type}
【摘要】{summary}
【可读字段】{fields}

只输出 JSON：{{"progress_text": "..."}}。禁止其它字段。
"""


def _extract_readable_fields(value):
    if not isinstance(value, dict):
        return {}
    out = {}
    for k, v in list(value.items())[:8]:
        if isinstance(v, (str, int, float, bool)):
            s = str(v)
            if len(s) <= 200 and not k.startswith('_'):
                out[k] = s
        elif isinstance(v, list):
            out[k + '_count'] = len(v)
        elif isinstance(v, dict):
            sub = _extract_readable_fields(v)
            if sub:
                out[k] = sub
    return out


def _try_llm_translation(ctx, params, fields_event, summary, fields):
    """Try one LLM call with hard 5s timeout via signal.alarm-compatible thread.
    Returns progress_text or "" on failure."""
    import threading
    result = [None]
    done = threading.Event()

    def _call():
        try:
            raw, usage = call_model(
                ctx, purpose='progress_report',
                prompt=_PROGRESS_PROMPT.format(
                    node_id=params.get('completed_node_id') or '',
                    flow_type=params.get('flow_type') or 'unknown',
                    event_type=fields_event or 'unknown',
                    summary=summary or '(无摘要)',
                    fields=json.dumps(fields, ensure_ascii=False)[:1200] or '(无字段)',
                ),
            )
            from ._llm import json_object
            parsed = json_object(raw) if raw and raw.strip().startswith('{') else {}
            result[0] = str(parsed.get('progress_text') or '').strip()
        except Exception:
            pass
        finally:
            done.set()

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    done.wait(timeout=5)
    return result[0] if result[0] else ''


def emit_progress(ctx, params):
    node_id = str(params.get('completed_node_id') or '')
    node_output = params.get('node_output') or {}
    flow_id = str(params.get('flow_id') or '')
    task_id = str(params.get('task_id') or '')
    instance_id = str(params.get('instance_id') or '')
    flow_type = str(params.get('flow_type') or '')

    summary = str(node_output.get('summary', '') or '')[:500]
    sem = node_output.get('semantic_output') or {}
    fields = _extract_readable_fields(sem)
    for alias in ('supported', 'rejected', 'unknown', 'candidates', 'hypotheses'):
        if alias in sem and isinstance(sem[alias], list):
            fields[alias + '_count'] = len(sem[alias])
    fields_event = str(node_output.get('event_type', '') or '')

    # 1. 优先尝试 LLM 翻译 (5s 限时, daemon thread, 不阻塞主线程)
    text = _try_llm_translation(ctx, params, fields_event, summary, fields)
    # 2. fallback: hardcoded summary
    if not text:
        text = f"已完成 {node_id}" + (f": {summary[:80]}" if summary else '')

    # 3. 写 outbound
    written_to = ''
    try:
        from partner.application.service import PartnerApplicationService
        workspace = str(getattr(ctx, 'workspace', '') or '')
        if workspace:
            svc = PartnerApplicationService(workspace)
            svc._enqueue_outbound_text(
                job_id=task_id or 'unknown',
                sender_id=str(getattr(ctx, 'sender_id', '') or 'progress'),
                project_id=str(getattr(ctx, 'project_id', '') or ''),
                content=text,
                persona_hint=instance_id or "progress",
            )
            written_to = workspace
    except Exception as exc:
        return {
            'ok': False, 'status': 'completed',
            'error': f'progress enqueue failed: {exc.__class__.__name__}: {str(exc)[:120]}',
            'summary': '进度消息投递失败，已降级',
            'progress_text': text,
        }

    return {
        'ok': True, 'status': 'completed',
        'progress_text': text,
        'outbound_written_to': written_to,
        'source_event': node_id,
        'summary': text[:120],
    }


DEFINITIONS = [EventDefinition(
    'notification.emit_progress', 'notification',
    '将刚跑完节点的输出翻译成 1-2 句进度消息并写入 outbound 队列',
    emit_progress,
    execution_method='llm',
)]
