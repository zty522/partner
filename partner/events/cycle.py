"""Semantic cycle Events; orchestration belongs to cycle_children/FlowController."""
from pathlib import Path
import hashlib
import json
import time
from partner.event_fabric.catalog import EventDefinition
from partner.runtime.action_execution import write_json
from ._llm import call_model, json_object


def folder(ctx):
    return Path(ctx.workspace) / 'state/cycles' / ctx.job_id


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def records(ctx):
    return {name: read(folder(ctx) / (name + '.json'))
            for name in ('round_one', 'round_two', 'report')}


def evidence(ctx):
    refs = []
    for name, value in records(ctx).items():
        if value:
            refs.append(str(folder(ctx) / (name + '.json')))
            refs.extend(value.get('files') or [])
            for output in (value.get('node_outputs') or {}).values():
                for key in ('path', 'pdf_path'):
                    p = output.get(key)
                    if p and str(p).startswith(str(Path(ctx.workspace) / 'state/event_runtime/work' / ctx.job_id)):
                        refs.append(str(p))
            # A timed-out action can still have real partial outputs. Discover
            # only this owned child's work directory; never infer validation.
            work = Path(ctx.workspace) / 'state/event_runtime/work' / ctx.job_id / str(value.get('flow_id') or '')
            if value.get('flow_id') and work.is_dir():
                from partner.runtime.artifact_checks import owned_files
                refs.extend(str(p) for p in owned_files(work) if p.is_file()
                            and '.execution' not in p.parts and '__pycache__' not in p.parts
                            and p.suffix in ('.py', '.json', '.jsonl', '.md', '.csv')
                            and p.stat().st_size < 250000)
    return list(dict.fromkeys(p for p in refs if Path(p).is_file()))


def enrich(ctx, params, outputs):
    refs = evidence(ctx)
    contract = {**params.get('intent_contract', {}), 'evidence_refs': refs,
                'notification_kind': 'milestone', 'force': True}
    recovery = read(folder(ctx) / 'supervisor_recovery.json')
    request = params.get('request', '')
    if recovery:
        request += ('\n监督恢复说明：之前的评估输入截断遗漏了第二轮，已保留原失败证据。'
                    '现在根据真实两轮快照重新评估与交付；若先前已经发过错误结论，请明确更正。'
                    '不重跑业务轮；自进化仍由后续独立Flow执行。')
    return {**params, 'request': request, 'intent_contract': contract, 'evidence_refs': refs,
            'notification_kind': 'milestone', 'force': True}


def result(value, summary, files=()):
    return {'ok': True, 'status': 'completed', 'semantic_output': value,
            'summary': summary, 'files': list(files), 'evidence_refs': list(files)}


def round_request(ctx, params):
    number = params['round_number']
    prior = read(folder(ctx) / 'round_one.json') if number == 2 else {}
    original = (params.get('intent_contract') or {}).get('original_request') or params['request']
    request = original + f'\n这是本周期第 {number}/2 轮。'
    if prior:
        last = prior.get('node_outputs') or {}
        request += ('必须先读取第一轮真实产物，依据结果选择补缺、独立复核或新的实验；不要重复原动作。'
                    '第一轮完成目标时第二轮验证边界，不扩大用户目标。\n第一轮证据文件：'
                    + str(folder(ctx) / 'round_one.json')
                    + '\n第一轮分析：' + json.dumps(last.get('reflect') or {}, ensure_ascii=False)[:7000]
                    + '\n第一轮产物：' + json.dumps(prior.get('files') or [], ensure_ascii=False)[:5000])
    else:
        request += '先完成一个能产生真实结果的有界动作，把剩余问题留给第二轮；不要只探查工具或写方案。'
    request += ('\n本子Flow只承担这一轮业务实验。两轮后的阶段消息、PDF交付、经验/成长/习惯更新和自进化实验'
                '由父Flow中的独立Event执行；业务动作不可合并、替代或自行再次触发这些阶段。'
                '研究框架实现时必须import并实际调用被测函数；手写等价副本只能作为假设，不能证明生产实现行为。')
    contract = {**params.get('intent_contract', {}), 'round_number': number,
                'original_request': original, 'evidence_refs': prior.get('files', [])}
    return {**result({'round_number': number}, f'启动项目第{number}轮'),
            'cycle_child': {'flow': 'project_cycle_round', 'owner_node': params['node_id'],
                            'context': {'request': request, 'intent_contract': contract,
                                        'evidence_refs': prior.get('files', [])}}}


