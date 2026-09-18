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
try:
    from partner.governance.evolution_events import append_evolution_event
except Exception:  # noqa: BLE001
    append_evolution_event = None


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
            from partner.index.resource_catalog import ResourceCatalog
            sources[relative] = ResourceCatalog(_workspace(ctx)).read(path,max_bytes=20000,purpose=purpose)['text']
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


def _freeze_boundary_check(target_files: list[str]) -> dict[str, Any]:
    """Reject a candidate whose target_files touch frozen layers."""
    from pathlib import Path
    import fnmatch
    yaml_path = Path(__file__).resolve().parents[1] / "governance" / "freeze_boundary.yaml"
    if not yaml_path.exists():
        return {"violated": False, "reason": "freeze_boundary.yaml missing; no rule"}
    try:
        import yaml
        rule = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"violated": False, "reason": f"freeze_boundary.yaml unreadable: {exc}"}
    frozen = list(rule.get("frozen_layers") or [])
    mutable = list(rule.get("mutable_layers") or [])
    for raw in target_files:
        target = str(raw).split("::", 1)[0].strip()
        if not target:
            continue
        if any(fnmatch.fnmatch(target, pat) for pat in frozen):
            return {"violated": True, "target": target, "rule": "frozen",
                    "matched_pattern": next(p for p in frozen if fnmatch.fnmatch(target, p))}
    return {"violated": False, "frozen_count": len(frozen), "mutable_count": len(mutable)}


def candidate_propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from pathlib import Path
    contract = params.get('intent_contract') or {}
    target_files = list(contract.get('target_files') or [])
    boundary = _freeze_boundary_check(target_files)
    if boundary.get("violated"):
        if append_evolution_event is not None:
            try:
                append_evolution_event(
                    _workspace(ctx),
                    "boundary/violation",
                    subject_id=str(contract.get('candidate_id') or boundary.get('target', '')),
                    project_id=str(params.get('project_id') or 'agent_self_evolution'),
                    payload={"target": boundary.get("target"),
                             "rule": boundary.get("rule"),
                             "matched_pattern": boundary.get("matched_pattern")},
                    idempotency_key=f"boundary-violation:{boundary.get('target')}",
                )
            except Exception:  # noqa: BLE001
                pass
        return {"ok": False, "status": "rejected",
                "semantic_output": {"boundary_violation": boundary,
                                    "decision": "boundary_violation"},
                "summary": f"candidate touches frozen layer: {boundary.get('target')}"}
    root = Path(__file__).resolve().parents[2]
    context = {}
    for relative in target_files[:3]:
        path = (root / str(relative)).resolve()
        if root / 'partner' in path.parents and path.suffix == '.py' and path.is_file():
            from partner.index.resource_catalog import ResourceCatalog
            context[str(relative)] = ResourceCatalog(_workspace(ctx)).read(path,max_bytes=20000,purpose='candidate_source')['text']
    # Enumerate existing pytest files so the LLM can pick reproducible ones
    test_dir = root / 'tests'
    from partner.index.resource_catalog import ResourceCatalog
    catalog=ResourceCatalog(_workspace(ctx))
    test_paths=[Path(r['path']) for r in catalog.query('code',scope='tests',limit=200)]
    test_files=sorted(p.name for p in test_paths if p.parent==test_dir and p.name.startswith('test_'))
    integration_files=sorted(p.name for p in test_paths if p.parent==test_dir/'integration' and p.name.startswith('test_'))
    tests_catalog = "Existing pytest files you MAY pick as reproducer_tests / regression_tests:\n"
    tests_catalog += "  tests/ (top-level):\n    " + "\n    ".join(test_files) + "\n"
    tests_catalog += "  tests/integration/ (longer runs):\n    " + "\n    ".join(integration_files) + "\n"
    tests_catalog += ("\nRULES:\n"
        "  - reproducer_tests MUST come from this catalog. Do NOT invent test paths.\n"
        "  - regression_tests MUST come from this catalog AND must be disjoint from reproducer_tests.\n"
        "  - reproducer_tests are tests that fail on baseline (current code) before your fix.\n"
        "  - regression_tests are tests that must continue to pass after your fix.\n"
        "  - if the bug actually reproduces in a test listed here, name it as reproducer.\n"
        "  - if no test in this catalog reproduces the bug, your candidate must declare failure_re_reproducer='inconclusive' (do not fabricate).\n")
    params = {'current_source':context,
              'tests_catalog':tests_catalog,
              'existing_reproducer_tests':contract.get('reproducer_tests') or [],
              'existing_regression_tests':contract.get('regression_tests') or [],
              'freeze_boundary':boundary, **params}
    return _llm(ctx, params, "self_evolution_candidate_propose",
        "针对已证实机制提出最小代码或配置 Candidate。字段 candidate_id,target,change,causal_hypothesis,unified_diff,reproducer_tests,regression_tests,success_criteria,rollback,risk。unified_diff 必须能精确应用于现有源文件。reproducer_tests / regression_tests 必须从 tests_catalog 列表里选 — 不要拼造路径。如果现有测试不覆盖该 bug, 允许 reproduction_strategy = 'inconclusive', 此时不要编造测试路径, unified_diff 仍要给出但 paired_tests=[], decision=skipped_isolated。没有源码证据时明确缺失, 不能捏造补丁。不得扩大修改面。每次 candidate 必须 1-3 个 target_files。")


