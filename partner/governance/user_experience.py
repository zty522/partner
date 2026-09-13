"""User-observable progress and domain-aware delivery copy for Campaign work."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from partner.interfaces.presentation import (
    ActivityKind,
    activity_heading,
    classify_activity,
    compact_message,
    report_filename as presentation_report_filename,
    terminal_kind,
)


PROJECT_NAMES = {
    "01": "小红书内容运营",
    "02": "分子生成方法研究",
    "03": "分子动力学模拟学习与验证",
    "04": "文献与 GitHub 学习",
    "05": "Partner 自进化与经验策略学习",
}


PROJECT_MILESTONES = {
    "01": ("读取上一轮内容证据与来源", "执行来源/主张/风险分析", "形成候选与安全边界"),
    "02": ("核验数据来源、拆分和字段合同", "运行可复现实验或统计分析", "比较指标并解释科学边界"),
    "03": ("承接上一轮体系与数值证据", "运行可复现模拟或参数对照", "核对稳定性、边界和下一实验"),
    "04": ("核验外部原文、代码和版本", "映射到 Partner 独立适配面", "验证可采用内容与未集成边界"),
    "05": ("摄取本轮新业务轨迹", "重算奖励与 baseline/candidate", "决定 Issue、Experiment 与是否晋升"),
}

STRATEGY_ACTIONS = {
    "01_source_fact_check": "逐条核验来源状态、内容指纹和待确认主张",
    "01_evidence_backed_draft": "读取来源正文并形成来源绑定候选稿",
    "01_claim_evidence_matrix": "整理主张、来源证据和发布边界",
    "01_claim_risk_queue": "按证据缺口和误导风险重排核验队列",
    "01_editorial_backlog": "根据证据完整度整理人工复核队列",
    "molecular_method_candidate_benchmark": "运行分子方法候选对照并分析科学边界",
    "molecular_data_readiness_audit": "检查靶点与活性数据是否足以进入下一阶段",
    "02_targetdiff_model_risk_register": "承接既有实验，登记模型风险并定义下一实验门",
    "03_md_integrator_comparison": "以相同初态和预算比较分子动力学积分器",
    "03_md_timestep_sweep": "运行时间步扫描并比较能量漂移",
    "03_md_timestep_stability": "扫描多个时间步并检查能量稳定性",
    "03_md_temperature_sweep": "改变初始温度并比较数值稳定性",
    "04_research_adoption_shadow": "读取外部原文和代码，在隔离环境验证可采用内容",
    "04_adoption_effect_probe": "检查外部知识是否被后续项目动作真实使用",
    "external_knowledge_scout": "检索并核验新的论文与 GitHub 源码证据",
    "05_event_contract_inventory": "核对 Partner 与 Hermes 的 Event 合同及运行边界",
    "05_failure_path_regression": "运行失败路径回归并检查责任归类",
    "05_candidate_gap_matrix": "从真实轨迹整理可证伪的代码改进缺口",
    "05_code_candidate_autonomous": "从真实失败提出最小代码改动并进行隔离验证",
    "offline_policy_learning_self_evolution": "摄取新轨迹，比较现行策略与候选策略",
    "agent_active_learning_observe_episode": "读取本轮失败 Episode 与原始轨迹",
    "agent_active_learning_select": "结合证据选择最值得诊断的问题",
    "agent_active_learning_diagnostic_shadow": "调用因果 Critic 诊断具体失败机制",
    "agent_active_learning_propose_episode_repair": "形成受限修复建议与可证伪验收门",
    "agent_active_learning_feedback": "记录诊断反馈与本轮学习终态",
}

METRIC_LABELS = {
    "train_rows": "训练样本", "test_rows": "测试样本",
    "train_test_group_overlap": "训练/测试组重叠", "records": "记录数",
    "unique_urls": "唯一来源", "new_trajectories": "新增轨迹",
    "grounded_drafts": "来源绑定草稿", "claims_with_source": "有来源主张",
    "records_mapped": "已整理记录", "records_with_source_evidence": "有证据记录",
    "publish_authorized": "获准发布", "sources_checked": "已检查来源",
    "sources_reachable": "可访问来源", "simulations_executed": "模拟次数",
    "stable_simulations": "稳定模拟", "integrators_compared": "积分器数量",
    "worst_relative_energy_drift": "最大相对能量漂移",
    "preferred_integrator": "推荐积分器", "adoption_contexts_consumed": "采用上下文",
    "adoption_checks_passed": "采用检查通过", "adoption_checks_total": "采用检查总数",
    "candidate_improved": "候选优于基线", "new_code_candidate_applied": "新代码已生效",
    "backlog_items": "待处理候选", "ready_for_human_claim_review": "可人工复核",
    "evidence_required": "仍缺证据", "llm_calls": "LLM 判断次数",
    "scaffold_count_delta": "骨架数变化", "fingerprint_diversity_delta": "指纹多样性变化",
    "mean_qed_delta": "平均 QED 变化",
    "sources_read": "真实读取源码", "focused_regression_passed": "聚焦回归通过",
    "gaps_identified": "识别缺口", "candidate_specs": "Candidate 规格数",
}


def instance_from_workspace(workspace: str) -> str:
    match = re.search(r"/instances/(0[1-5])(?:/|$)", str(workspace))
    return match.group(1) if match else ""


def visibility_mode(instruction: str) -> str:
    return "compact" if "[portfolio_scout=true]" in str(instruction) else "standard"


def _strategy(instruction: str, event_type: str) -> str:
    match = re.search(r"strategy_id=([^\s\]]+)", str(instruction))
    return match.group(1) if match else event_type


def _activity(*, instance_id: str, event_type: str, instruction: str,
              result: dict[str, Any] | None = None) -> ActivityKind:
    payload = _result_payload(result or {})
    return classify_activity(
        instance_id=instance_id,
        event_type=event_type,
        strategy_id=str(payload.get("strategy_id") or _strategy(instruction, event_type)),
        instruction=instruction,
        result=result,
    )


def _received_instruction(instruction: str) -> str:
    """Return user-readable business intent without controller protocol markers."""
    text = str(instruction or "").strip()
    if "Partner 自进化诊断" in text:
        return "针对本轮重复或失败证据，执行受限诊断、修复建议和隔离验证。"
    if "外部知识主动学习" in text or "主动学习 Event" in text:
        return "围绕当前项目缺口检索外部原文与源码，并验证哪些知识可以实际采用。"
    if "原生项目续跑" in text or "承接上一轮" in text:
        return "承接上一轮真实结果，选择并执行下一项有证据支持的项目工作。"
    if "检查交付物并补齐缺口" in text:
        return "检查上一轮交付物；有真实缺口才补齐，没有新证据则停止重复。"
    match = re.search(r"(?:^|\n)任务：(.*?)(?:\n\n强制要求：|$)", text, re.S)
    if match:
        text = match.group(1).strip()
    text = re.sub(r"\[[^\]\n]+\]\s*", "", text)
    text = re.sub(r"直接执行确定性事件\s+[A-Za-z0-9_]+[。.]?\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:700] or "未读取到有效任务正文"


def instruction_received_message(*, instance_id: str, title: str, event_type: str,
                                 instruction: str) -> str:
    project = PROJECT_NAMES.get(instance_id, f"实例 {instance_id or '?'}")
    activity = _activity(instance_id=instance_id, event_type=event_type, instruction=instruction)
    boundary = {
        "project": "我会承接上一轮证据，只有出现新的业务结果才记作推进。",
        "active_learning": "这是外部知识学习；读过不算学会，必须说明证据和将怎样用于项目。",
        "self_evolution": "这是 Partner 自身改进；反思不算进化，必须经过隔离对照并说明是否影响生产。",
    }.get(activity.key, "只报告真实执行；需要你处理时会明确说明。")
    return compact_message(
        f"{activity_heading(activity, instance_id)}｜收到\n"
        f"{project}\n"
        f"你要我做：{_received_instruction(instruction)}\n"
        f"{boundary}"
    )


def start_message(*, instance_id: str, title: str, event_type: str, instruction: str) -> str:
    activity = _activity(instance_id=instance_id, event_type=event_type, instruction=instruction)
    milestones = PROJECT_MILESTONES.get(instance_id, ("读取承接证据", "实际执行", "验收和交付"))
    if visibility_mode(instruction) == "compact":
        return compact_message(
            f"{activity_heading(activity, instance_id)}｜开始检查\n"
            f"检查：{milestones[0]}；{milestones[1]}\n"
            "无变化则直接说明并停止，不用重复报告充数。"
        )
    lead = {
        "project": "现在开始推进项目",
        "active_learning": "现在开始补知识缺口",
        "self_evolution": "现在开始验证改进假设",
    }.get(activity.key, "现在开始")
    return compact_message(
        f"{activity_heading(activity, instance_id)}｜{lead}\n"
        f"先做：{milestones[0]}\n"
        f"随后：{milestones[1]}；最后用真实证据判断是否继续。"
    )


def _result_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Unwrap execution envelopes until the domain result is reached.

    Native execution currently has both a step envelope and an Event envelope.
    Looking through only one layer hid ``strategy_id``, Candidate details and
    source names, which made genuinely different runs look like one template.
    Stop unwrapping as soon as domain fields are present so a domain's own
    nested ``result`` value is not accidentally discarded.
    """
    payload = result
    domain_fields = {
        "strategy_id", "business_metrics", "metrics", "candidate",
        "adoption_candidate", "source_inventory", "repository", "paper",
    }
    for _ in range(4):
        if any(key in payload for key in domain_fields):
            break
        nested = payload.get("result")
        if not isinstance(nested, dict) or nested is payload:
            break
        payload = nested
    return payload


