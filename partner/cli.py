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
    import json as _json
    
    workspace = args.workspace or get_workspace()
    
    if not workspace:
        print("❌ Partner not configured. Run 'partner setup' first.")
        return
    
    config_path = os.path.join(workspace, "partner_config.json")
    if not os.path.exists(config_path):
        print(f"❌ Config not found: {config_path}")
        print("Run 'partner setup' to configure.")
        return
    
    with open(config_path) as f:
        config = _json.load(f)
    
    state_dir = os.path.join(workspace, "state")
    
    # Header
    print()
    print(f"  {C_BOLD}{C_CYAN}🤝 Partner Status{C_RESET}")
    print(f"  {C_DIM}{'─' * 50}{C_RESET}")
    print()
    
    # Config
    backend = config.get("backend", config.get("agent", {}).get("backend", "unknown"))
    print(f"  {C_BOLD}Configuration{C_RESET}")
    print(f"    Backend:   {backend}")
    print(f"    Workspace: {config.get('workspace', {}).get('path', workspace) if isinstance(config.get('workspace'), dict) else workspace}")
    print()
    
    # Stats
    stats_path = os.path.join(state_dir, "stats.json")
    if os.path.exists(stats_path):
        with open(stats_path) as f:
            stats = _json.load(f)
        print(f"  {C_BOLD}Research Stats{C_RESET}")
        print(f"    ⏱  Cycles:          {stats.get('total_cycles', 0)}")
        print(f"    📋 Tasks completed:  {stats.get('total_tasks_completed', 0)}")
        print(f"    🕐 Last run:         {str(stats.get('last_run', 'never'))[:16]}")
        print()
    
    # Knowledge
    kb_path = os.path.join(state_dir, "knowledge.json")
    if os.path.exists(kb_path):
        with open(kb_path) as f:
            kb = _json.load(f)
        entries = kb.get("entries", []) if isinstance(kb, dict) else kb
        cats = {}
        for e in entries:
            c = e.get("category", "other")
            cats[c] = cats.get(c, 0) + 1
        print(f"  {C_BOLD}Knowledge Base{C_RESET}")
        print(f"    📚 Total: {len(entries)} entries")
        if cats:
            parts = ", ".join(f"{v} {k}" for k, v in sorted(cats.items(), key=lambda x: -x[1]))
            print(f"    📂 Types: {parts}")
        print()
    
    # Tasks
    tq_path = os.path.join(state_dir, "task_queue.json")
    if os.path.exists(tq_path):
        with open(tq_path) as f:
            tasks = _json.load(f)
        pending = [t for t in tasks if t.get("status") == "pending"]
        completed = [t for t in tasks if t.get("status") == "completed"]
        in_progress = [t for t in tasks if t.get("status") == "in_progress"]
        pending.sort(key=lambda t: -t.get("priority", 0))
        
        print(f"  {C_BOLD}Task Queue{C_RESET}")
        print(f"    ⏳ Pending:    {len(pending)}")
        print(f"    ✅ Completed:  {len(completed)}")
        print()
        
        if pending:
            print(f"  {C_BOLD}Upcoming Tasks{C_RESET} (top 5)")
            for i, t in enumerate(pending[:5], 1):
                prio = t.get("priority", 0)
                title = t.get("title", "")[:55]
                print(f"    {i}. [{prio:2d}] {title}")
            print()
    
    # Recent activity
    journal_path = os.path.join(state_dir, "journal.jsonl")
    if os.path.exists(journal_path):
        entries_j = []
        with open(journal_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    decoder = _json.JSONDecoder()
                    pos = 0
                    while pos < len(line):
                        try:
                            data, end = decoder.raw_decode(line, pos)
                            entries_j.append(data)
                            pos = end
                        except _json.JSONDecodeError:
                            break
        
        if entries_j:
            print(f"  {C_BOLD}Recent Activity{C_RESET} (last 5)")
            for e in entries_j[-5:]:
                ts = e.get("timestamp", "")[:16].replace("T", " ")
                title = e.get("task_title", "")[:50]
                print(f"    [{ts}] {title}")
            print()
    
    # Heartbeat
    hb_path = os.path.join(state_dir, "heartbeat.json")
    if os.path.exists(hb_path):
        with open(hb_path) as f:
            hb = _json.load(f)
        status = hb.get("status", "unknown")
        last_hb = hb.get("last_heartbeat", "never")[:16]
        emoji = {"idle": "💤", "working": "⚡", "crashed": "💥"}.get(status, "❓")
        print(f"  {C_BOLD}Health{C_RESET}")
        print(f"    {emoji} Status:     {status}")
        print(f"    💓 Last heartbeat: {last_hb}")
        print()
    
    # Log file
    print(f"  {C_BOLD}Files{C_RESET}")
    print(f"    📄 Journal:  {journal_path}")
    print(f"    📄 Knowledge:{kb_path}")
    print(f"    📄 Tasks:    {tq_path}")
    print(f"    📄 Config:   {config_path}")
    print()
    
    # Usage hint
    backend = config.get("backend", config.get("agent", {}).get("backend", "hermes"))
    print(f"  {C_DIM}Tip: Open {backend} and say 'partner, what have you been doing?'{C_RESET}")
    print()

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
