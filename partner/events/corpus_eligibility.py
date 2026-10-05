"""语料资格冻结 Event：先冻结"什么算合格输入"，再从中选择。"""
from __future__ import annotations

import json
import re
from typing import Any
from pathlib import Path
from partner.event_fabric.catalog import EventDefinition


def corpus_eligibility_freeze(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """冻结合格语料集合：只有满足条件的文件才能进入后续评分/分析。"""
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    job_id = str(getattr(ctx, 'job_id', ''))
    
    # 读取任务类型和冻结标准
    task_type = params.get('task_type', 'generic')
    eligibility_criteria = params.get('eligibility_criteria', {})
    candidate_files = params.get('candidate_files', [])
    
    # 根据任务类型确定默认标准
    if task_type == 'scientific_content':
        # 科研内容任务：只接受论文、科研文献
        default_criteria = {
            'required_extensions': ['.pdf', '.md', '.txt'],
            'required_keywords': ['论文', '研究', '实验', '方法', '结果', 'paper', 'research', 'method', 'result'],
            'excluded_patterns': ['project_brief', 'src_00', 'README', 'config', 'note'],
            'min_length_chars': 500
        }
    elif task_type == 'molecular':
        default_criteria = {
            'required_extensions': ['.csv', '.json', '.sdf', '.mol'],
            'required_keywords': ['分子', '靶点', '对接', 'molecule', 'target', 'docking'],
            'excluded_patterns': [],
            'min_length_chars': 100
        }
    else:
        default_criteria = {
            'required_extensions': [],
            'required_keywords': [],
            'excluded_patterns': [],
            'min_length_chars': 0
        }
    
    # 合并用户标准和默认标准
    criteria = {**default_criteria, **eligibility_criteria}
    
    # 过滤合格文件
    eligible = []
    ineligible = []
    
    for file_path in candidate_files:
        path = workspace / file_path if not Path(file_path).is_absolute() else Path(file_path)
        
        # 检查扩展名
        if criteria['required_extensions'] and path.suffix not in criteria['required_extensions']:
            ineligible.append({'file': file_path, 'reason': 'extension_not_allowed'})
            continue
        
        # 检查排除模式
        if any(pat in path.name for pat in criteria['excluded_patterns']):
            ineligible.append({'file': file_path, 'reason': 'excluded_pattern'})
            continue
        
        # 检查最小长度
        if path.exists() and criteria['min_length_chars'] > 0:
            try:
                content = path.read_text(encoding='utf-8')
                if len(content) < criteria['min_length_chars']:
                    ineligible.append({'file': file_path, 'reason': 'too_short'})
                    continue
            except Exception:
                ineligible.append({'file': file_path, 'reason': 'unreadable'})
                continue
        
        # 检查关键词（如果有）
        if criteria['required_keywords'] and path.exists():
            try:
                content = path.read_text(encoding='utf-8').lower()
                if not any(kw.lower() in content for kw in criteria['required_keywords']):
                    ineligible.append({'file': file_path, 'reason': 'no_required_keywords'})
                    continue
            except Exception:
                pass
        
        eligible.append(file_path)
    
    # 保存冻结结果
    freeze_path = workspace / 'state' / 'corpus_freezes' / f'{job_id}.json'
    freeze_path.parent.mkdir(parents=True, exist_ok=True)
    freeze_data = {
        'job_id': job_id,
        'task_type': task_type,
        'criteria': criteria,
        'eligible_files': eligible,
        'ineligible_files': ineligible,
        'eligible_count': len(eligible),
        'ineligible_count': len(ineligible)
    }
    freeze_path.write_text(json.dumps(freeze_data, indent=2, ensure_ascii=False), encoding='utf-8')
    
    return {
        'ok': True,
        'status': 'completed',
        'eligible_files': eligible,
        'ineligible_files': ineligible,
        'eligible_count': len(eligible),
        'ineligible_count': len(ineligible),
        'criteria': criteria,
        'summary': f"语料资格冻结：{len(eligible)} 个合格，{len(ineligible)} 个不合格"
    }


# Event 定义
corpus_eligibility_event = EventDefinition(
    name='corpus.eligibility_freeze',
    series='project',
    description='冻结合格语料集合：先定义"什么算合格输入"，再从中选择',
    handler=corpus_eligibility_freeze
)
