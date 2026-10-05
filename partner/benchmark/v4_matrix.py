"""Aggregate independently executed v4 suites without reinterpreting settlements."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Iterable
import json, statistics
from .v4_protocol import bootstrap_mean_ci


def aggregate_suite_results(paths: Iterable[str | Path]) -> dict[str, Any]:
    suites=[]
    for raw in paths:
        p=Path(raw); result=json.loads((p/'suite_result.json').read_text())
        manifest=json.loads((p/'manifest.json').read_text())
        episodes=list(result.get('episodes') or [])
        positive_project_effects=[float((row.get('settlement') or {}).get('effect') or 0)
                                  for row in episodes
                                  if (row.get('settlement') or {}).get('track')=='project'
                                  and str(row.get('control_role') or 'positive')=='positive']
        suites.append({'suite_id':result['suite_id'],'seed':manifest.get('bootstrap_seed'),
                       'benchmark_version':manifest.get('benchmark_version','4.0'),
                       'execution_mode':manifest.get('execution_mode','reference'),
                       'hard_pass':bool((result.get('settlement') or {}).get('hard_pass')),
                       'episode_count':int((result.get('transfer') or {}).get('episode_count') or 0),
                       'confirmed_episodes':int((result.get('transfer') or {}).get('confirmed_episodes') or 0),
                       'mean_effect':float((result.get('transfer') or {}).get('mean_effect') or 0),
                       'positive_project_effects':positive_project_effects,
                       'metrics':(result.get('transfer') or {}).get('metrics') or {}})
    effects=[r['mean_effect'] for r in suites]
    false_rates=[float((r['metrics']).get('false_promotion_rate') or 0) for r in suites]
    project_uplifts=[value for row in suites for value in row['positive_project_effects']]
    learning_gains=[float((r['metrics'].get('learning_to_action_gain') or {}).get('mean') or 0)
                    for r in suites]
    repair_rates=[float(r['metrics'].get('repair_at_1') or 0) for r in suites]
    positive_rates=[float((r['metrics'].get('v41_diversity') or {}).get('positive_control_confirmation_rate') or 0)
                    for r in suites]
    negative_rates=[float((r['metrics'].get('v41_diversity') or {}).get('negative_control_safe_rate') or 0)
                    for r in suites]
    real = bool(suites and all(r['execution_mode']=='real' for r in suites))
    versions=sorted({str(r['benchmark_version']) for r in suites})
    version_label=versions[0] if len(versions)==1 else '/'.join(versions)
    return {'schema_version':1,'suite_count':len(suites),'all_hard_pass':all(r['hard_pass'] for r in suites),
            'execution_mode':'real' if real else 'reference_or_mixed',
            'total_episodes':sum(r['episode_count'] for r in suites),
            'total_confirmed_episodes':sum(r['confirmed_episodes'] for r in suites),
            'primary_stratified_metrics':{
                'positive_project_effect_across_episodes':bootstrap_mean_ci(project_uplifts,seed=20260930),
                'learning_to_action_gain_across_suites':bootstrap_mean_ci(learning_gains,seed=20260930),
                'repair_at_1_mean':statistics.mean(repair_rates) if repair_rates else None,
                'positive_control_confirmation_rate_mean':statistics.mean(positive_rates) if positive_rates else None,
                'negative_control_safe_rate_mean':statistics.mean(negative_rates) if negative_rates else None,
            },
            'heterogeneous_effect_mean_not_primary':statistics.mean(effects) if effects else None,
            'max_false_promotion_rate':max(false_rates,default=None),'suites':suites,
            'claim_boundary':(f'Repeated real v{version_label} suites expand task/source/incident diversity, but remain a bounded local sample and do not establish population generalization.'
                              if real else 'Repeated deterministic reference suites test reproducibility, not population generalization.')}


def write_matrix(paths: Iterable[str | Path], output: str | Path) -> Path:
    target=Path(output); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(aggregate_suite_results(paths),ensure_ascii=False,indent=2)+'\n')
    return target
