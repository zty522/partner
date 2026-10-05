"""主动学习新来源恢复：novel_source_count=0 时重新选源或生成 no-new-source Settlement。"""
from __future__ import annotations

import json
from typing import Any
from pathlib import Path
from partner.event_fabric.catalog import EventDefinition


def learning_source_recover(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """当 novel_source_count=0 时，重新选源或生成 no-new-source Settlement。"""
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    job_id = str(getattr(ctx, 'job_id', ''))
    
    novel_source_count = params.get('novel_source_count', 0)
    max_reselction_attempts = params.get('max_reselection_attempts', 3)
    current_attempt = params.get('current_reselection_attempt', 0)
    
    # 读取阅读账本
    reading_ledger_path = workspace / 'state' / 'reading_ledger.json'
    reading_ledger = []
    if reading_ledger_path.exists():
        try:
            reading_ledger = json.loads(reading_ledger_path.read_text(encoding='utf-8'))
        except Exception:
            pass
    
    # 读取可用来源索引
    source_index_path = workspace / 'external' / 'insights' / 'discovery_index.jsonl'
    available_sources = []
    if source_index_path.exists():
        try:
            with open(source_index_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            source = json.loads(line)
                            available_sources.append(source)
                        except Exception:
                            pass
        except Exception:
            pass
    
    # 已读来源集合
    read_sources = {item.get('source_id') for item in reading_ledger if item.get('source_id')}
    
    # 如果还有新来源可选
    if novel_source_count > 0:
        return {
            'ok': True,
            'status': 'completed',
            'action': 'continue_with_novel_sources',
            'novel_source_count': novel_source_count,
            'summary': f"继续处理 {novel_source_count} 个新来源"
        }
    
    # novel_source_count=0，尝试重新选源
    if current_attempt < max_reselction_attempts:
        # 从未读来源中选择
        unread_sources = [s for s in available_sources if s.get('id') not in read_sources]
        
        if unread_sources:
            # 选择前 3 个未读来源
            new_selection = unread_sources[:3]
            return {
                'ok': True,
                'status': 'completed',
                'action': 'reselect_sources',
                'new_sources': new_selection,
                'attempt': current_attempt + 1,
                'summary': f"重新选择 {len(new_selection)} 个未读来源（第 {current_attempt + 1} 次尝试）"
            }
        else:
            # 没有未读来源，生成 no-new-source Settlement
            return _generate_no_new_source_settlement(ctx, params, reading_ledger, available_sources)
    
    # 超过最大尝试次数，生成 no-new-source Settlement
    return _generate_no_new_source_settlement(ctx, params, reading_ledger, available_sources)


def _generate_no_new_source_settlement(
    ctx: Any,
    params: dict[str, Any],
    reading_ledger: list,
    available_sources: list
) -> dict[str, Any]:
    """生成 no-new-source Settlement。"""
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    job_id = str(getattr(ctx, 'job_id', ''))
    
    settlement = {
        'job_id': job_id,
        'status': 'no_new_source',
        'reason': 'all_available_sources_already_read',
        'total_sources_read': len(reading_ledger),
        'total_sources_available': len(available_sources),
        'conclusion': '所有可用来源已读取，无新来源可学习',
        'recommendation': '建议补充新的外部来源或调整学习方向'
    }
    
    # 保存 Settlement
    settlement_path = workspace / 'state' / 'learning_settlements' / f'{job_id}.json'
    settlement_path.parent.mkdir(parents=True, exist_ok=True)
    settlement_path.write_text(json.dumps(settlement, indent=2, ensure_ascii=False), encoding='utf-8')
    
    return {
        'ok': True,
        'status': 'completed',
        'action': 'no_new_source_settlement',
        'settlement': settlement,
        'summary': "生成 no-new-source Settlement：所有可用来源已读取"
    }


# Event 定义
learning_source_recovery_event = EventDefinition(
    name='learning.source_recovery',
    series='project',
    description='主动学习新来源恢复：novel_source_count=0 时重新选源或生成 Settlement',
    handler=learning_source_recover
)
