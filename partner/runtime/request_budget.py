"""Operator supplied per-request observation limits, inherited across iterations."""
import math
import time


def validate_constraints(value):
    allowed = {'run_until_epoch', 'max_rounds', 'proposal_only', 'read_only_paths',
               'evolution_cycle', 'evolution_apply', 'action_seconds', 'local_learning_root',
               'method_arm', 'method_arm_label', 'benchmark_protocol_id',
               'benchmark_protocol_version', 'benchmark_inputs',
               'benchmark_guardrail_results', 'benchmark_allow_external_judges',
               'checkpoint_policy', 'delivery_channels'}
    allowed.update({'max_evolution_attempts', 'notification_mode',
                    'observation_job_ids', 'max_observation_steps',
                    'max_opportunities', 'learning_record_root'})
    if set(value)-allowed:
        raise ValueError('unknown execution constraint')
    result = dict(value)
    if 'local_learning_root' in result:
        from pathlib import Path
        root=result['local_learning_root']
        if not isinstance(root,str) or not Path(root).is_absolute():
            raise ValueError('local_learning_root must be an absolute local path')
    if 'learning_record_root' in result:
        from pathlib import Path
        root = result['learning_record_root']
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ValueError('learning_record_root must be an absolute local path')
    if 'observation_job_ids' in result:
        job_ids = result['observation_job_ids']
        if (not isinstance(job_ids, list) or len(job_ids) > 12
                or any(not isinstance(value, str) or not value.startswith('job_')
                       for value in job_ids)):
            raise ValueError('observation_job_ids must be a list of at most 12 Job ids')
        result['observation_job_ids'] = list(dict.fromkeys(job_ids))
    for key, ceiling in (('max_observation_steps', 10), ('max_opportunities', 3)):
        if key in result:
            count = int(result[key])
            if not 1 <= count <= ceiling:
                raise ValueError(f'{key} must be 1..{ceiling}')
            result[key] = count
    for key in ('evolution_cycle', 'evolution_apply'):
        if key in result and not isinstance(result[key], bool):
            raise ValueError(key + ' must be boolean')
    if 'delivery_channels' in result:
        channels = result['delivery_channels']
        if (not isinstance(channels, list) or not channels
                or any(channel not in {'web', 'qq', 'local', 'log', 'file'}
                       for channel in channels)):
            raise ValueError('delivery_channels must be a non-empty channel list')
        result['delivery_channels'] = list(dict.fromkeys(channels))
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
    if 'max_evolution_attempts' in result:
        attempts = int(result['max_evolution_attempts'])
        if not 1 <= attempts <= 8:
            raise ValueError('evolution attempt limit must be 1..8')
        result['max_evolution_attempts'] = attempts
    if ('notification_mode' in result and
            result['notification_mode'] not in {'standard','audit','debug'}):
        raise ValueError('notification_mode must be standard, audit or debug')
    return result


def expired(contract, now=None):
    value = (contract.get('execution_constraints') or {}).get('run_until_epoch')
    return bool(value and (time.time() if now is None else now) >= float(value))


def round_limit(contract, default):
    return int((contract.get('execution_constraints') or {}).get('max_rounds') or default)
