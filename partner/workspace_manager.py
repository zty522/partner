"""Workspace Manager - manages Partner workspace structure and daily log.

Handles:
  - Project-based directory organization
  - Standardized file naming (type_topic_serial_YYYYMMDD.ext)
  - Non-destructive workspace migration + file audit
  - Dialogue persistence by date (.log format)
  - Daily journal: summary + reflection
  - Proactive notification bridge for QQ
"""

import os
import re
import json
import shutil
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Project structure ────────────────────────────────────────────────

PROJECT_SUBDIRS = ["code", "ideas", "notes", "dialogue", "data", "figures"]
ROOT_SHARED_DIRS = ["code", "ideas", "knowledge", "logs", "state", "dialogue"]

# Known projects with aliases for duplicate merging
# format: canonical_key -> (display_name, [aliases])
PROJECT_REGISTRY: Dict[str, Tuple[str, list]] = {
    "age_prediction": ("年龄预测", ["aging", "age", "age_pred"]),
    "cytobridge": ("CytoBridge 基准测试", ["cytobridge_benchmark", "cytobridge_bench"]),
    "ligand_design": ("配体设计", ["molecular_generation", "drug_discovery"]),
    "mog": ("多组学 MOG", ["multi_omics_mog", "multi_omics"]),
    "acinetobacter": ("鲍曼不动杆菌 AMP", ["amp", "antibacterial", "prodcarl"]),
    "partner": ("Partner 自身开发", ["design", "architecture", "self_evolution"]),
}

# Map aliases to canonical keys
PROJECT_ALIASES = {}
for canonical, (_, aliases) in PROJECT_REGISTRY.items():
    for alias in aliases:
        PROJECT_ALIASES[alias] = canonical

# Build KNOWN_PROJECTS from registry
KNOWN_PROJECTS = {k: v[0] for k, v in PROJECT_REGISTRY.items()}


def _resolve_project_key(key: str) -> str:
    """Resolve alias to canonical project key."""
    return PROJECT_ALIASES.get(key, key)


def detect_projects(workspace: str) -> Dict[str, str]:
    """Auto-detect existing projects from workspace content."""
    projects = dict(KNOWN_PROJECTS)

    # Check project_surveys for additional projects
    survey_dir = os.path.join(workspace, "project_surveys")
    if os.path.exists(survey_dir):
        for fname in os.listdir(survey_dir):
            if fname.endswith(".md"):
                key = fname.replace(".md", "")
                key = _resolve_project_key(key)
                if key not in projects:
                    projects[key] = key.replace("_", " ").title()

    return projects


# ── Structure + Migration ────────────────────────────────────────────

def ensure_structure(workspace: str, migrate: bool = True) -> List[str]:
    """Ensure the standard workspace structure exists. Non-destructive."""
    actions = []

    for d in ROOT_SHARED_DIRS:
        path = os.path.join(workspace, d)
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
            actions.append(f"创建 {d}/")

    projects_root = os.path.join(workspace, "projects")
    if not os.path.exists(projects_root):
        os.makedirs(projects_root, exist_ok=True)
        actions.append("创建 projects/")

    projects = detect_projects(workspace)
    for key in projects:
        proj_dir = os.path.join(projects_root, key)
        if not os.path.exists(proj_dir):
            os.makedirs(proj_dir, exist_ok=True)
            actions.append(f"创建 projects/{key}/")
        for sub in PROJECT_SUBDIRS:
            sub_path = os.path.join(proj_dir, sub)
            if not os.path.exists(sub_path):
                os.makedirs(sub_path, exist_ok=True)

    if migrate:
        actions += _migrate_flat_content(workspace, projects_root, projects)
        actions += _merge_duplicate_projects(workspace, projects_root)
        actions += _audit_file_naming(workspace, projects_root, projects)

    return actions


