"""Bounded, read-only runtime receipts for intent and status answers."""
import json
from pathlib import Path


def runtime_status(ctx, params):
    root = Path(str(getattr(ctx, 'workspace', '') or ''))
    project = str(params.get('project_id') or getattr(ctx, 'project_id', ''))
    instance = str(getattr(ctx, 'instance_id', '') or '')
    current = str(getattr(ctx, 'job_id', '') or '')
    # Shared worker IDs are execution slots, not the user's receiving instance.
    try:
        current_job=json.loads((root/'state/application/jobs'/f'{current}.json').read_text())
        instance=str(current_job.get('intake_instance_id') or current_job.get('origin_instance') or instance)
    except (OSError, ValueError):
        pass
    rows = []
    from partner.index.resource_catalog import job_records, ResourceCatalog
    for job in job_records(root,project_id=project,limit=50):
        if job.get('job_id') == current or job.get('project_id') != project:
            continue
        if instance and (job.get('intake_instance_id') or job.get('origin_instance')) != instance:
            continue
        if job.get('flow_type') not in {'project_iteration', 'browser_video_learning', 'new_project'}:
            continue
        flow_path = root / 'state/event_flows' / (str(job.get('flow_id', '')) + '.json')
        try:
            flow = json.loads(flow_path.read_text())
        except (OSError, ValueError):
            continue
        outputs = flow.get('node_outputs') or {}
        work=root/'state/event_runtime/work'/job['job_id']/str(job.get('flow_id',''))
        try:
            checkpoint=json.loads((work/'.execution/checkpoint.json').read_text())
        except (OSError,ValueError):
            checkpoint={}
        # Include only actual execution receipts, not planned or query-only jobs.
        has_receipt = bool(outputs.get('execute') or checkpoint.get('commands') or any(k in outputs for k in ('capture', 'transcribe', 'vision', 'verify')))
        active_action = job.get('status') == 'running' and job.get('current_event_id') in {'execute','capture','transcribe','vision','synthesize'}
        if not has_receipt and not active_action:
            continue
        rows.append({'job_id':job['job_id'], 'created_at':job.get('created_at',''),
            'request':(job.get('request') or '')[:1600], 'status':job.get('status'),
            'has_execution_receipt':has_receipt,
            'work_directory':str(work),
            'partial_execution':{'state':checkpoint.get('state'),'command_count':len(checkpoint.get('commands',[]))},
            'current_node':job.get('current_event_id'), 'completed_nodes':flow.get('completed_node_ids',[]),
            'execution_summary':str((outputs.get('execute') or outputs.get('verify') or {}).get('summary',''))[:1000],
            'reflection':outputs.get('reflect',{}).get('semantic_output',{}),
            'evidence_refs':list(dict.fromkeys(p for value in outputs.values() if isinstance(value,dict)
                for p in (value.get('evidence_refs') or value.get('files') or [])))[:8]})
    rows.sort(key=lambda row:row['created_at'],reverse=True)
    from partner.runtime.artifact_checks import check_file
    for row in rows[:4]:
        work=Path(row['work_directory'])
        partial=[Path(r['path']) for r in ResourceCatalog(root).query('artifact',scope=row['job_id'],limit=12) if Path(r['path']).is_file()]
        row['actual_partial_files']=[{'path':str(p),'bytes':p.stat().st_size,
            'excerpt':ResourceCatalog(root).read(p,max_bytes=2000,purpose='status')['text'][:500] if p.suffix=='.md' else '',
            'scope':'file exists; not proof of complete task or scientific validity'} for p in partial]
        row['evidence_refs']=list(dict.fromkeys([str(p) for p in partial]+row['evidence_refs']))[:16]
        row['current_data_checks'] = [check_file(Path(p)) for p in row['evidence_refs']
            if '.execution' not in Path(p).parts and Path(p).suffix in {'.json','.jsonl','.csv','.tsv'}
            and Path(p).is_file() and Path(p).stat().st_size <= 2_000_000]
    return {'source':'read_only_actual_runtime_receipts', 'scope':'same project and instance; planned actions are not completed work', 'jobs':rows[:4]}
