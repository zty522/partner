"""CapabilityDiscovery — search for solutions to capability gaps.

Searches the web and known tool databases for solutions that can fill detected
capability gaps, evaluates candidates for feasibility, and returns structured
SolutionCandidate and SolutionEvaluation objects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .self_review import CapabilityGap

logger = logging.getLogger(__name__)


@dataclass
class SolutionCandidate:
    """A discovered candidate solution for a capability gap."""

    name: str
    source: str  # 'github', 'web_search', 'known_tool'
    url: str
    description: str
    stars: int = 0
    language: str = ""


@dataclass
class SolutionEvaluation:
    """Evaluation of a candidate's feasibility and integration approach."""

    feasible: bool
    difficulty: str  # 'easy', 'medium', 'hard'
    integration_type: str  # 'agent_manifest', 'skill', 'event'
    expected_value: str  # 'high', 'medium', 'low'
    notes: str = ""


# ── Known bioinformatics tools database ──────────────────────────────

_KNOWN_BIO_TOOLS: dict[str, dict[str, Any]] = {
    "AlphaFold": {
        "url": "https://github.com/google-deepmind/alphafold",
        "source": "github",
        "description": "蛋白质结构预测工具，基于深度学习预测蛋白质三维结构。支持单体及多聚体结构预测。",
        "stars": 13000,
        "language": "Python",
        "feasible": True,
        "difficulty": "hard",
        "integration_type": "agent_manifest",
        "expected_value": "high",
        "notes": "需要 GPU 和大量计算资源。可作为独立 Agent 集成，通过 CLI 调用。",
    },
    "DiffDock": {
        "url": "https://github.com/gcorso/DiffDock",
        "source": "github",
        "description": "分子对接工具，基于扩散模型预测配体-蛋白质结合构象。",
        "stars": 2800,
        "language": "Python",
        "feasible": True,
        "difficulty": "hard",
        "integration_type": "agent_manifest",
        "expected_value": "high",
        "notes": "需要 GPU。可通过 CLI 集成，或作为 Skill 调用 Python API。",
    },
    "CellChat": {
        "url": "https://github.com/sqjin/CellChat",
        "source": "github",
        "description": "单细胞通讯分析 R 包，推断和分析细胞间通讯网络。",
        "stars": 1100,
        "language": "R",
        "feasible": True,
        "difficulty": "medium",
        "integration_type": "agent_manifest",
        "expected_value": "medium",
        "notes": "R 包，需要安装 R 环境。可通过 CLI 封装调用 R 脚本。",
    },
    "Scanpy": {
        "url": "https://github.com/scverse/scanpy",
        "source": "github",
        "description": "单细胞分析 Python 库，可扩展的单细胞 RNA-seq 数据分析工具。",
        "stars": 2100,
        "language": "Python",
        "feasible": True,
        "difficulty": "easy",
        "integration_type": "skill",
        "expected_value": "high",
        "notes": "纯 Python，易于集成。可直接作为 Skill 或通过 Python API 接口使用。",
    },
    "Seurat": {
        "url": "https://satijalab.org/seurat/",
        "source": "web_search",
        "description": "单细胞分析 R 包，用于单细胞 RNA-seq 数据的质控、分析和探索。",
        "stars": 0,
        "language": "R",
        "feasible": True,
        "difficulty": "medium",
        "integration_type": "agent_manifest",
        "expected_value": "medium",
        "notes": "R 包，需 R 环境。可通过 CLI 封装。",
    },
    "GROMACS": {
        "url": "https://github.com/gromacs/gromacs",
        "source": "github",
        "description": "分子动力学模拟引擎，用于大分子的动力学模拟与分析。",
        "stars": 1800,
        "language": "C++",
        "feasible": True,
        "difficulty": "hard",
        "integration_type": "agent_manifest",
        "expected_value": "medium",
        "notes": "高性能计算工具，需要编译安装。通过 CLI 调用。",
    },
    "BLAST": {
        "url": "https://github.com/ncbi/blast_plus_docs",
        "source": "web_search",
        "description": "NCBI 基本局部比对搜索工具，用于序列相似性搜索和比对。",
        "stars": 0,
        "language": "C++",
        "feasible": True,
        "difficulty": "easy",
        "integration_type": "skill",
        "expected_value": "high",
        "notes": "广泛使用，可通过命令行或 NCBI API 调用。",
    },
    "DESeq2": {
        "url": "https://github.com/mikelove/DESeq2",
        "source": "github",
        "description": "RNA-seq 差异表达分析 R 包，基于负二项分布模型。",
        "stars": 600,
        "language": "R",
        "feasible": True,
        "difficulty": "medium",
        "integration_type": "agent_manifest",
        "expected_value": "medium",
        "notes": "R 包，需 R 环境。可通过 CLI 脚本封装。",
    },
    "GATK": {
        "url": "https://github.com/broadinstitute/gatk",
        "source": "github",
        "description": "基因组分析工具包，用于 variant calling 和基因组数据处理。",
        "stars": 2100,
        "language": "Java",
        "feasible": True,
        "difficulty": "medium",
        "integration_type": "agent_manifest",
        "expected_value": "medium",
        "notes": "Java 工具，需安装 GATK 包。通过命令行调用。",
    },
    "IQ-TREE": {
        "url": "https://github.com/iqtree/iqtree2",
        "source": "github",
        "description": "系统发育推断工具，高效的最大似然法系统发育分析。",
        "stars": 900,
        "language": "C++",
        "feasible": True,
        "difficulty": "easy",
        "integration_type": "skill",
        "expected_value": "medium",
        "notes": "命令行工具，易于集成。",
    },
    "PLINK": {
        "url": "https://github.com/chrchang/plink-ng",
        "source": "github",
        "description": "全基因组关联分析工具集，用于 GWA 分析和群体遗传学。",
        "stars": 700,
        "language": "C++",
        "feasible": True,
        "difficulty": "easy",
        "integration_type": "skill",
        "expected_value": "low",
        "notes": "命令行工具，易于集成。",
    },
    "PyMOL": {
        "url": "https://github.com/schrodinger/pymol-open-source",
        "source": "github",
        "description": "分子可视化系统，蛋白质结构和分子三维结构分析与可视化。",
        "stars": 5000,
        "language": "Python",
        "feasible": True,
        "difficulty": "medium",
        "integration_type": "skill",
        "expected_value": "high",
        "notes": "开源版本可通过 CLI 或 Python API 调用，适合可视化任务。",
    },
    "OpenMM": {
        "url": "https://github.com/openmm/openmm",
        "source": "github",
        "description": "高性能分子模拟库，支持 GPU 加速的分子动力学模拟。",
        "stars": 1600,
        "language": "Python",
        "feasible": True,
        "difficulty": "medium",
        "integration_type": "skill",
        "expected_value": "high",
        "notes": "纯 Python API，可直接作为 Skill 集成。",
    },
    "AutoDock Vina": {
        "url": "https://github.com/ccsb-scripps/AutoDock-Vina",
        "source": "github",
        "description": "分子对接与虚拟筛选工具，用于预测小分子与受体结合模式。",
        "stars": 1400,
        "language": "C++",
        "feasible": True,
        "difficulty": "medium",
        "integration_type": "agent_manifest",
        "expected_value": "high",
        "notes": "命令行工具，有 Python 封装。",
    },
}

