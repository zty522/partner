"""Shared bounded model port for cognitive Events."""
from __future__ import annotations

from typing import Any
import json
import re


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
    for attempt in range(3):
        remaining = deadline - time.monotonic() - 2 if deadline else 90
        if remaining <= 0:
            raise TimeoutError("cognitive Event deadline exhausted")
        try:
            if hasattr(adapter, "chat_once"):
                options = {"max_tokens":retry_budget} if retry_budget is not None else {}
                from partner.adapters.adapter import DirectAdapter
                if isinstance(adapter, DirectAdapter):
                    # A larger output budget also needs bounded generation time;
                    # retaining the 8k timeout made 16k truncation retries futile.
                    options["timeout"] = min(180 if (retry_budget or 8192) > 8192 else 150 if purpose.startswith("report_") else 90, remaining)
                raw = adapter.chat_once(prompt, purpose=purpose, **options)
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
        if attempt < 2:
            delay = 2 ** (attempt + 1) + random.uniform(0, 1.5)
            time.sleep(max(0, min(delay, deadline - time.monotonic() - 2)) if deadline else delay)
    raise RuntimeError(f"model failed after 3 attempts: {last_error}")


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
        from json_repair import repair_json
        parsed = repair_json(value[start:] if start >= 0 else value, return_objects=True)
    if not isinstance(parsed, dict):
        raise ValueError("expected a JSON object")
    return parsed


def event_facts(params: dict, *, max_chars: int = 32000) -> str:
    """Prioritize actual terminals over recursively duplicated upstream prompts."""
    outputs = params.get('flow_outputs') or params.get('parent_flow_outputs') or {}
    facts = {'original_user_request':(params.get('intent_contract') or {}).get('original_request') or params.get('request'),
             'project_id':params.get('project_id')}
    inspected = (outputs.get('inspect') or {}).get('semantic_output', {})
    documents = {}
    for item in inspected.get('local_input_context') or []:
        for doc in item.get('documents') or []:
            documents[str(doc.get('path') or len(documents))] = doc
    context = {
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
    if prior and not outputs: facts['previous'] = prior
    policy = (
        "纯本地 Event 的真实文件写入、回读和哈希回执也是执行证据，不要求每个动作都经过 shell 或 recent_execution。"
        "执行政策：在本次请求的授权和预算内自主查证、尝试和推进。此前模型提出的确认问题、阈值和技术路线不是用户硬约束。"
        "信息缺失时先读取输入文件所在项目的文档、已有脚本和结果，再做可复现检查或检索；不能把向用户提问当作下一次执行动作。"
        "后台动作失败时先读取 recent_execution 中的 checkpoint 和已完成命令回执，复用其中的真实部分结果；整体失败不表示前面的每条命令都没执行。命令行入口缺失不等于库的 Python/API 接口不可用；对照实际 import/调用回执选择已可用的入口，不能反复安装已可用能力。区分未记录、未知和已被证伪；缺注释本身不能证明某结构或性质不存在。"
        "原始目标未完成时，选择一个可实际执行、能补齐关键证据的动作；不得以重复解析或写说明冒充新进展。\n"
        "没有既有 before/after 记录不代表不能开展对照实验：可从当前源码或失败测试冻结基线，再构造候选真实运行。"
        "不得把尚待执行的实验结果作为允许开始实验的循环前置条件。采集器没有返回某字段，不等于原始来源不存在相应内容。\n"
    )
    return policy + json.dumps(facts, ensure_ascii=False)[:max_chars]
