"""AutoCheck — 事件总线 + 自检 + 通知器。

合并自 event_bus.py + proactive_notifier.py + self_check.py。

包含：
- PushEvent / EventBus: 基于 jsonl 的推送事件系统
- Notification / ProactiveNotifier: 简化版检查通知器
- SelfChecker: 轻量 3 步自检（知识冲突/卡死检测/数据泄漏）
"""

import json
import os
import re
import time
import logging
from datetime import datetime, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# PushEvent + EventBus（来自 event_bus.py）
# ════════════════════════════════════════════════════════════════

class PushEvent:
    """一个推送事件记录。"""

    def __init__(self, type: str, subtype: str, title: str,
                 body: str = "", priority: int = 5,
                 id: str = "", created_at: str = "",
                 pushed: bool = False, push_target: str = "qq"):
        self.id = id or f"ev_{int(time.time())}_{abs(hash(str(vars()))) % 10000}"
        self.type = type
        self.subtype = subtype
        self.title = title
        self.body = body
        self.priority = priority
        self.created_at = created_at or datetime.now().isoformat(timespec="seconds")
        self.pushed = pushed
        self.push_target = push_target

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "subtype": self.subtype,
            "title": self.title, "body": self.body, "priority": self.priority,
            "created_at": self.created_at, "pushed": self.pushed,
            "push_target": self.push_target,
        }