def _migrate_flat_content(workspace: str, projects_root: str, projects: Dict[str, str]) -> List[str]:
    """Move flat files into project directories (copy, don't delete originals)."""
    actions = []

    # Migrate project surveys
    survey_dir = os.path.join(workspace, "project_surveys")
    if os.path.exists(survey_dir):
        for fname in sorted(os.listdir(survey_dir)):
            if fname.endswith(".md"):
                key = _resolve_project_key(fname.replace(".md", ""))
                if key in projects:
                    dst = os.path.join(projects_root, key, "notes", fname)
                    if not os.path.exists(dst):
                        shutil.copy2(os.path.join(survey_dir, fname), dst)
                        actions.append(f"迁移 {fname} → projects/{key}/notes/")

    # Migrate ideas → project ideas
    ideas_dir = os.path.join(workspace, "ideas")
    if os.path.exists(ideas_dir):
        for fname in sorted(os.listdir(ideas_dir)):
            if fname.endswith(".md"):
                matched = False
                for key in projects:
                    if key in fname.lower():
                        dst = os.path.join(projects_root, key, "ideas", fname)
                        if not os.path.exists(dst):
                            shutil.copy2(os.path.join(ideas_dir, fname), dst)
                            actions.append(f"迁移 ideas/{fname} → projects/{key}/ideas/")
                        matched = True
                        break

    return actions


def _merge_duplicate_projects(workspace: str, projects_root: str) -> List[str]:
    """Merge duplicate project directories (e.g. cytobridge_benchmark → cytobridge)."""
    actions = []
    if not os.path.exists(projects_root):
        return actions

    for dirname in sorted(os.listdir(projects_root)):
        canonical = _resolve_project_key(dirname)
        if canonical != dirname and os.path.isdir(os.path.join(projects_root, dirname)):
            src = os.path.join(projects_root, dirname)
            dst = os.path.join(projects_root, canonical)
            # Copy contents into canonical project
            for item in os.listdir(src):
                s = os.path.join(src, item)
                d = os.path.join(dst, item)
                if os.path.isdir(s):
                    # Merge subdirectory content
                    if os.path.isdir(d):
                        for subfile in os.listdir(s):
                            sf = os.path.join(s, subfile)
                            df = os.path.join(d, subfile)
                            if not os.path.exists(df):
                                shutil.copy2(sf, df)
                                actions.append(f"合并 {dirname}/{item}/{subfile} → {canonical}/{item}/")
                    else:
                        shutil.copytree(s, d)
                        actions.append(f"合并 {dirname}/{item}/ → {canonical}/{item}/")
                else:
                    if not os.path.exists(d):
                        shutil.copy2(s, d)
                        actions.append(f"合并 {dirname}/{item} → {canonical}/{item}/")
            actions.append(f"✅ {dirname} → {canonical}（已合并）")

    return actions


# ── File naming audit ────────────────────────────────────────────────

def _audit_file_naming(workspace: str, projects_root: str, projects: Dict[str, str]) -> List[str]:
    """Scan existing files and flag those not following naming conventions."""
    actions = []
    naming_pattern = re.compile(r"^[a-z]+_[a-z0-9_]+_\d{3}_\d{8}\.")
    for root, dirs, files in os.walk(projects_root):
        for fname in sorted(files):
            if fname.startswith(".") or fname.startswith("_"):
                continue
            ext = os.path.splitext(fname)[1]
            if ext not in (".py", ".md", ".ipynb", ".sh", ".json", ".yaml", ".yml", ".log", ".txt"):
                continue
            if not naming_pattern.match(fname):
                actions.append(f"⚠ 命名不规范: {os.path.relpath(os.path.join(root, fname), workspace)}")
    return actions


# ── File naming conventions ──────────────────────────────────────────

def generate_filename(file_type: str, topic: str, serial: Optional[int] = None, ext: str = ".md") -> str:
    """Generate a standardized filename: type_topic_serial_YYYYMMDD.ext"""
    safe_topic = re.sub(r"[^a-z0-9_]", "", topic.lower().replace(" ", "_"))[:40]
    today = datetime.now().strftime("%Y%m%d")
    serial_str = f"_{serial:03d}" if serial is not None else ""
    return f"{file_type}_{safe_topic}{serial_str}_{today}{ext}"


