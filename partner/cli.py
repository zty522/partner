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
    # Check env var
    ws = os.environ.get("PARTNER_WORKSPACE")
    if ws and os.path.exists(ws):
        return ws
    
    # Check common locations
    candidates = [
        os.path.expanduser("~/partner_workspace"),
        os.path.expanduser("~/.partner"),
    ]
    for c in candidates:
        config = os.path.join(c, "partner_config.json")
        if os.path.exists(config):
            return c
    
    return None


def cmd_setup(args):
    """Run first-time setup."""
    from .setup import interactive_setup, find_hermes, find_claude_code
    interactive_setup()


def cmd_status(args):
    """Check Partner status."""
    workspace = args.workspace or get_workspace()
    
    if not workspace:
        print("❌ Partner 未配置。运行 'partner setup' 开始配置。")
        return
    
    config_path = os.path.join(workspace, "partner_config.json")
    if not os.path.exists(config_path):
        print(f"❌ 未找到配置文件: {config_path}")
        print("运行 'partner setup' 开始配置。")
        return
    
    with open(config_path) as f:
        config = json.load(f)
    
    print(f"🤝 Partner 状态")
    print(f"  工作区: {config.get('workspace', workspace)}")
    print(f"  后端: {config.get('backend', 'unknown')}")
    print(f"  配置时间: {config.get('setup_time', 'unknown')}")
    
    # Check state
    state_dir = os.path.join(workspace, "state")
    
    # Stats
    stats_path = os.path.join(state_dir, "stats.json")
    if os.path.exists(stats_path):
        with open(stats_path) as f:
            stats = json.load(f)
        print(f"  研究周期: {stats.get('total_cycles', 0)}")
        print(f"  完成任务: {stats.get('total_tasks_completed', 0)}")
    
    # Knowledge
    kb_path = os.path.join(state_dir, "knowledge.json")
    if os.path.exists(kb_path):
        with open(kb_path) as f:
            kb = json.load(f)
        entries = kb.get("entries", []) if isinstance(kb, dict) else kb
        print(f"  知识条目: {len(entries)}")
    
    # Tasks
    tq_path = os.path.join(state_dir, "task_queue.json")
    if os.path.exists(tq_path):
        with open(tq_path) as f:
            tasks = json.load(f)
        pending = sum(1 for t in tasks if t.get("status") == "pending")
        print(f"  待执行任务: {pending}")
    
    # Heartbeat
    hb_path = os.path.join(state_dir, "heartbeat.json")
    if os.path.exists(hb_path):
        with open(hb_path) as f:
            hb = json.load(f)
        print(f"  最后心跳: {hb.get('last_heartbeat', 'unknown')}")
        print(f"  状态: {hb.get('status', 'unknown')}")
    
    print()
    backend = config.get('backend', 'hermes')
    if backend == 'hermes':
        print("💡 在 Hermes 中说 'partner 最近在研究什么？' 来对话")
    elif backend == 'claude_code':
        print("💡 在 Claude Code 中说 'partner 最近在研究什么？' 来对话")


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


def main():
    parser = argparse.ArgumentParser(
        prog='partner',
        description='Partner 🤝 - Your AI Research Companion',
        add_help=True,
    )
    
    sub = parser.add_subparsers(dest='command')
    
    # setup
    p_setup = sub.add_parser('setup', help='First-time configuration')
    p_setup.add_argument('--status', action='store_true', help='Check status')
    p_setup.set_defaults(func=lambda args: cmd_status(args) if args.status else cmd_setup(args))
    
    # status
    p_status = sub.add_parser('status', help='Check Partner status')
    p_status.add_argument('--workspace', '-w', help='Workspace path')
    p_status.set_defaults(func=cmd_status)
    
    args = parser.parse_args()
    
    if hasattr(args, 'func'):
        args.func(args)
    else:
        cmd_default(args)


if __name__ == '__main__':
    main()
