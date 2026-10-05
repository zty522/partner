"""Operator supplied per-request observation limits, inherited across iterations."""
import math
import time


def validate_constraints(value):
    allowed = {'run_until_epoch', 'run_duration_seconds', 'continuation_mode',
               'finalization_reserve_seconds', 'max_rounds', 'proposal_only', 'read_only_paths',
               'evolution_cycle', 'evolution_apply', 'action_seconds', 'local_learning_root',
               'local_learning_files',
               'method_arm', 'method_arm_label', 'benchmark_protocol_id',
               'benchmark_protocol_version', 'benchmark_inputs',
               'benchmark_guardrail_results', 'benchmark_allow_external_judges',
               'benchmark_embedded', 'checkpoint_policy', 'delivery_channels'}
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
    if 'local_learning_files' in result:
        from pathlib import Path
        files = result['local_learning_files']
        if (not isinstance(files, list) or len(files) > 12
                or any(not isinstance(path, str) or not Path(path).is_absolute()
                       for path in files)):
            raise ValueError('local_learning_files must be at most 12 absolute paths')
        result['local_learning_files'] = list(dict.fromkeys(files))
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
    for key in ('evolution_cycle', 'evolution_apply', 'benchmark_embedded'):
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
    if 'run_duration_seconds' in result:
        duration = int(result['run_duration_seconds'])
        if not 300 <= duration <= 86400:
            raise ValueError('run duration must be 300..86400 seconds')
        result['run_duration_seconds'] = duration
    if 'finalization_reserve_seconds' in result:
        reserve = int(result['finalization_reserve_seconds'])
        if not 60 <= reserve <= 3600:
            raise ValueError('finalization reserve must be 60..3600 seconds')
        result['finalization_reserve_seconds'] = reserve
    if ('continuation_mode' in result and
            result['continuation_mode'] not in {'bounded_rounds', 'until_deadline'}):
        raise ValueError('continuation_mode must be bounded_rounds or until_deadline')
    if result.get('continuation_mode') == 'until_deadline' and not (
            result.get('run_duration_seconds') or result.get('run_until_epoch')):
        raise ValueError('until_deadline requires run_duration_seconds or run_until_epoch')
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
