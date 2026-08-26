"""Deterministic Campaign actions for instances 03, 04 and 05."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import math
from pathlib import Path
from typing import Any

from partner.governance.external_catalog import build_external_catalog
from partner.governance.evolution_loop import record_issue, start_experiment
from partner.governance.rl_evolution import run_offline_rl_update
from partner.governance.storage import governance_log, workspace_root


LOW_REWARD_ISSUE_THRESHOLD = 0.25
LOW_SUCCESS_ISSUE_THRESHOLD = 0.67


def _is_evidence_backed_low_reward(row: dict[str, Any]) -> bool:
    """Do not turn the lowest member of a healthy policy into an Issue."""
    if not row:
        return False
    return (
        float(row.get("mean_reward") or 0.0) < LOW_REWARD_ISSUE_THRESHOLD
        or float(row.get("success_rate") or 0.0) < LOW_SUCCESS_ISSUE_THRESHOLD
    )


def _paths(ctx: Any) -> tuple[Path, Path]:
    workspace = Path(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    working = Path(str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "")))
    working.mkdir(parents=True, exist_ok=True)
    return workspace_root(str(workspace)), working


def _pdf(ctx: Any, content: str, working: Path, stem: str, title: str) -> tuple[str, dict[str, Any]]:
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    output = working / f"{stem}.pdf"
    result = atomic_generate_detailed_pdf(ctx, {
        "content": content, "output_path": str(output), "title": title,
        "quality_profile": "detailed", "min_content_chars": 900, "min_sections": 4,
    })
    return (str(output) if result.get("ok") else ""), result


def atomic_framework_campaign_audit(ctx: Any, params: dict) -> dict:
    root, working = _paths(ctx)
    code = Path(os.environ.get("PARTNER_CODE_ROOT") or root.parent / "partner")
    command = [sys.executable, "-m", "pytest", "tests/test_campaign.py", "tests/test_rl_evolution.py", "-q"]
    try:
        proc = subprocess.run(command, cwd=code, text=True, capture_output=True, timeout=120, check=False)
        test_output = (proc.stdout + "\n" + proc.stderr).strip()
        test_ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        test_output, test_ok = str(exc), False
    report = f"""# Partner Campaign 框架合同审计

## 审计目标

本轮不使用泛化规划器猜测是否成功，而是在当前 Partner 代码上执行 Campaign 和离线 RL 的针对性合同测试。这些测试覆盖最多两实例、持久化恢复、交付验收、预算边界、最终报告和候选策略不自动晋升。

## 真实执行

命令：`{' '.join(command)}`。退出码为 {0 if test_ok else 1}。本轮的完成判定直接取自 pytest 进程，不由 LLM 自评。完整输出已写入同一任务目录的 JSON，便于后续复核。

## 结果

```
{test_output[-4000:]}
```

## 证据边界

通过测试只证明已声明合同没有回归，不证明两小时真实运行已成功。真实成功还需要 WorkItem 终态、新产物、QQ 回执、IterationReceipt 和预算内收口共同成立。

## 本轮覆盖的合同

Campaign 测试核验同时活动实例不超过两个、暂停时不派发、重启后从持久化任务日志恢复、
产物和交付回执缺失时不冒充完成、受控 blocked 保留 resume_event，以及失败预算达到后取消未开始业务项并只派发最终日报。

离线 RL 测试核验一条 WorkItem 能生成带来源的奖励轨迹，产物和真实送达会改变奖励分量，
一个正样本不足以获得 canary 资格，且 candidate policy 显式禁止自动 production promotion。

## 风险判读

代码合同测试的优点是可快速重复，但它使用隔离临时工作区和模拟 dispatch，不会自动发现 QQ 网络中断、
模型迟延、可见浏览器状态或 systemd 长时运行中的资源累积。因此通过后的合理次序仍是短 canary、审计证据、两小时重跑，最后才是整夜。

## 长跑验收补充条件

下一次实机不能只看最终 `completed`。审计者还应按 WorkItem 计算五实例的业务轮数、新产物率、送达率、平均租约时间、失败和重试数，
并核对最终日报生成后是否还有非报告 dispatch。对 05 还必须确认每个 evolution WorkItem 都能回溯到根 Issue，有 Experiment 文件和明确决策，且失败不会扩增成新的高优先级循环。

## 下一步

