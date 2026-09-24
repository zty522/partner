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
    return {name: read(folder(ctx) / (name + '.json'))
            for name in ('round_one', 'round_two', 'round_three',
                         'learning_one', 'learning_two', 'learning_three', 'report')}


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


_ROUND_NAMES = {1: 'one', 2: 'two', 3: 'three'}


def _constraints(params):
    contract = params.get('intent_contract') if isinstance(params.get('intent_contract'), dict) else {}
    value = contract.get('execution_constraints') if isinstance(contract.get('execution_constraints'), dict) else {}
    return dict(value)


def initialize(ctx, params):
    requested = int(_constraints(params).get('max_rounds') or 3)
    maximum = max(1, min(3, requested))
    original = str((params.get('intent_contract') or {}).get('original_request')
                   or params.get('request') or '')
    # max_rounds is a ceiling, but an explicitly phased user protocol also
    # defines a floor.  The model may judge a successful baseline "complete";
    # it must not thereby skip the user's declared candidate/second phase.
    minimum = 1
    if re.search(r'第三轮|第\s*3\s*轮|round\s*3|third\s+round', original, re.I):
        minimum = 3
    elif re.search(r'第二轮|第\s*2\s*轮|后一轮|下一轮|round\s*2|second\s+round', original, re.I):
        minimum = 2
    minimum = min(minimum, maximum)
    value = {
        'cycle_id': ctx.job_id, 'project_id': params.get('project_id', ''),
        'max_rounds': maximum, 'minimum_rounds': minimum,
        'round_policy': 'settlement_driven_after_explicit_protocol_floor',
        'learning_policy': 'only_for_resolvable_epistemic_gap',
        'evolution_policy': 'post_delivery_partner_mechanism_only',
    }
    path = folder(ctx) / 'cycle_policy.json'
    write_json(path, value)
    return result(value, f'初始化最多{maximum}轮的证据驱动项目周期', [str(path)])


def round_design(ctx, params):
    number = int(params['round_number'])
    name = _ROUND_NAMES[number]
    previous_name = _ROUND_NAMES.get(number - 1, '')
    previous = read(folder(ctx) / f'settle_{previous_name}.json') if number > 1 else {}
    learning = read(folder(ctx) / f'learning_{previous_name}.json') if number > 1 else {}
    impact = read(folder(ctx) / f'impact_{previous_name}.json') if number > 1 else {}
    raw, usage = call_model(ctx, purpose='cycle_round_design', prompt=(
        '你在设计当前项目下一轮的Event Flow蓝图。改进对象只能是用户项目；科学指标、模型、数据和实验属于项目迭代。'
        '不得把项目RMSE等指标问题写成Partner自进化。依据上一轮Settlement和主动学习handoff选择一个可证伪动作。'
        '只使用允许的Event序列：memory.context_recall -> project.state_inspect -> project.plan_propose -> '
        'core.state_build -> core.latent_forecast/core.jev_evaluate -> core.commitment_freeze -> '
        'project.action_execute -> project.outcome_verify -> project.outcome_reflect -> core.settlement。'
        '输出JSON：round_goal,hypothesis,expected_effect,failure_conditions,required_evidence,event_sequence,'
        'learning_handoff_refs,stop_condition,why_new。event_sequence必须是上述Event的有序子序列，'
        '但执行、核验、反思和结算不可省略。下一轮不得重复已否证动作。\n'
        '原始目标=' + str((params.get('intent_contract') or {}).get('original_request') or params.get('request') or '')[:6000]
        + '\n上一轮Settlement=' + json.dumps(previous, ensure_ascii=False)[:12000]
        + '\n主动学习结果=' + json.dumps(learning, ensure_ascii=False)[:10000]
        + '\n学习影响结算=' + json.dumps(impact, ensure_ascii=False)[:6000]))
    value = json_object(raw)
    required = {'project.action_execute', 'project.outcome_verify',
                'project.outcome_reflect', 'core.settlement'}
    sequence = [str(v) for v in value.get('event_sequence') or []]
    allowed = {'memory.context_recall', 'project.state_inspect', 'project.plan_propose',
               'core.state_build', 'core.latent_forecast', 'core.jev_evaluate',
               'core.commitment_freeze', *required}
    if not value.get('round_goal') or not required <= set(sequence) or not set(sequence) <= allowed:
        value['event_sequence'] = [
            'memory.context_recall', 'project.state_inspect', 'project.plan_propose',
            'core.state_build', 'core.latent_forecast', 'core.jev_evaluate',
            'core.commitment_freeze', 'project.action_execute', 'project.outcome_verify',
            'project.outcome_reflect', 'core.settlement']
        value['blueprint_repaired'] = True
    value.update(round_number=number, domain='project', evolution_target_forbidden=True,
                 previous_settlement_ref=(str(folder(ctx) / f'settle_{previous_name}.json')
                                          if number > 1 else ''))
    path = folder(ctx) / f'design_{name}.json'
    write_json(path, value)
    return {**result(value, f'第{number}轮Event蓝图已冻结', [str(path)]),
            'token_usage': usage}


