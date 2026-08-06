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

    # ── Strategy 0: Fuzzy glob for truncated/incomplete paths (fast, no find) ──
    import glob as _fzglob
    path_parts = user_path.replace('\\', '/').rstrip('/').split('/')
    if len(path_parts) >= 2 and not os.path.exists(user_path):
        # Try wildcard from the deepest existing parent
        for i in range(len(path_parts) - 1, max(0, len(path_parts) - 4), -1):
            prefix = '/'.join(path_parts[:i]) if i > 0 else '/'
            if not os.path.isdir(prefix):
                continue
            suffix = path_parts[i] if i < len(path_parts) else ''
            if len(suffix) < 3:
                continue
            # Glob with wildcard
            pattern = os.path.join(prefix, suffix + '*')
            candidates = _fzglob.glob(pattern)
            if candidates:
                files = [c for c in candidates if os.path.isfile(c)]
                if files:
                    logger.info("[FILE_RESOLVER] fuzzy-glob: %s → %s", user_path, files[0])
                    return files[0], True
                dirs = [c for c in candidates if os.path.isdir(c)]
                if dirs:
                    logger.info("[FILE_RESOLVER] fuzzy-dir: %s → %s/", user_path, dirs[0])
                    return dirs[0], True
            # Also try: split the last component further
            if i == len(path_parts) - 1 and len(suffix) > 5:
                for j in range(len(suffix) - 1, 2, -1):
                    pattern2 = os.path.join(prefix, suffix[:j] + '*')
                    c2 = _fzglob.glob(pattern2)
                    if c2:
                        logger.info("[FILE_RESOLVER] fuzzy-partial: %s → %s", user_path, c2[0])
                        return c2[0], True

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

    # ── Strategy 4: Fuzzy glob matching for truncated/incomplete paths ──
    # Handle cases where the path is truncated or contains partial directory names
    import glob as _glob
    import re as _re_fuzzy
    
    # Try wildcard matching: if path contains a truncated dir, search up
    path_parts = user_path.replace('\\', '/').split('/')
    if len(path_parts) >= 2:
        # Build candidate: replace last component with wildcard and search
        for i in range(len(path_parts) - 1, max(0, len(path_parts) - 3), -1):
            prefix = '/'.join(path_parts[:i])
            suffix_pattern = '*'.join(path_parts[i:]) + '*'
            if os.path.isdir(prefix):
                candidates = _glob.glob(os.path.join(prefix, suffix_pattern))
                if candidates:
                    # Pick the best match: prefer existing files over dirs
                    files = [c for c in candidates if os.path.isfile(c)]
                    if files:
                        found = files[0]
                        logger.info("[FILE_RESOLVER] fuzzy-glob resolved: %s → %s", user_path, found)
                        return found, True
                    # If the best match is a directory with known files
                    dirs = [c for c in candidates if os.path.isdir(c)]
                    if dirs:
                        # Look for common target files inside
                        for _ext in ('.txt', '.md', '.py', '.h5ad', '.pdf', '.pdb', '.json', '.sdf', '.smi'):
                            for _df in _glob.glob(os.path.join(dirs[0], f'*{_ext}')):
                                logger.info("[FILE_RESOLVER] fuzzy-dir resolved: %s → %s", user_path, _df)
                                return _df, True
                        # Return the dir itself as a fallback
                        logger.info("[FILE_RESOLVER] fuzzy-dir fallback: %s → %s/", user_path, dirs[0])
                        return dirs[0], True

    # ── Last resort: create symlink if path looks like truncated directory ──
    # E.g., /.../molgen_explorati → /.../molgen_exploration
    parent = os.path.dirname(user_path)
    if os.path.isdir(parent):
        import glob as _lrglob
        truncated_name = os.path.basename(user_path)
        if len(truncated_name) >= 5:
            candidates = _lrglob.glob(os.path.join(parent, truncated_name + '*'))
            if candidates:
                target = candidates[0]
                try:
                    os.symlink(target, user_path)
                    logger.info("[FILE_RESOLVER] symlink created: %s → %s", user_path, target)
                    return user_path, True
                except OSError:
                    pass

    logger.warning("[FILE_RESOLVER] file not found: %s (basename=%s)", user_path, fname)
    return user_path, False
