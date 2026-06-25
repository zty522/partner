"""
partner-manager — the central management CLI for multi-instance Partner.

Manages multiple independent Partner instances under ~/.partner/instances/.
Each instance has isolated workspace, config, logs, records, and temp directories.

Usage:
    partner-manager create --id age_pred --qq-config /path/to/qq_config.json
    partner-manager start --id age_pred
    partner-manager stop --id age_pred
    partner-manager restart --id age_pred
    partner-manager list
    partner-manager logs --id age_pred --tail 50
    partner-manager enable --id age_pred
    partner-manager disable --id age_pred
    partner-manager start --all
    partner-manager stop --all
    partner-manager status --watch
    partner-manager watchdog [--interval 30] [--max-restarts 3]
"""

import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .instance_root import (
    resolve_global_config_path,
    resolve_instance_workspace,
    resolve_instances_dir,
    resolve_partner_root,
)
from .workspace_layout import ensure_instance_layout

# ── Constants ──────────────────────────────────────────────────────────

PARTNER_DIR = Path(__file__).resolve().parent.parent  # /mnt/e/work/partner
HOME = Path.home()
PARTNER_ROOT = resolve_partner_root()
INSTANCES_DIR = resolve_instances_dir()
GLOBAL_CONFIG_PATH = resolve_global_config_path()

INSTANCE_SUBDIRS = ["state/record", "projects", "99_temp"]
PID_FILENAME = "instance.pid"
LOG_FILENAME = "instance.log"

# Status strings
STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_CRASHED = "crashed"

# ── ANSI colors ────────────────────────────────────────────────────────

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"

# ── Default global config ──────────────────────────────────────────────

DEFAULT_GLOBAL_CONFIG = {
    "default_instance": "",
    "auto_start_on_boot": False,
    "log_level": "INFO",
    "python_cmd": sys.executable,
    "partner_dir": str(PARTNER_DIR),
    "created_at": datetime.now().isoformat(),
}


# ══════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════


