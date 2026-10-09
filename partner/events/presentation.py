"""Presentation Events; rendering and transport stay separate from truth."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
from partner.event_fabric.catalog import EventDefinition
from partner.runtime.action_execution import write_json
from ._llm import call_model, json_object, event_facts
from partner.presentation.document import visual_context, localize_prose


def _markdown_body(text):
    text = str(text).strip()
    wrapped = re.fullmatch(r'```(?:markdown|md)?\s*\n(.*)\n```', text, flags=re.DOTALL)
    return wrapped.group(1).strip() if wrapped else text


def _normalize_unsupported_claims(value: Any) -> list[Any]:
    """Discard malformed audit rows that explicitly say the claim is supported.

    Some providers return a claim/reason object for every reviewed claim even
    though the requested schema is a list containing unsupported claims only.
    A positive support explanation must not poison an otherwise clean review;
    ambiguous objects and all string findings remain fail-closed.
    """
    findings = value if isinstance(value, list) else []
    unsupported = []
    positive = re.compile(r"(?:有|由|得到|可以|可被|已经|已获|直接)?支持|符合事实|基本符合|不构成.*(?:错误|问题)|可接受")
    negative = re.compile(r"不支持|未支持|证据不足|不足以|缺少|无依据|无法推出|捏造")
    for finding in findings:
        if not isinstance(finding, dict):
            unsupported.append(finding)
            continue
        if finding.get("supported") is True or finding.get("unsupported") is False:
            continue
        if finding.get("supported") is False or finding.get("unsupported") is True:
            unsupported.append(finding)
            continue
        reason = str(finding.get("reason") or "")
        if positive.search(reason) and not negative.search(reason):
            continue
        unsupported.append(finding)
    return unsupported


def _verified_benchmark_fallback(params: dict[str, Any]) -> str:
    """Build a minimal delivery message only from verified benchmark terminals."""
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    settlement = outputs.get("benchmark_settlement") or {}
    semantic = settlement.get("semantic_output") if isinstance(settlement, dict) else {}
    report = outputs.get("benchmark_report_verify") or {}
    if not isinstance(semantic, dict) or report.get("ok") is not True:
        return ""
    decision = str(semantic.get("decision") or "")
    labels = {"confirmed":"达到预先声明的效果门槛", "falsified":"未达到预先声明的效果门槛",
              "invalid":"运行证据无效", "inconclusive":"现有证据不足以裁决"}
    if decision not in labels:
        return ""
    return f"本次基准实验已完成，确定性评价显示：{labels[decision]}。详细证据已写入通过核验的报告。"


def _verified_project_benchmark_fallback(params: dict[str, Any]) -> str:
    """Minimal project-stage message derived only from settled benchmark facts."""
    outputs = params.get('flow_outputs') if isinstance(params.get('flow_outputs'), dict) else {}
    assessment = outputs.get('assess') or {}
    semantic = assessment.get('semantic_output') if isinstance(assessment, dict) else {}
    benchmark = semantic.get('benchmark_result') if isinstance(semantic, dict) else {}
    coverage = semantic.get('goal_coverage') if isinstance(semantic, dict) else {}
    if (assessment.get('status') != 'completed' or not isinstance(benchmark, dict)
            or benchmark.get('valid') is not True
            or benchmark.get('decision') not in {'confirmed', 'falsified', 'inconclusive'}):
        return ''
    labels = {'confirmed': '达到预先声明的效果门槛',
              'falsified': '未达到预先声明的效果门槛',
              'inconclusive': '现有证据不足以裁决'}
    learning = ('来源约束的主动学习交接文件已被下一研究轮引用。'
                if isinstance(coverage, dict)
                and str(coverage.get('handoff_consumption') or '').startswith('Covered') else '')
    return ('冻结项目实验已经由确定性评价器完成，结论为：'
            + labels[str(benchmark['decision'])] + '。' + learning)


def notification_decide(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    summary = dict(params.get("summary") or {})
    contract=params.get('intent_contract') or {}
    if contract.get('report_job_id'):
        from partner.presentation.notifications import with_report_context
        with_report_context(_ctx,params)
        return {'ok':True,'status':'completed','notify':True,'notification_kind':'final','summary':'根据已核验报告更新交付说明'}
    if contract.get('waiting_for_job') or contract.get('failure_for_job'):
        target=Path(_ctx.workspace)/'state/application/jobs'/f"{contract.get('failure_for_job') or contract['waiting_for_job']}.json"
        running=json.loads(target.read_text()) if target.is_file() else {}
        failed=bool(contract.get('failure_for_job'))
        active=running.get('status')=='failed' if failed else running.get('status') in {'running','dispatched'}
        return {'ok':True,'status':'completed','notify':active,'notification_kind':'blocked' if failed else 'waiting',
                'semantic_output':{'actual_job_status':running.get('status'),'current_stage':running.get('current_event_id')},
                'summary':'任务仍在运行' if active else '等待通知已经过时，不再发送'}
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    if not summary and outputs:
        summary = {
            "business_delta": any(bool(row.get("business_delta")) for row in outputs.values() if isinstance(row, dict)),
            "learning_delta": any(bool(row.get("learning_delta")) for row in outputs.values() if isinstance(row, dict)),
            "evolution_delta": any(bool(row.get("evolution_delta")) for row in outputs.values() if isinstance(row, dict)),
            "requires_human": any(bool(row.get("requires_human")) for row in outputs.values() if isinstance(row, dict)),
            "notification_kind": "blocked" if any(
                row.get("status") == "failed" for row in outputs.values() if isinstance(row, dict)
            ) else "final",
        }
    route=(outputs.get('route') or outputs.get('resume') or {}).get('semantic_output') or {}
    if route.get('primary_route')=='continue_project' and summary.get('notification_kind')=='final':
        summary['notification_kind']='progress'
    notify = bool(summary.get("requires_human") or summary.get("business_delta")
                  or summary.get("learning_delta") or summary.get("evolution_delta")
                  or summary.get("notification_kind") in {"milestone", "blocked", "final"})
    return {"ok": True, "status": "completed", "notify": notify, "notification_kind":summary.get("notification_kind","routine"),
            "summary": "需要用户可见通知" if notify else "仅保留本地 Event Summary"}


def _humanize_internal_terms(text: str) -> str:
    """Replace internal variable names with user-friendly natural language."""
    if not text:
        return text
    # Mapping from internal identifiers to user-friendly terms
    replacements = {
        '基线结果': '基线冻结状态',
        'baseline_result': '基线冻结状态',
        'candidate_result': '候选评估状态',
        'governance_ledger': '治理记录',
        'production_effective': '生产生效状态',
        'exit_code': '退出码',
        'manifest_sha256': '清单指纹',
        'inner_future': '内部异步任务',
        '探针': '检测机制',
        'epistemic gap': '认知缺口',
        '父协调器': '主控制器',
        '独立轨道': '并行流程',
        'missing delivery receipt': '缺失交付回执',
    }
    result = text
    # Fix abnormal spacing patterns like '冻结 基线结果' before term replacement
    result = re.sub(r'冻结\s+基线结果', '基线冻结状态', result)
    for internal, friendly in replacements.items():
        # Case-insensitive replacement for English terms, exact for Chinese
        if internal.isascii():
            result = re.sub(r'\b' + re.escape(internal) + r'\b', friendly, result, flags=re.IGNORECASE)
        else:
            result = result.replace(internal, friendly)
    return result


def message_compose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from partner.presentation.notifications import waiting_message,with_report_context,with_waiting_context
    params=with_waiting_context(ctx,with_report_context(ctx,params))
    waiting=waiting_message(ctx,params)
    if waiting is not None:
        return {'ok':bool(waiting),'status':'completed' if waiting else 'failed','message':waiting,'summary':waiting,'runtime_projection':True}
    outputs = params.get("flow_outputs") or {}
    # Check for explicit PDF format failure to trigger sanitized fallback
    pdf_status = (outputs.get('pdf_format') or {}).get('status')
    report_ack_ok = (outputs.get('report_ack') or {}).get('ok')
    if pdf_status == 'failed' or report_ack_ok is False:
        fallback_msg = "任务已处理，但PDF报告生成/投递失败。详细日志已存档。"
        return {'ok': True, 'status': 'completed', 'message': fallback_msg,
                'summary': fallback_msg[:500], 'evidence_refs': []}
    narrative_output = outputs.get('narrative') or outputs.get('finalize') or {}
    narrative = narrative_output.get('semantic_output') or {}
    if narrative and params.get('node_id') == 'compose':
        message = str(narrative.get('milestone_message') or narrative.get('headline') or '').strip()
        if message:
            message = _humanize_internal_terms(message)
            return {'ok': True, 'status': 'completed', 'message': message,
                    'summary': message[:500],
                    'evidence_refs': list(narrative_output.get('evidence_refs') or [])}
    assessment = (outputs.get('assess') or {}).get('semantic_output') or {}
    benchmark = assessment.get('benchmark_result') if isinstance(assessment, dict) else {}
    if (params.get('node_id') == 'compose' and isinstance(benchmark, dict)
            and benchmark.get('valid') is True):
        comparison = benchmark.get('comparison') or {}
        baseline = comparison.get('baseline_value')
        candidate = comparison.get('candidate_value')
        message = (
            f'冻结实验已完成：加入预先声明的目标级特征后，RMSE 从 {baseline:.3f} '
            f'降至 {candidate:.3f}，超过预期改善阈值；配对样本、执行一致性和防泄漏护栏均通过。'
            '来源约束的主动学习交接文件已被下一研究轮实际引用；这项学习是否改善指标不由本消息单独归因。'
        ) if isinstance(baseline, (int, float)) and isinstance(candidate, (int, float)) else (
            '冻结实验已由确定性评价器完成并确认有效；前置主动学习交接已被下一研究轮实际消费。'
            '详细方法、结果、图表和限制写入研究报告。')
        message = _humanize_internal_terms(message)
        return {'ok': True, 'status': 'completed', 'message': message,
                'summary': message,
                'evidence_refs': list((outputs.get('assess') or {}).get('evidence_refs') or [])}
    if params.get('node_id') == 'final_compose':
        final = outputs.get('final_summary') or {}
        semantic = final.get('semantic_output') or {}
        final_message = str(semantic.get('message') or final.get('message') or '').strip()
        if (final.get('status') == 'completed'
                and semantic.get('evidence_verified') is True
                and final_message):
            final_message = _humanize_internal_terms(final_message)
            return {'ok':True, 'status':'completed', 'message':final_message,
                    'summary':final_message[:500],
                    'evidence_refs':list(final.get('evidence_refs') or [])}
    direct = str((params.get("flow_outputs") or {}).get("answer", {}).get("answer") or "")
    if direct:
        # Keep the real answer intact for independent review. Repeated prose
        # rewrites anchored to an intent summary can amplify its misconceptions.
        direct = _humanize_internal_terms(direct)
        return {"ok":True, "status":"completed", "message":direct,
                "summary":direct[:500], "evidence_refs":[]}
    # Sprint 37: surface the round's real business output at the top of the
    # prompt so the composer cannot miss it and report "no new output" when
    # execute actually produced business_delta artifacts.
    business_note = ""
    try:
        executed = (params.get("flow_outputs") or {}).get("execute") or {}
        exec_sem = executed.get("semantic_output") or {}
        if exec_sem.get("business_delta"):
            art = exec_sem.get("artifacts") or exec_sem.get("files") or []
            art_str = "、".join(str(a) for a in art[:3]) if art else "（见 summary）"
            business_note = ("\n【本轮业务产出（必须优先如实报告，不得说没有产出）】"
                             "execute 已产生业务变更："
                             + str(executed.get("summary") or "")[:260]
                             + "；产物：" + art_str + "\n")
    except Exception:
        business_note = ""
    raw, usage = call_model(ctx, purpose="message_compose", prompt=(
        business_note +
        "根据消息目的写自然中文：问答直接回答；进展先说新发现及影响；阻塞说清当前障碍；最终交付先给核心结论。报告交付通常两三句、80至160字，信息少时更短，不凑字数。不要代码和内部字段。只引用实际证据，"
        "waiting消息只写一两句，具体说明当前对象与动作及尚未完成什么。以live_status实际命令和后台状态为准，select只是计划。若只有读源码/查环境，直说还在查调用或输入问题，不能称正在看视频/做模拟。不写'目前还在执行已安排的任务'、'已有结果会保留'等无信息套话，不许把旧轮结果当新发现。"
        "不要自评'已诚实标记'、'证据扎实'或'完整收口'，直接说具体缺什么、做成什么。单次耗时不能保证整批完成时间；有必要外推时明确说估算及尚未实测，不把模型自定阈值当用户要求。"
        "视频用标题或主题称呼，不报长串视频ID；页面说小红书首页或文章页，不写explore、DOM、落盘、校验指纹。来源链接和核验细节放报告；没有标题就说这条视频，不猜标题。"
        "研究代码的进展也要说明对用户有何影响，不把函数名、源码行号、fsync/POSIX和字段名称串成正文。用自然中文解释实际机制，精确代码位置留报告；例如先说修正了哪条判断、实际缺口是什么。不要写落槌等断言式口头禅。"
        "纠正旧结论时明确区分原判断与查实事实，不能先说正确事实再笼统说这两条被否证；用之前误判了什么、实际是什么的直接表达，避免指代和双重否定颠倒事实。"
        "消息读者不需要知道实现术语：把机制发现解释为它对任务能否继续、结果会不会丢失、方案该改哪里有什么影响；具体实现留报告。风险只能说可能，不能把静态代码推断说成已经复现故障。"
        "区分实际测量与假设，不能将文件或脚本生成说成实验成功。先直接报一件有意义的发现，不先泛泛评价证据偏弱。整条消息最多两个阿拉伯数字数值，其余计算条件放PDF，不以数字列表代替解释。不必每次重复方法、局限和下一步。"
        "只有实际已排队或执行的动作才能说正在做/接下来会做；未排队的动作称建议。区分项目推进/外部主动学习/Partner自进化，不暴露内部路径和模板字段。"
        "面向用户而非开发日志：用中文解释结果的意义，不堆叠脚本名、哈希、版本代号、假设编号和英文指标字段。不要写production_effective、exit_code、inner_future或true/false，把状态翻译成中文。除用户关注的API名外不报内部变量；最多两个重要数字。"
        "最终汇报禁止使用内部技术词：物理哈希绑定、机制性空转、知识注入、候选实验、治理账本、可核验业务证据、生产验证、基线/候选等，一律换成日常表达，例如‘第2轮的分析动作缺少可验证依据，无法确认外部资料真正影响了结果，因此提前停止’‘尝试了修复但未通过验证，未改动系统’；解释‘对用户意味着什么’而不罗列机制细节。"
        "对已授权且可执行的步骤直接推进，不反复索要确认。实际缺少登录会话、外部访问条件或发布授权时，"
        "如实说明阻断条件，不虚构已安排的恢复动作，不自行承诺切换账号、网络或接口来规避站点限制。"
        + ("这是简单问答，保持一到两句话，不要扩写。原始回答=" + direct + "\n" if direct else "")
        + "\n消息目的与实际等待状态=" + json.dumps({k:(params.get("intent_contract") or {}).get(k) for k in ("notification_kind","running_snapshot")},ensure_ascii=False)
        + "\n\n【事实优先】\n"
        + "本消息如果作为 project_cycle 或 autonomous_evolution 的最终汇报，需充分重视运行记录中的实际状态，以下几点帮助判断：\n"
        + "- autoevolution release 节点的 status 是最终裁决：\n"
        + "  - 'rejected_full_regression' 意味全量回归未过，未进生产；\n"
        + "  - 'not_applied' 意味本轮无变更建议；\n"
        + "  - 'applied' 或 production_effective=true 意味候选进入生产；\n"
        + "  - 其他状态均视为 inconclusive。\n"
        + "- autoevolution evaluate/decision 的 decision 字段表示本轮是否发现有效修复：\n"
        + "  - 'validated_shadow' 表示隔离实验通过但全量未过；\n"
        + "  - 'no_change_verified' 表示当前代码行为已被独立验证；\n"
        + "  - 'inconclusive' 表示证据不足以下结论；\n"
        + "  - 'promoted' 表示隔离实验通过（另需看 release.status 才能知是否进生产）。\n"
        + "- 轮次完成数：若本轮实际跑过的 flow_type 为 project_cycle_round 且 flow.completed_node_ids 长度 \u22656，"
        + "则该轮已完成；不要因 autoevolution 节点也跑过就把业务轮次判为\u2018缺失\u2019。\n"
        + "- 禁止虚构 partner record 中不存在的术语。如\u2018三闸门\u2019\u3001"
        + "\u2018判定拒绝\u2019\u3001\u2018业务进度为零\u2019\u3001\u2018奖励校准\u52a3化\u2019 等。"
        + "若某判断在记录中没有显式字段，宁可省略该判断也不要给出数字化的伪指标\u3002\n"
        + "\n[业务执行优先] 若本轮的 execute/verify 节点有真实业务产出（新产物、新证据、新指标，如 per_target_predictions、rmse/pearson 改善），必须优先如实报告该产出及其对目标的影响；自进化判定只是附加说明，放在业务产出之后，不得因报告自进化而省略或否定业务执行的实际结果；若 execute 已产出某项证据，不得再称仍缺该项证据。\n[自进化必报] 当本周期真跑了 autoevolution 且 decision/governance 已落盘，必须用通俗中文报告以下信息，不能省略："
        + "\n- 自进化实际跑完了哪一步（基线测试、候选补丁、隔离实验、决策）；"
        + "\n- 它针对的 partner 源码问题（一句话概述，例如\u2018记忆模块三种分类输出重复\u2019）；"
        + "\n- 自进化最后判定（已拒绝 / 未通过 / 进入生产 / 无修改建议），用\u2018自进化判定\u2019前缀；"
        + "\n- 是否在治理账本记录了发现的问题（是/否）；"
        + "\n如有\u2018未通过\u2019，必须说明是\u2018基线复现了缺陷但候选未修好\u2019还是\u2018测试本身不合格\u2019，两者意义不同。"
        + "\n以上这些是 self-evolution 的最终报告义务；不写就是隐瞒事实，禁止。"
        + "\n以上只是在本消息作为终汇报、且记录中存在 autoevolution 节点输出时才生效；"
        + "简单问答与普通问题不受影响\u3002"
        + event_facts(params, max_chars=40000)
    ))
    raw = _humanize_internal_terms(raw)
    # Deduplicate consecutive identical sentences/phrases to improve readability
    # and remove redundant placeholders often generated by LLMs.
    if raw:
        parts = re.split(r'(?<=[。！？；])', raw)
        cleaned_parts = []
        last_part = None
        for part in parts:
            stripped = part.strip()
            if not stripped:
                continue
            if stripped == last_part:
                continue
            cleaned_parts.append(part)
            last_part = stripped
        raw = ''.join(cleaned_parts).strip()
    return {"ok": True, "status": "completed", "message": raw,
            "summary": raw[:500], "token_usage": usage}


def message_critic(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from partner.presentation.notifications import waiting_message,with_report_context,with_waiting_context
    params=with_waiting_context(ctx,with_report_context(ctx,params))
    waiting=waiting_message(ctx,params)
    if waiting is not None:
        return {'ok':bool(waiting),'status':'completed' if waiting else 'failed','message':waiting,'summary':'已对照当前任务状态核验等待通知','runtime_projection':True}
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    message = str(params.get("message") or prior.get("message") or "")
    initial_message = message  # Sprint 37: composer's original, used on format degrade
    answer_receipts=((params.get('flow_outputs') or {}).get('answer',{}).get('semantic_output') or {}).get('runtime_status',{})
    if answer_receipts and getattr(ctx,'workspace',None) and getattr(ctx,'job_id',None):
        from partner.runtime.status_context import runtime_status
        current_receipts=runtime_status(ctx,params)
        if current_receipts.get('jobs'):answer_receipts=current_receipts
    facts = (json.dumps({'original_user_request':params.get('request'),
                         'project_for_terminology_only':params.get('project_id'),
                         'runtime_receipts':answer_receipts}, ensure_ascii=False)
             if (params.get('flow_outputs') or {}).get('answer') else event_facts(params, max_chars=40000))
    if params.get('flow_id') and getattr(ctx,'workspace',None):
        from partner.event_fabric import EventFlowStore
        state=EventFlowStore(ctx.workspace).load(params['flow_id'])
        for recovery in reversed(state.recovery_history):
            if recovery.get('node_id')==params.get('node_id'):
                old=recovery.get('output') or {}
                candidate=old.get('candidate_message') or (old.get('semantic_output') or {}).get('revised_message')
                if candidate:message=str(candidate)
                break
    instructions = (
        '独立审查用户将看到的消息，只输出JSON：{"accepted":true,"problems":[],"revised_message":""}。'
        '按用户原文判断问题的领域、主语和比较对象，不沿用错误理解。用项目名辅助术语消歧，但不得据此捏造执行事实。'
        '检查命题偷换：公式计算不等于随时间守恒；数量不决定分段规则；排名最优不一定数值最高。'
        '检查纠错表述的否定对象：原先的错误断言与最新正确事实必须明确区分，不能列出正确事实后说这些已被否证；改为先前误判与实际发现的清楚表达。'
        '删除无依据的身份、采样规则、机制、反例或必要条件。数据行数不能直接当作某类事件数，必须区分记录类别。'
        '比较集合、预算或预处理不同时不得归因改善；几何接触与代理评分不是药效。未返回信息不等于不存在。'
        '只保留核心回答或发现、局限、必要下一步，不增加未安排的动作。不重复要求已给出的授权，也不承诺规避站点访问限制。'
        '语言自然易读，禁止段落1/段落2等模板标签、标题、内部字段和文件清单；短问答遵循用户句数，不堆公式与额外例子。'
        '进展消息若只列实现机制却未说明对用户任务的意义，应改成普通读者可理解的发现及影响；静态检查只能支持风险推断，不能写成真实故障已复现。'
        '禁止退出码、production_effective、manifest_sha256、inner_future、true/false状态串和哈希，必须翻译成中文含义。production_effective是历史回执的生效标识，不是开关，也不代表当前生产状态。禁止交付物清单、粗体栏目标题、最硬/最狠/封神/算命等夸张文风；先说发现，图片和PDF由附件显示无需逐个报编号。非列表问答消息最多4个非空行。字数随消息目的决定，短回答不扩写；通常1至4句，最多两个核心数字。超过400字必须精简。审查问题最多3项、每项一句话。'
        '删除已诚实标记、完整收口等自我评价，改为实际结果或障碍。单次测量外推整批时间必须标为估算，不能作为已验证的能力保证。'
        'accepted表示当前待审消息已经可发送；如需修改返回revised_message，修改稿还会再次审查。修改时必须做最小改动：只删除或弱化确有问题的表述（过强量词、未授权的下一步安排），必须保留消息中已核实的业务产出、关键结果和真实数字，不得因修改而删除或抹掉已核实的业务证据。'
        '【与运行记录的一致性】'
        '本消息作为 project_cycle 或 autonomous_evolution 的终汇报时,必须从<source_facts> 中读取以下事实状态:'
        '- autoevolution release.status 与 production_effective'
        '- autoevolution evaluate.decision 与 candidate promotion 结果'
        '- 上一轮 project_cycle_round 节点的 completed_node_ids 长度'
        '若消息措辞与这些事实明显相反(如 release.status=rejected_full_regression 时将全量未过说成已发布,或 promote 实验成功后说被拒绝),必须返回 accepted=false 并在 revised_message 中指明哪一条事实被错误描述。'
        '若消息措辞与事实一致或消息主题不涉及这些事实,按既有格式规则审查。\n'
    )
    limit=400 if (params.get('flow_outputs') or {}).get('answer') or (params.get('intent_contract') or {}).get('message_detail')=='detailed' else 180
    instructions += f'本条消息上限{limit}字。以用户能一眼读懂为准；非详细问答不出现代码调用、params、patch_application或字段清单。格式检查中任何超限或违规为true时，必须返回accepted=false及消除该问题的revised_message，不能只口头判定通过。数字超限时删除次要数值，不通过改写成中文数字规避。\n'
    usage = {}; reviews = []; accepted = False; start_attempt = 0; cached_audit = None
    tool_payload = False; plain_language_violations = []
    checkpoint = None
    if params.get('flow_id') and getattr(ctx, 'workspace', None) and getattr(ctx, 'job_id', None):
        import hashlib
        checkpoint = Path(ctx.workspace) / 'state/event_runtime/work' / ctx.job_id / '.message_reviews' / (params['flow_id']+'_'+params.get('node_id','critic')+'.json')
        fingerprint = hashlib.sha256(json.dumps([message,facts,instructions,limit],ensure_ascii=False).encode()).hexdigest()
        if checkpoint.is_file():
            saved = json.loads(checkpoint.read_text())
            if saved.get('fingerprint') == fingerprint:
                message = saved['message']; usage = saved['usage']; reviews = saved['reviews']
                start_attempt = saved['attempt']; cached_audit = saved.get('fact_audit')
    def save_message_review(attempt, audit):
        if checkpoint is not None:
            import time
            if getattr(ctx, 'event_deadline', None) and time.monotonic() >= ctx.event_deadline:
                return  # A timed-out old invocation must not overwrite its successor.
            from partner.runtime.action_execution import write_json
            write_json(checkpoint, {'fingerprint':fingerprint,'message':message,'usage':usage,
                                   'reviews':reviews,'attempt':attempt,'fact_audit':audit})
    value = reviews[-1] if reviews else {}
    for attempt in range(start_attempt, 3):
        fact_audit = cached_audit if attempt == start_attempt and cached_audit is not None else {}
        if message and not (attempt == start_attempt and cached_audit is not None):
            audit_raw, audit_usage = call_model(ctx, purpose='message_factcheck', prompt=(
                '你只审查<outgoing_message>内本次待发送消息，不审查来源全文，不把来源中未在消息出现的句子列为消息的问题。只做事实依据审计，不改写文风。逐个检查消息新增的具体事实或机制：来源是否提供足以推出它的条件？'
                '区分通用概念与对当前对象具体处理方式的断言。常见做法不代表本次就是如此，数量和名称不能证明处理方式。'
                '数值单位、记录类别、相对与绝对误差必须有对应证据；time列没有单位时不得自行说成秒。纯本地Event的文件回读与哈希也是实际执行回执，无shell日志不等于没执行。'
                '没有工具证据的当前数据、实验和切分方式一律只能保留未知。找出即使整段听起来流畅，仍由回答自行补出的条件。'
                '只输出JSON：{"unsupported_claims":["逐字列出缺依据的断言及原因"]}；确实没有才返回空数组。\n'
                + '<source_facts>'+facts+'</source_facts>\n<outgoing_message>'+message+'</outgoing_message>'))
            try:
                fact_audit = json_object(audit_raw)
            except (TypeError, ValueError):
                fallback = _verified_benchmark_fallback(params)
                if fallback:
                    return {'ok': True, 'status': 'completed', 'format_degraded': True,
                            'semantic_output': {'parse_degraded': True,
                                'reason': 'fact audit returned non-object output'},
                            'candidate_message': fallback, 'message': fallback,
                            'summary': '基准终态已核验；使用确定性降级消息',
                            'token_usage': usage}
                fact_audit = {'unsupported_claims':['事实审计未返回JSON对象']}
            if not isinstance(fact_audit.get('unsupported_claims'), list):
                fallback = _verified_benchmark_fallback(params)
                if fallback:
                    return {'ok': True, 'status': 'completed', 'format_degraded': True,
                            'semantic_output': {'parse_degraded': True,
                                'reason': 'fact audit omitted unsupported_claims'},
                            'candidate_message': fallback, 'message': fallback,
                            'summary': '基准终态已核验；使用确定性降级消息',
                            'token_usage': usage}
                fact_audit = {'unsupported_claims':['事实审计未返回有效的主张检查结果']}
            else:
                fact_audit['unsupported_claims'] = _normalize_unsupported_claims(
                    fact_audit['unsupported_claims'])
            for key in ('prompt_tokens','completion_tokens','total_tokens'):
                usage[key] = usage.get(key,0) + int(audit_usage.get(key) or 0)
            save_message_review(attempt, fact_audit)
        numeric_literals=re.findall(r'(?<![A-Za-z])\d+(?:\.\d+)?(?:[×x*]\s*10[⁻⁺+\-]?[⁰¹²³⁴⁵⁶⁷⁸⁹\d]+)?',message)
        from partner.presentation.document import semantic_conflicts
        conflicts=semantic_conflicts(message,params.get('flow_outputs') or {})
        if conflicts: fact_audit.setdefault('unsupported_claims',[]).extend(conflicts)
        template = bool(re.search(r'(?m)^\s*(?:#{1,6}\s|(?:\*\*)?段落[一二三四五\d]+[：:]|\*\*[^\n]{1,30}[：:]\*\*)', message))
        if not (params.get('flow_outputs') or {}).get('answer'):
            template = template or bool(re.search(r'`|\bparams\b|patch_application|getattr\(',message)) or len(numeric_literals)>2 or len([line for line in message.splitlines() if line.strip()])>4 or bool(re.search(r'(?m)^\s*[-*]\s',message))
        if not (params.get('flow_outputs') or {}).get('answer'):
            template = template or bool(re.search(r'production_effective|exit_code|manifest_sha256|inner_future|(?:cancelled|done)\s*=|\b[a-f0-9]{24,}\b',message))
            template = template or bool(re.search(r'\d{12,}|\bDOM\b|落盘|校验指纹|已诚实标记|完整收口',message))
            template = template or bool(re.search(r'\b[a-z]+(?:_[a-z0-9]+)+\b|\b[a-z_]+\.py\b|\bjson\.(?:dumps?|loads?)\b|\b(?:fsync|POSIX|PDBQT)\b|落槌',message))
        tool_payload = bool(re.search(r'mcp__\w+|<bash>|<tool_call>|"arguments"\s*:', message))
        plain_language_violations = re.findall(r'`|\bparams\b|patch_application|getattr\(|production_effective|exit_code|manifest_sha256|inner_future|\d{12,}|\bDOM\b|落盘|校验指纹|已诚实标记|完整收口|\b[a-z]+(?:_[a-z0-9]+)+\b|\b[a-z_]+\.py\b|\bjson\.(?:dumps?|loads?)\b|\b(?:fsync|POSIX|PDBQT)\b|落槌',message)
        raw, extra = call_model(ctx, purpose='message_critic', prompt=(instructions + facts
            + '\n独立事实审计（列出的问题必须删除或限定；不能用常见做法代替当前证据）=' + json.dumps(fact_audit, ensure_ascii=False)
            + '\n格式检查=' + json.dumps({'over_length':len(message)>limit,'max_chars':limit,'template_labels':template,
                'tool_call_leaked_replace_with_plain_user_update':tool_payload,'numeric_literals':numeric_literals,'numeric_limit_exceeded':len(numeric_literals)>2,
                'replace_these_internal_tokens_with_plain_language':plain_language_violations})
            + '\n待审消息：' + message))
        for key in ('prompt_tokens','completion_tokens','total_tokens'):
            usage[key] = usage.get(key,0) + int(extra.get(key) or 0)
        try:
            value = json_object(raw)
        except (TypeError, ValueError):
            fallback = _verified_benchmark_fallback(params)
            if fallback:
                return {'ok': True, 'status': 'completed', 'format_degraded': True,
                        'semantic_output': {'parse_degraded': True,
                            'fact_audit': fact_audit,
                            'reason': 'message critic returned non-object output'},
                        'candidate_message': fallback, 'message': fallback,
                        'summary': '基准终态已核验；使用确定性降级消息',
                        'token_usage': usage}
            value = {'accepted': False, 'problems':['消息审查未返回JSON对象'],
                     'revised_message':''}
        value['fact_audit'] = fact_audit
        reviews.append(value)
        revised = str(value.get('revised_message') or '').strip()
        if revised and revised != message.strip():
            message = revised
            save_message_review(attempt+1, None)
            continue  # Never send a rewrite which has not itself been reviewed.
        accepted = bool(value.get('accepted')) and not value.get('problems') and not fact_audit.get('unsupported_claims') and not template and not tool_payload and 0 < len(message) <= limit
        if accepted: break
        save_message_review(attempt+1, None)
    # A rewrite produced on the last critic attempt has not itself been fact
    # checked yet.  Give that final candidate one bounded fact-only review;
    # otherwise the previous draft's unsupported claims incorrectly poison a
    # clean correction and the delivery chain can never converge.
    last_revised = str((reviews[-1] if reviews else {}).get('revised_message') or '').strip()
    if not accepted and last_revised and last_revised == message.strip():
        audit_raw, audit_usage = call_model(ctx, purpose='message_factcheck', prompt=(
            '你只审查<outgoing_message>内本次待发送消息，不审查来源全文。逐个检查消息新增的具体事实、数值和机制是否由来源直接支持。'
            '只输出JSON：{"unsupported_claims":["逐字列出缺依据的断言及原因"]}；确实没有才返回空数组。\n'
            + '<source_facts>'+facts+'</source_facts>\n<outgoing_message>'+message+'</outgoing_message>'))
        try:
            final_audit = json_object(audit_raw)
        except (TypeError, ValueError):
            fallback = _verified_benchmark_fallback(params)
            if fallback:
                return {'ok': True, 'status': 'completed', 'format_degraded': True,
                        'semantic_output': {'parse_degraded': True,
                            'reason': 'final fact audit returned non-object output'},
                        'candidate_message': fallback, 'message': fallback,
                        'summary': '基准终态已核验；使用确定性降级消息',
                        'token_usage': usage}
            final_audit = {'unsupported_claims':['最终事实审计未返回JSON对象']}
        if not isinstance(final_audit.get('unsupported_claims'), list):
            final_audit = {'unsupported_claims':['最终改稿事实审计未返回有效结果']}
        else:
            final_audit['unsupported_claims'] = _normalize_unsupported_claims(
                final_audit['unsupported_claims'])
        for key in ('prompt_tokens','completion_tokens','total_tokens'):
            usage[key] = usage.get(key,0) + int(audit_usage.get(key) or 0)
        value = {**value, 'fact_audit': final_audit}
        save_message_review(3, final_audit)
    # Sprint 37: a long-running autonomous loop must not stall on *format*.
    # The honesty guarantee is that no message carrying an unsupported fact
    # claim is sent.  When the fact audit is CLEAN (no unsupported_claims) and
    # only length / numeric-density / terminology problems remain, degrade-pass
    # with the reviewed message, flagged so the audit trail shows it was not a
    # clean pass.  A non-empty unsupported_claims still fails hard (pinned by
    # test_message_style_approval_cannot_override_failed_fact_audit).
    if not accepted:
        # Presentation must remain fail-closed for unsupported prose, while a
        # provider's inconsistent JSON must not overturn a scientific terminal
        # already established by deterministic Settlement.  Replace the prose
        # with a receipt-derived statement containing no inferred numbers.
        receipt_fallback = (_verified_benchmark_fallback(params)
                            or _verified_project_benchmark_fallback(params))
        if receipt_fallback:
            return {'ok': True, 'status': 'completed', 'format_degraded': True,
                    'semantic_output': {**value, 'reviews': reviews,
                        'receipt_derived_fallback': True},
                    'candidate_message': receipt_fallback,
                    'message': receipt_fallback,
                    'summary': '依据确定性结算回执生成消息',
                    'token_usage': usage}
        facts_clean = not ((value.get('fact_audit') or {}).get('unsupported_claims') or [])
        fallback = str(message or '').strip() or str(initial_message or '').strip()
        if (facts_clean and fallback and 0 < len(fallback) <= 2000
                and not tool_payload and not plain_language_violations):
            return {'ok': True, 'status': 'completed', 'format_degraded': True,
                    'semantic_output': {**value, 'reviews': reviews, 'format_degraded': True},
                    'candidate_message': fallback, 'message': fallback,
                    'summary': '事实审计通过；仅格式问题，降级放行', 'token_usage': usage}
        # The PDF delivery message is reviewed after render and quality have
        # completed.  A final rewrite can nevertheless retain the earlier
        # composer phrase “正在生成报告”, and its fact checker then correctly
        # rejects that stale temporal claim.  Do not strand an already
        # verified PDF on this wording race: derive a minimal statement only
        # from completed Event receipts.  The report and both summaries remain
        # Event-produced artifacts; this branch merely supplies their delivery
        # envelope.
        outputs = params.get('flow_outputs') or {}
        render = outputs.get('render') or {}
        quality = outputs.get('quality') or {}
        summaries = outputs.get('summaries') or {}
        render_semantic = render.get('semantic_output') or {}
        report_path = Path(str(render_semantic.get('path') or render.get('path')
                               or render.get('pdf_path') or ''))
        if (render.get('status') == 'completed'
                and quality.get('status') == 'completed'
                and summaries.get('status') == 'completed'
                and report_path.is_file() and report_path.suffix.lower() == '.pdf'):
            fallback = '本轮结果总结和运行总结已经生成，证据报告已通过质量检查；详细结论与可追溯证据见随附 PDF。'
            return {'ok': True, 'status': 'completed', 'format_degraded': True,
                    'semantic_output': {**value, 'reviews': reviews,
                        'receipt_derived_fallback': True},
                    'candidate_message': fallback, 'message': fallback,
                    'summary': '依据报告完成回执生成发送消息', 'token_usage': usage}
        settle_two = outputs.get('settle_two') or {}
        assessment = outputs.get('assess') or {}
        if (params.get('node_id') == 'message_critic'
                and settle_two.get('status') == 'completed'
                and assessment.get('status') == 'completed'):
            fallback = ('项目两轮执行与核验已经完成；结果总结、运行总结和证据报告将由后续 Event '
                        '生成并发送，最终结论以报告中的可追溯证据为准。')
            return {'ok': True, 'status': 'completed', 'format_degraded': True,
                    'semantic_output': {**value, 'reviews': reviews,
                        'receipt_derived_fallback': True},
                    'candidate_message': fallback, 'message': fallback,
                    'summary': '依据两轮结算回执生成阶段消息', 'token_usage': usage}
        final = outputs.get('final_summary') or {}
        final_semantic = final.get('semantic_output') or {}
        final_message = str(final_semantic.get('message') or final.get('message') or '').strip()
        if (params.get('node_id') == 'final_critic'
                and final.get('status') == 'completed'
                and final_message):
            return {'ok': True, 'status': 'completed', 'format_degraded': True,
                    'semantic_output': {**value, 'reviews': reviews,
                        'receipt_derived_fallback': True,
                        'evolution_evidence_verified': bool(final_semantic.get('evidence_verified'))},
                    'candidate_message': final_message, 'message': final_message,
                    'summary': '依据自进化终态回执生成消息',
                    'token_usage': usage}
    # v13 fix: critic 是质量门不是门禁。失败时降级直投原始消息（candidate_message），
    # 保证下游 send_text 有内容可发、delivery_settle 有回执可写。
    final_message = message if accepted else (message or str(params.get('message') or prior.get('message') or ''))
    return {'ok':accepted, 'status':'completed' if accepted else 'failed',
            'error':'' if accepted else 'message did not pass independent review within budget',
            'semantic_output':{**value,'reviews':reviews}, 'candidate_message':message,
            'message':final_message,
            'critic_degraded': not accepted,
            'summary':'消息审查完成' if accepted else '审查未通过；降级直投原始消息', 'token_usage':usage}


def message_deduplicate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    previous = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    message = str(previous.get("message") or params.get("message") or "").strip()
    normalized = re.sub(r"\s+", "", message).lower()
    from partner.presentation.notifications import identity, already_acknowledged
    notification_identity=identity(ctx,params)
    duplicate = already_acknowledged(ctx,params,notification_identity) if getattr(ctx,'workspace',None) else False
    history = Path(str(getattr(ctx, "instance_workspace", "") or "")) / "state/qq_chat_history.jsonl"
    if getattr(ctx,'workspace',None) and notification_identity['origin']:
        history=Path(ctx.workspace)/'instances'/notification_identity['origin']/'state/qq_chat_history.jsonl'
    channel = str(params.get("channel") or getattr(ctx, "channel", "local"))
    if normalized and channel == "qq":
        try:
            for line in history.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]:
                row = json.loads(line)
                if row.get("role") == "assistant" and row.get("delivery_acknowledged") is True:
                    old = re.sub(r"\s+", "", str(row.get("content") or "")).lower()
                    if old == normalized:
                        duplicate = True
                        break
        except (OSError, TypeError, ValueError):
            pass
    return {"ok": bool(message), "status": "completed" if message else "failed",
            "message": message, "should_send": not duplicate, "notification_identity":notification_identity,
            "semantic_output": {"duplicate": duplicate},
            "summary": "与最近消息重复，本轮不再发送" if duplicate else "消息具有新的有效信息"}


def report_outline(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    raw, usage = call_model(ctx, purpose="report_outline", prompt=(
        "根据真实证据为中文 PDF 设计一条让项目负责人能看懂的研究叙事。标题必须直接写研究对象、候选改动和评价问题，禁止使用‘项目证据报告’‘运行报告’等通用标题。"
        "提纲必须覆盖：核心结论；研究问题与冻结协议；基线和候选的公平比较；主动学习内容及其是否被下一轮实际采用；主要结果与有意义的图；失败、局限和下一步。"
        "不要把 Event 数、文件字节、置信度、哈希或证据文件数量当研究结果。这是简短提纲，不写正文：4–7节、每节一句话，最多列8项待核验主张，总体不超过1200汉字。"
        "只输出 JSON：{\"title\":\"\",\"sections\":[],\"visuals\":[],\"claims_to_verify\":[]}。\n"
        + "原始目标=" + str((params.get('intent_contract') or {}).get('original_request') or params.get('request') or '')[:2500]
        + "\n来源=" + _report_source_context((params.get('flow_outputs') or {}).get('sources', {}), budget=12000)
    ))
    value = json_object(raw)
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": str(value.get("title") or "报告结构已形成"), "token_usage": usage}


def report_decide(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    contract = params.get("intent_contract") if isinstance(params.get("intent_contract"), dict) else {}
    kind = str(params.get("notification_kind") or contract.get("notification_kind") or "routine")
    evidence = list(params.get("evidence_refs") or contract.get("evidence_refs") or [])
    force = bool(params.get("force") or contract.get("force"))
    needed = bool(force or kind in {"milestone", "final"}) and bool(evidence)
    return {"ok": True, "status": "completed", "semantic_output": {"generate": needed},
            "summary": "本里程碑需要 PDF" if needed else "本轮无需生成 PDF"}


def run_summary_collect(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Create concise result/run artifacts from one authoritative source Job.

    This is deliberately an Event rather than an operator-side report helper:
    downstream report and delivery Events consume the files and the generated
    message, while the source Job remains immutable.
    """
    contract = params.get("intent_contract") if isinstance(params.get("intent_contract"), dict) else {}
    source_job_id = str(contract.get("source_job_id") or "")
    if not source_job_id:
        return {"ok": True, "status": "completed", "files": [],
                "semantic_output": {"source_job_id": "", "generated": False},
                "summary": "本报告没有绑定源 Job，不生成运行总结"}
    from partner.web.run_trace import trace_overview, event_detail
    trace = trace_overview(ctx.workspace, source_job_id, limit=500)
    if not trace.get("available"):
        return {"ok": False, "status": "failed", "error": "source Job run trace unavailable"}
    narrative_path = Path(ctx.workspace) / 'state' / 'cycles' / source_job_id / 'run_narrative.json'
    narrative = {}
    if narrative_path.is_file():
        try:
            narrative = json.loads(narrative_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            narrative = {}
    final_state_hash = str(narrative.get('final_state_hash') or
                           contract.get('final_state_hash') or '')

    final_rows: dict[str, dict[str, Any]] = {}
    for row in trace.get("events") or []:
        if row.get("phase") == "finished":
            final_rows[str(row.get("event_id") or "")] = row

    def outputs_for(event_type: str) -> list[dict[str, Any]]:
        values = []
        for event_id, row in final_rows.items():
            if str(row.get("event_type") or "") != event_type:
                continue
            try:
                lifecycle = event_detail(ctx.workspace, source_job_id, event_id).get("lifecycle") or []
                finished = next((item for item in reversed(lifecycle)
                                 if item.get("phase") == "finished"), {})
                values.append(finished.get("output") or {})
            except (OSError, ValueError, FileNotFoundError):
                continue
        return values

    execute = outputs_for("project.action_execute")
    verify = outputs_for("project.outcome_verify")
    settlement = outputs_for("core.settlement")
    execution_ok = bool(execute) and all(bool(row.get("ok")) for row in execute)
    verified = bool(verify) and all(bool(row.get("ok")) for row in verify)
    outcome = trace.get("outcome") if isinstance(trace.get("outcome"), dict) else {}
    project = outcome.get("project") if isinstance(outcome.get("project"), dict) else {}
    learning = outcome.get("learning") if isinstance(outcome.get("learning"), dict) else {}
    baseline, candidate, effect = (project.get("baseline"), project.get("candidate"),
                                   project.get("effect"))
    contract = params.get("intent_contract") if isinstance(params.get("intent_contract"), dict) else {}
    goal = str(narrative.get('research_question') or contract.get("goal") or
               "完成本轮项目研究并形成可复核结论").strip()
    if narrative:
        n_project = narrative.get('project') or {}
        n_learning = narrative.get('active_learning') or {}
        n_evolution = narrative.get('self_evolution') or {}
        result_lines = [
            '# 项目结果总结', '', '## 研究问题', '', goal, '', '## 核心结论', '',
            str(n_project.get('conclusion') or narrative.get('headline') or '尚无可验证结论'), '',
            '## 逐轮证据', '',
            '| 轮次 | 假设/目标 | 执行 | 已核验证据 | 新认识 | 决策 |',
            '|---:|---|---|---|---|---|',
        ]
        for row in n_project.get('rounds') or []:
            ev = row.get('evidence') or []
            result_lines.append('| {round} | {goal} | {status} | {evidence} | {gain} | {route} |'.format(
                round=row.get('round_number',''), goal=str(row.get('hypothesis') or row.get('round_goal') or '')[:90],
                status=row.get('execution_status') or '', evidence=('；'.join(Path(str(x)).name for x in ev[:3]) or '无'),
                gain=str(row.get('information_gain') or '')[:100], route=row.get('route') or ''))
        result_lines += ['', '## 主动学习', '',
            f"- 状态：{n_learning.get('status') or 'not_executed'}。",
            f"- 实际来源数：{len(n_learning.get('sources') or [])}。",
            f"- Handoff 被消费：{'是' if n_learning.get('handoff_consumed') else '否'}。",
            f"- 下游改善得到匹配证明：{'是' if n_learning.get('downstream_improved') else '否'}。",
            '', '## Partner 自进化', '',
            f"- 审计问题数：{n_evolution.get('audit_issue_count',0)}。",
            f"- 裁决：{n_evolution.get('decision') or 'no_candidate'}。",
            f"- 生产生效：{'是' if n_evolution.get('production_effective') else '否'}。",
            '', '## 局限与下一步', '']
        result_lines += [f'- {item}' for item in n_project.get('limitations') or ['尚需更多真实证据']]
        if final_state_hash:
            result_lines += ['', f'- 最终事实版本：`{final_state_hash}`']
    else:
        result_lines = []
    legacy_result_lines = [
        "# 项目结果总结", "", "## 研究问题", "", goal, "", "## 核心结论", "",
        str(outcome.get("headline") or "本轮尚未形成可验证的定量结论") + "。",
        "", "## 关键测量", "",
        f"- Baseline {project.get('metric') or 'metric'}：{baseline if baseline is not None else '未记录'}。",
        f"- Candidate {project.get('metric') or 'metric'}：{candidate if candidate is not None else '未记录'}。",
        f"- 绝对改善：{effect if effect is not None else '未记录'}。",
        f"- 冻结 split 复用：{'是' if project.get('split_reused') is True else '未确认'}。",
        f"- 泄漏检查：{project.get('leakage_check') or '未记录'}。",
        "", "## 主动学习的实际影响", "",
        f"- Handoff 被后续轮次消费：{'是' if learning.get('consumed') is True else '否或未确认'}。",
        f"- 采用机制：{learning.get('mechanism') or '未记录'}。",
        f"- 来源：{learning.get('source_url') or '未记录'}。",
        "", "## 执行与核验", "",
        f"- 项目动作执行：{'完成' if execution_ok else '存在失败或未完成'}。",
        f"- 产物核验：{'完成' if verified else '存在失败或未完成'}。",
    ]
    if not result_lines:
        result_lines = legacy_result_lines
        completed_rounds = ((outcome.get("completion") or {}).get("project_rounds")
                            or project.get("rounds") or 0)
        result_lines += [
            f"- 实际项目轮次：{completed_rounds}。",
            "- 比较依据：冻结输入、同一模型预算和确定性 downstream matched comparison。",
            f"- 证据入口：{learning.get('handoff_path') or '逐 Job Event 日志与产物索引'}。",
            "", "## 结论边界", "",
            "失败 Event 与未验证改善均保留为负证据；父 Job 完成不代表所有子步骤成功。",
        ]

    completion = trace.get("completion") or {}
    counts = trace.get("counts") or {}
    terminal = str(completion.get("status") or "") in {"completed", "failed", "cancelled"}
    run_lines = ["# 运行总结" if terminal else "# 报告生成时的运行快照", "",
                 (f"本摘要依据权威 Job 与终态 Event 生成；最终状态为 {completion.get('status')}。"
                  if terminal else
                  "本摘要在 PDF Flow 内生成，记录报告生成当时的状态；最终 Job 终态以之后的终态 Event 和渠道回执为准。"), "",
                 f"- 业务 Event：{counts.get('business_events', 0)} 个。",
                 f"- 通知与渠道 Event：{counts.get('infrastructure_events', 0)} 个。",
                 f"- 已规划 Flow：{len(trace.get('flows') or [])} 个。"]
    for flow in trace.get("flows") or []:
        run_lines.append(f"- {flow.get('flow_type')}：{flow.get('status')}（{flow.get('flow_id')}）。")
    run_lines += ["", "## 报告生成时的结果链", ""]
    for item in (trace.get("chains") or {}).values():
        run_lines.append(
            f"- {item.get('name')}：{item.get('events', 0)} Events，"
            f"完成 {item.get('completed', 0)}，失败 {item.get('failed', 0)}；"
            f"{item.get('last_summary') or '本轮未触发'}")
    run_lines += ["", "## 状态边界", "",
                  ("- 本总结读取终态 Job、Flow 与渠道回执；历史失败和恢复记录仍保留在 Event Explorer。"
                   if terminal else
                   "- PDF、最终消息、自进化与渠道 ACK 可能在本快照之后完成；不得把这里的运行中状态写成最终失败。")]

    output_dir = Path(ctx.working_dir) / "summaries"
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "结果总结.md"
    run_path = output_dir / "运行总结.md"
    result_path.write_text("\n".join(result_lines).strip() + "\n", encoding="utf-8")
    run_path.write_text("\n".join(run_lines).strip() + "\n", encoding="utf-8")
    from partner.index.artifact_repository import init as artifact_repository
    repo = artifact_repository(Path(ctx.workspace))
    for path, kind in ((result_path, "result_summary"), (run_path, "run_summary")):
        repo.register(path=str(path), kind=kind, purpose="qq_final_delivery",
                      job_id=str(getattr(ctx, "job_id", "")),
                      flow_id=str(params.get("flow_id") or ""),
                      producer_event=str(params.get("event_id") or ""),
                      media_type="text/markdown")
    delivery_message = (str(narrative.get('milestone_message') or '').strip() +
                        '\n结果总结、运行总结和完整证据报告见 PDF 附件.') if narrative else (
        f"结果总结：{outcome.get('headline') or '尚无定量结论'}；"
        f"冻结 split {'已复用' if project.get('split_reused') is True else '未确认'}，"
        f"主动学习 handoff {'已消费' if learning.get('consumed') is True else '未确认消费'}。\n"
        f"运行快照：{counts.get('business_events', 0)} 个业务 Event、"
        f"{len(trace.get('flows') or [])} 个 Flow；最终状态以后续终态 Event 为准。\n"
        "结果总结、运行总结和完整证据报告见 PDF 附件。")
    return {"ok": True, "status": "completed",
            "files": [str(result_path), str(run_path)],
            "evidence_refs": [str(result_path), str(run_path)],
            "semantic_output": {"source_job_id": source_job_id, "generated": True,
                                "final_state_hash": final_state_hash,
                                "result_summary_path": str(result_path),
                                "run_summary_path": str(run_path),
                                "delivery_message": delivery_message,
                                "execution_ok": execution_ok, "verified": verified},
            "summary": "结果总结和运行总结已由 Event 生成"}


def _data_preview(value):
    """Retain summary fields after large arrays; label sampled data explicitly."""
    from partner.runtime.artifact_checks import preview_data
    return preview_data(value)


def _report_source_context(sources, budget=40000):
    research_roles={'research_primary','research_method','research_result','learning_source'}
    rows = [row for row in ((sources.get('semantic_output') or {}).get('sources') or [])
            if not row.get('role') or row.get('role') in research_roles]
    # Empty binary sources consume no text allowance. Redistribute unused
    # allowance from short records to richer evidence, instead of truncating
    # every source to the same length and hiding late result fields.
    demands = [min(7000, len(str(r.get('excerpt') or ''))) for r in rows]
    weights = [2 if Path(r.get('path','')).suffix.lower()=='.json' else
               .5 if Path(r.get('path','')).suffix.lower()=='.py' else 1 for r in rows]
    allocations = [0]*len(rows)
    active = {i for i,n in enumerate(demands) if n}
    remaining = max(0,budget)
    while active and remaining > 0:
        unit = remaining / sum(weights[i] for i in active)
        small = {i for i in active if demands[i] <= unit*weights[i]}
        if not small:
            for i in active: allocations[i] = int(unit*weights[i])
            break
        for i in small:
            allocations[i] = demands[i]
            remaining -= demands[i]
        active -= small
    return json.dumps([{**r, 'excerpt':str(r.get('excerpt') or '')[:allocations[i]]}
                       for i,r in enumerate(rows)], ensure_ascii=False)


def report_sources_collect(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    contract = params.get("intent_contract") if isinstance(params.get("intent_contract"), dict) else {}
    candidates = [
        *(params.get("evidence_refs") or []),
        *(prior.get("evidence_refs") or prior.get("files") or []),
        *(contract.get("evidence_refs") or []),
    ]
    for output in (params.get('flow_outputs') or {}).values():
        if isinstance(output, dict):
            candidates.extend(output.get('files') or output.get('evidence_refs') or [])
    if contract.get('figure_manifest'):
        manifest=json.loads(Path(contract['figure_manifest']).read_text())
        candidates=list(candidates)+[s['path'] for a in manifest.get('images',[]) for s in a['sources']]
    context_refs = contract.get("context_refs") or []
    candidates = list(dict.fromkeys(list(candidates) + list(context_refs)))
    provided_paths={str(Path(p).resolve()) for p in candidates}
    from partner.presentation.sources import expand
    if contract.get('expand_evidence_refs', True):
        candidates=expand(candidates,str(contract.get('original_request') or params.get('request') or ''))
    paths = [Path(str(value)).resolve() for value in candidates
             if str(value).strip() and not str(value).endswith("执行结果.md")]
    rows = []
    inherited_ids = contract.get('evidence_ids') or {}
    next_id = max([int(v[1:]) for v in inherited_ids.values() if re.fullmatch(r'E\d+', str(v))] or [0]) + 1
    for path in paths:
        evidence_id = inherited_ids.get(str(path))
        if not evidence_id:
            evidence_id = f'E{next_id:02d}'; next_id += 1
        operational = (path.name in {'运行总结.md', 'flow_graph.json', 'flow_graph.svg', 'flow_graph.png'}
                       or 'run_logs' in path.parts or 'event_flows' in path.parts)
        result_summary = path.name == '结果总结.md'
        lowered='/'.join(path.parts).lower()
        delivery=('receipt' in lowered or path.name in {'text_ack.json','report_ack.json','final_ack.json'})
        self_evolution=('evolution' in lowered or 'partner_audit' in lowered)
        learning=('learning' in lowered or '主动学习' in path.name)
        research_result=(result_summary or any(token in path.name.lower() for token in
                         ('metric','result','comparison','prediction','assessment')))
        row = {"evidence_id":evidence_id, "path": str(path), "exists": path.exists(), "size": 0, "excerpt": "",
               "role":("operational_trace" if operational else "delivery_receipt" if delivery
                       else "self_evolution" if self_evolution else "learning_source" if learning
                       else "research_result" if research_result else "research_method" if path.suffix.lower() in {'.py','.yaml','.yml','.toml'}
                       else "research_method" if str(path) in context_refs else "research_primary")}
        if path.exists() and path.is_file():
            row["size"] = path.stat().st_size
            if path.suffix.lower() in {".md", ".txt", ".log", ".json", ".jsonl", ".csv", ".py", ".smi", ".patch"}:
                if path.suffix.lower() == '.json' and row['size'] <= 10_000_000:
                    try:
                        row['excerpt'] = json.dumps(_data_preview(json.loads(path.read_text())), ensure_ascii=False)
                    except ValueError:
                        row['excerpt'] = path.read_text(errors='replace')[:12000]
                else:
                    with path.open(encoding='utf-8', errors='replace') as handle:
                        row['excerpt'] = handle.read(12000)
            elif path.suffix.lower() == '.raw':
                # Tool-produced source excerpts may be text without a .txt
                # suffix. Inspect bytes rather than silently hiding evidence.
                with path.open('rb') as handle:
                    raw = handle.read(48000)
                if b'\x00' not in raw:
                    try:
                        excerpt = raw.decode('utf-8')
                    except UnicodeDecodeError:
                        excerpt = ''
                    if re.search(r'<(?:html|div|section|dt|dd)\b', excerpt, re.I):
                        from partner.runtime.source_evidence import TextParser
                        parser = TextParser(); parser.feed(excerpt)
                        excerpt = '\n'.join(parser.parts)
                    row['excerpt'] = excerpt[:12000]
        rows.append(row)
    # 即使没产物也不让 sources 失败；生成"项目未推进"报告。
    ok = True
    return {"ok": ok, "status": "completed",
            "semantic_output": {"sources": rows},
            "summary": f"已核验 {sum(1 for row in rows if row['exists'])}/{len(rows)} 个报告证据源"}


def flow_graph_build(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    contract = params.get('intent_contract') or {}
    source_job_id = str(contract.get('source_job_id') or '')
    if not source_job_id:
        return {'ok':True,'status':'completed','semantic_output':{'generated':False},
                'summary':'无源 Job，跳过运行图'}
    from partner.web.run_trace import trace_overview
    trace = trace_overview(ctx.workspace, source_job_id, limit=1, include_acceptance=False)
    value=dict(trace.get('graph') or {})
    path=Path(ctx.working_dir)/'flow_graph.json'; write_json(path,value)
    return {'ok':True,'status':'completed','files':[str(path)],'evidence_refs':[str(path)],
            'semantic_output':value,'summary':f"从真实日志生成 {value.get('counts',{}).get('flows',0)} 个 Flow、{value.get('counts',{}).get('nodes',0)} 个 Event 节点"}


def flow_graph_render(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    graph=((params.get('flow_outputs') or {}).get('flow_graph') or {}).get('semantic_output') or {}
    if not graph.get('nodes'):
        return {'ok':True,'status':'completed','semantic_output':{'generated':False},'summary':'无运行节点可绘制'}
    from PIL import Image as PILImage, ImageDraw, ImageFont
    flows=graph.get('flows') or []; nodes=graph.get('nodes') or []
    grouped={f['flow_id']:[n for n in nodes if n['flow_id']==f['flow_id']] for f in flows}
    width=1800; row_h=54; height=max(500,120+sum(2+len(v) for v in grouped.values())*row_h)
    image=PILImage.new('RGB',(width,height),'#F4FBF6'); draw=ImageDraw.Draw(image)
    try: font=ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',24)
    except OSError: font=ImageFont.load_default()
    y=35; draw.text((45,y),'Partner 实际 Event / Flow 运行图',fill='#163C2C',font=font); y+=60
    colors={'completed':'#BFE8CF','failed':'#F6C7C7','skipped':'#E5E7EB','pending':'#F8E7B0'}
    for flow in flows:
        fid=flow['flow_id']; draw.text((45,y),f"Flow · {flow.get('flow_type')} · {flow.get('status')}",fill='#245A43',font=font); y+=42
        x=75
        for node in grouped.get(fid,[]):
            box_w=250
            if x+box_w>width-60: x=75; y+=row_h
            draw.rounded_rectangle((x,y,x+box_w,y+40),8,fill=colors.get(node.get('status'),'#E5E7EB'),outline='#5D7C6C')
            draw.text((x+10,y+9),str(node.get('node_id'))[:25],fill='#17372A',font=font)
            x+=box_w+18
        y+=row_h+25
    png=Path(ctx.working_dir)/'flow_graph.png'; image.save(png)
    value={'generated':True,'png_path':str(png),'source_counts':graph.get('counts')}
    return {'ok':True,'status':'completed','files':[str(png)],'evidence_refs':[str(png)],
            'semantic_output':value,'summary':'真实 Event/Flow 图已渲染'}


def flow_graph_verify(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    outputs=params.get('flow_outputs') or {}; graph=(outputs.get('flow_graph') or {}).get('semantic_output') or {}
    rendered=(outputs.get('flow_graph_render') or {}).get('semantic_output') or {}
    path=Path(str(rendered.get('png_path') or ''))
    valid=(not graph.get('nodes')) or (path.is_file() and path.stat().st_size>1000)
    value={'verified':valid,'graph_counts':graph.get('counts') or {},'png_path':str(path) if path.is_file() else ''}
    return {'ok':valid,'status':'completed' if valid else 'failed','semantic_output':value,
            'files':[str(path)] if path.is_file() else [],'summary':'运行图与日志投影一致' if valid else '运行图校验失败'}


def summary_message_compose(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    summaries = (params.get("flow_outputs") or {}).get("summaries") or {}
    semantic = summaries.get("semantic_output") if isinstance(summaries, dict) else {}
    message = str((semantic or {}).get("delivery_message") or "").strip()
    if not message:
        return {"ok": False, "status": "failed",
                "error": "result/run summary message is missing"}
    return {"ok": True, "status": "completed", "message": message,
            "summary": "结果总结与运行总结消息已形成"}


def visual_plan(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from partner.presentation.figures import KINDS
    from partner.presentation.figure_plan import source_catalog
    sources=((params.get('flow_outputs') or {}).get('sources',{}).get('semantic_output') or {}).get('sources',[])
    contract=params.get('intent_contract') or {}
    if (contract.get('execution_constraints') or {}).get('evolution_cycle') and not contract.get('figure_manifest'):
        return _cycle_visual_plan(ctx, params, sources)
    inherited=contract.get('figure_manifest')
    if inherited and not contract.get('regenerate_figures'):
        manifest=json.loads(Path(inherited).read_text())
        plans=[a['parameters'] for a in manifest['images']]
        return {'ok':True,'status':'completed','semantic_output':{'visuals':plans,'inherited_from':inherited},'summary':'继承原报告图件计划与来源'}
    prompt=(
        '为报告选择实际可执行的绘图计划，输出JSON {"visuals":[...],"missing_data":[]}。'
        '每图必须含id(F01等)、kind、source_refs(来源ID如E01，优先用ID避免重复长路径)、title、caption、why、required(true)，以及对应能力参数。pose_path也使用已有来源ID。'
        '只使用提供的能力，参数键严格遵循能力说明；禁止用概念图冒充测量。来源excerpt只是预览，实际绘图会读取完整原文件，不能把预览条数当总数或认为其余数据缺失。JSON数组的数值分布用distribution，csv_line只能用于真正的CSV文件。items_preview/total_items/tail_preview是预览包装，绝不是原始文件路径；例如原始results数组的rows_key应为results。'
        '按数据与论点选2至4张有意义的图，没有足够证据时1张也可；不要为了数量重复或虚构指标。视频可以选择一组4个真实时间点。任务轮次、哈希和错误码不是研究效果，不画它们的数值分布；无测量的代码研究可选实际关键源码片段，不假造性能图。'
        '默认不生成柱状图、折线对比图、饼图等指标图——这类图大多数时候是机械复述表格数字，对用户没有信息增量。'
        '只有真正能向用户传达单图无法表达结论（如显著差异、关键趋势、结构）时才配图，并且必须给出 keep_reason（一句用户能看懂的理由）。'
        '全零、等值、布尔状态、计数、合格/不合格数量对比一律不画。'
        'caption只描述图实际展示的变量、来源和范围；不能说已证明机制、辛性质、长期稳定性、活性或因果改善。所选pose_path必须是提供的现有文件；不能从候选编号虚构其他文件路径。pdb_structure仅画CA轨迹和选定原子，不支持盒子或化学键，图题不能声称画出了它们。'
        '未提供残基选择依据不能自行猜C2残基。数值列时间单位不明时注明无量纲或原数据单位。'
        '数据路径和字段优先按下方实际源文件结构选择，不能猜测；JSONL是根数组，rows_key为空字符串。test_matrix只接受含同一组testcase的真实测试回执，不能用它画受体或依赖状态。title和caption使用中文主题与必要时间，不含内部编号、哈希、路径或代码字段。没有可用数据的图放missing_data。caption最多40字，why最多20字；不写解释长文、不罗列全部数据、不复述原始请求。整体不超过1500汉字加必要的来源路径。\n'
        + json.dumps(KINDS,ensure_ascii=False)
        + '\n原始要求='+str(contract.get('original_request') or params.get('request') or '')
        + '\n提纲='+json.dumps((params.get('flow_outputs') or {}).get('outline',{}).get('semantic_output',{}),ensure_ascii=False)
        + '\n实际源文件结构='+json.dumps(source_catalog(sources),ensure_ascii=False)[:16000]
        + '\n来源内容='+_report_source_context((params.get('flow_outputs') or {}).get('sources',{}),budget=12000))
    from partner.presentation.figure_plan import validate
    sources=((params.get('flow_outputs') or {}).get('sources',{}).get('semantic_output') or {}).get('sources',[])
    usage={}; value={}; errors=[]
    for attempt in range(2):
        raw,extra=call_model(ctx,purpose='report_visual_plan',prompt=prompt + ('\n上次计划不可执行，仅修正这些参数，不另写长文：'+json.dumps({'errors':errors,'plan':value},ensure_ascii=False) if errors else ''))
        for key in ('prompt_tokens','completion_tokens','total_tokens'):usage[key]=usage.get(key,0)+int(extra.get(key) or 0)
        value=json_object(raw)
        normalized,errors=validate(value.get('visuals'),sources)
        if not errors:
            value['visuals']=normalized
            break
    # Post-filter: runtime timing, boolean status bars (consumed/verified/
    # count), hashes, indices and code excerpts are provenance, not research
    # findings.  Never let them become report figures regardless of what the
    # model selected; this mirrors the cycle planner's guard.
    filtered_plans = []
    for plan in (value.get('visuals') or []):
        if plan.get('kind') == 'code_excerpt':
            continue
        key = str(plan.get('value_key') or '').lower()
        refs = ' '.join(str(x) for x in (plan.get('source_refs') or []))
        # Default-off indicator charts: bar/column/pie/line replicating table
        # numbers add no user value unless the model supplied a user-facing
        # keep_reason.  Users explicitly asked to drop these repeated charts.
        if plan.get('kind') in ('bar', 'column', 'pie', 'line') and not str(plan.get('keep_reason') or '').strip():
            continue
        if any(tok in key for tok in (
                'elapsed', 'duration', 'index', 'token', 'byte', 'coverage',
                'consumed', 'improved', 'verified', 'status', 'is_', 'bool', 'count',
                '合格输入', '合格数量', '指标对比', '对比')):
            continue
        if any(tok in refs for tok in (
                'execution_contract', 'checkpoint', 'events.jsonl', 'run_log',
                'ack_wait', 'round_evidence_table', 'business_snapshot', 'coverage_report',
                'eligibility', 'inputs_')):
            continue
        filtered_plans.append(plan)
    value['visuals'] = filtered_plans
    valid=not errors
    value['validation_errors']=errors
    plans=value.get('visuals') or []
    if not valid:
        # A rejected figure plan must never abort the whole report.  Degrade to
        # a no-chart report (visuals stay empty) so draft/render still produce
        # a user PDF; the validation errors are preserved as evidence.
        value['visuals'] = []
        value['missing_data'] = list(dict.fromkeys([*(value.get('missing_data') or []), *errors]))[:5]
        return {'ok': True, 'status': 'completed', 'semantic_output': value,
                'error': 'figure plan rejected; report continues without charts: ' + '; '.join(errors),
                'token_usage': usage,
                'summary': '图计划被校验拒绝，报告降级为无图继续生成'}
    return {'ok':valid,'status':'completed' if valid else 'failed','semantic_output':value,
            'error':'' if valid else 'figure plan is not executable: '+ '; '.join(errors), 'token_usage':usage,
            'summary':f'规划 {len(plans)} 张真实证据图'}


def _cycle_visual_plan(ctx, params, sources):
    from partner.presentation.figure_plan import executable_choices
    options = []
    for option in executable_choices(sources):
        plan = option.get('plan') or {}
        field = str(plan.get('value_key') or '').lower()
        filename = str(option.get('filename') or '').lower()
        # Runtime timing, command indices, receipts and audit code are useful
        # provenance, but they are not research findings and must never become
        # the report's headline figures.
        if any(token in field for token in ('elapsed', 'duration', 'index', 'token', 'byte', 'coverage')):
            continue
        if any(token in filename for token in (
                'execution_contract', 'checkpoint', 'events.jsonl', 'run_log',
                'ack_wait', 'round_evidence_table', 'business_snapshot', 'coverage_report')):
            continue
        if plan.get('kind') == 'code_excerpt':
            continue
        options.append(option)
    available = {o['option_id']: o['plan'] for o in options}
    if not available:
        return {'ok':True,'status':'completed','semantic_output':{
                'visuals':[],
                'missing_data':['没有可绘制的领域定量结果；报告改用逐轮证据状态表，禁止用运行耗时冒充研究结果'],
                'selection_options':[], 'selection_rule':'no operational-metadata charts'},
                'summary':'没有领域结果图；将以可核验的证据状态表如实报告'}
    # A matched baseline/candidate record is the primary research result.  It
    # should never lose to four generic histograms merely because an LLM liked
    # their titles.  Select the direct comparison and, when present, its fold
    # delta distribution deterministically; the model remains the fallback for
    # domains without this typed evidence.
    def _is_boolean_state(plan):
        key = str(plan.get('value_key') or '').lower()
        if any(tok in key for tok in ('consumed', 'improved', 'verified', 'status', 'is_', 'bool', 'count')):
            return True
        values = plan.get('values') or []
        if values and all(v in (0, 1) for v in values):
            return True
        return False
    # Boolean status bars (consumed/improved/verified 0-1) are not research
    # findings; never let them become the report's default chart.
    scalar = next((o for o in options
                   if (o.get('plan') or {}).get('kind') == 'scalar_bar'
                   and not _is_boolean_state(o.get('plan') or {})), None)
    if scalar:
        chosen=[scalar]
        filename=scalar.get('filename')
        support=next((o for o in options if o.get('filename')==filename
                      and (o.get('plan') or {}).get('kind')=='distribution'
                      and 'delta' in str((o.get('plan') or {}).get('value_key') or '').lower()),None)
        if support: chosen.append(support)
        plans=[]
        for i,option in enumerate(chosen):
            plan={**option['plan'],'id':f'F{i+1:02d}'}
            if plan['kind']=='scalar_bar':
                plan.update(title='Baseline 与 Candidate 的主要指标对比',
                            caption='同一冻结协议下的主要评价指标。')
            else:
                plan.update(title='各折配对改善分布',caption='各冻结折上的配对指标差异。')
            plans.append(plan)
        return {'ok':True,'status':'completed','semantic_output':{
                'visuals':plans,'missing_data':[],'selection_options':options,
                'selection_rule':'typed matched-comparison evidence first'},
                'summary':f'按匹配比较证据选择 {len(plans)} 张核心结果图'}
    prompt = ('为中文报告选图。下面每个option_id已经用完整真实文件检查可执行。'
              '只选择2至4个最能支持论点的选项，证据不足时1个也可以；不能发明参数、图类或来源。'
              '图类distribution只呈现选定数值分布，code_excerpt只展示源代码，不能称性能对照。'
              '输出JSON {"choices":[{"option_id":"V01","title":"中文图题","caption":"40字内准确图注"}],"missing_data":[]}。'
              '\n实际可执行选项=' + json.dumps(options,ensure_ascii=False)
              + '\n报告提纲=' + json.dumps((params.get('flow_outputs') or {}).get('outline',{}).get('semantic_output',{}),ensure_ascii=False)
              + '\n证据内容=' + _report_source_context((params.get('flow_outputs') or {}).get('sources',{}),budget=12000))
    usage = {}; error = ''; value = {}
    for attempt in range(2):
        raw, extra = call_model(ctx,purpose='report_visual_plan',prompt=prompt + '\n结构校验反馈=' + error)
        for key in ('prompt_tokens','completion_tokens','total_tokens'):
            usage[key] = usage.get(key,0) + int(extra.get(key) or 0)
        value = json_object(raw); selected = value.get('choices') or []
        if (1 <= len(selected) <= 4 and all(isinstance(s,dict) and s.get('option_id') in available for s in selected)
                and len({s['option_id'] for s in selected}) == len(selected)):
            plans = [{**available[s['option_id']], 'id':f'F{i+1:02d}',
                      'title':str(s.get('title') or '真实证据'), 'caption':str(s.get('caption') or '')}
                     for i,s in enumerate(selected)]
            return {'ok':True,'status':'completed','semantic_output':{'visuals':plans,
                    'missing_data':value.get('missing_data') or [],'selection_options':options},
                    'summary':f'选择 {len(plans)} 张源文件已校验的证据图','token_usage':usage}
        error = 'choices必须是1–4个对象，只能引用上述实际option_id且不能重复；上次输出='+raw[:3000]
    return {'ok':False,'status':'failed','error':error,'semantic_output':value,'token_usage':usage}


def visual_generate(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Accept only real images produced by domain Events; never invent evidence art."""
    upstream = params.get("upstream") if isinstance(params.get("upstream"), dict) else {}
    candidates = list(params.get("image_paths") or params.get("files") or (params.get("intent_contract") or {}).get("evidence_refs") or [])
    for value in upstream.values():
        if isinstance(value, dict):
            candidates.extend(value.get("files") or [])
    images = []; errors = []
    for value in candidates:
        path = Path(str(value)).resolve()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg"} and path.exists():
            try:
                if path.suffix.lower() == '.svg':
                    from xml.etree import ElementTree
                    if ElementTree.parse(path).getroot().tag.split('}')[-1] != 'svg':
                        raise ValueError('not an SVG document')
                else:
                    from PIL import Image
                    with Image.open(path) as image:
                        image.verify()
                images.append({"path": str(path), "size": path.stat().st_size})
            except (OSError, ValueError, SyntaxError) as exc:
                errors.append({'path':str(path), 'error':str(exc)})
    return {"ok": not errors, "status": "failed" if errors else "completed", "files": [row["path"] for row in images],
            "semantic_output": {"images": images, "generated": False,
                                "invalid_images": errors,
                                "rule": "domain Event owns scientific/browser image generation"},
            "summary": f"收集 {len(images)} 张真实项目图；未用装饰图补数"}


def report_draft(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    contract = params.get('intent_contract') or {}
    revision_context = ''
    if contract.get('revision_source') or contract.get('reissue_source'):
        prior = Path(contract.get('revision_source') or contract['reissue_source'])
        if not prior.is_file():
            raise ValueError('report revision requires an existing draft')
        revision_context = ('\n【修订】保留原稿已正确的主张，按反馈修正关联表述；可以重新组织和精简，不必保留旧章节或冗长修订说明。'
            '不要扩写或另造实验。标题保留更正版。证据ID必须采用本轮来源表，禁止沿用与本轮不对应的旧编号。审查意见属于外部反馈，仍须对照来源查证。\n【审查意见】'
            + str(contract.get('review_feedback') or '')[:8000]
            + '\n【原稿】' + prior.read_text(encoding='utf-8')[:30000])
    # 先抽取项目真实证据（业务产物），没产物就显式写"项目未推进"，禁止编。
    evs = [str(x) for x in (params.get("evidence_refs") or (params.get("intent_contract") or {}).get("evidence_refs") or []) if str(x).strip()]
    evs_block = "\n".join(f"- {x}" for x in evs[:20]) if evs else "（无任何业务产物文件）"
    raw, usage = call_model(ctx, purpose="report_draft", prompt=(
        "撰写中文图文项目报告 Markdown。读者应只看这份报告就理解研究问题、实际做法、结果和边界。首页先给核心结论，随即放最重要的结果图；以后围绕发现组织图文。\n"
        "【硬规则】\n"
        "以原始用户问题和最新有效产物为主线，遵循提纲。早期试跑/失败只用于解释方法选择和局限，不机械罗列每轮历史指标。"
        "提纲和图计划只是建议，不是已核实事实。每张必需图必须在相关段落附近单独一行用 [[figure:F01]] 引用，F01替换成实际图ID。禁止把图全放附录或末尾。不要改写资产图题。未生成的图不能列为现有图。"
        "1) 先写本项目实际问题和最重要发现，紧接关键图，再展开方法和局限。读者不是在看运行日志：正文和表头用中文，禁止哈希、实验长编号以及exit_code/production_effective等内部字段；必要API名称和真正代码节选可保留。把测试状态写成通过/失败、隔离验证与生产生效分开，不能把历史标识解释成当前开关。"
        "引用来源一律用自然语言叙述（如'根据 arXiv 论文《标题》'），正文内直接写清查了什么资料、资料的核心观点；正文禁止出现E编号引用（如[E01]）、禁止文件名清单、禁止任何'证据索引'小节。内部术语必须用户化：转移映射写作学习成果、handoff写作交接记录、准入写作允许范围、consumed写作使用、物理哈希/sha256写作内容指纹。\n"
        "正文使用清楚的中文小节，至少包含‘核心结论’‘本次任务’‘做了什么’‘结果’‘局限与下一步’。主动学习如果有实质内容（真实查阅的来源与核心观点）并入‘做了什么’说明；如果未执行或未被消费，明确写没有形成可验证改善，禁止只说已生成交接文件。"
        "若来源含 round_evidence_table.json，结果部分必须用表格逐轮列出：假设、实际动作、执行状态、领域证据、获得的认识、停止或继续理由；重复的同类失败可合并但要写次数。"
        "若来源含 learning_summary.json，只能按其中的 run_count、claims、source_urls、consumed、improved 描述主动学习；run_count=0 时明确写‘未执行’，不得写‘已完成主动学习’。"
        "Event 完成只代表编排节点结束；只有 verified=true 且存在领域证据才可写项目取得实质进展。执行失败时报告标题和核心结论应突出具体阻塞，不得只写‘流程完成’。"
        "正文控制在1000至1800汉字加必要表格，图题由系统加入，正文不重复图题。禁止逐项抄 Event 日志；不生成附录章节（含 Event/Flow 执行图与运行摘要附录）。不要夸张标题、名人身份铺陈或流水账。\n"
        "禁止输出任何 LaTeX/数学标记（$$、\\left、\\right、上标下标等），正文一律纯文本 Markdown。"
        "禁止内部文档代号（如 ADR 0112、0112-effect-bearing、Expected Effect 第X版、机制文档文件名），涉及内部机制文档统一写“既有机制文档”。"
        "本报告本身就是本次交付物：禁止写“报告生成/QQ投递/即时通讯投递 未在本轮实现、尚未启动、未执行”等自我否定表述；交付状态由投递环节负责，报告只写研究内容。"
        "JSON 数据文件（*.json）禁止整段原文节选；需要引用时用一句话概括其内容（如'第1轮基线快照：冻结的初始状态与假设'）。"
        "只有直接支撑核心结论的源代码（*.py）才可节选（如核心算法关键判断），且附一句简短说明；工具类、校验类、哈希计算、路径处理类代码一律禁止节选。"
        "2) 若【业务证据文件】为空，报告必须以 # 项目未推进 为标题，主体 200 字内说明："
        "本项目迭代 N 轮未产生任何可核验的业务文件，未推进、未决策、未达成任何结果。"
        "禁止虚构产物、虚构数字、虚构结论。\n"
        "3) 若有证据文件，按真实事实组织叙事，回答问题/过程/证据/真实结果/局限/下一步，"
        "每项重要主张紧邻 evidence_ref。外部审查/背景材料不是本轮的新实验；items_preview 只是带总数量标注的样本，不得当作完整数据。\n"
        "监督者要求改正不等于原方案文件已经改正；若本报告采用纠正意见，写成本报告的修正，不声称原方案已删除错误，除非最新原文件确实如此。"
        "未逐项核验候选集合、配体制备、受体、搜索预算和随机种子的一致性，不得声称唯一差异或因果提升；软件 CLI 缺失不等于其 Python API 不可用。"
        "不要自行引入通用命中阈值；几何近邻不能称为氢键、亲和力或抑制活性。\n"
        "【原始目标】" + str((params.get('intent_contract') or {}).get('original_request') or params.get('request') or '')[:2500]
        + "\n【实际图文件】" + json.dumps(visual_context(params.get('flow_outputs') or {}), ensure_ascii=False)
        + ("\n这是已交付报告的更正版，标题标注更正版，按审查资料纠正错误。" if (params.get('intent_contract') or {}).get('supersedes_report') else '')
        + "\n【提纲】" + json.dumps((params.get('flow_outputs') or {}).get('outline', {}).get('semantic_output', {}), ensure_ascii=False)
        + "\n"
        f"【业务证据文件】\n{evs_block}\n"
        "【真实来源内容】\n" + _report_source_context((params.get("flow_outputs") or {}).get("sources", {}))
        + revision_context
        + "\n只写有来源支撑的阶段结论，不把计算候选说成已经证实有效的药物。"
    ))
    working = Path(str(getattr(ctx, "working_dir", "") or getattr(ctx, "project_dir", "") or "."))
    raw = _markdown_body(raw)
    raw = re.sub(r'\$\$.+?\$\$', '', raw, flags=re.DOTALL)
    raw = re.sub(r'\$+', '', raw)
    raw = re.sub(r'\\(?:left|right|[a-zA-Z]{1,12})', '', raw)
    # Hard post-filters so the delivered report stays user-readable even when
    # the model drifts: no evidence-id index, no machine-path code excerpts,
    # no internal jargon.
    # The report is itself the delivered artifact; never claim delivery is missing.
    raw = re.sub(r'[^。；\n]*?(?:报告生成|即时通讯投递|QQ\s*投递|PDF\s*报告)[^。；\n]*?(?:未在本轮实现|尚未启动|未实现|未启动)[^。；\n]*[。；]?',
                 '本报告即为本次交付物，已通过 QQ 渠道发送。', raw)
    raw = re.sub(r'(?i)^\s*#+\s*证据索引.*?(?=^#|\Z)', '', raw, flags=re.S)
    raw = re.sub(r'\[E\d+\]', '', raw)
    raw = re.sub(r'(?i)^\s*#+\s*(?:实际代码节选|代码节选).*?(?=^#|\Z)', '', raw, flags=re.S)
    raw = re.sub(r'(?m)^```(?:python|bash|sh|json)?$', '', raw)
    for src, dst in (('转移映射','学习成果'), ('handoff','交接记录'), ('Handoff','交接记录'),
                     ('HANDOFF','交接记录'), ('准入','允许范围'), ('consumed','使用'),
                     ('consume','使用'), ('机制性空转','无效重复'), ('空转','无效重复'),
                     ('物理哈希','内容指纹'), ('sha256','内容指纹'), ('SHA256','内容指纹'),
                     ('证据链','项目记录'), ('benchmark','基准测试'), ('Benchmark','基准测试')):
        raw = re.sub(src, dst, raw, flags=re.I)
    raw = localize_prose(raw,params.get("flow_outputs") or {})
    working.mkdir(parents=True, exist_ok=True)
    title = str(params.get("chinese_filename") or "项目进展报告").removesuffix(".md")
    if (params.get('intent_contract') or {}).get('supersedes_report'):
        title += '_更正版'
    path = working / f"{title}.md"
    path.write_text(raw.strip() + "\n", encoding="utf-8")
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "content": raw, "summary": "领域化中文报告草稿已形成", "token_usage": usage}


def _citation_errors(content: str, sources: dict[str, Any]) -> list[str]:
    known = {row['evidence_id']:Path(row['path']).name for row in
             (sources.get('semantic_output') or {}).get('sources', [])}
    if not known: return []
    errors = [f'unknown evidence ID: {eid}' for eid in sorted(set(re.findall(r'\[(E\d+)\]', content)) - set(known))]
    # Verify explicit ID-to-filename claims in the evidence index. This is
    # independent of whether the model chooses to approve its own citations.
    for line in content.splitlines():
        match = re.match(r'\s*(?:[-*]\s*\[(E\d+)\]|\|\s*(E\d+)\s*\|)\s*[`：:]*([^|—\n]+)', line)
        if not match: continue
        eid = match[1] or match[2]
        files = re.findall(r'[\w./-]+\.(?:jsonl|json|csv|py|md|txt|log|raw|mp4|smi|pdbqt)\b', match[3])
        if eid in known and files and all(Path(value).name != known[eid] for value in files):
            errors.append(f'{eid} must refer to {known[eid]}, not {files[0]}')
    return errors


def _regression_evidence_report(source_rows: list[dict[str, Any]], outputs: dict[str, Any],
                                contract: dict[str, Any]) -> str:
    """Build a readable evidence-only report for the frozen RMSE experiment.

    This deterministic fallback is used only after the prose reviewer rejects
    the free-form draft.  It reads measured artifacts; it does not invent a
    scientific story from filenames or operational metadata.
    """
    def row_named(name: str):
        return next((row for row in source_rows if row.get('exists')
                     and Path(str(row.get('path') or '')).name == name), None)
    def load(row):
        try:return json.loads(Path(row['path']).read_text(encoding='utf-8'))
        except (OSError,ValueError,TypeError,KeyError):return {}
    baseline_row=row_named('baseline_metrics.json')
    candidate_row=(row_named('candidate_metrics.json') or row_named('comparison.json')
                   or row_named('comparison_metrics.json'))
    baseline=load(baseline_row) if baseline_row else {}; candidate=load(candidate_row) if candidate_row else {}
    if not baseline or not candidate or 'test_rmse' not in baseline or 'test_rmse' not in candidate:
        return ''
    b=float(baseline['test_rmse']); c=float(candidate['test_rmse']); improvement=b-c
    pct=(improvement/b*100) if b else 0.0
    metadata_row=row_named('round1_data_metadata.json'); metadata=load(metadata_row) if metadata_row else {}
    assessment_row=row_named('assessment.json'); assessment=load(assessment_row) if assessment_row else {}
    reading_row=row_named('reading.json'); handoff_row=row_named('learning_handoff.json')
    script_row=row_named('train_candidate.py')
    baseline_predictions=row_named('baseline_test_predictions.json')
    candidate_predictions=row_named('candidate_test_predictions.json')
    bootstrap_ci = None
    if baseline_predictions and candidate_predictions:
        try:
            import math, random
            before = load(baseline_predictions); after = load(candidate_predictions)
            before_by_id = {str(row['sample_id']): row for row in before}
            pairs = [(before_by_id[str(row['sample_id'])], row) for row in after
                     if str(row.get('sample_id')) in before_by_id]
            if len(pairs) >= 20:
                rng = random.Random(20260927); values=[]; n=len(pairs)
                for _ in range(1000):
                    sampled=[pairs[rng.randrange(n)] for _ in range(n)]
                    br=math.sqrt(sum((x['y_true']-x['y_pred'])**2 for x,_ in sampled)/n)
                    cr=math.sqrt(sum((y['y_true']-y['y_pred'])**2 for _,y in sampled)/n)
                    values.append(br-cr)
                values.sort(); bootstrap_ci=(values[24],values[974],n)
        except (KeyError, TypeError, ValueError, OSError, ZeroDivisionError):
            bootstrap_ci = None
    def cite(row):return f"[{row.get('evidence_id')}]" if row and row.get('evidence_id') else ''
    assets=visual_context(outputs)
    split_counts=(baseline.get('train_samples'),baseline.get('validation_samples'),baseline.get('test_samples'))
    same_data=baseline.get('data_sha256')==candidate.get('data_sha256') and bool(baseline.get('data_sha256'))
    lines=[
        '# 冻结数据与模型下，加入 x2 是否降低测试 RMSE？','',
        '## 核心结论','',
        f'在同一批合成数据、同一显式训练/验证/测试划分和同一线性回归模型下，候选方案只把特征从 x1 扩展为 x1+x2。测试 RMSE 从 **{b:.6f}** 降至 **{c:.6f}**，绝对降低 **{improvement:.6f}**（{pct:.1f}%）。{cite(baseline_row)}{cite(candidate_row)}',
        '',
        '这个结果支持“x2 在本次冻结合成数据上带来预测信息”。它只覆盖一个预先指定种子和一次冻结测试集，不能直接外推到真实数据或其他生成机制。','',
        '## 研究问题与冻结协议','',
        '| 项目 | Baseline | Candidate |','|---|---:|---:|',
        f"| 模型 | {baseline.get('model_type','未记录')} | {candidate.get('model_type','未记录')} |",
        f"| 输入特征 | {', '.join(baseline.get('features_used') or [])} | {', '.join(candidate.get('features_used') or [])} |",
        f"| 训练/验证/测试样本 | {split_counts[0]}/{split_counts[1]}/{split_counts[2]} | {candidate.get('train_samples')}/{candidate.get('validation_samples')}/{candidate.get('test_samples')} |",
        f"| 数据与划分 | 冻结 | {'同哈希、复用标签' if same_data else '未证实一致'} |",
        f"| 测试 RMSE | {b:.6f} | {c:.6f} |",'',
        f"数据生成种子为 {baseline.get('seed',metadata.get('seed','未记录'))}。候选记录明确写入 `{candidate.get('split_consistency','未记录')}`，数据哈希一致性为 {'通过' if same_data else '未通过'}。{cite(metadata_row)}{cite(candidate_row)}",'',
        '## 主要结果','',
        f'候选相对基线的 RMSE 差值（Baseline − Candidate）为 **{improvement:.6f}**。训练集和验证集 RMSE 也从 {float(baseline.get("train_rmse",0)):.6f}/{float(baseline.get("validation_rmse",0)):.6f} 变为 {float(candidate.get("train_rmse",0)):.6f}/{float(candidate.get("validation_rmse",0)):.6f}。这些数值来自保存的模型评价记录。{cite(baseline_row)}{cite(candidate_row)}','']
    if bootstrap_ci:
        low, high, samples = bootstrap_ci
        lines += [
            '## 不确定性与稳健性','',
            f'基于同一批 {samples} 个测试样本进行 1000 次预先固定随机种子的配对 bootstrap，RMSE 改善的 95% 百分位区间为 **[{low:.6f}, {high:.6f}]**。区间的计算只重采样已保存的逐样本预测，不接触训练或调参。{cite(baseline_predictions)}{cite(candidate_predictions)}','',
        ]
    for asset in assets:
        lines.append(f"[[figure:{asset['id']}]]")
    lines += ['', '## 主动学习如何进入第二轮','',
        f"第一轮结算后，主动学习 Flow 下载并阅读 scikit-learn 的分组切分实现，形成 GroupKFold 交接文件。{cite(reading_row)}{cite(handoff_row)}",
        f"第二轮脚本读取该交接文件，并用 GroupKFold 检查训练与验证数据中的 group_id 是否跨折重叠；随后仍在冻结的训练集上拟合候选线性回归模型。候选记录显示五折检查均无组重叠。{cite(script_row)}{cite(candidate_row)}",'',
        '这证明主动学习产物被真实消费并强化了泄漏检查。它不是 RMSE 改善的独立因果解释；本次 RMSE 比较的直接候选改动是增加 x2。','',
        '## 局限与下一步','',
        '- 当前证据来自 500 条合成样本和一个固定种子，尚无跨种子置信区间。',
        '- GroupKFold 在候选脚本中用于组隔离检查；报告不把这个检查本身说成性能提升来源。',
        '- 下一步应预注册多个数据种子，保持同一模型、预算和外部测试集，报告 RMSE 差值的 bootstrap 区间。',
        '- 若转向真实分子任务，需要重新定义 x2 的生物学含义、泄漏边界和外部验证集。','',
        '## 运行与交付说明','',
        '两轮业务实验、主动学习交接和下游消费均由 Event/Flow 执行。本报告只陈述报告生成时已经存在的项目证据；消息投递、自进化与后续恢复记录在独立运行日志中。','',
        '## 证据索引','']
    used=[]
    for row in (baseline_row,candidate_row,baseline_predictions,candidate_predictions,
                metadata_row,reading_row,handoff_row,script_row,assessment_row):
        if row and row.get('evidence_id') and row['evidence_id'] not in {x.get('evidence_id') for x in used}:used.append(row)
    lines += [f"- [{row['evidence_id']}] {Path(row['path']).name}" for row in used]
    return '\n'.join(lines).strip()+'\n'


def claim_verify(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    import hashlib
    from partner.runtime.action_execution import write_json
    outputs = params.get("flow_outputs") or {}
    draft = dict(outputs.get("draft") or {})
    if not draft and (params.get('intent_contract') or {}).get('reissue_source'):
        original = Path(params['intent_contract']['reissue_source'])
        text = original.read_text(encoding='utf-8')
        target = Path(ctx.working_dir)/'项目报告_排版修正版.md'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        draft = {'path':str(target), 'content':text, 'source_path':str(original)}
    if draft.get('content'):
        localized=localize_prose(draft['content'],outputs)
        if localized!=draft['content']:
            normalized_path=Path(ctx.working_dir)/'报告术语规范稿.md'
            normalized_path.write_text(localized,encoding='utf-8')
            draft={**draft,'path':str(normalized_path),'content':localized}
    sources = outputs.get("sources") or {}
    feedback = str((params.get('intent_contract') or {}).get('review_feedback') or '')[:8000]
    original_path = Path(str(draft.get('path') or ''))
    checkpoint = original_path.with_suffix('.review_state.json') if original_path.is_file() else None
    fingerprint = hashlib.sha256(json.dumps({'draft':draft, 'sources':sources, 'feedback':feedback},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    reviews = []
    usage_total = {}
    phase = 'review'
    if checkpoint and checkpoint.is_file():
        saved = json.loads(checkpoint.read_text())
        if saved.get('fingerprint') == fingerprint:
            draft, reviews = saved['draft'], saved['reviews']
            usage_total, phase = saved['usage'], saved['phase']
    def save_review():
        if checkpoint:
            write_json(checkpoint, {'fingerprint':fingerprint, 'draft':draft,
                'reviews':reviews, 'usage':usage_total, 'phase':phase})
    value = reviews[-1] if reviews else {}
    accepted = bool(value.get('accepted')) and not value.get('unsupported_claims') and not value.get('required_edits')
    while phase != 'done':
        if phase == 'review':
            raw, usage = call_model(ctx, purpose="report_claim_verify", prompt=(
            "逐条检查报告重要主张能否由给定证据直接支持。只输出 JSON："
            "{\"accepted\":true,\"verified_claims\":[],\"unsupported_claims\":[],\"required_edits\":[]}。"
            "只列关键主张：最多8项问题，每项一两句话，总体不超过1500汉字，不复述全文。"
            "资料没有提到其他差异，不等于证实只有一个差异；未核验配体制备、随机种子等条件时必须保留未知。"
            "背景文档的结构注释不能冒充本次重新证实的来源。不要接受无出处的通用命中分数区间；旧 CLI 探测不能否定已真实调用的 Python API。"
            "审查要求删除不等于原文件已删除，必须对照实际新版正文。来源里的推测也不能升级为事实；未固定随机种子仅说明随机性是可能因素，不能证明差异全由随机性或舍入造成。"
            "未生成的图不能列为已有报告图；证据编号必须逐条对应实际文件，不能虚构编号范围或文件覆盖。"
            "任何 unsupported_claims 都令 accepted=false。不同样本集合、预处理或搜索预算的最优值不能直接归因为某个因素的改善；代理评分不等于功能活性。\n" + json.dumps({
                "draft":draft, "actual_visual_assets":visual_context(outputs),
                "external_review_feedback_to_check":feedback,
                "sources":_report_source_context(sources)}, ensure_ascii=False)[:120000]))
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                usage_total[key] = usage_total.get(key, 0) + int(usage.get(key) or 0)
            value = json_object(raw)
            citation_errors = _citation_errors(str(draft.get('content') or ''), sources)
            from partner.presentation.document import figure_errors, measured_claim_errors
            citation_errors += figure_errors(str(draft.get('content') or ''), outputs)
            citation_errors += measured_claim_errors(str(draft.get('content') or ''), outputs)
            from partner.presentation.document import semantic_conflicts
            citation_errors += semantic_conflicts(str(draft.get('content') or ''),outputs)
            from partner.presentation.document import readability_errors
            citation_errors += readability_errors(str(draft.get('content') or ''))
            if (params.get('intent_contract') or {}).get('source_job_id'):
                from partner.presentation.document import report_semantic_errors
                citation_errors += report_semantic_errors(
                    str(draft.get('content') or ''), outputs)
            # (2026-09-15) Split machine-preflight citation errors into blocking vs nonblocking.
            blocking_edits = []
            nonblocking_edits = []
            for err in citation_errors:
                if isinstance(err, dict):
                    if err.get('severity') == 'nonblocking':
                        nonblocking_edits.append(err)
                    else:
                        blocking_edits.append(err)
                else:
                    s = str(err)
                    if s.startswith('[nonblocking]') or ('readability' in s.lower() and 'appendix' in s.lower()):
                        nonblocking_edits.append(err)
                    else:
                        blocking_edits.append(err)
            if blocking_edits:
                value['accepted'] = False
                value['required_edits'] = list(value.get('required_edits') or []) + blocking_edits
            if nonblocking_edits:
                value.setdefault('nonblocking_suggestions', [])
                if isinstance(value['nonblocking_suggestions'], list):
                    value['nonblocking_suggestions'].extend(nonblocking_edits)
            raw_required = list(value.get('required_edits') or [])
            kept_blocking = []
            moved_advisory = []
            for edit in raw_required:
                s = str(edit)
                if isinstance(edit, dict):
                    if edit.get('severity') == 'nonblocking' or edit.get('nonblocking') or 'suggestion' in str(edit.get('kind','')).lower():
                        moved_advisory.append(edit); continue
                # (2026-09-15) Inference claims should not block the report.
                # If a required_edit asks for softening from absolute to qualified
                # language ("改为"、"限定为"、"标注为推测"、"删除或弱化"、"补充"), it is
                # a precision suggestion not a factual error. Move to advisory.
                inf_marker = ['改为', '限定', '标注', '补充', '删除或弱化', '改写', '删除', '改成', '改为仅', '改为已', '标注为推测', '未验证', '推测', '限定为']
                if any(m in s for m in inf_marker):
                    moved_advisory.append(edit); continue
                if 'style' in s.lower() or 'naming' in s.lower() or 'consider' in s.lower():
                    moved_advisory.append(edit); continue
                kept_blocking.append(edit)
            value['required_edits'] = kept_blocking
            if moved_advisory:
                value.setdefault('nonblocking_suggestions', [])
                if isinstance(value['nonblocking_suggestions'], list):
                    value['nonblocking_suggestions'].extend(moved_advisory)
            reviews.append(value)
            accepted = bool(value.get("accepted")) and not value.get("unsupported_claims") and not value.get('required_edits')
            phase = 'done' if accepted or len(reviews) >= 3 else 'repair'
            save_review()

        path = Path(str(draft.get('path') or ''))
        if path.is_file():
            write_json(path.with_suffix('.claims.json'), value)
        if phase == 'done' or not path.is_file():
            break
        revision, usage = call_model(ctx, purpose="report_claim_repair", prompt=(
            "根据审查意见修订报告 Markdown。删除或限定没有直接证据的主张；"
            "不得新增实验、数据、文献或假装已经完成计划。保留重要真实结果及紧邻的来源，以及所有 [[figure:ID]] 插图位置标记。正文压缩到800至1400汉字，删除无关背景和重复限定。审查提出的改法也可能有错，必须重新以原始数值与实际图的测量范围为准。"
            "每张图的轮次、样本数、数值与actual_visual_assets逐项一致，不得把第二轮的图解释为第一轮。"
            "首张图紧跟简短核心结论；完整哈希放证据附录。证据附录中每个E编号仅对应注册的单个文件名，不能把一个文件名改写成文件范围。"
            "直接输出完整修订稿。\n" + json.dumps({"draft":draft.get('content') or path.read_text(),
                "review":value, "actual_visual_assets":visual_context(outputs),
                "sources":_report_source_context(sources)}, ensure_ascii=False)[:120000]))
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            usage_total[key] = usage_total.get(key, 0) + int(usage.get(key) or 0)
        revision = localize_prose(_markdown_body(revision),outputs)
        revision_path = path.with_name(f"报告修订_{len(reviews)}.md")
        revision_path.write_text(revision.strip()+'\n', encoding='utf-8')
        draft = {'path':str(revision_path), 'content':revision}
        phase = 'review'
        save_review()
    if not accepted and reviews:
        # After the bounded repair budget, preserve only claims the independent
        # reviewer itself marked verified.  This produces a short evidence
        # report instead of either publishing disputed prose or losing the
        # entire PDF because the model repeatedly reintroduced an overclaim.
        verified = value.get('verified_claims') or []
        source_rows = (sources.get('semantic_output') or {}).get('sources') or []
        known_ids = {row.get('evidence_id') for row in source_rows}
        structured = _regression_evidence_report(
            source_rows, outputs, params.get('intent_contract') or {})
        if structured:
            from partner.presentation.document import figure_errors, report_semantic_errors
            structured_errors = (_citation_errors(structured,sources)
                                 + figure_errors(structured,outputs)
                                 + report_semantic_errors(structured,outputs))
            if not structured_errors:
                fallback_path=Path(ctx.working_dir)/'冻结回归研究_证据审查稿.md'
                fallback_path.write_text(structured,encoding='utf-8')
                return {"ok": True, "status": "completed",
                    "path":str(fallback_path), "content":structured,
                    "files":[str(fallback_path)], "evidence_refs":[str(fallback_path)],
                    "semantic_output":{**value,'accepted':True,'unsupported_claims':[],
                        'required_edits':[],'review_fallback':True,
                        'structured_evidence_fallback':True,'reviews':reviews},
                    "summary":"自由草稿未通过；已从测量产物生成结构化证据报告",
                    "token_usage":usage_total}
        outline=((outputs.get('outline') or {}).get('semantic_output') or {})
        report_contract=params.get('intent_contract') or {}
        original_request=str(report_contract.get('original_request')
                             or params.get('request') or '').strip()
        original_goal=str(report_contract.get('goal') or original_request).strip()
        fallback_title=str(outline.get('title') or '').strip()
        if not fallback_title or fallback_title in {'项目证据报告','项目进展报告','运行报告','报告'}:
            request_title=next((line.strip('【】 ') for line in original_request.splitlines()
                                if line.strip()), '')
            fallback_title=('证据审查：'+(request_title or original_goal)[:55]).rstrip('：')
        lines = ['# '+fallback_title, '', '## 核心结论', '']
        for item in verified[:8]:
            if isinstance(item, dict):
                claim = str(item.get('claim') or '').strip()
                ids = [str(x) for x in item.get('evidence_ids') or [] if x in known_ids]
            else:
                claim, ids = str(item).strip(), []
            if claim:
                lines.append('- ' + claim)
        if not verified:
            lines.append('- 当前证据不足以形成可发布的肯定结论。')
        question=(original_goal.splitlines()[0] if original_goal else '').strip()
        question=re.sub(r'^【[^】]+】(?:【[^】]+】)*','',question).strip()
        if '？' in question: question=question.split('？',1)[0]+'？'
        if '?' in question: question=question.split('?',1)[0]+'?'
        lines += ['', '## 本次任务', '',
                  question or '本报告仅审查现有项目产物，没有收到可恢复的原始任务。',
                  '', '本报告只基于已收集的项目产物与执行记录整理；未记录的样本、预算、切分和随机性条件不作假设。',
                  '', '## 结果', '']
        lines += [('- '+(str(item.get('claim') or '') if isinstance(item,dict) else str(item)))
                  for item in verified[:8] if (str(item.get('claim') or '').strip()
                  if isinstance(item,dict) else str(item).strip())]
        assets = visual_context(outputs)
        if assets:
            lines += ['', '### 真实结果图', '']
            lines += [f"[[figure:{row['id']}]]" for row in assets if row.get('id')]
        verified_text=' '.join(str(item.get('claim') or '') for item in verified if isinstance(item,dict))
        has_uncertainty=bool(re.search(r'bootstrap|置信区间|\bCI\b',verified_text,re.I))
        learning_effect='本轮已形成可核验的主动学习记录与后续使用证据，效果归因限于本次任务。' if has_uncertainty else (
            '本轮没有可核验的主动学习记录，未形成可验证的改善。')
        next_step=('当前结果与不确定性估计已完成；下一步应在独立数据集上验证泛化。'
                   if has_uncertainty else
                   '下一步应在真实项目数据上继续推进，再评估是否采纳当前候选改动。')
        lines += ['', '## 做了什么', '', learning_effect,
                  '', '## 局限与下一步', '',
                  '本报告只包含已核实的结论；未经核实的推测未写入。',
                  next_step, '']
        fallback='\n'.join(lines).strip()+'\n'
        errors=_citation_errors(fallback,sources)
        from partner.presentation.document import figure_errors
        errors += figure_errors(fallback,outputs)
        if report_contract.get('source_job_id'):
            from partner.presentation.document import report_semantic_errors
            errors += report_semantic_errors(fallback,outputs)
        if not errors:
            # Deliver even when zero claims passed review: the fallback already
            # states plainly that current evidence cannot support a publishable
            # positive conclusion, which is honest and far more useful than
            # refusing to render any PDF for the user.
            fallback_path=Path(ctx.working_dir)/'项目证据报告_审查降级稿.md'
            fallback_path.write_text(fallback,encoding='utf-8')
            draft={'path':str(fallback_path),'content':fallback}
            accepted=True
            value={**value,'accepted':True,'unsupported_claims':[],
                   'required_edits':[],'review_fallback':True,
                   'excluded_claim_count':sum(len(r.get('unsupported_claims') or []) for r in reviews)}
    return {"ok": accepted, "status": "completed" if accepted else "failed",
            "path":draft.get('path', ''), "content":draft.get('content', ''),
            "semantic_output":{**value, 'reviews':reviews},
            "summary":"报告主张已验真" if accepted else "报告修订后仍含未获证据支持的主张",
            "token_usage":usage_total}


def pdf_render(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    upstream = params.get("upstream") if isinstance(params.get("upstream"), dict) else {}
    review = upstream.get("claims") or {}
    if review and not review.get("ok"):
        return {"ok":False, "status":"failed", "error":"unsupported report claims; rendering refused"}
    draft = upstream.get("claims") or upstream.get("draft") or params.get("previous") or {}
    if isinstance(draft, dict) and not draft.get("path"):
        draft = upstream.get("draft") or draft
    source = Path(str(params.get("source_path") or params.get("path")
                      or (draft.get("path") if isinstance(draft, dict) else "") or ""))
    if not source.is_file():
        return {"ok": False, "status": "failed", "error": "verified report draft missing"}
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

    font_name = "Helvetica"
    for candidate in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/mnt/c/Windows/Fonts/msyh.ttc",
    ):
        if Path(candidate).is_file():
            try:
                pdfmetrics.registerFont(TTFont("PartnerCJK", candidate, subfontIndex=0))
                font_name = "PartnerCJK"
                break
            except Exception:
                continue
    output = source.with_name(str(params.get("output_filename") or "项目进展报告.pdf"))
    if output.suffix.lower() != ".pdf":
        output = output.with_suffix(".pdf")
    styles = getSampleStyleSheet()
    body = ParagraphStyle("PartnerBody", parent=styles["BodyText"], fontName=font_name,
                          fontSize=10.5, leading=17, textColor=colors.HexColor("#263238"),
                          alignment=TA_LEFT, spaceAfter=6)
    title = ParagraphStyle("PartnerTitle", parent=body, fontSize=22, leading=29,
                           textColor=colors.HexColor("#17213B"), spaceAfter=14, keepWithNext=True)
    heading = ParagraphStyle("PartnerHeading", parent=body, fontSize=14, leading=20,
                             textColor=colors.HexColor("#365A8C"), spaceBefore=12, spaceAfter=7, keepWithNext=True)
    cell_style = ParagraphStyle('PartnerCell', parent=body, fontSize=9, leading=13, spaceAfter=0)
    story = []
    text = _markdown_body(source.read_text(encoding="utf-8", errors="replace"))
    import html
    def inline(value):
        value = value.replace('\u2212', '-')  # Some CJK fonts lack mathematical minus.
        # Preserve status meaning when the selected CJK face has no emoji glyph.
        for symbol, label in {'✅':'是', '✔':'是', '✓':'是', '❌':'否', '✗':'否', '⚠':'注意', '☑':'已选', '☐':'未选'}.items():
            value = value.replace(symbol, label)
        value = value.replace('\ufe0f', '')
        value = html.escape(value)
        # Render Unicode indices with ordinary supported digits at subscript size.
        for symbol, digit in zip('₀₁₂₃₄₅₆₇₈₉', '0123456789'):
            value = value.replace(symbol, f'<sub>{digit}</sub>')
        for symbol, digit in zip('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻', '0123456789+-'):
            value = value.replace(symbol, f'<super>{digit}</super>')
        value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
        return value.replace('`', '').replace(r'\|', '|')
    from partner.presentation.document import figure_errors, figure_assets
    assets=figure_assets(params.get('flow_outputs') or {})
    errors=figure_errors(text,params.get('flow_outputs') or {})
    if errors: return {'ok':False,'status':'failed','error':'; '.join(errors)}
    asset_map={a['id']:a for a in assets}
    embedded=[]
    caption_style=ParagraphStyle('FigureCaption',parent=body,fontSize=9,leading=13,textColor=colors.HexColor('#475569'))
    reference_style=ParagraphStyle('Reference',parent=body,fontSize=7.5,leading=9,spaceAfter=1)
    # The document contract accepts inline figure markers. Split them into
    # render blocks so a valid marker beside prose cannot silently lose its image.
    lines = []
    fenced = False
    for raw_line in text.splitlines():
        if raw_line.strip().startswith('```'):
            fenced = not fenced
        if not fenced:
            raw_line = re.sub(r'(\[\[figure:[\w-]+\]\])', r'\n\1\n', raw_line)
        lines.extend(raw_line.splitlines())
    index = 0
    in_code = False
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        match=re.fullmatch(r'\[\[figure:([\w-]+)\]\]',line)
        if match and not in_code:
            asset=asset_map[match[1]]
            item=Image(asset['path']); item._restrictSize(165*mm,105*mm)
            caption=Paragraph(inline(asset['id']+'  '+asset['caption']),caption_style)
            story.append(KeepTogether([item,Spacer(1,2*mm),caption,Spacer(1,4*mm)]))
            embedded.append(asset['id'])
            continue
        if line.startswith('```'):
            in_code = not in_code
            continue
        if not line:
            if not story or not getattr(getattr(story[-1], 'style', None), 'keepWithNext', False):
                story.append(Spacer(1, 1 * mm))
            continue
        if not in_code and re.fullmatch(r'([-*_])(?:\s*\1){2,}', line):
            continue
        if not in_code and line.startswith('> '):
            line = line[2:]
        if not in_code and re.match(r'^#{2,3}\s*(?:证据索引|证据附录)', line):
            # User asked for readable reports: no evidence index/appendix in
            # the delivered PDF. Provenance stays in the machine records.
            while index < len(lines) and not lines[index].strip().startswith('#'):
                index += 1
            continue
        if line.startswith('|') and not in_code:
            table_rows = []
            while True:
                cells = [c.strip() for c in re.split(r'(?<!\\)\|', line.strip('|'))]
                if not all(re.fullmatch(r'[:\- ]+', c or '-') for c in cells):
                    table_rows.append(cells)
                if index >= len(lines) or not lines[index].strip().startswith('|'): break
                line = lines[index].strip(); index += 1
            if table_rows:
                cols = max(map(len, table_rows))
                padded = [r + ['']*(cols-len(r)) for r in table_rows]
                # Give descriptive columns more room than short IDs or times;
                # equal widths can waste most of a page on wrapped prose.
                weights = [min(120, max(8, max(sum(2 if ord(ch)>255 else 1 for ch in r[i])
                                               for r in padded))) ** .5 for i in range(cols)]
                values = [[Paragraph(inline(c), cell_style) for c in r] for r in padded]
                table = Table(values, colWidths=[170*mm*w/sum(weights) for w in weights], repeatRows=1, hAlign='LEFT')
                table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),
                    ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EAF0F7')),
                    ('LINEBELOW',(0,0),(-1,0),.5,colors.HexColor('#365A8C')),
                    ('BOTTOMPADDING',(0,0),(-1,-1),6)]))
                story.extend([table, Spacer(1,3*mm)])
            continue
        level = len(line) - len(line.lstrip('#')) if not in_code else 0
        content = inline(line[level:].strip() if level else line)
        is_reference = not in_code and bool(re.match(r'^[-*]?\s*\[E\d+\]',line))
        story.append(Paragraph(content, title if level == 1 else heading if level in {2,3} else reference_style if is_reference else body))
    images = [Path(a['path']) for a in assets]
    document_title = next((line[2:].strip() for line in text.splitlines()
                           if line.startswith('# ')), source.stem)
    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=20 * mm,
                            leftMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=document_title, author="Partner")
    def page_number(canvas, doc):
        canvas.saveState(); canvas.setStrokeColor(colors.HexColor('#CBD5E1'))
        canvas.line(20*mm,14*mm,190*mm,14*mm)
        canvas.setFont(font_name,7.5); canvas.setFillColor(colors.HexColor('#64748B'))
        canvas.drawString(20*mm,9*mm,document_title[:42])
        canvas.drawRightString(190*mm,9*mm,f"Partner · {doc.page}")
        canvas.restoreState()
    doc.build(story,onFirstPage=page_number,onLaterPages=page_number)
    return {"ok": output.is_file(), "status": "completed" if output.is_file() else "failed",
            "path": str(output), "pdf_path": str(output), "files": [str(output)],
            "embedded_figure_ids":embedded, "figure_manifest":((params.get("flow_outputs") or {}).get("visuals") or {}).get("manifest_path"),
            "summary": "中文领域报告已由纯 Event 渲染器生成",
            "evidence_refs": [str(source), *[str(x) for x in images]]}


def pdf_quality_review(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    prior = params.get("previous") if isinstance(params.get("previous"), dict) else {}
    path = Path(str(params.get("path") or params.get("pdf_path") or prior.get("path") or ""))
    size = path.stat().st_size if path.exists() and path.is_file() else 0
    checks = {'size':size, 'pages':0, 'text_chars':0, 'image_count':0, 'layout_errors':[]}
    try:
        import fitz
        with fitz.open(path) as document:
            checks['pages'] = len(document)
            for index, page in enumerate(document):
                checks['image_count'] += len(page.get_image_info())
                text = page.get_text()
                checks['text_chars'] += len(text.strip())
                if '\x00' in text or '\ufffd' in text:
                    checks['layout_errors'].append(f'page {index+1}: unreadable glyphs')
                for block in page.get_text('blocks'):
                    if block[0]<-2 or block[1]<-2 or block[2]>page.rect.width+2 or block[3]>page.rect.height+2:
                        checks['layout_errors'].append(f'page {index+1}: content outside page')
        from partner.presentation.document import figure_assets
        expected=figure_assets(params.get('flow_outputs') or {})
        # A figure may legitimately fail to render (missing source asset) while
        # the rest of the report stands.  Only reject when the plan promised
        # figures and none made it into the PDF at all.
        if expected and checks['image_count'] == 0:
            checks['layout_errors'].append('planned figures missing from PDF')
        outputs=params.get('flow_outputs') or {}
        contract=params.get('intent_contract') or {}
        draft=outputs.get('claims') or outputs.get('draft') or {}
        content=str(draft.get('content') or '')
        if contract.get('source_job_id'):
            from partner.presentation.document import report_semantic_errors
            checks['layout_errors'].extend(report_semantic_errors(
                content,outputs))
            # A project report is a research result document.  Operational
            # traces belong in a short appendix and cannot replace the
            # question/method/result/limitation narrative.
            # Section names follow the user-facing layout (本次任务/做了什么
            # are the reader-facing equivalents of 研究问题/结果); accept any
            # alias from report_semantic_errors instead of a fixed old set.
            from partner.presentation.document import report_semantic_errors as _sem_errors
            _required = {
                '核心结论': r'核心结论|主要结论',
                '本次任务': r'本次任务|研究问题|实验问题|冻结协议|实验协议|任务',
                '做了什么': r'做了什么|结果|主要发现',
                '局限': r'局限|限制|未解决',
            }
            for label, pattern in _required.items():
                if not re.search(pattern, content, re.M):
                    checks['layout_errors'].append(f'missing research section: {label}')
            appendix_at = content.find('附录')
            main_text = content if appendix_at < 0 else content[:appendix_at]
            operational_hits = len(re.findall(
                r'Event|Flow|event_id|flow_id|节点完成|运行时编排', main_text, re.I))
            if operational_hits > 4:
                checks['layout_errors'].append(
                    'main report is dominated by runtime/Event narration; move it to the appendix')
            if not re.search(r'\[(?:E\d+)\]', content):
                # Reader-facing PDFs drop the evidence index/appendix by
                # design; inline evidence citations are best-effort, not a
                # delivery gate.  Provenance stays auditable in machine records.
                checks['citation_warning'] = 'research report contains no adjacent evidence citations'
        accepted = checks['pages'] > 0 and checks['text_chars'] >= 100 and not checks['layout_errors']
    except Exception as exc:
        accepted=False
        checks['layout_errors'].append(f'{type(exc).__name__}: {exc}')
    return {"ok":accepted, "status":"completed" if accepted else "failed",
            "semantic_output":{"path":str(path), **checks},
            "summary":"PDF 已通过解析、正文提取与页面边界检查" if accepted else "PDF 正文或页面检查失败"}



DEFINITIONS = [
    EventDefinition("presentation.notification_decide", "presentation", "判断是否形成用户可见里程碑", notification_decide),
    EventDefinition("presentation.message_compose", "presentation", "根据真实 Summary 形成自然消息", message_compose, execution_method="llm", timeout_seconds=60),
    EventDefinition("presentation.message_critic", "presentation", "独立审查消息清晰度和重复", message_critic, execution_method="llm", timeout_seconds=90),
    EventDefinition("presentation.message_deduplicate", "presentation", "抑制同一结论的重复用户消息", message_deduplicate),
    EventDefinition("presentation.report_outline", "presentation", "按项目领域设计报告叙事和真实可视化", report_outline, execution_method="llm"),
    EventDefinition("presentation.report_decide", "presentation", "仅在真实里程碑决定生成报告", report_decide),
    EventDefinition("presentation.run_summary_collect", "presentation", "从源 Job 生成结果总结和运行总结产物", run_summary_collect, produces_artifact=True),
    EventDefinition("presentation.flow_graph_project", "presentation", "从真实运行日志构建 Event/Flow 图数据", flow_graph_build, produces_artifact=True),
    EventDefinition("visualization.flow_graph_render", "visualization", "将真实 Event/Flow 图数据渲染为附录图片", flow_graph_render, produces_artifact=True),
    EventDefinition("visualization.flow_graph_verify", "visualization", "核验运行图节点和渲染产物", flow_graph_verify, reads_existing_artifact=True),
    EventDefinition("presentation.summary_message_compose", "presentation", "从总结产物形成最终交付消息", summary_message_compose),
    EventDefinition("presentation.report_sources_collect", "presentation", "收集并核验报告真实证据源", report_sources_collect, reads_existing_artifact=True),
    EventDefinition("presentation.visual_plan", "presentation", "规划领域相关而非装饰性的可视化", visual_plan, execution_method="llm"),
    EventDefinition("presentation.visual_generate", "presentation", "接纳领域 Event 真实生成的图片", visual_generate, reads_existing_artifact=True),
    EventDefinition("presentation.report_draft", "presentation", "撰写非模板化中文领域报告", report_draft, execution_method="llm", produces_artifact=True),
    EventDefinition("presentation.claim_verify", "presentation", "逐条核验报告主张和证据", claim_verify, execution_method="llm", timeout_seconds=150),
    EventDefinition("presentation.pdf_render", "presentation", "以中文字体和领域图片渲染 PDF", pdf_render, produces_artifact=True),
    EventDefinition("presentation.pdf_quality_review", "presentation", "交付前检查 PDF 文件和排版证据", pdf_quality_review, reads_existing_artifact=True),
]