def assess(ctx, params):
    data = records(ctx)
    selected = {}
    for name in ('round_one', 'round_two'):
        row = data[name]
        selected[name] = {'flow_id': row.get('flow_id'), 'status': row.get('status'),
            'outputs': {k: {a: b for a, b in v.items() if a in
                         ('ok','status','summary','error','files','evidence_refs','business_delta')}
                       for k, v in (row.get('node_outputs') or {}).items()
                        if k in ('plan', 'execute', 'verify', 'reflect')}}
    artifacts = []
    for raw_path in evidence(ctx):
        p = Path(raw_path)
        if '/state/event_runtime/work/' in raw_path and p.suffix in ('.json', '.py', '.md', '.csv'):
            artifacts.append({'path':raw_path, 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                              'excerpt':p.read_text(errors='replace')[:3500],
                              'preview_only':p.stat().st_size > 3500})
    snapshot = {'rounds': selected, 'artifacts': artifacts[:24],
                'phase': 'two business rounds ended; delivery, memory and evolution are subsequent Events',
                'evidence_rule': 'source excerpts are previews, not proof that later fields/artifacts are absent; copied implementation is not a call to the live function'}
    write_json(folder(ctx) / 'business_snapshot.json', snapshot)
    raw, usage = call_model(ctx, purpose='cycle_assess', prompt=(
        '综合这两轮真实执行，逐项核对原始目标。第二轮是否消费第一轮产物，是否真实推进，'
        '有什么失败、重复、未知？不得把产生文件等同于项目达成。'
        '本Event只评估两轮业务，消息/PDF/记忆/自进化尚在后续，不把未到达阶段误报为失败。'
        '以实际脚本参数为准，不重复摘要中未经核验的线程数等数值。区分实际import调用和手写副本。'
        '动作失败仍可能留下部分实测文件；不要把文本预览截断误认成原始计划或产物被截断。'
        '输出JSON：summary, round_comparison, goal_coverage, verified_progress, failures, remaining, next_question。\n'
        + '用户原始目标=' + params['request'] + '\n两轮真实快照=' + json.dumps(snapshot, ensure_ascii=False)[:65000]))
    value = json_object(raw)
    path = folder(ctx) / 'assessment.json'
    write_json(path, value)
    return {**result(value, str(value.get('summary') or '两轮结果已分析'), [str(path)]),
            'token_usage': usage, 'notification_kind': 'milestone'}


def report_request(ctx, params):
    # Avoid recursively embedding recall/planning histories ahead of results.
    refs = [str(folder(ctx) / name) for name in ('business_snapshot.json', 'assessment.json')]
    refs += [p for p in evidence(ctx) if '/state/event_runtime/work/' in p
             and Path(p).name not in ('state.json', '执行结果.md', 'verified_project_artifacts.json')]
    refs = list(dict.fromkeys(p for p in refs if Path(p).is_file()))
    contract = {**params.get('intent_contract', {}), 'evidence_refs': refs,
                'force': True, 'report_policy': 'final', 'expand_evidence_refs': False}
    return {**result({}, '根据两轮真实结果生成阶段报告'),
            'cycle_child': {'flow': 'pdf_report', 'owner_node': params['node_id'],
                'context': {'intent_contract': contract, 'evidence_refs': refs,
                            'force': True, 'notification_kind': 'final',
                            'request': '根据原始目标与所附两轮真实证据生成中文阶段报告；展示真实数据与失败边界，不引入新业务结果。消息/PDF/记忆/自进化由后续Event执行，不能将尚未到达阶段判为缺失。'}}}


def delivery_settle(ctx, params):
    if params['node_id'] == 'report_ack':
        sent = (read(folder(ctx) / 'report.json').get('node_outputs') or {}).get('send', {})
    else:
        sent = (params.get('flow_outputs') or {}).get('send') or {}
    if sent.get('delivered') or sent.get('suppressed'):
        return result({'delivered': bool(sent.get('delivered')), 'suppressed': bool(sent.get('suppressed'))}, '渠道状态已核验')
    path = (sent.get('receipt') or {}).get('path')
    if not path:
        return {'ok': False, 'status': 'failed', 'error': 'missing delivery receipt; retained for self audit'}
    ack = Path(path).with_suffix('.sent')
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        row = read(ack)
        if row.get('delivery_state') == 'sent' and row.get('text_delivered'):
            if sent.get('delivery_kind') == 'pdf' and not row.get('pdf_delivered'):
                break
            receipt = {'path': str(ack), 'delivery_state': 'sent',
                       'component_acks': row.get('component_acks', []),
                       'pdf_delivered': bool(row.get('pdf_delivered'))}
            target = folder(ctx) / (params['node_id'] + '.json')
            write_json(target, receipt)
            return result(receipt, '已取得本次渠道回执', [str(target)])
        if Path(path).with_suffix('.blocked').exists():
            break
        time.sleep(1)
    return {'ok': False, 'status': 'failed', 'error': 'delivery ACK missing after bounded wait; audit must inspect this failure'}


