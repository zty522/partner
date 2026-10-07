"""Project-line Events: one falsifiable business step at a time."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import re

from partner.event_fabric.catalog import EventDefinition
from partner.governance.project_cognition import build_project_cognition_context
from ._llm import call_model, json_object, event_facts


def _workspace(ctx: Any) -> str:
    return str(getattr(ctx, "workspace", "") or "")


def _fresh_first_round(params: dict[str, Any]) -> bool:
    contract = params.get('intent_contract') if isinstance(params.get('intent_contract'), dict) else {}
    try:
        round_number = int(contract.get('round_number') or 0)
    except (TypeError, ValueError):
        round_number = 0
    original = str(contract.get('original_request') or params.get('request') or '')
    return round_number == 1 and bool(re.search(
        r'(?:第一轮|第\s*1\s*轮|first\s+round).{0,100}(?:生成|建立|创建|只允许|generate|create|establish)',
        original, re.I | re.S))


def state_inspect(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    project_id = str(params.get("project_id") or "")
    value = build_project_cognition_context(
        _workspace(ctx), project_id, project_steps=int(params.get("project_steps") or 0),
        max_chars=int(params.get("max_chars") or 32000),
    )
    # Discover context beside user-specified inputs. Project IDs can be
    # newly classified; that must not hide the actual scientific project.
    request = str(params.get('request') or '')
    local = []
    for raw in re.findall(r'/[^\s\"<>，。]+\.(?:pdb|cif|csv|json|sdf)', request):
        source = Path(raw)
        if not source.is_file(): continue
        root = source.parent
        for parent in list(source.parents)[:4]:
            if (parent/'data').is_dir() and parent.name not in ('work','mnt'):
                root = parent
                break
        listing = []
        excerpts = []
        for entry in sorted(root.iterdir()):
            listing.append(entry.name + ('/' if entry.is_dir() else ''))
            if entry.is_file() and entry.suffix == '.md' and len(excerpts)<4:
                excerpts.append({'path':str(entry), 'text':entry.read_text(errors='replace')[:3000]})
            if entry.is_dir() and entry.name in ('data','analysis','results','docs'):
                listing.extend(str(x.relative_to(root)) + ('/' if x.is_dir() else '') for x in list(entry.iterdir())[:25])
        local.append({'input':str(source), 'project_root':str(root), 'entries':listing[:100], 'documents':excerpts})
    attached = []
    for attachment in params.get('attachments') or []:
        raw = attachment.get('path') if isinstance(attachment, dict) else attachment
        source = Path(str(raw or ''))
        if source.is_file():
            row = {'path': str(source), 'bytes': source.stat().st_size}
            if source.suffix.lower() in ('.json', '.csv', '.txt', '.md'):
                row['excerpt'] = source.read_text(errors='replace')[:5000]
            attached.append(row)
    from partner.runtime.execution_context import recent_execution_context
    recent = recent_execution_context(_workspace(ctx), project_id, str(getattr(ctx, 'job_id', '')))
    from partner.runtime.execution_context import verified_project_artifacts
    from partner.runtime.action_execution import write_json
    fresh_first = _fresh_first_round(params)
    artifacts = ([] if fresh_first else
        verified_project_artifacts(_workspace(ctx),project_id,str(getattr(ctx,'job_id','')),
                                   [r['input'] for r in local]))
    index=Path(getattr(ctx,'working_dir',Path(_workspace(ctx))/'state/event_runtime/work'/str(getattr(ctx,'job_id','inspect'))))/'verified_project_artifacts.json'
    write_json(index,{'project_id':project_id,'artifacts':artifacts})
    value['verified_artifact_index']={'path':str(index),'count':len(artifacts),'recent':artifacts[:16],
        'historical_execution_inputs_suppressed':fresh_first}
    canonical_project_root = Path(_workspace(ctx)) / 'share' / 'projects' / project_id
    if canonical_project_root.is_dir():
        value['project_root'] = str(canonical_project_root)
        value['project_root_index'] = [
            str(path.relative_to(canonical_project_root))
            for path in sorted(canonical_project_root.glob('*'))[:40]
        ]
    root_id=str(params.get('root_event_id') or '')
    if re.fullmatch(r'evt_[a-f0-9]+',root_id):
        feedback=Path(_workspace(ctx))/'state/application/request_context'/f'{root_id}.json'
        if feedback.is_file():
            value['operator_feedback']=json.loads(feedback.read_text())
    value = {'recent_execution':recent, 'attached_evidence':attached, 'local_input_context':local, **value}
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": f"已重建 {project_id} 的项目状态与证据上下文"}


def _deliberate(ctx: Any, params: dict[str, Any], purpose: str, instruction: str) -> dict[str, Any]:
    focus = ""
    upstream = params.get("upstream") if isinstance(params.get("upstream"), dict) else {}
    pre = (upstream.get("pre_iteration_reflect") or {}).get("semantic_output") or {}
    raw_focus = str(pre.get("improvement_focus") or "").strip()
    if raw_focus:
        focus = ("\n【上一轮自反思改进提示（来自 INSTANCE 历史 PROJECT_ITERATION 轮次 + 自进化修改 + message_critic 拒收记录，必须遵守）】\n"
                 + raw_focus + "\n")
    recalled=((params.get('flow_outputs') or {}).get('recall') or {}).get('semantic_output') or {}
    import hashlib as _hl
    def _synth_rid(r):
        rid = r.get('record_id')
        if rid:
            return rid
        content = r.get('content') if isinstance(r.get('content'), (str, dict)) else str(r.get('content') or '')
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False, sort_keys=True)
        mat = (str(r.get('recorded_at') or ''), str(r.get('project_id') or ''), str(content))
        return 'mem:' + _hl.sha1('|'.join(mat).encode('utf-8')).hexdigest()[:16]
    avail = []
    known = set()
    for k in ('lessons','active_habits','preferences'):
        for r in recalled.get(k,[]) or []:
            if not r:
                continue
            rid = _synth_rid(r)
            known.add(rid)
            content = r.get('content')
            # Lessons carry their full content (up to 480 chars) so the
            # LLM prompt actually sees the lesson text, not just a hint.
            limit = 480 if k == 'lessons' else 160
            cs = (str(content)[:limit] if not isinstance(content, dict)
                  else json.dumps(content, ensure_ascii=False)[:limit])
            avail.append({'kind': k, 'rid': rid,
                          'status': r.get('status', ''),
                          'snippet': cs})
    from partner.memory import EventMemory
    avail_json = json.dumps(avail[:24], ensure_ascii=False)
    # Sprint 37 (第四步纠偏): inject the project brief's falsified routes /
    # next minimum action / forbidden directions so the planner treats them as
    # hard constraints.  Without this, plan/continuation repeatedly re-propose
    # actions the project itself has already proven ineffective (e.g. re-sorting
    # by QED/SA in molecular_generation).
    guardrails = ""
    try:
        from partner.governance.project_cognition import load_project_brief_guardrails
        guardrails = load_project_brief_guardrails(
            getattr(ctx, "workspace", None) or "",
            str(params.get("project_id") or ""))
    except Exception:
        guardrails = ""
    if guardrails:
        guardrails = "\n" + guardrails + "\n"
    raw, usage = call_model(ctx, purpose=purpose, prompt=(
        focus + guardrails + instruction + f" 可引用的真实记忆(最多24条,kind+rid+snippet): {avail_json}. 使用记忆时输出memory_usage列表,每项含record_id(必须是上述rid之一)、effect、status(applied/rejected);没使用则空列表.不能声称读取未提供的记忆." + "\n简洁回答，每个字段一两句话，完整 JSON 不超过2000汉字，不复述输入全文。只输出 JSON；不得把生成报告、发送消息或重复旧结果算作项目推进。\n事实="
        + event_facts(params)
    ))
    value = json_object(raw)
    for usage_row in value.get('memory_usage') or []:
        if usage_row.get('record_id') not in known or usage_row.get('status') not in ('applied','rejected'):
            raise ValueError('memory usage cites unavailable memory or invalid status')
        EventMemory(ctx.workspace).record_usage(event_id=params.get('event_id',params.get('node_id','')),
            flow_id=params.get('flow_id',''),memory_ids=[usage_row['record_id']],
            effect={'model_reported_effect':usage_row.get('effect'), 'verification':'await downstream outcome'},status='referenced')
    summary = (value.get("reason") or value.get("hypothesis") or value.get("lesson")
               or value.get("next_question") or purpose.replace("_", " "))
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": str(summary),
            "token_usage": usage, "model_output": raw}



def plan_propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Single-step plan + event flow propose: 合并 hypothesis/critic/select.

    LLM 直接产出：本轮要执行的 event 是什么、参数是什么、为什么选它、
    怎么证伪、回滚方案。集成 proposer + attacker + selector 三视角于一次 LLM 调用。
    """
    output = _deliberate(ctx, params, "project_plan_propose",
        "你同时是 proposer / attacker / selector: 先列 2-3 个本轮可执行的 event 候选 (id, event_type, parameters)，"
        "再对每个候选攻击 (重复?不可证伪?缺输入?只产报告?超权限?)，"
        "最终选 1 个最能补齐原始目标缺口 (goal_gaps) 的 event。"
        "字段 candidates (id,event_type,parameters,attack_findings,expected_observation,disproof,risk), "
        "selected (event_type,parameters,reason,success_criteria,evidence_contract,rollback,goal_gaps)。"
        "每个候选必须是最小但完整的业务实验，必要依赖/加载/修错/一次实测包含在同 action 内。"
        "不要拆纯 import/文件列举/权重检查成独立轮次；不要凭追阈值绕过用户要求。"
        "除非用户目标本身只是核验已经存在的产物，否则不得选择只读检查作为最终动作；"
        "selected 的 event_type、参数、允许写入目录和预期产物会成为机器强制执行合同，执行阶段不得扩展。"
        "宁选能产生真实端到端业务测量的 event，工具检查作为其内部步骤。"
        "若一轮执行量过大, 先跑小样本再扩展, 不要凭空要求大规模阈值。"
        "已成功恢复/解析/索引/复制的产物不能再次作为主任务, 必须找新增 evidence 或新算法。")
    semantic = output.get("semantic_output") if isinstance(output.get("semantic_output"), dict) else {}
    selected = semantic.get("selected") if isinstance(semantic.get("selected"), dict) else {}
    blueprint = (params.get("intent_contract") or {}).get("round_blueprint") or {}
    # The parent cycle has already frozen the purpose of this round.  The
    # domain LLM may propose implementation details, but it must not advance to
    # a later round (or replay an earlier one) merely because historical
    # memories mention those artifacts.  Bind the executable action to the
    # current blueprint before Commitment hashes it.
    if isinstance(blueprint, dict) and str(blueprint.get("round_goal") or "").strip():
        proposed = dict(selected)
        round_goal = str(blueprint["round_goal"]).strip()
        required = list(blueprint.get("required_evidence") or [])
        failure_conditions = list(blueprint.get("failure_conditions") or [])
        semantic["planner_selected"] = proposed
        semantic["selected"] = {
            "id": "round_blueprint_action",
            "candidate_id": "round_blueprint_action",
            "event_type": "project.agent_action",
            "description": round_goal,
            "parameters": {
                "action": round_goal,
                "round_number": blueprint.get("round_number"),
                "required_evidence": required,
                "failure_conditions": failure_conditions,
            },
            "reason": "machine-bound to the parent cycle's frozen round blueprint",
            "expected_observation": str(blueprint.get("expected_effect") or ""),
            "success_criteria": required,
            "evidence_contract": {
                "requires_new_business_evidence": True,
                "required_evidence": required,
                "forbidden_future_round_execution": True,
            },
            "rollback": "remove only artifacts created by this bounded round",
            "goal_gaps": [round_goal],
            "proposed_by": "runtime_round_blueprint_guard",
        }
        semantic["selection_repaired"] = "selected_action_bound_to_frozen_round_blueprint"
        output["semantic_output"] = semantic
        selected = semantic["selected"]
    request = str((params.get("intent_contract") or {}).get("original_request")
                  or params.get("request") or "")
    if selected and _read_only_action(selected) and not _request_is_verification(request):
        candidates = [row for row in semantic.get("candidates") or []
                      if isinstance(row, dict) and not _read_only_action(row)]
        if candidates:
            chosen = dict(candidates[0])
        else:
            chosen = {"id":"repaired_blueprint_action", "event_type":"project.agent_action",
                      "parameters":{"action":str(blueprint.get("round_goal") or request)[:2000]},
                      "expected_observation":str(blueprint.get("expected_effect") or ""),
                      "disproof":"required project evidence is absent", "risk":"bounded"}
        semantic["selected"] = {
            **chosen,
            "reason":"deterministic guard replaced a read-only action that could not advance the project goal",
            "success_criteria": chosen.get("success_criteria") or
                list(((params.get("intent_contract") or {}).get("round_blueprint") or {}).get("required_evidence") or []),
            "evidence_contract": chosen.get("evidence_contract") or {"requires_new_business_evidence":True},
            "rollback": chosen.get("rollback") or "remove only artifacts created by this bounded action",
            "goal_gaps": chosen.get("goal_gaps") or ["execute the declared project experiment"],
        }
        semantic["selection_repaired"] = "read_only_action_rejected_for_experimental_goal"
        output["semantic_output"] = semantic
    return output