def _metrics_text(result: dict[str, Any]) -> str:
    payload = _result_payload(result)
    metrics = payload.get("business_metrics") or payload.get("metrics") or result.get("metrics") or {}
    if isinstance(metrics, dict) and metrics:
        pairs = []
        for key, value in list(metrics.items())[:6]:
            if isinstance(value, dict):
                continue
            label = METRIC_LABELS.get(str(key), str(key).replace("_", " "))
            if isinstance(value, bool):
                value = "是" if value else "否"
            elif isinstance(value, float):
                value = f"{value:.4g}"
            pairs.append(f"{label}：{value}")
        if pairs:
            return "；".join(pairs)
    for keys in (("train_rows", "test_rows", "train_test_group_overlap"),
                 ("records", "unique_urls"), ("new_trajectories",)):
        pairs = [f"{METRIC_LABELS.get(key, key)}：{payload[key]}" for key in keys if key in payload]
        if pairs:
            return "；".join(pairs)
    return str(result.get("summary") or result.get("status") or "已生成机器结果")[:500]


def action_text(event_type: str, result: dict[str, Any] | None = None) -> str:
    """Describe the real action without exposing internal event identifiers."""
    result = result or {}
    payload = _result_payload(result)
    strategy = str(payload.get("strategy_id") or result.get("strategy_id") or "")
    if not strategy:
        nested = result
        for _ in range(4):
            child = nested.get("result") if isinstance(nested, dict) else None
            if not isinstance(child, dict) or child is nested:
                break
            nested = child
            strategy = str(nested.get("strategy_id") or "")
            if strategy:
                break
    metrics = payload.get("metrics") or payload.get("business_metrics") or {}
    if event_type == "molecular_method_candidate_benchmark" and isinstance(metrics, dict):
        method = str(metrics.get("method_id") or "").strip()
        seed = metrics.get("replicate_seed")
        if method:
            suffix = f"（seed {seed}）" if seed not in (None, "") else ""
            return f"运行 {method} 分子筛选候选与同数据基线对照{suffix}"
    if event_type == "external_knowledge_scout":
        repo = payload.get("repository") or {}
        paper = payload.get("paper") or {}
        repo_name = str(repo.get("name") or "").strip() if isinstance(repo, dict) else ""
        paper_title = str(paper.get("title") or "").strip() if isinstance(paper, dict) else ""
        if repo_name or paper_title:
            return f"交叉核验 GitHub {repo_name or '源码'} 与论文《{paper_title or '待核验论文'}》"
    return STRATEGY_ACTIONS.get(strategy) or STRATEGY_ACTIONS.get(event_type) or {
        "framework_campaign_contract_audit": "运行框架合同测试并核对恢复路径",
        "continuous_project_step": "执行本项目当前有证据支持的下一项工作",
        "evidence_execution_slice": "读取内容证据并执行去重与来源检查",
        "targetdiff_provenance_audit": "核验数据来源、校验和与拆分结构",
        "external_learning_index_slice": "核验外部来源版本及实际采用边界",
    }.get(event_type, "执行本轮计划中的实际操作")


