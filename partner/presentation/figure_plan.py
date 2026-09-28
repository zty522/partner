"""Executable figure contracts checked before expensive report writing."""
from pathlib import Path
import json
from partner.presentation.figures import KINDS,select,csv_data,json_data


def validate(plans,sources):
    errors=[];normalized=[]
    if not isinstance(plans,list) or not 1<=len(plans)<=10:return [],['choose 1–10 executable figures']
    aliases={r.get('evidence_id'):r['path'] for r in sources if r.get('exists')}
    paths=list(aliases.values());names={}
    for p in paths:names.setdefault(Path(p).name,[]).append(p)
    def resolve(value):
        if value in aliases:return aliases[value]
        if value in paths:return value
        matches=names.get(Path(str(value)).name,[])
        if len(matches)==1:return matches[0]
        raise ValueError(f'unknown/ambiguous source {value}; use a supplied E ID')
    for value in plans:
        try:
            p=dict(value)
            options=p.pop('parameters', p.pop('params', {}))
            if not isinstance(options,dict):raise ValueError('parameters must be an object')
            if any(k in p and p[k]!=v for k,v in options.items()):raise ValueError('conflicting nested and top-level parameters')
            p={**options,**p};kind=p['kind'];identifier=p['id']
            if kind not in KINDS:raise ValueError(f'unsupported kind {kind}')
            p['source_refs']=[resolve(r) for r in p['source_refs']]
            if not p['source_refs']:raise ValueError('source_refs empty')
            if p.get('pose_path'):p['pose_path']=resolve(p['pose_path'])
            if p.get('residue_source'):p['residue_source']=resolve(p['residue_source'])
            source=Path(p['source_refs'][0])
            if kind=='pdb_structure' and source.suffix.lower()!='.pdb':raise ValueError('pdb_structure first source must be receptor PDB; ligand PDBQT belongs in pose_path')
            if kind=='csv_line':
                if any(Path(raw).suffix.lower()!='.csv' for raw in p['source_refs']):raise ValueError('csv_line requires CSV inputs; for JSON array scores use distribution with rows_key and value_key')
                for raw in p['source_refs']:
                    row=csv_data(raw)[0]
                    float(row[p['x']]);float(row[p['y']])
                if p.get('labels') and len(p['labels'])!=len(p['source_refs']):raise ValueError('labels must match source count')
            if kind=='distribution':
                if not p.get('value_key'):raise ValueError('distribution requires value_key (exact numeric field) and rows_key (exact array location)')
                field_name=str(p.get('value_key') or '').lower()
                if any(token in field_name for token in ('byte','confidence','token','hash','exit_code','duration_ms')):
                    raise ValueError('operational metadata is not a research-result distribution')
                data=json_data(source);requested=p.get('rows_key','')
                try:rows=select(data,requested)
                except (KeyError,ValueError,TypeError):
                    # Source excerpts wrap sampled arrays for display. Resolve
                    # that wrapper only against an actual existing full array.
                    canonical='.'.join(x for x in requested.split('.') if x not in {'items_preview','tail_preview'})
                    if canonical==requested:raise
                    rows=select(data,canonical)
                    if not isinstance(rows,list):raise ValueError('preview wrapper does not resolve to a real array')
                    p['rows_key']=canonical;p['array_path_resolution']={'requested':requested,'actual':canonical,'scope':'full source array, not prompt sample'}
                if not isinstance(rows,list) or not rows:
                    raise ValueError('rows_key is not a nonempty array')
                measured_values=[]
                for index, item in enumerate(rows):
                    try:
                        measured_values.append(float(select(item, p['value_key'])))
                    except (KeyError, TypeError, ValueError, IndexError) as exc:
                        raise ValueError(f"distribution needs a numeric value in every row; field {p['value_key']!r} "
                                         f"is missing/non-numeric in row {index}. Do not plot exception text or "
                                         "mixed event schemas as a numeric distribution; choose a supported field "
                                         "or a real code_excerpt instead") from exc
                if len(set(measured_values)) < 2:
                    raise ValueError('distribution requires at least two distinct measured values')
                if len(measured_values) < 3:
                    raise ValueError('distribution requires at least three measured rows')
            if kind=='scalar_bar':
                if source.suffix.lower() not in {'.json', '.jsonl'}:
                    raise ValueError('scalar_bar requires one JSON source')
                values=p.get('values') or {}
                if not isinstance(values,dict) or not 2<=len(values)<=6:
                    raise ValueError('scalar_bar requires 2–6 labeled numeric selectors')
                data=json_data(source)
                for selector in values.values():
                    number=float(select(data,selector))
                    if not __import__('math').isfinite(number):
                        raise ValueError('scalar_bar selector is not finite')
            if kind=='molecule_grid':
                data=json_data(source); requested=p.get('rows_key','candidates')
                try:
                    rows=select(data,requested)
                except (KeyError,ValueError,TypeError,IndexError):
                    canonical='.'.join(x for x in str(requested).split('.')
                                       if x not in {'items_preview','tail_preview'})
                    if canonical==requested:raise
                    rows=select(data,canonical)
                    if not isinstance(rows,list):
                        raise ValueError('preview wrapper does not resolve to a real molecule array')
                    p['rows_key']=canonical
                    p['array_path_resolution']={
                        'requested':requested,'actual':canonical,
                        'scope':'full source array, not prompt sample'}
                for i in p.get('indices',[0]):rows[int(i)][p.get('smiles_key','canonical_smiles')]
            if kind=='video_frame':float(p['timestamp'])
            if kind=='code_excerpt' and 'start_line' not in p:raise ValueError('code excerpt needs actual start_line/end_line')
            if kind=='existing_image' and source.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.svg'}:
                raise ValueError('existing_image requires a real image file, not a JSON/CSV summary')
            normalized.append(p)
        except (KeyError,TypeError,ValueError,IndexError,OSError) as exc:errors.append(f"{value.get('id','?')}: {exc}")
    if len({p.get('id') for p in plans})!=len(plans):errors.append('figure IDs must be unique')
    return normalized,errors


