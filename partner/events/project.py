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
    value = {'recent_execution':recent, 'attached_evidence':attached, 'local_input_context':local, **value}
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": f"已重建 {project_id} 的项目状态与证据上下文"}


def _deliberate(ctx: Any, params: dict[str, Any], purpose: str, instruction: str) -> dict[str, Any]:
    raw, usage = call_model(ctx, purpose=purpose, prompt=(
        instruction + "\n简洁回答，每个字段一两句话，完整 JSON 不超过2000汉字，不复述输入全文。只输出 JSON；不得把生成报告、发送消息或重复旧结果算作项目推进。\n事实="
        + event_facts(params)
    ))
    value = json_object(raw)
    summary = (value.get("reason") or value.get("hypothesis") or value.get("lesson")
               or value.get("next_question") or purpose.replace("_", " "))
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": str(summary),
            "token_usage": usage, "model_output": raw}


def hypothesis_propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _deliberate(ctx, params, "project_hypothesis_propose",
        "提出 2-4 个彼此不同、可证伪且本轮可执行的项目假设。字段 hypotheses，每项含 id,claim,action,event_type,parameters,expected_observation,disproof,required_evidence,risk。每个候选动作应是最小但完整的业务实验，必要的依赖检查、加载、修错和一次实测应包含在同一动作内。不要将纯 import、文件列举、权重形状检查拆成独立项目轮次；只有已经真实失败且需单独定位的问题才选诊断动作。")


