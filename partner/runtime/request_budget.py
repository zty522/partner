"""Operator supplied per-request observation limits, inherited across iterations."""
import math
import time


def validate_constraints(value):
    allowed = {'run_until_epoch', 'max_rounds', 'proposal_only', 'read_only_paths',
               'evolution_cycle', 'evolution_apply', 'action_seconds', 'local_learning_root',
               'method_arm', 'method_arm_label', 'benchmark_protocol_id',
               'benchmark_protocol_version', 'benchmark_inputs',
               'benchmark_guardrail_results', 'benchmark_allow_external_judges',
               'checkpoint_policy'}
    if set(value)-allowed:
        raise ValueError('unknown execution constraint')
    result = dict(value)
    if 'local_learning_root' in result:
        from pathlib import Path
        root=result['local_learning_root']
        if not isinstance(root,str) or not Path(root).is_absolute():
            raise ValueError('local_learning_root must be an absolute local path')
    for key in ('evolution_cycle', 'evolution_apply'):
        if key in result and not isinstance(result[key], bool):
            raise ValueError(key + ' must be boolean')
    if 'action_seconds' in result:
        result['action_seconds'] = max(60, min(1800, int(result['action_seconds'])))
    for key in ('benchmark_inputs', 'benchmark_guardrail_results'):
        if key in result and not isinstance(result[key], dict):
            raise ValueError(key + ' must be an object')
    if ('benchmark_allow_external_judges' in result
            and not isinstance(result['benchmark_allow_external_judges'], bool)):
        raise ValueError('benchmark_allow_external_judges must be boolean')
    if 'run_until_epoch' in result:
        deadline = float(result['run_until_epoch'])
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError('invalid observation deadline')
        result['run_until_epoch'] = deadline
    if 'max_rounds' in result:
        rounds = int(result['max_rounds'])
        if not 1 <= rounds <= 50:
            raise ValueError('request round limit must be 1..50')
        result['max_rounds'] = rounds
    return result


def expired(contract, now=None):
    value = (contract.get('execution_constraints') or {}).get('run_until_epoch')
    return bool(value and (time.time() if now is None else now) >= float(value))


def round_limit(contract, default):
    return int((contract.get('execution_constraints') or {}).get('max_rounds') or default)
