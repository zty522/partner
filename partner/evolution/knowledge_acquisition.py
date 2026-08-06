"""Knowledge Acquisition — multi-source knowledge ingestion for Partner's self-evolution system.

Part of the 5-step self-evolution cycle. Acquires structured knowledge from four
external and internal source types to feed into gap discovery, lesson extraction,
and architecture improvement:

1. **GitHub repos** — clone/pull repositories, analyze code structure, extract
   patterns from source files, class hierarchies, function signatures, and imports.

2. **Web pages** — fetch URLs asynchronously with aiohttp, extract structured
   information (titles, headings, code blocks, paragraphs), strip boilerplate.

3. **Config files** — read and parse YAML/JSON/TOML/INI/Python config sources,
   flatten nested structures, surface key-value insights.

4. **Local code** — read Python source files, extract class/function/import
   patterns, identify common idioms and coding conventions.

Every acquisition produces a typed ``Knowledge`` dataclass containing the raw
source reference, a content-type tag, extracted key insights, and a list of
relevant file paths. Downstream consumers (gap_discovery, architecture_mapper,
lesson_extractor) can use these to detect patterns worth adopting.

Usage:
    from partner.evolution.knowledge_acquisition import KnowledgeAcquirer

    acquirer = KnowledgeAcquirer(repos_dir=\"/tmp/partner_evolution_repos\")
    knowledge = await acquirer.fetch_from_github(
        repo_url=\"https://github.com/user/repo\",
        focus_area=\"pipeline\",
    )
    print(knowledge.key_insights)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_REPOS_DIR = Path("/tmp/partner_evolution_repos")
HTTP_TIMEOUT_SECONDS = 30
MAX_PAGE_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB
GIT_CLONE_TIMEOUT_SECONDS = 120
MAX_INSIGHTS_PER_SOURCE = 20

# ═══════════════════════════════════════════════════════════════════════════════
# Data Types
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Knowledge:
    """A structured knowledge record acquired from an external or internal source.

    This is the universal output type for all acquisition methods in this module.
    Each record captures what was found, where it came from, and which files are
    associated.

    Attributes:
        source: URI or filesystem path from which the knowledge was obtained.
            Examples: ``"https://github.com/user/repo"``, ``"/home/user/config.yaml"``,
            ``"https://example.com/docs"``.
        content_type: Semantic category of the content. One of:
            ``"github_repo"``, ``"web_page"``, ``"config_file"``, ``"local_code"``,
            ``"code_structure"``.
        key_insights: List of concise insight strings extracted from the source.
            Each insight is a human-readable statement about a pattern, feature,
            configuration option, or architectural element discovered.
        relevant_files: List of file paths (relative or absolute) that contributed
            to the knowledge record. For GitHub repos these are paths within the
            cloned repository; for local code they are filesystem paths.
        raw_metadata: Optional dictionary carrying additional structured data
            that downstream consumers may find useful — e.g. class names,
            import counts, parse errors, HTTP status codes.
    """

    source: str
    content_type: str  # github_repo | web_page | config_file | local_code | code_structure
    key_insights: tuple[str, ...] = field(default_factory=tuple)
    relevant_files: tuple[str, ...] = field(default_factory=tuple)
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this Knowledge record to a plain dictionary for JSON/logging."""
        return asdict(self)

    def __len__(self) -> int:
        """Number of key insights in this record."""
        return len(self.key_insights)


# ═══════════════════════════════════════════════════════════════════════════════
# Helper utilities
# ═══════════════════════════════════════════════════════════════════════════════


def _ensure_dir(path: Path) -> Path:
    """Create ``path`` (including parents) if it does not exist, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_repo_name(repo_url: str) -> str:
    """Derive a safe directory name from a GitHub repository URL.

    Args:
        repo_url: Full GitHub URL, e.g. ``"https://github.com/nousresearch/hermes"``.

    Returns:
        A filesystem-safe name like ``"nousresearch_hermes"``.
    """
    parsed = urlparse(repo_url.rstrip("/"))
    path_part = parsed.path.strip("/")
    # Replace path separators and dots with underscores
    return re.sub(r"[^\w\-]", "_", path_part) or "unknown_repo"


def _run_git(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = GIT_CLONE_TIMEOUT_SECONDS,
) -> tuple[str, str, int]:
    """Run a git subprocess and return ``(stdout, stderr, returncode)``.

    Args:
        args: List of arguments to pass to the ``git`` executable (excluding
            the ``git`` command itself).
        cwd: Working directory for the git process. ``None`` means CWD.
        timeout: Maximum seconds to wait for completion.

    Returns:
        A 3-tuple ``(stdout, stderr, returncode)``.

    Raises:
        RuntimeError: If git is not installed or the process fails to start.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "git executable not found on PATH. "
            "Please install git (apt install git / brew install git / etc.)."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git command {' '.join(args)} timed out after {timeout}s")

    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


