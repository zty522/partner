"""Partner CLI - minimal, because Partner talks through your agent.

Usage:
    partner setup              First-time configuration
    partner setup --status     Check configuration status
    partner                    Start talking (opens your agent)
"""

import argparse
import json
import os
import sys
from pathlib import Path


def get_workspace() -> str:
    """Get configured workspace path."""
    import json as _json
    
    # 1. Environment variable
    ws = os.environ.get("PARTNER_WORKSPACE")
    if ws and os.path.exists(ws):
        return ws
    
    # 2. Pointer file at ~/.partner
    pointer = os.path.expanduser("~/.partner")
    if os.path.exists(pointer):
        with open(pointer) as f:
            path = f.read().strip()
        if path and os.path.exists(os.path.join(path, "partner_config.json")):
            return path
    
    # 3. Common locations
    candidates = [
        os.path.expanduser("~/partner_workspace"),
        os.path.expanduser("~/.partner_workspace"),
    ]
    for c in candidates:
        config = os.path.join(c, "partner_config.json")
        if os.path.exists(config):
            return c
    
    return None


def cmd_setup(args):
    """Run first-time setup."""
    from .setup import interactive_setup, detect_hermes, detect_claude_code
    interactive_setup()


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
    workspace = args.workspace or find_workspace()
    show_status(workspace)

def cmd_default(args):
    """Default action: show intro + all commands."""
    print()
    print(f"  {C_BOLD}{C_CYAN}🤝 Partner — Your AI Research Companion{C_RESET}")
    print(f"  {C_DIM}An AI research companion that works independently in the background.{C_RESET}")
    print(f"  {C_DIM}You don't give it commands. You just check in.{C_RESET}")
    print()
    print(f"  {C_BOLD}Commands:{C_RESET}")
    print(f"    {C_DIM}partner setup        First-time configuration wizard{C_RESET}")
    print(f"    {C_DIM}partner status       View full status + research stats{C_RESET}")
    print(f"    {C_DIM}partner bot start qq Start QQ bot (background){C_RESET}")
    print(f"    {C_DIM}partner bot stop qq  Stop QQ bot{C_RESET}")
    print(f"    {C_DIM}partner queue clear  Clear task queue / reset plan{C_RESET}")
    print(f"    {C_DIM}partner update       Pull latest code + reinstall{C_RESET}")
    print()
    print(f"  {C_DIM}Or just type 'partner' anytime to see this menu.{C_RESET}")
    print()


def cmd_bot(args):
    workspace = args.workspace or get_workspace()
    if not workspace:
        print("❌ Partner 未配置")
        return
    platform = args.platform
    action = args.action
    if action == "start":
        _bot_start(workspace, platform)
    elif action == "stop":
        _bot_stop(workspace, platform)


def _bot_stop(workspace, platform):
    pid_path = os.path.join(workspace, "state", f"{platform}_bot.pid")
    label = {"qq": "QQ"}.get(platform, platform)
    if not os.path.exists(pid_path):
        print(f"  ⚠ {label} 机器人未在运行")
        return
    try:
        with open(pid_path) as f:
            pid = int(f.read().strip())
        os.kill(pid, 15)
        os.remove(pid_path)
        print(f"  ✅ {label} 机器人已停止 (PID: {pid})")
        _print_commands()
    except ProcessLookupError:
        os.remove(pid_path)
        print(f"  ⚠ {label} 进程已不存在，已清理")
        _print_commands()
    except Exception as e:
        print(f"  ❌ 停止失败: {e}")


