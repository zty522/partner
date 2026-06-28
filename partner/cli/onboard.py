"""Partner onboard — a guided setup wizard.

Usage:
    partner onboard                     Full interactive wizard
    partner onboard --quick             Use all defaults
    partner onboard --skip llm          Skip certain steps
    partner onboard --step 3            Start from step 3
"""

import json
import os
import shutil
import subprocess
import sys

from .. import i18n
from ..state.config import (
    load_partner_config_data,
    resolve_partner_config_path,
    save_partner_config_data,
    workspace_has_partner_config,
)
from ..monitoring.instance_root import resolve_instance_workspace, resolve_partner_root
from ..workspace.workspace_layout import ensure_instance_layout
from .common import (
    C_RESET, C_BOLD, C_DIM, C_CYAN, C_GREEN, C_YELLOW, C_RED,
    _cli_txt, _print_kv, _fmt_bool, _fmt_optional,
    get_workspace, _resolve_runtime_workspace, _launch_instance,
)


def _input(prompt: str, default: str = "") -> str:
    """Prompt user with a default value shown in dim."""
    if default:
        full = f"{prompt} [{C_DIM}{default}{C_RESET}]: "
    else:
        full = f"{prompt}: "
    try:
        val = input(full).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val if val else default


def _print_step(num: int, title: str):
    print()
    print(f"  {C_BOLD}{C_CYAN}Step {num}: {title}{C_RESET}")
    print(f"  {C_DIM}{'─' * 48}{C_RESET}")


def _save_workspace_config(workspace: str, config: dict):
    """Save partner_config.json and qq_config.json to workspace."""
    os.makedirs(os.path.join(workspace, "config"), exist_ok=True)
    save_partner_config_data(workspace, config)

    qq = config.get("qq_bot", {})
    if qq.get("app_id") and qq.get("app_secret"):
        qq_config_path = os.path.join(workspace, "config", "qq_config.json")
        qq_data = {
            "app_id": qq["app_id"],
            "app_secret": qq["app_secret"],
            "token": qq.get("token", ""),
        }
        with open(qq_config_path, "w", encoding="utf-8") as f:
            json.dump(qq_data, f, indent=2, ensure_ascii=False)
        print(f"  {C_GREEN}✅ QQ config saved to: {qq_config_path}{C_RESET}")

    # Save world model config
    wm = config.get("world_model", {})
    if wm:
        wm_path = os.path.join(workspace, "config", "world_model.yaml")
        try:
            import yaml
            with open(wm_path, "w", encoding="utf-8") as f:
                yaml.dump({"world_model": wm}, f, default_flow_style=False)
            print(f"  {C_GREEN}✅ World model config saved to: {wm_path}{C_RESET}")
        except ImportError:
            print(f"  {C_YELLOW}⚠ yaml not available, skipping world_model.yaml{C_RESET}")


def detect_environment():
    """Detect and print environment information."""
    print()
    print(f"  {C_BOLD}{C_CYAN}Environment Detection{C_RESET}")
    print(f"  {C_DIM}{'─' * 48}{C_RESET}")

    # Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info.major == 3 and sys.version_info.minor >= 10
    print(f"    Python:    {C_GREEN}{py_ver}{C_RESET}" if py_ok else f"    Python:    {C_RED}{py_ver} (need 3.10+){C_RESET}")

    # Hermes detection
    hermes_path = shutil.which("hermes")
    if hermes_path:
        try:
            r = subprocess.run([hermes_path, "--version"], capture_output=True, text=True, timeout=10)
            hermes_ver = r.stdout.strip() or r.stderr.strip() or "(unknown version)"
            print(f"    Hermes:    {C_GREEN}Found{C_RESET} at {hermes_path} ({hermes_ver})")
        except Exception:
            print(f"    Hermes:    {C_GREEN}Found{C_RESET} at {hermes_path}")
    else:
        print(f"    Hermes:    {C_YELLOW}Not found{C_RESET} (install: pip install hermes-agent)")

    # OpenClaw detection
    oc_path = shutil.which("openclaw")
    if oc_path:
        try:
            r = subprocess.run([oc_path, "--version"], capture_output=True, text=True, timeout=10)
            oc_ver = r.stdout.strip() or r.stderr.strip() or "(unknown version)"
            print(f"    OpenClaw:  {C_GREEN}Found{C_RESET} at {oc_path} ({oc_ver})")
        except Exception:
            print(f"    OpenClaw:  {C_GREEN}Found{C_RESET} at {oc_path}")
    else:
        print(f"    OpenClaw:  {C_YELLOW}Not found{C_RESET}")

    # Git
    git_path = shutil.which("git")
    if git_path:
        try:
            r = subprocess.run([git_path, "--version"], capture_output=True, text=True, timeout=10)
            print(f"    Git:       {C_GREEN}{r.stdout.strip()}{C_RESET}")
        except Exception:
            print(f"    Git:       {C_GREEN}Found{C_RESET}")
    else:
        print(f"    Git:       {C_YELLOW}Not found{C_RESET}")

    # pip
    pip_path = shutil.which("pip") or shutil.which("pip3")
    print(f"    pip:       {C_GREEN}Found{C_RESET}" if pip_path else f"    pip:       {C_YELLOW}Not found{C_RESET}")

    # Workspace detection
    ws = get_workspace()
    if ws and os.path.isdir(ws):
        print(f"    Workspace: {C_GREEN}{ws}{C_RESET}")
    else:
        print(f"    Workspace: {C_YELLOW}Not configured{C_RESET}")

    print()


