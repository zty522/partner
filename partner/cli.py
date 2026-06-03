"""Partner CLI - minimal, because Partner talks through your agent.

Usage:
    partner setup              First-time configuration
    partner setup --status     Check configuration status
    partner                    Start talking (opens your agent)
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import i18n
from .config import (
    load_partner_config_data,
    resolve_partner_config_path,
    save_partner_config_data,
    workspace_has_partner_config,
)
from .instance_root import resolve_instance_workspace, resolve_partner_root


def get_workspace() -> str:
    """Get configured workspace path."""
    import json as _json
    
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
    """Resolve commands like `partner start` to an actual instance workspace.

    `get_workspace()` may return the multi-instance root. Runtime commands must
    run against an instance directory, otherwise QQ starts but status checks a
    different path.
    """
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
                os.path.join(inst_ws, "00_config", "qq_config.json"),
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


def cmd_setup(args):
    """Run first-time setup."""
    from .setup import interactive_setup, detect_hermes, detect_claude_code
    interactive_setup(quick=bool(getattr(args, "quick", False)))


# ── ANSI Colors ──
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"


def cmd_status(args):
    """Check Partner status with full detail."""
    from .setup import show_status, find_workspace
    workspace = _resolve_runtime_workspace(args.workspace) or find_workspace()
    show_status(workspace)


def _cli_txt(zh: str, en: str) -> str:
    return zh if i18n.lang() != "en" else en


def _print_help_menu():
    print()
    print(f"  {C_BOLD}{C_CYAN}{_cli_txt('Partner 命令', 'Partner Commands')}{C_RESET}")
    print()
    print(f"    {C_DIM}partner{C_RESET}")
    print(f"      {_cli_txt('显示主菜单', 'Show the main menu')}")
    print(f"    {C_DIM}partner help{C_RESET}")
    print(f"      {_cli_txt('显示所有可用命令', 'Show all available commands')}")
    print(f"    {C_DIM}partner setup{C_RESET}")
    print(f"      {_cli_txt('配置 Partner，并管理实例与 QQ 机器人', 'Configure Partner, instances, and QQ bots')}")
    print(f"    {C_DIM}partner status{C_RESET}")
    print(f"      {_cli_txt('查看所有实例状态、QQ 配置和最近进展', 'Show all instance status, QQ config, and recent progress')}")
    print(f"    {C_DIM}partner start{C_RESET}")
    print(f"      {_cli_txt('启动 QQ 机器人', 'Start the QQ bot')}")
    print(f"    {C_DIM}partner stop{C_RESET}")
    print(f"      {_cli_txt('停止 QQ 机器人', 'Stop the QQ bot')}")
    print(f"    {C_DIM}partner restart{C_RESET}")
    print(f"      {_cli_txt('重启 QQ 机器人', 'Restart the QQ bot')}")
    print(f"    {C_DIM}partner bot start qq{C_RESET}")
    print(f"      {_cli_txt('使用兼容旧版的显式命令启动 QQ 机器人', 'Start the QQ bot with the explicit legacy command')}")
    print(f"    {C_DIM}partner bot stop qq{C_RESET}")
    print(f"      {_cli_txt('使用兼容旧版的显式命令停止 QQ 机器人', 'Stop the QQ bot with the explicit legacy command')}")
    print(f"    {C_DIM}partner update{C_RESET}")
    print(f"      {_cli_txt('更新 Partner 到最新版本', 'Update Partner to the latest version')}")
    print(f"    {C_DIM}partner instance list{C_RESET}")
    print(f"      {_cli_txt('列出所有 Partner 实例', 'List all Partner instances')}")
    print()


def cmd_help(args):
    _print_help_menu()


def _print_kv(label: str, value: str):
    print(f"  {C_BOLD}{label}:{C_RESET} {value}")


def _fmt_bool(ok: bool) -> str:
    return f"{C_GREEN}OK{C_RESET}" if ok else f"{C_RED}Missing{C_RESET}"


def _fmt_optional(ok: bool) -> str:
    return f"{C_GREEN}Configured{C_RESET}" if ok else f"{C_YELLOW}Optional{C_RESET}"


def _resolve_qq_config(workspace: str) -> str:
    candidates = [
        os.path.join(workspace, "00_config", "qq_config.json"),
        os.path.join(workspace, "qq_config.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def cmd_doctor(args):
    workspace = args.workspace or get_workspace()
    print()
    print(f"  {C_BOLD}{C_CYAN}🩺 Partner Doctor{C_RESET}")
    print()

    python_ok = sys.version_info >= (3, 10)
    git_ok = shutil.which("git") is not None
    workspace_ok = bool(workspace and os.path.isdir(workspace))
    config_ok = bool(workspace and workspace_has_partner_config(workspace))
    qq_ok = bool(workspace and os.path.exists(_resolve_qq_config(workspace)))
    hermes_ok = shutil.which("hermes") is not None
    codex_ok = shutil.which("codex") is not None

    _print_kv("Python", f"{sys.version.split()[0]} ({_fmt_bool(python_ok)})")
    _print_kv("Git", _fmt_bool(git_ok))
    _print_kv("Workspace", workspace if workspace else f"{C_YELLOW}Not configured{C_RESET}")
    _print_kv("Config", _fmt_bool(config_ok))
    _print_kv("QQ Config", _fmt_optional(qq_ok))
    _print_kv("Hermes", _fmt_bool(hermes_ok))
    _print_kv("Codex", _fmt_bool(codex_ok))

    issues = []
    if not python_ok:
        issues.append("Python 版本过低，需要 3.10+")
    if not git_ok:
        issues.append("未检测到 git")
    if not workspace_ok:
        issues.append("未找到工作区，请先运行 partner setup")
    elif not config_ok:
        issues.append(f"工作区存在但缺少配置: {resolve_partner_config_path(workspace)}")

    print()
    if issues:
        print(f"  {C_BOLD}Next Fixes:{C_RESET}")
        for item in issues:
            print(f"    - {item}")
    else:
        print(f"  {C_GREEN}环境检查通过，可以直接使用 Partner。{C_RESET}")
        print("    推荐下一步: partner status")
    print()
    _print_commands()


def _load_manager_module():
    from . import manager
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


def cmd_instance(args):
    manager = _load_manager_module()
    action = args.instance_action

    if action == "list":
        manager.print_instance_list()
        _print_commands()
        return

def cmd_default(args):
    """Default action: show intro + all commands."""
    workspace = get_workspace()
    title = _cli_txt('🤝 Partner — 你的 AI 研究伙伴', '🤝 Partner — Your AI Research Companion')
    subtitle = _cli_txt('一个会在后台自主推进工作的 AI 研究伙伴。', 'An AI research companion that works independently in the background.')
    intro = _cli_txt('你不用一直下命令，只需要随时来查看。', 'You do not give it commands. You just check in.')
    workspace_label = _cli_txt('当前工作区', 'Current Workspace')
    workspace_tip = _cli_txt("提示：运行 'partner status' 查看所有实例状态。", "Tip: run 'partner status' to inspect all instance status.")
    not_configured = _cli_txt('尚未完成配置。', 'Not configured yet.')
    setup_hint = _cli_txt('请先运行', 'Run')
    setup_suffix = _cli_txt('开始配置。', 'first.')
    print()
    print(f"  {C_BOLD}{C_CYAN}{title}{C_RESET}")
    print(f"  {C_DIM}{subtitle}{C_RESET}")
    print(f"  {C_DIM}{intro}{C_RESET}")
    print()
    if workspace:
        print(f"  {C_BOLD}{workspace_label}:{C_RESET} {workspace}")
        print(f"  {C_DIM}{workspace_tip}{C_RESET}")
    else:
        print(f"  {C_YELLOW}{not_configured}{C_RESET} {setup_hint} {C_BOLD}partner setup{C_RESET} {setup_suffix}")
    _print_help_menu()


def cmd_bot(args):
    workspace = _resolve_runtime_workspace(args.workspace)
    if not workspace:
        print("❌ Partner 未配置，请先运行: partner setup")
        return
    platform = args.platform
    action = args.action
    if action == "start":
        root_ws = _root_workspace_if_different(workspace)
        if root_ws:
            _bot_stop(root_ws, platform, quiet=True)
        _bot_start(workspace, platform)
    elif action == "stop":
        root_ws = _root_workspace_if_different(workspace)
        if root_ws:
            _bot_stop(root_ws, platform, quiet=True)
        _bot_stop(workspace, platform)


def cmd_short_bot(args):
    workspace = _resolve_runtime_workspace(args.workspace)
    if not workspace:
        print("❌ Partner 未配置，请先运行: partner setup")
        return
    action = args.command
    if action == "start":
        root_ws = _root_workspace_if_different(workspace)
        if root_ws:
            _bot_stop(root_ws, "qq", quiet=True)
        _bot_start(workspace, "qq")
    elif action == "stop":
        root_ws = _root_workspace_if_different(workspace)
        if root_ws:
            _bot_stop(root_ws, "qq", quiet=True)
        _bot_stop(workspace, "qq")
    elif action == "restart":
        root_ws = _root_workspace_if_different(workspace)
        if root_ws:
            _bot_stop(root_ws, "qq", quiet=True)
        _bot_stop(workspace, "qq", quiet=True)
        _bot_start(workspace, "qq")


def _bot_stop(workspace, platform, quiet=False):
    pid_path = os.path.join(workspace, "state", f"{platform}_bot.pid")
    label = {"qq": "QQ"}.get(platform, platform)
    stopped_any = False
    try:
        if os.path.exists(pid_path):
            with open(pid_path) as f:
                pid = int(f.read().strip())
            os.kill(pid, 15)
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
        print()  # blank line for spacing


def _auto_start_instance(instance_id, workspace):
    """Auto-start QQ bot for an instance (called by partner-manager)."""
    import subprocess
    if not workspace:
        # Resolve workspace from instance_id
        workspace = str(resolve_instance_workspace(instance_id))
    if not os.path.exists(workspace):
        print(f"❌ Instance workspace not found: {workspace}", file=sys.stderr)
        sys.exit(2)

    # Stop existing bot if running
    _bot_stop(workspace, "qq", quiet=True)

    # Start QQ bot
    _bot_start(workspace, "qq", quiet=True)

    # Keep the process alive (partner-manager watches this)
    import time
    pid_path = os.path.join(workspace, "state", "qq_bot.pid")
    try:
        while True:
            time.sleep(30)
            # Check if bot is still alive
            if os.path.exists(pid_path):
                with open(pid_path) as f:
                    pid = int(f.read().strip())
                try:
                    os.kill(pid, 0)  # Check if process exists
                except ProcessLookupError:
                    print(f"⚠ Bot died, restarting...", file=sys.stderr)
                    _bot_start(workspace, "qq", quiet=True)
            else:
                print(f"⚠ PID file gone, restarting...", file=sys.stderr)
                _bot_start(workspace, "qq", quiet=True)
    except KeyboardInterrupt:
        _bot_stop(workspace, "qq", quiet=True)


def _bot_start(workspace, platform, quiet=False):
    import subprocess
    label = {"qq": "QQ"}.get(platform, platform)
    pp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if platform == "qq":
        cfg = os.path.join(workspace, "qq_config.json")
        if not os.path.exists(cfg):
            # Also check 00_config/ subdirectory (multi-instance layout)
            cfg = os.path.join(workspace, "00_config", "qq_config.json")
        if not os.path.exists(cfg):
            print(f"  ❌ QQ 未配置，请先运行: partner setup")
            return
        
        # Check and auto-install dependencies
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
        log = os.path.join(workspace, "logs", "qq_bot.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        # Start the full Partner runtime, not just the QQ bridge. The runtime
        # launches mind_loop() and then attaches QQ for user messages/reports.
        instance_id = os.path.basename(os.path.normpath(workspace)) or _get_default_instance_id() or "default"
        cmd = [
            sys.executable, "-m", "partner",
            "--instance-id", instance_id,
            "--workspace", workspace,
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = pp + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.Popen(
            cmd,
            stdout=open(log, "w"), stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
            env=env,
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

        # Start watchdog (process monitor + auto-restart)
        watchdog_script = os.path.join(workspace, "scripts", "bot_watchdog.py")
        if os.path.exists(watchdog_script):
            watchdog_log = os.path.join(workspace, "logs", "watchdog.log")
            subprocess.Popen(
                [sys.executable, watchdog_script, workspace],
                stdout=open(watchdog_log, "a"), stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
            print(f"  🛡️  Watchdog 已启动 (自动守护)")

        if not quiet:
            _print_commands()
    else:
        print(f"  ❌ 未知机器人: {platform}（仅支持 qq）")


def cmd_update(args):
    """Update Partner to the latest version."""
    import subprocess

    # 1. Resolve partner repo directory
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"{C_BOLD}🔄 Updating Partner...{C_RESET}")
    print(f"   Repo: {C_CYAN}{repo_dir}{C_RESET}")
    print()

    # 2. git pull
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        print(f"{C_RED}❌ 当前目录不是 Git 仓库，无法执行 partner update{C_RESET}")
        print(f"   请重新 clone 仓库，或手动进入正确目录后再运行。")
        sys.exit(1)

    print(f"{C_YELLOW}➜ git fetch --all --prune{C_RESET}")
    fetch = subprocess.run(["git", "fetch", "--all", "--prune"], capture_output=True, text=True, timeout=120, cwd=repo_dir)
    if fetch.returncode != 0:
        print(f"{C_RED}❌ git fetch failed:{C_RESET}")
        err = (fetch.stderr or fetch.stdout or "").strip()
        if err:
            print(err)
        print("   无法连接远程仓库；请检查网络、代理或 Git 远程配置后重试。")
        sys.exit(1)

    status_r = subprocess.run(["git", "status", "-sb"], capture_output=True, text=True, timeout=30, cwd=repo_dir)
    status_line = status_r.stdout.strip().splitlines()[0] if status_r.stdout.strip() else ""
    if status_line:
        print(f"   {status_line}")

    print()
    print(f"{C_YELLOW}➜ git pull{C_RESET}")
    r = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=120, cwd=repo_dir)
    if r.returncode != 0:
        print(f"{C_RED}❌ git pull failed:{C_RESET}")
        print(r.stderr)
        sys.exit(1)
    # Print git output (trim trailing newline)
    out = r.stdout.rstrip("\n")
    if out:
        for line in out.split("\n"):
            print(f"   {line}")
    print(f"{C_GREEN}   ✅ git pull completed{C_RESET}")
    print()

    # 3. Remove stale editable metadata, then pip install -e .
    print(f"{C_YELLOW}➜ cleaning old partner-research installs{C_RESET}")
    for _ in range(3):
        old = subprocess.run(
            [sys.executable, "-m", "pip", "show", "partner-research"],
            capture_output=True, text=True, timeout=30,
        )
        if old.returncode != 0:
            break
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", "partner-research"],
            capture_output=True, text=True, timeout=120,
        )

    print(f"{C_YELLOW}➜ pip install -e .{C_RESET}")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".", "--break-system-packages"],
        capture_output=True, text=True, timeout=120, cwd=repo_dir,
    )
    if r.returncode != 0:
        # Retry without --break-system-packages (older pip versions)
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            capture_output=True, text=True, timeout=120, cwd=repo_dir,
        )
    if r.returncode != 0:
        print(f"{C_RED}❌ pip install failed:{C_RESET}")
        print(r.stderr)
        sys.exit(1)
    # Show last line of pip output (usually "Successfully installed ...")
    pip_lines = r.stdout.rstrip("\n").split("\n")
    last_line = pip_lines[-1].strip() if pip_lines else ""
    if last_line:
        print(f"   {last_line}")
    print(f"{C_GREEN}   ✅ pip install completed{C_RESET}")

    verify = subprocess.run(
        [
            sys.executable, "-c",
            "import importlib.metadata as m, partner, pathlib; "
            "print(m.version('partner-research')); "
            "print(pathlib.Path(partner.__file__).resolve())"
        ],
        capture_output=True, text=True, timeout=30,
    )
    if verify.returncode == 0:
        lines = [x.strip() for x in verify.stdout.splitlines() if x.strip()]
        if lines:
            print(f"   Version: {lines[0]}")
        if len(lines) > 1:
            print(f"   Import: {lines[1]}")
    else:
        print(f"{C_YELLOW}   ⚠ 安装后 import 校验失败，请重新打开终端或运行: hash -r{C_RESET}")
    print()

    # 4. Read and print latest CHANGELOG.md entry
    changelog_path = os.path.join(repo_dir, "CHANGELOG.md")
    if os.path.exists(changelog_path):
        print(f"{C_BOLD}📋 Latest Changes{C_RESET}")
        print()
        with open(changelog_path) as f:
            lines = f.readlines()

        # Find first ## version header (skip the top # Changelog)
        in_entry = False
        entry_lines = []
        for line in lines:
            if line.startswith("## ") and not in_entry:
                in_entry = True
                entry_lines.append(line)
            elif line.startswith("## ") and in_entry:
                # Found next section header — stop
                break
            elif in_entry:
                entry_lines.append(line)

        # Trim trailing blank lines
        while entry_lines and entry_lines[-1].strip() == "":
            entry_lines.pop()
        # Trim leading blank lines (after the header)
        while len(entry_lines) > 1 and entry_lines[1].strip() == "":
            entry_lines.pop(1)

        for line in entry_lines:
            print(line, end="")
        print()
    else:
        print(f"{C_YELLOW}⚠  No CHANGELOG.md found{C_RESET}")
        print()

    # 5. Auto-restart QQ bot if it was running
    print(f"{C_YELLOW}➜ Checking QQ bot...{C_RESET}")
    workspace = None
    try:
        from .setup import find_workspace as _fw
        workspace = _resolve_runtime_workspace(_fw())
    except Exception:
        pass

    qq_was_running = False
    if workspace:
        pid_path = os.path.join(workspace, "state", "qq_bot.pid")
        if os.path.exists(pid_path):
            try:
                with open(pid_path) as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)
                qq_was_running = True
            except (OSError, ValueError):
                pass

    if qq_was_running:
        print(f"   🤖 QQ 机器人运行中 → 自动重启...")
        _bot_stop(workspace, "qq", quiet=True)
        _bot_start(workspace, "qq", quiet=True)
    else:
        print(f"   ℹ QQ 机器人未运行（跳过重启）")
    print()

    # 6. Check and report current work state
    print(f"{C_YELLOW}➜ Checking work state...{C_RESET}")
    if workspace:
        state_dir = os.path.join(workspace, "state")
        plan_path = os.path.join(state_dir, "active_plan.json")
        queue_path = os.path.join(state_dir, "task_queue.json")
        cfg_path = resolve_partner_config_path(workspace)

        print(f"   📁 工作区: {workspace}")
        print(f"   ⚙️ 配置: {cfg_path}")

        # Active plan
        if os.path.exists(plan_path):
            try:
                with open(plan_path) as f:
                    plan = json.load(f)
                status_map = {"idle": "空闲", "planning": "规划中", "active": "执行中",
                              "completed": "已完成"}
                raw = plan.get("status", "idle")
                disp = status_map.get(raw, raw)
                title = plan.get("title", "")
                summary = plan.get("heartbeat_summary", "")
                hb = plan.get("last_heartbeat", "")[:16]
                print(f"   📶 状态: {C_BOLD}{disp}{C_RESET}")
                if hb:
                    print(f"   💓 心跳: {hb}")
                if title:
                    print(f"   📋 计划: {title}")
                if summary:
                    print(f"   📝 摘要: {summary}")
            except Exception:
                print(f"   ℹ 无法读取 active_plan")

        # Pending tasks
        if os.path.exists(queue_path):
            try:
                with open(queue_path) as f:
                    tasks = json.load(f)
                pending = sum(1 for t in tasks if isinstance(t, dict) and t.get("status") == "pending")
                if pending > 0:
                    print(f"   ⏳ 待执行: {C_BOLD}{pending}{C_RESET} 个任务")
                else:
                    print(f"   ⏳ 队列: 空")
            except Exception:
                pass

        # Cron check — auto-create if missing
        try:
            cr = subprocess.run(["hermes", "cron", "list"], capture_output=True, text=True, timeout=10)
            if "partner-research" in cr.stdout:
                print(f"   ⏰ Cron: 已设置")
            else:
                print(f"   ⏰ Cron: 未设置 → 自动创建...")
                # Read config for interval
                interval = 30
                try:
                    cfg = load_partner_config_data(workspace)
                    interval = cfg.get("scheduler", {}).get("interval_minutes", 30)
                except Exception:
                    pass

                cron_prompt = f"""你是 Partner 的执行引擎。在 {workspace} 下工作。

