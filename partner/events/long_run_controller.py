"""LongRunController Event：统一维护 deadline、问题图、已尝试动作、连续低信息计数。"""
from __future__ import annotations

import json
import time
from typing import Any
from pathlib import Path
from datetime import datetime, timezone
from partner.event_fabric.catalog import EventDefinition


def long_run_control(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """统一控制长期运行的 deadline、问题图、候选队列和收尾预算。"""
    job_id = str(getattr(ctx, 'job_id', ''))
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    
    # 读取或初始化控制器状态
    controller_path = workspace / 'state' / 'long_run_controllers' / f'{job_id}.json'
    controller_path.parent.mkdir(parents=True, exist_ok=True)
    
    if controller_path.exists():
        try:
            state = json.loads(controller_path.read_text(encoding='utf-8'))
        except Exception:
            state = _init_controller_state(params)
    else:
        state = _init_controller_state(params)
    
    # 更新已尝试动作
    current_action = params.get('current_action_signature', '')
    if current_action and current_action not in state['tried_actions']:
        state['tried_actions'].append(current_action)
    
    # 更新连续低信息计数
    reward = params.get('last_reward', 0)
    if reward <= 0:
        state['consecutive_low_info'] += 1
    else:
        state['consecutive_low_info'] = 0
    
    # 检查是否应该结束
    should_terminate, reason = _check_termination(state, params)
    
    # 如果需要换臂，选择不同未解决节点
    next_action = None
    if state['consecutive_low_info'] >= 3:
        next_action = _select_different_arm(state, params)
        state['consecutive_low_info'] = 0  # 重置计数
    
    state['last_check'] = datetime.now(timezone.utc).isoformat()
    state['check_count'] += 1
    
    # 保存状态
    controller_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8')
    
    return {
        'ok': True,
        'status': 'completed',
        'should_terminate': should_terminate,
        'termination_reason': reason,
        'next_action': next_action,
        'controller_state': state,
        'summary': f"长期运行控制：检查第 {state['check_count']} 次，{'应结束' if should_terminate else '继续'}"
    }


def _init_controller_state(params: dict[str, Any]) -> dict[str, Any]:
    """初始化控制器状态。"""
    deadline_seconds = params.get('run_duration_seconds', 0)
    start_time = datetime.now(timezone.utc).isoformat()
    
    return {
        'start_time': start_time,
        'deadline_seconds': deadline_seconds,
        'tried_actions': [],
        'consecutive_low_info': 0,
        'problem_graph': params.get('problem_graph', []),
        'candidate_queue': params.get('candidate_queue', []),
        'finalization_reserve_seconds': params.get('finalization_reserve_seconds', 600),
        'last_check': start_time,
        'check_count': 0
    }


def _check_termination(state: dict[str, Any], params: dict[str, Any]) -> tuple[bool, str]:
    """检查是否应该结束运行。"""
    # 1. 用户取消
    if params.get('user_cancelled', False):
        return True, 'user_cancelled'
    
    # 2. 资源硬阻塞
    if params.get('resource_hard_blocked', False):
        return True, 'resource_hard_blocked'
    
    # 3. Deadline 到达
    if state['deadline_seconds'] > 0:
        start = datetime.fromisoformat(state['start_time'].replace('Z', '+00:00'))
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        reserve = state['finalization_reserve_seconds']
        if elapsed >= state['deadline_seconds'] - reserve:
            return True, 'deadline_reached'
    
    # 4. 问题图可证明为空
    if not state['problem_graph'] and not state['candidate_queue']:
        return True, 'problem_graph_empty'
    
    return False, ''


def _select_different_arm(state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any] | None:
    """选择不同未解决节点。"""
    # 从问题图中选择未尝试的节点
    tried = set(state['tried_actions'])
    for node in state['problem_graph']:
        if node not in tried:
            return {'type': 'problem_graph_node', 'node': node}
    
    # 从候选队列中选择
    for candidate in state['candidate_queue']:
        if candidate not in tried:
            return {'type': 'candidate', 'candidate': candidate}
    
    return None


# Event 定义
long_run_controller_event = EventDefinition(
    name='long_run.control',
    series='project',
    description='统一控制长期运行的 deadline、问题图、候选队列和收尾预算',
    handler=long_run_control
)