def step_select_workspace(config: dict, skip: bool = False, quick: bool = False) -> str:
    """Step 1: Select or create workspace."""
    if skip:
        ws = config.get("workspace", {}).get("path") or get_workspace()
        if ws:
            print(f"  {C_DIM}Using workspace: {ws}{C_RESET}")
            return ws
        return os.path.expanduser("~/partner_workspace")

    _print_step(1, _cli_txt("选择/创建工作区", "Select/Create Workspace"))

    # List existing workspaces
    partner_root = str(resolve_partner_root())
    instances_dir = os.path.join(partner_root, "instances")
    existing = []
    if os.path.isdir(instances_dir):
        for entry in sorted(os.listdir(instances_dir)):
            inst_path = os.path.join(instances_dir, entry)
            if os.path.isdir(inst_path) and workspace_has_partner_config(inst_path):
                existing.append(entry)

    if existing:
        print(f"  {C_DIM}Existing workspaces:{C_RESET}")
        for idx, name in enumerate(existing, 1):
            print(f"    {idx}. {name}")
        print()
        choice = _input(
            _cli_txt("选择编号，或输入新名称创建", "Select number, or enter a new name to create"),
            default="1" if existing else "default",
        )
        if choice.isdigit() and 1 <= int(choice) <= len(existing):
            ws_name = existing[int(choice) - 1]
        else:
            ws_name = choice.strip() or "default"
        if ws_name in existing:
            workspace = str(resolve_instance_workspace(ws_name))
        else:
            workspace = str(resolve_instance_workspace(ws_name))
            ensure_instance_layout(workspace)
            print(f"  {C_GREEN}✅ Created workspace: {workspace}{C_RESET}")
    else:
        ws_name = _input(
            _cli_txt("新工作区名称", "New workspace name"),
            default="default",
        )
        workspace = str(resolve_instance_workspace(ws_name.strip() or "default"))
        ensure_instance_layout(workspace)
        print(f"  {C_GREEN}✅ Created workspace: {workspace}{C_RESET}")

    config.setdefault("workspace", {})["path"] = workspace
    return workspace


