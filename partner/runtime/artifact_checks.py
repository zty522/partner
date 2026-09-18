"""Artifact existence / content checks for the production project flow.

Read discipline: ``owned_files`` is a bounded point-read (``max_entries``
budget, prunes .git/__pycache__/.venv/node_modules symlinks, writes an
``artifact_read_receipt.json``) scoped to the current task's owned output
folder — not a full-tree scan.  Called by production Events
(``project``, ``cycle``, ``presentation``) and runtime helpers.
"""


"""Mechanical artifact checks; never substitutes for domain scientific validation."""
from pathlib import Path
import csv
import hashlib
import json


def preview_data(value):
    """Bound array previews while putting measured summaries before metadata."""
    if isinstance(value, dict):
        def priority(item):
            key, data = item
            if key in ('summary','metrics','statistics','stats','generation','provenance') or key.startswith('stats_'):
                return 0
            if key in ('work_dir','event_id','job_id','round') or key.endswith(('_path','_source')):
                return 3
            return 2 if isinstance(data, list) else 1
        return {k:preview_data(v) for k,v in sorted(value.items(), key=priority)}
    if isinstance(value, list):
        if len(value)>8:
            from collections import Counter
            counts={}
            if all(isinstance(row,dict) for row in value):
                for key in ('phase','event_type','kind','status','type'):
                    cells=[row.get(key) for row in value]
                    if any(isinstance(c,str) for c in cells) and all(c is None or isinstance(c,(str,int,bool)) for c in cells):
                        histogram=Counter(str(c) for c in cells)
                        if len(histogram)<=20:counts[key]=dict(histogram)
            return {'total_items':len(value),'category_counts':counts,
                    'items_preview':[preview_data(v) for v in value[:5]],
                    'tail_preview':[preview_data(v) for v in value[-2:]]}
        return [preview_data(v) for v in value]
    return value


def check_file(path: Path):
    result = {'path':str(path), 'valid':False, 'sha256':'', 'error':''}
    try:
        if path.stat().st_size > 32_000_000:raise ValueError('artifact exceeds 32 MB inspection budget; needs dedicated verifier')
        with path.open('rb') as handle:data = handle.read(32_000_001)
        if len(data)>32_000_000:raise ValueError('artifact grew beyond inspection budget')
        result.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        if not data: raise ValueError('empty artifact')
        if path.suffix == '.json':
            value = json.loads(data)
            if not isinstance(value,(dict,list)) or not value: raise ValueError('empty data')
            if isinstance(value,dict) and (value.get('failure_class') or value.get('error') or value.get('status') in ('failed','error')):
                raise ValueError('operational failure record, not verified business data')
            result['data_preview'] = json.dumps(preview_data(value), ensure_ascii=False)[:1500]
        elif path.suffix == '.jsonl':
            rows = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
            if not rows or any(not isinstance(row, (dict, list)) or not row for row in rows):
                raise ValueError('empty or invalid JSONL data rows')
            result['rows'] = len(rows)
            result['data_preview'] = json.dumps(preview_data(rows), ensure_ascii=False)[:1500]
        elif path.suffix in ('.csv','.tsv'):
            rows = list(csv.reader(data.decode().splitlines(), delimiter='\t' if path.suffix=='.tsv' else ','))
            if len(rows)<2 or len(rows[0])<2 or any(len(r)!=len(rows[0]) for r in rows):raise ValueError('invalid data table')
            result['rows'] = len(rows)-1
        elif path.suffix == '.smi':
            from rdkit import Chem
            smiles = [line.split()[0] for line in data.decode().splitlines()
                      if line.strip() and not line.lstrip().startswith('#')]
            if not smiles or any(Chem.MolFromSmiles(s) is None for s in smiles):
                raise ValueError('empty or unparseable SMILES collection')
            result['molecules'] = len(smiles)
            result['unique_molecules'] = len({Chem.MolToSmiles(Chem.MolFromSmiles(s)) for s in smiles})
        elif path.suffix in ('.sdf','.mol'):
            from rdkit import Chem
            molecules = list(Chem.SDMolSupplier(str(path))) if path.suffix=='.sdf' else [Chem.MolFromMolFile(str(path))]
            if not molecules or any(m is None for m in molecules):raise ValueError('unparseable molecules')
            result['molecules'] = len(molecules)
        elif path.suffix == '.pdb':
            if not any(l.startswith(('ATOM  ','HETATM')) for l in data.decode().splitlines()): raise ValueError('no atoms')
        elif path.suffix == '.pdbqt':
            import math
            lines=data.decode().splitlines()
            atoms=[line for line in lines if line.startswith(('ATOM  ','HETATM'))]
            if not atoms:raise ValueError('no PDBQT atoms')
            for line in atoms:
                values=[float(line[a:b]) for a,b in ((30,38),(38,46),(46,54))]
                values.append(float(line.split()[-2]))
                if not all(math.isfinite(v) for v in values):raise ValueError('nonfinite PDBQT coordinates or charge')
            models=sum(line.startswith('MODEL ') for line in lines)
            if models != sum(line.startswith('ENDMDL') for line in lines):raise ValueError('incomplete PDBQT pose')
            scores=[float(line.split()[3]) for line in lines if line.startswith('REMARK VINA RESULT:')]
            if not all(math.isfinite(v) for v in scores):raise ValueError('nonfinite docking score')
            result.update(atoms=len(atoms),poses=models or 1,vina_scores=scores)
        else:
            raise ValueError('supporting document/code; not a verified data artifact')
        result['valid']=True
    except Exception as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
    return result


def owned_files(work, *, max_entries=2000):
    """Bounded discovery only inside the current task's owned output folder."""
    import os
    root=Path(work).resolve();result=[];seen=0
    for base,dirs,files in os.walk(root,followlinks=False):
        seen+=len(dirs)+len(files)
        if seen>max_entries:raise ValueError('owned output discovery budget exhausted')
        dirs[:]=[d for d in dirs if d not in {'.execution','__pycache__','.git','.venv','node_modules'} and not (Path(base)/d).is_symlink()]
        for name in files:
            path=Path(base)/name
            if not path.is_symlink() and path.is_file():result.append(path)
    receipt=root/'.execution'/'artifact_read_receipt.json'
    receipt.parent.mkdir(parents=True,exist_ok=True)
    receipt.write_text(json.dumps({'scope':str(root),'max_entries':max_entries,
        'entries_observed':seen,'selected_paths':[str(p) for p in result],
        'max_file_bytes':32_000_000,'purpose':'current task artifact discovery'},ensure_ascii=False))
    return result


def inspect_artifacts(work):
    return [check_file(p) for p in owned_files(work)]
