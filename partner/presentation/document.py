"""Shared figure block contract for drafting, review, rendering and revisions."""
import re


def figure_assets(outputs):
    return (outputs.get('visuals',{}).get('semantic_output') or {}).get('images',[])


def figure_errors(text,outputs):
    assets=figure_assets(outputs); known={a['id'] for a in assets}
    used=re.findall(r'\[\[figure:([\w-]+)\]\]',text)
    errors=[f'unknown figure: {i}' for i in set(used)-known]
    errors += [f"required figure {a['id']} must appear once inline as [[figure:{a['id']}]]" for a in assets if a.get('required',True) and used.count(a['id'])!=1]
    if assets and not used: errors.append('illustrated report has no inline figures')
    return errors


def visual_context(outputs):
    """Do not recycle speculative plan prose as verified image evidence."""
    return [{'id':a['id'],'path':a['path'],'caption':a['caption'],'measured':a.get('measured',{}),
             'required':a.get('required',True),'sources':a.get('sources',[])} for a in figure_assets(outputs)]


def measured_claim_errors(text,outputs):
    """Small deterministic contradictions a prose reviewer must not overlook."""
    errors=[];assets=figure_assets(outputs)
    durations=[a.get('measured',{}).get('video_duration_seconds') for a in assets]
    durations=[d for d in durations if d]
    if durations:
        title=next((line for line in text.splitlines() if line.startswith('# ')),'')
        for minutes in re.findall(r'(\d+(?:\.\d+)?)\s*分钟',title):
            if abs(float(minutes)*60-durations[0])>max(60,durations[0]*.1):
                errors.append(f'标题时长与实际视频{durations[0]:.2f}秒不符；删掉无依据的分钟数')
    if assets:
        first=re.search(r'\[\[figure:[\w-]+\]\]',text)
        if first and first.start()>850: errors.append('首图之前正文太长，请将关键图放在简短核心结论之后')
    return errors


def readability_errors(text):
    import re
    body=re.split(r'(?m)^#{1,3}\s*(?:证据索引|来源|附录|参考)',text)[0]
    body=re.sub(r'```.*?```','',body,flags=re.S)
    bad=re.findall(r'\b[a-f0-9]{24,}\b|experiment_[a-f0-9]{12,}|production_effective|manifest_sha256|exit_code',body)
    return ['正文仍有内部记录字段/长标识，请译成中文结果并将追溯编号留在证据附录：'+', '.join(sorted(set(bad)))] if bad else []


def semantic_conflicts(text,outputs):
    """Check distinctions grounded in typed measurements, independent of prose LLMs."""
    errors=[];assets=figure_assets(outputs)
    if any(a.get('parameters',{}).get('value_key')=='score' for a in assets) and re.search(r'(?<!估计)(?<!预测)结合能',text):
        errors.append('score是计算评分，须称对接评分或明确为估计值，不能直接写成实测结合能')
    if any(a.get('parameters',{}).get('kind')=='convergence' for a in assets):
        if re.search(r'仅限[^。\n]{0,35}端点',text):
            errors.append('最大能量误差来自完整采样区间，不是仅在端点计算的误差；终点对齐只是比较条件')
    for asset in assets:
        if asset.get('parameters',{}).get('kind')!='experiment_timeline':continue
        if re.search(r'互不触发|完全独立|毫无因果',text):errors.append('不同状态不等于没有因果联系，wait_for超时可触发取消；不得称互不触发')
        groups=asset.get('measured',{}).get('groups',{})
        counts={g.get('function_tick_count') for g in groups.values()}-{None}
        if not counts:
            # Older genuine manifests retain source hashes; read the source to
            # disambiguate sampled tick records from the final return record.
            import json
            from pathlib import Path
            for source in asset.get('sources',[]):
                if Path(source['path']).suffix=='.json':
                    try:
                        data=json.loads(Path(source['path']).read_text())
                        counts={sum(t.get('phase')=='tick' for t in g['func_ticks']) for g in data.get('groups',{}).values()}
                    except (OSError,ValueError,KeyError):pass
        for value in re.findall(r'(\d+)\s*(?:帧|步|个tick|个 tick)',text):
            if counts and int(value) not in counts:errors.append(f'函数tick数为{sorted(counts)}，不能把含返回标记的总记录数称为{value}步/帧')
    return errors


def localize_prose(text,outputs=None):
    """Translate internal receipt labels before fact review; leave code blocks intact."""
    if any(a.get('parameters',{}).get('value_key')=='score' for a in figure_assets(outputs or {})):
        text=text.replace('最佳结合能','最佳对接评分')
    pieces=re.split(r'(```.*?```)',text,flags=re.S)
    for index,part in enumerate(pieces):
        if part.startswith('```'):continue
        part=re.sub(r'`?production_effective\s*=\s*false`?','历史收据中的生产生效标识为否',part,flags=re.I)
        part=re.sub(r'`?production_effective\s*=\s*true`?','该收据中的生产生效标识为是',part,flags=re.I)
        part=part.replace('production_effective','生产生效标识').replace('exit_code','进程退出码').replace('manifest_sha256','实验清单校验值')
        pieces[index]=part
    return ''.join(pieces)