class EventBus:
    """Event Bus — 读写 state/event_bus.jsonl。"""

    def __init__(self, state_dir: str):
        self.path = os.path.join(state_dir, "event_bus.jsonl")
        os.makedirs(state_dir, exist_ok=True)

    def push(self, event: PushEvent):
        """写入一条事件（追加）。"""
        event.pushed = False
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def pop_pending(self, min_priority: int = 5) -> list[PushEvent]:
        """获取所有未推送的事件（按优先级降序），并标记为已推。"""
        if not os.path.exists(self.path):
            return []
        pending = []
        remaining = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    ev = PushEvent(**data)
                    if not ev.pushed and ev.priority >= min_priority:
                        ev.pushed = True
                        pending.append(ev)
                    remaining.append(ev)
                except (json.JSONDecodeError, TypeError):
                    continue
        if pending:
            with open(self.path, "w", encoding="utf-8") as f:
                for ev in remaining:
                    f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
            pending.sort(key=lambda e: -e.priority)
        return pending

    def peek_recent(self, n: int = 5) -> list[PushEvent]:
        """查看最近 n 条事件（不修改推送状态）。"""
        if not os.path.exists(self.path):
            return []
        events = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(PushEvent(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        return events[-n:]

    def count_unpushed(self) -> int:
        """统计未推送事件数。"""
        if not os.path.exists(self.path):
            return 0
        count = 0
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if not data.get("pushed", False):
                        count += 1
                except (json.JSONDecodeError, TypeError):
                    continue
        return count

    def clear_pushed(self):
        """清理已推送事件（保留未推送的）。"""
        if not os.path.exists(self.path):
            return
        remaining = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if not data.get("pushed", False):
                        remaining.append(data)
                except (json.JSONDecodeError, TypeError):
                    continue
        if remaining:
            with open(self.path, "w", encoding="utf-8") as f:
                for ev in remaining:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        elif os.path.exists(self.path):
            os.remove(self.path)


# ════════════════════════════════════════════════════════════════
# ProactiveNotifier（来自 proactive_notifier.py）
# ════════════════════════════════════════════════════════════════

class Notification:
    """一条通知。"""
    def __init__(self, title: str, body: str = "", priority: str = "normal"):
        self.title = title
        self.body = body
        self.priority = priority  # "low" | "normal" | "high"


class ProactiveNotifier:
    """简化版主动通知器 — 检查 Event Bus 的未推送事件。"""

    def __init__(self, knowledge, journal, state, workspace: str = ""):
        self.workspace = workspace
        state_dir = os.path.join(workspace, "state") if workspace else None
        self._event_bus_path = os.path.join(state_dir, "event_bus.jsonl") if state_dir else None

    def check_and_notify(self) -> List[Notification]:
        """检查是否有未推送的事件需要通知用户。"""
        if not self._event_bus_path or not os.path.exists(self._event_bus_path):
            return []
        notifications = []
        try:
            with open(self._event_bus_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if not data.get("pushed", False) and data.get("priority", 0) >= 8:
                            notifications.append(Notification(
                                title=data.get("title", ""),
                                body=data.get("body", ""),
                                priority="high" if data.get("priority", 0) >= 9 else "normal",
                            ))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except Exception as e:
            logger.warning(f"Failed to read event bus: {e}")
        return notifications

    def format_notifications(self, notifications: List[Notification]) -> str:
        """格式化通知为可读文本。"""
        if not notifications:
            return ""
        lines = ["📬 Partner 有新的发现：", ""]
        for n in notifications:
            icon = "🔴" if n.priority == "high" else "🟡"
            lines.append(f"  {icon} {n.title}")
            if n.body:
                lines.append(f"    {n.body}")
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# SelfChecker（来自 self_check.py）
# ════════════════════════════════════════════════════════════════

class SelfChecker:
    """轻量自检器，每次心跳执行一次。

    每次心跳做 3 件事（总耗时 <10 秒）：
    1. 知识冲突检测：相同主题不同置信度？
    2. 卡死检测：当前 phase 超过 2h？
    3. 代码泄漏检测：batch correction 在 CV 外部？
    """

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.event_bus = EventBus(state_dir)

    def run_all(self, active_plan: Optional[dict] = None) -> list[PushEvent]:
        """执行全部自检，返回新产生的事件。"""
        events = []
        for check in [self._check_knowledge_contradictions,
                      self._check_stuck(active_plan),
                      self._check_data_leakage]:
            try:
                evt = check() if callable(check) else check
                if evt:
                    events.append(evt)
            except Exception:
                pass
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
        seen = {}
        for e in entries:
            title = e.get("title", "") or e.get("topic", "")
            conf = e.get("confidence", 0.5)
            if isinstance(conf, str):
                try:
                    conf = float(conf)
                except (ValueError, TypeError):
                    conf = 0.5
            key = None
            for t in seen:
                chars = set(c for c in title if "\u4e00" <= c <= "\u9fff")
                t_chars = set(c for c in t if "\u4e00" <= c <= "\u9fff")
                if len(chars & t_chars) >= 2:
                    key = t
                    break
            if key is None:
                seen[title] = [(title, conf)]
            else:
                seen[key].append((title, conf))
        for topic, items in seen.items():
            if len(items) >= 2:
                confs = [c for _, c in items]
                if max(confs) - min(confs) > 0.3:
                    return PushEvent(
                        type="self_check",
                        subtype="contradiction",
                        title=f"知识冲突: 「{topic}」置信度差异 {max(confs)-min(confs):.1f}",
                        body=f"条目: {', '.join(t for t, _ in items)}",
                        priority=7,
                    )
        return None

    def _check_stuck(self, active_plan: Optional[dict]) -> Optional[PushEvent]:
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

    def _check_data_leakage(self) -> Optional[PushEvent]:
        """扫描实验脚本，检测可能在划分前拟合预处理器的泄漏问题。"""
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
                    if not f.endswith(".py"):
                        continue
                    path = os.path.join(root, f)
                    try:
                        with open(path, "r", encoding="utf-8") as fh:
                            content = fh.read()
                    except (OSError, UnicodeDecodeError):
                        continue

                    has_preprocess_fit = bool(re.search(r"\b(?:fit_transform|fit)\s*\(", content))
                    has_split = bool(re.search(r"\b(?:train_test_split|KFold|StratifiedKFold|GroupKFold|\.split\s*\()", content))
                    if not (has_preprocess_fit and has_split):
                        continue

                    lines = content.split("\n")
                    preprocess_line = -1
                    split_line = -1
                    for i, line in enumerate(lines):
                        if preprocess_line < 0 and re.search(r"\b(?:fit_transform|fit)\s*\(", line):
                            preprocess_line = i
                        if split_line < 0 and re.search(r"\b(?:train_test_split|KFold|StratifiedKFold|GroupKFold|\.split\s*\()", line):
                            split_line = i

                    if preprocess_line >= 0 and split_line >= 0 and preprocess_line < split_line:
                        in_fold_loop = False
                        for j in range(preprocess_line, min(preprocess_line + 20, len(lines))):
                            if re.search(r"for\s+\w+\s+in\s+.*split\(", lines[j]):
                                in_fold_loop = True
                                break
                        if not in_fold_loop:
                            return PushEvent(
                                type="self_check",
                                subtype="leak_warning",
                                title=f"数据泄漏风险: {f}",
                                body=f"预处理拟合 (第{preprocess_line+1}行) 可能发生在数据划分 (第{split_line+1}行) 前。"
                                     f"请确认拟合只使用训练折/训练集。",
                                priority=9,
                            )
        return None
