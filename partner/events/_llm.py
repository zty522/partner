"""Shared bounded model port for cognitive Events."""
from __future__ import annotations

from typing import Any
import json
import re


def _minimal_json_repair(value: str) -> Any:
    """Repair common provider truncation without a mandatory extra package."""
    candidate = re.sub(r",\s*([}\]])", r"\1", value.strip())
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]" and stack and char == stack[-1]:
            stack.pop()
    if in_string:
        candidate += '"'
    candidate += "".join(reversed(stack))
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    return json.loads(candidate)


def call_model(ctx: Any, *, purpose: str, prompt: str) -> tuple[str, dict[str, Any]]:
    adapter = getattr(ctx, "adapter", None) or getattr(ctx, "model", None)
    if adapter is None:
        raise RuntimeError("cognitive Event requires a configured model adapter")
    import time
    import random
    last_error = "empty response"
    retry_budget = None
    # One attempt here means one HTTP call for DirectAdapter; no nested retries.
    deadline = getattr(ctx, "event_deadline", None)
    deep = (purpose.startswith(('intent_', 'cycle_', 'learning_', 'autoevolution_',
                                'self_evolution_', 'report_', 'message_factcheck'))
            or purpose in {'project_plan_propose', 'project_outcome_reflect'})
    attempts = 1 if purpose == 'cycle_partner_audit' else 2
    for attempt in range(attempts):
        remaining = deadline - time.monotonic() - 2 if deadline else (180 if deep else 90)
        if remaining <= 0:
            raise TimeoutError("cognitive Event deadline exhausted")
        try:
            if hasattr(adapter, "chat_once"):
                options = {"max_tokens":retry_budget} if retry_budget is not None else {}
                from partner.adapters.adapter import DirectAdapter
                if isinstance(adapter, DirectAdapter):
                    # A larger output budget also needs bounded generation time;
                    # retaining the 8k timeout made 16k truncation retries futile.
                    call_cap = 120 if purpose == 'cycle_partner_audit' else (
                        180 if deep or (retry_budget or 8192) > 8192 else 90)
                    options["timeout"] = min(call_cap,
                                             remaining)
                active_prompt = prompt
                if attempt and len(active_prompt) > 36000:
                    # A provider timeout is not repaired by replaying the same
                    # oversized request.  Preserve the contract header and the
                    # newest evidence tail for one compact recovery call.
                    active_prompt = (active_prompt[:14000]
                                     + '\n[中间重复上下文已由运行器压缩]\n'
                                     + active_prompt[-20000:])
                raw = adapter.chat_once(active_prompt, purpose=purpose, **options)
            elif callable(adapter):
                raw = adapter(prompt, purpose=purpose)
            elif hasattr(adapter, "chat"):
                raw = adapter.chat(prompt, purpose=purpose)
            else:
                raise RuntimeError("model adapter has no callable/chat port")
            usage = dict(getattr(adapter, "last_usage", {}) or {})
            text = re.sub(r"<(think|analysis)>.*?</\1>", "", str(raw or ""),
                          flags=re.IGNORECASE | re.DOTALL).strip()
            if usage.get("finish_reason") == "length":
                last_error = "output truncated (finish_reason=length)"
                retry_budget = min(16384, max(8192, int(usage.get("completion_tokens") or 8192)) * 2)
            elif re.search(r"</?(think|analysis)>", text, re.IGNORECASE):
                last_error = "model reasoning block was not closed"
            elif text:
                return text, usage
            else:
                last_error = usage.get("error") or "model returned an empty result after removing reasoning"
        except Exception as exc:
            last_error = str(exc)[:200]
        if attempt < attempts - 1:
            delay = 2 ** (attempt + 1) + random.uniform(0, 1.5)
            time.sleep(max(0, min(delay, deadline - time.monotonic() - 2)) if deadline else delay)
    raise RuntimeError(f"model failed after {attempts} attempts: {last_error}")


