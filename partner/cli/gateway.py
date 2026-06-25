"""Partner gateway — background service management.

Commands:
    partner gateway start     Start the Partner background service
    partner gateway stop      Stop the background service
    partner gateway status    Check if service is running
    partner gateway logs      View service logs
"""

import json
import os
import re
import subprocess
import sys
import time

from ..config import resolve_partner_config_path, workspace_has_partner_config
from ..instance_root import resolve_instance_workspace, resolve_partner_root, resolve_global_config_path
from ..workspace_layout import ensure_instance_layout
from .common import (
    C_RESET, C_BOLD, C_DIM, C_CYAN, C_GREEN, C_YELLOW, C_RED,
    _cli_txt, _print_commands, _bot_start, _bot_stop,
    _resolve_runtime_workspace, _root_workspace_if_different,
    get_workspace, _load_manager_module, _launch_instance,
)


GATEWAY_PID_FILE = "partner_gateway.pid"
GATEWAY_LOG_FILE = "partner_gateway.log"


def _gateway_pid_path(workspace: str) -> str:
    return os.path.join(workspace, "state", GATEWAY_PID_FILE)


def _gateway_log_path(workspace: str) -> str:
    return os.path.join(workspace, "state", "logs", GATEWAY_LOG_FILE)


def _get_instance_ids_from_global_config() -> list[str]:
    """Read instance IDs from the global config."""
    try:
        global_cfg_path = resolve_global_config_path()
        if global_cfg_path.exists():
            with open(global_cfg_path) as f:
                cfg = json.load(f)
            instances = cfg.get("instances", {})
            if isinstance(instances, dict):
                return list(instances.keys())
    except Exception:
        pass
    return []


def _get_instance_ids_from_partner_root() -> list[str]:
    """Auto-discover instances by scanning the instances directory."""
    instances_dir = resolve_partner_root() / "instances"
    if instances_dir.exists():
        ids = []
        for entry in sorted(os.listdir(str(instances_dir))):
            inst_path = instances_dir / entry
            if inst_path.is_dir() and workspace_has_partner_config(str(inst_path)):
                ids.append(entry)
        return ids
    return []


def _resolve_instance_ids(workspace: str) -> list[tuple[str, str]]:
    """Return list of (instance_id, instance_workspace) tuples from workspace config."""
    # If workspace is under instances/, it's a single instance
    ws_parent = os.path.basename(os.path.dirname(workspace))
    if ws_parent == "instances":
        instance_id = os.path.basename(workspace)
        return [(instance_id, workspace)]

    # Try global config
    ids = _get_instance_ids_from_global_config()
    if ids:
        return [(iid, str(resolve_instance_workspace(iid))) for iid in ids]

    # Scan instances directory
    ids = _get_instance_ids_from_partner_root()
    if ids:
        return [(iid, str(resolve_instance_workspace(iid))) for iid in ids]

    return []


def _is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def cmd_gateway_start(args):
    """Start Partner background service — launches all instances as daemon processes."""
    workspace = _resolve_runtime_workspace(args.workspace) or get_workspace()
    if not workspace:
        print("❌ Partner 未配置，请先运行: partner setup")
        return

    pid_path = _gateway_pid_path(workspace)
    if os.path.exists(pid_path):
        try:
            with open(pid_path) as f:
                pid = int(f.read().strip())
            if _is_process_running(pid):
                print(f"  ⚠ Gateway 已在运行 (PID: {pid})")
                return
        except (OSError, ValueError):
            pass

    print()
    print(f"  {C_BOLD}{C_CYAN}Starting Gateway...{C_RESET}")
    print(f"  Workspace: {workspace}")
    print()

    # Resolve which instances to launch
    instances = _resolve_instance_ids(workspace)
    if not instances:
        # Single instance mode — launch a single instance using the workspace as its ID
        instance_id = os.path.basename(os.path.normpath(workspace))
        instances = [(instance_id, workspace)]

    # Ensure state directories
    state_dir = os.path.join(workspace, "state")
    logs_dir = os.path.join(state_dir, "logs")
    os.makedirs(state_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)

    launched_instances = []
    for instance_id, instance_ws in instances:
        print(f"  {C_DIM}Launching instance {C_BOLD}{instance_id}{C_RESET}{C_DIM}...{C_RESET}")
        proc = _launch_instance(instance_id, instance_ws)
        if proc:
            launched_instances.append((instance_id, proc.pid))
            print(f"    {C_GREEN}✅ Started (PID: {proc.pid}){C_RESET}")
        else:
            print(f"    {C_RED}❌ Failed to start{C_RESET}")

    # Write gateway PID (use a sentinel PID — the gateway itself is just a launcher/coordinator)
    # We use the PID of the last launched instance as the gateway PID for tracking
    if launched_instances:
        # Actually, write a proper gateway PID that's the parent of this process
        # For tracking purposes, we create a simple shell process that holds the gateway
        # Better approach: write the current process PID as gateway coordinator
        gateway_pid = os.getpid()
        try:
            with open(pid_path, "w") as f:
                f.write(str(gateway_pid))
            print(f"\n  {C_GREEN}✅ Gateway started (Coordinator PID: {gateway_pid}){C_RESET}")
        except Exception as e:
            print(f"  {C_RED}❌ Failed to write gateway PID: {e}{C_RESET}")

        # Also save the launched instance PIDs for clean stop
        instances_json = os.path.join(state_dir, "gateway_instances.json")
        try:
            with open(instances_json, "w") as f:
                json.dump(launched_instances, f, indent=2)
        except Exception:
            pass

        print(f"\n  {C_DIM}Launched {len(launched_instances)} instance(s):{C_RESET}")
        for iid, pid in launched_instances:
            print(f"    {C_DIM}• {iid} (PID: {pid}){C_RESET}")
        print()
    else:
        print(f"\n  {C_RED}❌ No instances launched{C_RESET}")
        return

    _print_commands()


