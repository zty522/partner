"""Interaction Events: three epistemically distinct reads and direct answer."""
from __future__ import annotations

from typing import Any
import json

from partner.event_fabric.catalog import EventDefinition
from ._llm import call_model, json_object


def _intent(ctx: Any, params: dict[str, Any], role: str) -> dict[str, Any]:
    request = str(params.get("request") or "").strip()
    upstream = params.get("upstream") if isinstance(params.get("upstream"), dict) else {}
    prior = params.get("prior") or [
        value.get("semantic_output", value) for value in upstream.values()
        if isinstance(value, dict)
    ]

    from partner.runtime.status_context import runtime_status
    status_context = runtime_status(ctx, params)
    role_prompts = {
        "observe": (
            "忠实提取目标、明确约束、期望结果；不得扩张授权。"
            "必须区分用户明确要求与暂定方案：constraints 仅包含原文明示约束。"
            "自行选用的算法、阈值、候选数量写入 assumptions 并标为待验证，不得冒充用户要求。"
            "结构域范围、物种等事实没有文件或来源证据时保持 unknown，先安排证据检查，不得猜成事实。"
            "这是意图摘要，不是研究方案：每个数组最多4项，每项一句话，整个 JSON 不超过1500汉字。"
            "详细技术方案留给项目执行阶段，不要在此重复长篇论证。"
            "只输出 JSON，字段 assumptions,goal,constraints,success_criteria,evidence_requirements,"
              "knowledge_gaps,route,project_hint,reason。"
        ),
        "counter_read": (
            "攻击已有理解：找遗漏、相反解释、不可证伪目标和过度授权。"
            "问候语、专业术语和已有项目归属都不能单独决定是否执行工具。"
            "用户明确只问概念或不启动任务时不得扩张为实验；已有事实需查证的请求不能仅凭口头回答冒充完成。"
            "只输出 JSON，字段 assumptions,goal,constraints,success_criteria,evidence_requirements,"
              "knowledge_gaps,route,project_hint,reason。"
        ),
        "synthesize": (
            "综合原文和前两遍审议，形成最小、可证伪、可验证的最终契约。"
            "route 只能为 direct_answer 或 project_iteration：用户仅问概念、解释差别且无需读取当前外部材料时选 direct_answer；"
            "查询已有任务进展且下方已有运行器读取的真实记录时选 direct_answer，不为状态查询再开研究或取证迭代；缺失部分如实说尚不能确认。"
            "需要新的外部文件读取、检索、计算、修改或推进任务时选 project_iteration。"
            "派发目标 dispatch_target 必须从下列候选中选一个：\n"
            "  - direct_answer：直接答，不入队\n"
            "  - browser_video_learning：用户想学一条具体视频（payload.url 必填）\n"
            "  - xhs_authoring：用户想在小红书发布内容（payload.topic 必填，payload.media 可选）\n"
            "  - 已有 project_id：从下方当前 instance 项目列表中选一个\n"
            "payload 仅在 dispatch_target=browser_video_learning / xhs_authoring 时填写。"
            "warm_reply：1-2 句普通中文，告诉用户接下来要做什么；不要承诺未确定的结果；不要复读内部 ID、文件名、哈希。"
            "这是意图摘要，不是研究方案：每个数组最多4项，每项一句话，整个 JSON 不超过1500汉字。"
            "只输出 JSON，字段 assumptions,goal,constraints,success_criteria,evidence_requirements,"
              "knowledge_gaps,route,dispatch_target,warm_reply,payload,reason。"
        ),
    }
    prompt = (
        "你是 Partner 意图审议 Event。" + role_prompts[role]
        + '\n类型契约：route 必须是字符串枚举 "direct_answer" 或 "project_iteration"，不是计划、数组或步骤。'
        + "\n用户原文=" + json.dumps(request, ensure_ascii=False)
        + "\n用户随附的证据路径（内容须在执行中读取，不可仅凭文件名下结论）="
        + json.dumps(params.get("attachments") or [], ensure_ascii=False)[:4000]
        + "\n当前 instance 已有 project（供 dispatch_target 选择）="
        + json.dumps(params.get("available_projects") or [], ensure_ascii=False)[:4000]
        + "\n已有审议=" + json.dumps(prior, ensure_ascii=False)[:16000]
        + "\n运行器已读取的当前任务记录=" + json.dumps(status_context,ensure_ascii=False)[:14000]
        + "\n基准/候选/消融方法臂=" + json.dumps(params.get("method_arm") or "", ensure_ascii=False)
        + (("\n消融 drop=" + json.dumps((params.get("execution_constraints") or {}).get("ablation_drop") or "", ensure_ascii=False))
           if (params.get("method_arm") or "").startswith("ablation") else "")
        + "\n方法臂语义：candidate 用 GEPA 候选 + DGM 谱系 + ACE 记忆消费；"
          "\nbaseline 走最简单可控路径；no_change 禁止任何代码改动；"
          "\nreflection-only 允许 prompt 改写但不允许 skill/flow 改写；"
          "\nexternal-only 强制外部学习优先；joint 三者并用且交叉验证。"
    )
    raw, usage = call_model(ctx, purpose=f"intent_{role}", prompt=prompt)
    value = json_object(raw)
    calls = 1
    if role == 'synthesize' and value.get('route') not in ('direct_answer', 'project_iteration'):
        original_raw = raw
        raw, retry_usage = call_model(ctx, purpose=f"intent_{role}", prompt=prompt
            + '\n你上次输出违反 route 类型契约。请重新输出完整契约；仅修正结构，不扩张原始授权。'
            + '\n错误输出=' + original_raw[:12000])
        usage = {k: usage.get(k, 0) + retry_usage.get(k, 0)
                 for k in set(usage) | set(retry_usage)
                 if isinstance(usage.get(k, 0), (int, float)) and isinstance(retry_usage.get(k, 0), (int, float))}
        value = json_object(raw)
        calls += 1
        if value.get('route') not in ('direct_answer', 'project_iteration'):
            raise ValueError('intent synthesize route remains invalid after one schema repair')
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": str(value.get("goal") or value.get("reason") or "意图审议完成"),
            "token_usage": usage, "model_output": raw, "model_calls": calls}


