"""Canonical workspace layout helpers.

The runtime keeps legacy paths readable, but all new writes should go through
this module so instance-private files, shared assets, and external content do
not drift into ad hoc folders.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable


LAYOUT_VERSION = 1


def safe_name(value: str, fallback: str = "item") -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = text.strip(" .。/\\")
    if not text:
        text = fallback
    return re.sub(r"[^\w\u4e00-\u9fff .-]+", "_", text).strip(" ._") or fallback


def workspace_root_from_instance(instance_workspace: str) -> str:
    path = Path(instance_workspace).expanduser()
    if path.parent.name == "instances":
        return str(path.parent.parent)
    return str(path)


def is_instance_workspace(path: str) -> bool:
    p = Path(path)
    return p.parent.name == "instances" or (p / "state").exists()


def instance_dir(root_or_instance: str, partner_id: str = "") -> str:
    root = Path(root_or_instance).expanduser()
    if partner_id and (root / "instances").exists():
        return str(root / "instances" / partner_id)
    return str(root)


def history_dir(instance_workspace: str) -> str:
    """对话历史存到 dialogue/。"""
    d = os.path.join(instance_workspace, "dialogue")
    os.makedirs(d, exist_ok=True)
    return d


def qq_history_path(instance_workspace: str) -> str:
    return os.path.join(history_dir(instance_workspace), "qq_chat_history.jsonl")


def dialog_history_path(instance_workspace: str) -> str:
    return os.path.join(history_dir(instance_workspace), "dialog_history.jsonl")


def history_paths(instance_workspace: str, name: str) -> list[str]:
    """Return canonical path first, then legacy mirrors for compatibility."""
    if name not in {"qq_chat_history.jsonl", "dialog_history.jsonl"}:
        raise ValueError(f"unsupported history file: {name}")
    canonical = os.path.join(history_dir(instance_workspace), name)
    legacy = os.path.join(instance_workspace, "state", name)
    return [canonical, legacy]


def append_history(instance_workspace: str, row: dict, names: Iterable[str]):
    for name in names:
        for path in history_paths(instance_workspace, name):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def shared_files_dir(workspace_root: str) -> str:
    """Shared file storage at workspace_root/files/ — used before a project exists."""
    root = workspace_root_from_instance(workspace_root)
    return _ensure(root, "files")


def uploads_dir(workspace_root: str) -> str:
    """Upload storage at workspace_root/files/uploads/."""
    root = workspace_root_from_instance(workspace_root)
    return _ensure(root, "files", "uploads")


def incoming_dir(workspace_root: str) -> str:
    """Incoming file storage at workspace_root/files/incoming/."""
    root = workspace_root_from_instance(workspace_root)
    return _ensure(root, "files", "incoming")


def outgoing_dir(workspace_root: str) -> str:
    """Outgoing file storage at workspace_root/files/outgoing/."""
    root = workspace_root_from_instance(workspace_root)
    return _ensure(root, "files", "outgoing")


def working_files_dir(instance_workspace: str, project: str = "") -> str:
    if project:
        return _ensure(project_dir(instance_workspace, project), "files")
    root = workspace_root_from_instance(instance_workspace)
    return _ensure(root, "files", "working")


def outputs_dir(instance_workspace: str, project: str = "") -> str:
    if project:
        return _ensure(project_dir(instance_workspace, project), "outputs")
    root = workspace_root_from_instance(instance_workspace)
    return _ensure(root, "files", "outputs")


def projects_dir(instance_workspace: str) -> str:
    """Legacy: projects now live in shared_projects/ pool. Keep for backward compat."""
    root = workspace_root_from_instance(instance_workspace)
    return _ensure(root, "shared_projects")


def project_dir(instance_workspace: str, project_name: str) -> str:
    """Return the project directory under shared_projects/.

    All projects live in a single shared pool. No per-instance hash suffix.
    """
    from .project_registry import shared_projects_base  # avoid circular import
    base = shared_projects_base(instance_workspace)
    safe = safe_name(project_name, "project")
    return _ensure(str(base), safe)


def legacy_project_dirs(instance_workspace: str, project_name: str) -> list[str]:
    """Legacy: instance-local project dirs no longer exist.

    Return the shared_projects/ path as the only candidate.
    """
    return [project_dir(instance_workspace, project_name)]


def common_dir(root_or_instance: str) -> str:
    """Legacy: common/ was merged into shared_projects/. Return shared_projects/."""
    root = workspace_root_from_instance(root_or_instance)
    return _ensure(root, "shared_projects")


def common_projects_dir(root_or_instance: str) -> str:
    """Legacy: all projects are in shared_projects/ now."""
    root = workspace_root_from_instance(root_or_instance)
    return _ensure(root, "shared_projects")


def common_files_dir(root_or_instance: str) -> str:
    """Legacy: common/files merged into shared_projects/."""
    root = workspace_root_from_instance(root_or_instance)
    return _ensure(root, "shared_projects")


def external_dir(root_or_instance: str) -> str:
    root = workspace_root_from_instance(root_or_instance)
    return _ensure(root, "external")


def external_content_dir(root_or_instance: str) -> str:
    return _ensure(external_dir(root_or_instance), "content")


def ensure_instance_layout(instance_workspace: str):
    for parts in (
        ("state",),
        ("system",),
        ("dialogue",),
    ):
        _ensure(instance_workspace, *parts)


def _seed_history_from_legacy(instance_workspace: str):
    for name in ("qq_chat_history.jsonl", "dialog_history.jsonl"):
        canonical = os.path.join(history_dir(instance_workspace), name)
        legacy = os.path.join(instance_workspace, "state", name)
        if os.path.exists(canonical) or not os.path.exists(legacy):
            continue
        try:
            with open(legacy, "r", encoding="utf-8", errors="replace") as src:
                text = src.read()
            with open(canonical, "w", encoding="utf-8") as dst:
                dst.write(text)
        except Exception:
            pass


def _ensure(*parts: str) -> str:
    path = os.path.join(*(str(part) for part in parts if str(part)))
    os.makedirs(path, exist_ok=True)
    return path
