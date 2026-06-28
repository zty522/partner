"""Workspace Migration Script — Migrate old workspace to ~/.partner structure.

Usage:
    from partner.migrate_workspace import migrate_workspace
    migrate_workspace(old_workspace="/mnt/e/work/partner_workspace",
                      new_base="~/.partner")

Or standalone:
    python -m partner.migrate_workspace [--old OLD] [--new NEW]

This moves files from a flat/messy workspace into a clean hierarchy:
    ~/.partner/
    ├── 00_config/          # Config files
    ├── 10_logs/            # Log/journal files
    ├── 20_records/         # Core records
    │   └── projects/{name}/
    │       ├── exploration_log.md
    │       ├── knowledge.json
    │       ├── experiments.csv
    │       └── artifacts/
    └── 99_temp/            # Temporary files
"""

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _expand_home(path: str) -> str:
    """Expand ~ to the user's home directory."""
    return os.path.expanduser(path)


def _ensure_dirs(base: str, *subdirs: str) -> str:
    """Create a nested directory structure under base and return its path."""
    target = os.path.join(base, *subdirs)
    os.makedirs(target, exist_ok=True)
    return target


def _try_copy(src: str, dst: str) -> Optional[str]:
    """Copy a file from src to dst if src exists. Returns destination path or None."""
    if not os.path.isfile(src):
        logger.debug(f"File not found, skipping: {src}")
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.copy2(src, dst)
        logger.info(f"  Copied: {src} -> {dst}")
        return dst
    except Exception as e:
        logger.warning(f"  Failed to copy {src} -> {dst}: {e}")
        return None


def _try_move(src: str, dst: str) -> Optional[str]:
    """Move a file from src to dst if src exists. Returns destination path or None."""
    if not os.path.isfile(src) and not os.path.isdir(src):
        logger.debug(f"Path not found, skipping: {src}")
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.move(src, dst)
        logger.info(f"  Moved: {src} -> {dst}")
        return dst
    except Exception as e:
        logger.warning(f"  Failed to move {src} -> {dst}: {e}")
        return None


