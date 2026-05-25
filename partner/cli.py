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
    """Default action: guide user to the right place."""
    workspace = get_workspace()

    if not workspace:
        print("🤝 欢迎使用 Partner！")
        print()
        print("首次使用请先配置：")
        print("  partner setup")
        print()
        return

    # Check config
    config_path = os.path.join(workspace, "partner_config.json")
    if not os.path.exists(config_path):
        print("🤝 Partner 需要配置。")
        print("  partner setup")
        return

    with open(config_path) as f:
        config = json.load(f)

    backend = config.get('backend', 'hermes')

    print("🤝 Partner 已就绪！")
    print()
    if backend == 'hermes':
        print("Partner 通过 Hermes 与你对话。")
        print("打开 Hermes，然后说：")
        print()
        print("  'partner 最近在研究什么？'")
        print("  '让 partner 去研究 XXX'")
        print("  'partner 知道关于 XXX 的什么？'")
        print()
        print("  hermes")
    elif backend == 'claude_code':
        print("Partner 通过 Claude Code 与你对话。")
        print("打开 Claude Code，然后说：")
        print()
        print("  'partner 最近在研究什么？'")
        print()
        print("  claude")
    else:
        print(f"Partner 使用 {backend} 后端。")


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
    except ProcessLookupError:
        os.remove(pid_path)
        print(f"  ⚠ {label} 进程已不存在，已清理")
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
    else:
        print(f"  ❌ 未知机器人: {platform}（仅支持 qq）")


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

    # default
    parser.set_defaults(func=cmd_default)
    
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        cmd_default(args)


if __name__ == '__main__':
    main()
