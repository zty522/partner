"""Evidence-led self audit, expectation-driven experiments and bounded revision.

Every function is one catalog Event. No hidden Event invocation and no
candidate-count reward: weak evidence requests investigation rather than edits.
"""
from pathlib import Path
import json
import hashlib
import os
import time
import fcntl
from partner.event_fabric.catalog import EventDefinition
from partner.runtime.action_execution import write_json
from partner.runtime import evolution_experiment as experiment
from ._llm import call_model, json_object
from .cycle import read, result

ASPECTS = ('intent','planning','execution','iteration','project_progress',
           'event_flow','message_content','delivery','pdf_report','experience','growth','habit','efficiency')


def runtime_contract():
    from partner.event_flows import build_flow_registry
    registry = build_flow_registry()
    return {
        'active_evolution_flow': 'autonomous_evolution',
        'handler_module': 'partner/events/autonomous_evolution.py',
        'child_owner': 'partner/runtime/cycle_children.py',
        'experiment_runner': 'partner/runtime/matched_execution.py',
        'experiment_scope': 'isolated pytest, not business action outcome merging',
        'node_events': {n.node_id:n.event_type for n in registry.get('autonomous_evolution').nodes},
        'project_cycle_definition': {'version':registry.get('project_cycle').version,
            'nodes':[{'node_id':n.node_id,'event_type':n.event_type,'depends_on':list(n.depends_on)}
                     for n in registry.get('project_cycle').nodes]},
        'snapshot_boundary': 'sealed_cycle is captured BEFORE this autonomous_evolution child runs. '
            'Absence of this child in that historical snapshot is expected, not proof of a spawn failure. '
            'Executing audit/counter/design establishes entry into evolution, not its successful completion.',
        'budget_semantics': 'action_seconds is an execution time ceiling, not a minimum experiment duration. '
            'A shorter completed action does not prove the budget was ignored; inspect its actual consumer.',
        'protected_source_prefixes': list(experiment._frozen_patterns()),
        'prior_llm_reviews_are_evidence_not_ground_truth': True}


def compact(value, text_limit=700, items=4):
    if isinstance(value, str):
        return value if len(value) <= text_limit else value[:text_limit] + ' [excerpt shortened; full record retained]'
    if isinstance(value, list):
        return [compact(v,text_limit,items) for v in value[:items]] + ([{'omitted_items':len(value)-items}] if len(value)>items else [])
    if isinstance(value, dict):
        return {k:compact(v,text_limit,items) for k,v in value.items()}
    return value


def cycle_view(value, inventory=False):
    result = {k:compact(value.get(k),700,4) for k in
        ('cycle_id','instance_id','project_id','original_request','aspects','delivery','supervisor_recovery')}
    result['rounds'] = {name:{'flow_id':row.get('flow_id'),'status':row.get('status'),
        'nodes':{n:{k:compact(out.get(k),700) for k in ('ok','status','summary','error','business_delta')}
                 for n,out in row.get('nodes',{}).items()}}
        for name,row in value.get('rounds',{}).items()}
    result['memory'] = {kind:{'status':row.get('status'),'production_effective':row.get('production_effective'),
        'content_excerpt':json.dumps(row.get('content', row),ensure_ascii=False)[:700],
        'schema_content_present':'content' in row,
        'confidence':str(row.get('confidence'))[:100], 'record_ref':'memory_'+kind+'.json'}
        for kind,row in value.get('memory',{}).items()}
    result['assessment'] = {'summary':str(value.get('assessment',{}).get('summary',''))[:1500],
        'excerpt':json.dumps(value.get('assessment',{}),ensure_ascii=False)[:2000],
        'full_record':'assessment.json'}
    result['aspects'] = list(ASPECTS)
    result['runtime_contract'] = runtime_contract()
    reports = value.get('report_content') or []
    result['report_content'] = [next((r for r in reports if r['path'].endswith('.pdf')), reports[0])] if reports else []
    result['substantive_artifacts'] = [{**r,'excerpt':r['excerpt'][:1500]} for r in value.get('substantive_artifacts',[])[:4]]
    columns = ('event_id','event_type','node_id','status','created_at','mechanism')
    result['event_terminals'] = {'columns':columns,
        'rows':[[r.get(k) for k in columns] for r in value.get('event_terminals',[])]}
    if inventory: result['source_inventory'] = value.get('source_inventory', [])
    result['full_cycle_record'] = value.get('manifest')
    return result


def source_view(value):
    files = {}
    for index, (name, row) in enumerate(value.get('files', {}).items()):
        source = row.get('source', '')
        limit = 6000 if index < 2 else 3000
        excerpt = source if len(source)<=limit else source[:limit-1000]+'\n[Middle excerpt omitted; full read retained in sources.json]\n'+source[-1000:]
        files[name] = {**row,'source':excerpt}
    return {'files':files,'full_read_record':'sources.json'}


