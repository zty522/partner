"""Recover actual recent action receipts without rerunning their commands."""
import json
import hashlib
from pathlib import Path


def recent_execution_context(workspace, project_id, current_job_id):
    root=Path(workspace)
    rows=[]
    from partner.index.resource_catalog import ResourceCatalog
    for item in ResourceCatalog(root).query('background',limit=100):
        path=Path(item['path'])
        try:
            task=json.loads(path.read_text())
            if task['job_id']==current_job_id or task['status'] not in ('completed','failed','cancelled','timed_out'):
                continue
            from partner.index.job_repository import init
            job=init(root).get_record(task['job_id']) or {}
            if job['project_id']!=project_id:continue
            rows.append((task['created_at'],path.parent,task))
        except (OSError,ValueError,KeyError):continue
    result=[]
    for _,folder,task in sorted(rows,reverse=True)[:2]:
        work=Path(task['work'])
        checkpoint=work/'.execution/checkpoint.json'
        try: saved=json.loads(checkpoint.read_text())
        except (OSError,ValueError):saved={}
        try: terminal=json.loads((folder/'result.json').read_text())
        except (OSError,ValueError):terminal={}
        commands=saved.get('commands') or []
        result.append({'task_id':task['task_id'],'status':task['status'],
            'terminal_success':terminal.get('ok'),
            'terminal_outcome':str(terminal.get('outcome') or terminal.get('error') or '')[:1600],
            'result_receipt':str(folder/'result.json'),
            'work':str(work),'checkpoint':str(checkpoint),'receipt':str(folder/'task.json'),
            'error':task.get('error',''), 'command_count':len(commands),
            'artifacts':[r['path'] for r in ResourceCatalog(root).query('artifact',scope=task['job_id'],limit=15)],
            'command_results':[{'index':c['index'],'exit_code':c.get('exit_code'),
                'command':c.get('command','')[:130], 'output_tail':c.get('output','')[-160:]}
                for c in commands[-12:]],
            'rule':'Terminal result is authoritative for final status; command errors may be repaired intermediate attempts. Use checkpoint receipts to recover partial work, not to overwrite the terminal result with an earlier failure.'})
    return result


def verified_project_artifacts(workspace, project_id, current_job_id, input_paths=()):
    """Index actual verified files across runs, including nested experiment data.

    Recency is a ranking hint, never proof that an unrelated same-name file
    belongs to the requested experiment. Preserve the full index on disk.
    """
    root=Path(workspace);rows=[];seen=set()
    from partner.index.resource_catalog import job_records
    for job in job_records(root,limit=200):
        try:
            request=str((job.get('intent_contract') or {}).get('original_request') or job.get('request') or '')
            same_input=any(str(p) in request for p in input_paths)
            if (job.get('project_id')!=project_id and not same_input) or job.get('job_id')==current_job_id or not job.get('flow_id'):continue
            flow=json.loads((root/'state/event_flows'/f"{job['flow_id']}.json").read_text())
            evidence=(flow.get('node_outputs',{}).get('verify',{}).get('semantic_output') or {}).get('evidence') or []
            for record in evidence:
                if not isinstance(record,dict) or not record.get('valid') or not record.get('sha256'):continue
                source=Path(record['path'])
                if str(source) in seen or not source.is_file():continue
                with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                if digest!=record['sha256']:continue
                seen.add(str(source));rows.append({'path':str(source),'sha256':digest,'bytes':source.stat().st_size,
                    'job_id':job['job_id'],'flow_id':job['flow_id'],'project_id':job['project_id'],
                    'relation':'same_project' if job['project_id']==project_id else 'same_explicit_input', 'created_at':job.get('created_at',''),
                    'role':'verified historical artifact; inspect experiment inputs before reuse'})
        except (OSError,ValueError,KeyError):continue
    return sorted(rows,key=lambda r:r['created_at'],reverse=True)
