"""Durable child/resume bridge for benchmark subject flows."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def start_child(worker, job, parent, output):
    request = output.get("benchmark_child") or (output.get("semantic_output") or {}).get(
        "benchmark_child")
    if not isinstance(request, dict):
        return
    if parent.flow_type != "benchmark_experiment" or request.get("flow") != "benchmark_subject":
        raise ValueError("invalid benchmark child request")
    arm = str(request.get("arm_id") or "")
    if arm not in {"baseline", "candidate"}:
        raise ValueError("benchmark child arm must be baseline or candidate")
    node = str(request.get("owner_node") or "")
    definition = worker.flows.get(parent.flow_type, version=parent.definition_version)
    following = next(n.node_id for n in definition.nodes if node in n.depends_on)
    context = dict(parent.run_context)
    context.update({"run_mode": "benchmark", "benchmark_arm_id": arm,
                    "evaluation_visibility": "hidden_until_terminal"})
    child = worker.controller.start(
        worker.flows.get("benchmark_subject"), catalog_version=parent.catalog_version,
        task_id=job.job_id, project_id=job.project_id,
        instance_id=job.assigned_instance or job.origin_instance,
        run_context=context)
    child.root_event_id = job.root_event_id
    worker.store.save(child)
    worker.controller.suspend_for_child(
        parent, resume_node_id=following, child_flow_id=child.flow_id,
        reason=f"benchmark arm {arm}")
    job.suspended_flows.append({
        "kind": "benchmark", "parent_flow_id": parent.flow_id,
        "child_flow_id": child.flow_id, "owner_node": node, "arm_id": arm,
        "context": dict(request.get("context") or {}),
    })
    job.flow_id, job.flow_type, job.status = child.flow_id, child.flow_type, "running"
    job.benchmark_arm_id = arm
    job.ready_event_ids = list(child.ready_node_ids)


def merge_child(worker, parent, child, suspension):
    arm = str(suspension.get("arm_id") or "")
    node = str(suspension.get("owner_node") or "")
    files: list[str] = []
    checkpoints: list[dict[str, Any]] = []
    for output in child.node_outputs.values():
        if not isinstance(output, dict):
            continue
        files.extend(str(v) for v in output.get("files") or [])
        files.extend(str(v) for v in output.get("evidence_refs") or [])
        semantic = output.get("semantic_output")
        if isinstance(semantic, dict) and semantic.get("checkpoint_id"):
            checkpoints.append(dict(semantic))
    target = worker.root / "state/benchmarks/runs" / str(
        parent.run_context.get("benchmark_run_id") or "unknown") / "arms" / arm / "child_flow.json"
    record = {
        "schema_version": 1, "arm_id": arm, "flow_id": child.flow_id,
        "flow_type": child.flow_type, "status": child.status,
        "instance_id": child.instance_id, "node_outputs": child.node_outputs,
        "node_event_ids": child.node_event_ids, "checkpoints": checkpoints,
        "files": list(dict.fromkeys(files)),
    }
    _write(target, record)
    parent.node_outputs[node] = {
        "ok": True, "status": "completed",
        "files": [str(target), *record["files"]], "evidence_refs": [str(target)],
        "semantic_output": {"arm_id": arm, "child_flow_id": child.flow_id,
                            "child_status": child.status, "record_path": str(target),
                            "checkpoint_count": len(checkpoints)},
        "summary": f"{arm} child {child.status}; {len(checkpoints)} checkpoints",
    }
    worker.store.save(parent)

