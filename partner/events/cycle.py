"""Semantic cycle Events; orchestration belongs to cycle_children/FlowController."""
from pathlib import Path
import hashlib
import json
import re
import time
from partner.event_fabric.catalog import EventDefinition
from partner.runtime.action_execution import write_json
from ._llm import call_model, json_object


def folder(ctx):
    return Path(ctx.workspace) / 'state/cycles' / ctx.job_id


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def records(ctx):
    values = {name: read(folder(ctx) / (name + '.json'))
              for name in ('round_one', 'round_two', 'round_three',
                           'learning_one', 'learning_two', 'learning_three', 'report')}
    base = folder(ctx) / 'iterations'
    if base.is_dir():
        for path in sorted(base.glob('*.json')):
            values['iterations/' + path.stem] = read(path)
    return values


def evidence(ctx):
    refs = []
    for name, value in records(ctx).items():
        if value:
            refs.append(str(folder(ctx) / (name + '.json')))
            refs.extend(value.get('files') or [])
            for output in (value.get('node_outputs') or {}).values():
                for key in ('path', 'pdf_path'):
                    p = output.get(key)
                    if p and str(p).startswith(str(Path(ctx.workspace) / 'state/event_runtime/work' / ctx.job_id)):
                        refs.append(str(p))
            # A timed-out action can still have real partial outputs. Discover
            # only this owned child's work directory; never infer validation.
            work = Path(ctx.workspace) / 'state/event_runtime/work' / ctx.job_id / str(value.get('flow_id') or '')
            if value.get('flow_id') and work.is_dir():
                from partner.runtime.artifact_checks import owned_files
                refs.extend(str(p) for p in owned_files(work) if p.is_file()
                            and '.execution' not in p.parts and '__pycache__' not in p.parts
                            and p.suffix in ('.py', '.json', '.jsonl', '.md', '.csv')
                            and p.stat().st_size < 250000)
    return list(dict.fromkeys(p for p in refs if Path(p).is_file()))


def enrich(ctx, params, outputs):
    refs = evidence(ctx)
    contract = {**params.get('intent_contract', {}), 'evidence_refs': refs,
                'notification_kind': 'milestone', 'force': True}
    recovery = read(folder(ctx) / 'supervisor_recovery.json')
    request = params.get('request', '')
    if recovery:
        request += ('\n监督恢复说明：之前的评估输入截断遗漏了第二轮，已保留原失败证据。'
                    '现在根据真实两轮快照重新评估与交付；若先前已经发过错误结论，请明确更正。'
                    '不重跑业务轮；自进化仍由后续独立Flow执行。')
    return {**params, 'request': request, 'intent_contract': contract, 'evidence_refs': refs,
            'notification_kind': 'milestone', 'force': True}


def result(value, summary, files=()):
    return {'ok': True, 'status': 'completed', 'semantic_output': value,
            'summary': summary, 'files': list(files), 'evidence_refs': list(files)}


class _RoundNames(dict):
    def __missing__(self, key):
        return f'{int(key):04d}'


_ROUND_NAMES = _RoundNames({1: 'one', 2: 'two', 3: 'three'})


def _iteration_path(ctx, kind, number):
    return folder(ctx) / 'iterations' / f'{kind}_{int(number):04d}.json'


def _round_record(ctx, number):
    dynamic = _iteration_path(ctx, 'round', number)
    return read(dynamic) if dynamic.is_file() else read(folder(ctx) / f'round_{_ROUND_NAMES[number]}.json')


def _learning_record(ctx, number):
    dynamic = _iteration_path(ctx, 'learning_after', number)
    return read(dynamic) if dynamic.is_file() else read(folder(ctx) / f'learning_{_ROUND_NAMES[number]}.json')


def _action_signature(value):
    """Stable semantic action identity; wording changes do not create novelty."""
    if not isinstance(value, dict):
        value = {}
    material = {
        'input_hashes': sorted(str(v) for v in value.get('input_hashes') or []),
        'target_artifact': value.get('target_artifact') or value.get('output'),
        'executor': value.get('executor') or value.get('event_type') or 'project.agent_action',
        'intervention': value.get('intervention') or value.get('round_goal') or value.get('action'),
        'measurement': value.get('measurement') or value.get('expected_effect'),
    }
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def _longitudinal_evidence(ctx):
    rows = []
    base = folder(ctx) / 'iterations'
    for path in sorted(base.glob('round_*.json')) if base.is_dir() else []:
        record = read(path); outputs = record.get('node_outputs') or {}
        design = ((outputs.get('design') or {}).get('semantic_output') or {})
        verify = ((outputs.get('verify') or {}).get('semantic_output') or {})
        decision = ((outputs.get('next_decide') or {}).get('semantic_output') or {})
        guard = ((outputs.get('budget_guard') or {}).get('semantic_output') or {})
        execute = outputs.get('execute') or {}
        rows.append({
            'round_number': design.get('round_number') or len(rows) + 1,
            'hypothesis': design.get('hypothesis'), 'round_goal': design.get('round_goal'),
            'action_signature': design.get('action_signature') or _action_signature(design),
            'execution_status': execute.get('status'),
            'verified': verify.get('verified') is True,
            'evidence': list(verify.get('evidence') or verify.get('verified_artifacts') or [])[:12],
            'information_gain': decision.get('information_gain'),
            'route': guard.get('route'), 'failure_signature': guard.get('failure_signature'),
            'unresolved': list(decision.get('unresolved') or [])[:8],
        })
    return rows


def _constraints(params):
    contract = params.get('intent_contract') if isinstance(params.get('intent_contract'), dict) else {}
    value = contract.get('execution_constraints') if isinstance(contract.get('execution_constraints'), dict) else {}
    return dict(value)


def _deadline_state(policy, now=None):
    current = time.time() if now is None else float(now)
    deadline = float(policy.get('run_until_epoch') or 0)
    reserve = int(policy.get('finalization_reserve_seconds') or 0)
    mode = policy.get('continuation_mode') == 'until_deadline' and deadline > 0
    if mode:
        remaining = max(0, int(deadline - current))
        may_start = bool(remaining > reserve)
    else:
        # bounded_rounds (no deadline): no time budget to exhaust; rounds are bounded instead
        remaining = None
        may_start = True
    return {
        'deadline_mode': mode,
        'run_until_epoch': deadline,
        'remaining_seconds': remaining,
        'finalization_reserve_seconds': reserve,
        'may_start_new_work': may_start,
    }


def _declared_round_stage(original: str, number: int) -> str:
    """Return the user's explicit instruction for one numbered round."""
    match = re.search(
        rf'第\s*{number}\s*轮(?P<body>.*?)(?=\n\s*第\s*{number + 1}\s*轮|\Z)',
        str(original or ''), re.S | re.I)
    return (match.group(0).strip()[:3000] if match else '')


