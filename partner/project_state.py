"""Project State — 自然语言状态管理。

所有项目状态存储为 .md 文件，Partner 只负责读写，不解析结构。
Hermes 可读取整个文件理解上下文。
"""

import json
import os
import logging
import re
import shutil
import time as _time
from datetime import datetime
from typing import Optional

from .workspace_layout import (
    ensure_instance_layout,
    legacy_project_dirs,
    project_dir as canonical_project_dir,
    working_files_dir,
)

logger = logging.getLogger(__name__)

GENERIC_PROJECT_NAMES = {
    "",
    "当前项目",
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


def _collapse_repeated_tokens(text: str) -> str:
    if not text:
        return ""
    for token in ("最终", "完成"):
        text = re.sub(f"(?:{re.escape(token)})" + r"{4,}", token, text)
    text = re.sub(r"(_final){4,}", "_final", text, flags=re.I)
    text = re.sub(r"(final){4,}", "final", text, flags=re.I)
    return text


def _looks_like_brief_noise(text: str) -> bool:
    """Return True for bookkeeping that should not live in the hot prompt brief."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    hard_noise_patterns = [
        r"\d+\s*字节",
        r"\bbytes?\b",
        r"\blines?\b",
        r"\bfiles?\b",
        r"字符内容",
        r"总文件数",
        r"文件数",
        r"文件膨胀",
        r"Verified文件",
        r"Hypothesis文件",
        r"Inferred文件",
        r"其他文件",
        r"目录结构",
        r"关键目录",
        r"文件完整",
        r"文件齐全",
        r"文件体系",
        r"现有文件",
        r"当前相关文件",
        r"路径验证状态",
        r"path_reality_check",
        r"system:",
        r"完成态/等待态空转",
        r"[\w.-]+\.md",
        r"[\w.-]+\.py",
        r"/(?:mnt|home|tmp)/",
        r"\bdata,\s*scripts\b",
        r"\btree_search\b",
    ]
    if any(re.search(pattern, stripped, re.I) for pattern in hard_noise_patterns):
        return True
    noise_patterns = [
        r"更新.*\.md",
        r"创建.*\.md",
        r"写入.*\.md",
        r"产出.*\.md",
    ]
    if not any(re.search(pattern, stripped, re.I) for pattern in noise_patterns):
        return False
    content_markers = (
        "发现",
        "结论",
        "验证",
        "审计",
        "风险",
        "瓶颈",
        "偏差",
        "失败",
        "提升",
        "下降",
        "对比",
        "差异",
        "不可信",
        "缺失",
    )
    return not any(marker in stripped for marker in content_markers)


def _sanitize_brief_value(value: str, *, fallback: str = "") -> str:
    """Keep research content, remove file/path bookkeeping from one brief field."""
    value = _collapse_repeated_tokens(value or "").strip()
    if not value:
        return fallback
    cleaned_lines = []
    for raw in value.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _looks_like_brief_noise(line):
            continue
        line = re.sub(r"产出\s*[\w.-]+\.md（?\d+\s*字节）?[，,]?", "", line)
        line = re.sub(r"更新\s*[\w.-]+\.md（?\d+\s*字节）?[，,]?", "", line)
        line = re.sub(r"写入\s*[\w.-]+\.md（?\d+\s*字节）?[，,]?", "", line)
        line = re.sub(r"创建\s*[\w.-]+\.md（?\d+\s*字节）?[，,]?", "", line)
        line = re.sub(r"[\w.-]+\.(?:md|py)", "", line)
        line = re.sub(r"/(?:mnt|home|tmp)/[^\s，,；;]+", "", line)
        line = re.sub(r"\d+\s*字节", "", line)
        line = re.sub(r"\([^)]*\b(?:bytes?|files?|lines?)\b[^)]*\)", "", line, flags=re.I)
        line = re.sub(r"\s+", " ", line).strip(" ，,；;")
        if line and not _looks_like_brief_noise(line):
            cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned or fallback


def sanitize_project_brief_text(text: str, project_name: str = "") -> str:
    """Compact project_brief.md into prompt-safe research memory.

    The brief is injected into every project round, so it must contain current
    research state instead of filesystem bookkeeping. Detailed files remain in
    trace/exploration logs and memory_index.json.
    """
    text = _collapse_repeated_tokens(text or "").strip()
    if not text:
        return f"# {project_name or '项目'} 项目简报\n"

    lines = text.splitlines()
    out = []
    current_heading = ""
    buffer = []
    drop_section = False

    def flush():
        nonlocal buffer, current_heading, drop_section
        if current_heading:
            if not drop_section:
                value = _sanitize_brief_value("\n".join(buffer), fallback="待补充。")
                if value and value != "待补充。" or current_heading in {
                    "## 项目目标",
                    "## 当前主线",
                    "## 当前最佳结果",
                    "## 当前瓶颈",
                    "## 最近有效进展",
                    "## 已证明不行的路线",
                    "## 下一步最小动作",
                    "## 禁止跑偏方向",
                    "## 项目边界",
                    "## 核心发现",
                }:
                    out.append(current_heading)
                    out.append(value)
                    out.append("")
        buffer = []
        drop_section = False

    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            flush()
            out.append(stripped)
            out.append("")
            current_heading = ""
            continue
        if stripped.startswith("## "):
            flush()
            current_heading = stripped
            drop_section = bool(re.search(
                r"(路径验证状态|path_reality_check|证据索引|文件索引|目录|当前相关文件|最新进展（?Step)",
                stripped,
                re.I,
            ))
            continue
        if current_heading:
            buffer.append(raw)
        elif stripped and not _looks_like_brief_noise(stripped):
            out.append(stripped)

    flush()
    cleaned = "\n".join(out).strip() + "\n"
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


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
    project_roots = [
        os.path.join(workspace, "projects"),
        os.path.join(workspace, "projects", "projects"),
    ]

    candidates = []
    seen_paths: set[str] = set()
    for projects_dir in project_roots:
        if not os.path.isdir(projects_dir):
            continue
        for dirname in os.listdir(projects_dir):
            path = os.path.join(projects_dir, dirname)
            norm_path = os.path.abspath(path)
            if not os.path.isdir(path) or norm_path in seen_paths:
                continue
            seen_paths.add(norm_path)
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
            matched_preferred = False
            if preferred and preferred in combined:
                score += 10_000_000
                matched_preferred = True
            if preferred and any(token and token in combined for token in re.split(r"[\s,，。/]+", preferred) if len(token) >= 2):
                score += 5_000_000
                matched_preferred = True
            candidates.append((score, label, matched_preferred))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        if preferred:
            for _, label, matched_preferred in candidates:
                if matched_preferred:
                    return label
            if not is_generic_project_name(preferred):
                return preferred
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
        temp_dir: 实例内工作文件目录，默认 {workspace}/files/working/

    Returns:
        副本的路径（如果已在 workspace 内，则返回原路径）。
    """
    effective_workspace = workspace
    if not effective_workspace:
        logger.warning("[DataCopy] 无 workspace，返回原始路径")
        return source_path

    if temp_dir is None:
        ensure_instance_layout(effective_workspace)
        temp_dir = working_files_dir(effective_workspace)

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
    ensure_instance_layout(workspace)
    d = canonical_project_dir(workspace, project_name)
    _copy_legacy_project_seed(workspace, project_name, d)
    _ensure_project_workspace_files(d, project_name)
    return d


def _copy_legacy_project_seed(workspace: str, project_name: str, target_dir: str):
    """Seed canonical project folders from legacy records without moving user files."""
    marker = os.path.join(target_dir, ".legacy_seeded")
    if os.path.exists(marker):
        return
    key_files = {
        "state.md",
        "exploration_log.md",
        "log.md",
        "project_brief.md",
        "project_contract.json",
        "memory_index.json",
        "trace_detail.md",
    }
    copied = False
    for legacy_dir in legacy_project_dirs(workspace, project_name):
        if not os.path.isdir(legacy_dir) or os.path.abspath(legacy_dir) == os.path.abspath(target_dir):
            continue
        for name in key_files:
            src = os.path.join(legacy_dir, name)
            dst = os.path.join(target_dir, name)
            if os.path.isfile(src) and not os.path.exists(dst):
                try:
                    shutil.copy2(src, dst)
                    copied = True
                except Exception:
                    pass
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(datetime.now().isoformat() + ("\nseeded_from_legacy=true\n" if copied else "\n"))
    except Exception:
        pass


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

    for subdir in ("outputs", "papers", "reports"):
        os.makedirs(os.path.join(project_dir, subdir), exist_ok=True)

    brief_path = os.path.join(project_dir, "project_brief.md")
    if not os.path.exists(brief_path):
        with open(brief_path, "w", encoding="utf-8") as f:
            f.write(
                f"# {project_name} 项目简报\n\n"
                "## 项目目标\n待补充。\n\n"
                "## 当前主线\n待补充。\n\n"
                "## 当前最佳结果\n待补充。\n\n"
                "## 当前瓶颈\n待补充。\n\n"
                "## 最近有效进展\n待补充。\n\n"
                "## 已证明不行的路线\n待补充。\n\n"
                "## 下一步最小动作\n待补充。\n\n"
                "## 禁止跑偏方向\n待补充。\n"
            )

    contract_path = os.path.join(project_dir, "project_contract.json")
    if not os.path.exists(contract_path):
        with open(contract_path, "w", encoding="utf-8") as f:
            json.dump({
                "project_name": project_name,
                "current_goal": "",
                "allowed_scope": [],
                "forbidden_scope": [],
                "current_mainline": "",
                "latest_valid_state": "",
                "source_roots": [],
                "allowed_data_types": [],
                "forbidden_evidence_patterns": [],
                "completion_criteria": [],
                "project_status": "active",
                "user_corrections": [],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }, f, ensure_ascii=False, indent=2)

    memory_index_path = os.path.join(project_dir, "memory_index.json")
    if not os.path.exists(memory_index_path):
        with open(memory_index_path, "w", encoding="utf-8") as f:
            json.dump({
                "project_name": project_name,
                "last_updated": datetime.now().isoformat(timespec="seconds"),
                "artifacts": [],
                "evidence": [],
                "open_questions": [],
            }, f, ensure_ascii=False, indent=2)

    # User-visible project journey files. These are intentionally separate from
    # internal trace/state files so users can see how Partner thinks, learns and
    # changes behavior over time.
    workspace = _workspace_from_project_dir(project_dir)
    if workspace:
        user_project_dir = os.path.join(workspace, "state", "user", "projects", safe_project_name(project_name))
        os.makedirs(user_project_dir, exist_ok=True)
        for filename, title in (
            ("research_journey.md", "Research Journey"),
            ("growth_journal.md", "Growth Journal"),
            ("habit_applications.md", "Habit Applications"),
            ("reflection_log.md", "Reflection Log"),
            ("breakthroughs.md", "Breakthroughs"),
            ("insight_log.md", "Insight Log"),
            ("mind_status.md", "Mind Status"),
        ):
            path = os.path.join(user_project_dir, filename)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"# {project_name} {title}\n\n")


