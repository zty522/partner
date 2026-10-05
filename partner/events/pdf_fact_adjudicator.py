"""PDF 跨产物事实裁决：检查原始字段为 N/A/failed/unknown 时，禁止强结论。"""
from __future__ import annotations

import re
import json
from typing import Any
from pathlib import Path
from partner.event_fabric.catalog import EventDefinition


def pdf_fact_adjudicate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """跨产物事实裁决：检测 CSV/JSON 中的 N/A/failed/unknown，禁止标题和结论中出现强结论。"""
    job_id = str(getattr(ctx, 'job_id', ''))
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    
    # 读取数据文件
    data_files = params.get('data_files', [])
    title = params.get('title', '')
    conclusion = params.get('conclusion', '')
    
    # 检查数据文件中是否有 N/A/failed/unknown
    weak_evidence = []
    for data_file in data_files:
        file_path = workspace / data_file
        if file_path.exists() and file_path.suffix == '.csv':
            content = file_path.read_text(encoding='utf-8')
            # 检查 N/A、failed、unknown
            if re.search(r'\bN/A\b|failed|unknown', content, re.IGNORECASE):
                weak_evidence.append({
                    'file': data_file,
                    'issue': 'contains_na_or_failed_or_unknown'
                })
    
    # 定义强结论词汇
    strong_conclusion_words = [
        '验证', '排除', '支持', '证明', '确认', '巩固', '稳定',
        'verified', 'excluded', 'supported', 'proven', 'confirmed', 'consolidated', 'stable'
    ]
    
    # 检查标题和结论中是否出现强结论词汇
    violations = []
    if weak_evidence:
        for word in strong_conclusion_words:
            if word in title or word in conclusion:
                violations.append({
                    'word': word,
                    'location': 'title' if word in title else 'conclusion',
                    'weak_evidence_files': [e['file'] for e in weak_evidence]
                })
    
    # 如果有违规，生成修正建议
    if violations:
        corrected_title = _weaken_title(title)
        corrected_conclusion = _weaken_conclusion(conclusion)
        
        return {
            'ok': True,
            'status': 'completed',
            'has_violations': True,
            'violations': violations,
            'weak_evidence': weak_evidence,
            'corrected_title': corrected_title,
            'corrected_conclusion': corrected_conclusion,
            'summary': f"事实裁决：发现 {len(violations)} 处违规，已生成修正建议"
        }
    
    return {
        'ok': True,
        'status': 'completed',
        'has_violations': False,
        'summary': "事实裁决：无违规"
    }


def _weaken_title(title: str) -> str:
    """弱化标题中的强结论。"""
    # 替换强结论词汇
    replacements = {
        '验证': '探索了',
        '排除': '未确认',
        '支持': '初步显示',
        '证明': '提示',
        '确认': '初步确认',
        '巩固': '初步巩固',
        '稳定': '初步稳定'
    }
    
    result = title
    for strong, weak in replacements.items():
        if strong in result:
            result = result.replace(strong, weak)
            break  # 只替换第一个
    
    return result


def _weaken_conclusion(conclusion: str) -> str:
    """弱化结论中的强结论。"""
    # 添加限定词
    if not any(word in conclusion for word in ['但', '然而', '不过', '但是']):
        conclusion = conclusion.rstrip('。.') + '，但证据尚不充分。'
    
    return conclusion


# Event 定义
pdf_fact_adjudicator_event = EventDefinition(
    name='pdf.fact_adjudicate',
    series='project',
    description='PDF 跨产物事实裁决：检测弱证据，禁止强结论',
    handler=pdf_fact_adjudicate
)
