"""CLI - the command-line interface for Partner.

Usage:
    partner start [--workspace PATH]     Start Partner
    partner chat                         Talk to Partner
    partner status                       Quick status check
    partner task add "title" "desc"      Add a research task
    partner task list                    List tasks
    partner knowledge search "query"     Search knowledge base
    partner run                          Run one research cycle
"""

import argparse
import os
import sys
import json

from .config import PartnerConfig, WorkspaceConfig, AgentConfig, SchedulerConfig
from .core import Partner


def get_partner(args=None) -> Partner:
    """Initialize Partner from args or default config."""
    workspace = getattr(args, 'workspace', None) or os.environ.get(
        'PARTNER_WORKSPACE', os.path.join(os.getcwd(), 'partner_workspace')
    )
    
    config_path = os.path.join(workspace, "partner_config.json")
    if os.path.exists(config_path):
        config = PartnerConfig.load(config_path)
    else:
        config = PartnerConfig(
            workspace=WorkspaceConfig(path=workspace),
            agent=AgentConfig(backend=getattr(args, 'backend', 'hermes')),
            scheduler=SchedulerConfig(
                interval_minutes=getattr(args, 'interval', 30),
            ),
        )
    
    return Partner(config)


def cmd_start(args):
    """Start Partner."""
    p = get_partner(args)
    p.start()
    
    # If --once flag, run one cycle and exit
    if getattr(args, 'once', False):
        result = p.run_cycle()
        if result:
            print(f"\n📋 Cycle result:\n{result}")
        else:
            print("ℹ️  No pending tasks.")
        return
    
    # Otherwise, show status and exit (background mode via cron)
    print(p.status())


def cmd_chat(args):
    """Interactive chat with Partner."""
    p = get_partner(args)
    
    if args.message:
        # Single message mode
        print(p.chat(args.message))
        return
    
    # Interactive mode
    print("🤝 Partner Chat (type 'exit' to quit)\n")
    print(p.chat("帮助"))
    print()
    
    while True:
        try:
            msg = input("你: ").strip()
            if msg.lower() in ('exit', 'quit', 'q', '退出'):
                print("👋 再见！我会继续在后台研究。")
                break
            if not msg:
                continue
            response = p.chat(msg)
            print(f"\nPartner: {response}\n")
        except (KeyboardInterrupt, EOFError):
            print("\n👋 再见！")
            break


def cmd_status(args):
    """Quick status check."""
    p = get_partner(args)
    print(p.status())


def cmd_task(args):
    """Task management."""
    p = get_partner(args)
    
    if args.task_action == 'add':
        task_id = p.add_task(args.title, args.description or "", priority=args.priority)
        print(f"✅ Task added: {task_id}")
    elif args.task_action == 'list':
        stats = p.task_queue.stats()
        print(f"📋 Tasks: {stats['total']} total")
        for status, count in stats.get("by_status", {}).items():
            print(f"  {status}: {count}")
        
        pending = [t for t in p.task_queue.tasks if t.status == "pending"]
        pending.sort(key=lambda t: -t.priority)
        if pending:
            print(f"\n⏳ Pending (top 10):")
            for t in pending[:10]:
                print(f"  [{t.priority:2d}] {t.title}")


def cmd_knowledge(args):
    """Knowledge base operations."""
    p = get_partner(args)
    
    if args.kb_action == 'search':
        results = p.knowledge.search(args.query, top_k=5)
        if results:
            for i, e in enumerate(results, 1):
                print(f"{i}. [{e.confidence}] {e.title}")
                print(f"   {e.content[:200]}...")
                print()
        else:
            print("No results found.")
    elif args.kb_action == 'stats':
        stats = p.knowledge.stats()
        print(f"📚 Knowledge: {stats['total']} entries")
        for cat, count in stats.get("by_category", {}).items():
            print(f"  {cat}: {count}")


def cmd_run(args):
    """Run one research cycle."""
    p = get_partner(args)
    result = p.run_cycle()
    if result:
        print(f"📋 Cycle completed:\n{result}")
    else:
        print("ℹ️  No pending tasks.")


