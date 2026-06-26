"""Hermes-style dynamic file path resolver.

Instead of hardcoded search paths, uses `find` / `locate` / `os.walk`
to locate files the user referenced by an incomplete or incorrect path.
Mirrors how Hermes Agent uses its terminal+file tools to dynamically probe
the filesystem when a given path doesn't exist.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories to skip during os.walk (too large or irrelevant)
_SKIP_DIRS = {
    "/proc", "/sys", "/dev", "/run", "/tmp",
    "/var/cache", "/var/log", "/var/tmp",
    "/snap", "/lost+found",
    # Python envs
    "/home/os/miniconda3", "/home/os/anaconda3",
    "/opt/conda",
    # Windows (WSL)
    "/mnt/c/Windows",
}


def _should_skip(path: str) -> bool:
    """Check if a directory should be skipped during os.walk."""
    p = path.rstrip("/")
    if p in _SKIP_DIRS:
        return True
    # Skip hidden directories
    basename = os.path.basename(p)
    if basename.startswith(".") and basename not in (".", ".."):
        return True
    # Skip __pycache__, node_modules, etc.
    if basename in ("__pycache__", "node_modules", ".git", ".svn", "venv", ".venv"):
        return True
    return False


def resolve_file_path(
    user_path: str,
    search_timeout: int = 15,
) -> tuple[str, bool]:
    """Resolve a user-provided file path to a real file on disk.

    Uses three strategies in order, matching how Hermes Agent dynamically
    searches for files when the given path doesn't exist.

    Args:
        user_path: The path the user provided (may be incomplete/incorrect).
        search_timeout: Total max seconds for all search strategies.

    Returns:
        Tuple of (resolved_path, found). If found is False, the path
        couldn't be located anywhere.
    """
    user_path = str(user_path or "").strip()
    if not user_path:
        return "", False

    fname = os.path.basename(user_path)

    # ── Quick check: the path already exists ──
    if os.path.exists(user_path):
        resolved = os.path.abspath(user_path)
        logger.info("[FILE_RESOLVER] path exists: %s", resolved)
        return resolved, True

    # ── Strategy 1: `find` command (fastest, like Hermes' terminal tool) ──
    # Searches up to depth 8 to avoid deep recursion into massive dirs
    try:
        result = subprocess.run(
            ["find", "/", "-maxdepth", "8", "-name", fname, "-type", "f"],
            capture_output=True, text=True, timeout=search_timeout,
        )
        if result.stdout.strip():
            found_path = result.stdout.strip().split("\n")[0]
            if os.path.isfile(found_path):
                logger.info("[FILE_RESOLVER] find resolved: %s → %s", user_path, found_path)
                return found_path, True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug("[FILE_RESOLVER] find failed: %s", e)

    # ── Strategy 2: `locate` database (instant, may have stale data) ──
    try:
        result = subprocess.run(
            ["locate", "-l", "3", "--basename", fname],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line and os.path.isfile(line):
                    logger.info("[FILE_RESOLVER] locate resolved: %s → %s", user_path, line)
                    return line, True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug("[FILE_RESOLVER] locate failed: %s", e)

    # ── Strategy 3: os.walk in common data directories ──
    # This catches files in locations that find might take too long on
    for root in ("/data", "/mnt/e/work/data", "/mnt/e/work", "/mnt", str(Path.home())):
        if not os.path.isdir(root):
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                # Prune search: skip irrelevant directories
                dirnames[:] = [d for d in dirnames if not _should_skip(os.path.join(dirpath, d))]
                # Limit depth to avoid deep recursion
                depth = dirpath.count(os.sep) - root.count(os.sep)
                if depth > 8:
                    dirnames.clear()
                    continue
                if fname in filenames:
                    found_path = os.path.join(dirpath, fname)
                    logger.info("[FILE_RESOLVER] os.walk resolved: %s → %s", user_path, found_path)
                    return found_path, True
        except (PermissionError, OSError) as e:
            logger.debug("[FILE_RESOLVER] os.walk error at %s: %s", root, e)
            continue

    logger.warning("[FILE_RESOLVER] file not found: %s (basename=%s)", user_path, fname)
    return user_path, False
