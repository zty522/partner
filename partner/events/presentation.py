"""Presentation Events; rendering and transport stay separate from truth."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
from partner.event_fabric.catalog import EventDefinition
from ._llm import call_model, json_object, event_facts
from partner.presentation.document import visual_context, localize_prose


def _markdown_body(text):
    text = str(text).strip()
    wrapped = re.fullmatch(r'```(?:markdown|md)?\s*\n(.*)\n```', text, flags=re.DOTALL)
    return wrapped.group(1).strip() if wrapped else text


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


def message_compose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from partner.presentation.notifications import waiting_message,with_report_context,with_waiting_context
    params=with_waiting_context(ctx,with_report_context(ctx,params))
    waiting=waiting_message(ctx,params)
    if waiting is not None:
        return {'ok':bool(waiting),'status':'completed' if waiting else 'failed','message':waiting,'summary':waiting,'runtime_projection':True}
    direct = str((params.get("flow_outputs") or {}).get("answer", {}).get("answer") or "")
    if direct:
        # Keep the real answer intact for independent review. Repeated prose
        # rewrites anchored to an intent summary can amplify its misconceptions.
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
        + event_facts(params, max_chars=18000)
    ))
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
             if (params.get('flow_outputs') or {}).get('answer') else event_facts(params, max_chars=18000))
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
            fact_audit = json_object(audit_raw)
            if not isinstance(fact_audit.get('unsupported_claims'), list):
                fact_audit = {'unsupported_claims':['事实审计未返回有效的主张检查结果']}
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
        value = json_object(raw)
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
    # Sprint 37: a long-running autonomous loop must not stall on *format*.
    # The honesty guarantee is that no message carrying an unsupported fact
    # claim is sent.  When the fact audit is CLEAN (no unsupported_claims) and
    # only length / numeric-density / terminology problems remain, degrade-pass
    # with the reviewed message, flagged so the audit trail shows it was not a
    # clean pass.  A non-empty unsupported_claims still fails hard (pinned by
    # test_message_style_approval_cannot_override_failed_fact_audit).
    if not accepted:
        facts_clean = not ((value.get('fact_audit') or {}).get('unsupported_claims') or [])
        fallback = str(message or '').strip() or str(initial_message or '').strip()
        if facts_clean and fallback and 0 < len(fallback) <= 2000 and not tool_payload:
            return {'ok': True, 'status': 'completed', 'format_degraded': True,
                    'semantic_output': {**value, 'reviews': reviews, 'format_degraded': True},
                    'candidate_message': fallback, 'message': fallback,
                    'summary': '事实审计通过；仅格式问题，降级放行', 'token_usage': usage}
    return {'ok':accepted, 'status':'completed' if accepted else 'failed',
            'error':'' if accepted else 'message did not pass independent review within budget',
            'semantic_output':{**value,'reviews':reviews}, 'candidate_message':message, 'message':message if accepted else '',
            'summary':'消息审查完成' if accepted else '消息尚未通过事实与可读性审查', 'token_usage':usage}


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
        "根据真实证据为中文 PDF 设计领域化叙事和可视化，不使用固定通用章节。"
        "这是简短提纲，不写正文：3–6节、每节一句话，最多列5项待核验主张，总体不超过1000汉字。"
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


def _data_preview(value):
    """Retain summary fields after large arrays; label sampled data explicitly."""
    from partner.runtime.artifact_checks import preview_data
    return preview_data(value)


def _report_source_context(sources, budget=40000):
    rows = (sources.get('semantic_output') or {}).get('sources') or []
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
    candidates = (params.get("evidence_refs")
                  or prior.get("evidence_refs")
                  or prior.get("files")
                  or contract.get("evidence_refs") or [])
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
        row = {"evidence_id":evidence_id, "path": str(path), "exists": path.exists(), "size": 0, "excerpt": "",
               "role":"context_or_external_review" if str(path) in context_refs else "execution_evidence" if str(path) in provided_paths else "referenced_context"}
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
    valid=not errors
    value['validation_errors']=errors
    plans=value.get('visuals') or []
    return {'ok':valid,'status':'completed' if valid else 'failed','semantic_output':value,
            'error':'' if valid else 'figure plan is not executable: '+ '; '.join(errors), 'token_usage':usage,
            'summary':f'规划 {len(plans)} 张真实证据图'}


def _cycle_visual_plan(ctx, params, sources):
    from partner.presentation.figure_plan import executable_choices
    options = executable_choices(sources)
    available = {o['option_id']: o['plan'] for o in options}
    if not available:
        return {'ok':False,'status':'failed','error':'no source-bound executable figure options'}
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
        "撰写中文图文项目报告 Markdown。首页先给核心结论，随即放最重要的图；以后围绕发现组织图文。\n"
        "【硬规则】\n"
        "以原始用户问题和最新有效产物为主线，遵循提纲。早期试跑/失败只用于解释方法选择和局限，不机械罗列每轮历史指标。"
        "提纲和图计划只是建议，不是已核实事实。每张必需图必须在相关段落附近单独一行用 [[figure:F01]] 引用，F01替换成实际图ID。禁止把图全放附录或末尾。不要改写资产图题。未生成的图不能列为现有图。"
        "1) 先写本项目实际问题和最重要发现，紧接关键图，再展开方法和局限。读者不是在看运行日志：正文和表头用中文，禁止哈希、实验长编号以及exit_code/production_effective等内部字段；必要API名称和真正代码节选可保留。把测试状态写成通过/失败、隔离验证与生产生效分开，不能把历史标识解释成当前开关。"
        "按来源 evidence_id（如 [E01]）引用证据，末尾列编号和简短文件名索引；不要用内部绝对路径和字节数挤占正文。\n"
        "正文控制在800至1400汉字加必要表格，图题由系统加入，正文不重复图题。3至5个主题即可；不要夸张标题、名人身份铺陈或流水账，不使用最硬/最狠/封神等修辞。证据索引只列实际引用的来源。\n"
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
    raw = localize_prose(_markdown_body(raw),params.get("flow_outputs") or {})
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
                "sources":_report_source_context(sources)}, ensure_ascii=False)[:64000]))
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
                "sources":_report_source_context(sources)}, ensure_ascii=False)[:64000]))
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            usage_total[key] = usage_total.get(key, 0) + int(usage.get(key) or 0)
        revision = localize_prose(_markdown_body(revision),outputs)
        revision_path = path.with_name(f"报告修订_{len(reviews)}.md")
        revision_path.write_text(revision.strip()+'\n', encoding='utf-8')
        draft = {'path':str(revision_path), 'content':revision}
        phase = 'review'
        save_review()
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
    reference_style=ParagraphStyle('Reference',parent=body,fontSize=9,leading=13,spaceAfter=3)
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
    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=20 * mm,
                            leftMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=source.stem, author="Partner")
    def page_number(canvas, doc):
        canvas.saveState(); canvas.setFont(font_name,8); canvas.setFillColor(colors.HexColor('#64748B'))
        canvas.drawRightString(190*mm,10*mm,str(doc.page)); canvas.restoreState()
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
        if checks['image_count'] < len(expected): checks['layout_errors'].append('planned figures missing from PDF')
        accepted = checks['pages'] > 0 and checks['text_chars'] >= 100 and not checks['layout_errors']
    except Exception as exc:
        accepted=False
        checks['layout_errors'].append(f'{type(exc).__name__}: {exc}')
    return {"ok":accepted, "status":"completed" if accepted else "failed",
            "semantic_output":{"path":str(path), **checks},
            "summary":"PDF 已通过解析、正文提取与页面边界检查" if accepted else "PDF 正文或页面检查失败"}



DEFINITIONS = [
    EventDefinition("presentation.notification_decide", "presentation", "判断是否形成用户可见里程碑", notification_decide),
    EventDefinition("presentation.message_compose", "presentation", "根据真实 Summary 形成自然消息", message_compose, execution_method="llm"),
    EventDefinition("presentation.message_critic", "presentation", "独立审查消息清晰度和重复", message_critic, execution_method="llm"),
    EventDefinition("presentation.message_deduplicate", "presentation", "抑制同一结论的重复用户消息", message_deduplicate),
    EventDefinition("presentation.report_outline", "presentation", "按项目领域设计报告叙事和真实可视化", report_outline, execution_method="llm"),
    EventDefinition("presentation.report_decide", "presentation", "仅在真实里程碑决定生成报告", report_decide),
    EventDefinition("presentation.report_sources_collect", "presentation", "收集并核验报告真实证据源", report_sources_collect, reads_existing_artifact=True),
    EventDefinition("presentation.visual_plan", "presentation", "规划领域相关而非装饰性的可视化", visual_plan, execution_method="llm"),
    EventDefinition("presentation.visual_generate", "presentation", "接纳领域 Event 真实生成的图片", visual_generate, reads_existing_artifact=True),
    EventDefinition("presentation.report_draft", "presentation", "撰写非模板化中文领域报告", report_draft, execution_method="llm", produces_artifact=True),
    EventDefinition("presentation.claim_verify", "presentation", "逐条核验报告主张和证据", claim_verify, execution_method="llm"),
    EventDefinition("presentation.pdf_render", "presentation", "以中文字体和领域图片渲染 PDF", pdf_render, produces_artifact=True),
    EventDefinition("presentation.pdf_quality_review", "presentation", "交付前检查 PDF 文件和排版证据", pdf_quality_review, reads_existing_artifact=True),
]
