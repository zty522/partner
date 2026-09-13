"""Registered figure execution and verification Events."""
from pathlib import Path
from partner.event_fabric.catalog import EventDefinition
from partner.presentation.figures import render, verify_asset


def generate(ctx, params):
    outputs=params.get('flow_outputs') or {}
    plans=(outputs.get('visual_plan',{}).get('semantic_output') or {}).get('visuals',[])
    sources=(outputs.get('sources',{}).get('semantic_output') or {}).get('sources',[])
    allowed=[r['path'] for r in sources if r.get('exists')]
    aliases={r.get('evidence_id'):r['path'] for r in sources}
    basenames={}
    for path in allowed: basenames.setdefault(Path(path).name,[]).append(path)
    assets=[]; failures=[]
    for plan in plans:
        try:
            plan=dict(plan);options=plan.pop('parameters',{})
            if any(k in plan and plan[k]!=v for k,v in options.items()):raise ValueError('conflicting figure parameters')
            plan={**options,**plan};resolutions=[]
            def resolve(value):
                if value in aliases: actual=aliases[value]
                elif value in allowed: actual=value
                elif len(basenames.get(Path(str(value)).name,[]))==1: actual=basenames[Path(str(value)).name][0]
                else: return value
                if actual!=value: resolutions.append({'requested':value,'resolved_evidence':actual})
                return actual
            plan['source_refs']=[resolve(v) for v in plan.get('source_refs',[])]
            if plan.get('pose_path'):plan['pose_path']=resolve(plan['pose_path'])
            if plan.get('residue_source'):plan['residue_source']=resolve(plan['residue_source'])
            if resolutions:plan['source_resolution']=resolutions
            assets.append(render(plan,Path(ctx.working_dir)/'figures',allowed))
        except Exception as exc: failures.append({'id':plan.get('id'),'required':plan.get('required',True),'error':f'{type(exc).__name__}: {exc}'})
    from partner.runtime.action_execution import write_json
    manifest=Path(ctx.working_dir)/'figure_manifest.json'
    value={'schema_version':1,'images':assets,'failures':failures,'planned_count':len(plans)}
    write_json(manifest,value)
    ok=not any(f['required'] for f in failures)
    return {'ok':ok,'status':'completed' if ok else 'failed','files':[r['path'] for r in assets],
            'manifest_path':str(manifest),'semantic_output':value,
            'retryable':not ok,'error':'' if ok else 'required figure could not be rendered',
            'summary':f'实际生成并回读 {len(assets)} 张证据图；{len(failures)} 项缺口'}


def verify(ctx,params):
    prior=(params.get('flow_outputs') or {}).get('visuals') or params.get('previous') or {}
    errors=[]; assets=(prior.get('semantic_output') or {}).get('images',[])
    for asset in assets:
        try:
            verify_asset(asset)
            if not asset.get('caption'): raise ValueError('figure caption missing')
        except Exception as exc: errors.append(f"{asset.get('id')}: {exc}")
    if any(f.get('required',True) for f in (prior.get('semantic_output') or {}).get('failures',[])): errors.append('required figures missing')
    return {'ok':not errors,'status':'failed' if errors else 'completed','files':prior.get('files',[]),
            'semantic_output':{'images':assets,'errors':errors},'summary':f'{len(assets)} 张图完成来源、格式与图题检查'}


DEFINITIONS=[EventDefinition('visualization.render','visualization','按真实数据类型绘制可追溯图件',generate,
    produces_artifact=True,reads_existing_artifact=True,timeout_seconds=300,max_attempts=2),
    EventDefinition('visualization.verify','visualization','独立回读图件及来源哈希',verify,reads_existing_artifact=True)]