def memory_update(ctx, params):
    from partner.memory import EventMemory
    kind = params['kind']
    path = folder(ctx) / ('memory_' + kind + '.json')
    if path.exists():
        return result(read(path), f'{kind} 复用同周期已保存更新', [str(path)])
    summary = read(folder(ctx) / 'assessment.json')
    raw, usage = call_model(ctx, purpose='cycle_memory_' + kind, prompt=(
        f'根据真实周期记录更新{kind}。lesson记录做法与结果；growth仅记录待复验的能力观察，'
        '不能凭任务完成宣称持久成长；habit提出待验证习惯，不能自动激活。'
        '输出JSON：content, scope, confidence, counterexample, evidence_refs。'
        + '\n分析=' + json.dumps(summary, ensure_ascii=False)
        + '\n交付=' + json.dumps({k: read(folder(ctx)/(k+'.json')) for k in ('text_ack','report_ack')}, ensure_ascii=False)
        + '\n证据=' + json.dumps(evidence(ctx), ensure_ascii=False)[:9000]))
    value = json_object(raw)
    if 'content' not in value:
        value = {'content':value, 'schema_normalization':'preserved model object under required content field'}
    value.update(project_id=params['project_id'], cycle_id=ctx.job_id,
                 status='candidate' if kind in ('growth','habit') else 'active',
                 production_effective=False, evidence_refs=evidence(ctx))
    if not path.exists():
        memory_path = EventMemory(ctx.workspace).append_semantic(kind, value)
        value['memory_path'] = memory_path
        write_json(path, value)
    return {**result(value, f'{kind} 已按证据更新', [str(path)]), 'token_usage': usage}


