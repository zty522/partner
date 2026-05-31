"""Project State — 自然语言状态管理。

所有项目状态存储为 .md 文件，Partner 只负责读写，不解析结构。
Hermes 可读取整个文件理解上下文。
"""

import os
import logging
import re
import shutil
import time as _time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

GENERIC_PROJECT_NAMES = {
    "",
    "你自己决定",
    "当前项目优化方向",
    "最新研究进展",
    "推进研究项目",
    "project_knowledge",
    "推进",
    "继续",
    "自由",
    "开始",
    "运行",
    "项目",
    "研究",
    "任务",
    "继续推进",
    "继续做",
}

LEADING_ACTION_PREFIXES = (
    "继续推进",
    "继续做",
    "继续",
    "推进",
    "自由探索",
    "自由",
    "开始",
)


def _clean_project_name(project_name: str) -> str:
    name = re.sub(r"\s+", " ", (project_name or "")).strip()
    name = name.strip("，。！？,.!?:：;；、/\\-_=+")
    return name


def simplify_project_query(project_name: str) -> str:
    """Strip directive prefixes and keep the subject of the project."""
    name = _clean_project_name(project_name)
    for prefix in LEADING_ACTION_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            stripped = _clean_project_name(name[len(prefix):])
            if stripped:
                return stripped
    return name


def is_generic_project_name(project_name: str) -> bool:
    """Return True when a project label is too generic to anchor replies."""
    name = _clean_project_name(project_name)
    if not name:
        return True
    if any(token in name for token in ("你自己决定", "你可以自由探索", "甚至编写代码运行")):
        return True
    if name in GENERIC_PROJECT_NAMES:
        return True
    if len(name) <= 2:
        return True
    if name.startswith(("推进", "继续", "自由")) and len(name) <= 4:
        return True
    return False


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _clip_project_title(name: str) -> str:
    name = _clean_project_name(name)
    name = re.split(r"[。；;，,\n]", name, maxsplit=1)[0].strip()
    return _clean_project_name(name)


def _extract_state_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# 项目："):
            return _clip_project_title(stripped.split("：", 1)[1])
        if stripped.startswith("项目启动："):
            return _clip_project_title(stripped.split("：", 1)[1])
        if stripped.startswith("项目 ") and "：" in stripped:
            left = stripped[3:].split("：", 1)[0].strip()
            if "/" in left:
                left = os.path.basename(left.rstrip("/")) or left
            return _clip_project_title(left)
    return ""


def resolve_project_name(workspace: str, preferred_name: Optional[str] = None) -> Optional[str]:
    """Resolve a stable, user-facing project name from workspace state."""
    preferred = simplify_project_query(preferred_name or "")
    projects_dir = os.path.join(workspace, "20_records", "projects")

    candidates = []
    if os.path.isdir(projects_dir):
        for dirname in os.listdir(projects_dir):
            path = os.path.join(projects_dir, dirname)
            if not os.path.isdir(path):
                continue
            state_path = os.path.join(path, "state.md")
            log_path = os.path.join(path, "log.md")
            state_text = _read_text(state_path)
            state_title = _extract_state_title(state_text)
            label = state_title or _clean_project_name(dirname)
            if is_generic_project_name(label):
                continue
            best_path = state_path if os.path.exists(state_path) else log_path
            if not os.path.exists(best_path):
                continue
            score = os.path.getmtime(best_path)
            combined = f"{dirname}\n{state_title}\n{state_text[:400]}"
            if preferred and preferred in combined:
                score += 10_000_000
            if preferred and any(token and token in combined for token in re.split(r"[\s,，。/]+", preferred) if len(token) >= 2):
                score += 5_000_000
            candidates.append((score, label))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    if preferred and not is_generic_project_name(preferred):
        return preferred
    return None


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
    project_name = resolve_project_name(workspace, project_name) or _clean_project_name(project_name) or "未命名项目"
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in project_name).strip()
    d = os.path.join(workspace, "20_records", "projects", safe_name)
    os.makedirs(d, exist_ok=True)
    _ensure_project_workspace_files(d, project_name)
    return d


def _ensure_project_workspace_files(project_dir: str, project_name: str):
    """Ensure every project folder has readable state and exploration log files."""
    state_path = os.path.join(project_dir, "state.md")
    if not os.path.exists(state_path):
        with open(state_path, "w", encoding="utf-8") as f:
            f.write(f"# 项目：{project_name}\n")

    exploration_path = os.path.join(project_dir, "exploration_log.md")
    legacy_log_path = os.path.join(project_dir, "log.md")
    if os.path.exists(legacy_log_path) and not os.path.exists(exploration_path):
        shutil.copy2(legacy_log_path, exploration_path)
    elif not os.path.exists(exploration_path):
        with open(exploration_path, "w", encoding="utf-8") as f:
            f.write(f"# {project_name} 探索过程记录\n")


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
    project_name = resolve_project_name(workspace, project_name) or _clean_project_name(project_name) or "未命名项目"
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
        return resolve_project_name(workspace, name) or (name if name else None)
    except Exception:
        return None


def append_log(workspace: str, project_name: str, entry: str):
    """向项目日志追加一行自然语言记录。"""
    project_dir = get_project_dir(workspace, project_name)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"\n## [{ts}]\n{entry.strip()}\n"
    for filename in ("log.md", "exploration_log.md"):
        log_path = os.path.join(project_dir, filename)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(block)
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
