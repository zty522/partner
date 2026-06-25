"""Partner world-model — manage world model connection.

Commands:
    partner world-model status       Check world model connection status
    partner world-model test         Test world model connection
    partner world-model configure    Interactive configuration
"""

import json
import os
import sys

from ..config import resolve_partner_config_path, workspace_has_partner_config
from ..world_model.client import WorldModelClient, load_world_model_config
from .common import (
    C_RESET, C_BOLD, C_DIM, C_CYAN, C_GREEN, C_YELLOW, C_RED,
    _cli_txt, _print_commands,
    get_workspace, _resolve_runtime_workspace,
)


def _resolve_workspace(args) -> str | None:
    return getattr(args, "workspace", None) or _resolve_runtime_workspace(None) or get_workspace()


def _ensure_workspace(args) -> str | None:
    ws = _resolve_workspace(args)
    if not ws:
        print("❌ Partner 未配置，请先运行: partner setup")
    return ws


def _simple_health_check(endpoint: str, timeout: int = 5) -> dict:
    """Fallback health check using urllib (no httpx dependency required)."""
    import urllib.request
    try:
        health_url = endpoint.rstrip("/") + "/health"
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception:
        # Try root endpoint
        try:
            root_url = endpoint.rstrip("/") + "/"
            req = urllib.request.Request(root_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return {"available": True, "message": body[:100]}
        except Exception as e:
            return {"available": False, "error": str(e)}


def _do_health_check(config: dict) -> dict:
    """Perform health check, trying httpx first then falling back to urllib."""
    try:
        import asyncio
        client = WorldModelClient(config)
        return asyncio.run(client.health_check())
    except ImportError:
        return _simple_health_check(config.get("endpoint", "http://localhost:8100"), config.get("timeout", 5))
    except Exception:
        return _simple_health_check(config.get("endpoint", "http://localhost:8100"), config.get("timeout", 5))


def cmd_world_model_status(args):
    """Check world model connection status."""
    workspace = _ensure_workspace(args)
    if not workspace:
        return

    config = load_world_model_config(workspace)

    print()
    print(f"  {C_BOLD}{C_CYAN}World Model Status{C_RESET}")
    print()
    print(f"  Workspace: {workspace}")
    print()

    enabled = config.get("enabled", False)
    print(f"  Enabled:     {C_GREEN}Yes{C_RESET}" if enabled else f"  Enabled:     {C_RED}No{C_RESET}")

    if not enabled:
        print()
        print(f"  {C_DIM}World model is disabled. Enable with:{C_RESET}")
        print(f"    {C_DIM}partner world-model configure{C_RESET}")
        print()
        return

    endpoint = config.get("endpoint", "http://localhost:8100")
    provider = config.get("provider", "aether")
    timeout = config.get("timeout", 60)
    fallback = config.get("fallback_to_llm", True)
    mode = config.get("mode", "hybrid")

    print(f"  Provider:    {provider}")
    print(f"  Endpoint:    {endpoint}")
    print(f"  Timeout:     {timeout}s")
    print(f"  Fallback:    {C_GREEN}Yes{C_RESET}" if fallback else f"  Fallback:    {C_RED}No{C_RESET}")
    print(f"  Mode:        {mode}")

    # Try health check
    print()
    print(f"  {C_BOLD}Health Check:{C_RESET}")
    try:
        result = _do_health_check(config)
        available = result.get("available", False) or result.get("status") == "ok"
        if available:
            print(f"    Status:     {C_GREEN}Available{C_RESET}")
            if "aether" in result:
                aether = result["aether"]
                print(f"    AETHER:     {'Available' if aether.get('available') else 'Unavailable'}")
            if "llm" in result:
                llm_ = result["llm"]
                print(f"    LLM:        {'Available' if llm_.get('available') else 'Unavailable'}")
            print(f"    Backend:    {result.get('current_backend', 'unknown')}")
        else:
            print(f"    Status:     {C_RED}Unavailable{C_RESET}")
            print(f"    {C_DIM}Check that the world model server is running at {endpoint}{C_RESET}")
    except Exception as e:
        print(f"    Status:     {C_RED}Error{C_RESET}")
        print(f"    {e}")

    print()


def cmd_world_model_test(args):
    """Test world model connection by sending a test request."""
    workspace = _ensure_workspace(args)
    if not workspace:
        return

    config = load_world_model_config(workspace)

    if not config.get("enabled", False):
        print("❌ World model is disabled. Enable it first with: partner world-model configure")
        return

    endpoint = config.get("endpoint", "http://localhost:8100")
    print()
    print(f"  {C_BOLD}{C_CYAN}World Model Test{C_RESET}")
    print()
    print(f"  Endpoint: {endpoint}")
    print()

    # Test health endpoint first
    print(f"  {C_BOLD}1. Health Check:{C_RESET}")
    try:
        result = _do_health_check(config)
        available = result.get("available", False) or result.get("status") == "ok"
        if available:
            print(f"     {C_GREEN}✅ Health check passed{C_RESET}")
            print(f"     Backend: {result.get('current_backend', 'unknown')}")
        else:
            print(f"     {C_RED}❌ Health check failed{C_RESET}")
            error = result.get("error", "Server not reachable")
            print(f"     {C_DIM}Error: {error}{C_RESET}")
            print()
            _print_commands()
            return
    except Exception as e:
        print(f"     {C_RED}❌ Health check failed: {e}{C_RESET}")
        print()
        _print_commands()
        return

    # Test simulation endpoint
    print(f"  {C_BOLD}2. Simulation Test:{C_RESET}")
    try:
        import asyncio
        test_plan = [
            {"action": "analyze", "target": "test_system", "params": {}},
        ]
        test_state = {}
        client = WorldModelClient(config)
        result = asyncio.run(client.simulate_plan(test_plan, test_state))
        status = result.get("status", "unknown")
        if status == "fallback":
            reason = result.get("reason", "unknown")
            print(f"     {C_YELLOW}⚠ Simulation returned fallback: {reason}{C_RESET}")
        else:
            print(f"     {C_GREEN}✅ Simulation completed{C_RESET}")
            risk = result.get("total_risk_score", "N/A")
            print(f"     Risk Score: {risk}")
    except Exception as e:
        print(f"     {C_YELLOW}⚠ Simulation test not available: {e}{C_RESET}")
        # Connection worked (health check passed), simulation may not be supported

    print()
    _print_commands()


def cmd_world_model_configure(args):
    """Interactive configuration for world model."""
    workspace = _ensure_workspace(args)
    if not workspace:
        return

    config = load_world_model_config(workspace)

    print()
    print(f"  {C_BOLD}{C_CYAN}World Model Configuration{C_RESET}")
    print()

    current_enabled = config.get("enabled", False)
    enabled_str = input(f"  Enable world model? (yes/no) [{C_DIM}{'yes' if current_enabled else 'no'}{C_RESET}]: ").strip().lower()
    if enabled_str:
        config["enabled"] = enabled_str in ("yes", "y", "true")
    else:
        config["enabled"] = current_enabled

    if config["enabled"]:
        provider = input(f"  Provider [{C_DIM}{config.get('provider', 'aether')}{C_RESET}]: ").strip()
        if provider:
            config["provider"] = provider

        endpoint = input(f"  Endpoint URL [{C_DIM}{config.get('endpoint', 'http://localhost:8100')}{C_RESET}]: ").strip()
        if endpoint:
            config["endpoint"] = endpoint

        timeout_str = input(f"  Timeout (seconds) [{C_DIM}{config.get('timeout', 60)}{C_RESET}]: ").strip()
        if timeout_str:
            try:
                config["timeout"] = int(timeout_str)
            except ValueError:
                print(f"  {C_YELLOW}⚠ Invalid timeout, keeping default{C_RESET}")

        current_fallback = config.get("fallback_to_llm", True)
        fallback_str = input(f"  Fallback to LLM on failure? (yes/no) [{C_DIM}{'yes' if current_fallback else 'no'}{C_RESET}]: ").strip().lower()
        if fallback_str:
            config["fallback_to_llm"] = fallback_str in ("yes", "y", "true")
        else:
            config["fallback_to_llm"] = current_fallback

    # Save config as YAML at config/world_model.yaml
    wm_path = os.path.join(workspace, "config", "world_model.yaml")
    os.makedirs(os.path.dirname(wm_path), exist_ok=True)
    try:
        import yaml
        with open(wm_path, "w", encoding="utf-8") as f:
            yaml.dump({"world_model": config}, f, default_flow_style=False)
        print(f"\n  {C_GREEN}✅ World model config saved to: {wm_path}{C_RESET}")
    except ImportError:
        # Fallback: write JSON
        wm_json_path = os.path.join(workspace, "config", "world_model.json")
        try:
            with open(wm_json_path, "w", encoding="utf-8") as f:
                json.dump({"world_model": config}, f, indent=2, ensure_ascii=False)
            print(f"\n  {C_GREEN}✅ World model config saved to: {wm_json_path}{C_RESET}")
            print(f"  {C_YELLOW}⚠ yaml module not available, saved as JSON instead{C_RESET}")
        except Exception as e:
            print(f"\n  {C_RED}❌ Failed to save config: {e}{C_RESET}")

    print()


def register_subparser(sub):
    """Register the 'world-model' subcommand family."""
    p = sub.add_parser(
        "world-model",
        help=_cli_txt("管理世界模型连接", "Manage world model connection"),
        aliases=["wm"],
    )
    wm_sub = p.add_subparsers(dest="world_model_action")
    wm_sub.required = True

    p_status = wm_sub.add_parser("status", help=_cli_txt("检查连接状态", "Check connection status"))
    p_status.add_argument("--workspace", "-w", help=_cli_txt("工作区路径", "Workspace path"))
    p_status.set_defaults(func=cmd_world_model_status)

    p_test = wm_sub.add_parser("test", help=_cli_txt("测试连接", "Test connection"))
    p_test.add_argument("--workspace", "-w", help=_cli_txt("工作区路径", "Workspace path"))
    p_test.set_defaults(func=cmd_world_model_test)

    p_configure = wm_sub.add_parser("configure", help=_cli_txt("交互式配置", "Interactive configuration"))
    p_configure.add_argument("--workspace", "-w", help=_cli_txt("工作区路径", "Workspace path"))
    p_configure.set_defaults(func=cmd_world_model_configure)
