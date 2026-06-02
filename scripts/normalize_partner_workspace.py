#!/usr/bin/env python3
"""Normalize partner_workspace layout after multi-instance migration."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


WORKSPACE_ROOT = Path("/mnt/e/work/partner_workspace")
INSTANCES_DIR = WORKSPACE_ROOT / "instances"
LEGACY_ARCHIVE = WORKSPACE_ROOT / "_legacy_root_archive"
INSTANCE_ALLOWED = {"01", "03", "04"}
ROOT_SYSTEM_NAMES = {
    "instances",
    "global_config.json",
    "partner_config.json",
    "_legacy_root_archive",
    "README.md",
}
INSTANCE_KEEP_NAMES = {
    "00_config",
    "10_logs",
    "20_records",
    "99_temp",
    ".hermes",
    "dialogue",
    "knowledge",
    "logs",
    "state",
    "partner_config.json",
    "qq_config.json",
}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def ensure_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def active_project_name(instance_dir: Path) -> str:
    path = instance_dir / "20_records" / "active_project.txt"
    name = read_text(path).strip()
    return name or "未命名项目"


def ensure_link(link_path: Path, target: Path):
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.exists() or link_path.is_symlink():
        if link_path.is_symlink() and link_path.resolve() == target.resolve():
            return
        if link_path.is_dir() and not link_path.is_symlink():
            return
        if link_path.is_file() or link_path.is_symlink():
            link_path.unlink()
    try:
        relative = target.relative_to(link_path.parent)
        target_ref = str(relative)
    except ValueError:
        target_ref = str(target)
    try:
        link_path.symlink_to(target_ref)
    except OSError:
        pointer = link_path.with_suffix(link_path.suffix + ".link.txt") if link_path.suffix else Path(str(link_path) + ".link.txt")
        ensure_text(pointer, str(target) + "\n")


def safe_project_dir(instance_dir: Path, project_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in project_name).strip() or "未命名项目"
    d = instance_dir / "20_records" / "projects" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_instance_overview(instance_dir: Path):
    active_name = active_project_name(instance_dir)
    projects_dir = instance_dir / "20_records" / "projects"
    project_dirs = sorted([p for p in projects_dir.iterdir() if p.is_dir()]) if projects_dir.exists() else []
    lines = [
        f"# {instance_dir.name} 实例总览",
        "",
        f"- 当前活跃项目：{active_name}",
        f"- 项目目录数：{len(project_dirs)}",
        "",
        "## 目录说明",
        "- `00_config/`: 配置",
        "- `10_logs/`: 运行日志",
        "- `20_records/projects/<项目>/`: 每个项目自己的工作目录",
        "- `state/`: 队列、上下文、去重状态",
        "- `logs/`: 对接 Hermes / QQ 的专用日志",
        "",
        "## 项目列表",
    ]
    for project_dir in project_dirs:
        lines.append(f"- `{project_dir.name}`")
    ensure_text(instance_dir / "README.md", "\n".join(lines) + "\n")


def build_user_and_system_views(instance_dir: Path):
    active_name = active_project_name(instance_dir)
    active_dir = safe_project_dir(instance_dir, active_name)

    system_dir = instance_dir / "system"
    user_dir = instance_dir / "user"
    user_projects_dir = user_dir / "projects"
    system_dir.mkdir(parents=True, exist_ok=True)
    user_projects_dir.mkdir(parents=True, exist_ok=True)

    system_readme = [
        f"# {instance_dir.name} system",
        "",
        "这个目录给 Partner 自己运行用。",
        "",
        "建议优先看这些入口：",
        "- `config/` 配置",
        "- `runtime_logs/` 运行日志",
        "- `runtime_state/` 队列、上下文、去重状态",
        "- `project_store/` 项目原始存储",
    ]
    ensure_text(system_dir / "README.md", "\n".join(system_readme) + "\n")

    ensure_link(system_dir / "config", instance_dir / "00_config")
    ensure_link(system_dir / "runtime_logs", instance_dir / "10_logs")
    ensure_link(system_dir / "runtime_state", instance_dir / "state")
    ensure_link(system_dir / "bridge_logs", instance_dir / "logs")
    ensure_link(system_dir / "project_store", instance_dir / "20_records" / "projects")

    user_readme = [
        f"# {instance_dir.name} user",
        "",
        "这个目录给用户阅读。",
        "",
        "建议优先看：",
        "- `current_project/` 当前项目",
        "- `reports/` 自动生成的阶段汇报 PPT/PDF",
        "- `projects/` 每个项目的可读视图",
        "- 每个项目里的 `project_overview.md`、`state.md`、`exploration_log.md`、`reports/`",
    ]
    ensure_text(user_dir / "README.md", "\n".join(user_readme) + "\n")
    ensure_link(user_dir / "current_project", active_dir)

    projects_dir = instance_dir / "20_records" / "projects"
    for project_dir in sorted([p for p in projects_dir.iterdir() if p.is_dir()]):
        if project_dir.name.startswith("_archived"):
            continue
        ensure_link(user_projects_dir / project_dir.name, project_dir)


def ensure_project_files(project_dir: Path):
    state_path = project_dir / "state.md"
    if not state_path.exists():
        ensure_text(state_path, f"# 项目：{project_dir.name}\n")

    log_path = project_dir / "log.md"
    exploration_path = project_dir / "exploration_log.md"
    if log_path.exists() and not exploration_path.exists():
        shutil.copy2(log_path, exploration_path)
    elif not exploration_path.exists():
        ensure_text(exploration_path, f"# {project_dir.name} 探索过程记录\n")

    overview = [
        f"# {project_dir.name}",
        "",
        "## 文件说明",
        "- `state.md`: 当前项目状态摘要",
        "- `exploration_log.md`: 用自然语言记录探索过程",
        "- `log.md`: 原始执行日志（保留兼容）",
        "- `reports/`: 阶段汇报 PPT/PDF",
    ]
    other_files = sorted([p.name for p in project_dir.iterdir() if p.is_file() and p.name not in {"project_overview.md"}])
    if other_files:
        overview.extend(["", "## 当前文件", *[f"- `{name}`" for name in other_files]])
    ensure_text(project_dir / "project_overview.md", "\n".join(overview) + "\n")


def archive_legacy_root_items():
    LEGACY_ARCHIVE.mkdir(parents=True, exist_ok=True)
    for path in WORKSPACE_ROOT.iterdir():
        if path.name in ROOT_SYSTEM_NAMES:
            continue
        target = LEGACY_ARCHIVE / path.name
        if target.exists():
            continue
        shutil.move(str(path), str(target))


def normalize_instance(instance_dir: Path):
    active_name = active_project_name(instance_dir)
    active_dir = safe_project_dir(instance_dir, active_name)

    workspace_dir = active_dir / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    materials_dir = active_dir / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)

    for child in list(instance_dir.iterdir()):
        if child.name in INSTANCE_KEEP_NAMES:
            continue
        if child.name == "README.md":
            continue
        if child.is_file():
            target_dir = materials_dir if child.suffix.lower() in {".pdf", ".html", ".txt"} else workspace_dir
            shutil.move(str(child), str(target_dir / child.name))
        elif child.is_dir():
            # Keep legacy unknown directories, but move them under the active project workspace.
            shutil.move(str(child), str(workspace_dir / child.name))

    projects_dir = instance_dir / "20_records" / "projects"
    archived_generic = projects_dir / "_archived_generic"
    archived_aliases = projects_dir / "_archived_aliases"
    archived_generic.mkdir(parents=True, exist_ok=True)
    archived_aliases.mkdir(parents=True, exist_ok=True)
    for project_dir in list(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        if project_dir.name in {"_archived_generic", "_archived_aliases", active_dir.name}:
            ensure_project_files(project_dir)
            continue
        if any(
            token in project_dir.name
            for token in [
                "你自己决定",
                "当前项目优化方向",
                "最新研究进展",
                "你不是在做",
                "现在在干嘛",
                "可以，你就按照这个继续就行",
            ]
        ):
            target = archived_generic / project_dir.name
            if not target.exists():
                shutil.move(str(project_dir), str(target))
            continue
        if project_dir.name in {
            "推进年龄预测项目",
            "年龄预测项目（转录组学）",
            "年龄预测项目_转录组学_",
            "推进鲍曼不动杆菌分子生成",
            "推进鲍曼不动杆菌分子生成项目",
            "自由探索_agent_前沿文献",
            "自由探索_agent前沿文献",
            "自由探索agent最前沿的文献进行学习",
        }:
            target = archived_aliases / project_dir.name
            if not target.exists():
                shutil.move(str(project_dir), str(target))
            continue
        ensure_project_files(project_dir)

    ensure_project_files(active_dir)
    write_instance_overview(instance_dir)
    build_user_and_system_views(instance_dir)


def write_root_readme():
    instances = sorted([p.name for p in INSTANCES_DIR.iterdir() if p.is_dir() and p.name in INSTANCE_ALLOWED])
    lines = [
        "# partner_workspace",
        "",
        "## 目录结构",
        "- `instances/<id>/`: 每个 Partner 实例自己的完整工作区",
        "- `_legacy_root_archive/`: 旧单实例时代残留文件的归档",
        "- `global_config.json`: 多实例管理配置",
        "- `instances/<id>/user/`: 给用户看的入口",
        "- `instances/<id>/system/`: 给 Partner 运行用的入口",
        "",
        "## 当前实例",
    ]
    lines.extend([f"- `{name}`" for name in instances])
    ensure_text(WORKSPACE_ROOT / "README.md", "\n".join(lines) + "\n")


def rename_default_instance():
    old_dir = INSTANCES_DIR / "default"
    new_dir = INSTANCES_DIR / "01"
    if old_dir.exists() and not new_dir.exists():
        old_dir.rename(new_dir)

    cfg_path = WORKSPACE_ROOT / "global_config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        instances = cfg.get("instances", {})
        if "default" in instances and "01" not in instances:
            instances["01"] = instances.pop("default")
        if "01" in instances:
            instances["01"]["working_dir"] = str(new_dir)
        cfg["default_instance"] = "01"
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    for path in [new_dir / "partner_config.json", new_dir / "00_config" / "partner_config.json"]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        text = text.replace("/instances/default", "/instances/01")
        path.write_text(text, encoding="utf-8")


def main():
    rename_default_instance()
    archive_legacy_root_items()

    for instance_dir in list(INSTANCES_DIR.iterdir()):
        if not instance_dir.is_dir():
            continue
        if instance_dir.name not in INSTANCE_ALLOWED:
            shutil.move(str(instance_dir), str(LEGACY_ARCHIVE / f"instance_{instance_dir.name}"))
            continue
        normalize_instance(instance_dir)

    write_root_readme()


if __name__ == "__main__":
    main()