def json_object(raw: str) -> dict[str, Any]:
    value = str(raw or "").strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0]
    start = value.find("{")
    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(value[start:]) if start >= 0 else ({}, 0)
    except (json.JSONDecodeError, ValueError):
        # Providers occasionally return a truncated quote/comma while the
        # surrounding semantic object is intact.  Repair syntax only; every
        # caller still validates required fields and allowed decisions.
        fragment = value[start:] if start >= 0 else value
        try:
            from json_repair import repair_json
        except ModuleNotFoundError:
            parsed = _minimal_json_repair(fragment)
        else:
            parsed = repair_json(fragment, return_objects=True)
    if not isinstance(parsed, dict):
        raise ValueError("expected a JSON object")
    return parsed


def event_facts(params: dict, *, max_chars: int = 32000) -> str:
    """Prioritize actual terminals over recursively duplicated upstream prompts."""
    outputs = params.get('flow_outputs') or params.get('parent_flow_outputs') or {}
    facts = {'original_user_request':(params.get('intent_contract') or {}).get('original_request') or params.get('request'),
             'project_id':params.get('project_id'),
             'execution_constraints':(params.get('intent_contract') or {}).get('execution_constraints') or {}}
    recalled=(outputs.get('recall') or {}).get('semantic_output') or {}
    facts['memory_context']={k:recalled.get(k,[]) for k in ('lessons','active_habits','preferences')}
    if outputs.get('assess'):
        facts['cycle_assessment'] = outputs['assess'].get('semantic_output') or {}
    executed = (outputs.get('execute') or {}).get('semantic_output') or {}
    answer_status = (outputs.get('answer',{}).get('semantic_output') or {}).get('runtime_status')
    if answer_status:
        facts['answer_runtime_receipts'] = answer_status
    if executed.get('child_flow_id'):
        facts['executed_child_flow'] = {'flow_id':executed['child_flow_id'],
            'status':executed.get('child_status'), 'lineage':executed.get('child_lineage',{}),
            'source':'runtime parent/child resume receipt, not a model proposed next step'}
    inspected = (outputs.get('inspect') or {}).get('semantic_output', {})
    documents = {}
    for item in inspected.get('local_input_context') or []:
        for doc in item.get('documents') or []:
            documents[str(doc.get('path') or len(documents))] = doc
    context = {
        'operator_feedback':inspected.get('operator_feedback') or {},
        'verified_artifact_index':inspected.get('verified_artifact_index') or {},
        'project_documents':json.dumps(list(documents.values()), ensure_ascii=False)[:6000],
        'continuation_request':str(params.get('request') or '')[:2500],
        'attachments':params.get('attachments') or [],
        'recent_execution':json.dumps(inspected.get('recent_execution', []), ensure_ascii=False)[:2500],
        'evidence_inputs':json.dumps(inspected.get('attached_evidence', []), ensure_ascii=False)[:2000]}
    # Latest execution/verification must survive the context budget.
    for key in ('init','execute','verify','reflect','route','select','critic','hypothesis','understand_3','inspect','recall'):
        if key == 'select':
            facts.update(context)  # Sources before planning, after real terminals.
        value = outputs.get(key)
        if value:
            clean = {k:v for k,v in value.items() if k not in ('model_output','upstream','flow_outputs')}
            if key == 'verify' and isinstance(clean.get('semantic_output'), dict):
                clean['semantic_output'] = {k:v for k,v in clean['semantic_output'].items() if k != 'execution'}
            if key == 'inspect':
                semantic = clean.get('semantic_output') or {}
                clean = {k:v for k,v in semantic.items() if k not in ('recent_execution','attached_evidence')}
                if clean.get('local_input_context'):
                    clean['local_input_context'] = [{k:v for k,v in item.items() if k != 'documents'}
                                                    for item in clean['local_input_context']]
            facts[key] = json.dumps(clean, ensure_ascii=False)[:6000]
    if outputs.get('claims', {}).get('ok'):
        facts['verified_report'] = str(outputs['claims'].get('content') or '')[:10000]
        facts['verified_figures'] = [{k:a.get(k) for k in ('id','caption','measured')} for a in (outputs.get('visuals',{}).get('semantic_output') or {}).get('images',[])]
    prior = params.get('previous_semantic') or (params.get('previous') or {}).get('semantic_output')
    # Other domain flows have different node names; omitting them made a
    # failed browser phase invisible to the user-facing message composer.
    for key, value in outputs.items():
        if key not in facts and isinstance(value, dict) and key not in {'compose','message_critic','deduplicate','channel','send'}:
            facts[key] = {k:v for k,v in value.items() if k in
                          {'ok','status','error','summary','requires_human','business_delta','learning_delta'}}
            if isinstance(value.get('semantic_output'), dict) and value['semantic_output'].get('notes_excerpt'):
                facts[key]['notes_excerpt'] = str(value['semantic_output']['notes_excerpt'])[:2500]
    # Preserve the deterministic final settlement facts.  The generic compact
    # projection intentionally omits semantic_output, which previously hid the
    # apply/reload/rollback result from the final message reviewer.
    final = outputs.get('final_summary') or {}
    if isinstance(final, dict) and isinstance(final.get('semantic_output'), dict):
        semantic = final['semantic_output']
        facts['final_summary_receipt'] = {k:semantic.get(k) for k in (
            'message','decision','production_effective','governance_recorded',
            'matched_verified','runtime_verified','rollback_status',
            'evidence_verified','selected_issue_id')}
    if prior and not outputs: facts['previous'] = prior
    policy = (
        "纯本地 Event 的真实文件写入、回读和哈希回执也是执行证据，不要求每个动作都经过 shell 或 recent_execution。"
        "执行政策：在本次请求的授权和预算内自主查证、尝试和推进。此前模型提出的确认问题、阈值和技术路线不是用户硬约束。"
        "信息缺失时先读取输入文件所在项目的文档、已有脚本和结果，再做可复现检查或检索；不能把向用户提问当作下一次执行动作。"
        "后台动作失败时先读取 recent_execution 中的 checkpoint 和已完成命令回执，复用其中的真实部分结果；整体失败不表示前面的每条命令都没执行。命令行入口缺失不等于库的 Python/API 接口不可用；对照实际 import/调用回执选择已可用的入口，不能反复安装已可用能力。区分未记录、未知和已被证伪；缺注释本身不能证明某结构或性质不存在。"
        "原始目标未完成时，选择一个可实际执行、能补齐关键证据的动作；不得以重复解析或写说明冒充新进展。\n"
        "不同阶段的历史观察是不可变快照：打开视频时尚未转录与后续转录完成不是矛盾，不要修改旧playback记录。正式子Flow的终态和父子绑定可证明执行归属；域目录中的已核验产物可直接消费，不需要复制到work_dir或补写已完成标志才能算执行。下一步应利用内容推进，不能只对齐元数据。\n"
        "执行能力边界：project.action_execute已是Event，它内部只能用真实库与底层工具，不能递归调用其他Event。浏览器Bridge的read_page用于读取网页，voice_transcribe不是浏览器接口。完整视频学习通过本轮真实页面来源加next_flow_request.json请求browser_video_learning子Flow，由runtime顺序执行下载、转录、画面理解和综合；选择动作时就要使用这个能力边界，不再虚构CLI或同名函数。\n"
        "没有既有 before/after 记录不代表不能开展对照实验：可从当前源码或失败测试冻结基线，再构造候选真实运行。"
        "不得把尚待执行的实验结果作为允许开始实验的循环前置条件。采集器没有返回某字段，不等于原始来源不存在相应内容。\n"
    )
    return policy + json.dumps(facts, ensure_ascii=False)[:max_chars]
