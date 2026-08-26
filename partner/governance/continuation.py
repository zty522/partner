"""Turn verified business outcomes into executable, governed NextActions."""
from __future__ import annotations

import re
from typing import Any

from .models import NextAction
from .rl_control import choose_action


PRIMARY_CHOICES: dict[str, tuple[str, list[dict[str, Any]]]] = {
    "xiaohongshu_operations": (
        "01_content_validation",
        [
            {"strategy_id": "01_backlog_recheck", "event_type": "evidence_execution_slice", "params": {"wave": 3},
             "title": "01 基线：内容 backlog 复核"},
            {"strategy_id": "01_source_fact_check", "event_type": "continuous_project_step", "params": {},
             "title": "01 候选：真实来源可达性核验"},
        ],
    ),
    "molecular_generation": (
        "02_official_split_followup",
        [
            {"strategy_id": "02_split_bootstrap_recheck", "event_type": "targetdiff_official_split_bootstrap", "params": {},
             "title": "02 基线：官方 split bootstrap 复核"},
            {"strategy_id": "02_calibration_analysis", "event_type": "continuous_project_step", "params": {},
             "title": "02 候选：官方测试集校准分析"},
        ],
    ),
    "partner_framework_frontend": (
        "03_framework_improvement",
        [
            {"strategy_id": "03_contract_recheck", "event_type": "framework_campaign_contract_audit", "params": {},
             "title": "03 基线：框架合同复核"},
            {"strategy_id": "03_evidence_graph_canary", "event_type": "continuous_project_step", "params": {},
             "title": "03 候选：持久证据图 canary"},
        ],
    ),
    "literature_github_learning": (
        "04_external_adoption",
        [
            {"strategy_id": "04_index_recheck", "event_type": "external_learning_index_slice", "params": {},
             "title": "04 基线：外部来源索引复核"},
            {"strategy_id": "04_harness_mapping", "event_type": "continuous_project_step", "params": {},
             "title": "04 候选：Harness 概念到 Partner 合同映射"},
        ],
    ),
}

FOLLOWUPS: dict[str, dict[str, Any]] = {
    "01_source_fact_check": {"strategy_id": "01_candidate_brief", "event_type": "continuous_project_step",
                             "title": "01 根据核验来源生成候选 brief"},
    "01_candidate_brief": {"strategy_id": "01_content_readiness_gate", "event_type": "continuous_project_step",
                           "title": "01 内容候选安全门"},
    "02_calibration_analysis": {"strategy_id": "02_error_slices", "event_type": "continuous_project_step",
                                "title": "02 官方测试集误差切片"},
    "03_evidence_graph_canary": {"strategy_id": "03_policy_integration", "event_type": "continuous_project_step",
                                 "title": "03 RL 策略接入合同 canary"},
    "04_harness_mapping": {"strategy_id": "04_adapter_contract", "event_type": "continuous_project_step",
                            "title": "04 Harness 独立适配合同验证"},
}

TERMINAL_STRATEGIES = {
    "01_content_readiness_gate", "02_error_slices", "03_policy_integration", "04_adapter_contract",
    "01_claim_risk_queue", "01_editorial_backlog",
    "02_model_risk_register", "02_next_experiment_gate",
    "03_user_observability_canary", "03_soak_density_analysis",
    "04_reference_gap_matrix", "04_adoption_backlog",
}


def _marker(instruction: str, key: str) -> str:
    match = re.search(rf"\[{re.escape(key)}=([^\]]+)\]", str(instruction or ""))
    return str(match.group(1)).strip() if match else ""


def _to_action(choice: dict[str, Any], *, decision: str, arm: str, campaign_id: str) -> NextAction:
    params = dict(choice.get("params") or {})
    strategy = str(choice["strategy_id"])
    event_type = str(choice["event_type"])
    markers = f"[strategy_id={strategy}] [policy_decision={decision}] [policy_arm={arm}]"
    wave = f" [execution_wave={int(params.get('wave') or 1)}]" if event_type == "evidence_execution_slice" else ""
    params.update({
        "strategy_id": strategy, "policy_decision": decision, "policy_arm": arm,
        "campaign_id": campaign_id,
        "user_request": (
            f"{markers}{wave} 直接执行确定性事件 {event_type}。"
            "必须读取最新 Receipt 和持久 evidence bundle，产出机器结果和业务指标；"
            "不得把报告、PDF 或发送本身写成项目进步。"
        ),
    })
    return NextAction(title=str(choice["title"]), event_type=event_type, params=params)


def propose_continuation(workspace: str, *, project_id: str, campaign_id: str,
                         instruction: str, event_types: list[str], business_progress: bool) -> dict[str, Any]:
    """Return one executable action or an explicit evidence-based wait boundary."""
    if project_id == "agent_self_evolution" or "[portfolio_scout=true]" in instruction:
        return {"action": None, "stop_reason": "monitor/evolution outcome does not own the project continuation"}
    if not business_progress:
        return {"action": None, "stop_reason": "no semantic business increment; wait for changed evidence"}
    strategy = _marker(instruction, "strategy_id")
    if strategy in TERMINAL_STRATEGIES:
        return {"action": None, "stop_reason": "declared evidence chain reached an external-input or approval boundary"}
    if strategy in FOLLOWUPS:
        choice = dict(FOLLOWUPS[strategy])
        choice.setdefault("params", {})
        action = _to_action(choice, decision=f"{project_id}:declared_followup", arm="baseline", campaign_id=campaign_id)
        return {"action": action, "stop_reason": ""}
    entry = PRIMARY_CHOICES.get(project_id)
    if not entry:
        return {"action": None, "stop_reason": "project has no declared safe continuation candidates"}
    decision_key, choices = entry
    selected = choose_action(workspace, project_id, decision_key, choices)
    action = _to_action(
        selected, decision=str(selected["policy_decision"]), arm=str(selected["policy_arm"]),
        campaign_id=campaign_id,
    )
    return {"action": action, "stop_reason": "", "experiment_id": selected.get("experiment_id", "")}