def intent_observe(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _intent(ctx, params, "observe")


def intent_counter_read(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _intent(ctx, params, "counter_read")


def intent_synthesize(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _intent(ctx, params, "synthesize")


def direct_answer(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from partner.runtime.status_context import runtime_status
    status_context = runtime_status(ctx, params)
    raw, usage = call_model(ctx, purpose="direct_answer", prompt=(
        "直接回答用户问题。先根据整句语义判断问题的主语、谓语和比较对象，不能因局部词语改答另一个专业问题。"
        "只回答原命题，不擅自换成更强命题：数值按某公式计算，不等于该数值随时间守恒；记录分段数量，不等于知道它按场景还是固定时长切分。"
        "未知的切分方式、采样规则、排名方向和参与原子范围必须保留未知，不自行补成事实。"
        "不为显得专业而增加未经核实的反例、算法身份或必要/充分条件；短问答先清楚回答，再给一句成立的理由。"
        "用两三句普通中文回答，不展开数学证明或额外举例。"
        "回答任务进展时控制在180字以内，优先说具体内容、意义与尚未完成的环节，不报内部编号、文件名、哈希和处理日志；需要纠正上次答复就直接说明更正后的事实。"
        "不得声称自己进行了新实验；可以依据下方运行器真实记录回答进展，计划不等于完成，未知不猜。表达自然、清楚、简洁。\n"
        "对话项目（仅供术语消歧，不代表已执行或已验证）：" + str(params.get('project_id') or '') + "\n用户原文："
        + str(params.get("request") or "")
        + "\n运行器已读取的真实任务记录=" + json.dumps(status_context,ensure_ascii=False)[:16000]
    ))
    return {"ok": True, "status": "completed", "answer": raw,
            "summary": raw[:500], "token_usage": usage,
            "semantic_output":{"runtime_status":status_context}}


def project_init(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Initialise a brand-new project from a user-described direction.

    Triggered when ``interaction.intent_synthesize`` produced a contract
    whose ``project_hint`` looks like a fresh project name (no existing
    project matches).  We call into ``project_registry.register_project``
    and ``governance.project_scaffold.scaffold_project`` so the new
    project lands on disk with a real brief and an empty
    ``external_artifacts/`` tree before the next ``project.job_dispatch``
    runs.  No LLM call — the intent contract already contains the goal.

    Parameters:
      params["request"]              — raw user text (for diagnostic only)
      params["intent_contract"]      — dict from upstream intent_synthesize
      params["project_id_hint"]      — optional override; else we
                                        normalize from intent_contract.project_hint
      params["goal"]                 — optional override; else intent_contract.goal
      params["instance_id"]          — which instance is hosting the project
      params["workspace"]            — workspace root path
    """
    contract = params.get("intent_contract") if isinstance(params.get("intent_contract"), dict) else {}
    synthesized = (params.get("flow_outputs") or {}).get("understand_3", {}).get("semantic_output", {})
    if isinstance(synthesized, dict):
        contract = {**contract, **synthesized}
    project_hint = (params.get("project_id_hint")
                    or contract.get("explicit_project_id")
                    or contract.get("project_hint")
                    or contract.get("goal")
                    or params.get("request")
                    or "")
    raw = str(project_hint).strip().split()[0] if project_hint else ""
    if not raw:
        # Truly empty input — fail-closed so callers know they forgot
        # to provide a hint.
        return {"ok": False, "status": "failed",
                "error": "project_init: empty project_id after normalisation"}
    import re as _re
    # Normalise: lowercase ASCII tokens joined by "_" + keep CJK chars.
    # filesystem on WSL/NTFS supports UTF-8 so CJK is fine as long as
    # we strip whitespace and path separators.
    ascii_parts = _re.findall(r"[A-Za-z0-9]+", raw)
    cjk_chars = _re.findall(r"[\u4e00-\u9fff]", raw)
    if ascii_parts:
        project_id = "_".join(ascii_parts).lower()[:48].rstrip("_") or "new_project"
    elif cjk_chars:
        project_id = ("".join(cjk_chars)[:16]) or "new_project"
    else:
        project_id = "new_project"
    # Strip any leftover path separators defensively
    project_id = project_id.replace("/", "_").replace("\\", "_")
    if not project_id:
        return {"ok": False, "status": "failed",
                "error": "project_init: empty project_id after normalisation"}
    goal = (params.get("goal") or contract.get("goal")
            or contract.get("desired_outcome") or params.get("request") or "")
    workspace = str(getattr(ctx, "workspace", "") or params.get("workspace") or "")
    instance_id = str(getattr(ctx, "intake_instance_id", "") or params.get("origin_instance")
                      or getattr(ctx, "instance_id", "") or params.get("instance_id") or "")
    if not workspace:
        return {"ok": False, "status": "failed",
                "error": "project_init: workspace missing in ctx/params"}
    # Order matters.  register_project internally calls _summarize_project
    # which calls project_dir(...).mkdir() — so if we register first the
    # scaffold step will see the directory as already-existing and skip the
    # brief write.  Scaffold first (creates dir + brief), then register
    # (the registry row then finds the brief and summarises it).
    scaffold_summary: dict[str, Any] = {}
    try:
        from partner.governance.project_scaffold import scaffold_project
        scaffold_summary = scaffold_project(workspace, instance_id, project_id, goal) or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "failed", "error": f"scaffold_project: {type(exc).__name__}: {exc}"}
    # Now the brief tree is real; register so the project shows up in the
    # global project registry.
    reg_row: dict[str, Any] = {}
    try:
        from partner.projects.project_registry import register_project
        from pathlib import Path
        registry_workspace = Path(workspace).expanduser().resolve()
        if registry_workspace.parent.name == 'instances':
            registry_workspace = registry_workspace.parent.parent
        if instance_id and _re.fullmatch(r'[A-Za-z0-9_-]+', instance_id):
            registry_workspace = registry_workspace / 'instances' / instance_id
        reg_row = register_project(str(registry_workspace), project_id,
                                   status="active",
                                   reason="auto-created by interaction.project_init",
                                   make_public=True) or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "failed", "error": f"register_project: {type(exc).__name__}: {exc}"}
    # 3. Write a minimal intent contract for the new project.
    contract_path = ""
    try:
        from pathlib import Path as _Path
        if workspace:
            base = _Path(workspace).expanduser().resolve()
            if base.parent.name == "instances":
                base = base.parent.parent
            share = base / "share" / "projects" / project_id
            # share must already exist thanks to scaffold_project; mkdir is
            # only a safety net for the edge case where scaffold was skipped
            # because the registry row already existed but the dir was deleted.
            share.mkdir(parents=True, exist_ok=True)
            contract_path = str(share / "project_contract.json")
            payload = {
                "project_name": project_id,
                "current_goal": str(goal)[:600],
                "current_mainline": "fresh project; first Job pending",
                "allowed_scope": list(contract.get("explicit_constraints") or contract.get("constraints") or [])[:8],
                "forbidden_scope": [],
                "source_roots": [],
                "completion_criteria": list(contract.get("success_criteria") or [])[:8],
                "updated_at": _now_iso(),
                "origin": "interaction.project_init",
            }
            import json as _json
            tmp = contract_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(_json.dumps(payload, ensure_ascii=False, indent=2))
            import os as _os
            _os.replace(tmp, contract_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "failed", "error": f"project_contract: {type(exc).__name__}: {exc}"}
    # "created_new" means: this call materially created the brief tree on
    # disk.  scaffold_project reports skipped_existing=True when the project
    # root was already there (the registry path may pre-create it via
    # ``_summarize_project``'s project_dir call, but that does not write
    # a brief).
    brief_path = ""
    try:
        from pathlib import Path as _Path
        base = _Path(workspace).expanduser().resolve()
        if base.parent.name == "instances":
            base = base.parent.parent
        brief_path = str(base / "share" / "projects" / project_id / "project_brief.md")
    except Exception:
        pass
    brief_existed_before = bool(scaffold_summary.get("created") and
                                 "project_brief.md" in (scaffold_summary.get("created") or []))
    semantic = {
        "project_id": project_id,
        "goal": str(goal)[:240],
        "registry_row": reg_row,
        "scaffold": scaffold_summary,
        "contract_path": contract_path,
        "created_new": brief_existed_before,
    }
    import hashlib
    from pathlib import Path
    evidence = []
    for filename in (brief_path, contract_path):
        path = Path(filename)
        if not path.is_file() or not path.stat().st_size:
            return {'ok':False, 'status':'failed', 'error':'project artifact missing or empty after write', 'path':filename}
        data = path.read_bytes()
        evidence.append({'path':str(path), 'bytes':len(data),
                         'sha256':hashlib.sha256(data).hexdigest(),
                         'excerpt':data.decode('utf-8')[:1800]})
    semantic['artifact_readback'] = evidence
    return {"ok": True, "status": "completed",
            "project_id": project_id,
            "evidence_refs": [brief_path, contract_path],
            "semantic_output": semantic,
            "summary": (f"新项目已注册 {project_id}（brief={bool(scaffold_summary.get('created'))}）"
                        if semantic["created_new"]
                        else f"项目 {project_id} 已存在，沿用现有 brief")}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat()




DEFINITIONS = [
    EventDefinition("interaction.intent_observe", "interaction", "第一遍忠实理解用户意图", intent_observe, execution_method="llm"),
    EventDefinition("interaction.intent_counter_read", "interaction", "第二遍反向审查意图理解", intent_counter_read, execution_method="llm"),
    EventDefinition("interaction.intent_synthesize", "interaction", "第三遍形成可证伪意图契约", intent_synthesize, execution_method="llm"),
    EventDefinition("interaction.direct_answer", "interaction", "无需工具的简单问题直接回答", direct_answer, execution_method="llm"),
    EventDefinition("interaction.project_init", "interaction", "从用户描述创建新项目并写入 brief/contract", project_init),
]
