"""User-observable progress and domain-aware delivery copy for Campaign work."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PROJECT_NAMES = {
    "01": "小红书内容运营",
    "02": "分子生成方法研究",
    "03": "Partner 框架与前端",
    "04": "文献与 GitHub 学习",
    "05": "Partner 自进化与 RL",
}


PROJECT_MILESTONES = {
    "01": ("读取上一轮内容证据与来源", "执行来源/主张/风险分析", "形成候选与安全边界"),
    "02": ("核验数据来源、拆分和字段合同", "运行可复现实验或统计分析", "比较指标并解释科学边界"),
    "03": ("定位本轮框架假设与影响范围", "运行代码、测试或恢复 canary", "核对回归、证据和晋升边界"),
    "04": ("核验外部原文、代码和版本", "映射到 Partner 独立适配面", "验证可采用内容与未集成边界"),
    "05": ("摄取本轮新业务轨迹", "重算奖励与 baseline/candidate", "决定 Issue、Experiment 与是否晋升"),
}


def instance_from_workspace(workspace: str) -> str:
    match = re.search(r"/instances/(0[1-5])(?:/|$)", str(workspace))
    return match.group(1) if match else ""


def visibility_mode(instruction: str) -> str:
    return "compact" if "[portfolio_scout=true]" in str(instruction) else "standard"


def _strategy(instruction: str, event_type: str) -> str:
    match = re.search(r"strategy_id=([^\s\]]+)", str(instruction))
    return match.group(1) if match else event_type


def _received_instruction(instruction: str) -> str:
    """Return user-readable business intent without controller protocol markers."""
    text = str(instruction or "").strip()
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
    return (
        f"📋 {instance_id} 收到本轮任务\n"
        f"项目：{project}\n"
        f"任务内容：{_received_instruction(instruction)}\n"
        "执行范围：承接上一轮结果，只完成本轮明确任务；遇到需要你处理的边界会单独说明。"
    )


def start_message(*, instance_id: str, title: str, event_type: str, instruction: str) -> str:
    project = PROJECT_NAMES.get(instance_id, f"实例 {instance_id or '?'}")
    strategy = _strategy(instruction, event_type)
    milestones = PROJECT_MILESTONES.get(instance_id, ("读取承接证据", "实际执行", "验收和交付"))
    if visibility_mode(instruction) == "compact":
        return (
            f"🔎 {instance_id} 开始低频证据检查\n"
            f"项目：{project}\n检查：{milestones[0]} → {milestones[1]}\n"
            "这是监测任务；无变化会明确报告，不会冒充项目进步。"
        )
    return (
        f"▶️ {instance_id} 开始本轮执行\n"
        f"项目：{project}\n任务：{title}\n策略：{strategy}\n"
        "本轮关键步骤：\n"
        f"1. {milestones[0]}\n2. {milestones[1]}\n3. {milestones[2]}\n"
        "我会在实际执行后发送步骤证据、结果和下一步，不只发送一个 PDF。"
    )


def _result_payload(result: dict[str, Any]) -> dict[str, Any]:
    nested = result.get("result")
    return nested if isinstance(nested, dict) else result


def _metrics_text(result: dict[str, Any]) -> str:
    payload = _result_payload(result)
    metrics = payload.get("business_metrics") or payload.get("metrics") or result.get("metrics") or {}
    if isinstance(metrics, dict) and metrics:
        pairs = []
        for key, value in list(metrics.items())[:8]:
            if isinstance(value, dict):
                continue
            pairs.append(f"{key}={value}")
        if pairs:
            return "；".join(pairs)
    for keys in (("train_rows", "test_rows", "train_test_group_overlap"),
                 ("records", "unique_urls"), ("new_trajectories",)):
        pairs = [f"{key}={payload[key]}" for key in keys if key in payload]
        if pairs:
            return "；".join(pairs)
    return str(result.get("summary") or result.get("status") or "已生成机器结果")[:500]


def execution_receipt_message(*, instance_id: str, event_type: str, result: dict[str, Any],
                              instruction: str) -> str:
    files = [Path(str(value)).name for value in result.get("files") or []]
    state = "通过" if result.get("ok") else "未通过"
    monitor = visibility_mode(instruction) == "compact"
    prefix = "🔎 证据检查完成" if monitor else "⚙️ 关键操作完成"
    payload = _result_payload(result)
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
        action_names = {
            "framework_campaign_contract_audit": "运行 Campaign/RL 合同测试并核对恢复路径",
            "continuous_project_step": "执行本项目当前策略并核对机器指标",
            "evidence_execution_slice": "读取内容证据，运行去重与来源检查脚本",
            "targetdiff_provenance_audit": "核验 TargetDiff 来源、镜像校验和及 split/affinity 结构",
            "external_learning_index_slice": "核验外部来源版本及已索引/已集成边界",
            "offline_rl_self_evolution": "摄取本轮新轨迹，重算奖励与候选策略",
        }
        action = action_names.get(event_type, f"执行本轮 {event_type} 任务")
    return (
        f"{prefix}\n实例：{instance_id}\n事件：{event_type}\n执行状态：{state}\n"
        f"实际操作：{action[:700]}\n"
        f"机器结果：{_metrics_text(result)}\n"
        f"产物：{', '.join(files[:6]) if files else '无有效产物'}"
    )


def verification_receipt_message(*, instance_id: str, result: dict[str, Any],
                                 file_delivery: dict[str, Any], instruction: str) -> str:
    files = [Path(str(value)).name for value in result.get("files") or []]
    report_ok = file_delivery_confirmed(file_delivery)
    monitor = visibility_mode(instruction) == "compact"
    return (
        f"{'🔎' if monitor else '🧪'} {instance_id} 结果核验完成\n"
        f"机器验收：{'通过' if result.get('ok') else '未通过'}\n"
        f"结果文件：{', '.join(files[:6]) if files else '无'}\n"
        f"PDF 真实送达：{'已确认' if report_ok else '未确认'}\n"
        f"最终指标：{_metrics_text(result)}"
    )


def finish_message(*, instance_id: str, title: str, event_type: str, result: dict[str, Any],
                   instruction: str, report_delivered: bool) -> str:
    payload = _result_payload(result)
    blocked = bool(result.get("blocked") or payload.get("blocked"))
    boundary = str(result.get("blocked_reason") or payload.get("blocked_reason") or "")
    next_action = str(result.get("next_action") or payload.get("next_action") or "")
    if blocked:
        conclusion = f"受控等待：{boundary or '等待明确恢复条件'}"
    elif result.get("ok"):
        conclusion = str(result.get("summary") or result.get("status") or "本轮验收通过")
    else:
        conclusion = f"本轮未通过：{result.get('error') or result.get('status') or '未知错误'}"
    if not next_action:
        next_action = "由最新 Receipt/Campaign 调度决定；没有真实入队回执时不会声称已开始。"
    return (
        f"{'✅' if result.get('ok') else '❌'} {instance_id} 本轮结果\n"
        f"任务：{title}\n结论：{conclusion}\n"
        f"关键指标：{_metrics_text(result)}\n"
        f"报告送达：{'已确认' if report_delivered else '未确认'}\n"
        f"下一步：{next_action}"
    )


def report_caption(instance_id: str, title: str) -> str:
    project = PROJECT_NAMES.get(instance_id, f"实例 {instance_id}")
    return f"{instance_id} {project}｜{title}｜结果报告"


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