def cmd_gateway_stop(args):
    """Stop the background service — kills all instance processes."""
    workspace = _resolve_runtime_workspace(args.workspace) or get_workspace()
    if not workspace:
        print("❌ Partner 未配置，请先运行: partner setup")
        return

    state_dir = os.path.join(workspace, "state")
    pid_path = _gateway_pid_path(workspace)
    instances_json = os.path.join(state_dir, "gateway_instances.json")
    stopped_any = False

    # 1. Kill instances recorded in gateway_instances.json
    if os.path.exists(instances_json):
        try:
            with open(instances_json) as f:
                launched = json.load(f)
            for instance_id, pid in launched:
                try:
                    os.kill(pid, 15)  # SIGTERM
                    print(f"  ✅ Instance {instance_id} stopped (PID: {pid})")
                    stopped_any = True
                except ProcessLookupError:
                    print(f"  ⚠ Instance {instance_id} already stopped")
                    stopped_any = True
                except Exception as e:
                    print(f"  ❌ Failed to stop instance {instance_id}: {e}")
                # Clean up PID file
                inst_pid_path = os.path.join(state_dir, f"instance_{instance_id}.pid")
                if os.path.exists(inst_pid_path):
                    try:
                        os.remove(inst_pid_path)
                    except Exception:
                        pass
                qq_pid_path = os.path.join(state_dir, "qq_bot.pid")
                if os.path.exists(qq_pid_path):
                    try:
                        os.remove(qq_pid_path)
                    except Exception:
                        pass
        except Exception as e:
            print(f"  ❌ Failed to read instances list: {e}")

    # 2. Kill instance PIDs found via individual PID files
    for fname in os.listdir(state_dir):
        if fname.endswith(".pid") and fname.startswith("instance_"):
            inst_pid_path = os.path.join(state_dir, fname)
            try:
                with open(inst_pid_path) as f:
                    pid = int(f.read().strip())
                try:
                    os.kill(pid, 15)
                    print(f"  ✅ Stopped process (PID: {pid}) from {fname}")
                    stopped_any = True
                except ProcessLookupError:
                    pass
                os.remove(inst_pid_path)
            except Exception:
                pass

    # 3. Also stop QQ bot via legacy path
    _bot_stop(workspace, "qq", quiet=True)

    # 4. Kill gateway PID
    if os.path.exists(pid_path):
        try:
            with open(pid_path) as f:
                pid = int(f.read().strip())
            if pid != os.getpid():  # Don't kill ourselves
                try:
                    os.kill(pid, 15)
                    print(f"  ✅ Gateway stopped (PID: {pid})")
                    stopped_any = True
                except ProcessLookupError:
                    pass
            os.remove(pid_path)
        except Exception as e:
            print(f"  ❌ Failed to clean gateway PID: {e}")

    # 5. Clean up pid files by pkill pattern
    try:
        pattern = f"python3 -m partner --instance-id .* --workspace {re.escape(workspace)}"
        subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        pattern = f"python -m partner --instance-id .* --workspace {re.escape(workspace)}"
        subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=5)
    except Exception:
        pass

    # 6. Clean up instances JSON
    if os.path.exists(instances_json):
        try:
            os.remove(instances_json)
        except Exception:
            pass

    if not stopped_any:
        print("  ⚠ Gateway 未在运行")

    _print_commands()