def find_next_serial(directory: str, file_type: str, topic: str) -> int:
    """Find the next available serial number."""
    max_serial = 0
    pattern = re.compile(rf"^{file_type}_{re.escape(topic)}_(\d+)_.*")
    if os.path.exists(directory):
        for fname in os.listdir(directory):
            m = pattern.match(fname)
            if m:
                serial = int(m.group(1))
                if serial > max_serial:
                    max_serial = serial
    return max_serial + 1


def save_versioned_file(directory: str, file_type: str, topic: str, content: str,
                        ext: str = ".md", max_versions: int = 20) -> str:
    """Save a file with versioned naming. Archives old versions."""
    os.makedirs(directory, exist_ok=True)
    serial = find_next_serial(directory, file_type, topic)
    fname = generate_filename(file_type, topic, serial, ext)
    fpath = os.path.join(directory, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    _archive_old_versions(directory, file_type, topic, ext, max_versions)
    return fpath


def _archive_old_versions(directory: str, file_type: str, topic: str, ext: str, max_versions: int):
    pattern = re.compile(rf"^{file_type}_{re.escape(topic)}_(\d+)_.*")
    versions = []
    if os.path.exists(directory):
        for fname in os.listdir(directory):
            m = pattern.match(fname)
            if m:
                versions.append((int(m.group(1)), fname))
    versions.sort(key=lambda x: x[0])
    if len(versions) > max_versions:
        archive_dir = os.path.join(directory, "_archive")
        os.makedirs(archive_dir, exist_ok=True)
        for _, fname in versions[:-max_versions]:
            shutil.move(os.path.join(directory, fname), os.path.join(archive_dir, fname))


# ── Dialogue logging (.log format) ───────────────────────────────────

def get_dialogue_path(workspace: str, project: str = "", date: Optional[datetime] = None) -> str:
    """Get the dialogue log path. Returns .log file."""
    if date is None:
        date = datetime.now()
    base = os.path.join(workspace, "projects", project, "dialogue") if project else os.path.join(workspace, "dialogue")
    year_dir = os.path.join(base, str(date.year))
    os.makedirs(year_dir, exist_ok=True)
    return os.path.join(year_dir, f"{date.strftime('%Y-%m-%d')}.log")


def append_dialogue(workspace: str, sender: str, message: str, reply: str,
                    platform: str = "qq", project: str = ""):
    """Append a dialogue turn to today's .log file."""
    fpath = get_dialogue_path(workspace, project)
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = (
        f"[{timestamp}] [{platform.upper()}] {sender}\n"
        f"  Q: {message}\n"
        f"  A: {reply}\n\n"
    )
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, "a", encoding="utf-8") as f:
        f.write(entry)


# ── Daily journal ────────────────────────────────────────────────────

def get_journal_path(workspace: str, date: Optional[datetime] = None) -> str:
    """Get the daily journal path. .log format."""
    if date is None:
        date = datetime.now()
    journal_root = os.path.join(workspace, "journal")
    year_dir = os.path.join(journal_root, str(date.year))
    os.makedirs(year_dir, exist_ok=True)
    return os.path.join(year_dir, f"{date.strftime('%Y-%m-%d')}.log")


def write_daily_journal(workspace: str, summary: str, reflection: str = "",
                        date: Optional[datetime] = None) -> str:
    """Write (or append to) the daily journal. Returns file path."""
    if date is None:
        date = datetime.now()
    fpath = get_journal_path(workspace, date)

    header = f"""# {date.strftime('%Y-%m-%d %A')} 日志
═══════════════════════════════════════

📊 今日总结
{summary}

"""
    if reflection:
        header += f"""💭 反思
{reflection}

"""
    header += "───────────────────────────────────────\n"

    # Only write header if file doesn't exist
    if not os.path.exists(fpath):
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(header)
    else:
        # Update summary if file exists
        with open(fpath, "r+", encoding="utf-8") as f:
            content = f.read()
            f.seek(0)
            new_content = header + content.split("───────────────────────────────────────", 1)[-1].lstrip("\n") if "───────────────────────────────────────" in content else content
            f.write(new_content)
            f.truncate()

    return fpath


