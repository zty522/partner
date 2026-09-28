#!/usr/bin/env python3
"""Completion-signal-driven Sprint 18 campaign for 02/04/05, max two slots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from partner.governance.campaign import (
    cancel_campaign, create_campaign, enqueue_work_item, tick_campaign,
)
from partner.governance.campaign_models import CampaignBudget
from partner.governance.campaign_runtime import (
    dispatch_to_instance, runtime_instance_ready, switch_runtime_slots,
)
from partner.governance.campaign_storage import active_campaign_id, list_work_items, load_campaign
from partner.governance.completion_signal import TaskTerminalReceiver
from partner.governance.continuous_policy_loop import (
    CANDIDATE_ID as LONGITUDINAL_CANDIDATE_ID,
    ensure_real_date_window,
)
from partner.governance.sprint18_learning import run_sprint18_learning_cycle


TERMINAL_STATES = {"completed", "failed", "blocked", "cancelled"}


def _enqueue_once(root: Path, campaign_id: str, *, key: str, instance_id: str,
                  project_id: str, title: str, instruction: str, priority: int) -> str:
    for item in list_work_items(str(root), campaign_id):
        if item.source_action_id == key:
            return item.work_item_id
    item = enqueue_work_item(str(root), campaign_id, {
        "instance_id": instance_id, "project_id": project_id,
        "kind": "evolution_experiment" if instance_id == "05" else "project_iteration",
        "title": title, "instruction": instruction, "priority": priority,
        "max_attempts": 1, "requires_artifact": True,
        "requires_delivery": False, "autonomy": "safe", "source_action_id": key,
    })
    return item.work_item_id


def _enqueue_adaptive(root: Path, campaign_id: str) -> dict:
    cycle = run_sprint18_learning_cycle(str(root))
    selection = cycle.get("selection", {}).get("decision", {})
    candidate = cycle.get("candidate", {}).get("candidate", {})
    if not candidate:
        return _enqueue_next_evidence_probe(root, campaign_id, cycle_status=cycle.get("status"))
    decision_id = str(selection.get("decision_id"))
    topic = str(selection.get("topic_key"))
    candidate_path = str(cycle.get("candidate", {}).get("path"))
    work_id = _enqueue_once(
        root, campaign_id, key=decision_id, instance_id="05",
        project_id="agent_self_evolution",
        title=f"Sprint18 adaptive repair {topic}", priority=100,
        instruction=(
            f"[sprint18=true] [topic_decision={decision_id}] [topic={topic}] "
            f"真实读取 CandidateBundle {candidate_path} 及其中所有 evidence trajectory；"
            "使用 Event-first 执行最小因果诊断和可复现的 matched baseline/candidate 验证。"
            "只允许 bundle 的 allowed_surface；不修改 control_policy，不晋升生产。"
            "必须生成机器 JSON，包含冻结条件、正负结果、truth/safety/business/cost 和 rollback；"
            "候选不优就记 rejected/inconclusive，然后写 Receipt 的下一业务动作。"
        ),
    )
    return {"status": "adaptive_work_enqueued", "topic": topic,
            "decision_id": decision_id, "work_item_id": work_id}


def _enqueue_next_evidence_probe(root: Path, campaign_id: str, *, cycle_status: str) -> dict:
    """Run a finite, pre-registered curriculum when old evidence is exhausted.

    These are not keepalive jobs.  Each probe changes one frozen variable or
    consumes one previously unseen source/question.  Once exhausted, the
    controller truthfully returns WAITING_EVIDENCE until a new terminal,
    source revision, user observation, or real date arrives.
    """
    items = list_work_items(str(root), campaign_id)
    if any(item.status not in TERMINAL_STATES for item in items):
        return {"status": "useful_work_already_pending", "cycle": cycle_status}
    probes = [
        {
            "key": "s18-four-harness-unseen-pair-2", "instance_id": "04",
            "project_id": "literature_github_learning", "title": "Four Harness unseen evidence pair 2",
            "priority": 94,
            "instruction": (
                "[sprint18=true] 对 research_active_learning 项目继续下一条未调查的问题-来源组合。"
                "真实读取 openai-codex、deepseek-harness、hermes-agent、openclaw；沿用冻结的三个问题，"
                "不得重复上一条 Hermes context_compressor 证据。执行 observe/select/investigate/matched；"
                "保留逐字证据、source digest、负结果和 production_effective=false。"
            ),
        },
        {
            "key": "s18-targetdiff-robust-seed-20260902", "instance_id": "02",
            "project_id": "molecular_generation", "title": "TargetDiff active selector seed robustness 2",
            "priority": 93,
            "instruction": (
                "[sprint18=true] 执行确定性 Event targetdiff_active_learning，"
                "run_id=sprint18_robust_seed_20260902,rounds=3,batch_size=40,"
                "active_weights=[0.98,0.02,0.0],seed=20260902。冻结 official split、预算和模型，"
                "检验上一轮优势能否跨随机池复现；不优必须拒绝，不做药效因果主张。"
            ),
        },
        {
            "key": "s18-four-harness-unseen-pair-3", "instance_id": "04",
            "project_id": "literature_github_learning", "title": "Four Harness unseen evidence pair 3",
            "priority": 92,
            "instruction": (
                "[sprint18=true] 对 research_active_learning 项目继续第三条未调查的问题-来源组合。"
                "真实读取 openai-codex、deepseek-harness、hermes-agent、openclaw，"
                "不得复用前两条 evidence digest；执行 observe/select/investigate/matched，"
                "记录支持或反证及不采用边界，production_effective=false。"
            ),
        },
        {
            "key": "s18-targetdiff-robust-seed-20260903", "instance_id": "02",
            "project_id": "molecular_generation", "title": "TargetDiff active selector seed robustness 3",
            "priority": 91,
            "instruction": (
                "[sprint18=true] 执行确定性 Event targetdiff_active_learning，"
                "run_id=sprint18_robust_seed_20260903,rounds=3,batch_size=40,"
                "active_weights=[0.98,0.02,0.0],seed=20260903。与前两 seed 做同预算稳健性复核；"
                "完整保留 random、MaxVar、active 结果，production_effective=false。"
            ),
        },
        {
            "key": "s18-targetdiff-robustness-decision-v1", "instance_id": "05",
            "project_id": "agent_self_evolution", "title": "TargetDiff seed robustness decision",
            "priority": 90,
            "instruction": (
                "[sprint18=true] 执行确定性 Event targetdiff_active_robustness，"
                "run_ids=[sprint18_campaign_real_v4,sprint18_robust_seed_20260902,"
                "sprint18_robust_seed_20260903],evaluation_id=sprint18_targetdiff_seed_robustness_v1。"
                "至少三 seed、赢 2/3 且均值同时优于 random/MaxVar 才允许 extended shadow；"
                "否则正式 rejected，production_effective=false。"
            ),
        },
        {
            "key": "s18-policy-recompute-after-new-evidence", "instance_id": "05",
            "project_id": "agent_self_evolution", "title": "Sprint18 posterior recompute",
            "priority": 89,
            "instruction": (
                "[sprint18=true] 执行确定性 Event sprint18_learning_cycle。只消费新产生的 durable evidence，"
                "复算 TopicSelectionDecision、Beta 后验和探索概率；没有高信息目标就明确 WAITING_EVIDENCE，"
                "不修改 control_policy，不晋升生产。"
            ),
        },
        {
            "key": "s18-targetdiff-uncertainty-diagnostic-v1", "instance_id": "02",
            "project_id": "molecular_generation", "title": "TargetDiff uncertainty reliability diagnosis",
            "priority": 88,
            "instruction": (
                "[sprint18=true] 执行确定性 Event targetdiff_uncertainty_diagnostic，"
                "seeds=[20260901,20260902,20260903],labelled_budget=240,"
                "evaluation_id=sprint18_targetdiff_uncertainty_diagnostic_v1。"
                "在 official test 上只做后验可靠性评估，检查 RF 方差与绝对误差相关性、"
                "高不确定性误差富集及 RMSD 分片；test 标签不得进入训练或选择。"
                "本轮不是 Candidate，不调 acquisition 权重，不晋升生产。"
            ),
        },
        {
            "key": "s18-four-harness-context-shadow-v1", "instance_id": "04",
            "project_id": "literature_github_learning", "title": "Four Harness evidence context Candidate shadow",
            "priority": 87,
            "instruction": (
                "[sprint18=true] 执行确定性 Event research_adoption_context_shadow，"
                "query=如何用固定源码证据和历史失败轨迹承接下一轮 Harness 机制采用，"
                "project_id=literature_github_learning,"
                "research_project_id=sprint18_four_harness_adoption,budget_chars=9000。"
                "必须保留最新 Receipt、同项目轨迹、当前 source digest 和直接证据；"
                "只产出隔离 Candidate context，production_effective=false。"
            ),
        },
        {
            "key": "s18-policy-recompute-after-diagnostics-v2", "instance_id": "05",
            "project_id": "agent_self_evolution", "title": "Sprint18 diagnostic posterior recompute",
            "priority": 86,
            "instruction": (
                "[sprint18=true] 执行确定性 Event sprint18_learning_cycle。消费 TargetDiff uncertainty "
                "diagnostic 与 Four Harness context shadow 的新 durable evidence；重新计算选题和 Beta 后验。"
                "没有信息充分的修复题就明确 WAITING_EVIDENCE；不修改 control_policy，不晋升生产。"
            ),
        },
        {
            "key": "s18-targetdiff-crossfit-uncertainty-candidate-v1", "instance_id": "02",
            "project_id": "molecular_generation", "title": "TargetDiff cross-fitted uncertainty matched Candidate",
            "priority": 85,
            "instruction": (
                "[sprint18=true] 执行确定性 Event targetdiff_uncertainty_candidate，"
                "seeds=[20260901,20260902,20260903],labelled_budget=240,"
                "evaluation_id=sprint18_targetdiff_crossfit_uncertainty_candidate_v1。"
                "冻结 official test、标注预算和预测模型，matched 比较 raw RF variance 与"
                "只用已标注样本训练的 cross-fitted residual uncertainty。"
                "test 标签只用于后验验收；不优即 rejected，不修改生产。"
            ),
        },
        {
            "key": "s18-four-harness-context-shadow-redrive-v2", "instance_id": "04",
            "project_id": "literature_github_learning", "title": "Four Harness context Candidate shadow redrive",
            "priority": 84,
            "instruction": (
                "[sprint18=true] 执行确定性 Event research_adoption_context_shadow，"
                "query=如何用固定源码证据和历史失败轨迹承接下一轮 Harness 机制采用，"
                "project_id=literature_github_learning,"
                "research_project_id=sprint18_four_harness_adoption,budget_chars=9000。"
                "这是修复本地 shadow artifact 验收合同后的新 WorkItem；保留旧失败记录，"
                "production_effective=false，不要求外部 channel delivery。"
            ),
        },
        {
            "key": "s18-policy-recompute-after-candidates-v3", "instance_id": "05",
            "project_id": "agent_self_evolution", "title": "Sprint18 candidate outcome posterior recompute",
            "priority": 83,
            "instruction": (
                "[sprint18=true] 执行确定性 Event sprint18_learning_cycle。只消费 cross-fitted uncertainty "
                "matched 结果和 Harness context redrive 新证据，更新 LearningObservation 与后验；"
                "Candidate rejected 也必须作为负样本，不修改 control_policy，不晋升生产。"
            ),
        },
        {
            "key": "s18-four-harness-context-valid-receipt-v3", "instance_id": "04",
            "project_id": "literature_github_learning", "title": "Four Harness context Candidate valid-handoff v3",
            "priority": 82,
            "instruction": (
                "[sprint18=true] 执行确定性 Event research_adoption_context_shadow，"
                "query=如何用固定源码证据和历史失败轨迹承接下一轮 Harness 机制采用，"
                "project_id=literature_github_learning,"
                "research_project_id=sprint18_four_harness_adoption,budget_chars=9000。"
                "旧学习型 Receipt 已追加失效；本轮必须自动绑定当前最新有效业务 Receipt，"
                "同时保留 DeepSeek/Hermes 当前 digest 与同项目正负轨迹。"
                "只产出隔离 Candidate，production_effective=false，不触发项目 continuation。"
            ),
        },
        {
            "key": "s18-policy-final-recompute-v4", "instance_id": "05",
            "project_id": "agent_self_evolution", "title": "Sprint18 final corrected-ledger recompute",
            "priority": 81,
            "instruction": (
                "[sprint18=true] 执行确定性 Event sprint18_learning_cycle。消费 v3 有效 handoff Candidate "
                "与所有 append-only correction 后的最新轨迹，输出最终 Observation summary、TopicDecision "
                "和 learning policy；低于信息阈值就 WAITING_EVIDENCE，不改 control_policy，不晋升生产。"
            ),
        },
    ]
    existing = {item.source_action_id for item in items}
    for probe in probes:
        if probe["key"] in existing:
            continue
        work_id = _enqueue_once(root, campaign_id, **probe)
        return {"status": "evidence_probe_enqueued", "cycle": cycle_status,
                "probe_key": probe["key"], "work_item_id": work_id}
    return {"status": "waiting_evidence", "cycle": cycle_status,
            "reason": "bounded informative probe curriculum exhausted"}


def _seed(root: Path, campaign_id: str) -> list[str]:
    adaptive = _enqueue_adaptive(root, campaign_id)
    external = _enqueue_once(
        root, campaign_id, key="s18-external-four-harness-v1", instance_id="04",
        project_id="literature_github_learning", title="Four Harness mechanism adoption", priority=95,
        instruction=(
            "[sprint18=true] 真实读取固定本地源："
            "/mnt/e/work/partner_workspace/external/code/openai-codex，"
            "/mnt/e/work/partner_workspace/external/code/deepseek-harness，"
            "/mnt/e/work/partner_workspace/external/code/hermes-agent，"
            "/mnt/e/work/partner_workspace/external/code/openclaw。"
            "主动选择一个能改善 Partner 项目承接/失败恢复/长上下文的可证伪机制，"
            "记录 git revision、源文件、逐字证据、不采用边界和 copied_source=false；"
            "生成最小隔离 Candidate 与三类真实下游 matched 验证方案，不替换 Event-first 根基。"
        ),
    )
    molecular = _enqueue_once(
        root, campaign_id, key="s18-targetdiff-active-real-v2", instance_id="02",
        project_id="molecular_generation", title="TargetDiff real active-learning three rounds", priority=90,
        instruction=(
            "[sprint18=true] 执行确定性 Event targetdiff_active_learning，run_id=sprint18_campaign_real_v2,"
            "rounds=3,batch_size=40,active_weights=[0.98,0.02,0.0]。这是官方 split 上的真实样本主动选择，不是函数选择。"
            "必须比较 active/random/MaxVar 等预算三轮结果，候选不优就明确拒绝，"
            "不做药效因果主张；完成后写 Receipt 和下一个由结果决定的实验。"
        ),
    )
    return [str(adaptive.get("work_item_id") or ""), external, molecular]


def _create(root: Path, duration: int) -> str:
    old_id = active_campaign_id(str(root))
    old = load_campaign(str(root), old_id) if old_id else None
    if old and old.status not in {"completed", "cancelled"}:
        cancel_campaign(str(root), old.campaign_id,
                        "superseded by user-authorized Sprint18 active-learning campaign")
    state = create_campaign(
        str(root), goal=("Sprint18 real active learning and bounded self-evolution; "
                         "completion signal dispatch; MiniMax-M3 only; max two slots"),
        allowed_instances=["02", "04", "05"], duration_seconds=duration, max_active=2,
        # This runner is an isolated experiment controller, not an instance's
        # mind.  Keep its control-plane reporting local; only meaningful task
        # transitions may reach the user channel.
        report_interval_seconds=0,
        budget=CampaignBudget(max_work_items=120, max_failures=60, max_retries_per_item=0,
                              max_runtime_seconds=duration, max_model_calls=300,
                              max_cost_units=300.0),
    )
    seeded = _seed(root, state.campaign_id)
    print(json.dumps({"event": "sprint18_campaign_created", "campaign_id": state.campaign_id,
                      "seeded": seeded}, ensure_ascii=False), flush=True)
    return state.campaign_id


def run(root: Path, campaign_id: str, watchdog_seconds: int) -> int:
    # A resumed controller may find that all previously queued work already
    # reached durable terminal state while it was offline.  Refill once before
    # the first tick so a blocked projection does not strand useful work.
    if not any(item.status not in TERMINAL_STATES
               for item in list_work_items(str(root), campaign_id)):
        initial = _enqueue_adaptive(root, campaign_id)
        print(json.dumps({"event": "resume_curriculum_check", "adaptive": initial},
                         ensure_ascii=False), flush=True)
    seen_terminal = {
        item.work_item_id for item in list_work_items(str(root), campaign_id)
        if item.status in TERMINAL_STATES
    }
    with TaskTerminalReceiver(root) as terminals:
        while True:
            temporal = ensure_real_date_window(
                str(root), campaign_id,
                candidate_id=LONGITUDINAL_CANDIDATE_ID,
                pairs_per_project=2,
                max_real_date_windows=3,
            )
            result = tick_campaign(
                str(root), campaign_id,
                dispatch=lambda item, text: dispatch_to_instance(str(root), item, text),
                switch_slots=lambda ids: switch_runtime_slots(str(root), ids),
                runtime_ready=lambda iid: runtime_instance_ready(str(root), iid),
            )
            print(json.dumps({"campaign": result, "temporal": temporal},
                             ensure_ascii=False), flush=True)
            if result.get("status") in {"completed", "cancelled", "missing_campaign"}:
                return 0
            current_terminal = {
                item.work_item_id for item in list_work_items(str(root), campaign_id)
                if item.status in TERMINAL_STATES
            }
            newly_terminal = sorted(current_terminal - seen_terminal)
            if newly_terminal:
                adaptive = _enqueue_adaptive(root, campaign_id)
                seen_terminal = current_terminal
                print(json.dumps({"event": "durable_terminal_change",
                                  "work_item_ids": newly_terminal,
                                  "adaptive": adaptive}, ensure_ascii=False), flush=True)
            event = terminals.wait(max(2, watchdog_seconds))
            if event:
                adaptive = _enqueue_adaptive(root, campaign_id)
                print(json.dumps({"terminal": event, "adaptive": adaptive}, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--campaign-id", default="")
    parser.add_argument("--duration", type=int, default=172800)
    parser.add_argument("--watchdog-seconds", type=int, default=30)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    campaign_id = args.campaign_id or _create(root, args.duration)
    return run(root, campaign_id, args.watchdog_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
