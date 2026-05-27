"""Self Check — 轻量自检模块，替换旧版 603 行的 SelfEvolutionEngine。

每次心跳做 3 件事（总耗时 <10 秒）：
1. 知识冲突检测：相同主题不同置信度？
2. 卡死检测：当前 phase 超过 2h？
3. 代码泄漏检测：batch correction 在 CV 外部？
"""

import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Optional

from .event_bus import EventBus, PushEvent


class SelfChecker:
    """轻量自检器，每次心跳执行一次。"""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.event_bus = EventBus(state_dir)

    def run_all(self, active_plan: Optional[dict] = None) -> list[PushEvent]:
        """执行全部自检，返回新产生的事件。"""
        events = []

        # 1. 知识冲突检测
        try:
            evt = self._check_knowledge_contradictions()
            if evt:
                events.append(evt)
        except Exception:
            pass

        # 2. 卡死检测
        try:
            evt = self._check_stuck(active_plan)
            if evt:
                events.append(evt)
        except Exception:
            pass

        # 3. 代码泄漏检测
        try:
            evt = self._check_data_leakage()
            if evt:
                events.append(evt)
        except Exception:
            pass

        # 写入 event bus
        for ev in events:
            self.event_bus.push(ev)

        return events

    def _check_knowledge_contradictions(self):
        """扫描知识库，找主题相似但置信度差异大的条目。"""
        kb_path = os.path.join(self.state_dir, "knowledge.json")
        if not os.path.exists(kb_path):
            return None

        with open(kb_path, "r", encoding="utf-8") as f:
            try:
                kb = json.load(f)
            except (json.JSONDecodeError, TypeError):
                return None

        entries = kb.get("entries", kb if isinstance(kb, list) else [])
        if len(entries) < 2:
            return None

        # 简易标题相似度检测：共享 ≥2 个中文字符的视为同一主题
        seen = {}  # topic -> list of (title, confidence)
        for e in entries:
            title = e.get("title", "") or e.get("topic", "")
            conf = e.get("confidence", 0.5)
            if isinstance(conf, str):
                try:
                    conf = float(conf)
                except (ValueError, TypeError):
                    conf = 0.5

            # Find topic key
            key = None
            for t in seen:
                # Simple Chinese char overlap check
                chars = set(c for c in title if '\u4e00' <= c <= '\u9fff')
                t_chars = set(c for c in t if '\u4e00' <= c <= '\u9fff')
                if len(chars & t_chars) >= 2:
                    key = t
                    break

            if key is None:
                seen[title] = [(title, conf)]
            else:
                seen[key].append((title, conf))

        # Check for contradictions
        for topic, items in seen.items():
            if len(items) >= 2:
                confs = [c for _, c in items]
                max_c = max(confs)
                min_c = min(confs)
                if max_c - min_c > 0.3:
                    return PushEvent(
                        type="self_check",
                        subtype="contradiction",
                        title=f"知识冲突: 「{topic}」置信度差异 {max_c-min_c:.1f}",
                        body=f"条目: {', '.join(t for t, _ in items)}",
                        priority=7,
                    )

        return None

    def _check_stuck(self, active_plan: Optional[dict]):
        """检查 active_plan 当前 phase 是否卡死。"""
        if not active_plan:
            return None

        phases = active_plan.get("phases", [])
        idx = active_plan.get("current_phase_index", -1)
        if idx < 0 or idx >= len(phases):
            return None

        phase = phases[idx]
        if phase.get("status") != "in_progress":
            return None

        started_at = phase.get("started_at", "")
        if not started_at:
            return None

        try:
            start = datetime.fromisoformat(started_at)
            if datetime.now() - start > timedelta(hours=2):
                return PushEvent(
                    type="result",
                    subtype="stuck",
                    title=f"卡死了: phase {idx} '{phase.get('description','')[:30]}' 超过 2 小时",
                    body=f"开始于 {started_at}，建议标记为 stuck 并跳转到下一阶段",
                    priority=8,
                )
        except (ValueError, TypeError):
            pass

        return None

    def _check_data_leakage(self):
        """扫描实验脚本，检测 batch correction 在 CV 外部的泄漏问题。"""
        # 扫描 workspace 内的 Python 脚本
        workspace_root = os.path.dirname(self.state_dir)
        script_dir = os.path.join(workspace_root, "scripts")
        research_dir = os.path.join(workspace_root, "research_results")

        targets = []
        for d in [workspace_root, script_dir, research_dir]:
            if os.path.isdir(d):
                targets.append(d)

        for d in targets:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.endswith(".py"):
                        path = os.path.join(root, f)
                        try:
                            with open(path, "r", encoding="utf-8") as fh:
                                content = fh.read()
                        except (OSError, UnicodeDecodeError):
                            continue

                        # Pattern: ComBat or age_aware_correction called
                        # before GroupKFold.split()
                        has_correction = bool(
                            re.search(r"(ComBat|age_aware_correction|batch.correct)", content)
                        )
                        has_cv = bool(
                            re.search(r"(GroupKFold|KFold|StratifiedKFold)", content)
                        )
                        # Check if correction is inside CV loop
                        if has_correction and has_cv:
                            # Simple heuristic: check if correction appears before CV
                            lines = content.split("\n")
                            correction_line = -1
                            cv_line = -1
                            for i, line in enumerate(lines):
                                if re.search(r"(ComBat|age_aware_correction|batch\s*=\s*correct)", line):
                                    correction_line = i
                                if re.search(r"(GroupKFold|\.split\()", line):
                                    cv_line = i

                            if correction_line >= 0 and cv_line >= 0 and correction_line < cv_line:
                                # Check if correction is inside a for loop with split
                                # Look for the fold loop
                                in_fold_loop = False
                                for j in range(correction_line, min(correction_line + 20, len(lines))):
                                    if re.search(r"for\s+\w+\s+in\s+.*split\(", lines[j]):
                                        in_fold_loop = True
                                        break
                                if not in_fold_loop:
                                    return PushEvent(
                                        type="self_check",
                                        subtype="leak_warning",
                                        title=f"数据泄漏风险: {f}",
                                        body=f"batch correction (第{correction_line+1}行) 在 CV (第{cv_line+1}行) 外部。"
                                             f"修正必须在 fold 内部执行才诚实。",
                                        priority=9,
                                    )
        return None
