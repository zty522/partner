"""Partner CLI — agent management subcommands.

Commands:
  partner agent list              — List all registered agents with capabilities
  partner agent info <name>       — Show manifest details for an agent
  partner agent register <path>   — Register a new agent from manifest file
  partner agent unregister <name> — Remove an agent registration
  partner agent health <name>     — Check if an agent is available
  partner agent call <name> <task> — Call an agent with a task (sync)
"""

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

from ..agents import AgentRegistry, AgentDispatcher, AgentTask, AgentManifest

from .common import (
    C_RESET, C_BOLD, C_DIM, C_CYAN, C_GREEN, C_YELLOW, C_RED,
    _cli_txt, _print_commands,
)


def _get_registry(args=None) -> AgentRegistry:
    """Create registry, optionally from workspace."""
    workspace = None
    if args and hasattr(args, 'workspace') and args.workspace:
        workspace = args.workspace
    return AgentRegistry(workspace=workspace)


def cmd_list(args):
    """List all registered agents with capabilities."""
    registry = _get_registry(args)
    agents = registry.list_agents()

    if not agents:
        print(f"\n  {C_YELLOW}No agents registered.{C_RESET}")
        print(f"  Register one with: {C_BOLD}partner agent register <manifest.json>{C_RESET}")
        print()
        return

    print()
    print(f"  {C_BOLD}{C_CYAN}Registered Agents ({len(agents)}){C_RESET}")
    print()
    for m in agents:
        caps = ", ".join(m.capabilities[:5])
        if len(m.capabilities) > 5:
            caps += f" {C_DIM}(+{len(m.capabilities)-5} more){C_RESET}"
        print(f"  {C_BOLD}{m.name}{C_RESET}  {C_DIM}v{m.version}{C_RESET}")
        print(f"    {m.description}")
        print(f"    {C_CYAN}{m.endpoint_type}{C_RESET}  capabilities: {caps}")
        print()
    _print_commands()


def cmd_info(args):
    """Show manifest details for an agent."""
    registry = _get_registry(args)
    manifest = registry.get_agent(args.name)

    if not manifest:
        print(f"\n  {C_RED}Agent '{args.name}' not found.{C_RESET}")
        print()
        return

    print()
    print(f"  {C_BOLD}{C_CYAN}Agent: {manifest.name}{C_RESET}  {C_DIM}v{manifest.version}{C_RESET}")
    print()
    print(f"  {C_BOLD}Name:{C_RESET}         {manifest.name}")
    print(f"  {C_BOLD}Version:{C_RESET}      {manifest.version}")
    print(f"  {C_BOLD}Description:{C_RESET}  {manifest.description}")
    print(f"  {C_BOLD}Endpoint:{C_RESET}     {manifest.endpoint_type}")
    print(f"  {C_BOLD}Timeout:{C_RESET}      {manifest.timeout}s")
    print()
    print(f"  {C_BOLD}Capabilities:{C_RESET}")
    for cap in manifest.capabilities:
        print(f"    - {cap}")
    print()
    print(f"  {C_BOLD}Input Formats:{C_RESET}  {', '.join(manifest.input_formats)}")
    print(f"  {C_BOLD}Output Formats:{C_RESET} {', '.join(manifest.output_formats)}")
    print()
    print(f"  {C_BOLD}Endpoint Config:{C_RESET}")
    for k, v in manifest.endpoint_config.items():
        print(f"    {k}: {v}")
    print()
    if manifest.health_check_cmd:
        print(f"  {C_BOLD}Health Check:{C_RESET} {manifest.health_check_cmd}")
    print()

    # Validate
    errors = manifest.validate()
    if errors:
        print(f"  {C_YELLOW}Validation errors:{C_RESET}")
        for e in errors:
            print(f"    - {e}")
        print()

    _print_commands()


def cmd_register(args):
    """Register a new agent from a manifest file."""
    manifest_path = os.path.expanduser(args.path)

    if not os.path.exists(manifest_path):
        print(f"\n  {C_RED}File not found: {manifest_path}{C_RESET}")
        print()
        return

    try:
        manifest = AgentManifest.from_file(manifest_path)
    except Exception as exc:
        print(f"\n  {C_RED}Failed to parse manifest: {exc}{C_RESET}")
        print()
        return

    errors = manifest.validate()
    if errors:
        print(f"\n  {C_RED}Manifest validation failed:{C_RESET}")
        for e in errors:
            print(f"    - {e}")
        print()
        return

    registry = _get_registry(args)
    success = registry.register_agent(manifest)
    if success:
        print(f"\n  {C_GREEN}Agent '{manifest.name}' registered successfully.{C_RESET}")
        print(f"  Manifest saved to ~/.partner/agents/{manifest.name}.json")
    else:
        print(f"\n  {C_RED}Failed to register agent.{C_RESET}")
    print()
    _print_commands()


