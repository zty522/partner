"""Executable figure contracts checked before expensive report writing."""
from pathlib import Path
import json
from partner.presentation.figures import KINDS,select,csv_data


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
            options=p.pop('parameters',{})
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
                data=json.loads(source.read_text());requested=p.get('rows_key','')
                try:rows=select(data,requested)
                except (KeyError,ValueError,TypeError):
                    # Source excerpts wrap sampled arrays for display. Resolve
                    # that wrapper only against an actual existing full array.
                    canonical='.'.join(x for x in requested.split('.') if x not in {'items_preview','tail_preview'})
                    if canonical==requested:raise
                    rows=select(data,canonical)
                    if not isinstance(rows,list):raise ValueError('preview wrapper does not resolve to a real array')
                    p['rows_key']=canonical;p['array_path_resolution']={'requested':requested,'actual':canonical,'scope':'full source array, not prompt sample'}
                if not isinstance(rows,list) or not rows:raise ValueError('rows_key is not a nonempty array')
                float(select(rows[0],p['value_key']))
            if kind=='molecule_grid':
                rows=select(json.loads(source.read_text()),p.get('rows_key','candidates'))
                for i in p.get('indices',[0]):rows[int(i)][p.get('smiles_key','canonical_smiles')]
            if kind=='video_frame':float(p['timestamp'])
            if kind=='code_excerpt' and 'start_line' not in p:raise ValueError('code excerpt needs actual start_line/end_line')
            normalized.append(p)
        except (KeyError,TypeError,ValueError,IndexError,OSError) as exc:errors.append(f"{value.get('id','?')}: {exc}")
    if len({p.get('id') for p in plans})!=len(plans):errors.append('figure IDs must be unique')
    return normalized,errors
