"""State Persistence — Partner 状态持久化与自动恢复。

在每次研究循环处理前后保存当前状态到 last_state.json，
重启时读取并生成结构化汇报，替代模糊的"我回来了"。
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


LAST_STATE_FILE = "last_state.json"


def save(workspace: str, state: Dict):
    """保存当前状态到 last_state.json。

    Args:
        workspace: 工作区路径
        state: {
            "active_project": str,
            "last_action": str,
            "last_metrics": {str: str},
            "pending_tasks": [str],
            "last_dialog_summary": str,
            "source": str,  # e.g. "project", "curiosity", "wake_up"
        }
    """
    state_path = os.path.join(workspace, "state", LAST_STATE_FILE)
    try:
        full_state = {
            **state,
            "saved_at": datetime.now().isoformat(),
        }
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(full_state, f, indent=2, ensure_ascii=False)
        logger.info(f"[State] 状态已保存: {state.get('active_project', '无项目')}")
    except Exception as e:
        logger.warning(f"[State] 保存失败: {e}")


def load(workspace: str) -> Optional[Dict]:
    """读取上次保存的状态。

    Args:
        workspace: 工作区路径

    Returns:
        上次保存的状态字典，或 None（文件不存在/损坏）
    """
    state_path = os.path.join(workspace, "state", LAST_STATE_FILE)
    if not os.path.exists(state_path):
        logger.info("[State] 无上次状态记录")
        return None
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        logger.info(f"[State] 已恢复上次状态: {state.get('active_project', '无项目')}")
        return state
    except Exception as e:
        logger.warning(f"[State] 读取失败: {e}")
        return None


def _humanize_metrics(metrics: Dict) -> str:
    """Convert internal metric names to natural language."""
    if not metrics:
        return ""
    parts = []
    for k, v in metrics.items():
        key_lower = k.lower()
        if key_lower == "pool_size":
            parts.append(f"系统状态：队列中有 {v} 个待处理任务")
        elif key_lower == "task_id":
            parts.append(f"任务编号：{v}")
        else:
            parts.append(f"{k}：{v}")
    return "（" + "；".join(parts) + "）"


def _humanize_project(name: str) -> str:
    """Convert internal project names to natural language."""
    if not name:
        return "未记录"
    if name == "CRON_TICK":
        return "periodic check"
    # Filter out meaningless/placeholder project names
    meaningless = [
        "推进研究项目", "project_knowledge", "最新研究进展",
        "最近的研究发现", "general", "default", "unknown",
        "测试", "test", "temp",
    ]
    name_lower = name.lower().strip()
    for bad in meaningless:
        if name_lower == bad.lower() or name_lower == bad:
            return "no active project"
    # Single word chinese phrases that are too vague
    if len(name) <= 4 and all('\u4e00' <= c <= '\u9fff' for c in name):
        return "no active project"
    return name


def format_restart_report(last_state: Optional[Dict]) -> str:
    """根据 last_state 生成重启后结构化汇报文本。

    格式：
    ✅ 系统已重启，恢复运行。
    📌 上次工作状态：[项目名称]，已执行到：[具体进度]
    📋 当前计划：[接下来 1-3 步]
    🕒 预计下次汇报：有实质性进展时主动发送。

    Args:
        last_state: load() 返回的状态字典

    Returns:
        格式化的汇报文本
    """
    if not last_state:
        return (
            "✅ 系统已重启，恢复运行。\n"
            "📌 上次工作状态：未找到历史记录（全新启动或数据已清理）。\n"
            "📋 当前计划：检查知识库和项目状态，自主规划下一步。\n"
            "🕒 预计下次汇报：有实质性进展时主动发送。"
        )

    raw_project = last_state.get("active_project", "未记录")
    last_action = last_state.get("last_action", "未知")
    metrics = last_state.get("last_metrics", {})
    pending = last_state.get("pending_tasks", [])
    dialog = last_state.get("last_dialog_summary", "")

    project = _humanize_project(raw_project)

    # 如果没有活跃项目，展示简洁版本
    if project == "no active project":
        return (
            "✅ 系统已重启，恢复运行。\n"
            "📌 没有活跃项目。等待用户指令或自动开始探索。\n"
            "🕒 预计下次汇报：有实质性进展时主动发送。"
        )

    # 构建指标描述（自然语言）
    metrics_str = _humanize_metrics(metrics)

    # 构建进度描述
    if metrics_str:
        progress = f"{last_action} {metrics_str}"
    else:
        progress = last_action

    # 构建计划
    plan_lines = []
    if pending:
        for i, task in enumerate(pending[:3], 1):
            plan_lines.append(f"{i}. {task}")
    elif project == "no active project":
        plan_lines = ["1. 等待用户指令", "2. 或自动开始知识探索"]
    else:
        plan_lines = ["1. 根据上次进度继续推进", "2. 搜索相关文献寻找改进方向"]

    plan_str = "\n".join(plan_lines)

    # 对话摘要
    dialog_str = f"\n💬 根据对话记录：{dialog}" if dialog else ""

    return (
        f"✅ 系统已重启，恢复运行。\n"
        f"📌 上次工作状态：{project}，已执行到：{progress}{dialog_str}\n"
        f"📋 当前计划：\n{plan_str}\n"
        f"🕒 预计下次汇报：有实质性进展时主动发送。"
    )


def format_status_report(last_state: Optional[Dict]) -> str:
    """生成"在做什么/做得咋样了"的结构化状态汇报。

    格式：
    📊 当前研究：[项目名称]
    📈 最近成果：[具体指标变化]
    ⏳ 正在进行：[具体操作]
    🎯 下一步计划：[基于现状的自主规划]

    Args:
        last_state: 当前状态

    Returns:
        格式化的状态汇报文本
    """
    if not last_state:
        return (
            "📊 当前研究：暂无活跃项目\n"
            "📈 最近成果：—\n"
            "⏳ 正在进行：检查知识库和项目文件\n"
            "🎯 下一步计划：识别可探索的方向并开始研究"
        )

    raw_project = last_state.get("active_project", "暂无活跃项目")
    last_action = last_state.get("last_action", "分析中")
    metrics = last_state.get("last_metrics", {})
    pending = last_state.get("pending_tasks", [])

    project = _humanize_project(raw_project)
    metrics_str = _humanize_metrics(metrics)

    if metrics_str:
        progress = f"{last_action} {metrics_str}"
    else:
        progress = last_action

    plan_str = ""
    if pending:
        plan_str = "接下来计划：\n" + "\n".join(
            f"  • {t}" for t in pending[:3]
        )
    else:
        plan_str = "下一步：根据项目进展自动规划"

    return (
        f"📊 当前研究：{project}\n"
        f"📈 最近成果：{progress}\n"
        f"⏳ 正在进行：推进研究计划中\n"
        f"🎯 {plan_str}"
    )


def build_last_state_from_task(event_type: str, payload: Dict,
                                knowledge=None, journal=None) -> Dict:
    """从事件 payload 构建 last_state。

    Args:
        event_type: 事件类型字符串
        payload: 事件 payload
        knowledge: 知识库实例（可选）
        journal: 日志实例（可选）

    Returns:
        last_state 字典
    """
    state = {
        "active_project": payload.get("title", payload.get("topic", "")),
        "last_action": f"{event_type} 执行中",
        "last_metrics": {},
        "pending_tasks": [],
        "last_dialog_summary": "",
        "source": event_type,
    }
    return state
