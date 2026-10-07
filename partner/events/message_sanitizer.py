"""消息清洗与格式化：过滤内部信息，只保留用户可读内容。"""
from __future__ import annotations

import re
from typing import Any


def sanitize_message(text: str) -> str:
    """过滤掉内部路径、bytes、Event 名称、步骤计数等内部信息。"""
    if not text:
        return text
    
    result = text
    
    # 过滤绝对路径
    result = re.sub(r'/mnt/[a-z]/[^\s,，。！？；：\u201c\u201d\u2018\u2019（）\[\]【】]+', '[路径已隐藏]', result)
    result = re.sub(r'[A-Z]:\\[^\s,，。！？；：\u201c\u201d\u2018\u2019（）\[\]【】]+', '[路径已隐藏]', result)
    
    # 过滤 bytes 数（含 [bytes=N] 与 "bytes: N" 两种形态）
    result = re.sub(r'\[bytes?\s*[:=]?\s*\d{1,3}(?:,\d{3})*\]', '[数据量已隐藏]', result, flags=re.IGNORECASE)
    result = re.sub(r'\b(?:bytes?|字节)\s*[:=]?\s*\d{1,3}(?:,\d{3})*\b', '[数据量已隐藏]', result, flags=re.IGNORECASE)
    result = re.sub(r'\b\d{1,3}(?:,\d{3})*\s*(?:bytes?|字节)\b', '[数据量已隐藏]', result, flags=re.IGNORECASE)

    # 业务产物 / 执行动作 整块折叠为一句用户可读说明
    result = re.sub(r'【业务产物】[^\n；;]*', '【产出】已生成数据文件', result)
    result = re.sub(r'【执行动作】[^\n；;]*', '【执行】已执行本轮研究动作', result)

    # 内部术语替换为通俗词（保留句意，面向用户）
    term_map = {
        'handoff': '交接记录', 'baseline': '基线结果', 'candidate': '改进方案',
        'benchmark': '基准测试', '结算': '阶段总结', '准入': '输入审核',
        '消费审计': '数据审核', 'dry-run': '预演', '回归测试': '兼容性测试',
        'Shadow': '灰度', '验证层': '验证', '实时性约束': '时效性要求',
        '转移映射': '学习成果映射',
    }
    for term, repl in term_map.items():
        result = result.replace(term, repl)
    result = re.sub(r'\bFlow\b', '流程', result)
    result = re.sub(r'\bEvent\b', '步骤', result)

    # 内部步骤进度短语折叠
    result = re.sub(r'完成（第\s*\d+\s*/\s*\d+\s*步）[：:][^\n]*', '进度：内部流程步骤已完成。', result)
    
    # 过滤 Event 名称（常见内部 Event）
    event_names = [
        'local_read', 'local_compare', 'source_extraction', 'handoff',
        'project_cycle_round', 'learning_improvement_cycle', 'self_improvement_cycle',
        'context_recall', 'state_inspect', 'plan_propose', 'action_execute',
        'outcome_verify', 'outcome_reflect', 'settlement', 'input_resolve',
        'input_eligibility', 'round_design', 'round_blueprint_critic'
    ]
    for event in event_names:
        result = re.sub(rf'\b{event}\b', '[处理步骤]', result)
    
    # 过滤步骤计数
    result = re.sub(r'第\s*\d+\s*/\s*\d+\s*步', '[进度]', result)
    result = re.sub(r'\bstep\s+\d+\s*/\s*\d+\b', '[进度]', result, flags=re.IGNORECASE)
    
    # 过滤 Job ID、Flow ID
    result = re.sub(r'job_[a-f0-9]{8,}', '[任务]', result)
    result = re.sub(r'flow_[a-f0-9]{8,}', '[流程]', result)
    
    # 过滤内部状态码
    result = re.sub(r'\b(?:completed|failed|dispatched|running)\s*=\s*(?:true|false)\b', '[状态]', result, flags=re.IGNORECASE)
    
    return result


def format_user_message(
    what_done: str,
    what_got: str,
    next_step: str,
    why: str
) -> str:
    """格式化用户消息，只说四件事。"""
    parts = []
    if what_done:
        parts.append(f"【做了什么】\n{what_done}")
    if what_got:
        parts.append(f"\n【得到什么】\n{what_got}")
    if next_step:
        parts.append(f"\n【下一步】\n{next_step}")
    if why:
        parts.append(f"\n【为什么】\n{why}")
    return '\n'.join(parts)


def format_failure_message(
    what_tried: list[str],
    why_failed: str,
    how_recover: str
) -> str:
    """格式化失败消息，解释三件事。"""
    parts = []
    if what_tried:
        tried_list = '\n'.join(f"  • {item}" for item in what_tried)
        parts.append(f"【尝试了什么】\n{tried_list}")
    if why_failed:
        parts.append(f"\n【为什么失败】\n{why_failed}")
    if how_recover:
        parts.append(f"\n【下一步怎么恢复】\n{how_recover}")
    return '\n'.join(parts)


# Event 定义
from partner.event_fabric.catalog import EventDefinition

def message_sanitize_handler(ctx: Any, params: dict) -> dict:
    """消息清洗 Event 处理器"""
    # 从上游节点（deduplicate）的输出中读取消息文本
    # runner.py 将上游输出放在 params['previous'] 中
    previous = params.get('previous', {})
    text = previous.get('message', '') or params.get('message', '') or params.get('text', '')
    
    cleaned = sanitize_message(text)
    return {
        'ok': True,
        'status': 'completed',
        'cleaned_text': cleaned,
        'original_length': len(text),
        'cleaned_length': len(cleaned)
    }

message_sanitize_event = EventDefinition(
    name='message.sanitizer',
    series='project',
    description='消息清洗：过滤内部路径、bytes、Event 名称等',
    handler=message_sanitize_handler
)
