"""Typed projections of actual request-chain terminals; never schedules work."""
from pathlib import Path
import json
from partner.governance.models import IterationReceipt, NextAction
from partner.runtime.action_execution import write_json
from partner.event_fabric import EventLedger


def write_request_receipts(workspace, root_event_id):
    if not root_event_id: return
    root=Path(workspace)
    acknowledgments={row.get("job_id"): row["event_id"] for row in
        EventLedger(root).recent_summaries(limit=100000, include_audit=True)
        if row.get("event_type")=="delivery.channel_ack" and row.get("status")=="completed"}
    jobs=[]
    for path in (root/'state/application/jobs').glob('*.json'):
        row=json.loads(path.read_text())
        if row.get('root_event_id')==root_event_id: jobs.append(row)
    jobs.sort(key=lambda j:j['created_at'])
    rounds=[j for j in jobs if j.get('flow_type')=='project_iteration']
    for index,job in enumerate(rounds):
        if job['status'] not in ('completed','failed','cancelled'):continue
        flow=json.loads((root/'state/event_flows'/f"{job['flow_id']}.json").read_text())
        verify=flow['node_outputs'].get('verify',{})
        attempted=list(dict.fromkeys(flow['completed_node_ids'] + flow.get('failed_node_ids', [])))
        if not attempted:
            continue  # No Event executed; do not invent an action for the typed receipt.
        following=rounds[index+1] if index+1<len(rounds) else None
        next_actions=[]
        if following:
            status={'dispatched':'queued','failed':'blocked','paused':'blocked'}.get(following['status'],following['status'])
            next_actions=[NextAction(title=following['request'][:300],event_type='project.action_execute',
                task_id=following['job_id'],status=status,
                blocked_reason=(following.get('error') or following['status']) if status=='blocked' else '')]
        receipt=IterationReceipt(project_id=job['project_id'],iteration=index+1,
            goal=job.get('intent_contract',{}).get('original_request') or job['request'],
            inputs=[str(a.get('path') if isinstance(a,dict) else a) for a in job.get('attachments') or []],
            actions_executed=attempted,
            artifacts=verify.get('evidence_refs') or [],
            findings=[flow['node_outputs'].get('execute',{}).get('outcome') or job.get('error') or 'No verified business result'],
            next_actions=next_actions,stop_reason='' if next_actions else ('bounded round terminal: '+flow.get('selected_route','')),
            # ACK is separately authoritative; queue acceptance is never true here.
            delivery_confirmed=job["job_id"] in acknowledgments,receipt_id='receipt_'+job['job_id'])
        result=receipt.to_dict()
        result.update(job_id=job['job_id'],flow_id=job['flow_id'],root_event_id=root_event_id,
                      business_verified=bool(verify.get('business_delta')),
                      delivery_ack_event_id=acknowledgments.get(job["job_id"], ""),
                      delivery_ack_ledger=str(root/'state/event_fabric/summaries.jsonl'))
        write_json(root/'state/application/iteration_receipts'/f"{job['job_id']}.json",result)