def _bot_start(workspace, platform):
    import subprocess
    label = {"qq": "QQ"}.get(platform, platform)
    pp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if platform == "qq":
        cfg = os.path.join(workspace, "qq_config.json")
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
        cmd = [sys.executable, "-c",
            f"import sys; sys.path.insert(0,'{pp}'); from partner.qq_official_bridge import QQQfficialBridge; b=QQQfficialBridge('{workspace}'); b.load_config_from_file('{cfg}'); b.start()"]
        proc = subprocess.Popen(cmd, stdout=open(log,"w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        pidf = os.path.join(workspace, "state", "qq_bot.pid")
        os.makedirs(os.path.dirname(pidf), exist_ok=True)
        with open(pidf, "w") as f:
            f.write(str(proc.pid))
        print(f"  ✅ {label} 已后台启动 (PID: {proc.pid})")
        print(f"     日志: {log}")
        print(f"     停止: partner bot stop qq")
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

    # 3. pip install -e .
    print(f"{C_YELLOW}➜ pip install -e .{C_RESET}")
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

    # 5. Success message
    print(f"{C_BOLD}{C_GREEN}✅ Partner is up to date!{C_RESET}")
    print()
    # ── Commands ──
    print(f"  {C_BOLD}Commands:{C_RESET}")
    print(f"    {C_DIM}partner status       Check Partner status{C_RESET}")
    print(f"    {C_DIM}partner setup        Reconfigure{C_RESET}")
    print(f"    {C_DIM}partner bot start qq Start QQ bot{C_RESET}")
    print(f"    {C_DIM}partner bot stop qq  Stop QQ bot{C_RESET}")
    print(f"    {C_DIM}partner update       Update to latest version{C_RESET}")
    print(f"    {C_DIM}partner queue clear  Clear task queue{C_RESET}")
    print()


def _print_commands():
    """Print the standard commands menu."""
    print()
    print(f"  {C_BOLD}Commands:{C_RESET}")
    print(f"    {C_DIM}partner status              Check Partner status{C_RESET}")
    print(f"    {C_DIM}partner setup               Reconfigure{C_RESET}")
    print(f"    {C_DIM}partner bot start qq        Start QQ bot{C_RESET}")
    print(f"    {C_DIM}partner bot stop qq         Stop QQ bot{C_RESET}")
    print(f"    {C_DIM}partner config set interval N  Change heartbeat interval{C_RESET}")
    print(f"    {C_DIM}partner queue clear         Clear task queue{C_RESET}")
    print(f"    {C_DIM}partner update              Update to latest version{C_RESET}")
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

    cfg_path = os.path.join(workspace, "partner_config.json")
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
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
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            print(f"  ✅ 心跳间隔已设为 {minutes} 分钟")
            print(f"  ⚠ 需要重启 cron 后才能生效: hermes cron update ...")
        except ValueError:
            print("❌ value 必须是数字（分钟数）")
            return

    _print_commands()


def main():
    parser = argparse.ArgumentParser(
        prog='partner',
        description='Partner 🤝 - Your AI Research Companion',
        add_help=True,
    )
    
    sub = parser.add_subparsers(dest='command')
    
    # setup
    p_setup = sub.add_parser('setup', help='配置 Partner（QQ机器人等）')
    p_setup.add_argument('--status', action='store_true', help='查看状态')
    p_setup.set_defaults(func=lambda args: cmd_status(args) if args.status else cmd_setup(args))
    
    # status
    p_status = sub.add_parser('status', help='查看 Partner 状态')
    p_status.add_argument('--workspace', '-w', help='工作区路径')
    p_status.set_defaults(func=cmd_status)

    # bot
    p_bot = sub.add_parser('bot', help='启动/停止机器人')
    p_bot.add_argument('action', choices=['start', 'stop'], help='操作')
    p_bot.add_argument('platform', choices=['qq'], help='机器人类型')
    p_bot.add_argument('--workspace', '-w', help='工作区路径')
    p_bot.set_defaults(func=cmd_bot)

    # update
    p_update = sub.add_parser('update', help='Update Partner to the latest version')
    p_update.set_defaults(func=cmd_update)

    # queue
    p_queue = sub.add_parser('queue', help='管理任务队列')
    q_sub = p_queue.add_subparsers(dest='queue_action')
    p_queue_clear = q_sub.add_parser('clear', help='清空任务队列')
    p_queue_clear.set_defaults(func=lambda args: _cmd_queue_clear(args))

    # config
    p_config = sub.add_parser('config', help='配置管理')
    c_sub = p_config.add_subparsers(dest='config_action')
    p_config_set = c_sub.add_parser('set', help='修改配置')
    p_config_set.add_argument('key', choices=['interval'], help='配置项')
    p_config_set.add_argument('value', help='新值')
    p_config_set.set_defaults(func=_cmd_config_set)

    # default
    parser.set_defaults(func=cmd_default)
    
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        cmd_default(args)


if __name__ == '__main__':
    main()
