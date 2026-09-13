"""Recover actual recent action receipts without rerunning their commands."""
import json
from pathlib import Path


def recent_execution_context(workspace, project_id, current_job_id):
    root=Path(workspace)
    rows=[]
    for path in (root/'state/event_runtime/background').glob('*/task.json'):
        try:
            task=json.loads(path.read_text())
            if task['job_id']==current_job_id or task['status'] not in ('completed','failed','cancelled','timed_out'):
                continue
            job=json.loads((root/'state/application/jobs'/f"{task['job_id']}.json").read_text())
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
            'artifacts':[str(p) for p in work.glob('*') if p.is_file()][:15],
            'command_results':[{'index':c['index'],'exit_code':c.get('exit_code'),
                'command':c.get('command','')[:130], 'output_tail':c.get('output','')[-160:]}
                for c in commands[-12:]],
            'rule':'Terminal result is authoritative for final status; command errors may be repaired intermediate attempts. Use checkpoint receipts to recover partial work, not to overwrite the terminal result with an earlier failure.'})
    return result