def probe_target_sources(ctx):
    """Resolve the counter-reader's concrete source gaps before designing edits."""
    import ast, re
    reconsidered = saved(ctx,'reconsider')
    counter_value = saved(ctx, 'counter')
    # Scope fresh reads to the selected issue rather than the broad inventory.
    focus = json.dumps({'selected_issue':counter_value.get('selected_issue') or {},
                        'missing_evidence':counter_value.get('missing_evidence') or [],
                        'read_plan':reconsidered if reconsidered.get('selected_issue') else
                        ({} if counter_value.get('selected_issue') else saved(ctx,'read_plan'))},
                       ensure_ascii=False)
    files = {}
    for relative in source_records(ctx).get('files',{}):
        if relative not in focus and Path(relative).name not in focus:
            continue
        path = (experiment.REPO / relative).resolve()
        if experiment.REPO not in path.parents or not path.is_file(): continue
        source=path.read_text(); lines=source.splitlines()
        try: tree=ast.parse(source)
        except SyntaxError: continue
        functions={n.name:n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
        selected=[name for name in functions if re.search(r'\b'+re.escape(name)+r'\b',focus)]
        for name in list(selected):
            selected.extend(n.func.id for n in ast.walk(functions[name])
                            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in functions)
        fragments=[]
        for name in dict.fromkeys(selected):
            node=functions[name]
            fragments.append({'function':name,'start_line':node.lineno,'end_line':node.end_lineno,
                              'source':'\n'.join(lines[node.lineno-1:node.end_lineno])})
            if sum(len(x['source']) for x in fragments)>=18000: break
        files[relative]={'sha256':experiment.sha(path),'functions':fragments,
                         'module_excerpt':source[:3000] if not fragments else ''}
    # (2026-09-15) Expanded probe: when no file matches by function name and the
    # selected issue references domain tokens (decision_rule_result, scalar,
    # promoted, CI sign, Pareto, etc.), grep the full repo for those tokens so
    # design can locate the actual decision function rather than silently
    # returning no functions and forcing no_change=True.
    if not any(row.get('functions') for row in files.values()):
        issue = counter_value.get('selected_issue') or {}
        symptom = issue.get('symptom') or ''
        hypothesis = issue.get('hypothesis') or ''
        keywords = set()
        for text in (symptom, hypothesis, issue.get('concrete_evidence') or '',
                     issue.get('reproduction_steps') or '', issue.get('oracle') or ''):
            if isinstance(text, list):
                text = ' '.join(str(t) for t in text)
            elif not isinstance(text, str):
                text = str(text)
            for token in re.findall(r'[A-Za-z_][A-Za-z0-9_]{4,}', text):
                if token in {'partner', 'expected', 'value', 'should', 'would', 'could',
                             'partner', 'module', 'class', 'function', 'method',
                             'return', 'result', 'result', 'output', 'assert', 'test',
                             'value', 'verify', 'check', 'expect', 'could', 'would'}:
                    continue
                keywords.add(token)
        extra_files = {}
        from partner.index.resource_catalog import ResourceCatalog
        for item in ResourceCatalog(ctx.workspace).query('code',terms=sorted(keywords),limit=10):
            relative=Path(item['path'])
            try:
                rel = str(relative.relative_to(experiment.REPO))
            except ValueError:
                continue
            if rel.startswith(('.git', 'tests', 'docs', 'state', 'instances', '__pycache__', 'scripts')):
                continue
            try:
                source_text = relative.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            hits = [tok for tok in keywords if tok in source_text]
            if not hits:
                continue
            try:
                tree2 = ast.parse(source_text)
            except SyntaxError:
                continue
            functions2 = {n.name: n for n in ast.walk(tree2)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            matched = []
            for tok in hits:
                for fname, node in functions2.items():
                    if tok.lower() in fname.lower() or any(
                        tok in (sub.func.id if isinstance(sub.func, ast.Name) else '')
                        for sub in ast.walk(node) if isinstance(sub, ast.Call)
                    ):
                        matched.append(fname)
            matched = list(dict.fromkeys(matched))[:6]
            fragments = []
            lines = source_text.splitlines()
            for name in matched:
                if name not in functions2:
                    continue
                node = functions2[name]
                fragments.append({'function': name,
                                  'start_line': node.lineno,
                                  'end_line': node.end_lineno,
                                  'source': '\n'.join(lines[node.lineno - 1:node.end_lineno]),
                                  'matched_keyword': ', '.join(hits[:3])})
            extra_files[rel] = {
                'sha256': experiment.sha(relative),
                'functions': fragments,
                'module_excerpt': source_text[:3000],
                'matched_keywords': hits[:8],
                'expanded_probe': True,
            }
            if len(extra_files) >= 5:
                break
        for rel, row in extra_files.items():
            files.setdefault(rel, row)
    value = {'files': files,
             'reason': 'counter-reader requested missing implementation; fresh exact functions and local callees read before design'}
    write_json(directory(ctx) / 'target_source_probe.json', value)
    return value


def directory(ctx):
    return Path(ctx.workspace) / 'state/cycles' / ctx.job_id / 'evolution'


def saved(ctx, name):
    value = read(directory(ctx)/(name+'.json'))
    if name == 'tests':
        for node in ('test_repair_2','test_repair_v2','test_repair'):
            repaired = read(directory(ctx)/(node+'.json'))
            if 'test_code' in repaired: return repaired
    if name == 'test_review':
        for node in ('tests_review_2','tests_review','test_confirm_2','test_confirm'):
            confirmed = read(directory(ctx)/(node+'.json'))
            if 'accepted' in confirmed: return confirmed
    if name == 'design':
        confirmed = read(directory(ctx)/'design_confirm.json')
        if 'target_files' in confirmed: return confirmed
    if name == 'counter':
        next_issue = read(directory(ctx)/'reconsider.json').get('selected_issue')
        if next_issue and any(r.get('id') == next_issue.get('id') and r.get('verdict') == 'supported' for r in value.get('issues_review', [])): return {**value, 'selected_issue':next_issue, 'selection_revision':'bounded additional investigation'}
    return value


def source_records(ctx):
    first=saved(ctx,'sources'); extra=saved(ctx,'sources_extra')
    return {**first,'files':{**extra.get('files',{}),
        **{k:v for k,v in first.get('files',{}).items() if k not in extra.get('files',{})}}}


def verification_dependencies(ctx):
    """Read actual helpers imported by the functions named in the design."""
    import ast, textwrap
    focus=json.dumps(saved(ctx,'design'),ensure_ascii=False)
    files={};bindings={}
    for relative_source,row in read(directory(ctx)/'target_source_probe.json').get('files',{}).items():
        module_name=relative_source[:-3].replace('/','.')
        source_path=experiment.REPO/relative_source
        global_imports=set()
        if source_path.is_file():
            for node in ast.parse(source_path.read_text()).body:
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    global_imports.update(a.asname or a.name for a in node.names)
        for function in row.get('functions',[]):
            if function['function'] not in focus: continue
            try:tree=ast.parse(textwrap.dedent(function['source']))
            except SyntaxError:continue
            calls={n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
            local_imports={a.asname or a.name:(n.module+'.'+a.name) for n in ast.walk(tree)
                           if isinstance(n,ast.ImportFrom) and n.module and n.level==0 for a in n.names}
            bindings[module_name+'.'+function['function']]={name:{'lookup':'function-time import' if name in local_imports else 'consumer module global',
                'patch_location':local_imports.get(name,module_name+'.'+name)} for name in calls if name in local_imports or name in global_imports}
            for node in ast.walk(tree):
                module=node.module if isinstance(node,ast.ImportFrom) else ''
                if not module or not module.startswith('partner.'):continue
                relative=module.replace('.','/')+'.py';p=experiment.REPO/relative
                if p.is_file() and relative not in files and len(files)<3:
                    files[relative]={'sha256':experiment.sha(p),'source':p.read_text()[:24000]}
    value={'files':files,'python_call_bindings':bindings}
    write_json(directory(ctx)/'verification_dependencies.json',value)
    return value


def persist(ctx, params, value, summary='', usage=None):
    node_id = params.get('node_id') if isinstance(params, dict) else None
    node_id = node_id or '_ad_hoc'
    path=directory(ctx)/(node_id+'.json')
    write_json(path,value)
    return {**result(value,summary or str(value.get('summary') or value.get('reason') or node_id),[str(path)]),
            'token_usage':usage or {}}


def parse_design(raw):
    """Validate the whole object, never silently accept a valid JSON prefix."""
    text = raw.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('design must be one JSON object')
    if not isinstance(value.get('no_change'), bool):
        raise ValueError('no_change must be a boolean')
    for key in ('target_files', 'verification_files', 'expectations'):
        if not isinstance(value.get(key), list):
            raise ValueError(key + ' must be a list')
    for relative in value['target_files'] + value['verification_files']:
        experiment.safe_source(relative)
    if not value['expectations']:
        raise ValueError('design requires executable expectations')
    if value['no_change'] and (value['target_files'] or not value['verification_files']):
        raise ValueError('no_change requires empty target_files and nonempty verification_files')
    for item in value['expectations']:
        if not isinstance(item, dict) or not str(item.get('test_name', '')).startswith('test_'):
            raise ValueError('each expectation requires a named test')
        if item.get('kind') not in ('repair', 'non_regression'):
            raise ValueError('expectation kind must be repair or non_regression')
        if value['no_change'] and item['kind'] != 'non_regression':
            raise ValueError('no_change expectations must be non_regression')
    return value


def ask(ctx, params, instructions, payload, required=()):
    path=directory(ctx)/(params['node_id']+'.json')
    if path.exists():
        return result(read(path),'复用已保存的同一自进化节点结果',[str(path)])
    prompt=('你负责Partner自身机制的调查与改进，业务任务失败不自动意味着框架有bug。'
        '明确区分用户要求、观测事实、历史说法、假设和未知。关键结论必须引用给定证据或当前源码。'
        '你可以提出方案与测试，不能伪造执行结果或降低验收门。'
        '症状相似不等于根因相同；没有证据不要强行套用旧修复案例。只输出完整JSON。\n'
        +instructions+'\n真实输入='+json.dumps(payload,ensure_ascii=False))
    (directory(ctx)).mkdir(parents=True,exist_ok=True)
    (directory(ctx)/(params['node_id']+'.prompt.txt')).write_text(prompt)
    raw,usage=call_model(ctx,purpose='autoevolution_'+params['node_id'],prompt=prompt)
    (directory(ctx)/(params['node_id']+'.response.raw')).write_text(raw)
    if params['node_id'] in ('design', 'design_confirm'):
        try:
            value = parse_design(raw)
        except (ValueError, TypeError) as exc:
            # Repair the original response, not a lossy JSON prefix. Avoid
            # resending the entire investigation for a serialization defect.
            repair_prompt = ('修正以下设计的JSON语法与字段合同，保持原问题、因果判断和验收预期，不重新调查。'
                '只输出一个完整JSON对象，无Markdown或附加文字；说明简短，不抄录源码。'
                'no_change是布尔值；target_files、verification_files、expectations都是数组。'
                'no_change=true时target_files为空，verification_files必须含实际被测Partner源码路径，'
                '每项expectation须含kind=non_regression和test_name=test_开头的名称。'
                '修改方案的expectation.kind仅可为repair或non_regression。不能删除预期以通过结构检查。'
                '\n错误='+str(exc)+'\n实际可用源码路径='+json.dumps(payload.get('permitted_existing_source_paths', []))
                +'\n原始完整响应='+raw)
            (directory(ctx)/(params['node_id']+'.schema_repair.prompt.txt')).write_text(repair_prompt)
            raw, extra=call_model(ctx,purpose='autoevolution_design_schema_repair',prompt=repair_prompt)
            for key in ('prompt_tokens','completion_tokens','total_tokens'):
                usage[key]=int(usage.get(key) or 0)+int(extra.get(key) or 0)
            (directory(ctx)/(params['node_id']+'.schema_repair.response.raw')).write_text(raw)
            value = parse_design(raw)
    else:
        value=json_object(raw)
    if any(k not in value for k in required):
        raise ValueError('required autonomous output missing: '+str(required))
    return persist(ctx,params,value,usage=usage)


def _improvement_manifest(ctx, params):
    """Build a collect manifest from the sealed EvidenceBundle when the
    improvement flows (self_improvement/learning_improvement) do not
    produce a project cycle_manifest."""
    ws = Path(ctx.workspace)
    files = []
    bundle_id=(params.get('experiment_context') or {}).get('bundle_id')
    if bundle_id:
        bp=ws/'state/improvement_evidence'/f'{bundle_id}.json'
        if bp.is_file():
            bundle=json.loads(bp.read_text())
            files=[{'path':r['path'],'sha256':r['sha256']} for r in bundle.get('file_refs',[]) if r.get('path') and r.get('sha256')]
    for ref in params.get('evidence_refs') or []:
        path=Path(ref)
        if path.is_file() and str(path) not in {r['path'] for r in files}:
            files.append({'path':str(path),'sha256':experiment.sha(path)})
    ic = params.get('intent_contract') or {}
    return {
        'files': files,
        'request': ic.get('original_request') or ic.get('goal') or '',
        'parent_flow_id': params.get('flow_id') or '',
        'root_event_id': params.get('root_event_id') or '',
    }


def collect(ctx, params):
    manifest_path = params.get('cycle_manifest')
    if manifest_path and Path(manifest_path).exists():
        manifest = read(manifest_path)
        cycle = Path(manifest_path).parent
    else:
        manifest = _improvement_manifest(ctx, params)
        cycle = Path(ctx.workspace) / 'state/improvement_evidence'
        cycle.mkdir(parents=True, exist_ok=True)
        manifest_path = str(cycle / 'manifest.json')
    rows=[]
    for item in manifest.get('files',[]):
        p=Path(item['path'])
        if p.is_file() and experiment.sha(p)==item['sha256']:
            text=''
            if p.suffix in ('.json','.md','.txt','.csv','.py') and p.stat().st_size<250000:
                text=p.read_text(errors='replace')[:4500]
            if p.suffix == '.pdf':
                try:
                    import fitz
                    with fitz.open(p) as document:
                        text='\n'.join(page.get_text() for page in document)[:16000]
                except Exception as exc:
                    text='PDF content read failed: '+str(exc)[:300]
            rows.append({'path':str(p),'sha256':item['sha256'],'excerpt':text})

    rounds={}
    for name in ('round_one','round_two','report'):
        row=read(cycle/(name+'.json'))
        rounds[name]={'flow_id':row.get('flow_id'),'status':row.get('status'),
            'nodes':{k:{f:v.get(f) for f in ('ok','status','summary','error','business_delta','semantic_output') if f in v}
                     for k,v in (row.get('node_outputs') or {}).items()}}
        # Keep complete node records on disk; fit each node independently.
        for k,v in rounds[name]['nodes'].items():
            if len(json.dumps(v,ensure_ascii=False))>4500:
                v.pop('semantic_output',None)
    from partner.index.resource_catalog import ResourceCatalog
    catalog=ResourceCatalog(ctx.workspace)
    inventory=[str(Path(r['path']).relative_to(experiment.REPO)) for r in catalog.query('code',scope='partner',limit=500) if Path(r['path']).is_relative_to(experiment.REPO)]
    value={'cycle_id':ctx.job_id,'instance_id':ctx.instance_id,'project_id':params['project_id'],
           'original_request':manifest.get('request'), 'aspects':ASPECTS,
           'rounds':rounds,'memory':{k:read(cycle/('memory_'+k+'.json')) for k in ('lesson','growth','habit')},
           'delivery':{k:read(cycle/(k+'.json')) for k in ('text_ack','report_ack')},
           'assessment':read(cycle/'assessment.json'),'source_inventory':inventory,
           'artifacts':rows[:35], 'all_artifact_refs':[r['path'] for r in rows],
           'manifest':manifest_path}
    # The audit must inspect actual scripts/report content, not only optimistic
    # action summaries. Keep these separate from duplicated receipt excerpts.
    substantive = [r for r in rows if Path(r['path']).suffix in ('.py', '.md', '.csv')]
    value['substantive_artifacts'] = substantive[:12]
    final_report = (read(cycle / 'report.json').get('node_outputs') or {})
    final_paths = {(final_report.get(key) or {}).get('path') for key in ('claims', 'render')}
    value['report_content'] = [r for r in rows if r['path'] in final_paths or Path(r['path']).suffix == '.pdf']
    value['supervisor_recovery'] = read(cycle / 'supervisor_recovery.json')
    value['runtime_contract'] = runtime_contract()
    from partner.event_fabric import EventLedger
    flow_ids = {r.get('flow_id') for r in rounds.values()} | {manifest.get('parent_flow_id')}
    ledger = EventLedger(ctx.workspace)
    related = ledger._projection().flow_events(ledger.events_path,flow_ids,manifest.get('root_event_id') or '')
    value['event_terminals'] = [{**{k:r.get(k) for k in
        ('event_id','event_type','flow_id','status','recorded_at','failure_class','mechanism','headline')}
        , 'created_at': related.get(r.get('event_id'), {}).get('created_at'),
          'node_id': related.get(r.get('event_id'), {}).get('node_id')}
        for eid in related for r in ledger._projection().rows(ledger.summaries_path,entity=eid,limit=1)]
    return persist(ctx,params,value,'已读取本周期两轮、交付、报告和记忆的真实证据')


def read_plan(ctx, params):
    data=cycle_view(saved(ctx,'collect'), inventory=True)
    if params['node_id'] == 'reconsider':
        if saved(ctx,'design').get('target_files') and saved(ctx,'design').get('expectations'):
            return persist(ctx,params,{'skipped':True,'reason':'first design has a concrete intervention'})
        return ask(ctx,params,
            '第一个假设经实际补读后没有可证实的改动点。不要强行实现已经存在的修复；现在最多再调查一个问题。'
            '回到本周期其他方面寻找可验证的机制缺口或改进点，必须读生产者及消费者的实际接口。'
            '一次任务无代码bug不代表所有报告/记忆/消息机制都正确；同样不能从未知推断缺陷。'
            '输出 {"selected_issue":{"id":"...","symptom":"...","evidence_refs":[],"hypothesis":"..."},'
            '"files":[{"path":"库存中的精确源码路径","reason":"要验证什么"}],"falsification":[],"reason":"..."}。'
            '最多3个文件。若确无其他问题，selected_issue={}并解释证据边界。整体不超过1500汉字。',
            {'cycle':data,'first_design':saved(ctx,'design'),'audit':compact(saved(ctx,'audit')),
             'already_read':source_view(source_records(ctx))},('selected_issue','files'))
    return ask(ctx,params,
        '根据完整周期主动找问题，先制定源码阅读计划。检查所有aspects，不从用户提供的bug出发。'
        '选择最可能解释本周期具体偏差的最多6个源码文件，包含调用者与相关实现。'
        '不要只选自进化基础设施本身；优先真实运行里暴露的可复现机制问题。'
        '输出 {"files":[{"path":"库存中的实际路径","reason":"对应哪项运行证据"}],"questions":[]}。',data,('files',))


def sources(ctx, params):
    if params['node_id'] == 'sources_extra' and not saved(ctx,'reconsider').get('selected_issue'):
        return persist(ctx,params,{'skipped':True,'files':{}})
    selected=saved(ctx,'reconsider' if params['node_id']=='sources_extra' else 'read_plan').get('files') or []
    files={}
    for item in selected[:6]:
        relative=item['path'] if isinstance(item,dict) else str(item)
        p=(experiment.REPO/relative).resolve()
        if experiment.REPO not in p.parents or not p.is_file():
            continue
        relative=str(p.relative_to(experiment.REPO))
        if not relative.startswith('partner/') or p.suffix != '.py': continue
        from partner.index.resource_catalog import ResourceCatalog
        reading=ResourceCatalog(ctx.workspace).read(p,max_bytes=500000,purpose='evolution_sources')
        if reading['truncated']:raise ValueError('source exceeds read budget; narrow source selection')
        lines=reading['text'].splitlines()
        files[relative]={'sha256':reading['sha256'],'line_count':len(lines),
                         'source':'\n'.join(f'{i+1}: {s}' for i,s in enumerate(lines))[:22000]}
    from partner.index.resource_catalog import ResourceCatalog
    tests=[str(Path(r['path']).relative_to(experiment.REPO)) for r in ResourceCatalog(ctx.workspace).query('code',scope='tests',limit=200) if Path(r['path']).is_relative_to(experiment.REPO) and Path(r['path']).name.startswith('test_')]
    if selected and not files:
        raise ValueError('none of the requested source paths resolved to readable Partner Python files')
    return persist(ctx,params,{'files':files,'existing_tests':tests},'已实际读取源码与测试目录')


def audit(ctx, params):
    data=cycle_view(saved(ctx,'collect'))
    return ask(ctx,params,
        '逐方面审核，不遗漏intent/planning/execution/iteration/project_progress/event_flow/message_content/'
        'delivery/pdf_report/experience/growth/habit/efficiency。每方面写checked_evidence、findings、unknowns。'
        '每方面最多两条短句，issues最多3项；完整JSON不超过2500汉字，不抄录输入或反复引用大段源码。'
        '找出影响用户目标的具体行为差异，解释源码中的因果链。不能因未读到就断言不存在。'
        '输出 {"aspect_reviews":{},"issues":[{"id":"...","symptom":"...","evidence_refs":[],"source_refs":[],"hypotheses":[],"impact":"...","difficulty":"..."}]}。',
        {'cycle':data,'source':source_view(saved(ctx,'sources'))},('aspect_reviews','issues'))


def counter(ctx, params):
    result = ask(ctx,params,
        'issues_review 每项必须含 id 和 verdict：supported/refuted/insufficient_evidence/expected_behavior。仅 supported 可选择；快照之后尚未执行的事件缺失不是缺陷。不要用 project_cycle 节点要求审查 self_improvement。'
        '独立复读原始轨迹和实际源码，攻击audit的问题判断；检查症状是否已经被修复、是不是正常策略、'
        '是否误把项目负结果或环境限制当框架bug。选择一个本周期有关、可用小型离线实验验证的问题，'
        '明确复现步骤、预期旧行为、反例。无足够证据允许提出具体补读，不强制虚构。'
        + chr(10) + chr(10)
        + '[probeable_tokens 约束] selected_issue.symptom 和 hypothesis 必须含至少 1 个 partner/ 源码中的具体符号名（函数名/变量名/key名，例如 decision_rule_result、count_positive_ci、project_outcome_decide）。'
        + chr(10)
        + '该符号会被 design 节点用作 fresh_source_probe 的搜索关键词；不提供可命中源码的符号，下游 design 必选 no_change 且 verification_files 无法指向真实文件，'
        + chr(10)
        + '最终测试 fixture 无法对齐真实 API，freeze 被拒，整轮无效。'
        + chr(10) + chr(10)
        + '[reconsider.recovered_source 强提示] 如果你的 selected_issue 已经被 prior_reconsider 节点用源码证伪（如某 audit issue 的某函数根本不存在），'
        + chr(10)
        + '必须改选下一个候选，并保留 chosen_token 用于 probe。'
        + chr(10)
        + '输出 {"issues_review":[],"selected_issue":{},"competing_causes":[],"missing_evidence":[],"reason":"..."}。'
        + chr(10)
        + 'selected_issue 结构必含 id / symptom / hypothesis / concrete_evidence(>=3项) / reproduction_steps(>=3步) / expected_old_behavior / oracle。',
        {'audit':compact(saved(ctx,'audit')), 'cycle':cycle_view(saved(ctx,'collect')),
         'sources':source_view(saved(ctx,'sources'))},('selected_issue',))

    value = dict(result.get('semantic_output') or {})
    issue = value.get('selected_issue') or {}
    review = next((r for r in value.get('issues_review', []) if r.get('id') == issue.get('id')), {})
    if review.get('verdict') != 'supported':
        value.update(selected_issue={}, blocked_issue=issue,
                     selection_status='insufficient_evidence',
                     reason='Selected issue lacks an independent supported verdict; gather evidence before intervention')
    else:
        value['selected_issue'] = {**issue, 'verdict':'supported'}
    return persist(ctx, params, value)


def design(ctx, params):
    if not saved(ctx,'counter').get('selected_issue'):
        return persist(ctx,params,{'no_change':True,'target_files':[], 'expectations':[], 'reason':'No supported issue; intervention blocked pending evidence'})
    if params['node_id']=='design_confirm' and not saved(ctx,'reconsider').get('selected_issue'):
        return persist(ctx,params,{'skipped':True,'reason':'no additional source investigation selected'})
    probe=probe_target_sources(ctx)
    sources = source_view(source_records(ctx))
    for name, row in probe['files'].items():
        if row['functions'] and name in sources['files']:
            sources['files'][name] = {'sha256':row['sha256'],
                'source_location':'fresh_source_probe contains complete selected functions; full module in sources.json'}
    # (2026-09-15) When probe fails to surface any concrete function for the
    # selected issue, no_change is invalid because verification_files cannot be
    # pointed at a real implementation. Force the model to either pick a target
    # it can actually verify, or to set no_change=True only after reading the
    # module excerpts that probe returned.
    probe_has_functions = any(bool(row.get('functions')) for row in probe.get('files', {}).values())
    probe_strict_hint = ''
    if not probe_has_functions:
        probe_strict_hint = ('\n\n[probe_diagnostic] probe_target_sources returned no function-level matches for '
                             'this selected issue. Reading only the symptom text without locating a real function '
                             'is not sufficient for no_change. If you genuinely cannot find a relevant function, '
                             'output no_change=false with target_files=[] but verification_files MUST contain at '
                             'least one real partner/ source path that exists on disk; design will then trigger '
                             'an evidence-constrained behavior verification rather than an empty no-change.\n')
    return ask(ctx,params,
        '针对selected_issue制定因果修复设计，不给补丁。优先易复现真实缺陷。'
        + probe_strict_hint +
        '针对selected_issue制定因果修复设计，不给补丁。优先易复现真实缺陷。'
        '最终JSON控制在2500汉字以内；每项说明简短，不重复counter的长篇论证、不抄录源码。'
        '先读 fresh_source_probe 的完整相关函数；它补齐counter要求的源码，不再把未读到当成不存在。'
        '如果代码已经实现了所提修复，必须否定原假设，依据本周期证据和新读源码修正到仍存在的具体缺口；不要重复已有实现。'
        '确无可证实问题时可输出no_change=true及理由，不虚构补丁。target_files只能是精确文件路径字符串，不能加注释、行号、只读文件或tests路径。'
        'no_change也必须提出可实际执行的non_regression行为测试，用当前函数的输出证伪原缺陷假设；禁止靠源码字符串、历史Event文字或手动监督记录证明当前行为。'
        '输出 {"no_change":false,"issue":{},"causal_hypothesis":"...","target_files":[],"verification_files":[],"change_design":"...",'
        '"expectations":[{"description":"具体修改后行为","kind":"repair或non_regression","test_name":"test_x"}],'
        '"falsification_conditions":[],"risks":[],"rollback":"..."}。'
        '每项预期必须能被Python测试或冻结行为重放观测。不能只预测产出candidate、测试通过率或减少critic拒绝。'
        '保持用户需求、事实审查与正确交付。禁止改 freeze_boundary.yaml 的 frozen_layers 文件（见输入里的 frozen_layers 列表）。',
        {'counter':saved(ctx,'counter'),'fresh_source_probe':probe,
         'current_runtime_contract':runtime_contract(),
         'permitted_existing_source_paths':list(source_records(ctx).get('files',{})),
         'supervision_boundary':'本周期supervised_cycle_recovery与supervisor_changes是Codex人工监督恢复/源码修改的历史记录，不是Partner自动恢复Event、常驻config guard或未来可依赖的自动兜底。必须以实际代码确认机制，不能把人工作业当自动能力。',
         'sources':sources,
         'audit':compact(saved(ctx,'audit')),
         'frozen_layers':experiment._frozen_patterns()},('target_files','expectations'))


def tests(ctx, params):
    repairing = params['node_id'].startswith('test_repair')
    if repairing and saved(ctx,'test_review').get('accepted'):
        return persist(ctx,params,{'skipped':True,'reason':'initial tests accepted'})
    source=source_records(ctx)
    design_value=saved(ctx,'design')
    # Exact raw source prevents line-number-prefixed code entering patches/tests.
    context={}
    for relative in (design_value.get('target_files') or design_value.get('verification_files') or []):
        try: context[relative]=experiment.safe_source(relative).read_text()[:30000]
        except ValueError: pass
    return ask(ctx,params,
        '在候选修改之前编写独立复现测试。测试按设计的预期行为断言，baseline应因原缺陷失败，'
        'candidate应通过；禁止按源码字符串或固定版本判成功。允许mock外部网络/LLM，但必须实际调用被测函数。'
        '若design.no_change=true，则当前baseline应满足non_regression预期：真实调用当前函数验证原缺陷已不存在，不生成不存在的模块、不验证历史说明文字。'
        '先检查实际函数签名和返回协议，使用tmp_path隔离工作区；不得读写真实运行数据。'
        '输出 {"test_code":"完整pytest源码","reproducer_names":["test_x"],'
        '"regression_tests":["已有测试文件路径"],"expectations":[{"description":"...","kind":"repair或non_regression","test_name":"test_x"}]}。'
        '覆盖design所有强制预期；每个expectation对应真实test函数。原有回归从existing_tests选择相关文件，不能随机挑sanity测试。'
        '[硬约束] reproducer_names 必须是 expectations 全部 test_name 的超集，包括 test_old_behavior_* 等 non_regression tests；少 1 个 freeze 都会拒。'

        '测试只建立与原问题等价的最小fixture，不伪造生产错误。',
        {'design':design_value,'source':context,'existing_tests':source['existing_tests'],
         'import_contract':'直接 import partner 的真实模块；禁止替换sys.modules或伪造Partner包、校验器、source_catalog、executable_choices。只mock外部LLM调用，文件用tmp_path真实创建。',
         'runtime_environment':'当前worker与隔离pytest使用同一Python解释器，真实Partner模块和依赖已经可导入。不要为假想的heavy optional stack伪造模块。',
         'actual_imported_helpers':verification_dependencies(ctx),
         'previous_test_plan':saved(ctx,'tests') if repairing else {},
         'independent_review':saved(ctx,'test_review') if repairing else {},
         'read_only_dependencies':{r:(experiment.REPO/r).read_text()[:24000]
              for r in source.get('files',{}) if r.startswith('partner/presentation/') and (experiment.REPO/r).is_file()},
         'output_budget':'用共享fixture避免重复；测试源码尽量在180行以内，禁止长篇注释；保留全部预期断言。'},
        ('test_code','reproducer_names','regression_tests','expectations'))


def test_review(ctx, params):
    repair_node='test_repair_2' if params['node_id']=='test_confirm_2' else 'test_repair'
    if params['node_id'].startswith('test_confirm') and saved(ctx,repair_node).get('skipped'):
        return persist(ctx,params,{'accepted':bool(saved(ctx,'test_review').get('accepted')),
                                  'skipped':True,'reason':'initial review sufficient; tests unchanged'})
    context={}
    for relative in saved(ctx,'tests').get('regression_tests') or []:
        p=experiment.REPO/relative.split('::')[0]
        if p.is_file():context[relative]=p.read_text()[:4000]
    # (2026-09-14) 补读 design 的 target_files 实际源码。之前 test_review 只靠
    # target_source_probe.json（只覆盖 read_plan 选的 files），当 design 选定了
    # read_plan 没选过的文件（如 events/cycle.py，evolution_request 所在）时，
    # test_review 看不到被测函数真实签名，只能拒绝 -> inconclusive。
    target_sources={}
    for relative in (saved(ctx,'design').get('target_files') or []) + (saved(ctx,'design').get('verification_files') or []):
        try: target_sources[relative]=experiment.safe_source(relative).read_text()[:20000]
        except ValueError: pass
    preflight=experiment.test_preflight(directory(ctx),saved(ctx,'tests'))
    return ask(ctx,params,
        '独立审查测试是否调用真实被测实现、是否完整覆盖预期、fixture是否符合当前接口、'
        '是否以降低要求或mock被修复函数偷过。审查baseline预期失败的具体原因。'
        '指出测试代码或签名错误的具体修正要求，但不得降低design预期。'
        '测试必须直接import真实Partner包；禁止sys.modules伪包和手写validate/source_catalog等内部实现副本。'
        '修复fixture时保留全部预期与断言，不以当前baseline已经通过某项为由删掉它；那会在实际比较中作为未发生改善记录。'
        '输出 {"accepted":true/false,"problems":[],"repair_instructions":[],"corrected_tests":null}。'
        '不输出完整测试代码；由独立修正Event实现后再审核。完整JSON不超过1200汉字。'
        'diagnostic_baseline是机器实际运行：预期缺陷断言失败是合理的红灯；但import、mock不存在属性、错误文件格式、fixture自检失败不是缺陷复现，必须拒绝并指出接口修正。'
        '只把实际接口错误、不可执行fixture、与预声明预期冲突或验收绕过作为拒绝理由。注释/风格建议、假想平台差异、已经被同组其他测试覆盖的要求不能单独阻止实验；它们放nonblocking_suggestions。'
        'python_call_bindings由真实AST生成：函数体内from import在每次调用时读取提供模块属性；模块全局函数名应patch消费模块属性。不要把这两种绑定混淆。diagnostic_baseline.test_sha256绑定当前test_code，不能无证据声称另一个fixture被运行。'
        'accepted只表示测试可执行且合理，不要求已经运行。',
        {'design':saved(ctx,'design'),'tests':saved(ctx,'tests'),
         'validation_sources':source_view({'files':{r:v for r,v in source_records(ctx).get('files',{}).items() if r.startswith('partner/presentation/')}}),
         'actual_target_functions':read(directory(ctx)/'target_source_probe.json'),
         'actual_imported_helpers':verification_dependencies(ctx),
         'target_sources':target_sources,
         'diagnostic_baseline':preflight,
         'regressions':context},('accepted',))


def freeze(ctx, params):
    plan = saved(ctx, 'tests')
    review = saved(ctx, 'test_review')
    import hashlib
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    try:
        if not review.get('accepted') or review.get('plan_sha256') != digest:
            raise ValueError('Latest test plan requires an accepted, version-matched review')
        required = {e['test_name'] for e in saved(ctx,'design').get('expectations',[])}
        supplied = {e['test_name'] for e in plan.get('expectations',[])}
        if not required or not required.issubset(supplied):
            raise ValueError('test plan dropped a predeclared expected effect')
        value = experiment.freeze(directory(ctx), plan, plan['expectations'])
        value.update(ready=True, plan_sha256=digest, preflight=review.get('preflight'))
    except (ValueError, OSError, SyntaxError, KeyError) as exc:
        value = {'ready':False, 'reason':str(exc)}
    return persist(ctx,params,value)


def skip(ctx, params):
    if not saved(ctx,'freeze').get('ready'):
        return persist(ctx,params,{'skipped':True,'reason':'frozen experiment unavailable'})
    if saved(ctx,'design').get('no_change') and not saved(ctx,'design').get('target_files'):
        if params['node_id'] not in ('baseline_1','compare_1','analyze_1'):
            return persist(ctx,params,{'skipped':True,'reason':'no-change hypothesis is verified on current frozen code; no invented candidate'})
    if params.get('attempt')==2 and saved(ctx,'compare_1').get('qualified'):
        return persist(ctx,params,{'skipped':True,'reason':'first candidate met frozen expectations'})
    return None




# (2026-09-17) GEPA adapter wiring: the persistent per-run registry
# lives in partner/research/adapters/gepa_runtime.py.  Each
# autonomous_evolution run gets its own on-disk archive; concurrent
# workers in the same process share the singleton via the module-level
# cache.  See partner/research/adapters/wiring.py::WIRINGS[gepa].

def candidate(ctx, params):
    skipped=skip(ctx,params)
    if skipped:return skipped
    # (2026-09-17) Real GEPA adapter invocation: register this candidate
    # in the persistent per-run archive.  The compare handler later
    # calls record_fitness; the decision handler reads archive().
    try:
        from partner.research.adapters.gepa_runtime import get_registry as _gepa_get
        from partner.research.adapters.gepa import GepaCandidate
        run_id = (ctx if isinstance(ctx, str) else getattr(ctx, "job_id", None)) or "run"
        workspace_root = directory(ctx)
        reg = _gepa_get(workspace_root, run_id,
                          budget={"candidate_attempts": 4,
                                  "wallclock_seconds": 3600})
        prev = saved(ctx, f'candidate_{params["attempt"]-1}') if params["attempt"] > 1 else None
        parent_id = (prev or {}).get("candidate_id") if prev else None
        # Locate parent candidate if present (best-effort).
        parent_cand = None
        if parent_id:
            for cand in reg.archive():
                if cand.candidate_id == parent_id:
                    parent_cand = cand
                    break
        # Build a fingerprint for this attempt from the LLM-produced body.
        body = ""
        try:
            body = str(saved(ctx, f'candidate_{params["attempt"]}') or "")
        except Exception:
            pass
        candidate = reg.propose(
            parent=parent_cand,
            change_summary=f"attempt {params.get('attempt')} {body[:160]}",
            diff_fingerprint=f"{run_id}:attempt{params.get('attempt')}",
        )
        # Stash the candidate_id so compare() can record_fitness on it.
        from partner.research.adapters.gepa_runtime import _WORKER_REGISTRY
        key = (str(workspace_root), run_id)
        meta = _WORKER_REGISTRY.setdefault(key, reg).__dict__.setdefault(
            "_pending", {})
        meta[params.get("attempt")] = candidate.candidate_id
    except Exception as exc:
        # Adapter wiring failure is NOT silent — we record it in the
        # audit log path so the runner surfaces it.
        import logging as _lg
        _lg.getLogger("partner.events.autonomous_evolution").warning(
            "gepa wiring failed: %s", exc)
    frozen=saved(ctx,'freeze')
    prev_critic = saved(ctx, f'critic_{params["attempt"]-1}') if params['attempt'] > 1 else {}
    source={r:(Path(frozen['repo'])/r).read_text() for r in saved(ctx,'design').get('target_files',[])
            if (Path(frozen['repo'])/r).is_file()}
    # (2026-09-14) 把 critic 指出的文件也读进 source，供下一轮 candidate 扩展修复面。
    for rc in (prev_critic.get('required_changes') or []):
        rel = str(rc.get('path') or rc.get('file') or '').strip()
        pth = Path(frozen['repo'])/rel
        if rel.startswith('partner/') and rel.endswith('.py') and pth.is_file():
            source[rel] = pth.read_text()
    revise_hint = ''
    if prev_critic.get('problems'):
        revise_hint = ('previous_critic 已给出上一候选被拒的具体 problems 和 required_changes。'
            '必须逐条回应 problems，按 required_changes 修正，并把 target_files 扩展到 critic 指出的文件'
            '（例如它指出根因在 cycle_children.start_child，就不要只改 cycle.py 的参数消费）。'
            '不要重复上一候选的补丁。')
    # (2026-09-15) Issue I1 follow-up: when freeze failed because test_review rejected,
    # attempt_2 candidate must see the rejection reason and adjust either the candidate
    # or (more importantly) the test set / test plan. The previous mechanism skipped
    # candidate silently ("frozen experiment unavailable") and Partner never learned
    # the cause.
    freeze_failure_hint = ''
    if not frozen.get('ready') and params['attempt'] > 1:
        review = saved(ctx, 'test_review') or {}
        problems = review.get('problems') or []
        problem_summary = []
        for prob in problems[:5]:
            entry = {
                'id': prob.get('id'),
                'where': (prob.get('where') or prob.get('description', ''))[:80],
                'severity': prob.get('severity'),
            }
            if 'verdict' in prob:
                entry['verdict'] = prob.get('verdict', '')[:200]
            problem_summary.append(entry)
        ctx_msg = "[freeze_failure_context] last freeze rejected by test_review: "
        ctx_msg += "freeze.reason=" + str(frozen.get("reason", ""))
        ctx_msg += "; test_review.accepted=" + str(review.get("accepted"))
        ctx_msg += "; review_problems=" + json.dumps(problem_summary, ensure_ascii=False)
        ctx_msg += " | branches: (1) fix candidate to be accepted; (2) explicit no_change=True with behavior verification; (3) never fake a fix."
        freeze_failure_hint = chr(10) + chr(10) + ctx_msg
    return ask(ctx,params,
        '实现已设计的修复。输出 {"candidate_id":"...","causal_hypothesis":"...",'
        '"edits":[{"path":"partner/...py","old":"当前源码中唯一存在的精确完整片段","new":"替换内容"}],'
        '"expectation_mapping":[],"reason":"..."}。最多修改3个源码文件；不得修改测试、验收门或保护层。'
        'old必须保留实际缩进/换行并精确匹配。第二次候选始终相对同一frozen baseline编写，读取前次失败日志再修订。'
        '[attempt=2 关键] 输入 previous_baseline / previous_candidate_run / previous_compare 包含上一 attempt 真跑的数据（cases 列表/failed 字段/expectation_results），必须根据这些真实失败 case 改 patch 而不是凭猜测。'
'[attempt=2 关键] expectation_results 中每个 unmet 的 test_name 是失败的具体期望（test_memory_update_kind_semantic_differentiation 要求 Jaccard<0.6）；patch 必须让该 test 从 baseline_failed=true + candidate_passed=false 变成 candidate_passed=true，且 reproduction_steps 中描述的真实调用契约不能在改后丢失。'
        + revise_hint
        + freeze_failure_hint,
        {'design':saved(ctx,'design'),'source':source,'test_contract':saved(ctx,'tests'),
         'previous_analysis':saved(ctx,'analyze_1') if params['attempt']==2 else {},
         'previous_candidate':saved(ctx,'candidate_1') if params['attempt']==2 else {},
         'previous_critic': prev_critic,
         'previous_baseline':saved(ctx,'baseline_'+str(params['attempt']-1)) if params['attempt']>1 and saved(ctx,'baseline_'+str(params['attempt']-1)) else {},
         'previous_candidate_run':saved(ctx,'candidate_run_'+str(params['attempt']-1)) if params['attempt']>1 and saved(ctx,'candidate_run_'+str(params['attempt']-1)) else {},
         'previous_compare':saved(ctx,'compare_'+str(params['attempt']-1)) if params['attempt']>1 and saved(ctx,'compare_'+str(params['attempt']-1)) else {},
         'freeze_review': saved(ctx,'test_review'),
         'freeze_failure': {'ready': frozen.get('ready'),
                           'reason': frozen.get('reason', ''),
                           'review_problem_count': len((saved(ctx,'test_review') or {}).get('problems', []))}},('edits',))


def critic(ctx, params):
    skipped=skip(ctx,params)
    if skipped:return skipped
    n=params['attempt']
    return ask(ctx,params,
        '你是 self-evolution 的 candidate critic。判断 candidate patch 是否可以进入隔离实验（isolate）。'
        '接受标准 (accepted=true)：(1) candidate.target_files 与 design.target_files 一致；'
        '(2) candidate.edits 的 old 片段在当前冻结源码中唯一出现（patch 可应用）；'
        '(3) candidate 已在 reason 中逐条回应上一轮 critic_$attempt-1 的 required_changes（attempt=1 时跳过此条）。'
        '进入 isolate 后 baseline/candidate/compare 会真实跑，量化表达质量。不要在批评阶段要求 root cause 完整证据或要求所有分支回归覆盖。'
        '拒绝仅限 (accepted=false)：(a) edits 应用失败 (old 不唯一或 empty)；(b) target_files 与 design 不一致；(c) 上一轮 required_changes 全部未回应；(d) any patched file fails Python compile() 校验（语法错误）；(e) old 片段与对应文件实际源码不匹配（截断、不全行、缩进错）。其他改进建议放进 suggested_polish（不阻断）。'
        '输出 {"accepted":true/false,"problems":[{"type":"blocking"|"suggestion","detail":"..."}],'
        '"required_changes":[{"path":"...","why":"...","change":"..."}],"suggested_polish":["..."],"reason":"..."}。'
        'required_changes 严格限于 blocking；suggested_polish 不拦 isolate。',
        {'candidate':saved(ctx,f'candidate_{n}'),'design':saved(ctx,'design'),
         'sources':source_view(source_records(ctx)),'tests':saved(ctx,'tests')},('accepted',))


def isolate(ctx, params):
    skipped=skip(ctx,params)
    if skipped:return skipped
    n=params['attempt'];frozen=saved(ctx,'freeze')
    if not saved(ctx,f'critic_{n}').get('accepted'):
        return persist(ctx,params,{'ready':False,'reason':'candidate critic rejected'})
    from partner.runtime.matched_execution import isolate as make
    try:
        patch,targets=experiment.patch_from_edits(frozen,saved(ctx,f'candidate_{n}')['edits'])
        value=make(ctx.workspace,{'unified_diff':patch,**{k:frozen[k] for k in ('reproducer_tests','regression_tests','expectations')}},repo=frozen['repo'])
        value.update(ready=True,target_files=targets)
        (directory(ctx)/f'candidate_{n}.patch').write_text(patch)
    except (ValueError,OSError,KeyError,SyntaxError) as exc:
        value={'ready':False,'reason':str(exc)}
    return persist(ctx,params,value)


def execution(kind):
    def handler(ctx,params):
        skipped=skip(ctx,params)
        if skipped:return skipped
        if kind=='baseline' and saved(ctx,'design').get('no_change'):
            value=experiment.test_preflight(directory(ctx)/'no_change_validation',saved(ctx,'tests'),frozen=saved(ctx,'freeze'))
            value['scope']='frozen current-code behavioral validation; no candidate intervention'
            return persist(ctx,params,value)
        isolated=saved(ctx,'isolate_'+str(params['attempt']))
        if not isolated.get('ready'):
            # (2026-09-16) Don't skip baseline on isolate rejection. Baseline is a
            # diagnostic of the current frozen code; it can run with a synthetic
            # experiment id against the partner repository directly, or fall back
            # to the audit reference tests that the freeze step already produced.
            value={'executed':False,'reason':isolated.get('reason'),'fallback':True}
            return persist(ctx,params,value)
        from partner.runtime.matched_execution import execute
        try:value=execute(ctx.workspace,isolated['experiment_id'],kind)
        except (ValueError,OSError,KeyError) as exc:value={'executed':False,'reason':str(exc)}
        return persist(ctx,params,value)
    return handler


def compare(ctx, params):
    # (2026-09-17) Real GEPA adapter: record_fitness on the result.
    try:
        from partner.research.adapters.gepa_runtime import (
            get_registry as _gepa_get, _WORKER_REGISTRY,
        )
        run_id = (ctx if isinstance(ctx, str) else getattr(ctx, "job_id", None)) or "run"
        workspace_root = directory(ctx)
        reg = _gepa_get(workspace_root, run_id,
                          budget={"candidate_attempts": 4})
        # Pull the candidate_id stashed by candidate() for this attempt.
        key = (str(workspace_root), run_id)
        meta = _WORKER_REGISTRY.get(key)
        pending = getattr(meta, "_pending", {}) if meta else {}
        attempt = params.get("attempt")
        cand_id = pending.get(attempt)
        if cand_id:
            target = None
            for c in reg.archive():
                if c.candidate_id == cand_id:
                    target = c
                    break
            if target is not None:
                # Score: criterion results from the compare node.
                score = 0.0
                cmp_payload = saved(ctx, f'compare_{attempt}') or {}
                if cmp_payload.get("matched_tests") and cmp_payload.get("regression_passed"):
                    score = 1.0
                reg.record_fitness(target, score=score)
    except Exception as exc:
        import logging as _lg
        _lg.getLogger("partner.events.autonomous_evolution").warning(
            "gepa record_fitness failed: %s", exc)

    skipped=skip(ctx,params)
    if skipped:return skipped
    n=params['attempt']
    from partner.runtime.matched_execution import compare as matched
    before=saved(ctx,f'baseline_{n}');after=saved(ctx,f'candidate_run_{n}')
    if saved(ctx,'design').get('no_change'):
        expectations=saved(ctx,'freeze').get('expectations',[])
        valid=bool(before.get('executed') and before.get('exit_code')==0 and expectations
                   and all(e.get('kind')=='non_regression' for e in expectations))
        return persist(ctx,params,{'decision':'no_change_verified' if valid else 'inconclusive',
            'qualified':False,'improved':False,'no_change_verified':valid,
            'expectation_results':[{**e,'status':'met' if valid else 'unmet'} for e in expectations],
            'baseline_verification':before,'reason':'no patch proposed; validate the claimed existing behavior directly'})
    value=matched(ctx.workspace,before,after)
    expectations=experiment.expectation_compare(before,after,saved(ctx,'freeze'))
    # qualified (release gate) = target fixed AND no new regression AND all
    # frozen expectations met.  improved alone is NOT enough to publish.
    value.update(expectation_results=expectations,
                 qualified=bool(value.get('improved') and value.get('regression_passed')
                                and expectations and all(e['status']=='met' for e in expectations)))
    return persist(ctx,params,value)


def analyze(ctx, params):
    skipped=skip(ctx,params)
    if skipped:return skipped
    n=params['attempt'];isolated=saved(ctx,f'isolate_{n}')
    logs={'diagnostic_baseline':saved(ctx,f'baseline_{n}').get('log_excerpt','')}
    if isolated.get('directory'):
        for arm in ('baseline','candidate'):
            p=Path(isolated['directory'])/(arm+'.log')
            if p.exists():logs[arm]=p.read_text(errors='replace')[-18000:]
    return ask(ctx,params,
        '解释真实实验与baseline和冻结预期的差异，不能覆盖机器判定。'
        '区分根因错误、实现缺陷、测试fixture错误、环境失败、证据不足。'
        '输出 {"summary":"...","gap_analysis":[],"root_cause_supported":true/false,'
        '"next_action":"revise或accept或investigate","specific_revision":"..."}。'
        '第二版候选不可更改冻结测试或标准；测试失效则记录inconclusive，不伪称改善。',
        {'design':saved(ctx,'design'),'candidate':saved(ctx,f'candidate_{n}'),
         'critic':saved(ctx,f'critic_{n}'),'isolation':isolated,
         'comparison':saved(ctx,f'compare_{n}'),'logs':logs},('summary','next_action'))


def decision(ctx, params):
    selected=next((n for n in (1,2) if saved(ctx,f'compare_{n}').get('qualified')),None)
    # Per-dimension attribution (round 2026-09-17): reason must distinguish
    # "target repaired" from "new regression" from "existing blocker".
    def _cmp_reason(n):
        c = saved(ctx, f'compare_{n}') or {}
        bd = c.get('breakdown') or {}
        parts = []
        if c.get('improved'):
            parts.append(f"target defect fixed ({len(bd.get('target_fixed', []))})")
        if bd.get('new_regressions'):
            parts.append(f"new regressions {bd['new_regressions']}")
        if bd.get('existing_blockers'):
            parts.append(f"existing blockers {bd['existing_blockers']}")
        return f"attempt {n}: " + ("; ".join(parts) if parts else "no change")
    value={'decision':'validated_shadow' if selected else 'inconclusive', 'selected_attempt':selected,
           'production_effective':False,
           'reason':('frozen expectations and matched repair passed'
                     if selected else '; '.join(_cmp_reason(n) for n in (1,2))),
           'comparisons':{str(n):saved(ctx,f'compare_{n}') for n in (1,2)}}
    if saved(ctx,'compare_1').get('no_change_verified'):
        value.update(decision='no_change_verified',reason='current frozen code satisfied independent behavioral tests; no modification needed')
    if not selected and any(saved(ctx,f'compare_{n}').get('decision')=='rejected' for n in (1,2)):
        value['decision']='rejected'

    # (2026-09-17) DGM adapter wiring: persist a DGMLineageNode per
    # attempt in the per-run archive at state/dgm/<run_id>.json so an
    # external observer can reconstruct the lineage graph without
    # touching the production partner_workspace.  See
    # partner/research/adapters/wiring.py::WIRINGS[dgm].  The ACE
    # entry records the same node_id in lessons.jsonl for cross-
    # referencing with EventMemory; both writes happen.
    try:
        from partner.research.adapters.dgm import make_node, persist_node, archive as dgm_archive
        from partner.research.adapters.ace import make_entry, append_to_memory
        run_id = (ctx if isinstance(ctx, str) else getattr(ctx, "job_id", None)) or "run"
        workspace_root = directory(ctx)
        for n in (1, 2):
            cmp_ = saved(ctx, f'compare_{n}') or {}
            archive_status = ("active" if selected == n
                              else ("rejected" if cmp_.get("decision") == "rejected" else "candidate"))
            code_fp = (cmp_ or {}).get("code_fingerprint") or cmp_.get("baseline", {}).get("code_fingerprint") or ""
            if code_fp:
                node = make_node(
                    parent_id=(run_id + "_" + str(n - 1)) if n > 1 else None,
                    generation=n,
                    archive_status=archive_status,
                    code_fingerprint=str(code_fp),
                    fitness=cmp_.get("criteria_results", {}).get("expectations_met_score"),
                    rationale=f"attempt={n} selected={selected}",
                )
                persist_node(workspace_root, run_id, node)
                entry = make_entry(
                    strategy_id="dgm_lineage",
                    scope="autonomous_evolution",
                    content="node_id=" + node.node_id + "; generation=" + str(node.generation) +
                             "; archive_status=" + node.archive_status,
                )
                append_to_memory(entry, workspace_root=workspace_root)
    except Exception as exc:
        # Non-fatal: the decision value is the primary signal; the
        # DGM archive is observation metadata.
        import logging as _lg
        _lg.getLogger("partner.events.autonomous_evolution").warning(
            "dgm wiring failed: %s", exc)

    return persist(ctx,params,value)





# (2026-09-16) v2 self-evolution handlers

def tests_preflight(ctx, params):
    from partner.runtime.test_validity import classify_preflight
    plan = saved(ctx, 'tests')
    value = classify_preflight(experiment.test_preflight(directory(ctx), plan), plan)
    return persist(ctx, params, value)


def tests_review_v2(ctx, params):
    # Always run the latest plan; the diagnostic runner caches by content hash.
    from partner.runtime.test_validity import classify_preflight
    plan = saved(ctx, 'tests')
    preflight = classify_preflight(experiment.test_preflight(directory(ctx), plan), plan)
    if not preflight['valid']:
        return persist(ctx,params,{'accepted':False, 'plan_sha256':preflight['plan_sha256'],
            'preflight':preflight, 'problems':[{'kind':'blocking','detail':preflight['classification']}]})
    result = test_review(ctx, {**params, 'node_id':'semantic_test_review'})
    value = dict(result.get('semantic_output') or {})
    value.update(plan_sha256=preflight['plan_sha256'], preflight=preflight)
    return persist(ctx,params,value)


def apply_source_handler(ctx, params):
    from partner.runtime.evolution_experiment import apply_source
    decision = saved(ctx,'decision')
    if not decision.get('selected_attempt'):
        return persist(ctx,params,{'status':'no_attempt','production_effective':False})
    allowed = ((params.get('intent_contract') or {}).get('execution_constraints') or {}).get('evolution_apply') is True
    iso = saved(ctx,'isolate_'+str(decision['selected_attempt']))
    if not iso.get('ready'):
        return persist(ctx,params,{'status':'isolate_unavailable','production_effective':False})
    value = apply_source(directory(ctx), iso, saved(ctx,'freeze'), iso['target_files'], allowed)
    return persist(ctx,params,value,
        'patch 已原子写入生产源码' if value.get('status')=='applied' else f"apply 失败: {value.get('status')}")


def runtime_reload_handler(ctx, params):
    from partner.runtime.evolution_experiment import perform_runtime_reload, request_runtime_reload
    apply_receipt = saved(ctx,'apply_source')
    if not apply_receipt or apply_receipt.get('status')!='applied':
        return persist(ctx,params,{'status':'not_applied','production_effective':False,
            'reason':'apply_source did not complete; refusing to reload'})
    record = perform_runtime_reload(ctx.workspace, apply_receipt)
    request = request_runtime_reload(ctx.workspace, apply_receipt.get('files') or [])
    return persist(ctx,params,{**record,'reload_request':request},
        '已记录 reload 契约，等待外部生命周期管理器重启 worker')


def runtime_verify_handler(ctx, params):
    from partner.runtime.evolution_experiment import perform_runtime_verify
    apply_receipt = saved(ctx,'apply_source')
    if not apply_receipt or apply_receipt.get('status')!='applied':
        return persist(ctx,params,{'status':'not_applied','production_effective':False,
            'reason':'apply_source did not complete'})
    verify = perform_runtime_verify(ctx.workspace, apply_receipt)
    return persist(ctx,params,verify,
        '运行验证完成' if verify.get('all_ok') else '运行验证失败，需要回滚')


def rollback_handler(ctx, params):
    from partner.runtime.evolution_experiment import rollback_source
    apply_receipt = saved(ctx,'apply_source')
    if not apply_receipt:
        return persist(ctx,params,{'status':'nothing_to_rollback','production_effective':False})
    value = rollback_source(directory(ctx), apply_receipt)
    return persist(ctx,params,value, '源码已回滚')


def rollback_verify_handler(ctx, params):
    apply_receipt = saved(ctx,'apply_source') or {}
    rollback_receipt = saved(ctx,'rollback') or {}
    files = apply_receipt.get('files') or []
    restored = (rollback_receipt.get('restored') or [])
    verified = (rollback_receipt.get('verified') or {})
    # A candidate that was never applied (no_attempt) or produced no changed
    # files has NOTHING to verify.  all([]) would be True and would falsely
    # report a "verified rollback"; report not_applicable instead.
    if not files or str(apply_receipt.get('status') or '') in ('no_attempt', 'nothing_to_rollback'):
        value = {'status':'not_applicable', 'all_hashes_match': False,
                 'restored': restored, 'verified': verified,
                 'production_effective': False,
                 'reason': 'no source changes were applied; rollback verification is not applicable'}
        return persist(ctx, params, value, '未应用源码，回滚验证不适用')
    all_match = bool(files) and all(verified.get(f) for f in files)
    value = {'status':'verified' if all_match else 'mismatch',
             'all_hashes_match': all_match,
             'restored':restored,'verified':verified,'production_effective':False}
    return persist(ctx,params,value, '回滚验证完成' if all_match else '回滚后哈希不匹配')


def failure_analyze_handler(ctx, params):
    rc = saved(ctx,'release_compare') or {}
    apply = saved(ctx,'apply_source') or {}
    decision = saved(ctx,'decision') or {}
    cause = params.get('cause') or 'unspecified'
    classification = {
        'cause':cause,
        'release_compare_decision':rc.get('decision'),
        'release_compare_diff':rc.get('common_failure_count'),
        'release_compare_new_failures':rc.get('new_failures'),
        'apply_status':apply.get('status'),
        'decision_decision':decision.get('decision'),
    }
    if rc.get('decision') == 'rejected_new_regression':
        bucket,action='candidate_regression','candidate_revision'
    elif rc.get('decision') == 'rejected_test_disappeared':
        bucket,action='test_contract_broken','tests_repair'
    elif cause == 'release_timeout':
        bucket,action='env_timeout','tests_repair_or_replan'
    elif decision.get('decision') == 'inconclusive':
        bucket,action='no_candidate_qualified','candidate_revision'
    elif apply.get('status') == 'rebase_required':
        bucket,action='live_source_drift','blocked'
    else:
        bucket,action='unknown','blocked'
    classification['bucket']=bucket
    classification['action']=action
    return persist(ctx,params,classification, f"失败归类: {bucket} -> {action}")


def release_baseline_handler(ctx, params):
    from partner.runtime.evolution_experiment import release_compare
    decision = saved(ctx,'decision') or {}
    selected = decision.get('selected_attempt') or 1
    base_node = saved(ctx,f'baseline_{selected}') or {}
    return persist(ctx,params,base_node, f"baseline arm reused from baseline_{selected}")


def release_candidate_handler(ctx, params):
    decision = saved(ctx,'decision') or {}
    selected = decision.get('selected_attempt') or 1
    cand_node = saved(ctx,f'candidate_run_{selected}') or {}
    return persist(ctx,params,cand_node, f"candidate arm reused from candidate_run_{selected}")


def release_compare_handler(ctx, params):
    # (2026-09-16) v2 release gate: use the structured compare_N results that the
    # isolated experiment already produced, augmented with the per-target release
    # evidence (regression_passed based on diff vs frozen).
    from partner.runtime.evolution_experiment import release_compare as _rc
    decision = saved(ctx,'decision') or {}
    selected = decision.get('selected_attempt') or 1
    base_node = saved(ctx,f'baseline_{selected}') or {}
    cand_node = saved(ctx,f'candidate_run_{selected}') or {}
    compare_node = saved(ctx,f'compare_{selected}') or {}
    frozen = saved(ctx,'freeze') or {}
    diff = _rc(base_node, cand_node, frozen)
    diff['selected_attempt'] = selected
    # Cross-reference compare_node decision for the audit trail.
    diff['matched_experiment_decision'] = compare_node.get('decision')
    diff['matched_experiment_qualified'] = compare_node.get('qualified')
    diff['matched_experiment_improved'] = compare_node.get('improved')
    diff['matched_experiment_regression_passed'] = compare_node.get('regression_passed')
    diff['matched_expectations_met'] = sum(
        1 for e in (compare_node.get('expectation_results') or []) if e.get('status') == 'met')
    diff['matched_expectations_total'] = len(compare_node.get('expectation_results') or [])
    # Re-decide: only promote when matched_experiment is qualified and regression_passed.
    if diff.get('matched_experiment_qualified') and diff.get('matched_experiment_regression_passed'):
        diff['release_decision'] = 'validated_promote'
    elif diff.get('matched_expectations_met', 0) == 0:
        diff['release_decision'] = 'rejected_no_progress'
    else:
        diff['release_decision'] = 'rejected_release_regression'
    diff['decision'] = diff['release_decision']
    return persist(ctx,params,diff, f"release compare: {diff.get('release_decision')}")


def test_repair_handler(ctx, params):
    return tests(ctx, params)


def test_repair_2_handler(ctx, params):
    return tests(ctx, params)
def release(ctx, params):
    choice=saved(ctx,'decision').get('selected_attempt')
    if not choice:return persist(ctx,params,{'status':'not_applied','production_effective':False})
    allowed=((params.get('intent_contract') or {}).get('execution_constraints') or {}).get('evolution_apply') is True
    isolated=saved(ctx,f'isolate_{choice}')
    value=experiment.activate(directory(ctx),isolated,saved(ctx,'freeze'),isolated['target_files'],allowed)
    return persist(ctx,params,value)


def record(ctx, params):
    from partner.governance.evolution_loop import record_issue, start_experiment, decide_experiment
    from partner.memory import EventMemory
    design_value=saved(ctx,'design');choice=saved(ctx,'decision').get('selected_attempt')
    path=directory(ctx)/'governance.json'
    if not path.exists():
        refs=[str(p) for p in directory(ctx).glob('*.json')]
        issue=record_issue(ctx.workspace,{'summary':str(design_value.get('causal_hypothesis') or 'cycle audit'),
            'category':'event','severity':'medium','evidence':refs,
            'instance_id':ctx.instance_id,'project_id':params['project_id']})
        exp=start_experiment(ctx.workspace,{'issue_id':(issue.get('issue') or {}).get('issue_id',''),
            'project_id':params['project_id'],'hypothesis':design_value.get('causal_hypothesis',''),
            'intervention':design_value.get('change_design',''),'success_criteria':[str(e) for e in design_value.get('expectations',[])],
            'baseline':{},'tests':refs})
        verdict=decide_experiment(ctx.workspace,{'experiment_id':(exp.get('experiment') or {}).get('experiment_id',''),
            'project_id':params['project_id'],'decision':'inconclusive' if choice or saved(ctx,'decision').get('decision')=='no_change_verified' else saved(ctx,'decision').get('decision','inconclusive'),
            'reason':'paired validation recorded separately; activation requires process reload evidence' if choice else
                     'current frozen behavior verified; original bug hypothesis unsupported; no candidate proposed' if saved(ctx,'compare_1').get('no_change_verified') else 'frozen criteria not met',
            'evidence':refs,'regression_passed':bool(choice),'criteria_results':{'expectations_met':bool(choice)}})
        memory=EventMemory(ctx.workspace).append_semantic('lesson',{'project_id':params['project_id'],
            'status':'active','content':{'design':design_value,'decision':saved(ctx,'decision'),'release':saved(ctx,'release')},
            'evidence_refs':refs,'production_effective':False})
        write_json(path,{'issue':issue,'experiment':exp,'decision':verdict,'memory':memory})
    value={'decision':saved(ctx,'decision'),'release':saved(ctx,'release'),'governance':str(path),
           'audit_aspects':list((saved(ctx,'audit').get('aspect_reviews') or {}).keys()),
           'real_experiment_executed':any(saved(ctx,f'baseline_{n}').get('executed') and saved(ctx,f'candidate_run_{n}').get('executed') for n in (1,2)),
           'valid_matched_experiment':any((saved(ctx,f'compare_{n}').get('criteria_results') or {}).get('matched_tests') is True for n in (1,2)),
           'no_change_behavior_verified':bool(saved(ctx,'compare_1').get('no_change_verified')),
           'production_effective':False}
    return persist(ctx,params,value,'自主调查、冻结预期、实验与最终结果已记录')


_HANDLERS = {'collect':collect,'read_plan':read_plan,'sources':sources,'audit':audit,'counter':counter,
 'design':design,'tests':tests,'test_review':test_review,'freeze':freeze,'candidate':candidate,
 'critic':critic,'isolate':isolate,'baseline':execution('baseline'),'candidate_run':execution('candidate'),
 'compare':compare,'analyze':analyze,'decision':decision,'release':release,'record':record,
 'tests_preflight':tests_preflight,'tests_review':tests_review_v2,'test_repair_v2':test_repair_handler,'test_repair_2_v2':test_repair_2_handler,
 'release_baseline':release_baseline_handler,'release_candidate':release_candidate_handler,
 'release_compare':release_compare_handler,
 'apply_source':apply_source_handler,'runtime_reload':runtime_reload_handler,
 'runtime_verify':runtime_verify_handler,'rollback':rollback_handler,
 'rollback_verify':rollback_verify_handler,'failure_analyze':failure_analyze_handler}
_LOCAL = {'collect','sources','freeze','isolate','baseline','candidate_run','compare',
           'decision','release','record','tests_preflight','tests_review',
           'release_baseline','release_candidate','release_compare',
           'apply_source','runtime_reload','runtime_verify',
           'rollback','rollback_verify','failure_analyze',
           'test_repair_v2','test_repair_2_v2'}
DEFINITIONS = [EventDefinition('autoevolution.'+name,'evolution',
    '自动调查与冻结预期实验：'+name,handler,
    execution_method='local' if name in _LOCAL else 'llm',
    timeout_seconds=(900 if name in ('runtime_verify','runtime_reload','release_baseline','release_candidate') else 600 if name=='release' else 300),
    max_attempts=2) for name,handler in _HANDLERS.items()]