def append_journal_entry(workspace: str, entry: str, date: Optional[datetime] = None):
    """Append an entry to the daily journal."""
    fpath = get_journal_path(workspace, date)
    if not os.path.exists(fpath):
        write_daily_journal(workspace, "(今日无总结)", "", date)
    timestamp = datetime.now().strftime("%H:%M")
    with open(fpath, "a", encoding="utf-8") as f:
        f.write(f"\n[{timestamp}] {entry}\n")


# ── Full daily maintenance ───────────────────────────────────────────

def run_daily_maintenance(workspace: str) -> Dict:
    """Run all daily maintenance tasks.

    Returns dict with:
      - actions: list of file organization actions
      - journal_path: path to today's journal
      - summary: text summary of today's work
      - interesting: list of interesting findings to share
    """
    results = {"actions": [], "journal_path": "", "summary": "", "interesting": []}

    # 1. Workspace organization
    results["actions"] = migrate_workspace(workspace)

    # 2. Read today's stats
    stats = {}
    stats_path = os.path.join(workspace, "state", "stats.json")
    if os.path.exists(stats_path):
        try:
            with open(stats_path) as f:
                stats = json.load(f)
        except Exception:
            pass

    cycles = stats.get("total_cycles", 0)
    tasks_done = stats.get("total_tasks_completed", 0)

    # 3. Count knowledge entries
    kb_count = 0
    kb_path = os.path.join(workspace, "state", "knowledge.json")
    if os.path.exists(kb_path):
        try:
            with open(kb_path) as f:
                kb = json.load(f)
            kb_count = len(kb.get("entries", []))
        except Exception:
            pass

    # 4. Recent activities from journal
    recent = []
    journal_path = os.path.join(workspace, "state", "journal.jsonl")
    if os.path.exists(journal_path):
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            with open(journal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp", "")
                        if today_str in ts:
                            recent.append(entry.get("task_title", ""))
                    except Exception:
                        pass
        except Exception:
            pass

    # 5. Build summary
    summary_parts = [f"完成 {cycles} 个研究周期，{tasks_done} 个任务"]
    if kb_count:
        summary_parts.append(f"知识库 {kb_count} 条")
    if recent:
        summary_parts.append(f"今日活动: {len(recent)} 项")
    if results["actions"]:
        summary_parts.append(f"文件整理: {len(results['actions'])} 项操作")
    results["summary"] = "，".join(summary_parts)

    # 6. Check for interesting findings
    results["interesting"] = _find_interesting(workspace)

    # 7. Write journal
    fpath = write_daily_journal(workspace, results["summary"])
    results["journal_path"] = fpath

    return results


def _find_interesting(workspace: str) -> List[str]:
    """Find interesting findings worth sharing via QQ."""
    interesting = []
    kb_path = os.path.join(workspace, "state", "knowledge.json")
    if os.path.exists(kb_path):
        try:
            with open(kb_path) as f:
                kb = json.load(f)
            today = datetime.now().strftime("%Y-%m-%d")
            for entry in kb.get("entries", []):
                created = entry.get("created_at", "")
                if today in created:
                    title = entry.get("title", "")
                    conf = entry.get("confidence", "medium")
                    cat = entry.get("category", "")
                    if conf == "high" and cat in ("findings", "breakthrough", "discovery"):
                        interesting.append(f"🔥 {title}")
                    elif "重要" in title or "突破" in title or "发现" in title:
                        interesting.append(f"💡 {title}")
        except Exception:
            pass
    return interesting


# ── Workspace migration main entry ───────────────────────────────────

def migrate_workspace(workspace: str) -> List[str]:
    """Run full workspace migration. Non-destructive."""
    actions = []
    actions += ensure_structure(workspace, migrate=True)
    actions += _remove_old_empty_dirs(workspace)
    return actions


def _remove_old_empty_dirs(workspace: str) -> List[str]:
    actions = []
    old_dirs = ["project_surveys", "literature", "design", "docs", "exploration_trees", "skills"]
    for d in old_dirs:
        path = os.path.join(workspace, d)
        if os.path.isdir(path):
            try:
                remaining = os.listdir(path)
                if not remaining:
                    os.rmdir(path)
                    actions.append(f"删除空目录: {d}/")
                else:
                    logger.info(f"目录 {d}/ 非空，保留: {len(remaining)} 文件")
            except OSError:
                pass
    return actions