def round_request(ctx, params):
    number = params['round_number']
    name = _ROUND_NAMES[number]
    previous_name = _ROUND_NAMES.get(number - 1, '')
    prior = read(folder(ctx) / f'round_{previous_name}.json') if number > 1 else {}
    design = read(folder(ctx) / f'design_{name}.json')
    learning = read(folder(ctx) / f'learning_{previous_name}.json') if number > 1 else {}
    original = (params.get('intent_contract') or {}).get('original_request') or params['request']
    maximum = int(read(folder(ctx) / 'cycle_policy.json').get('max_rounds') or 3)
    request = original + f'\n这是证据驱动周期第 {number}/{maximum} 轮。'
    if prior:
        last = prior.get('node_outputs') or {}
        request += ('必须先读取第一轮真实产物，依据结果选择补缺、独立复核或新的实验；不要重复原动作。'
                    '第一轮完成目标时第二轮验证边界，不扩大用户目标。\n第一轮证据文件：'
                    + str(folder(ctx) / 'round_one.json')
                    + '\n第一轮分析：' + json.dumps(last.get('reflect') or {}, ensure_ascii=False)[:7000]
                    + '\n第一轮产物：' + json.dumps(prior.get('files') or [], ensure_ascii=False)[:5000])
    else:
        request += '先完成一个能产生真实结果的有界动作，把剩余问题留给第二轮；不要只探查工具或写方案。'
    request += '\n本轮冻结Event蓝图：' + json.dumps(design, ensure_ascii=False)[:12000]
    if learning:
        request += ('\n上一边界主动学习handoff，必须在plan和执行证据中明确引用或说明为何不采用：'
                    + json.dumps(learning, ensure_ascii=False)[:12000])
    request += ('\n本子Flow只承担这一轮业务实验。两轮后的阶段消息、PDF交付、经验/成长/习惯更新和自进化实验'
                '由父Flow中的独立Event执行；业务动作不可合并、替代或自行再次触发这些阶段。'
                '研究框架实现时必须import并实际调用被测函数；手写等价副本只能作为假设，不能证明生产实现行为。')
    contract = {**params.get('intent_contract', {}), 'round_number': number,
                'original_request': original, 'round_blueprint': design,
                'learning_handoff': learning, 'evidence_refs': prior.get('files', [])}
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
    if number < minimum:
        route = 'continue_project'
        judged['objective_complete'] = False
        judged['explicit_protocol_floor'] = minimum
        judged['reason'] = ('当前阶段虽已通过验证，但用户显式声明的后续轮次尚未执行；'
                            '继续到下一项目轮完成冻结协议。')
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
    for name in ('round_one', 'round_two', 'round_three'):
        row = data[name]
        if not row:
            continue
        selected[name] = {'flow_id': row.get('flow_id'), 'status': row.get('status'),
            'outputs': {k: {a: b for a, b in v.items() if a in
                         ('ok','status','summary','error','files','evidence_refs','business_delta')}
                       for k, v in (row.get('node_outputs') or {}).items()
                        if k in ('plan', 'execute', 'verify', 'reflect')}}
    artifacts = []
    for raw_path in evidence(ctx):
        p = Path(raw_path)
        if '/state/event_runtime/work/' in raw_path and p.suffix in ('.json', '.py', '.md', '.csv'):
            artifacts.append({'path':raw_path, 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                              'excerpt':p.read_text(errors='replace')[:3500],
                              'preview_only':p.stat().st_size > 3500})
    snapshot = {'rounds': selected, 'artifacts': artifacts[:24],
                'settlements': {name: read(folder(ctx) / f'settle_{name}.json')
                                for name in ('one', 'two', 'three')
                                if (folder(ctx) / f'settle_{name}.json').is_file()},
                'learning_impacts': {name: read(folder(ctx) / f'impact_{name}.json')
                                     for name in ('one', 'two', 'three')
                                     if (folder(ctx) / f'impact_{name}.json').is_file()},
                'learning_downstream': {name: read(folder(ctx) / f'learning_downstream_{name}.json')
                                        for name in ('two', 'three')
                                        if (folder(ctx) / f'learning_downstream_{name}.json').is_file()},
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
        + '用户原始目标=' + params['request'] + '\n两轮真实快照=' + json.dumps(snapshot, ensure_ascii=False)[:65000]))
    value = json_object(raw)
    path = folder(ctx) / 'assessment.json'
    write_json(path, value)
    return {**result(value, str(value.get('summary') or '两轮结果已分析'), [str(path)]),
            'token_usage': usage, 'notification_kind': 'milestone'}


