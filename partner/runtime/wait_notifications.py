"""Worker-owned durable waiting notices. Observers/watchdogs never create work."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4
from partner.application.models import JobRecord
from partner.runtime.action_execution import write_json


def enqueue_if_due(worker,job,state,now=None):
    now=time.time() if now is None else now
    if job.channel!='qq' or job.flow_type=='message_delivery': return None
    folder=worker.root/'state/application/wait_notices';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(job.job_id+'.json')
    if path.exists(): saved=json.loads(path.read_text())
    else: saved={'started_at':now,'next_at':now+180,'count':0}
    if now<saved['next_at']:
        if not path.exists(): write_json(path,saved)
        return None
    old=saved.get('notice_job_id')
    if old:
        notice=worker._load_job(worker.jobs_dir/(old+'.json'))
        if notice and notice.status in {'queued','running','dispatched'}: return None
    definition=worker.flows.get('message_delivery');jid='job_'+uuid4().hex[:16]
    timestamp=datetime.now(timezone.utc).isoformat()
    # Snapshot is a statement about actual execution, never a new business result.
    stage=state.current_event_id or ','.join(state.ready_node_ids)
    notice=JobRecord(job_id=jid,project_id=job.project_id,title='长任务等待状态',request='告诉用户当前任务尚在运行，简短说明当前阶段；不要编造进度百分比、完成时间、新发现或下一步。',channel=job.channel,sender_id=job.sender_id,origin_instance=job.origin_instance,intake_instance_id=job.intake_instance_id,assigned_instance=job.assigned_instance,persona_hint=job.persona_hint,status='queued',created_at=timestamp,updated_at=timestamp,root_event_id=job.root_event_id,report_policy='none',event_catalog_version=worker.catalog.version,intent_contract={'notification_kind':'waiting','notification_scope':job.root_event_id,'waiting_for_job':job.job_id,'running_snapshot':{'flow_type':job.flow_type,'current_stage':stage,'elapsed_seconds':round(now-saved['started_at'])}})
    flow=worker.controller.start(definition,catalog_version=worker.catalog.version,task_id=jid,project_id=job.project_id,instance_id=job.intake_instance_id)
    flow.root_event_id=job.root_event_id;worker.store.save(flow)
    notice.flow_id=flow.flow_id;notice.flow_type=flow.flow_type;worker._save_job(notice)
    saved.update(count=saved['count']+1,notice_job_id=jid,next_at=now+min(900,180*2**min(saved['count']+1,3)))
    write_json(path,saved)
    return jid


async def dispatch_if_due(worker,job,state,ctx,now=None):
    """Run the lightweight notice graph inside the owning worker's existing slot.

    This prevents an all-busy pool from starving notifications. The ordinary
    claim gate still arbitrates against other workers; notice handlers only
    project durable status and use the canonical outbound queue.
    """
    jid=enqueue_if_due(worker,job,state,now)
    if not jid or not worker.shared_mode or not worker._try_acquire_lock(jid):return jid
    from dataclasses import replace
    notice=worker._load_job(worker.jobs_dir/(jid+'.json'))
    try:
        flow=worker.store.load(notice.flow_id);definition=worker.flows.get(flow.flow_type,version=flow.definition_version)
        notice.status='running';worker._save_job(notice)
        initial={'request':notice.request,'channel':notice.channel,'sender_id':notice.sender_id,'origin_instance':notice.origin_instance,
                 'project_id':notice.project_id,'root_event_id':notice.root_event_id,'intent_contract':notice.intent_contract}
        context=replace(ctx,job_id=jid)
        while flow.ready_node_ids:
            result=await worker.runner.run_ready_node(flow,definition,flow.ready_node_ids[0],context,initial)
            flow=result.flow_state
            if result.output.get('status')=='waiting':break
        notice.status=flow.status;notice.completed_event_ids=list(flow.completed_node_ids);worker._save_job(notice)
    finally:
        (worker.lock_dir/f'{jid}.lock').unlink(missing_ok=True)
    return jid


def enqueue_report_failure(worker,job):
    if job.status!='failed' or not job.flow_type.startswith('pdf_report'):return None
    marker=worker.root/'state/application/wait_notices'/(job.job_id+'_failure.json')
    if marker.exists():return None
    definition=worker.flows.get('message_delivery');jid='job_'+uuid4().hex[:16];now=datetime.now(timezone.utc).isoformat()
    flow=worker.controller.start(definition,catalog_version=worker.catalog.version,task_id=jid,project_id=job.project_id,instance_id=job.intake_instance_id)
    flow.root_event_id=job.root_event_id;worker.store.save(flow)
    notice=JobRecord(job_id=jid,project_id=job.project_id,title='报告交付缺口',request='如实说明报告未完成的阶段，不将报告失败称为业务实验失败。',channel=job.channel,sender_id=job.sender_id,origin_instance=job.origin_instance,intake_instance_id=job.intake_instance_id,assigned_instance=job.assigned_instance,persona_hint=job.persona_hint,status='queued',created_at=now,updated_at=now,root_event_id=job.root_event_id,report_policy='none',event_catalog_version=worker.catalog.version,flow_id=flow.flow_id,flow_type=flow.flow_type,intent_contract={'failure_for_job':job.job_id,'notification_kind':'blocked','notification_scope':job.root_event_id})
    worker._save_job(notice);write_json(marker,{'notice_job_id':jid,'at':now});return jid