你的核心原则：
1. 30 分钟是最小心跳间隔，不是执行窗口
2. **不要停下来** — 计划完成后，立即搜索该领域最新前沿文献，
   看有没有新的改进方向。如果有 → 创建延续计划继续研究。
   不要等用户下指令才继续。

每次心跳：
1. 检查 active_plan.json → 有活跃计划正在执行就不打断，只更新心跳
2. 计划已完成 → 读取 goal 和结果 → 搜索该领域前沿文献 →
   有新方向就创建延续计划，没有就检查队列
3. 空闲 + 队列有任务 → 自动创建计划
4. 每次心跳向 QQ 汇报当前状态

用中文。只在 {workspace} 内写文件。"""

                cr_create = subprocess.run(
                    ["hermes", "cron", "create",
                     "--name", "partner-research-cycle",
                     f"every {interval}m",
                     cron_prompt],
                    capture_output=True, text=True, timeout=30,
                )
                if cr_create.returncode == 0:
                    import re as _re
                    m = _re.search(r'\[([a-f0-9-]+)\]', cr_create.stdout)
                    if m:
                        new_id = m.group(1)
                        try:
                            with open(cfg_path) as f:
                                cfg = json.load(f)
                            cfg.setdefault("scheduler", {})["cron_job_id"] = new_id
                            with open(cfg_path, 'w') as f:
                                json.dump(cfg, f, indent=2, ensure_ascii=False)
                        except Exception:
                            pass
                    print(f"   ✅ Cron 已创建（每 {interval} 分钟）")
                    # Trigger an immediate run
                    subprocess.run(
                        ["hermes", "cron", "run", "partner-research-cycle", "--accept-hooks"],
                        capture_output=True, timeout=120,
                    )
                    print(f"   🚀 已触发首次研究循环")
                else:
                    print(f"   ⚠ Cron 创建失败: {cr_create.stderr[:100]}")
        except Exception as e:
            print(f"   ⚠ Cron 检查失败: {e}")
    else:
        print(f"   ℹ 未找到工作区（运行 partner setup 配置）")
        # 没工作区 → 询问是否运行 setup
        _tty = None
        try:
            _tty = open("/dev/tty", "r")
        except OSError:
            pass
        if _tty:
            try:
                print(f"  {C_CYAN}是否运行配置向导？{C_RESET}[Y/n] ", end="", flush=True)
                answer = _tty.readline().strip().lower()
                if answer in ("", "y", "yes"):
                    print()
                    from .setup import interactive_setup
                    interactive_setup()
            except (EOFError, OSError):
                pass
            finally:
                _tty.close()
    print()

    # 7. Success message
    print(f"{C_BOLD}{C_GREEN}✅ Partner is up to date!{C_RESET}")
    print()
    # ── Commands ──
    _print_commands()

    # 8. Ask if user wants to reconfigure
    if workspace:
        # Try to read input from /dev/tty if available (works inside curl|bash pipes)
        _tty = None
        try:
            _tty = open("/dev/tty", "r")
        except OSError:
            pass

        if _tty:
            try:
                print(f"\n  {C_CYAN}检测到已有配置，是否运行配置向导修改？{C_RESET}[Y/n] ", end="", flush=True)
                answer = _tty.readline().strip().lower()
                if answer in ("", "y", "yes"):
                    print()
                    from .setup import interactive_setup
                    interactive_setup()
            except (EOFError, OSError):
                print()
            finally:
                _tty.close()
        else:
            print(f"\n  检测到已有配置。可稍后运行: {C_BOLD}partner setup{C_RESET}")


def _print_commands():
    """Print the standard commands menu."""
    print()
    print(f"  {C_BOLD}Commands:{C_RESET}")
    print(f"    {C_DIM}partner{C_RESET}")
    print(f"    {C_DIM}partner help{C_RESET}")
    print(f"    {C_DIM}partner setup{C_RESET}")
    print(f"    {C_DIM}partner status{C_RESET}")
    print(f"    {C_DIM}partner start{C_RESET}")
    print(f"    {C_DIM}partner stop{C_RESET}")
    print(f"    {C_DIM}partner restart{C_RESET}")
    print(f"    {C_DIM}partner bot start qq{C_RESET}")
    print(f"    {C_DIM}partner bot stop qq{C_RESET}")
    print(f"    {C_DIM}partner update{C_RESET}")
    print(f"    {C_DIM}partner instance list{C_RESET}")
    print()


def _cmd_queue_clear(args):
    """Clear the task queue."""
    from .setup import find_workspace
    workspace = find_workspace()
    if not workspace:
        print("❌ Partner 未配置")
        return

    state_dir = os.path.join(workspace, "state")

    # Clear task queue
    queue_path = os.path.join(state_dir, "task_queue.json")
    try:
        with open(queue_path, 'w', encoding='utf-8') as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        print("  ✅ 任务队列已清空")
    except Exception as e:
        print(f"  ❌ 清空失败: {e}")
        return

    # Reset active_plan
    plan_path = os.path.join(state_dir, "active_plan.json")
    try:
        from datetime import datetime
        plan = {
            "status": "idle",
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

    print()
    print("  Commands:")
    print("    partner status       Check Partner status")
    print("    partner setup        Reconfigure")
    print("    partner bot start qq Start QQ bot")
    print("    partner bot stop qq  Stop QQ bot")
    print("    partner queue clear  Clear task queue")
    print("    partner update       Update to latest version")


def _cmd_config_set(args):
    """Modify runtime configuration."""
    from .setup import find_workspace
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
            print(f"  ⚠ 需要重启 cron 后才能生效: hermes cron edit ...")
        except ValueError:
            print("❌ value 必须是数字（分钟数）")
            return

    _print_commands()


def main():
    parser = argparse.ArgumentParser(
        prog='partner',
        description='Partner 🤝 - Your AI Research Companion',
        add_help=False,
    )

    # Global arguments (used by partner-manager)
    parser.add_argument('-h', '--help', action='store_true', dest='show_help',
                        help=argparse.SUPPRESS)
    parser.add_argument('--instance-id', dest='instance_id', default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument('--workspace', '-w', default=None,
                        help='工作区路径')

    sub = parser.add_subparsers(dest='command')
    
    # setup
    p_setup = sub.add_parser('setup', help='配置 Partner（QQ机器人等）')
    p_setup.add_argument('--status', action='store_true', help='查看状态')
    p_setup.set_defaults(func=lambda args: cmd_status(args) if args.status else cmd_setup(args))

    p_help = sub.add_parser('help', help='显示完整命令帮助')
    p_help.set_defaults(func=cmd_help)
    
    # status
    p_status = sub.add_parser('status', help='查看 Partner 状态')
    p_status.add_argument('--workspace', '-w', help='工作区路径')
    p_status.set_defaults(func=cmd_status)

    for action, desc in [('start', '启动 QQ 机器人'), ('stop', '停止 QQ 机器人'), ('restart', '重启 QQ 机器人')]:
        p_short = sub.add_parser(action, help=desc)
        p_short.add_argument('--workspace', '-w', help='工作区路径')
        p_short.set_defaults(func=cmd_short_bot, command=action)

    # bot
    p_bot = sub.add_parser('bot', help='启动/停止机器人')
    p_bot.add_argument('action', choices=['start', 'stop'], help='操作')
    p_bot.add_argument('platform', choices=['qq'], help='机器人类型')
    p_bot.add_argument('--workspace', '-w', help='工作区路径')
    p_bot.set_defaults(func=cmd_bot)

    # update
    p_update = sub.add_parser('update', help='Update Partner to the latest version')
    p_update.set_defaults(func=cmd_update)

    p_instance = sub.add_parser('instance', help='多实例管理快捷入口')
    i_sub = p_instance.add_subparsers(dest='instance_action')
    i_sub.required = True
    i_sub.add_parser('list', help='列出所有实例')
    p_instance.set_defaults(func=cmd_instance)

    # default
    parser.set_defaults(func=cmd_default)

    args = parser.parse_args()

    if getattr(args, 'show_help', False):
        cmd_help(args)
        return

    # When partner-manager starts an instance: --instance-id <id> --workspace <path>
    # No subcommand → auto-start QQ bot for that instance
    if args.instance_id and args.command is None:
        _auto_start_instance(args.instance_id, args.workspace)
        return

    if hasattr(args, 'func'):
        args.func(args)
    else:
        cmd_default(args)


if __name__ == '__main__':
    main()