def cmd_gateway_status(args):
    """Check if the gateway service is running — shows all instances."""
    workspace = _resolve_runtime_workspace(args.workspace) or get_workspace()
    if not workspace:
        print("❌ Partner 未配置")
        return

    pid_path = _gateway_pid_path(workspace)
    state_dir = os.path.join(workspace, "state")

    print()
    print(f"  {C_BOLD}{C_CYAN}Gateway Status{C_RESET}")
    print()
    print(f"  Workspace: {workspace}")
    print()

    # Gateway coordinator status
    gateway_running = False
    gateway_pid = None
    if os.path.exists(pid_path):
        try:
            with open(pid_path) as f:
                gateway_pid = int(f.read().strip())
            gateway_running = _is_process_running(gateway_pid)
            if gateway_running:
                print(f"  {C_BOLD}Gateway:{C_RESET}     {C_GREEN}Running{C_RESET} (PID: {gateway_pid})")
            else:
                print(f"  {C_BOLD}Gateway:{C_RESET}     {C_RED}Not running{C_RESET} (stale PID)")
        except (OSError, ValueError):
            print(f"  {C_BOLD}Gateway:{C_RESET}     {C_RED}Not running{C_RESET}")
    else:
        print(f"  {C_BOLD}Gateway:{C_RESET}     {C_RED}Not running{C_RESET}")

    print()

    # Get instances from global config or scan
    instances = _resolve_instance_ids(workspace)
    if not instances:
        # Fallback: try to find PID files in state dir
        found_any = False
        for fname in sorted(os.listdir(state_dir)):
            if fname.endswith(".pid"):
                inst_pid_path = os.path.join(state_dir, fname)
                try:
                    with open(inst_pid_path) as f:
                        pid = int(f.read().strip())
                    running = _is_process_running(pid)
                    label = fname.replace(".pid", "")
                    status_str = f"{C_GREEN}Running{C_RESET}" if running else f"{C_RED}Stopped{C_RESET}"
                    print(f"  {C_BOLD}{label}:{C_RESET} {status_str} (PID: {pid})")
                    found_any = True
                except Exception:
                    pass
        if not found_any:
            print(f"  {C_DIM}No instances configured or running{C_RESET}")
        print()
        return

    # Show each instance
    print(f"  {C_BOLD}Instances:{C_RESET}")
    print()
    for instance_id, instance_ws in instances:
        # Check instance PID
        inst_pid_path = os.path.join(state_dir, f"instance_{instance_id}.pid")
        inst_running = False
        inst_pid = None
        if os.path.exists(inst_pid_path):
            try:
                with open(inst_pid_path) as f:
                    inst_pid = int(f.read().strip())
                inst_running = _is_process_running(inst_pid)
            except (OSError, ValueError):
                pass

        # Check QQ Bot PID
        qq_pid_path = os.path.join(state_dir, "qq_bot.pid")
        qq_running = False
        qq_pid = None
        if os.path.exists(qq_pid_path):
            try:
                with open(qq_pid_path) as f:
                    qq_pid = int(f.read().strip())
                qq_running = _is_process_running(qq_pid)
            except (OSError, ValueError):
                pass

        # Check world model enabled
        wm_enabled = False
        try:
            wm_config_path = os.path.join(instance_ws, "config", "world_model.yaml")
            if os.path.exists(wm_config_path):
                import yaml
                with open(wm_config_path) as f:
                    wm_data = yaml.safe_load(f)
                wm_enabled = bool(wm_data.get("world_model", {}).get("enabled", False)) if isinstance(wm_data, dict) else False
            else:
                wm_json_path = os.path.join(instance_ws, "config", "world_model.json")
                if os.path.exists(wm_json_path):
                    with open(wm_json_path) as f:
                        wm_data = json.load(f)
                    wm_enabled = bool(wm_data.get("world_model", {}).get("enabled", False)) if isinstance(wm_data, dict) else False
        except Exception:
            pass

        # Last heartbeat time
        active_plan_path = os.path.join(instance_ws, "state", "active_plan.json")
        heartbeat = "N/A"
        if os.path.exists(active_plan_path):
            try:
                with open(active_plan_path) as f:
                    plan = json.load(f)
                heartbeat = plan.get("last_heartbeat", "N/A")
            except Exception:
                pass

        instance_status = f"{C_GREEN}Running{C_RESET}" if inst_running else f"{C_RED}Stopped{C_RESET}"
        qq_status = f"{C_GREEN}Running{C_RESET}" if qq_running else f"{C_RED}Stopped{C_RESET}"
        wm_status = f"{C_GREEN}Enabled{C_RESET}" if wm_enabled else f"{C_DIM}Disabled{C_RESET}"

        print(f"    {C_BOLD}{instance_id}:{C_RESET}")
        print(f"      Instance:  {instance_status}" + (f" (PID: {inst_pid})" if inst_pid else ""))
        print(f"      QQ Bot:    {qq_status}" + (f" (PID: {qq_pid})" if qq_pid else ""))
        print(f"      World Model: {wm_status}")
        print(f"      Heartbeat: {C_DIM}{heartbeat}{C_RESET}")
        print()

    _print_commands()