若测试失败，应先建立 Issue 并进行单一可证伪实验；若通过，则只允许进入短时 canary，不能将合同测试写成“五实例已稳定运行”。
"""
    md = working / "framework_campaign_contract_audit.md"
    raw = working / "framework_campaign_contract_audit.json"
    md.write_text(report, encoding="utf-8")
    raw.write_text(json.dumps({"command": command, "ok": test_ok, "output": test_output}, ensure_ascii=False, indent=2), encoding="utf-8")
    pdf, pdf_result = _pdf(ctx, report, working, "framework_campaign_contract_audit", "Partner Campaign 框架合同审计")
    files = [str(md), str(raw)] + ([pdf] if pdf else [])
    return {"ok": test_ok and bool(pdf), "status": "verified" if test_ok else "tests_failed",
            "files": files, "summary": f"针对性测试 {'通过' if test_ok else '失败'}；报告文件 {len(files)} 个",
            "test_output": test_output[-4000:], "pdf_quality": pdf_result.get("quality")}


def atomic_external_learning_slice(ctx: Any, params: dict) -> dict:
    root, working = _paths(ctx)
    catalog = build_external_catalog(str(root))
    present = [row for row in catalog["sources"] if row["exists"]]
    source_lines = "\n".join(
        f"- `{row['source_id']}`：`{row['path']}`，SHA256 `{row['sha256'][:16]}`，用于 {', '.join(row['use_for'])}。"
        for row in present
    )
    report = f"""# Partner 外部资料学习切片：自进化与 RL

## 真实索引结果

本轮在 `{root / 'external'}` 对策划源进行存在性、大小和 SHA256 核验。策划 {len(catalog['sources'])} 项，实际存在 {len(present)} 项。`indexed` 只表示可阅读，不表示已集成，外部代码仍被禁止直接执行。

{source_lines}

## 吸收的设计

Polar 提供 harness、trajectory 和 evaluator 分层的参考；RLVR-World 强调任务特定、可验证的奖励；SESA 将失败队列转成结构化技能，并区分提案与求解；JIT-RL 为不更新模型权重的经验复用提供参考。

## 本地落地方式

Partner 先实现离线、保守的 contextual bandit：从 Campaign WorkItem 生成轨迹，以新产物、真实交付、验收、重试与 watchdog 构成奖励。策略输出只是 candidate，样本少于 3 或均值/成功率不达标时不允许 canary。

## 没有做的事

没有把 Polar、SESA 或 RLVR-World 的训练栈直接安装到当前电脑，也没有宣称已做 GRPO 或模型微调。这些路径需要更大算力、隔离环境和单独验收，当前仅保留兼容的数据接口。

## 可复核产物

机器可读目录位于 `{catalog['path']}`，本报告同时生成 Markdown 和 PDF。下一轮应选一个低奖励动作建立正式 Experiment，而不是继续扩大资料清单。
"""
    md = working / "external_rl_learning_slice.md"
    snapshot = working / "external_catalog_snapshot.json"
    md.write_text(report, encoding="utf-8")
    snapshot.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    pdf, pdf_result = _pdf(ctx, report, working, "external_rl_learning_slice", "Partner 外部资料学习切片")
    files = [str(md), str(snapshot)] + ([pdf] if pdf else [])
    return {"ok": len(present) == len(catalog["sources"]) and bool(pdf), "status": "indexed",
            "files": files, "catalog_path": catalog["path"], "summary": f"策划源 {len(present)}/{len(catalog['sources'])} 存在",
            "pdf_quality": pdf_result.get("quality")}


def atomic_offline_rl_evolution(ctx: Any, params: dict) -> dict:
    root, working = _paths(ctx)
    campaign_id = str(params.get("campaign_id") or "")
    result = run_offline_rl_update(str(root), campaign_id)
    if not result.get("ok"):
        return result
    actions = list(result["policy"].get("actions") or [])
    # Prefer repeated harm over a single noisy low score.  This makes the
    # proposer react to persistent failure modes while the policy's canary
    # gate still handles positive-strategy promotion separately.
    weakest = min(
        actions,
        key=lambda row: float(row["mean_reward"]) * math.sqrt(max(1, int(row["samples"]))),
        default={},
    )
    issue_result: dict[str, Any] = {
        "ok": True, "status": "no_policy_eligible_business_samples", "issue": {},
    }
    if _is_evidence_backed_low_reward(weakest):
        issue_result = record_issue(str(root), {
            "summary": f"离线 RL 识别低收益动作: {weakest['action_key']}",
            "category": "verification", "severity": "high",
            "evidence": [f"campaign_id={campaign_id}", f"mean_reward={weakest.get('mean_reward')}",
                         f"samples={weakest.get('samples')}", result["trajectory_path"], result["policy_path"]],
            "instance_id": "05", "project_id": "agent_self_evolution",
        })
    elif weakest:
        issue_result["status"] = "healthy_or_insufficient_evidence_no_issue"
    issue = issue_result.get("issue") or {}
    experiment_result: dict[str, Any] = {}
    if issue.get("issue_id"):
        existing = []
        try:
            existing = [json.loads(line) for line in governance_log(str(root), "experiments").read_text(encoding="utf-8").splitlines()]
        except (OSError, ValueError):
            pass
        prior = next((row for row in reversed(existing) if row.get("issue_id") == issue["issue_id"]), None)
        experiment_result = {"ok": True, "status": "existing", "experiment": prior} if prior else start_experiment(str(root), {
            "issue_id": issue["issue_id"],
            "hypothesis": "用确定性协议替代该低收益泛化动作，可降低超时和缺失交付",
            "intervention": "只在隔离 canary 中为该 action_key 使用有限步骤的确定性协议",
            "baseline": {"action": weakest.get("action_key"), "mean_reward": weakest.get("mean_reward"),
                         "samples": weakest.get("samples")},
            "success_criteria": ["at least 3 canary samples", "mean reward improves by >=0.30",
                                 "delivery success >=0.67", "no budget or two-slot violation"],
            "tests": ["tests/test_campaign.py", "tests/test_rl_evolution.py"],
            "project_id": "agent_self_evolution",
        })
    action_lines = "\n".join(
        f"- `{row['action_key']}`：样本 {row['samples']}，均奖励 {row['mean_reward']}，"
        f"成功率 {row['success_rate']}，置信下界 {row['lower_confidence_bound']}，"
        f"canary 资格={row['eligible_for_canary']}。" for row in actions
    ) or "- 暂无可用轨迹。"
    report = f"""# Partner 离线 RL 自进化审计

