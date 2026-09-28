"""Subject-side Events for Partner-LoopBench.

Only the public task is read here.  Correct choices and scores remain sealed in
the runner/evaluator and never occur in prompts or Event outputs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import hashlib
import json
import os
import subprocess
import sys

from partner.event_fabric.catalog import EventDefinition
from ._llm import call_model, json_object


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _outputs(params: Mapping[str, Any]) -> dict[str, Any]:
    value = params.get("flow_outputs")
    return dict(value) if isinstance(value, Mapping) else {}


def _semantic(params: Mapping[str, Any], node: str) -> dict[str, Any]:
    row = _outputs(params).get(node)
    value = row.get("semantic_output") if isinstance(row, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _view(params: Mapping[str, Any]) -> dict[str, Any]:
    contract = params.get("intent_contract")
    value = contract.get("benchmark_subject_view") if isinstance(contract, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _task(params: Mapping[str, Any]) -> dict[str, Any]:
    return dict(_semantic(params, "inspect").get("task") or {})


def _memory(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = _semantic(params, "inspect").get("verified_memory")
    return [dict(row) for row in value or [] if isinstance(row, Mapping)]


def _policy(params: Mapping[str, Any]) -> str:
    value = _view(params).get("arm_configuration") or {}
    return str(value.get("policy") or "full_partner")


def task_inspect(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    view = _view(params)
    inputs = view.get("inputs") if isinstance(view.get("inputs"), Mapping) else {}
    path = Path(str(inputs.get("task_path") or "")).expanduser().resolve()
    if not path.is_file():
        return {"ok": False, "status": "failed", "error": "public task_path missing"}
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "status": "failed", "error": f"invalid public task: {exc}"}
    candidates = task.get("candidates") if isinstance(task, Mapping) else None
    if not isinstance(candidates, list) or len(candidates) < 2:
        return {"ok": False, "status": "failed", "error": "task needs at least two candidates"}
    forbidden = {"answer", "correct", "oracle", "score", "label"}
    def keys(value: Any) -> set[str]:
        if isinstance(value, Mapping):
            return ({str(key).lower() for key in value} |
                    set().union(*(keys(item) for item in value.values()), set()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value), set())
        return set()
    leaked = sorted(forbidden & keys(task))
    if leaked:
        return {"ok": False, "status": "failed", "error": f"public task leaks evaluator keys: {leaked}"}
    memory = []
    memory_path = Path(str(inputs.get("memory_path") or "")).expanduser().resolve()
    if str(inputs.get("memory_path") or ""):
        if not memory_path.is_file():
            return {"ok": False, "status": "failed", "error": "declared memory_path missing"}
        try:
            memory_doc = json.loads(memory_path.read_text(encoding="utf-8"))
            for row in memory_doc.get("lessons") or []:
                if not isinstance(row, Mapping) or row.get("verified") is not True \
                        or not row.get("source_settlement"):
                    continue
                source = Path(str(row.get("source_artifact") or "")).expanduser().resolve()
                expected_hash = str(row.get("source_sha256") or "")
                if not source.is_file() or expected_hash != hashlib.sha256(source.read_bytes()).hexdigest():
                    continue
                memory.append(dict(row))
        except (OSError, ValueError, TypeError) as exc:
            return {"ok": False, "status": "failed", "error": f"invalid verified memory: {exc}"}
    record = {"visibility": "public_only", "history_access": False,
              "memory_access": bool(memory), "verified_memory": memory[-8:],
              "policy": _policy(params), "task": task, "task_path": str(path),
              "task_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "memory_path": str(memory_path) if memory else "",
              "memory_sha256": hashlib.sha256(memory_path.read_bytes()).hexdigest() if memory else "",
              "memory_records_verified": bool(memory)}
    refs = [str(path)] + ([str(memory_path)] if memory else [])
    return {"ok": True, "status": "completed", "summary": "inspected public blind task",
            "evidence_refs": refs, "semantic_output": record}


def candidate_propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    task = _task(params)
    policy = _policy(params)
    prompt = (
        "You are the subject in a blinded benchmark. You cannot see the answer. "
        "Use only this public task. Return JSON with candidate_ids (best-first), "
        "hypothesis, expected_observation, failure_condition, and evidence_used. "
        "Treat VERIFIED_MEMORY as prior evidence, not as an answer key. "
        "Do not invent candidate IDs.\nPUBLIC_TASK=" + json.dumps(task, ensure_ascii=False) +
        "\nVERIFIED_MEMORY=" + json.dumps(_memory(params), ensure_ascii=False))
    raw, usage = call_model(ctx, purpose="loop_bench_propose", prompt=prompt)
    parsed = json_object(raw)
    allowed = {str(row.get("id")) for row in task.get("candidates") or [] if isinstance(row, Mapping)}
    ordered = [str(v) for v in parsed.get("candidate_ids") or [] if str(v) in allowed]
    if not ordered:
        ordered = [str((task.get("candidates") or [{}])[0].get("id") or "")]
    record = {"policy": policy, "candidate_ids": list(dict.fromkeys(ordered)),
              "hypothesis": str(parsed.get("hypothesis") or ""),
              "expected_observation": str(parsed.get("expected_observation") or ""),
              "failure_condition": str(parsed.get("failure_condition") or ""),
              "evidence_used": list(parsed.get("evidence_used") or []),
              "model_output_hash": hashlib.sha256(raw.encode()).hexdigest()}
    return {"ok": True, "status": "completed", "summary": "generated blind candidates",
            "semantic_output": record, "token_usage": usage}


def candidate_critic(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    proposal = _semantic(params, "propose")
    if _policy(params) in {"single_turn", "no_critic"}:
        record = {"status": "ablated", "surviving_ids": proposal.get("candidate_ids") or [],
                  "objections": [], "policy": _policy(params)}
        return {"ok": True, "status": "completed", "summary": "critic ablated",
                "semantic_output": record}
    prompt = (
        "Act as an adversarial critic. Given the public task and a proposed ranking, "
        "identify unsupported assumptions and return JSON with surviving_ids, objections, "
        "and recommended_id. IDs must come from the task.\n" + json.dumps(
            {"task": _task(params), "verified_memory": _memory(params),
             "proposal": proposal}, ensure_ascii=False))
    raw, usage = call_model(ctx, purpose="loop_bench_critic", prompt=prompt)
    parsed = json_object(raw)
    allowed = {str(row.get("id")) for row in _task(params).get("candidates") or []
               if isinstance(row, Mapping)}
    survivors = [str(v) for v in parsed.get("surviving_ids") or [] if str(v) in allowed]
    recommended = str(parsed.get("recommended_id") or "")
    if recommended in allowed:
        survivors = [recommended, *[v for v in survivors if v != recommended]]
    if not survivors:
        survivors = list(proposal.get("candidate_ids") or [])
    record = {"status": "reviewed", "surviving_ids": survivors,
              "objections": list(parsed.get("objections") or []), "policy": _policy(params),
              "model_output_hash": hashlib.sha256(raw.encode()).hexdigest()}
    return {"ok": True, "status": "completed", "summary": "critic reviewed candidates",
            "semantic_output": record, "token_usage": usage}


def candidate_select(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    proposal = _semantic(params, "propose")
    critic = _semantic(params, "critic")
    candidates = critic.get("surviving_ids") or proposal.get("candidate_ids") or []
    selected = str(candidates[0]) if candidates else "abstain"
    public_candidates = []
    for row in _task(params).get("candidates") or []:
        if not isinstance(row, Mapping):
            continue
        identifier = str(row.get("id") or "")
        public_candidates.append({"id": identifier, "event_type": "loop_bench.action_execute",
            "description": str(row.get("description") or identifier),
            "parameters": {"selection": identifier},
            "expected_observation": proposal.get("expected_observation") or "",
            "disproof": proposal.get("failure_condition") or "",
            "risk": "unknown"})
    chosen = next((row for row in public_candidates if row["id"] == selected),
                  public_candidates[0] if public_candidates else {})
    record = {"selected_id": selected, "alternatives": [str(v) for v in candidates[1:]],
              "policy": _policy(params), "selection_rule": "critic_rank_then_first",
              "expected_observation": proposal.get("expected_observation"),
              "failure_condition": proposal.get("failure_condition"),
              "candidates": public_candidates, "selected": chosen}
    return {"ok": True, "status": "completed", "summary": f"selected {selected}",
            "semantic_output": record}


def commitment_freeze(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    plan = _semantic(params, "plan")
    task = _task(params)
    if plan.get("selected_id") == "abstain":
        return {"ok": False, "status": "failed", "error": "no candidate selected"}
    enforced = _policy(params) != "no_commitment"
    record = {"schema_version": 1, "selected_id": plan.get("selected_id"),
              "alternatives": plan.get("alternatives") or [],
              "expected_observation": plan.get("expected_observation") or "",
              "failure_condition": plan.get("failure_condition") or "",
              "evaluation": "sealed external oracle after terminal",
              "budget": _view(params).get("budget") or {}, "policy": _policy(params),
              "task_id": task.get("task_id"), "frozen_at": _now(),
              "commitment_enforced": enforced,
              "ablation": "commitment_not_enforced" if not enforced else "none"}
    record["content_hash"] = hashlib.sha256(json.dumps(
        record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"ok": True, "status": "completed", "summary": "froze blind commitment",
            "semantic_output": record}


def action_execute(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    view = _view(params)
    inputs = view.get("inputs") if isinstance(view.get("inputs"), Mapping) else {}
    runner = Path(str(inputs.get("arm_runner_path") or "")).expanduser().resolve()
    task_path = Path(str(inputs.get("task_path") or "")).expanduser().resolve()
    committed = _semantic(params, "core_commit")
    decision = committed.get("decision") if isinstance(committed.get("decision"), Mapping) else {}
    selected_row = decision.get("selected") if isinstance(decision.get("selected"), Mapping) else {}
    selected = str(selected_row.get("candidate_id") or committed.get("selected_id") or "")
    if not runner.is_file() or not task_path.is_file() or not selected:
        return {"ok": False, "status": "failed", "error": "runner, task or commitment missing"}
    work = Path(ctx.working_dir) / str(params.get("flow_id") or "unknown_flow")
    work.mkdir(parents=True, exist_ok=True)
    output = work / "benchmark_evidence.json"
    command = [sys.executable, str(runner), "--task", str(task_path),
               "--selection", selected, "--output", str(output)]
    timeout = min(300, max(15, int((_view(params).get("budget") or {}).get("max_seconds") or 60)))
    completed = subprocess.run(command, cwd=work, capture_output=True, text=True,
                               timeout=timeout, check=False, env=dict(os.environ))
    receipt = work / "execution_receipt.json"
    receipt.write_text(json.dumps({"argv": command, "returncode": completed.returncode,
                                   "stdout": completed.stdout[-4000:],
                                   "stderr": completed.stderr[-4000:],
                                   "completed_at": _now()}, ensure_ascii=False, indent=2), encoding="utf-8")
    if completed.returncode or not output.is_file():
        return {"ok": False, "status": "failed", "error": "blind action runner failed",
                "files": [str(receipt)]}
    evidence = json.loads(output.read_text(encoding="utf-8"))
    run_config = dict(evidence.get("run_config") or {})
    run_config["policy"] = _policy(params)
    evidence["run_config"] = run_config
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "status": "completed", "summary": f"executed committed action {selected}",
            "files": [str(output), str(receipt)], "evidence_refs": [str(output), str(receipt)],
            "metrics": dict(evidence.get("metrics") or {}),
            "semantic_output": {"selected_id": selected, "metrics": evidence.get("metrics") or {},
                                "verified_artifacts": [str(output)], "run_config": run_config}}


def outcome_verify(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    execution = _semantic(params, "execute")
    valid = bool(execution.get("selected_id") and execution.get("verified_artifacts"))
    record = {"valid_execution": valid, "selected_id": execution.get("selected_id"),
              "oracle_visible": False, "metric_visible": False,
              "note": "subject verifies execution shape only; parent owns outcome score"}
    return {"ok": valid, "status": "completed" if valid else "failed",
            "summary": "verified execution without oracle feedback", "semantic_output": record}


def settlement(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    verified = _semantic(params, "verify").get("valid_execution") is True
    record = {"decision": "executed_pending_blind_evaluation" if verified else "invalid",
              "authoritative": False, "oracle_visible": False, "settled_at": _now()}
    return {"ok": verified, "status": "completed" if verified else "failed",
            "summary": record["decision"], "semantic_output": record}


DEFINITIONS = [
    EventDefinition("loop_bench.task_inspect", "benchmark", "读取公开盲测任务", task_inspect, reads_existing_artifact=True),
    EventDefinition("loop_bench.candidate_propose", "benchmark", "基于公开证据提出候选", candidate_propose, execution_method="llm"),
    EventDefinition("loop_bench.candidate_critic", "benchmark", "反驳候选或执行消融", candidate_critic, execution_method="llm"),
    EventDefinition("loop_bench.candidate_select", "benchmark", "选择一个可执行候选", candidate_select),
    EventDefinition("loop_bench.commitment_freeze", "benchmark", "冻结盲测承诺", commitment_freeze, produces_artifact=True),
    EventDefinition("loop_bench.action_execute", "benchmark", "执行冻结候选", action_execute, execution_method="subprocess", produces_artifact=True, idempotent=False, timeout_seconds=300),
    EventDefinition("loop_bench.outcome_verify", "benchmark", "在不读取答案时验证执行完整性", outcome_verify),
    EventDefinition("loop_bench.settlement", "benchmark", "等待父级盲化裁决", settlement),
]