def cmd_init(args):
    """Initialize a new Partner workspace."""
    workspace = args.workspace or os.path.join(os.getcwd(), 'partner_workspace')
    os.makedirs(workspace, exist_ok=True)
    
    config = PartnerConfig(
        workspace=WorkspaceConfig(path=workspace),
        agent=AgentConfig(backend=args.backend),
        scheduler=SchedulerConfig(interval_minutes=args.interval),
    )
    
    config_path = os.path.join(workspace, "partner_config.json")
    config.save(config_path)
    
    # Create directory structure
    for d in ["state", "knowledge", "ideas", "logs"]:
        os.makedirs(os.path.join(workspace, d), exist_ok=True)
    
    print(f"✅ Partner workspace initialized at: {workspace}")
    print(f"   Config: {config_path}")
    print(f"   Backend: {args.backend}")
    print(f"\nNext steps:")
    print(f"  partner start --workspace {workspace}")
    print(f"  partner chat --workspace {workspace}")


def main():
    parser = argparse.ArgumentParser(
        prog='partner',
        description='Partner - Your AI Research Companion 🤝',
    )
    parser.add_argument('--workspace', '-w', help='Workspace path')
    parser.add_argument('--backend', '-b', default='hermes', 
                       choices=['hermes', 'direct'],
                       help='Agent backend (default: hermes)')
    parser.add_argument('--interval', '-i', type=int, default=30,
                       help='Research cycle interval in minutes')
    
    sub = parser.add_subparsers(dest='command')
    
    # start
    p_start = sub.add_parser('start', help='Start Partner')
    p_start.add_argument('--once', action='store_true', help='Run one cycle and exit')
    p_start.add_argument('--workspace', '-w', help='Workspace path')
    p_start.set_defaults(func=cmd_start)
    
    # chat
    p_chat = sub.add_parser('chat', help='Talk to Partner')
    p_chat.add_argument('message', nargs='?', help='Message (omit for interactive mode)')
    p_chat.add_argument('--workspace', '-w', help='Workspace path')
    p_chat.set_defaults(func=cmd_chat)
    
    # status
    p_status = sub.add_parser('status', help='Quick status check')
    p_status.add_argument('--workspace', '-w', help='Workspace path')
    p_status.set_defaults(func=cmd_status)
    
    # task
    p_task = sub.add_parser('task', help='Task management')
    p_task.add_argument('--workspace', '-w', help='Workspace path')
    task_sub = p_task.add_subparsers(dest='task_action')
    
    p_task_add = task_sub.add_parser('add', help='Add a task')
    p_task_add.add_argument('title', help='Task title')
    p_task_add.add_argument('description', nargs='?', default='', help='Task description')
    p_task_add.add_argument('--priority', '-p', type=int, default=5, help='Priority (1-10)')
    
    p_task_list = task_sub.add_parser('list', help='List tasks')
    p_task.set_defaults(func=cmd_task)
    
    # knowledge
    p_kb = sub.add_parser('knowledge', help='Knowledge base')
    p_kb.add_argument('--workspace', '-w', help='Workspace path')
    kb_sub = p_kb.add_subparsers(dest='kb_action')
    
    p_kb_search = kb_sub.add_parser('search', help='Search knowledge')
    p_kb_search.add_argument('query', help='Search query')
    
    p_kb_stats = kb_sub.add_parser('stats', help='Knowledge stats')
    p_kb.set_defaults(func=cmd_knowledge)
    
    # run
    p_run = sub.add_parser('run', help='Run one research cycle')
    p_run.add_argument('--workspace', '-w', help='Workspace path')
    p_run.set_defaults(func=cmd_run)
    
    # init
    p_init = sub.add_parser('init', help='Initialize workspace')
    p_init.add_argument('--workspace', '-w', help='Workspace path')
    p_init.add_argument('--backend', '-b', default='hermes', choices=['hermes', 'direct'])
    p_init.add_argument('--interval', '-i', type=int, default=30)
    p_init.set_defaults(func=cmd_init)
    
    args = parser.parse_args()
    
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
