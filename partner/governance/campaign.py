"""Recoverable long-running campaign orchestration.

The controller deliberately executes one bounded WorkItem at a time per
instance.  Partner tasks remain event driven; this layer persists what should
run next, survives process restarts, and prevents an in-memory research loop
from pretending to be an overnight campaign.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .campaign_models import (
    CampaignBudget, CampaignReport, CampaignState, InstanceLease, WorkItem,
)
from .campaign_storage import (
    active_campaign_id, append_campaign_event, campaign_dir, campaign_lock,
    list_leases, list_work_items, load_campaign, load_lease, load_work_item,
    save_campaign, save_lease, save_report, save_work_item, set_active_campaign,
)
from .evolution_loop import record_issue
from .evidence_archive import archive_work_item_evidence, semantic_outcome_fingerprint
from .continuation import propose_continuation
from .models import NextAction, now_iso
from .project_loop import record_action_state, record_iteration, request_next_action
from .scheduler import ROLES, load_scheduler
from .storage import atomic_json, governance_log, latest_receipt, load_project_state, workspace_root


CAMPAIGN_MARKER = re.compile(
    r"\[PARTNER_CAMPAIGN\s+campaign_id=(?P<campaign>[^\s\]]+)\s+work_item_id=(?P<work>[^\s\]]+)\]"
)
TERMINAL_WORK = {"completed", "blocked", "cancelled"}
BUSY_WORK = {"leased", "queued", "running"}
BOUNDED_CAMPAIGN_EVENTS = {
    "framework_campaign_contract_audit",
    "external_learning_index_slice",
    "offline_rl_self_evolution",
    "evidence_execution_slice",
    "targetdiff_project_slice",
    "targetdiff_data_contract",
    "targetdiff_group_baseline",
    "targetdiff_nonlinear_compare",
    "targetdiff_residual_analysis",
    "targetdiff_group_cv",
    "targetdiff_outlier_sensitivity",
    "targetdiff_nonlinear_group_cv",
    "targetdiff_provenance_audit",
    "targetdiff_ligand_aggregation_cv",
    "targetdiff_target_balanced_metrics",
    "targetdiff_failure_group_diagnostics",
    "targetdiff_group_bootstrap",
    "targetdiff_method_decision",
    "targetdiff_official_split_benchmark",
    "targetdiff_official_split_bootstrap",
    "targetdiff_official_split_calibration",
    "targetdiff_official_split_error_slices",
    "continuous_project_step",
}
NAMED_ARTIFACT_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.(?:jsonl|py|md|pdf|csv|json|png|jpe?g|webp|xlsx)(?![A-Za-z0-9])", re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event(workspace: str, campaign_id: str, event: str, **details: Any) -> None:
    append_campaign_event(workspace, campaign_id, {"ts": now_iso(), "event": event, **details})


def _dependencies_satisfied(item: WorkItem, items: list[WorkItem]) -> bool:
    """Keep the RL audit behind the business/framework evidence it evaluates."""
    waits_for_campaign_evidence = (
        "offline_rl_self_evolution" in item.instruction
        or (item.instance_id == "05" and "evidence_execution_slice" in item.instruction)
    )
    if not waits_for_campaign_evidence:
        return True
    return all(
        other.status in TERMINAL_WORK
        for other in items
        if other.work_item_id != item.work_item_id
        and other.kind != "report"
        and other.instance_id != "05"
    )


def _sync_offline_rl_at_stop(workspace: str, campaign_id: str) -> None:
    """Capture late outcomes, including the 05 audit itself, before final reporting."""
    # Local import avoids coupling the Campaign model/storage import graph to
    # the optional offline learner during module initialization.
    from .rl_evolution import run_offline_rl_update

    result = run_offline_rl_update(workspace, campaign_id)
    created = int(result.get("new_trajectories") or 0) if result.get("ok") else 0
    if created:
        _event(
            workspace,
            campaign_id,
            "offline_rl_final_sync",
            new_trajectories=created,
            policy_path=str(result.get("policy_path") or ""),
        )


def create_campaign(
    workspace: str,
    *,
    goal: str,
    allowed_instances: list[str],
    duration_seconds: int,
    max_active: int = 2,
    report_interval_seconds: int = 3600,
    budget: CampaignBudget | None = None,
) -> CampaignState:
    existing_id = active_campaign_id(workspace)
    existing = load_campaign(workspace, existing_id) if existing_id else None
    if existing and existing.status not in {"completed", "cancelled"}:
        raise ValueError(f"unfinished campaign already active: {existing_id} ({existing.status})")
    started = _now()
    effective_budget = budget or CampaignBudget(max_runtime_seconds=max(60, int(duration_seconds)))
    effective_budget.max_runtime_seconds = min(effective_budget.max_runtime_seconds, max(60, int(duration_seconds)))
    state = CampaignState(
        goal=goal,
        allowed_instances=allowed_instances,
        deadline_at=(started + timedelta(seconds=int(duration_seconds))).isoformat(timespec="seconds"),
        budget=effective_budget,
        status="running",
        max_active=max_active,
        restore_instances=list(load_scheduler(str(workspace_root(workspace))).get("active_slots") or [])[:2],
        report_interval_seconds=report_interval_seconds,
        last_report_at=started.isoformat(timespec="seconds"),
        started_at=started.isoformat(timespec="seconds"),
    )
    save_campaign(workspace, state)
    set_active_campaign(workspace, state.campaign_id)
    _event(workspace, state.campaign_id, "campaign_created", goal=goal, allowed_instances=allowed_instances)
    return state


def enqueue_work_item(workspace: str, campaign_id: str, params: dict[str, Any]) -> WorkItem:
    with campaign_lock(workspace, campaign_id):
        state = load_campaign(workspace, campaign_id)
        if not state:
            raise ValueError("campaign not found")
        is_final_report = (
            str(params.get("kind") or "project_iteration") == "report"
            and str(params.get("title") or "") == "Campaign 最终日报"
        )
        # The budget is a hard total, including transport/report work.  Keep
        # one slot for the final report so a full business queue can still
        # terminate visibly without exceeding its declared cap.
        reserve_final = state.budget.max_work_items > 1
        creation_limit = state.budget.max_work_items if is_final_report else (
            state.budget.max_work_items - 1 if reserve_final else 1
        )
        if state.usage.work_items_created >= creation_limit:
            raise ValueError("campaign work-item budget exhausted")
        autonomy = str(params.get("autonomy") or "").strip()
        instruction_text = str(params.get("instruction") or "")
        if not autonomy:
            sensitive = ("真实发布", "付款", "支付", "购买", "输入密码", "使用凭证", "删除生产")
            negated = ("不得真实发布", "不会真实发布", "不真实发布", "禁止真实发布")
            autonomy = "human_required" if any(word in instruction_text for word in sensitive) and not any(
                word in instruction_text for word in negated
            ) else "safe"
        item = WorkItem(
            campaign_id=campaign_id,
            instance_id=str(params.get("instance_id") or ""),
            project_id=str(params.get("project_id") or ""),
            kind=str(params.get("kind") or "project_iteration"),
            title=str(params.get("title") or ""),
            instruction=instruction_text,
            priority=int(params.get("priority", 50)),
            max_attempts=int(params.get("max_attempts", state.budget.max_retries_per_item + 1)),
            source_action_id=str(params.get("source_action_id") or ""),
            source_issue_id=str(params.get("source_issue_id") or ""),
            requires_artifact=bool(params.get("requires_artifact", True)),
            requires_delivery=bool(params.get("requires_delivery", True)),
            autonomy=autonomy,
        )
        if item.instance_id not in state.allowed_instances:
            raise ValueError(f"instance {item.instance_id} is outside campaign scope")
        item.validate()
        save_work_item(workspace, item)
        state.usage.work_items_created += 1
        state.updated_at = now_iso()
        save_campaign(workspace, state)
        _event(workspace, campaign_id, "work_item_created", work_item_id=item.work_item_id,
               instance_id=item.instance_id, project_id=item.project_id, kind=item.kind)
        return item


DEFAULT_SEEDS = {
    "01": (
        "小红书流程安全审计",
        "执行确定性事件 xiaohongshu_inspect_upload_requirements。读取最新小红书 ProjectState、Receipt "
        "和操作手册；执行一个不会真实发布的最小验证。"
        "如果操作浏览器，每个关键步骤必须截图、调用视觉模型描述，并通过真实 QQ callback 发送说明。"
        "产出一份包含证据、发现、限制和下一项可执行动作的 Markdown。不得发布内容。",
    ),
    "02": (
        "分子项目证据边界与数据接入准备",
        "执行确定性事件 molecular_data_readiness_audit。读取分子项目最新 Receipt。"
        "不得重复前四轮 QED/SA 排序；检查恢复第五轮需要的真实目标、"
        "活性、对接或实验数据，完成一个可验证的数据接入准备动作并产出报告。如果仍缺数据，明确 blocked。",
    ),
    "03": (
        "Partner 框架最小改进候选",
        "执行确定性事件 framework_campaign_contract_audit。在当前代码上运行 Campaign/RL "
        "针对性合同测试，产出机器可读结果和详细 PDF；不得用“设计了修复”代替真实测试。",
    ),
    "04": (
        "文献与 GitHub 真实学习切片",
        "执行确定性事件 external_learning_index_slice。核验 external 中 Polar、RLVR-World、SESA 和 "
        "JIT-RL 的真实文件、哈希和可用设计，生成目录、学习报告和详细 PDF。索引不等于已集成。",
    ),
    "05": (
        "自进化机制证据审计",
        "执行确定性事件 offline_rl_self_evolution。把当前 Campaign 真实 WorkItem 转换为可审计轨迹，"
        "用产物、QQ 送达、验收、重试和 watchdog 更新离线候选策略，建立一个正式 candidate "
        "Experiment。未达样本和回归门槛不得 promoted。",
    ),
}


def seed_default_work(workspace: str, campaign_id: str) -> list[WorkItem]:
    state = load_campaign(workspace, campaign_id)
    if not state:
        raise ValueError("campaign not found")
    created: list[WorkItem] = []
    existing = {(item.instance_id, item.kind) for item in list_work_items(workspace, campaign_id)}
    for instance in state.allowed_instances:
        if (instance, "project_iteration") in existing:
            continue
        title, instruction = DEFAULT_SEEDS[instance]
        created.append(enqueue_work_item(workspace, campaign_id, {
            "instance_id": instance,
            "project_id": ROLES[instance],
            "kind": "project_iteration",
            "title": title,
            "instruction": f"Campaign 总目标：{state.goal}\n\n当前实例职责：{instruction}",
            "priority": 70 if instance in {"01", "02"} else 60,
            "requires_artifact": True,
            "requires_delivery": True,
        }))
    return created


def seed_execution_work(workspace: str, campaign_id: str, waves: int = 2) -> list[WorkItem]:
    """Seed bounded execution waves that write/run code and analyze real inputs."""
    state = load_campaign(workspace, campaign_id)
    if not state:
        raise ValueError("campaign not found")
    waves = max(1, min(3, int(waves)))
    roles = {
        "01": "分析真实 content inbox，编写并运行内容来源分析代码；只准备候选 brief，不上传、不发布。",
        "02": "读取 TargetDiff affinity_info.pkl，编写并运行统计代码，建立亲和力/对接/RMSD 基线与分层分析。",
        "03": "读取历史 Campaign 状态，编写并运行运行质量分析器，回放预算、泛化动作与交付指标。",
        "04": "真实 clone/fetch SESA 仓库，运行静态代码分析并形成 Partner Skill Card 适配契约；不启动 GPU 训练。",
    }
    created: list[WorkItem] = []
    for instance in state.allowed_instances:
        if instance == "05":
            continue
        for wave in range(1, waves + 1):
            created.append(enqueue_work_item(workspace, campaign_id, {
                "instance_id": instance,
                "project_id": ROLES[instance],
                "kind": "project_iteration",
                "title": f"执行型项目推进 Wave {wave}",
                "instruction": (
                    f"Campaign 总目标：{state.goal}\n\n"
                    f"[execution_wave={wave}] 直接执行确定性事件 evidence_execution_slice。{roles[instance]}"
                    "必须保存实际 Python 源码、进程退出码、机器可读结果、分析 Markdown/PDF 并真实发送；"
                    "不得只写计划或把文件存在当成执行成功。"
                ),
                "priority": 72 - wave,
                "requires_artifact": True,
                "requires_delivery": True,
            }))
    if "05" in state.allowed_instances:
        created.append(enqueue_work_item(workspace, campaign_id, {
            "instance_id": "05", "project_id": ROLES["05"], "kind": "project_iteration",
            "title": "执行型自进化回放与决策",
            "instruction": (
                f"Campaign 总目标：{state.goal}\n\n[execution_wave=1] 直接执行确定性事件 "
                "evidence_execution_slice。等待 01–04 全部执行波次终态后，补齐离线 RL 轨迹，"
                "编写并运行候选动作评估器，对正式 Experiment 写出明确的 inconclusive/rejected/promoted 决策；"
                "样本不足不得 promoted。必须发送源码、结果和详细 PDF。"
            ),
            "priority": 40, "requires_artifact": True, "requires_delivery": True,
        }))
    return created


def seed_targetdiff_project_work(workspace: str, campaign_id: str, stages: int = 5) -> list[WorkItem]:
    """Seed the evidence-linked TargetDiff project arc, followed by one RL audit."""
    state = load_campaign(workspace, campaign_id)
    if not state:
        raise ValueError("campaign not found")
    if "02" not in state.allowed_instances:
        raise ValueError("molecular profile requires instance 02")
    stages = max(1, min(5, int(stages)))
    events = [
        ("targetdiff_data_contract", "数据字段合同"),
        ("targetdiff_group_baseline", "分组防泄漏基线"),
        ("targetdiff_nonlinear_compare", "非线性候选比较"),
        ("targetdiff_residual_analysis", "残差与失败组分析"),
        ("targetdiff_group_cv", "五折分组稳健性"),
    ]
    created: list[WorkItem] = []
    for stage, (event_type, label) in enumerate(events[:stages], 1):
        created.append(enqueue_work_item(workspace, campaign_id, {
            "instance_id": "02", "project_id": ROLES["02"], "kind": "project_iteration",
            "title": f"TargetDiff 单项目 Stage {stage}：{label}",
            "instruction": (
                f"Campaign 总目标：{state.goal}\n\n[targetdiff_stage={stage}] "
                f"直接执行确定性事件 {event_type}。只用 pk 作为监督目标，vina/rmsd 作为特征；"
                "按靶点组做确定性拆分，训练与测试组必须零重叠。必须实际运行源码、保存 JSON/Markdown/"
                "详细 PDF 并真实发送；每一阶段消费同一项目上一阶段的合同和结果，不得退回旧 QED/SA 排序。"
            ),
            # The scheduler sorts descending; this makes Stage 1 run first.
            "priority": 91 - stage,
            "requires_artifact": True, "requires_delivery": True,
        }))
    if "05" in state.allowed_instances:
        created.append(enqueue_work_item(workspace, campaign_id, {
            "instance_id": "05", "project_id": ROLES["05"], "kind": "project_iteration",
            "title": "TargetDiff 项目里程碑自进化审计",
            "instruction": (
                f"Campaign 总目标：{state.goal}\n\n直接执行确定性事件 offline_rl_self_evolution。"
                "等待 02 所有阶段终态后，把真实产物、验收、QQ 交付、失败与重试转成离线轨迹；"
                "只提出有证据且可证伪的下一项方法改进。样本或回归门不足必须保持 inconclusive，"
                "不得用自评替代晋升证据。生成并发送详细报告。"
            ),
            "priority": 40, "requires_artifact": True, "requires_delivery": True,
        }))
    return created


TARGETDIFF_CONTINUOUS_STAGES = {
    9: ("targetdiff_ligand_aggregation_cv", "配体聚合伪重复审计"),
    10: ("targetdiff_target_balanced_metrics", "靶点等权宏观评估"),
    11: ("targetdiff_failure_group_diagnostics", "失败靶点组诊断"),
    12: ("targetdiff_group_bootstrap", "靶点组 Bootstrap 稳健性"),
    13: ("targetdiff_method_decision", "预注册方法决策"),
}


def _targetdiff_continuous_instruction(state: CampaignState, stage: int) -> str:
    event_type, label = TARGETDIFF_CONTINUOUS_STAGES[stage]
    return (
        f"Campaign 总目标：{state.goal}\n\n[molecular_continuous=true] [targetdiff_stage={stage}] "
        f"直接执行确定性事件 {event_type}（{label}）。必须读取最新 Receipt 的 JSON 产物并在结果 lineage "
        "中记录 consumed=true；实际运行源码，保存机器 JSON、详细 Markdown/PDF 并真实发送。"
        "监督目标只能是 pk，vina/rmsd 只能作特征，训练测试靶点组 overlap 必须为零；"
        "不得用自由 batch_plan 替代声明实验，不得把数据集内相关性表述为药效因果。"
    )


def seed_targetdiff_continuous_work(workspace: str, campaign_id: str) -> list[WorkItem]:
    """Start the replenished TargetDiff experiment graph at Stage 9."""
    state = load_campaign(workspace, campaign_id)
    if not state or "02" not in state.allowed_instances:
        raise ValueError("molecular-continuous profile requires instance 02")
    _event_type, label = TARGETDIFF_CONTINUOUS_STAGES[9]
    return [enqueue_work_item(workspace, campaign_id, {
        "instance_id": "02", "project_id": ROLES["02"], "kind": "project_iteration",
        "title": f"TargetDiff Stage 9：{label}",
        "instruction": _targetdiff_continuous_instruction(state, 9),
        "priority": 90, "requires_artifact": True, "requires_delivery": True,
    })]


def _is_targetdiff_continuous(items: list[WorkItem]) -> bool:
    return any("molecular_continuous=true" in item.instruction for item in items)


PORTFOLIO_MARKER = "[portfolio_continuous=true]"
PORTFOLIO_BUSINESS_INSTANCES = ("01", "02", "03", "04")


def _is_portfolio_continuous(state: CampaignState | None) -> bool:
    return bool(state and PORTFOLIO_MARKER in state.goal)


def _portfolio_path(workspace: str, campaign_id: str) -> Path:
    return campaign_dir(workspace, campaign_id) / "portfolio_state.json"


def _load_portfolio_state(workspace: str, campaign_id: str) -> dict[str, Any]:
    try:
        value = json.loads(_portfolio_path(workspace, campaign_id).read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, ValueError, TypeError):
        pass
    # Carry input consumption and curriculum position across Campaign budget
    # boundaries. A new controller must not forget what the previous one
    # already ran and replay the same evidence as if it were novel.
    root = campaign_dir(workspace, campaign_id).parent
    candidates = sorted(
        (path for path in root.glob("*/portfolio_state.json")
         if path.parent.name != campaign_dir(workspace, campaign_id).name),
        key=lambda path: path.stat().st_mtime, reverse=True,
    ) if root.exists() else []
    for path in candidates:
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(previous, dict) or not isinstance(previous.get("lanes"), dict):
            continue
        inherited = json.loads(json.dumps(previous))
        inherited.update({"campaign_id": campaign_id, "inherited_from": previous.get("campaign_id") or path.parent.name})
        inherited.pop("updated_at", None)
        for lane in inherited["lanes"].values():
            if isinstance(lane, dict):
                lane.pop("work_item_id", None)
                lane["status"] = "inherited"
        return inherited
    return {"version": 1, "campaign_id": campaign_id, "lanes": {}, "updated_at": ""}


def _save_portfolio_state(workspace: str, campaign_id: str, value: dict[str, Any]) -> None:
    value["version"] = 1
    value["campaign_id"] = campaign_id
    value["updated_at"] = now_iso()
    atomic_json(_portfolio_path(workspace, campaign_id), value)


def _bounded_files_fingerprint(root: Path, paths: list[Path]) -> str:
    """Hash bounded, explicit evidence without walking unbounded data trees."""
    digest = hashlib.sha256()
    found = 0
    for path in sorted(set(paths), key=lambda value: str(value))[:64]:
        if not path.is_file():
            continue
        found += 1
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = path
        digest.update(str(relative).encode("utf-8", errors="replace"))
        try:
            stat = path.stat()
            digest.update(f":{stat.st_size}:".encode("ascii"))
            with path.open("rb") as handle:
                digest.update(handle.read(1024 * 1024))
        except OSError:
            digest.update(b":unreadable")
    return digest.hexdigest() if found else ""


def _portfolio_inputs(workspace: str) -> dict[str, dict[str, Any]]:
    root = workspace_root(workspace)
    external = root / "external"
    code_root = Path(__file__).resolve().parents[2]

    content_paths = [external / "content" / "inbox.jsonl"]
    content_fingerprint = _bounded_files_fingerprint(root, content_paths)

    targetdiff = external / "targetdiff"
    official_split_paths: list[Path] = []
    for directory in (targetdiff / "data", targetdiff / "datasets"):
        if directory.exists():
            for pattern in ("*split*.pt", "*split*.pkl", "*split*.json"):
                official_split_paths.extend(directory.glob(pattern))
    split_fingerprint = _bounded_files_fingerprint(root, official_split_paths)

    framework_paths = [
        code_root / "partner" / "governance" / "campaign.py",
        code_root / "partner" / "governance" / "rl_evolution.py",
        code_root / "partner" / "mind" / "executor.py",
        code_root / "scripts" / "partner_campaign.py",
        code_root / "tests" / "test_campaign.py",
    ]
    framework_fingerprint = _bounded_files_fingerprint(code_root, framework_paths)

    learning_paths: list[Path] = []
    for directory in (
        external / "code" / "RLVR-World-main",
        external / "code" / "SESA-Self-Evolving-Search-Agents-master",
        external / "code" / "deepseek-harness",
        external / "code" / "openai-codex",
        external / "ProRL-Agent-Server-stable",
        external / "literature",
    ):
        if not directory.exists():
            continue
        learning_paths.extend(directory.glob("README*"))
        learning_paths.extend(directory.glob("*.pdf"))
        learning_paths.extend(directory.glob("*.md"))
        learning_paths.append(directory / ".git" / "HEAD")
    learning_paths.extend([
        external / "code" / "deepseek-harness" / "docs" / "architecture.md",
        external / "code" / "deepseek-harness" / "docs" / "agent-lifecycle.md",
        external / "code" / "deepseek-harness" / "docs" / "tool-execution-pipeline.md",
        external / "code" / "deepseek-harness" / "docs" / "subsystems" / "session.md",
        external / "code" / "deepseek-harness" / "docs" / "subsystems" / "compaction.md",
        external / "code" / "openai-codex" / "codex-rs" / "rollout-trace" / "README.md",
        external / "code" / "openai-codex" / "codex-rs" / "thread-store" / "README.md",
        external / "code" / "openai-codex" / "codex-rs" / "execpolicy" / "README.md",
        external / "code" / "openai-codex" / "docs" / "sandbox.md",
    ])
    learning_fingerprint = _bounded_files_fingerprint(root, learning_paths)

    return {
        "01": {
            "ready": bool(content_fingerprint), "fingerprint": content_fingerprint,
            "reason": "content inbox has bounded evidence" if content_fingerprint else "waiting for external/content/inbox.jsonl",
        },
        "02": {
            "ready": bool(split_fingerprint), "fingerprint": split_fingerprint,
            "reason": "official split candidate detected" if split_fingerprint else "waiting for an official TargetDiff split candidate",
        },
        "03": {
            "ready": bool(framework_fingerprint), "fingerprint": framework_fingerprint,
            "reason": "framework source fingerprint available" if framework_fingerprint else "waiting for framework source",
        },
        "04": {
            "ready": bool(learning_fingerprint), "fingerprint": learning_fingerprint,
            "reason": "bounded external learning sources available" if learning_fingerprint else "waiting for declared external sources",
        },
    }


PORTFOLIO_WORK = {
    "01": (
        "小红书项目：新输入证据推进",
        "直接执行确定性事件 evidence_execution_slice。分析真实 content inbox，实际运行来源分析代码，"
        "产出候选 brief、源码、机器结果和详细报告并真实发送；不得上传或发布。",
        84,
    ),
    "02": (
        "分子项目：官方拆分接入审计",
        "直接执行确定性事件 targetdiff_provenance_audit。消费新出现的官方 split 候选，核验 provenance、"
        "组边界和可复现性；不得重跑已经结束的 Stage 13，不得把候选文件名当作来源已证实。",
        83,
    ),
    "03": (
        "Partner 框架：代码变化合同审计",
        "直接执行确定性事件 framework_campaign_contract_audit。针对当前代码指纹实际运行 Campaign/RL 合同测试，"
        "保存机器结果、Markdown/PDF 并真实发送；失败必须给出具体测试和下一修复入口。",
        82,
    ),
    "04": (
        "外部学习：新来源证据切片",
        "直接执行确定性事件 external_learning_index_slice。对声明的外部资料做真实文件、哈希和适配边界审计，"
        "产出可执行 Skill Card 候选；索引不等于已集成，必须清楚说明本轮新增证据。",
        81,
    ),
}

PORTFOLIO_EXPLORATION = {
    "01": [
        ("evidence_execution_slice", 2, "把内容 inbox 转成逐条可核查 brief，补充事实核验与禁止发布标记。"),
        ("evidence_execution_slice", 3, "对 open 内容去重并形成带来源键、风险和下一执行步的候选 backlog。"),
        ("continuous_project_step", 1, "[strategy_id=01_claim_evidence_matrix] 对候选逐条建立来源、原文证据、"
         "主张状态和发布授权矩阵；只形成可审核候选，不发布。"),
        ("continuous_project_step", 1, "[strategy_id=01_claim_risk_queue] 按来源完整度、主张风险和重复度"
         "建立真实核验优先队列；不得把排序写成发布授权。"),
        ("continuous_project_step", 1, "[strategy_id=01_editorial_backlog] 把已核验边界转成分层编辑 backlog，"
         "明确可写、待补证据和禁止发布三类。"),
    ],
    "02": [
        ("targetdiff_official_split_benchmark", 1, "在校验后的 split_by_name train/test identity 上比较线性与 HGB。"),
        ("targetdiff_official_split_bootstrap", 1, "对官方小测试集做靶点组 bootstrap，量化候选优势不确定性。"),
        ("targetdiff_official_split_error_slices", 1, "对官方测试集按 Vina/RMSD 分位数做误差切片，"
         "定位模型失效区而不是重复汇报整体 RMSE。"),
        ("continuous_project_step", 1, "[strategy_id=02_model_risk_register] 汇总官方 split、bootstrap、"
         "校准和误差切片证据，建立模型风险登记表。"),
        ("continuous_project_step", 1, "[strategy_id=02_next_experiment_gate] 根据风险登记表生成可证伪的"
         "下一实验及验收阈值，不把统计预测写成药效因果。"),
    ],
    "03": [
        ("evidence_execution_slice", 1, "回放真实 Campaign 历史，建立预算、交付和泛化动作基线。"),
        ("evidence_execution_slice", 2, "计算干净 Campaign 比率并定位新出现的运行质量回归。"),
        ("evidence_execution_slice", 3, "把历史故障转成可执行 runtime gate 候选，不自动合并。"),
        ("continuous_project_step", 1, "[strategy_id=03_runtime_recovery_canary] 隔离验证双槽、重启恢复、"
         "Scout 批次和 RL 波次门，不修改生产状态。"),
        ("continuous_project_step", 1, "[strategy_id=03_user_observability_canary] 运行用户三阶段回执、"
         "领域报告和文件投递归一化合同测试。"),
        ("continuous_project_step", 1, "[strategy_id=03_soak_density_analysis] 分析最近 Campaign 的业务推进"
         "密度、空槽时间和 Scout 占比，形成机器可验收的退化判断。"),
    ],
    "04": [
        ("evidence_execution_slice", 1, "真实 fetch SESA 并建立代码结构与 Skill 适配面基线。"),
        ("evidence_execution_slice", 2, "形成外部 Skill Bank 到 Partner Issue/Experiment 的字段契约。"),
        ("evidence_execution_slice", 3, "用真实 Issue 构建只读 adapter prototype，不执行外部训练栈。"),
        ("continuous_project_step", 1, "[strategy_id=04_adapter_contract] 把 Harness 概念映射落实为"
         "独立适配合同并运行本地验证；保持 copied_source=false，未经晋升不得接管 Partner 根基。"),
        ("continuous_project_step", 1, "[strategy_id=04_reference_gap_matrix] 对照固定 revision 与 Partner"
         "本地合同，输出已采用、缺口和明确不采用矩阵。"),
        ("continuous_project_step", 1, "[strategy_id=04_adoption_backlog] 将证据充分的缺口转成有测试、"
         "回滚和来源边界的采用实验 backlog。"),
    ],
}

PORTFOLIO_SCOUTS = {
    "01": ("evidence_execution_slice", 3, "复查 content inbox 是否出现新来源、状态变化或重复风险；无变化也必须如实记录。"),
    "02": ("targetdiff_provenance_audit", 1, "复查作者来源可用性、镜像校验和与 split/affinity 结构合同是否变化。"),
    "03": ("framework_campaign_contract_audit", 1, "重跑当前代码合同测试，捕捉长期 Controller 与恢复路径回归。"),
    "04": ("external_learning_index_slice", 1, "重新核验声明外部来源的哈希和 present/indexed/integrated 边界。"),
}


def _portfolio_item_lane(item: WorkItem) -> str:
    match = re.search(r"\[portfolio_lane=(0[1-5])\]", item.instruction)
    return match.group(1) if match else ""


def _portfolio_business_outcome_fingerprint(items: list[WorkItem]) -> str:
    # A rotating scout is monitoring, not a new business wave.  Including it
    # here used to wake 05 after every no-change audit and produced repetitive
    # RL reports while the real project lanes were idle.
    completed = [
        item for item in items
        if _portfolio_item_lane(item) in PORTFOLIO_BUSINESS_INSTANCES
        and item.kind == "project_iteration"
        and "[portfolio_scout=true]" not in item.instruction
        and item.status in TERMINAL_WORK
    ]
    if not completed:
        return ""
    digest = hashlib.sha256()
    for item in sorted(completed, key=lambda value: value.work_item_id):
        digest.update(json.dumps({
            "work_item_id": item.work_item_id,
            "status": item.status,
            "event_types": item.event_types,
            "artifacts": item.artifacts,
            "updated_at": item.updated_at,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def materialize_portfolio_work(workspace: str, campaign_id: str) -> list[WorkItem]:
    """Admit changed project evidence and one RL audit after each settled wave."""
    state = load_campaign(workspace, campaign_id)
    if not _is_portfolio_continuous(state):
        return []
    portfolio = _load_portfolio_state(workspace, campaign_id)
    lanes = portfolio.setdefault("lanes", {})
    items = list_work_items(workspace, campaign_id)
    inputs = _portfolio_inputs(workspace)
    created: list[WorkItem] = []

    for instance in PORTFOLIO_BUSINESS_INSTANCES:
        lane = lanes.setdefault(instance, {})
        evidence = inputs[instance]
        previous_observed = str(lane.get("observed_fingerprint") or "")
        if evidence["fingerprint"] and evidence["fingerprint"] == previous_observed:
            stable_observations = int(lane.get("stable_observations") or 0) + 1
        else:
            stable_observations = 1 if evidence["fingerprint"] else 0
        lane.update({
            "project_id": ROLES[instance],
            "observed_fingerprint": evidence["fingerprint"],
            "stable_observations": stable_observations,
            "reason": evidence["reason"],
        })
        if instance not in state.allowed_instances:
            lane["status"] = "outside_campaign_scope"
            continue
        active = next((item for item in items if _portfolio_item_lane(item) == instance
                       and item.status not in TERMINAL_WORK), None)
        if active:
            lane.update({"status": active.status, "work_item_id": active.work_item_id})
            continue
        if not evidence["ready"]:
            lane["status"] = "waiting_input"
            continue
        if stable_observations < 2:
            lane.update({
                "status": "observing_stability",
                "reason": "new input must keep the same fingerprint across two controller ticks",
            })
            continue
        if lane.get("last_dispatched_fingerprint") == evidence["fingerprint"]:
            lane["status"] = "waiting_change"
            continue
        title, body, priority = PORTFOLIO_WORK[instance]
        try:
            item = enqueue_work_item(workspace, campaign_id, {
                "instance_id": instance, "project_id": ROLES[instance], "kind": "project_iteration",
                "title": title,
                "instruction": (
                    f"{PORTFOLIO_MARKER} [portfolio_lane={instance}] "
                    f"[source_fingerprint={evidence['fingerprint']}]\n{body}\n"
                    "必须读取最新 ProjectState/Receipt 并明确写出承接关系；只在输入指纹变化时开启新一轮。"
                ),
                "priority": priority, "requires_artifact": True, "requires_delivery": True,
            })
        except ValueError as exc:
            if "budget exhausted" in str(exc):
                lane.update({"status": "budget_exhausted", "reason": str(exc)})
                break
            raise
        created.append(item)
        items.append(item)
        lane.update({
            "status": "queued", "work_item_id": item.work_item_id,
            "last_dispatched_fingerprint": evidence["fingerprint"],
            "last_dispatched_at": now_iso(),
        })

    proposed_continuations = any(
        request_next_action(workspace, {"project_id": ROLES[instance]}).get("ok")
        for instance in PORTFOLIO_BUSINESS_INSTANCES if instance in state.allowed_instances
    )
    rl_lane = lanes.setdefault("05", {"project_id": ROLES["05"]})
    nonterminal_business = [item for item in items if _portfolio_item_lane(item) in PORTFOLIO_BUSINESS_INSTANCES
                            and item.status not in TERMINAL_WORK]
    outcome_fingerprint = _portfolio_business_outcome_fingerprint(items)
    rl_lane["observed_fingerprint"] = outcome_fingerprint
    if "05" not in state.allowed_instances:
        rl_lane.update({"status": "outside_campaign_scope", "reason": "instance 05 not allowed"})
    elif nonterminal_business:
        rl_lane.update({"status": "waiting_wave", "reason": "waiting for all admitted business work to settle"})
    elif proposed_continuations:
        rl_lane.update({"status": "waiting_wave", "reason": "waiting for declared project continuations to settle"})
    elif not outcome_fingerprint:
        rl_lane.update({"status": "waiting_evidence", "reason": "waiting for a completed business outcome"})
    elif rl_lane.get("last_dispatched_fingerprint") == outcome_fingerprint:
        rl_lane.update({"status": "waiting_change", "reason": "current business outcomes already learned"})
    elif not any(_portfolio_item_lane(item) == "05" and item.status not in TERMINAL_WORK for item in items):
        try:
            item = enqueue_work_item(workspace, campaign_id, {
                "instance_id": "05", "project_id": ROLES["05"], "kind": "project_iteration",
                "title": "五项目组合：本轮结果离线 RL 审计",
                "instruction": (
                    f"{PORTFOLIO_MARKER} [portfolio_lane=05] [source_fingerprint={outcome_fingerprint}] "
                    "直接执行确定性事件 offline_rl_self_evolution。只摄取本轮新终态 WorkItem 的真实产物、"
                    "验收、QQ 回执、失败与重试，奖励新证据和跨轮承接；形成 candidate Experiment。"
                    "样本或回归门不足不得 promoted，自进化不得替代 01–04 项目推进。"
                ),
                "priority": 79, "requires_artifact": True, "requires_delivery": True,
            })
            created.append(item)
            rl_lane.update({
                "status": "queued", "work_item_id": item.work_item_id,
                "last_dispatched_fingerprint": outcome_fingerprint, "last_dispatched_at": now_iso(),
                "reason": "new settled business wave admitted for offline learning",
            })
        except ValueError as exc:
            if "budget exhausted" in str(exc):
                rl_lane.update({"status": "budget_exhausted", "reason": str(exc)})
            else:
                raise

    # Once 05 has consumed the settled business wave, advance a finite,
    # declared exploration curriculum. These tasks differ by executable
    # objective; exhausting the curriculum is safer than replaying one report.
    outcomes_learned = bool(outcome_fingerprint and
                            rl_lane.get("last_dispatched_fingerprint") == outcome_fingerprint)
    inherited_fresh_start = bool(portfolio.get("inherited_from") and
                                 not any(_portfolio_item_lane(item) for item in items))
    if ((outcomes_learned or inherited_fresh_start) and not proposed_continuations
            and not created and not nonterminal_business):
        for instance in PORTFOLIO_BUSINESS_INSTANCES:
            if instance not in state.allowed_instances:
                continue
            curriculum = PORTFOLIO_EXPLORATION[instance]
            lane = lanes[instance]
            round_index = int(lane.get("exploration_round") or 0)
            if round_index >= len(curriculum):
                lane["exploration_status"] = "curriculum_complete"
                continue
            if instance == "02" and (not inputs["02"]["ready"] or
                                     lane.get("last_dispatched_fingerprint") != inputs["02"]["fingerprint"]):
                lane["exploration_status"] = "waiting_verified_stable_split"
                continue
            event_type, wave, objective = curriculum[round_index]
            try:
                item = enqueue_work_item(workspace, campaign_id, {
                    "instance_id": instance, "project_id": ROLES[instance], "kind": "project_iteration",
                    "title": f"{instance} 主动探索 Round {round_index + 1}",
                    "instruction": (
                        f"{PORTFOLIO_MARKER} [portfolio_lane={instance}] "
                        f"[portfolio_exploration_round={round_index + 1}] [execution_wave={wave}] "
                        f"直接执行确定性事件 {event_type}。{objective}"
                        "必须承接最新 Receipt/本轮业务结果，产出新的机器证据、详细 PDF 和 QQ 回执；"
                        "不得只改标题或原样重跑。"
                    ),
                    "priority": 74 - round_index, "requires_artifact": True, "requires_delivery": True,
                })
            except ValueError as exc:
                if "budget exhausted" in str(exc):
                    lane.update({"exploration_status": "budget_exhausted", "reason": str(exc)})
                    break
                raise
            created.append(item)
            lane.update({"exploration_round": round_index + 1, "exploration_status": "queued",
                         "work_item_id": item.work_item_id})
            rl_lane.update({"status": "waiting_wave", "reason": "waiting for proactive exploration wave to settle"})

    # After every declared curriculum is exhausted, keep the controller alive
    # with a rotating, low-frequency evidence scout batch. This is monitoring and
    # source acquisition, not fake project progress; unchanged evidence gets
    # a repeated signature and therefore no novelty reward.
    curriculum_complete = all(
        int(lanes.get(instance, {}).get("exploration_round") or 0) >= len(PORTFOLIO_EXPLORATION[instance])
        for instance in PORTFOLIO_BUSINESS_INSTANCES if instance in state.allowed_instances
    )
    scout_ready = outcomes_learned or inherited_fresh_start
    if (scout_ready and curriculum_complete and not proposed_continuations
            and not created and not nonterminal_business):
        next_scout_at = str(portfolio.get("next_scout_at") or "")
        scout_due = not next_scout_at or _now() >= _parse_time(next_scout_at)
        if scout_due:
            recent = sorted(
                (item for item in items if item.kind != "report" and item.status in TERMINAL_WORK),
                key=lambda item: item.updated_at,
            )[-12:]
            recent_business = sum(
                "business_progress=true" in " ".join(item.evidence or []) for item in recent
            )
            recent_scouts = sum("[portfolio_scout=true]" in item.instruction for item in recent)
            density = recent_business / max(1, len(recent))
            # A live runner must not spend the rest of its budget manufacturing
            # identical no-change PDFs.  New input fingerprints are still
            # checked every tick above; only repetitive Scout dispatch pauses.
            if len(recent) >= 8 and recent_scouts >= 6 and density < 0.25:
                portfolio["progress_density"] = {
                    "window": len(recent), "business_items": recent_business,
                    "scout_items": recent_scouts, "density": round(density, 4),
                    "status": "degraded_waiting_new_hypothesis",
                }
                portfolio["next_scout_at"] = (_now() + timedelta(minutes=60)).isoformat(timespec="seconds")
                for instance in PORTFOLIO_BUSINESS_INSTANCES:
                    if instance in state.allowed_instances:
                        lanes[instance]["scout_status"] = "suppressed_low_business_density"
                rl_lane.update({"status": "waiting_change",
                                "reason": "Scout suppressed: recent business progress density below 0.25"})
                _save_portfolio_state(workspace, campaign_id, portfolio)
                return created
            eligible = [value for value in PORTFOLIO_BUSINESS_INSTANCES if value in state.allowed_instances]
            cursor = int(portfolio.get("scout_cursor") or 0) % len(eligible)
            admitted = 0
            checked = 0
            # Admit up to max_active distinct lanes so a quiet long run does
            # not leave the second slot unused. Ineligible lanes are skipped,
            # and no-change scouts remain excluded from the RL wave digest.
            while admitted < state.max_active and checked < len(eligible):
                instance = eligible[(cursor + checked) % len(eligible)]
                checked += 1
                if instance == "02" and not inputs["02"]["ready"]:
                    continue
                event_type, wave, objective = PORTFOLIO_SCOUTS[instance]
                try:
                    item = enqueue_work_item(workspace, campaign_id, {
                        "instance_id": instance, "project_id": ROLES[instance], "kind": "audit",
                        "title": f"{instance} 长期证据 Scout {int(portfolio.get('scout_count') or 0) + 1}",
                        "instruction": (
                            f"{PORTFOLIO_MARKER} [portfolio_lane={instance}] [portfolio_scout=true] "
                            f"[execution_wave={wave}] 直接执行确定性事件 {event_type}。{objective}"
                            "必须报告实际变化或明确 no-change；no-change 只算监测证据，不算项目创新。"
                        ),
                        "priority": 55, "requires_artifact": True, "requires_delivery": True,
                    })
                    created.append(item)
                    admitted += 1
                    portfolio["scout_count"] = int(portfolio.get("scout_count") or 0) + 1
                    lanes[instance].update({"scout_status": "queued", "work_item_id": item.work_item_id})
                except ValueError as exc:
                    if "budget exhausted" in str(exc):
                        portfolio["scout_status"] = "budget_exhausted"
                        break
                    else:
                        raise
            if admitted:
                portfolio["scout_cursor"] = cursor + checked
                portfolio["next_scout_at"] = (_now() + timedelta(minutes=15)).isoformat(timespec="seconds")
                rl_lane.update({"status": "waiting_wave", "reason": "waiting for rotating evidence scout batch"})
    _save_portfolio_state(workspace, campaign_id, portfolio)
    return created


def seed_portfolio_work(workspace: str, campaign_id: str) -> list[WorkItem]:
    state = load_campaign(workspace, campaign_id)
    if not _is_portfolio_continuous(state):
        raise ValueError(f"portfolio profile goal must contain {PORTFOLIO_MARKER}")
    return materialize_portfolio_work(workspace, campaign_id)


def materialize_targetdiff_continuous_work(workspace: str, campaign_id: str) -> list[WorkItem]:
    """Replenish one declared experiment at a time and insert RL only at milestones."""
    state = load_campaign(workspace, campaign_id)
    if not state:
        return []
    items = list_work_items(workspace, campaign_id)
    if not _is_targetdiff_continuous(items):
        return []
    if any(item.status not in TERMINAL_WORK and item.kind != "report" for item in items):
        return []
    event_to_stage = {event: stage for stage, (event, _label) in TARGETDIFF_CONTINUOUS_STAGES.items()}
    completed_stages = [event_to_stage[event] for item in items if item.status == "completed"
                        for event in item.event_types if event in event_to_stage]
    current = max(completed_stages, default=0)
    if current == 0:
        return []
    if current in {10, 13} and "05" in state.allowed_instances:
        marker = f"rl_after_targetdiff_stage={current}"
        checkpoint = next((item for item in items if marker in item.instruction), None)
        if checkpoint is None:
            return [enqueue_work_item(workspace, campaign_id, {
                "instance_id": "05", "project_id": ROLES["05"], "kind": "project_iteration",
                "title": f"TargetDiff Stage {current} 里程碑 RL 审计",
                "instruction": (
                    f"[molecular_continuous=true] [{marker}] 直接执行确定性事件 offline_rl_self_evolution。"
                    f"只摄取截至 Stage {current} 的真实完成、失败、重试、产物 lineage 和 QQ 回执；"
                    "计算新证据与结果承接奖励，形成 candidate Experiment。样本或回归门不足不得 promoted。"
                ),
                "priority": 85, "requires_artifact": True, "requires_delivery": True,
            })]
        if checkpoint.status != "completed":
            return []
    if current >= 13:
        return []
    next_stage = current + 1
    if any(f"targetdiff_stage={next_stage}" in item.instruction for item in items):
        return []
    _event_type, label = TARGETDIFF_CONTINUOUS_STAGES[next_stage]
    return [enqueue_work_item(workspace, campaign_id, {
        "instance_id": "02", "project_id": ROLES["02"], "kind": "project_iteration",
        "title": f"TargetDiff Stage {next_stage}：{label}",
        "instruction": _targetdiff_continuous_instruction(state, next_stage),
        "priority": 90, "requires_artifact": True, "requires_delivery": True,
    })]


def _read_latest_issue_rows(workspace: str) -> list[dict[str, Any]]:
    path = governance_log(workspace, "issues")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-1000:]
    except OSError:
        return []
    latest: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("issue_id"):
            latest[str(row["issue_id"])] = row
    return list(latest.values())


def materialize_evolution_work(workspace: str, campaign_id: str) -> list[WorkItem]:
    state = load_campaign(workspace, campaign_id)
    if not state or "05" not in state.allowed_instances:
        return []
    if _is_portfolio_continuous(state):
        # Portfolio mode owns the 05 gate and only learns after a newly
        # settled business wave. Historical issues cannot pre-empt it.
        return []
    campaign_items = list_work_items(workspace, campaign_id)
    if _is_targetdiff_continuous(campaign_items):
        # This profile owns its two declared RL checkpoints. Historical Issues
        # must not pre-empt the business experiment chain.
        return []
    # The seeded 05 policy audit owns the first evolution slot.  Do not let
    # historical Issues pre-empt it or let repeated controller ticks create a
    # succession of experiments before the previous one has an outcome.
    if any(item.instance_id == "05" and item.kind == "project_iteration"
           for item in campaign_items):
        return []
    if any(item.kind == "evolution_experiment" for item in campaign_items):
        return []
    existing = {item.source_issue_id for item in campaign_items if item.source_issue_id}
    candidates = [row for row in _read_latest_issue_rows(workspace)
                  if row.get("status") in {"open", "investigating", "candidate"}
                  and row.get("issue_id") not in existing
                  and not str(row.get("summary") or "").startswith("Campaign WorkItem 验收失败: 验证 Issue")
                  and row.get("source_work_kind") != "evolution_experiment"
                  and (row.get("severity") in {"high", "critical"} or int(row.get("occurrences", 1)) >= 2)]
    created: list[WorkItem] = []
    # One root-cause experiment at a time.  Failed experiments must enrich
    # their source Issue rather than recursively creating more experiments.
    for issue in candidates[:1]:
        try:
            created.append(enqueue_work_item(workspace, campaign_id, {
            "instance_id": "05",
            "project_id": str(issue.get("project_id") or ROLES["05"]),
            "kind": "evolution_experiment",
            "title": f"验证 Issue {issue['issue_id']}",
            "instruction": (
                f"执行确定性事件 offline_rl_self_evolution。基于 Issue {issue['issue_id']} "
                f"的真实证据进行诊断：{issue.get('summary','')}。"
                "建立带 baseline、可证伪假设、成功标准和回滚策略的 candidate Experiment；"
                "执行聚焦测试。只有全部标准和回归通过才可 promoted，否则 rejected/inconclusive。"
                "完成后回到 Issue 所属项目，不得让自进化替代项目。产出实验报告。"
            ),
            "priority": 80 if issue.get("severity") == "critical" else 65,
            "source_issue_id": issue["issue_id"],
            "requires_artifact": True,
            "requires_delivery": True,
            }))
        except ValueError as exc:
            if "budget exhausted" in str(exc):
                break
            raise
    return created


def _effective_action_is_open(workspace: str, project_id: str, action_id: str) -> bool:
    result = request_next_action(workspace, {"project_id": project_id})
    return bool(result.get("ok") and result.get("action", {}).get("action_id") == action_id)


def materialize_project_actions(workspace: str, campaign_id: str) -> list[WorkItem]:
    state = load_campaign(workspace, campaign_id)
    if not state:
        return []
    items = list_work_items(workspace, campaign_id)
    portfolio_mode = _is_portfolio_continuous(state)
    if portfolio_mode and any(
        item.status not in TERMINAL_WORK and _portfolio_item_lane(item) == "05" for item in items
    ):
        return []
    if portfolio_mode and any(
        item.status not in TERMINAL_WORK and _portfolio_item_lane(item) in PORTFOLIO_BUSINESS_INSTANCES
        for item in items
    ):
        return []
    if _is_targetdiff_continuous(items):
        return []
    source_ids = {item.source_action_id for item in items if item.source_action_id}
    created: list[WorkItem] = []
    for instance in state.allowed_instances:
        project_id = ROLES[instance]
        prepared = request_next_action(workspace, {"project_id": project_id})
        action = prepared.get("action") if prepared.get("ok") else None
        if not action or action.get("action_id") in source_ids:
            continue
        instruction = str(action.get("params", {}).get("user_request") or (
            f"执行事件 {action.get('event_type')}，承接最新 Receipt 的全部真实产物。"
        ))
        if portfolio_mode:
            instruction = f"{PORTFOLIO_MARKER} [portfolio_lane={instance}] {instruction}"
        try:
            created.append(enqueue_work_item(workspace, campaign_id, {
            "instance_id": instance,
            "project_id": project_id,
            "kind": "project_iteration",
            "title": str(action.get("title") or "项目下一轮"),
            "instruction": instruction,
            "source_action_id": str(action.get("action_id") or ""),
            "priority": 75,
            "requires_artifact": True,
            "requires_delivery": True,
            }))
        except ValueError as exc:
            if "budget exhausted" in str(exc):
                break
            raise
    return created


def _marker(item: WorkItem) -> str:
    return f"[PARTNER_CAMPAIGN campaign_id={item.campaign_id} work_item_id={item.work_item_id}]"


def campaign_instruction(item: WorkItem) -> str:
    recoveries = sum(str(value).startswith("transport_recovery=") for value in item.evidence)
    progress_marker = (
        " [user_progress_v2=true]"
        if item.kind != "report" and any(event in item.instruction for event in BOUNDED_CAMPAIGN_EVENTS)
        else ""
    )
    return (
        f"{_marker(item)} [campaign_attempt={max(1, item.attempt)}] "
        f"[transport_recovery={recoveries}]{progress_marker}\n"
        "这是长期 Campaign 中的一个有边界 WorkItem，只执行这一轮；不要自行启动旧 Research Loop。\n"
        f"项目：{item.project_id}\n任务：{item.instruction}\n\n"
        "强制要求：先读取最新 ProjectState/IterationReceipt 和相关上下文；承接上一轮产物；"
        "实际执行并验证；产出文件；需要 user_progress_v2 时通过真实 QQ callback 依次发送 "
        "instruction_received、started、executed、verified、finished 五阶段消息并记录回执，"
        "必须复述收到的业务指令并逐步说明实际动作，不能只在最后发送 PDF。"
        "如果需要文件交付则调用 push_files 并检查 delivered。遇到登录、发布、付费、凭证、"
        "不可恢复操作或缺失数据时明确 blocked，不得猜测执行。不要在本任务内部创建无限循环。"
    )


def _lease_expiry(seconds: int, deadline_at: str = "", grace_seconds: int = 120) -> str:
    """Bound a WorkItem lease by the Campaign deadline plus a short drain grace."""
    expiry = _now() + timedelta(seconds=seconds)
    if deadline_at:
        expiry = min(expiry, _parse_time(deadline_at) + timedelta(seconds=max(0, grace_seconds)))
    return expiry.isoformat(timespec="seconds")


def _release_lease(workspace: str, campaign_id: str, lease_id: str, status: str = "released") -> None:
    lease = load_lease(workspace, campaign_id, lease_id)
    if not lease:
        return
    lease.status = status
    lease.released_at = now_iso()
    lease.heartbeat_at = now_iso()
    save_lease(workspace, lease)


def _expire_stale(workspace: str, state: CampaignState, now: datetime) -> None:
    for lease in list_leases(workspace, state.campaign_id):
        if lease.status != "active" or _parse_time(lease.expires_at) > now:
            continue
        lease.status = "expired"
        lease.released_at = now_iso()
        save_lease(workspace, lease)
        item = load_work_item(workspace, state.campaign_id, lease.work_item_id)
        if not item or item.status not in BUSY_WORK:
            continue
        if item.attempt < item.max_attempts:
            item.status = "proposed"
            item.lease_id = ""
            item.task_id = ""
            item.updated_at = now_iso()
            state.usage.retries += 1
            _event(workspace, state.campaign_id, "work_item_retry", work_item_id=item.work_item_id,
                   reason="lease expired")
        else:
            item.status = "blocked"
            item.blocked_reason = "watchdog: lease expired and retry budget exhausted"
            state.usage.failures += 1
            _event(workspace, state.campaign_id, "work_item_blocked", work_item_id=item.work_item_id,
                   reason=item.blocked_reason)
        save_work_item(workspace, item)


def _budget_stop_reason(state: CampaignState, now: datetime) -> str:
    if now >= _parse_time(state.deadline_at):
        return "campaign deadline reached"
    creation_boundary = state.budget.max_work_items - 1 if state.budget.max_work_items > 1 else 1
    if state.usage.work_items_created >= creation_boundary:
        return "work-item budget exhausted"
    if state.usage.failures >= state.budget.max_failures:
        return "failure budget exhausted"
    if state.usage.model_calls >= state.budget.max_model_calls:
        return "model-call budget exhausted"
    if state.usage.cost_units >= state.budget.max_cost_units:
        return "cost budget exhausted"
    return ""


def build_campaign_report(
    workspace: str, campaign_id: str, report_type: str = "checkpoint", stop_reason: str = "",
) -> tuple[CampaignReport, Path]:
    state = load_campaign(workspace, campaign_id)
    if not state:
        raise ValueError("campaign not found")
    items = list_work_items(workspace, campaign_id)
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    primary = [item for item in items if item.kind != "report"]
    reports = [item for item in items if item.kind == "report"]
    primary_counts: dict[str, int] = {}
    for item in primary:
        primary_counts[item.status] = primary_counts.get(item.status, 0) + 1
    primary_closed = sum(primary_counts.get(value, 0) for value in ("completed", "blocked", "cancelled"))
    report_delivered = sum(item.status == "completed" for item in reports)
    report_issues = sum(item.status in {"failed", "blocked", "cancelled"} for item in reports)
    blocked = [f"{item.instance_id}:{item.title} — {item.blocked_reason}" for item in primary if item.status == "blocked"]
    pending = [f"{item.instance_id}:{item.title}" for item in items if item.status in {"proposed", "leased", "queued", "running"}]
    final_context = ""
    display_status = state.status
    if report_type == "final":
        display_status = (
            "finalizing（本报告送达回执成功后自动 completed；当前统计不含本报告自身）"
            if state.status != "completed" else "completed"
        )
        final_reason = stop_reason or state.stop_reason or _budget_stop_reason(state, _now())
        final_context = f"最终收尾原因={final_reason or 'controller stop boundary'}；"
    summary = (
        f"Campaign {campaign_id} 状态={display_status}；{final_context}"
        f"业务轮次已收口 {primary_closed}/{len(primary)}"
        f"（成功 {primary_counts.get('completed', 0)}，受控阻塞 {primary_counts.get('blocked', 0)}，"
        f"失败 {primary_counts.get('failed', 0)}）；报告送达 {report_delivered}，报告链问题 {report_issues}；"
        f"当前实例={','.join(state.active_instances) or '无'}。"
    )
    report = CampaignReport(
        campaign_id=campaign_id,
        report_type=report_type,
        status=state.status,
        summary=summary,
        metrics={"work_items": counts, "primary_work_items": primary_counts,
                 "report_delivery": {"delivered": report_delivered, "issues": report_issues},
                 "usage": state.usage.to_dict(), "budget": state.budget.to_dict()},
        evidence=[str(campaign_dir(workspace, campaign_id) / "events.jsonl")],
        blocked_items=blocked,
        next_actions=pending,
    )
    json_path = save_report(workspace, report)
    md_path = json_path.with_suffix(".md")
    lines = [f"# Partner Campaign {'最终报告' if report_type == 'final' else '阶段报告'}", "", summary, "",
             "## 预算与使用", "", "```json", json.dumps(report.metrics, ensure_ascii=False, indent=2), "```", "",
             "## 阻塞项", ""]
    lines.extend(f"- {value}" for value in blocked or ["无"])
    lines.extend(["", "## 待执行", ""])
    lines.extend(f"- {value}" for value in pending or ["无"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report, md_path


def _schedule_report_if_due(workspace: str, state: CampaignState, now: datetime) -> None:
    if state.last_report_at:
        due = _parse_time(state.last_report_at) + timedelta(seconds=state.report_interval_seconds)
        if now < due:
            return
    items = list_work_items(workspace, state.campaign_id)
    creation_boundary = state.budget.max_work_items - 1 if state.budget.max_work_items > 1 else 1
    if state.usage.work_items_created >= creation_boundary:
        return
    if any(item.kind == "report" and item.title == "Campaign 最终日报" for item in items):
        return
    if any(item.kind == "report" and item.status not in TERMINAL_WORK for item in items):
        return
    report, path = build_campaign_report(workspace, state.campaign_id)
    target = next((value for value in state.active_instances if value in state.allowed_instances), state.allowed_instances[0])
    enqueue_work_item(workspace, state.campaign_id, {
        "instance_id": target,
        "project_id": ROLES[target],
        "kind": "report",
        "title": "Campaign 定时进度摘要",
        "instruction": f"读取并通过 send_user_text 真实发送阶段摘要文件 {path} 的核心内容；消息必须包含 campaign_id。",
        "priority": 90,
        "requires_artifact": False,
        "requires_delivery": True,
    })
    state = load_campaign(workspace, state.campaign_id) or state
    state.last_report_at = now.isoformat(timespec="seconds")
    save_campaign(workspace, state)


def _ensure_final_report_work(workspace: str, state: CampaignState, stop_reason: str) -> WorkItem:
    items = list_work_items(workspace, state.campaign_id)
    existing = next((item for item in items if item.kind == "report" and item.title == "Campaign 最终日报"), None)
    if existing:
        return existing
    _report, path = build_campaign_report(workspace, state.campaign_id, "final", stop_reason)
    target = next((value for value in state.active_instances if value in state.allowed_instances), state.allowed_instances[0])
    return enqueue_work_item(workspace, state.campaign_id, {
        "instance_id": target,
        "project_id": ROLES[target],
        "kind": "report",
        "title": "Campaign 最终日报",
        "instruction": (
            f"Campaign 已到停止边界：{stop_reason}。读取最终报告 {path}，通过 send_user_text "
            "向用户真实发送简明总结；必须说明完成/失败/阻塞/预算和恢复条件。"
        ),
        "priority": 100,
        "requires_artifact": False,
        "requires_delivery": True,
    })


def _unfinished_primary_work(items: list[WorkItem]) -> list[WorkItem]:
    """Return created non-report work which still needs a terminal outcome."""
    return [item for item in items if item.kind != "report" and item.status not in TERMINAL_WORK]


def _effective_stop_reason(state: CampaignState, now: datetime, items: list[WorkItem]) -> str:
    """A creation cap must not pre-empt work already admitted by that cap."""
    for prefix in ("finalizing: ", "draining before final report: ", "stop boundary: "):
        if str(state.stop_reason or "").startswith(prefix):
            return str(state.stop_reason)[len(prefix):]
    reason = _budget_stop_reason(state, now)
    if reason == "work-item budget exhausted" and _unfinished_primary_work(items):
        return ""
    return reason


def _cancel_unstarted_at_stop(workspace: str, state: CampaignState, reason: str) -> None:
    """Close work that must never start after a hard safety boundary."""
    if reason == "work-item budget exhausted":
        return
    for item in list_work_items(workspace, state.campaign_id):
        if item.kind == "report" or item.status not in {"proposed", "failed"}:
            continue
        item.status = "cancelled"
        item.blocked_reason = f"not started: {reason}"
        item.updated_at = now_iso()
        save_work_item(workspace, item)
        _event(workspace, state.campaign_id, "work_item_cancelled_at_stop",
               work_item_id=item.work_item_id, reason=reason)


def tick_campaign(
    workspace: str,
    campaign_id: str,
    *,
    dispatch: Callable[[WorkItem, str], str],
    switch_slots: Callable[[list[str]], None] | None = None,
    runtime_ready: Callable[[str], bool] | None = None,
    lease_seconds: int = 1800,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Advance a campaign once. Safe to call repeatedly after restarts."""
    now = now or _now()
    # Materialization functions use the same lock internally when creating
    # work, so run them before the state-transition critical section.
    reconcile_campaign_tasks(workspace, campaign_id)
    pre_state = load_campaign(workspace, campaign_id)
    pre_items = list_work_items(workspace, campaign_id) if pre_state else []
    pre_stop_reason = _effective_stop_reason(pre_state, now, pre_items) if pre_state else ""
    creation_stop_reason = _budget_stop_reason(pre_state, now) if pre_state else ""
    if pre_state and not creation_stop_reason:
        # Materialize a Receipt-owned continuation before Portfolio considers
        # an RL checkpoint.  The previous order allowed 05 to run after every
        # small step instead of once after the whole business chain settled.
        materialize_project_actions(workspace, campaign_id)
        materialize_portfolio_work(workspace, campaign_id)
        materialize_targetdiff_continuous_work(workspace, campaign_id)
        materialize_evolution_work(workspace, campaign_id)
        pre_state = load_campaign(workspace, campaign_id)
        pre_items = list_work_items(workspace, campaign_id) if pre_state else []
        pre_stop_reason = _effective_stop_reason(pre_state, now, pre_items) if pre_state else ""
    if pre_state and pre_stop_reason:
        _sync_offline_rl_at_stop(workspace, campaign_id)
        _ensure_final_report_work(workspace, pre_state, pre_stop_reason)
    with campaign_lock(workspace, campaign_id):
        state = load_campaign(workspace, campaign_id)
        if not state:
            return {"ok": False, "status": "missing_campaign"}
        if state.status in {"completed", "cancelled"}:
            # A superseded controller can observe its terminal state one last
            # time after a new Campaign has already taken ownership. It must
            # not restore stale slots over the new Campaign's selection.
            if (switch_slots and state.restore_instances
                    and active_campaign_id(workspace) == campaign_id):
                switch_slots(state.restore_instances)
            return {"ok": True, "status": state.status, "dispatched": []}
        if state.status == "paused":
            return {"ok": True, "status": state.status, "dispatched": []}
        _expire_stale(workspace, state, now)
        all_items = list_work_items(workspace, campaign_id)
        stop_reason = _effective_stop_reason(state, now, all_items)
        if stop_reason:
            _cancel_unstarted_at_stop(workspace, state, stop_reason)
            all_items = list_work_items(workspace, campaign_id)
            nonfinal_busy = [
                item for item in all_items
                if item.status in BUSY_WORK and not (item.kind == "report" and item.title == "Campaign 最终日报")
            ]
            if nonfinal_busy:
                state.status = "running"
                state.stop_reason = f"draining before final report: {stop_reason}"
                state.active_instances = list(dict.fromkeys(item.instance_id for item in nonfinal_busy))[:state.max_active]
                state.updated_at = now_iso()
                save_campaign(workspace, state)
                return {"ok": True, "status": "running", "phase": "draining",
                        "stop_reason": stop_reason, "active_instances": state.active_instances,
                        "dispatched": []}
            final_item = next(
                (item for item in all_items
                 if item.kind == "report" and item.title == "Campaign 最终日报"),
                None,
            )
            if final_item and final_item.status in {"completed", "blocked", "cancelled"}:
                state.status = "completed"
                delivery_note = "" if final_item.status == "completed" else f"; final report {final_item.status}"
                state.stop_reason = stop_reason + delivery_note
                state.active_instances = []
                state.updated_at = now_iso()
                save_campaign(workspace, state)
                report, path = build_campaign_report(workspace, campaign_id, "final")
                _event(workspace, campaign_id, "campaign_completed", reason=state.stop_reason, report_path=str(path))
                if switch_slots and state.restore_instances:
                    switch_slots(state.restore_instances)
                return {"ok": True, "status": "completed", "stop_reason": state.stop_reason,
                        "report": report.to_dict()}
            state.status = "running"
            state.stop_reason = f"finalizing: {stop_reason}"
        items = list_work_items(workspace, campaign_id)
        busy = [item for item in items if item.status in BUSY_WORK]
        busy_instances = {item.instance_id for item in busy}
        runnable = sorted(
            (item for item in items if item.status in {"proposed", "failed"}
             and item.autonomy == "safe" and item.attempt < item.max_attempts
             and _dependencies_satisfied(item, items)),
            key=lambda item: (-item.priority, item.created_at, item.work_item_id),
        )
        if stop_reason:
            runnable = [item for item in runnable if item.kind == "report" and item.title == "Campaign 最终日报"]
            busy = [item for item in busy if item.kind == "report" and item.title == "Campaign 最终日报"]
            busy_instances = {item.instance_id for item in busy}
        selected = list(busy_instances)
        for item in runnable:
            if item.instance_id not in selected and len(selected) < state.max_active:
                selected.append(item.instance_id)
        selected = [value for value in state.allowed_instances if value in selected][:state.max_active]
        state.active_instances = selected
        portfolio_wait_until = ""
        if _is_portfolio_continuous(state):
            portfolio_wait_until = str(_load_portfolio_state(workspace, campaign_id).get("next_scout_at") or "")
        scheduled_wait = bool(portfolio_wait_until and _parse_time(portfolio_wait_until) > now)
        state.status = "running" if runnable or busy or scheduled_wait else "blocked"
        if scheduled_wait and not runnable and not busy:
            state.stop_reason = f"scheduled evidence scout at {portfolio_wait_until}"
        elif state.status == "blocked":
            state.stop_reason = "no runnable work; waiting for resume event or new evidence"
        elif not stop_reason:
            state.stop_reason = ""
        state.updated_at = now_iso()
        save_campaign(workspace, state)

    if switch_slots:
        # An empty selection is meaningful: release the last active instance
        # while the portfolio is waiting for changed input. Reports or newly
        # admitted work will start the required slot on a later tick.
        switch_slots(selected)

    dispatched: list[dict[str, str]] = []
    # Dispatch outside the lock because callbacks may touch the filesystem or
    # systemd. Each item is first leased under a short critical section.
    for candidate in runnable:
        if candidate.instance_id not in selected or candidate.instance_id in busy_instances:
            continue
        if runtime_ready is not None and candidate.requires_delivery and not runtime_ready(candidate.instance_id):
            _event(workspace, campaign_id, "work_item_waiting_delivery_channel",
                   work_item_id=candidate.work_item_id, instance_id=candidate.instance_id)
            continue
        with campaign_lock(workspace, campaign_id):
            item = load_work_item(workspace, campaign_id, candidate.work_item_id)
            state = load_campaign(workspace, campaign_id)
            if not item or not state or item.status not in {"proposed", "failed"}:
                continue
            item.attempt += 1
            lease = InstanceLease(
                campaign_id=campaign_id,
                work_item_id=item.work_item_id,
                instance_id=item.instance_id,
                holder=f"campaign-controller:{os.getpid()}",
                acquired_at=now_iso(),
                expires_at=_lease_expiry(lease_seconds, state.deadline_at),
            )
            item.status = "leased"
            item.lease_id = lease.lease_id
            item.updated_at = now_iso()
            save_lease(workspace, lease)
            save_work_item(workspace, item)
        try:
            task_id = str(dispatch(item, campaign_instruction(item)) or "").strip()
            if not task_id:
                raise RuntimeError("dispatch did not return a task/message id")
            with campaign_lock(workspace, campaign_id):
                item = load_work_item(workspace, campaign_id, item.work_item_id) or item
                item.status = "queued"
                item.task_id = task_id
                item.updated_at = now_iso()
                save_work_item(workspace, item)
                if item.source_action_id:
                    record_action_state(workspace, item.project_id, item.source_action_id, "queued", task_id=task_id)
                _event(workspace, campaign_id, "work_item_dispatched", work_item_id=item.work_item_id,
                       instance_id=item.instance_id, task_id=task_id)
            dispatched.append({"work_item_id": item.work_item_id, "instance_id": item.instance_id, "task_id": task_id})
            busy_instances.add(item.instance_id)
        except Exception as exc:
            with campaign_lock(workspace, campaign_id):
                item = load_work_item(workspace, campaign_id, item.work_item_id) or item
                item.status = "failed"
                item.evidence.append(f"dispatch_error:{exc}")
                item.updated_at = now_iso()
                save_work_item(workspace, item)
                _release_lease(workspace, campaign_id, item.lease_id)
                state = load_campaign(workspace, campaign_id)
                if state:
                    state.usage.failures += 1
                    state.updated_at = now_iso()
                    save_campaign(workspace, state)
                _event(workspace, campaign_id, "work_item_dispatch_failed", work_item_id=item.work_item_id,
                       error=str(exc))

    state = load_campaign(workspace, campaign_id)
    # A Campaign waiting on an external resume event still owes the operator
    # periodic visibility.  `blocked` pauses business work, not reporting.
    if state and state.status in {"running", "blocked"} and not pre_stop_reason:
        _schedule_report_if_due(workspace, state, now)
    return {"ok": True, "status": state.status if state else "unknown", "active_instances": selected,
            "dispatched": dispatched}


