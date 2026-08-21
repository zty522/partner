"""SelfReview — capability inventory & gap analysis for Partner self-evolution.

Generates a structured capability inventory from AgentRegistry, SkillRegistry,
EventRegistry, and experience stats, then identifies capability gaps based on
low success rates or missing coverage for requested task types.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CapabilityInventory:
    """Snapshot of Partner's current capabilities."""

    agents: list[dict] = field(default_factory=list)
    """List of dicts with keys: name, capabilities, health_status."""

    skill_count: int = 0
    """Number of registered skills."""

    event_types: list[str] = field(default_factory=list)
    """List of harness event type names."""

    experience_stats: dict = field(default_factory=dict)
    """Aggregate from get_experience_stats(): total, by_type, success_rates."""

    weaknesses: list[str] = field(default_factory=list)
    """List of known weakness descriptions."""


@dataclass
class CapabilityGap:
    """A detected gap between current capabilities and desired coverage."""

    name: str
    description: str
    priority: str  # 'high', 'medium', 'low'
    detection_method: str  # e.g. 'low_success_rate', 'missing_agent_for_task'


# ── Helpers ──────────────────────────────────────────────────────────

_REFERENCE_TASK_TYPES: list[str] = [
    "文献综述",
    "数据分析",
    "蛋白质结构预测",
    "分子对接",
    "单细胞分析",
    "序列比对",
    "分子动力学",
    "通路富集分析",
    "差异表达分析",
    "系统发育分析",
    "基因组注释",
    "变体调用",
    "药物重定位",
    "网络药理学",
    "数据可视化",
    "报告生成",
    "命令行自动化",
    "API集成",
    "工作流编排",
    "表格处理",
]


def _load_config() -> dict:
    """Load evolution_external.yaml from config/."""
    import yaml

    # Try workspace-relative and partner-relative paths
    candidates = [
        os.path.join(os.getcwd(), "config", "evolution_external.yaml"),
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "evolution_external.yaml"),
        os.path.expanduser("~/.partner/config/evolution_external.yaml"),
    ]
    for path in candidates:
        path = os.path.normpath(path)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                return data.get("external_evolution", {})
            except Exception as exc:
                logger.debug("[SELF_REVIEW] failed to load config from %s: %s", path, exc)

    return {}


def _get_health_label(status: str) -> str:
    """Map health status codes to display labels."""
    mapping = {
        "ok": "健康",
        "unknown": "未知",
        "unavailable": "未安装",
        "timeout": "超时",
        "error": "异常",
    }
    return mapping.get(status, status)


# ── Main Class ──────────────────────────────────────────────────────


