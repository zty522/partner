"""Bounded discovery of explicitly referenced scientific/report assets.

No directory crawl: only existing paths in the provided evidence or original
request, plus inherited figure sources. Configuration/credential files are not
report evidence and are excluded from recursive discovery.
"""
from pathlib import Path
import json
import re

EXTENSIONS={'.patch','.csv','.pdb','.pdbqt','.sdf','.smi','.mp4','.png','.jpg','.jpeg','.webp','.svg','.json','.md','.txt','.py','.xml','.xtc','.dcd','.gro'}


def expand(paths,request='',limit=120):
    result=list(dict.fromkeys(str(Path(p).resolve()) for p in paths));seen=set(result)
    def accept(value,base):
        if not isinstance(value,str) or len(value)>1500 or '\n' in value:return
        p=Path(value)
        if p.suffix.lower() not in EXTENSIONS:return
        if not p.is_absolute():p=base/p
        try:p=p.resolve()
        except OSError:return
        if p.name.lower() in {'api.json','partner_config.json','credentials.json','cookies.json'}:return
        if p.is_file() and str(p) not in seen and len(result)<limit:
            result.append(str(p));seen.add(str(p))
    for value in re.findall(r'/(?:[^\s`"<>，。；：()\[\]]+)',request):accept(value,Path('/'))
    def walk(value,base,depth=0):
        if depth>8:return
        if isinstance(value,str):accept(value,base)
        elif isinstance(value,dict):
            for item in list(value.values())[:200]:walk(item,base,depth+1)
        elif isinstance(value,list):
            for item in value[:200]:walk(item,base,depth+1)
    # Only two generations of references; large training data are never fully loaded.
    frontier=result[:]
    for _ in range(2):
        before=len(result)
        for raw in frontier:
            p=Path(raw)
            if p.suffix=='.json' and p.is_file() and p.stat().st_size<5_000_000:
                try:
                    value=json.loads(p.read_text());walk(value,p.parent)
                    if isinstance(value,dict) and value.get('experiment_id') and isinstance(value.get('source_hashes'),dict):
                        from partner.presentation.figures import digest
                        for relative,expected in value['source_hashes'].items():
                            baseline=p.parent/'baseline'/relative
                            if baseline.is_file() and digest(baseline)==expected:
                                accept(str(baseline),p.parent)
                                accept(str(p.parent/'candidate'/relative),p.parent)
                        patch=p.parent/'candidate.patch'
                        if patch.is_file() and digest(patch)==value.get('patch_sha256'):accept(str(patch),p.parent)
                except (ValueError,OSError):pass
        frontier=result[before:]
    return result