def execution_receipt_message(*, instance_id: str, event_type: str, result: dict[str, Any],
                              instruction: str) -> str:
    files = [Path(str(value)).name for value in result.get("files") or []]
    payload = _result_payload(result)
    metrics = payload.get("business_metrics") or payload.get("metrics") or {}
    if result.get("ok") and isinstance(metrics, dict) and metrics.get("candidate_improved") in {0, False}:
        state = "实验已完成；候选未优于基线"
    else:
        state = "执行完成" if result.get("ok") else "未通过"
    activity = _activity(instance_id=instance_id, event_type=event_type,
                         instruction=instruction, result=result)
    monitor = visibility_mode(instruction) == "compact"
    command = payload.get("command") or result.get("command") or []
    if isinstance(command, list):
        readable = []
        for value in command:
            part = str(value)
            if "/" in part and (part.startswith("/") or part.startswith("\\")):
                part = Path(part).name or part
            readable.append(part)
        action = " ".join(readable)
    else:
        action = str(command or "")
    if not action:
        action = action_text(event_type, result)
    pdf_count = sum(name.lower().endswith(".pdf") for name in files)
    evidence_count = max(0, len(files) - pdf_count)
    artifacts = (f"PDF {pdf_count} 份，本地证据 {evidence_count} 份"
                 if files else "无有效产物")
    active_learning_results = {
        "agent_active_learning_observe_episode": "已读取并固定失败证据",
        "agent_active_learning_select": "已选定一个有证据支持的诊断目标",
        "agent_active_learning_diagnostic_shadow": "已形成可由后续实验否证的因果诊断",
        "agent_active_learning_propose_episode_repair": "已形成受限修复建议与验收条件",
        "agent_active_learning_feedback": "诊断反馈已进入追加式学习记录",
    }
    verdict = str(result.get("experiment_verdict") or payload.get("experiment_verdict") or "").strip()
    if not verdict and str(payload.get("strategy_id") or "") == "05_code_candidate_autonomous":
        verdict = str(payload.get("decision") or payload.get("status") or "").strip()
    if verdict:
        verdict_text = {
            "rolled_back": "已回滚", "rejected": "已拒绝",
            "inconclusive": "证据不足", "no_new_candidate": "没有新 Candidate",
            "validated_shadow": "隔离验证通过", "promoted": "已晋升",
        }.get(verdict, verdict)
        result_text = (
            f"Candidate 结论：{verdict_text}；"
            f"生产{'已' if (result.get('production_effective') or payload.get('production_effective')) else '未'}生效"
        )
        state = ("实验与晋升已完成" if (result.get("production_effective") or payload.get("production_effective"))
                 else "实验已完成；未晋升")
    else:
        result_text = active_learning_results.get(event_type) or _metrics_text(result)
    if activity.key == "self_evolution" and isinstance(payload.get("candidate"), dict):
        candidate = payload.get("candidate") or {}
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        intervention = str(candidate.get("intervention") or "").strip()
        if candidate_id:
            result_text += f"；提出 {candidate_id}"
        if intervention:
            result_text += f"；改动假设：{intervention}"
    meaning = {
        "project": "这是否推进：由新业务证据和下一实验决定，不按文件数量计算。",
        "active_learning": "如何使用：只有被下一次项目动作消费并改善结果，才算完成学习闭环。",
        "self_evolution": "生产影响：只有对照、回归、Critic 和晋升门都通过后才会生效。",
    }.get(activity.key, "结论以机器证据为准。")
    return compact_message(
        f"{activity_heading(activity, instance_id)}｜{'检查结果' if monitor else '新结果'}\n"
        f"做了什么：{action[:420]}\n"
        f"发现：{result_text}\n"
        f"{meaning}\n"
        f"状态：{state}；{artifacts}"
    )


