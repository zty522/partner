"""Partner-internal self-evolution Events.

These Events never treat a model verdict as promotion evidence.  The model may
diagnose and propose; isolated executions and machine criteria decide.
"""
from __future__ import annotations

from typing import Any
import json

from partner.event_fabric.catalog import EventDefinition
from partner.governance.evolution_loop import record_issue, start_experiment, decide_experiment
from ._llm import call_model, json_object


def _workspace(ctx: Any) -> str:
    return str(getattr(ctx, "workspace", "") or "")


def _output(params: dict[str, Any], node_id: str) -> dict[str, Any]:
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    value = outputs.get(node_id)
    return dict(value) if isinstance(value, dict) else {}


def _semantic(params: dict[str, Any], node_id: str) -> dict[str, Any]:
    value = _output(params, node_id).get("semantic_output")
    return dict(value) if isinstance(value, dict) else {}


def issue_observe(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    evidence = list(params.get("evidence_refs") or params.get("evidence")
                    or (params.get('intent_contract') or {}).get('evidence_refs') or [])
    if not evidence:
        parent = params.get("parent_flow_outputs") if isinstance(params.get("parent_flow_outputs"), dict) else {}
        for row in parent.values():
            if isinstance(row, dict):
                evidence.extend(str(value) for value in row.get("evidence_refs") or row.get("files") or [])
    evidence = list(dict.fromkeys(str(value) for value in evidence if value))
    if not evidence:
        return {"ok": False, "status": "failed", "error": "self-evolution requires persisted failure evidence"}
    requested_class = str(params.get("failure_class") or "event")
    allowed_classes = {"context", "planning", "event", "environment", "verification",
                       "delivery", "scheduling", "data", "model", "unknown"}
    value = record_issue(_workspace(ctx), {
        "summary": str(params.get("summary") or params.get('request') or "observed Partner mechanism failure"),
        "category": requested_class if requested_class in allowed_classes else "event",
        "severity": str(params.get("severity") or "medium"),
        "evidence": evidence, "instance_id": str(params.get("instance_id") or ""),
        "project_id": str(params.get("project_id") or ""),
    })
    value.setdefault("status", "completed" if value.get("ok") else "failed")
    return value


def _llm(ctx: Any, params: dict[str, Any], purpose: str, instruction: str) -> dict[str, Any]:
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    contract = params.get('intent_contract') or {}
    sources = {}
    for relative in list(contract.get('target_files') or [])[:3] + list(contract.get('reproducer_tests') or [])[:3]:
        relative = str(relative).split('::',1)[0]
        path = (root / relative).resolve()
        if root in path.parents and relative.startswith(('partner/','tests/')) and path.suffix == '.py' and path.is_file():
            sources[relative] = path.read_text()[:20000]
    params = {'actual_source_read_now':sources, 'request':params.get('request'), **params}
    raw, usage = call_model(ctx, purpose=purpose, prompt=(instruction
        + "\n只输出 JSON。必须区分业务假设失败、知识缺口、环境错误和 Partner 机制缺陷。"
          "actual_source_read_now 是本 Event 实际读取的源码与复现断言，优先于之前模型写下的未知或猜测。"
          "模型判断只是 Candidate 输入，绝不是晋升证据。\n证据="
        + json.dumps(params, ensure_ascii=False)[:64000]))
    value = json_object(raw)
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": str(value.get("causal_mechanism") or value.get("reason") or purpose),
            "token_usage": usage, "model_output": raw}


def issue_diagnose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _llm(ctx, params, "self_evolution_issue_diagnose",
        "重建时间线，提出至少三个竞争根因及能区分它们的反事实。字段 timeline,causal_hypotheses,disconfirming_evidence,verified_boundary,unknowns,minimum_probe。")


def candidate_propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from pathlib import Path
    contract = params.get('intent_contract') or {}
    root = Path(__file__).resolve().parents[2]
    context = {}
    for relative in list(contract.get('target_files') or [])[:3]:
        path = (root / str(relative)).resolve()
        if root / 'partner' in path.parents and path.suffix == '.py' and path.is_file():
            context[str(relative)] = path.read_text()[:20000]
    params = {'current_source':context,
              'existing_reproducer_tests':contract.get('reproducer_tests') or [],
              'existing_regression_tests':contract.get('regression_tests') or [], **params}
    return _llm(ctx, params, "self_evolution_candidate_propose",
        "针对已证实机制提出最小代码或配置 Candidate。字段 candidate_id,target,change,causal_hypothesis,unified_diff,reproducer_tests,regression_tests,success_criteria,rollback,risk。unified_diff 必须能精确应用于现有源文件；测试必须是仓库现有 pytest 路径，不能输出伪造的 result。没有源码证据时明确缺失，不能捏造补丁。不得扩大修改面。")