def step_configure_agent(config: dict, skip: bool = False, quick: bool = False):
    """Step 2: Configure agent backend."""
    if skip:
        return

    _print_step(2, _cli_txt("配置 Agent", "Configure Agent"))

    agent = config.setdefault("agent", {})
    current_backend = agent.get("backend", "hermes")

    backend = _input(
        _cli_txt("Agent 后端 (hermes/openclaw)", "Agent backend (hermes/openclaw)"),
        default=current_backend,
    )
    backend = backend.strip().lower()
    if backend not in ("hermes", "openclaw"):
        backend = "hermes"

    agent["backend"] = backend

    if backend == "hermes":
        hermes_path = shutil.which("hermes")
        if not hermes_path:
            print(f"  {C_YELLOW}⚠ Hermes not found in PATH{C_RESET}")
            install = _input(
                _cli_txt("自动安装 Hermes? (yes/no)", "Auto-install Hermes? (yes/no)"),
                default="yes",
            )
            if install.strip().lower() in ("yes", "y", ""):
                print(f"  {C_DIM}Installing hermes-agent...{C_RESET}")
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "hermes-agent"],
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode == 0:
                    hermes_path = shutil.which("hermes") or "hermes"
                    print(f"  {C_GREEN}✅ Hermes installed{C_RESET}")
                else:
                    print(f"  {C_RED}❌ Install failed: {r.stderr[:200]}{C_RESET}")
                    print(f"  {C_YELLOW}Manually run: pip install hermes-agent{C_RESET}")
            else:
                print(f"  {C_YELLOW}⚠ Skipping Hermes install. Set path manually.{C_RESET}")
        else:
            try:
                r = subprocess.run([hermes_path, "--version"], capture_output=True, text=True, timeout=10)
                ver = r.stdout.strip() or r.stderr.strip() or "(unknown)"
                print(f"  {C_GREEN}✅ Hermes found: {hermes_path} ({ver}){C_RESET}")
            except Exception:
                print(f"  {C_GREEN}✅ Hermes found: {hermes_path}{C_RESET}")

        path = _input(
            _cli_txt("Hermes 可执行文件路径", "Hermes executable path"),
            default=hermes_path or "hermes",
        )
        if path:
            agent["executable_path"] = path

    elif backend == "openclaw":
        oc_path = shutil.which("openclaw")
        if not oc_path:
            print(f"  {C_YELLOW}⚠ OpenClaw not found in PATH{C_RESET}")
        else:
            print(f"  {C_GREEN}✅ OpenClaw found: {oc_path}{C_RESET}")
        path = _input(
            _cli_txt("OpenClaw 可执行文件路径", "OpenClaw executable path"),
            default=oc_path or "openclaw",
        )
        if path:
            agent["executable_path"] = path

    print(f"  {C_GREEN}✅ Agent configured: {backend}{C_RESET}")