class SelfReview:
    """Generates capability inventories and identifies capability gaps.

    Uses lazy imports throughout to avoid circular dependency issues with
    AgentRegistry and SkillRegistry.
    """

    def __init__(self, workspace: str | None = None):
        self._workspace = workspace or os.getcwd()
        self._config = _load_config()

    # ── Inventory Generation ────────────────────────────────────────

    def generate_capability_inventory(self) -> CapabilityInventory:
        """Build a snapshot of current capabilities from all registries."""
        logger.info("[SELF_REVIEW] generating capability inventory ...")

        agents = self._collect_agents()
        skill_count = self._count_skills()
        event_types = self._collect_event_types()
        experience_stats = self._collect_experience_stats()
        weaknesses = self._derive_weaknesses(experience_stats, agents)

        inventory = CapabilityInventory(
            agents=agents,
            skill_count=skill_count,
            event_types=event_types,
            experience_stats=experience_stats,
            weaknesses=weaknesses,
        )

        logger.info(
            "[SELF_REVIEW] inventory: %d agents, %d skills, %d events, %d experiences",
            len(agents),
            skill_count,
            len(event_types),
            experience_stats.get("total", 0),
        )
        return inventory

    def _collect_agents(self) -> list[dict]:
        """Load agent manifests via AgentRegistry and run health checks."""
        # Lazy import to avoid circular dependency
        from partner.agents.registry import AgentRegistry  # type: ignore

        registry = AgentRegistry(workspace=self._workspace)
        manifests = registry.list_agents()

        agents: list[dict] = []
        for m in manifests:
            try:
                hc = registry.health_check(m.name)
            except Exception:
                hc = {"status": "unknown", "details": "check failed"}

            agents.append({
                "name": m.name,
                "capabilities": list(m.capabilities),
                "health_status": hc.get("status", "unknown"),
                "health_details": hc.get("details", ""),
                "version": getattr(m, "version", ""),
                "endpoint_type": getattr(m, "endpoint_type", ""),
            })

        return agents

    def _count_skills(self) -> int:
        """Count registered skills via skills_registry.db, fall back to in-memory SkillRegistry."""
        # 优先查持久化技能注册表（skills_registry.db）
        try:
            import sqlite3

            from partner.utils.workspace import get_skills_db_path  # type: ignore
            from partner.workspace.workspace_layout import workspace_root_from_instance  # type: ignore

            root = workspace_root_from_instance(self._workspace)
            db_path = get_skills_db_path(root)
            if os.path.isfile(db_path):
                conn = sqlite3.connect(db_path)
                try:
                    row = conn.execute("SELECT COUNT(*) FROM skills").fetchone()
                    if row and row[0]:
                        return int(row[0])
                finally:
                    conn.close()
        except Exception as exc:
            logger.debug("[SELF_REVIEW] skill db count failed: %s", exc)
        # 回退：内存 SkillRegistry
        try:
            from partner.skills import SkillRegistry  # type: ignore

            registry = SkillRegistry.from_workspace(self._workspace)
            meta = registry.dump_metadata()
            return len(meta.get("skills", []))
        except Exception as exc:
            logger.debug("[SELF_REVIEW] skill count failed: %s", exc)
            return 0

    def _collect_event_types(self) -> list[str]:
        """Collect harness event types from the default EventRegistry."""
        try:
            from partner.mind.harness import default_registry  # type: ignore

            registry = default_registry()
            if hasattr(registry, "_events"):
                return sorted(registry._events.keys())
            return []
        except Exception as exc:
            logger.debug("[SELF_REVIEW] event type collection failed: %s", exc)
            return []

    def _collect_experience_stats(self) -> dict:
        """Load experience stats from evolution_db."""
        try:
            # Lazy import
            from partner.evolution.evolution_db import get_experience_stats  # type: ignore

            stats = get_experience_stats()
            # Add per-type stats by scanning experience keywords
            by_type: dict[str, dict] = {}
            for task_type in _REFERENCE_TASK_TYPES:
                from partner.evolution.evolution_db import get_experiences_by_task_type  # type: ignore

                exps = get_experiences_by_task_type(task_type, limit=50)
                if not exps:
                    continue
                total = len(exps)
                successes = sum(1 for e in exps if e.get("success"))
                by_type[task_type] = {
                    "total": total,
                    "successes": successes,
                    "success_rate": round(successes / max(total, 1), 4),
                }

            stats["by_type"] = by_type
            return stats
        except Exception as exc:
            logger.debug("[SELF_REVIEW] experience stats failed: %s", exc)
            return {"total": 0, "successes": 0, "success_rate": 0.0, "by_agent": [], "by_output": [], "by_type": {}}

    def _derive_weaknesses(self, stats: dict, agents: list[dict]) -> list[str]:
        """Derive known weaknesses from stats and agent coverage."""
        weaknesses: list[str] = []

        # Low overall success rate
        sr = stats.get("success_rate", 0.0)
        if 0 < sr < 0.4:
            weaknesses.append(f"总体成功率偏低 ({sr:.0%})")

        # Low per-type success rates
        for task_type, tstats in stats.get("by_type", {}).items():
            tsr = tstats.get("success_rate", 0.0)
            if 0 < tsr < 0.4:
                weaknesses.append(f"'{task_type}' 成功率僅 {tsr:.0%}")

        # Missing coverage for reference task types
        agent_caps: set[str] = set()
        for a in agents:
            agent_caps.update(c.lower() for c in a.get("capabilities", []))

        # Map task types to expected capability keywords
        task_cap_map: dict[str, list[str]] = {
            "蛋白质结构预测": ["protein", "structure", "alphafold", "esmfold"],
            "分子对接": ["docking", "molecular", "diffdock", "autodock"],
            "单细胞分析": ["single cell", "scrna", "scanpy", "seurat", "cellchat"],
            "序列比对": ["sequence", "alignment", "blast", "minimap"],
            "分子动力学": ["molecular dynamics", "gromacs", "amber", "openmm"],
            "通路富集分析": ["pathway", "enrichment", "gsea", "kegg"],
            "差异表达分析": ["differential", "expression", "deseq", "edger"],
            "系统发育分析": ["phylogeny", "tree", "iqtree", "raxml"],
            "基因组注释": ["genome", "annotation", "prokka", "braker"],
            "变体调用": ["variant", "calling", "gatk", "freebayes", "snpcalling"],
        }

        for task_type, expected_caps in task_cap_map.items():
            covered = any(
                any(ec in ac for ec in expected_caps) for ac in agent_caps
            )
            if not covered:
                weaknesses.append(f"缺少 '{task_type}' 相关 Agent 覆盖")

        return weaknesses

    # ── Gap Identification ──────────────────────────────────────────

    def identify_gaps(self, inventory: CapabilityInventory) -> list[CapabilityGap]:
        """Compare current inventory against the reference model to find gaps.

        Gaps identified by:
        - Task types with < 40% success rate in experience stats
        - Task types users asked about but no agent handles
        """
        gaps: list[CapabilityGap] = []
        seen: set[str] = set()

        # Gap type 1: low success rate per task type
        by_type = inventory.experience_stats.get("by_type", {})
        for task_type, tstats in by_type.items():
            tsr = tstats.get("success_rate", 0.0)
            if 0 < tsr < 0.4:
                name = f"成功率不足: {task_type}"
                if name not in seen:
                    seen.add(name)
                    gaps.append(CapabilityGap(
                        name=name,
                        description=f"'{task_type}' 类任务当前成功率仅 {tsr:.0%}（共 {tstats.get('total', 0)} 条经验），低于 40% 阈值，"
                                    f"需要改进相关 Agent 或引入更专业的工具。",
                        priority="high" if tsr < 0.2 else "medium",
                        detection_method="low_success_rate",
                    ))

        # Gap type 2: missing agent coverage for reference task types
        agent_names = {a["name"] for a in inventory.agents}
        agent_caps_map: dict[str, set[str]] = {}
        for a in inventory.agents:
            agent_caps_map[a["name"]] = {c.lower() for c in a.get("capabilities", [])}

        # Known bioinformatics tools and their capability keywords
        known_tools: dict[str, dict] = {
            "AlphaFold": {"caps": ["protein", "structure", "folding", "alphafold"], "priority": "high"},
            "DiffDock": {"caps": ["docking", "molecular", "diffdock", "ligand"], "priority": "high"},
            "Rosetta": {"caps": ["protein", "design", "rosetta", "folding"], "priority": "medium"},
            "CellChat": {"caps": ["single cell", "cellchat", "cell communication"], "priority": "medium"},
            "Scanpy": {"caps": ["single cell", "scanpy", "scrna"], "priority": "high"},
            "Seurat": {"caps": ["single cell", "seurat", "scrna"], "priority": "medium"},
            "GROMACS": {"caps": ["molecular dynamics", "gromacs", "md simulation"], "priority": "medium"},
            "PLINK": {"caps": ["genetics", "plink", "gwas", "association"], "priority": "low"},
            "BLAST": {"caps": ["sequence", "alignment", "blast", "homology"], "priority": "high"},
            "GATK": {"caps": ["variant", "calling", "gatk", "snpcalling"], "priority": "medium"},
            "DESeq2": {"caps": ["differential", "expression", "deseq", "edger"], "priority": "medium"},
            "IQ-TREE": {"caps": ["phylogeny", "tree", "iqtree", "raxml"], "priority": "low"},
        }

        # Check which known tools are not covered by any agent
        all_covered_caps: set[str] = set()
        for caps in agent_caps_map.values():
            all_covered_caps.update(caps)

        for tool_name, info in known_tools.items():
            # Check if any agent already handles this tool's capabilities
            covered = any(
                any(ec in all_covered_caps for ec in info["caps"])
                for ec in info["caps"]
            )
            if not covered:
                name = f"缺少工具: {tool_name}"
                if name not in seen:
                    seen.add(name)
                    gaps.append(CapabilityGap(
                        name=name,
                        description=f"Partner 未集成 '{tool_name}'，其核心能力（{', '.join(info['caps'][:3])}）在现有 Agent 中无覆盖。",
                        priority=info["priority"],
                        detection_method="missing_agent_for_task",
                    ))

        # Gap type 3: from weaknesses list
        for w in inventory.weaknesses:
            # Avoid duplicates with existing gap names
            gap_name = f"已知不足: {w[:40]}"
            if gap_name not in seen:
                seen.add(gap_name)
                gaps.append(CapabilityGap(
                    name=gap_name,
                    description=w,
                    priority="medium",
                    detection_method="derived_from_weaknesses",
                ))

        gaps.sort(key=lambda g: {"high": 0, "medium": 1, "low": 2}.get(g.priority, 99))
        logger.info("[SELF_REVIEW] identified %d capability gaps", len(gaps))
        return gaps