def safe_project_name(project_name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", project_name or "project").strip("_") or "project"


def _workspace_from_project_dir(project_dir: str) -> str:
    parts = os.path.normpath(project_dir).split(os.sep)
    # New layout: .../shared_projects/<safe_name>/
    for idx, part in enumerate(parts):
        if part == "shared_projects":
            return os.sep.join(parts[:idx]) or os.sep
    # Legacy: .../projects/<name>_<hash>/ (instance-local, now obsolete)
    for idx, part in enumerate(parts):
        if part == "projects":
            if idx > 0 and parts[idx - 1] == "projects":
                return os.sep.join(parts[: idx - 1]) or os.sep
            return os.sep.join(parts[:idx]) or os.sep
    return ""


def get_state_path(workspace: str, project_name: str) -> str:
    """获取项目状态文件路径。"""
    return os.path.join(get_project_dir(workspace, project_name), "state.md")


def get_project_contract_path(workspace: str, project_name: str) -> str:
    return os.path.join(get_project_dir(workspace, project_name), "project_contract.json")


def get_project_brief_path(workspace: str, project_name: str) -> str:
    return os.path.join(get_project_dir(workspace, project_name), "project_brief.md")


def read_project_contract(workspace: str, project_name: str) -> dict:
    path = get_project_contract_path(workspace, project_name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_project_contract(workspace: str, project_name: str, contract: dict):
    path = get_project_contract_path(workspace, project_name)
    contract = dict(contract or {})
    contract.setdefault("project_name", resolve_project_name(workspace, project_name) or project_name)
    contract.setdefault("source_roots", [])
    contract.setdefault("allowed_data_types", [])
    contract.setdefault("forbidden_evidence_patterns", [])
    contract.setdefault("completion_criteria", [])
    contract.setdefault("project_status", "active")
    contract["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(contract, f, ensure_ascii=False, indent=2)
    try:
        from .project_registry import register_project

        register_project(
            workspace,
            contract.get("project_name") or project_name,
            status=str(contract.get("project_status") or "active"),
            reason=str(contract.get("status_reason") or ""),
            make_public=False,
        )
    except Exception:
        pass


def get_project_status(workspace: str, project_name: str) -> str:
    """Return active/cooling_down/done/waiting. Default active."""
    contract = read_project_contract(workspace, project_name)
    status = str(contract.get("project_status") or "").strip().lower()
    if status in {"active", "cooling_down", "done", "waiting"}:
        return status
    plan = _read_active_plan(workspace)
    status = str(plan.get("project_status") or plan.get("status") or "").strip().lower()
    if status in {"active", "cooling_down", "done", "waiting"}:
        return status
    return "active"


def set_project_status(workspace: str, project_name: str, status: str, reason: str = ""):
    status = (status or "active").strip().lower()
    if status not in {"active", "cooling_down", "done", "waiting"}:
        status = "active"
    now = datetime.now().isoformat(timespec="seconds")
    contract = read_project_contract(workspace, project_name)
    contract["project_status"] = status
    contract["status_reason"] = reason[:240]
    contract["updated_at"] = now
    write_project_contract(workspace, project_name, contract)
    plan = _read_active_plan(workspace)
    plan["project_status"] = status
    if status == "active":
        # Do not preserve stale lifecycle labels such as "waiting" after a
        # user-shared material wakes the project.  Several status surfaces read
        # active_plan.status directly, so keeping the old value makes a running
        # project look stuck.
        plan["status"] = "active"
    else:
        plan["status"] = status
    plan["last_heartbeat"] = now
    if reason:
        plan["heartbeat_summary"] = reason[:240]
    _write_active_plan(workspace, plan)
    try:
        from .project_registry import claim_project, register_project

        if status == "active":
            claim_project(workspace, project_name, reason=reason)
        else:
            register_project(workspace, project_name, status=status, reason=reason, make_public=False)
    except Exception:
        pass


def is_project_done_signal(parsed: dict) -> bool:
    text = "；".join(
        str(parsed.get(k, "") or "")
        for k in ("step_done", "next_action", "state_delta")
    )
    if re.search(r"(项目已完成|已完成|项目关闭|已关闭|无下一步|无需|等待新指令|NEXT:\s*无)", text):
        next_action = str(parsed.get("next_action", "") or "")
        return not next_action or re.search(r"(无|等待|无需|已完成|关闭)", next_action)
    return False


def read_project_brief(workspace: str, project_name: str, max_chars: int = 1800) -> str:
    path = get_project_brief_path(workspace, project_name)
    text = _read_text(path).strip()
    sanitized = sanitize_project_brief_text(text, project_name).strip()
    if sanitized and sanitized != text:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(sanitized.rstrip() + "\n")
            text = sanitized
        except OSError:
            text = sanitized
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def update_project_brief_from_contract(workspace: str, project_name: str, contract: dict):
    """Keep the human-readable hot brief aligned with strong project boundaries."""
    path = get_project_brief_path(workspace, project_name)
    existing = _read_text(path).strip() or f"# {project_name} 项目简报"
    last_raw = (contract.get("user_corrections") or [{}])[-1].get("text", "") if contract.get("user_corrections") else ""
    grounded = _ground_guardrail_to_raw_text({
        "raw_text": last_raw,
        "allowed_scope": contract.get("allowed_scope") or [],
        "forbidden_scope": contract.get("forbidden_scope") or [],
        "current_mainline": contract.get("current_mainline") or "",
    })
    mainline = _sanitize_brief_value(grounded.get("current_mainline") or "")
    goal = _sanitize_brief_value(contract.get("current_goal") or "")
    latest = _sanitize_brief_value(contract.get("latest_valid_state") or "")
    forbidden = grounded.get("forbidden_scope") or []

    def replace_section(text: str, heading: str, value: str) -> str:
        if not value:
            return text
        pattern = rf"(## {re.escape(heading)}\n)(.*?)(?=\n## |\Z)"
        replacement = rf"\1{value.strip()}\n"
        if re.search(pattern, text, flags=re.DOTALL):
            return re.sub(pattern, replacement, text, flags=re.DOTALL)
        return text.rstrip() + f"\n\n## {heading}\n{value.strip()}\n"

    updated = sanitize_project_brief_text(existing, project_name)
    updated = replace_section(updated, "项目目标", goal)
    updated = replace_section(updated, "当前主线", mainline)
    updated = replace_section(updated, "最近有效进展", latest)
    if forbidden:
        updated = replace_section(updated, "禁止跑偏方向", "；".join(str(x) for x in forbidden[:12]))
    updated = sanitize_project_brief_text(updated, project_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(updated.rstrip() + "\n")


def update_project_brief_from_round(workspace: str, project_name: str, parsed: dict):
    path = get_project_brief_path(workspace, project_name)
    existing = _read_text(path).strip() or f"# {project_name} 项目简报"
    step_done = _sanitize_brief_value(parsed.get("step_done") or "")
    findings = [
        _sanitize_brief_value(str(x).strip())
        for x in (parsed.get("findings") or [])
        if _sanitize_brief_value(str(x).strip())
    ]
    next_action = _sanitize_brief_value(parsed.get("next_action") or "")
    evidence = _sanitize_brief_value(parsed.get("evidence") or "")
    files = (parsed.get("files") or "").strip()

    def replace_section(text: str, heading: str, value: str) -> str:
        if not value:
            return text
        pattern = rf"(## {re.escape(heading)}\n)(.*?)(?=\n## |\Z)"
        replacement = rf"\1{value.strip()}\n"
        if re.search(pattern, text, flags=re.DOTALL):
            return re.sub(pattern, replacement, text, flags=re.DOTALL)
        return text.rstrip() + f"\n\n## {heading}\n{value.strip()}\n"

    updated = sanitize_project_brief_text(existing, project_name)
    if step_done:
        updated = replace_section(updated, "最近有效进展", step_done)
        round_text = "\n".join(
            [
                step_done,
                next_action,
                "；".join(findings),
                str(parsed.get("state_delta") or ""),
            ]
        )
        if (
            re.search(r"(当前最佳|新最佳|best|优于|显著提升|突破)", round_text, re.I)
            and not re.search(r"(合成|模拟|simulation|proxy|代理分数|不可比|hypothesis|可疑|不可信)", round_text, re.I)
        ):
            updated = replace_section(updated, "当前最佳结果", step_done)
    bottlenecks = [x for x in findings if re.search(r"(瓶颈|失败|没有提升|差于|不稳定|偏差|卡住|缺失|噪声)", x)]
    if bottlenecks:
        updated = replace_section(updated, "当前瓶颈", "；".join(bottlenecks[:2]))
    if next_action:
        updated = replace_section(updated, "下一步最小动作", next_action)
    if evidence and evidence.lower() != "hypothesis" and not _looks_like_brief_noise(evidence):
        updated = replace_section(updated, "证据索引", evidence)
    with open(path, "w", encoding="utf-8") as f:
        f.write(sanitize_project_brief_text(consolidate_markdown_text(updated), project_name).rstrip() + "\n")

    _update_memory_index(workspace, project_name, parsed)


def consolidate_markdown_text(text: str, max_chars: int = 12000) -> str:
    """Compact polluted hot files without losing the newest useful context."""
    text = _collapse_repeated_tokens(text or "")
    lines = []
    seen = set()
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if re.match(r"^[A-Z]?_records/projects/", stripped):
            continue
        key = stripped
        if stripped.startswith(("## 最新进展", "### ")):
            key = ""
        if key and key in seen and not stripped.startswith("## "):
            continue
        if key:
            seen.add(key)
        lines.append(line)
    compact = "\n".join(lines).strip() + "\n"
    if len(compact) <= max_chars:
        return compact
    head = "\n".join(compact.splitlines()[:30])
    tail = "\n".join(compact.splitlines()[-120:])
    return head.rstrip() + "\n\n## 中间历史已压缩\n\n" + tail.strip() + "\n"


def consolidate_project_files(workspace: str, project_name: str):
    project_dir = get_project_dir(workspace, project_name)
    contract = read_project_contract(workspace, project_name)
    if contract.get("user_corrections"):
        raw_text = (contract.get("user_corrections") or [{}])[-1].get("text", "")
        grounded = _ground_guardrail_to_raw_text({
            "raw_text": raw_text,
            "allowed_scope": contract.get("allowed_scope") or [],
            "forbidden_scope": contract.get("forbidden_scope") or [],
            "current_mainline": contract.get("current_mainline") or "",
        })
        changed = False
        for key in ("allowed_scope", "forbidden_scope", "current_mainline"):
            if grounded.get(key) != contract.get(key):
                contract[key] = grounded.get(key)
                changed = True
        if changed:
            write_project_contract(workspace, project_name, contract)
    for name, limit in (
        ("project_brief.md", 9000),
        ("exploration_log.md", 60000),
        ("state.md", 16000),
    ):
        path = os.path.join(project_dir, name)
        if not os.path.exists(path):
            continue
        text = _read_text(path)
        compact = consolidate_markdown_text(text, max_chars=limit)
        if name == "project_brief.md":
            compact = sanitize_project_brief_text(compact, project_name)
        if compact != text:
            with open(path, "w", encoding="utf-8") as f:
                f.write(compact)


def audit_project_round(workspace: str, project_name: str, parsed: dict) -> list[str]:
    """Evidence auditor driven by project_contract.json, not project-specific code."""
    issues = []
    contract = read_project_contract(workspace, project_name)
    evidence = str(parsed.get("evidence") or "").strip()
    done = str(parsed.get("step_done") or "").strip()
    state_delta = str(parsed.get("state_delta") or "").strip()
    combined = f"{done}\n{state_delta}"
    if re.search(r"(项目已完成|最终|最佳模型|MAE|R²|R2)", combined) and (
        not evidence or evidence.lower() == "hypothesis"
    ):
        issues.append("关键结论缺少 evidence 文件，不能标记为完成")

    forbidden_patterns = [str(x).strip() for x in contract.get("forbidden_evidence_patterns") or [] if str(x).strip()]
    for pattern in forbidden_patterns:
        try:
            matched = re.search(pattern, combined, re.I)
        except re.error:
            matched = pattern.lower() in combined.lower()
        if matched:
            issues.append(f"命中禁止证据/数据模式：{pattern[:80]}")
            break

    source_roots = [str(x).strip() for x in contract.get("source_roots") or [] if str(x).strip()]
    if source_roots and evidence and evidence.lower() != "hypothesis":
        evidence_paths = [x.strip() for x in re.split(r"[；;,\n]+", evidence) if x.strip().startswith("/")]
        for ev_path in evidence_paths:
            if not any(ev_path.startswith(root.rstrip("/") + "/") or ev_path == root.rstrip("/") for root in source_roots):
                issues.append(f"证据不在项目 source_roots 内：{ev_path[:120]}")
                break

    criteria = [str(x).strip() for x in contract.get("completion_criteria") or [] if str(x).strip()]
    if criteria and re.search(r"(项目已完成|已完成|最终)", combined):
        missing = [c for c in criteria if c not in combined and c not in evidence]
        if missing:
            issues.append(f"完成态缺少验收条件：{missing[0][:80]}")

    if evidence and evidence.lower() != "hypothesis":
        for item in re.split(r"[；;,\n]+", evidence):
            path = item.strip()
            if not path or re.match(r"^[A-Za-z_]+$", path):
                continue
            if path.startswith("/") and not os.path.exists(path):
                issues.append(f"证据文件不存在：{path[:120]}")
                break
    return issues


def audit_project_context(workspace: str, project_name: str, context_text: str) -> list[str]:
    """Audit historical context against project_contract.json.

    This is intentionally contract-driven. Partner should not know project-specific
    fake data names or forbidden topics in code; users/projects declare those in
    project_contract.json through normal chat or setup.
    """
    issues = []
    contract = read_project_contract(workspace, project_name)
    text = context_text or ""

    forbidden_patterns = [
        str(x).strip()
        for x in contract.get("forbidden_evidence_patterns") or []
        if str(x).strip()
    ]
    for pattern in forbidden_patterns:
        try:
            matched = re.search(pattern, text, re.I)
        except re.error:
            matched = pattern.lower() in text.lower()
        if matched:
            issues.append(f"历史上下文命中禁止证据/数据模式：{pattern[:80]}")

    criteria = [
        str(x).strip()
        for x in contract.get("completion_criteria") or []
        if str(x).strip()
    ]
    if criteria and re.search(r"(项目已完成|已完成|最终|best|current_best|最佳)", text, re.I):
        missing = [c for c in criteria if c not in text]
        if missing:
            issues.append(f"历史完成态缺少验收条件：{missing[0][:80]}")

    return issues


def _update_memory_index(workspace: str, project_name: str, parsed: dict):
    project_dir = get_project_dir(workspace, project_name)
    path = os.path.join(project_dir, "memory_index.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data.setdefault("project_name", project_name)
    data["last_updated"] = datetime.now().isoformat(timespec="seconds")
    artifacts = data.setdefault("artifacts", [])
    evidence_items = data.setdefault("evidence", [])
    files = (parsed.get("files") or "").strip()
    evidence = (parsed.get("evidence") or "").strip()
    if files and files.upper() != "EMPTY":
        artifacts.append({"time": data["last_updated"], "files": files, "action": parsed.get("action", "")})
    if evidence and evidence.lower() != "hypothesis":
        evidence_items.append({"time": data["last_updated"], "evidence": evidence, "action": parsed.get("action", "")})
    data["artifacts"] = artifacts[-50:]
    data["evidence"] = evidence_items[-50:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_active_path(workspace: str) -> str:
    """获取当前活跃项目标记文件路径。"""
    d = os.path.join(workspace, "projects")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "active_project.txt")


def set_active(workspace: str, project_name: str):
    """设置当前活跃项目（自然语言一行）。"""
    project_name = resolve_project_name(workspace, project_name) or _clean_project_name(project_name) or "未命名项目"
    path = get_active_path(workspace)
    with open(path, "w", encoding="utf-8") as f:
        f.write(project_name.strip() + "\n")
    logger.info(f"[State] 活跃项目已设置: {project_name}")
    try:
        from .project_registry import claim_project

        claim_project(workspace, project_name, reason="set_active")
    except Exception:
        pass


def clear_active(workspace: str, project_name: str = "") -> bool:
    """Clear active_project.txt when it points to a completed one-shot task."""
    path = get_active_path(workspace)
    if not os.path.exists(path):
        return False
    try:
        current = ""
        with open(path, "r", encoding="utf-8") as f:
            current = f.readline().strip()
        if project_name and current and current != project_name:
            return False
        os.remove(path)
        logger.info(f"[State] 活跃项目已清除: {current or project_name}")
        return True
    except OSError:
        return False


def get_active(workspace: str) -> Optional[str]:
    """读取当前活跃项目名。返回 None 表示无项目。"""
    path = get_active_path(workspace)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            name = f.readline().strip()
        if not name:
            return None
        return resolve_project_name(workspace, name) or (name if name else None)
    except Exception:
        return None


def recover_active_from_plan(workspace: str) -> Optional[str]:
    """Recover active_project.txt from active_plan.json when the marker is missing.

    This is intentionally generic: after a crash, migration, or workspace cleanup,
    active_plan.json may still contain the current research thread while
    active_project.txt is gone. WAKE_UP/CRON should not treat that as "no project".
    """
    current = get_active(workspace)

    plan = _read_active_plan(workspace)
    if not plan:
        if current and not is_generic_project_name(current):
            return current
        return None
    status = str(plan.get("status") or "").strip().lower()
    if status in {"", "idle", "waiting", "done", "completed", "closed"}:
        if current and not is_generic_project_name(current):
            current_status = get_project_status(workspace, current)
            if current_status not in {"waiting", "done"}:
                return current
        return None

    candidates = [
        str(plan.get("title") or "").strip(),
        str(plan.get("project") or "").strip(),
        str(plan.get("goal") or "").strip(),
    ]
    for candidate in candidates:
        name = _clip_project_title(candidate)
        name = simplify_project_query(name)
        if name and not is_generic_project_name(name):
            resolved = resolve_project_name(workspace, name) or name
            if current != resolved:
                set_active(workspace, resolved)
                logger.info(f"[State] 从 active_plan 恢复活跃项目: {resolved}")
            return get_active(workspace) or resolved
    if current and not is_generic_project_name(current):
        current_status = get_project_status(workspace, current)
        if current_status in {"waiting", "done", "archived"}:
            return None
        return current
    return None


def append_log(workspace: str, project_name: str, entry: str):
    """向项目日志追加记录。
    
    完整内容写入 trace_detail.md（溯源用），不再重复写入 log.md 和 exploration_log.md。
    摘要由 executor._append_log_summary 单独处理。
    """
    project_dir = get_project_dir(workspace, project_name)
    trace_path = os.path.join(project_dir, "trace_detail.md")
    block = f"\n{entry.strip()}\n"
    try:
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write(block)
    except OSError:
        # fallback: 如果 trace_detail.md 写入失败，写入 log.md
        log_path = os.path.join(project_dir, "log.md")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(block)
    logger.info(f"[State] 已记录日志到 {project_name}/trace_detail.md")


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


def _active_plan_path(workspace: str) -> str:
    state_dir = os.path.join(workspace, "state")
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, "active_plan.json")


def _read_active_plan(workspace: str) -> dict:
    path = _active_plan_path(workspace)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # Auto-cleanup: stale plans with no phases and old heartbeat
        status = data.get("status", "")
        phases = data.get("phases", [])
        if status in ("planning", "active") and not phases:
            hb = data.get("last_heartbeat", "")
            age = 9999
            if hb:
                try:
                    hb_dt = datetime.fromisoformat(hb)
                    now = datetime.now(hb_dt.tzinfo) if hb_dt.tzinfo else datetime.now()
                    age = (now - hb_dt).total_seconds()
                except Exception:
                    pass
            if age > 120:
                data["status"] = "idle"
                data["phases"] = []
                _write_active_plan(workspace, data)
        return data
    except Exception:
        return {}


def _write_active_plan(workspace: str, plan: dict):
    path = _active_plan_path(workspace)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def _split_scope_text(text: str) -> list[str]:
    items = []
    for part in re.split(r"[；;，,\n、/]+", text or ""):
        item = part.strip(" -。.!?？")
        if item and item not in items:
            items.append(item)
    return items


def _ground_scope_item_to_text(item: str, raw_text: str) -> str:
    """Return only the user-provided anchor for a long-term scope item."""
    item_norm = (item or "").strip()
    raw_norm = (raw_text or "").strip()
    if not item_norm or not raw_norm:
        return ""
    item_lc = item_norm.lower()
    raw_lc = raw_norm.lower()
    if item_lc in raw_lc:
        return item_norm
    ascii_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{1,}", item_norm)
    for tok in ascii_tokens:
        if tok.lower() in raw_lc:
            return tok
    return ""


def _ground_guardrail_to_raw_text(guardrail: dict) -> dict:
    raw_text = (guardrail.get("raw_text") or "").strip()
    if not raw_text:
        return guardrail
    allowed = []
    for item in guardrail.get("allowed_scope") or []:
        grounded = _ground_scope_item_to_text(str(item), raw_text)
        if grounded and grounded not in allowed:
            allowed.append(grounded)
    forbidden = []
    for item in guardrail.get("forbidden_scope") or []:
        grounded = _ground_scope_item_to_text(str(item), raw_text)
        if grounded and grounded not in forbidden:
            forbidden.append(grounded)
    mainline = (guardrail.get("current_mainline") or "").strip()
    if mainline and mainline.lower() not in raw_text.lower():
        mainline = "；".join(allowed[:4])
    return {
        **guardrail,
        "allowed_scope": allowed[:8],
        "forbidden_scope": forbidden[:12],
        "current_mainline": mainline,
    }


def _extract_scope_after(text: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        if marker in text:
            tail = text.split(marker, 1)[1]
            tail = re.split(r"(?:。|\n|；但|，但|；不过|，不过)", tail, maxsplit=1)[0]
            return tail.strip()
    return ""


def infer_guardrail_from_user_text(text: str) -> dict:
    """Extract a small correction anchor from natural language.

    This is intentionally conservative. The LLM can still enrich later, but
    these anchors make obvious corrections survive without a separate contract
    module or a heavy prompt.
    """
    raw = (text or "").strip()
    forbidden = []
    allowed = []

    forbidden_text = _extract_scope_after(raw, ("不是做", "不要做", "别做", "停止", "不要再做", "别再做"))
    if forbidden_text:
        forbidden.extend(_split_scope_text(forbidden_text))

    allowed_text = _extract_scope_after(raw, ("是做", "应该做", "要做", "聚焦", "回到"))
    if allowed_text:
        allowed_text = re.split(r"(?:不是做|不要做|别做|停止)", allowed_text, maxsplit=1)[0]
        allowed.extend(_split_scope_text(allowed_text))

    # Common correction form: "是做 A，不是做 B"
    match = re.search(r"是做(?P<allowed>.+?)(?:，|,|；|;|\s)*不是做(?P<forbidden>.+)", raw)
    if match:
        allowed.extend(_split_scope_text(match.group("allowed")))
        forbidden.extend(_split_scope_text(match.group("forbidden")))

    seen = set()
    allowed = [x for x in allowed if not (x in seen or seen.add(x))]
    seen = set()
    forbidden = [x for x in forbidden if not (x in seen or seen.add(x))]

    mainline = allowed[0] if allowed else ""
    return {
        "raw_text": raw,
        "allowed_scope": allowed[:8],
        "forbidden_scope": forbidden[:12],
        "current_mainline": mainline,
    }


def _format_guardrail_block(guardrail: dict) -> str:
    lines = ["## 用户纠偏 / 项目边界"]
    mainline = (guardrail.get("current_mainline") or "").strip()
    if mainline:
        lines.append(f"- 当前主线：{mainline}")
    allowed = guardrail.get("allowed_scope") or []
    forbidden = guardrail.get("forbidden_scope") or []
    if allowed:
        lines.append(f"- 允许方向：{'；'.join(allowed[:8])}")
    if forbidden:
        lines.append(f"- 禁止方向：{'；'.join(forbidden[:12])}")
    source_roots = guardrail.get("source_roots") or []
    if source_roots:
        lines.append(f"- 真实源目录/source_roots：{'；'.join(source_roots[:6])}")
    forbidden_patterns = guardrail.get("forbidden_evidence_patterns") or []
    if forbidden_patterns:
        lines.append(f"- 禁止证据/数据模式：{'；'.join(forbidden_patterns[:8])}")
    criteria = guardrail.get("completion_criteria") or []
    if criteria:
        lines.append(f"- 完成验收条件：{'；'.join(criteria[:8])}")
    raw = (guardrail.get("raw_text") or "").strip()
    if raw:
        lines.append(f"- 最近纠偏：{raw[:160]}")
    return "\n".join(lines).strip()


def _replace_guardrail_block(state_text: str, guardrail: dict) -> str:
    existing = (state_text or "").strip() or "# 项目：未命名项目"
    block = _format_guardrail_block(guardrail)
    pattern = r"\n*## 用户纠偏 / 项目边界\n(?:.*?)(?=\n## |\Z)"
    if re.search(pattern, existing, flags=re.DOTALL):
        return re.sub(pattern, "\n\n" + block, existing, flags=re.DOTALL).strip() + "\n"
    return existing.rstrip() + "\n\n" + block + "\n"


def record_project_guardrail(workspace: str, project_name: str, guardrail: dict) -> dict:
    """Persist correction anchors in state.md and active_plan.json."""
    project_name = resolve_project_name(workspace, project_name) or project_name or get_active(workspace) or "当前项目"
    normalized = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "raw_text": (guardrail.get("raw_text") or "").strip(),
        "allowed_scope": _split_scope_text("；".join(guardrail.get("allowed_scope") or [])),
        "forbidden_scope": _split_scope_text("；".join(guardrail.get("forbidden_scope") or [])),
        "current_mainline": (guardrail.get("current_mainline") or "").strip(),
        "source_roots": _split_scope_text("；".join(guardrail.get("source_roots") or [])),
        "forbidden_evidence_patterns": _split_scope_text("；".join(guardrail.get("forbidden_evidence_patterns") or [])),
        "completion_criteria": _split_scope_text("；".join(guardrail.get("completion_criteria") or [])),
    }
    state_text = read_state_md(workspace, project_name)
    write_state_md(workspace, project_name, _replace_guardrail_block(state_text, normalized))

    plan = _read_active_plan(workspace)
    plan["title"] = project_name or plan.get("title", "")
    plan["project_guardrails"] = normalized
    plan["last_heartbeat"] = normalized["updated_at"]
    if normalized["current_mainline"]:
        plan["heartbeat_summary"] = f"用户纠偏：当前主线改为 {normalized['current_mainline']}"
    _write_active_plan(workspace, plan)

    contract = read_project_contract(workspace, project_name)
    contract["project_name"] = project_name
    if normalized["current_mainline"]:
        contract["current_mainline"] = normalized["current_mainline"]
    if normalized["allowed_scope"]:
        contract["allowed_scope"] = normalized["allowed_scope"]
    if normalized["forbidden_scope"]:
        contract["forbidden_scope"] = normalized["forbidden_scope"]
    if normalized["source_roots"]:
        contract["source_roots"] = normalized["source_roots"]
    if normalized["forbidden_evidence_patterns"]:
        contract["forbidden_evidence_patterns"] = normalized["forbidden_evidence_patterns"]
    if normalized["completion_criteria"]:
        contract["completion_criteria"] = normalized["completion_criteria"]
    contract["project_status"] = "active"
    if normalized["raw_text"]:
        corrections = contract.get("user_corrections") or []
        corrections.append({
            "time": normalized["updated_at"],
            "text": normalized["raw_text"],
            "allowed_scope": normalized["allowed_scope"],
            "forbidden_scope": normalized["forbidden_scope"],
            "current_mainline": normalized["current_mainline"],
        })
        contract["user_corrections"] = corrections[-20:]
    write_project_contract(workspace, project_name, contract)
    update_project_brief_from_contract(workspace, project_name, contract)
    if normalized["forbidden_scope"]:
        prune_active_plan_forbidden_scope(workspace, normalized["forbidden_scope"])
    return normalized


def load_project_guardrail(workspace: str, project_name: str) -> dict:
    """Read correction anchors from active_plan.json, falling back to state.md."""
    contract = read_project_contract(workspace, project_name)
    contract_keys = (
        "allowed_scope",
        "forbidden_scope",
        "current_mainline",
        "source_roots",
        "allowed_data_types",
        "forbidden_evidence_patterns",
        "completion_criteria",
    )
    if contract and any(contract.get(k) for k in contract_keys):
        return _ground_guardrail_to_raw_text({
            "updated_at": contract.get("updated_at", ""),
            "raw_text": (contract.get("user_corrections") or [{}])[-1].get("text", "") if contract.get("user_corrections") else "",
            "allowed_scope": contract.get("allowed_scope") or [],
            "forbidden_scope": contract.get("forbidden_scope") or [],
            "current_mainline": contract.get("current_mainline") or "",
            "source_roots": contract.get("source_roots") or [],
            "allowed_data_types": contract.get("allowed_data_types") or [],
            "forbidden_evidence_patterns": contract.get("forbidden_evidence_patterns") or [],
            "completion_criteria": contract.get("completion_criteria") or [],
        })

    plan = _read_active_plan(workspace)
    guardrail = plan.get("project_guardrails") if isinstance(plan, dict) else None
    if isinstance(guardrail, dict):
        return _ground_guardrail_to_raw_text(guardrail)

    state = read_state_md(workspace, project_name)
    if "## 用户纠偏 / 项目边界" not in state:
        return {}
    block = state.split("## 用户纠偏 / 项目边界", 1)[1]
    block = re.split(r"\n## ", block, maxsplit=1)[0]
    result = {"allowed_scope": [], "forbidden_scope": [], "current_mainline": "", "raw_text": ""}
    for raw in block.splitlines():
        line = raw.strip().lstrip("- ").strip()
        if line.startswith("当前主线："):
            result["current_mainline"] = line.split("：", 1)[1].strip()
        elif line.startswith("允许方向："):
            result["allowed_scope"] = _split_scope_text(line.split("：", 1)[1])
        elif line.startswith("禁止方向："):
            result["forbidden_scope"] = _split_scope_text(line.split("：", 1)[1])
        elif line.startswith("最近纠偏："):
            result["raw_text"] = line.split("：", 1)[1].strip()
    return result


def format_project_guardrail_for_prompt(workspace: str, project_name: str) -> str:
    guardrail = load_project_guardrail(workspace, project_name)
    if not guardrail:
        return ""
    lines = ["项目边界（最高优先级，必须服从）："]
    mainline = (guardrail.get("current_mainline") or "").strip()
    if mainline:
        lines.append(f"- 当前主线：{mainline}")
    allowed = guardrail.get("allowed_scope") or []
    forbidden = guardrail.get("forbidden_scope") or []
    if allowed:
        lines.append(f"- 允许方向：{'；'.join(allowed[:8])}")
    if forbidden:
        lines.append(f"- 禁止方向：{'；'.join(forbidden[:12])}")
    source_roots = guardrail.get("source_roots") or []
    if source_roots:
        lines.append(f"- 真实源目录/source_roots：{'；'.join(source_roots[:6])}")
    forbidden_patterns = guardrail.get("forbidden_evidence_patterns") or []
    if forbidden_patterns:
        lines.append(f"- 禁止证据/数据模式：{'；'.join(forbidden_patterns[:8])}")
    criteria = guardrail.get("completion_criteria") or []
    if criteria:
        lines.append(f"- 完成验收条件：{'；'.join(criteria[:8])}")
    raw = (guardrail.get("raw_text") or "").strip()
    if raw:
        lines.append(f"- 用户原话：{raw[:160]}")
    return "\n".join(lines)


def guardrail_mentions(workspace: str, project_name: str, *keywords: str) -> bool:
    guardrail = load_project_guardrail(workspace, project_name)
    text = " ".join(
        [guardrail.get("current_mainline", ""), guardrail.get("raw_text", "")]
        + list(guardrail.get("allowed_scope") or [])
        + list(guardrail.get("forbidden_scope") or [])
    ).lower()
    return any((kw or "").lower() in text for kw in keywords)


def _merge_scope(existing: list, incoming: list) -> list:
    merged = []
    for item in list(existing or []) + list(incoming or []):
        text = str(item).strip()
        if text and text not in merged:
            merged.append(text)
    return merged[:20]


def prune_active_plan_forbidden_scope(workspace: str, forbidden_scope: list[str]) -> int:
    """Remove active-plan phases that clearly belong to forbidden directions."""
    forbidden = [str(x).strip() for x in (forbidden_scope or []) if str(x).strip()]
    if not forbidden:
        return 0
    plan = _read_active_plan(workspace)
    phases = plan.get("phases") or []
    if not isinstance(phases, list) or not phases:
        return 0
    kept = []
    removed = []
    for phase in phases:
        text = json.dumps(phase, ensure_ascii=False)
        if any(term and term in text for term in forbidden):
            phase = dict(phase)
            phase["status"] = "blocked_by_user_correction"
            phase["blocked_reason"] = "matches forbidden_scope"
            removed.append(phase)
        else:
            kept.append(phase)
    if not removed:
        return 0
    plan["phases"] = kept
    plan.setdefault("blocked_phases", [])
    plan["blocked_phases"] = (plan["blocked_phases"] + removed)[-50:]
    plan["last_heartbeat"] = datetime.now().isoformat(timespec="seconds")
    plan["heartbeat_summary"] = "用户纠偏后已清理旧方向阶段"
    idx = int(plan.get("current_phase_index", 0) or 0)
    if idx >= len(kept):
        plan["current_phase_index"] = max(0, len(kept) - 1)
    _write_active_plan(workspace, plan)
    return len(removed)