def _test_llm_connection(config: dict) -> bool:
    """Try a simple HTTP request to the LLM API to verify connectivity."""
    provider = config.get("provider", "")
    base_url = config.get("base_url", "")
    api_key = config.get("api_key", "")
    model = config.get("model", "")

    if not api_key or not base_url:
        return False

    try:
        import urllib.request
        import json as _json

        if provider == "openai" or "openai" in base_url:
            url = base_url.rstrip("/") + "/chat/completions"
            data = _json.dumps({
                "model": model or "gpt-4o",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 10,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {api_key}")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = _json.loads(resp.read())
            return "choices" in result
        elif provider == "anthropic" or "anthropic" in base_url:
            url = base_url.rstrip("/") + "/messages"
            data = _json.dumps({
                "model": model or "claude-3-haiku-20240307",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 10,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("x-api-key", api_key)
            req.add_header("anthropic-version", "2023-06-01")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = _json.loads(resp.read())
            return "content" in result
        else:
            # Generic test
            url = base_url.rstrip("/") + "/chat/completions"
            data = _json.dumps({
                "model": model or "gpt-4o",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 10,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {api_key}")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return True
    except Exception:
        return False


def step_configure_llm(config: dict, skip: bool = False, quick: bool = False):
    """Step 3: Configure LLM API."""
    if skip:
        return

    _print_step(3, _cli_txt("配置 LLM API", "Configure LLM API"))

    llm = config.setdefault("llm", {})

    providers = {
        "1": ("openai", "https://api.openai.com/v1"),
        "2": ("deepseek", "https://api.deepseek.com"),
        "3": ("anthropic", "https://api.anthropic.com"),
        "4": ("custom", ""),
    }

    print(f"  {C_DIM}Select provider:{C_RESET}")
    for key, (name, url) in providers.items():
        print(f"    {key}. {name} ({url})")

    current_provider = llm.get("provider", "openai")
    current_key = "1"
    for k, (name, _) in providers.items():
        if name == current_provider:
            current_key = k
            break

    choice = _input(
        _cli_txt("选择编号", "Select number"),
        default=current_key,
    )
    provider_name, default_url = providers.get(choice, ("custom", ""))

    llm["provider"] = provider_name
    if default_url:
        llm["base_url"] = _input(
            _cli_txt("API 地址", "API Base URL"),
            default=llm.get("base_url", default_url),
        )
    else:
        llm["base_url"] = _input(
            _cli_txt("API 地址", "API Base URL"),
            default=llm.get("base_url", ""),
        )

    llm["api_key"] = _input(
        _cli_txt("API Key", "API Key"),
        default=llm.get("api_key", ""),
    )

    llm["model"] = _input(
        _cli_txt("模型名称", "Model name"),
        default=llm.get("model", "gpt-4o"),
    )

    print(f"  {C_GREEN}✅ LLM configured: {llm.get('provider')} / {llm.get('model')}{C_RESET}")

    # Quick connectivity test
    if llm.get("api_key"):
        test = _input(
            _cli_txt("测试 API 连接? (yes/no)", "Test API connection? (yes/no)"),
            default="no",
        )
        if test.strip().lower() in ("yes", "y", ""):
            print(f"  {C_DIM}Testing connection...{C_RESET}")
            ok = _test_llm_connection(llm)
            if ok:
                print(f"  {C_GREEN}✅ API connection successful{C_RESET}")
            else:
                print(f"  {C_YELLOW}⚠ API connection failed (network/proxy issue or invalid key){C_RESET}")
                print(f"     You can proceed anyway and fix later.")


def step_configure_qq(config: dict, skip: bool = False, quick: bool = False):
    """Step 4: Configure QQ Bot."""
    if skip:
        return

    _print_step(4, _cli_txt("配置 QQ 机器人", "Configure QQ Bot"))

    qq = config.setdefault("qq_bot", {})

    print(f"  {C_DIM}QQ 机器人配置 (可选){C_RESET}")
    app_id = _input("App ID", default=qq.get("app_id", ""))
    app_secret = _input("App Secret", default=qq.get("app_secret", ""))
    token = _input("Token", default=qq.get("token", ""))

    if app_id:
        qq["app_id"] = app_id
        qq["app_secret"] = app_secret
        qq["token"] = token
        print(f"  {C_GREEN}✅ QQ Bot configured{C_RESET}")
        # Verify config can be written
        ws = config.get("workspace", {}).get("path", "")
        if ws:
            test_path = os.path.join(ws, "config", "qq_config.json")
            os.makedirs(os.path.dirname(test_path), exist_ok=True)
            try:
                with open(test_path, "w", encoding="utf-8") as f:
                    json.dump({"test": True}, f)
                os.remove(test_path)
                print(f"  {C_GREEN}✅ Config directory writable{C_RESET}")
            except Exception as e:
                print(f"  {C_RED}❌ Config directory not writable: {e}{C_RESET}")
    else:
        print(f"  {C_YELLOW}⚠ QQ Bot skipped (no App ID){C_RESET}")


def _test_wm_connection(endpoint: str, timeout: int = 10) -> tuple[bool, str]:
    """Test world model connection with a simple HTTP GET."""
    import urllib.request
    try:
        health_url = endpoint.rstrip("/") + "/health"
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return (True, body[:200])
    except Exception as e:
        # Try root endpoint
        try:
            req = urllib.request.Request(endpoint.rstrip("/") + "/", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return (True, body[:200])
        except Exception as e2:
            return (False, str(e2))


def step_configure_world_model(config: dict, skip: bool = False, quick: bool = False):
    """Step 5: Configure World Model."""
    if skip:
        return

    _print_step(5, _cli_txt("配置世界模型", "Configure World Model"))

    wm = config.setdefault("world_model", {})

    enabled_default = "yes" if wm.get("enabled", False) else "no"
    enabled_str = _input(
        _cli_txt("启用世界模型? (yes/no)", "Enable world model? (yes/no)"),
        default=enabled_default,
    )
    wm["enabled"] = enabled_str.strip().lower() in ("yes", "y", "true")

    if wm["enabled"]:
        wm["provider"] = _input(
            _cli_txt("World Model 提供商", "World Model provider"),
            default=wm.get("provider", "aether"),
        )
        wm["endpoint"] = _input(
            _cli_txt("World Model 端点", "World Model endpoint"),
            default=wm.get("endpoint", "http://localhost:8100"),
        )
        wm["timeout"] = int(_input(
            _cli_txt("超时时间 (秒)", "Timeout (seconds)"),
            default=str(wm.get("timeout", 60)),
        ) or 60)
        fallback_default = "yes" if wm.get("fallback_to_llm", True) else "no"
        fallback_str = _input(
            _cli_txt("回退到 LLM?", "Fallback to LLM?"),
            default=fallback_default,
        )
        wm["fallback_to_llm"] = fallback_str.strip().lower() in ("yes", "y", "true")
        print(f"  {C_GREEN}✅ World model configured{C_RESET}")

        # Test connection
        test = _input(
            _cli_txt("测试连接? (yes/no)", "Test connection? (yes/no)"),
            default="yes",
        )
        if test.strip().lower() in ("yes", "y", ""):
            print(f"  {C_DIM}Testing connection to {wm['endpoint']}...{C_RESET}")
            ok, msg = _test_wm_connection(wm["endpoint"], timeout=10)
            if ok:
                print(f"  {C_GREEN}✅ Connection successful: {msg[:100]}{C_RESET}")
            else:
                print(f"  {C_YELLOW}⚠ Connection failed: {msg}{C_RESET}")
                print(f"     You can proceed anyway and configure later.")
    else:
        print(f"  {C_YELLOW}⚠ World model disabled{C_RESET}")


def step_summary(config: dict):
    """Print configuration summary."""
    print()
    print(f"  {C_BOLD}{C_CYAN}{_cli_txt('配置总结', 'Configuration Summary')}{C_RESET}")
    print(f"  {C_DIM}{'─' * 48}{C_RESET}")

    ws = config.get("workspace", {}).get("path", "")
    if ws:
        print(f"    {C_BOLD}Workspace:{C_RESET} {ws}")

    agent = config.get("agent", {})
    if agent:
        print(f"    {C_BOLD}Agent:{C_RESET} {agent.get('backend', 'hermes')}")
        if agent.get("executable_path"):
            print(f"    {C_BOLD}  Path:{C_RESET} {agent['executable_path']}")

    llm = config.get("llm", {})
    if llm.get("provider"):
        print(f"    {C_BOLD}LLM:{C_RESET} {llm.get('provider')} / {llm.get('model', '?')}")
        if llm.get("base_url"):
            print(f"    {C_BOLD}  URL:{C_RESET} {llm['base_url']}")

    qq = config.get("qq_bot", {})
    if qq.get("app_id"):
        print(f"    {C_BOLD}QQ Bot:{C_RESET} App ID: {qq['app_id'][:8]}...")

    wm = config.get("world_model", {})
    if wm:
        print(f"    {C_BOLD}World Model:{C_RESET} {'Enabled' if wm.get('enabled') else 'Disabled'}")

    print()


def cmd_onboard(args):
    """Run the guided setup wizard."""
    from ..state.setup import find_workspace

    quick = getattr(args, "quick", False)
    skip_steps_raw = getattr(args, "skip", "") or ""
    skip_steps = set(s.strip().lower() for s in skip_steps_raw.split(",") if s.strip())
    start_step = getattr(args, "step", 0)
    try:
        start_step = int(start_step) if start_step else 1
    except (ValueError, TypeError):
        start_step = 1

    # Determine workspace early or create config skeleton
    existing_workspace = get_workspace() or find_workspace()
    config = {}
    if existing_workspace and workspace_has_partner_config(existing_workspace):
        try:
            config = load_partner_config_data(existing_workspace)
        except Exception:
            config = {}

    # Run environment detection if not in quick mode
    if not quick:
        detect_environment()

    steps = [
        ("workspace", step_select_workspace, _cli_txt("选择/创建工作区", "Select/Create Workspace")),
        ("agent", step_configure_agent, _cli_txt("配置 Agent", "Configure Agent")),
        ("llm", step_configure_llm, _cli_txt("配置 LLM API", "Configure LLM API")),
        ("qq", step_configure_qq, _cli_txt("配置 QQ 机器人", "Configure QQ Bot")),
        ("world_model", step_configure_world_model, _cli_txt("配置世界模型", "Configure World Model")),
    ]

    print()
    print(f"  {C_BOLD}{C_CYAN}🚀 Partner 设置向导 / Setup Wizard{C_RESET}")
    print(f"  {C_DIM}This will guide you through configuring Partner.{C_RESET}")
    print()

    workspace = None

    for step_name, step_fn, step_title in steps:
        if start_step > 0:
            step_num = next(i + 1 for i, (n, _, _) in enumerate(steps) if n == step_name)
            if step_num < start_step:
                # Skip but still run workspace step to get workspace var
                if step_name == "workspace" and start_step > 1:
                    ws = config.get("workspace", {}).get("path") or get_workspace()
                    if not ws:
                        ws = os.path.expanduser("~/partner_workspace")
                    config.setdefault("workspace", {})["path"] = ws
                    workspace = ws
                continue

        should_skip = step_name in skip_steps or (quick and step_name != "workspace")
        step_fn(config, skip=should_skip, quick=quick)
        if step_name == "workspace":
            workspace = config.get("workspace", {}).get("path")
            if not workspace:
                workspace = os.path.expanduser("~/partner_workspace")

    step_summary(config)

    confirm = _input(
        _cli_txt("保存配置? (yes/no)", "Save configuration? (yes/no)"),
        default="yes",
    )
    if confirm.strip().lower() in ("yes", "y", ""):
        if workspace:
            # Ensure workspace has proper layout
            ensure_instance_layout(workspace)
            # Generate proper config files
            config_dir = os.path.join(workspace, "config")
            os.makedirs(config_dir, exist_ok=True)
            _save_workspace_config(workspace, config)

            # Generate world_model.yaml if enabled
            wm = config.get("world_model", {})
            if wm:
                wm_path = os.path.join(config_dir, "world_model.yaml")
                try:
                    import yaml
                    with open(wm_path, "w", encoding="utf-8") as f:
                        yaml.dump({"world_model": wm}, f, default_flow_style=False)
                except ImportError:
                    pass

            print(f"\n  {C_GREEN}✅ 配置已保存到: {workspace}{C_RESET}")

            # Offer to start the instance
            start_now = _input(
                _cli_txt("立即启动实例? (yes/no)", "Start instance now? (yes/no)"),
                default="no",
            )
            if start_now.strip().lower() in ("yes", "y", ""):
                instance_id = os.path.basename(os.path.normpath(workspace))
                print(f"  {C_DIM}Starting instance {instance_id}...{C_RESET}")
                proc = _launch_instance(instance_id, workspace)
                if proc:
                    print(f"  {C_GREEN}✅ Instance started (PID: {proc.pid}){C_RESET}")
                    print(f"     {C_DIM}Logs: {os.path.join(workspace, 'state', 'logs', f'instance_{instance_id}.log')}{C_RESET}")
                else:
                    print(f"  {C_RED}❌ Failed to start instance{C_RESET}")
                    print(f"     {C_DIM}Start manually: python -m partner --instance-id {instance_id} --workspace {workspace}{C_RESET}")
            else:
                print()
                print(f"  {C_DIM}Next steps:{C_RESET}")
                print(f"    {C_DIM}1. Start the instance:{C_RESET}")
                print(f"       partner gateway start --workspace {workspace}")
                print(f"    {C_DIM}2. Check status:{C_RESET}")
                print(f"       partner gateway status --workspace {workspace}")
                print(f"    {C_DIM}3. Open TUI:{C_RESET}")
                print(f"       partner tui")
            print()
    else:
        print(f"\n  {C_YELLOW}⚠ 配置未保存{C_RESET}")
        print()


def register_subparser(sub):
    """Register the 'onboard' subcommand on the given subparsers group."""
    p = sub.add_parser("onboard", help=_cli_txt("引导式设置向导", "Guided setup wizard"))
    p.add_argument("--quick", action="store_true", help=_cli_txt("快速模式，使用默认值", "Quick mode, use defaults"))
    p.add_argument("--skip", default="", help=_cli_txt("跳过步骤 (逗号分隔): workspace,agent,llm,qq,world_model", "Skip steps (comma-sep): workspace,agent,llm,qq,world_model"))
    p.add_argument("--step", type=int, default=0, help=_cli_txt("从指定步骤开始 (1-5)", "Start from specific step (1-5)"))
    p.set_defaults(func=cmd_onboard)
