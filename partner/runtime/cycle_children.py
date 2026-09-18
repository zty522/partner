"""Durable child/resume mechanics. Semantic Events only request children."""
from pathlib import Path


def start_child(worker, job, parent, output):
    request = output.get('cycle_child')
    if not request:
        return
    # Only the new bounded parent can request these fixed child types.
    if parent.flow_type not in {'project_cycle', 'self_improvement_cycle', 'learning_improvement_cycle'} or request['flow'] not in {
            'project_cycle_round', 'pdf_report', 'autonomous_evolution'}:
        raise ValueError('invalid cycle child request')
    node = request['owner_node']
    definition = worker.flows.get(parent.flow_type, version=parent.definition_version)
    following = next(n.node_id for n in definition.nodes if node in n.depends_on)
    child = worker.controller.start(worker.flows.get(request['flow']),
        catalog_version=parent.catalog_version, task_id=job.job_id,
        project_id=job.project_id, instance_id=job.assigned_instance or job.origin_instance)
    child.root_event_id = job.root_event_id
    worker.store.save(child)
    worker.controller.suspend_for_child(parent, resume_node_id=following,
        child_flow_id=child.flow_id, reason='bounded cycle child')
    job.suspended_flows.append({'kind': 'cycle', 'parent_flow_id': parent.flow_id,
        'child_flow_id': child.flow_id, 'owner_node': node,
        'context': request.get('context') or {}})
    job.flow_id, job.flow_type, job.status = child.flow_id, child.flow_type, 'running'
    job.ready_event_ids = list(child.ready_node_ids)


def merge_child(worker, parent, child, suspension):
    from partner.runtime.action_execution import write_json
    node = suspension['owner_node']
    files = []
    for out in child.node_outputs.values():
        files.extend(out.get('files') or [])
        files.extend(out.get('evidence_refs') or [])
    path = worker.root / 'state/cycles' / parent.task_id / (node + '.json')
    record = {'flow_id': child.flow_id, 'flow_type': child.flow_type,
              'status': child.status, 'instance_id': child.instance_id,
              'node_outputs': child.node_outputs, 'node_event_ids': child.node_event_ids,
              'files': list(dict.fromkeys(files))}
    write_json(path, record)
    parent.node_outputs[node] = {
        'ok': child.status == 'completed', 'status': child.status,
        'files': [str(path)] + record['files'], 'evidence_refs': [str(path)],
        'semantic_output': {'child_flow_id': child.flow_id, 'child_status': child.status,
                            'record_path': str(path)},
        'summary': f'{node}: child {child.status}; evidence {path.name}',
    }
    worker.store.save(parent)