def report_request(ctx, params):
    # Avoid recursively embedding recall/planning histories ahead of results.
    refs = [str(folder(ctx) / name) for name in ('business_snapshot.json', 'assessment.json')]
    refs += [p for p in evidence(ctx) if '/state/event_runtime/work/' in p
             and Path(p).name not in ('state.json', '执行结果.md', 'verified_project_artifacts.json')]
    refs = list(dict.fromkeys(p for p in refs if Path(p).is_file()))
    contract = {**params.get('intent_contract', {}), 'evidence_refs': refs,
                'force': True, 'report_policy': 'final', 'expand_evidence_refs': False}
    return {**result({}, '根据两轮真实结果生成阶段报告'),
            'cycle_child': {'flow': 'pdf_report', 'owner_node': params['node_id'],
                'context': {'intent_contract': contract, 'evidence_refs': refs,
                            'force': True, 'notification_kind': 'final',
                            'request': '根据原始目标与所附两轮真实证据生成中文阶段报告；展示真实数据与失败边界，不引入新业务结果。消息/PDF/记忆/自进化由后续Event执行，不能将尚未到达阶段判为缺失。'}}}


def delivery_settle(ctx, params):
    if params['node_id'] == 'report_ack':
        sent = (read(folder(ctx) / 'report.json').get('node_outputs') or {}).get('send', {})
    else:
        source_node = 'final_send' if params['node_id'] == 'final_ack' else 'send'
        sent = (params.get('flow_outputs') or {}).get(source_node) or {}
    if sent.get('delivered') or sent.get('suppressed'):
        delivered = bool(sent.get('delivered'))
        receipt = {'delivered': delivered,
                   'suppressed': bool(sent.get('suppressed')),
                   'delivery_state': 'sent' if delivered else 'suppressed',
                   'pdf_delivered': bool(delivered and params['node_id'] == 'report_ack')}
        target = folder(ctx) / (params['node_id'] + '.json')
        write_json(target, receipt)
        return result(receipt, '渠道状态已核验', [str(target)])
    path = (sent.get('receipt') or {}).get('path')
    if not path:
        return {'ok': False, 'status': 'failed', 'error': 'missing delivery receipt; retained for self audit'}
    ack = Path(path).with_suffix('.sent')
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        row = read(ack)
        if row.get('delivery_state') == 'sent' and row.get('text_delivered'):
            if sent.get('delivery_kind') == 'pdf' and not row.get('pdf_delivered'):
                break
            receipt = {'path': str(ack), 'delivery_state': 'sent',
                       'component_acks': row.get('component_acks', []),
                       'pdf_delivered': bool(row.get('pdf_delivered'))}
            target = folder(ctx) / (params['node_id'] + '.json')
            write_json(target, receipt)
            return result(receipt, '已取得本次渠道回执', [str(target)])
        if Path(path).with_suffix('.blocked').exists():
            break
        time.sleep(1)
    return {'ok': False, 'status': 'failed', 'error': 'delivery ACK missing after bounded wait; audit must inspect this failure'}


