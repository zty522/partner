"""Project State — 自然语言状态管理。

所有项目状态存储为 .md 文件，Partner 只负责读写，不解析结构。
Hermes 可读取整个文件理解上下文。
"""

import os
import logging
import shutil
import time as _time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def copy_external_data_to_workspace(source_path: str, workspace: str = None, temp_dir: str = None) -> str:
    """将外部数据文件复制到实例工作目录。

    确保外部文件被隔离到实例专属的临时目录，避免跨实例污染。

    Args:
        source_path: 源文件路径
        workspace: 实例工作目录。为 None 时返回原路径。
        temp_dir: 实例内临时目录，默认 {workspace}/99_temp/inputs/

    Returns:
        副本的路径（如果已在 workspace 内，则返回原路径）。
    """
    effective_workspace = workspace
    if not effective_workspace:
        logger.warning("[DataCopy] 无 workspace，返回原始路径")
        return source_path

    if temp_dir is None:
        temp_dir = os.path.join(effective_workspace, "99_temp", "inputs")

    os.makedirs(temp_dir, exist_ok=True)

    # 只在路径不在 workspace 内时才复制
    if not source_path.startswith(effective_workspace):
        dest = os.path.join(temp_dir, os.path.basename(source_path))
        # 避免覆盖已有文件
        if os.path.exists(dest):
            base, ext = os.path.splitext(os.path.basename(source_path))
            dest = os.path.join(temp_dir, f"{base}_{int(_time.time())}{ext}")
        shutil.copy2(source_path, dest)
        logger.info(f"[DataCopy] 复制 {source_path} → {dest}")
        return dest

    return source_path  # 已经在 workspace 内，不需要复制


def get_project_dir(workspace: str, project_name: str) -> str:
    """获取项目状态目录。"""
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in project_name).strip()
    d = os.path.join(workspace, "20_records", "projects", safe_name)
    os.makedirs(d, exist_ok=True)
    return d


def get_state_path(workspace: str, project_name: str) -> str:
    """获取项目状态文件路径。"""
    return os.path.join(get_project_dir(workspace, project_name), "state.md")


def get_active_path(workspace: str) -> str:
    """获取当前活跃项目标记文件路径。"""
    d = os.path.join(workspace, "20_records")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "active_project.txt")


def set_active(workspace: str, project_name: str):
    """设置当前活跃项目（自然语言一行）。"""
    path = get_active_path(workspace)
    with open(path, "w", encoding="utf-8") as f:
        f.write(project_name.strip() + "\n")
    logger.info(f"[State] 活跃项目已设置: {project_name}")


def get_active(workspace: str) -> Optional[str]:
    """读取当前活跃项目名。返回 None 表示无项目。"""
    path = get_active_path(workspace)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            name = f.readline().strip()
        return name if name else None
    except Exception:
        return None


def append_log(workspace: str, project_name: str, entry: str):
    """向项目日志追加一行自然语言记录。"""
    log_path = os.path.join(get_project_dir(workspace, project_name), "log.md")
    with open(log_path, "a", encoding="utf-8") as f:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        f.write(f"\n## [{ts}]\n{entry.strip()}\n")
    logger.info(f"[State] 已记录日志到 {project_name}")


def read_state_md(workspace: str, project_name: str) -> str:
    """读取项目完整状态 Markdown。"""
    path = get_state_path(workspace, project_name)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def write_state_md(workspace: str, project_name: str, content: str):
    """写入项目状态 Markdown（覆盖）。"""
    path = get_state_path(workspace, project_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"[State] 已更新状态文件: {project_name}")
