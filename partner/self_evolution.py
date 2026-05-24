"""Self-Evolution Engine — Partner 自我进化能力原型

三层进化架构：
1. StrategyLearner: 从历史任务中学习任务选择策略
2. MemoryPruner: 自动清理过期知识、强化高价值条目
3. CPEGuard: 防止能力退化，定期验证核心能力

兼容 cron 驱动架构，无在线训练。
"""

import json
import os
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


# ============================================================
# Data Classes
# ============================================================

@dataclass
class StrategyProfile:
    """任务策略画像"""
    task_type: str
    tags: List[str] = field(default_factory=list)
    success_rate: float = 0.5
    avg_value_score: float = 0.0
    execution_count: int = 0
    last_updated: str = ""
    recommended_priority_boost: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PruneAction:
    """记忆清理动作"""
    entry_id: str
    action: str  # "archive" | "merge" | "promote" | "demote"
    reason: str
    confidence: float = 0.8

    def to_dict(self):
        return asdict(self)


@dataclass
class Capability:
    """核心能力注册"""
    id: str
    name: str
    task_type: str
    baseline_success_rate: float = 0.8
    current_success_rate: float = 0.8
    last_verified: str = ""
    verification_interval_hours: int = 72
    degradation_threshold: float = 0.15

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Capability":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ============================================================
# StrategyLearner — 策略学习
# ============================================================

