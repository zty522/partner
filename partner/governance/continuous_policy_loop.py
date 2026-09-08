"""Bounded, diverse baseline/candidate work for sustained Partner learning.

This module only materialises auditable Campaign WorkItems.  The normal
Campaign controller, Partner Harness, Claim gate, delivery callbacks and
Episode/Reward reducers remain the execution and truth authorities.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .campaign import enqueue_work_item
from .campaign_storage import campaign_dir, campaign_lock, list_work_items, load_campaign, save_work_item
from .models import now_iso
from .storage import atomic_json


CANDIDATE_ID = "candidate_research_downstream_a420574f08ad"
CANDIDATE_STRATEGY = "candidate_evidence_trajectory_context_v1"
BASELINE_STRATEGY = "baseline_governed_context_v1"
RESEARCH_PROJECT_ID = "partner04_harness_learning_20260831_v3"
HERMES_SOURCE = "/mnt/e/work/partner_workspace/external/code/hermes-agent/agent/context_compressor.py"
JITRL_SOURCE = ("/mnt/e/work/partner_workspace/external/literature/"
                "Just-In-Time Reinforcement Learning Continual Learning in LLM Agents Without Gradient Updates.pdf")
CODEX_SOURCE = ("/mnt/e/work/partner_workspace/external/code/openai-codex/"
                "codex-rs/core/src/compact.rs")


TOPICS = [
    "上下文压缩时哪些消息必须保护，以及这会怎样影响跨轮项目承接",
    "运行失败如何转化为下一轮训练指令，而不是机械重试",
    "token 预算、旧工具结果裁剪与近期上下文保留之间的权衡",
    "critic 如何控制任务难度，使主动学习不过难也不过易",
    "无梯度持续学习与持久记忆分别解决什么问题、不能混淆什么",
    "如何从失败轨迹形成可证伪的最小 Candidate，而不是大改框架",
    "上下文 handoff 怎样避免重复工作并保留当前真实状态",
    "稀疏反馈下如何选择下一项最有信息增益的安全任务",
    "运行时学习如何保留负样本，同时避免把假成功写成正奖励",
    "上下文压缩与 Event-first 状态恢复可以如何协同但保持职责隔离",
    "如何用 evidence quote、Claim Ledger 和 critic 共同抑制幻觉",
    "工具结果预剪枝为何应与 LLM 总结分层执行，并如何验收",
    "长期任务中任务熟练度如何影响课程生成与计算预算",
    "如何把项目 Receipt、运行轨迹和外部证据分级装入上下文",
    "Agent 自进化何时只应更新策略配置，何时才需要代码 Candidate",
    "怎样定义 baseline/candidate 匹配任务，避免来源和预算混杂",
    "从失败中学习时如何区分用户输入、规划合同、Event 与交付责任",
    "连续运行怎样同时保证完成即接续、去重、可恢复和双槽上限",
    "如何用真实业务 Reward 约束研究学习，避免只优化报告格式",
    "什么证据足以让 shadow Candidate 进入受限 canary，什么仍不足",
]


PROJECTS = {
    "04": ("literature_github_learning", "从文献与源码学习角度"),
    "05": ("agent_self_evolution", "从 Partner 主动学习与自进化治理角度"),
}


def experiment_id(candidate_id: str = CANDIDATE_ID) -> str:
    digest = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:10]
    return f"experiment_research_continuous_{digest}_v1"


def _sources_for(index: int) -> list[str]:
    # One predeclared stress pair per 20 topics asks the Candidate to handle a
    # source it may not yet have investigated.  An honest rejection supplies
    # useful negative evidence; it must never be converted into false success.
    if index == len(TOPICS) - 1:
        return [CODEX_SOURCE, JITRL_SOURCE]
    return [HERMES_SOURCE, JITRL_SOURCE]


def build_instruction(*, instance_id: str, topic_index: int, policy_arm: str,
                      date_window: str, candidate_id: str = CANDIDATE_ID,
                      research_project_id: str = RESEARCH_PROJECT_ID) -> str:
    project_id, perspective = PROJECTS[instance_id]
    topic = TOPICS[topic_index % len(TOPICS)]
    match_key = f"{date_window}:{project_id}:topic_{topic_index + 1:02d}"
    strategy = CANDIDATE_STRATEGY if policy_arm == "candidate" else BASELINE_STRATEGY
    candidate_marker = f" Candidate ID={candidate_id}" if policy_arm == "candidate" else ""
    source_contract = ",".join(_sources_for(topic_index % len(TOPICS)))
    return (
        f"[execution_mode=serial_queue] [continuous_policy_loop=true] "
        f"[experiment_id={experiment_id(candidate_id)}] [match_key={match_key}] "
        f"[policy_arm={policy_arm}] [strategy_id={strategy}]"
        f"{candidate_marker} query: {perspective}分析：{topic}。"
        "必须真实读取冻结来源，输出有领域结论的 Markdown；每条实质 Claim 只能绑定一个来源绝对路径，"
        "逐字 evidence_quote，跨来源综合只写正文。不得修改生产代码、control_policy 或执行 promotion。"
        f", project_id: {project_id}, research_project_id: {research_project_id}, "
        f"instance_id: {instance_id}, budget_chars: 9000, source_paths=[{source_contract}]"
    )


def seed_window(workspace: str, campaign_id: str, *, date_window: str | None = None,
                pairs_per_project: int = 14, topic_offset: int = 0,
                candidate_id: str = CANDIDATE_ID) -> list[dict[str, Any]]:
    date_window = date_window or datetime.now().astimezone().date().isoformat()
    created: list[dict[str, Any]] = []
    count = max(1, min(int(pairs_per_project), len(TOPICS)))
    for instance_id, (project_id, _) in PROJECTS.items():
        for serial in range(count):
            topic_index = (topic_offset + serial) % len(TOPICS)
            for arm in ("baseline", "candidate"):
                instruction = build_instruction(
                    instance_id=instance_id,
                    topic_index=topic_index,
                    policy_arm=arm,
                    date_window=date_window,
                    candidate_id=candidate_id,
                )
                item = enqueue_work_item(workspace, campaign_id, {
                    "instance_id": instance_id,
                    "project_id": project_id,
                    "kind": "project_iteration",
                    "title": f"{project_id} matched {topic_index + 1:02d} {arm}",
                    "instruction": instruction,
                    # Campaign scheduling is priority-first.  Encode strict
                    # pair order explicitly instead of relying on timestamps
                    # or random WorkItem IDs created in the same clock tick.
                    "priority": max(1, 100 - 2 * topic_index
                                    - (1 if arm == "candidate" else 0)),
                    "max_attempts": 1,
                    "requires_artifact": True,
                    # The experiment remains useful during a QQ outage: the
                    # normal Harness persists a local observation and Reward.
                    # Production-readiness still independently requires real
                    # delivery, so this cannot inflate sustained-business
                    # evidence or claim that the user received the artifact.
                    "requires_delivery": False,
                    "autonomy": "safe",
                })
                created.append({"work_item_id": item.work_item_id,
                                "instance_id": instance_id,
                                "project_id": project_id,
                                "policy_arm": arm,
                                "match_key": f"{date_window}:{project_id}:topic_{topic_index + 1:02d}"})
    return created


def ensure_real_date_window(
    workspace: str,
    campaign_id: str,
    *,
    candidate_id: str = CANDIDATE_ID,
    pairs_per_project: int = 2,
    max_real_date_windows: int = 3,
) -> dict[str, Any]:
    """Idempotently materialise one matched window per real local date.

    The Campaign creation date is recorded as the first historical window so
    a controller that crosses midnight can seed the second window without
    inventing time.  This function only creates ordinary WorkItems; normal
    Harness truth, delivery and Reward governance remain authoritative.
    """
    state = load_campaign(workspace, campaign_id)
    if not state:
        return {"ok": False, "status": "campaign_not_found"}
    path = campaign_dir(workspace, campaign_id) / "sprint18_longitudinal_windows.json"
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        created_date = str(state.created_at or "")[:10]
        ledger = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "windows": ([{"date": created_date, "source": "campaign_creation",
                           "work_items": 0}] if created_date else []),
            "max_real_date_windows": max_real_date_windows,
        }
    windows = list(ledger.get("windows") or [])
    today = datetime.now().astimezone().date().isoformat()
    if any(str(row.get("date") or "") == today for row in windows):
        if not path.exists():
            ledger["updated_at"] = now_iso()
            atomic_json(path, ledger)
        return {"ok": True, "status": "date_window_already_seeded",
                "date": today, "windows": len(windows), "path": str(path)}
    limit = max(1, min(int(ledger.get("max_real_date_windows") or max_real_date_windows), 3))
    if len(windows) >= limit:
        return {"ok": True, "status": "temporal_gate_complete",
                "windows": len(windows), "path": str(path)}
    missing = validate_sources()
    if missing:
        return {"ok": False, "status": "source_missing", "missing": missing}
    count = max(1, min(int(pairs_per_project), len(TOPICS)))
    required = 2 * len(PROJECTS) * count
    remaining = state.budget.max_work_items - state.usage.work_items_created
    if remaining <= required:  # preserve one final-report boundary
        return {"ok": False, "status": "work_item_budget_insufficient",
                "required": required + 1, "remaining": remaining}
    topic_offset = (len(windows) * count) % len(TOPICS)
    created = seed_window(
        workspace, campaign_id, date_window=today,
        pairs_per_project=count, topic_offset=topic_offset,
        candidate_id=candidate_id,
    )
    windows.append({
        "date": today, "source": "real_local_date",
        "pairs_per_project": count, "topic_offset": topic_offset,
        "work_items": len(created), "work_item_ids": [row["work_item_id"] for row in created],
        "created_at": now_iso(),
    })
    ledger.update({
        "schema_version": 1, "candidate_id": candidate_id,
        "windows": windows, "max_real_date_windows": limit,
        "updated_at": now_iso(),
    })
    atomic_json(path, ledger)
    return {"ok": True, "status": "real_date_window_seeded", "date": today,
            "windows": len(windows), "work_items": len(created),
            "work_item_ids": [row["work_item_id"] for row in created], "path": str(path)}


def enable_local_observation_fallback(workspace: str, campaign_id: str) -> int:
    """Migrate only unstarted experiment items; never rewrite finished facts."""
    changed = 0
    with campaign_lock(workspace, campaign_id):
        for item in list_work_items(workspace, campaign_id):
            if item.status != "proposed" or not item.requires_delivery:
                continue
            if "[continuous_policy_loop=true]" not in item.instruction:
                continue
            item.requires_delivery = False
            item.evidence.append("delivery_policy=local_observation_allowed_during_channel_outage")
            item.updated_at = now_iso()
            save_work_item(workspace, item)
            changed += 1
    return changed


def validate_sources() -> list[str]:
    return [path for path in (HERMES_SOURCE, JITRL_SOURCE, CODEX_SOURCE)
            if not Path(path).is_file()]