def source_catalog(sources):
    """Expose actual array paths and fields, never display-preview wrapper paths."""
    catalog=[]
    for source in sources:
        path=Path(source['path'])
        if not source.get('exists') or not path.is_file():continue
        row={'source_id':source.get('evidence_id'),'filename':path.name,'format':path.suffix}
        try:
            if path.suffix in {'.json','.jsonl'} and path.stat().st_size<=10_000_000:
                arrays=[]
                def walk(value,key='',depth=0):
                    if depth>5 or len(arrays)>=12:return
                    if isinstance(value,list):
                        sample=value[:8]
                        fields=sorted({k for item in sample if isinstance(item,dict) for k in item})
                        numeric=sorted({k for item in sample if isinstance(item,dict) for k,v in item.items() if isinstance(v,(int,float)) and not isinstance(v,bool)})
                        arrays.append({'rows_key':key,'count':len(value),'fields':fields[:20],'numeric_fields':numeric[:12]})
                    elif isinstance(value,dict):
                        for k,v in value.items():walk(v,f'{key}.{k}' if key else k,depth+1)
                walk(json_data(path));row['actual_arrays']=arrays
            elif path.suffix in {'.py','.txt','.md','.log','.raw'}:
                row['actual_line_count']=len(path.read_text(errors='replace').splitlines())
                if path.suffix=='.py':
                    import ast
                    try:
                        row['function_ranges']=[{'name':node.name,'start_line':node.lineno,'end_line':node.end_lineno} for node in ast.walk(ast.parse(path.read_text())) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))][:40]
                    except SyntaxError:pass
            elif path.suffix=='.csv':
                values=csv_data(path);row.update(columns=list(values[0]),rows=len(values))
        except (OSError,ValueError,TypeError) as exc:
            row['read_error']=str(exc)[:120]
        catalog.append(row)
    return catalog


def executable_choices(sources):
    """Offer source-bound executable selectors; the model chooses relevance."""
    choices = []
    for row in source_catalog(sources):
        candidates = []
        if row.get('format') in {'.json', '.jsonl'}:
            source = next((s for s in sources if s.get('evidence_id') == row['source_id']), {})
            try:
                data = json_data(source.get('path', ''))
            except (OSError, ValueError, TypeError):
                data = {}
            if isinstance(data, dict):
                numeric = {str(k):v for k,v in data.items()
                           if isinstance(v,(int,float)) and not isinstance(v,bool)}
                pairs = []
                for key in numeric:
                    if key.startswith('baseline_'):
                        suffix=key[len('baseline_'):]
                        peer='candidate_'+suffix
                        own_peer = ''
                        if peer in numeric:
                            pairs.append((key,peer))
                        # Candidate result records commonly store their own
                        # metric as ``test_rmse`` beside ``baseline_rmse``.
                        # Accept that shape only when the same record carries
                        # concrete candidate-role evidence.
                        else:
                            own_peer = next((name for name in (suffix, 'test_'+suffix)
                                             if name in numeric), '')
                        if not any(pair[0] == key for pair in pairs) and own_peer and (
                            str(data.get('role') or data.get('model_role') or '').lower() == 'candidate'
                            or (isinstance(data.get('features'), list)
                                and len(data.get('features')) >= 2)):
                            pairs.append((key,own_peer))
                for baseline,candidate in pairs[:2]:
                    suffix=baseline[len('baseline_'):]
                    candidates.append({'kind':'scalar_bar','source_refs':[row['source_id']],
                        'values':{'Baseline':baseline,'Candidate':candidate},
                        'y_label':('RMSE' if suffix.lower()=='rmse' else suffix)+'（原数据单位）'})
        for array in row.get('actual_arrays', []):
            for field in array.get('numeric_fields', []):
                if array.get('count', 0) < 5 or any(token in field.lower() for token in (
                        'byte','confidence','token','hash','exit_code','duration_ms')):
                    continue
                candidates.append({'kind':'distribution', 'source_refs':[row['source_id']],
                                   'rows_key':array['rows_key'], 'value_key':field,
                                   'x_label':field + '（原数据单位）'})
        for function in row.get('function_ranges', [])[:5]:
            candidates.append({'kind':'code_excerpt', 'source_refs':[row['source_id']],
                               'start_line':function['start_line'],
                               'end_line':min(function['end_line'], function['start_line']+23),
                               'function':function['name']})
        kept = 0
        for candidate in candidates:
            candidate.update(id='V'+str(len(choices)+1).zfill(2), required=True)
            valid, errors = validate([candidate], sources)
            if errors: continue
            choices.append({'option_id':candidate['id'], 'filename':row['filename'], 'plan':valid[0]})
            kept += 1
            if kept >= 5: break
    # Put direct outcome comparisons ahead of source-code excerpts and generic
    # distributions.  Otherwise a source-rich learning run can consume the
    # bounded option list before the actual baseline/candidate evidence is
    # even offered to the report planner.
    def priority(choice):
        plan=choice.get('plan') or {}; name=str(choice.get('filename') or '').lower()
        metric=str(plan.get('value_key') or '').lower()
        result_name=any(token in name for token in ('comparison','result','evidence','metrics'))
        return (0 if plan.get('kind')=='scalar_bar' else
                1 if result_name and ('delta' in metric or 'rmse' in metric) else
                2 if result_name else
                3 if plan.get('kind')=='distribution' else 4,
                name, str(choice.get('option_id') or ''))
    return sorted(choices,key=priority)[:50]