def cmd_gateway_logs(args):
    """View gateway logs."""
    workspace = _resolve_runtime_workspace(args.workspace) or get_workspace()
    if not workspace:
        print("❌ Partner 未配置")
        return

    log_paths = [
        _gateway_log_path(workspace),
        os.path.join(workspace, "state", "logs", "qq_bot.log"),
    ]

    # Also add instance logs
    state_logs_dir = os.path.join(workspace, "state", "logs")
    if os.path.isdir(state_logs_dir):
        for fname in sorted(os.listdir(state_logs_dir)):
            if fname.startswith("instance_") and fname.endswith(".log"):
                log_paths.append(os.path.join(state_logs_dir, fname))

    log_file = None
    for path in log_paths:
        if os.path.exists(path):
            log_file = path
            break

    if not log_file:
        print("  ⚠ 没有找到 Gateway 日志文件")
        return

    follow = getattr(args, "follow", False)

    if follow:
        # Tail -f equivalent
        try:
            if os.name == "nt":
                subprocess.run(["powershell", "Get-Content", "-Wait", "-Tail", "50", log_file], timeout=None)
            else:
                subprocess.run(["tail", "-n", "50", "-f", log_file], timeout=None)
        except KeyboardInterrupt:
            pass
        except FileNotFoundError:
            # Fallback: read and print with polling
            try:
                with open(log_file, "r") as f:
                    lines = f.readlines()
                    for line in lines[-50:]:
                        print(line.rstrip())
                print(f"\n{C_DIM}Following {log_file}... (Ctrl+C to stop){C_RESET}")
                with open(log_file, "r") as f:
                    f.seek(0, 2)  # Seek to end
                    while True:
                        line = f.readline()
                        if line:
                            print(line.rstrip())
                        else:
                            time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    else:
        # Print last 50 lines
        try:
            if os.name == "nt":
                r = subprocess.run(["powershell", "Get-Content", "-Tail", "50", log_file], capture_output=True, text=True, timeout=10)
                print(r.stdout)
            else:
                r = subprocess.run(["tail", "-n", "50", log_file], capture_output=True, text=True, timeout=10)
                print(r.stdout)
        except Exception as e:
            print(f"  ❌ 读取日志失败: {e}")
            return

    if not follow:
        print(f"  {C_DIM}日志文件: {log_file}{C_RESET}")
        print(f"  {C_DIM}流式查看: partner gateway logs --follow{C_RESET}")
        print()


def register_subparser(sub):
    """Register the 'gateway' subcommand family."""
    p = sub.add_parser("gateway", help=_cli_txt("管理 Partner 后台服务", "Manage Partner background service"))
    g_sub = p.add_subparsers(dest="gateway_action")
    g_sub.required = True

    p_start = g_sub.add_parser("start", help=_cli_txt("启动后台服务", "Start background service"))
    p_start.add_argument("--workspace", "-w", help=_cli_txt("工作区路径", "Workspace path"))
    p_start.set_defaults(func=cmd_gateway_start)

    p_stop = g_sub.add_parser("stop", help=_cli_txt("停止后台服务", "Stop background service"))
    p_stop.add_argument("--workspace", "-w", help=_cli_txt("工作区路径", "Workspace path"))
    p_stop.set_defaults(func=cmd_gateway_stop)

    p_status = g_sub.add_parser("status", help=_cli_txt("检查服务状态", "Check service status"))
    p_status.add_argument("--workspace", "-w", help=_cli_txt("工作区路径", "Workspace path"))
    p_status.set_defaults(func=cmd_gateway_status)

    p_logs = g_sub.add_parser("logs", help=_cli_txt("查看日志", "View logs"))
    p_logs.add_argument("--workspace", "-w", help=_cli_txt("工作区路径", "Workspace path"))
    p_logs.add_argument("--follow", "-f", action="store_true", help=_cli_txt("实时跟踪日志", "Follow log output"))
    p_logs.set_defaults(func=cmd_gateway_logs)