class StrategyLearner:
    """从 journal.jsonl 学习任务执行策略"""

    def __init__(self, journal_path: str, profile_path: str):
        self.journal_path = journal_path
        self.profile_path = profile_path

    def analyze(self, lookback: int = 50) -> Dict[str, StrategyProfile]:
        """解析最近 lookback 条日志，生成策略画像"""
        entries = self._load_journal(lookback)
        if not entries:
            return {}

        # 按 task_type 聚合
        by_type: Dict[str, List[dict]] = defaultdict(list)
        for e in entries:
            ttype = e.get("task_type", "unknown")
            by_type[ttype].append(e)

        profiles = {}
        for task_type, task_entries in by_type.items():
            total = len(task_entries)
            completed = sum(1 for e in task_entries
                          if "FAILED" not in e.get("task_title", "")
                          and "FAILED" not in e.get("result_summary", ""))
            success_rate = completed / total if total > 0 else 0.5

            # 价值分数：基于 new_tasks_generated 和 knowledge_entries_added
            value_scores = []
            for e in task_entries:
                v = e.get("new_tasks_generated", 0) + e.get("knowledge_entries_added", 0)
                value_scores.append(v)
            avg_value = sum(value_scores) / len(value_scores) if value_scores else 0

            # 计算优先级调整
            boost = 0
            if success_rate > 0.8 and avg_value > 2:
                boost = 2
            elif success_rate > 0.6 and avg_value > 1:
                boost = 1
            elif success_rate < 0.3:
                boost = -3
            elif avg_value < 0.5 and total >= 3:
                boost = -2  # 边际收益递减

            # 收集 tags
            all_tags = []
            for e in task_entries:
                all_tags.extend(e.get("tags", []))
            tag_counts = defaultdict(int)
            for t in all_tags:
                tag_counts[t] += 1
            top_tags = sorted(tag_counts.keys(), key=lambda t: tag_counts[t], reverse=True)[:5]

            profiles[task_type] = StrategyProfile(
                task_type=task_type,
                tags=top_tags,
                success_rate=round(success_rate, 3),
                avg_value_score=round(avg_value, 2),
                execution_count=total,
                last_updated=datetime.now().isoformat(),
                recommended_priority_boost=boost,
            )

        return profiles

    def save(self, profiles: Dict[str, StrategyProfile]):
        """保存策略画像到文件"""
        data = {k: v.to_dict() for k, v in profiles.items()}
        data["_meta"] = {"updated_at": datetime.now().isoformat(), "count": len(profiles)}
        os.makedirs(os.path.dirname(self.profile_path) or ".", exist_ok=True)
        with open(self.profile_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Strategy profile saved: {len(profiles)} profiles")

    def load(self) -> Dict[str, StrategyProfile]:
        """加载已有的策略画像"""
        try:
            with open(self.profile_path, "r", encoding="utf-8") as f:
                data = json.loads(f.read(), strict=False)
            return {k: StrategyProfile.from_dict(v) for k, v in data.items()
                    if k != "_meta"}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _load_journal(self, limit: int) -> List[dict]:
        """读取 journal.jsonl 最近 limit 条记录"""
        entries = []
        try:
            with open(self.journal_path, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return []

        decoder = json.JSONDecoder(strict=False)
        pos = 0
        while pos < len(content):
            stripped = content[pos:].lstrip()
            if not stripped:
                break
            try:
                obj, end = decoder.raw_decode(stripped)
                entries.append(obj)
                next_nl = stripped.find('\n', end)
                if next_nl == -1:
                    break
                pos = pos + (len(content[pos:]) - len(stripped)) + next_nl + 1
            except json.JSONDecodeError:
                next_nl = content.find('\n', pos)
                if next_nl == -1:
                    break
                pos = next_nl + 1

        return entries[-limit:]


# ============================================================
# MemoryPruner — 记忆优化
# ============================================================

class MemoryPruner:
    """知识库自动清理和强化"""

    def __init__(self, knowledge_path: str, prune_log_path: str):
        self.knowledge_path = knowledge_path
        self.prune_log_path = prune_log_path

    def prune(self, max_age_days: int = 30) -> List[PruneAction]:
        """扫描知识库，生成清理动作列表"""
        kb = self._load_knowledge()
        entries = kb.get("entries", [])
        actions = []

        now = datetime.now()

        # 1. 过期清理：confidence=low 且超过 max_age_days
        for e in entries:
            created = e.get("created_at", "")
            confidence = e.get("confidence", "medium")
            if confidence == "low" and created:
                try:
                    created_dt = datetime.fromisoformat(created)
                    age_days = (now - created_dt).days
                    if age_days > max_age_days:
                        actions.append(PruneAction(
                            entry_id=e.get("id", ""),
                            action="archive",
                            reason=f"low confidence + {age_days} days old",
                            confidence=0.9,
                        ))
                except ValueError:
                    pass

        # 2. 冗余检测：标题相似（简单子串匹配）
        titles = [(e.get("id", ""), e.get("title", "")) for e in entries]
        seen_titles: Dict[str, str] = {}  # normalized_title -> entry_id
        for eid, title in titles:
            normalized = title.strip().lower()
            if not normalized:
                continue
            if normalized in seen_titles:
                # 保留后发现者（假设更新的更准确），标记前者
                actions.append(PruneAction(
                    entry_id=seen_titles[normalized],
                    action="merge",
                    reason=f"duplicate title with {eid}",
                    confidence=0.7,
                ))
            else:
                seen_titles[normalized] = eid

        # 3. 价值强化：被高频引用的条目
        ref_counts = self._count_references(entries)
        for eid, count in ref_counts.items():
            if count >= 3:
                entry = next((e for e in entries if e.get("id") == eid), None)
                if entry and entry.get("confidence") != "high":
                    actions.append(PruneAction(
                        entry_id=eid,
                        action="promote",
                        reason=f"referenced {count} times",
                        confidence=0.85,
                    ))

        # 4. 孤立清理：无有效 tag 的条目
        for e in entries:
            tags = e.get("tags", [])
            if not tags and e.get("confidence", "medium") != "high":
                actions.append(PruneAction(
                    entry_id=e.get("id", ""),
                    action="demote",
                    reason="no tags, low discoverability",
                    confidence=0.6,
                ))

        return actions

    def apply(self, actions: List[PruneAction]):
        """执行清理动作，修改 knowledge.json"""
        if not actions:
            return

        kb = self._load_knowledge()
        entries = kb.get("entries", [])
        entry_map = {e.get("id"): e for e in entries}

        applied = 0
        for act in actions:
            entry = entry_map.get(act.entry_id)
            if not entry:
                continue

            if act.action == "archive":
                entry["confidence"] = "archived"
                entry["_archived_reason"] = act.reason
                applied += 1

            elif act.action == "promote":
                entry["confidence"] = "high"
                applied += 1

            elif act.action == "demote":
                entry["confidence"] = "low"
                applied += 1

            elif act.action == "merge":
                # 标记为 merged，实际内容保留但标记
                entry["confidence"] = "merged"
                entry["_merged_reason"] = act.reason
                applied += 1

        # 更新 meta
        kb["meta"]["last_updated"] = datetime.now().isoformat()
        active = sum(1 for e in entries if e.get("confidence") not in ("archived", "merged"))
        kb["meta"]["active_entries"] = active

        self._save_knowledge(kb)

        # 记录清理日志
        self._log_prune_actions(actions, applied)
        logger.info(f"Memory pruned: {applied}/{len(actions)} actions applied")

    def _count_references(self, entries: List[dict]) -> Dict[str, int]:
        """统计每个条目被其他条目引用的次数（通过 related_projects 或 tag 匹配）"""
        ref_counts: Dict[str, int] = defaultdict(int)
        all_tags = defaultdict(list)  # tag -> [entry_ids]

        for e in entries:
            for tag in e.get("tags", []):
                all_tags[tag].append(e.get("id", ""))

        # 如果多个条目共享 tag，互相算引用
        for tag, eids in all_tags.items():
            if len(eids) > 1:
                for eid in eids:
                    ref_counts[eid] += len(eids) - 1

        return ref_counts

    def _load_knowledge(self) -> dict:
        try:
            with open(self.knowledge_path, "r", encoding="utf-8") as f:
                return json.loads(f.read(), strict=False)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"entries": [], "meta": {"total_entries": 0}}

    def _save_knowledge(self, kb: dict):
        os.makedirs(os.path.dirname(self.knowledge_path) or ".", exist_ok=True)
        with open(self.knowledge_path, "w", encoding="utf-8") as f:
            json.dump(kb, f, ensure_ascii=False, indent=2)

    def _log_prune_actions(self, actions: List[PruneAction], applied: int):
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "total_actions": len(actions),
            "applied": applied,
            "actions": [a.to_dict() for a in actions],
        }
        with open(self.prune_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# ============================================================
# CPEGuard — 能力退化防护
# ============================================================

class CPEGuard:
    """Capability Preservation through Evaluation

    参考 CPE 论文的核心思想：自我进化系统必须显式保护已有能力。
    """

    def __init__(self, registry_path: str, journal_path: str):
        self.registry_path = registry_path
        self.journal_path = journal_path

    def check_all(self) -> List[Dict]:
        """检查所有注册能力，返回退化告警列表"""
        registry = self._load_registry()
        capabilities = registry.get("capabilities", [])
        alerts = []

        for cap_data in capabilities:
            cap = Capability.from_dict(cap_data)
            current_rate = self._compute_current_rate(cap.task_type)

            cap_data["current_success_rate"] = round(current_rate, 3)
            cap_data["last_verified"] = datetime.now().isoformat()

            # 检查退化
            degradation = cap.baseline_success_rate - current_rate
            if degradation > cap.degradation_threshold:
                alerts.append({
                    "capability_id": cap.id,
                    "capability_name": cap.name,
                    "baseline": cap.baseline_success_rate,
                    "current": current_rate,
                    "degradation": round(degradation, 3),
                    "threshold": cap.degradation_threshold,
                })
                logger.warning(
                    f"CPE Alert: {cap.name} degraded "
                    f"({cap.baseline_success_rate:.1%} → {current_rate:.1%})"
                )

        # 保存更新后的 current_success_rate
        self._save_registry(registry)
        return alerts

    def protect(self, alerts: List[Dict]):
        """对退化的能力执行保护动作"""
        if not alerts:
            return

        registry = self._load_registry()
        caps = registry.get("capabilities", [])

        for alert in alerts:
            cap_id = alert["capability_id"]
            cap = next((c for c in caps if c["id"] == cap_id), None)
            if cap:
                # 提高验证频率
                cap["verification_interval_hours"] = max(
                    12, cap.get("verification_interval_hours", 72) // 2
                )
                cap["degradation_alert"] = True
                cap["alert_time"] = datetime.now().isoformat()

        self._save_registry(registry)
        logger.info(f"CPE protection activated for {len(alerts)} capabilities")

    def register_capability(self, cap: Capability):
        """注册新的核心能力"""
        registry = self._load_registry()
        caps = registry.get("capabilities", [])

        # 检查是否已存在
        existing = next((c for c in caps if c["id"] == cap.id), None)
        if existing:
            # 更新
            existing.update(cap.to_dict())
        else:
            caps.append(cap.to_dict())

        registry["capabilities"] = caps
        self._save_registry(registry)

    def _compute_current_rate(self, task_type: str, lookback: int = 20) -> float:
        """从 journal 计算当前成功率"""
        entries = self._load_journal_last(lookback)
        type_entries = [e for e in entries if e.get("task_type") == task_type]
        if not type_entries:
            return 0.8  # 无数据时假设基线

        completed = sum(1 for e in type_entries
                       if "FAILED" not in e.get("task_title", "")
                       and "FAILED" not in e.get("result_summary", ""))
        return completed / len(type_entries)

    def _load_journal_last(self, limit: int) -> List[dict]:
        """读取 journal.jsonl 最近记录"""
        try:
            with open(self.journal_path, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return []

        entries = []
        decoder = json.JSONDecoder(strict=False)
        pos = 0
        while pos < len(content):
            stripped = content[pos:].lstrip()
            if not stripped:
                break
            try:
                obj, end = decoder.raw_decode(stripped)
                entries.append(obj)
                next_nl = stripped.find('\n', end)
                if next_nl == -1:
                    break
                pos = pos + (len(content[pos:]) - len(stripped)) + next_nl + 1
            except json.JSONDecodeError:
                next_nl = content.find('\n', pos)
                if next_nl == -1:
                    break
                pos = next_nl + 1

        return entries[-limit:]

    def _load_registry(self) -> dict:
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                return json.loads(f.read(), strict=False)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"capabilities": [], "meta": {"created": datetime.now().isoformat()}}

    def _save_registry(self, registry: dict):
        os.makedirs(os.path.dirname(self.registry_path) or ".", exist_ok=True)
        registry.setdefault("meta", {})["last_updated"] = datetime.now().isoformat()
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)


