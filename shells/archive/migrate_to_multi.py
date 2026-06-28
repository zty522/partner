#!/usr/bin/env python3
"""Migration script: converts single-instance workspace to multi-instance layout.

This script:
1. Checks if ~/.partner/global_config.json exists
2. If not, creates it from the template
3. Checks if the current workspace (/mnt/e/work/partner_workspace) needs migration
4. Creates ~/.partner/instances/default/ with 00_config, 10_logs, 20_records, 99_temp
5. Copies current config and state files into the default instance
6. Creates global_config.json with the default instance entry
7. Prints success message
"""

import json
import os
import shutil
import sys
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "configs" / "global_config.template.json"
CONTROL_DIR = Path.home() / ".partner"
GLOBAL_CONFIG = CONTROL_DIR / "global_config.json"
CURRENT_WORKSPACE = Path("/mnt/e/work/partner_workspace")
DEFAULT_INSTANCE_DIR = CONTROL_DIR / "instances" / "default"

INSTANCE_SUBDIRS = ["state/record", "projects", "99_temp"]

# ── Helpers ──────────────────────────────────────────────────────────────────


def green(msg: str) -> str:
    return f"\033[32m{msg}\033[0m"


def yellow(msg: str) -> str:
    return f"\033[33m{msg}\033[0m"


def red(msg: str) -> str:
    return f"\033[31m{msg}\033[0m"


def load_template() -> dict:
    """Load the global config template JSON."""
    if not TEMPLATE_PATH.exists():
        print(red(f"✗ Template not found: {TEMPLATE_PATH}"))
        print(yellow("  Create configs/global_config.template.json first."))
        sys.exit(1)
    with open(TEMPLATE_PATH) as f:
        return json.load(f)


def check_or_create_global_config() -> bool:
    """Return True if global_config.json exists or was created."""
    if GLOBAL_CONFIG.exists():
        print(green(f"✓ Global config already exists: {GLOBAL_CONFIG}"))
        return True

    print(yellow("→ Creating global config from template..."))
    template = load_template()
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    with open(GLOBAL_CONFIG, "w") as f:
        json.dump(template, f, indent=2)
    print(green(f"✓ Created {GLOBAL_CONFIG}"))
    return True


def needs_migration() -> bool:
    """Check if the old workspace exists and hasn't already been migrated."""
    if not CURRENT_WORKSPACE.is_dir():
        print(yellow("  Current workspace does not exist — nothing to migrate."))
        return False

    # Already migrated if default instance dir exists and has content
    config_dir = DEFAULT_INSTANCE_DIR / "config"
    if config_dir.is_dir() and any(config_dir.iterdir()):
        print(green("  Default instance already appears to be migrated."))
        return False

    return True


def copy_tree(src: Path, dst: Path, desc: str) -> None:
    """Copy contents of src directory into dst, creating dst if needed."""
    if not src.is_dir():
        print(yellow(f"  Skipping {desc}: source {src} does not exist"))
        return

    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        count += 1
    print(green(f"  ✓ Copied {count} item(s) from {src.name}/ → {desc}"))


def migrate_workspace() -> bool:
    """Migrate files from the old workspace into the default instance layout."""
    print(yellow("\n── Migrating workspace ──────────────────────────────"))

    # 1. Create instance directory structure
    print(yellow("  Creating instance directory structure..."))
    for sub in INSTANCE_SUBDIRS:
        (DEFAULT_INSTANCE_DIR / sub).mkdir(parents=True, exist_ok=True)
    print(green(f"  ✓ Created {DEFAULT_INSTANCE_DIR}/ with subdirectories"))

    # 2. Copy 00_config from old workspace
    copy_tree(
        CURRENT_WORKSPACE / "config",
        DEFAULT_INSTANCE_DIR / "config",
        "config",
    )

    # 3. Copy state/ directory into 20_records
    copy_tree(
        CURRENT_WORKSPACE / "state",
        DEFAULT_INSTANCE_DIR / "projects",
        "20_records (from state/)",
    )

    # 4. Copy any logs into 10_logs
    copy_tree(
        CURRENT_WORKSPACE / "logs",
        DEFAULT_INSTANCE_DIR / "state/record",
        "10_logs (from logs/)",
    )

    # 5. Update global_config.json with the default instance entry
    print(yellow("  Updating global_config.json with default instance..."))
    with open(GLOBAL_CONFIG) as f:
        config = json.load(f)

    config.setdefault("instances", {})
    config["instances"]["default"] = {
        "enabled": True,
        "working_dir": str(DEFAULT_INSTANCE_DIR),
        "qq_config": "00_config/qq_config.json",
        "agent_backend": "hermes",
        "interval_minutes": 15,
    }

    with open(GLOBAL_CONFIG, "w") as f:
        json.dump(config, f, indent=2)
    print(green("  ✓ Updated global_config.json"))

    return True


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    print("╔══════════════════════════════════════════════════╗")
    print("║  Partner Multi-Instance Migration Tool           ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    # Step 1: Ensure global config exists
    if not check_or_create_global_config():
        sys.exit(1)

    # Step 2: Check if migration is needed
    if not needs_migration():
        print(green("\n✓ No migration needed. System is already set up."))
        return

    # Step 3: Perform migration
    if not migrate_workspace():
        print(red("\n✗ Migration failed."))
        sys.exit(1)

    # Step 4: Success message
    print()
    print(green("╔══════════════════════════════════════════════════╗"))
    print(green("║  Migration Complete!                             ║"))
    print(green("╚══════════════════════════════════════════════════╝"))
    print()
    print(f"  Global config:      {GLOBAL_CONFIG}")
    print(f"  Default instance:   {DEFAULT_INSTANCE_DIR}")
    print(f"  Old workspace:      {CURRENT_WORKSPACE}")
    print()
    print(yellow("  The old workspace is still in place for safety."))
    print(yellow("  Once verified, you may remove it manually:"))
    print(f"    rm -rf {CURRENT_WORKSPACE}")
    print()


if __name__ == "__main__":
    main()
