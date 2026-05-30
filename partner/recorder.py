"""Timeline-based standard record system.

Timeline 中心式记录，替代旧的 exploration_log.md 驱动方式。
保留 Recorder 类名及 log_exploration 方法签名保证向后兼容。
"""
import os
import json
import csv
import logging
from datetime import datetime
from typing import Optional, Any, Dict, List

logger = logging.getLogger(__name__)


class Recorder:
    """Timeline 中心式记录器"""

    def __init__(self, workspace: str):
        self._workspace = workspace
        self._records_dir = os.path.join(workspace, "20_records")
        self._projects_dir = os.path.join(self._records_dir, "projects")
        self._archives_dir = os.path.join(self._records_dir, "archived_plans")
        self._metrics_dir = os.path.join(self._records_dir, "metrics")
        os.makedirs(self._projects_dir, exist_ok=True)
        os.makedirs(self._archives_dir, exist_ok=True)
        os.makedirs(self._metrics_dir, exist_ok=True)

    def _project_path(self, name: str) -> str:
        """获取项目目录路径"""
        safe = name.replace(" ", "_").replace("/", "_")[:64]
        return os.path.join(self._projects_dir, safe)

    def ensure_project(self, name: str):
        """确保项目目录存在，含所有必需子文件"""
        pdir = self._project_path(name)
        os.makedirs(pdir, exist_ok=True)
        os.makedirs(os.path.join(pdir, "artifacts"), exist_ok=True)
        # 初始化 timeline.jsonl 和 experiments.csv（如果不存在）
        tl = os.path.join(pdir, "timeline.jsonl")
        if not os.path.exists(tl):
            with open(tl, "w") as f:
                pass
        ec = os.path.join(pdir, "experiments.csv")
        if not os.path.exists(ec):
            with open(ec, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "experiment", "config", "metric", "value", "notes"])
        kn = os.path.join(pdir, "knowledge.json")
        if not os.path.exists(kn):
            with open(kn, "w") as f:
                json.dump([], f)

    def record_timeline(self, project: str, **kwargs):
        """追加一条 timeline 记录。
        kwargs 可包含：action, hypothesis, result, reflection, next
        """
        self.ensure_project(project)
        tl_path = os.path.join(self._project_path(project), "timeline.jsonl")
        entry = {"timestamp": datetime.now().isoformat()}
        entry.update(kwargs)
        with open(tl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record_experiment(self, project: str, experiment: str, config: str = "",
                          metric: str = "", value: Any = "", notes: str = ""):
        """记录实验结果到 experiments.csv"""
        self.ensure_project(project)
        ec_path = os.path.join(self._project_path(project), "experiments.csv")
        with open(ec_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([datetime.now().isoformat(), experiment, config, metric, str(value), notes])

    def add_knowledge(self, project: str, entry_type: str, content: str,
                      source: str = "auto", confidence: float = 0.5):
        """添加知识条目"""
        self.ensure_project(project)
        kn_path = os.path.join(self._project_path(project), "knowledge.json")
        with open(kn_path, "r") as f:
            try:
                entries = json.load(f)
            except Exception:
                entries = []
        entries.append({
            "timestamp": datetime.now().isoformat(),
            "type": entry_type,
            "content": content,
            "source": source,
            "confidence": confidence,
        })
        with open(kn_path, "w") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def get_timeline(self, project: str, limit: int = 10) -> list:
        """获取最近 N 条 timeline 记录"""
        tl_path = os.path.join(self._project_path(project), "timeline.jsonl")
        if not os.path.exists(tl_path):
            return []
        with open(tl_path, "r") as f:
            lines = f.readlines()
        entries = [json.loads(l) for l in lines if l.strip()]
        return entries[-limit:]

    def get_projects(self) -> list:
        """列出所有项目名称"""
        if not os.path.isdir(self._projects_dir):
            return []
        return sorted([d for d in os.listdir(self._projects_dir)
                       if os.path.isdir(os.path.join(self._projects_dir, d))])

    # ── Backward compatibility ────────────────────────────────

    def log_exploration(self, project: str, action: str, findings: str,
                        conclusion: str = "", next_action: str = "") -> None:
        """兼容旧接口，转调 record_timeline"""
        self.record_timeline(
            project,
            action=action,
            hypothesis=findings,
            result=conclusion,
            next=next_action,
        )
