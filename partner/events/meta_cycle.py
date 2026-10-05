"""True multi-workstream coordinator for explicitly mixed requests."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import time

from partner.event_fabric.catalog import EventDefinition
from partner.runtime.action_execution import write_json


def _root(ctx) -> Path:
    path = Path(ctx.workspace) / 'state/cycles' / str(ctx.job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}


def _result(value, summary, files=()):
    return {'ok': True, 'status': 'completed', 'semantic_output': value,
            'summary': summary, 'files': list(files), 'evidence_refs': list(files)}


def initialize(ctx, params):
    contract = params.get('intent_contract') or {}
    original = str(contract.get('original_request') or params.get('request') or '')
    primary = str(contract.get('primary_workstream') or contract.get('workstream_type') or '')
    tracks = []
    if primary in {'project', 'project_research', 'mixed', ''}:
        tracks.append('project')
    lowered = original.lower()
    if ('主动学习' in original or 'active learning' in lowered
            or any(str(row.get('workstream') or '') == 'active_learning'
                   for row in contract.get('conditional_branches') or [] if isinstance(row, dict))):
        tracks.append('learning')
    if ('自进化' in original or 'self-evolution' in lowered or 'self evolution' in lowered
            or any(str(row.get('workstream') or '') == 'self_evolution'
                   for row in contract.get('conditional_branches') or [] if isinstance(row, dict))):
        tracks.append('evolution')
    tracks = list(dict.fromkeys(tracks)) or ['project']
    value = {'schema_version': 1, 'tracks': tracks, 'phase': 'ready',
             'next_index': 0, 'history': [],
             'report_required': str(params.get('report_policy') or 'milestone') != 'none',
             'coordination_rule': 'one child workstream at a time; merge only terminal evidence'}
    path = _root(ctx) / 'meta_state.json'; write_json(path, value)
    return {**_result(value, '混合目标已拆分为独立工作轨道', [str(path)]),
            'report_required': value['report_required']}


def coordinate(ctx, params):
    path = _root(ctx) / 'meta_state.json'
    state = _read(path)
    if state.get('phase') == 'child_running':
        track = str(state.get('active_track') or '')
        record_path = _root(ctx) / 'meta' / f'{track}.json'
        record = _read(record_path)
        if not record:
            return {'ok': False, 'status': 'failed', 'error': f'missing meta child record: {record_path}'}
        state.setdefault('history', []).append({
            'track': track, 'status': record.get('status'),
            'record_path': str(record_path), 'flow_type': record.get('flow_type'),
        })
        state['next_index'] = int(state.get('next_index') or 0) + 1
        state['phase'] = 'ready'
    tracks = list(state.get('tracks') or [])
    index = int(state.get('next_index') or 0)
    if index >= len(tracks):
        state.update(phase='done', terminal_reason='all declared workstreams reached a terminal')
        write_json(path, state)
        return _result(state, '混合任务的所有声明轨道均已归集', [str(path)])
    track = tracks[index]
    flow = {'project': 'project_research_cycle',
            'learning': 'learning_improvement_cycle',
            'evolution': 'self_improvement_cycle'}[track]
    state.update(phase='child_running', active_track=track)
    write_json(path, state)
    contract = dict(params.get('intent_contract') or {})
    contract['primary_workstream'] = track
    contract['workstream_type'] = {
        'project': 'project_research', 'learning': 'active_learning',
        'evolution': 'self_evolution'}[track]
    constraints = dict(contract.get('execution_constraints') or {})
    constraints['suppress_user_delivery'] = True
    if track == 'project':
        constraints['evolution_cycle'] = False
    contract['execution_constraints'] = constraints
    if track == 'project':
        # The meta root owns the single final presentation.  The project child
        # still executes all domain/evolution checkpoints but suppresses its
        # own report to avoid contradictory duplicate finals.
        report_policy = 'none'
    else:
        report_policy = 'none'
    return {**_result(state, f'启动 {track} 子轨道', [str(path)]),
            'cycle_child': {'flow': flow, 'owner_node': params['node_id'],
                'repeat_owner': True, 'record_name': f'meta/{track}',
                'context': {'request': params.get('request') or original_request(contract),
                            'intent_contract': contract, 'report_policy': report_policy}}}


def original_request(contract):
    return str(contract.get('original_request') or '')


def finalize(ctx, params):
    state = _read(_root(ctx) / 'meta_state.json')
    children = []
    refs = []
    for row in state.get('history') or []:
        record = _read(Path(row['record_path']))
        refs.append(row['record_path'])
        outputs = record.get('node_outputs') or {}
        substantive = {}
        for node_name in ('final_state', 'narrative', 'learning_settlement',
                          'outcome_settle', 'finish'):
            semantic = (outputs.get(node_name) or {}).get('semantic_output') or {}
            if semantic:
                substantive[node_name] = semantic
        children.append({**row, 'files': list(record.get('files') or [])[:40],
                         'substantive_outcome': substantive})
    research = next((row for row in children if row.get('track') == 'project'), {})
    learning = next((row for row in children if row.get('track') == 'learning'), {})
    evolution = next((row for row in children if row.get('track') == 'evolution'), {})
    project_state = ((research.get('substantive_outcome') or {}).get('final_state') or {})
    learning_state = ((learning.get('substantive_outcome') or {}).get('narrative') or
                      (learning.get('substantive_outcome') or {}).get('learning_settlement') or {})
    evolution_state = ((evolution.get('substantive_outcome') or {}).get('narrative') or
                       (evolution.get('substantive_outcome') or {}).get('outcome_settle') or {})
    value = {'schema_version': 2, 'job_id': str(ctx.job_id),
             'workstream_type': 'mixed', 'tracks': children,
             'research_outcome': project_state.get('research_outcome') or
                                 {'status': research.get('status') or 'not_requested'},
             'learning_outcome': learning_state.get('active_learning') or
                                 {'status': learning.get('status') or 'not_requested'},
             'evolution_outcome': evolution_state.get('self_evolution') or
                                  {'status': evolution.get('status') or 'not_requested'},
             'source_records': refs,
             'outcome_semantics': {'flow_completed': True,
                                   'presentation_pending': True,
                                   'delivery_pending': True}}
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    value['final_state_hash'] = 'sha256:' + hashlib.sha256(body).hexdigest()
    final_path = _root(ctx) / 'final_run_state.json'; write_json(final_path, value)
    statuses = ', '.join(f"{row['track']}={row['status']}" for row in children)
    narrative = {'schema_version': 2, 'job_id': str(ctx.job_id),
                 'final_state_hash': value['final_state_hash'],
                 'headline': '混合任务独立轨道已归集：' + statuses,
                 'milestone_message': '最终结算：' + statuses,
                 'tracks': children, 'evidence_refs': refs}
    narrative_path = _root(ctx) / 'run_narrative.json'; write_json(narrative_path, narrative)
    return {**_result(narrative, narrative['headline'], [str(final_path), str(narrative_path)]),
            'message': narrative['milestone_message'], 'notification_kind': 'milestone'}


def finish(ctx, params):
    state = _read(_root(ctx) / 'meta_state.json')
    value = {'stopped': True, 'tracks': state.get('history') or [],
             'final_state_hash': _read(_root(ctx) / 'final_run_state.json').get('final_state_hash'),
             'finished_at': time.time()}
    path = _root(ctx) / 'completion.json'; write_json(path, value)
    return _result(value, '混合工作流已完成并保存独立轨道终态', [str(path)])


DEFINITIONS = [
    EventDefinition('meta.initialize', 'project', '拆分并冻结混合任务的独立工作轨道', initialize),
    EventDefinition('meta.coordinate', 'planning', '按终态证据逐一调度项目、学习和自进化子Flow', coordinate),
    EventDefinition('meta.finalize', 'planning', '归集各轨道证据并冻结统一最终事实', finalize),
    EventDefinition('meta.finish', 'project', '保存混合任务完成记录', finish),
]
