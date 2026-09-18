"""Evidence-bound user narratives for application Jobs.

The runtime owns facts; this module only turns a completed Job and its task
evidence into language a project owner can understand.  It deliberately does
not select Events, alter rewards, or infer success from file existence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_SHORT_NAMES = {
    "xiaohongshu_operations": "小红书内容运营",
    "molecular_generation": "分子方法研究",
    "molecular_dynamics_study": "分子动力学研究",
    "literature_github_learning": "文献与 GitHub 学习",
    "partner_explore": "Partner 自进化",
}


def _task_text(task: Mapping[str, Any]) -> str:
    return " ".join((str(task.get("user_message") or ""),
                     str(task.get("root_user_request") or ""))).lower()


def task_series(task: Mapping[str, Any]) -> str:
    text = _task_text(task)
    if "native_kind=external_learning" in text or "外部知识主动学习" in text:
        return "active_learning"
    if "native_kind=learning" in text or "partner 自进化" in text or "自进化诊断" in text:
        return "self_evolution"
    return "project"


def journey_sentence(tasks: Sequence[Mapping[str, Any]]) -> str:
    """Summarise meaningful cross-Event transitions without exposing Events."""
    series = [task_series(task) for task in tasks]
    ordered: list[str] = []
    for value in series:
        if not ordered or ordered[-1] != value:
            ordered.append(value)
    labels = {
        "project": "项目证据检查/实验",
        "active_learning": "外部知识补充与采用",
        "self_evolution": "Partner 机制诊断",
    }
    if not ordered:
        return ""
    if len(ordered) == 1:
        return f"本轮路径：{labels[ordered[0]]}。"
    return "本轮路径：" + " → ".join(labels[value] for value in ordered) + "。"


def _load_json_artifact(files: Sequence[str], suffix: str) -> dict[str, Any]:
    for raw in files:
        path = Path(str(raw))
        if path.name.endswith(suffix) and path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                continue
            if isinstance(value, dict):
                return value
    return {}


def _fmt(value: object, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "未知"


def _receipt(task: Mapping[str, Any]) -> Mapping[str, Any]:
    governance = ((task.get("metadata") or {}).get("manual_iteration_governance") or {})
    return governance.get("receipt") or {}


def _next_action(task: Mapping[str, Any], fallback: str) -> str:
    actions = _receipt(task).get("next_actions") or []
    for row in actions:
        if not isinstance(row, Mapping):
            continue
        title = str(row.get("title") or "").strip()
        if title and title not in {"检查交付物并补齐缺口", "继续推进", "下一步"}:
            return title.rstrip("。") + "。"
    return fallback


def _molecular_message(*, job: Mapping[str, Any], task: Mapping[str, Any],
                       files: Sequence[str], tasks: Sequence[Mapping[str, Any]]) -> str:
    metrics = _load_json_artifact(files, "molecular_method_candidate_metrics.json")
    baseline = metrics.get("baseline") or {}
    candidate = metrics.get("candidate") or {}
    deltas = metrics.get("candidate_minus_baseline") or {}
    improved = bool(metrics.get("candidate_improved"))
    correction = bool(metrics.get("supersedes_previous_conclusion"))
    # Older metrics do not carry the presentation flag.  A current statistical
    # decision following a point-estimate-only positive decision is still a
    # correction and must be named as such.
    if not correction and metrics.get("statistical_validation") and not improved:
        request = str(job.get("request") or "").lower()
        correction = "复核" in request or "不得沿用" in request

    if correction:
        heading = "⚠️ 结论更正｜02 分子项目"
    else:
        heading = "🧪 项目实验｜02 分子项目"
    if not metrics:
        return (
            f"{heading}\n"
            f"{journey_sentence(tasks)}\n"
            "本轮任务已结束，但没有找到可复核的分子指标文件；因此不能判断候选是否改善。\n"
            "工程状态：执行记录已形成；科学状态：证据不足。\n"
            "下一步：先补齐指标证据，不重复宣称实验成功。"
        )

    b_scaffold = baseline.get("selected_scaffold_count")
    c_scaffold = candidate.get("selected_scaffold_count")
    fp_delta = deltas.get("fingerprint_diversity")
    qed_delta = deltas.get("mean_qed")
    sa_delta = deltas.get("mean_sa")
    statistical = metrics.get("statistical_validation") or {}
    supported_axes = statistical.get("supported_axes") or {}
    supported_count = sum(bool(value) for value in supported_axes.values())
    verdict = ("统计硬门支持候选，本轮可以进入下一层验证。" if improved else
               "点估计虽有变化，但没有一个期望方向指标形成不重叠的 95% 区间；本轮证据不足，不晋升。")
    correction_text = (
        "上一版只检查点估计便写成“支持候选”，该结论现已作废。" if correction else ""
    )
    path = journey_sentence(tasks)
    learning = ""
    if any(task_series(row) == "active_learning" for row in tasks):
        learning = "外部学习补齐并冻结了可复核输入，随后已被本次项目实验真实消费。"
    next_action = _next_action(
        task,
        str(metrics.get("next_action") or (
            "下一轮保持数据和预算不变，改测机制不同的候选；"
            "若仍无显著改善，就停止继续调整同类筛选权重。"
        )),
    )
    return "\n".join(line for line in (
        heading,
        correction_text,
        path,
        learning,
        (f"实验结果：骨架数 {b_scaffold}→{c_scaffold}；指纹多样性变化 "
         f"{_fmt(fp_delta)}；QED 变化 {_fmt(qed_delta)}；SA 变化 {_fmt(sa_delta)}。"),
        f"统计复核：{supported_count}/4 个期望方向指标通过区间门。{verdict}",
        "工程状态：真实 RDKit 对照、机器验真和报告生成均已完成。",
        f"下一步：{next_action}",
    ) if line)


def _molecular_readiness_message(*, task: Mapping[str, Any], files: Sequence[str],
                                 tasks: Sequence[Mapping[str, Any]]) -> str:
    validation = _load_json_artifact(files, "molecular_data_readiness_validation.json")
    usable = int(validation.get("usable_dataset_count") or 0)
    scanned = int(validation.get("scanned_file_count") or 0)
    blocked = bool(validation.get("blocked"))
    if blocked:
        conclusion = "没有发现同时具备分子身份、靶点身份和活性测量的数据，暂不能进入靶点条件实验。"
        next_action = "从官方来源补齐一份满足三组字段的数据；取得后必须回到原项目做同输入对照。"
    else:
        conclusion = f"发现 {usable} 份满足分子、靶点和活性三组字段的数据，可以进入下一阶段实验。"
        next_action = "读取已通过验真的冻结数据，保持同一输入和预算，执行候选方法对照。"
    return "\n".join((
        "🧪 项目检查｜02 分子项目",
        journey_sentence(tasks),
        f"做了什么：在受控范围内检查了 {scanned} 个项目与外部数据文件。",
        f"项目结论：{conclusion}",
        f"工程状态：扫描与字段验真已完成；{'项目受控等待，不把缺数据说成实验失败。' if blocked else '数据合同通过。'}",
        f"下一步：{_next_action(task, next_action)}",
    ))


def _md_comparison_message(*, task: Mapping[str, Any], files: Sequence[str],
                           tasks: Sequence[Mapping[str, Any]]) -> str:
    machine = _load_json_artifact(files, "03_md_integrator_comparison.json")
    result = machine.get("result") if isinstance(machine.get("result"), Mapping) else machine
    comparison = ((result.get("comparison") or {}).get("matched") or {})
    if not comparison:
        return "\n".join((
            "⚠️ 项目实验未验收｜03 分子动力学",
            journey_sentence(tasks),
            "本轮没有形成可复核的 baseline/candidate 配对指标，因此不能声称方法改善。",
            "下一步：重新执行同初始条件、同时间步和同预算的两臂对照。",
        ))
    ci = comparison.get("improvement_95ci") or [0.0, 0.0]
    supported = bool(comparison.get("candidate_supported"))
    decision = (
        "数值基准支持 candidate 进入真实小分子体系 canary；production_effective=false。"
        if supported else
        "区间没有稳定支持 candidate，本轮拒绝采用；production_effective=false。"
    )
    return "\n".join((
        "🧪 项目实验｜03 分子动力学",
        journey_sentence(tasks),
        (f"做了什么：在 {len(comparison.get('matched_dts') or [])} 个相同时间步、相同初始条件和"
         f"相同步数下，对比 {comparison.get('baseline')} 与 {comparison.get('candidate')}。"),
        (f"结果：candidate 的平均绝对末态能量漂移减少量为 "
         f"{_fmt(comparison.get('candidate_improvement'), 6)}；配对 bootstrap 95% 区间为 "
         f"[{_fmt(ci[0], 6)}, {_fmt(ci[1], 6)}]。"),
        f"项目判断：{decision}",
        "证据边界：本轮是真实数值执行，但体系是无量纲谐振子，不等同于真实分子力场验证。",
        f"下一步：{_next_action(task, '把同一对照合同迁移到一个小型真实分子体系。')}",
    ))


def _external_source_message(*, task: Mapping[str, Any], files: Sequence[str],
                             tasks: Sequence[Mapping[str, Any]]) -> str:
    machine = _load_json_artifact(files, "04_harness_source_candidate.json")
    candidate = machine.get("adoption_candidate") or {}
    metrics = machine.get("business_metrics") or {}
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    inference = ("源码事实把“压缩历史仅供参考”和“当前轮次上下文”明确分开，"
                 "为 Partner 防止压缩后错误续跑旧任务提供了一个可测试机制。")
    matched = str(candidate.get("matched_test") or "").strip()
    if candidate.get("candidate_id") == "preflight_reset_signal":
        matched = ("冻结同一份已替换的 history，对比无信号 baseline 与 "
                   "preflight_reset=true candidate 是否只改变旧历史读取；无差异即拒绝。")
    elif candidate_id in {"reference_only_preflight_hook", "summary_reference_only_prefix"}:
        matched = ("冻结包含 stop、旧待办和主题切换的三类会话，对比 baseline 与 candidate 是否错误恢复旧任务；"
                   "误续跑率没有下降或正常续跑受损即拒绝。")
    elif len(matched) > 190:
        matched = matched[:187].rstrip("，；。") + "……"
    return "\n".join((
        "📚 外部主动学习｜04 Harness 源码",
        (f"学了什么：真实读取同一 Harness 的 {metrics.get('source_files_read', 0)} 个核心源码文件，"
         f"形成 {metrics.get('grounded_source_claims', 0)} 条带行号事实，并提出 "
         f"{candidate.get('candidate_id') or '无'}。"),
        f"为何有用：{inference}",
        "没证明什么：尚未执行 baseline/candidate 对照；没有集成代码，也没有生产生效。",
        f"下一步：{matched or '先补齐源码证据，再设计同输入对照。'}",
    ))


def _self_evolution_inventory_message(*, files: Sequence[str],
                                      tasks: Sequence[Mapping[str, Any]]) -> str:
    machine = _load_json_artifact(files, "05_event_contract_inventory.json")
    metrics = machine.get("business_metrics") or {}
    candidate = machine.get("candidate") or {}
    regressions = machine.get("regression_results") or []
    passed = sum(int(row.get("exit_code") == 0) for row in regressions if isinstance(row, Mapping))
    return "\n".join((
        "🧬 Partner 自进化审查｜05 Event 合同",
        (f"检查了什么：读取 {metrics.get('sources_read', 0)} 个 Hermes/Partner 真实源码面，"
         f"运行 {len(regressions)} 个聚焦行为检查，{passed}/{len(regressions)} 通过。"),
        (f"形成了什么：提出 shadow Candidate `{candidate.get('candidate_id') or '无'}`，"
         "用于把项目动作与真实失败机制绑定。"),
        "没证明什么：本轮是自进化前置审查，不是业务项目改善；Candidate 未做匹配 canary，也未进入生产。",
        "下一步：选取一个真实失败 Episode，执行同输入 baseline/candidate；只在候选修复失败且聚焦回归无退化时考虑晋升。",
    ))


def terminal_message(*, job: Mapping[str, Any], task: Mapping[str, Any],
                     files: Sequence[str], summary: str,
                     tasks: Sequence[Mapping[str, Any]] = ()) -> str:
    """Build one coherent terminal narrative from authoritative task facts."""
    project_id = str(job.get("project_id") or "")
    if project_id == "molecular_generation" and any(
        Path(str(path)).name.endswith("molecular_method_candidate_metrics.json")
        for path in files
    ):
        return _molecular_message(job=job, task=task, files=files, tasks=tasks)
    if project_id == "molecular_generation" and any(
        Path(str(path)).name.endswith("molecular_data_readiness_validation.json")
        for path in files
    ):
        return _molecular_readiness_message(task=task, files=files, tasks=tasks)
    if project_id == "molecular_dynamics_study" and any(
        Path(str(path)).name.endswith("03_md_integrator_comparison.json")
        for path in files
    ):
        return _md_comparison_message(task=task, files=files, tasks=tasks)
    if project_id == "literature_github_learning" and any(
        Path(str(path)).name.endswith("04_harness_source_candidate.json")
        for path in files
    ):
        return _external_source_message(task=task, files=files, tasks=tasks)
    if project_id == "partner_explore" and any(
        Path(str(path)).name.endswith("05_event_contract_inventory.json")
        for path in files
    ):
        return _self_evolution_inventory_message(files=files, tasks=tasks)

    status = str(job.get("status") or "")
    succeeded = status == "completed"
    heading = "✅ 项目结果" if succeeded else "❌ 本轮未通过"
    project = PROJECT_SHORT_NAMES.get(project_id, project_id or "当前项目")
    cleaned = re.sub(r"(?:[A-Za-z]:[\\/]|/)[^\s;,]+", "[本地证据]", str(summary or ""))
    cleaned = re.sub(r"\b(?:event_handler|batch_plan_handler|decision=)[^\s;,]*", "", cleaned,
                     flags=re.I).strip(" ；。")
    if not cleaned:
        cleaned = "真实执行与验真已经完成。" if succeeded else "本轮未形成可验收结果。"
    return "\n".join((
        f"{heading}｜{project}",
        journey_sentence(tasks),
        f"本轮结论：{cleaned[:700]}",
        ("工程状态：执行完成；业务结论以报告中的机器证据为准。" if succeeded else
         "工程状态：执行未通过；已保留失败证据，没有把失败描述成成功。"),
        f"下一步：{_next_action(task, '根据本轮证据选择一个具体、可验证且不重复的动作。')}",
    ))


__all__ = ["journey_sentence", "task_series", "terminal_message"]