async def _async_shell(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = GIT_CLONE_TIMEOUT_SECONDS,
) -> tuple[str, str, int]:
    """Run a shell command asynchronously via ``asyncio.create_subprocess_exec``.

    Preferred over :func:`_run_git` for long operations (clones) because it
    does not block the event loop.

    Args:
        cmd: Full command list including the executable, e.g. ``["git", "clone", ...]``.
        cwd: Working directory.
        timeout: Maximum seconds to wait.

    Returns:
        ``(stdout, stderr, returncode)``.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Command {' '.join(cmd)} timed out after {timeout}s")

    rc = proc.returncode if proc.returncode is not None else -1
    return stdout.decode().strip(), stderr.decode().strip(), rc


# ═══════════════════════════════════════════════════════════════════════════════
# Code analysis utilities
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_python_patterns(file_path: str | Path) -> dict[str, Any]:
    """Extract structural patterns from a Python source file.

    Identifies:
    - Class definitions and their base classes
    - Async function definitions
    - Regular function definitions
    - Import statements (both ``import x`` and ``from x import y``)
    - Module-level docstrings
    - Decorator usage

    Args:
        file_path: Path to a ``.py`` file.

    Returns:
        A dict with keys ``classes``, ``functions``, ``async_functions``,
        ``imports``, ``has_docstring``, ``decorators``, ``lines``.
    """
    result: dict[str, Any] = {
        "classes": [],
        "functions": [],
        "async_functions": [],
        "imports": [],
        "has_docstring": False,
        "decorators": [],
        "lines": 0,
    }
    path = Path(file_path)
    if not path.suffix == ".py":
        return result

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug("[KNOWLEDGE_ACQ] cannot read %s: %s", file_path, exc)
        return result

    result["lines"] = len(text.splitlines())

    # Quick regex-based scan (lightweight; not a full AST parse)
    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("class ") and stripped.endswith(":"):
            class_def = stripped[6:-1].strip()
            if "(" in class_def and ")" in class_def:
                name = class_def.split("(")[0].strip()
                bases = [b.strip() for b in class_def.split("(")[1].rstrip(")").split(",") if b.strip()]
                result["classes"].append({"name": name, "bases": bases})
            else:
                result["classes"].append({"name": class_def, "bases": []})

        elif stripped.startswith("async def "):
            func_name = stripped.split("(")[0].replace("async def ", "").strip()
            result["async_functions"].append(func_name)
            result["functions"].append(func_name)

        elif stripped.startswith("def "):
            func_name = stripped.split("(")[0].replace("def ", "").strip()
            result["functions"].append(func_name)

        elif stripped.startswith("import "):
            modules = stripped[7:].split(",")
            result["imports"].extend(m.strip().split(" as ")[0].strip() for m in modules if m.strip())

        elif stripped.startswith("from ") and "import " in stripped:
            parts = stripped.split(" import ", 1)
            from_module = parts[0].replace("from ", "", 1).strip()
            names = [n.strip().split(" as ")[0].strip() for n in parts[1].split(",")]
            result["imports"].append({"from": from_module, "names": names})

        elif stripped.startswith("@") and not stripped.startswith("@@"):
            decorator = stripped.lstrip("@").split("(")[0].strip()
            if decorator:
                result["decorators"].append(decorator)

    # Detect module docstring
    if text.lstrip().startswith('"""') or text.lstrip().startswith("'''"):
        result["has_docstring"] = True

    return result