def hypothesis_propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _deliberate(ctx, params, "project_hypothesis_propose",
        "提出 2-4 个彼此不同、可证伪且本轮可执行的项目假设。字段 hypotheses，每项含 id,claim,action,event_type,parameters,expected_observation,disproof,required_evidence,risk。每个候选动作应是最小但完整的业务实验，必要的依赖检查、加载、修错和一次实测应包含在同一动作内。不要将纯 import、文件列举、权重形状检查拆成独立项目轮次；只有已经真实失败且需单独定位的问题才选诊断动作。")


def hypothesis_critic(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _deliberate(ctx, params, "project_hypothesis_critic",
        "扮演反驳者，检查每个假设是否重复、不可证伪、缺输入、只产报告或超出权限。字段 accepted,rejected,unknowns,reason。")


def action_select(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _deliberate(ctx, params, "project_action_select",
        "从已通过反驳的可用动作中选择一个最能补齐原始目标缺口的动作。先列 goal_gaps（原始目标尚缺哪些可验证能力/产物），不要反复调整参数追逐模型自己设定的阈值而绕过用户要求的算法实现。字段 selected_event,parameters,reason,success_criteria,evidence_contract,rollback,goal_gaps。优先选择能产生最小端到端业务测量的动作，将工具检查作为该动作的内部步骤；不要仅为取得一个新 JSON 而选择探活。当计算量过大时先真实运行小批样本，不凭空要求大规模阈值。")


_READ_ONLY_ACTION_MARKERS = ("read", "inspect", "verify", "hash_check", "state_check")


def _read_only_action(selected: dict[str, Any]) -> bool:
    event_type = str(selected.get("event_type") or "").lower()
    action = str((selected.get("parameters") or {}).get("action") or "").lower()
    return any(marker in event_type or marker in action for marker in _READ_ONLY_ACTION_MARKERS)


def _request_is_verification(request: str) -> bool:
    text = str(request or "")
    if re.search(r"生成|训练|建立|创建|推进|改进|修改|写入|candidate|experiment|train|create", text, re.I):
        return False
    return bool(re.search(r"只(?:做|需|需要)?(?:核验|检查|读取)|验证已有|核对已有|verify existing|read[- ]only", text, re.I))


def _declared_output_root(request: str, workspace: str,
                          intent_contract: dict[str, Any] | None = None) -> tuple[Path | None, str]:
    """Return an explicit user output root when it is inside the shared work tree."""
    patterns = (
        r"工作目录(?:限定为|必须为|设为|为|[:：])\s*[`\"']?(/[^\s`\"'，。；;]+)",
        r"(?:working[_ ]?directory|work[_ ]?dir)\s*(?:=|:|is)\s*[`\"']?(/[^\s`\"'，。；;]+)",
        r"(?:在|输出到|写入|保存到)\s*[`\"']?(/[^\s`\"'，。；;]+)\s*(?:创建并推进|创建|推进|执行|开展|完成|保存|输出)",
    )
    raw = ""
    for pattern in patterns:
        match = re.search(pattern, request, re.IGNORECASE)
        if match:
            raw = match.group(1).rstrip(".、)")
            break
    if not raw and isinstance(intent_contract, dict):
        constraints = intent_contract.get("constraints") or []
        if isinstance(constraints, str):
            constraints = [constraints]
        for constraint in constraints:
            text = str(constraint)
            if not re.search(r"工作目录|输出目录|指定路径|路径.*(?:限定|严格|必须)", text):
                continue
            match = re.search(r"(/[^\s`\"'，。；;]+)", text)
            if match:
                raw = match.group(1).rstrip(".、)")
                break
    if not raw:
        return None, ""
    candidate = Path(raw).expanduser().resolve()
    allowed_root = Path(workspace).expanduser().resolve().parent
    try:
        candidate.relative_to(allowed_root)
    except ValueError:
        return None, f"declared output root is outside shared work tree: {candidate}"
    return candidate, ""


def _artifact_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    value: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or ".execution" in path.parts:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        value[str(path.resolve())] = (stat.st_size, stat.st_mtime_ns)
    return value


def _deterministic_paired_audit(refs: list[str], work: Path) -> Path | None:
    """Audit two frozen regression arms when the action LLM is unavailable."""
    import hashlib
    candidates = []
    for raw in refs:
        path = Path(str(raw))
        if path.suffix.lower() != '.json' or not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(value.get('predictions'), list) and isinstance(value.get('metrics'), dict):
            candidates.append((path, value))
    baseline = next(((p,v) for p,v in candidates
        if not ((v.get('run_config') or {}).get('features') or {}).get('declared_feature')), None)
    candidate = next(((p,v) for p,v in candidates
        if ((v.get('run_config') or {}).get('features') or {}).get('declared_feature')), None)
    if not baseline or not candidate:
        return None
    bp, b = baseline; cp, c = candidate
    bpred, cpred = b['predictions'], c['predictions']
    paired = (len(bpred) == len(cpred) and all(
        str(x.get('sample_id')) == str(y.get('sample_id'))
        and float(x.get('y_true')) == float(y.get('y_true'))
        for x,y in zip(bpred,cpred)))
    bf = {int(row['fold']): row for row in b.get('fold_metrics') or []}
    cf = {int(row['fold']): row for row in c.get('fold_metrics') or []}
    folds = [{'fold':fold, 'baseline_rmse':float(bf[fold]['rmse']),
              'candidate_rmse':float(cf[fold]['rmse']),
              'rmse_delta':float(bf[fold]['rmse'])-float(cf[fold]['rmse'])}
             for fold in sorted(set(bf) & set(cf))]
    bm, cm = b['metrics'], c['metrics']
    delta = float(bm['rmse']) - float(cm['rmse'])
    same_data = ((b.get('provenance') or {}).get('data_hash') ==
                 (c.get('provenance') or {}).get('data_hash'))
    same_folds = ((b.get('run_config') or {}).get('folds') ==
                  (c.get('run_config') or {}).get('folds'))
    same_seeds = ((b.get('run_config') or {}).get('seeds') ==
                  (c.get('run_config') or {}).get('seeds'))
    guardrails = {
        'paired_sample_ids_and_y_true': paired, 'same_data_hash': same_data,
        'same_folds': same_folds, 'same_seeds': same_seeds,
        'five_fold_results_complete': len(folds) == 5,
        'no_target_leakage': bool((b.get('guardrails') or {}).get('no_target_leakage'))
            and bool((c.get('guardrails') or {}).get('no_target_leakage')),
        'official_test_not_used_for_tuning': bool((b.get('guardrails') or {}).get('official_test_not_used_for_tuning'))
            and bool((c.get('guardrails') or {}).get('official_test_not_used_for_tuning')),
        'within_budget': bool((b.get('guardrails') or {}).get('within_budget'))
            and bool((c.get('guardrails') or {}).get('within_budget')),
        'mae_not_worse': float(cm['mae']) <= float(bm['mae']),
        'r2_not_worse': float(cm['r2']) >= float(bm['r2']),
    }
    accepted = delta >= .03 and all(guardrails.values())
    def digest(path): return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
    value = {'schema_version':1, 'audit_kind':'paired_fold_guardrail',
        'execution_source':'deterministic_event_fallback_after_action_model_unavailable',
        'inputs':[{'path':str(bp),'sha256':digest(bp)},{'path':str(cp),'sha256':digest(cp)}],
        'paired_sample_count':len(bpred) if paired else 0,
        'baseline_metrics':bm, 'candidate_metrics':cm,
        'rmse_delta':delta, 'threshold':.03, 'fold_results':folds,
        'guardrails':guardrails, 'verdict':'accept' if accepted else 'reject',
        'reason':'threshold and all frozen guardrails passed' if accepted else
                 'one or more threshold/guardrail checks failed'}
    from partner.runtime.action_execution import write_json
    target=work/'final_audit.json'; write_json(target,value); return target


def _next_molecular_method(workspace: str, requested: str = "") -> tuple[str, list[str]]:
    """Choose an untried candidate arm from a small declared method portfolio.

    The append-only history is an index, so a new round never scans prior Job
    JSON.  An explicit requested arm remains authoritative when it is valid.
    """
    allowed = ("maxmin_fingerprint", "scaffold_round_robin", "scaffold_cap",
               "pareto_diverse")
    if requested:
        if requested not in allowed:
            raise ValueError(f"unknown molecular candidate method: {requested}")
        return requested, []
    history = Path(workspace) / "state" / "project_iteration" / "molecular_method_history.jsonl"
    tried: list[str] = []
    if history.is_file():
        for line in history.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                method = str(json.loads(line).get("candidate_method") or "")
            except (ValueError, TypeError):
                continue
            if method in allowed and method not in tried:
                tried.append(method)
    return next((method for method in allowed if method not in tried), allowed[-1]), tried


def _deterministic_molecular_selection_canary(workspace: str, work: Path,
                                               request: str = "") -> list[Path]:
    """Execute one novel frozen molecular-method comparison inside this Event."""
    import hashlib
    from partner.application import molecular_selection_adapter as molecular
    project = Path(workspace) / 'share' / 'projects' / 'molecular_generation'
    pool_path = project / 'datasets' / 'bootstrap' / 'molecular_synth_comparison.csv'
    rows = molecular.load_pool(project)

    def arm(method: str) -> dict[str, Any]:
        repeats = []
        for seed in molecular.SEED_PROTOCOL:
            picked = molecular.select(rows, method=method, seed=seed)
            repeats.append({'seed': seed, 'selected': [
                {'smiles': rows[i]['smiles'], 'qed': rows[i]['qed'],
                 'sa': rows[i]['sa'], 'scaffold': rows[i]['scaffold']}
                for i in picked]})
        payload = {'schema_version': 1, 'method': method,
                   'pool_size': len(rows), 'pool_hash': molecular.pool_hash(rows),
                   'select_count': molecular.SELECT_COUNT,
                   'seeds': list(molecular.SEED_PROTOCOL), 'repeats': repeats}
        payload['metrics'] = {
            'validity': molecular._validity(payload),
            'uniqueness': molecular._uniqueness(payload),
            'qed_mean': molecular._qed_mean(payload),
            'sa_mean': molecular._sa_mean(payload),
            'scaffold_count': molecular._scaffold_count(payload),
            'selected_count': molecular._selected_count(payload),
            'nn_tanimoto_mean': molecular._nn_tanimoto_mean(payload),
        }
        return payload

    work.mkdir(parents=True, exist_ok=True)
    explicit = re.search(r"candidate_method\s*=\s*([a-z_]+)", request or "", re.I)
    candidate_method, prior_methods = _next_molecular_method(
        workspace, explicit.group(1).lower() if explicit else "")
    baseline, candidate = arm(molecular.BASELINE_METHOD), arm(candidate_method)
    baseline_path, candidate_path = work/'baseline_selection.json', work/'candidate_selection.json'
    from partner.runtime.action_execution import write_json
    write_json(baseline_path, baseline)
    write_json(candidate_path, candidate)
    bm, cm = baseline['metrics'], candidate['metrics']
    guardrails = {
        'same_pool_hash': baseline['pool_hash'] == candidate['pool_hash'],
        'same_seeds': baseline['seeds'] == candidate['seeds'],
        'validity_complete': cm['validity'] == 1.0,
        'selected_count_complete': cm['selected_count'] == molecular.SELECT_COUNT,
        'candidate_more_diverse': cm['nn_tanimoto_mean'] > bm['nn_tanimoto_mean'],
        'candidate_sa_within_frozen_ceiling': cm['sa_mean'] <= 1.60,
        'candidate_qed_gain_meets_frozen_minimum': (
            cm['qed_mean'] - bm['qed_mean'] >= 0.02),
    }
    comparison = {
        'schema_version': 1,
        'protocol': 'molecular_selection_canary_v2',
        'research_question': (f'Does frozen {candidate_method} selection improve the declared '
                              'molecular objectives over the project rule baseline on the same pool?'),
        'iteration': {'candidate_method': candidate_method,
                      'previously_tested_methods': prior_methods,
                      'novel_arm': candidate_method not in prior_methods},
        'input': {'path': str(pool_path),
                  'sha256': 'sha256:' + hashlib.sha256(pool_path.read_bytes()).hexdigest()},
        'baseline': {'method': baseline['method'], 'metrics': bm},
        'candidate': {'method': candidate['method'], 'metrics': cm},
        'deltas_candidate_minus_baseline': {
            key: round(float(cm[key])-float(bm[key]), 6) for key in bm},
        'guardrails': guardrails,
        'verdict': 'accept' if all(guardrails.values()) else 'reject',
        'limitations': ('QED, SA, scaffold and fingerprint metrics are computational proxies; '
                        'they do not establish target activity, wet-lab synthesizability or clinical value.'),
    }
    comparison_path = work/'molecular_selection_comparison.json'
    write_json(comparison_path, comparison)
    history = Path(workspace) / 'state' / 'project_iteration' / 'molecular_method_history.jsonl'
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({
            'candidate_method': candidate_method,
            'verdict': comparison['verdict'],
            'comparison_path': str(comparison_path),
            'input_sha256': comparison['input']['sha256'],
        }, ensure_ascii=False) + '\n')
    return [baseline_path, candidate_path, comparison_path]


def action_execute_inline(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Execute one bounded project action through the configured agent port.

    The action is still a canonical Event: it receives a frozen intent,
    evidence contract and working directory and must return an auditable
    textual terminal.  No PlanExecutor, registry or recursive Harness is used.
    """
    selected = params.get("selected") if isinstance(params.get("selected"), dict) else {}
    if not selected:
        selected = params.get("previous_semantic") if isinstance(params.get("previous_semantic"), dict) else {}
    # Core v1 freezes a commitment immediately before execution.  Its envelope
    # is not itself an executable plan, so unwrap the frozen selected action.
    if isinstance(selected.get("decision"), dict):
        frozen = selected["decision"].get("selected")
        if isinstance(frozen, dict):
            selected = dict(frozen)
    if not selected:
        selected = params
    adapter = getattr(ctx, "adapter", None)
    if adapter is None or not hasattr(adapter, "execute_task"):
        return {"ok": False, "status": "failed", "error": "project agent port unavailable"}
    # Authorized supervision can arrive while the bounded action is running.
    # Read it between commands without restarting or replaying the action.
    root_id = str(params.get('root_event_id') or '')
    if re.fullmatch(r'evt_[a-f0-9]+', root_id):
        adapter.execution_feedback_path = str(Path(_workspace(ctx)) / 'state/application/request_context' / f'{root_id}.json')
    project_id = str(params.get("project_id") or getattr(ctx, "project_id", ""))
    runtime_work = Path(str(params.get("action_work") or
        (Path(_workspace(ctx)) / "state/event_runtime/work" /
         str(getattr(ctx, "job_id", "job")) / str(params.get("flow_id") or "action"))))
    request = str(params.get("request", ""))
    declared_root, root_error = _declared_output_root(
        request, _workspace(ctx),
        params.get("intent_contract") if isinstance(params.get("intent_contract"), dict) else None)
    work = declared_root or runtime_work
    work.mkdir(parents=True, exist_ok=True)
    runtime_work.mkdir(parents=True, exist_ok=True)
    read_only = _read_only_action(selected)
    decision = ((params.get("flow_outputs") or {}).get("core_commit") or {}).get("semantic_output") or {}
    decision = decision.get("decision") if isinstance(decision.get("decision"), dict) else {}
    frozen_hash = str(decision.get("content_hash") or "")
    if root_error:
        contract = {
            "schema_version": 1,
            "decision_id": str(decision.get("decision_id") or ""),
            "frozen_content_hash": frozen_hash,
            "frozen_event_type": str(selected.get("event_type") or ""),
            "read_only": read_only,
            "required_output_root": "",
            "runtime_work_root": str(runtime_work.resolve()),
            "actual_work_root": "",
            "changed_paths": [],
            "execution_started": False,
            "conformant": False,
            "violations": [root_error],
        }
        from partner.runtime.action_execution import write_json
        contract_path = runtime_work / "execution_contract.json"
        write_json(contract_path, contract)
        return {"ok": False, "status": "failed", "error": root_error,
                "business_delta": False, "executed_capability": "project.agent_action",
                "files": [str(contract_path)], "evidence_refs": [str(contract_path)],
                "semantic_output": {"business_delta": False,
                    "execution_contract": contract,
                    "execution_contract_path": str(contract_path),
                    "artifact_count": 0, "artifact_checks": [],
                    "command_receipts": [], "verified_artifacts": []}}
    before = _artifact_snapshot(work)
    constraints = ((params.get('intent_contract') or {}).get('execution_constraints') or {})
    benchmark_inputs = constraints.get('benchmark_inputs') or {}
    round_number = int((params.get('intent_contract') or {}).get('round_number') or 1)
    if constraints.get('benchmark_embedded') and benchmark_inputs and round_number == 1:
        # Research-boundary rounds around a frozen benchmark do not need an
        # open-ended coding agent.  Emit a typed, reproducible state artifact
        # from the declared dataset/feature/protocol; the benchmark child still
        # performs the actual baseline/candidate computation independently.
        import csv
        import hashlib
        dataset = Path(str(benchmark_inputs.get('dataset_path') or ''))
        runner = Path(str(benchmark_inputs.get('arm_runner_path') or ''))
        if dataset.is_file() and runner.is_file():
            with dataset.open('r', encoding='utf-8', errors='replace', newline='') as handle:
                reader = csv.reader(handle)
                columns = next(reader, [])
                row_count = sum(1 for _ in reader)
            handoff_refs = ((((params.get('flow_outputs') or {}).get('design') or {})
                             .get('semantic_output') or {}).get('learning_handoff_refs') or [])
            state = {
                'schema_version': 1,
                'kind': 'benchmark_research_state',
                'round_number': round_number,
                'research_question': str(params.get('request') or '')[:4000],
                'protocol_id': constraints.get('benchmark_protocol_id'),
                'declared_feature': benchmark_inputs.get('declared_feature'),
                'dataset': {'path': str(dataset.resolve()), 'rows': row_count,
                            'columns': columns,
                            'sha256': hashlib.sha256(dataset.read_bytes()).hexdigest()},
                'arm_runner': {'path': str(runner.resolve()),
                               'sha256': hashlib.sha256(runner.read_bytes()).hexdigest()},
                'guardrails': constraints.get('benchmark_guardrail_results') or {},
                'learning_handoff_refs': handoff_refs,
                'learning_consumed': bool(round_number > 1 and handoff_refs),
                'decision_effect': ('freeze data/split/feature boundary before execution'
                                    if round_number == 1 else
                                    'apply the cited handoff to leakage review and interpretation'),
                'claim_boundary': ('This artifact freezes inputs and risks; it does not claim '
                                   'a model improvement. Only benchmark Settlement may do so.'),
            }
            from partner.runtime.action_execution import write_json
            typed_path = work / f'benchmark_research_state_round_{round_number}.json'
            write_json(typed_path, state)
            typed_reply = (
                f'【业务产物】{typed_path} [bytes={typed_path.stat().st_size}]\n'
                f'【执行动作】基于冻结数据、特征、评价器与护栏生成第{round_number}轮研究状态。\n'
                '【真实发现】已核验输入哈希、行数、字段和学习交接引用；尚未提前声称指标改善。\n'
                '【未解决】实际效应由后续 baseline/candidate 与 Settlement 裁决。')
        else:
            typed_reply = ''
    else:
        typed_reply = ''
    prompt = (
        "你正在执行 Partner 的一个有界项目 Event，不是在写计划或报告。\n"
        f"项目={project_id}\n用户目标={request}\n"
        f"已选动作={json.dumps(selected, ensure_ascii=False)}\n"
        # The action model needs the frozen decision and direct evidence refs,
        # not a replay of the whole memory/Flow history.  Keeping this bounded
        # prevents long-context provider timeouts before the first command.
        f"已核实上下文={event_facts(params, max_chars=8000)}\n"
        f"工作目录={work}\n"
        f"运行时审计目录={runtime_work}\n"
        f"运行时 Event ID={params.get('event_id', '')}；Job ID={getattr(ctx, 'job_id', '')}。需要记录溯源编号时只使用这些真实编号，不自行发明。\n"
        "只能执行冻结的已选动作；不得把核验扩展为训练、修改、生成或其他未冻结动作。"
        "不得写占位文件或人为编造指标来满足产物要求。数值必须来自真实输入和实际运行。"
        "若实在无法产出真文件，必须明确说明失败原因 + 缺失的输入/工具。"
        "用户明确指定输出目录时必须使用该目录；否则使用上方运行时工作目录。不得修改 Partner 运行框架、控制策略或凭据。"
        "跨轮对照实验中，如果上游数据已有 split/fold/partition 标签或样本ID，必须逐样本复用这些冻结分组；"
        "重新调用随机划分函数，即使 seed 和比例相同，也不能声称是同一 split。任何输入哈希不匹配都必须先修正或明确失败，不能以人工判断绕过。"
        "结束时必须输出最后一段：\n"
        "【业务产物】<绝对路径1> [bytes=N]\n"
        "【执行动作】<一句话>\n"
        "【真实发现】<一句话>\n"
        "【未解决】<一句话>"
    )
    inspected = (((params.get('flow_outputs') or {}).get('inspect') or {})
                 .get('semantic_output') or {})
    if inspected.get('project_root'):
        prompt += ('\n已定位项目根目录=' + str(inspected['project_root'])
                   + '\n项目根目录一级索引=' + json.dumps(
                       inspected.get('project_root_index') or [], ensure_ascii=False)
                   + '。先使用这个有界目录和已验真索引，禁止扫描整个 partner_workspace。')
    if read_only:
        prompt += ("\n这是机器冻结的只读动作。只能读取、列举、计算哈希或验证已有内容；"
                   "禁止训练模型、修改文件或生成新的业务产物。没有新文件不是失败，缺失输入时应直接报告失败。")
    else:
        prompt += ("\n这是机器冻结的可写执行动作。必须产生与 selected 明确一致的业务证据；"
                   "只跑 ls/find/cat/head/tail 且没有新业务证据等同于失败。")
    if _fresh_first_round(params):
        prompt += (f'\n这是显式新鲜首轮协议。不得读取或复用其他 Job 的运行产物；任何包含 '
                   f'/state/event_runtime/work/job_ 且 Job ID 不是 {getattr(ctx, "job_id", "")} 的路径都不是本轮输入。'
                   '本轮只能使用用户显式输入和当前 Event 新生成的数据，且不得提前执行后一轮候选。')
    index=((params.get('flow_outputs') or {}).get('inspect',{}).get('semantic_output') or {}).get('verified_artifact_index')
    if index:
        recent=[]
        for row in list(index.get('recent') or [])[:6]:
            if isinstance(row,dict):
                recent.append({key:row.get(key) for key in
                               ('path','sha256','job_id','flow_id','relation','role')})
        compact_index={'path':index.get('path'),'count':index.get('count'),'recent':recent,
                       'historical_execution_inputs_suppressed':index.get('historical_execution_inputs_suppressed')}
        prompt += '\n已验真历史产物索引摘要='+json.dumps(compact_index,ensure_ascii=False)+'。优先按此索引找到所需实验；工作根目录同名旧文件不能替代实际运行来源。此索引由运行器核验生成，只读使用，禁止自行改写索引或完成/验收记录；只提交实际业务文件给后续核验Event。'
    prompt += ('\n你已经处于project.action_execute Event内。selected_event是上层动作意图，不能将它当作Python函数或命令。'
        '禁止在本动作内调用其他Event，也不需要查找Event分发入口、list_registered_events或CLI。'
        '直接使用真实Python库、shell工具及下方给出的底层只读浏览器接口完成已选业务动作。')
    prompt += ('\n当前行动本身已经由 Partner 统一 Model Gateway 中配置的模型执行。若任务需要语义判断，'
               '直接在本次模型响应中完成并把依据写入业务产物；禁止生成脚本自行查找 OPENAI_API_KEY、'
               'ANTHROPIC_API_KEY 或猜测模型供应商，也禁止绕过 config/api.json 新建第二套模型配置。'
               '确需后续独立模型裁决时，生成类型化 model_request.json 交给后续 LLM Event，不能把“未发现其他厂商环境变量”'
               '报告成 Partner 没有可用模型。')
    prompt += ('\n浏览器只读能力可在本Event内调用底层工具：from partner.social_video.integration import ensure_edge; '
        f"browser=ensure_edge({str(_workspace(ctx))!r},'video'); result=browser('read_page',{{'url':实际发现的HTTPS地址}})。"
        '返回正文、链接、真实截图和阻塞状态，保存原始JSON，不把搜索页当完整文章。'
        "read_page的status是'read'或'blocked'，不是HTTP数字状态码；它不保证duration字段，时长与实际播放由视频子Flow核验。不要用自己假定的status==200或duration阈值否定接口返回。"
        "读取小红书时将ensure_edge的purpose改为'xhs'，复用其独立已有登录态；视频继续用'video'，不混用浏览器会话。"
        '若要学习一个已发现的视频，保存next_flow_request.json：'
        '{"flow":"browser_video_learning","url":"实际发现URL","source_evidence":"本工作目录内包含该URL的真实抓取JSON绝对路径"}。'
        '随后结束本动作并说明已提出视频子流程请求；运行时会验证并执行正式视频Event链，禁止自己调用其他Event或伪造request_id。'
        '没有URL时先真实获取页面链接或搜索结果，一轮只完成一项内容发现与读取，不反复阅读工具源码。')
    attachment_refs = [str(row.get('path') if isinstance(row, dict) else row)
                       for row in params.get('attachments') or []]
    attached_context_refs = [str(row.get('input') or '') for row in
        ((((params.get('flow_outputs') or {}).get('inspect') or {}).get('semantic_output') or {})
         .get('local_input_context') or []) if isinstance(row, dict)]
    eligible_rows = ((((params.get('flow_outputs') or {}).get('input_eligibility') or {})
                     .get('semantic_output') or {}).get('eligible_inputs') or [])
    eligible_refs = [str(row.get('path') or '') for row in eligible_rows
                     if isinstance(row, dict) and row.get('path')]
    explicit_refs = [str(path) for path in (
        list(params.get('evidence_refs') or [])
        + list((params.get('intent_contract') or {}).get('evidence_refs') or [])
        + attachment_refs + attached_context_refs + eligible_refs) if str(path)]
    if eligible_refs:
        prompt += ('\n语料准入Event已冻结以下研究输入，当前实验必须直接消费这些路径，不得回退到项目目录中'
                   '未经准入的同名文件、日志或临时产物：' +
                   json.dumps(eligible_refs[:30], ensure_ascii=False) +
                   '。结果产物必须记录实际消费的输入路径与SHA256，并在工作目录写入'
                   'input_consumption.json（字段 consumed_inputs，每项含path,sha256,purpose）；'
                   '若未消费这些合格输入，后续Verify必须拒绝科学主张。')
    prompt += ('\n运行器允许的跨 Job 证据路径=' + json.dumps(
        list(dict.fromkeys(explicit_refs))[:40], ensure_ascii=False)
        + '。未列出的其他 Job 目录禁止读取；历史索引只用于发现，不能直接作为本轮证据。')
    typed_audit = (_deterministic_paired_audit(explicit_refs, work)
                   if re.search(r'final_audit|异质性.{0,12}审计|paired.{0,12}audit', request, re.I)
                   else None)
    molecular_outputs = (_deterministic_molecular_selection_canary(_workspace(ctx), work, request)
                         if project_id == 'molecular_generation'
                         and 'molecular_selection_canary_v1' in request else [])
    if typed_reply:
        pass
    elif molecular_outputs:
        typed_reply = ('\n'.join(
            f"【业务产物】{path} [bytes={path.stat().st_size}]" for path in molecular_outputs)
            + "\n【执行动作】Event 在冻结数据、方法、seed 和评价器下执行分子选择 baseline/candidate。\n"
              "【真实发现】配对比较已经写入机器可读产物，结论交由后续 Verify 与 Settlement。\n"
              "【未解决】这些指标仅是计算代理，不能证明靶点活性或湿实验可合成性。")
    else:
        typed_reply = (f"【业务产物】{typed_audit} [bytes={typed_audit.stat().st_size}]\n"
                       "【执行动作】Event 执行冻结的类型化 paired/fold/guardrail 审计。\n"
                       "【真实发现】机器审计产物已生成，结论交由后续 Verify 与 Settlement。\n"
                       "【未解决】无。" if typed_audit else "")
    try:
        reply = typed_reply or str(adapter.execute_task(prompt, **(
            {"seconds": ctx.action_seconds} if hasattr(ctx, "action_seconds") else {})) or "").strip()
    except (RuntimeError, TimeoutError) as exc:
        if re.search(r'final_audit|异质性.{0,12}审计|paired.{0,12}audit', request, re.I):
            audit = _deterministic_paired_audit(explicit_refs, work)
            if audit:
                reply = (f"【业务产物】{audit} [bytes={audit.stat().st_size}]\n"
                         "【执行动作】动作模型不可用；Event 执行冻结的类型化 paired/fold/guardrail 审计。\n"
                         "【真实发现】机器审计产物已生成，结论交由后续 Verify 与 Settlement。\n"
                         "【未解决】模型没有返回终态叙述。")
            else:
                raise
        else:
            audit = None
        if audit:
            recovered_checks = []
        else:
        # The bounded action runner can exhaust its final model turn after its
        # commands have already produced parseable business data.  Recover the
        # execution terminal from those machine receipts; scientific success
        # still belongs to outcome_verify/Settlement.  Other failures remain
        # failures and retain their original exception.
            from partner.runtime.artifact_checks import inspect_artifacts
            from partner.runtime.action_execution import _business_data_checks
            recovered_checks = inspect_artifacts(work)
            receipts = list((runtime_work / '.execution').glob('command_*.json'))
            successful_receipts = []
            for receipt in receipts:
                try:
                    row = json.loads(receipt.read_text())
                except (OSError, ValueError, TypeError):
                    continue
                if (row.get('executed') and not row.get('timed_out')
                        and row.get('exit_code') == 0):
                    successful_receipts.append(receipt)
            valid = _business_data_checks(recovered_checks)
            if not successful_receipts or not valid or not re.search(
                    r'budget exhausted|deadline exhausted', str(exc), re.I):
                raise
            reply = ('\n'.join(f"【业务产物】{row['path']} [bytes={row.get('bytes', 0)}]"
                               for row in valid[:20])
                     + '\n【执行动作】模型终端预算耗尽；Event 根据成功命令回执和可解析业务产物恢复执行终态。'
                     + '\n【真实发现】仅确认本 Event 产生了真实数据，目标结论由后续核验与 Settlement 裁决。'
                     + '\n【未解决】模型未返回最终叙述。')
    reply = re.sub(r"<(think|analysis)>.*?</\1>", "", reply,
                   flags=re.IGNORECASE | re.DOTALL).strip()
    if re.search(r"</?(think|analysis)>", reply, re.IGNORECASE):
        return {"ok": False, "status": "failed", "error": "project agent reasoning block was not closed",
                "failure_class": "model", "mechanism": "project_agent/unclosed_reasoning"}
    if not reply or reply.startswith("Task recorded:"):
        return {"ok": False, "status": "failed", "error": "project agent returned no executed result",
                "failure_class": "environment", "mechanism": "project_agent/no_execution"}
    after = _artifact_snapshot(work)
    changed = sorted(path for path, fingerprint in after.items()
                     if before.get(path) != fingerprint)
    violations = []
    if root_error:
        violations.append(root_error)
    if read_only and changed:
        violations.append("read-only frozen action modified or created business artifacts")
    if declared_root and any(not Path(path).is_relative_to(declared_root) for path in changed):
        violations.append("artifact written outside declared output root")
    contract = {
        "schema_version": 1,
        "decision_id": str(decision.get("decision_id") or ""),
        "frozen_content_hash": frozen_hash,
        "frozen_event_type": str(selected.get("event_type") or ""),
        "read_only": read_only,
        "required_output_root": str(declared_root) if declared_root else "",
        "runtime_work_root": str(runtime_work.resolve()),
        "actual_work_root": str(work.resolve()),
        "changed_paths": changed,
        "execution_started": True,
        "conformant": not violations,
        "violations": violations,
    }
    from partner.runtime.action_execution import write_json
    contract_path = runtime_work / "execution_contract.json"
    write_json(contract_path, contract)
    from partner.runtime.artifact_checks import owned_files
    files = [str(path) for path in owned_files(work)]
    # The execution narrative is runtime audit evidence, not a project mutation.
    # Keep it out of an explicitly declared project directory, especially for
    # read-only commitments.
    evidence_path = runtime_work / "执行结果.md"
    evidence_path.write_text(reply + "\n", encoding="utf-8")
    if str(evidence_path) not in files:
        files.append(str(evidence_path))
    if str(contract_path) not in files:
        files.append(str(contract_path))
    # 业务产物判定：抓 reply 中"【业务产物】<绝对路径>"声明，逐个 stat 校验。
    # deepseek-v4-pro 常幻觉"声明产出"但实际没跑命令，必须以文件系统为准。
    declared = []
    for line in reply.splitlines():
        if "业务产物" in line and "bytes=" in line:
            m = re.search(r"(/\S+\.(?:json|txt|csv|pdb|py|sh|md|sdf|mol|npy|nii|png|svg))", line)
            if m:
                declared.append(m.group(1))
    # Declarations are not evidence. Only this task's generated data and
    # recorded successful commands can establish an execution delta.
    from partner.runtime.artifact_checks import inspect_artifacts
    checks = inspect_artifacts(work)
    from partner.runtime.domain_handoff import read_request
    handoff=read_request(work)
    receipts = list((runtime_work / '.execution').glob('command_*.json'))
    executed = bool(typed_reply) or any(json.loads(p.read_text()).get('exit_code') == 0 for p in receipts)
    def is_domain_artifact(row):
        """Exclude runtime/audit paperwork from project progress evidence."""
        path = Path(str(row.get('path') or ''))
        lowered = path.name.lower()
        if not row.get('valid') or '.execution' in path.parts:
            return False
        if lowered in {'execution_contract.json', '执行结果.md', 'checkpoint.json'}:
            return False
        if any(token in lowered for token in (
                'event_log', 'run_log', 'flow_graph', 'receipt', 'ack_wait')):
            return False
        return True
    domain_checks = [c for c in checks if is_domain_artifact(c)]
    artifact_files = [c['path'] for c in domain_checks]
    from partner.index.resource_catalog import ResourceCatalog
    catalog=ResourceCatalog(_workspace(ctx))
    for artifact in files:
        catalog.register(artifact,'artifact',str(params.get('job_id') or getattr(ctx,'job_id','') or ''),
                         {'flow_id':params.get('flow_id'),'project_id':params.get('project_id')})
    business_delta = bool(executed and artifact_files and contract["conformant"] and not read_only)

    ok = bool(contract["conformant"])
    
    # v2: 添加 goal_coverage 和 artifact_claims
    design = ((params.get('flow_outputs') or {}).get('design') or {}).get('semantic_output') or {}
    round_goal = design.get('round_goal', '')
    
    goal_coverage = []
    artifact_claims = []
    
    for file_path in files:
        file_type = 'unknown'
        if file_path.endswith('.json'):
            file_type = 'json'
        elif file_path.endswith('.md'):
            file_type = 'markdown'
        elif file_path.endswith('.py'):
            file_type = 'python'
        
        coverage_item = {
            'artifact': file_path,
            'goal_aspect': '未明确绑定',
            'coverage_status': 'partial'
        }
        goal_coverage.append(coverage_item)
        
        claim = {
            'artifact': file_path,
            'claim': f'产出了 {file_type} 类型的文件',
            'evidence_requirement': '需要验证文件内容是否符合 round_goal',
            'verification_status': 'pending'
        }
        artifact_claims.append(claim)
    
    return {"ok": ok, "status": "completed" if ok else "failed",
            "error": "; ".join(violations), "summary": reply[:500],"requested_child_flow":handoff,
            "outcome": reply[:4000], "files": files, "evidence_refs": files,
            "business_delta": business_delta, "executed_capability": "project.agent_action",
            "semantic_output": {"result": reply, "files": files,
                                "business_delta": business_delta,
                                "execution_contract": contract,
                                "execution_contract_path": str(contract_path),
                                "artifact_count": len(artifact_files),
                                "artifact_checks":domain_checks, "all_artifact_checks":checks,
                                "command_receipts":[str(p) for p in receipts],
                                "declared_artifacts": declared,
                                "verified_artifacts": artifact_files,
                                "fabrication_detected": any(not Path(p).is_file() for p in declared),
                                "goal_coverage": goal_coverage,
                                "artifact_claims": artifact_claims}}


def action_execute(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from partner.runtime.background_actions import BackgroundActions, TERMINAL
    # Fixture adapters remain synchronous for deterministic unit tests. All
    # production DirectAdapter actions use the durable process boundary.
    from partner.adapters.adapter import DirectAdapter
    if not isinstance(ctx.adapter, DirectAdapter):
        return action_execute_inline(ctx, params)
    work = Path(_workspace(ctx)) / "state/event_runtime/work" / ctx.job_id / params["flow_id"]
    manager = BackgroundActions(_workspace(ctx))
    try:
        runtime = json.loads((Path(_workspace(ctx))/'config/partner_config.json').read_text()).get('runtime') or {}
        seconds = max(60, min(86400, int(runtime.get('background_action_seconds', 900))))
    except (OSError, ValueError, TypeError):
        seconds = 900
    seconds = int(((params.get('intent_contract') or {}).get('execution_constraints') or {}).get('action_seconds') or seconds)
    row = manager.submit(f"{ctx.job_id}:{params['flow_id']}:{params['node_id']}",
        {k: str(getattr(ctx, k, "")) for k in ("workspace", "job_id", "project_id", "instance_id")},
        {**params, "action_work":str(work)}, seconds=seconds)
    if row['status'] not in TERMINAL:
        return {"ok":False, "status":"waiting", "background_task_id":row['task_id'],
                "summary":"已提交后台动作，等待真实执行回执"}
    result = manager.directory / row['task_id'] / 'result.json'
    if row['status'] in {'completed','failed'} and result.exists():
        output = json.loads(result.read_text())
        if not output.get('ok'):
            checkpoint = work / '.execution/checkpoint.json'
            output['evidence_refs'] = list(dict.fromkeys((output.get('evidence_refs') or []) + [str(result), str(checkpoint)]))
            semantic = output.setdefault('semantic_output', {})
            semantic.update(execution_status='failed', working_dir=str(work),
                            error=output.get('error', ''), checkpoint=str(checkpoint))
        return output
    return {"ok":False, "status":"failed", "error":row.get('error') or 'background action failed',
            "evidence_refs":[str(manager.directory / row["task_id"] / "task.json")] + ([str(result)] if result.exists() else [])}


def input_consumption_verify(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Prove that the action consumed admitted inputs rather than nearby noise."""
    import hashlib
    from partner.runtime.action_execution import write_json
    eligible = ((((params.get('flow_outputs') or {}).get('input_eligibility') or {})
                 .get('semantic_output') or {}).get('eligible_inputs') or [])
    frozen = {str(row.get('path')): str(row.get('sha256') or '') for row in eligible
              if isinstance(row, dict) and row.get('path')}
    work_value = str(getattr(ctx, 'working_dir', '') or '').strip()
    work = (Path(work_value) if work_value else
            Path(_workspace(ctx)) / 'state/event_runtime/work' /
            str(getattr(ctx, 'job_id', 'project')))
    # The executor's working dir is <job>/<flow_id>/ (see action_execute), so
    # the declaration may land under the flow subdirectory while the audit
    # resolves <job>/ itself.  Probe the known layouts in order, then fall
    # back to the first declaration found under the job dir.
    declared_path = work / 'input_consumption.json'
    if not declared_path.is_file():
        flow_id = str(params.get('flow_id') or '').strip()
        if flow_id:
            candidate = work / flow_id / 'input_consumption.json'
            if candidate.is_file():
                declared_path = candidate
    if not declared_path.is_file():
        for candidate in sorted(work.glob('**/input_consumption.json')):
            declared_path = candidate
            break
    declared = {}
    if declared_path.is_file():
        try:
            declared = json.loads(declared_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            declared = {}
    rows = declared.get('consumed_inputs') if isinstance(declared, dict) else []
    if not isinstance(rows, list):
        rows = []
    verified, rejected = [], []
    hash_index = {str(h): p for p, h in frozen.items() if h}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_path = str(row.get('path') or '')
        path = Path(raw_path)
        declared_hash = str(row.get('sha256') or '')
        expected = frozen.get(raw_path)
        if not expected or not path.is_file():
            # The executor may declare the consumed path using a different
            # copy/location of the same admitted content (e.g. a re-located
            # transfer file whose body still names the original job path).
            # Accept it when the declared content hash matches a frozen
            # admission exactly; the admission contract is about the content,
            # not the path spelling.
            if declared_hash and declared_hash in hash_index:
                try:
                    actual = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    actual = ''
                if actual == declared_hash:
                    verified.append({
                        'path': raw_path,
                        'normalized_to': hash_index[declared_hash],
                        'sha256': actual,
                        'purpose': str(row.get('purpose'))[:500],
                        'content_match': True,
                    })
                    continue
            rejected.append({'path': raw_path, 'reason': 'not an admitted current-round input'})
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected or declared_hash != expected:
            rejected.append({'path': raw_path, 'reason': 'hash does not match frozen admission'})
            continue
        if not str(row.get('purpose') or '').strip():
            rejected.append({'path': raw_path, 'reason': 'missing consumption purpose'})
            continue
        verified.append({'path': raw_path, 'sha256': actual,
                         'purpose': str(row.get('purpose'))[:500]})
    request = str((params.get('intent_contract') or {}).get('original_request') or
                  params.get('request') or '')
    corpus_required = bool(re.search(
        r'论文|文献|内容|来源|corpus|paper|literature|source', request, re.I))
    valid = bool(verified) if corpus_required else (bool(verified) or not frozen)
    value = {
        'consumption_valid': valid,
        'corpus_required': corpus_required,
        'eligible_count': len(frozen),
        'verified_consumed_inputs': verified,
        'rejected_consumption_claims': rejected,
        'receipt_path': str(declared_path),
        'rule': 'an admitted path, matching frozen hash and declared purpose is required for corpus claims',
    }
    audit_path = work / 'input_consumption_audit.json'
    write_json(audit_path, value)
    return {
        'ok': True, 'status': 'completed', 'semantic_output': value,
        'evidence_refs': [str(audit_path)] + ([str(declared_path)] if declared_path.is_file() else []),
        'summary': ('已验证实际消费的准入输入' if valid else
                    '执行未证明消费任何准入输入，科学主张将被拒绝'),
    }


def outcome_verify(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    from partner.runtime.artifact_checks import check_file
    sem = prior.get('semantic_output') or {}
    evidence = [check_file(Path(row['path'])) for row in sem.get('artifact_checks', []) if row.get('valid')]
    expected = {row['path']:row.get('sha256') for row in sem.get('artifact_checks', [])}
    previous_hashes = set((params.get('intent_contract') or {}).get('previous_artifact_hashes') or [])
    import hashlib
    for attachment in params.get('attachments') or []:
        path = Path(str(attachment.get('path') if isinstance(attachment, dict) else attachment))
        if path.is_file():
            with path.open('rb') as handle:
                previous_hashes.add(hashlib.file_digest(handle, 'sha256').hexdigest())

    novel = any(row.get('sha256') not in previous_hashes for row in evidence)
    foreign_job_refs = []
    if _fresh_first_round(params):
        current = str(getattr(ctx, 'job_id', ''))
        for row in evidence:
            path = Path(row['path'])
            if path.suffix.lower() not in ('.json', '.jsonl', '.md', '.py', '.txt'):
                continue
            try:
                text = path.read_text(errors='replace')[:200000]
            except OSError:
                continue
            foreign_job_refs.extend(ref for ref in re.findall(
                r'/state/event_runtime/work/(job_[A-Za-z0-9]+)', text) if ref != current)
    contract = sem.get("execution_contract") if isinstance(sem.get("execution_contract"), dict) else {}
    contract_required = prior.get("executed_capability") == "project.agent_action"
    contract_ok = bool(contract.get("conformant")) if contract_required else True
    required_root = Path(str(contract.get("required_output_root"))).resolve() if contract.get("required_output_root") else None
    root_ok = (not required_root or all(Path(row["path"]).resolve().is_relative_to(required_root)
                                       for row in evidence))
    scientific_contract_violations = _matched_split_violations(params, evidence)
    execution_verified = bool(prior.get('business_delta') and evidence and novel and contract_ok and root_ok
                    and not foreign_job_refs and not scientific_contract_violations and all(
        row['valid'] and row['sha256'] == expected.get(row['path']) for row in evidence))
    contract_params = params.get('intent_contract') if isinstance(params.get('intent_contract'), dict) else {}
    eligibility = contract_params.get('input_eligibility') if isinstance(
        contract_params.get('input_eligibility'), dict) else {}
    if not eligibility:
        eligibility = ((((params.get('flow_outputs') or {}).get('input_eligibility') or {})
                        .get('semantic_output')) or {})
    corpus_required = bool(re.search(
        r'论文|文献|内容|来源|corpus|paper|literature|source',
        str(contract_params.get('original_request') or params.get('request') or ''), re.I))
    consumption = ((((params.get('flow_outputs') or {}).get('input_consumption') or {})
                    .get('semantic_output')) or {})
    corpus_eligible = ((eligibility.get('corpus_ready') is True
                        and consumption.get('consumption_valid') is True)
                       if corpus_required else True)
    adequacy = ((((params.get('flow_outputs') or {}).get('input_adequacy') or {})
                .get('semantic_output')) or {})
    metric_protocol_ready = adequacy.get('adequate_for_declared_metrics') is not False
    comparison_required = bool(contract_params.get('comparison_required'))
    if not comparison_required:
        comparison_required = bool(((((params.get('flow_outputs') or {}).get('design') or {})
                                     .get('semantic_output')) or {}).get('comparison_required'))
    matched = _learning_matched_evidence(params, evidence)
    comparison_complete = bool(matched) or not comparison_required
    scientific_claim_supported = bool(execution_verified and corpus_eligible
                                      and metric_protocol_ready and comparison_complete)
    from partner.runtime.implementation_evidence import inspect_implementations
    implementations = inspect_implementations(prior.get("files") or [])
    learning_matched = matched
    layers = {
        'execution_verified': execution_verified,
        'artifact_verified': bool(evidence and all(row.get('valid') for row in evidence)),
        'input_eligible': corpus_eligible,
        'metric_protocol_ready': metric_protocol_ready,
        'comparison_complete': comparison_complete,
        'scientific_claim_supported': scientific_claim_supported,
    }
    
    # v2: 添加 verdict_report
    fail_layer = None
    missing_evidence = []
    next_round_fix = []
    
    if not execution_verified:
        if not layers.get('execution_verified'):
            fail_layer = 'execution'
            missing_evidence.append('执行未通过验证')
            next_round_fix.append('确保执行过程符合合约要求')
        elif not layers.get('artifact_verified'):
            fail_layer = 'artifact'
            missing_evidence.append('产物验证失败')
            next_round_fix.append('检查产物完整性和哈希值')
        elif not layers.get('input_eligible'):
            fail_layer = 'input_eligibility'
            missing_evidence.append('输入资格不符')
            next_round_fix.append('重新筛选符合资格的输入')
        elif not layers.get('metric_protocol_ready'):
            fail_layer = 'metric_protocol'
            missing_evidence.append('指标协议未就绪')
            next_round_fix.append('完善指标定义和计算方法')
        elif not layers.get('comparison_complete'):
            fail_layer = 'comparison'
            missing_evidence.append('比较未完成')
            next_round_fix.append('完成 baseline 和 candidate 的比较')
        elif not layers.get('scientific_claim_supported'):
            fail_layer = 'scientific_claim'
            missing_evidence.append('科学主张未获支持')
            next_round_fix.append('提供更充分的证据支持主张')
    
    progress_note = '本轮无显著进展'
    if evidence:
        progress_note = f'本轮验证了 {len(evidence)} 个产物'
    
    verdict_report = {
        'fail_layer': fail_layer,
        'missing_evidence': missing_evidence,
        'next_round_fix': next_round_fix,
        'progress_note': progress_note,
        'progress_since_last_round': len(evidence)
    }
    
    return {"ok": True, "status": "completed", "business_delta":execution_verified,
            "semantic_output":{"verified":execution_verified, "scientific_claim_supported": scientific_claim_supported,
                "verification_layers": layers, "evidence":evidence, "implementation_evidence":implementations,
                "execution":sem, "new_content":novel,
                "execution_contract": contract, "execution_contract_ok": contract_ok,
                "input_consumption": consumption,
                "input_adequacy": adequacy,
                "required_output_root_ok": root_ok,
                "provenance_violations": sorted(set(foreign_job_refs)),
                "scientific_contract_violations": scientific_contract_violations,
                "learning_matched_evidence": learning_matched,
                "limitation":"执行与产物核验不等于输入合格、比较完成或科学主张成立", "verdict_report": verdict_report},
            "evidence_refs":[r['path'] for r in evidence if r['valid']],
            "summary":("科学主张已通过分层核验" if scientific_claim_supported else
                       "执行产物已核验，但输入资格、匹配比较或科学主张仍未通过")
                      if execution_verified else "未获得可验证的业务数据，不计为推进"}


def _learning_matched_evidence(params: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Project comparison projection used by the later learning settlement.

    The learning handoff has to be physically available to the round and the
    comparison must be a verified JSON artifact.  This Event only projects
    measured values; it does not let an LLM award improvement.
    """
    contract = params.get('intent_contract') if isinstance(params.get('intent_contract'), dict) else {}
    handoff_path = Path(str(contract.get('learning_handoff_path') or ''))
    if not handoff_path.is_file():
        return {}
    job_id = str(getattr(ctx, 'job_id', '') or '')
    if job_id and 'job_' in str(handoff_path) and job_id not in str(handoff_path):
        return {}
    for row in evidence:
        path = Path(str(row.get('path') or ''))
        if not row.get('valid') or path.suffix.lower() != '.json' or not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        baseline = value.get('baseline_test_rmse', value.get('baseline_rmse'))
        candidate = value.get('candidate_test_rmse', value.get('candidate_rmse'))
        if candidate is None and baseline is not None and 'candidate' in path.name.lower():
            candidate = value.get('test_rmse')
        if baseline is None and isinstance(value.get('baseline'), dict):
            baseline = value['baseline'].get('test_rmse', value['baseline'].get('rmse'))
        if candidate is None and isinstance(value.get('candidate'), dict):
            candidate = value['candidate'].get('test_rmse', value['candidate'].get('rmse'))
        if baseline is None or candidate is None:
            continue
        try:
            baseline_f, candidate_f = float(baseline), float(candidate)
        except (TypeError, ValueError):
            continue
        improved = bool(value.get('rmse_improved', candidate_f < baseline_f))
        return {'baseline': baseline_f, 'candidate': candidate_f,
                'effect': baseline_f - candidate_f, 'improved': improved,
                'metric': 'test_rmse', 'comparison_ref': str(path),
                'learning_handoff_ref': str(handoff_path)}
    return {}


def _matched_split_violations(params: dict[str, Any], evidence: list[dict[str, Any]]) -> list[str]:
    """Fail closed when a later matched-comparison round changes explicit rows.

    This is deliberately narrow: it activates only for round > 1 whose frozen
    blueprint requires the same/frozen split, and only when the source CSV has
    an explicit split/fold/partition column.  In that case a candidate
    prediction table must identify exactly the frozen test rows.  Replaying a
    random splitter with the same seed is not equivalent evidence.
    """
    import csv
    from collections import Counter

    contract = params.get("intent_contract") if isinstance(params.get("intent_contract"), dict) else {}
    blueprint = contract.get("round_blueprint") if isinstance(contract.get("round_blueprint"), dict) else {}
    try:
        round_number = int(blueprint.get("round_number") or 0)
    except (TypeError, ValueError):
        round_number = 0
    frozen_text = json.dumps(blueprint, ensure_ascii=False).lower()
    requires_match = bool(re.search(
        r"(?:相同|同一|冻结|复用).{0,20}(?:split|fold|分组|划分)|(?:same|frozen|reuse).{0,20}(?:split|fold)",
        frozen_text, re.I))
    if round_number <= 1 or not requires_match:
        return []
    paths = [Path(str(row.get("path") or "")) for row in evidence if row.get("valid")]
    csv_paths = [p for p in paths if p.suffix.lower() == ".csv" and p.is_file()]
    split_source = None
    source_rows: list[dict[str, str]] = []
    split_key = ""
    for path in csv_paths:
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
        if not rows:
            continue
        key = next((name for name in rows[0] if name.lower() in
                    {"split", "fold", "partition", "subset", "set"}), "")
        if key and any(str(row.get(key, "")).lower() in {"test", "holdout"} for row in rows):
            split_source, source_rows, split_key = path, rows, key
            break
    if split_source is None:
        return []
    predictions = [p for p in csv_paths if p != split_source and re.search(
        r"candidate|prediction|predictions|预测", p.name, re.I)]
    if not predictions:
        return ["frozen split has explicit labels but no candidate prediction rows identify the evaluated samples"]
    expected_rows = [row for row in source_rows
                     if str(row.get(split_key, "")).lower() in {"test", "holdout"}]
    for prediction in predictions:
        try:
            with prediction.open(newline="", encoding="utf-8-sig") as handle:
                actual_rows = list(csv.DictReader(handle))
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
        if not actual_rows:
            continue
        actual_split_key = next(
            (
                name
                for name in actual_rows[0]
                if name.lower() in {"split", "fold", "partition", "subset", "set"}
            ),
            "",
        )
        if actual_split_key and any(
            str(row.get(actual_split_key, "")).strip().lower() in {"test", "holdout"}
            for row in actual_rows
        ):
            actual_rows = [
                row
                for row in actual_rows
                if str(row.get(actual_split_key, "")).strip().lower() in {"test", "holdout"}
            ]

        source_fields = set(expected_rows[0]) - {split_key}
        actual_fields = set(actual_rows[0]) - ({actual_split_key} if actual_split_key else set())
        aliases = {"y_true": "y", "target": "y", "label": "y"}
        pairs = [(name, name) for name in sorted(source_fields & actual_fields)]
        pairs += [(actual, source) for actual, source in aliases.items()
                  if actual in actual_fields and source in source_fields and (actual, source) not in pairs]
        # At least one feature/ID plus the target is needed to bind rows.
        pairs = list(dict.fromkeys(pairs))
        if len(pairs) < 2:
            continue
        def canonical(row, *, actual):
            values = []
            for actual_name, source_name in pairs:
                raw = row.get(actual_name if actual else source_name, "")
                try:
                    values.append(f"{float(raw):.12g}")
                except (TypeError, ValueError):
                    values.append(str(raw))
            return tuple(values)
        expected_set = Counter(canonical(row, actual=False) for row in expected_rows)
        actual_set = Counter(canonical(row, actual=True) for row in actual_rows)
        if expected_set != actual_set:
            return [f"candidate evaluation rows do not match explicit frozen split in {split_source.name}"]
        # A machine-readable method claim that says the split was regenerated
        # contradicts the row-level contract even if a lucky seed overlaps.
        for path in paths:
            if path.suffix.lower() != ".json":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if "train_test_split" in text and not re.search(r"explicit|label|column|逐样本|标签", text):
                return ["candidate regenerated a random split instead of consuming explicit frozen split labels"]
        return []
    return ["candidate predictions cannot be joined to the explicit frozen split"]


def _confidence_weight(supported, rejected, unknown, business_delta):
    """EWA: weight = sum(weight_i * evidence_i) for supported/unknown.
    A rejected item drops confidence but does not zero it (unlike a hard
    drop).  business_delta adds a small prior if it is True.
    """
    w_supported = sum(0.9 for _ in supported or [])
    w_unknown = sum(0.4 for _ in unknown or [])
    w_rejected = sum(0.1 for _ in rejected or [])
    score = w_supported + w_unknown - w_rejected + (0.3 if business_delta else 0.0)
    return round(min(1.0, max(0.0, score / 2.0)), 3)


def outcome_reflect(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    # 注入幻觉警告：如果 execute 的产物声明与真实文件系统不一致，必须明说"幻觉"。
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    sem = prior.get("semantic_output") if isinstance(prior.get("semantic_output"), dict) else {}
    extra = ""
    if sem.get("fabrication_detected"):
        declared = sem.get("declared_artifacts") or []
        verified = sem.get("verified_artifacts") or []
        extra = (f"\n【系统警告】execute 声明 {len(declared)} 个产物，但文件系统只核验到 "
                 f"{len(verified)} 个（{declared[:5]}）。"
                 "在 supported/rejected 中必须显式记一条 rejected=execute_fabrication_detected，"
                 "并把 business_delta 判 false，禁止用叙事掩盖执行缺口。\n")
    base = _deliberate(ctx, params, "project_outcome_reflect",
        "只依据已验证结果反思：哪些认识被支持或否证、是否真的推进业务、下一项目问题是什么。有限样本未达阈值只是否定本次配置下的假设，不能推出物理不可能、化学不可达或性能上限。比较两轮效果前必须核对同一样本集合、预处理、搜索预算与指标口径；多个因素变化不能归因于单因素，不同候选数的最优值或TopK不能直接声称排名改善。还须对照 implementation_evidence 的实际函数调用和常量列表：写死候选列表再过滤不等于实现生成算法，记录方法名称不等于运行了该方法；仅有静态调用名也不能证明某分支已执行，须结合命令回执。字段 supported,rejected,unknown,business_delta,next_question,lesson,evidence_refs,objective_complete,failure_class(project/science/epistemic/mechanism/runtime/data),epistemic_gap,missing_external_evidence,source_query,reproducer_passed,repeated_falsified_route,scientific_negative_result,data_scarcity。路由字段必须依据证据：知识或外部来源缺口才标 epistemic；Partner 机制缺陷必须有稳定复现才标 mechanism/runtime 和 reproducer_passed；科学假设被否证要标 scientific_negative_result，不能伪装成自进化。"
        + "执行整体失败与中间数据有效是不同命题：若 evidence 已读取并给出有效数据行数，保留该部分事实，不能仅因后续超时或模型预算耗尽否定已经生成的数据。文件格式核验不证明来源科学合理，结合命令回执判断具体步骤是否运行。"
        + extra)
    sem_out = base.get("semantic_output") or {}
    execution = sem.get("execution") if isinstance(sem.get("execution"), dict) else {}
    verified_evidence = sem.get("evidence") or []
    allowed_refs = {str(row.get("path") or "") for row in verified_evidence
                    if isinstance(row, dict) and row.get("path")}
    claimed_refs = {str(path) for path in sem_out.get("evidence_refs") or [] if str(path)}
    foreign_refs = sorted(path for path in claimed_refs if path not in allowed_refs)
    if foreign_refs and verified_evidence:
        # Reflection may use memory as a hypothesis, but it cannot replace the
        # current round's evidence with metrics or limitations from an older
        # Job.  This exact contamination invented a 27-row external-test
        # bottleneck even though the current paired evidence had 30,056 rows.
        sem_out = {
            "supported": ["本轮已产生并核验当前 Job 的业务证据"],
            "rejected": ["拒绝引用其他 Job 或未被本轮 verifier 核验的证据来解释当前结果"],
            "unknown": ["只保留当前 verifier 尚未裁决的项目边界"],
            "business_delta": bool(sem.get("verified")),
            "next_question": "按用户冻结的下一阶段协议继续，或在协议完成后结算",
            "lesson": "反思证据被限制为当前轮 verifier 的 allowlist",
            "evidence_refs": sorted(allowed_refs),
            "objective_complete": bool(sem.get("verified")),
            "failure_class": "project",
            "epistemic_gap": False,
            "missing_external_evidence": False,
            "source_query": "",
            "reproducer_passed": False,
            "repeated_falsified_route": False,
            "scientific_negative_result": False,
            "data_scarcity": False,
            "foreign_evidence_refs": foreign_refs,
            "truth_guard_applied": True,
        }
    # With no command or verified artifact, a runtime failure cannot support
    # a scientific explanation. Keep its exact class and prevent a fabricated
    # data-scarcity claim from steering the next round into active learning.
    if execution.get("execution_status") == "failed" and not verified_evidence:
        error = str(execution.get("error") or "project action failed")
        sem_out = {
            "supported": [],
            "rejected": ["本轮没有执行命令或可核验业务产物，不能推断数据稀缺、统计功效或科学结论"],
            "unknown": ["冻结动作尚未成功执行"],
            "business_delta": False,
            "next_question": "如何在相同冻结协议下恢复或重试本轮动作？",
            "lesson": f"运行时失败：{error}",
            "evidence_refs": list(prior.get("evidence_refs") or []),
            "objective_complete": False,
            "failure_class": "runtime",
            "epistemic_gap": False,
            "missing_external_evidence": False,
            "source_query": "",
            "reproducer_passed": False,
            "repeated_falsified_route": False,
            "scientific_negative_result": False,
            "data_scarcity": False,
            "runtime_error": error,
            "truth_guard_applied": True,
        }
    base["semantic_output"] = {
        **sem_out,
        "weight": _confidence_weight(sem_out.get("supported") or [],
                                    sem_out.get("rejected") or [],
                                    sem_out.get("unknown") or [],
                                    sem_out.get("business_delta") or False),
    }
    return base


def continuation_propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _deliberate(ctx, params, "project_continuation_propose",
        "把本轮终态变成下一轮新的可证伪任务，而非重复本轮。必须核对已验真历史产物和监督纠正，已经成功的恢复、解析、索引、文件复制不能再次作为主任务。why_new具体写新增哪个实验/来源/方案结论及与现有结果的差异，停止条件应对应用户目标而非文件数量；在剩余时间内选能完成的最小真实推进。字段 next_goal,why_new,required_inputs,stop_condition。")


DEFINITIONS = [
    EventDefinition("project.state_inspect", "project", "重建项目真实状态和证据边界", state_inspect, reads_existing_artifact=True),
    EventDefinition("project.plan_propose", "project", "单步plan+event流: proposer+attacker+selector 合一", plan_propose, execution_method="llm"),
    EventDefinition("project.action_execute", "project", "执行一个经过选择的有界项目能力", action_execute, idempotent=False),
    EventDefinition("project.input_consumption_verify", "project", "独立核验执行确实消费了准入且哈希冻结的输入", input_consumption_verify, reads_existing_artifact=True),
    EventDefinition("project.outcome_verify", "project", "核验业务动作及其证据", outcome_verify, reads_existing_artifact=True),
    EventDefinition("project.outcome_reflect", "project", "根据终态修订项目认识", outcome_reflect, execution_method="llm"),
    EventDefinition("project.continuation_propose", "project", "形成不重复的下一项目任务", continuation_propose, execution_method="llm"),
]
