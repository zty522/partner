"""自进化候选发现合并排序器：把 recall、当前运行异常、失败签名和报告事实矛盾合并排序。"""
from __future__ import annotations

import json
from typing import Any
from pathlib import Path
from datetime import datetime, timezone
from partner.event_fabric.catalog import EventDefinition


def evolution_candidate_merge(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """合并排序自进化候选：recall + 当前运行异常 + 失败签名 + 报告事实矛盾。"""
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    job_id = str(getattr(ctx, 'job_id', ''))
    current_job_id = params.get('current_job_id', job_id)
    
    # 1. 读取 recall 历史机会
    recall_candidates = _load_recall_candidates(workspace)
    
    # 2. 读取当前运行异常
    current_anomalies = _load_current_anomalies(workspace, current_job_id)
    
    # 3. 读取失败签名
    failure_signatures = _load_failure_signatures(workspace)
    
    # 4. 读取报告事实矛盾
    report_contradictions = _load_report_contradictions(workspace)
    
    # 5. 合并并排序（排除当前 Job 自己和尚未开始的 Job）
    all_candidates = []
    
    # recall 候选（权重 0.3）
    for c in recall_candidates:
        if c.get('job_id') != current_job_id:
            all_candidates.append({
                'source': 'recall',
                'weight': 0.3,
                'candidate': c
            })
    
    # 当前运行异常（权重 0.4）
    for c in current_anomalies:
        all_candidates.append({
            'source': 'current_anomaly',
            'weight': 0.4,
            'candidate': c
        })
    
    # 失败签名（权重 0.5）
    for c in failure_signatures:
        if c.get('job_id') != current_job_id:
            all_candidates.append({
                'source': 'failure_signature',
                'weight': 0.5,
                'candidate': c
            })
    
    # 报告事实矛盾（权重 0.6）
    for c in report_contradictions:
        if c.get('job_id') != current_job_id:
            all_candidates.append({
                'source': 'report_contradiction',
                'weight': 0.6,
                'candidate': c
            })
    
    # 按权重降序排序
    all_candidates.sort(key=lambda x: x['weight'], reverse=True)
    
    # 取前 5 个
    top_candidates = all_candidates[:5]
    
    return {
        'ok': True,
        'status': 'completed',
        'total_candidates': len(all_candidates),
        'top_candidates': top_candidates,
        'summary': f"合并排序 {len(all_candidates)} 个候选，取前 {len(top_candidates)} 个"
    }


def _load_recall_candidates(workspace: Path) -> list[dict]:
    """读取 recall 历史机会。"""
    recall_path = workspace / 'state' / 'evolution' / 'recall_opportunities.json'
    if recall_path.exists():
        try:
            return json.loads(recall_path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []


def _load_current_anomalies(workspace: Path, current_job_id: str) -> list[dict]:
    """读取当前运行异常。"""
    anomalies = []
    
    # 读取最近 Job 的异常
    jobs_dir = workspace / 'state' / 'jobs'
    if jobs_dir.exists():
        for job_file in jobs_dir.glob('*.json'):
            try:
                job_data = json.loads(job_file.read_text(encoding='utf-8'))
                if job_data.get('status') in ['failed', 'error']:
                    anomalies.append({
                        'job_id': job_data.get('job_id'),
                        'anomaly_type': job_data.get('error_type', 'unknown'),
                        'description': job_data.get('error_message', '')
                    })
            except Exception:
                pass
    
    return anomalies


def _load_failure_signatures(workspace: Path) -> list[dict]:
    """读取失败签名。"""
    sig_path = workspace / 'state' / 'evolution' / 'failure_signatures.json'
    if sig_path.exists():
        try:
            return json.loads(sig_path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []


def _load_report_contradictions(workspace: Path) -> list[dict]:
    """读取报告事实矛盾。"""
    contradiction_path = workspace / 'state' / 'evolution' / 'report_contradictions.json'
    if contradiction_path.exists():
        try:
            return json.loads(contradiction_path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []


# Event 定义
evolution_candidate_merger_event = EventDefinition(
    name='evolution.candidate_merge',
    series='project',
    description='自进化候选发现合并排序器：recall + 当前异常 + 失败签名 + 报告矛盾',
    handler=evolution_candidate_merge
)
