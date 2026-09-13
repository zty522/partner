"""Reproducible plotting capabilities. No model-generated pixels or instance dispatch.

Plans use verified source paths and explicit selectors. Every asset is content
addressed by source bytes, renderer version and parameters; retries reuse only
verified assets. A failed required plot never becomes an empty successful report.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import threading

RENDERER_VERSION = '1'
RENDERER_SOURCE = Path(__file__).read_bytes()
RENDERER_SHA256 = hashlib.sha256(RENDERER_SOURCE).hexdigest()
PLOT_LOCK = threading.Lock()  # matplotlib global state is not thread safe
KINDS = {
    'video_frame': 'source_refs:[video], timestamp: seconds (choose meaningful moments from transcript)',
    'csv_line': 'source_refs:[CSV,...], x: column, y: column, labels:[], x_label, y_label; optional subtract_initial:true',
    'convergence': 'source_refs:[CSV,...], time_column, energy_column; measured step vs max relative energy error, dimensionless unless source says otherwise',
    'pdb_structure': 'source_refs:[PDB], residues:[integers] OR residue_source:E-ID of pocket JSON with residues_key:target_residue_set (use whole set); chain, local:false for whole protein or true for pocket, optional pose_path (MODEL 1 only); coordinates in angstrom',
    'molecular_diversity': 'source_refs:[candidate JSON], rows_key:candidates, smiles_key:canonical_smiles; compute actual Morgan r=2 2048-bit similarities and Murcko scaffold counts',
    'molecule_grid': 'source_refs:[JSON], rows_key:candidates, smiles_key:canonical_smiles, id_key:candidate_id, indices:[0,...] (max 6)',
    'distribution': 'source_refs:[JSON], rows_key: dot-separated array key, value_key: numeric field, x_label with evidenced units',
    'experiment_timeline': 'source_refs:[JSON] containing groups.*.future_state_timeline and func_ticks; separate clocks explicitly shown',
    'test_matrix': 'source_refs:[baseline_receipt.json,candidate_receipt.json], labels:[baseline,candidate]; exact same testcase identity required',
    'code_excerpt': 'source_refs:[text/code], start_line, end_line (max 24); actual source only',
    'existing_image': 'source_refs:[PNG/JPEG/WebP/SVG], caption based on evidence; SVG converted to PNG',
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def select(data, key):
    for part in str(key or '').split('.'):
        if part: data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def csv_data(path):
    with Path(path).open() as f: rows = list(csv.DictReader(f))
    if len(rows) < 2: raise ValueError('plot requires at least two measured rows')
    return rows


def verify_asset(asset):
    from PIL import Image
    path = Path(asset['path'])
    if digest(path) != asset['sha256']: raise ValueError('figure bytes changed')
    for row in asset['sources']:
        if digest(row['path']) != row['sha256']: raise ValueError('figure source changed')
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        if min(im.size) < 240: raise ValueError('figure resolution too small')
    return True


def render(plan, directory, allowed_paths):
    from partner.runtime.action_execution import write_json
    kind = plan.get('kind')
    if kind not in KINDS: raise ValueError(f'unsupported real plotting capability: {kind}')
    paths = [Path(p).resolve() for p in plan.get('source_refs', [])]
    if plan.get('residue_source'): paths.append(Path(plan['residue_source']).resolve())
    if plan.get('pose_path'): paths.append(Path(plan['pose_path']).resolve())
    allowed = {str(Path(p).resolve()) for p in allowed_paths}
    if not paths or any(str(p) not in allowed or not p.is_file() for p in paths):
        raise ValueError('all figure inputs must be collected evidence files')
    sources = [{'path':str(p), 'sha256':digest(p)} for p in paths]
    fingerprint = hashlib.sha256(json.dumps({'plan':plan,'sources':sources,'renderer':RENDERER_VERSION,
        'script_sha256':RENDERER_SHA256}, sort_keys=True).encode()).hexdigest()
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    script_snapshot=directory/('renderer_'+RENDERER_SHA256[:20]+'.py')
    if not script_snapshot.exists(): script_snapshot.write_bytes(RENDERER_SOURCE)
    receipt = directory / (fingerprint[:20]+'.json')
    if receipt.is_file():
        old = json.loads(receipt.read_text())
        try:
            verify_asset(old)
            return old
        except (OSError, ValueError): pass
    target = directory/(fingerprint[:20]+'.png')
    measured = {}
    with PLOT_LOCK:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        for font in ('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'):
            if Path(font).exists():
                font_manager.fontManager.addfont(font)
                plt.rcParams['font.family'] = font_manager.FontProperties(fname=font).get_name()
                break
        plt.rcParams.update({'font.size':12, 'axes.unicode_minus':False, 'axes.spines.top':False,'axes.spines.right':False})
        fig, ax = plt.subplots(figsize=(10,5.8), layout='constrained')
        try:
            if kind == 'video_frame':
                t = float(plan['timestamp'])
                probe = json.loads(subprocess.run(['ffprobe','-v','error','-show_format','-of','json',str(paths[0])], capture_output=True,text=True,check=True,timeout=20).stdout)
                if not 0 <= t < float(probe['format']['duration']): raise ValueError('timestamp outside actual video')
                # Decode by timestamps, rather than fabricate or label a montage as a frame.
                subprocess.run(['ffmpeg','-v','error','-ss',str(t),'-i',str(paths[0]),'-frames:v','1','-y',str(target)],check=True,capture_output=True,timeout=45)
                measured = {'requested_timestamp_seconds':t, 'video_duration_seconds':float(probe['format']['duration']),
                            'selection':'ffmpeg timestamp seek; frame at or immediately after requested time'}
            elif kind == 'existing_image':
                from PIL import Image, ImageOps
                if paths[0].suffix.lower() == '.svg':
                    import cairosvg
                    cairosvg.svg2png(url=str(paths[0]), write_to=str(target), output_width=1600)
                else:
                    with Image.open(paths[0]) as im: ImageOps.exif_transpose(im).convert('RGB').save(target)
            elif kind == 'csv_line':
                measured['series'] = []
                for i, path in enumerate(paths):
                    rows = csv_data(path)
                    x = [float(r[plan['x']]) for r in rows]; y = [float(r[plan['y']]) for r in rows]
                    if not all(math.isfinite(v) for v in x+y): raise ValueError('non-finite measured values')
                    if plan.get('subtract_initial'): y = [v-y[0] for v in y]
                    label = (plan.get('labels') or [p.stem for p in paths])[i]
                    ax.plot(x,y,label=label,linewidth=1.6)
                    measured['series'].append({'source':str(path),'rows':len(rows),'x_min':min(x),'x_max':max(x),'y_min':min(y),'y_max':max(y)})
                ax.set(xlabel=plan.get('x_label',plan['x']),ylabel=plan.get('y_label',plan['y']))
                ax.legend(); ax.grid(alpha=.18)
            elif kind == 'convergence':
                points = []
                for path in paths:
                    rows = csv_data(path); times = [float(r[plan.get('time_column','time')]) for r in rows]
                    energy = [float(r[plan.get('energy_column','energy')]) for r in rows]
                    steps = [b-a for a,b in zip(times,times[1:])]
                    dt = sum(steps)/len(steps)
                    if dt<=0 or max(abs(s-dt) for s in steps)>max(1e-9,dt*1e-4): raise ValueError('nonuniform time steps')
                    if energy[0] == 0: raise ValueError('relative error undefined for zero initial energy')
                    error = max(abs(e-energy[0])/abs(energy[0]) for e in energy)
                    if error<=0: raise ValueError('zero error cannot be fitted on log axes')
                    points.append({'step':dt,'error':error,'start':times[0],'end':times[-1]})
                if len(points)<2 or max(p['end'] for p in points)-min(p['end'] for p in points)>1e-8: raise ValueError('convergence requires matched end times')
                import numpy as np
                slope = float(np.polyfit(np.log([p['step'] for p in points]),np.log([p['error'] for p in points]),1)[0])
                points.sort(key=lambda p:p['step'])
                ax.loglog([p['step'] for p in points],[p['error'] for p in points],'o-',label=f'实测拟合斜率 {slope:.4f}')
                ax.set(xlabel='步长 dt（对数坐标；使用原数据时间单位）',ylabel='最大相对能量误差（对数坐标）'); ax.legend(); ax.grid(alpha=.2)
                measured={'points':points,'log_log_slope':slope,'metric_definition':'maximum absolute relative energy deviation across ALL sampled rows in the full interval; NOT endpoint error'}
            elif kind == 'pdb_structure':
                import numpy as np
                atoms = _atoms(paths[0]); chain = plan.get('chain'); selected = set(map(int,plan.get('residues',[])))
                if plan.get('residue_source'):
                    selected=set(map(int,select(json.loads(Path(plan['residue_source']).read_text()),plan.get('residues_key','target_residue_set'))))
                atoms = [a for a in atoms if not chain or a['chain']==chain]
                ca = [a for a in atoms if a['name']=='CA']
                if not ca: raise ValueError('no protein alpha carbons in supplied PDB')
                highlight = [a for a in atoms if a['residue'] in selected]
                if selected and selected-set(a['residue'] for a in highlight): raise ValueError('requested residues absent')
                fig.clear(); ax=fig.add_subplot(111,projection='3d')
                for ch in sorted(set(a['chain'] for a in ca)):
                    xyz=np.array([a['xyz'] for a in ca if a['chain']==ch]); ax.plot(*xyz.T,color='#7297b5',alpha=.6,linewidth=1.8)
                if highlight:
                    xyz=np.array([a['xyz'] for a in highlight]); ax.scatter(*xyz.T,c='#d58a2e',s=10,alpha=.65,label='指定残基重原子')
                    if plan.get('local'):
                        center=xyz.mean(axis=0); radius=max(np.ptp(xyz,axis=0).max()/2+5,8)
                        ax.set(xlim=(center[0]-radius,center[0]+radius),ylim=(center[1]-radius,center[1]+radius),zlim=(center[2]-radius,center[2]+radius))
                if plan.get('pose_path'):
                    pose=_atoms(paths[-1]); xyz=np.array([a['xyz'] for a in pose]); ax.scatter(*xyz.T,c='#b63364',s=28,label='配体 MODEL 1 重原子')
                    measured['pose_atoms']=len(pose)
                ax.set(xlabel='X (Å)',ylabel='Y (Å)',zlabel='Z (Å)'); ax.view_init(elev=25,azim=-55)
                if highlight: ax.legend(loc='upper left',fontsize=10)
                measured.update({'model':1,'representation':'CA trace and selected heavy atoms; not molecular surface','chain':chain,'residues':sorted(selected),'coordinate_unit':'angstrom','atoms':len(atoms)})
            elif kind == 'molecular_diversity':
                from rdkit import Chem,DataStructs
                from rdkit.Chem import rdFingerprintGenerator
                from rdkit.Chem.Scaffolds import MurckoScaffold
                from collections import Counter
                rows=select(json.loads(paths[0].read_text()),plan.get('rows_key','candidates'))
                mols=[Chem.MolFromSmiles(r[plan.get('smiles_key','canonical_smiles')]) for r in rows]
                if len(mols)<2 or any(m is None for m in mols):raise ValueError('diversity needs at least two valid actual molecules')
                generator=rdFingerprintGenerator.GetMorganGenerator(radius=2,fpSize=2048)
                fps=[generator.GetFingerprint(m) for m in mols]
                similarities=[DataStructs.TanimotoSimilarity(fps[i],fps[j]) for i in range(len(fps)) for j in range(i)]
                scaffolds=Counter(Chem.MolToSmiles(MurckoScaffold.GetScaffoldForMol(m)) for m in mols)
                fig.clear();left,right=fig.subplots(1,2)
                left.hist(similarities,bins=12,color='#416d91',edgecolor='white');left.set(xlabel='Morgan 指纹 Tanimoto 相似度',ylabel='候选对数',title='集合内两两结构相似度')
                frequencies=Counter(scaffolds.values());right.bar(list(frequencies),list(frequencies.values()),color='#c58b38');right.set(xlabel='每种骨架包含的候选数',ylabel='骨架种类数',title=f'{len(scaffolds)} 种 Murcko 骨架')
                measured={'molecule_count':len(mols),'pair_count':len(similarities),'mean_tanimoto':sum(similarities)/len(similarities),'scaffold_count':len(scaffolds),'scaffold_frequency_counts':dict(frequencies),'radius':2,'bits':2048}
            elif kind == 'molecule_grid':
                from rdkit import Chem
                from rdkit.Chem import Draw
                rows=select(json.loads(paths[0].read_text()),plan.get('rows_key','candidates'))
                indices=plan.get('indices',list(range(min(6,len(rows)))))
                if not 1<=len(indices)<=6: raise ValueError('select 1–6 readable molecules')
                chosen=[rows[int(i)] for i in indices]; smiles=[r[plan.get('smiles_key','canonical_smiles')] for r in chosen]
                mols=[Chem.MolFromSmiles(s) for s in smiles]
                if any(m is None for m in mols): raise ValueError('invalid SMILES')
                Draw.MolsToGridImage(mols,molsPerRow=2,subImgSize=(650,400),legends=[str(r.get(plan.get('id_key','candidate_id'),'')) for r in chosen]).save(target)
                measured={'indices':indices,'smiles':smiles}
            elif kind == 'distribution':
                rows=select(json.loads(paths[0].read_text()),plan.get('rows_key',''))
                values=[float(select(r,plan['value_key'])) for r in rows]
                if not values or not all(math.isfinite(v) for v in values): raise ValueError('no finite measured values')
                ax.hist(values,bins=min(12,max(3,int(math.sqrt(len(values))))),color='#416d91',edgecolor='white')
                ax.set(xlabel=plan.get('x_label',plan['value_key']),ylabel='候选数量')
                measured={'count':len(values),'min':min(values),'max':max(values),'mean':sum(values)/len(values)}
            elif kind == 'experiment_timeline':
                data=json.loads(paths[0].read_text()); measured['groups']={}
                ticklabels=[]
                for i,(name,g) in enumerate(data['groups'].items()):
                    events=g['future_state_timeline']; ticks=g['func_ticks']
                    ax.scatter([e['t_ms'] for e in events],[2*i]*len(events),marker='|',s=220,color='#416d91')
                    ax.scatter([e['t_ms_local'] for e in ticks],[2*i+1]*len(ticks),marker='|',s=150,color='#cd862c')
                    timeout=next(e for e in events if e.get('exception_type')=='TimeoutError')
                    ax.annotate(f"等待结束；cancelled={timeout['inner_cancelled']}",(timeout['t_ms'],2*i),xytext=(0,-24),textcoords='offset points',fontsize=10)
                    ticklabels += [name+' / Future',name+' / 函数']
                    measured['groups'][name]={'future_events':events,'function_return':[e for e in ticks if e.get('phase')=='func_return'],'function_tick_count':sum(e.get('phase')=='tick' for e in ticks),'return_record_count':sum(e.get('phase')=='func_return' for e in ticks),'total_record_count':len(ticks)}
                ax.set_yticks(range(len(ticklabels)),ticklabels); ax.set_xlabel('毫秒；Future 使用组时钟，函数使用本地时钟（不可当成同步绝对时间）'); ax.grid(axis='x',alpha=.2)
            elif kind == 'test_matrix':
                data=[json.loads(p.read_text()) for p in paths]
                if any(not d.get('executed') or d.get('timed_out') for d in data): raise ValueError('test execution not complete')
                names=[(r['class'],r['name']) for r in data[0]['cases']]
                if any([(r['class'],r['name']) for r in d['cases']]!=names for d in data): raise ValueError('test sets differ')
                values=[[0 if r.get('failed') or r.get('error') else .5 if r.get('skipped') else 1 for r in d['cases']] for d in data]
                ax.imshow(values,vmin=0,vmax=1,cmap='RdYlGn',aspect='auto')
                for y,row in enumerate(values):
                    for x,v in enumerate(row): ax.text(x,y,{0:'失败',.5:'跳过',1:'通过'}[v],ha='center',va='center',fontsize=18)
                ax.set_xticks(range(len(names)),[f'用例 {i+1}' for i in range(len(names))]); ax.set_yticks(range(len(data)),plan.get('labels',[p.stem for p in paths]))
                measured={'test_cases':names,'outcomes':values,'performance_not_measured':True}
            elif kind == 'code_excerpt':
                lines=paths[0].read_text().splitlines(); start=int(plan.get('start_line',1)); end=int(plan.get('end_line',min(start+15,len(lines))))
                if not 1<=start<=end<=len(lines) or end-start>=24: raise ValueError('invalid readable source range')
                fig.set_size_inches(10, max(1.8, 1.0 + (end-start+1)*.23))
                ax.axis('off'); ax.text(0,1,'\n'.join(f'{n:>4}  {lines[n-1]}' for n in range(start,end+1)),va='top',transform=ax.transAxes,fontsize=10)
                measured={'start_line':start,'end_line':end,'verbatim':True}
            if not target.exists():
                ax.set_title({'csv_line':f"{plan.get('y','')} / {plan.get('x','')}",'convergence':'实测步长与能量误差','pdb_structure':'PDB 结构与所选重原子','distribution':'原始记录分布','experiment_timeline':'外层状态与后台函数记录','test_matrix':'同组测试前后状态','code_excerpt':'实际代码节选'}.get(kind,''),loc='left',pad=18,fontweight='bold')
                fig.savefig(target,dpi=180,facecolor='white')
        finally: plt.close(fig)
    asset={'id':str(plan['id']),'path':str(target),'sha256':digest(target),'sources':sources,'parameters':plan,
           'caption':observed_caption(kind,plan,measured,paths),'proposed_caption':str(plan.get('caption') or ''),'finding_proposal':str(plan.get('why') or ''),
           'required':plan.get('required',True),'renderer_version':RENDERER_VERSION,'script':str(script_snapshot),
           'script_sha256':RENDERER_SHA256,'measured':measured,'verified':True}
    verify_asset(asset); write_json(receipt,asset)
    return asset


def _atoms(path):
    atoms=[]
    for line in Path(path).read_text().splitlines():
        if line.startswith('ENDMDL'): break
        if line.startswith(('ATOM  ','HETATM')):
            name=line[12:16].strip()
            if name.startswith('H'): continue
            atoms.append({'name':name,'residue':int(line[22:26]),'chain':line[21:22].strip(),
                          'xyz':[float(line[30:38]),float(line[38:46]),float(line[46:54])]})
    return atoms


def observed_caption(kind,plan,measured,paths):
    """Describe only pixels actually rendered; proposed interpretation is separate."""
    source='来源：'+ '、'.join(p.name for p in paths[:3])+'。'
    if kind=='video_frame':
        t=measured['requested_timestamp_seconds'];return f'原视频 {int(t//60):02d}:{t%60:04.1f} 附近的实际画面。画面与字幕内容仍以原视频为准。'+source
    if kind=='pdb_structure':
        selection=f"链 {measured.get('chain') or '全部'}；所选残基编号："+','.join(map(str,measured['residues']))+'。'
        pose='粉色为配体 MODEL 1 重原子；未绘制化学键或氢键。' if measured.get('pose_atoms') else ''
        return '蓝色为蛋白 Cα 轨迹，橙色为所选残基重原子；坐标单位 Å。'+selection+pose+source
    if kind=='csv_line':
        transform='减去各组初值后的 ' if plan.get('subtract_initial') else ''
        return f"原始数据列 {plan['x']} 与{transform}{plan['y']} 的关系；仅展示文件覆盖范围。"+source
    if kind=='convergence':return f"从各 CSV 重算最大相对能量误差。对 {len(measured['points'])} 个实测点作对数拟合，斜率 {measured['log_log_slope']:.4f}；不能据此证明长期稳定性或一般收敛阶。"+source
    if kind=='molecular_diversity':return f"由 {measured['molecule_count']} 个实际候选重算 {measured['pair_count']} 对 Morgan 指纹相似度及 {measured['scaffold_count']} 种 Murcko 骨架；这是集合结构差异，不是药效或跨批次改善证明。"+source
    if kind=='molecule_grid':return '按原始 SMILES 绘制的所选候选二维结构；下方标出原候选编号，不表示已证实结合或抑制活性。'+source
    if kind=='distribution':return f"数据字段 {plan['value_key']} 的分布，共 {measured['count']} 个实际记录；没有绘制跨批次因果比较。"+source
    if kind=='experiment_timeline':return '蓝色为 Future 状态采样，橙色为后台函数记录。两条轨道使用各自记录的时钟；位置不能直接解释为同步绝对时间。'+source
    if kind=='test_matrix':return '同一组测试的实际状态对照，绿为通过、红为失败；横轴编号对应报告中的用例说明。没有性能测量。'+source
    if kind=='code_excerpt':return f"原始代码第 {measured['start_line']}–{measured['end_line']} 行的原文节选。"+source
    return '原始图像转为可嵌入格式；未改写图像内容。'+source