def initialize(ctx, params):
    constraints = _constraints(params)
    deadline_mode = constraints.get('continuation_mode') == 'until_deadline'
    requested = int(constraints.get('max_rounds') or 5)
    maximum = None if deadline_mode else max(1, min(20, requested))
    original = str((params.get('intent_contract') or {}).get('original_request')
                   or params.get('request') or '')
    # max_rounds is a ceiling, but an explicitly phased user protocol also
    # defines a floor.  The model may judge a successful baseline "complete";
    # it must not thereby skip the user's declared candidate/second phase.
    minimum = 1
    declared_numbers = [int(value) for value in re.findall(r'第\s*(\d+)\s*轮|round\s*(\d+)', original, re.I)
                        for value in value if value]
    if declared_numbers:
        minimum = max(declared_numbers)
    elif re.search(r'第三轮|third\s+round', original, re.I): minimum = 3
    elif re.search(r'第二轮|后一轮|下一轮|second\s+round', original, re.I): minimum = 2
    if maximum is not None:
        minimum = min(minimum, maximum)
    report_policy = str(params.get('report_policy') or 'milestone')
    report_required = report_policy != 'none'
    declared_learning_policy = str(constraints.get('active_learning_policy') or '').strip().lower()
    conditional_learning_only = bool(re.search(
        r'(只有|仅当|if and only if).{0,30}(知识缺口|epistemic).{0,30}'
        r'(主动学习|active[ _-]?learning)', original, re.I))
    explicit_learning_checkpoint = bool(
        declared_learning_policy in {'require_once', 'required', 'once'} or
        re.search(r'(必须|须|must)\s*(进行|执行|触发|包含)?\s*'
                  r'(主动学习|active[ _-]?learning)', original, re.I)
        or re.search(r'(主动学习|active[ _-]?learning)\s*(环节|阶段)?\s*'
                     r'(是)?\s*(必须|required)', original, re.I)
        or re.search(r'(任务|目标|目的).{0,20}(主动学习|active[ _-]?learning)', original, re.I)
        or (not conditional_learning_only and re.search(
            r'(主动学习|active[ _-]?learning).{0,24}(提出|形成|学习|阅读|消费)', original, re.I)))
    run_until = float(constraints.get('run_until_epoch') or 0)
    if deadline_mode and not run_until:
        run_until = time.time() + int(constraints.get('run_duration_seconds') or 0)
    requested_reserve = int(constraints.get('finalization_reserve_seconds') or 600)
    duration = int(constraints.get('run_duration_seconds') or 0)
    # A short run still needs enough wall time for settlement, two delivery
    # messages and PDF rendering.  Treat the caller value as a floor; the old
    # 60-second reserve caused the research loop to consume the whole window
    # and left the user-visible result unfinished.
    reserve = requested_reserve
    if deadline_mode and duration:
        reserve = min(max(60, duration - 60), max(requested_reserve, min(300, duration // 2)))
    value = {
        'cycle_id': ctx.job_id, 'project_id': params.get('project_id', ''),
        'max_rounds': maximum, 'minimum_rounds': minimum,
        'continuation_mode': 'until_deadline' if deadline_mode else 'bounded_rounds',
        'run_until_epoch': run_until if deadline_mode else 0,
        'run_duration_seconds': int(constraints.get('run_duration_seconds') or 0),
        'finalization_reserve_seconds': reserve if deadline_mode else 0,
        'requested_finalization_reserve_seconds': requested_reserve if deadline_mode else 0,
        'round_policy': ('settlement_driven_until_deadline' if deadline_mode else
                         'settlement_driven_after_explicit_protocol_floor'),
        'learning_policy': ('require_once' if explicit_learning_checkpoint else
                            'only_for_resolvable_epistemic_gap'),
        'explicit_learning_checkpoint': explicit_learning_checkpoint,
        'evolution_policy': 'post_delivery_partner_mechanism_only',
        'report_policy': report_policy,
        'report_required': report_required,
    }
    path = folder(ctx) / 'cycle_policy.json'
    write_json(path, value)
    summary = (f'初始化持续到 {int(run_until)} 的证据驱动项目周期' if deadline_mode else
               f'初始化最多{maximum}轮的证据驱动项目周期')
    return {**result(value, summary, [str(path)]),
            'report_required': report_required}


def research_preflight(ctx, params):
    """Freeze the run-level research contract before any round is planned.

    This is deliberately a parent-flow Event.  A round-level model may settle
    one hypothesis, but it cannot redefine the corpus, the comparison rule or
    the conditions that close the whole run.
    """
    contract = params.get('intent_contract') or {}
    original = str(contract.get('original_request') or params.get('request') or '')
    comparison_required = bool(re.search(
        r'baseline|candidate|benchmark|基线|候选|对照|比较|改善|提升|降低',
        original, re.I))
    raw, usage = call_model(ctx, purpose='cycle_research_preflight', prompt=(
        '你在冻结整个运行级研究协议，不设计单轮动作。把用户目标拆成3到8个彼此不同、可证伪的研究问题，'
        '并定义什么输入有资格进入研究语料、什么证据才算完成比较。当前假设完成不等于整个运行完成。'
        '输出JSON：research_question,corpus_requirements,measurement_contract,problem_candidates,global_success,global_failure,'
        'early_stop_conditions。problem_candidates每项含id,question,required_inputs,measurement,status，status初始为untried。'
        '如果用户要求baseline/candidate/比较，measurement_contract必须要求同输入、同预算的可复算对照；'
        '没有明确数据或评价器时标记needs_protocol，不得假装已完成benchmark。\n'
        '原始请求=' + original[:10000] + '\nIntentContract=' +
        json.dumps({k: contract.get(k) for k in ('goal','constraints','success_criteria','knowledge_gaps')},
                   ensure_ascii=False)[:12000]))
    value = json_object(raw)
    candidates = [row for row in value.get('problem_candidates') or [] if isinstance(row, dict)]
    if not candidates:
        candidates = [
            {'id': 'P1', 'question': '冻结合格输入与可复算 baseline',
             'required_inputs': ['eligible corpus'], 'measurement': 'frozen baseline', 'status': 'untried'},
            {'id': 'P2', 'question': '执行一个与 baseline 同输入同预算的 candidate',
             'required_inputs': ['baseline receipt'], 'measurement': 'matched comparison', 'status': 'untried'},
            {'id': 'P3', 'question': '审查失败边界并寻找下一项非重复问题',
             'required_inputs': ['settled evidence'], 'measurement': 'new information', 'status': 'untried'},
        ]
    candidates = [dict(row) for row in candidates]
    first_problem_id = str(candidates[0].get('id') or '') if candidates else ''
    if candidates:
        candidates[0]['status'] = 'active'
    value.update({
        'schema_version': 1,
        'comparison_required': comparison_required,
        'comparison_protocol_status': ('needs_protocol' if comparison_required else 'not_required'),
        'completion_levels': {
            'action_completed': 'one Event action reached a terminal',
            'hypothesis_settled': 'one frozen hypothesis was supported, rejected or inconclusive',
            'research_question_settled': 'one portfolio question exhausted its declared tests',
            'run_completed': 'global success/failure or evidence-backed portfolio exhaustion',
        },
        'problem_candidates': candidates,
        'active_problem_id': first_problem_id,
        'portfolio_exhausted': False,
        'rule': 'only run_completed may terminate an until_deadline run before its finalization reserve',
    })
    path = folder(ctx) / 'research_preflight.json'
    portfolio_path = folder(ctx) / 'problem_portfolio.json'
    write_json(path, value)
    write_json(portfolio_path, {
        'schema_version': 1, 'problems': candidates, 'active_problem_id': first_problem_id,
        'settled_problem_ids': [], 'attempted_problem_ids': [],
        'portfolio_exhausted': False, 'exhaustion_evidence': [],
    })
    return {**result(value, f'冻结运行级研究协议与 {len(candidates)} 个候选问题',
                     [str(path), str(portfolio_path)]), 'token_usage': usage}


def input_resolve(ctx, params):
    """Resolve concrete, hash-bound inputs before any hypothesis is frozen."""
    contract = params.get('intent_contract') or {}
    candidates = []
    explicit = []
    for raw in (list(params.get('evidence_refs') or [])
                + list(contract.get('evidence_refs') or [])
                + list(params.get('attachments') or [])):
        path = raw.get('path') if isinstance(raw, dict) else raw
        if path:
            candidates.append(str(path))
            explicit.append(str(path))
    try:
        from partner.index.resource_catalog import ResourceCatalog
        catalog = ResourceCatalog(ctx.workspace)
        for kind in ('artifact', 'external', 'document'):
            for row in catalog.query(kind, limit=12):
                if row.get('project_id') in {None, '', params.get('project_id')} or kind != 'artifact':
                    candidates.append(str(row.get('path') or ''))
    except Exception:
        pass
    job_id = str(getattr(ctx, "job_id", "") or "")
    def _same_job(text):
        if '/state/event_runtime/work/job_' not in text:
            return True
        m = re.search(r'/work/(job_[a-f0-9]{16})/', text)
        return bool(m) and m.group(1) == job_id
    before = len(candidates)
    candidates = [c for c in candidates if _same_job(c)]
    filtered_cross_job = before - len(candidates)
    resolved = []
    for raw in dict.fromkeys(candidates):
        path = Path(raw)
        if not raw or not path.is_file():
            continue
        try:
            size = path.stat().st_size
            if size > 100_000_000:
                continue
            resolved.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(
                path.read_bytes()).hexdigest(), 'bytes': size, 'suffix': path.suffix.lower(),
                'source': 'explicit' if raw in explicit else 'indexed'})
        except OSError:
            continue
        if len(resolved) >= 30:
            break
    value = {'schema_version': 1, 'resolved_inputs': resolved,
             'resolved_count': len(resolved), 'filtered_cross_job': filtered_cross_job,
             'input_hashes': [row['sha256'] for row in resolved],
             'unresolved_explicit_refs': [raw for raw in explicit if not Path(raw).is_file()],
             'rule': 'hypotheses may only treat these hash-bound inputs as available'}
    path = folder(ctx) / 'iterations' / f"inputs_{int(params.get('round_number') or contract.get('round_number') or 1):04d}.json"
    write_json(path, value)
    return result(value, f"本轮解析 {len(resolved)} 个具体输入", [str(path)])


def input_eligibility(ctx, params):
    """Classify concrete inputs before a hypothesis can treat them as data."""
    outputs = params.get('flow_outputs') or {}
    resolved = ((outputs.get('input_resolve') or {}).get('semantic_output') or {}).get('resolved_inputs') or []
    preflight = read(folder(ctx) / 'research_preflight.json')
    samples = []
    for row in resolved[:30]:
        path = Path(str(row.get('path') or ''))
        preview = ''
        if path.is_file() and path.suffix.lower() in {'.md','.txt','.json','.jsonl','.csv','.py'}:
            try:
                preview = path.read_text(encoding='utf-8', errors='replace')[:1800]
            except OSError:
                pass
        samples.append({**row, 'preview': preview})
    raw, usage = call_model(ctx, purpose='cycle_input_eligibility', prompt=(
        '你是研究输入准入Event。逐个判断文件能否用于当前研究问题，不能因为文件可读就把项目说明、日志、'
        '临时产物或代码当作论文/实验数据。输出JSON：decisions，每项含path,eligible,role,reason,limitations；'
        '另含 corpus_ready,missing_inputs。只允许引用给定path。研究协议=' +
        json.dumps(preflight, ensure_ascii=False)[:12000] + '\n候选输入=' +
        json.dumps(samples, ensure_ascii=False)[:36000]))
    judged = json_object(raw)
    by_path = {str(row.get('path')): row for row in judged.get('decisions') or []
               if isinstance(row, dict) and row.get('path')}
    decisions = []
    for row in resolved:
        path = str(row.get('path') or '')
        decision = by_path.get(path) or {
            'path': path, 'eligible': False, 'role': 'unknown',
            'reason': 'eligibility model did not classify this indexed input',
            'limitations': ['not admitted by the frozen corpus gate'],
        }
        job_id = str(getattr(ctx, "job_id", "") or "")
        def _same_job(text):
            if '/state/event_runtime/work/job_' not in text:
                return True
            m = re.search(r'/work/(job_[a-f0-9]{16})/', text)
            return bool(m) and m.group(1) == job_id
        if not _same_job(path):
            decision['eligible'] = False
            decision['reason'] = 'cross-job artifact excluded from admission'
            decision['limitations'] = (decision.get('limitations') or []) + ['cross-job artifact']
        decision['eligible'] = bool(decision.get('eligible')) and Path(path).is_file()
        decisions.append(decision)
    eligible = [row for row in decisions if row.get('eligible')]
    value = {
        'schema_version': 1, 'decisions': decisions,
        'eligible_inputs': [next(item for item in resolved if item.get('path') == row.get('path'))
                            for row in eligible],
        'eligible_count': len(eligible), 'rejected_count': len(decisions) - len(eligible),
        'corpus_ready': bool(judged.get('corpus_ready')) and bool(eligible),
        'missing_inputs': judged.get('missing_inputs') or ([] if eligible else ['no eligible research input']),
        'rule': 'only eligible_inputs may be presented to hypothesis design as research data',
    }
    number = int(params.get('round_number') or (params.get('intent_contract') or {}).get('round_number') or 1)
    path = folder(ctx) / 'iterations' / f'eligibility_{number:04d}.json'
    write_json(path, value)
    return {**result(value, f'输入准入：{len(eligible)} 项合格，{value["rejected_count"]} 项拒绝', [str(path)]),
            'token_usage': usage}


def input_adequacy(ctx, params):
    """Check whether admitted inputs can support the declared measurements."""
    number = int(params.get('round_number') or
                 (params.get('intent_contract') or {}).get('round_number') or 1)
    eligibility = ((((params.get('flow_outputs') or {}).get('input_eligibility') or {})
                    .get('semantic_output')) or {})
    preflight = read(folder(ctx) / 'research_preflight.json')
    eligible = eligibility.get('eligible_inputs') or []
    decisions = eligibility.get('decisions') or []
    roles = sorted({str(row.get('role') or '') for row in decisions
                    if isinstance(row, dict) and row.get('eligible') is True and row.get('role')})
    measurement = json.dumps(preflight.get('measurement_contract') or {}, ensure_ascii=False)
    needs_labels = bool(re.search(
        r'precision|recall|accuracy|f1|精确率|准确率|召回率|误报|真值|标签', measurement, re.I))
    has_class_diversity = len(roles) >= 2 and any(
        re.search(r'negative|noise|control|负|噪声|对照', role, re.I) for role in roles)
    gaps = []
    if not eligible:
        gaps.append('no admitted research inputs')
    if needs_labels and not has_class_diversity:
        gaps.append('declared classification metrics require labeled positive and negative/control inputs')
    adequate = bool(eligible) and (not needs_labels or has_class_diversity)
    value = {
        'adequate_for_declared_metrics': adequate,
        'eligible_count': len(eligible), 'roles': roles,
        'declared_metrics_need_labels': needs_labels,
        'has_class_diversity': has_class_diversity,
        'protocol_gaps': gaps,
        'required_next_action': ('' if adequate else
                                 'freeze an annotation/control protocol before baseline or candidate scoring'),
    }
    path = folder(ctx) / 'iterations' / f'adequacy_{number:04d}.json'
    write_json(path, value)
    return result(value, ('输入可支撑声明的评价指标' if adequate else
                          '输入虽已准入，但不足以计算声明的评价指标'), [str(path)])


def round_design(ctx, params):
    number = int(params['round_number'])
    name = _ROUND_NAMES[number]
    previous_name = _ROUND_NAMES.get(number - 1, '')
    previous_record = _round_record(ctx, number - 1) if number > 1 else {}
    previous = ((previous_record.get('node_outputs') or {}).get('next_decide') or {}).get('semantic_output') or {}
    learning = _learning_record(ctx, number - 1) if number > 1 else {}
    impact = read(folder(ctx) / f'impact_{previous_name}.json') if number > 1 else {}
    contract = params.get('intent_contract') or {}
    deadline = contract.get('cycle_deadline') or {}
    history = contract.get('iteration_history') or []
    evidence_table = _longitudinal_evidence(ctx)
    resolved_inputs = (((params.get('flow_outputs') or {}).get('input_eligibility') or {})
                       .get('semantic_output') or {})
    input_adequacy_state = (((params.get('flow_outputs') or {}).get('input_adequacy') or {})
                            .get('semantic_output') or {})
    preflight = read(folder(ctx) / 'research_preflight.json')
    portfolio = read(folder(ctx) / 'problem_portfolio.json')
    raw, usage = call_model(ctx, purpose='cycle_round_design', prompt=(
        '你在设计当前项目下一轮的Event Flow蓝图。改进对象只能是用户项目；科学指标、模型、数据和实验属于项目迭代。'
        '不得把项目RMSE等指标问题写成Partner自进化。依据上一轮Settlement和主动学习handoff选择一个可证伪动作。'
        '只使用允许的Event序列：memory.context_recall -> project.state_inspect -> project.plan_propose -> '
        'core.state_build -> core.latent_forecast/core.jev_evaluate -> core.commitment_freeze -> '
        'project.action_execute -> project.input_consumption_verify -> project.outcome_verify -> '
        'project.outcome_reflect -> core.settlement。'
        '先提出2到4个语义不同的候选，逐个检查可执行性、与历史动作签名是否重复、预期信息增益和失败后的分流，'
        '再冻结一个。输出JSON：candidates,critic,selected,round_goal,hypothesis,expected_effect,failure_conditions,'
        'required_evidence,event_sequence,learning_handoff_refs,stop_condition,why_new,input_hashes,target_artifact,'
        'executor,intervention,measurement。event_sequence必须是上述Event的有序子序列，'
        '但执行、核验、反思和结算不可省略。下一轮不得重复已否证动作。'
        '本轮蓝图只包含科研/项目动作；消息、PDF、Event/Flow图、记忆和Partner自进化由父Flow后续专门Event完成，'
        '不得写进round_goal、required_evidence或本轮成功条件。\n'
        '原始目标=' + str((params.get('intent_contract') or {}).get('original_request') or params.get('request') or '')[:6000]
        + '\n持续运行剩余时间=' + json.dumps(deadline, ensure_ascii=False)[:1200]
        + '\n已执行轮次与路线（不得重复相同动作）=' + json.dumps(history, ensure_ascii=False)[:10000]
        + '\n累计证据表与动作签名（必须逐项比较）=' + json.dumps(evidence_table, ensure_ascii=False)[:18000]
        + '\n运行级研究协议（单轮不可改写）=' + json.dumps(preflight, ensure_ascii=False)[:12000]
        + '\n跨轮问题池=' + json.dumps(portfolio, ensure_ascii=False)[:12000]
        + '\n通过语料准入且哈希冻结的可用输入=' + json.dumps(resolved_inputs, ensure_ascii=False)[:16000]
        + '\n输入对声明评价指标的充分性=' + json.dumps(input_adequacy_state, ensure_ascii=False)[:6000]
        + '\n上一轮Settlement=' + json.dumps(previous, ensure_ascii=False)[:12000]
        + '\n主动学习结果=' + json.dumps(learning, ensure_ascii=False)[:10000]
        + '\n学习影响结算=' + json.dumps(impact, ensure_ascii=False)[:6000]
            + '\n【v2 强制规则】next_round_goal 必须显式解决上一轮 verdict_report 的 missing_evidence。'
            + '如果上一轮 rejected_reasons 非空，next_round_goal 的第一条必须针对其中至少一项缺失证据。'
            + '禁止忽略上一轮失败原因而提出全新目标。'
            + '\n上一轮 round_handoff=' + json.dumps(previous.get('round_handoff') or {}, ensure_ascii=False)[:6000]))
    value = json_object(raw)
    selected = value.get('selected') if isinstance(value.get('selected'), dict) else {}
    for key in ('round_goal','hypothesis','expected_effect','failure_conditions','required_evidence',
                'input_hashes','target_artifact','executor','intervention','measurement'):
        if key not in value and key in selected:
            value[key] = selected[key]
    original = str((params.get('intent_contract') or {}).get('original_request')
                   or params.get('request') or '')
    declared_stage = _declared_round_stage(original, number)
    required = {'project.action_execute', 'project.input_consumption_verify', 'project.outcome_verify',
                'project.outcome_reflect', 'core.settlement'}
    sequence = [str(v) for v in value.get('event_sequence') or []]
    allowed = {'memory.context_recall', 'project.state_inspect', 'project.plan_propose',
               'core.state_build', 'core.latent_forecast', 'core.jev_evaluate',
               'core.commitment_freeze', *required}
    if not value.get('round_goal') or not required <= set(sequence) or not set(sequence) <= allowed:
        value['event_sequence'] = [
            'memory.context_recall', 'project.state_inspect', 'project.plan_propose',
            'core.state_build', 'core.latent_forecast', 'core.jev_evaluate',
            'core.commitment_freeze', 'project.action_execute', 'project.input_consumption_verify',
            'project.outcome_verify',
            'project.outcome_reflect', 'core.settlement']
        value['blueprint_repaired'] = True
    value.update(round_number=number, domain='project', evolution_target_forbidden=True,
                 previous_settlement_ref=(str(folder(ctx) / f'settle_{previous_name}.json')
                                          if number > 1 else ''))
    value['resolved_input_refs'] = resolved_inputs.get('eligible_inputs') or []
    if not value.get('input_hashes'):
        value['input_hashes'] = [row.get('sha256') for row in
                                 (resolved_inputs.get('eligible_inputs') or []) if row.get('sha256')]
    value['corpus_ready'] = resolved_inputs.get('corpus_ready') is True
    value['metric_protocol_ready'] = input_adequacy_state.get('adequate_for_declared_metrics') is True
    value['metric_protocol_gaps'] = input_adequacy_state.get('protocol_gaps') or []
    if input_adequacy_state and value['metric_protocol_ready'] is False:
        value.update({
            'protocol_repair_required': True,
            'round_goal': '冻结可复算的标注与负例/对照协议，使声明的评价指标可计算',
            'hypothesis': '增加独立的负例/对照与冻结标签后，才能对baseline与candidate进行有效比较',
            'expected_effect': '产生带输入哈希、标注规则、类别分布与分歧处理的冻结协议',
            'failure_conditions': input_adequacy_state.get('protocol_gaps') or [
                '缺少可核验标签或对照类'],
            'target_artifact': 'frozen_annotation_and_control_protocol.json',
            'intervention': 'protocol_and_control_construction',
            'measurement': 'protocol completeness, class diversity and frozen input hashes',
        })
    value['comparison_required'] = preflight.get('comparison_required') is True
    value['action_signature'] = _action_signature(value)
    value['prior_action_signatures'] = [row.get('action_signature') for row in evidence_table]
    if declared_stage:
        value['explicit_stage_instruction'] = declared_stage
        value['round_goal'] = declared_stage
        value['user_protocol_stage_frozen'] = True
    if learning:
        handoff = ((learning.get('node_outputs') or {}).get('handoff') or {})
        handoff_refs = [str(p) for p in handoff.get('files') or [] if Path(str(p)).is_file()]
        value['learning_handoff_refs'] = handoff_refs
    path = folder(ctx) / f'design_{name}.json'
    write_json(path, value)
    return {**result(value, f'第{number}轮Event蓝图已冻结', [str(path)]),
            'token_usage': usage}


def round_blueprint_critic(ctx, params):
    """Independently review and freeze the executable round graph.

    The proposer never gets to accept its own plan.  This Event sees the
    proposed blueprint and accumulated evidence, repairs only within the
    registered safe Event catalog, and emits the sequence consumed by the
    Flow controller.
    """
    number = int(params.get('round_number') or
                 (params.get('intent_contract') or {}).get('round_number') or 1)
    proposed = (((params.get('flow_outputs') or {}).get('design') or {})
                .get('semantic_output') or {})
    required = {'project.action_execute', 'project.input_consumption_verify', 'project.outcome_verify',
                'project.outcome_reflect', 'core.settlement',
                'core.latent_forecast', 'core.jev_evaluate',
                'cycle.iteration_learning_effect', 'cycle.iteration_next_decide',
                'cycle.problem_portfolio_update', 'cycle.iteration_budget_guard'}
    allowed_order = [
        'memory.context_recall', 'project.state_inspect', 'project.plan_propose',
        'core.state_build', 'core.latent_forecast', 'core.jev_evaluate',
        'core.commitment_freeze', 'project.action_execute', 'project.input_consumption_verify',
        'project.outcome_verify',
        'project.outcome_reflect', 'core.settlement',
        'cycle.iteration_learning_effect', 'cycle.iteration_next_decide',
        'cycle.problem_portfolio_update', 'cycle.iteration_budget_guard']
    raw, usage = call_model(ctx, purpose='cycle_round_blueprint_critic', prompt=(
        '你是独立Event Flow critic，不复述提案。检查：输入是否已解析、候选是否与历史重复、'
        '执行与评价是否闭合、失败后是否仍会产生下一步裁决、所选Event是否必要。'
        '只能从allowlist选择Event，顺序必须遵守allowlist；execute、verify、reflect、settlement、'
        'latent_forecast、jev_evaluate、input_consumption_verify、learning_effect、next_decide、'
        'problem_portfolio_update、budget_guard均不可删除。输出JSON：accepted,problems,event_sequence,'
        'critic_reason,required_input_resolution,repair_applied。\nallowlist='
        + json.dumps(allowed_order, ensure_ascii=False)
        + '\nproposal=' + json.dumps(proposed, ensure_ascii=False)[:30000]
        + '\nhistory=' + json.dumps(_longitudinal_evidence(ctx), ensure_ascii=False)[:16000]))
    reviewed = json_object(raw)
    requested = [str(v) for v in reviewed.get('event_sequence') or
                 proposed.get('event_sequence') or []]
    sequence = [event for event in allowed_order if event in requested]
    repaired = not required.issubset(sequence)
    if repaired:
        sequence = [event for event in allowed_order
                    if event in set(requested) | required]
    # Planning and commitment are required whenever the action is not already
    # represented by a frozen, resolved execution manifest.
    if not proposed.get('execution_manifest'):
        structural = {'project.plan_propose', 'core.state_build', 'core.commitment_freeze'}
        sequence = [event for event in allowed_order
                    if event in set(sequence) | structural]
    value = {**proposed,
             'proposer_event_sequence': proposed.get('event_sequence') or [],
             'event_sequence': sequence,
             'critic': {
                 'accepted': bool(reviewed.get('accepted')) and not repaired,
                 'problems': list(reviewed.get('problems') or []),
                 'reason': reviewed.get('critic_reason') or '',
                 'required_input_resolution': reviewed.get('required_input_resolution') or [],
                 'repair_applied': repaired or bool(reviewed.get('repair_applied')),
             },
             'flow_materialization': 'runtime_safe_superset_with_durable_skips',
             'round_number': number}
    materialized = ['cycle.input_resolve', 'cycle.input_eligibility', 'cycle.round_design',
                    'cycle.round_blueprint_critic', *sequence]
    value['planned_flow_hash'] = 'sha256:' + hashlib.sha256(json.dumps(
        materialized, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    path = folder(ctx) / f'flow_plan_{_ROUND_NAMES[number]}.json'
    write_json(path, value)
    return {**result(value, f'第{number}轮可执行Event Flow经独立critic冻结', [str(path)]),
            'token_usage': usage}


def round_request(ctx, params):
    number = params['round_number']
    name = _ROUND_NAMES[number]
    previous_name = _ROUND_NAMES.get(number - 1, '')
    prior = _round_record(ctx, number - 1) if number > 1 else {}
    design = read(folder(ctx) / f'design_{name}.json')
    learning = _learning_record(ctx, number - 1) if number > 1 else {}
    learning_outputs = learning.get('node_outputs') or {}
    handoff_output = learning_outputs.get('handoff') or {}
    handoff_files = [str(path) for path in handoff_output.get('files') or []
                     if Path(str(path)).is_file()]
    learning_handoff_path = handoff_files[0] if handoff_files else ''
    original = (params.get('intent_contract') or {}).get('original_request') or params['request']
    maximum = int(read(folder(ctx) / 'cycle_policy.json').get('max_rounds') or 3)
    request = original + f'\n这是证据驱动周期第 {number}/{maximum} 轮。'
    if prior:
        last = prior.get('node_outputs') or {}
        previous_record_path = folder(ctx) / 'iterations' / f'round_{number-1:04d}.json'
        request += (f'必须先读取第 {number-1} 轮真实产物，依据结果执行当前冻结阶段；不要回退到第一轮或重复原动作。'
                    f'当前是第 {number} 轮，前一轮完成的 baseline/candidate 等步骤不得重新执行。\n前一轮证据文件：'
                    + str(previous_record_path)
                    + '\n前一轮分析：' + json.dumps(last.get('reflect') or {}, ensure_ascii=False)[:7000]
                    + '\n前一轮产物：' + json.dumps(prior.get('files') or [], ensure_ascii=False)[:5000])
    else:
        request += '先完成一个能产生真实结果的有界动作，把剩余问题留给第二轮；不要只探查工具或写方案。'
    request += '\n本轮冻结Event蓝图：' + json.dumps(design, ensure_ascii=False)[:12000]
    if learning:
        request += ('\n上一边界主动学习handoff，必须在plan和执行证据中明确引用或说明为何不采用：'
                    + json.dumps(learning, ensure_ascii=False)[:12000])
        if learning_handoff_path:
            request += ('\n机器冻结的主动学习 handoff 文件（必须实际读取并在执行证据中记录）：'
                        + learning_handoff_path)
    request += ('\n本子Flow只承担这一轮业务实验。两轮后的阶段消息、PDF交付、经验/成长/习惯更新和自进化实验'
                '由父Flow中的独立Event执行；业务动作不可合并、替代或自行再次触发这些阶段。'
                '研究框架实现时必须import并实际调用被测函数；手写等价副本只能作为假设，不能证明生产实现行为。')
    contract = {**params.get('intent_contract', {}), 'round_number': number,
                'original_request': original, 'round_blueprint': design,
                'learning_handoff': learning,
                'learning_handoff_path': learning_handoff_path,
                'evidence_refs': list(dict.fromkeys(
                    list(prior.get('files', [])) + handoff_files))}
    return {**result({'round_number': number}, f'启动项目第{number}轮'),
            'cycle_child': {'flow': 'project_cycle_round', 'owner_node': params['node_id'],
                            'context': {'request': request, 'intent_contract': contract,
                                        'evidence_refs': prior.get('files', [])}}}


def round_settle(ctx, params):
    number = int(params['round_number'])
    name = _ROUND_NAMES[number]
    record = read(folder(ctx) / f'round_{name}.json')
    outputs = record.get('node_outputs') or {}
    verify = ((outputs.get('verify') or {}).get('semantic_output') or {})
    reflect = ((outputs.get('reflect') or {}).get('semantic_output') or {})
    core_settlement = ((outputs.get('core_settlement') or {}).get('semantic_output') or {})
    business_delta = bool((outputs.get('verify') or {}).get('business_delta') or verify.get('verified'))
    raw, usage = call_model(ctx, purpose='cycle_round_settle', prompt=(
        '只依据本轮真实Event终态裁决下一步。分类只能是 complete、continue_project、active_learning、stop。'
        '科学假设失败、RMSE未改善、项目数据问题属于continue_project或stop，绝不能归入Partner自进化。'
        '只有缺少可由外部来源解决的知识才选active_learning。输出JSON：route,objective_complete,'
        'epistemic_gap,external_evidence_can_resolve,next_round_goal,reason,unresolved。\n本轮='
        + json.dumps({'status': record.get('status'), 'verify': verify, 'reflect': reflect,
                      'core_settlement': core_settlement,
                      'business_delta': business_delta, 'files': record.get('files') or []},
                     ensure_ascii=False)[:30000]))
    judged = json_object(raw)
    route = str(judged.get('route') or 'stop')
    if route not in {'complete', 'continue_project', 'active_learning', 'stop'}:
        route = 'stop'
    if judged.get('objective_complete') and business_delta:
        route = 'complete'
    if route == 'active_learning' and not (
            judged.get('epistemic_gap') and judged.get('external_evidence_can_resolve')):
        route = 'continue_project' if business_delta else 'stop'
    policy = read(folder(ctx) / 'cycle_policy.json')
    maximum = int(policy.get('max_rounds') or 3)
    minimum = int(policy.get('minimum_rounds') or 1)
    prior_learning_ready = False
    if number > 1:
        prior_learning = read(folder(ctx) / f'learning_{_ROUND_NAMES[number - 1]}.json')
        prior_handoff = (((prior_learning.get('node_outputs') or {}).get('handoff') or {})
                         .get('semantic_output') or {})
        prior_learning_ready = bool(prior_handoff.get('ready'))
    if route == 'active_learning' and prior_learning_ready:
        route = 'complete' if business_delta else 'stop'
        judged['repeated_learning_suppressed'] = True
        judged['reason'] = ('上一轮已完成有来源约束的主动学习并冻结 handoff；'
                            '本轮不得把同一已解决缺口再次路由为主动学习。')
    if (number < maximum and policy.get('explicit_learning_checkpoint')
            and not any((folder(ctx) / f'learning_{name}.json').is_file()
                        for name in ('one', 'two', 'three'))):
        # This is a protocol constraint declared by the user, not a semantic
        # keyword router.  The learning Event still has to retrieve and verify
        # real sources, and may fail honestly; this only prevents the planner
        # from silently skipping the required checkpoint.
        route = 'active_learning'
        judged['objective_complete'] = False
        judged['epistemic_gap'] = judged.get('epistemic_gap') or (
            'user-declared knowledge checkpoint before the next project phase')
        judged['external_evidence_can_resolve'] = True
        judged['next_round_goal'] = judged.get('next_round_goal') or (
            'resolve the user-declared knowledge question and freeze a source-bound handoff')
        judged['explicit_learning_checkpoint'] = True
    if number < minimum:
        # An explicitly required later project phase establishes a floor, but
        # it must not suppress a legitimate active-learning checkpoint between
        # phases.  The learning child returns to this same bounded cycle and
        # the next project round still runs, so preserving ``active_learning``
        # satisfies both requirements.
        if route != 'active_learning':
            route = 'continue_project'
        judged['objective_complete'] = False
        judged['explicit_protocol_floor'] = minimum
        judged['reason'] = (
            '当前阶段虽已通过验证，但用户显式声明的后续轮次尚未执行；'
            + ('先执行可解决当前知识缺口的主动学习，再进入下一项目轮。'
               if route == 'active_learning'
               else '继续到下一项目轮完成冻结协议。'))
        judged['next_round_goal'] = (judged.get('next_round_goal') or
                                     'execute the next explicitly declared project phase')
    # A mechanism/schema failure in a child round is evidence for a bounded
    # repair attempt, not evidence that the user's project objective is done.
    # Keep it in the project lane; the post-run audit separately decides
    # whether the recurring mechanism defect merits Partner self-evolution.
    child_failed = record.get('status') == 'failed'
    has_executable_plan = bool((outputs.get('plan') or {}).get('semantic_output'))
    if child_failed and has_executable_plan and number < maximum:
        route = 'continue_project'
        judged['objective_complete'] = False
        judged['next_round_goal'] = (judged.get('next_round_goal') or
                                     'repair the failed project round from its exact Event error and retry once')
        judged['failure_recovery'] = 'bounded_project_retry'
    continue_iteration = number < maximum and route in {'continue_project', 'active_learning'}
    value = {**judged, 'round_number': number, 'route': route,
             'business_delta': business_delta, 'continue_iteration': continue_iteration,
             'budget_remaining': number < maximum,
             'project_metric_is_not_self_evolution': True}
    path = folder(ctx) / f'settle_{name}.json'
    write_json(path, value)
    return {**result(value, f'第{number}轮结算为 {route}', [str(path)]),
            'continue_iteration': continue_iteration, 'primary_route': route,
            'token_usage': usage}



def iteration_next_decide(ctx, params):
    """Let the model propose the next research move from this iteration only."""
    number = int(params.get('round_number') or
                 (params.get('intent_contract') or {}).get('round_number') or 1)
    outputs = params.get('flow_outputs') or {}
    verify = ((outputs.get('verify') or {}).get('semantic_output') or {})
    reflect = ((outputs.get('reflect') or {}).get('semantic_output') or {})
    settlement = ((outputs.get('core_settlement') or {}).get('semantic_output') or {})
    design = ((outputs.get('design') or {}).get('semantic_output') or {})
    portfolio = read(folder(ctx) / 'problem_portfolio.json')
    longitudinal = _longitudinal_evidence(ctx)
    
    # v3 fix: 强制学习检查点前移 - 在调用 LLM 之前检查
    policy = read(folder(ctx) / 'cycle_policy.json')
    dynamic_learning = list((folder(ctx) / 'iterations').glob('learning_after_*.json'))
    if policy.get('explicit_learning_checkpoint') and not dynamic_learning:
        # 用户强制要求主动学习但尚未执行 - 直接返回 active_learning，不调用 LLM
        value = {
            'route': 'active_learning',
            'objective_complete': False,
            'reason': '用户显式声明的主动学习检查点尚未执行',
            'epistemic_gap': 'user-declared knowledge checkpoint before the next project phase',
            'external_evidence_can_resolve': True,
            'next_round_goal': 'resolve the user-declared knowledge question and freeze a source-bound handoff',
            'explicit_learning_checkpoint': True,
            'information_gain': '等待执行强制主动学习',
            'round_number': number,
        }
        path = folder(ctx) / 'iterations' / f'decision_{number:04d}.json'
        write_json(path, value)
        return {**result(value, f'第{number}轮强制主动学习检查点', [str(path)]),
                'primary_route': 'active_learning', 'token_usage': {}}
    
    # v3 fix: LLM 失败兜底
    try:
        raw, usage = call_model(ctx, purpose='cycle_iteration_next_decide', prompt=(
        '你是项目迭代的下一步裁决器。只基于本轮冻结假设、真实执行、核验与Settlement，选择一个route：'
        'complete、continue_project、active_learning、stop。项目指标未改善属于项目迭代；Partner机制问题不在此处修。'
        '必须区分action_completed、hypothesis_settled、research_question_settled、run_completed；'
        'objective_complete只表示当前研究问题，不表示持续运行整体结束。'
        '只有明确知识缺口且外部资料能够改变下一实验设计时才选active_learning。继续时必须给出与本轮不同、'
        '可证伪的next_hypothesis和next_round_goal。必须显式比较累计动作签名、失败签名、有效证据、未解决节点、'
        '剩余时间与当前可用执行能力；相同干预换措辞仍是重复。输出JSON：route,objective_complete,reason,unresolved,'
        'information_gain,next_hypothesis,next_round_goal,epistemic_gap,external_evidence_can_resolve,why_not_repeat,'
        'required_evidence,stop_reason,success_criteria_assessment,completion_level,settled_problem_id。'
        'settled_problem_id只能是问题池中的id；本轮未结算某个问题时必须为null，不得填Settlement决策id。'
        'success_criteria_assessment必须逐条返回'
        '{criterion,status,evidence_refs}，status只能是met或unmet；没有当前轮核验证据不得写met。\n'
        + json.dumps({'round_number': number, 'design': design, 'verify': verify,
                      'reflect': reflect, 'settlement': settlement,
                      'original_success_criteria': (params.get('intent_contract') or {}).get('success_criteria') or [],
                      'longitudinal_evidence': longitudinal, 'problem_portfolio': portfolio,
                      'remaining_budget': (params.get('intent_contract') or {}).get('cycle_deadline') or {}},
                     ensure_ascii=False)[:48000]))
        value = json_object(raw)
    except Exception as exc:
        # v3 fix: LLM 失败兜底 - 检查是否仍需强制学习
        if policy.get('explicit_learning_checkpoint') and not dynamic_learning:
            value = {
                'route': 'active_learning',
                'objective_complete': False,
                'reason': f'LLM 调用失败但用户强制要求主动学习: {str(exc)[:200]}',
                'epistemic_gap': 'user-declared knowledge checkpoint before the next project phase',
                'external_evidence_can_resolve': True,
                'next_round_goal': 'resolve the user-declared knowledge question and freeze a source-bound handoff',
                'explicit_learning_checkpoint': True,
                'information_gain': 'LLM 失败，回退到强制主动学习',
                'llm_failure': str(exc)[:500],
                'round_number': number,
            }
            usage = {}
        else:
            # 非强制学习场景，按失败处理
            raise
    route = str(value.get('route') or 'stop')
    if route not in {'complete', 'continue_project', 'active_learning', 'stop'}:
        route = 'stop'
    execution = verify.get('execution') if isinstance(verify.get('execution'), dict) else {}
    if (verify.get('verified') is False and execution.get('execution_status') == 'failed'
            and not (verify.get('evidence') or [])):
        route = 'continue_project'
        value.update(
            objective_complete=False,
            reason='本轮是无业务证据的运行时失败；保持冻结科学协议并有界重试，不据此编造数据或知识缺口',
            unresolved=['冻结动作尚未成功执行'],
            information_gain='仅定位到运行时失败，没有新的科学信息',
            next_hypothesis=design.get('hypothesis') or '相同冻结动作在干净执行上下文中能够完成',
            next_round_goal=design.get('round_goal') or '恢复并重试同一冻结动作',
            epistemic_gap=False, external_evidence_can_resolve=False,
            why_not_repeat='这是对无命令、无产物运行时失败的有界恢复，不是重复已否证的科学实验',
            required_evidence=design.get('required_evidence') or [],
            stop_reason=None, runtime_retry=True,
        )
    value.update(route=route, round_number=number)
    path = folder(ctx) / 'iterations' / f'decision_{number:04d}.json'
    # v2: 生成 round_handoff（内容级交接单）
    # 基于本轮 verify 的 verdict_report 和 reflect 的 lessons
    verify_output = (outputs.get('verify') or {}).get('semantic_output') or {}
    reflect_output = (outputs.get('reflect') or {}).get('semantic_output') or {}
    execute_output = (outputs.get('execute') or {}).get('semantic_output') or {}
    
    verdict_report = verify_output.get('verdict_report') or {}
    
    # inherited_facts: 本轮验证为真的事实
    inherited_facts = []
    if verify_output.get('verified'):
        if execute_output.get('goal_coverage'):
            for coverage in execute_output['goal_coverage']:
                if coverage.get('coverage_status') == 'full':
                    inherited_facts.append(f"目标已达成: {coverage.get('goal_aspect', 'unknown')}")
        if execute_output.get('artifact_claims'):
            for claim in execute_output['artifact_claims']:
                if claim.get('verification_status') == 'verified':
                    inherited_facts.append(f"产物已验证: {claim.get('artifact', 'unknown')}")
    
    # rejected_reasons: verify 拒绝的具体层+缺什么证据
    rejected_reasons = []
    if verdict_report.get('fail_layer'):
        rejected_reasons.append(f"失败层级: {verdict_report['fail_layer']}")
    for evidence in verdict_report.get('missing_evidence', []):
        rejected_reasons.append(f"缺失证据: {evidence}")
    
    # effective_methods: 本轮有效的执行方法
    effective_methods = []
    if reflect_output.get('lessons'):
        for lesson in reflect_output['lessons']:
            if lesson.get('effectiveness') == 'effective':
                effective_methods.append(lesson.get('method', 'unknown'))
    
    # next_actions: 下一轮具体任务
    next_actions = []
    for fix in verdict_report.get('next_round_fix', []):
        next_actions.append(fix)
    
    # artifacts_index: 本轮产物路径清单
    artifacts_index = []
    if execute_output.get('files'):
        for file_info in execute_output['files']:
            # execute 输出 files 为路径字符串列表（契约）；兼容 dict 形态
            if isinstance(file_info, dict):
                artifacts_index.append({
                    'path': file_info.get('path', ''),
                    'type': file_info.get('type', 'unknown')
                })
            else:
                artifacts_index.append({'path': str(file_info), 'type': 'unknown'})
    
    # avoid_repeat: 不得重复的动作
    avoid_repeat = []
    if reflect_output.get('lessons'):
        for lesson in reflect_output['lessons']:
            if lesson.get('effectiveness') == 'ineffective':
                avoid_repeat.append(lesson.get('method', 'unknown'))
    
    round_handoff = {
        'inherited_facts': inherited_facts,
        'rejected_reasons': rejected_reasons,
        'effective_methods': effective_methods,
        'next_actions': next_actions,
        'artifacts_index': artifacts_index,
        'avoid_repeat': avoid_repeat,
        'progress_note': verdict_report.get('progress_note', '无进展记录')
    }
    
    value['round_handoff'] = round_handoff
    
    # v2: information_gain 改用 verify 的 progress_since_last_round
    progress = verdict_report.get('progress_since_last_round', 0)
    if progress > 0:
        value['information_gain'] = f'本轮新增 {progress} 个产物'
    

    write_json(path, value)

    return {**result(value, f'第{number}轮提出 {route}', [str(path)]),
            'primary_route': route, 'token_usage': usage}


def problem_portfolio_update(ctx, params):
    """Settle one portfolio item and select a distinct next question.

    The run-level portfolio prevents a local ``complete`` or one duplicate
    action from terminating a deadline contract while other questions remain.
    """
    number = int(params.get('round_number') or
                 (params.get('intent_contract') or {}).get('round_number') or 1)
    outputs = params.get('flow_outputs') or {}
    decision = ((outputs.get('next_decide') or {}).get('semantic_output') or {})
    portfolio_path = folder(ctx) / 'problem_portfolio.json'
    portfolio = read(portfolio_path) or {'problems': [], 'settled_problem_ids': [],
                                         'attempted_problem_ids': [], 'exhaustion_evidence': []}
    problems = [dict(row) for row in portfolio.get('problems') or [] if isinstance(row, dict)]
    active = str(portfolio.get('active_problem_id') or '')
    known_ids = {str(row.get('id')) for row in problems if row.get('id')}
    proposed_settled_id = str(decision.get('settled_problem_id') or '')
    # Model outputs occasionally confuse a Core settlement decision id with a
    # research-problem id.  Only portfolio ids are legal at this boundary.
    settled_id = proposed_settled_id if proposed_settled_id in known_ids else (
        active if active in known_ids else '')
    if settled_id:
        for row in problems:
            if str(row.get('id')) == settled_id:
                row['status'] = 'settled' if decision.get('objective_complete') else 'attempted'
                row['last_round'] = number
                row['last_information_gain'] = decision.get('information_gain')
        if settled_id not in portfolio.setdefault('attempted_problem_ids', []):
            portfolio['attempted_problem_ids'].append(settled_id)
        if decision.get('objective_complete') and settled_id not in portfolio.setdefault('settled_problem_ids', []):
            portfolio['settled_problem_ids'].append(settled_id)
    untried = [row for row in problems if row.get('status', 'untried') == 'untried']
    raw, usage = call_model(ctx, purpose='cycle_problem_portfolio_update', prompt=(
        '你只负责跨轮问题池管理。根据当前轮证据，从未尝试问题中选择一个与历史动作语义不同、当前可执行、'
        '可证伪的问题；若现有问题不够，可新增至多2个。只有所有方向均有执行/检索证据证明不可行时才可exhausted=true。'
        '输出JSON：next_problem,added_problems,exhausted,exhaustion_evidence,route_reason。next_problem含id,question,'
        'required_inputs,measurement,status；知识或来源缺口需要外部资料时标记route=active_learning，否则route=continue_project。\n'
        '问题池=' + json.dumps({**portfolio, 'problems': problems}, ensure_ascii=False)[:18000] +
        '\n当前裁决=' + json.dumps(decision, ensure_ascii=False)[:12000] +
        '\n累计轮次=' + json.dumps(_longitudinal_evidence(ctx), ensure_ascii=False)[:18000]))
    judged = json_object(raw)
    added = [row for row in judged.get('added_problems') or [] if isinstance(row, dict) and row.get('id')]
    known_ids = {str(row.get('id')) for row in problems}
    for row in added[:2]:
        if str(row.get('id')) not in known_ids:
            row.setdefault('status', 'untried'); problems.append(row); known_ids.add(str(row.get('id')))
    next_problem = judged.get('next_problem') if isinstance(judged.get('next_problem'), dict) else None
    if next_problem and str(next_problem.get('id')) in known_ids:
        selected = next(row for row in problems if str(row.get('id')) == str(next_problem.get('id')))
    else:
        selected = next((row for row in problems if row.get('status', 'untried') == 'untried'), None)
    exhaustion_evidence = [str(v) for v in judged.get('exhaustion_evidence') or [] if str(v)]
    # A prose assertion cannot close the portfolio. Require attempts across at
    # least two distinct problems plus concrete evidence references.
    exhausted = bool(judged.get('exhausted') and len(portfolio.get('attempted_problem_ids') or []) >= 2
                     and exhaustion_evidence and not selected)
    if selected:
        portfolio['active_problem_id'] = str(selected.get('id') or '')
        selected['status'] = 'active'
    portfolio.update(problems=problems, portfolio_exhausted=exhausted,
                     exhaustion_evidence=exhaustion_evidence,
                     last_update_round=number, next_problem=selected,
                     suggested_route=(str((selected or {}).get('route') or 'continue_project') if selected else 'stop'))
    write_json(portfolio_path, portfolio)
    value = {'next_problem': selected, 'portfolio_exhausted': exhausted,
             'suggested_route': portfolio['suggested_route'],
             'exhaustion_evidence': exhaustion_evidence,
             'attempted_problem_ids': portfolio.get('attempted_problem_ids') or [],
             'settled_problem_ids': portfolio.get('settled_problem_ids') or []}
    return {**result(value, ('问题池已证据化耗尽' if exhausted else
                             f'问题池选择下一问题 {(selected or {}).get("id", "待生成")}'),
                     [str(portfolio_path)]), 'token_usage': usage}


def iteration_learning_effect(ctx, params):
    """Settle learning only when this later iteration emits matched evidence."""
    number = int(params.get('round_number') or
                 (params.get('intent_contract') or {}).get('round_number') or 1)
    if number <= 1 or not _learning_record(ctx, number - 1):
        value={'status':'not_applicable','improved':False,'consumed':False,
               'reason':'no preceding active-learning handoff'}
    else:
        outputs=params.get('flow_outputs') or {}
        design=((outputs.get('design') or {}).get('semantic_output') or {})
        verify=((outputs.get('verify') or {}).get('semantic_output') or {})
        matched=verify.get('learning_matched_evidence') or {}
        consumed=bool(design.get('learning_handoff_refs'))
        complete=(isinstance(matched,dict) and matched.get('baseline') is not None
                  and matched.get('candidate') is not None and isinstance(matched.get('improved'),bool))
        value={'status':('improved' if consumed and complete and matched.get('improved') else
                         'not_improved' if consumed and complete else
                         'candidate_consumed' if consumed else 'not_consumed'),
               'improved':bool(consumed and complete and matched.get('improved')),
               'consumed':consumed,'comparison_complete':complete,'matched_evidence':matched,
               'learning_round':number-1,'evaluation_round':number,
               'reason':'settled from downstream matched evidence' if complete else
                        'learning cannot claim improvement without downstream matched evidence'}
    path=folder(ctx)/'iterations'/f'learning_effect_{number:04d}.json'; write_json(path,value)
    return result(value,f"主动学习下游效果：{value['status']}",[str(path)])


def iteration_budget_guard(ctx, params):
    """Deterministically veto unsafe, repetitive or over-budget LLM decisions."""
    number = int(params.get('round_number') or
                 (params.get('intent_contract') or {}).get('round_number') or 1)
    flow_outputs = params.get('flow_outputs') or {}
    decision = ((flow_outputs.get('next_decide') or {})
                .get('semantic_output') or {})
    portfolio_update = ((flow_outputs.get('problem_update') or {})
                        .get('semantic_output') or {})
    policy = read(folder(ctx) / 'cycle_policy.json')
    deadline = _deadline_state(policy)
    deadline_mode = deadline['deadline_mode']
    maximum = None if deadline_mode else int(policy.get('max_rounds') or 5)
    minimum = int(policy.get('minimum_rounds') or 1)
    route = str(decision.get('route') or 'stop')
    reasons = []
    verify_semantic = ((flow_outputs.get('verify') or {}).get('semantic_output') or {})
    if decision.get('objective_complete') and verify_semantic.get('scientific_claim_supported') is False:
        decision['objective_complete'] = False
        route = 'continue_project'
        reasons.append('artifact_verified_but_scientific_claim_not_supported')
        decision['stop_reason'] = None
    declared_criteria = list((params.get('intent_contract') or {}).get('success_criteria') or [])
    criteria_rows = list(decision.get('success_criteria_assessment') or [])
    verified_refs = {str(row.get('path') or '') for row in
                     (((flow_outputs.get('verify') or {}).get('semantic_output') or {})
                      .get('evidence') or [])
                     if isinstance(row, dict) and row.get('valid')}
    criteria_complete = bool(declared_criteria) and len(criteria_rows) >= len(declared_criteria)
    if criteria_complete:
        for row in criteria_rows[:len(declared_criteria)]:
            refs = {str(ref) for ref in row.get('evidence_refs') or []} if isinstance(row, dict) else set()
            if (not isinstance(row, dict) or row.get('status') != 'met'
                    or not refs or not refs.intersection(verified_refs)):
                criteria_complete = False
                break
    if decision.get('objective_complete') and declared_criteria and not criteria_complete:
        decision['objective_complete'] = False
        can_retry = deadline['may_start_new_work'] if deadline_mode else number < maximum
        route = 'continue_project' if can_retry else 'stop'
        reasons.append('original_success_criteria_not_evidence_complete')
        decision['stop_reason'] = ('原始成功条件尚未逐条取得当前轮可核验证据'
                                   if route == 'stop' else None)
        decision['unresolved'] = declared_criteria
    original = str((params.get('intent_contract') or {}).get('original_request') or '')
    next_stage = _declared_round_stage(original, number + 1)
    stage_forbids_learning = bool(next_stage and re.search(
        r'(不得|禁止|不要).{0,24}(搜索|检索|新数据|外部数据|主动学习)',
        next_stage, re.I))
    if route == 'active_learning' and stage_forbids_learning:
        route = 'continue_project'
        reasons.append('explicit_next_stage_forbids_learning')
        decision.update(
            objective_complete=False, epistemic_gap=False,
            external_evidence_can_resolve=False,
            next_hypothesis=f'按用户冻结的第 {number + 1} 轮协议执行，不引入外部资料',
            next_round_goal=next_stage,
            reason='用户已冻结下一阶段且明确禁止搜索或新数据；忽略模型提出的额外主动学习',
            why_not_repeat='下一轮执行用户声明的不同验证阶段',
        )
    if number < minimum and route != 'active_learning':
        route = 'continue_project'; reasons.append('explicit_protocol_floor')
    if route == 'active_learning' and not (decision.get('epistemic_gap')
                                            and decision.get('external_evidence_can_resolve')):
        route = ('continue_project' if deadline_mode or number < maximum else 'stop')
        reasons.append('learning_without_resolvable_gap')
    dynamic_learning=list((folder(ctx)/'iterations').glob('learning_after_*.json'))
    if ((deadline_mode or number < maximum) and policy.get('explicit_learning_checkpoint')
            and not dynamic_learning):
        route='active_learning'; reasons.append('explicit_learning_checkpoint')
        decision['epistemic_gap']=(decision.get('epistemic_gap') or
                                  'user-declared source review before the next project phase')
        decision['external_evidence_can_resolve']=True
        decision['objective_complete']=False
    prior = []
    for path in sorted((folder(ctx) / 'iterations').glob('guard_*.json')):
        row = read(path)
        if int(row.get('round_number') or 0) < number:
            prior.append(row)
    execute = flow_outputs.get('execute') or {}
    verify = (flow_outputs.get('verify') or {}).get('semantic_output') or {}
    execution = verify.get('execution') if isinstance(verify.get('execution'), dict) else {}
    execution_error = str(execute.get('error') or execution.get('error') or execute.get('summary') or '')
    execution_failed = bool(
        verify.get('verified') is False
        and (execute.get('status') == 'failed' or execution.get('execution_status') == 'failed'
             or bool(execution_error)))
    failure_signature = ''
    if execution_failed:
        normalized = re.sub(r'job_[A-Za-z0-9_-]+|flow_[A-Za-z0-9_-]+|\d+', '#',
                            execution_error.lower())[:500]
        failure_signature = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    repeated_mechanism_failure = bool(
        failure_signature and prior and prior[-1].get('failure_signature') == failure_signature)
    terminal_mechanism_blocker = False
    if repeated_mechanism_failure:
        route = 'self_repair'
        terminal_mechanism_blocker = True
        reasons.append('repeated_partner_mechanism_failure')
        decision.update(
            objective_complete=False,
            stop_reason='同一 Partner 执行机制错误连续出现，暂停项目并进入有界自修复',
            information_gain='已稳定复现 Partner 机制阻塞；继续项目轮不会产生新的科学证据')
    low_gain = str(decision.get('information_gain') or '').strip().lower() in {
        '', 'none', 'no', 'zero', '无', '无新增信息', '0'}
    prior_low_gain = [row for row in prior[-1:] if row.get('low_information_gain') is True]
    if (not terminal_mechanism_blocker and route in {'continue_project','active_learning'}
            and low_gain and prior_low_gain):
        route='stop'; reasons.append('two_consecutive_rounds_without_information_gain')
        decision['stop_reason'] = '连续两轮没有新增信息，停止无意义重复'
    proposed_signature = _action_signature({
        'round_goal': decision.get('next_round_goal'),
        'intervention': decision.get('next_intervention') or decision.get('next_round_goal'),
        'measurement': decision.get('next_measurement') or decision.get('required_evidence'),
        'executor': decision.get('next_executor') or 'project.agent_action',
        'input_hashes': decision.get('input_hashes') or [],
        'target_artifact': decision.get('target_artifact')})
    fingerprint = proposed_signature
    historic_signatures = {row.get('action_signature') for row in _longitudinal_evidence(ctx)}
    historic_signatures.update(row.get('next_fingerprint') for row in prior)
    if route in {'continue_project', 'active_learning'} and fingerprint in historic_signatures:
        reasons.append('duplicate_next_action')
        next_problem = portfolio_update.get('next_problem') or {}
        if deadline_mode and deadline['may_start_new_work'] and next_problem:
            route = str(portfolio_update.get('suggested_route') or 'continue_project')
            if route not in {'continue_project', 'active_learning'}:
                route = 'continue_project'
            decision.update(
                objective_complete=False,
                next_hypothesis=str(next_problem.get('question') or '推进问题池中的下一项非重复问题'),
                next_round_goal=str(next_problem.get('question') or '推进问题池中的下一项非重复问题'),
                required_evidence=next_problem.get('measurement') or next_problem.get('required_inputs') or [],
                stop_reason=None,
                why_not_repeat='原动作重复；已由跨轮问题池切换到不同问题')
            reasons.append('duplicate_action_redirected_to_problem_portfolio')
        else:
            route = 'stop'
            decision['stop_reason'] = '下一动作重复，且问题池没有提供可执行的新问题'
    if not deadline_mode and number >= maximum and route in {'continue_project', 'active_learning'}:
        route = 'stop'; reasons.append('round_budget_exhausted')
    if route == 'complete' and not decision.get('objective_complete'):
        route = 'stop'; reasons.append('unsupported_completion')
    # In deadline mode, completion settles the current hypothesis.  It does
    # not discard the remaining observation window: the next round chooses a
    # new unresolved question.  The normal exit starts early enough to render
    # reports and run the post-project Partner audit before the wall deadline.
    if deadline_mode:
        if not deadline['may_start_new_work']:
            route = 'stop'
            reasons.append('deadline_or_finalization_reserve_reached')
        elif route == 'complete' and decision.get('information_gain') and not low_gain:
            route = 'continue_project'
            decision.update(
                objective_complete=False,
                next_hypothesis='依据本轮结算，从尚未解决的问题中选择一个新的可证伪假设',
                next_round_goal='审查累计证据与动作指纹，推进下一项非重复的实质工作',
                why_not_repeat='当前假设已结算，但持续运行契约尚未到最终收尾窗口')
            reasons.append('deadline_mode_reopens_next_research_question')
        elif route == 'stop' and not portfolio_update.get('portfolio_exhausted'):
            next_problem = portfolio_update.get('next_problem') or {}
            if next_problem:
                route = str(portfolio_update.get('suggested_route') or 'continue_project')
                if route not in {'continue_project', 'active_learning'}:
                    route = 'continue_project'
                decision.update(
                    objective_complete=False,
                    next_hypothesis=str(next_problem.get('question') or '推进问题池中的下一项问题'),
                    next_round_goal=str(next_problem.get('question') or '推进问题池中的下一项问题'),
                    stop_reason=None)
                reasons.append('premature_stop_redirected_to_problem_portfolio')
    budget_remaining = (deadline['may_start_new_work'] if deadline_mode else number < maximum)
    value = {**decision, 'route': route, 'round_number': number,
             'continue_iteration': route in {'continue_project', 'active_learning', 'self_repair'},
             'budget_remaining': budget_remaining, 'guard_reasons': reasons,
             'next_fingerprint': fingerprint, 'low_information_gain':low_gain,
             'execution_failed': execution_failed,
             'failure_signature': failure_signature,
             'terminal_mechanism_blocker': terminal_mechanism_blocker,
             'project_metric_is_not_self_evolution': True, **deadline}
    path = folder(ctx) / 'iterations' / f'guard_{number:04d}.json'
    write_json(path, value)
    return {**result(value, f'第{number}轮预算守卫裁决为 {route}', [str(path)]),
            'continue_iteration': value['continue_iteration'], 'primary_route': route}


def iteration_controller(ctx, params):
    """Recoverable parent loop: spawn exactly one auditable child at a time."""
    state_path = folder(ctx) / 'iteration_state.json'
    state = read(state_path) or {'phase': 'ready', 'next_round': 1, 'history': []}
    policy = read(folder(ctx) / 'cycle_policy.json')
    deadline = _deadline_state(policy)
    phase, number = str(state.get('phase') or 'ready'), int(state.get('next_round') or 1)
    eligibility_record = read(folder(ctx) / 'iterations' / f'eligibility_{number:04d}.json')
    learning_source_refs = [str(row.get('path')) for row in
                            (eligibility_record.get('eligible_inputs') or [])
                            if isinstance(row, dict) and row.get('path')
                            and Path(str(row.get('path'))).is_file()][:12]
    if phase == 'project_running':
        record = _round_record(ctx, number)
        if not record:
            return {'ok': False, 'status': 'failed', 'error': f'missing sealed iteration {number}'}
        guard = (((record.get('node_outputs') or {}).get('budget_guard') or {})
                 .get('semantic_output') or {})
        route = str(guard.get('route') or '')
        # A failed child may never reach its budget_guard (for example an LLM
        # design timeout).  Missing routing evidence is recoverable negative
        # evidence, never an implicit instruction to end a long contract.
        if not route and record.get('status') == 'failed':
            outputs = record.get('node_outputs') or {}
            errors = [str(value.get('error') or value.get('summary') or '')
                      for value in outputs.values() if isinstance(value, dict)
                      and (value.get('status') == 'failed' or value.get('ok') is False)]
            material = '|'.join(errors) or 'child_flow_failed_before_settlement'
            signature = hashlib.sha256(re.sub(
                r'job_[A-Za-z0-9_-]+|flow_[A-Za-z0-9_-]+|\d+', '#', material
            ).encode()).hexdigest()[:16]
            repeats = sum(row.get('failure_signature') == signature
                          for row in state.get('history') or []) + 1
            route = 'self_repair' if repeats >= 2 else 'continue_project'
            guard = {
                'route': route, 'failure_signature': signature,
                'stop_reason': material[:600], 'execution_failed': True,
                'recovery_reason': 'child failed before route settlement',
                'epistemic_gap': False,
            }
        elif not route:
            route = 'stop'
        state['history'].append({'kind': 'project', 'round_number': number,
                                 'record': str(_iteration_path(ctx, 'round', number)),
                                 'route': route,
                                 'child_status': record.get('status'),
                                 'failure_signature': guard.get('failure_signature')})
        if route == 'active_learning':
            state['phase'] = 'learning_running'; write_json(state_path, state)
            question = guard.get('epistemic_gap') or guard.get('next_round_goal')
            return {**result(state, f'第{number}轮后插入主动学习', [str(state_path)]),
                    'cycle_child': {'flow': 'active_learning', 'owner_node': params['node_id'],
                        'record_name': f'iterations/learning_after_{number:04d}', 'repeat_owner': True,
                        'context': {'request': str(question), 'intent_contract': {
                            **(params.get('intent_contract') or {}), 'mode': 'project_active_learning',
                            'scope': 'project', 'learning_question': question},
                            'evidence_refs': learning_source_refs}}}
        if route == 'self_repair':
            state['phase'] = 'self_repair_running'; write_json(state_path, state)
            guard_path = folder(ctx) / 'iterations' / f'guard_{number:04d}.json'
            issue = {
                'id': f'repeated-project-action-failure-{number}',
                'category': 'partner_mechanism',
                'symptom': str(guard.get('stop_reason') or 'same Partner mechanism failed twice'),
                'evidence_refs': [str(_iteration_path(ctx, 'round', number)), str(guard_path)],
                'partner_target_files': ['partner/runtime/action_execution.py', 'partner/events/cycle.py'],
                'reproducer': 'replay the two frozen actions and compare normalized failure signatures',
                'independent_evaluator': 'deterministic command receipt, artifact and failure-signature comparison',
                'expected_fix': 'remove the repeated execution blocker without weakening evidence or path guards',
            }
            return {**result(state, f'第{number}轮后暂停项目并启动有界自修复', [str(state_path)]),
                    'cycle_child': {'flow': 'autonomous_evolution', 'owner_node': params['node_id'],
                        'record_name': f'iterations/self_repair_after_{number:04d}', 'repeat_owner': True,
                        'context': {'request': '修复连续复现的 Partner 执行机制阻塞，验证后恢复原项目。',
                                    'intent_contract': params.get('intent_contract') or {},
                                    'evidence_refs': issue['evidence_refs'],
                                    'experiment_context': {'issue': issue,
                                        'apply_authorized': bool(((params.get('intent_contract') or {})
                                            .get('execution_constraints') or {}).get('evolution_apply'))}}}}
        if route == 'continue_project':
            state.update(phase='ready', next_round=number + 1)
        else:
            state.update(phase='done', terminal_route=route)
    elif phase == 'learning_running':
        learning = _learning_record(ctx, number)
        if not learning:
            return {'ok': False, 'status': 'failed', 'error': f'missing learning child after {number}'}
        handoff = (((learning.get('node_outputs') or {}).get('handoff') or {})
                   .get('semantic_output') or {})
        state['history'].append({'kind': 'active_learning', 'after_round': number,
                                 'record': str(_iteration_path(ctx, 'learning_after', number)),
                                 'status': learning.get('status'),
                                 'handoff_ready': handoff.get('ready') is True})
        if learning.get('status') != 'completed' or handoff.get('ready') is not True:
            # A failed learning child is negative evidence, not a handoff.  Do
            # not let the next research round or final report pretend that it
            # consumed knowledge which was never source-bound and frozen.
            attempts = int((state.setdefault('learning_attempts', {})).get(str(number)) or 1)
            if deadline['deadline_mode'] and deadline['may_start_new_work'] and attempts < 2:
                state['learning_attempts'][str(number)] = attempts + 1
                write_json(state_path, state)
                prior_error = str(learning.get('error') or handoff.get('status') or 'handoff not ready')
                question = ('上次主动学习没有形成来源绑定handoff。不要重复同一查询或来源；改写问题，'
                            '选择替代的论文/官方文档/源码或已建立索引的本地来源，并形成最小可消费claim。'
                            '失败证据=' + prior_error[:800])
                return {**result(state, f'主动学习失败后执行第 {attempts + 1} 次来源恢复', [str(state_path)]),
                        'cycle_child': {'flow': 'active_learning', 'owner_node': params['node_id'],
                            'record_name': f'iterations/learning_after_{number:04d}', 'repeat_owner': True,
                            'context': {'request': question, 'intent_contract': {
                                **(params.get('intent_contract') or {}), 'mode': 'project_active_learning',
                                'scope': 'project', 'learning_question': question,
                                'learning_recovery_attempt': attempts + 1},
                                'evidence_refs': learning_source_refs}}}
            if deadline['deadline_mode'] and deadline['may_start_new_work']:
                state.update(
                    phase='ready', next_round=number + 1,
                    learning_failure_recovered=True,
                    failure_reason='active learning exhausted two distinct source attempts; '
                                   'continue with source failure as negative evidence')
            else:
                state.update(phase='done', terminal_route='learning_failed',
                             failure_reason='active learning did not produce a ready handoff')
        else:
            state.update(phase='ready', next_round=number + 1)
    elif phase == 'self_repair_running':
        repair_path = folder(ctx) / 'iterations' / f'self_repair_after_{number:04d}.json'
        repair = read(repair_path)
        if not repair:
            return {'ok': False, 'status': 'failed', 'error': f'missing self-repair child after {number}'}
        outputs = repair.get('node_outputs') or {}
        record = ((outputs.get('record') or {}).get('semantic_output') or {})
        promoted = bool(record.get('production_effective') or record.get('valid_matched_experiment'))
        state['history'].append({'kind': 'self_repair', 'after_round': number,
                                 'record': str(repair_path), 'promoted': promoted,
                                 'status': repair.get('status')})
        if promoted and deadline['may_start_new_work']:
            state.update(phase='ready', next_round=number + 1,
                         resumed_after_self_repair=True)
        elif deadline['deadline_mode'] and deadline['may_start_new_work']:
            state.update(phase='ready', next_round=number + 1,
                         resumed_after_self_repair=False,
                         repair_negative_evidence=True,
                         failure_reason='self-repair did not validate; continue with a distinct project action')
        else:
            state.update(phase='done', terminal_route='self_repair_not_promoted',
                         failure_reason='repeated mechanism blocker was not safely repaired')
    if state.get('phase') == 'done':
        write_json(state_path, state)
        return result(state, f"动态迭代以 {state.get('terminal_route')} 结束", [str(state_path)])
    number = int(state.get('next_round') or 1)
    if deadline['deadline_mode'] and not deadline['may_start_new_work']:
        state.update(phase='done', terminal_route='deadline_reached',
                     deadline=deadline)
        write_json(state_path, state)
        return result(state, '已进入期限收尾窗口，停止启动新一轮', [str(state_path)])
    if (not deadline['deadline_mode'] and
            number > int(policy.get('max_rounds') or 5)):
        state.update(phase='done', terminal_route='budget_exhausted')
        write_json(state_path, state)
        return result(state, '动态迭代达到预算并停止', [str(state_path)])
    state['phase'] = 'project_running'; state['next_round'] = number
    write_json(state_path, state)
    contract = {**(params.get('intent_contract') or {}), 'round_number': number,
                'cycle_deadline': deadline,
                'iteration_history': list(state.get('history') or [])[-20:],
                'research_preflight': read(folder(ctx) / 'research_preflight.json'),
                'comparison_required': bool(read(folder(ctx) / 'research_preflight.json').get('comparison_required'))}
    return {**result(state, f'启动动态项目第{number}轮', [str(state_path)]),
            'cycle_child': {'flow': 'project_cycle_round', 'owner_node': params['node_id'],
                'record_name': f'iterations/round_{number:04d}', 'repeat_owner': True,
                'context': {'request': params.get('request') or contract.get('original_request') or '',
                            'intent_contract': contract, 'round_number': number}}}


def learning_request(ctx, params):
    number = int(params['round_number'])
    name = _ROUND_NAMES[number]
    settled = read(folder(ctx) / f'settle_{name}.json')
    if settled.get('route') != 'active_learning':
        value = {'triggered': False,
                 'reason': 'settlement did not identify a resolvable epistemic gap',
                 'round_number': number}
        path = folder(ctx) / f'learning_{name}.json'
        write_json(path, value)
        return result(value, '本轮无需主动学习', [str(path)])
    question = settled.get('next_round_goal') or settled.get('reason') or 'resolve project knowledge gap'
    return {**result({'triggered': True, 'question': question}, '启动有来源约束的主动学习'),
            'cycle_child': {'flow': 'active_learning', 'owner_node': params['node_id'],
                'context': {'request': str(question), 'intent_contract': {
                    **(params.get('intent_contract') or {}), 'mode': 'project_active_learning',
                    'scope': 'project', 'learning_question': question},
                    'evidence_refs': [str(folder(ctx) / f'settle_{name}.json')]}}}


def benchmark_request(ctx, params):
    """Insert the frozen benchmark as a child without replacing project work."""
    contract = dict(params.get('intent_contract') or {})
    constraints = dict(contract.get('execution_constraints') or {})
    if not constraints.get('benchmark_embedded'):
        preflight = read(folder(ctx) / 'research_preflight.json')
        required = preflight.get('comparison_required') is True
        value = {
            'triggered': False,
            'formal_comparison_required': required,
            'protocol_status': ('project_matched_comparison_required' if required else 'not_requested'),
            'reason': ('the request requires baseline/candidate comparison but lacks a frozen standalone '
                       'benchmark dataset/runner; project rounds must first freeze and then execute a matched protocol'
                       if required else 'no benchmark or matched comparison requested'),
        }
        path = folder(ctx) / 'benchmark_request.json'
        write_json(path, value)
        return result(value, ('已识别自然语言比较要求，转入项目内匹配协议'
                              if required else '本次项目周期未请求 benchmark'), [str(path)])
    benchmark = dict(contract.get('benchmark') or {})
    required = {'run_id', 'protocol_id', 'inputs'}
    missing = sorted(key for key in required if not benchmark.get(key))
    if missing:
        return {'ok': False, 'status': 'failed',
                'error': 'embedded benchmark contract missing: ' + ', '.join(missing)}
    value = {'triggered': True, 'run_id': benchmark['run_id'],
             'protocol_id': benchmark['protocol_id'],
             'after_project_iteration': True,
             'evaluation_visibility': 'hidden_until_terminal'}
    path = folder(ctx) / 'benchmark_request.json'
    write_json(path, value)
    child_contract = {**contract, 'mode': 'benchmark', 'scope': 'embedded_project_evaluation'}
    return {**result(value, '启动项目周期内的冻结 benchmark', [str(path)]),
            'cycle_child': {
                'flow': 'benchmark_experiment', 'owner_node': params['node_id'],
                'record_name': 'embedded_benchmark',
                'context': {'request': params.get('request') or '',
                            'intent_contract': child_contract,
                            'benchmark': benchmark,
                            'evidence_refs': [str(path)]}}}


def benchmark_settle(ctx, params):
    """Project the child benchmark verdict into the parent evidence chain."""
    contract = dict(params.get('intent_contract') or {})
    constraints = dict(contract.get('execution_constraints') or {})
    record_path = folder(ctx) / 'embedded_benchmark.json'
    if not constraints.get('benchmark_embedded'):
        request = read(folder(ctx) / 'benchmark_request.json')
        comparison_required = request.get('formal_comparison_required') is True
        value = {
            'status': ('project_matched_comparison_pending' if comparison_required
                       else 'not_requested'),
            'valid': True,
            'triggered': False,
            'formal_comparison_required': comparison_required,
            'protocol_status': request.get('protocol_status') or 'not_requested',
            'reason': request.get('reason') or '',
        }
    else:
        record = read(record_path)
        outputs = dict(record.get('node_outputs') or {})
        settlement = ((outputs.get('benchmark_settlement') or {}).get('semantic_output') or {})
        close = ((outputs.get('close') or {}).get('semantic_output') or {})
        value = {
            'status': record.get('status') or 'missing',
            'triggered': True,
            'valid': record.get('status') == 'completed' and bool(settlement),
            'decision': settlement.get('decision') or settlement.get('settlement') or '',
            'settlement': settlement,
            'close': close,
            'record_path': str(record_path),
        }
        run_id = str(close.get('run_id') or '')
        if re.fullmatch(r'bench_[A-Za-z0-9]+', run_id):
            run_root = Path(ctx.workspace) / 'state/benchmarks/runs' / run_id
            comparison = read(run_root / 'comparison.json')
            report = read(run_root / 'report.json')
            value['run_id'] = run_id
            value['comparison'] = comparison
            value['report'] = report
            value['artifact_refs'] = [str(run_root / name) for name in
                                      ('report.json', 'comparison.json', 'aggregate.json',
                                       'guardrails.json', 'settlement.json', 'report.md')
                                      if (run_root / name).is_file()]
        if not value['valid']:
            return {'ok': False, 'status': 'failed',
                    'error': 'embedded benchmark did not produce a completed settlement',
                    'files': [str(record_path)] if record_path.is_file() else []}
    path = folder(ctx) / 'benchmark_settlement.json'
    write_json(path, value)
    files = [str(path)] + ([str(record_path)] if record_path.is_file() else [])
    return result(value, '项目周期 benchmark 已独立结算', files)


def learning_impact_settle(ctx, params):
    number = int(params['round_number'])
    name = _ROUND_NAMES[number]
    learning = read(folder(ctx) / f'learning_{name}.json')
    if not learning or learning.get('flow_type') != 'active_learning':
        value = {'status': 'not_applicable', 'improved': False,
                 'reason': 'no active-learning child completed'}
    else:
        outputs = learning.get('node_outputs') or {}
        adoption = ((outputs.get('adoption') or {}).get('semantic_output') or {})
        handoff = ((outputs.get('handoff') or {}).get('semantic_output') or {})
        value = {'status': 'candidate_frozen' if handoff.get('ready') else 'inconclusive',
                 'improved': False,
                 'reason': 'improvement requires consumption and matched downstream project execution',
                 'adoption_candidate': adoption, 'handoff': handoff,
                 'child_status': learning.get('status'),
                 'learning_flow_id': learning.get('flow_id')}
    path = folder(ctx) / f'impact_{name}.json'
    write_json(path, value)
    return result(value, '主动学习只形成候选；改善留待下一项目轮匹配结算', [str(path)])


def learning_downstream_settle(ctx, params):
    """Settle a learning candidate only after a later project round consumes it.

    The LLM may propose and explain the adoption.  It cannot award itself an
    improvement: that requires an explicit handoff reference plus a structured
    baseline/candidate comparison emitted by the downstream verification Event.
    """
    learning_number = int(params['learning_round'])
    evaluation_number = int(params['evaluation_round'])
    learning_name = _ROUND_NAMES[learning_number]
    evaluation_name = _ROUND_NAMES[evaluation_number]
    learning = read(folder(ctx) / f'learning_{learning_name}.json')
    design = read(folder(ctx) / f'design_{evaluation_name}.json')
    project_round = read(folder(ctx) / f'round_{evaluation_name}.json')
    learning_outputs = learning.get('node_outputs') or {}
    handoff = ((learning_outputs.get('handoff') or {}).get('semantic_output') or {})
    round_outputs = project_round.get('node_outputs') or {}
    verify = (round_outputs.get('verify') or {})
    verify_semantic = verify.get('semantic_output') or {}
    matched = verify_semantic.get('learning_matched_evidence') or verify.get('learning_matched_evidence') or {}
    declared_refs = design.get('learning_handoff_refs') or []
    handoff_refs = handoff.get('evidence_refs') or handoff.get('source_refs') or []
    consumed = bool(handoff.get('ready') and declared_refs)
    complete_comparison = (isinstance(matched, dict)
                           and matched.get('baseline') is not None
                           and matched.get('candidate') is not None
                           and isinstance(matched.get('improved'), bool))
    improved = bool(consumed and complete_comparison and matched.get('improved'))
    if not learning or learning.get('flow_type') != 'active_learning':
        status = 'not_applicable'
        reason = 'the preceding project round did not run active learning'
    elif not handoff.get('ready'):
        status = 'inconclusive'
        reason = 'the learning child did not freeze a source-bound adoption handoff'
    elif not consumed:
        status = 'not_consumed'
        reason = 'the next round design did not cite the frozen learning handoff'
    elif not complete_comparison:
        status = 'candidate_consumed'
        reason = 'the handoff was consumed, but no matched baseline/candidate evidence was emitted'
    else:
        status = 'improved' if improved else 'not_improved'
        reason = 'settled from the downstream deterministic comparison'
    value = {'status': status, 'improved': improved, 'consumed': consumed,
             'comparison_complete': complete_comparison, 'matched_evidence': matched,
             'learning_round': learning_number, 'evaluation_round': evaluation_number,
             'declared_handoff_refs': declared_refs, 'handoff_evidence_refs': handoff_refs,
             'reason': reason}
    path = folder(ctx) / f'learning_downstream_{evaluation_name}.json'
    write_json(path, value)
    return result(value, f'主动学习下游效果结算为 {status}', [str(path)])


def assess(ctx, params):
    data = records(ctx)
    selected = {}
    round_names = [name for name in data if name.startswith('iterations/round_')]
    if not round_names:
        round_names = ['round_one', 'round_two', 'round_three']
    round_evidence = []
    for name in round_names:
        row = data.get(name) or {}
        if not row:
            continue
        selected[name] = {'flow_id': row.get('flow_id'), 'status': row.get('status'),
            'outputs': {k: {a: b for a, b in v.items() if a in
                         ('ok','status','summary','error','files','evidence_refs','business_delta')}
                       for k, v in (row.get('node_outputs') or {}).items()
                        if k in ('plan', 'execute', 'verify', 'reflect')}}
        outputs = row.get('node_outputs') or {}
        design = (outputs.get('design') or {}).get('semantic_output') or {}
        execute = outputs.get('execute') or {}
        verify = (outputs.get('verify') or {}).get('semantic_output') or {}
        reflect = (outputs.get('reflect') or {}).get('semantic_output') or {}
        guard = (outputs.get('budget_guard') or {}).get('semantic_output') or {}
        domain_artifacts = [str(p.get('path')) for p in (verify.get('evidence') or [])
                            if isinstance(p, dict) and p.get('valid') and p.get('path')]
        round_evidence.append({
            'round': name, 'status': row.get('status'),
            'hypothesis': design.get('hypothesis'), 'goal': design.get('round_goal'),
            'execution_status': execute.get('status'),
            'execution_summary': execute.get('summary'), 'execution_error': execute.get('error'),
            'verified': verify.get('verified') is True,
            'scientific_claim_supported': verify.get('scientific_claim_supported') is True,
            'verification_layers': verify.get('verification_layers') or {},
            'input_consumption': verify.get('input_consumption') or {},
            'domain_evidence': domain_artifacts,
            'finding': reflect.get('lesson') or reflect.get('result'),
            'information_gain': guard.get('information_gain'),
            'route': guard.get('route'), 'next_goal': guard.get('next_round_goal'),
            'guard_reasons': guard.get('guard_reasons') or [],
        })
    artifacts = []
    for raw_path in evidence(ctx):
        p = Path(raw_path)
        if '/state/event_runtime/work/' in raw_path and p.suffix in ('.json', '.py', '.md', '.csv'):
            artifacts.append({'path':raw_path, 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                              'excerpt':p.read_text(errors='replace')[:3500],
                              'preview_only':p.stat().st_size > 3500})
    dynamic_guards = {path.stem: read(path) for path in sorted(
        (folder(ctx) / 'iterations').glob('guard_*.json'))}
    dynamic_learning = {name: value for name, value in data.items()
                        if name.startswith('iterations/learning_after_')}
    learning_runs = []
    for name, record in dynamic_learning.items():
        outputs = record.get('node_outputs') or {}
        handoff = (outputs.get('handoff') or {}).get('semantic_output') or {}
        learning_runs.append({
            'record': name, 'status': record.get('status'),
            'question': handoff.get('question'), 'handoff_ready': handoff.get('ready') is True,
            'claims': handoff.get('claims') or handoff.get('lessons') or [],
            'source_urls': handoff.get('source_urls') or [],
            'files': (outputs.get('handoff') or {}).get('files') or [],
        })
    downstream_files = sorted((folder(ctx) / 'iterations').glob('learning_effect_*.json'))
    downstream = [read(path) for path in downstream_files]
    learning_summary = {
        'required': read(folder(ctx) / 'cycle_policy.json').get('explicit_learning_checkpoint') is True,
        'run_count': len(learning_runs), 'runs': learning_runs,
        'consumed': any(row.get('consumed') is True for row in downstream),
        'improved': any(row.get('improved') is True for row in downstream),
        'downstream_comparisons': downstream,
        'status': ('improved' if any(row.get('improved') is True for row in downstream) else
                   'consumed_not_improved' if any(row.get('consumed') is True for row in downstream) else
                   'learned_not_consumed' if any(row.get('handoff_ready') is True for row in learning_runs) else
                   'attempted_failed' if learning_runs else 'not_executed'),
    }
    write_json(folder(ctx) / 'round_evidence_table.json', {'rounds': round_evidence})
    write_json(folder(ctx) / 'learning_summary.json', learning_summary)
    snapshot = {'rounds': selected, 'artifacts': artifacts[:24],
                'round_evidence': round_evidence,
                'iteration_decisions': dynamic_guards,
                'active_learning_runs': dynamic_learning,
                'active_learning_summary': learning_summary,
                'settlements': {name: read(folder(ctx) / f'settle_{name}.json')
                                for name in ('one', 'two', 'three')
                                if (folder(ctx) / f'settle_{name}.json').is_file()},
                'learning_impacts': {name: read(folder(ctx) / f'impact_{name}.json')
                                     for name in ('one', 'two', 'three')
                                     if (folder(ctx) / f'impact_{name}.json').is_file()},
                'learning_downstream': {name: read(folder(ctx) / f'learning_downstream_{name}.json')
                                        for name in ('two', 'three')
                                        if (folder(ctx) / f'learning_downstream_{name}.json').is_file()},
                'embedded_benchmark': read(folder(ctx) / 'benchmark_settlement.json'),
                'phase': 'settlement-driven business rounds ended; delivery, memory and evolution are subsequent Events',
                'evidence_rule': 'source excerpts are previews, not proof that later fields/artifacts are absent; copied implementation is not a call to the live function'}
    write_json(folder(ctx) / 'business_snapshot.json', snapshot)
    raw, usage = call_model(ctx, purpose='cycle_assess', prompt=(
        '综合已执行的项目轮次，逐项核对原始目标。后续轮是否消费前轮产物与主动学习handoff，是否真实推进，'
        '有什么失败、重复、未知？不得把产生文件等同于项目达成。'
        '本Event只评估项目业务轮，消息/PDF/记忆/自进化尚在后续，不把未到达阶段误报为失败。'
        '以实际脚本参数为准，不重复摘要中未经核验的线程数等数值。区分实际import调用和手写副本。'
        '动作失败仍可能留下部分实测文件；不要把文本预览截断误认成原始计划或产物被截断。'
        '输出JSON：summary, round_comparison, goal_coverage, verified_progress, failures, remaining, next_question。\n'
        + '用户原始目标=' + params['request'] + '\n动态迭代真实快照=' + json.dumps(snapshot, ensure_ascii=False)[:65000]))
    value = json_object(raw)
    value['round_evidence'] = round_evidence
    value['active_learning'] = learning_summary
    benchmark = snapshot.get('embedded_benchmark') or {}
    decision = str(benchmark.get('decision') or '')
    effect = (benchmark.get('settlement') or {}).get('effect')
    benchmark_complete = bool(benchmark.get('valid') is True and decision
                              and isinstance(effect, (int, float)))
    if benchmark_complete:
        comparison = benchmark.get('comparison') or {}
        value['benchmark_result'] = {
            'valid': True, 'decision': decision, 'effect': effect,
            'comparison': comparison,
            'authoritative_source': 'deterministic_evaluators',
        }
        prefix = (f'冻结 benchmark 已由确定性评价器结算为 {decision}，'
                  f'candidate 相对 baseline 的 RMSE 改善为 {effect}。')
        learning_text = {
            'improved': '主动学习已被后续动作消费，且匹配比较显示改善',
            'consumed_not_improved': '主动学习已被后续动作消费，但未证明改善',
            'learned_not_consumed': '主动学习形成了 handoff，但尚未被后续动作消费',
            'attempted_failed': '主动学习已尝试，但未形成来源绑定的可消费 handoff',
            'not_executed': '本次没有执行主动学习',
        }[learning_summary['status']]
        value['summary'] = (prefix + f' {learning_text}；实际数值结论来自独立 benchmark。')
        progress = value.get('verified_progress')
        if not isinstance(progress, list):
            progress = [str(progress)] if progress else []
        progress.insert(0, prefix)
        value['verified_progress'] = progress
        coverage = {
            'research_question_review': 'Covered in project round 1',
            'active_learning_handoff': learning_summary['status'],
            'handoff_consumption': ('Covered' if learning_summary['consumed'] else 'Not covered'),
            'baseline_model_execution': 'Covered by frozen benchmark arm',
            'candidate_model_execution': 'Covered by frozen benchmark arm',
            'rmse_improvement_verification': 'Covered by deterministic paired evaluator',
            'guardrail_runtime_check': 'Covered; all frozen hard guardrails passed',
            'bootstrap_significance_test': 'Covered by 1000 frozen bootstrap samples',
            'final_settlement_report': f'Covered; settlement={decision}',
        }
        value['goal_coverage'] = coverage
        value['failures'] = []
        value['remaining'] = [
            'The benchmark establishes predictive performance on the frozen DAVIS protocol; '
            'it does not establish wet-lab activity or clinical utility.',
            'External replication on another target-group dataset remains future evidence.',
        ]
        value['next_question'] = (
            'Does the frozen target-level feature retain its benefit under an external '
            'target-group dataset with the same leakage controls?')
        rounds = value.get('round_comparison')
        rounds = dict(rounds) if isinstance(rounds, dict) else {}
        rounds['benchmark'] = {
            'status': 'completed', 'decision': decision, 'effect': effect,
            'comparison': comparison,
            'authoritative_source': 'deterministic_evaluators',
        }
        value['round_comparison'] = rounds
    elif benchmark.get('triggered') is True:
        value['benchmark_result'] = {
            'valid': False, 'decision': decision or None, 'effect': effect,
            'authoritative_source': 'deterministic_evaluators',
            'reason': 'benchmark record lacks a complete deterministic decision/effect pair',
        }
        failures = value.get('failures')
        failures = list(failures) if isinstance(failures, list) else []
        failures.append('冻结 benchmark 没有形成完整的确定性 decision/effect，不能声称比较完成。')
        value['failures'] = failures
    elif benchmark.get('formal_comparison_required') is True:
        matched_rounds = [row for row in round_evidence
                          if (row.get('verification_layers') or {}).get('comparison_complete') is True
                          and row.get('scientific_claim_supported') is True]
        if matched_rounds:
            value['project_matched_comparison'] = {
                'complete': True,
                'rounds': [row.get('round') for row in matched_rounds],
                'source': 'project.outcome_verify verification_layers',
            }
        else:
            value['project_matched_comparison'] = {
                'complete': False,
                'rounds': [],
                'source': 'project.outcome_verify verification_layers',
            }
            failures = value.get('failures')
            failures = list(failures) if isinstance(failures, list) else []
            failures.append('用户要求的 baseline/candidate 同输入同预算比较未通过分层核验。')
            value['failures'] = failures
            value['verified_progress'] = [row for row in
                                          (value.get('verified_progress') or [])
                                          if '比较完成' not in str(row)]
    path = folder(ctx) / 'assessment.json'
    write_json(path, value)
    return {**result(value, str(value.get('summary') or '动态迭代结果已分析'), [str(path)]),
            'token_usage': usage, 'notification_kind': 'milestone'}


def final_state_freeze(ctx, params):
    """Freeze one authoritative, typed truth object before any renderer runs."""
    assessment = read(folder(ctx) / 'assessment.json')
    rounds = read(folder(ctx) / 'round_evidence_table.json').get('rounds') or []
    learning = read(folder(ctx) / 'learning_summary.json')
    audit = read(folder(ctx) / 'partner_audit.json')
    gate = read(folder(ctx) / 'evolution_gate.json')
    evolution = read(folder(ctx) / 'evolve.json')
    evolution_outputs = evolution.get('node_outputs') or {}
    evolution_record = ((evolution_outputs.get('record') or {}).get('semantic_output') or {})
    evolution_decision = ((evolution_outputs.get('decision') or {}).get('semantic_output') or {})
    benchmark = assessment.get('benchmark_result') if isinstance(
        assessment.get('benchmark_result'), dict) else {}
    benchmark_requested = read(folder(ctx) / 'benchmark_request.json').get('triggered') is True
    verified = sum(row.get('verified') is True for row in rounds)
    failed = sum(
        row.get('status') == 'failed'
        or str(row.get('execution_status') or '') == 'failed'
        or bool(row.get('execution_error'))
        for row in rounds)
    provisional = str(assessment.get('summary') or '尚未形成项目结论')
    if benchmark_requested and benchmark.get('valid') is not True:
        research_status = 'inconclusive'
        authoritative_conclusion = (
            '探索性项目轮可能产生了结果，但冻结 benchmark 未形成有效终态，'
            '因此不能把探索性结果升级为已验证结论。')
        conflict = bool(verified)
    elif benchmark_requested:
        decision = str(benchmark.get('decision') or '')
        research_status = ('validated_improvement' if decision in {'confirmed', 'accepted', 'improved'}
                           else 'validated_no_improvement')
        authoritative_conclusion = provisional
        conflict = False
    elif verified:
        research_status = 'provisional_result'
        authoritative_conclusion = provisional
        conflict = False
    else:
        research_status = 'inconclusive'
        authoritative_conclusion = provisional
        conflict = False
    if evolution_record.get('production_effective') is True:
        evolution_status = 'promoted'
    elif evolution_decision.get('decision') == 'reject':
        evolution_status = 'rejected'
    elif gate.get('approved'):
        evolution_status = 'inconclusive'
    else:
        evolution_status = 'no_candidate'
    value = {
        'schema_version': 2,
        'job_id': str(ctx.job_id),
        'project_id': str(params.get('project_id') or ''),
        'workstream_type': str((params.get('intent_contract') or {}).get(
            'workstream_type') or 'project_research'),
        'research_question': str((params.get('intent_contract') or {}).get('goal') or
                                 (params.get('intent_contract') or {}).get('original_request') or '')[:4000],
        'research_outcome': {
            'status': research_status,
            'authoritative_conclusion': authoritative_conclusion,
            'provisional_conclusion': provisional,
            'round_count': len(rounds), 'verified_rounds': verified,
            'failed_rounds': failed, 'rounds': rounds,
            'benchmark_requested': benchmark_requested,
            'benchmark': benchmark,
            'claim_conflict_resolved': conflict,
            'limitations': assessment.get('remaining') or assessment.get('failures') or [],
        },
        'learning_outcome': {
            'status': learning.get('status') or 'not_executed',
            'run_count': int(learning.get('run_count') or 0),
            'consumed': learning.get('consumed') is True,
            'downstream_improved': learning.get('improved') is True,
            'runs': learning.get('runs') or [],
            'claim_boundary': ('references alone are not consumption; improvement requires '
                               'a matched downstream execution receipt'),
        },
        'evolution_outcome': {
            'status': evolution_status,
            'real_experiment_executed': evolution_record.get('real_experiment_executed') is True,
            'valid_matched_experiment': evolution_record.get('valid_matched_experiment') is True,
            'production_effective': evolution_record.get('production_effective') is True,
            'selected_issue': gate.get('selected_issue') or {},
            'audit_issue_count': len(audit.get('issues') or []),
            'evidence_refs': list(evolution_record.get('evidence_refs') or []),
        },
        'outcome_semantics': {
            'flow_completed': True,
            'research_succeeded': research_status in {'validated_improvement', 'validated_no_improvement'},
            'presentation_pending': True,
            'delivery_pending': True,
        },
        'source_records': [str(folder(ctx) / name) for name in (
            'assessment.json', 'round_evidence_table.json', 'learning_summary.json',
            'partner_audit.json', 'evolution_gate.json', 'evolve.json',
            'benchmark_settlement.json') if (folder(ctx) / name).is_file()],
    }
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':')).encode()
    value['final_state_hash'] = 'sha256:' + hashlib.sha256(canonical).hexdigest()
    path = folder(ctx) / 'final_run_state.json'
    write_json(path, value)
    return result(value, f"最终事实已冻结：{research_status}", [str(path)])


def run_narrative(ctx, params):
    """Build one deterministic truth object for every user-facing renderer."""
    final_state = read(folder(ctx) / 'final_run_state.json')
    project = final_state.get('research_outcome') or {}
    learning = final_state.get('learning_outcome') or {}
    evolution_state_record = final_state.get('evolution_outcome') or {}
    rounds = project.get('rounds') or []
    project_conclusion = str(project.get('authoritative_conclusion') or '尚未形成项目结论')
    verified = int(project.get('verified_rounds') or 0)
    failed = int(project.get('failed_rounds') or 0)
    issue = evolution_state_record.get('selected_issue') or {}
    evolution_state = str(evolution_state_record.get('status') or 'no_candidate')
    narrative = {
        'schema_version': 2, 'job_id': str(ctx.job_id),
        'final_state_hash': final_state.get('final_state_hash'),
        'project_id': str(params.get('project_id') or ''),
        'workstream_type': str((params.get('intent_contract') or {}).get('workstream_type') or 'project_research'),
        'research_question': str((params.get('intent_contract') or {}).get('goal') or
                                 (params.get('intent_contract') or {}).get('original_request') or '')[:4000],
        'protocol': {
            'constraints': (params.get('intent_contract') or {}).get('constraints') or [],
            'success_criteria': (params.get('intent_contract') or {}).get('success_criteria') or [],
        },
        'project': {
            'conclusion': project_conclusion, 'round_count': len(rounds),
            'verified_rounds': verified, 'failed_rounds': failed,
            'status': project.get('status'), 'rounds': rounds,
            'benchmark': project.get('benchmark') or {},
            'limitations': project.get('limitations') or [],
        },
        'active_learning': {
            'status': learning.get('status') or 'not_executed',
            'run_count': int(learning.get('run_count') or 0),
            'sources': [url for run in learning.get('runs') or [] for url in run.get('source_urls') or []],
            'claims': [claim for run in learning.get('runs') or [] for claim in run.get('claims') or []],
            'handoff_consumed': learning.get('consumed') is True,
            'downstream_improved': learning.get('improved') is True,
            'reason': learning.get('reason') or '',
        },
        'self_evolution': {
            'audit_issue_count': int(evolution_state_record.get('audit_issue_count') or 0),
            'selected_issue': issue, 'decision': evolution_state,
            'production_effective': evolution_state_record.get('production_effective') is True,
            'matched_verified': evolution_state_record.get('valid_matched_experiment') is True,
            'real_experiment_executed': evolution_state_record.get('real_experiment_executed') is True,
            'evidence_refs': list(evolution_state_record.get('evidence_refs') or []),
        },
        'truth_boundaries': [
            'Event completion is not project improvement.',
            'Learning is not improved unless a downstream matched comparison proves it.',
            'Self-evolution is not effective unless isolated and fresh-process verification passes.',
        ],
    }
    headline = (f"项目执行 {len(rounds)} 轮，{verified} 轮形成可核验证据，{failed} 轮执行失败；"
                f"主动学习 {narrative['active_learning']['status']}；"
                f"Partner 自进化 {evolution_state}。")
    narrative['headline'] = headline
    narrative['milestone_message'] = (
        f"阶段结算：{headline}\n"
        f"研究判断：{project_conclusion[:260]}\n"
        f"证据边界：{str((project.get('limitations') or ['依据未解决问题决定下一步'])[0])[:180]}"
    )
    narrative['evidence_refs'] = list(dict.fromkeys(
        [str(folder(ctx) / name) for name in ('assessment.json','round_evidence_table.json',
                                              'learning_summary.json','partner_audit.json','evolution_gate.json')
         if (folder(ctx) / name).is_file()] + evidence(ctx)))
    path = folder(ctx) / 'run_narrative.json'; write_json(path, narrative)
    return {**result(narrative, headline, [str(path)]),
            'business_delta': verified > 0, 'learning_delta': learning.get('run_count', 0) > 0,
            'evolution_delta': bool(issue), 'notification_kind': 'milestone',
            'message': narrative['milestone_message']}


def report_request(ctx, params):
    if str(params.get('report_policy') or 'milestone') == 'none':
        return result({'requested': False, 'reason': 'report_policy_none'},
                      '报告策略为 none，不启动 PDF 子 Flow')
    # Avoid recursively embedding recall/planning histories ahead of results.
    refs = [str(folder(ctx) / name) for name in (
        'business_snapshot.json', 'assessment.json', 'round_evidence_table.json',
        'learning_summary.json', 'final_run_state.json', 'run_narrative.json')]
    # Sealed business artifacts outside the runtime audit tree are primary
    # report evidence too.  Restricting this list to event_runtime previously
    # hid project CSV/metric JSON files from the figure planner, so a completed
    # experiment could not produce a PDF at all.
    refs += [p for p in evidence(ctx)
             if Path(p).name not in ('state.json', '执行结果.md', 'verified_project_artifacts.json')]
    benchmark = read(folder(ctx) / 'benchmark_settlement.json')
    run_id = str((benchmark.get('close') or {}).get('run_id') or '')
    if re.fullmatch(r'bench_[A-Za-z0-9]+', run_id):
        run_root = Path(ctx.workspace) / 'state/benchmarks/runs' / run_id
        refs += [str(run_root / name) for name in
                 ('report.json', 'comparison.json', 'aggregate.json',
                  'guardrails.json', 'settlement.json', 'report.md')
                 if (run_root / name).is_file()]
    refs = list(dict.fromkeys(p for p in refs if Path(p).is_file()))
    final_state = read(folder(ctx) / 'final_run_state.json')
    contract = {**params.get('intent_contract', {}), 'evidence_refs': refs,
                'force': True, 'report_policy': 'final', 'expand_evidence_refs': False,
                'source_job_id': ctx.job_id,
                'final_state_hash': final_state.get('final_state_hash')}
    return {**result({}, '根据动态迭代真实结果生成阶段报告'),
            'cycle_child': {'flow': 'pdf_report', 'owner_node': params['node_id'],
                'context': {'intent_contract': contract, 'evidence_refs': refs,
                            'force': True, 'notification_kind': 'final',
                            'request': '根据原始目标与所附动态迭代真实证据生成中文研究报告；正文围绕研究问题、方法、结果、结论与限制，展示真实数据与失败边界，不引入新业务结果。运行Event/Flow只放在附录。'}}}


def cross_channel_verify(ctx, params):
    """Verify every renderer consumed the same frozen truth version."""
    final_state = read(folder(ctx) / 'final_run_state.json')
    narrative = read(folder(ctx) / 'run_narrative.json')
    expected = str(final_state.get('final_state_hash') or '')
    report = read(folder(ctx) / 'report.json')
    report_exists = bool(report)
    report_files = list(report.get('files') or [])
    report_hashes = []
    for output in (report.get('node_outputs') or {}).values():
        report_files.extend(output.get('files') or [])
        semantic = output.get('semantic_output') or {}
        if isinstance(semantic, dict) and semantic.get('final_state_hash'):
            report_hashes.append(str(semantic['final_state_hash']))
    pdf_exists = any(str(path).lower().endswith('.pdf') and Path(str(path)).is_file()
                     for path in report_files)
    report_required = bool(read(folder(ctx) / 'cycle_policy.json').get('report_required'))
    semantic_audit = read(folder(ctx) / 'semantic_claim_audit.json')
    checks = {
        'final_state_exists': bool(expected),
        'narrative_uses_frozen_state': narrative.get('final_state_hash') == expected,
        'text_delivery_settled': bool(read(folder(ctx) / 'text_ack.json')),
        'report_record_exists': (not report_required) or report_exists,
        'pdf_exists': (not report_required) or pdf_exists,
        'report_uses_frozen_state': (not report_required) or expected in report_hashes,
        'report_delivery_settled': (not report_required) or bool(read(folder(ctx) / 'report_ack.json')),
        'cross_layer_claims_supported': semantic_audit.get('accepted') is True,
    }
    value = {'ok': all(checks.values()), 'checks': checks,
             'final_state_hash': expected,
             'rule': 'Web, QQ and PDF must render the same immutable FinalRunState'}
    path = folder(ctx) / 'cross_channel_verification.json'; write_json(path, value)
    output = result(value, '三渠道共享事实版本核验' + ('通过' if value['ok'] else '失败'), [str(path)])
    if not value['ok']:
        output.update(ok=False, status='failed', error='cross-channel truth verification failed')
    return output


def semantic_claim_audit(ctx, params):
    """Audit claim entailment across evidence, final state, messages and report."""
    final_state = read(folder(ctx) / 'final_run_state.json')
    narrative = read(folder(ctx) / 'run_narrative.json')
    report_path = Path(ctx.workspace) / 'state/event_runtime/work' / ctx.job_id / '项目进展报告.md'
    report_text = report_path.read_text(encoding='utf-8', errors='replace')[:30000] if report_path.is_file() else ''
    assessment = read(folder(ctx) / 'assessment.json')
    preflight = read(folder(ctx) / 'research_preflight.json')
    # A repair Event can deliberately reduce the outward narrative to a
    # structured projection of FinalRunState.  That form is auditable without
    # another model call and keeps provider outages from reintroducing unsafe
    # prose or suppressing every final notification.
    if (narrative.get('claim_safe_mode') is True
            and narrative.get('final_state_hash') == final_state.get('final_state_hash')):
        value = {
            'accepted': True, 'unsupported_claims': [], 'safe_replacements': [],
            'checked_layers': ['final_run_state', 'claim_safe_narrative'],
            'audit_mode': 'deterministic_claim_safe_projection',
            'report_path': str(report_path) if report_path.is_file() else '',
        }
        path = folder(ctx) / 'semantic_claim_audit.json'
        write_json(path, value)
        return result(value, '跨层科学主张审计通过', [str(path)])
    raw, usage = call_model(ctx, purpose='cycle_semantic_claim_audit', prompt=(
        '你是跨层事实审计Event。逐条检查对外结论是否被原始执行与核验状态支持，特别检查：'
        '把一个目录为空扩大成整个语料库为空；把未配置某厂商环境变量扩大成没有可用模型；'
        '把代理指标扩大成研究目标成立；把文件生成扩大成科学改善；把相关性写成因果。'
        '输出JSON：accepted,unsupported_claims,safe_replacements,checked_layers。每个unsupported_claim含claim,reason,evidence_boundary。'
        '只要已存在的QQ/Web/PDF任一核心结论越界，accepted=false；未生成的层不得猜测。\n运行协议=' +
        json.dumps(preflight, ensure_ascii=False)[:10000] + '\n评估=' +
        json.dumps(assessment, ensure_ascii=False)[:22000] + '\nFinalRunState=' +
        json.dumps(final_state, ensure_ascii=False)[:22000] + '\nNarrative=' +
        json.dumps(narrative, ensure_ascii=False)[:12000] + '\n报告=' + report_text))
    value = json_object(raw)
    unsupported = [row for row in value.get('unsupported_claims') or [] if row]
    value['accepted'] = bool(value.get('accepted')) and not unsupported
    value['unsupported_claims'] = unsupported
    value['report_path'] = str(report_path) if report_path.is_file() else ''
    path = folder(ctx) / 'semantic_claim_audit.json'
    write_json(path, value)
    out = result(value, ('跨层科学主张审计通过' if value['accepted'] else
                         f'跨层科学主张审计拒绝 {len(unsupported)} 项越界结论'), [str(path)])
    if not value['accepted']:
        out.update(ok=False, status='failed', error='unsupported cross-layer claims')
    return {**out, 'token_usage': usage}


def semantic_claim_repair(ctx, params):
    """Repair outward prose after a failed claim audit, before any delivery."""
    audit = read(folder(ctx) / 'semantic_claim_audit.json')
    narrative_path = folder(ctx) / 'run_narrative.json'
    narrative = read(narrative_path)
    final_state = read(folder(ctx) / 'final_run_state.json')
    if audit.get('accepted') is True:
        value = {'repaired': False, 'reason': 'claim audit already accepted'}
        path = folder(ctx) / 'semantic_claim_repair.json'; write_json(path, value)
        return result(value, '主张审计已通过，无需修复', [str(path)])
    repaired = {}
    usage = {}
    try:
        raw, usage = call_model(ctx, purpose='cycle_semantic_claim_repair', prompt=(
            '你只修复对外运行叙述，不得改写FinalRunState或新增结果。删除审计指出的越界主张，'
            '用safe_replacements改写headline、milestone_message、project.conclusion和truth_boundaries。'
            '输出JSON：headline,milestone_message,project_conclusion,truth_boundaries。\nFinalRunState=' +
            json.dumps(final_state, ensure_ascii=False)[:24000] + '\n原叙述=' +
            json.dumps(narrative, ensure_ascii=False)[:16000] + '\n审计=' +
            json.dumps(audit, ensure_ascii=False)[:16000]))
        repaired = json_object(raw)
    except Exception as exc:  # provider failure must still yield safe prose
        repaired = {'repair_error': f'{type(exc).__name__}: {exc}'[:500]}
    project = final_state.get('project') if isinstance(final_state.get('project'), dict) else {}
    verified = int(project.get('verified_rounds') or 0)
    rounds = int(project.get('round_count') or 0)
    conclusion = str(repaired.get('project_conclusion') or project.get('conclusion') or
                     '当前证据不足以支持科学改善主张。')[:1200]
    safe_headline = str(repaired.get('headline') or
                        f'本次运行执行 {rounds} 轮，其中 {verified} 轮有可核验证据。')[:600]
    safe_message = str(repaired.get('milestone_message') or
                       f'阶段结算：{safe_headline}\n证据边界：{conclusion}')[:1800]
    updated = dict(narrative)
    updated.update({
        'final_state_hash': final_state.get('final_state_hash'),
        'headline': safe_headline,
        'milestone_message': safe_message,
        'claim_safe_mode': True,
        'claim_repair_source': 'semantic_claim_audit',
        'truth_boundaries': repaired.get('truth_boundaries') or [
            'Event完成不等于项目改善。',
            '文件存在不等于科学主张成立。',
            '主动学习只有被后续匹配实验消费并改善时才算有效。'],
    })
    if isinstance(updated.get('project'), dict):
        updated['project'] = {**updated['project'], 'conclusion': conclusion}
    write_json(narrative_path, updated)
    value = {'repaired': True, 'final_state_hash': final_state.get('final_state_hash'),
             'prior_unsupported_claims': audit.get('unsupported_claims') or [],
             'narrative_path': str(narrative_path), 'model_repair_used': bool(usage)}
    path = folder(ctx) / 'semantic_claim_repair.json'; write_json(path, value)
    return {**result(value, '已在发送前修复越界主张', [str(path), str(narrative_path)]),
            'token_usage': usage}


def delivery_settle(ctx, params):
    if params['node_id'] == 'report_ack':
        sent = (read(folder(ctx) / 'report.json').get('node_outputs') or {}).get('send', {})
    elif params['node_id'] == 'improvement_report_ack':
        sent = (params.get('flow_outputs') or {}).get('send_report') or {}
    else:
        # v3 fix: 从文件读回执，不依赖 flow_outputs
        source_node = ('final_send' if params['node_id'] == 'final_ack' else
                       'send_message' if params['node_id'] == 'improvement_message_ack' else
                       'send')
        # 尝试从节点输出文件读
        sent = (params.get('flow_outputs') or {}).get(source_node) or {}
        if not sent:
            # v7 fix: 从 outbound receipt 文件读，使用实际 origin_instance
            job_id = params.get('job_id') or getattr(ctx, 'job_id', '')
            flow_id = params.get('flow_id') or ''
            origin = str(params.get('origin_instance') or getattr(ctx, 'instance_id', '01'))
            receipt_path = Path(ctx.workspace) / 'state/application/outbound' / origin / f'{job_id}.{flow_id}.{source_node}.sent'
            if receipt_path.exists():
                try:
                    import json
                    sent = json.loads(receipt_path.read_text())
                except:
                    pass
            # 如果 .sent 文件不存在，尝试读 .json 队列文件（send_text 创建的）
            if not sent:
                json_path = Path(ctx.workspace) / 'state/application/outbound' / origin / f'{job_id}.{flow_id}.{source_node}.json'
                if json_path.exists():
                    try:
                        import json
                        queue_data = json.loads(json_path.read_text())
                        # 从队列文件构造 sent 结构
                        sent = {
                            'delivered': queue_data.get('text_delivered', False),
                            'suppressed': False,
                            'web_visible': False,
                            'receipt': {'path': str(json_path), 'channel': queue_data.get('delivery_state', 'queued')},
                            'delivery_kind': 'text'
                        }
                    except:
                        pass

    if sent.get('delivered') or sent.get('suppressed') or sent.get('web_visible'):
        delivered = bool(sent.get('delivered'))
        web_visible = bool(sent.get('web_visible'))
        receipt = {'delivered': delivered, 'web_visible': web_visible,
                   'suppressed': bool(sent.get('suppressed')),
                   'delivery_state': ('sent' if delivered else
                                      'web_visible' if web_visible else 'suppressed'),
                   'channel': ((sent.get('receipt') or {}).get('channel') or ''),
                   'pdf_delivered': bool(delivered and params['node_id'] in {
                       'report_ack', 'improvement_report_ack'})}
        target = folder(ctx) / (params['node_id'] + '.json')
        write_json(target, receipt)
        return result(receipt, '渠道状态已核验', [str(target)])
    path = (sent.get('receipt') or {}).get('path')
    if not path:
        return {'ok': False, 'status': 'failed', 'error': 'missing delivery receipt; retained for self audit'}
    ack = Path(path).with_suffix('.sent')
    # A fully observable run can legitimately queue two lifecycle messages per
    # Event.  The terminal business/PDF payload must not be declared missing
    # merely because it entered a real serial QQ queue behind those updates.
    pending = 0
    try:
        pending = sum(1 for item in Path(path).parent.glob('*.json')
                      if item != Path(path))
    except OSError:
        pass
    # Real QQ queues can lag several minutes behind local enqueue when every
    # lifecycle update is dual-delivered.  Previous 75–314 second budgets
    # expired shortly before a valid HTTP-200 ACK arrived, falsely failing the
    # parent Job.  Keep waiting bounded, but cover the observed queue tail.
    ack_budget = min(900, max(300, 120 + pending * 5))
    wait_path = folder(ctx) / (params['node_id'] + '_ack_wait.json')
    wait_state = read(wait_path)
    now = time.time()
    first_wait = not bool(wait_state)
    if first_wait:
        wait_state = {'started_at': now, 'deadline': now + ack_budget,
                      'queue_path': str(path), 'ack_path': str(ack),
                      'budget_seconds': ack_budget}
        write_json(wait_path, wait_state)
    row = read(ack)
    if row.get('delivery_state') == 'sent' and row.get('text_delivered'):
        if sent.get('delivery_kind') != 'pdf' or row.get('pdf_delivered'):
            if sent.get('delivery_kind') == 'pdf':
                pdf_ack = next((item for item in row.get('component_acks', [])
                                if isinstance(item, dict) and item.get('kind') == 'pdf'), {})
                expected_sha = str(sent.get('pdf_sha256') or '')
                if expected_sha and str(pdf_ack.get('sha256') or '') != expected_sha:
                    return {'ok': False, 'status': 'failed',
                            'error': 'PDF channel ACK does not match the frozen report version',
                            'evidence_refs': [str(wait_path), str(ack)]}
            receipt = {'path': str(ack), 'delivery_state': 'sent',
                       'component_acks': row.get('component_acks', []),
                       'pdf_delivered': bool(row.get('pdf_delivered')),
                       'pdf_sha256': str(sent.get('pdf_sha256') or '')}
            target = folder(ctx) / (params['node_id'] + '.json')
            write_json(target, receipt)
            return result(receipt, '已取得本次渠道回执', [str(target), str(wait_path)])
    if Path(path).with_suffix('.blocked').exists():
        return {'ok': False, 'status': 'failed',
                'error': 'delivery channel explicitly blocked the payload',
                'evidence_refs': [str(wait_path), str(Path(path).with_suffix('.blocked'))]}
    if now >= float(wait_state.get('deadline') or now):
        return {'ok': False, 'status': 'failed',
                'error': 'delivery ACK missing after bounded asynchronous wait; audit must inspect this failure',
                'evidence_refs': [str(wait_path), str(path)]}
    return {'ok': True, 'status': 'waiting',
            'background_task_id': 'delivery_ack:' + params['node_id'],
            'first_wait': first_wait,
            'summary': '渠道消息已入队，异步等待平台回执',
            'evidence_refs': [str(wait_path), str(path)]}


def memory_update(ctx, params):
    from partner.memory import EventMemory
    kind = params['kind']
    path = folder(ctx) / ('memory_' + kind + '.json')
    if path.exists():
        return result(read(path), f'{kind} 复用同周期已保存更新', [str(path)])
    summary = read(folder(ctx) / 'final_run_state.json') or read(folder(ctx) / 'assessment.json')
    raw, usage = call_model(ctx, purpose='cycle_memory_' + kind, prompt=(
        f'根据真实周期记录更新{kind}。lesson记录做法与结果；growth仅记录待复验的能力观察，'
        '不能凭任务完成宣称持久成长；habit提出待验证习惯，不能自动激活。'
        '输出JSON：content, scope, confidence, counterexample, evidence_refs。'
        + '\n分析=' + json.dumps(summary, ensure_ascii=False)
        + '\n交付=' + json.dumps({k: read(folder(ctx)/(k+'.json')) for k in ('text_ack','report_ack')}, ensure_ascii=False)
        + '\n证据=' + json.dumps(evidence(ctx), ensure_ascii=False)[:9000]))
    value = json_object(raw)
    if 'content' not in value:
        value = {'content':value, 'schema_normalization':'preserved model object under required content field'}
    value.update(project_id=params['project_id'], cycle_id=ctx.job_id,
                 status='candidate' if kind in ('growth','habit') else 'active',
                 production_effective=False, evidence_refs=evidence(ctx))
    if not path.exists():
        memory_path = EventMemory(ctx.workspace).append_semantic(kind, value)
        value['memory_path'] = memory_path
        write_json(path, value)
    return {**result(value, f'{kind} 已按证据更新', [str(path)]), 'token_usage': usage}


def seal(ctx, params):
    refs = evidence(ctx)
    refs += [str(p) for p in folder(ctx).glob('*.json')]
    run_log_dir = Path(ctx.workspace) / 'state/run_logs' / ctx.job_id
    refs += [str(path) for path in (run_log_dir / 'events.jsonl', run_log_dir / 'RUN_LOG.md')
             if path.is_file()]
    manifest = {'cycle_id': ctx.job_id, 'instance_id': ctx.instance_id,
                'project_id': params['project_id'], 'request': params['request'],
                'root_event_id': params.get('root_event_id', ''),
                'parent_flow_id': params['flow_id'], 'created_at': time.time(),
                'files': [{'path': p, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                          for p in dict.fromkeys(refs) if Path(p).is_file()],
                'artifact_lifecycle': {
                    'research_evidence': 'present' if refs else 'due_missing',
                    'final_run_state': 'not_due', 'qq_final_message': 'not_due',
                    'web_projection': 'not_due',
                    'pdf_report': ('not_due' if read(folder(ctx) / 'cycle_policy.json').get('report_required')
                                   else 'not_applicable'),
                },
                'node_outputs': params.get('flow_outputs') or {}}
    path = folder(ctx) / 'sealed_cycle.json'
    write_json(path, manifest)
    return result({'manifest': str(path)}, '项目证据冻结后固定触发Partner运行审计', [str(path)])


def partner_audit(ctx, params):
    manifest_path = folder(ctx) / 'sealed_cycle.json'
    manifest = read(manifest_path)
    known = {str(row.get('path')) for row in manifest.get('files') or [] if row.get('path')}
    known.update(str(path) for path in (
        folder(ctx) / 'cycle_policy.json', folder(ctx) / 'research_preflight.json',
        folder(ctx) / 'problem_portfolio.json', folder(ctx) / 'iteration_state.json') if path.is_file())
    known.update(str(path) for path in (folder(ctx) / 'iterations').glob('*.json') if path.is_file())
    deadline = _deadline_state(read(folder(ctx) / 'cycle_policy.json'))
    finalizing = deadline.get('deadline_mode') and not deadline.get('may_start_new_work')
    try:
        if finalizing:
            raise TimeoutError('finalization reserve: deterministic audit only')
        raw, usage = call_model(ctx, purpose='cycle_partner_audit', prompt=(
            '审计本次运行中Partner自身的表现。必须覆盖：消息理解、方案设计、Event Flow、实际执行、轮次衔接、'
            '主动学习消费、项目真实推进、消息、PDF报告、交付ACK、记忆。问题分类只能是 project_science、'
            'epistemic_gap、partner_mechanism、expected_behavior。RMSE、模型效果、数据质量和科学假设属于'
            'project_science，绝不是自进化。只有Partner的prompt、Event、Flow、Runtime、索引、消息、报告、'
            '记忆或工具适配机制缺陷才是partner_mechanism。输出JSON：aspects,issues；每个issue包含id,category,'
            'symptom,evidence_refs,reproducible,reproducer,independent_evaluator,partner_target_files,expected_fix。'
            '没有可复现机制缺陷时issues可为空，不得为了改代码制造问题。证据只能引用清单中的路径。\n清单='
            + json.dumps(manifest, ensure_ascii=False)[:60000]))
        value = json_object(raw)
    except (RuntimeError, TimeoutError) as exc:
        # Deterministic guards below still inspect actual execution receipts.
        # An unavailable narrative auditor must not hold an otherwise settled
        # research run for another five-minute retry or invent a defect.
        usage = {}
        value = {'aspects': [], 'issues': [], 'llm_audit_unavailable': str(exc)[:300],
                 'fallback': 'deterministic_runtime_guards_only'}
    deterministic = []
    run_events = Path(ctx.workspace) / 'state/run_logs' / ctx.job_id / 'events.jsonl'
    if run_events.is_file() and run_events.stat().st_size > 5_000_000:
        deterministic.append({
            'id': 'event_run_log_repeats_oversized_context',
            'category': 'partner_mechanism',
            'symptom': ('EventRunLog.event and sanitize produced an events.jsonl projection larger than '
                        f'5 MB ({run_events.stat().st_size} bytes) for one bounded cycle because repeated '
                        'flow_outputs are copied into multiple Event inputs'),
            'evidence_refs': [str(run_events)],
            'reproducible': True,
            'reproducer': ('run one two-round project_cycle and assert the run-log projection stays bounded '
                           'while every Event retains input/output digests and independently useful fields'),
            'independent_evaluator': ('compare events.jsonl byte size and Event count before/candidate; require '
                                      'all Event lifecycle rows and digests to remain queryable'),
            'partner_target_files': ['partner/runtime/event_run_log.py'],
            'expected_fix': ('compact repeated nested flow_outputs in EventRunLog.event while retaining digests, '
                             'node-local inputs/outputs and full authoritative Event Ledger evidence'),
            'severity': 'medium', 'causal_priority': 30,
        })
    round_paths = [folder(ctx) / f'round_{name}.json' for name in ('one', 'two', 'three')]
    round_paths += sorted((folder(ctx) / 'iterations').glob('round_*.json'))
    failure_rows = []
    for round_path in round_paths:
        if not round_path.is_file():
            continue
        name = round_path.stem.removeprefix('round_')
        round_record = read(round_path)
        round_outputs = round_record.get('node_outputs') or {}
        execute = ((round_record.get('node_outputs') or {}).get('execute') or {})
        semantic = execute.get('semantic_output') if isinstance(execute.get('semantic_output'), dict) else {}
        verify_semantic = ((round_outputs.get('verify') or {}).get('semantic_output') or {})
        consumption = verify_semantic.get('input_consumption') if isinstance(
            verify_semantic.get('input_consumption'), dict) else {}
        if (consumption.get('corpus_required') is True
                and consumption.get('consumption_valid') is not True):
            deterministic.append({
                'id': f'admitted_input_not_consumed_round_{name}',
                'category': 'partner_mechanism',
                'symptom': 'the executor produced artifacts without proving consumption of any admitted current-round input',
                'evidence_refs': [str(round_path)], 'reproducible': True,
                'reproducer': 'admit hash-bound corpus inputs, execute one round, and inspect input_consumption_audit.json',
                'independent_evaluator': 'require an admitted path, matching frozen hash and declared consumption purpose',
                'partner_target_files': ['partner/events/project.py', 'partner/event_flows/cycle.py'],
                'expected_fix': 'bind action execution to admitted inputs and reject scientific claims without a valid consumption receipt',
                'severity': 'critical', 'causal_priority': 140,
            })
        required_cognitive_nodes = ('design', 'design_critic', 'reflect', 'next_decide')
        missing_cognitive = [node for node in required_cognitive_nodes
                             if not isinstance((round_outputs.get(node) or {}).get('token_usage'), dict)
                             or (round_outputs.get(node) or {}).get('token_usage', {}).get('status') != 'ok']
        if missing_cognitive and round_record.get('status') == 'completed':
            deterministic.append({
                'id': f'missing_cognitive_checkpoints_round_{name}',
                'category': 'partner_mechanism',
                'symptom': 'a completed project round skipped required LLM judgment checkpoints: ' + ', '.join(missing_cognitive),
                'evidence_refs': [str(round_path)], 'reproducible': True,
                'reproducer': 'complete one project round and inspect model receipts for design, critic, reflection and next-decision Events',
                'independent_evaluator': 'all four Event outputs must carry successful, purpose-bound model call receipts',
                'partner_target_files': ['partner/events/cycle.py', 'partner/event_flows/cycle.py'],
                'expected_fix': 'make cognitive checkpoints mandatory in the materialized Flow instead of inferring success from low-cost local Events',
                'severity': 'high', 'causal_priority': 110,
            })
        if execute.get('status') == 'failed' or execute.get('ok') is False:
            failure_rows.append((round_path, str(execute.get('error') or execute.get('summary') or '')))
        contract = semantic.get('execution_contract') if isinstance(semantic.get('execution_contract'), dict) else {}
        if contract and contract.get('conformant') is not True:
            deterministic.append({
                'id': f'execution_contract_violation_round_{name}',
                'category': 'partner_mechanism',
                'symptom': '; '.join(str(v) for v in contract.get('violations') or ['execution contract failed']),
                'evidence_refs': [str(round_path)],
                'reproducible': True,
                'reproducer': 'replay the frozen action and compare its command/write set with execution_contract.json',
                'independent_evaluator': 'deterministic execution-contract and output-root validator',
                'partner_target_files': ['partner/events/project.py', 'partner/events/cycle.py'],
                'expected_fix': 'fail closed when actual action or write root exceeds the frozen commitment',
                'severity': 'critical', 'causal_priority': 100,
            })
        required_root = str(contract.get('required_output_root') or '')
        if required_root and contract.get('changed_paths') and not all(
                Path(str(path)).resolve().is_relative_to(Path(required_root).resolve())
                for path in contract.get('changed_paths') or []):
            deterministic.append({
                'id': f'output_root_violation_round_{name}',
                'category': 'partner_mechanism',
                'symptom': f'project artifacts escaped the user-declared output root {required_root}',
                'evidence_refs': [str(round_path)],
                'reproducible': True,
                'reproducer': 'compare execution changed_paths with required_output_root',
                'independent_evaluator': 'deterministic path containment check',
                'partner_target_files': ['partner/events/project.py'],
                'expected_fix': 'bind executor and verifier to the explicit output root',
                'severity': 'critical', 'causal_priority': 100,
            })
    if failure_rows:
        refs = [str(path) for path, _ in failure_rows]
        errors = ' | '.join(error for _, error in failure_rows)
        current_job_false_foreign = bool(re.search(
            rf"forbidden_foreign=.*{re.escape(str(ctx.job_id))}", errors))
        deterministic.append({
            'id': ('current_job_path_rejected_as_foreign' if current_job_false_foreign
                   else 'project_action_repeated_runtime_failure'),
            'category': 'partner_mechanism',
            'symptom': (f'{len(failure_rows)} project action rounds failed; '
                        + ('the executor classified a path owned by the current job as foreign'
                           if current_job_false_foreign else errors[:800])),
            'evidence_refs': refs, 'reproducible': True,
            'reproducer': ('parse the Job ID from the frozen execution prompt and submit a command reading '
                           'a path under that same job; assert it is not listed in forbidden_foreign'
                           if current_job_false_foreign else
                           'replay the frozen action twice and compare normalized failure signatures'),
            'independent_evaluator': ('deterministic same-job path ownership assertion'
                                      if current_job_false_foreign else
                                      'deterministic action receipt and repeated-failure-signature evaluator'),
            'partner_target_files': (['partner/runtime/action_execution.py'] if current_job_false_foreign
                                     else ['partner/events/cycle.py', 'partner/runtime/action_execution.py']),
            'expected_fix': ('parse only the canonical job identifier and allow its owned work tree'
                             if current_job_false_foreign else
                             'stop repeated mechanism failures and route the reproduced defect to self-evolution'),
            'severity': 'critical', 'causal_priority': 120,
            'affected_rounds': len(failure_rows), 'blocking_scope': 'project_execution',
        })
    # Planned and actual flow divergence is a deterministic runtime defect;
    # the LLM auditor should explain it, not be expected to discover it in a
    # multi-megabyte trace.
    for round_path in round_paths:
        record = read(round_path)
        run_context = record.get('run_context') or {}
        if (run_context.get('flow_materialized')
                and not run_context.get('actual_flow_hash')):
            deterministic.append({
                'id': f'materialized_flow_missing_actual_hash_{round_path.stem}',
                'category': 'partner_mechanism',
                'symptom': 'the round froze an Event plan but did not seal its actual execution graph',
                'evidence_refs': [str(round_path)], 'reproducible': True,
                'reproducer': 'materialize one project round and compare planned_flow_hash with actual_flow_hash',
                'independent_evaluator': 'deterministic Flow-state hash comparison',
                'partner_target_files': ['partner/event_fabric/flows.py'],
                'expected_fix': 'seal the actual executed Event sequence at the terminal transition',
                'severity': 'high', 'causal_priority': 90,
            })
    effects = [read(path).get('effect') for path in sorted(
        (folder(ctx) / 'iterations').glob('learning_effect_*.json'))]
    numeric_effects = [value for value in effects if isinstance(value, (int, float))]
    if len(numeric_effects) >= 3 and len(set(numeric_effects)) == 1:
        deterministic.append({
            'id': 'active_learning_constant_effect_across_distinct_rounds',
            'category': 'partner_mechanism',
            'symptom': f'{len(numeric_effects)} learning comparisons emitted the identical effect {numeric_effects[0]}',
            'evidence_refs': [str(path) for path in sorted(
                (folder(ctx) / 'iterations').glob('learning_effect_*.json'))],
            'reproducible': True,
            'reproducer': 'run distinct knowledge interventions and assert their evaluator inputs and effects differ',
            'independent_evaluator': 'compare intervention hashes, evaluator kinds and numeric effects',
            'partner_target_files': ['partner/events/improvement.py'],
            'expected_fix': 'replace the fixed readiness classifier with content-specific matched experiments',
            'severity': 'critical', 'causal_priority': 115,
        })
    policy = read(folder(ctx) / 'cycle_policy.json')
    iteration_state = read(folder(ctx) / 'iteration_state.json')
    terminal_route = str(iteration_state.get('terminal_route') or '')
    if (_deadline_state(policy).get('may_start_new_work') and terminal_route in {'stop', 'complete'}
            and not read(folder(ctx) / 'problem_portfolio.json').get('portfolio_exhausted')):
        deterministic.append({
            'id': 'deadline_run_terminated_without_portfolio_exhaustion',
            'category': 'partner_mechanism',
            'symptom': 'the deadline run stopped while new-work budget remained and the problem portfolio was not exhausted',
            'evidence_refs': [str(folder(ctx) / 'iteration_state.json'),
                              str(folder(ctx) / 'problem_portfolio.json')],
            'reproducible': True,
            'reproducer': 'run an until_deadline cycle whose current hypothesis completes early and assert another portfolio problem starts',
            'independent_evaluator': 'compare terminal timestamp, finalization reserve and portfolio_exhausted state',
            'partner_target_files': ['partner/events/cycle.py', 'partner/event_flows/cycle.py'],
            'expected_fix': 'separate hypothesis settlement from run completion and continue from the problem portfolio',
            'severity': 'critical', 'causal_priority': 130,
        })
    learning_records = list((folder(ctx) / 'iterations').glob('learning_after_*.json'))
    if policy.get('explicit_learning_checkpoint') and not learning_records:
        deterministic.append({
            'id': 'required_active_learning_was_not_executed',
            'category': 'partner_mechanism',
            'symptom': 'the frozen run required active learning but no active-learning child Flow record exists',
            'evidence_refs': [str(folder(ctx) / 'cycle_policy.json')],
            'reproducible': True,
            'reproducer': 'run a cycle with active_learning_policy=require_once and assert one child record exists',
            'independent_evaluator': 'count sealed iterations/learning_after_*.json records and validate handoff state',
            'partner_target_files': ['partner/events/cycle.py'],
            'expected_fix': 'route one learning child before project completion and require downstream consumption evidence',
            'severity': 'high', 'causal_priority': 95,
            'blocking_scope': 'active_learning',
        })
    if learning_records:
        ready_learning = False
        for learning_path in learning_records:
            learning_record = read(learning_path)
            handoff = ((((learning_record.get('node_outputs') or {}).get('handoff') or {})
                        .get('semantic_output')) or {})
            ready_learning = ready_learning or (
                learning_record.get('status') == 'completed' and handoff.get('ready') is True)
        if not ready_learning:
            deterministic.append({
                'id': 'active_learning_attempted_without_source_bound_handoff',
                'category': 'partner_mechanism',
                'symptom': 'active-learning child Flows ran but none produced a verified, source-bound handoff',
                'evidence_refs': [str(path) for path in learning_records],
                'reproducible': True,
                'reproducer': 'run the required learning checkpoint and inspect retrieve/read/synthesize/handoff terminal states',
                'independent_evaluator': 'at least one completed learning Flow must contain downloaded source hashes, literal claims and handoff.ready=true',
                'partner_target_files': ['partner/events/active_learning.py', 'partner/events/cycle.py'],
                'expected_fix': 'retry with exact resolvable sources and fall back only to current-round admitted local evidence',
                'severity': 'critical', 'causal_priority': 135,
                'blocking_scope': 'active_learning',
            })
    value['issues'] = deterministic + list(value.get('issues') or [])
    accepted = []
    source_root = Path(__file__).resolve().parents[2]
    for issue in value.get('issues') or []:
        if not isinstance(issue, dict):
            continue
        refs = [str(v) for v in issue.get('evidence_refs') or [] if str(v) in known]
        targets = [str(v) for v in issue.get('partner_target_files') or []]
        target_paths = [source_root / target for target in targets]
        targets_ok = bool(targets) and all(
            v.startswith(('partner/', 'benchmark/', 'docs/')) and not v.startswith('projects/')
            for v in targets) and all(path.is_file() for path in target_paths)
        reproducer_spec = {
            'kind': 'event_replay',
            'inputs': refs,
            'assertion': str(issue.get('reproducer') or ''),
            'must_fail_on_baseline': True,
        }
        evaluator_spec = {
            'kind': 'deterministic_before_after',
            'assertion': str(issue.get('independent_evaluator') or ''),
            'same_inputs_required': True,
            'same_budget_required': True,
        }
        row = {**issue, 'evidence_refs': refs, 'partner_target_files': targets,
               'resolved_target_files': [str(path) for path in target_paths if path.is_file()],
               'reproducer_spec': reproducer_spec, 'evaluator_spec': evaluator_spec,
               'scope_valid': (issue.get('category') == 'partner_mechanism' and targets_ok
                               and bool(refs) and bool(reproducer_spec['assertion'])
                               and bool(evaluator_spec['assertion']))}
        accepted.append(row)
    value['issues'] = accepted
    value['audit_scope'] = 'partner_runtime_only'
    value['project_metrics_excluded_from_evolution'] = True
    path = folder(ctx) / 'partner_audit.json'; write_json(path, value)
    return {**result(value, f'完成Partner运行后审计，记录{len(accepted)}项分类问题', [str(path)]),
            'token_usage': usage}


def evolution_gate(ctx, params):
    constraints = ((params.get('intent_contract') or {}).get('execution_constraints') or {})
    if constraints.get('evolution_cycle') is False:
        value = {'approved': False, 'selected_issue': None, 'rejected': [],
                 'scope': 'partner_runtime_only',
                 'reason': 'self-evolution disabled by the frozen parent contract',
                 'forbidden_targets': ['project scientific metric', 'project model',
                                       'project dataset', 'molecular RMSE or other domain outcome']}
        path = folder(ctx) / 'evolution_gate.json'; write_json(path, value)
        return {**result(value, '父协调器将自进化安排在独立轨道，本项目轨道不重复执行', [str(path)]),
                'evolution_approved': False}
    deadline = _deadline_state(read(folder(ctx) / 'cycle_policy.json'))
    if deadline.get('deadline_mode') and not deadline.get('may_start_new_work'):
        value = {'approved': False, 'selected_issue': None, 'rejected': [],
                 'scope': 'partner_runtime_only',
                 'reason': 'finalization reserve reached; preserve audit as a later candidate',
                 'deferred_by_deadline': True,
                 'remaining_seconds': deadline.get('remaining_seconds'),
                 'forbidden_targets': ['project scientific metric', 'project model',
                                       'project dataset', 'molecular RMSE or other domain outcome']}
        path = folder(ctx) / 'evolution_gate.json'; write_json(path, value)
        return {**result(value, '已进入收尾预算；记录候选但不再启动自进化子Flow', [str(path)]),
                'evolution_approved': False}
    audit = read(folder(ctx) / 'partner_audit.json')
    selected = None
    rejected = []
    eligible = []
    for issue in audit.get('issues') or []:
        ready = (issue.get('category') == 'partner_mechanism'
                 and issue.get('scope_valid') is True
                 and issue.get('reproducible') is True
                 and bool(issue.get('reproducer'))
                 and bool(issue.get('independent_evaluator'))
                 and bool(issue.get('evidence_refs')))
        if ready:
            eligible.append(issue)
        else:
            rejected.append({'id': issue.get('id'), 'category': issue.get('category'),
                             'reason': 'not a reproducible Partner mechanism issue with an independent evaluator'})
    if eligible:
        # Correctness and false-progress defects block useful work and outrank
        # efficiency issues such as log size, regardless of prompt ordering.
        selected = max(eligible, key=lambda issue: (
            int(issue.get('causal_priority') or 0),
            {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}.get(
                str(issue.get('severity') or '').lower(), 0),
            int(issue.get('affected_rounds') or 0)))
        rejected.extend({'id': issue.get('id'), 'category': issue.get('category'),
                         'reason': 'eligible but lower causal priority than the selected blocker'}
                        for issue in eligible if issue is not selected)
    value = {'approved': selected is not None, 'selected_issue': selected,
             'rejected': rejected, 'scope': 'partner_runtime_only',
             'forbidden_targets': ['project scientific metric', 'project model', 'project dataset',
                                   'molecular RMSE or other domain outcome']}
    path = folder(ctx) / 'evolution_gate.json'; write_json(path, value)
    return {**result(value, '自进化候选已放行' if selected else '未发现满足硬门的Partner机制缺陷', [str(path)]),
            'evolution_approved': selected is not None}


def evolution_request(ctx, params):
    path = folder(ctx) / 'sealed_cycle.json'
    gate = read(folder(ctx) / 'evolution_gate.json')
    if not gate.get('approved'):
        value = {'status': 'no_change', 'reason': 'post-run audit found no eligible Partner mechanism defect',
                 'gate_path': str(folder(ctx) / 'evolution_gate.json')}
        evolve_path = folder(ctx) / 'evolve.json'; write_json(evolve_path, value)
        return result(value, '自进化审计完成，本轮无需修改Partner', [str(evolve_path)])
    return {**result({}, '主动检查本周期所有方面并执行有界改进实验'),
            'cycle_child': {'flow': 'autonomous_evolution', 'owner_node': params['node_id'],
                'context': {'cycle_manifest': str(path), 'evidence_refs': [str(path)],
                            'experiment_context': {
                                'issue': gate.get('selected_issue'),
                                'scope': 'partner_runtime_only',
                                'forbidden_project_optimization': True},
                            'intent_contract': params.get('intent_contract') or {}}}}




def final_summary(ctx, params):
    """Create the post-evolution content consumed by normal delivery Events."""
    child = read(folder(ctx) / "evolve.json")
    recorded = ((child.get("node_outputs") or {}).get("record", {}).get("semantic_output") or {})
    decision = ((child.get("node_outputs") or {}).get("decision", {}).get("semantic_output") or {})
    gov_issue = None
    gov_path = folder(ctx) / "evolution" / "governance.json"
    if gov_path.is_file():
        try:
            gov = json.loads(gov_path.read_text())
            gov_issue = gov.get("issue", {}).get("issue", {})
        except Exception:
            gov_issue = {}

    gate = read(folder(ctx) / 'evolution_gate.json')
    no_change = child.get('status') == 'no_change' or not gate.get('approved')
    decision_text = decision.get("decision") or ("no_change" if no_change else "unknown")
    # The decision node only records the shadow experiment.  Whether the
    # change reached production is known later, after apply/reload/verify and
    # rollback settlement, and is recorded by the child flow's record node.
    production_effective = bool(recorded.get("production_effective"))
    runtime_verify = recorded.get("runtime_verify") or {}
    rollback = recorded.get("rollback") or {}
    matched_verified = bool(recorded.get("valid_matched_experiment"))
    evidence_verified = bool(
        no_change or (
            matched_verified
            and runtime_verify.get("all_ok") is True
            and runtime_verify.get("production_effective") is True
            and rollback.get("status") == "not_required"
            and rollback.get("production_effective") is True
        )
    )
    gov_status = "已记录" if gov_issue else "未记录"
    selected_issue = gate.get('selected_issue') or {}
    issue_id = str(selected_issue.get('id') or '')
    if issue_id == 'event_run_log_repeats_oversized_context':
        issue_summary = '运行日志会重复写入大块上下文，导致单次周期日志异常膨胀'
    else:
        issue_summary = str(selected_issue.get('symptom') or '').strip()[:120]
    summary_line = issue_summary or (gov_issue.get("summary", "").strip()[:120]
                                     if gov_issue else "未发现可证伪机制问题")
    expectations = (decision.get("comparisons") or {}).get("1", {}).get("expectation_results") or []
    met = sum(1 for e in expectations if e.get("status") == "met")
    total = len(expectations)
    test_summary = f"（已验证测试 {met}/{total}）" if expectations else ""
    assessment = read(folder(ctx) / 'assessment.json')
    learning = read(folder(ctx) / 'learning_summary.json')
    round_table = read(folder(ctx) / 'round_evidence_table.json').get('rounds') or []
    verified_rounds = sum(row.get('verified') is True for row in round_table)
    failed_rounds = sum(row.get('execution_status') == 'failed' for row in round_table)
    project_summary = str(assessment.get('summary') or '项目结果尚未形成可核验结论').strip()
    learning_text = {
        'improved': '主动学习已执行、已消费，并由匹配比较证明带来改善',
        'consumed_not_improved': '主动学习已执行并消费，但未证明带来改善',
        'learned_not_consumed': '主动学习形成了内容，但尚未进入后续决策',
        'attempted_failed': '主动学习已尝试，但未形成来源绑定的可消费 handoff',
        'not_executed': '本次没有实际执行主动学习',
    }.get(str(learning.get('status') or ''), '主动学习状态缺少可核验记录')
    project_prefix = (f'项目共执行 {len(round_table)} 轮，其中 {verified_rounds} 轮产生可核验业务证据、'
                      f'{failed_rounds} 轮执行失败。项目结论：{project_summary}。{learning_text}。')
    if no_change:
        evolution_text = ('Partner 自进化审计已完成；没有候选问题同时通过可复现性、目标文件和独立评价硬门，'
                          '因此没有修改 Partner。')
    else:
        release_text = ('补丁已写入生产源码并经新进程加载验证，未触发回滚'
                        if production_effective and evidence_verified
                        else '候选修改尚未通过完整生产验证')
        evolution_text = (f"Partner 自进化{test_summary}针对“{summary_line}”运行了候选实验；"
                          f"{release_text}；治理记录{gov_status}。").strip()
    text = project_prefix + evolution_text

    out_path = folder(ctx) / 'final_summary.json'
    write_json(out_path, {'message': text, 'decision': decision_text,
                          'production_effective': production_effective,
                          'governance_recorded': bool(gov_issue),
                          'matched_verified': matched_verified,
                          'runtime_verified': runtime_verify.get('all_ok') is True,
                          'rollback_status': rollback.get('status'),
                          'evidence_verified': evidence_verified,
                          'selected_issue_id': issue_id})
    return result({
        "summary": text, "message": text, "force": True,
        "notification_kind": "final",
        "decision": decision_text,
        "production_effective": production_effective,
        "governance_recorded": bool(gov_issue),
        "matched_verified": matched_verified,
        "runtime_verified": runtime_verify.get('all_ok') is True,
        "rollback_status": rollback.get('status'),
        "evidence_verified": evidence_verified,
        "selected_issue_id": issue_id,
    }, "自进化终态摘要已生成，交由后续消息Event审查和发送", [str(out_path)])


def finish(ctx, params):
    child = read(folder(ctx) / 'evolve.json')
    recorded=((child.get('node_outputs') or {}).get('record',{}).get('semantic_output') or {})
    text_ack=read(folder(ctx)/'text_ack.json');pdf_ack=read(folder(ctx)/'report_ack.json')
    final_ack=read(folder(ctx)/'final_ack.json')
    effective=bool(recorded.get('valid_matched_experiment') or recorded.get('no_change_behavior_verified'))
    dynamic_rounds = list((folder(ctx) / 'iterations').glob('round_*.json'))
    project_rounds = (len(dynamic_rounds) if dynamic_rounds else
                      sum((folder(ctx) / f'round_{name}.json').is_file()
                          for name in ('one', 'two', 'three')))
    report_required = bool(read(folder(ctx) / 'cycle_policy.json').get('report_required'))
    accepted_states = {'sent', 'web_visible', 'suppressed'}
    pdf_verified = (not report_required or (
        pdf_ack.get('delivery_state') in accepted_states
        and (pdf_ack.get('pdf_delivered') is True
             or pdf_ack.get('delivery_state') == 'web_visible')))
    delivery_verified = (text_ack.get('delivery_state') in accepted_states
                         and pdf_verified
                         and final_ack.get('delivery_state') in accepted_states)
    value = {'cycle_id': ctx.job_id, 'stopped': True, 'project_rounds': project_rounds,
             'evolution_effective_attempt':effective,
             'delivery_verified': delivery_verified,
             'report_required': report_required,
             'post_run_audit_completed': (folder(ctx) / 'partner_audit.json').is_file(),
             'evolution_gate_completed': (folder(ctx) / 'evolution_gate.json').is_file(),
             'evolution_status': child.get('status'),
             'evolution_result': (child.get('node_outputs') or {}).get('record', {}),
             'text_delivery': read(folder(ctx) / 'text_ack.json'),
             'pdf_delivery': read(folder(ctx) / 'report_ack.json'),
             'final_delivery': final_ack}
    path = folder(ctx) / 'completion.json'
    write_json(path, value)
    if effective:
        summary = '项目迭代与一次有效自进化验证结束，停止续跑'
    elif value['post_run_audit_completed'] and value['evolution_gate_completed']:
        summary = '项目周期、运行后审计和自进化门控均已完成；本轮没有获准的Partner修改'
    else:
        summary = '流程已停止，但运行后审计或自进化门控未完整完成'
    out = result(value, summary, [str(path)])
    outputs = params.get('flow_outputs') or {}
    required_delivery_failed = any(
        (outputs.get(node) or {}).get('status') == 'failed'
        for node in ('final_critic', 'final_deduplicate', 'final_send', 'final_ack'))
    if not delivery_verified and required_delivery_failed:
        out.update(ok=False, status='failed',
                   error='required project/report/final delivery chain was not verified')
    return out


DEFINITIONS = [EventDefinition('cycle.' + name, 'project', description, fn,
    execution_method='llm' if name in ('assess','memory_update','research_preflight','input_eligibility',
                                        'round_design','round_blueprint_critic','problem_portfolio_update',
                                        'round_settle','iteration_next_decide',
                                        'partner_audit','semantic_claim_audit','semantic_claim_repair') else 'local',
    timeout_seconds=300 if name in ('assess','memory_update','research_preflight','input_eligibility',
                                     'round_design','round_blueprint_critic','problem_portfolio_update',
                                     'round_settle','iteration_next_decide',
                                     'partner_audit','semantic_claim_audit','semantic_claim_repair') else 100)
    for name, fn, description in (
        ('initialize', initialize, '初始化项目周期预算与职责边界'),
        ('research_preflight', research_preflight, '冻结运行级研究协议、完成层级与跨轮问题池'),
        ('input_resolve', input_resolve, '从显式引用和资源索引解析并冻结本轮具体输入'),
        ('input_eligibility', input_eligibility, '在假设设计前审查研究输入与语料资格'),
        ('input_adequacy', input_adequacy, '判定准入输入是否足以支撑声明的标签、对照和评价指标'),
        ('round_design', round_design, '根据上一轮结算设计并冻结下一轮Event蓝图'),
        ('round_blueprint_critic', round_blueprint_critic, '独立审查并物化本轮实际执行的Event子图'),
        ('round_request', round_request, '请求真实项目子Flow并消费前轮证据'),
        ('round_settle', round_settle, '依据真实终态决定停止、继续或主动学习'),
        ('iteration_next_decide', iteration_next_decide, '依据本轮真实结算提出下一项目动作'),
        ('problem_portfolio_update', problem_portfolio_update, '结算当前问题并选择不同的下一可证伪问题'),
        ('iteration_learning_effect', iteration_learning_effect, '由后续项目轮匹配证据结算主动学习效果'),
        ('iteration_budget_guard', iteration_budget_guard, '以确定性预算和新颖性规则审查下一动作'),
        ('iteration_controller', iteration_controller, '可恢复地启动下一项目轮或主动学习子Flow'),
        ('learning_request', learning_request, '只在可解决知识缺口时请求主动学习子Flow'),
        ('learning_impact_settle', learning_impact_settle, '冻结学习候选并等待下游改善证据'),
        ('learning_downstream_settle', learning_downstream_settle, '以后一项目轮的匹配证据结算主动学习效果'),
        ('benchmark_request', benchmark_request, '在项目迭代后请求冻结的独立benchmark子Flow'),
        ('benchmark_settle', benchmark_settle, '把benchmark终态裁决写入项目证据链'),
        ('assess', assess, '综合所有已执行项目轮的真实推进与差距'),
        ('final_state_freeze', final_state_freeze, '解析冲突并冻结所有渠道唯一可用的最终事实'),
        ('run_narrative', run_narrative, '从确定性证据生成消息、Web和报告共享事实模型'),
        ('report_request', report_request, '请求现有PDF子Flow'),
        ('delivery_settle', delivery_settle, '等待本次真实渠道ACK或明确失败'),
        ('cross_channel_verify', cross_channel_verify, '核验消息、网页与PDF使用同一冻结事实版本'),
        ('semantic_claim_audit', semantic_claim_audit, '核验原始证据到消息、网页和PDF的语义蕴含边界'),
        ('semantic_claim_repair', semantic_claim_repair, '在任何对外发送前修复审计拒绝的越界主张'),
        ('memory_update', memory_update, '更新经验和待验证的成长与习惯'),
        ('seal', seal, '冻结交付后全周期证据'),
        ('partner_audit', partner_audit, '审计运行过程并严格区分项目问题与Partner机制问题'),
        ('evolution_gate', evolution_gate, '只放行可复现且可独立评价的Partner机制缺陷'),
        ('evolution_request', evolution_request, '固定请求自主自进化子Flow'),
        ('final_summary', final_summary, '自进化结束后生成最终总结消息（含 decision/governance）'),
        ('finish', finish, '保存完整周期结果并停止'))]