def seal(ctx, params):
    refs = evidence(ctx)
    refs += [str(p) for p in folder(ctx).glob('*.json')]
    manifest = {'cycle_id': ctx.job_id, 'instance_id': ctx.instance_id,
                'project_id': params['project_id'], 'request': params['request'],
                'root_event_id': params.get('root_event_id', ''),
                'parent_flow_id': params['flow_id'], 'created_at': time.time(),
                'files': [{'path': p, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                          for p in dict.fromkeys(refs) if Path(p).is_file()],
                'node_outputs': params.get('flow_outputs') or {}}
    path = folder(ctx) / 'sealed_cycle.json'
    write_json(path, manifest)
    return result({'manifest': str(path)}, '交付与记忆之后冻结完整周期，固定触发自进化', [str(path)])


def evolution_request(ctx, params):
    path = folder(ctx) / 'sealed_cycle.json'
    return {**result({}, '主动检查本周期所有方面并执行有界改进实验'),
            'cycle_child': {'flow': 'autonomous_evolution', 'owner_node': params['node_id'],
                'context': {'cycle_manifest': str(path), 'evidence_refs': [str(path)],
                            'intent_contract': params.get('intent_contract') or {}}}}




def final_summary(ctx, params):
    """Compose a final summary message after self-evolution completes.

    (2026-09-15) Self-evolution runs as a child flow of the project_cycle and finishes
    asynchronously. The earlier compose/message_critic/send chain only sees
    flow_outputs from before self-evolution started, so users never learn what
    the self-evolution found or decided. This event reads evolve.json's record
    and builds a brief final summary, then sends it through the existing outbound
    channel path used by delivery.send_text.
    """
    import time
    child = read(folder(ctx) / "evolve.json")
    recorded = ((child.get("node_outputs") or {}).get("record", {}).get("semantic_output") or {})
    decision = ((child.get("node_outputs") or {}).get("decision", {}).get("semantic_output") or {})
    gov_issue = None
    gov_path = folder(ctx) / "evolution" / "governance.json"
    if gov_path.is_file():
        try:
            gov = json.loads(gov_path.read_text())
            gov_issue = gov.get("issue", {}).get("issue", {})
        except Exception:
            gov_issue = {}

    decision_text = decision.get("decision") or "unknown"
    production_effective = bool(decision.get("production_effective"))
    gov_status = "已记录" if gov_issue else "未记录"
    summary_line = gov_issue.get("summary", "").strip()[:200] if gov_issue else "未发现可证伪机制问题"
    expectations = (decision.get("comparisons") or {}).get("1", {}).get("expectation_results") or []
    met = sum(1 for e in expectations if e.get("status") == "met")
    total = len(expectations)
    test_summary = f"（已验证测试 {met}/{total}）" if expectations else ""
    text = (
        f"自进化结果：本轮针对 partner 源码的可证伪机制问题展开了一次完整实验{test_summary}。"
        f"自进化判定={decision_text}，production_effective={production_effective}。"
        f"治理账本{gov_status}。"
        f"如发现问题：{summary_line}"
    ).strip()

    # Write a final summary outbound directly so the user actually sees it.
    # We piggyback on the existing outbound channel used by delivery.send_text.
    ws = getattr(ctx, "workspace", None) or str(getattr(ctx, "root", "/mnt/e/work/partner_workspace"))
    instance_id = getattr(ctx, "instance_id", None) or "02"
    user_id = "F4B9E0C9D915FE7280F83DEF7305B27F"
    out_dir = Path(ws) / "state/application/outbound" / instance_id
    out_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt, timezone as _tz
    payload = {
        "schema_version": 2,
        "job_id": getattr(ctx, "job_id", ""),
        "to_user": user_id,
        "content": text,
        "notification_identity": {},
        "pdf_artifacts": [],
        "text_delivered": False,
        "delivery_state": "queued",
        "created_at": _dt.now(_tz.utc).isoformat(),
    }
    out_path = out_dir / f"{getattr(ctx, 'job_id', 'final')}.final_summary.sent"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return result({
        "summary": text,
        "decision": decision_text,
        "production_effective": production_effective,
        "governance_recorded": bool(gov_issue),
        "outbound_path": str(out_path),
        "outbound_text_delivered": False,
    }, "final_summary 已写入 outbound（待 delivery.send_text 实际发送）", [str(out_path)])


def finish(ctx, params):
    child = read(folder(ctx) / 'evolve.json')
    recorded=((child.get('node_outputs') or {}).get('record',{}).get('semantic_output') or {})
    text_ack=read(folder(ctx)/'text_ack.json');pdf_ack=read(folder(ctx)/'report_ack.json')
    effective=bool(recorded.get('valid_matched_experiment') or recorded.get('no_change_behavior_verified'))
    value = {'cycle_id': ctx.job_id, 'stopped': True, 'project_rounds': 2,
             'evolution_effective_attempt':effective,
             'delivery_verified':text_ack.get('delivery_state')=='sent' and pdf_ack.get('delivery_state')=='sent' and pdf_ack.get('pdf_delivered') is True,
             'evolution_status': child.get('status'),
             'evolution_result': (child.get('node_outputs') or {}).get('record', {}),
             'text_delivery': read(folder(ctx) / 'text_ack.json'),
             'pdf_delivery': read(folder(ctx) / 'report_ack.json')}
    path = folder(ctx) / 'completion.json'
    write_json(path, value)
    return result(value, '两轮项目与一次有效自进化验证结束，停止续跑' if effective else
                  '流程已停止，但尚未形成有效自进化实验；失败证据已保留', [str(path)])


DEFINITIONS = [EventDefinition('cycle.' + name, 'project', description, fn,
    execution_method='llm' if name in ('assess','memory_update') else 'local',
    timeout_seconds=300 if name in ('assess','memory_update') else 100)
    for name, fn, description in (
        ('round_request', round_request, '请求真实项目子Flow并消费前轮证据'),
        ('assess', assess, '综合两轮真实推进与差距'),
        ('report_request', report_request, '请求现有PDF子Flow'),
        ('delivery_settle', delivery_settle, '等待本次真实渠道ACK或明确失败'),
        ('memory_update', memory_update, '更新经验和待验证的成长与习惯'),
        ('seal', seal, '冻结交付后全周期证据'),
        ('evolution_request', evolution_request, '固定请求自主自进化子Flow'),
        ('final_summary', final_summary, '自进化结束后生成最终总结消息（含 decision/governance）'),
        ('finish', finish, '保存完整周期结果并停止'))]
