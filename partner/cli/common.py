"""Shared CLI utilities extracted from the monolithic cli.py."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import i18n
from ..state.config import (
    load_partner_config_data,
    resolve_partner_config_path,
    save_partner_config_data,
    workspace_has_partner_config,
)
from ..monitoring.instance_root import resolve_instance_workspace, resolve_partner_root

# ── Windows ──
CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# ── ANSI Colors ──
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"


def _cli_txt(zh: str, en: str) -> str:
    return zh if i18n.lang() != "en" else en


def _fmt_bool(ok: bool) -> str:
    return f"{C_GREEN}OK{C_RESET}" if ok else f"{C_RED}Missing{C_RESET}"


def _fmt_optional(ok: bool) -> str:
    return f"{C_GREEN}Configured{C_RESET}" if ok else f"{C_YELLOW}Optional{C_RESET}"


def _print_kv(label: str, value: str):
    print(f"  {C_BOLD}{label}:{C_RESET} {value}")


# ── Command groups ──
COMMAND_GROUPS = [
    {
        "title": _cli_txt("基础", "Basic"),
        "commands": [
            ("partner", _cli_txt("显示主菜单", "Show the main menu")),
            ("partner help", _cli_txt("显示所有可用命令", "Show all available commands")),
        ],
    },
    {
        "title": _cli_txt("配置与安装", "Setup & Installation"),
        "commands": [
            ("partner setup", _cli_txt("配置 Partner", "Configure Partner")),
            ("partner onboard", _cli_txt("引导式安装向导", "Guided setup wizard")),
            ("partner agent list/info/register/...", _cli_txt("管理 Agent 注册", "Manage agent registrations")),
        ],
    },
    {
        "title": _cli_txt("状态与管理", "Status & Management"),
        "commands": [
            ("partner status", _cli_txt("查看所有实例状态", "View all instance status")),
            ("partner doctor", _cli_txt("检查本机运行环境", "Check local environment")),
            ("partner gateway status", _cli_txt("查看后台服务状态", "Check background service status")),
            ("partner instance list", _cli_txt("列出所有实例", "List all instances")),
            ("partner world-model status/test/...", _cli_txt("管理世界模型", "Manage world model connection")),
        ],
    },
    {
        "title": _cli_txt("启动/停止", "Start / Stop"),
        "commands": [
            ("partner gateway start/stop", _cli_txt("管理后台服务", "Manage background service")),
            ("partner bot start/stop qq", _cli_txt("管理 QQ 机器人", "Manage the QQ bot")),
            ("partner start/stop/restart", _cli_txt("快捷 QQ 操作", "Quick QQ bot actions")),
        ],
    },
    {
        "title": _cli_txt("交互模式", "Interactive Mode"),
        "commands": [
            ("partner tui", _cli_txt("进入交互终端", "Enter interactive terminal")),
            ("partner desktop", _cli_txt("打开桌面 GUI", "Open desktop GUI")),
        ],
    },
    {
        "title": _cli_txt("更新与工具", "Update & Utilities"),
        "commands": [
            ("partner update", _cli_txt("更新 Partner", "Update Partner")),
            ("partner showcase build", _cli_txt("生成展示材料", "Build showcase materials")),
            ("partner server add/list/remove", _cli_txt("管理服务器", "Manage servers")),
            ("partner ollama setup/add/list/test", _cli_txt("管理 Ollama", "Manage Ollama pool")),
            ("partner queue clear", _cli_txt("清空任务队列", "Clear task queue")),
            ("partner config set", _cli_txt("修改运行时配置", "Modify runtime config")),
        ],
    },
]

# Max command width for alignment in help menu
_HELP_CMD_WIDTH = 42


def _print_group_header(title: str):
    """Print a group header with unicode box-drawing line."""
    print(f"  {C_BOLD}{C_CYAN}━━━ {title} {C_RESET}{C_DIM}{'━' * (50 - len(title) - 2)}{C_RESET}")


def _print_commands():
    """Print a compact grouped commands menu."""
    print()
    for group in COMMAND_GROUPS:
        title = group["title"]
        cmds = " · ".join(cmd for cmd, _ in group["commands"])
        print(f"  {C_BOLD}{C_CYAN}━━━ {title} ━━━{C_RESET}  {C_DIM}{cmds}{C_RESET}")
    print()


def _print_help_menu():
    """Print the full grouped help menu with descriptions."""
    print()
    for group in COMMAND_GROUPS:
        _print_group_header(group["title"])
        for cmd, desc in group["commands"]:
            padded = cmd.ljust(_HELP_CMD_WIDTH)
            print(f"    {C_DIM}{padded}{C_RESET}{desc}")
        print()


def _resolve_qq_config(workspace: str) -> str:
    candidates = [
        os.path.join(workspace, "config", "qq_config.json"),
        os.path.join(workspace, "qq_config.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def get_workspace() -> str | None:
    """Get configured workspace path."""
    # 1. Environment variable
    ws = os.environ.get("PARTNER_WORKSPACE")
    if ws and os.path.exists(ws):
        return ws

    # 2. Check pointers and repo directory
    partner_home = str(resolve_partner_root())
    pointer_file = os.path.expanduser("~/.partner_workspace")

    # 2a. Pointer file ~/.partner_workspace (new)
    if os.path.isfile(pointer_file):
        try:
            with open(pointer_file) as f:
                path = f.read().strip()
            if path and workspace_has_partner_config(path):
                return path
        except OSError:
            pass

    # 2b. ~/.partner — could be a pointer file (old)
    if os.path.isfile(partner_home):
        try:
            with open(partner_home) as f:
                path = f.read().strip()
            if path and workspace_has_partner_config(path):
                return path
        except OSError:
            pass

    # 2c. ~/.partner is the repo directory — check for config inside
    if os.path.isdir(partner_home):
        if workspace_has_partner_config(partner_home):
            return partner_home

    # 3. Common locations
    candidates = [
        os.path.expanduser("~/partner_workspace"),
        os.path.expanduser("~/.partner_workspace"),
    ]
    for c in candidates:
        if workspace_has_partner_config(c):
            return c

    return None


def _resolve_runtime_workspace(workspace: str | None = None) -> str | None:
    """Resolve commands like `partner start` to an actual instance workspace."""
    explicit = bool(workspace)
    ws = workspace or get_workspace()
    if explicit:
        return ws
    try:
        manager = _load_manager_module()
        cfg = manager.load_global_config()
        instances = cfg.get("instances", {}) if isinstance(cfg.get("instances"), dict) else {}

        def _usable_instance(instance_id: str) -> str:
            inst_ws = str(resolve_instance_workspace(instance_id))
            if not os.path.isdir(inst_ws):
                return ""
            qq_cfgs = (
                os.path.join(inst_ws, "config", "qq_config.json"),
                os.path.join(inst_ws, "qq_config.json"),
            )
            if workspace_has_partner_config(inst_ws) or any(os.path.exists(p) for p in qq_cfgs):
                return inst_ws
            return inst_ws if os.path.isdir(os.path.join(inst_ws, "state")) else ""

        default_id = str(cfg.get("default_instance") or "").strip()
        if default_id:
            inst_ws = _usable_instance(default_id)
            if inst_ws:
                return inst_ws
        if len(instances) == 1:
            only_id = next(iter(instances.keys()))
            inst_ws = _usable_instance(str(only_id))
            if inst_ws:
                return inst_ws
        if "01" in instances:
            inst_ws = _usable_instance("01")
            if inst_ws:
                return inst_ws
    except Exception:
        pass
    return ws


def _root_workspace_if_different(runtime_workspace: str | None) -> str | None:
    root = str(resolve_partner_root())
    if runtime_workspace and os.path.abspath(root) == os.path.abspath(runtime_workspace):
        return None
    if workspace_has_partner_config(root) or os.path.isdir(os.path.join(root, "instances")):
        return root
    return None


def _load_manager_module():
    from .. import manager
    return manager


def _get_default_instance_id() -> str:
    manager = _load_manager_module()
    cfg = manager.load_global_config()
    default_id = str(cfg.get("default_instance") or "").strip()
    if default_id:
        return default_id
    instances = manager.list_instances()
    if len(instances) == 1:
        return next(iter(instances.keys()))
    return ""


def _save_default_instance_id(instance_id: str):
    manager = _load_manager_module()
    cfg = manager.load_global_config()
    cfg["default_instance"] = instance_id
    manager.save_global_config(cfg)


def _resolve_instance_id(value: str | None) -> str:
    if value:
        return value
    default_id = _get_default_instance_id()
    if default_id:
        return default_id
    return ""


def _load_global_cfg() -> dict:
    try:
        from .. import manager
        return manager.load_global_config()
    except Exception:
        return {}


def _save_global_cfg(cfg: dict):
    from .. import manager
    manager.save_global_config(cfg)


def _resolve_config_workspace(args) -> str | None:
    from ..state.setup import find_workspace
    return getattr(args, "workspace", None) or _resolve_runtime_workspace(None) or find_workspace()


def _load_cfg_for_workspace(workspace: str) -> dict:
    try:
        return load_partner_config_data(workspace)
    except Exception:
        return {"workspace": {"path": workspace}, "agent": {"backend": "hermes"}}


def _ensure_agent_cfg(cfg: dict) -> dict:
    agent = cfg.get("agent")
    if not isinstance(agent, dict):
        agent = {}
        cfg["agent"] = agent
    return agent


def _server_tunnel_command(server: dict, remote_port: int = 11434, local_port: int = 11434) -> str:
    user = server.get("user") or "ubuntu"
    host = server.get("host") or "<server>"
    port = int(server.get("port") or 22)
    key = server.get("key_path") or ""
    parts = ["ssh", "-N", "-R", f"{remote_port}:127.0.0.1:{local_port}"]
    if key:
        parts += ["-i", key]
    if port != 22:
        parts += ["-p", str(port)]
    parts.append(f"{user}@{host}")
    return " ".join(parts)


def _launch_instance(instance_id: str, workspace: str) -> subprocess.Popen | None:
    """Launch `python3 -m partner --instance-id X --workspace Y` as background process."""
    log_dir = os.path.join(workspace, "state", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"instance_{instance_id}.log")
    pid_dir = os.path.join(workspace, "state")
    os.makedirs(pid_dir, exist_ok=True)
    pid_path = os.path.join(pid_dir, f"instance_{instance_id}.pid")

    pp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [
        sys.executable, "-m", "partner",
        "--instance-id", instance_id,
        "--workspace", workspace,
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = pp + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=open(log_path, "a"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            creationflags=CREATION_FLAGS,
        )
        with open(pid_path, "w") as f:
            f.write(str(proc.pid))
        return proc
    except Exception as e:
        print(f"  ❌ Failed to launch instance {instance_id}: {e}")
        return None


def _stop_instance(instance_id: str, workspace: str) -> bool:
    """Stop a specific instance by killing its process and cleaning up PID file."""
    pid_dir = os.path.join(workspace, "state")
    pid_path = os.path.join(pid_dir, f"instance_{instance_id}.pid")
    stopped = False
    if os.path.exists(pid_path):
        try:
            with open(pid_path) as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 15)  # SIGTERM
                print(f"  ✅ Instance {instance_id} stopped (PID: {pid})")
                stopped = True
            except ProcessLookupError:
                print(f"  ⚠ Instance {instance_id} already stopped")
                stopped = True
            except Exception as e:
                print(f"  ❌ Failed to stop instance {instance_id}: {e}")
            os.remove(pid_path)
        except (OSError, ValueError) as e:
            print(f"  ❌ Failed to read PID for instance {instance_id}: {e}")
    return stopped


def _get_instance_status(instance_id: str, workspace: str) -> dict:
    """Return status dict for an instance: 'running'/'stopped' with details."""
    result = {
        "instance_id": instance_id,
        "workspace": workspace,
        "running": False,
        "pid": None,
        "qq_running": False,
        "qq_pid": None,
        "world_model_enabled": False,
        "heartbeat": "N/A",
        "plan_status": "",
    }
    state_dir = os.path.join(workspace, "state")

    # Instance PID
    inst_pid_path = os.path.join(state_dir, f"instance_{instance_id}.pid")
    if os.path.exists(inst_pid_path):
        try:
            with open(inst_pid_path) as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                result["running"] = True
                result["pid"] = pid
            except OSError:
                pass
        except (OSError, ValueError):
            pass

    # QQ Bot PID
    qq_pid_path = os.path.join(state_dir, "qq_bot.pid")
    if os.path.exists(qq_pid_path):
        try:
            with open(qq_pid_path) as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                result["qq_running"] = True
                result["qq_pid"] = pid
            except OSError:
                pass
        except (OSError, ValueError):
            pass

    # World model enabled
    try:
        wm_yaml = os.path.join(workspace, "config", "world_model.yaml")
        if os.path.exists(wm_yaml):
            import yaml
            with open(wm_yaml) as f:
                data = yaml.safe_load(f)
            result["world_model_enabled"] = bool(data.get("world_model", {}).get("enabled", False)) if isinstance(data, dict) else False
        else:
            wm_json = os.path.join(workspace, "config", "world_model.json")
            if os.path.exists(wm_json):
                with open(wm_json) as f:
                    data = json.load(f)
                result["world_model_enabled"] = bool(data.get("world_model", {}).get("enabled", False)) if isinstance(data, dict) else False
    except Exception:
        pass

    # Heartbeat / plan status
    plan_path = os.path.join(state_dir, "active_plan.json")
    if os.path.exists(plan_path):
        try:
            with open(plan_path) as f:
                plan = json.load(f)
            result["heartbeat"] = plan.get("last_heartbeat", "N/A")
            result["plan_status"] = plan.get("status", "")
        except Exception:
            pass

    return result


def _bot_stop(workspace, platform, quiet=False):
    pid_path = os.path.join(workspace, "state", f"{platform}_bot.pid")
    label = {"qq": "QQ"}.get(platform, platform)
    stopped_any = False

    def _terminate_runtime_pid(pid: int):
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if result.returncode != 0:
                raise ProcessLookupError(result.stderr or result.stdout or f"PID {pid} not found")
            return
        try:
            os.killpg(os.getpgid(pid), 15)
        except ProcessLookupError:
            raise
        except Exception:
            os.kill(pid, 15)

    try:
        if os.path.exists(pid_path):
            with open(pid_path) as f:
                pid = int(f.read().strip())
            _terminate_runtime_pid(pid)
            os.remove(pid_path)
            print(f"  ✅ {label} 机器人已停止 (PID: {pid})")
            stopped_any = True

        # Also kill watchdog for this workspace
        import subprocess as _sp
        try:
            _sp.run(
                ["pkill", "-f", f"bot_watchdog.py {workspace}"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

        if platform == "qq":
            try:
                pattern = f"QQQfficialBridge\\('{workspace}'\\)"
                r = _sp.run(
                    ["pkill", "-f", pattern],
                    capture_output=True, timeout=5,
                )
                if r.returncode == 0:
                    stopped_any = True
            except Exception:
                pass
            try:
                main_pattern = f"python3 -m partner --instance-id .* --workspace {workspace}"
                r = _sp.run(
                    ["pkill", "-f", main_pattern],
                    capture_output=True, timeout=5,
                )
                if r.returncode == 0:
                    stopped_any = True
            except Exception:
                pass
            try:
                main_pattern = f"python -m partner --instance-id .* --workspace {workspace}"
                r = _sp.run(
                    ["pkill", "-f", main_pattern],
                    capture_output=True, timeout=5,
                )
                if r.returncode == 0:
                    stopped_any = True
            except Exception:
                pass
            try:
                instance_pid = os.path.join(workspace, "instance.pid")
                if os.path.exists(instance_pid):
                    os.remove(instance_pid)
            except Exception:
                pass

    except ProcessLookupError:
        if os.path.exists(pid_path):
            os.remove(pid_path)
        print(f"  ⚠ {label} 进程已不存在，已清理")
        stopped_any = True
    except Exception as e:
        print(f"  ❌ 停止失败: {e}")
        return

    if not stopped_any:
        print(f"  ⚠ {label} 机器人未在运行")
        return

    if not quiet:
        _print_commands()
    if quiet:
        print()


def _auto_start_instance(instance_id, workspace):
    """Auto-start QQ bot for an instance (called by partner-manager).

    Starts the QQ bot and enters a watchdog loop. If QQ is not configured
    (qq_config.json missing), the bot can't start — exits immediately instead
    of looping forever.
    """
    if not workspace:
        workspace = str(resolve_instance_workspace(instance_id))
    if not os.path.exists(workspace):
        print(f"❌ Instance workspace not found: {workspace}", file=sys.stderr)
        sys.exit(2)

    _bot_stop(workspace, "qq", quiet=True)
    _bot_start(workspace, "qq", quiet=True)

    import time
    # Check if bot actually started (PID file was written)
    pid_path = os.path.join(workspace, "state", "qq_bot.pid")
    if not os.path.exists(pid_path):
        print(f"  ⚠ QQ 未配置或启动失败，不进入 watchdog 循环", file=sys.stderr)
        return

    try:
        while True:
            time.sleep(30)
            if os.path.exists(pid_path):
                with open(pid_path) as f:
                    pid = int(f.read().strip())
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    print(f"⚠ Bot died, restarting...", file=sys.stderr)
                    _bot_start(workspace, "qq", quiet=True)
            else:
                print(f"⚠ PID file gone, restarting...", file=sys.stderr)
                _bot_start(workspace, "qq", quiet=True)
    except KeyboardInterrupt:
        _bot_stop(workspace, "qq", quiet=True)


def _bot_start(workspace, platform, quiet=False):
    label = {"qq": "QQ"}.get(platform, platform)
    pp = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if platform == "qq":
        cfg = os.path.join(workspace, "qq_config.json")
        if not os.path.exists(cfg):
            cfg = os.path.join(workspace, "config", "qq_config.json")
        if not os.path.exists(cfg):
            print(f"  ❌ QQ 未配置，请先运行: partner setup")
            return

        try:
            import aiohttp
        except ImportError:
            print(f"  ⚠ 缺少 QQ 机器人依赖 (aiohttp)")
            yn = input(f"     自动安装？[Y/n]: ").strip().lower()
            if yn != "n":
                print(f"     正在安装 aiohttp...")
                r = subprocess.run([sys.executable, "-m", "pip", "install", "aiohttp>=3.8"],
                                   capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    print(f"     ✅ aiohttp 安装成功")
                else:
                    print(f"     ❌ 安装失败: {r.stderr[:100]}")
                    print(f"     手动安装: pip install aiohttp")
                    return
            else:
                print(f"     跳过，稍后手动安装: pip install aiohttp")
                return
        log = os.path.join(workspace, "state", "logs", "qq_bot.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        instance_id = os.path.basename(os.path.normpath(workspace)) or _get_default_instance_id() or "default"
        cmd = [
            sys.executable, "-m", "partner",
            "--instance-id", instance_id,
            "--workspace", workspace,
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = pp + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd,
            stdout=open(log, "w"), stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
            env=env,
            creationflags=CREATION_FLAGS,
        )
        pidf = os.path.join(workspace, "state", "qq_bot.pid")
        os.makedirs(os.path.dirname(pidf), exist_ok=True)
        with open(pidf, "w") as f:
            f.write(str(proc.pid))
        instance_pidf = os.path.join(workspace, "instance.pid")
        try:
            with open(instance_pidf, "w") as f:
                f.write(str(proc.pid))
        except OSError:
            pass
        print(f"  ✅ {label} 已后台启动，研究引擎同步运行 (PID: {proc.pid})")
        print(f"     日志: {log}")
        print(f"     停止: partner bot stop qq")

        watchdog_script = os.path.join(workspace, "scripts", "bot_watchdog.py")
        if os.path.exists(watchdog_script):
            watchdog_log = os.path.join(workspace, "logs", "watchdog.log")
            subprocess.Popen(
                [sys.executable, watchdog_script, workspace],
                stdout=open(watchdog_log, "a"), stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
                creationflags=CREATION_FLAGS,
            )
            print(f"  🛡️  Watchdog 已启动 (自动守护)")

        if not quiet:
            _print_commands()
    else:
        print(f"  ❌ 未知机器人: {platform}（仅支持 qq）")


def _cmd_queue_clear(args):
    """Clear the task queue."""
    from ..state.setup import find_workspace
    workspace = find_workspace()
    if not workspace:
        print("❌ Partner 未配置")
        return

    state_dir = os.path.join(workspace, "state")

    queue_path = os.path.join(state_dir, "task_queue.json")
    try:
        with open(queue_path, 'w', encoding='utf-8') as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        print("  ✅ 任务队列已清空")
    except Exception as e:
        print(f"  ❌ 清空失败: {e}")
        return

    plan_path = os.path.join(state_dir, "active_plan.json")
    try:
        from datetime import datetime
        plan = {
            "status": "",
            "title": "",
            "goal": "",
            "created_at": datetime.now().isoformat(),
            "current_phase_index": 0,
            "phases": [],
            "last_heartbeat": datetime.now().isoformat(),
            "heartbeat_summary": "队列已清空，等待新计划",
        }
        with open(plan_path, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        print("  ✅ 活跃计划已重置")
    except Exception as e:
        print(f"  ⚠ active_plan 重置失败: {e}")

    _print_commands()


def _cmd_config_set(args):
    """Modify runtime configuration."""
    from ..state.setup import find_workspace
    workspace = find_workspace()
    if not workspace:
        print("❌ Partner 未配置")
        return

    try:
        cfg = load_partner_config_data(workspace)
    except Exception as e:
        print(f"❌ 读取配置失败: {e}")
        return

    key = args.key
    value = args.value

    if key == "interval":
        try:
            minutes = int(value)
            if minutes < 1:
                print("❌ 间隔不能小于 1 分钟")
                return
            if "scheduler" not in cfg:
                cfg["scheduler"] = {}
            cfg["scheduler"]["interval_minutes"] = minutes
            save_partner_config_data(workspace, cfg)
            print(f"  ✅ 心跳间隔已设为 {minutes} 分钟")
            print(f"  ⚠ 这是自脉冲/恢复间隔，不是项目执行频率；重启 Partner 后生效。")
        except ValueError:
            print("❌ value 必须是数字（分钟数）")
            return

    _print_commands()