def _extract_config_patterns(file_path: str | Path) -> dict[str, Any]:
    """Extract key-value insights from a configuration file.

    Supports YAML (``.yaml`` / ``.yml``), JSON (``.json``), TOML (``.toml``),
    INI (``.ini`` / ``.cfg``), and ``.env`` files.  For Python config files
    (``.py`` with key = value patterns) a simple regex scan is used.

    Args:
        file_path: Path to the configuration file.

    Returns:
        A dict with keys ``keys`` (list of discovered keys), ``format``
        (detected format string), ``size_bytes``, ``parse_error`` (if any).
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    result: dict[str, Any] = {
        "keys": [],
        "format": suffix.lstrip(".") or "unknown",
        "size_bytes": 0,
        "parse_error": None,
    }

    if not path.is_file():
        result["parse_error"] = "not a file"
        return result

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        result["parse_error"] = str(exc)
        return result

    result["size_bytes"] = len(text.encode("utf-8"))

    try:
        if suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore[import-untyped]
                data = yaml.safe_load(text)
                if isinstance(data, dict):
                    result["keys"] = list(_flatten_dict(data).keys())
            except ImportError:
                # Fallback: simple key: value pattern
                for line in text.splitlines():
                    if ":" in line and not line.strip().startswith("#"):
                        key = line.split(":", 1)[0].strip()
                        if key:
                            result["keys"].append(key)
                result["parse_error"] = "PyYAML not available; shallow scan only"
            except Exception as exc:
                result["parse_error"] = str(exc)

        elif suffix == ".json":
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    result["keys"] = list(_flatten_dict(data).keys())
                elif isinstance(data, list):
                    result["keys"] = [f"[{i}]" for i in range(min(len(data), 20))]
            except json.JSONDecodeError as exc:
                result["parse_error"] = str(exc)

        elif suffix == ".toml":
            try:
                import tomllib  # Python 3.11+
                data = tomllib.loads(text)
                if isinstance(data, dict):
                    result["keys"] = list(_flatten_dict(data).keys())
            except ImportError:
                try:
                    import tomli  # type: ignore[import-untyped]
                    data = tomli.loads(text)
                    if isinstance(data, dict):
                        result["keys"] = list(_flatten_dict(data).keys())
                except ImportError:
                    result["parse_error"] = "tomllib/tomli not available"
                except Exception as exc:
                    result["parse_error"] = str(exc)
            except Exception as exc:
                result["parse_error"] = str(exc)

        elif suffix in (".ini", ".cfg"):
            from configparser import ConfigParser
            parser = ConfigParser()
            try:
                parser.read_string(text)
                for section in parser.sections():
                    for key in parser[section]:
                        result["keys"].append(f"{section}.{key}")
            except Exception as exc:
                result["parse_error"] = str(exc)

        elif suffix == ".env":
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key = line.split("=", 1)[0].strip()
                    if key:
                        result["keys"].append(key)

        elif suffix == ".py":
            # Python config files: look for top-level ASSIGNMENT patterns
            for line in text.splitlines():
                stripped = line.strip()
                if "=" in stripped and not stripped.startswith("#") and not stripped.startswith(" " * 4):
                    key = stripped.split("=", 1)[0].strip()
                    if key.isidentifier():
                        result["keys"].append(key)

    except Exception as exc:
        result["parse_error"] = str(exc)

    result["keys"] = list(dict.fromkeys(result["keys"]))  # deduplicate preserving order
    return result


def _flatten_dict(
    d: dict[str, Any],
    parent_key: str = "",
    sep: str = ".",
) -> dict[str, Any]:
    """Recursively flatten a nested dictionary into dot-separated keys.

    Args:
        d: The dictionary to flatten.
        parent_key: Prefix for recursion (internal).
        sep: Separator between key levels (default ``"."``).

    Returns:
        A flat dict like ``{"a.b.c": "value"}``.
    """
    items: dict[str, Any] = {}
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(_flatten_dict(v, new_key, sep=sep))
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                items[f"{new_key}[{i}]"] = item
        else:
            items[new_key] = v
    return items


def _insights_from_config(keys: list[str], source: str) -> list[str]:
    """Generate human-readable insight strings from flattened config keys."""
    if not keys:
        return [f"No configuration keys found in {source}"]

    grouped: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        top = k.split(".")[0] if "." in k else "_root"
        grouped[top].append(k)

    insights: list[str] = []
    for group, members in sorted(grouped.items()):
        if len(members) == 1:
            insights.append(f"Config key: {members[0]}")
        else:
            insights.append(f"Config group \"{group}\" contains {len(members)} sub-keys")
    return insights[:MAX_INSIGHTS_PER_SOURCE]


def _insights_from_python(stats: dict[str, Any], rel_path: str) -> list[str]:
    """Generate insight strings from Python code structure stats."""
    insights: list[str] = []
    if stats["classes"]:
        class_names = [c["name"] for c in stats["classes"]]
        insights.append(f"Classes ({len(stats['classes'])}): {', '.join(class_names)}")
    if stats["async_functions"]:
        insights.append(f"Async functions ({len(stats['async_functions'])}): {', '.join(stats['async_functions'][:8])}")
    if stats["functions"]:
        insights.append(f"Total functions: {len(stats['functions'])}")
    if stats["imports"]:
        import_descriptions = []
        for imp in stats["imports"][:10]:
            if isinstance(imp, dict):
                import_descriptions.append(f"from {imp['from']} import {', '.join(imp['names'][:3])}")
            else:
                import_descriptions.append(f"import {imp}")
        insights.append(f"Imports ({len(stats['imports'])}): {'; '.join(import_descriptions)}")
    if stats.get("has_docstring"):
        insights.append(f"Module has docstring")
    if stats["lines"]:
        insights.append(f"Source lines: {stats['lines']}")
    return insights


# ═══════════════════════════════════════════════════════════════════════════════
# Web scraping utilities
# ═══════════════════════════════════════════════════════════════════════════════


async def _fetch_url(url: str, timeout: int = HTTP_TIMEOUT_SECONDS) -> str:
    """Fetch a URL's text content asynchronously using ``aiohttp``.

    Args:
        url: The full URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        The response body as a string (truncated to ``MAX_PAGE_SIZE_BYTES``).

    Raises:
        RuntimeError: If ``aiohttp`` is not installed or the request fails.
    """
    try:
        import aiohttp
    except ImportError:
        raise RuntimeError(
            "aiohttp is required for web fetching. "
            "Install it with: pip install aiohttp"
        )

    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(url, headers={"User-Agent": "Partner-Evolution/1.0"}) as resp:
                resp.raise_for_status()
                text = await resp.text()
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc

    if len(text) > MAX_PAGE_SIZE_BYTES:
        logger.debug("[KNOWLEDGE_ACQ] Truncating %s from %d to %d bytes",
                     url, len(text), MAX_PAGE_SIZE_BYTES)
        text = text[:MAX_PAGE_SIZE_BYTES]

    return text


def _extract_web_insights(html: str, url: str) -> list[str]:
    """Extract structured insights from raw HTML content.

    Uses simple regex / text-based heuristics (no full HTML parser dependency)
    to extract:
    - Page title (``<title>`` tag)
    - All headings (``<h1>`` through ``<h6>``)
    - All code blocks (``<code>``, ``<pre>``)
    - Notable paragraphs (``<p>`` with sufficient length)

    Args:
        html: Raw HTML content.
        url: Source URL (for logging).

    Returns:
        List of insight strings.
    """
    insights: list[str] = []

    # Title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
        if title:
            insights.append(f"Page title: {title[:200]}")

    # Headings
    for level in range(1, 7):
        headings = re.findall(
            rf"<h{level}[^>]*>(.*?)</h{level}>", html, re.IGNORECASE | re.DOTALL
        )
        for h in headings[:5]:  # cap per level
            text = re.sub(r"<[^>]+>", "", h).strip()
            if text:
                insights.append(f"H{level}: {text[:200]}")

    # Code blocks
    code_blocks = re.findall(
        r"<(?:code|pre)[^>]*>(.*?)</(?:code|pre)>", html, re.IGNORECASE | re.DOTALL
    )
    if code_blocks:
        code_snippets = []
        for cb in code_blocks[:5]:
            code_text = re.sub(r"<[^>]+>", "", cb).strip()
            if code_text:
                snippet = code_text[:150]
                code_snippets.append(snippet)
        insights.append(f"Code blocks: {len(code_blocks)} found")
        for i, s in enumerate(code_snippets[:3]):
            insights.append(f"Code snippet {i + 1}: {s}")

    # Paragraphs
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL)
    meaningful_paragraphs = []
    for p in paragraphs:
        text = re.sub(r"<[^>]+>", "", p).strip()
        if len(text) > 60:  # Skip very short fragments
            meaningful_paragraphs.append(text[:200])
    for p in meaningful_paragraphs[:3]:
        insights.append(f"Content: {p}")

    if not insights:
        insights.append(f"Fetched {url} but could not extract structured content")

    return insights[:MAX_INSIGHTS_PER_SOURCE]


# ═══════════════════════════════════════════════════════════════════════════════
# GitHub repo analysis
# ═══════════════════════════════════════════════════════════════════════════════


def _scan_repo_structure(repo_path: Path, focus_area: str = "") -> dict[str, Any]:
    """Walk a cloned repository and collect structural metadata.

    Scans for:
    - Python source files (``.py``)
    - Configuration files (``.yaml``, ``.yml``, ``.json``, ``.toml``, ``.ini``,
      ``.cfg``, ``.env``)
    - Documentation files (``.md``, ``.rst``, ``.txt``)
    - Top-level directory listing
    - Total file counts by category
    - If ``focus_area`` is provided, filter paths containing that substring.

    Args:
        repo_path: The path to the cloned/checked-out repository.
        focus_area: Optional keyword substring to filter relevant paths. Pass
            an empty string to include everything.

    Returns:
        A dict with keys ``top_dirs``, ``python_files``, ``config_files``,
        ``doc_files``, ``total_files``, ``source_lines``, ``structure`` (nested
        directory map limited to depth 3).
    """
    result: dict[str, Any] = {
        "top_dirs": [],
        "python_files": [],
        "config_files": [],
        "doc_files": [],
        "total_files": 0,
        "source_lines": 0,
        "structure": {},
    }

    if not repo_path.is_dir():
        logger.warning("[KNOWLEDGE_ACQ] repo path %s is not a directory", repo_path)
        return result

    # Top-level directories
    for entry in sorted(repo_path.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            result["top_dirs"].append(entry.name)

    # Recursive file scan (limit depth to avoid huge repos)
    for root_str, dirs, files in os.walk(str(repo_path)):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel_root = Path(root_str).relative_to(repo_path)

        for fname in files:
            fpath = Path(root_str) / fname
            rel_path = str(rel_root / fname)

            # Apply focus filter if set
            if focus_area and focus_area.lower() not in rel_path.lower():
                continue

            result["total_files"] += 1

            if fname.endswith(".py"):
                result["python_files"].append(rel_path)
                try:
                    result["source_lines"] += len(
                        fpath.read_text(encoding="utf-8", errors="replace").splitlines()
                    )
                except Exception:
                    pass
            elif fname.endswith((".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".env")):
                result["config_files"].append(rel_path)
            elif fname.endswith((".md", ".rst", ".txt")):
                result["doc_files"].append(rel_path)

    # Build a shallow structure tree (depth 3)
    structure: dict[str, Any] = {}
    for py_file in result["python_files"]:
        parts = py_file.replace("\\", "/").split("/")
        for i in range(1, min(len(parts) + 1, 4)):
            key = "/".join(parts[:i])
            if key not in structure:
                structure[key] = {"type": "dir" if i < len(parts) else "file", "children": 0}
            structure[key]["children"] += 1
    result["structure"] = structure

    return result


def _insights_from_repo_scan(scan: dict[str, Any], repo_url: str) -> list[str]:
    """Generate human-readable insight strings from a repo structure scan."""
    insights: list[str] = []

    insights.append(f"Repository: {repo_url}")

    if scan["total_files"]:
        insights.append(f"Total files scanned: {scan['total_files']}")

    if scan["top_dirs"]:
        insights.append(f"Top-level directories ({len(scan['top_dirs'])}): {', '.join(scan['top_dirs'][:12])}")

    if scan["python_files"]:
        total_py = len(scan["python_files"])
        insights.append(f"Python source files: {total_py} ({scan['source_lines']:,} lines)")

    if scan["config_files"]:
        insights.append(f"Config files: {len(scan['config_files'])}")

    if scan["doc_files"]:
        insights.append(f"Documentation files: {len(scan['doc_files'])}")

    # Top-level python module summary
    top_packages = sorted(
        set(p.split("/")[0] for p in scan["python_files"] if "/" in p)
    )
    if top_packages:
        insights.append(f"Python packages: {', '.join(top_packages[:10])}")

    if not insights:
        insights.append(f"No relevant files found matching focus area")

    return insights[:MAX_INSIGHTS_PER_SOURCE]


# ═══════════════════════════════════════════════════════════════════════════════
# KnowledgeAcquirer — public API
# ═══════════════════════════════════════════════════════════════════════════════


class KnowledgeAcquirer:
    """Multi-source knowledge acquisition for Partner's self-evolution system.

    Acquires structured ``Knowledge`` records from GitHub repositories, web pages,
    local config files, and local Python source code. Designed as a stateless-ish
    service — all mutable state (e.g. cloned repo directories) is kept under
    ``repos_dir``.

    Args:
        repos_dir: Directory under which cloned repositories are stored. Created
            automatically if it does not exist. Defaults to
            ``/tmp/partner_evolution_repos``.
    """

    def __init__(self, repos_dir: str | Path = DEFAULT_REPOS_DIR) -> None:
        self._repos_dir = Path(repos_dir)
        _ensure_dir(self._repos_dir)
        logger.debug("[KNOWLEDGE_ACQ] initialized, repos_dir=%s", self._repos_dir)

    # ── Public acquisition methods ──────────────────────────────────────────

    async def fetch_from_github(
        self,
        repo_url: str,
        focus_area: str = "",
    ) -> Knowledge:
        """Clone (or pull) a GitHub repository and analyze its code structure.

        The repository is cloned into ``{repos_dir}/{repo_name}``. If the
        directory already exists, a ``git pull`` is attempted instead of a
        full clone. After acquiring the source, a structural scan and Python
        pattern extraction are performed.

        Args:
            repo_url: Full HTTPS or SSH URL of the GitHub repository, e.g.
                ``"https://github.com/nousresearch/hermes-agent"``.
            focus_area: Optional keyword substring to filter the analysis to
                files/paths that contain this string (case-insensitive). Pass
                ``""`` to analyze the entire repository.

        Returns:
            A ``Knowledge`` record with ``content_type="github_repo"``,
            key insights about the repo structure, and a list of relevant file
            paths.

        Raises:
            RuntimeError: If the clone/pull operation fails or git is not
                available.
        """
        logger.info("[KNOWLEDGE_ACQ] fetching from GitHub: %s (focus=%s)", repo_url, focus_area or "*")

        repo_name = _safe_repo_name(repo_url)
        repo_path = self._repos_dir / repo_name

        # Clone or pull
        if repo_path.is_dir():
            logger.debug("[KNOWLEDGE_ACQ] repo exists, pulling %s", repo_name)
            _stdout, stderr, rc = await _async_shell(
                ["git", "pull", "--ff-only"],
                cwd=repo_path,
            )
            if rc != 0:
                logger.warning("[KNOWLEDGE_ACQ] git pull failed (rc=%d): %s", rc, stderr)
                # Continue with stale data rather than failing entirely
        else:
            logger.debug("[KNOWLEDGE_ACQ] cloning %s → %s", repo_url, repo_path)
            _stdout, stderr, rc = await _async_shell(
                ["git", "clone", repo_url, str(repo_path)],
                timeout=GIT_CLONE_TIMEOUT_SECONDS,
            )
            if rc != 0:
                raise RuntimeError(
                    f"Failed to clone {repo_url}: {stderr or 'unknown error (rc=%d)' % rc}"
                )

        # Scan structure
        scan = _scan_repo_structure(repo_path, focus_area=focus_area)
        insights = _insights_from_repo_scan(scan, repo_url)

        # Analyze individual Python files for deeper patterns
        relevant_files: list[str] = []
        for py_file in scan["python_files"][:30]:  # cap for performance
            full_path = repo_path / py_file
            stats = _extract_python_patterns(full_path)
            file_insights = _insights_from_python(stats, py_file)
            insights.extend(file_insights)
            relevant_files.append(py_file)

        # Cap insights
        insights = insights[:MAX_INSIGHTS_PER_SOURCE]

        return Knowledge(
            source=repo_url,
            content_type="github_repo",
            key_insights=tuple(insights),
            relevant_files=tuple(relevant_files),
            raw_metadata={
                "repo_name": repo_name,
                "total_files": scan["total_files"],
                "python_files": len(scan["python_files"]),
                "source_lines": scan["source_lines"],
                "top_dirs": scan["top_dirs"],
            },
        )

    async def fetch_from_web(self, url: str) -> Knowledge:
        """Fetch a web page and extract structured information.

        Uses ``aiohttp`` for async HTTP GET, then parses the HTML with regex-
        based heuristics to extract title, headings, code blocks, and meaningful
        paragraphs. No external HTML parsing library is required.

        Args:
            url: The full URL to fetch. Must be a valid HTTP or HTTPS URL.

        Returns:
            A ``Knowledge`` record with ``content_type="web_page"``, insights
            extracted from the page content, and an empty ``relevant_files``
            list (since the source is remote).

        Raises:
            RuntimeError: If ``aiohttp`` is not installed, the URL is invalid,
                or the HTTP request fails.
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Unsupported URL scheme '{parsed.scheme}'. Only http:// and https:// are supported."
            )

        logger.info("[KNOWLEDGE_ACQ] fetching web page: %s", url)

        html = await _fetch_url(url)
        insights = _extract_web_insights(html, url)

        return Knowledge(
            source=url,
            content_type="web_page",
            key_insights=tuple(insights),
            relevant_files=(),
            raw_metadata={
                "url": url,
                "html_size_bytes": len(html.encode("utf-8")),
            },
        )

    async def fetch_from_local(self, path: str | Path) -> Knowledge:
        """Read a local file or directory and extract structured knowledge.

        Behaviour depends on the target:
        - **Python file** (``.py``): Extracts class/function/import patterns.
        - **Config file** (``.yaml``, ``.yml``, ``.json``, ``.toml``, ``.ini``,
          ``.cfg``, ``.env``): Parses and flattens key-value pairs.
        - **Directory**: Recursively scans for Python and config files,
          aggregates patterns from all discovered sources.
        - **Other files**: Returns a basic metadata-only knowledge record.

        Args:
            path: Filesystem path to the target file or directory.

        Returns:
            A ``Knowledge`` record with ``content_type`` set appropriately
            (``"local_code"``, ``"config_file"``, or ``"local_code"`` for
            directories).

        Raises:
            FileNotFoundError: If the path does not exist.
        """
        target = Path(path).resolve()
        logger.info("[KNOWLEDGE_ACQ] fetching local: %s", target)

        if not target.exists():
            raise FileNotFoundError(f"Local path does not exist: {target}")

        if target.is_dir():
            return await self._acquire_from_directory(target)

        suffix = target.suffix.lower()

        if suffix == ".py":
            stats = _extract_python_patterns(target)
            insights = _insights_from_python(stats, target.name)
            return Knowledge(
                source=str(target),
                content_type="local_code",
                key_insights=tuple(insights),
                relevant_files=(str(target),),
                raw_metadata=stats,
            )

        if suffix in (".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".env"):
            config_stats = _extract_config_patterns(target)
            insights = _insights_from_config(config_stats["keys"], str(target))
            insights.append(f"Format: {config_stats['format']}")
            if config_stats["parse_error"]:
                insights.append(f"Parse note: {config_stats['parse_error']}")
            return Knowledge(
                source=str(target),
                content_type="config_file",
                key_insights=tuple(insights),
                relevant_files=(str(target),),
                raw_metadata=config_stats,
            )

        # Generic file — just record metadata
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            lines = len(text.splitlines())
            size = len(text.encode("utf-8"))
        except Exception:
            lines = 0
            size = 0

        return Knowledge(
            source=str(target),
            content_type="local_code",
            key_insights=(f"File: {target.name} ({lines} lines, {size} bytes)",),
            relevant_files=(str(target),),
            raw_metadata={"lines": lines, "size_bytes": size, "suffix": suffix},
        )

    async def analyze_code_structure(self, repo_path: str | Path) -> Knowledge:
        """Analyze the code structure of a local repository or project directory.

        Performs a deep structural scan comparable to
        :meth:`fetch_from_github`, but operates on an already-local path
        (no cloning). Useful for analyzing Partner's own source tree or an
        already-downloaded project.

        Args:
            repo_path: Path to a local project directory containing source code.

        Returns:
            A ``Knowledge`` record with ``content_type="code_structure"``,
            structural insights, and a list of relevant Python file paths.

        Raises:
            FileNotFoundError: If ``repo_path`` does not exist.
            NotADirectoryError: If ``repo_path`` is not a directory.
        """
        target = Path(repo_path).resolve()
        logger.info("[KNOWLEDGE_ACQ] analyzing code structure: %s", target)

        if not target.exists():
            raise FileNotFoundError(f"Path does not exist: {target}")
        if not target.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {target}")

        scan = _scan_repo_structure(target)
        insights = _insights_from_repo_scan(scan, str(target))

        # Deeper Python analysis on each source file
        relevant_files: list[str] = []
        all_class_names: list[str] = []
        total_async_funcs = 0

        for py_file in scan["python_files"][:40]:
            full_path = target / py_file
            stats = _extract_python_patterns(full_path)
            file_insights = _insights_from_python(stats, py_file)
            insights.extend(file_insights)
            relevant_files.append(py_file)
            for c in stats["classes"]:
                all_class_names.append(c["name"])
            total_async_funcs += len(stats["async_functions"])

        # Summary insights
        if all_class_names:
            insights.append(
                f"Total classes across project: {len(all_class_names)} "
                f"({', '.join(all_class_names[:10])})"
            )
        if total_async_funcs:
            insights.append(f"Total async functions: {total_async_funcs}")

        insights = insights[:MAX_INSIGHTS_PER_SOURCE]

        return Knowledge(
            source=str(target),
            content_type="code_structure",
            key_insights=tuple(insights),
            relevant_files=tuple(relevant_files),
            raw_metadata={
                "total_files": scan["total_files"],
                "python_files": len(scan["python_files"]),
                "source_lines": scan["source_lines"],
                "config_files": len(scan["config_files"]),
                "doc_files": len(scan["doc_files"]),
                "top_dirs": scan["top_dirs"],
                "total_classes": len(all_class_names),
                "total_async_funcs": total_async_funcs,
            },
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _acquire_from_directory(self, directory: Path) -> Knowledge:
        """Acquire knowledge from a directory by recursively scanning contents."""
        scan = _scan_repo_structure(directory)

        all_insights: list[str] = []
        all_files: list[str] = []

        # Process Python files
        for py_file in scan["python_files"][:30]:
            full_path = directory / py_file
            stats = _extract_python_patterns(full_path)
            all_insights.extend(_insights_from_python(stats, py_file))
            all_files.append(str(full_path))

        # Process config files
        for cfg_file in scan["config_files"][:15]:
            full_path = directory / cfg_file
            config_stats = _extract_config_patterns(full_path)
            all_insights.extend(_insights_from_config(config_stats["keys"], str(cfg_file)))
            all_files.append(str(full_path))

        # Add directory-level insights
        all_insights.extend(_insights_from_repo_scan(scan, str(directory)))

        all_insights = all_insights[:MAX_INSIGHTS_PER_SOURCE]

        return Knowledge(
            source=str(directory),
            content_type="local_code",
            key_insights=tuple(all_insights),
            relevant_files=tuple(all_files),
            raw_metadata={
                "total_files": scan["total_files"],
                "python_files": len(scan["python_files"]),
                "config_files": len(scan["config_files"]),
                "doc_files": len(scan["doc_files"]),
                "source_lines": scan["source_lines"],
            },
        )

    # ── Batch / convenience ──────────────────────────────────────────────────

    async def acquire_batch(
        self,
        sources: list[dict[str, str]],
    ) -> list[Knowledge]:
        """Acquire knowledge from multiple sources concurrently.

        Each source dict must have a ``"type"`` key (one of ``"github"``,
        ``"web"``, ``"local"``, ``"code_structure"``) and source-specific keys:

        - ``github``: requires ``"url"``, optional ``"focus_area"``.
        - ``web``: requires ``"url"``.
        - ``local``: requires ``"path"``.
        - ``code_structure``: requires ``"path"``.

        All acquisitions run concurrently via ``asyncio.gather``. Failures for
        individual sources are captured and returned as ``Knowledge`` records
        with a single error insight rather than propagating.

        Args:
            sources: List of source descriptor dicts.

        Returns:
            A list of ``Knowledge`` records, one per source, in the same order
            as the input. Failed acquisitions produce records with
            ``content_type="error"`` and the error message as an insight.

        Example::

            results = await acquirer.acquire_batch([
                {"type": "github", "url": "https://github.com/user/repo", "focus_area": "pipeline"},
                {"type": "web", "url": "https://example.com/docs"},
                {"type": "local", "path": "/path/to/config.yaml"},
            ])
        """
        async def _acquire_one(spec: dict[str, str]) -> Knowledge:
            source_type = spec.get("type", "")
            try:
                if source_type == "github":
                    return await self.fetch_from_github(
                        repo_url=spec["url"],
                        focus_area=spec.get("focus_area", ""),
                    )
                elif source_type == "web":
                    return await self.fetch_from_web(url=spec["url"])
                elif source_type == "local":
                    return await self.fetch_from_local(path=spec["path"])
                elif source_type == "code_structure":
                    return await self.analyze_code_structure(repo_path=spec["path"])
                else:
                    return Knowledge(
                        source=json.dumps(spec),
                        content_type="error",
                        key_insights=(f"Unknown source type: {source_type}",),
                    )
            except Exception as exc:
                logger.warning("[KNOWLEDGE_ACQ] batch source failed: %s — %s", spec, exc)
                return Knowledge(
                    source=json.dumps(spec),
                    content_type="error",
                    key_insights=(f"Acquisition failed: {exc}",),
                )

        tasks = [_acquire_one(spec) for spec in sources]
        results: list[Knowledge] = await asyncio.gather(*tasks)
        return results

    async def fetch_from_github_sync(
        self,
        repo_url: str,
        focus_area: str = "",
    ) -> Knowledge:
        """Synchronous-style alias for :meth:`fetch_from_github`.

        This method exists for compatibility with codebases that expect a
        sync-like interface and internally call ``asyncio.run()``. Prefer
        ``await acquirer.fetch_from_github(...)`` directly in async contexts.
        """
        return await self.fetch_from_github(repo_url, focus_area=focus_area)

    async def fetch_from_local_sync(
        self,
        path: str | Path,
    ) -> Knowledge:
        """Synchronous-style alias for :meth:`fetch_from_local`.

        Prefer ``await acquirer.fetch_from_local(...)`` directly in async contexts.
        """
        return await self.fetch_from_local(path)
