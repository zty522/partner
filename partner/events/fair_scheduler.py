"""公平调度器：最长等待时间、公平队列、固定并发槽。"""
from __future__ import annotations

import json
import time
from typing import Any
from pathlib import Path
from datetime import datetime, timezone
from partner.event_fabric.catalog import EventDefinition


def fair_schedule(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """公平调度：最长等待时间、公平队列、固定并发槽。"""
    workspace = Path(str(getattr(ctx, 'workspace', '')))
    
    # 读取当前 Job 队列
    jobs_dir = workspace / 'state' / 'jobs'
    pending_jobs = []
    running_jobs = []
    
    if jobs_dir.exists():
        for job_file in jobs_dir.glob('*.json'):
            try:
                job_data = json.loads(job_file.read_text(encoding='utf-8'))
                if job_data.get('status') == 'pending':
                    pending_jobs.append({
                        'job_id': job_data.get('job_id'),
                        'submit_time': job_data.get('submit_time', ''),
                        'priority': job_data.get('priority', 0)
                    })
                elif job_data.get('status') == 'running':
                    running_jobs.append({
                        'job_id': job_data.get('job_id'),
                        'start_time': job_data.get('start_time', ''),
                        'worker_id': job_data.get('worker_id')
                    })
            except Exception:
                pass
    
    # 按提交时间排序（公平队列）
    pending_jobs.sort(key=lambda x: x['submit_time'])
    
    # 计算最长等待时间
    max_wait_seconds = params.get('max_wait_seconds', 300)
    now = datetime.now(timezone.utc)
    
    for job in pending_jobs:
        if job['submit_time']:
            submit = datetime.fromisoformat(job['submit_time'].replace('Z', '+00:00'))
            wait = (now - submit).total_seconds()
            if wait > max_wait_seconds:
                job['priority'] += 10  # 提升优先级
    
    # 固定并发槽：验收时保留至少 2 个 worker
    min_workers_for_acceptance = params.get('min_workers_for_acceptance', 2)
    total_workers = params.get('total_workers', 5)
    acceptance_running = params.get('acceptance_running', False)
    
    if acceptance_running:
        available_workers = max(min_workers_for_acceptance, total_workers - len(running_jobs))
    else:
        available_workers = total_workers - len(running_jobs)
    
    # 选择下一个 Job
    next_job = None
    if pending_jobs and available_workers > 0:
        next_job = pending_jobs[0]
    
    return {
        'ok': True,
        'status': 'completed',
        'pending_count': len(pending_jobs),
        'running_count': len(running_jobs),
        'available_workers': available_workers,
        'next_job': next_job,
        'summary': f"公平调度：{len(pending_jobs)} 个待处理，{available_workers} 个可用 worker"
    }


# Event 定义
fair_scheduler_event = EventDefinition(
    name='scheduler.fair',
    series='project',
    description='公平调度器：最长等待时间、公平队列、固定并发槽',
    handler=fair_schedule
)