def candidate_critic(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _llm(ctx, params, "self_evolution_candidate_critic",
        "攻击 Candidate 的因果隔离、reward hacking、回归风险和不可逆性。字段 accepted,problems,missing_evidence,required_changes,rollback_trigger。"
        "accepted 仅表示是否可以进入后续隔离试验，不是修复成立或生产晋升；基线/候选运行和补丁精确适用检查将在后续 Event 执行，"
        "不能把尚未运行这些后续步骤作为拒绝所有实验的循环前置条件。指出真实阻碍，不捏造与已提供源码相反的行为。")


def candidate_isolate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    if ((params.get('intent_contract') or {}).get('execution_constraints') or {}).get('proposal_only'):
        return {'ok':False,'status':'blocked','error':'用户仅授权研究方案，不能执行候选修改或隔离实验。'}
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
            # Fallback: light isolated runner still gives promotion_decide a real exit_code
            from partner.observe.isolated_runner import run_fallback_isolated
            fb = run_fallback_isolated(_workspace(ctx), candidate)
            value["fallback_isolation"] = fb
            if fb.get("applied"):
                value["isolation_error"] = None
                value["isolation"] = {
                    "directory": "<fallback>",
                    "manifest_sha256": "",
                    "experiment_id": "fallback_" + str(int(time.time())),
                    "tests": [],
                    "regression_tests": [],
                    "fallback": True,
                    "sanity_test": fb.get("sanity_test", ""),
                    "exit_code": fb.get("exit_code"),
                }
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
        elif isolated.get("fallback") and kind == "candidate":
            # Reuse fallback_isolation already computed in candidate_isolate
            fb = _output(params, "isolate").get("fallback_isolation") or {}
            result = {
                "executed": bool(fb.get("applied")),
                "exit_code": fb.get("exit_code"),
                "reason": "fallback_isolation reused",
                "fallback": True,
            }
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
    if ((params.get('intent_contract') or {}).get('execution_constraints') or {}).get('proposal_only'):
        return {'ok':False,'status':'blocked','error':'用户仅授权研究方案，不能晋升或应用修改。'}
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




def proximity_check(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Before a candidate patch enters the event stream, check it against
    recent committed experiment interventions. If the unified_diff is too
    similar to a recent patch (same target files + overlapping change),
    reject the candidate and record a proximity.rejected evolution event
    so a human can audit the duplicate-detection rule later.

    Rejection is an automatic down-weight, not a hard fail. The flow
    runner treats this event's verdict as a vote; the candidate_critic
    event still decides whether the proposal is acceptable to test.
    """
    candidate = dict(params.get("candidate") or _semantic(params, "candidate") or {})
    target = str(candidate.get("target") or "")
    diff = str(candidate.get("unified_diff") or "")
    hypothesis = str(candidate.get("causal_hypothesis") or "")
    if not target or not diff:
        return {"ok": True, "status": "completed",
                "semantic_output": {"decision": "skipped",
                                    "reason": "no target/unified_diff in candidate"},
                "summary": "Proximity check skipped: incomplete candidate"}
    from pathlib import Path
    root = Path(_workspace(ctx))
    experiments_path = root / "share" / "mind" / "governance" / "experiments"
    threshold = float((params.get("intent_contract") or {}).get("proximity_threshold") or 0.55)
    history = []
    from partner.index.resource_catalog import ResourceCatalog
    if experiments_path.exists():
        for item in ResourceCatalog(root).query('experiment',limit=20):
            path=Path(item['path'])
            try:
                import json as _json
                rec = _json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
            history.append({
                "experiment_id": rec.get("experiment_id", ""),
                "intervention": str(rec.get("intervention") or ""),
                "hypothesis": str(rec.get("hypothesis") or ""),
                "status": str(rec.get("status") or ""),
                "outcome": str(rec.get("outcome") or ""),
                "occurred_at": str(rec.get("started_at") or ""),
            })
    def _lines(text: str) -> set[str]:
        out = set()
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith(("#","//","diff","---","+++","@@","index","=")):
                continue
            if s.startswith(("+","-","!")) and len(s) > 1:
                out.add(s[1:].strip())
        return out
    new_lines = _lines(diff)
    new_targets = set(target.split(","))
    scored = []
    for h in history:
        old_targets = set(str(h.get("intervention","")).split("==")[0].split(","))
        target_overlap = len(new_targets & old_targets) / max(1, len(new_targets | old_targets))
        line_overlap = len(new_lines & _lines(h["intervention"])) / max(1, len(new_lines))
        score = 0.6 * target_overlap + 0.4 * line_overlap
        scored.append({"experiment_id": h["experiment_id"], "score": round(score, 3),
                       "target_overlap": round(target_overlap, 3),
                       "line_overlap": round(line_overlap, 3),
                       "outcome": h["outcome"]})
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:3]
    accepted = not top or top[0]["score"] < threshold
    decision = "accepted" if accepted else "rejected"
    if not accepted:
        from partner.governance.evolution_events import append_evolution_event
        try:
            append_evolution_event(
                _workspace(ctx),
                "proximity/rejected",
                subject_id=str(candidate.get("candidate_id") or ""),
                project_id=str(params.get("project_id") or "agent_self_evolution"),
                payload={
                    "candidate_target": target,
                    "candidate_hypothesis": hypothesis,
                    "neighbors": top,
                    "threshold": threshold,
                    "decision": decision,
                },
                evidence_refs=[str(experiments_path)],
                idempotency_key=f"proximity-rejected:{candidate.get('candidate_id') or target}",
            )
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "status": "completed",
            "semantic_output": {"decision": decision, "score": top[0]["score"] if top else 0.0,
                                "threshold": threshold, "neighbors": top},
            "summary": (f"Proximity: {decision}; top score "
                        f"{round(top[0]['score'],3) if top else 0.0} "
                        f"< threshold {threshold}")}


DEFINITIONS = [
    EventDefinition("self_evolution.issue_observe", "self_evolution", "从真实 Episode 登记 Partner 机制问题", issue_observe),
    EventDefinition("self_evolution.issue_diagnose", "self_evolution", "三类边界与反事实根因诊断", issue_diagnose, execution_method="llm"),
    EventDefinition("self_evolution.candidate_propose", "self_evolution", "提出最小可回滚机制 Candidate", candidate_propose, execution_method="llm"),
    EventDefinition("self_evolution.proximity_check", "self_evolution", "candidate 与历史 patch 距离判定，过近自动降权并记 proximity/rejected 事件", proximity_check),
    EventDefinition("self_evolution.candidate_critic", "self_evolution", "独立反驳 Candidate 与 reward hacking", candidate_critic, execution_method="llm"),
    EventDefinition("self_evolution.candidate_isolate", "self_evolution", "建立 production_effective=false 的隔离实验", candidate_isolate, produces_artifact=True),
    EventDefinition("self_evolution.baseline_execute", "self_evolution", "登记冻结基线隔离执行结果", _execution_record("baseline"), idempotent=False),
    EventDefinition("self_evolution.candidate_execute", "self_evolution", "登记 Candidate 隔离执行结果", _execution_record("candidate"), idempotent=False),
    EventDefinition("self_evolution.matched_compare", "self_evolution", "同输入同预算比较基线与 Candidate", matched_compare),
    EventDefinition("self_evolution.promotion_decide", "self_evolution", "由机器证据决定拒绝、保留或晋升", promotion_decide, idempotent=False),
]
