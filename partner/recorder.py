"""Recorder — 标准化记录系统。

为 Partner 的探索、实验和知识提供结构化记录能力。

Directory structure:
~/.partner/records/
├── projects/{project_name}/
│   ├── exploration_log.md   # Human-readable log (markdown)
│   ├── knowledge.json        # Structured knowledge entries
│   └── experiments.csv       # Experiment results table
├── global_knowledge.json     # Cross-project knowledge
└── session_history.jsonl     # Session summaries
"""

import os
import json
import csv
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class Recorder:
    """Standardized record system for Partner explorations, experiments, and knowledge."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.records_dir = os.path.join(os.path.expanduser("~"), ".partner", "records")

    # ── Internal helpers ─────────────────────────────────────

    def _ensure_project_dir(self, project: str) -> str:
        """Ensure the project record directory exists and return its path."""
        proj_dir = os.path.join(self.records_dir, "projects", project)
        os.makedirs(proj_dir, exist_ok=True)
        return proj_dir

    def _ensure_records_dir(self) -> None:
        """Ensure the top-level records directory exists."""
        os.makedirs(self.records_dir, exist_ok=True)

    # ── exploration_log.md ───────────────────────────────────

    def log_exploration(self, project: str, action: str, findings: str,
                        conclusion: str = "", next_action: str = "") -> None:
        """Append to exploration_log.md with markdown formatting.

        Creates date headings automatically. Each entry is timestamped.
        """
        proj_dir = self._ensure_project_dir(project)
        log_path = os.path.join(proj_dir, "exploration_log.md")

        now = datetime.now()
        date_header = now.strftime("%Y-%m-%d")
        time_stamp = now.strftime("%H:%M:%S")

        entry_parts = [
            f"\n",
            f"### {time_stamp} — {action}\n",
            f"\n",
            f"**Findings:** {findings}\n",
        ]
        if conclusion:
            entry_parts.append(f"\n**Conclusion:** {conclusion}\n")
        if next_action:
            entry_parts.append(f"\n**Next Action:** {next_action}\n")
        entry_parts.append("\n---\n")

        entry_text = "".join(entry_parts)

        # Check if date heading already exists
        date_heading = f"## {date_header}\n"
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                content = f.read()
            if date_heading not in content:
                # Prepend date heading and entry at the top
                with open(log_path, "r", encoding="utf-8") as f:
                    existing = f.read()
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(date_heading + entry_text + "\n" + existing)
            else:
                # Append entry after the date heading
                idx = content.rfind(date_heading)
                before = content[:idx]
                after = content[idx:]
                # Find where the date section ends (next date heading or EOF)
                rest = after[len(date_heading):]
                next_date = rest.find("\n## ")
                if next_date != -1:
                    section_content = rest[:next_date]
                    remaining = rest[next_date:]
                else:
                    section_content = rest
                    remaining = ""
                new_section = date_heading + section_content.rstrip() + entry_text + "\n"
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(before + new_section + remaining)
        else:
            # New file: write date heading and entry
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"# Exploration Log — {project}\n\n")
                f.write(date_heading + entry_text)

        logger.info(f"[Recorder] Exploration logged for project '{project}': {action}")

    # ── knowledge.json ──────────────────────────────────────

    def add_knowledge(self, project: str, entry_type: str, content: str,
                      source: str = "auto", confidence: float = 0.5) -> str:
        """Add a knowledge entry to the structured knowledge.json file.

        Args:
            project: Project name (or "" for cross-project knowledge)
            entry_type: Type of knowledge (e.g., "finding", "metric", "fact", "insight")
            content: The knowledge content text
            source: Origin of the knowledge (e.g., "auto", "search", "user_dialog")
            confidence: Confidence score between 0.0 and 1.0

        Returns:
            The generated entry_id string.
        """
        entry_id = uuid.uuid4().hex[:12]
        now = datetime.now()

        entry = {
            "id": entry_id,
            "type": entry_type,
            "content": content,
            "source": source,
            "confidence": round(confidence, 3),
            "timestamp": now.isoformat(),
        }

        if project:
            proj_dir = self._ensure_project_dir(project)
            kb_path = os.path.join(proj_dir, "knowledge.json")
        else:
            self._ensure_records_dir()
            kb_path = os.path.join(self.records_dir, "global_knowledge.json")

        # Load existing entries
        existing = []
        if os.path.exists(kb_path):
            try:
                with open(kb_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    existing = data
                else:
                    existing = [data]
            except (json.JSONDecodeError, Exception):
                existing = []

        existing.append(entry)

        # Keep max 500 entries, trim oldest
        if len(existing) > 500:
            existing = existing[-500:]

        with open(kb_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

        project_label = project or "global"
        logger.info(f"[Recorder] Knowledge added [{project_label}] {entry_type}: "
                    f"{content[:60]}... (id={entry_id})")
        return entry_id

    # ── experiments.csv ──────────────────────────────────────

    def add_experiment(self, project: str, config: str, metric: str,
                       value: float, notes: str = "") -> None:
        """Append a row to experiments.csv.

        The CSV has header: timestamp,project,config,metric,value,notes
        """
        proj_dir = self._ensure_project_dir(project)
        csv_path = os.path.join(proj_dir, "experiments.csv")

        now = datetime.now().isoformat()
        header = ["timestamp", "project", "config", "metric", "value", "notes"]
        row = [now, project, config, metric, str(value), notes]

        file_exists = os.path.exists(csv_path)
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(header)
            writer.writerow(row)

        logger.info(f"[Recorder] Experiment recorded [{project}] {metric}={value} "
                    f"({config})")

    # ── session_history.jsonl ────────────────────────────────

    def add_session(self, summary: str) -> None:
        """Append a session summary entry to session_history.jsonl."""
        self._ensure_records_dir()
        hist_path = os.path.join(self.records_dir, "session_history.jsonl")

        entry = {
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
        }

        with open(hist_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.info(f"[Recorder] Session recorded: {summary[:60]}...")

    # ── Query methods ───────────────────────────────────────

    def get_recent_explorations(self, project: str = "", limit: int = 5) -> List[Dict]:
        """Get recent exploration log entries as structured records.

        Args:
            project: Project name. If empty, scans all projects.
            limit: Max entries per project.

        Returns:
            List of dicts with keys: project, timestamp, action, findings, conclusion, next_action
        """
        results = []
        projects_to_check = []

        if project:
            proj_dir = os.path.join(self.records_dir, "projects", project)
            log_path = os.path.join(proj_dir, "exploration_log.md")
            if os.path.exists(log_path):
                projects_to_check = [(project, log_path)]
        else:
            proj_base = os.path.join(self.records_dir, "projects")
            if os.path.isdir(proj_base):
                for pname in sorted(os.listdir(proj_base)):
                    pdir = os.path.join(proj_base, pname)
                    log_path = os.path.join(pdir, "exploration_log.md")
                    if os.path.isfile(log_path):
                        projects_to_check.append((pname, log_path))

        for pname, log_path in projects_to_check:
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Parse entries: ### HH:MM:SS — Action
                import re
                entries = re.findall(
                    r"### (\d{2}:\d{2}:\d{2})\s*—\s*(.+?)\n"
                    r"(.*?)(?=###|\Z)",
                    content, re.DOTALL
                )
                for ts, action, body in entries:
                    findings = ""
                    conclusion = ""
                    next_action = ""

                    fm = re.search(r"\*\*Findings:\*\*\s*(.+?)(?:\n\*\*|\Z)", body, re.DOTALL)
                    if fm:
                        findings = fm.group(1).strip()

                    cm = re.search(r"\*\*Conclusion:\*\*\s*(.+?)(?:\n\*\*|\Z)", body, re.DOTALL)
                    if cm:
                        conclusion = cm.group(1).strip()

                    nm = re.search(r"\*\*Next Action:\*\*\s*(.+?)(?:\n\*\*|\Z)", body, re.DOTALL)
                    if nm:
                        next_action = nm.group(1).strip()

                    results.append({
                        "project": pname,
                        "timestamp": ts,
                        "action": action.strip(),
                        "findings": findings,
                        "conclusion": conclusion,
                        "next_action": next_action,
                    })

                # Sort by project, then reverse timestamp (most recent first)
                results.sort(key=lambda r: (r["project"], r["timestamp"]), reverse=True)

            except Exception as e:
                logger.warning(f"[Recorder] Failed to parse exploration log for '{pname}': {e}")

        return results[:limit]

    def get_recent_knowledge(self, project: str = "", limit: int = 5) -> List[Dict]:
        """Get recent knowledge entries.

        Args:
            project: Project name. If empty, queries all projects + global.
            limit: Max entries total.

        Returns:
            List of knowledge entry dicts sorted by timestamp descending.
        """
        all_entries = []

        # Collect from project-specific knowledge files
        if project:
            proj_dir = os.path.join(self.records_dir, "projects", project)
            kb_path = os.path.join(proj_dir, "knowledge.json")
            if os.path.exists(kb_path):
                try:
                    with open(kb_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        for entry in data:
                            entry["_project"] = project
                        all_entries.extend(data)
                except Exception as e:
                    logger.warning(f"[Recorder] Failed to read knowledge.json for '{project}': {e}")
        else:
            # Read all project knowledge files
            proj_base = os.path.join(self.records_dir, "projects")
            if os.path.isdir(proj_base):
                for pname in sorted(os.listdir(proj_base)):
                    kb_path = os.path.join(proj_base, pname, "knowledge.json")
                    if os.path.isfile(kb_path):
                        try:
                            with open(kb_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            if isinstance(data, list):
                                for entry in data:
                                    entry["_project"] = pname
                                all_entries.extend(data)
                        except Exception:
                            pass

            # Also include global knowledge
            global_path = os.path.join(self.records_dir, "global_knowledge.json")
            if os.path.exists(global_path):
                try:
                    with open(global_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        for entry in data:
                            entry["_project"] = "global"
                        all_entries.extend(data)
                except Exception:
                    pass

        # Sort by timestamp descending (most recent first)
        def _sort_key(entry):
            ts = entry.get("timestamp", "")
            return ts if ts else ""

        all_entries.sort(key=_sort_key, reverse=True)

        return all_entries[:limit]

    # ── Summary formatter ───────────────────────────────────

    def format_summary(self, project: str = "", limit: int = 5) -> str:
        """Format recent records as a readable summary for QQ output.

        Combines recent explorations and knowledge entries into a
        human-readable markdown report.
        """
        parts = []

        if project:
            parts.append(f"## 📊 记录摘要 — {project}\n")
        else:
            parts.append("## 📊 所有记录摘要\n")

        # Recent explorations
        explorations = self.get_recent_explorations(project=project, limit=limit)
        if explorations:
            parts.append("\n### 🔍 最近探索\n")
            for i, exp in enumerate(explorations, 1):
                proj_tag = f"[{exp['project']}] " if not project else ""
                timestamp = exp.get("timestamp", "")
                action = exp.get("action", "")
                findings = exp.get("findings", "")
                conclusion = exp.get("conclusion", "")
                next_action = exp.get("next_action", "")

                parts.append(f"**{i}. {proj_tag}{action}** ({timestamp})")
                if findings:
                    parts.append(f"   > {findings[:200]}")
                if conclusion:
                    parts.append(f"   ✅ 结论: {conclusion[:150]}")
                if next_action:
                    parts.append(f"   ⏩ 下一步: {next_action[:150]}")
                parts.append("")

        # Recent knowledge
        knowledge = self.get_recent_knowledge(project=project, limit=limit)
        if knowledge:
            parts.append("\n### 💡 最近知识\n")
            for i, entry in enumerate(knowledge, 1):
                proj_tag = f"[{entry.get('_project', '?')}] " if not project else ""
                etype = entry.get("type", "?")
                content = entry.get("content", "")
                source = entry.get("source", "")
                confidence = entry.get("confidence", "")
                ts = entry.get("timestamp", "")[:19]

                conf_str = f" (置信度: {confidence})" if confidence else ""
                parts.append(
                    f"**{i}. {proj_tag}{etype}**{conf_str} — {ts}"
                )
                parts.append(f"   > {content[:300]}")
                if source:
                    parts.append(f"   来源: {source}")
                parts.append("")

        if not explorations and not knowledge:
            parts.append("\n暂无记录。\n")

        return "\n".join(parts)