def _load_json(path: str) -> Optional[Dict]:
    """Load a JSON file, returning None if it doesn't exist or is invalid."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"  Failed to read JSON {path}: {e}")
        return None


def _determine_project_name(old_workspace: str) -> str:
    """Try to determine the project name from knowledge.json or directory names.

    Heuristics:
    1. Check state/knowledge.json for related_projects
    2. Check for dominant project directory patterns
    3. Fall back to 'default'
    """
    # Try knowledge.json
    knowledge_path = os.path.join(old_workspace, "state", "knowledge.json")
    knowledge = _load_json(knowledge_path)
    if knowledge and "entries" in knowledge:
        projects: Dict[str, int] = {}
        for entry in knowledge["entries"]:
            related = entry.get("related_projects", [])
            if isinstance(related, list):
                for proj in related:
                    projects[proj] = projects.get(proj, 0) + 1
        # Try knowledge.json top-level project_name
        if knowledge.get("project_name"):
            return knowledge["project_name"]
        if projects:
            # Return most common project name
            return max(projects, key=projects.get)

    # Check for result directories that look like projects
    project_indicators = [
        d for d in os.listdir(old_workspace)
        if os.path.isdir(os.path.join(old_workspace, d))
        and any(kw in d.lower() for kw in ["result", "project", "_results"])
    ]
    if project_indicators:
        # Extract project name from directory name
        name = project_indicators[0]
        # age_prediction_results -> age_prediction
        for suffix in ["_results", "_result", "s"]:
            if name.lower().endswith(suffix):
                name = name[: -len(suffix)]
                break
        return name

    # Try state/last_state.json
    last_state = _load_json(os.path.join(old_workspace, "state", "last_state.json"))
    if last_state and last_state.get("active_project"):
        return last_state["active_project"]

    return "default"


def _migrate_configs(old_workspace: str, config_dir: str) -> List[str]:
    """Migrate config files (partner_config.json, qq_config.json)."""
    moved = []
    for fname in ["partner_config.json", "qq_config.json"]:
        src = os.path.join(old_workspace, fname)
        dst = os.path.join(config_dir, fname)
        result = _try_copy(src, dst)
        if result:
            moved.append(result)
    return moved


def _migrate_state_files(old_workspace: str, records_dir: str, temp_dir: str,
                         project_dir: str, logs_dir: str) -> List[str]:
    """Migrate state files (*.json, *.jsonl) to appropriate new locations."""
    moved = []
    state_dir = os.path.join(old_workspace, "state")
    if not os.path.isdir(state_dir):
        logger.warning("  state/ directory not found, skipping state migration")
        return moved

    # journal.jsonl -> 10_logs/
    journal_src = os.path.join(state_dir, "journal.jsonl")
    journal_dst = os.path.join(logs_dir, "journal.jsonl")
    if _try_move(journal_src, journal_dst):
        moved.append(journal_dst)

    # knowledge.json -> 20_records/projects/{project}/
    knowledge_src = os.path.join(state_dir, "knowledge.json")
    knowledge_dst = os.path.join(project_dir, "knowledge.json")
    if _try_move(knowledge_src, knowledge_dst):
        moved.append(knowledge_dst)

    # Other state JSON files -> 20_records/ (as supporting records)
    for fname in os.listdir(state_dir):
        fpath = os.path.join(state_dir, fname)
        if not os.path.isfile(fpath):
            continue

        # Skip files already moved
        if fname in ("journal.jsonl", "knowledge.json"):
            continue

        # .json files
        if fname.endswith(".json"):
            dst = os.path.join(records_dir, fname)
            if _try_move(fpath, dst):
                moved.append(dst)
        # .jsonl files
        elif fname.endswith(".jsonl"):
            dst = os.path.join(records_dir, fname)
            if _try_move(fpath, dst):
                moved.append(dst)

    return moved


def _migrate_project_files(old_workspace: str, project_dir: str) -> List[str]:
    """Migrate project-specific files: exploration_log.md, experiments.csv, artifacts."""
    moved = []

    # exploration_log.md
    for candidate in [
        os.path.join(old_workspace, "logs", "exploration_log.md"),
        os.path.join(old_workspace, "exploration_log.md"),
    ]:
        dst = os.path.join(project_dir, "exploration_log.md")
        if _try_move(candidate, dst):
            moved.append(dst)
            break

    # experiments.csv
    for candidate in [
        os.path.join(old_workspace, "experiments.csv"),
    ]:
        dst = os.path.join(project_dir, "experiments.csv")
        if _try_move(candidate, dst):
            moved.append(dst)
            break

    # Also search for experiments.csv deeper (e.g., in code/, age_prediction_results/)
    if not any("experiments.csv" in p for p in moved):
        found_csv = _find_and_move(os.path.join(old_workspace, "age_prediction_results"),
                                    "experiments.csv", project_dir)
        if found_csv:
            moved.append(found_csv)
        else:
            found_csv = _find_and_move(os.path.join(old_workspace, "code"),
                                        "experiments.csv", project_dir)
            if found_csv:
                moved.append(found_csv)

    # artifacts/ dir
    artifacts_src = os.path.join(old_workspace, "artifacts")
    artifacts_dst = os.path.join(project_dir, "artifacts")
    if os.path.isdir(artifacts_src):
        try:
            shutil.copytree(artifacts_src, artifacts_dst, dirs_exist_ok=True)
            shutil.rmtree(artifacts_src)
            moved.append(artifacts_dst)
            logger.info(f"  Moved: {artifacts_src} -> {artifacts_dst}")
        except Exception as e:
            logger.warning(f"  Failed to move {artifacts_src} -> {artifacts_dst}: {e}")

    # project_artifacts/ -> artifacts/
    proj_artifacts_src = os.path.join(old_workspace, "project_artifacts")
    if os.path.isdir(proj_artifacts_src):
        try:
            shutil.copytree(proj_artifacts_src, artifacts_dst, dirs_exist_ok=True)
            shutil.rmtree(proj_artifacts_src)
            moved.append(artifacts_dst)
            logger.info(f"  Moved: {proj_artifacts_src} -> {artifacts_dst}")
        except Exception as e:
            logger.warning(f"  Failed to move {proj_artifacts_src}: {e}")

    return moved


def _find_and_move(search_dir: str, filename: str, dest_dir: str) -> Optional[str]:
    """Search a directory tree for a filename and move the first found instance."""
    if not os.path.isdir(search_dir):
        return None
    for root, _dirs, files in os.walk(search_dir, topdown=True):
        if filename in files:
            src = os.path.join(root, filename)
            dst = os.path.join(dest_dir, filename)
            return _try_move(src, dst)
    return None


def _migrate_logs(old_workspace: str, logs_dir: str) -> List[str]:
    """Migrate log files from logs/ directory."""
    moved = []
    logs_src = os.path.join(old_workspace, "logs")
    if not os.path.isdir(logs_src):
        logger.debug("  logs/ directory not found, skipping")
        return moved

    for fname in os.listdir(logs_src):
        fpath = os.path.join(logs_src, fname)
        if not os.path.isfile(fpath):
            continue
        # exploration_log.md is handled separately as a project file
        if fname == "exploration_log.md":
            continue
        dst = os.path.join(logs_dir, fname)
        if _try_move(fpath, dst):
            moved.append(dst)

    # Also move root-level .log files
    for fname in os.listdir(old_workspace):
        if fname.endswith(".log"):
            fpath = os.path.join(old_workspace, fname)
            if os.path.isfile(fpath):
                dst = os.path.join(logs_dir, fname)
                if _try_move(fpath, dst):
                    moved.append(dst)

    return moved


def _migrate_temp_files(old_workspace: str, temp_dir: str,
                        exclude_dirs: set) -> List[str]:
    """Migrate uncategorized files to 99_temp/."""
    moved = []
    for fname in os.listdir(old_workspace):
        fpath = os.path.join(old_workspace, fname)
        if not os.path.isfile(fpath):
            continue
        # Skip files already handled
        if fname in ("partner_config.json", "qq_config.json",
                     "exploration_log.md", "experiments.csv",
                     "heartbeat.json", "active_plan.json"):
            continue
        if fname.endswith(".log"):
            continue
        # This is a leftover file — move to temp
        dst = os.path.join(temp_dir, fname)
        if _try_move(fpath, dst):
            moved.append(dst)
    return moved


def _update_config(config_dir: str, new_workspace_path: str) -> bool:
    """Update partner_config.json's workspace.path to the new location."""
    config_path = os.path.join(config_dir, "partner_config.json")
    config = _load_json(config_path)
    if config is None:
        logger.warning("  No partner_config.json found to update")
        return False

    try:
        if "workspace" not in config:
            config["workspace"] = {}
        old_path = config["workspace"].get("path", "unknown")
        config["workspace"]["path"] = new_workspace_path
        # Write back
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info(f"  Updated workspace.path: '{old_path}' -> '{new_workspace_path}'")
        return True
    except Exception as e:
        logger.warning(f"  Failed to update config: {e}")
        return False