def verification_receipt_message(*, instance_id: str, result: dict[str, Any],
                                 file_delivery: dict[str, Any], instruction: str) -> str:
    report_ok = file_delivery_confirmed(file_delivery)
    monitor = visibility_mode(instruction) == "compact"
    activity = _activity(instance_id=instance_id, event_type="", instruction=instruction, result=result)
    return compact_message(
        f"{activity_heading(activity, instance_id)}｜已核验证据\n"
        f"机器验收：{'通过' if result.get('ok') else '未通过'}；结果与本地证据已核对\n"
        f"PDF：{'已送达' if report_ok else '未确认送达'}"
    )


def finish_message(*, instance_id: str, title: str, event_type: str, result: dict[str, Any],
                   instruction: str, report_delivered: bool) -> str:
    payload = _result_payload(result)
    activity = _activity(instance_id=instance_id, event_type=event_type,
                         instruction=instruction, result=result)
    blocked = bool(result.get("blocked") or payload.get("blocked"))
    boundary = str(result.get("blocked_reason") or payload.get("blocked_reason") or "")
    next_action = str(result.get("next_action") or payload.get("next_action") or "")
    if blocked:
        conclusion = f"受控等待：{boundary or '等待明确恢复条件'}"
    elif result.get("ok"):
        explicit = str(payload.get("conclusion") or "").strip()
        project_narrative = str(payload.get("project_narrative") or "").strip()
        decision = str(payload.get("decision") or payload.get("status") or "").strip()
        if explicit:
            conclusion = explicit
        elif project_narrative:
            conclusion = project_narrative
        elif decision.lower() in {"rejected", "inconclusive", "promoted", "accepted"}:
            production = bool(payload.get("production_effective"))
            conclusion = f"Candidate 隔离验证结论为 {decision}；生产{'已' if production else '未'}生效。"
        elif decision == "candidate_no_change":
            conclusion = "没有出现支持新 Candidate 的证据，本轮未重复修改生产。"
        elif isinstance(payload.get("candidate"), dict) and payload.get("candidate"):
            candidate = payload["candidate"]
            candidate_id = str(candidate.get("candidate_id") or "未命名 Candidate")
            scope = str(candidate.get("scope") or "shadow")
            production = bool(payload.get("production_effective"))
            conclusion = (
                f"已形成 {candidate_id}（{scope}）；"
                f"生产{'已' if production else '未'}生效，仍需匹配对照后才能晋升。"
            )
        else:
            conclusion = {
                "project": "本轮执行与交付已完成；业务是否改善以上一条指标和后续实验为准。",
                "active_learning": "本轮外部证据动作已完成；只有被后续项目采用并改善结果才算学会。",
                "self_evolution": "本轮自身检查已完成；只有明确通过对照与晋升门的 Candidate 才会影响生产。",
            }.get(activity.key, "本轮实际操作、证据核对和交付均已完成。")
    else:
        conclusion = f"本轮未通过：{result.get('error') or result.get('status') or '未知错误'}"
    if not next_action:
        project_question = str(payload.get("project_question") or "").strip()
        next_action = (f"围绕“{project_question}”选择并执行下一项可证伪工作。"
                       if project_question else
                       "承接本轮结果选择下一项有证据的工作；没有新证据时停止重复。")
    terminal = terminal_kind(activity, result)
    label = "完成" if result.get("ok") and terminal.key != "waiting" else terminal.label
    report_status = "已送达" if report_delivered else "未确认送达"
    if event_type == "research_adoption_context_shadow":
        report_status = "本轮为知识衔接，不单独生成 PDF"
    return compact_message(
        f"{terminal.icon} {activity.label}｜{instance_id}｜{label}\n"
        f"本轮结论：{conclusion}\n"
        f"给你的报告：{report_status}\n"
        f"接下来：{next_action}"
    )


