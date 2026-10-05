#!/usr/bin/env python3
"""Evaluate five long-running Jobs against Expected Effect v4.

The evaluator never infers success from a terminal Job or file existence.
It reads the shared RunNarrative, provider usage, channel receipts and report
artifacts, and emits machine-readable gates plus a reviewer-oriented Markdown
table.  Human report/message ratings remain explicit review fields.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import json
import re


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}


def api_usage(workspace: Path, job_id: str):
    rows = []
    path = workspace / 'state/logs/api_calls.jsonl'
    if path.is_file():
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            try: row = json.loads(line)
            except ValueError: continue
            if str(row.get('task_id') or '') == job_id:
                rows.append(row)
    # Dedicated and child flows currently persist receipts in the Event log even
    # when the provider ledger has no task_id row.  Fall back to those receipts
    # and de-duplicate because one receipt may occur in several projected
    # payloads.
    receipts = {}
    event_log = workspace / 'state/run_logs' / job_id / 'events.jsonl'
    if event_log.is_file():
        for line in event_log.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                root = json.loads(line)
            except ValueError:
                continue
            stack = [root]
            while stack:
                value = stack.pop()
                if isinstance(value, dict):
                    if value.get('call_id') and value.get('model') and value.get('purpose'):
                        receipts[str(value['call_id'])] = value
                    stack.extend(value.values())
                elif isinstance(value, list):
                    stack.extend(value)
    if receipts and len(receipts) > len(rows):
        rows = list(receipts.values())
    purposes = Counter(str(row.get('purpose') or '') for row in rows)
    total = sum(int(row.get('total_tokens') or 0) for row in rows)
    action = sum(int(row.get('total_tokens') or 0) for row in rows
                 if row.get('purpose') == 'action')
    thinking = sum(row.get('thinking_requested') == 'enabled' for row in rows)
    return {'calls': len(rows), 'total_tokens': total, 'action_tokens': action,
            'action_token_ratio': action / total if total else None,
            'token_metrics_available': total > 0,
            'thinking_calls': thinking, 'purposes': dict(purposes),
            'models': dict(Counter(str(row.get('model') or '') for row in rows)),
            'statuses': dict(Counter(str(row.get('status') or '') for row in rows))}


def learning_history(workspace: Path, job: dict):
    flow = load(workspace / 'state/event_flows' / f"{job.get('flow_id')}.json")
    output = (flow.get('node_outputs') or {}).get('iterate_more') or {}
    semantic = output.get('semantic_output') or output
    history = semantic.get('history') or []
    completed = [row for row in history if int(row.get('novel_source_count') or 0) > 0]
    effects = [row.get('learning_effect') for row in completed
               if isinstance(row.get('learning_effect'), (int, float))]
    return {
        'attempted_rounds': len(history),
        'completed_rounds': len(completed),
        'effect_count': len(effects),
        'unique_effects': sorted(set(effects)),
        'effect_varies_across_rounds': len(effects) <= 1 or len(set(effects)) > 1,
        'stop_reason': str(semantic.get('stop_reason') or ''),
    }


def narrative_checks(workspace: Path, job_id: str, narrative: dict):
    project = narrative.get('project') or {}
    benchmark = project.get('benchmark') or {}
    conclusion = str(project.get('conclusion') or '')
    invalid_benchmark_claimed_success = (
        benchmark.get('valid') is False and
        any(word in conclusion for word in ('成功达成', 'benchmark 已成功', 'Benchmark 已成功'))
    )
    evolution = narrative.get('self_evolution') or {}
    issue = evolution.get('selected_issue') or {}
    issue_evidence = {key: value for key, value in issue.items()
                      if key not in {'partner_target_files', 'target_files'}}
    issue_text = json.dumps(issue_evidence, ensure_ascii=False)
    referenced_python = set(re.findall(r'partner/[A-Za-z0-9_./-]+\.py', issue_text))
    targets = set(issue.get('partner_target_files') or evolution.get('target_files') or [])
    target_consistent = not referenced_python or bool(referenced_python & targets)
    record = load(workspace / 'state/cycles' / job_id / 'evolution' / 'record.json')
    final_decision = ((record.get('decision') or {}).get('decision') if record else '')
    narrative_decision = str(evolution.get('decision') or '')
    final_evolution_synced = not final_decision or final_decision == narrative_decision
    return {
        'invalid_benchmark_claimed_success': invalid_benchmark_claimed_success,
        'evolution_target_consistent': target_consistent,
        'final_evolution_synced': final_evolution_synced,
        'final_evolution_decision': final_decision,
        'narrative_evolution_decision': narrative_decision,
    }


def sent_messages(workspace: Path, job_id: str):
    roots = [workspace / 'state/application/outbound', workspace / 'state/outbound']
    files = []
    for root in roots:
        if root.is_dir():
            files.extend(path for path in root.rglob('*.sent') if job_id in str(path))
    contents = []
    for path in files:
        row = load(path)
        contents.append(str(row.get('content') or row.get('message') or row.get('text') or ''))
    return {'sent_count': len(files), 'unique_count': len(set(contents)),
            'empty_count': sum(not text.strip() for text in contents)}


def evaluate_one(workspace: Path, job_id: str, role: str):
    job = load(workspace / 'state/application/jobs' / f'{job_id}.json')
    cycle = workspace / 'state/cycles' / job_id
    narrative = load(cycle / 'run_narrative.json')
    project = narrative.get('project') or {}
    learning = narrative.get('active_learning') or {}
    evolution = narrative.get('self_evolution') or {}
    rounds = project.get('rounds') or []
    signatures = [row.get('action_signature') for row in rounds if row.get('action_signature')]
    evidence_rounds = sum(
        row.get('verified') is True and bool(row.get('domain_evidence') or row.get('evidence'))
        for row in rounds
    )
    repeated = len(signatures) - len(set(signatures))
    usage = api_usage(workspace, job_id)
    messages = sent_messages(workspace, job_id)
    learning_metrics = learning_history(workspace, job)
    consistency = narrative_checks(workspace, job_id, narrative)
    work = workspace / 'state/event_runtime/work' / job_id
    pdfs = list(work.rglob('*.pdf')) if work.is_dir() else []
    gates = {
        'narrative_present': bool(narrative),
        'route_correct': (
            role == 'learning' and job.get('flow_type') == 'learning_improvement_cycle' or
            role == 'self_evolution' and job.get('flow_type') == 'self_improvement_cycle' or
            role == 'mixed' and job.get('flow_type') == 'meta_cycle' or
            role == 'project' and job.get('flow_type') in {'project_research_cycle','meta_cycle'}),
        'real_evidence': evidence_rounds > 0 if role in {'project','mixed'} else True,
        'no_semantic_action_repeat': repeated == 0,
        'action_token_ratio_below_40pct': (
            usage['action_token_ratio'] is not None and usage['action_token_ratio'] <= .40),
        'token_metrics_available': usage['token_metrics_available'],
        'cognitive_thinking_used': usage['thinking_calls'] > 0,
        'qq_message_budget': 3 <= messages['sent_count'] <= 20,
        'pdf_present': bool(pdfs),
        'learning_source_bound': (int(learning.get('run_count') or 0) > 0 and
                                  bool(learning.get('sources')))
                                 if role in {'learning','mixed'} else True,
        'learning_consumed': learning.get('handoff_consumed') is True
                             if role == 'mixed' else True,
        'self_evolution_grounded': (bool((evolution.get('selected_issue') or {}).get('partner_target_files'))
                                    and evolution.get('decision') in {'promoted','rejected','inconclusive'})
                                   if role in {'self_evolution','mixed'} else True,
        'learning_effect_not_constant_across_many_rounds': (
            learning_metrics['effect_varies_across_rounds']
            if role == 'learning' else True),
        'no_invalid_benchmark_success_claim': not consistency['invalid_benchmark_claimed_success'],
        'evolution_target_consistent': (
            consistency['evolution_target_consistent']
            if role in {'self_evolution', 'mixed'} else True),
        'final_evolution_synced': consistency['final_evolution_synced'],
    }
    return {'job_id': job_id, 'role': role, 'job_status': job.get('status'),
            'flow_type': job.get('flow_type'), 'gates': gates,
            'automatic_pass': all(gates.values()), 'round_count': len(rounds),
            'verified_evidence_rounds': evidence_rounds, 'repeated_action_signatures': repeated,
            'usage': usage, 'messages': messages, 'pdfs': [str(path) for path in pdfs],
            'learning_metrics': learning_metrics, 'consistency': consistency,
            'human_review': {'message_usefulness_1_to_5': None,
                             'pdf_research_quality_1_to_5': None,
                             'web_trace_clarity_1_to_5': None,
                             'factual_contradictions': None}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', default='/mnt/e/work/partner_workspace')
    parser.add_argument('--manifest', required=True,
                        help='JSON with jobs: [{job_id, role}]')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    manifest = load(Path(args.manifest))
    results = [evaluate_one(workspace, row['job_id'], row['role'])
               for row in manifest.get('jobs') or []]
    payload = {'schema_version': 2, 'expected_effect': 'v4', 'results': results,
               'automatic_pass': bool(results) and all(row['automatic_pass'] for row in results),
               'human_review_complete': False}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    md = output.with_suffix('.md')
    lines = ['# 五实例三小时 Expected Effect v4 对照', '',
             '| Job | 角色 | Flow | 自动门 | 证据轮 | QQ | Action token占比 |',
             '|---|---|---|---|---:|---:|---:|']
    for row in results:
        ratio = row['usage']['action_token_ratio']
        lines.append(f"| {row['job_id']} | {row['role']} | {row['flow_type']} | "
                     f"{'通过' if row['automatic_pass'] else '失败'} | {row['verified_evidence_rounds']} | "
                     f"{row['messages']['sent_count']} | {ratio:.1%} |" if ratio is not None else
                     f"| {row['job_id']} | {row['role']} | {row['flow_type']} | 失败 | "
                     f"{row['verified_evidence_rounds']} | {row['messages']['sent_count']} | 无数据 |")
    lines += ['', '> 自动门通过不等于最终通过；消息、PDF、Web 的人工效果评分及事实矛盾审查必须完成。', '']
    md.write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
