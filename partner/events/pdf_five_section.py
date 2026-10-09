"""PDF 五段式格式化：核心结论、研究问题、方法与结果、局限与未解决、下一步。"""
from __future__ import annotations

import json
import re
from typing import Any
from pathlib import Path
from partner.event_fabric.catalog import EventDefinition


def _sanitize_user_facing_content(content: Any) -> Any:
    """Remove internal file names and evidence indices from content."""
    if isinstance(content, str):
        # Remove patterns like eligibility_0001.json, .py, .md files
        content = re.sub(r'\b[a-zA-Z0-9_]+\.json\b', '', content)
        content = re.sub(r'\b[a-zA-Z0-9_]+\.py\b', '', content)
        content = re.sub(r'\b[a-zA-Z0-9_]+\.md\b', '', content)
        # Remove evidence index markers ([E01], [E编号]); keep bracketed
        # business text such as "[Example]" untouched.
        content = re.sub(r'\[E\s*编号\s*\]', '', content)
        content = re.sub(r'\[E\d+\]', '', content)
        # Clean up extra spaces left by removals
        content = re.sub(r'\s+', ' ', content).strip()
        return content
    elif isinstance(content, dict):
        return {k: _sanitize_user_facing_content(v) for k, v in content.items()}
    elif isinstance(content, list):
        return [_sanitize_user_facing_content(item) for item in content]
    return content

def format_pdf_five_section(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """将报告内容格式化为五段式结构。"""
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    job_id = str(getattr(ctx, 'job_id', ''))
    
    # 提取各段内容
    core_conclusion = params.get('core_conclusion', '')
    research_question = params.get('research_question', '')
    methods_and_results = params.get('methods_and_results', {})
    limitations = params.get('limitations', [])
    next_steps = params.get('next_steps', [])
    
    # Sanitize inputs to remove internal jargon/files/indices
    core_conclusion = _sanitize_user_facing_content(core_conclusion)
    research_question = _sanitize_user_facing_content(research_question)
    methods_and_results = _sanitize_user_facing_content(methods_and_results)
    limitations = _sanitize_user_facing_content(limitations)
    next_steps = _sanitize_user_facing_content(next_steps)
    
    # 如果研究问题为空，从 Job 请求中提取
    if not research_question:
        job_request_path = workspace / 'state' / 'jobs' / f'{job_id}.json'
        if job_request_path.exists():
            try:
                job_data = json.loads(job_request_path.read_text(encoding='utf-8'))
                research_question = job_data.get('original_request', '未提供研究问题')
            except Exception:
                research_question = '未提供研究问题'
    
    # 构建五段式内容
    sections = {
        'core_conclusion': {
            'title': '核心结论',
            'content': core_conclusion[:200] if len(core_conclusion) > 200 else core_conclusion
        },
        'research_question': {
            'title': '研究问题',
            'content': research_question
        },
        'methods_and_results': {
            'title': '方法与结果',
            'content': methods_and_results,
            'data_sources': params.get('data_sources', [])
        },
        'limitations': {
            'title': '局限与未解决',
            'content': limitations
        },
        'next_steps': {
            'title': '下一步',
            'content': next_steps
        }
    }
    
    # 生成 Markdown 格式
    markdown = _generate_markdown(sections)
    
    # 保存格式化结果
    output_path = workspace / 'state' / 'pdf_sections' / f'{job_id}.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sections, indent=2, ensure_ascii=False), encoding='utf-8')
    
    return {
        'ok': True,
        'status': 'completed',
        'sections': sections,
        'markdown': markdown,
        'output_path': str(output_path),
        'summary': 'PDF 五段式格式化完成'
    }


def _generate_markdown(sections: dict) -> str:
    """生成 Markdown 格式的 PDF 内容。"""
    lines = []
    
    # 核心结论
    lines.append(f"# {sections['core_conclusion']['title']}\n")
    lines.append(f"{sections['core_conclusion']['content']}\n")
    
    # 研究问题
    lines.append(f"\n# {sections['research_question']['title']}\n")
    lines.append(f"{sections['research_question']['content']}\n")
    
    # 方法与结果
    lines.append(f"\n# {sections['methods_and_results']['title']}\n")
    if isinstance(sections['methods_and_results']['content'], dict):
        for key, value in sections['methods_and_results']['content'].items():
            lines.append(f"## {key}\n{value}\n")
    else:
        lines.append(f"{sections['methods_and_results']['content']}\n")
    
    # 数据来源
    if sections['methods_and_results'].get('data_sources'):
        lines.append("\n**数据来源：**\n")
        for source in sections['methods_and_results']['data_sources']:
            lines.append(f"- {source}\n")
    
    # 局限与未解决
    lines.append(f"\n# {sections['limitations']['title']}\n")
    if isinstance(sections['limitations']['content'], list):
        for item in sections['limitations']['content']:
            lines.append(f"- {item}\n")
    else:
        lines.append(f"{sections['limitations']['content']}\n")
    
    # 下一步
    lines.append(f"\n# {sections['next_steps']['title']}\n")
    if isinstance(sections['next_steps']['content'], list):
        for item in sections['next_steps']['content']:
            lines.append(f"- {item}\n")
    else:
        lines.append(f"{sections['next_steps']['content']}\n")
    
    return '\n'.join(lines)


# Event 定义
pdf_five_section_event = EventDefinition(
    name='pdf.format_five_section',
    series='project',
    description='PDF 五段式格式化：核心结论、研究问题、方法与结果、局限与未解决、下一步',
    handler=format_pdf_five_section
)