## 轨迹转换

Campaign `{campaign_id}` 新写入 {result['new_trajectories']} 条轨迹。每条都保留 WorkItem、实例、动作、终态、产物、送达回执、重试和奖励分解，可以回溯到 `{result['trajectory_path']}`。

## 奖励与策略

当前策略是 `offline conservative contextual bandit`，只处于 candidate。候选动作数 {len(actions)}；最低观测奖励动作为 `{weakest.get('action_key', 'none')}`，样本 {weakest.get('samples', 0)}，均值 {weakest.get('mean_reward', 0)}。只有均奖励低于 {LOW_REWARD_ISSUE_THRESHOLD} 或成功率低于 {LOW_SUCCESS_ISSUE_THRESHOLD} 才建立低收益 Issue；“在健康动作中相对最低”不等于故障。置信下界和最小样本门槛防止一次偶然成功被误当成新策略。

### 动作奖励快照

{action_lines}

奖励被限制在 -1 到 1。可验证业务指标改善是主奖励；新 outcome fingerprint 与真实承接上轮机器产物是次级奖励。完成、PDF 和 QQ 回执只提供小额合同分，不能把监测、报告或自评变成项目进步。缺失产物、缺失交付、重试、超时和 watchdog 分别扣分。

## 正式自进化记录

Issue 状态：`{issue_result.get('status')}`，Issue ID：`{issue.get('issue_id', '')}`。Experiment 状态：`{experiment_result.get('status')}`，Experiment ID：`{(experiment_result.get('experiment') or {}).get('experiment_id', '')}`。没有 v2 业务样本时不会再制造 `no_samples` Issue。canary 决策数：`{len((result.get('canary') or {}).get('decisions') or [])}`；只有满足双臂样本与收益门才可能 promoted。

## 门槛与回滚

至少 3 个 canary 样本、均奖励提升 0.30、交付成功率至少 0.67，且不得违反预算和两实例限制。任一条不满足则 rejected/inconclusive，不替换生产策略。

## 对本次两小时运行的结论

奖励明确区分了真实可验证动作与泛化规划/写文档循环。05 的失败不再递归生成新的高优先级自进化 WorkItem；它们只能回填源 Issue 和影响候选策略。
"""
    md = working / "offline_rl_evolution_audit.md"
    snapshot = working / "offline_rl_evolution_snapshot.json"
    md.write_text(report, encoding="utf-8")
    snapshot.write_text(json.dumps({"rl": result, "issue": issue_result, "experiment": experiment_result},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    pdf, pdf_result = _pdf(ctx, report, working, "offline_rl_evolution_audit", "Partner 离线 RL 自进化审计")
    files = [str(md), str(snapshot)] + ([pdf] if pdf else [])
    return {"ok": bool(pdf) and bool(experiment_result.get("ok", True)),
            "status": ("candidate_experiment" if experiment_result.get("experiment") else
                       "candidate_policy_observed" if actions else "waiting_business_samples"),
            "files": files, "summary": f"新轨迹 {result['new_trajectories']} 条；候选动作 {len(actions)} 个；未自动晋升",
            "policy_path": result["policy_path"], "experiment": experiment_result.get("experiment"),
            "pdf_quality": pdf_result.get("quality")}


HANDLERS = {
    "framework_campaign_contract_audit": atomic_framework_campaign_audit,
    "external_learning_index_slice": atomic_external_learning_slice,
    "offline_rl_self_evolution": atomic_offline_rl_evolution,
}
