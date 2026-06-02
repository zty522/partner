"""Project Manager — 活跃项目持久化与自动恢复。

每个实例独立维护 active_project.json，记录用户指定的当前研究方向。
重启/空闲时优先读取该文件，而不是进行泛搜索。
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# 数据结构格式：
# {
#   "project_name": "年龄预测项目",
#   "project_path": "",  # 如果项目在工作区外，记录路径
#   "last_user_instruction": "继续推进年龄预测项目",
#   "last_instruction_time": "2026-05-30T21:38:07",
#   "current_phase": "修复batch correction数据泄漏",
#   "next_actions": [
#     "修复 integrate_age_aware_correction.py 第239行泄漏",
#     "重新运行 PLS 集成模型",
#     "探索 Transformer 架构"
#   ]
# }


def get_path(workspace: str) -> str:
    """获取 active_project.json 的完整路径。

    Args:
        workspace: 实例工作目录
    Returns:
        文件路径
    """
    return os.path.join(workspace, "20_records", "active_project.json")


def load(workspace: str) -> Optional[Dict]:
    """读取当前活跃项目。

    Args:
        workspace: 实例工作目录
    Returns:
        活跃项目字典，或 None（文件不存在/损坏）
    """
    path = get_path(workspace)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"[ProjectManager] 已加载活跃项目: {data.get('project_name', '?')}")
        return data
    except Exception as e:
        logger.warning(f"[ProjectManager] 加载失败: {e}")
        return None


def save(workspace: str, project_data: Dict):
    """保存活跃项目到文件。

    Args:
        workspace: 实例工作目录
        project_data: 项目数据结构
    """
    path = get_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        data = {
            **project_data,
            "saved_at": datetime.now().isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"[ProjectManager] 已保存活跃项目: {project_data.get('project_name', '?')}")
    except Exception as e:
        logger.warning(f"[ProjectManager] 保存失败: {e}")


def update_from_instruction(workspace: str, instruction: str):
    """从用户指令更新活跃项目。

    用户说「推进年龄预测」时，更新 project_name 和时间。
    如果已存在相同项目，只更新时间；如果不同项目，切换。

    Args:
        workspace: 实例工作目录
        instruction: 用户指令（如"继续推进年龄预测项目"）
    """
    current = load(workspace)

    # 从指令中提取项目名称：去掉指令前缀
    name = instruction
    for prefix in ["继续推进", "推进", "继续做", "去做", "开始做", "做一下", "继续",
                   "研究", "搜索"]:
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    if not name:
        name = instruction[:40]

    data = {
        "project_name": name,
        "project_path": current.get("project_path", "") if current else "",
        "last_user_instruction": instruction,
        "last_instruction_time": datetime.now().isoformat(),
        "current_phase": current.get("current_phase", "") if current else "",
        "next_actions": current.get("next_actions", []) if current else [],
    }
    save(workspace, data)


def clear(workspace: str):
    """清空活跃项目。"""
    path = get_path(workspace)
    if os.path.exists(path):
        try:
            os.remove(path)
            logger.info("[ProjectManager] 活跃项目已清空")
        except Exception as e:
            logger.warning(f"[ProjectManager] 清空失败: {e}")


def format_status(project_data: Optional[Dict]) -> str:
    """格式化活跃项目为自然语言状态描述。

    用于启动/恢复消息。

    Args:
        project_data: load() 返回的项目字典
    Returns:
        格式化的状态文本
    """
    if not project_data:
        return "当前没有指定项目。请发送「推进 <项目名>」来开始研究。"

    name = project_data.get("project_name", "未知")
    phase = project_data.get("current_phase", "")
    actions = project_data.get("next_actions", [])

    parts = [f"当前项目：{name}"]
    if phase:
        parts.append(f"当前阶段：{phase}")
    if actions:
        parts.append("下一步计划：")
        for i, a in enumerate(actions[:3], 1):
            parts.append(f"  {i}. {a}")
    else:
        parts.append("下一步：根据项目状态选择一个最小动作继续推进。")

    return "\n".join(parts)
