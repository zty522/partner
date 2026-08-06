"""Dynamic input path resolution for Agent dispatch.

When an agent task specifies an input file path that doesn't exist on disk,
this module searches across known data directories.  Supports exact match,
basename match, and fuzzy substring match.

Extensible: other agents register their own search paths.
"""

import glob as _glob
import logging
import os
from typing import Callable

logger = logging.getLogger(__name__)

# ── Default search directories ──
# These are checked in order when the given input path doesn't exist.
_DEFAULT_SEARCH_ROOTS: list[str] = [
    "/mnt/e/work/data/",
    "/mnt/e/work/partner_workspace/data/",
    "/mnt/e/work/partner_workspace/",
    "/data/",
    "/home/os/data/",
]

# ── Per-agent search path registrations ──
# Agents can register additional directories to search.
_AGENT_SEARCH_ROOTS: dict[str, list[str]] = {
    "cytobridge": [
        "/mnt/e/work/data/",
        "/mnt/e/work/partner_workspace/",
    ],
    "cytobridge-agent": [
        "/mnt/e/work/data/",
        "/mnt/e/work/partner_workspace/",
    ],
}

# ── Custom resolvers ──
# Advanced per-agent resolver logic beyond simple path search.
_AGENT_RESOLVERS: dict[str, Callable[[str], str | None]] = {}


def register_agent_resolver(agent_name: str, resolver_fn: Callable[[str], str | None]) -> None:
    """Register a custom path resolver for a specific agent.

    Args:
        agent_name: Agent manifest name.
        resolver_fn: Called with the unresolved input path.  Must return
                     an absolute path or None (fall through to default search).
    """
    _AGENT_RESOLVERS[agent_name] = resolver_fn


def register_agent_search_roots(agent_name: str, roots: list[str]) -> None:
    """Add extra search directories for a specific agent."""
    _AGENT_SEARCH_ROOTS.setdefault(agent_name, [])
    for r in roots:
        if r not in _AGENT_SEARCH_ROOTS[agent_name]:
            _AGENT_SEARCH_ROOTS[agent_name].append(r)


def _search_dirs_for(agent_name: str) -> list[str]:
    """Assemble the ordered list of directories to search."""
    dirs = list(_DEFAULT_SEARCH_ROOTS)
    agent_dirs = _AGENT_SEARCH_ROOTS.get(agent_name, [])
    for d in agent_dirs:
        if d not in dirs:
            dirs.append(d)
    return dirs


def resolve_input_path(input_path: str, agent_name: str = "") -> str:
    """Resolve an input file path, searching dynamically if it doesn't exist.

    Steps (in order):
    1. If input_path already exists → return as-is.
    2. If a custom resolver is registered for the agent → try it.
    3. Extract basename → search registered directories:
       a. Exact basename match in each directory root.
       b. Recursive ``**`` glob for basename.
       c. Substring match (any file whose name contains any word from basename).
    4. Return original path if nothing found (lets the agent fail gracefully).

    Args:
        input_path: The path provided by the planner / user (may be absolute).
        agent_name: Name of the agent being dispatched (for agent-specific roots).

    Returns:
        Resolved absolute path, or the original ``input_path`` if nothing found.
    """
    # 1. Already exists
    if os.path.exists(input_path):
        return os.path.abspath(input_path)

    logger.info(
        "[PATH_RESOLVER] Input path not found: %s (agent=%s). Searching...",
        input_path, agent_name or "(none)",
    )

    # 2. Custom resolver
    if agent_name:
        resolver = _AGENT_RESOLVERS.get(agent_name)
        if resolver:
            try:
                result = resolver(input_path)
                if result and os.path.exists(result):
                    logger.info("[PATH_RESOLVER] Custom resolver found: %s", result)
                    return os.path.abspath(result)
            except Exception as e:
                logger.warning("[PATH_RESOLVER] Custom resolver failed: %s", e)

    basename = os.path.basename(input_path)
    if not basename:
        return input_path

    # Strip any query/fragment (unlikely in h5ad paths but safe)
    basename = basename.split("?")[0]

    dirs = _search_dirs_for(agent_name)

    # 3a. Exact basename match in each root
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        candidate = os.path.join(directory, basename)
        if os.path.isfile(candidate):
            logger.info("[PATH_RESOLVER] Found: %s", candidate)
            return os.path.abspath(candidate)

    # 3b. Recursive glob
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        pattern = os.path.join(directory, "**", basename)
        matches = _glob.glob(pattern, recursive=True)
        if matches:
            logger.info("[PATH_RESOLVER] Found (recursive): %s", matches[0])
            return os.path.abspath(matches[0])

    # 3c. Fuzzy substring match
    # Split basename into tokens (e.g. "pancreas.h5ad" → ["pancreas", "h5ad"])
    stem, *_rest = basename.rsplit(".", 1)
    tokens = stem.replace("-", " ").replace("_", " ").split()
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        for f in os.listdir(directory):
            if not os.path.isfile(os.path.join(directory, f)):
                continue
            f_lower = f.lower()
            # All non-trivial tokens must appear in the filename
            if all(t.lower() in f_lower for t in tokens if len(t) > 2):
                candidate = os.path.join(directory, f)
                logger.info("[PATH_RESOLVER] Found (fuzzy): %s", candidate)
                return os.path.abspath(candidate)

    logger.warning(
        "[PATH_RESOLVER] Could not resolve: %s (basename=%s, searched=%s)",
        input_path, basename, dirs,
    )
    return input_path  # Give up — let the agent fail gracefully