def _print_summary(stats: Dict):
    """Print a human-readable summary of the migration."""
    print()
    print("=" * 60)
    print("  MIGRATION SUMMARY")
    print("=" * 60)
    print(f"  Old workspace: {stats.get('old_workspace', 'N/A')}")
    print(f"  New base:      {stats.get('new_base', 'N/A')}")
    print(f"  Project name:  {stats.get('project_name', 'default')}")
    print()
    print(f"  00_config/          {stats.get('configs', 0)} file(s)")
    print(f"  10_logs/            {stats.get('logs', 0)} file(s)")
    print(f"  20_records/         {stats.get('records', 0)} file(s)")
    print(f"  20_records/projects/{stats.get('project_name', 'default')}/  {stats.get('project_files', 0)} file(s)")
    print(f"  99_temp/            {stats.get('temp_files', 0)} file(s)")
    print(f"  Config updated:     {'Yes' if stats.get('config_updated') else 'No'}")
    print()
    print(f"  Total files moved: {stats.get('total', 0)}")
    print("=" * 60)
    print()


def migrate_workspace(old_workspace: Optional[str] = None,
                      new_base: Optional[str] = None,
                      dry_run: bool = False) -> Dict:
    """Migrate a partner workspace to the new ~/.partner structure.

    Args:
        old_workspace: Path to the old workspace directory.
                       If None, will try to read from partner_config.json
                       in the parent of common locations, or default to
                       /mnt/e/work/partner_workspace.
        new_base: Target base directory (default: ~/.partner).
        dry_run: If True, only print what would be done without moving files.

    Returns:
        Dict with migration statistics.
    """
    # --- Determine old workspace path ---
    if old_workspace is None:
        # Try to find partner_config.json in common locations
        candidates = [
            os.path.expanduser("~/.partner/config/partner_config.json"),
            "/mnt/e/work/partner_workspace/partner_config.json",
            "/mnt/e/work/partner/config.json",
        ]
        for candidate in candidates:
            cfg = _load_json(candidate)
            if cfg and cfg.get("workspace") and cfg["workspace"].get("path"):
                old_workspace = cfg["workspace"]["path"]
                break
        if old_workspace is None:
            old_workspace = "/mnt/e/work/partner_workspace"
            logger.info(f"No config found, using default workspace: {old_workspace}")

    # Convert to absolute path
    old_workspace = os.path.abspath(old_workspace)
    if not os.path.isdir(old_workspace):
        raise FileNotFoundError(
            f"Old workspace not found: {old_workspace}"
        )

    # --- Determine new base directory ---
    if new_base is None:
        new_base = os.path.expanduser("~/.partner")
    new_base = _expand_home(new_base)

    # --- Build directory structure ---
    config_dir = _ensure_dirs(new_base, "config")
    logs_dir = _ensure_dirs(new_base, "state/record")
    records_dir = _ensure_dirs(new_base, "projects")
    projects_base = _ensure_dirs(records_dir, "projects")
    temp_dir = _ensure_dirs(new_base, "99_temp")

    # Determine project name
    project_name = _determine_project_name(old_workspace)
    project_dir = _ensure_dirs(projects_base, project_name)
    _ensure_dirs(project_dir, "artifacts")

    # --- Migration stats ---
    stats: Dict = {
        "old_workspace": old_workspace,
        "new_base": new_base,
        "project_name": project_name,
        "configs": 0,
        "logs": 0,
        "records": 0,
        "project_files": 0,
        "temp_files": 0,
        "config_updated": False,
        "total": 0,
    }

    if dry_run:
        print(f"[DRY RUN] Would migrate:")
        print(f"  Old: {old_workspace}")
        print(f"  New: {new_base}")
        print(f"  Project: {project_name}")
        print(f"  Directories would be created under: {new_base}")
        return stats

    print(f"Migrating workspace...")
    print(f"  Old: {old_workspace}")
    print(f"  New: {new_base}")
    print(f"  Project: {project_name}")
    print()

    # 1. Migrate configs
    print("[1/5] Migrating config files...")
    moved_configs = _migrate_configs(old_workspace, config_dir)
    stats["configs"] = len(moved_configs)

    # 2. Migrate log files
    print("[2/5] Migrating log files...")
    moved_logs = _migrate_logs(old_workspace, logs_dir)
    stats["logs"] = len(moved_logs)

    # 3. Migrate state/record files
    print("[3/5] Migrating state/record files...")
    moved_state = _migrate_state_files(
        old_workspace, records_dir, temp_dir, project_dir, logs_dir
    )
    stats["records"] = len(moved_state)

    # 4. Migrate project-specific files
    print("[4/5] Migrating project files...")
    moved_project = _migrate_project_files(old_workspace, project_dir)
    stats["project_files"] = len(moved_project)

    # 5. Migrate leftover temp files
    print("[5/5] Migrating uncategorized files...")
    moved_temp = _migrate_temp_files(
        old_workspace, temp_dir,
        exclude_dirs={"config", "state/record", "projects", "99_temp"}
    )
    stats["temp_files"] = len(moved_temp)

    # Update config
    stats["config_updated"] = _update_config(config_dir, new_base)

    # Total
    stats["total"] = (
        stats["configs"]
        + stats["logs"]
        + stats["records"]
        + stats["project_files"]
        + stats["temp_files"]
    )

    _print_summary(stats)
    return stats