def cmd_unregister(args):
    """Remove an agent registration."""
    registry = _get_registry(args)
    success = registry.unregister_agent(args.name)

    if success:
        print(f"\n  {C_GREEN}Agent '{args.name}' unregistered.{C_RESET}")
    else:
        print(f"\n  {C_YELLOW}Agent '{args.name}' not found in user registry.{C_RESET}")
        print("  (Built-in agents cannot be unregistered)")
    print()
    _print_commands()


def cmd_health(args):
    """Check if an agent is available."""
    registry = _get_registry(args)
    result = registry.health_check(args.name)

    status = result.get("status", "unknown")
    details = result.get("details", "")

    if status == "ok":
        status_display = f"{C_GREEN}✅ OK{C_RESET}"
    elif status == "unavailable":
        status_display = f"{C_RED}❌ Unavailable{C_RESET}"
    elif status == "timeout":
        status_display = f"{C_YELLOW}⏰ Timeout{C_RESET}"
    else:
        status_display = f"{C_YELLOW}❓ {status.upper()}{C_RESET}"

    print()
    print(f"  {C_BOLD}Agent Health: {args.name}{C_RESET}")
    print(f"  Status: {status_display}")
    if details:
        print(f"  Details: {details}")
    print()
    _print_commands()


def cmd_call(args):
    """Call an agent with a task (synchronous)."""
    registry = _get_registry(args)
    dispatcher = AgentDispatcher(registry)

    task_params = {}
    if args.param:
        for p in args.param:
            if "=" in p:
                k, v = p.split("=", 1)
                task_params[k] = v

    context = {}
    if args.workspace:
        context["working_dir"] = args.workspace

    task = AgentTask(
        agent=args.name,
        task=args.task_text,
        parameters=task_params,
        context=context,
    )

    import asyncio

    print(f"\n  {C_BOLD}Calling agent: {args.name}{C_RESET}")
    print(f"  Task: {args.task_text}")
    if task_params:
        print(f"  Parameters: {json.dumps(task_params, ensure_ascii=False)}")
    print()

    try:
        result = asyncio.run(dispatcher.dispatch(task))
    except Exception as exc:
        print(f"  {C_RED}Dispatch error: {exc}{C_RESET}")
        print()
        return

    duration = result.metadata.get("duration", "?")
    if result.status == "success":
        print(f"  {C_GREEN}✅ Success ({duration}s){C_RESET}")
    elif result.status == "partial":
        print(f"  {C_YELLOW}⚠️  Partial ({duration}s){C_RESET}")
    else:
        print(f"  {C_RED}❌ Error ({duration}s){C_RESET}")

    if result.error:
        print(f"  Error: {result.error}")

    if result.output:
        output_text = result.output.get("text", "")
        if output_text:
            print(f"\n  {C_BOLD}Output:{C_RESET}")
            for line in output_text.strip().splitlines():
                print(f"    {line}")
        output_files = result.output.get("files", [])
        if output_files:
            print(f"\n  {C_BOLD}Files:{C_RESET}")
            for f in output_files:
                print(f"    - {f}")

    print()
    _print_commands()


def register_subparser(sub):
    """Register the 'agent' subcommand tree."""
    p_agent = sub.add_parser("agent", help="管理 Agent（注册、查询、调用）")

    a_sub = p_agent.add_subparsers(dest="agent_action")
    a_sub.required = True

    # list
    p_list = a_sub.add_parser("list", help="列出所有已注册的 Agent")
    p_list.add_argument("--workspace", "-w", help="工作区路径")
    p_list.set_defaults(func=cmd_list)

    # info
    p_info = a_sub.add_parser("info", help="查看 Agent 清单详情")
    p_info.add_argument("name", help="Agent 名称")
    p_info.add_argument("--workspace", "-w", help="工作区路径")
    p_info.set_defaults(func=cmd_info)

    # register
    p_register = a_sub.add_parser("register", help="从 manifest 文件注册新 Agent")
    p_register.add_argument("path", help="manifest 文件路径 (.json)")
    p_register.add_argument("--workspace", "-w", help="工作区路径")
    p_register.set_defaults(func=cmd_register)

    # unregister
    p_unregister = a_sub.add_parser("unregister", help="移除 Agent 注册")
    p_unregister.add_argument("name", help="Agent 名称")
    p_unregister.add_argument("--workspace", "-w", help="工作区路径")
    p_unregister.set_defaults(func=cmd_unregister)

    # health
    p_health = a_sub.add_parser("health", help="检查 Agent 可用性")
    p_health.add_argument("name", help="Agent 名称")
    p_health.add_argument("--workspace", "-w", help="工作区路径")
    p_health.set_defaults(func=cmd_health)

    # call
    p_call = a_sub.add_parser("call", help="调用 Agent 执行任务")
    p_call.add_argument("name", help="Agent 名称")
    p_call.add_argument("task_text", help="任务描述")
    p_call.add_argument("--param", "-p", action="append",
                        help="参数键值对，如 --param model=default")
    p_call.add_argument("--workspace", "-w", help="工作区路径")
    p_call.set_defaults(func=cmd_call)

    return p_agent