def candidate_critic(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _llm(ctx, params, "self_evolution_candidate_critic",
        "攻击 Candidate 的因果隔离、reward hacking、回归风险和不可逆性。字段 accepted,problems,missing_evidence,required_changes,rollback_trigger。"
        "accepted 仅表示是否可以进入后续隔离试验，不是修复成立或生产晋升；基线/候选运行和补丁精确适用检查将在后续 Event 执行，"
        "不能把尚未运行这些后续步骤作为拒绝所有实验的循环前置条件。指出真实阻碍，不捏造与已提供源码相反的行为。")


def candidate_isolate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(params.get("candidate") or _semantic(params, "candidate") or {})
    observed = _output(params, "observe")
    issue = observed.get("issue") if isinstance(observed.get("issue"), dict) else {}
    value = start_experiment(_workspace(ctx), {
        "issue_id": str(params.get("issue_id") or candidate.get("issue_id") or issue.get("issue_id") or ""),
        "project_id": str(params.get("project_id") or "agent_self_evolution"),
        "hypothesis": str(candidate.get("causal_hypothesis") or ""),
        "intervention": str(candidate.get("change") or ""),
        "baseline": dict(candidate.get("baseline_probe") or {}),
        "success_criteria": list(candidate.get("success_criteria") or []),
        "tests": list(params.get("evidence_refs") or []),
    })
    value["production_effective"] = False
    critic = _semantic(params, "critic")
    if critic.get("accepted") and candidate.get("unified_diff"):
        from partner.runtime.matched_execution import isolate
        try:
            value["isolation"] = isolate(_workspace(ctx), candidate)
            value["files"] = [value["isolation"]["directory"] + "/manifest.json"]
        except (OSError, ValueError) as exc:
            value["isolation_error"] = str(exc)
    else:
        value["isolation_error"] = "No accepted executable patch with preexisting tests"
    value.setdefault("status", "completed" if value.get("ok") else "failed")
    return value


def _execution_record(kind: str):
    def handler(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        from partner.runtime.matched_execution import execute
        isolated = _output(params, "isolate").get("isolation") or {}
        result = {"executed": False, "exit_code": None,
                  "reason": f"{kind} has no trusted isolated runner result"}
        if isolated.get("experiment_id"):
            try:
                result = execute(_workspace(_ctx), isolated["experiment_id"], kind)
            except (OSError, ValueError, KeyError) as exc:
                result["reason"] = str(exc)
        executed = result.get("executed") is True
        return {"ok": True, "status": "completed",
                "semantic_output": result,
                "summary": (f"{kind} 隔离执行结果已登记" if executed else
                            f"{kind} 缺少隔离执行证据，标记为 inconclusive"),
                "production_effective": False}
    return handler


def matched_compare(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    before = dict(params.get("baseline") or _semantic(params, "baseline") or {})
    after = dict(params.get("candidate_result") or _semantic(params, "candidate_run") or {})
    from partner.runtime.matched_execution import compare
    comparison = compare(_workspace(_ctx), before, after)
    from pathlib import Path
    from partner.runtime.action_execution import write_json
    path = Path(_workspace(_ctx)) / 'state/event_runtime/comparisons' / (str(params.get('flow_id') or 'standalone') + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, comparison)
    comparison['evidence_refs'] = comparison['evidence_refs'] + [str(path)]
    return {"ok": True, "status": "completed", "semantic_output": comparison,
            "evidence_refs": comparison["evidence_refs"],
            "summary": "隔离匹配比较：" + comparison["decision"], "production_effective": False}


def promotion_decide(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    comparison = _semantic(params, "compare")
    requested = str(comparison.get("decision") or "inconclusive")
    isolate = _output(params, "isolate")
    experiment = isolate.get("experiment") if isinstance(isolate.get("experiment"), dict) else {}
    candidate = _semantic(params, "candidate")
    if requested == 'promoted':
        from partner.governance.candidate_execution import load_candidate, validate_execution_contract
        registered = load_candidate(_workspace(ctx), str(candidate.get('candidate_id') or ''))
        if not registered or not validate_execution_contract(registered.get('execution_contract'))[0]:
            requested = 'inconclusive'
            params = {**params, 'reason':'隔离候选通过匹配测试，但尚未绑定可治理的生产执行契约；保留候选，未应用生产代码。'}
    criteria = dict(params.get("criteria_results") or comparison.get("criteria_results") or {})
    value = decide_experiment(_workspace(ctx), {
        "experiment_id": str(params.get("experiment_id") or experiment.get("experiment_id") or ""),
        "candidate_id": str(params.get("candidate_id") or candidate.get("candidate_id") or ""),
        "project_id": str(params.get("project_id") or "agent_self_evolution"),
        "decision": requested, "criteria_results": criteria,
        "regression_passed": bool(comparison.get("regression_passed")),
        "metrics_before": dict(params.get("metrics_before") or {}),
        "metrics_after": dict(params.get("metrics_after") or {}),
        "evidence": list(comparison.get("evidence_refs") or []),
        "reason": str(params.get("reason") or "matched Event Flow decision"),
    })
    value["production_effective"] = False  # shadow approval does not apply code to production
    value["evolution_delta"] = bool(value.get("status") == "promoted")
    value.setdefault("status", "completed" if value.get("ok") else "failed")
    return value


DEFINITIONS = [
    EventDefinition("self_evolution.issue_observe", "self_evolution", "从真实 Episode 登记 Partner 机制问题", issue_observe),
    EventDefinition("self_evolution.issue_diagnose", "self_evolution", "三类边界与反事实根因诊断", issue_diagnose, execution_method="llm"),
    EventDefinition("self_evolution.candidate_propose", "self_evolution", "提出最小可回滚机制 Candidate", candidate_propose, execution_method="llm"),
    EventDefinition("self_evolution.candidate_critic", "self_evolution", "独立反驳 Candidate 与 reward hacking", candidate_critic, execution_method="llm"),
    EventDefinition("self_evolution.candidate_isolate", "self_evolution", "建立 production_effective=false 的隔离实验", candidate_isolate, produces_artifact=True),
    EventDefinition("self_evolution.baseline_execute", "self_evolution", "登记冻结基线隔离执行结果", _execution_record("baseline"), idempotent=False),
    EventDefinition("self_evolution.candidate_execute", "self_evolution", "登记 Candidate 隔离执行结果", _execution_record("candidate"), idempotent=False),
    EventDefinition("self_evolution.matched_compare", "self_evolution", "同输入同预算比较基线与 Candidate", matched_compare),
    EventDefinition("self_evolution.promotion_decide", "self_evolution", "由机器证据决定拒绝、保留或晋升", promotion_decide, idempotent=False),
]
