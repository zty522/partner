"""Code Learner — generic code learning engine for Partner's self-evolution.

Core responsibility: fetch project code from GitHub → analyze structure →
extract components and patterns → generate structured knowledge.

This module is GENERIC — it does not hardcode "frontend" or any specific
framework. It adapts its analysis to whatever type of code repository it
encounters, using heuristics to detect the language, framework, and
architecture patterns present in the code.

Usage:
    from partner.evolution.code_learner import CodeLearner

    # Learn from a GitHub repo
    knowledge = await CodeLearner.learn(
        repo_urls=["https://github.com/user/project"],
        focus_area="frontend",  # optional hint for analysis depth
    )

    # Or learn from a local directory
    knowledge = await CodeLearner.learn_from_local(
        local_path="/path/to/code",
        focus_area="backend",
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# File extensions by language category
EXTENSIONS_BY_LANGUAGE = {
    "python": {".py", ".pyi", ".pyx", ".pxd"},
    "typescript": {".ts", ".tsx"},
    "javascript": {".js", ".jsx", ".mjs", ".cjs"},
    "vue": {".vue"},
    "svelte": {".svelte"},
    "rust": {".rs", ".rlib"},
    "go": {".go"},
    "java": {".java", ".kt", ".scala"},
    "cpp": {".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx"},
    "c": {".c", ".h"},
    "css": {".css", ".scss", ".sass", ".less", ".styl"},
    "ruby": {".rb"},
    "php": {".php"},
    "swift": {".swift"},
    "kotlin": {".kt", ".kts"},
    "yaml": {".yaml", ".yml"},
    "json": {".json"},
    "markdown": {".md", ".mdx"},
    "html": {".html", ".htm"},
    "shell": {".sh", ".bash", ".zsh"},
}

# Directories to skip during traversal
EXCLUDED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".env", "dist", "build", ".next", ".nuxt", ".output",
    "target", "vendor", ".tox", ".eggs", "eggs", "egg-info",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "bower_components", ".sass-cache", ".gradle",
    "bin", "obj", "Debug", "Release", "x64", "x86",
    "coverage", ".nyc_output",
}
# Additional subdirectory-name fragments to skip (runtime/compiled dirs)
EXCLUDED_DIR_FRAGMENTS = {"node-v", ".cache", "cache", "runtime", "syslib"}

# Language-specific patterns
CLASS_PATTERNS: dict[str, re.Pattern] = {
    "python": re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
    "typescript": re.compile(
        r"(?:export\s+)?(?:default\s+)?(?:class|interface|type|enum|abstract\s+class)\s+(\w+)"
    ),
    "javascript": re.compile(
        r"(?:export\s+)?(?:default\s+)?(?:class|function)\s+(\w+)"
    ),
    "java": re.compile(
        r"(?:public|private|protected|static|abstract|final|sealed)?\s*"
        r"(?:class|interface|enum|record)\s+(\w+)"
    ),
    "rust": re.compile(
        r"(?:pub\s+)?(?:struct|enum|trait|impl|type|fn)\s+(\w+)"
    ),
    "go": re.compile(
        r"(?:type\s+(\w+)\s+struct|type\s+(\w+)\s+interface)"
    ),
    "cpp": re.compile(
        r"(?:class|struct)\s+(\w+)"
    ),
}

FUNCTION_PATTERNS: dict[str, re.Pattern] = {
    "python": re.compile(r"^\s*def\s+(\w+)\s*\(", re.MULTILINE),
    "typescript": re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\("
        r"|(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=]\s*(?:async\s*)?\("
    ),
    "javascript": re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\("
        r"|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("
    ),
    "rust": re.compile(r"(?:pub\s+)?(?:unsafe\s+)?fn\s+(\w+)"),
    "go": re.compile(r"^func\s+(\w+)"),
}

IMPORT_PATTERNS: dict[str, list[re.Pattern]] = {
    "python": [
        re.compile(r"^import\s+(\S+)"),
        re.compile(r"^from\s+(\S+)\s+import"),
    ],
    "typescript": [
        re.compile(r"import\s+(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"import\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"require\s*\(\s*['\"]([^'\"]+)['\"]"),
    ],
    "javascript": [
        re.compile(r"import\s+(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"require\s*\(\s*['\"]([^'\"]+)['\"]"),
    ],
}

# Architecture directory pattern → role mapping
ARCHITECTURE_DIR_PATTERNS: dict[str, str] = {
    # Frontend
    "components": "ui_component",
    "pages": "ui_page",
    "views": "ui_page",
    "screens": "ui_page",
    "layouts": "ui_layout",
    "ui": "ui_component",
    "styles": "ui_style",
    "theme": "ui_theme",
    "assets": "ui_asset",
    # Backend / API
    "api": "api_endpoint",
    "routes": "api_route",
    "controllers": "api_controller",
    "handlers": "api_handler",
    "middleware": "api_middleware",
    # Data
    "models": "data_model",
    "schemas": "data_schema",
    "migrations": "data_migration",
    "database": "data_access",
    "repositories": "data_access",
    "dao": "data_access",
    # Core
    "core": "core_module",
    "utils": "utility",
    "helpers": "utility",
    "lib": "library",
    "config": "configuration",
    "services": "service",
    "providers": "service",
    # Testing
    "tests": "test",
    "spec": "test",
    "__tests__": "test",
    # Infrastructure
    "docker": "infra_container",
    "kubernetes": "infra_orchestration",
    "ci": "infra_ci",
    "deploy": "infra_deploy",
    "scripts": "script",
    "bin": "script",
    "cli": "cli",
    "cmd": "cli_command",
    "tools": "tool",
}

# ── Data Types ─────────────────────────────────────────────────────────────────


@dataclass
class FileInfo:
    """Information about a single source file in the repository."""

    path: str  # relative path from repo root
    language: str  # detected language
    lines: int
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    summary: str = ""
    content: str = ""  # cached content, may be truncated


@dataclass
class DirectoryEntry:
    """Structural entry in the directory tree."""

    path: str
    entry_type: str  # "dir" | "file"
    file_count: int = 0
    children: list[str] = field(default_factory=list)
    role: str = ""  # architectural role if detected


@dataclass
class RepositoryKnowledge:
    """Complete structured knowledge about a code repository.

    This is the output of the learning process — a rich, structured
    representation that can be consumed by pattern_extractor, plan_formation,
    etc.
    """

    # Identity
    source_name: str
    source_url: str = ""
    local_path: str = ""

    # Languages & framework detection
    primary_language: str = ""
    languages: dict[str, int] = field(default_factory=dict)  # lang → file count
    frameworks: list[str] = field(default_factory=list)
    package_manager: str = ""

    # Structure
    directory_tree: list[DirectoryEntry] = field(default_factory=list)
    architecture_roles: dict[str, int] = field(default_factory=dict)  # role → count
    file_count: int = 0
    dir_count: int = 0
    total_lines: int = 0

    # Components
    files: list[FileInfo] = field(default_factory=list)
    all_classes: list[str] = field(default_factory=list)
    all_functions: list[str] = field(default_factory=list)

    # Dependencies
    dependencies: dict[str, list[str]] = field(default_factory=dict)  # file → imports
    external_dependencies: list[str] = field(default_factory=list)

    # Metadata
    analyzed_at: str = ""
    analysis_duration_s: float = 0.0

    # Type-specific analysis (populated when focus_area is specified)
    ui_components: list[dict] = field(default_factory=list)
    api_endpoints: list[dict] = field(default_factory=list)
    design_tokens: dict[str, Any] = field(default_factory=dict)
    key_insights: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Core Module
# ═══════════════════════════════════════════════════════════════════════════════


class CodeLearner:
    """Generic code learning engine.

    Designed to be framework-agnostic. It can learn from any codebase,
    but applies deeper analysis when a focus_area hint is provided.
    """

    # Repository cache directory
    _CACHE_DIR = Path(tempfile.gettempdir()) / "partner_code_learner"

    # ── Public API ─────────────────────────────────────────────────────────

    @classmethod
    async def learn(
        cls,
        repo_urls: list[str],
        focus_area: str = "",
        workspace: str | None = None,
        cache_dir: str | Path | None = None,
        progress_callback=None,
    ) -> list[RepositoryKnowledge]:
        """Fetch and analyze one or more GitHub repositories.

        Args:
            repo_urls: GitHub URLs or shorthand names (e.g. "Hermes" → auto-resolved).
            focus_area: Optional hint for deeper analysis (e.g. "frontend", "backend", "api").
            workspace: Optional workspace path for Partner data.
            cache_dir: Override cache directory for cloned repos.
            progress_callback: Async callable for progress reporting.

        Returns:
            List of RepositoryKnowledge objects, one per repo.
        """
        if cache_dir:
            cls._CACHE_DIR = Path(cache_dir)

        results: list[RepositoryKnowledge] = []

        for i, url in enumerate(repo_urls):
            if progress_callback:
                await progress_callback(
                    f"[{i + 1}/{len(repo_urls)}] 正在获取 {url}..."
                )

            # Resolve named sources
            resolved_url, local_path = cls._resolve_source(url, workspace)

            try:
                # Step 1: Fetch/clone
                local_dir = await cls._fetch_repo(resolved_url, local_path)
                if not local_dir or not os.path.isdir(local_dir):
                    logger.warning("Failed to fetch repository: %s", url)
                    continue

                if progress_callback:
                    await progress_callback(
                        f"✅ 已获取 {url} → {local_dir}"
                    )

                # Step 2: Analyze structure
                knowledge = cls._analyze_all(local_dir, url, focus_area)
                results.append(knowledge)

                if progress_callback:
                    await progress_callback(
                        f"📊 {url}: {knowledge.file_count} 个文件, "
                        f"{len(knowledge.all_classes)} 个类, "
                        f"主语言 {knowledge.primary_language}"
                    )

            except Exception as exc:
                logger.error("Failed to learn from %s: %s", url, exc)
                if progress_callback:
                    await progress_callback(f"❌ {url} 学习失败: {exc}")

        return results

    @classmethod
    async def learn_from_local(
        cls,
        local_path: str,
        source_name: str = "",
        focus_area: str = "",
        progress_callback=None,
    ) -> RepositoryKnowledge:
        """Analyze a local directory without fetching from GitHub.

        Args:
            local_path: Absolute path to the local code directory.
            source_name: Optional display name for the source.
            focus_area: Optional hint for deeper analysis.
            progress_callback: Async callable for progress reporting.

        Returns:
            RepositoryKnowledge with full analysis.
        """
        path = Path(local_path).resolve()
        if not path.is_dir():
            raise FileNotFoundError(f"Local path does not exist: {local_path}")

        source_name = source_name or path.name
        knowledge = cls._analyze_all(path, source_name, focus_area)

        if progress_callback:
            await progress_callback(
                f"✅ 本地分析完成: {source_name} — "
                f"{knowledge.file_count} 个文件, "
                f"{len(knowledge.all_classes)} 个类"
            )

        return knowledge

    # ── Repo fetching ──────────────────────────────────────────────────────

    @classmethod
    def _resolve_source(
        cls, url: str, workspace: str | None = None
    ) -> tuple[str, str | None]:
        """Resolve a named source or URL to a GitHub URL and local path.

        Known source names (e.g. "Hermes", "OpenClaw") are mapped to their
        GitHub URLs. If the source name matches a known local path AND that
        path exists, the local path is used directly instead of cloning.

        Returns:
            Tuple of (url_or_local_path, local_path_hint_or_None).
        """
        url_lower = url.lower().strip()

        # Known source → GitHub URL mapping
        KNOWN_SOURCES = {
            "hermes": "https://github.com/nousresearch/hermes-agent",
            "openclaw": "https://github.com/nousresearch/openclaw",
            "langchain": "https://github.com/langchain-ai/langchain",
            "langgraph": "https://github.com/langchain-ai/langgraph",
            "autogen": "https://github.com/microsoft/autogen",
            "crewai": "https://github.com/joaomdmoura/crewai",
            "pytorch": "https://github.com/pytorch/pytorch",
            "transformers": "https://github.com/huggingface/transformers",
            "fastapi": "https://github.com/fastapi/fastapi",
            "flask": "https://github.com/pallets/flask",
            "django": "https://github.com/django/django",
            "react": "https://github.com/facebook/react",
            "vue": "https://github.com/vuejs/core",
            "scikit-learn": "https://github.com/scikit-learn/scikit-learn",
            "openclaw desktop": "https://github.com/nousresearch/openclaw-desktop",
            "hermes desktop": "https://github.com/nousresearch/hermes-desktop",
        }

        # Check if it's a known name
        for name, github_url in KNOWN_SOURCES.items():
            if name in url_lower:
                # Check local mirrors first
                local_mirrors = {
                    "hermes": "/mnt/e/work/hermes-agent",
                    "openclaw": "/home/os/.openclaw",
                }
                for known_name, local_path in local_mirrors.items():
                    if known_name in url_lower and os.path.isdir(local_path):
                        logger.info(
                            "Found local mirror for '%s': %s",
                            url, local_path,
                        )
                        return local_path, local_path
                return github_url, None

        # If it's already a local path, use it directly
        if os.path.isdir(url):
            return url, url

        # If it's a full GitHub URL or other URL, return as-is
        if url.startswith(("http://", "https://", "git@")):
            return url, None

        # If it's a shorthand like "user/repo", convert to GitHub URL
        if "/" in url and not url.startswith(("http://", "https://", "git@")):
            return f"https://github.com/{url}", None

        # Otherwise treat as a local path
        return url, None

    @classmethod
    async def _fetch_repo(
        cls, url: str, local_path_hint: str | None = None
    ) -> str | None:
        """Clone or update a GitHub repository.

        Uses a cache under _CACHE_DIR/repo_name. If the cache exists,
        pulls the latest changes. If local_path_hint is provided and
        the path exists, uses it directly.

        Args:
            url: The URL or local path of the repository.
            local_path_hint: If provided and valid, use this path directly.

        Returns:
            Absolute path to the repository root, or None on failure.
        """
        # Use local path directly if available
        if local_path_hint and os.path.isdir(local_path_hint):
            return str(Path(local_path_hint).resolve())

        # Check if it's already a local path
        if os.path.isdir(url):
            return str(Path(url).resolve())

        # Derive repo name from URL
        repo_name = cls._repo_name_from_url(url)
        if not repo_name:
            logger.error("Cannot determine repo name from URL: %s", url)
            return None

        cache_dir = cls._CACHE_DIR / repo_name
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Use a shallow clone (depth=1) to minimise download time
        if (cache_dir / ".git").exists():
            # Update existing clone
            try:
                result = subprocess.run(
                    ["git", "-C", str(cache_dir), "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode != 0:
                    logger.warning(
                        "Git pull for %s failed: %s",
                        repo_name, result.stderr[:200],
                    )
                    # Still use the existing clone
                else:
                    logger.info("Updated %s: %s", repo_name, result.stdout.strip()[:100])
            except subprocess.TimeoutExpired:
                logger.warning("Git pull timed out for %s, using existing", repo_name)
            return str(cache_dir.resolve())

        # Fresh clone
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", url, str(cache_dir)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.error(
                    "Git clone failed for %s: %s",
                    url, result.stderr[:300],
                )
                # Clean up failed clone directory
                shutil.rmtree(cache_dir, ignore_errors=True)
                return None
            logger.info("Cloned %s to %s", repo_name, cache_dir)
            return str(cache_dir.resolve())
        except subprocess.TimeoutExpired:
            logger.error("Git clone timed out for %s (120s)", url)
            shutil.rmtree(cache_dir, ignore_errors=True)
            return None

    @staticmethod
    def _repo_name_from_url(url: str) -> str:
        """Extract a filesystem-safe repo name from a URL."""
        # Strip trailing .git
        url = url.rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]

        # Extract the last path component
        name = url.rstrip("/").split("/")[-1]
        # Clean for filesystem safety
        name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
        return name

    # ── Project metadata detection ─────────────────────────────────────────

    @classmethod
    def _detect_project_metadata(cls, root: Path) -> dict[str, Any]:
        """Detect project metadata from configuration files.

        Reads package.json, openclaw.json, setup.py, pyproject.toml, etc.
        to determine the project's name, type, and tech stack.

        Returns:
            dict with keys: type, name, description, tech_stack
        """
        meta: dict[str, Any] = {
            "type": "unknown",
            "name": root.name,
            "description": "",
            "tech_stack": [],
        }

        # Try package.json
        pkg_json = root / "package.json"
        if pkg_json.exists():
            try:
                import json as _j
                data = _j.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                meta["name"] = data.get("name", meta["name"])
                meta["description"] = data.get("description", "")
                meta["type"] = "node_project"
                for dep in list(data.get("dependencies", {}).keys())[:10]:
                    meta["tech_stack"].append(dep)
            except Exception:
                pass

        # Try openclaw.json
        oc_json = root / "openclaw.json"
        if oc_json.exists():
            try:
                import json as _j
                data = _j.loads(oc_json.read_text(encoding="utf-8", errors="replace"))
                meta["type"] = "openclaw_agent_runtime"
                meta["name"] = data.get("name", meta["name"])
                meta["description"] = data.get("description", data.get("purpose", ""))
                # Check if this is an agent config (YAML-based, not source code)
                agents = data.get("agents", data.get("plugins", []))
                if agents:
                    meta["tech_stack"].append(f"{len(agents)} agent(s)")
                for pv in data.get("providers", []):
                    if isinstance(pv, dict):
                        meta["tech_stack"].append(pv.get("name", str(pv)[:40]))
            except Exception:
                pass

        # Try pyproject.toml
        pyproj = root / "pyproject.toml"
        if pyproj.exists():
            meta["type"] = "python_project"
            try:
                content = pyproj.read_text(encoding="utf-8", errors="replace")
                for line in content.split("\n"):
                    if line.strip().startswith("name ="):
                        meta["name"] = line.split("=")[-1].strip().strip('"')
                        break
            except Exception:
                pass

        return meta

    # ── Analysis pipeline ──────────────────────────────────────────────────

    @classmethod
    def _analyze_all(
        cls,
        root_path: str | Path,
        source_name: str,
        focus_area: str = "",
    ) -> RepositoryKnowledge:
        """Run the full analysis pipeline on a local directory.

        Steps:
        1. Detect language & framework
        2. Build directory tree
        3. Scan all source files
        4. Extract classes, functions, imports
        5. Detect architecture patterns
        6. Focus-specific deep analysis
        7. Generate insights
        """
        import time as _t

        start = _t.time()
        root = Path(root_path).resolve()

        # Step 1: Detect primary language
        language_stats = cls._detect_languages(root)
        primary_lang = cls._get_primary_language(language_stats)
        frameworks = cls._detect_frameworks(root, language_stats)
        project_meta = cls._detect_project_metadata(root)

        # Log project type info for non-code projects
        if not language_stats or all(v == 0 for v in language_stats.values()):
            logger.info(
                "[CodeLearner] %s: No source code files found. "
                "Project metadata: %s",
                source_name, project_meta.get("type", "unknown"),
            )
        elif primary_lang not in ("python", "typescript", "javascript"):
            logger.info(
                "[CodeLearner] %s: Primary language is '%s'. "
                "Limited structural analysis available.",
                source_name, primary_lang,
            )

        # Step 2: Build directory tree & architecture roles
        dir_tree, dir_count = cls._build_directory_tree(root)
        arch_roles = cls._detect_architecture_roles(dir_tree)

        # Step 3 & 4: Scan files and extract declarations
        files, total_lines, all_classes, all_functions = cls._scan_files(
            root, language_stats, max_files=2000
        )

        # Step 5: Extract dependencies
        external_deps = cls._extract_external_dependencies(files)

        # Step 6: Focus-specific analysis
        ui_components: list[dict] = []
        api_endpoints: list[dict] = []
        design_tokens: dict[str, Any] = {}

        focus_lower = focus_area.lower()
        if any(kw in focus_lower for kw in ("frontend", "ui", "gui", "界面", "前端")):
            ui_components = cls._extract_ui_components(files, root)
            design_tokens = cls._extract_design_tokens(files, root)

        if any(kw in focus_lower for kw in ("api", "backend", "后端")):
            api_endpoints = cls._extract_api_endpoints(files, root)

        # Step 7: Generate insights
        key_insights = cls._generate_insights(
            primary_lang=primary_lang,
            frameworks=frameworks,
            file_count=len(files),
            total_lines=total_lines,
            class_count=len(all_classes),
            function_count=len(all_functions),
            arch_roles=arch_roles,
            external_deps=external_deps,
            focus_area=focus_area,
            ui_components=ui_components,
            project_meta=project_meta,
        )

        elapsed = _t.time() - start

        knowledge = RepositoryKnowledge(
            source_name=source_name,
            source_url="",
            local_path=str(root),
            primary_language=primary_lang,
            languages=language_stats,
            frameworks=frameworks,
            directory_tree=dir_tree,
            architecture_roles=arch_roles,
            file_count=len(files),
            dir_count=dir_count,
            total_lines=total_lines,
            files=files,
            all_classes=all_classes,
            all_functions=all_functions,
            external_dependencies=external_deps,
            ui_components=ui_components,
            api_endpoints=api_endpoints,
            design_tokens=design_tokens,
            key_insights=key_insights,
            analyzed_at=datetime.now().isoformat(),
            analysis_duration_s=round(elapsed, 2),
        )

        logger.info(
            "Analysis complete: %s — %d files, %d classes, "
            "%d functions, %.1fs",
            source_name, len(files), len(all_classes),
            len(all_functions), elapsed,
        )
        return knowledge

    # ── Language & Framework Detection ─────────────────────────────────────

    @classmethod
    def _detect_languages(cls, root: Path) -> dict[str, int]:
        """Count files by extension to determine language distribution."""
        counts: Counter[str] = Counter()

        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not any(frag in d for frag in EXCLUDED_DIR_FRAGMENTS)]
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                for lang, extensions in EXTENSIONS_BY_LANGUAGE.items():
                    if ext in extensions:
                        counts[lang] += 1
                        break

        return dict(counts.most_common())

    @classmethod
    def _get_primary_language(cls, language_stats: dict[str, int]) -> str:
        """Return the most common language."""
        if not language_stats:
            return "Unknown"
        return list(language_stats.keys())[0]

    @classmethod
    def _detect_frameworks(
        cls, root: Path, language_stats: dict[str, int]
    ) -> list[str]:
        """Detect likely frameworks from dependency files and code patterns."""
        frameworks: list[str] = []
        primary_lang = cls._get_primary_language(language_stats)

        # Check package manager files
        pkg_files = {
            "package.json": "Node.js/npm",
            "yarn.lock": "Node.js/yarn",
            "pnpm-lock.yaml": "Node.js/pnpm",
            "Cargo.toml": "Rust/Cargo",
            "go.mod": "Go Modules",
            "requirements.txt": "Python/pip",
            "Pipfile": "Python/pipenv",
            "poetry.lock": "Python/poetry",
            "pyproject.toml": "Python (modern)",
            "Gemfile": "Ruby/Bundler",
            "composer.json": "PHP/Composer",
            "build.gradle": "Gradle",
            "pom.xml": "Maven",
            "CMakeLists.txt": "CMake",
            "Makefile": "Make",
            "Cargo.lock": "Rust",
        }

        for pkg_file, framework_name in pkg_files.items():
            if (root / pkg_file).exists():
                frameworks.append(framework_name)

        # Framework-specific heuristics via file scanning (limited depth)
        max_check = 200  # check at most this many files
        checked = 0

        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not any(frag in d for frag in EXCLUDED_DIR_FRAGMENTS)]
            if checked >= max_check:
                break
            for fname in filenames:
                if checked >= max_check:
                    break
                if not fname.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".rs", ".go")):
                    continue
                try:
                    filepath = os.path.join(dirpath, fname)
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        head = f.read(4096)
                    checked += 1

                    head_lower = head.lower()
                    # React
                    if "react" in head_lower and "react" not in " ".join(frameworks).lower():
                        frameworks.append("React")
                    # Vue
                    if "vue" in head_lower and "Vue.js" not in " ".join(frameworks):
                        # Vue is already detected by .vue extension, don't double-add
                        pass
                    # FastAPI
                    if "fastapi" in head_lower and "FastAPI" not in " ".join(frameworks):
                        frameworks.append("FastAPI")
                    # Flask
                    if "flask" in head_lower and "Flask" not in " ".join(frameworks):
                        frameworks.append("Flask")
                    # Django
                    if "django" in head_lower and "Django" not in " ".join(frameworks):
                        frameworks.append("Django")
                    # Express
                    if "express" in head_lower and "Express" not in " ".join(frameworks):
                        frameworks.append("Express")
                    # PySide/Qt
                    if "pyside" in head_lower or "pyqt" in head_lower:
                        if "Qt" not in " ".join(frameworks):
                            frameworks.append("Qt (PySide)")
                    # Actix
                    if "actix" in head_lower and "Actix" not in " ".join(frameworks):
                        frameworks.append("Actix")
                    # Gin
                    if "gin" in head_lower and "Gin" not in " ".join(frameworks):
                        frameworks.append("Gin")
                    # Ink (React terminal)
                    if "ink" in head_lower and "react" in head_lower and "React-Ink" not in " ".join(frameworks):
                        frameworks.append("React-Ink")
                except Exception:
                    continue

        # Deduplicate
        seen: set[str] = set()
        unique_frameworks: list[str] = []
        for fw in frameworks:
            fw_lower = fw.lower()
            if fw_lower not in seen:
                seen.add(fw_lower)
                unique_frameworks.append(fw)

        return unique_frameworks

    # ── Directory Tree & Architecture ──────────────────────────────────────

    @classmethod
    def _build_directory_tree(
        cls, root: Path, max_depth: int = 4, max_entries: int = 150
    ) -> tuple[list[DirectoryEntry], int]:
        """Build a compact directory tree showing structure."""
        entries: list[DirectoryEntry] = []
        dir_count = 0

        # Only show the top few levels with key subdirectories
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not any(frag in d for frag in EXCLUDED_DIR_FRAGMENTS)]
            rel = Path(dirpath).relative_to(root)
            depth = len(rel.parts) if str(rel) != "." else 0

            if depth > max_depth:
                dirnames.clear()
                continue

            if len(entries) >= max_entries:
                dirnames.clear()
                continue

            # Add directory entry
            rel_str = str(rel) if str(rel) != "." else ""
            if rel_str:
                role = cls._detect_dir_role(rel_str)
                entries.append(
                    DirectoryEntry(
                        path=rel_str,
                        entry_type="dir",
                        file_count=len(filenames),
                        role=role,
                    )
                )
                dir_count += 1

            # Add file entries (limited)
            file_entries_needed = min(len(filenames), 10)  # cap at 10 files per dir
            for fname in sorted(filenames)[:file_entries_needed]:
                ext = Path(fname).suffix.lower()
                if ext in {e for exts in EXTENSIONS_BY_LANGUAGE.values() for e in exts}:
                    file_rel = f"{rel_str}/{fname}" if rel_str else fname
                    if len(entries) < max_entries:
                        entries.append(
                            DirectoryEntry(
                                path=file_rel,
                                entry_type="file",
                                role="",
                            )
                        )

        return entries, dir_count

    @staticmethod
    def _detect_dir_role(dir_path: str) -> str:
        """Detect the architectural role of a directory from its name."""
        parts = dir_path.lower().split("/")
        for part in parts:
            for pattern, role in ARCHITECTURE_DIR_PATTERNS.items():
                if part == pattern or part.startswith(pattern + "_"):
                    return role
        return ""

    @classmethod
    def _detect_architecture_roles(
        cls, dir_tree: list[DirectoryEntry]
    ) -> dict[str, int]:
        """Count how many directories play each architectural role."""
        role_counts: Counter[str] = Counter()
        for entry in dir_tree:
            if entry.entry_type == "dir" and entry.role:
                role_counts[entry.role] += 1

        # Also count from path patterns
        for entry in dir_tree:
            if entry.entry_type == "dir":
                for pattern, role in ARCHITECTURE_DIR_PATTERNS.items():
                    if pattern in entry.path.lower():
                        role_counts[role] += 1
                        break

        return dict(role_counts.most_common())

    # ── File Scanning ──────────────────────────────────────────────────────

    @classmethod
    def _scan_files(
        cls, root: Path, language_stats: dict[str, int],
        max_files: int = 2000,
    ) -> tuple[list[FileInfo], int, list[str], list[str]]:
        """Scan all source files and extract structural information.

        Returns:
            Tuple of (files, total_lines, all_classes, all_functions).
        """
        files: list[FileInfo] = []
        all_classes: list[str] = []
        all_functions: list[str] = []
        total_lines = 0

        ext_to_lang: dict[str, str] = {}
        for lang, extensions in EXTENSIONS_BY_LANGUAGE.items():
            for ext in extensions:
                ext_to_lang[ext] = lang

        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not any(frag in d for frag in EXCLUDED_DIR_FRAGMENTS)]
            for fname in sorted(filenames):
                if len(files) >= max_files:
                    dirnames.clear()
                    break
                ext = Path(fname).suffix.lower()
                lang = ext_to_lang.get(ext)
                if not lang:
                    continue

                file_path = Path(dirpath) / fname
                rel = str(file_path.relative_to(root))

                content = cls._safe_read(file_path)
                if not content:
                    continue

                lines = content.count("\n") + 1
                total_lines += lines

                classes = cls._extract_classes(content, lang)
                functions = cls._extract_functions(content, lang)
                imports = cls._extract_imports(content, lang)

                # Build summary
                summary_parts = []
                if classes:
                    cls_list = ", ".join(classes[:5])
                    if len(classes) > 5:
                        cls_list += f" ... (+{len(classes) - 5})"
                    summary_parts.append(f"classes: [{cls_list}]")
                if functions:
                    fn_list = ", ".join(functions[:5])
                    if len(functions) > 5:
                        fn_list += f" ... (+{len(functions) - 5})"
                    summary_parts.append(f"functions: [{fn_list}]")
                summary = "; ".join(summary_parts) if summary_parts else rel

                all_classes.extend(classes)
                all_functions.extend(functions)

                files.append(
                    FileInfo(
                        path=rel,
                        language=lang,
                        lines=lines,
                        classes=classes,
                        functions=functions,
                        imports=imports,
                        summary=summary,
                        content=content[:5000] if len(content) > 5000 else content,
                    )
                )

        return files, total_lines, list(set(all_classes)), list(set(all_functions))

    @staticmethod
    def _safe_read(file_path: Path) -> str:
        """Read a file safely, returning empty string on error."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return ""

    @classmethod
    def _extract_classes(cls, content: str, language: str) -> list[str]:
        """Extract class/type names from file content for the given language."""
        pattern = CLASS_PATTERNS.get(language)
        if not pattern:
            return []
        classes: list[str] = []
        for m in pattern.finditer(content):
            # Some languages have multiple capture groups (e.g. Go)
            for g in m.groups():
                if g:
                    classes.append(g)
        return sorted(set(classes))

    @classmethod
    def _extract_functions(cls, content: str, language: str) -> list[str]:
        """Extract function names from file content for the given language."""
        pattern = FUNCTION_PATTERNS.get(language)
        if not pattern:
            return []
        functions: list[str] = []
        for m in pattern.finditer(content):
            g = m.group(1)
            if g:
                functions.append(g)
        return sorted(set(functions))

    @classmethod
    def _extract_imports(cls, content: str, language: str) -> list[str]:
        """Extract import statements from file content."""
        patterns = IMPORT_PATTERNS.get(language, [])
        imports: list[str] = []
        for pattern in patterns:
            for m in pattern.finditer(content):
                for g in m.groups():
                    if g:
                        imports.append(g)
        return sorted(set(imports))

    # ── External Dependencies ──────────────────────────────────────────────

    @classmethod
    def _extract_external_dependencies(
        cls, files: list[FileInfo]
    ) -> list[str]:
        """Extract external dependency names from import statements."""
        all_imports: list[str] = []
        for f in files:
            all_imports.extend(f.imports)

        # For Python imports, extract the top-level package name
        external_deps: set[str] = set()
        for imp in all_imports:
            # Get the top-level module name
            top_level = imp.split(".")[0].split("/")[0].split("\\")[0]
            # Filter out stdlib and relative imports
            if (
                top_level
                and not top_level.startswith(".")
                and not top_level.startswith("_")
                and top_level not in _STDLIB_MODULES
            ):
                external_deps.add(top_level)

        return sorted(external_deps)[:100]  # cap at 100

    # ── Focus-specific: UI Components (for frontend repos) ─────────────────

    @classmethod
    def _extract_ui_components(
        cls, files: list[FileInfo], root: Path
    ) -> list[dict]:
        """Extract UI component information from frontend code files."""
        components: list[dict] = []

        for file_info in files:
            # Only look at frontend-relevant files
            if file_info.language not in (
                "typescript", "javascript", "vue", "svelte", "python"
            ):
                continue

            # Check if it looks like a UI component file
            path_lower = file_info.path.lower()
            is_ui_file = any(
                keyword in path_lower
                for keyword in (
                    "component", "widget", "page", "view", "screen",
                    "button", "input", "card", "bubble", "dialog",
                    "modal", "nav", "sidebar", "header", "footer",
                    "form", "panel", "layout", "container",
                )
            )

            if not is_ui_file:
                continue

            # Check file content for UI indicators
            content = file_info.content.lower()
            ui_keywords = {
                "render": "renders UI",
                "component": "is a component",
                "jsx": "uses JSX",
                "template": "has template",
                "onclick": "has click handler",
                "onchange": "has change handler",
                "class=": "uses CSS classes",
                "style=": "has inline styles",
                "qwidget": "Qt widget",
                "qpushbutton": "Qt button",
                "qlabel": "Qt label",
                "qss": "Qt stylesheet",
            }

            features: list[str] = []
            for keyword, feature in ui_keywords.items():
                if keyword in content:
                    features.append(feature)

            # Determine widget type from path and content
            widget_type = cls._infer_ui_widget_type(file_info, content)

            components.append({
                "name": Path(file_info.path).stem,
                "file_path": file_info.path,
                "language": file_info.language,
                "widget_type": widget_type,
                "lines": file_info.lines,
                "classes": file_info.classes,
                "functions": file_info.functions,
                "features": features,
                "summary": file_info.summary,
            })

        return components

    @classmethod
    def _infer_ui_widget_type(
        cls, file_info: FileInfo, content: str
    ) -> str:
        """Infer what kind of UI widget a file represents."""
        name_lower = Path(file_info.path).stem.lower()
        path_lower = file_info.path.lower()

        # Check by name
        if any(kw in name_lower for kw in ("button", "btn", "action")):
            return "button"
        if any(kw in name_lower for kw in ("input", "textarea", "form", "combo")):
            return "input"
        if any(kw in name_lower for kw in ("card", "bubble", "item", "entry")):
            return "card"
        if any(kw in name_lower for kw in ("dialog", "modal", "popup", "alert")):
            return "dialog"
        if any(kw in name_lower for kw in ("nav", "sidebar", "tab", "menu", "header")):
            return "navigation"
        if any(kw in name_lower for kw in ("scroll", "list", "tree", "table")):
            return "list"
        if any(kw in name_lower for kw in ("container", "box", "layout", "panel")):
            return "container"
        if any(kw in name_lower for kw in ("label", "text", "icon", "badge", "status")):
            return "display"

        # Check by path
        if "pages" in path_lower or "views" in path_lower or "screens" in path_lower:
            return "page"
        if "layouts" in path_lower:
            return "layout"

        # Check by content
        if "qpushbutton" in content or 'type="button"' in content:
            return "button"
        if "qlineedit" in content or "qtextedit" in content or 'type="text"' in content:
            return "input"

        return "unknown"

    # ── Focus-specific: Design Tokens ─────────────────────────────────────

    @classmethod
    def _extract_design_tokens(
        cls, files: list[FileInfo], root: Path
    ) -> dict[str, Any]:
        """Extract design tokens (colors, spacing, fonts, radii) from theme/CSS files."""
        tokens: dict[str, Any] = {
            "colors": [],
            "spacing": [],
            "fonts": [],
            "border_radii": [],
            "theme_files": [],
        }

        for file_info in files:
            path_lower = file_info.path.lower()

            # Look for theme/style files
            is_theme = any(
                kw in path_lower
                for kw in ("theme", "style", "color", "palette", "css", "qss")
            )
            if not is_theme:
                continue

            tokens["theme_files"].append(file_info.path)
            content = file_info.content

            # Extract color definitions
            color_patterns = [
                re.compile(r"(?:color|bg|background|border)[:\s]*(#[a-fA-F0-9]{6,8})"),
                re.compile(r"#([a-fA-F0-9]{6})\b"),
                re.compile(r"rgba?\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+"),
                re.compile(r"(--[\w-]+)\s*:\s*(#[a-fA-F0-9]+|[a-zA-Z]+)"),
            ]

            for pat in color_patterns:
                for m in pat.finditer(content):
                    val = m.group(0).strip()
                    if val and len(val) > 3:
                        tokens["colors"].append(val)

            # Extract spacing values (common design token patterns)
            spacing_pat = re.compile(r"(?:padding|margin|gap|spacing)[:\s]*(\d+)")
            for m in spacing_pat.finditer(content):
                tokens["spacing"].append(m.group(0).strip())

            # Extract font definitions
            font_pat = re.compile(r"(?:font-family|font-size|font)[:\s]*([^;{]+)")
            for m in font_pat.finditer(content):
                tokens["fonts"].append(m.group(0).strip())

            # Extract border-radius
            radius_pat = re.compile(r"border-radius[:\s]*([^;{]+)")
            for m in radius_pat.finditer(content):
                tokens["border_radii"].append(m.group(0).strip())

        # Deduplicate and limit
        for key in ("colors", "spacing", "fonts", "border_radii"):
            tokens[key] = list(set(tokens[key]))[:30]

        tokens["theme_files"] = list(set(tokens["theme_files"]))
        return tokens

    # ── Focus-specific: API Endpoints (for backend repos) ─────────────────

    @classmethod
    def _extract_api_endpoints(
        cls, files: list[FileInfo], root: Path
    ) -> list[dict]:
        """Extract API endpoint definitions from backend code."""
        endpoints: list[dict] = []
        http_methods = {"get", "post", "put", "delete", "patch", "head", "options"}

        for file_info in files:
            if file_info.language not in ("python", "typescript", "javascript", "go", "rust", "java"):
                continue

            content = file_info.content
            content_lower = content.lower()
            path_lower = file_info.path.lower()

            # FastAPI-style: @app.get(...), @router.post(...)
            if file_info.language == "python":
                for method in http_methods:
                    for m in re.finditer(
                        rf'@(?:app|router|api)\.{method}\s*\(\s*["\']([^"\']+)["\']',
                        content,
                    ):
                        endpoints.append({
                            "method": method.upper(),
                            "path": m.group(1),
                            "file": file_info.path,
                            "framework": "FastAPI",
                        })

            # Express-style: app.get(...), router.post(...)
            if file_info.language in ("typescript", "javascript"):
                for method in http_methods:
                    for m in re.finditer(
                        rf'(?:app|router|route)\.{method}\s*\(\s*["\']([^"\']+)["\']',
                        content,
                    ):
                        endpoints.append({
                            "method": method.upper(),
                            "path": m.group(1),
                            "file": file_info.path,
                            "framework": "Express",
                        })

            # Go Gin-style: r.GET(...), router.POST(...)
            if file_info.language == "go":
                for method in http_methods:
                    for m in re.finditer(
                        rf'(?:r|router|engine)\.{method.upper()}\s*\(\s*["\']([^"\']+)["\']',
                        content,
                    ):
                        endpoints.append({
                            "method": method.upper(),
                            "path": m.group(1),
                            "file": file_info.path,
                            "framework": "Gin",
                        })

        return endpoints

    # ── Insight Generation ────────────────────────────────────────────────

    @classmethod
    def _generate_insights(
        cls,
        primary_lang: str,
        frameworks: list[str],
        file_count: int,
        total_lines: int,
        class_count: int,
        function_count: int,
        arch_roles: dict[str, int],
        external_deps: list[str],
        focus_area: str,
        ui_components: list[dict],
        project_meta: dict[str, Any] | None = None,
    ) -> list[str]:
        """Generate human-readable insights about the codebase."""
        insights: list[str] = []

        # Language & scale
        lang_note = ""
        if primary_lang not in ("python", "typescript", "javascript", "Unknown"):
            lang_note = f"（{primary_lang}项目，部分分析结果可能不完整）"
        insights.append(
            f"主语言: {primary_lang}{lang_note}, 共 {file_count} 个源文件, "
            f"{total_lines} 行代码"
        )

        # Project type note
        if project_meta:
            ptype = project_meta.get("type", "")
            pname = project_meta.get("name", "")
            pdesc = project_meta.get("description", "")
            if ptype == "openclaw_agent_runtime":
                insights.append(
                    f"项目类型: OpenClaw Agent 运行时配置 (YAML/JSON配置驱动，非前端源码项目)"
                )
                if pdesc:
                    insights.append(f"项目描述: {pdesc[:200]}")
                tech = project_meta.get("tech_stack", [])
                if tech:
                    insights.append(f"技术栈: {', '.join(tech[:6])}")
            elif ptype == "node_project":
                insights.append(f"项目类型: Node.js 项目")
                if pdesc:
                    insights.append(f"项目描述: {pdesc[:200]}")
            elif ptype == "python_project":
                insights.append(f"项目类型: Python 项目")
            elif class_count == 0 and file_count > 0:
                insights.append(
                    f"项目类型: {ptype} — {file_count} 个文件，但未检测到类定义。"
                    f"可能为配置文件/声明式项目而非代码项目。"
                )

        if frameworks:
            insights.append(f"检测到框架: {', '.join(frameworks)}")

        if class_count > 0:
            insights.append(f"定义了 {class_count} 个类/类型, {function_count} 个函数/方法")

        # Architecture
        if arch_roles:
            role_names = {
                "ui_component": "UI组件",
                "ui_page": "页面",
                "ui_layout": "布局",
                "ui_style": "样式",
                "ui_theme": "主题",
                "api_endpoint": "API端点",
                "api_route": "路由",
                "api_controller": "控制器",
                "data_model": "数据模型",
                "data_access": "数据访问层",
                "core_module": "核心模块",
                "utility": "工具函数",
                "configuration": "配置",
                "service": "服务层",
                "test": "测试",
            }
            roles_str = ", ".join(
                f"{role_names.get(role, role)} [{count}个]"
                for role, count in list(arch_roles.items())[:8]
            )
            insights.append(f"架构层次: {roles_str}")

        # Dependencies
        if external_deps:
            insights.append(
                f"外部依赖: {', '.join(external_deps[:10])}"
                + (f" 等 {len(external_deps)} 个" if len(external_deps) > 10 else "")
            )

        # Focus-specific insights
        focus_lower = focus_area.lower()
        if any(kw in focus_lower for kw in ("frontend", "ui", "gui", "界面", "前端")):
            ui_types: Counter[str] = Counter()
            for c in ui_components:
                ui_types[c["widget_type"]] += 1
            if ui_types:
                ui_summary = ", ".join(f"{t}: {c}个" for t, c in ui_types.most_common())
                insights.append(f"UI组件分布: {ui_summary}")
            design_insights = cls._generate_design_insights(ui_components)
            insights.extend(design_insights)

        if any(kw in focus_lower for kw in ("api", "backend", "后端")):
            insights.append("包含API端点定义（可通过进一步分析提取接口文档）")

        return insights

    @classmethod
    def _generate_design_insights(
        cls, ui_components: list[dict]
    ) -> list[str]:
        """Generate design-specific insights from UI component analysis."""
        insights: list[str] = []

        # Detect key interaction patterns
        all_features: list[str] = []
        for c in ui_components:
            all_features.extend(c.get("features", []))

        feature_counts = Counter(all_features)
        if feature_counts:
            top_features = feature_counts.most_common(5)
            insights.append(
                f"交互模式: {', '.join(f'{f[0]} ({f[1]}处)' for f in top_features)}"
            )

        return insights

    # ── Serialization Helpers ──────────────────────────────────────────────

    @staticmethod
    def knowledge_to_dict(knowledge: RepositoryKnowledge) -> dict[str, Any]:
        """Convert RepositoryKnowledge to a JSON-serializable dict."""
        return {
            "source_name": knowledge.source_name,
            "source_url": knowledge.source_url,
            "local_path": knowledge.local_path,
            "primary_language": knowledge.primary_language,
            "languages": knowledge.languages,
            "frameworks": knowledge.frameworks,
            "file_count": knowledge.file_count,
            "dir_count": knowledge.dir_count,
            "total_lines": knowledge.total_lines,
            "all_classes": knowledge.all_classes[:500],  # cap size
            "all_functions": knowledge.all_functions[:500],
            "external_dependencies": knowledge.external_dependencies[:100],
            "architectural_roles": knowledge.architecture_roles,
            "key_insights": knowledge.key_insights,
            "ui_component_count": len(knowledge.ui_components),
            "api_endpoint_count": len(knowledge.api_endpoints),
            "analysis_duration_s": knowledge.analysis_duration_s,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Python stdlib modules — these are filtered out of "external dependencies"
# to avoid false positives.
# ═══════════════════════════════════════════════════════════════════════════════

_STDLIB_MODULES: set[str] = {
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
    "asyncore", "atexit", "audioop", "base64", "bdb", "binascii", "binhex",
    "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk",
    "cmath", "cmd", "code", "codecs", "codeop", "collections", "colorsys",
    "compileall", "concurrent", "configparser", "contextlib", "contextvars",
    "copy", "copyreg", "cProfile", "crypt", "csv", "ctypes", "curses",
    "dataclasses", "datetime", "dbm", "decimal", "difflib", "dis",
    "distutils", "doctest", "email", "encodings", "enum", "errno",
    "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch", "fractions",
    "ftplib", "functools", "gc", "getopt", "getpass", "gettext", "glob",
    "grp", "gzip", "hashlib", "heapq", "hmac", "html", "http",
    "idlelib", "imaplib", "imghdr", "imp", "importlib", "inspect", "io",
    "ipaddress", "itertools", "json", "keyword", "lib2to3", "linecache",
    "locale", "logging", "lzma", "mailbox", "mailcap", "marshal",
    "math", "mimetypes", "mmap", "modulefinder", "multiprocessing",
    "netrc", "nis", "nntplib", "numbers", "operator", "optparse",
    "os", "ossaudiodev", "parser", "pathlib", "pdb", "pickle",
    "pickletools", "pipes", "pkgutil", "platform", "plistlib", "poplib",
    "posix", "posixpath", "pprint", "profile", "pstats", "pty", "pwd",
    "py_compile", "pyclbr", "pydoc", "queue", "quopri", "random",
    "re", "readline", "reprlib", "resource", "rlcompleter", "runpy",
    "sched", "secrets", "select", "selectors", "shelve", "shlex",
    "shutil", "signal", "site", "smtpd", "smtplib", "sndhdr", "socket",
    "socketserver", "sqlite3", "ssl", "stat", "statistics", "string",
    "stringprep", "struct", "subprocess", "sunau", "symtable", "sys",
    "sysconfig", "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile",
    "termios", "test", "textwrap", "threading", "time", "timeit",
    "tkinter", "token", "tokenize", "tomllib", "trace", "traceback",
    "tracemalloc", "tty", "turtle", "turtledemo", "types", "typing",
    "unicodedata", "unittest", "urllib", "uu", "uuid", "venv",
    "warnings", "wave", "weakref", "webbrowser", "winreg", "winsound",
    "wsgiref", "xdrlib", "xml", "xmlrpc", "xxlimited", "xxsubtype",
    "zipapp", "zipfile", "zipimport", "zlib",
}