# ============================================================
# SelfEvolutionEngine — 总控
# ============================================================

class SelfEvolutionEngine:
    """自我进化引擎，每 N 个周期执行一次进化"""

    def __init__(self, state_dir: str, evolution_interval: int = 5):
        """
        Args:
            state_dir: state/ 目录路径
            evolution_interval: 每隔多少个周期执行一次进化
        """
        self.state_dir = state_dir
        self.evolution_interval = evolution_interval
        self.cycle_count_path = os.path.join(state_dir, "evolution_counter.json")

        # 初始化三个子模块
        self.strategy_learner = StrategyLearner(
            journal_path=os.path.join(state_dir, "journal.jsonl"),
            profile_path=os.path.join(state_dir, "strategy_profile.json"),
        )
        self.memory_pruner = MemoryPruner(
            knowledge_path=os.path.join(state_dir, "knowledge.json"),
            prune_log_path=os.path.join(state_dir, "prune_log.jsonl"),
        )
        self.cpe_guard = CPEGuard(
            registry_path=os.path.join(state_dir, "capability_registry.json"),
            journal_path=os.path.join(state_dir, "journal.jsonl"),
        )

    def run_evolution_cycle(self) -> Optional[str]:
        """运行一次进化周期。返回 None 表示跳过。"""
        count = self._get_cycle_count()
        count += 1
        self._save_cycle_count(count)

        if count % self.evolution_interval != 0:
            return None

        logger.info(f"=== Self-Evolution Cycle (every {self.evolution_interval}) ===")
        results = []

        # 1. 策略学习
        try:
            profiles = self.strategy_learner.analyze()
            self.strategy_learner.save(profiles)
            boosts = {k: v.recommended_priority_boost for k, v in profiles.items()
                     if v.recommended_priority_boost != 0}
            results.append(f"📊 策略分析: {len(profiles)} 个画像, "
                         f"{len(boosts)} 个有调整: {boosts}")
        except Exception as e:
            results.append(f"⚠️ 策略学习失败: {e}")

        # 2. 记忆优化
        try:
            actions = self.memory_pruner.prune()
            self.memory_pruner.apply(actions)
            action_summary = defaultdict(int)
            for a in actions:
                action_summary[a.action] += 1
            results.append(f"🧹 记忆优化: {len(actions)} 个操作 "
                         f"{dict(action_summary)}")
        except Exception as e:
            results.append(f"⚠️ 记忆优化失败: {e}")

        # 3. 能力退化检查
        try:
            alerts = self.cpe_guard.check_all()
            if alerts:
                self.cpe_guard.protect(alerts)
                alert_names = [a["capability_name"] for a in alerts]
                results.append(f"🚨 能力退化告警: {', '.join(alert_names)}")
            else:
                results.append("✅ 能力基线正常，无退化")
        except Exception as e:
            results.append(f"⚠️ 能力检查失败: {e}")

        summary = "\n".join(results)
        logger.info(f"Evolution cycle result:\n{summary}")
        return summary

    def get_strategy_boost(self, task_type: str) -> int:
        """获取指定任务类型的优先级调整值（供 TaskQueue 调用）"""
        profiles = self.strategy_learner.load()
        profile = profiles.get(task_type)
        return profile.recommended_priority_boost if profile else 0

    def _get_cycle_count(self) -> int:
        try:
            with open(self.cycle_count_path, "r", encoding="utf-8") as f:
                data = json.loads(f.read(), strict=False)
            return data.get("count", 0)
        except (FileNotFoundError, json.JSONDecodeError):
            return 0

    def _save_cycle_count(self, count: int):
        with open(self.cycle_count_path, "w", encoding="utf-8") as f:
            json.dump({"count": count, "last_run": datetime.now().isoformat()},
                     f, ensure_ascii=False, indent=2)
