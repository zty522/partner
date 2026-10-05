"""改进模式路由修复：显式 improvement mode 无论是否给 scope，都必须选择专用根 Flow。"""
from __future__ import annotations

from typing import Any


def route_improvement_mode(params: dict[str, Any]) -> dict[str, Any]:
    """修复改进模式路由：显式 improvement mode 必须选择专用根 Flow。"""
    task_type = params.get('task_type', '')
    scope = params.get('scope', '')
    
    # 显式 improvement mode 映射
    improvement_modes = {
        'learning_improvement': 'learning_improvement_cycle',
        'self_improvement': 'self_improvement_cycle',
        'active_learning': 'learning_improvement_cycle',
        'partner_self_evolution': 'self_improvement_cycle'
    }
    
    # 如果 task_type 是改进模式，直接返回对应的根 Flow
    if task_type in improvement_modes:
        return {
            'ok': True,
            'root_flow': improvement_modes[task_type],
            'mode': task_type,
            'summary': f"改进模式路由：{task_type} → {improvement_modes[task_type]}"
        }
    
    # 如果 scope 是 partner 且 task_type 匹配
    if scope == 'partner' and task_type in improvement_modes:
        return {
            'ok': True,
            'root_flow': improvement_modes[task_type],
            'mode': task_type,
            'summary': f"改进模式路由（scope=partner）：{task_type} → {improvement_modes[task_type]}"
        }
    
    # 不是改进模式，返回普通路由
    return {
        'ok': True,
        'root_flow': 'project_iteration',
        'mode': 'project',
        'summary': "普通项目路由"
    }