def report_caption(instance_id: str, title: str, event_type: str = "",
                   instruction: str = "", result: dict[str, Any] | None = None) -> str:
    activity = _activity(instance_id=instance_id, event_type=event_type,
                         instruction=instruction, result=result)
    topic = action_text(event_type, result) if event_type else title
    return f"{activity_heading(activity, instance_id)}｜{topic or PROJECT_NAMES.get(instance_id, '本轮结果')}"


def report_filename(instance_id: str, title: str, event_type: str = "",
                    instruction: str = "", result: dict[str, Any] | None = None) -> str:
    activity = _activity(instance_id=instance_id, event_type=event_type,
                         instruction=instruction, result=result)
    payload = _result_payload(result or {})
    return presentation_report_filename(
        instance_id=instance_id,
        activity=activity,
        title=action_text(event_type, result) if event_type else title,
        strategy_id=str(payload.get("strategy_id") or _strategy(instruction, event_type)),
    )


def file_delivery_confirmed(delivery: dict[str, Any]) -> bool:
    """Normalize the two supported file-delivery acknowledgement shapes."""
    if delivery.get("delivered") is True:
        return True
    try:
        pushed = int(delivery.get("pushed") or 0)
        total = int(delivery.get("total") or 0)
    except (TypeError, ValueError):
        return False
    return bool(delivery.get("ok") and total > 0 and pushed == total)


def progress_receipt(*, phase: str, message: str, delivery: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": phase,
        "message": message,
        "delivered": bool(delivery.get("delivered")),
        "ok": bool(delivery.get("ok")),
    }


def validate_progress_receipts(receipts: list[dict[str, Any]], instruction: str) -> dict[str, Any]:
    required = ({"instruction_received", "started", "executed", "verified", "finished"}
                if "[user_progress_v2=true]" in str(instruction)
                else {"started", "executed", "finished"})
    present = {str(row.get("phase")) for row in receipts if row.get("delivered")}
    missing = sorted(required - present)
    return {"ok": not missing, "required": sorted(required), "delivered": sorted(present), "missing": missing,
            "mode": visibility_mode(instruction)}
