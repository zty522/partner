"""Explicit, bounded index reconciliation. Never invoked by an ordinary Event."""
import argparse
import json
from pathlib import Path
from partner.index.resource_catalog import ResourceCatalog, register_runtime
from partner.index.stream_projection import StreamProjection
from partner.index.job_repository import init as jobs


def refresh(workspace, repo, external=False):
    catalog=ResourceCatalog(workspace)
    for name in ('partner','tests','scripts'):
        print(name,catalog.maintain(repo/name,'code',scope=name,max_files=15000),flush=True)
    print('docs',catalog.maintain(repo/'docs','document',max_files=10000),flush=True)
    for directory,kind in [('state/event_flows','flow'),('state/improvement_evidence','bundle'),
            ('state/opportunities','opportunity'),('state/event_runtime/background','background'),
            ('share/mind/governance/cognition_shadow','cognition'),('share/mind/governance/experiments','experiment')]:
        count=0
        for p in (workspace/directory).glob('**/*.json' if kind=='background' else '*.json'):
            if kind=='background' and p.name!='task.json':continue
            data=json.loads(p.read_text());catalog.register(p,kind,str(data.get('project_id') or (data.get('bundle') or {}).get('project_id') or data.get('job_id') or ''),
                {k:data.get(k) for k in ('flow_type','status','instance_id','job_id')});count+=1
        print(kind,count,flush=True)
    repo_jobs=jobs(workspace)
    for p in (workspace/'state/application/jobs').glob('*.json'):
        data=json.loads(p.read_text())
        if repo_jobs.get_record(data['job_id']):continue
        if data.get('status') in ('queued','dispatched','running'):
            raise RuntimeError('unmigrated active job requires explicit recovery: '+data['job_id'])
        repo_jobs.upsert_from_record(data,actor='explicit_index_migration')
    projection=StreamProjection(workspace)
    for p in (workspace/'state/event_fabric').glob('*.jsonl'):
        print('stream',p.name,flush=True);projection.sync(p)
    for p in (workspace/'share/mind/memory').glob('*.jsonl'):projection.sync(p)
    if external:
        # External is chunked per top-level dir because /mnt/e is a slow 9p
        # mount and code/ alone has ~100k files (200s+ to walk).  A single
        # os.walk(external) stalls inside code/ and never reaches the
        # alphabetical successors (insights/, literature/, PocketFlow/,
        # targetdiff/) — which were silently unindexed.  Each chunk carries
        # its own scope so a slow chunk cannot starve the others.
        _EXCLUDE = {'.git', '__pycache__', '.venv', 'node_modules', 'isolated', 'frozen'}
        ext_root = workspace / 'external'
        for _sub in sorted(p for p in ext_root.iterdir() if p.is_dir()):
            if _sub.name in _EXCLUDE:
                continue
            try:
                _count = catalog.maintain(_sub, 'external', scope=_sub.name, max_files=30000)
                print('external', _sub.name, _count, flush=True)
            except RuntimeError as _exc:
                # Huge dir (code/): fall back to its own children as scopes.
                _total = 0
                for _ss in sorted(p for p in _sub.iterdir() if p.is_dir()):
                    if _ss.name in _EXCLUDE:
                        continue
                    _total += catalog.maintain(_ss, 'external', scope=f"{_sub.name}/{_ss.name}", max_files=30000)
                print('external', _sub.name, _total, '(chunked)', flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--external',action='store_true')
    a=parser.parse_args();refresh(a.workspace,a.repo,a.external)
