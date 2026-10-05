"""Token 汇总器：按 Job 汇总每个 model call receipt，报告有效证据/100k token。"""
from __future__ import annotations

import json
from typing import Any
from pathlib import Path
from partner.event_fabric.catalog import EventDefinition


def aggregate_tokens(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """按 Job 汇总每个 model call receipt，报告有效证据/100k token。"""
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    job_id = str(getattr(ctx, 'job_id', ''))
    
    # 读取 API 调用日志
    api_log_path = workspace / 'state' / 'api_log.jsonl'
    total_prompt_tokens = 0
    total_completion_tokens = 0
    call_count = 0
    failed_calls = 0
    duplicate_prompts = 0
    prompt_hashes = set()
    
    if api_log_path.exists():
        try:
            with open(api_log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            call = json.loads(line)
                            if call.get('job_id') == job_id:
                                call_count += 1
                                total_prompt_tokens += call.get('prompt_tokens', 0)
                                total_completion_tokens += call.get('completion_tokens', 0)
                                
                                if call.get('status') == 'failed':
                                    failed_calls += 1
                                
                                # 检查重复 prompt
                                prompt_hash = call.get('prompt_hash', '')
                                if prompt_hash:
                                    if prompt_hash in prompt_hashes:
                                        duplicate_prompts += 1
                                    else:
                                        prompt_hashes.add(prompt_hash)
                        except Exception:
                            pass
        except Exception:
            pass
    
    # 计算有效证据数量
    evidence_count = params.get('evidence_count', 0)
    
    # 计算有效证据/100k token
    total_tokens = total_prompt_tokens + total_completion_tokens
    evidence_per_100k = (evidence_count / (total_tokens / 100000)) if total_tokens > 0 else 0
    
    # 计算失败调用占比
    failure_rate = (failed_calls / call_count) if call_count > 0 else 0
    
    # 计算重复 prompt 比例
    duplicate_rate = (duplicate_prompts / call_count) if call_count > 0 else 0
    
    return {
        'ok': True,
        'status': 'completed',
        'job_id': job_id,
        'total_tokens': total_tokens,
        'prompt_tokens': total_prompt_tokens,
        'completion_tokens': total_completion_tokens,
        'call_count': call_count,
        'failed_calls': failed_calls,
        'failure_rate': failure_rate,
        'duplicate_prompts': duplicate_prompts,
        'duplicate_rate': duplicate_rate,
        'evidence_count': evidence_count,
        'evidence_per_100k_tokens': evidence_per_100k,
        'summary': f"Token 汇总：{total_tokens} tokens，{call_count} 次调用，{evidence_per_100k:.2f} 证据/100k token"
    }


# Event 定义
token_aggregator_event = EventDefinition(
    name='metrics.token_aggregate',
    series='project',
    description='Token 汇总器：按 Job 汇总 token，报告有效证据/100k token',
    handler=aggregate_tokens
)
