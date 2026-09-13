"""ACK-based semantic identity and pending-progress selection.

Fingerprints use verified artifact bytes, not model phrasing. Separate requests
and recipients never suppress one another; blocked/correction/final deliveries
are never coalesced away. Channel ACK remains the authority for known facts.
"""
from pathlib import Path
import hashlib
import json


def identity(ctx,params):
    outputs=params.get('flow_outputs') or {};contract=params.get('intent_contract') or {}
    decision=next((v for v in outputs.values() if isinstance(v,dict) and 'notify' in v),{})
    purpose=decision.get('notification_kind') or params.get('notification_kind') or contract.get('notification_kind') or ('answer' if outputs.get('answer') else 'final')
    facts=[]
    for key in ('execute','verify','crosscheck','compare','synthesize'):
        row=outputs.get(key) or {}
        for ref in row.get('evidence_refs') or row.get('files') or []:
            p=Path(str(ref))
            if p.is_file():
                from partner.presentation.figures import digest
                facts.append(digest(p))
    # No proven artifact identity means semantic suppression is disabled.
    fingerprint=hashlib.sha256(json.dumps(sorted(set(facts))).encode()).hexdigest() if facts else ''
    origin=str(params.get('origin_instance') or getattr(ctx,'intake_instance_id','') or getattr(ctx,'instance_id',''))
    return {'purpose':purpose,'fact_fingerprint':fingerprint,'project_id':params.get('project_id') or getattr(ctx,'project_id',''),
            'request_scope':params.get('root_event_id') or contract.get('notification_scope') or getattr(ctx,'job_id',''),
            'origin':origin}


def already_acknowledged(ctx,params,identity_value):
    if not identity_value.get('fact_fingerprint') or identity_value['purpose'] in {'blocked','correction','final','answer'}: return False
    folder=Path(ctx.workspace)/'state/application/outbound'/identity_value['origin']
    sender=str(params.get('sender_id') or getattr(ctx,'sender_id',''))
    for path in folder.glob('*.sent'):
        try: row=json.loads(path.read_text())
        except (OSError,ValueError): continue
        if row.get('to_user')==sender and row.get('text_delivered') and row.get('notification_identity')==identity_value: return True
    return False


def stale_progress(path,value):
    identity_value=value.get('notification_identity') or {}
    if identity_value.get('purpose') not in {'progress','waiting'} or value.get('text_delivered'): return False
    for newer in path.parent.glob('*.json'):
        if newer==path: continue
        try: row=json.loads(newer.read_text())
        except (OSError,ValueError): continue
        other=row.get('notification_identity') or {}
        same=all(other.get(k)==identity_value.get(k) for k in ('project_id','request_scope','origin'))
        if same and row.get('to_user')==value.get('to_user') and other.get('purpose') in {'progress','waiting','final'} and str(row.get('created_at',''))>str(value.get('created_at','')):
            return True
    return False


def waiting_message(ctx,params):
    """A runtime status projection, not a generated business conclusion."""
    contract=params.get('intent_contract') or {}
    failed=bool(contract.get('failure_for_job'))
    job_id=contract.get('failure_for_job') or contract.get('waiting_for_job')
    if not job_id:return None
    path=Path(ctx.workspace)/'state/application/jobs'/f'{job_id}.json'
    try:job=json.loads(path.read_text())
    except (OSError,ValueError):return ''
    if failed:
        if job.get('status')!='failed':return ''
        error=str(job.get('error') or '')
        reason='必需图件没有通过检查' if 'visual' in error else '部分结论尚未通过证据核对' if 'claims' in error else '报告交付链中的一个步骤没有通过检查'
        return f'这份报告暂未完成：{reason}。这不等于之前的业务实验失败；已有数据和本轮检查记录都已保留。'
    if job.get('status') not in {'running','dispatched'}:return ''
    stage=job.get('current_event_id')
    labels={'outline':'整理报告结构','visual_plan':'选择有依据的图表','visuals':'生成真实图件','visual_verify':'核验图件来源',
            'draft':'撰写图文报告','claims':'逐项核对报告结论','render':'排版报告','quality':'检查报告页面',
            'compose':'整理交付说明','message_critic':'核对交付说明','execute':'执行已安排的任务','verify':'核验任务结果'}
    action=labels.get(stage,'处理已接收的任务')
    return f'目前还在{action}，这一步尚未完成。已有结果会保留，完成核验后再交付。'


def with_report_context(ctx,params):
    """Resolve a prior report by durable Job/Flow, never accept invented outputs."""
    report_id=(params.get('intent_contract') or {}).get('report_job_id')
    if not report_id:return params
    path=Path(ctx.workspace)/'state/application/jobs'/f'{report_id}.json'
    job=json.loads(path.read_text())
    if job.get('status')!='completed' or job.get('project_id')!=str(params.get('project_id') or ctx.project_id):raise ValueError('report context must be a completed report in the same project')
    from partner.event_fabric import EventFlowStore
    outputs=EventFlowStore(ctx.workspace).load(job['flow_id']).node_outputs
    if not outputs.get('claims',{}).get('ok'):raise ValueError('report claims were not verified')
    return {**params,'flow_outputs':{**(params.get('flow_outputs') or {}),
            'claims':outputs['claims'],'visuals':outputs.get('visuals',{})}}