def hypothesis_critic(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _deliberate(ctx, params, "project_hypothesis_critic",
        "扮演反驳者，检查每个假设是否重复、不可证伪、缺输入、只产报告或超出权限。字段 accepted,rejected,unknowns,reason。")


def action_select(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _deliberate(ctx, params, "project_action_select",
        "从已通过反驳的可用动作中选择一个最能补齐原始目标缺口的动作。先列 goal_gaps（原始目标尚缺哪些可验证能力/产物），不要反复调整参数追逐模型自己设定的阈值而绕过用户要求的算法实现。字段 selected_event,parameters,reason,success_criteria,evidence_contract,rollback,goal_gaps。优先选择能产生最小端到端业务测量的动作，将工具检查作为该动作的内部步骤；不要仅为取得一个新 JSON 而选择探活。当计算量过大时先真实运行小批样本，不凭空要求大规模阈值。")


def action_execute_inline(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Execute one bounded project action through the configured agent port.

    The action is still a canonical Event: it receives a frozen intent,
    evidence contract and working directory and must return an auditable
    textual terminal.  No PlanExecutor, registry or recursive Harness is used.
    """
    selected = params.get("selected") if isinstance(params.get("selected"), dict) else {}
    if not selected:
        selected = params.get("previous_semantic") if isinstance(params.get("previous_semantic"), dict) else {}
    if not selected:
        selected = params
    adapter = getattr(ctx, "adapter", None)
    if adapter is None or not hasattr(adapter, "execute_task"):
        return {"ok": False, "status": "failed", "error": "project agent port unavailable"}
    project_id = str(params.get("project_id") or getattr(ctx, "project_id", ""))
    work = Path(_workspace(ctx)) / "state/event_runtime/work" / str(getattr(ctx, "job_id", "job")) / str(params.get("flow_id") or "action")
    work.mkdir(parents=True, exist_ok=True)
    request = str(params.get("request", ""))
    prompt = (
        "你正在执行 Partner 的一个有界项目 Event，不是在写计划或报告。\n"
        f"项目={project_id}\n用户目标={request}\n"
        f"已选动作={json.dumps(selected, ensure_ascii=False)}\n"
        f"已核实上下文={event_facts(params, max_chars=16000)}\n"
        f"工作目录={work}\n"
        f"运行时 Event ID={params.get('event_id', '')}；Job ID={getattr(ctx, 'job_id', '')}。需要记录溯源编号时只使用这些真实编号，不自行发明。\n"
        "本轮必须真实执行已选动作，在工作目录保存可解析的实验数据和可复现代码。"
        "不得写占位文件或人为编造指标来满足产物要求。数值必须来自真实输入和实际运行。"
        "只跑 ls/find/cat/head/tail 这类探索性命令（无新文件产出）等同于失败。"
        "若实在无法产出真文件，必须明确说明失败原因 + 缺失的输入/工具。"
        "本动作新脚本和结果写入上方工作目录；selected 中路径只是规划建议，运行时工作目录为准。只有用户明确要求修改指定项目源文件才修改该源文件。不得修改 Partner 运行框架、控制策略或凭据。"
        "结束时必须输出最后一段：\n"
        "【业务产物】<绝对路径1> [bytes=N]\n"
        "【执行动作】<一句话>\n"
        "【真实发现】<一句话>\n"
        "【未解决】<一句话>"
    )
    reply = str(adapter.execute_task(prompt, **(
        {"seconds": ctx.action_seconds} if hasattr(ctx, "action_seconds") else {})) or "").strip()
    reply = re.sub(r"<(think|analysis)>.*?</\1>", "", reply,
                   flags=re.IGNORECASE | re.DOTALL).strip()
    if re.search(r"</?(think|analysis)>", reply, re.IGNORECASE):
        return {"ok": False, "status": "failed", "error": "project agent reasoning block was not closed",
                "failure_class": "model", "mechanism": "project_agent/unclosed_reasoning"}
    if not reply or reply.startswith("Task recorded:"):
        return {"ok": False, "status": "failed", "error": "project agent returned no executed result",
                "failure_class": "environment", "mechanism": "project_agent/no_execution"}
    files = [str(path) for path in work.rglob("*") if path.is_file() and ".execution" not in path.parts]
    evidence_path = work / "执行结果.md"
    evidence_path.write_text(reply + "\n", encoding="utf-8")
    if str(evidence_path) not in files:
        files.append(str(evidence_path))
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
    receipts = list((work / '.execution').glob('command_*.json'))
    executed = any(json.loads(p.read_text()).get('exit_code') == 0 for p in receipts)
    artifact_files = [c['path'] for c in checks if c['valid']]
    business_delta = bool(executed and artifact_files)

    return {"ok": True, "status": "completed", "summary": reply[:500],
            "outcome": reply[:4000], "files": files, "evidence_refs": files,
            "business_delta": business_delta, "executed_capability": "project.agent_action",
            "semantic_output": {"result": reply, "files": files,
                                "business_delta": business_delta,
                                "artifact_count": len(artifact_files),
                                "artifact_checks":checks, "command_receipts":[str(p) for p in receipts],
                                "declared_artifacts": declared,
                                "verified_artifacts": artifact_files,
                                "fabrication_detected": any(not Path(p).is_file() for p in declared)}}


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
    verified = bool(prior.get('business_delta') and evidence and novel and all(
        row['valid'] and row['sha256'] == expected.get(row['path']) for row in evidence))
    from partner.runtime.implementation_evidence import inspect_implementations
    implementations = inspect_implementations(prior.get("files") or [])
    return {"ok": True, "status": "completed", "business_delta":verified,
            "semantic_output":{"verified":verified, "evidence":evidence, "implementation_evidence":implementations,
                "execution":sem, "new_content":novel, "limitation":"解析和执行来源核验不等于科学结论成立"},
            "evidence_refs":[r['path'] for r in evidence if r['valid']],
            "summary":"执行产物已通过格式、哈希和执行来源检查" if verified else "未获得可验证的业务数据，不计为推进"}


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
    return _deliberate(ctx, params, "project_outcome_reflect",
        "只依据已验证结果反思：哪些认识被支持或否证、是否真的推进业务、下一项目问题是什么。有限样本未达阈值只是否定本次配置下的假设，不能推出物理不可能、化学不可达或性能上限。比较两轮效果前必须核对同一样本集合、预处理、搜索预算与指标口径；多个因素变化不能归因于单因素，不同候选数的最优值或TopK不能直接声称排名改善。还须对照 implementation_evidence 的实际函数调用和常量列表：写死候选列表再过滤不等于实现生成算法，记录方法名称不等于运行了该方法；仅有静态调用名也不能证明某分支已执行，须结合命令回执。字段 supported,rejected,unknown,business_delta,next_question,lesson,evidence_refs。"
        + extra)


def continuation_propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _deliberate(ctx, params, "project_continuation_propose",
        "把本轮终态变成下一轮新的可证伪任务，而非重复本轮。字段 next_goal,why_new,required_inputs,stop_condition。")


DEFINITIONS = [
    EventDefinition("project.state_inspect", "project", "重建项目真实状态和证据边界", state_inspect, reads_existing_artifact=True),
    EventDefinition("project.hypothesis_propose", "project", "提出本轮可证伪项目假设", hypothesis_propose, execution_method="llm"),
    EventDefinition("project.hypothesis_critic", "project", "以反例审查项目假设", hypothesis_critic, execution_method="llm"),
    EventDefinition("project.action_select", "project", "选择一个能减少关键不确定性的动作", action_select, execution_method="llm"),
    EventDefinition("project.action_execute", "project", "执行一个经过选择的有界项目能力", action_execute, idempotent=False),
    EventDefinition("project.outcome_verify", "project", "核验业务动作及其证据", outcome_verify, reads_existing_artifact=True),
    EventDefinition("project.outcome_reflect", "project", "根据终态修订项目认识", outcome_reflect, execution_method="llm"),
    EventDefinition("project.continuation_propose", "project", "形成不重复的下一项目任务", continuation_propose, execution_method="llm"),
]