# ── Toolkit capability mapping for heuristic detection ──────────────

_TOOLKIT_KEYWORDS: dict[str, list[str]] = {
    "蛋白质结构预测": ["alphafold", "esmfold", "rosettafold", "protein structure prediction", "folding"],
    "分子对接": ["docking", "diffdock", "autodock", "vina", "gnina", "ligand"],
    "单细胞分析": ["scanpy", "seurat", "cellchat", "single cell", "scrna", "monocle"],
    "序列比对": ["blast", "minimap", "muscle", "mafft", "clustal", "alignment"],
    "分子动力学": ["gromacs", "openmm", "amber", "namd", "molecular dynamics", "lammps"],
    "通路富集分析": ["gsea", "enrichr", "kegg", "clusterprofiler", "pathway"],
    "差异表达分析": ["deseq2", "edger", "limma", "differential expression"],
    "系统发育分析": ["iqtree", "raxml", "fasttree", "phylip", "phylogeny", "mrbayes"],
    "基因组注释": ["prokka", "braker", "maker", "genome annotation", "augustus"],
    "变体调用": ["gatk", "freebayes", "bcftools", "samtools", "variant calling", "deepvariant"],
}


class CapabilityDiscovery:
    """Discovers and evaluates solutions for capability gaps."""

    def __init__(self):
        self._session = None  # Lazy HTTP session

    # ── Solution Search ─────────────────────────────────────────────

    async def search_for_solution(
        self, gap: CapabilityGap, adapter=None
    ) -> list[SolutionCandidate]:
        """Search for solutions addressing a capability gap.

        Checks known tools first, then falls back to web search.
        Returns up to configurable max_candidates_per_gap results.
        """
        candidates: list[SolutionCandidate] = []

        # 1. Check known bioinformatics tools
        known_matches = self._check_known_tools(gap)
        candidates.extend(known_matches)

        # 2. Check toolkit keywords mapping
        keyword_matches = self._check_toolkit_keywords(gap)
        for km in keyword_matches:
            if not any(c.name == km.name for c in candidates):
                candidates.append(km)

        # 3. Web search if adapter provided
        if adapter is not None and len(candidates) < 5:
            try:
                web_results = await self._web_search(gap, adapter)
                for wr in web_results:
                    if not any(c.name == wr.name for c in candidates):
                        candidates.append(wr)
            except Exception as exc:
                logger.debug("[CAP_DISCOVERY] web search failed: %s", exc)

        # Limit results (from config or default 3)
        max_candidates = self._get_config().get("max_candidates_per_gap", 3)
        return candidates[:max_candidates]

    def _check_known_tools(self, gap: CapabilityGap) -> list[SolutionCandidate]:
        """Match gap name/description against known bioinformatics tools."""
        results: list[SolutionCandidate] = []
        gap_lower = (gap.name + " " + gap.description).lower()

        for tool_name, info in _KNOWN_BIO_TOOLS.items():
            tool_lower = tool_name.lower()
            # Match if tool name appears in gap text
            if tool_lower in gap_lower:
                results.append(SolutionCandidate(
                    name=tool_name,
                    source=info["source"],
                    url=info["url"],
                    description=info["description"],
                    stars=info.get("stars", 0),
                    language=info.get("language", ""),
                ))
                continue

            # Match if gap description mentions keywords from the tool
            kw_list = _TOOLKIT_KEYWORDS.get(gap.name, [])
            if any(kw in tool_lower for kw in kw_list):
                if not any(r.name == tool_name for r in results):
                    results.append(SolutionCandidate(
                        name=tool_name,
                        source=info["source"],
                        url=info["url"],
                        description=info["description"],
                        stars=info.get("stars", 0),
                        language=info.get("language", ""),
                    ))

        return results

    def _check_toolkit_keywords(self, gap: CapabilityGap) -> list[SolutionCandidate]:
        """Match gap against toolkit keyword map for heuristic discovery."""
        results: list[SolutionCandidate] = []
        gap_lower = (gap.name + " " + gap.description).lower()

        for task_type, keywords in _TOOLKIT_KEYWORDS.items():
            if task_type.lower() in gap_lower:
                for known_name, info in _KNOWN_BIO_TOOLS.items():
                    tool_lower = known_name.lower()
                    if any(kw in tool_lower for kw in keywords):
                        if not any(r.name == known_name for r in results):
                            results.append(SolutionCandidate(
                                name=known_name,
                                source=info["source"],
                                url=info["url"],
                                description=info["description"],
                                stars=info.get("stars", 0),
                                language=info.get("language", ""),
                            ))
        return results

    async def _web_search(self, gap: CapabilityGap, adapter) -> list[SolutionCandidate]:
        """Perform web search for solutions using an adapter.

        The adapter should support async web_search(query) returning
        a list of dicts with at least 'title', 'url', 'snippet'.
        """
        query = f"{gap.name} {gap.description[:100]} open source tool"
        results: list[SolutionCandidate] = []

        try:
            if hasattr(adapter, "web_search"):
                raw = await adapter.web_search(query)
            elif hasattr(adapter, "search"):
                raw = await adapter.search(query)
            else:
                logger.warning("[CAP_DISCOVERY] adapter %r has no search method", type(adapter).__name__)
                return results

            for item in (raw or []):
                title = str(item.get("title", "") or item.get("name", ""))
                url = str(item.get("url", "") or item.get("link", ""))
                snippet = str(item.get("snippet", "") or item.get("description", ""))
                if title and url:
                    results.append(SolutionCandidate(
                        name=title,
                        source="web_search",
                        url=url,
                        description=snippet,
                    ))
        except Exception as exc:
            logger.warning("[CAP_DISCOVERY] web search error: %s", exc)

        return results

    # ── Candidate Evaluation ────────────────────────────────────────

    async def evaluate_candidate(
        self, candidate: SolutionCandidate
    ) -> SolutionEvaluation:
        """Evaluate a solution candidate for feasibility and integration approach.

        Checks whether the tool has a CLI/GitHub/API, estimates difficulty,
        and determines integration type.
        """
        # Check if this is a known tool with pre-evaluated data
        if candidate.name in _KNOWN_BIO_TOOLS:
            info = _KNOWN_BIO_TOOLS[candidate.name]
            return SolutionEvaluation(
                feasible=info["feasible"],
                difficulty=info["difficulty"],
                integration_type=info["integration_type"],
                expected_value=info["expected_value"],
                notes=info["notes"],
            )

        # Heuristic evaluation for unknown tools
        return self._heuristic_evaluate(candidate)

    def discover_bioinformatics_tools(self) -> dict:
        """Scan system PATH for installed bioinformatics tools not yet in Partner.
        
        Returns:
            dict with keys: installed_not_registered, unavailable, already_registered
        """
        import shutil
        result = {
            "installed_not_registered": [],
            "unavailable": [],
            "already_registered": [],
        }
        
        # Check what bioinformatics tools are on PATH
        from ..bioinformatics.cli_ops import CLI_TOOLS, scan_installed_tools
        scan_result = scan_installed_tools()
        
        # Check what's already registered (check dispatch route)
        from ..bioinformatics import dispatch, _resolve_tool_from_task
        
        for name, info in scan_result.get("installed", {}).items():
            # Check if already reachable via dispatch
            test_task = f"运行{name}"
            resolved = _resolve_tool_from_task(test_task)
            if resolved == "cli" and name in ("samtools", "seqkit", "bwa", "bowtie2", "fastqc"):
                # Already partially supported via cli route, but no dedicated wrapper
                result["installed_not_registered"].append({
                    "name": name,
                    "path": info["path"],
                    "version": info.get("version", "unknown"),
                    "description": info.get("description", ""),
                    "reason": "通用 CLI 路由可用，但无专用 Python 封装",
                })
            elif resolved:
                result["already_registered"].append({
                    "name": name, "path": info["path"], "version": info.get("version", ""),
                })
            else:
                result["installed_not_registered"].append({
                    "name": name,
                    "path": info["path"],
                    "version": info.get("version", "unknown"),
                    "description": info.get("description", ""),
                    "reason": "未接入 Partner",
                })
        
        for name, info in scan_result.get("unavailable", {}).items():
            result["unavailable"].append({
                "name": name,
                "install_cmd": info.get("install_cmd", ""),
                "description": info.get("description", ""),
            })
        
        return result

    def _heuristic_evaluate(
        self, candidate: SolutionCandidate
    ) -> SolutionEvaluation:
        """Heuristic evaluation based on candidate metadata."""
        name_lower = candidate.name.lower()
        desc_lower = candidate.description.lower()
        url_lower = candidate.url.lower()

        # Determine integration type
        integration_type = "agent_manifest"  # default
        if any(kw in desc_lower or kw in name_lower for kw in ["python", "library", "api", "sdk"]):
            integration_type = "skill"
        elif any(kw in desc_lower or kw in name_lower for kw in ["pipeline", "workflow", "event", "hook"]):
            integration_type = "event"

        # Determine difficulty
        stars = candidate.stars
        if stars >= 5000:
            # Well-established, well-documented tools
            difficulty = "easy"
        elif stars >= 1000:
            difficulty = "medium"
        else:
            difficulty = "hard"

        # Check for CLI/GitHub presence
        has_github = "github.com" in url_lower
        has_cli = any(kw in desc_lower for kw in ["cli", "command", "command-line", "terminal"])
        has_api = any(kw in desc_lower for kw in ["api", "rest", "http", "python api"])

        if has_github and (has_cli or has_api):
            difficulty = "easy" if stars >= 1000 else "medium"
            integration_type = integration_type
        elif has_github and not has_cli:
            difficulty = "medium"
        elif not has_github:
            difficulty = "hard"

        # Determine expected value
        expected_value = "medium"
        if integration_type == "skill" and has_api:
            expected_value = "high"
        elif stars >= 10000:
            expected_value = "high"
        elif stars < 100:
            expected_value = "low"

        notes_parts = []
        if has_github:
            notes_parts.append("GitHub 仓库可用")
        if has_cli:
            notes_parts.append("支持命令行调用")
        if has_api:
            notes_parts.append("提供 API 接口")
        notes = "，".join(notes_parts) if notes_parts else "需进一步调研可行性"

        return SolutionEvaluation(
            feasible=True,
            difficulty=difficulty,
            integration_type=integration_type,
            expected_value=expected_value,
            notes=notes,
        )

    # ── Config ──────────────────────────────────────────────────────

    def _get_config(self) -> dict:
        """Load configuration from evolution_external.yaml."""
        import os

        import yaml

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
                except Exception:
                    continue
        return {}