def clean_old_structure(old_workspace: str, dry_run: bool = False) -> List[str]:
    """Remove empty directories left behind after migration.

    This is optional — use with caution.

    Args:
        old_workspace: Path to the old workspace directory.
        dry_run: If True, only print what would be removed.

    Returns:
        List of removed directory paths.
    """
    removed = []
    for root, dirs, files in os.walk(old_workspace, topdown=False):
        if root == old_workspace:
            continue
        if not files and not dirs:
            # Empty directory
            if dry_run:
                print(f"[DRY RUN] Would remove empty dir: {root}")
            else:
                try:
                    os.rmdir(root)
                    removed.append(root)
                    logger.info(f"Removed empty directory: {root}")
                except Exception as e:
                    logger.warning(f"Failed to remove {root}: {e}")
    return removed


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate partner workspace to ~/.partner structure"
    )
    parser.add_argument(
        "--old", "-o",
        default=None,
        help="Path to old workspace (default: auto-detect)"
    )
    parser.add_argument(
        "--new", "-n",
        default=None,
        help="Target base directory (default: ~/.partner)"
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Print what would be done without moving files"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove empty directories left behind after migration"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        stats = migrate_workspace(
            old_workspace=args.old,
            new_base=args.new,
            dry_run=args.dry_run,
        )

        if args.clean and not args.dry_run:
            print("\nCleaning empty directories...")
            removed = clean_old_structure(stats["old_workspace"])
            if removed:
                print(f"  Removed {len(removed)} empty directories")
            else:
                print("  No empty directories found")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