def load_global_config() -> dict:
    """Read ~/.partner/global_config.json, create with defaults if missing."""
    PARTNER_ROOT.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_CONFIG_PATH.exists():
        cfg = dict(DEFAULT_GLOBAL_CONFIG)
        cfg["created_at"] = datetime.now().isoformat()
        with open(GLOBAL_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return cfg
    try:
        with open(GLOBAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"{C_RED}Error reading global config: {e}{C_RESET}", file=sys.stderr)
        return dict(DEFAULT_GLOBAL_CONFIG)


def save_global_config(cfg: dict):
    """Write global config to disk."""
    PARTNER_ROOT.mkdir(parents=True, exist_ok=True)
    with open(GLOBAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════
# Instance Path Helpers
# ══════════════════════════════════════════════════════════════════════════


def instance_dir(instance_id: str) -> Path:
    """Return the base directory for an instance."""
    return resolve_instance_workspace(instance_id)


def instance_subdir(instance_id: str, subdir: str) -> Path:
    """Return a specific subdirectory for an instance."""
    return instance_dir(instance_id) / subdir


def pid_path(instance_id: str) -> Path:
    """Return path to the PID file for an instance."""
    return instance_dir(instance_id) / PID_FILENAME


def log_path(instance_id: str) -> Path:
    """Return path to the log file for an instance."""
    return instance_subdir(instance_id, "state/record") / LOG_FILENAME


def qq_config_path(instance_id: str) -> Path:
    """Return path to the QQ config file — unified at workspace_root/config/."""
    return PARTNER_ROOT / "config" / "qq_config.json"


# ══════════════════════════════════════════════════════════════════════════
# Instance Status
# ══════════════════════════════════════════════════════════════════════════


def get_instance_status(instance_id: str) -> str:
    """Return running/stopped/crashed for a single instance."""
    pid_file = pid_path(instance_id)
    if not pid_file.exists():
        live_pid = find_running_instance_pid(instance_id)
        if live_pid is not None:
            try:
                pid_file.write_text(str(live_pid))
            except OSError:
                pass
            return STATUS_RUNNING
        return STATUS_STOPPED
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # Signal 0 = test existence
        return STATUS_RUNNING
    except (ProcessLookupError, OSError):
        live_pid = find_running_instance_pid(instance_id)
        if live_pid is not None:
            try:
                pid_file.write_text(str(live_pid))
            except OSError:
                pass
            return STATUS_RUNNING
        # Process not found; it's crashed (PID file exists but no process)
        return STATUS_CRASHED
    except (ValueError, OSError):
        return STATUS_CRASHED


def is_instance_running(instance_id: str) -> bool:
    """Quick check if a specific instance is running."""
    return get_instance_status(instance_id) == STATUS_RUNNING


def clean_stale_pid(instance_id: str):
    """Remove PID file if the process is gone (stale PID)."""
    status = get_instance_status(instance_id)
    if status == STATUS_CRASHED:
        pid_file = pid_path(instance_id)
        if pid_file.exists():
            pid_file.unlink(missing_ok=True)


def find_running_instance_pid(instance_id: str) -> Optional[int]:
    """Find a live instance PID by matching the workspace in process args."""
    workspace = str(instance_dir(instance_id))
    pattern = re.compile(rf"python3?\s+-m\s+partner\b.*--instance-id\s+{re.escape(instance_id)}\b.*--workspace\s+{re.escape(workspace)}")
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True)
    except Exception:
        return None
    for line in out.splitlines():
        if pattern.search(line):
            try:
                return int(line.strip().split(None, 1)[0])
            except (IndexError, ValueError):
                continue
    return None


def _get_instance_stats(instance_id: str) -> dict:
    """获取实例资源使用统计."""
    stats = {"cpu_percent": "N/A", "memory_mb": "N/A", "status": "unknown"}
    pid_file = pid_path(instance_id)
    if not pid_file.exists():
        return stats
    try:
        pid = int(pid_file.read_text().strip())
        # Use psutil if available
        try:
            import psutil
            proc = psutil.Process(pid)
            stats["cpu_percent"] = proc.cpu_percent(interval=0.1)
            stats["memory_mb"] = proc.memory_info().rss / 1024 / 1024
            stats["status"] = proc.status()
        except ImportError:
            # Fallback: read /proc/[pid]/stat
            try:
                with open(f"/proc/{pid}/stat") as f:
                    parts = f.read().split()
                    # utime + stime in clock ticks
                    utime = int(parts[13])
                    stime = int(parts[14])
                    total_ticks = utime + stime
                    clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
                    # Rough estimate
                    stats["cpu_percent"] = round(total_ticks / clk_tck * 100, 1)
                    stats["status"] = parts[2]  # Process state
            except (OSError, ValueError, KeyError):
                pass
            try:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            parts = line.split()
                            if len(parts) >= 2:
                                stats["memory_mb"] = int(parts[1]) / 1024
                            break
            except (OSError, ValueError):
                pass
        # Also check if process is actually alive
        os.kill(pid, 0)
        if stats["status"] == "unknown":
            stats["status"] = "running"
    except (ProcessLookupError, OSError):
        stats["status"] = "dead"
    except (ValueError, OSError):
        pass
    return stats


def _get_restart_count(instance_id: str) -> int:
    """读取实例重启计数文件."""
    restart_file = instance_dir(instance_id) / "_restart_count"
    if restart_file.exists():
        try:
            data = json.loads(restart_file.read_text())
            count = data.get("count", 0)
            last = data.get("last_reset", 0)
            # Reset if more than 1 hour old
            if time.time() - last > 3600:
                count = 0
            return count
        except (json.JSONDecodeError, OSError):
            pass
    return 0


def _increment_restart_count(instance_id: str) -> int:
    """增加实例重启计数并写入文件."""
    restart_file = instance_dir(instance_id) / "_restart_count"
    data = {"count": 1, "last_reset": time.time()}
    if restart_file.exists():
        try:
            prev = json.loads(restart_file.read_text())
            if time.time() - prev.get("last_reset", 0) > 3600:
                data["count"] = 1
            else:
                data["count"] = prev.get("count", 0) + 1
        except (json.JSONDecodeError, OSError):
            pass
    data["last_reset"] = time.time()
    try:
        restart_file.write_text(json.dumps(data))
    except OSError:
        pass
    return data["count"]


def _reset_restart_count(instance_id: str):
    """重置实例重启计数."""
    restart_file = instance_dir(instance_id) / "_restart_count"
    try:
        restart_file.write_text(json.dumps({"count": 0, "last_reset": time.time()}))
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════
# Create Instance
# ══════════════════════════════════════════════════════════════════════════


def instance_exists(instance_id: str) -> bool:
    """Check if an instance directory exists."""
    return instance_dir(instance_id).exists()


def create_instance(instance_id: str, qq_config_src: Optional[str] = None) -> bool:
    """Create a new instance with directory structure and optional QQ config.

    If qq_config_src is not provided, interactively prompt for credentials.

    Args:
        instance_id: Unique identifier for this instance.
        qq_config_src: Path to source qq_config.json to copy in (optional).

    Returns:
        True on success, False on failure.
    """
    inst = instance_dir(instance_id)
    if inst.exists():
        print(f"{C_YELLOW}Instance '{instance_id}' already exists.{C_RESET}")
        return False

    # Create directory structure
    print(f"Creating instance '{instance_id}'...")
    inst.mkdir(parents=True, exist_ok=True)
    for sub in INSTANCE_SUBDIRS:
        (inst / sub).mkdir(parents=True, exist_ok=True)
        print(f"  Created  {sub}/")
    ensure_instance_layout(str(inst))

    # QQ config: copy or prompt
    if qq_config_src:
        src = Path(qq_config_src)
        if not src.exists():
            print(f"{C_RED}QQ config not found: {src}{C_RESET}", file=sys.stderr)
            print("Instance structure created but QQ config was not copied.")
            return False
        dst = qq_config_path(instance_id)
        shutil.copy2(str(src), str(dst))
        print(f"  Copied   qq_config.json from {src}")
    else:
        # Interactive prompt
        print(f"  {C_YELLOW}No --qq-config provided. Let's configure the QQ bot.{C_RESET}")
        print()
        try:
            app_id = input("  AppID (from QQ开放平台): ").strip()
            if not app_id:
                print(f"  {C_RED}AppID is required.{C_RESET}")
                return False
            app_secret = input("  AppSecret: ").strip()
            if not app_secret:
                print(f"  {C_RED}AppSecret is required.{C_RESET}")
                return False
            sandbox_input = input("  Use sandbox? (Y/n): ").strip().lower()
            is_sandbox = sandbox_input not in ("n", "no", "false", "0")
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {C_YELLOW}Cancelled.{C_RESET}")
            return False

        qq_config = {
            "app_id": app_id,
            "app_secret": app_secret,
            "is_sandbox": is_sandbox,
        }
        dst = qq_config_path(instance_id)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(qq_config, f, indent=2, ensure_ascii=False)
        print(f"  Written  qq_config.json")
        print(f"    AppID:    {app_id}")
        print(f"    Sandbox:  {is_sandbox}")

    print()
    print(f"{C_GREEN}Instance '{instance_id}' created.{C_RESET}")
    show_create_tip(instance_id)
    return True


def show_create_tip(instance_id: str):
    """Print next-step tips after creating an instance."""
    print()
    print(f"{C_BOLD}Next steps:{C_RESET}")
    print(f"  {C_CYAN}> partner-manager start --id {instance_id}{C_RESET}")
    print(f"  {C_DIM}  Starts the QQ bot and Mind Pool {C_RESET}")
    print()
    print(f"  {C_CYAN}> partner-manager list{C_RESET}")
    print(f"  {C_DIM}  Show all instances{C_RESET}")
    print()
    print(f"  {C_CYAN}> partner-manager enable --id {instance_id}{C_RESET}")
    print(f"  {C_DIM}  Auto-start on boot (systemd){C_RESET}")
    print()
    print(f"  {C_CYAN}> partner-manager logs --id {instance_id} --tail 50{C_RESET}")
    print(f"  {C_DIM}  View runtime logs{C_RESET}")


# ══════════════════════════════════════════════════════════════════════════
# Start / Stop / Restart
# ══════════════════════════════════════════════════════════════════════════


def build_start_command(instance_id: str) -> List[str]:
    """Build the subprocess command list to start an instance.

    Uses: python3 -m partner --instance-id {id} --workspace ~/.partner/instances/{id}
    """
    cfg = load_global_config()
    python = cfg.get("python_cmd", sys.executable)
    workspace = str(instance_dir(instance_id))

    cmd = [
        python,
        "-m",
        "partner",
        "--instance-id",
        instance_id,
        "--workspace",
        workspace,
    ]
    return cmd


def start_instance(instance_id: str) -> bool:
    """Start an instance as a background subprocess.

    Args:
        instance_id: The instance to start.

    Returns:
        True if started successfully, False otherwise.
    """
    if not instance_exists(instance_id):
        print(f"{C_RED}Instance '{instance_id}' not found.{C_RESET}", file=sys.stderr)
        print(f"Create it first: partner-manager create --id {instance_id}")
        return False

    # Check if already running
    status = get_instance_status(instance_id)
    if status == STATUS_RUNNING:
        pid_file = pid_path(instance_id)
        pid = int(pid_file.read_text().strip()) if pid_file.exists() else "?"
        print(f"{C_YELLOW}Instance '{instance_id}' is already running (PID {pid}).{C_RESET}")
        return True
    elif status == STATUS_CRASHED:
        # Clean up stale PID
        clean_stale_pid(instance_id)
        print(f"  Cleaned stale PID for '{instance_id}'.")

    # Ensure log directory exists
    log_dir = instance_subdir(instance_id, "state/record")
    log_dir.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = build_start_command(instance_id)
    log_file = log_path(instance_id)

    print(f"Starting instance '{instance_id}'...")
    print(f"  Command: {' '.join(cmd)}")
    print(f"  Log:     {log_file}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=open(log_file, "a"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        # Write PID
        pid_file = pid_path(instance_id)
        pid_file.write_text(str(proc.pid))

        # Brief wait to see if process dies immediately
        time.sleep(1.5)
        if proc.poll() is not None:
            # Process exited already — likely a config error
            exit_code = proc.returncode
            live_pid = find_running_instance_pid(instance_id)
            if exit_code == 0 and live_pid is not None:
                pid_file.write_text(str(live_pid))
                print(f"{C_GREEN}Instance '{instance_id}' started (PID {live_pid}).{C_RESET}")
                print(f"  Logs: partner-manager logs --id {instance_id} --tail 50")
                print(f"  Stop: partner-manager stop --id {instance_id}")
                return True
            # Read last few log lines for diagnostic
            log_lines = []
            try:
                with open(log_file) as f:
                    log_lines = f.readlines()[-10:]
            except OSError:
                pass
            print(f"{C_RED}Instance '{instance_id}' exited immediately (code {exit_code}).{C_RESET}")
            if log_lines:
                print(f"{C_DIM}  Last log lines:{C_RESET}")
                for line in log_lines:
                    print(f"    {line.rstrip()}")
            clean_stale_pid(instance_id)
            return False

        print(f"{C_GREEN}Instance '{instance_id}' started (PID {proc.pid}).{C_RESET}")
        print(f"  Logs: partner-manager logs --id {instance_id} --tail 50")
        print(f"  Stop: partner-manager stop --id {instance_id}")
        return True

    except FileNotFoundError as e:
        print(f"{C_RED}Failed to start: executable not found: {e}{C_RESET}", file=sys.stderr)
        return False
    except OSError as e:
        print(f"{C_RED}Failed to start: {e}{C_RESET}", file=sys.stderr)
        return False


def stop_instance(instance_id: str) -> bool:
    """Stop a running instance by sending SIGTERM to its PID.

    Args:
        instance_id: The instance to stop.

    Returns:
        True if stopped successfully, False otherwise.
    """
    if not instance_exists(instance_id):
        print(f"{C_RED}Instance '{instance_id}' not found.{C_RESET}", file=sys.stderr)
        return False

    status = get_instance_status(instance_id)
    if status == STATUS_STOPPED:
        print(f"Instance '{instance_id}' is not running.")
        # Clean up any stale PID file just in case
        clean_stale_pid(instance_id)
        return True
    elif status == STATUS_CRASHED:
        clean_stale_pid(instance_id)
        print(f"Instance '{instance_id}' had crashed. PID cleaned up.")
        return True

    # Status is running — kill it
    pid_file = pid_path(instance_id)
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid = find_running_instance_pid(instance_id)
        if pid is None:
            print(f"{C_RED}Invalid PID file for '{instance_id}' and no live process found.{C_RESET}", file=sys.stderr)
            return False

    print(f"Stopping instance '{instance_id}' (PID {pid})...")

    def _signal_instance(sig: int):
        if os.name == "nt":
            os.kill(pid, sig)
            return
        try:
            os.killpg(os.getpgid(pid), sig)
        except ProcessLookupError:
            raise
        except Exception:
            os.kill(pid, sig)

    try:
        # Send SIGTERM (15) to the whole process group first. Instances are
        # started with start_new_session=True, so this also stops active agent
        # subprocesses such as `hermes chat` instead of leaving stale writers.
        _signal_instance(signal.SIGTERM)

        # Wait for graceful shutdown (up to 5 seconds)
        for _ in range(25):
            try:
                os.kill(pid, 0)  # Check if alive
                time.sleep(0.2)
            except ProcessLookupError:
                break
        else:
            # Process still alive after timeout — use SIGKILL
            try:
                _signal_instance(signal.SIGKILL)
                print(f"  {C_YELLOW}Force killed (SIGKILL) after timeout.{C_RESET}")
            except ProcessLookupError:
                pass

    except ProcessLookupError:
        # Process already gone
        pass
    except PermissionError as e:
        print(f"{C_RED}Cannot stop '{instance_id}': {e}{C_RESET}", file=sys.stderr)
        return False
    except OSError as e:
        print(f"{C_RED}Error stopping '{instance_id}': {e}{C_RESET}", file=sys.stderr)
        return False

    # Clean up PID file
    pid_file.unlink(missing_ok=True)
    print(f"{C_GREEN}Instance '{instance_id}' stopped.{C_RESET}")
    return True


def restart_instance(instance_id: str) -> bool:
    """Restart an instance (stop then start).

    Args:
        instance_id: The instance to restart.

    Returns:
        True if restart succeeded, False otherwise.
    """
    print(f"Restarting instance '{instance_id}'...")
    stop_instance(instance_id)
    # Brief pause to let resources release
    time.sleep(1)
    return start_instance(instance_id)


# ══════════════════════════════════════════════════════════════════════════
# List / Status
# ══════════════════════════════════════════════════════════════════════════


def list_instances() -> Dict[str, str]:
    """Return {instance_id: status_string} for all known instances."""
    results: Dict[str, str] = {}
    cfg = load_global_config()
    configured = sorted((cfg.get("instances") or {}).keys())
    for inst_id in configured:
        results[inst_id] = get_instance_status(inst_id)

    if not results and INSTANCES_DIR.exists():
        for item in sorted(INSTANCES_DIR.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                results[item.name] = get_instance_status(item.name)

    return results


def print_instance_list():
    """Print a formatted table of all instances."""
    instances = list_instances()
    if not instances:
        print("No instances found.")
        print(f"  Create one: partner-manager create --id <name> --qq-config <path>")
        return

    # Header
    print(f"{C_BOLD}{'Instance ID':<24} {'Status':<12} {'PID':<10} {'Details'}{C_RESET}")
    print(f"{C_DIM}{'─' * 70}{C_RESET}")

    for inst_id, status in instances.items():
        pid_str = ""
        details = ""
        if status == STATUS_RUNNING:
            try:
                pid_file = pid_path(inst_id)
                pid_str = pid_file.read_text().strip()
            except (OSError, ValueError):
                pid_str = "?"
            details = f"{C_DIM}log: {log_path(inst_id)}{C_RESET}"

        if status == STATUS_RUNNING:
            status_str = f"{C_GREEN}{status}{C_RESET}"
        elif status == STATUS_CRASHED:
            status_str = f"{C_RED}{status}{C_RESET}"
        else:
            status_str = f"{C_DIM}{status}{C_RESET}"

        print(f"  {inst_id:<22} {status_str:<12} {pid_str:<10} {details}")


def get_instance_logs(instance_id: str, tail: int = 50) -> List[str]:
    """Return the last N lines from an instance's log file.

    Args:
        instance_id: The instance to read logs from.
        tail: Number of lines to return (default 50).

    Returns:
        List of log lines (may be empty if log file doesn't exist).
    """
    log_file = log_path(instance_id)
    if not log_file.exists():
        return [f"No log file found at {log_file}"]

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        # Return last N lines
        return lines[-tail:]
    except OSError as e:
        return [f"Error reading log: {e}"]


def print_instance_logs(instance_id: str, tail: int = 50):
    """Print the last N log lines for an instance."""
    if not instance_exists(instance_id):
        print(f"{C_RED}Instance '{instance_id}' not found.{C_RESET}", file=sys.stderr)
        return

    log_file = log_path(instance_id)
    if not log_file.exists():
        print(f"No log file yet for '{instance_id}'.")
        print(f"  Expected at: {log_file}")
        return

    lines = get_instance_logs(instance_id, tail)
    status = get_instance_status(instance_id)
    status_label = {
        STATUS_RUNNING: f"{C_GREEN}{status}{C_RESET}",
        STATUS_STOPPED: f"{C_DIM}{status}{C_RESET}",
        STATUS_CRASHED: f"{C_RED}{status}{C_RESET}",
    }.get(status, status)

    print(f"Logs for instance '{instance_id}' [{status_label}] (last {len(lines)} lines):")
    print(f"  File: {log_file}")
    print(f"{C_DIM}─{'─' * 68}{C_RESET}")
    for line in lines:
        print(line.rstrip())


# ══════════════════════════════════════════════════════════════════════════
# Start / Stop All
# ══════════════════════════════════════════════════════════════════════════


def start_all():
    """Start all instances that are not already running."""
    instances = list_instances()
    if not instances:
        print("No instances found to start.")
        return

    results = {"started": 0, "skipped": 0, "failed": 0}
    for inst_id, status in instances.items():
        if status == STATUS_RUNNING:
            print(f"  {C_YELLOW}⏭  {inst_id}: already running{C_RESET}")
            results["skipped"] += 1
            continue
        print(f"  {inst_id}: starting...")
        if start_instance(inst_id):
            results["started"] += 1
        else:
            results["failed"] += 1

    print()
    print(
        f"Start all complete: "
        f"{C_GREEN}{results['started']} started{C_RESET}, "
        f"{results['skipped']} skipped, "
        f"{C_RED}{results['failed']} failed{C_RESET}"
    )


def stop_all():
    """Stop all running instances."""
    instances = list_instances()
    if not instances:
        print("No instances found.")
        return

    running = [iid for iid, s in instances.items() if s == STATUS_RUNNING]
    if not running:
        print("No instances are currently running.")
        return

    results = {"stopped": 0, "failed": 0}
    for inst_id in running:
        print(f"  {inst_id}: stopping...")
        if stop_instance(inst_id):
            results["stopped"] += 1
        else:
            results["failed"] += 1

    print()
    print(
        f"Stop all complete: "
        f"{C_GREEN}{results['stopped']} stopped{C_RESET}, "
        f"{C_RED}{results['failed']} failed{C_RESET}"
    )


# ══════════════════════════════════════════════════════════════════════════
# Watchdog Daemon (auto-restart crashed instances)
# ══════════════════════════════════════════════════════════════════════════


def watchdog_daemon(interval: float = 30.0, max_restarts_per_hour: int = 3):
    """Watchdog daemon: periodically check all running instances and auto-restart crashed ones.

    Tracks restart count per instance via {workspace}/_restart_count file.
    Notifies via admin_notify.log when max restarts exceeded.

    Args:
        interval: Check interval in seconds (default 30).
        max_restarts_per_hour: Max restarts per hour before giving up (default 3).
    """
    if not INSTANCES_DIR.exists():
        print(f"{C_RED}No instances directory found.{C_RESET}")
        print(f"  Expected: {INSTANCES_DIR}")
        return

    log_dir = PARTNER_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    notify_file = log_dir / "watchdog_notify.log"

    def _log_notify(msg: str):
        ts = datetime.now().isoformat()
        try:
            with open(notify_file, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except OSError:
            pass
        print(f"[{ts}] {msg}")

    _log_notify(f"🔍 Watchdog started (interval={interval}s, max_restarts={max_restarts_per_hour}/h)")

    try:
        while True:
            instances = list_instances()
            for inst_id, status in instances.items():
                if status == STATUS_CRASHED:
                    # Check restart count
                    rc = _get_restart_count(inst_id)
                    if rc >= max_restarts_per_hour:
                        _log_notify(
                            f"{C_RED}⚠️  {inst_id}: crashed but restart limit "
                            f"({max_restarts_per_hour}/h) reached, not restarting{C_RESET}"
                        )
                        continue

                    # Clean stale PID
                    clean_stale_pid(inst_id)
                    # Start the instance
                    _log_notify(f"{C_YELLOW}♻️  {inst_id}: crashed, restarting (attempt {rc+1}){C_RESET}")
                    success = start_instance(inst_id)
                    if success:
                        _increment_restart_count(inst_id)
                        _log_notify(f"{C_GREEN}✅ {inst_id}: restarted successfully{C_RESET}")
                    else:
                        _log_notify(f"{C_RED}❌ {inst_id}: restart failed{C_RESET}")

            time.sleep(interval)

    except KeyboardInterrupt:
        _log_notify("🛑 Watchdog stopped by user")
        print()
        print("Watchdog stopped.")


# ══════════════════════════════════════════════════════════════════════════
# Idle Detection
# ══════════════════════════════════════════════════════════════════════════


def _get_idle_since(instance_id: str) -> Optional[float]:
    """读 idle_heartbeat.txt，返回空闲开始时间戳（秒）。

    检查多个可能的位置：
    1. {workspace}/projects/idle_heartbeat.txt
    2. {workspace}/state/record/idle_heartbeat.txt

    Returns:
        float 时间戳，或 None（无 idle 标记）。
    """
    hb_paths = [
        instance_dir(instance_id) / "projects" / "idle_heartbeat.txt",
        instance_dir(instance_id) / "idle_heartbeat.txt",
        instance_dir(instance_id) / "state/record" / "idle_heartbeat.txt",
    ]
    for hb in hb_paths:
        if hb.exists():
            try:
                content = hb.read_text().strip()
                # Try ISO format: "idle_since=1234567890" or ISO datetime
                if "idle_since=" in content:
                    ts_str = content.split("idle_since=")[1].strip()
                    return float(ts_str)
                # Try plain ISO datetime
                from datetime import datetime as dt
                return dt.fromisoformat(content[:19]).timestamp()
            except (ValueError, OSError, IndexError):
                pass
    return None


def _check_idle_timeout(instance_id: str, max_idle_minutes: int = 60) -> bool:
    """检查实例是否空闲超时。

    Args:
        instance_id: 实例 ID。
        max_idle_minutes: 最大空闲分钟数（默认 60）。

    Returns:
        True 如果空闲超时。
    """
    idle_since = _get_idle_since(instance_id)
    if idle_since is None:
        return False
    idle_duration = time.time() - idle_since
    idle_minutes = idle_duration / 60
    return idle_minutes >= max_idle_minutes


def _write_idle_marker(instance_id: str):
    """写入空闲标记，保持进程存活标记。"""
    paths = [
        instance_dir(instance_id) / "projects" / "idle_heartbeat.txt",
        instance_dir(instance_id) / "idle_heartbeat.txt",
    ]
    content = f"idle_since={time.time():.0f}"
    for p in paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        except OSError:
            pass


def _clear_idle_marker(instance_id: str):
    """清除空闲标记。"""
    paths = [
        instance_dir(instance_id) / "projects" / "idle_heartbeat.txt",
        instance_dir(instance_id) / "idle_heartbeat.txt",
    ]
    for p in paths:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════════
# Status Watch (interactive monitoring) with resource stats
# ══════════════════════════════════════════════════════════════════════════

def status_watch(interval: float = 5.0):
    """Interactive monitoring mode. Refreshes instance status every N seconds.

    Shows CPU%, Memory, and last active time for each instance.
    Press Ctrl+C to exit.
    """
    if not INSTANCES_DIR.exists():
        print("No instances directory found. Nothing to watch.")
        print(f"  Expected: {INSTANCES_DIR}")
        return

    print(f"{C_BOLD}Partner Instance Monitor (with resource stats){C_RESET}")
    print(f"{C_DIM}Refreshing every {interval}s. Press Ctrl+C to exit.{C_RESET}")
    print()

    try:
        while True:
            instances = list_instances()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Clear screen using ANSI
            sys.stdout.write("\033[H\033[J")
            sys.stdout.flush()

            print(f"{C_BOLD}Partner Instance Monitor{C_RESET} — {C_DIM}{timestamp}{C_RESET}")
            print(f"{C_DIM}─{'─' * 80}{C_RESET}")

            if not instances:
                print("  No instances found.")
            else:
                print(
                    f"{C_BOLD}{'Instance ID':<20} {'Status':<10} {'CPU%':>6} "
                    f"{'Mem(MB)':>8} {'Uptime':<10} {'Last Active'}{C_RESET}"
                )
                print(f"{C_DIM}─{'─' * 80}{C_RESET}")
                for inst_id, status in instances.items():
                    pid_str = ""
                    uptime = ""
                    last_active = ""

                    # Get resource stats
                    stats = _get_instance_stats(inst_id)
                    cpu_str = str(stats.get("cpu_percent", "N/A"))
                    if isinstance(stats.get("cpu_percent"), (int, float)):
                        cpu_str = f"{stats['cpu_percent']:>5.1f}"
                    mem_str = str(stats.get("memory_mb", "N/A"))
                    if isinstance(stats.get("memory_mb"), (int, float)):
                        mem_str = f"{stats['memory_mb']:>7.0f}"

                    # Read idle heartbeat
                    if status == STATUS_RUNNING:
                        hb_paths = [
                            instance_dir(inst_id) / "projects" / "idle_heartbeat.txt",
                            instance_dir(inst_id) / "idle_heartbeat.txt",
                            instance_dir(inst_id) / "state/record" / "idle_heartbeat.txt",
                        ]
                        for hb in hb_paths:
                            if hb.exists():
                                try:
                                    last_active = hb.read_text().strip()[:19]
                                except OSError:
                                    pass
                                break

                    if status == STATUS_RUNNING:
                        try:
                            pid_file = pid_path(inst_id)
                            pid_str = pid_file.read_text().strip()
                            pid = int(pid_str)
                            mtime = pid_file.stat().st_mtime
                            elapsed = time.time() - mtime
                            if elapsed < 120:
                                uptime = f"{elapsed:.0f}s"
                            elif elapsed < 7200:
                                uptime = f"{elapsed / 60:.0f}m"
                            else:
                                uptime = f"{elapsed / 3600:.1f}h"
                        except (OSError, ValueError):
                            pid_str = "?"

                    if status == STATUS_RUNNING:
                        status_str = f"{C_GREEN}{status}{C_RESET}"
                    elif status == STATUS_CRASHED:
                        status_str = f"{C_RED}{status}{C_RESET}"
                    else:
                        status_str = f"{C_DIM}{status}{C_RESET}"

                    print(
                        f"  {inst_id:<18} {status_str:<10} {cpu_str:>6} "
                        f"{mem_str:>8} {uptime:<10} {last_active}"
                    )

            print()
            running_count = sum(1 for s in instances.values() if s == STATUS_RUNNING)
            stopped_count = sum(1 for s in instances.values() if s == STATUS_STOPPED)
            crashed_count = sum(1 for s in instances.values() if s == STATUS_CRASHED)
            print(
                f"  Total: {len(instances)} | "
                f"{C_GREEN}Running: {running_count}{C_RESET} | "
                f"Stopped: {stopped_count} | "
                f"{C_RED}Crashed: {crashed_count}{C_RESET}"
            )

            time.sleep(interval)

    except KeyboardInterrupt:
        print()
        print("Monitor stopped.")


# ══════════════════════════════════════════════════════════════════════════
# Systemd Boot Enable/Disable
# ══════════════════════════════════════════════════════════════════════════


def _systemd_user_path() -> Optional[Path]:
    """Return the systemd user directory path, or None if unsupported."""
    if os.name == "nt":
        return None  # Windows / WSL may not have systemd
    # WSL2 does not run systemd by default
    # But we still generate service files for real Linux hosts
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "systemd" / "user"
    return Path.home() / ".config" / "systemd" / "user"


def generate_service_content(instance_id: str) -> str:
    """Generate a systemd user service unit for the given instance.

    The service runs the instance manager's start command as a foreground
    process so systemd can track its lifecycle.
    """
    python_cmd = sys.executable
    partner_dir = str(PARTNER_DIR)
    instance_workspace = str(instance_dir(instance_id))
    log_file = str(log_path(instance_id))

    return f"""[Unit]
Description=Partner Instance — {instance_id}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={partner_dir}
ExecStartPre=/bin/sleep 3
ExecStart={python_cmd} -m partner --instance-id {instance_id} --workspace {instance_workspace}
Restart=on-failure
RestartSec=10
StandardOutput=append:{log_file}
StandardError=append:{log_file}
Environment=PARTNER_INSTANCE_ID={instance_id}
Environment=PARTNER_WORKSPACE={instance_workspace}

[Install]
WantedBy=default.target
"""


def enable_on_boot(instance_id: str) -> bool:
    """Create a systemd user service for auto-start on boot.

    Args:
        instance_id: Instance to enable.

    Returns:
        True on success.
    """
    if not instance_exists(instance_id):
        print(f"{C_RED}Instance '{instance_id}' not found.{C_RESET}", file=sys.stderr)
        return False

    systemd_dir = _systemd_user_path()
    if systemd_dir is None:
        print(f"{C_YELLOW}Systemd is not available on this platform.{C_RESET}")
        print(f"  To enable auto-start, set up a cron @reboot or shell profile script.")
        return False

    service_name = f"partner-{instance_id}"
    service_path = systemd_dir / f"{service_name}.service"

    systemd_dir.mkdir(parents=True, exist_ok=True)
    content = generate_service_content(instance_id)
    service_path.write_text(content)

    print(f"Created systemd user service:")
    print(f"  {service_path}")
    print()

    # Try to enable and start
    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", f"{service_name}.service"],
            capture_output=True, timeout=10,
        )
        print(f"{C_GREEN}Service '{service_name}' enabled for auto-start on boot.{C_RESET}")
        print(f"  Manage: systemctl --user {{start|stop|status}} {service_name}.service")
        return True

    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"{C_YELLOW}Service file created but systemctl commands failed:{C_RESET}")
        print(f"  {e}")
        print(f"  To enable manually: systemctl --user enable {service_name}.service")
        return True


def disable_on_boot(instance_id: str) -> bool:
    """Remove the systemd user service for an instance.

    Args:
        instance_id: Instance to disable.

    Returns:
        True on success.
    """
    systemd_dir = _systemd_user_path()
    if systemd_dir is None:
        print(f"{C_YELLOW}Systemd is not available on this platform.{C_RESET}")
        return False

    service_name = f"partner-{instance_id}"
    service_path = systemd_dir / f"{service_name}.service"

    if not service_path.exists():
        print(f"No systemd service found for '{instance_id}'.")
        print(f"  Looked for: {service_path}")
        return True

    try:
        subprocess.run(
            ["systemctl", "--user", "disable", f"{service_name}.service"],
            capture_output=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    service_path.unlink(missing_ok=True)
    print(f"{C_GREEN}Service '{service_name}' disabled.{C_RESET}")
    return True


# ══════════════════════════════════════════════════════════════════════════
# Migration from single-instance layout
# ══════════════════════════════════════════════════════════════════════════


def migrate_existing(workspace: Optional[str] = None) -> bool:
    """Migrate a current single-instance workspace to instances/default/.

    Scans the legacy workspace (usually from partner_config.json or
    current working directory) and copies its structure into the
    multi-instance layout under ~/.partner/instances/default/.

    Args:
        workspace: Path to the existing single instance workspace.
                   If None, tries to auto-detect.

    Returns:
        True on success.
    """
    print(f"{C_BOLD}Migrating existing workspace to multi-instance layout...{C_RESET}")

    # Auto-detect workspace if not provided
    if workspace is None:
        # Try common locations
        candidates = [
            Path("/mnt/e/work/partner_workspace"),
            PARTNER_DIR / "workspace",
            Path.home() / "partner_workspace",
        ]
        # Also check if there's a partner config file
        for cfg_path in [
            Path.home() / ".partner" / "workspace_config.json",
            PARTNER_DIR / "config.json",
        ]:
            if cfg_path.exists():
                try:
                    with open(cfg_path) as f:
                        cfg = json.load(f)
                    ws = cfg.get("workspace", "")
                    if ws:
                        candidates.insert(0, Path(ws))
                except (json.JSONDecodeError, OSError):
                    pass

        existing = [c for c in candidates if c.exists()]
        if existing:
            workspace = str(existing[0])
        else:
            # Prompt user
            print(f"{C_YELLOW}No existing workspace detected.{C_RESET}")
            print("Please provide the workspace path:")
            print("  partner-manager migrate --workspace /path/to/workspace")
            return False

    src = Path(workspace)
    if not src.exists():
        print(f"{C_RED}Workspace not found: {src}{C_RESET}", file=sys.stderr)
        return False

    print(f"  Source: {src}")

    target_id = "default"
    dst = instance_dir(target_id)

    if dst.exists():
        print(f"{C_YELLOW}Instance 'default' already exists.{C_RESET}")
        yn = input("  Overwrite? This will NOT delete the source. [y/N] ").strip().lower()
        if yn != "y":
            print("  Migration cancelled.")
            return False

    # Create directory structure
    dst.mkdir(parents=True, exist_ok=True)
    for sub in INSTANCE_SUBDIRS:
        (dst / sub).mkdir(parents=True, exist_ok=True)
    ensure_instance_layout(str(dst))

    # Copy config files
    config_src = src / "config"
    if config_src.exists() and config_src.is_dir():
        shutil.copytree(str(config_src), str(dst / "config"), dirs_exist_ok=True)
        print(f"  Copied  00_config/")

    # Copy logs
    logs_src = src / "logs"
    if logs_src.exists() and logs_src.is_dir():
        shutil.copytree(str(logs_src), str(dst / "state/record"), dirs_exist_ok=True)
        print(f"  Copied  logs/ → 10_logs/")

    # Copy records / state
    state_src = src / "state"
    if state_src.exists() and state_src.is_dir():
        shutil.copytree(str(state_src), str(dst / "projects"), dirs_exist_ok=True)
        print(f"  Copied  state/ → 20_records/")

    # Also check for qq_config.json at workspace root
    qq_cfg_src = src / "qq_config.json"
    if qq_cfg_src.exists():
        shutil.copy2(str(qq_cfg_src), str(dst / "config" / "qq_config.json"))
        print(f"  Copied  qq_config.json")

    print(f"{C_GREEN}Migration complete.{C_RESET}")
    print(f"  Target: {dst}")
    print(f"  Source files remain untouched at: {src}")
    print()
    print("You can now manage this instance:")
    print(f"  partner-manager start --id {target_id}")
    print(f"  partner-manager list")
    return True


# ══════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ══════════════════════════════════════════════════════════════════════════


def main():
    """Partner Manager CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="partner-manager",
        description="Manage multiple Partner instances.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  partner-manager create
    → prompts for Instance ID, AppID, AppSecret interactively

  partner-manager create --id age_pred --qq-config ./qq_config.json

  partner-manager start --id age_pred
  partner-manager stop --id age_pred
  partner-manager restart --id age_pred
  partner-manager list
  partner-manager logs --id age_pred --tail 50
  partner-manager enable --id age_pred
  partner-manager disable --id age_pred
  partner-manager start --all
  partner-manager stop --all
  partner-manager status --watch
  partner-manager watchdog --interval 30 --max-restarts 3
  partner-manager migrate --workspace /path/to/legacy_workspace

""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # ── create ──
    p_create = subparsers.add_parser("create", help="Create a new instance")
    p_create.add_argument("--id", dest="instance_id", default=None,
                          help="Instance identifier (optional — prompts interactively if omitted)")
    p_create.add_argument("--qq-config", dest="qq_config_src", default=None,
                          help="Path to qq_config.json (optional — prompts interactively if omitted)")

    # ── start ──
    p_start = subparsers.add_parser("start", help="Start one or all instances")
    p_start.add_argument("--id", dest="instance_id", default=None,
                         help="Instance ID to start")
    p_start.add_argument("--all", dest="start_all", action="store_true",
                         help="Start all instances")

    # ── stop ──
    p_stop = subparsers.add_parser("stop", help="Stop one or all instances")
    p_stop.add_argument("--id", dest="instance_id", default=None,
                        help="Instance ID to stop")
    p_stop.add_argument("--all", dest="stop_all", action="store_true",
                        help="Stop all instances")

    # ── restart ──
    p_restart = subparsers.add_parser("restart", help="Restart an instance")
    p_restart.add_argument("--id", required=True, dest="instance_id",
                           help="Instance ID to restart")

    # ── list ──
    subparsers.add_parser("list", help="List all instances and their status")

    # ── logs ──
    p_logs = subparsers.add_parser("logs", help="View instance logs")
    p_logs.add_argument("--id", required=True, dest="instance_id",
                        help="Instance ID to show logs for")
    p_logs.add_argument("--tail", type=int, default=50,
                        help="Number of lines from the end (default: 50)")

    # ── status ──
    p_status = subparsers.add_parser("status", help="Show instance status")
    p_status.add_argument("--id", dest="instance_id", default=None,
                          help="Instance ID (omit to show all)")
    p_status.add_argument("--watch", action="store_true",
                          help="Continuous monitoring mode")

    # ── watchdog ──
    p_watchdog = subparsers.add_parser("watchdog",
                                        help="Run watchdog daemon (auto-restart crashed instances)")
    p_watchdog.add_argument("--interval", type=float, default=30.0,
                            help="Check interval in seconds (default: 30)")
    p_watchdog.add_argument("--max-restarts", type=int, default=3,
                            dest="max_restarts",
                            help="Max restarts per hour (default: 3)")

    # ── enable / disable (systemd boot) ──
    p_enable = subparsers.add_parser("enable", help="Enable auto-start on boot (systemd)")
    p_enable.add_argument("--id", required=True, dest="instance_id",
                          help="Instance ID to enable")

    p_disable = subparsers.add_parser("disable", help="Disable auto-start on boot (systemd)")
    p_disable.add_argument("--id", required=True, dest="instance_id",
                           help="Instance ID to disable")

    # ── migrate ──
    p_migrate = subparsers.add_parser("migrate", help="Migrate single workspace to multi-instance layout")
    p_migrate.add_argument("--workspace", "-w", default=None,
                           help="Path to existing single-instance workspace")

    args = parser.parse_args()

    # ── Dispatch ──
    if args.command == "create":
        instance_id = args.instance_id
        if not instance_id:
            try:
                instance_id = input("  Instance ID: ").strip()
                if not instance_id:
                    print(f"{C_RED}Instance ID is required.{C_RESET}")
                    sys.exit(1)
            except (EOFError, KeyboardInterrupt):
                print(f"\n{C_YELLOW}Cancelled.{C_RESET}")
                sys.exit(1)
        create_instance(instance_id, args.qq_config_src)

    elif args.command == "start":
        if args.start_all:
            start_all()
        elif args.instance_id:
            start_instance(args.instance_id)
        else:
            parser.error("Use --id <name> or --all")
        print()
        print(f"{C_BOLD}Tip:{C_RESET} {C_CYAN}partner-manager list{C_RESET} to see all instances")

    elif args.command == "stop":
        if args.stop_all:
            stop_all()
        elif args.instance_id:
            stop_instance(args.instance_id)
        else:
            parser.error("Use --id <name> or --all")

    elif args.command == "restart":
        restart_instance(args.instance_id)
        print()
        print(f"{C_BOLD}Tip:{C_RESET} {C_CYAN}partner-manager logs --id {args.instance_id} --tail 10{C_RESET} to check startup")

    elif args.command == "list":
        print_instance_list()
        print()
        print(f"{C_BOLD}Tip:{C_RESET} {C_CYAN}partner-manager start --id <name>{C_RESET} to start an instance")

    elif args.command == "logs":
        print_instance_logs(args.instance_id, tail=args.tail)

    elif args.command == "status":
        if args.watch:
            status_watch()
        elif args.instance_id:
            status = get_instance_status(args.instance_id)
            label = {
                STATUS_RUNNING: f"{C_GREEN}{status}{C_RESET}",
                STATUS_STOPPED: f"{C_DIM}{status}{C_RESET}",
                STATUS_CRASHED: f"{C_RED}{status}{C_RESET}",
            }.get(status, status)
            print(f"Instance '{args.instance_id}': {label}")
            if status == STATUS_RUNNING:
                pid_file = pid_path(args.instance_id)
                if pid_file.exists():
                    print(f"  PID: {pid_file.read_text().strip()}")
                print(f"  Log: {log_path(args.instance_id)}")
            elif status == STATUS_CRASHED:
                print(f"  {C_YELLOW}Stale PID file found but process is gone.{C_RESET}")
                print(f"  Run: partner-manager start --id {args.instance_id}")
            print()
            print(f"{C_BOLD}Tip:{C_RESET} {C_CYAN}partner-manager list{C_RESET} for all instances")
        else:
            print_instance_list()
            print()
            print(f"{C_BOLD}Tip:{C_RESET} {C_CYAN}partner-manager logs --id <name> --tail 50{C_RESET} to see logs")

    elif args.command == "watchdog":
        print(f"{C_BOLD}🐾 Watchdog Daemon{C_RESET}")
        print(f"  Interval: {args.interval}s, Max restarts/h: {args.max_restarts}")
        print(f"{C_DIM}Press Ctrl+C to stop.{C_RESET}")
        print()
        watchdog_daemon(interval=args.interval, max_restarts_per_hour=args.max_restarts)

    elif args.command == "enable":
        enable_on_boot(args.instance_id)

    elif args.command == "disable":
        disable_on_boot(args.instance_id)

    elif args.command == "migrate":
        migrate_existing(args.workspace)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