def parse_campaign_marker(user_request: str) -> tuple[str, str] | None:
    match = CAMPAIGN_MARKER.search(str(user_request or ""))
    return (match.group("campaign"), match.group("work")) if match else None


def _task_runtime_evidence(workspace: str, marker: str, instance_id: str = "") -> dict[str, Any]:
    candidate_workspace = Path(workspace)
    if instance_id and candidate_workspace.name != instance_id:
        candidate_workspace = workspace_root(workspace) / "instances" / instance_id
    tasks = candidate_workspace / "state" / "tasks"
    candidates = sorted(tasks.glob("*/task_instance.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:12]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if marker not in str(data.get("user_message") or ""):
            continue
        delivered = False
        stack: list[Any] = [data.get("metadata", {}).get("step_results", {})]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if value.get("delivered") is True or value.get("delivery_confirmed") is True:
                    delivered = True
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        task_log = path.with_name("task_log.jsonl")
        complete = False
        execution_done = False
        failed = False
        blocked_reason = ""
        resume_event = ""
        event_types: list[str] = []
        progress_phases: set[str] = set()
        planner_model_calls = 0
        step_model_calls = 0
        reported_total_model_calls = 0
        try:
            for line in task_log.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("event") == "completion_status_updated" and row.get("status") == "done":
                    execution_done = True
                if row.get("event") == "completion_status_updated" and row.get("status") == "failed":
                    execution_done = True
                    failed = True
                if row.get("event") in {"batch_plan_handler_failed", "task_failed"}:
                    execution_done = True
                    failed = True
                if row.get("event") == "iteration_llm_check" and row.get("satisfied") is True:
                    # completion_status=done is written before LLM_CHECK and is
                    # only an iteration boundary. Recovery may call the final
                    # completion hook only after acceptance actually passed.
                    complete = True
                if row.get("event") == "campaign_work_blocked":
                    blocked_reason = str(row.get("reason") or "external evidence unavailable")
                    resume_event = str(row.get("resume_event") or "")
                if row.get("event") == "plan_executor_step_completed" and row.get("event_type"):
                    event_types.append(str(row["event_type"]))
                if row.get("event") == "campaign_progress_update" and row.get("delivered") is True:
                    phase = str(row.get("phase") or "")
                    if phase:
                        progress_phases.add(phase)
                try:
                    planner_model_calls = max(
                        planner_model_calls, int(row.get("planner_llm_calls") or 0),
                    )
                    row_llm_calls = int(row.get("llm_calls") or 0)
                    if row.get("event") == "plan_executor_step_completed":
                        step_model_calls += row_llm_calls
                    else:
                        reported_total_model_calls = max(reported_total_model_calls, row_llm_calls)
                except (TypeError, ValueError):
                    pass
        except (OSError, ValueError, TypeError):
            pass
        task_dir = path.parent
        artifacts = [str(value) for value in task_dir.iterdir()
                     if value.is_file() and not value.name.startswith("_")
                     and value.name not in {"task_instance.json", "task_log.jsonl", "active_plan.json"}]
        model_calls = max(reported_total_model_calls, planner_model_calls + step_model_calls)
        return {"found": True, "complete": complete, "execution_done": execution_done,
                "failed": failed, "blocked_reason": blocked_reason, "resume_event": resume_event,
                "delivered": delivered,
                "progress_phases": sorted(progress_phases),
                "model_calls": model_calls, "event_types": event_types, "artifacts": artifacts,
                "task_id": str(data.get("task_id") or task_dir.name)}
    return {"found": False, "complete": False, "execution_done": False, "failed": False,
            "blocked_reason": "", "resume_event": "",
            "delivered": False, "model_calls": 0,
            "progress_phases": [],
            "event_types": [], "artifacts": [], "task_id": ""}


def _delivery_ack_from_latest_task(workspace: str, marker: str, instance_id: str = "") -> bool:
    return bool(_task_runtime_evidence(workspace, marker, instance_id).get("delivered"))


def reconcile_campaign_tasks(workspace: str, campaign_id: str) -> list[str]:
    """Recover queued/running work after either process has restarted."""
    reconciled: list[str] = []
    for item in list_work_items(workspace, campaign_id):
        if item.status not in {"queued", "running"}:
            continue
        marker = f"campaign_id={campaign_id} work_item_id={item.work_item_id}"
        runtime = _task_runtime_evidence(workspace, marker, item.instance_id)
        if not runtime.get("found"):
            # A process can persist the inbox message ID as "seen" and die
            # before creating TaskInstance.  A queued item with no runtime
            # evidence must be transport-recoverable without charging a new
            # business attempt.  The recovery evidence gives redispatch a new
            # inbox ID so the persisted deduplicator cannot swallow it again.
            if item.status == "queued" and (_now() - _parse_time(item.updated_at)).total_seconds() >= 60:
                with campaign_lock(workspace, campaign_id):
                    current = load_work_item(workspace, campaign_id, item.work_item_id)
                    if current and current.status == "queued":
                        recovery = 1 + sum(
                            str(value).startswith("transport_recovery=") for value in current.evidence
                        )
                        current.evidence.append(f"transport_recovery={recovery}")
                        current.status = "proposed"
                        current.attempt = max(0, current.attempt - 1)
                        current.task_id = ""
                        _release_lease(workspace, campaign_id, current.lease_id)
                        current.lease_id = ""
                        current.updated_at = now_iso()
                        save_work_item(workspace, current)
                        _event(workspace, campaign_id, "work_item_transport_recovered",
                               work_item_id=current.work_item_id, recovery=recovery)
                        reconciled.append(current.work_item_id)
            continue
        if runtime.get("complete") or runtime.get("failed"):
            complete_campaign_work(
                workspace,
                campaign_instruction(item),
                files=list(runtime.get("artifacts") or []),
                event_types=list(runtime.get("event_types") or []),
                success=not bool(runtime.get("failed")),
                evidence=["reconciled from persisted task log"],
            )
            reconciled.append(item.work_item_id)
        elif item.status == "queued":
            with campaign_lock(workspace, campaign_id):
                current = load_work_item(workspace, campaign_id, item.work_item_id)
                if current and current.status == "queued":
                    current.status = "running"
                    current.updated_at = now_iso()
                    save_work_item(workspace, current)
                    if current.source_action_id:
                        record_action_state(workspace, current.project_id, current.source_action_id,
                                            "running", task_id=current.task_id)
                    _event(workspace, campaign_id, "work_item_running", work_item_id=current.work_item_id)
                    reconciled.append(item.work_item_id)
    return reconciled


def _progress_signature(event_types: list[str], artifacts: list[str]) -> str:
    # Keep the same width as EvidenceManifest.semantic_outcome_fingerprint.
    # Mixing the former 20-character legacy value with the archived 24-character
    # value disabled the three-identical-outcomes fuse after evidence archival.
    return semantic_outcome_fingerprint(artifacts, event_types)


def _instruction_marker(instruction: str, key: str) -> str:
    match = re.search(rf"\[{re.escape(key)}=([^\]]+)\]", str(instruction or ""))
    return str(match.group(1)).strip() if match else ""


def _requested_named_artifacts(instruction: str) -> set[str]:
    """Extract named outputs without treating explicitly read inputs as deliverables."""
    text = str(instruction or "")
    names: set[str] = set()
    for match in NAMED_ARTIFACT_RE.finditer(text):
        value = match.group(0)
        if "*" in value:
            continue
        boundary = max(text.rfind(mark, 0, match.start()) for mark in "。；;\n")
        prefix = text[boundary + 1:match.start()]
        input_context = bool(re.search(r"读取|读入|输入|基于|承接|核验|检查|read|input|source|verify|inspect", prefix, re.I))
        output_context = bool(re.search(r"生成|输出|写入|编写|保存|产出|create|write|output|save", prefix, re.I))
        if input_context and not output_context:
            continue
        names.add(Path(value).name.lower())
    return names


def _artifact_semantic_problems(instruction: str, artifacts: list[str]) -> list[str]:
    """Reject deterministic, high-confidence scientific target leakage."""
    if "targetdiff_residual_analysis" not in str(instruction or "").lower():
        return []
    json_path = next((Path(p) for p in artifacts if Path(p).name.lower() == "targetdiff_residual_analysis.json"), None)
    if not json_path:
        return []
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        metrics = payload.get("metrics") or payload
        model = metrics.get("linear_model") or metrics.get("vina_linear") or {}
        baseline = metrics.get("baseline_train_mean") or metrics.get("mean_baseline") or {}
        identity_fit = (
            float(model.get("rmse")) == 0.0 and float(model.get("coef")) == 1.0
            and float(model.get("intercept")) == 0.0 and float(baseline.get("rmse")) > 0.0
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return ["TargetDiff result JSON is not semantically readable"]
    if identity_fit or "train_mean_vina" in metrics:
        return ["TargetDiff target leakage: Vina was used as both feature and prediction target"]
    return []


def complete_campaign_work(
    workspace: str,
    user_request: str,
    *,
    files: list[str] | None = None,
    event_types: list[str] | None = None,
    success: bool = True,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    marker_ids = parse_campaign_marker(user_request)
    if not marker_ids:
        return {"handled": False}
    campaign_id, work_item_id = marker_ids
    marker = f"campaign_id={campaign_id} work_item_id={work_item_id}"
    with campaign_lock(workspace, campaign_id):
        item = load_work_item(workspace, campaign_id, work_item_id)
        state = load_campaign(workspace, campaign_id)
        if not item or not state:
            return {"handled": True, "ok": False, "status": "missing_campaign_work"}
        if item.status in TERMINAL_WORK:
            return {
                "handled": True,
                "ok": item.status in {"completed", "blocked"},
                "status": f"already_{item.status}",
                "work_item": item.to_dict(),
            }
        item.artifacts = [str(value) for value in files or [] if Path(str(value)).is_file()]
        item.event_types = [str(value) for value in event_types or []]
        item.evidence.extend(str(value) for value in evidence or [])
        runtime = _task_runtime_evidence(workspace, marker, item.instance_id)
        item.artifacts = list(dict.fromkeys([
            *item.artifacts,
            *(str(value) for value in runtime.get("artifacts") or [] if Path(str(value)).is_file()),
        ]))
        delivered = bool(runtime.get("delivered"))
        runtime_blocked_reason = str(runtime.get("blocked_reason") or "")
        runtime_resume_event = str(runtime.get("resume_event") or "")
        state.usage.model_calls += int(runtime.get("model_calls") or 0)
        state.usage.cost_units += float(runtime.get("model_calls") or 0)
        problems = []
        if not success:
            problems.append("task completion reported failure")
        if item.kind != "report" and runtime.get("found") and not runtime.get("complete"):
            problems.append("final LLM acceptance not found")
        if item.requires_artifact and not item.artifacts:
            problems.append("required artifact missing")
        requested_names = _requested_named_artifacts(item.instruction)
        artifact_names = {Path(value).name.lower() for value in item.artifacts}
        missing_names = sorted(requested_names - artifact_names)
        if item.requires_artifact and missing_names:
            problems.append(f"explicitly requested artifact missing: {', '.join(missing_names)}")
        problems.extend(_artifact_semantic_problems(item.instruction, item.artifacts))
        if item.requires_delivery and not delivered:
            problems.append("real delivery callback not found")
        if "[user_progress_v2=true]" in user_request or "[user_progress_v1=true]" in user_request:
            required_progress = ({"instruction_received", "started", "executed", "verified", "finished"}
                                 if "[user_progress_v2=true]" in user_request
                                 else {"started", "executed", "finished"})
            missing_progress = sorted(required_progress - set(runtime.get("progress_phases") or []))
            if missing_progress:
                problems.append(f"required user progress callback missing: {', '.join(missing_progress)}")
        archive = archive_work_item_evidence(
            workspace,
            campaign_id=campaign_id,
            work_item_id=item.work_item_id,
            project_id=item.project_id,
            instance_id=item.instance_id,
            artifacts=item.artifacts,
            event_types=item.event_types,
        ) if item.artifacts else {}
        if archive.get("ok"):
            item.artifacts = list(archive["artifacts"])
            item.evidence.append(f"evidence_manifest={archive['manifest_path']}")
        signature = str(archive.get("semantic_outcome_fingerprint") or
                        _progress_signature(item.event_types, item.artifacts))
        input_fingerprint = _instruction_marker(item.instruction, "source_fingerprint")
        if input_fingerprint:
            item.evidence.append(f"input_fingerprint={input_fingerprint}")
        previous_receipt = latest_receipt(workspace, item.project_id)
        previous_outcome = next((
            value.split("=", 1)[1] for value in reversed(previous_receipt.findings)
            if value.startswith("outcome_fingerprint=")
        ), "") if previous_receipt else ""
        monitor_only = item.kind == "audit" or "[portfolio_scout=true]" in item.instruction
        business_progress = bool(
            item.kind == "project_iteration" and item.instance_id in PORTFOLIO_BUSINESS_INSTANCES
            and not monitor_only
            and not problems and not runtime_blocked_reason
            and signature and signature != previous_outcome
        )
        item.evidence.extend([
            f"outcome_fingerprint={signature}",
            f"monitor_only={str(monitor_only).lower()}",
            f"business_progress={str(business_progress).lower()}",
        ])
        if monitor_only:
            item.evidence.append("no_change=true")
        completed = [value for value in list_work_items(workspace, campaign_id)
                     if value.status == "completed" and value.instance_id == item.instance_id
                     and value.project_id == item.project_id]
        previous_signatures = []
        for value in sorted(completed, key=lambda row: row.updated_at)[-2:]:
            previous_signatures.extend(
                evidence_value.split("=", 1)[1] for evidence_value in value.evidence
                if evidence_value.startswith("progress_signature=")
            )
        # Repeated evidence is a business-iteration fuse. Reports intentionally
        # reuse `campaign_report_delivery` and often have no artifact, so their
        # signatures are expected to match and must not trigger this guard.
        if (item.kind != "report" and len(previous_signatures) >= 2
                and previous_signatures[-2:] == [signature, signature]):
            problems.append("three consecutive rounds produced the same event/artifact signature")
        if problems:
            item.status = "failed" if item.attempt < item.max_attempts else "blocked"
            item.blocked_reason = "; ".join(problems) if item.status == "blocked" else ""
            item.evidence.extend(problems)
            if item.source_action_id and item.status == "blocked":
                record_action_state(
                    workspace, item.project_id, item.source_action_id, "blocked",
                    task_id=item.task_id, blocked_reason=item.blocked_reason,
                )
            state.usage.failures += 1
            if item.status == "failed":
                state.usage.retries += 1
            if item.kind != "evolution_experiment":
                record_issue(workspace, {
                    "summary": f"Campaign WorkItem 验收失败: {item.title}",
                    "category": "delivery" if not delivered and item.requires_delivery else "verification",
                    "severity": "high",
                    "evidence": [f"work_item={item.work_item_id}", *problems],
                    "instance_id": item.instance_id,
                    "project_id": item.project_id,
                    "source_work_kind": item.kind,
                    "source_work_item_id": item.work_item_id,
                })
        elif runtime_blocked_reason:
            item.status = "blocked"
            item.blocked_reason = runtime_blocked_reason
            item.evidence.append(f"delivery_confirmed={delivered}")
            item.evidence.append(f"resume_event={runtime_resume_event}")
            item.evidence.append(f"progress_signature={signature}")
            state.usage.work_items_completed += 1
            if item.source_action_id:
                record_action_state(
                    workspace, item.project_id, item.source_action_id, "blocked",
                    task_id=item.task_id, blocked_reason=runtime_blocked_reason,
                )
        else:
            item.status = "completed"
            item.evidence.append(f"delivery_confirmed={delivered}")
            item.evidence.append(f"progress_signature={signature}")
            state.usage.work_items_completed += 1
            if item.source_action_id:
                record_action_state(workspace, item.project_id, item.source_action_id, "completed", task_id=item.task_id)
        item.updated_at = now_iso()
        save_work_item(workspace, item)
        _release_lease(workspace, campaign_id, item.lease_id)
        state.active_instances = [value for value in state.active_instances if value != item.instance_id]
        state.updated_at = now_iso()
        save_campaign(workspace, state)
        terminal_event = "work_item_completed" if item.status == "completed" else (
            "work_item_blocked" if item.status == "blocked" and not problems else "work_item_failed"
        )
        _event(workspace, campaign_id, terminal_event,
               work_item_id=item.work_item_id, status=item.status, artifacts=item.artifacts,
               delivery_confirmed=delivered, problems=problems)

    receipt_result: dict[str, Any] = {}
    if item.status in {"completed", "blocked"} and item.kind == "project_iteration":
        previous = latest_receipt(workspace, item.project_id)
        bounded_stage = bool(set(item.event_types) & BOUNDED_CAMPAIGN_EVENTS)
        continuation = propose_continuation(
            workspace, project_id=item.project_id, campaign_id=campaign_id,
            instruction=item.instruction, event_types=item.event_types,
            business_progress=bool(item.status == "completed" and business_progress
                                   and state.usage.work_items_created < state.budget.max_work_items),
        )
        proposed_action = continuation.get("action")
        next_actions = [proposed_action.to_dict()] if isinstance(proposed_action, NextAction) else []
        receipt_result = record_iteration(workspace, {
            "project_id": item.project_id,
            "owner_instance": item.instance_id,
            "project_goal": (load_project_state(workspace, item.project_id).goal
                             if load_project_state(workspace, item.project_id) else item.title),
            "goal": item.title,
            "inputs": list(previous.artifacts) if previous else [],
            "actions_executed": item.event_types or ["batch_plan"],
            "artifacts": item.artifacts,
            "findings": item.evidence[-5:] or ["Campaign bounded iteration completed"],
            "next_actions": next_actions,
            "stop_reason": (
                runtime_blocked_reason
                or str(continuation.get("stop_reason") or "campaign work-item budget reached")
            ) if not next_actions else "",
            "delivery_confirmed": delivered,
            "requires_delivery": item.requires_delivery,
            "project_status": "blocked" if item.status == "blocked" else "completed",
            "resume_event": runtime_resume_event,
        })
        if not receipt_result.get("ok"):
            record_issue(workspace, {
                "summary": f"Campaign 完成但 IterationReceipt 写入失败: {item.title}",
                "category": "verification", "severity": "critical",
                "evidence": [str(receipt_result)], "instance_id": item.instance_id,
                "project_id": item.project_id,
            })
    return {"handled": True, "ok": item.status in {"completed", "blocked"}, "status": item.status,
            "work_item": item.to_dict(), "receipt": receipt_result, "delivery_confirmed": delivered}


def cancel_campaign(workspace: str, campaign_id: str, reason: str) -> CampaignState:
    with campaign_lock(workspace, campaign_id):
        state = load_campaign(workspace, campaign_id)
        if not state:
            raise ValueError("campaign not found")
        state.status = "cancelled"
        state.stop_reason = str(reason or "cancelled by operator")
        for item in list_work_items(workspace, campaign_id):
            if item.lease_id:
                _release_lease(workspace, campaign_id, item.lease_id, status="released")
            if item.status in TERMINAL_WORK:
                continue
            item.status = "cancelled"
            item.blocked_reason = state.stop_reason
            item.updated_at = now_iso()
            save_work_item(workspace, item)
        state.active_instances = []
        state.updated_at = now_iso()
        save_campaign(workspace, state)
        closed_runtime_tasks = 0
        try:
            from ..tasks.task_queue import TaskQueue

            root = workspace_root(workspace)
            for instance in state.allowed_instances:
                queue_path = root / "instances" / instance / "state" / "task_queue.json"
                if queue_path.is_file():
                    closed_runtime_tasks += TaskQueue(str(queue_path)).fail_matching_description_fragment(
                        f"campaign_id={campaign_id}", state.stop_reason,
                    )
        except (OSError, ValueError) as exc:
            governance_log(workspace, "campaign_cancel_runtime_queue_cleanup_failed", {
                "campaign_id": campaign_id, "error": str(exc),
            })
        _event(workspace, campaign_id, "campaign_cancelled", reason=state.stop_reason,
               runtime_tasks_closed=closed_runtime_tasks)
        return state


def campaign_snapshot(workspace: str, campaign_id: str = "") -> dict[str, Any]:
    campaign_id = campaign_id or active_campaign_id(workspace)
    state = load_campaign(workspace, campaign_id) if campaign_id else None
    if not state:
        return {"campaign_id": campaign_id, "status": "missing"}
    items = list_work_items(workspace, campaign_id)
    result = {"campaign": state.to_dict(), "work_items": [item.to_dict() for item in items],
              "leases": [lease.to_dict() for lease in list_leases(workspace, campaign_id)]}
    if _is_portfolio_continuous(state):
        result["portfolio"] = _load_portfolio_state(workspace, campaign_id)
    return result