def memory_update(ctx, params):
    from partner.memory import EventMemory
    kind = params['kind']
    path = folder(ctx) / ('memory_' + kind + '.json')
    if path.exists():
        return result(read(path), f'{kind} 复用同周期已保存更新', [str(path)])
    summary = read(folder(ctx) / 'assessment.json')
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
    manifest = {'cycle_id': ctx.job_id, 'instance_id': ctx.instance_id,
                'project_id': params['project_id'], 'request': params['request'],
                'root_event_id': params.get('root_event_id', ''),
                'parent_flow_id': params['flow_id'], 'created_at': time.time(),
                'files': [{'path': p, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                          for p in dict.fromkeys(refs) if Path(p).is_file()],
                'node_outputs': params.get('flow_outputs') or {}}
    path = folder(ctx) / 'sealed_cycle.json'
    write_json(path, manifest)
    return result({'manifest': str(path)}, '交付与记忆之后冻结完整周期，固定触发自进化', [str(path)])


def partner_audit(ctx, params):
    manifest_path = folder(ctx) / 'sealed_cycle.json'
    manifest = read(manifest_path)
    known = {str(row.get('path')) for row in manifest.get('files') or [] if row.get('path')}
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
    accepted = []
    for issue in value.get('issues') or []:
        if not isinstance(issue, dict):
            continue
        refs = [str(v) for v in issue.get('evidence_refs') or [] if str(v) in known]
        targets = [str(v) for v in issue.get('partner_target_files') or []]
        targets_ok = bool(targets) and all(
            v.startswith(('partner/', 'tests/', 'docs/')) and not v.startswith('projects/')
            for v in targets)
        row = {**issue, 'evidence_refs': refs, 'partner_target_files': targets,
               'scope_valid': issue.get('category') == 'partner_mechanism' and targets_ok}
        accepted.append(row)
    value['issues'] = accepted
    value['audit_scope'] = 'partner_runtime_only'
    value['project_metrics_excluded_from_evolution'] = True
    path = folder(ctx) / 'partner_audit.json'; write_json(path, value)
    return {**result(value, f'完成Partner运行后审计，记录{len(accepted)}项分类问题', [str(path)]),
            'token_usage': usage}


def evolution_gate(ctx, params):
    audit = read(folder(ctx) / 'partner_audit.json')
    selected = None
    rejected = []
    for issue in audit.get('issues') or []:
        ready = (issue.get('category') == 'partner_mechanism'
                 and issue.get('scope_valid') is True
                 and issue.get('reproducible') is True
                 and bool(issue.get('reproducer'))
                 and bool(issue.get('independent_evaluator'))
                 and bool(issue.get('evidence_refs')))
        if ready and selected is None:
            selected = issue
        else:
            rejected.append({'id': issue.get('id'), 'category': issue.get('category'),
                             'reason': 'not a reproducible Partner mechanism issue with an independent evaluator'})
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
    production_effective = bool(decision.get("production_effective"))
    gov_status = "已记录" if gov_issue else "未记录"
    summary_line = gov_issue.get("summary", "").strip()[:200] if gov_issue else "未发现可证伪机制问题"
    expectations = (decision.get("comparisons") or {}).get("1", {}).get("expectation_results") or []
    met = sum(1 for e in expectations if e.get("status") == "met")
    total = len(expectations)
    test_summary = f"（已验证测试 {met}/{total}）" if expectations else ""
    if no_change:
        text = ('本轮运行后的 Partner 自进化审计已完成。没有候选问题通过可复现性、真实目标文件和独立评价硬门，'
                '因此本轮没有修改 Partner。')
    else:
        text = (
            f"Partner 自进化实验已完成{test_summary}。"
            f"实验判定={decision_text}，修改是否通过生产验证={production_effective}。"
            f"治理账本{gov_status}。问题摘要：{summary_line}"
        ).strip()

    out_path = folder(ctx) / 'final_summary.json'
    write_json(out_path, {'message': text, 'decision': decision_text,
                          'production_effective': production_effective,
                          'governance_recorded': bool(gov_issue)})
    return result({
        "summary": text, "message": text, "force": True,
        "notification_kind": "final",
        "decision": decision_text,
        "production_effective": production_effective,
        "governance_recorded": bool(gov_issue),
    }, "自进化终态摘要已生成，交由后续消息Event审查和发送", [str(out_path)])


def finish(ctx, params):
    child = read(folder(ctx) / 'evolve.json')
    recorded=((child.get('node_outputs') or {}).get('record',{}).get('semantic_output') or {})
    text_ack=read(folder(ctx)/'text_ack.json');pdf_ack=read(folder(ctx)/'report_ack.json')
    final_ack=read(folder(ctx)/'final_ack.json')
    effective=bool(recorded.get('valid_matched_experiment') or recorded.get('no_change_behavior_verified'))
    project_rounds = sum((folder(ctx) / f'round_{name}.json').is_file()
                         for name in ('one', 'two', 'three'))
    delivery_verified = (text_ack.get('delivery_state') == 'sent'
                         and pdf_ack.get('delivery_state') == 'sent'
                         and pdf_ack.get('pdf_delivered') is True
                         and final_ack.get('delivery_state') == 'sent')
    value = {'cycle_id': ctx.job_id, 'stopped': True, 'project_rounds': project_rounds,
             'evolution_effective_attempt':effective,
             'delivery_verified': delivery_verified,
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
    return result(value, summary, [str(path)])


DEFINITIONS = [EventDefinition('cycle.' + name, 'project', description, fn,
    execution_method='llm' if name in ('assess','memory_update') else 'local',
    timeout_seconds=300 if name in ('assess','memory_update') else 100)
    for name, fn, description in (
        ('initialize', initialize, '初始化项目周期预算与职责边界'),
        ('round_design', round_design, '根据上一轮结算设计并冻结下一轮Event蓝图'),
        ('round_request', round_request, '请求真实项目子Flow并消费前轮证据'),
        ('round_settle', round_settle, '依据真实终态决定停止、继续或主动学习'),
        ('learning_request', learning_request, '只在可解决知识缺口时请求主动学习子Flow'),
        ('learning_impact_settle', learning_impact_settle, '冻结学习候选并等待下游改善证据'),
        ('learning_downstream_settle', learning_downstream_settle, '以后一项目轮的匹配证据结算主动学习效果'),
        ('assess', assess, '综合所有已执行项目轮的真实推进与差距'),
        ('report_request', report_request, '请求现有PDF子Flow'),
        ('delivery_settle', delivery_settle, '等待本次真实渠道ACK或明确失败'),
        ('memory_update', memory_update, '更新经验和待验证的成长与习惯'),
        ('seal', seal, '冻结交付后全周期证据'),
        ('partner_audit', partner_audit, '审计运行过程并严格区分项目问题与Partner机制问题'),
        ('evolution_gate', evolution_gate, '只放行可复现且可独立评价的Partner机制缺陷'),
        ('evolution_request', evolution_request, '固定请求自主自进化子Flow'),
        ('final_summary', final_summary, '自进化结束后生成最终总结消息（含 decision/governance）'),
        ('finish', finish, '保存完整周期结果并停止'))]
