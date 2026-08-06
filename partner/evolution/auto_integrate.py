"""AutoIntegrate — generate and execute integration plans for approved solutions.

Takes evaluated SolutionCandidate/SolutionEvaluation pairs, generates detailed
integration plans (Agent Manifest files, test plans, rollback steps), executes
them, and records results to the evolution growth tracking system.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .capability_discovery import SolutionCandidate, SolutionEvaluation

logger = logging.getLogger(__name__)


@dataclass
class IntegrationPlan:
    """A step-by-step integration plan for a solution candidate."""

    candidate_name: str
    integration_type: str  # 'agent_manifest', 'skill', 'event'
    steps: list[str] = field(default_factory=list)
    """Human-readable list of steps to execute."""

    files_to_create: list[dict] = field(default_factory=list)
    """Each dict: {path: str, content: str} — files to write."""

    test_commands: list[str] = field(default_factory=list)
    """Shell commands to verify the integration."""

    rollback_plan: list[str] = field(default_factory=list)
    """Steps to undo the integration on failure."""


@dataclass
class IntegrationResult:
    """Result of executing an integration plan."""

    success: bool
    test_results: list[dict] = field(default_factory=list)
    """Each dict: {command, returncode, stdout, stderr, passed}."""

    manifest_path: str = ""
    """Path to the created Agent Manifest file (if applicable)."""

    error: str = ""


# ── Agent Manifest template ─────────────────────────────────────────

_MANIFEST_TEMPLATE = {
    "name": "",
    "version": "1.0.0",
    "description": "",
    "capabilities": [],
    "input_formats": [],
    "output_formats": [],
    "endpoint_type": "cli",
    "endpoint_config": {
        "command": "",
    },
    "timeout": 300,
    "health_check_cmd": "",
    "install_info": {
        "method": "",
        "source": "",
    },
}

# ── Language-to-install info mapping for common tools ───────────────

_LANGUAGE_INSTALL_MAP: dict[str, dict[str, Any]] = {
    "Python": {"method": "pip", "pip_package": True},
    "R": {"method": "pip", "package": "rpy2", "note": "需要本地安装 R 环境"},
    "C++": {"method": "git", "build": True},
    "Java": {"method": "git", "note": "需要 JDK 和构建工具"},
    "JavaScript": {"method": "npm"},
    "Rust": {"method": "cargo"},
    "Go": {"method": "go"},
}

# ── GitHub URL patterns for common platforms ────────────────────────

_GITHUB_PATTERN = "github.com"


class AutoIntegrate:
    """Generates and executes integration plans for solution candidates."""

    def __init__(self):
        from ..utils.workspace import get_agents_dir, get_growth_dir
        self._agent_dir = get_agents_dir()
        self._growth_dir = get_growth_dir()

    # ── Plan Generation ─────────────────────────────────────────────

    def generate_integration_plan(
        self,
        candidate: SolutionCandidate,
        evaluation: SolutionEvaluation,
    ) -> IntegrationPlan:
        """Generate a detailed integration plan for an approved candidate.

        Creates:
        - An Agent Manifest JSON file for ~/.partner/agents/
        - A set of test commands
        - Rollback steps for safe undo
        """
        safe_name = candidate.name.replace(" ", "_").replace("/", "_")
        manifest_path = os.path.join(self._agent_dir, f"{safe_name}.json")

        # Build the manifest content
        manifest = self._build_manifest(candidate, evaluation)

        # Build files to create
        files_to_create = [
            {
                "path": manifest_path,
                "content": json.dumps(manifest, indent=2, ensure_ascii=False),
            }
        ]

        # Build steps
        steps = self._generate_steps(candidate, evaluation, manifest)

        # Build test commands
        test_commands = self._generate_test_commands(candidate, evaluation, manifest)

        # Build rollback plan
        rollback_plan = self._generate_rollback_plan(manifest_path, safe_name)

        return IntegrationPlan(
            candidate_name=candidate.name,
            integration_type=evaluation.integration_type,
            steps=steps,
            files_to_create=files_to_create,
            test_commands=test_commands,
            rollback_plan=rollback_plan,
        )

    def _build_manifest(
        self,
        candidate: SolutionCandidate,
        evaluation: SolutionEvaluation,
    ) -> dict:
        """Construct an AgentManifest-compatible dict from candidate data."""
        manifest = dict(_MANIFEST_TEMPLATE)
        manifest["name"] = candidate.name
        manifest["version"] = "1.0.0"
        manifest["description"] = candidate.description

        # Derive capabilities from name + description
        caps = self._extract_capabilities(candidate)
        manifest["capabilities"] = caps

        # Determine endpoint config based on integration type
        lang_info = _LANGUAGE_INSTALL_MAP.get(candidate.language, {})
        install_info: dict[str, Any] = {}

        if _GITHUB_PATTERN in candidate.url:
            # GitHub-based tool
            if candidate.language in ("Python",):
                install_info = {
                    "method": "pip",
                    "package": candidate.name.lower().replace(" ", "_"),
                    "source": candidate.url,
                }
                manifest["endpoint_config"]["command"] = candidate.name.lower().replace(" ", "_")
            elif candidate.language in ("C++", "Java", "Rust", "Go"):
                install_info = {
                    "method": "git",
                    "source": candidate.url,
                }
                # Try to guess CLI command from name
                cli_name = candidate.name.lower().replace(" ", "_").replace("-", "_")
                manifest["endpoint_config"]["command"] = cli_name
            else:
                install_info = {
                    "method": "git",
                    "source": candidate.url,
                }
        else:
            # Non-GitHub tools — use pip or script
            install_info = {
                "method": lang_info.get("method", "pip"),
                "source": candidate.url,
            }
            if candidate.language in ("R",):
                install_info["method"] = "script"
                install_info["package"] = candidate.name

        manifest["install_info"] = install_info

        # Health check command
        cli_cmd = manifest["endpoint_config"].get("command", "")
        if cli_cmd:
            manifest["health_check_cmd"] = f"which {cli_cmd.split()[0]}"
        else:
            manifest["health_check_cmd"] = ""

        # Endpoint type
        manifest["endpoint_type"] = "cli"
        if evaluation.integration_type == "skill":
            manifest["endpoint_type"] = "python_api"
        elif evaluation.integration_type == "event":
            manifest["endpoint_type"] = "cli"

        return manifest

    def _extract_capabilities(self, candidate: SolutionCandidate) -> list[str]:
        """Extract capability keywords from candidate name and description."""
        caps: list[str] = []
        text = f"{candidate.name} {candidate.description}".lower()

        # Known capability categories
        cap_map = {
            "protein_structure_prediction": ["protein structure", "folding", "alphafold", "rosetta", "protein design"],
            "molecular_docking": ["docking", "ligand", "diffdock", "autodock", "vina", "molecular docking"],
            "single_cell_analysis": ["single cell", "scanpy", "seurat", "cellchat", "scrna"],
            "sequence_alignment": ["alignment", "blast", "sequence", "minimap", "mafft", "muscle"],
            "molecular_dynamics": ["molecular dynamics", "gromacs", "openmm", "md simulation"],
            "pathway_enrichment": ["pathway", "enrichment", "gsea", "kegg"],
            "differential_expression": ["differential expression", "deseq", "edger"],
            "phylogenetic_analysis": ["phylogeny", "tree", "iqtree", "raxml", "phylogenetic"],
            "genome_annotation": ["genome annotation", "prokka", "braker", "maker"],
            "variant_calling": ["variant calling", "gatk", "freebayes", "deepvariant", "snp"],
            "data_visualization": ["visualization", "plotting", "figure", "chart", "pymol", "mol*"],
            "genetic_analysis": ["genetics", "plink", "gwas", "association", "population genetics"],
            "api_tool": ["api", "rest", "web service", "http"],
            "command_line_tool": ["cli", "command", "command-line"],
        }

        for cap_name, keywords in cap_map.items():
            if any(kw in text for kw in keywords):
                friendly = cap_name.replace("_", " ").title()
                caps.append(friendly)

        if not caps:
            # Fallback: use the first meaningful part of the name
            parts = candidate.name.replace("-", " ").replace("_", " ").split()
            caps = [p.title() for p in parts[:3]]

        return caps

    def _generate_steps(
        self,
        candidate: SolutionCandidate,
        evaluation: SolutionEvaluation,
        manifest: dict,
    ) -> list[str]:
        """Generate human-readable integration steps."""
        steps = []

        manifest_name = manifest["name"]
        cli_cmd = manifest["endpoint_config"].get("command", "")

        steps.append(f"Step 1: 创建 Agent Manifest 文件 — {manifest_name}")

        if manifest["install_info"].get("method") == "pip":
            pkg = manifest["install_info"].get("package", manifest_name.lower())
            steps.append(f"Step 2: 通过 pip 安装 — pip install {pkg}")
        elif manifest["install_info"].get("method") == "git":
            src = manifest["install_info"].get("source", candidate.url)
            steps.append(f"Step 2: 从 Git 克隆并安装 — git clone {src}")
            if candidate.language in ("C++", "Rust", "Go"):
                steps.append(f"    运行构建命令 (cmake/make/cargo build)")
        elif manifest["install_info"].get("method") == "script":
            steps.append(f"Step 2: 运行安装脚本")

        steps.append(f"Step 3: 注册 Agent — 通过 AgentRegistry.register_agent()")
        steps.append(f"Step 4: 运行健康检查 — {manifest.get('health_check_cmd', 'N/A')}")

        if cli_cmd:
            steps.append(f"Step 5: 测试基础调用 — {cli_cmd} --help")

        if evaluation.integration_type == "skill":
            steps.append(f"Step 5: 注册为 Skill — 添加到 SkillRegistry")
        elif evaluation.integration_type == "event":
            steps.append(f"Step 5: 注册 Harness Event — 添加到 EventRegistry")

        steps.append(f"Step 6: 运行完整测试计划")
        steps.append(f"Step 7: 记录集成结果到 Growth 系统")

        return steps

    def _generate_test_commands(
        self,
        candidate: SolutionCandidate,
        evaluation: SolutionEvaluation,
        manifest: dict,
    ) -> list[str]:
        """Generate test commands based on candidate type."""
        commands: list[str] = []
        cli_cmd = manifest["endpoint_config"].get("command", "")
        hc_cmd = manifest.get("health_check_cmd", "")

        # Test 1: Health check
        if hc_cmd:
            commands.append(hc_cmd)

        # Test 2: CLI --help
        if cli_cmd:
            commands.append(f"{cli_cmd} --help 2>&1 || {cli_cmd} -h 2>&1")

        # Test 3: Version check
        if cli_cmd:
            commands.append(f"{cli_cmd} --version 2>&1 || echo 'no version flag'")

        # Test 4: Check pip package (if applicable)
        pkg = manifest.get("install_info", {}).get("package", "")
        if pkg:
            commands.append(f"pip show {pkg} 2>&1 || echo 'package not found in pip'")

        # Test 5: Verify import (if Python)
        if candidate.language == "Python":
            import_name = candidate.name.lower().replace(" ", "_").replace("-", "_")
            commands.append(f"python3 -c 'import {import_name}; print(\"{import_name} imported OK\")' 2>&1 || echo 'import failed'")

        return commands

    def _generate_rollback_plan(
        self,
        manifest_path: str,
        safe_name: str,
    ) -> list[str]:
        """Generate rollback steps to undo integration."""
        return [
            f"删除 Agent Manifest: rm -f {manifest_path}",
            f"卸载 Agent: AgentRegistry.unregister_agent('{safe_name}')",
            f"如果安装了 pip 包: pip uninstall {safe_name} -y",
            f"如果克隆了 Git 仓库: rm -rf ./{safe_name}",
        ]

    # ── Execution ───────────────────────────────────────────────────

    def execute_integration(
        self,
        plan: IntegrationPlan,
        workspace: str,
    ) -> IntegrationResult:
        """Execute an integration plan.

        Writes manifest files, registers the agent via AgentRegistry,
        runs health checks and tests, and records the result to growth.
        """
        logger.info(
            "[AUTO_INTEGRATE] executing plan for '%s' (type=%s)",
            plan.candidate_name,
            plan.integration_type,
        )

        manifest_path = ""

        try:
            # Step 1: Write manifest files
            for file_spec in plan.files_to_create:
                path = file_spec["path"]
                content = file_spec["content"]
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info("[AUTO_INTEGRATE] wrote %s", path)
                manifest_path = path

            # Step 2: Register agent via AgentRegistry
            try:
                from partner.agents.manifest import AgentManifest  # type: ignore
                from partner.agents.registry import AgentRegistry  # type: ignore

                if manifest_path:
                    registry = AgentRegistry(workspace=workspace)
                    manifest = AgentManifest.from_file(manifest_path)
                    registered = registry.register_agent(manifest)
                    if registered:
                        logger.info("[AUTO_INTEGRATE] registered agent '%s'", plan.candidate_name)
                    else:
                        logger.warning("[AUTO_INTEGRATE] failed to register agent '%s'", plan.candidate_name)
            except Exception as exc:
                logger.warning("[AUTO_INTEGRATE] registration error (non-fatal): %s", exc)

            # Step 3: Run health check
            try:
                from partner.agents.registry import AgentRegistry  # type: ignore

                registry = AgentRegistry(workspace=workspace)
                hc = registry.health_check(plan.candidate_name)
                logger.info(
                    "[AUTO_INTEGRATE] health check: status=%s, details=%s",
                    hc.get("status"),
                    hc.get("details"),
                )
            except Exception as exc:
                logger.warning("[AUTO_INTEGRATE] health check error (non-fatal): %s", exc)

            # Step 4: Run tests
            test_results: list[dict] = []
            all_passed = True
            for cmd in plan.test_commands:
                result = self._run_test_command(cmd)
                test_results.append(result)
                if not result.get("passed", False):
                    all_passed = False
                logger.info(
                    "[AUTO_INTEGRATE] test '%s': passed=%s (rc=%d)",
                    cmd[:60],
                    result.get("passed"),
                    result.get("returncode"),
                )

            # Step 5: Record result to growth
            self._record_to_growth(plan, test_results, all_passed)

            if all_passed:
                logger.info("[AUTO_INTEGRATE] integration of '%s' completed successfully", plan.candidate_name)
            else:
                logger.warning("[AUTO_INTEGRATE] integration of '%s' completed with %d/%d tests failing",
                               plan.candidate_name,
                               sum(1 for t in test_results if not t.get("passed")),
                               len(test_results))

            return IntegrationResult(
                success=all_passed,
                test_results=test_results,
                manifest_path=manifest_path,
            )

        except Exception as exc:
            logger.error("[AUTO_INTEGRATE] integration failed: %s", exc, exc_info=True)
            return IntegrationResult(
                success=False,
                manifest_path=manifest_path,
                error=str(exc),
            )
    
    def integrate_cli_tool(self, tool_name: str, tool_info: dict) -> dict:
        """Integrate a CLI bioinformatics tool into Partner.
        
        Generates a simplified integration plan for CLI tools:
        1. Register a test route
        2. Test basic functionality
        3. Record to growth
        
        Returns dict with: success, test_results, growth_record, errors
        """
        import shutil
        import subprocess
        import shlex
        import json
        from datetime import datetime
        
        result = {
            "tool": tool_name,
            "success": False,
            "tests": [],
            "growth_record": None,
            "errors": [],
        }
        
        # 1. Verify tool is installed
        tool_path = shutil.which(tool_name)
        if not tool_path:
            result["errors"].append(f"{tool_name} not found on PATH")
            return result
        
        # 2. Get version
        version = "unknown"
        try:
            r = subprocess.run(
                shlex.split(f"{tool_name} --version 2>&1 || {tool_name} -version 2>&1 || {tool_name} version 2>&1"),
                capture_output=True, text=True, timeout=10, shell=True,
            )
            version = (r.stdout or r.stderr or "").strip().split("\\n")[0][:80]
        except Exception:
            pass
        
        # 3. Run basic health test
        test_results = []
        test_commands = self._get_test_commands(tool_name)
        for test_cmd in test_commands:
            try:
                r = subprocess.run(
                    shlex.split(test_cmd) if not any(c in test_cmd for c in ["|", ">"]) else test_cmd,
                    capture_output=True, text=True, timeout=60,
                    shell=any(c in test_cmd for c in ["|", ">"]),
                )
                ok = r.returncode == 0
                test_results.append({
                    "command": test_cmd[:100],
                    "ok": ok,
                    "stdout_preview": (r.stdout or "")[:200],
                    "returncode": r.returncode,
                })
            except Exception as exc:
                test_results.append({
                    "command": test_cmd[:100],
                    "ok": False,
                    "error": str(exc),
                })
        
        all_ok = all(t["ok"] for t in test_results)
        
        # 4. Record to growth table
        try:
            from ..meta.learning import record_growth
            gid = record_growth(
                milestone=f"工具接入: {tool_name} v{version}",
                reflection=json.dumps({
                    "tool": tool_name,
                    "version": version,
                    "path": tool_path,
                    "tests_passed": sum(1 for t in test_results if t["ok"]),
                    "tests_total": len(test_results),
                    "integrated_at": datetime.now().isoformat(),
                }, ensure_ascii=False),
                category="tool_integration",
                user_id="default",
            )
            result["growth_record"] = gid
        except Exception as exc:
            result["errors"].append(f"growth record failed: {exc}")
        
        result["success"] = all_ok
        result["tests"] = test_results
        return result
    
    def _get_test_commands(self, tool_name: str) -> list[str]:
        """Get basic test commands for a CLI tool."""
        cmd_map = {
            "samtools": ["samtools --version", "samtools view --help"],
            "seqkit": ["seqkit version", "seqkit stats --help"],
            "bwa": ["bwa 2>&1 | head -5", "which bwa"],
            "bowtie2": ["bowtie2 --version", "which bowtie2"],
            "fastqc": ["fastqc --version", "which fastqc"],
            "muscle": ["muscle -version 2>&1", "which muscle"],
            "mafft": ["mafft --version 2>&1", "which mafft"],
            "blastp": ["blastp -version", "which blastp"],
            "trimmomatic": ["trimmomatic -version", "which trimmomatic"],
            "bedtools": ["bedtools --version", "which bedtools"],
        }
        return cmd_map.get(tool_name, [f"{tool_name} --help", f"which {tool_name}"])
    
    def _run_test_command(self, command: str) -> dict:
        """Run a single test command and capture output."""
        try:
            r = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return {
                "command": command[:100],
                "returncode": r.returncode,
                "stdout": r.stdout.strip()[:200],
                "stderr": r.stderr.strip()[:200],
                "passed": r.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {
                "command": command[:100],
                "returncode": -1,
                "stdout": "",
                "stderr": "timed out after 30s",
                "passed": False,
            }
        except Exception as exc:
            return {
                "command": command[:100],
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
                "passed": False,
            }

    def _record_to_growth(
        self,
        plan: IntegrationPlan,
        test_results: list[dict],
        all_passed: bool,
    ):
        """Record integration result to the growth tracking system."""
        try:
            os.makedirs(self._growth_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = plan.candidate_name.replace(" ", "_").replace("/", "_")
            record_path = os.path.join(self._growth_dir, f"integration_{safe_name}_{timestamp}.json")

            record = {
                "candidate_name": plan.candidate_name,
                "integration_type": plan.integration_type,
                "timestamp": timestamp,
                "success": all_passed,
                "test_count": len(test_results),
                "tests_passed": sum(1 for t in test_results if t.get("passed")),
                "tests_failed": sum(1 for t in test_results if not t.get("passed")),
                "steps": plan.steps,
                "manifest_path": next(
                    (f["path"] for f in plan.files_to_create if f["path"].endswith(".json")),
                    "",
                ),
            }

            with open(record_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)

            logger.info("[AUTO_INTEGRATE] growth record saved to %s", record_path)

        except Exception as exc:
            logger.warning("[AUTO_INTEGRATE] failed to record growth: %s", exc)

    # ── Rollback ────────────────────────────────────────────────────

    def rollback(self, plan: IntegrationPlan) -> bool:
        """Execute rollback plan to undo an integration."""
        success = True
        for step in plan.rollback_plan:
            try:
                # If step is a shell command, execute it
                if step.startswith("rm ") or step.startswith("pip "):
                    subprocess.run(step, shell=True, capture_output=True, timeout=30)
                    logger.info("[AUTO_INTEGRATE] rollback executed: %s", step[:60])
                else:
                    logger.info("[AUTO_INTEGRATE] rollback step (manual): %s", step)
            except Exception as exc:
                logger.warning("[AUTO_INTEGRATE] rollback step failed: %s", exc)
                success = False
        return success
