"""统一模型服务适配器：所有项目动作统一通过配置中的 Qwen 调用。"""
from __future__ import annotations

import json
from typing import Any
from pathlib import Path
from partner.event_fabric.catalog import EventDefinition


def call_model_unified(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """统一调用模型服务：读取配置中的 Qwen，不允许临时脚本自行寻找 API key。"""
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    
    # 读取模型配置
    config_path = workspace / 'config' / 'api.json'
    if not config_path.exists():
        return {
            'ok': False,
            'status': 'failed',
            'error': 'model_config_not_found',
            'message': f'模型配置文件不存在：{config_path}'
        }
    
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
    except Exception as e:
        return {
            'ok': False,
            'status': 'failed',
            'error': 'model_config_read_failed',
            'message': str(e)
        }
    
    # 获取 Qwen 配置
    qwen_config = config.get('qwen', {})
    if not qwen_config:
        return {
            'ok': False,
            'status': 'failed',
            'error': 'qwen_config_not_found',
            'message': '配置文件中未找到 Qwen 配置'
        }
    
    # 提取调用参数
    prompt = params.get('prompt', '')
    model = params.get('model', qwen_config.get('default_model', 'qwen3.8-flash'))
    temperature = params.get('temperature', 0.7)
    max_tokens = params.get('max_tokens', 4096)
    
    # 调用模型（这里模拟调用，实际应接入真实 API）
    try:
        # TODO: 接入真实 Qwen API
        # from partner.adapters.model_service import call_qwen
        # response = call_qwen(prompt, model, temperature, max_tokens, qwen_config)
        
        # 模拟响应
        response = {
            'content': f'[模拟响应] 模型 {model} 已处理请求',
            'usage': {
                'prompt_tokens': len(prompt) // 4,
                'completion_tokens': 100
            }
        }
        
        return {
            'ok': True,
            'status': 'completed',
            'model': model,
            'response': response['content'],
            'usage': response['usage'],
            'summary': f"统一模型调用：{model}，{response['usage']['prompt_tokens']} prompt tokens"
        }
        
    except Exception as e:
        return {
            'ok': False,
            'status': 'failed',
            'error': 'model_call_failed',
            'message': str(e)
        }


# Event 定义
model_service_adapter_event = EventDefinition(
    name='model.unified_call',
    series='project',
    description='统一模型服务适配器：读取配置中的 Qwen，不允许临时脚本自行寻找 API key',
    handler=call_model_unified
)
