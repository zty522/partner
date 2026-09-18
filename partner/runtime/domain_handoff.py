"""Validated read-only child-flow requests; only the worker executes flows."""
import json
from pathlib import Path


def read_request(work):
    work=Path(work).resolve()
    requests=[p for p in work.rglob('next_flow_request.json')
              if '.execution' not in p.relative_to(work).parts and p.resolve().is_relative_to(work)]
    if not requests:return None
    if len(requests)!=1:raise ValueError('ambiguous child flow requests in current work directory')
    path=requests[0]
    value=json.loads(path.read_text())
    if value.get('flow')!='browser_video_learning':raise ValueError('unsupported child flow')
    from partner.social_video.events import valid_url
    url=valid_url(str(value.get('url','')))
    source=Path(value.get('source_evidence','')).resolve()
    if not source.is_relative_to(work) or not source.is_file():raise ValueError('discovered source evidence must be in current work directory')
    if url not in source.read_text(errors='replace'):raise ValueError('video URL absent from discovery evidence')
    return {'flow':value['flow'],'url':url,'source_evidence':str(source),'request_path':str(path)}


def insert_video(worker, job, parent, request):
    """Start a child browser_video_learning flow from project_iteration.execute.

    Replaces the old ``social_video.integration.intake()`` call: dispatch
    decisions live in INTENT_FLOW / execute's LLM verdict, not in regex.
    We materialise runs/<run_id>/owner.json directly so the social/video
    downstream can still verify ownership via trusted_request.
    """
    from partner.social_video.integration import data_root, digest as _digest, write_json as _write_json
    import uuid as _uuid
    url = str(request.get("url") or "")
    if not url:
        raise ValueError("insert_video requires a verified url in request")
    run_id = "video-" + _uuid.uuid4().hex[:12]
    base = data_root(worker.root)
    run_dir = base / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    owner = _digest({
        "channel": job.channel,
        "sender": job.sender_id,
        "instance": job.intake_instance_id or job.origin_instance or "",
    })
    _write_json(run_dir / "owner.json", {
        "owner": owner,
        "event": "browser_video_learning",
        "params": {"run_id": run_id, "url": url},
    })
    definition = worker.flows.get("browser_video_learning")
    child = worker.controller.start(
        definition, catalog_version=worker.catalog.version,
        task_id=job.job_id, project_id=job.project_id,
        instance_id=job.intake_instance_id,
    )
    child.root_event_id = job.root_event_id
    worker.store.save(child)
    worker.controller.suspend_for_child(
        parent, resume_node_id="verify", child_flow_id=child.flow_id,
        reason="execute requested verified video URL; runtime owns child execution",
    )
    job.suspended_flows.append({
        "parent_flow_id": parent.flow_id,
        "child_flow_id": child.flow_id,
        "kind": "domain_handoff",
        "context": {
            "request": {"url": url, "run_id": run_id},
            "parent_flow_outputs": parent.node_outputs,
        },
    })
    job.flow_id = child.flow_id
    job.flow_type = child.flow_type
    job.status = "running"
    job.ready_event_ids = list(child.ready_node_ids)


def merge_video_result(parent,child):
    from partner.runtime.artifact_checks import check_file
    files=[]
    for value in child.node_outputs.values():
        files.extend(value.get('files') or value.get('evidence_refs') or [])
    checks=[check_file(Path(p)) for p in dict.fromkeys(files) if Path(p).is_file()]
    success=child.status=='completed' and not child.failed_node_ids
    output=parent.node_outputs['execute'];semantic=output.setdefault('semantic_output',{})
    semantic.update(child_flow_id=child.flow_id,child_status=child.status,artifact_checks=checks,
        child_lineage={'parent_flow_id':parent.flow_id if hasattr(parent,'flow_id') else '',
            'job_id':getattr(child,'task_id',''), 'root_event_id':getattr(child,'root_event_id',''),
            'completed_nodes':getattr(child,'completed_node_ids',[])},
        business_delta=success and any(r['valid'] for r in checks),
        result='视频子流程已完成并保留实际转录及画面证据' if success else '视频子流程未完成，请根据子流程失败记录修正；不能声称看完')
    output.update(files=files,evidence_refs=files,business_delta=semantic['business_delta'],summary=semantic['result'])
