"""Partner CLI — modular argument parser.

Preserves ALL existing subcommands and adds new ones (onboard, gateway, world-model, tui).
Delegates each command to its module.
"""

import argparse
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
from .common import (
    C_RESET, C_BOLD, C_DIM, C_CYAN, C_GREEN, C_YELLOW, C_RED,
    _cli_txt, _fmt_bool, _fmt_optional, _print_kv, _print_commands, _print_help_menu,
    _resolve_qq_config,
    _resolve_runtime_workspace, _root_workspace_if_different, get_workspace,
    _load_manager_module, _get_default_instance_id, _save_default_instance_id,
    _bot_start, _bot_stop, _auto_start_instance,
    _load_global_cfg, _save_global_cfg,
    _resolve_config_workspace, _load_cfg_for_workspace, _ensure_agent_cfg,
    _server_tunnel_command,
    _cmd_queue_clear, _cmd_config_set,
    CREATION_FLAGS,
)


# ── Existing command handlers (ported from cli.py) ──


def cmd_setup(args):
    """Run first-time setup."""
    from ..state.setup import interactive_setup
    interactive_setup(quick=bool(getattr(args, "quick", False)))


def cmd_status(args):
    """Check Partner status with full detail."""
    from ..state.setup import show_status, find_workspace
    workspace = _resolve_runtime_workspace(args.workspace) or find_workspace()
    show_status(workspace)


def cmd_doctor(args):
    """Check local Partner environment."""
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
    try:
        from ..state.setup import detect_openclaw
        openclaw_info = detect_openclaw()
        openclaw_ok = bool(openclaw_info.available)
    except Exception:
        openclaw_ok = shutil.which("openclaw") is not None

    _print_kv("Python", f"{sys.version.split()[0]} ({_fmt_bool(python_ok)})")
    _print_kv("Git", _fmt_bool(git_ok))
    _print_kv("Workspace", workspace if workspace else f"{C_YELLOW}Not configured{C_RESET}")
    _print_kv("Config", _fmt_bool(config_ok))
    _print_kv("QQ Config", _fmt_optional(qq_ok))
    _print_kv("Hermes", _fmt_bool(hermes_ok))
    _print_kv("OpenClaw", _fmt_bool(openclaw_ok))

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


def cmd_help(args):
    _print_help_menu()


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


def cmd_instance(args):
    manager = _load_manager_module()
    action = args.instance_action
    if action == "list":
        manager.print_instance_list()
        _print_commands()
        return


def cmd_showcase(args):
    workspace = args.workspace or _resolve_runtime_workspace(None)
    if not workspace:
        print("❌ Partner 未配置，请先运行: partner setup")
        return
    try:
        from ..showcase import build_showcase
        out = build_showcase(workspace, project=args.project, output=args.output)
    except Exception as exc:
        print(f"❌ showcase 生成失败: {exc}")
        return
    print(f"  ✅ Showcase 已生成: {out}")
    print(f"     入口: {out / 'README.md'}")
    _print_commands()


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


def cmd_update(args):
    """Update Partner to the latest version."""
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"{C_BOLD}🔄 Updating Partner...{C_RESET}")
    print(f"   Repo: {C_CYAN}{repo_dir}{C_RESET}")
    print()

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
    out = r.stdout.rstrip("\n")
    if out:
        for line in out.split("\n"):
            print(f"   {line}")
    print(f"{C_GREEN}   ✅ git pull completed{C_RESET}")
    print()

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
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            capture_output=True, text=True, timeout=120, cwd=repo_dir,
        )
    if r.returncode != 0:
        print(f"{C_RED}❌ pip install failed:{C_RESET}")
        print(r.stderr)
        sys.exit(1)
    pip_lines = r.stdout.rstrip("\n").split("\n")
    last_line = pip_lines[-1].strip() if pip_lines else ""
    if last_line:
        print(f"   {last_line}")
    print(f"{C_GREEN}   ✅ pip install completed{C_RESET}")

    verify = subprocess.run(
        [sys.executable, "-c",
         "import importlib.metadata as m, partner, pathlib; "
         "print(m.version('partner-research')); "
         "print(pathlib.Path(partner.__file__).resolve())"],
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

    # Print CHANGELOG
    changelog_path = os.path.join(repo_dir, "CHANGELOG.md")
    if os.path.exists(changelog_path):
        print(f"{C_BOLD}📋 Latest Changes{C_RESET}")
        print()
        with open(changelog_path) as f:
            lines = f.readlines()
        in_entry = False
        entry_lines = []
        for line in lines:
            if line.startswith("## ") and not in_entry:
                in_entry = True
                entry_lines.append(line)
            elif line.startswith("## ") and in_entry:
                break
            elif in_entry:
                entry_lines.append(line)
        while entry_lines and entry_lines[-1].strip() == "":
            entry_lines.pop()
        while len(entry_lines) > 1 and entry_lines[1].strip() == "":
            entry_lines.pop(1)
        for line in entry_lines:
            print(line, end="")
        print()
    else:
        print(f"{C_YELLOW}⚠  No CHANGELOG.md found{C_RESET}")
        print()

    # Auto-restart QQ bot
    print(f"{C_YELLOW}➜ Checking QQ bot...{C_RESET}")
    workspace = None
    try:
        from ..state.setup import find_workspace as _fw
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

    # Check work state
    print(f"{C_YELLOW}➜ Checking work state...{C_RESET}")
    if workspace:
        state_dir = os.path.join(workspace, "state")
        plan_path = os.path.join(state_dir, "active_plan.json")
        queue_path = os.path.join(state_dir, "task_queue.json")
        cfg_path = resolve_partner_config_path(workspace)

        print(f"   📁 工作区: {workspace}")
        print(f"   ⚙️ 配置: {cfg_path}")

        if os.path.exists(plan_path):
            try:
                with open(plan_path) as f:
                    plan = json.load(f)
                status_map = {"": "", "planning": "规划中", "active": "执行中",
                              "completed": "已完成"}
                raw = plan.get("status", "")
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

        # 实例是消息驱动的，没有自主 cron 循环
        print(f"   ⏰ 模式: 消息驱动（响应式）")
    else:
        print(f"   ℹ 未找到工作区（运行 partner setup 配置）")
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
                    from ..state.setup import interactive_setup
                    interactive_setup()
            except (EOFError, OSError):
                pass
            finally:
                _tty.close()
    print()

    print(f"{C_BOLD}{C_GREEN}✅ Partner is up to date!{C_RESET}")
    print()
    _print_commands()

    # Ask to reconfigure
    if workspace:
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
                    from ..state.setup import interactive_setup
                    interactive_setup()
            except (EOFError, OSError):
                print()
            finally:
                _tty.close()
        else:
            print(f"\n  检测到已有配置。可稍后运行: {C_BOLD}partner setup{C_RESET}")


def _print_ollama_usage(mode: str):
    print()
    print(f"  {C_BOLD}Ollama 使用范围:{C_RESET} {mode}")
    print("  off     不使用 Ollama，全部走主 API/Agent")
    print("  lite    只把分类、用户短回复、简短汇报交给 Ollama；项目执行仍走主 API/Agent")
    print("  project 项目执行优先尝试 Ollama；短回复仍走主 API/Agent")
    print("  all     分类、短回复、汇报、项目执行都优先尝试 Ollama")
    print("  任一 endpoint/model 不可用时，Partner 自动回退到主 API/Agent，不给用户报错。")


def cmd_server(args):
    cfg = _load_global_cfg()
    servers = cfg.get("servers") if isinstance(cfg.get("servers"), dict) else {}
    action = args.server_action

    if action == "add":
        name = args.name
        if not name or not args.host:
            print("❌ 需要 --name 和 --host")
            return
        servers[name] = {
            "name": name,
            "host": args.host,
            "user": args.user or "ubuntu",
            "port": int(args.port or 22),
            "key_path": args.key or "",
            "workspace": args.remote_workspace or "",
            "enabled": not bool(args.disabled),
        }
        cfg["servers"] = servers
        _save_global_cfg(cfg)
        print(f"  ✅ 已保存服务器: {name} ({args.user or 'ubuntu'}@{args.host}:{int(args.port or 22)})")
        if args.print_tunnel:
            print()
            print("  本地 Ollama 暴露给该服务器的反向隧道命令：")
            print(f"    {_server_tunnel_command(servers[name], args.remote_port, args.local_port)}")
            print(f"  服务器实例 Ollama endpoint 配置为: http://127.0.0.1:{args.remote_port}")
        return

    if action == "list":
        print()
        print(f"  {C_BOLD}Servers{C_RESET}")
        if not servers:
            print("  (empty)")
            return
        for name, row in servers.items():
            if not isinstance(row, dict):
                continue
            print(
                f"  {name}: {row.get('user') or 'ubuntu'}@{row.get('host')}:{row.get('port') or 22} "
                f"workspace={row.get('workspace') or '-'} enabled={row.get('enabled', True)}"
            )
        return

    if action == "remove":
        name = args.name
        if name in servers:
            servers.pop(name, None)
            cfg["servers"] = servers
            _save_global_cfg(cfg)
            print(f"  ✅ 已删除服务器: {name}")
        else:
            print(f"  ⚠ 未找到服务器: {name}")
        return

    if action == "tunnel-hint":
        name = args.name
        row = servers.get(name) if isinstance(servers.get(name), dict) else None
        if not row:
            print(f"❌ 未找到服务器: {name}。先运行 partner server add")
            return
        print()
        print("  在本地电脑运行下面命令，把本地 Ollama 暴露给服务器：")
        print(f"    {_server_tunnel_command(row, args.remote_port, args.local_port)}")
        print()
        print("  然后在服务器实例配置 Ollama endpoint：")
        print(f"    partner ollama add --name local-tunnel --base-url http://127.0.0.1:{args.remote_port} --models {args.models} --mode {args.mode}")
        print()
        print("  Windows PowerShell 也可以直接运行同一条 ssh 命令；确保本机 Ollama 已启动。")
        return


def cmd_ollama(args):
    workspace = _resolve_config_workspace(args)
    if not workspace:
        print("❌ Partner 未配置，先运行 partner setup")
        return

    cfg = _load_cfg_for_workspace(workspace)
    agent = _ensure_agent_cfg(cfg)
    pool = agent.get("ollama_pool") if isinstance(agent.get("ollama_pool"), dict) else {}
    action = args.ollama_action

    if action == "setup":
        mode = args.mode or input("Ollama 使用范围 [lite/off/project/all] (默认 lite): ").strip() or "lite"
        mode = mode.strip().lower()
        if mode not in {"off", "lite", "project", "all"}:
            print("❌ mode 只能是 off/lite/project/all")
            return
        pool["enabled"] = mode != "off"
        pool["mode"] = mode
        pool.setdefault("probe_timeout_sec", 2)
        pool.setdefault("chat_timeout_sec", 30)
        pool.setdefault("max_input_chars", 4000)
        endpoints = pool.get("endpoints") if isinstance(pool.get("endpoints"), list) else []
        if mode != "off":
            print()
            print("配置一个 Ollama endpoint。示例：")
            print("  本机: http://127.0.0.1:11434")
            print("  SSH 隧道: http://127.0.0.1:11435")
            print("  远程服务器: http://server-ip:11434")
            name = args.name or input("名称 (如 local/lab160/server1): ").strip() or f"ollama{len(endpoints)+1}"
            base_url = args.base_url or input("Ollama 地址: ").strip()
            models = args.models or input("模型优先级，逗号分隔 (如 qwen3:1.7b,qwen3:4b,qwen2.5:7b): ").strip() or "qwen3:1.7b,qwen3:4b,qwen2.5:7b"
            if not base_url:
                print("❌ base_url 不能为空")
                return
            endpoints = [e for e in endpoints if not (isinstance(e, dict) and e.get("name") == name)]
            endpoints.append({
                "name": name,
                "base_url": base_url.rstrip("/"),
                "models": [x.strip() for x in models.split(",") if x.strip()],
                "enabled": True,
            })
            pool["endpoints"] = endpoints
        agent["ollama_pool"] = pool
        agent["dynamic_ollama"] = {**(agent.get("dynamic_ollama") if isinstance(agent.get("dynamic_ollama"), dict) else {}), "enabled": mode in {"project", "all"}}
        cfg["agent"] = agent
        save_partner_config_data(workspace, cfg)
        print(f"  ✅ Ollama pool 已保存到: {workspace}")
        _print_ollama_usage(mode)
        print("  测试命令: partner ollama test")
        return

    if action == "add":
        endpoints = pool.get("endpoints") if isinstance(pool.get("endpoints"), list) else []
        name = args.name or f"ollama{len(endpoints)+1}"
        if not args.base_url:
            print("❌ 需要 --base-url，例如 partner ollama add --name lab --base-url http://127.0.0.1:11435 --models qwen3:1.7b,qwen3:4b,qwen2.5:7b")
            return
        models = [x.strip() for x in (args.models or "qwen3:1.7b,qwen3:4b,qwen2.5:7b").split(",") if x.strip()]
        endpoints = [e for e in endpoints if not (isinstance(e, dict) and e.get("name") == name)]
        endpoint = {"name": name, "base_url": args.base_url.rstrip("/"), "models": models, "enabled": True}
        if getattr(args, "location", None):
            endpoint["location"] = args.location
        if getattr(args, "server", None):
            endpoint["server"] = args.server
        endpoints.append(endpoint)
        pool["enabled"] = True
        pool["mode"] = args.mode or pool.get("mode") or "lite"
        pool["endpoints"] = endpoints
        agent["ollama_pool"] = pool
        agent["dynamic_ollama"] = {**(agent.get("dynamic_ollama") if isinstance(agent.get("dynamic_ollama"), dict) else {}), "enabled": pool["mode"] in {"project", "all"}}
        save_partner_config_data(workspace, cfg)
        print(f"  ✅ 已添加 Ollama endpoint: {name}")
        return

    if action == "mode":
        mode = args.mode
        if mode not in {"off", "lite", "project", "all"}:
            print("❌ mode 只能是 off/lite/project/all")
            return
        pool["enabled"] = mode != "off"
        pool["mode"] = mode
        agent["ollama_pool"] = pool
        agent["dynamic_ollama"] = {**(agent.get("dynamic_ollama") if isinstance(agent.get("dynamic_ollama"), dict) else {}), "enabled": mode in {"project", "all"}}
        save_partner_config_data(workspace, cfg)
        print(f"  ✅ Ollama 使用范围已设为: {mode}")
        _print_ollama_usage(mode)
        return

    if action == "disable":
        pool["enabled"] = False
        pool["mode"] = "off"
        agent["ollama_pool"] = pool
        agent["dynamic_ollama"] = {**(agent.get("dynamic_ollama") if isinstance(agent.get("dynamic_ollama"), dict) else {}), "enabled": False}
        save_partner_config_data(workspace, cfg)
        print("  ✅ 已关闭 Ollama，后续全部回主 API/Agent")
        return

    if action == "list":
        print()
        print(f"  {C_BOLD}Ollama Pool{C_RESET}")
        print(f"  Workspace: {workspace}")
        print(f"  Enabled: {pool.get('enabled', False)}")
        print(f"  Mode: {pool.get('mode', 'off')}")
        for i, e in enumerate(pool.get("endpoints") or [], start=1):
            if not isinstance(e, dict):
                continue
            print(f"  {i}. {e.get('name') or 'ollama'}  {e.get('base_url')}  models={','.join(str(x) for x in (e.get('models') or []))}  enabled={e.get('enabled', True)}")
        _print_ollama_usage(str(pool.get("mode") or "off"))
        return

    if action == "test":
        from ..ollama_pool import test_pool
        result = test_pool(workspace, purpose=args.purpose or "report")
        selected = result.get("selected")
        status = result.get("status") or {}
        print()
        print(f"  {C_BOLD}Ollama Test{C_RESET}")
        if selected:
            print(f"  ✅ selected: {selected.get('name')} {selected.get('model')} {selected.get('base_url')}")
        else:
            print("  ⚠ 没有可用 Ollama，将回退到主 API/Agent")
        print(f"  mode: {status.get('mode') or result.get('configured', {}).get('mode')}")
        print(f"  purpose: {status.get('purpose') or args.purpose or 'report'}")
        print(f"  reason: {status.get('reason')}")
        for row in status.get("probe_results") or []:
            print(f"  - {row}")
        return


# ── Main parser builder ──

def build_parser() -> argparse.ArgumentParser:
    """Build the full argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog='partner',
        description='Partner 🤝 - Your AI Research Companion',
        add_help=False,
    )

    # Global arguments
    parser.add_argument('-h', '--help', action='store_true', dest='show_help',
                        help=argparse.SUPPRESS)
    parser.add_argument('--instance-id', dest='instance_id', default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument('--workspace', '-w', default=None,
                        help='工作区路径')

    sub = parser.add_subparsers(dest='command')

    # ── Existing commands ──

    # setup
    p_setup = sub.add_parser('setup', help='配置 Partner（QQ机器人等）')
    p_setup.add_argument('--status', action='store_true', help='查看状态')
    p_setup.add_argument('--quick', action='store_true', help='快速配置，尽量使用默认值')
    p_setup.set_defaults(func=lambda args: cmd_status(args) if args.status else cmd_setup(args))

    # help
    p_help = sub.add_parser('help', help='显示完整命令帮助')
    p_help.set_defaults(func=cmd_help)

    # status
    p_status = sub.add_parser('status', help='查看 Partner 状态')
    p_status.add_argument('--workspace', '-w', help='工作区路径')
    p_status.set_defaults(func=cmd_status)

    # doctor
    p_doctor = sub.add_parser('doctor', help='检查本机 Partner 运行环境')
    p_doctor.add_argument('--workspace', '-w', help='工作区路径')
    p_doctor.set_defaults(func=cmd_doctor)

    # Short bot commands: start, stop, restart
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

    # instance
    p_instance = sub.add_parser('instance', help='多实例管理快捷入口')
    i_sub = p_instance.add_subparsers(dest='instance_action')
    i_sub.required = True
    i_sub.add_parser('list', help='列出所有实例')
    p_instance.set_defaults(func=cmd_instance)

    # showcase
    p_showcase = sub.add_parser('showcase', help='生成用户可读 demo/showcase 材料')
    s_sub = p_showcase.add_subparsers(dest='showcase_action')
    s_sub.required = True
    p_showcase_build = s_sub.add_parser('build', help='Build showcase materials')
    p_showcase_build.add_argument('--workspace', '-w', default=argparse.SUPPRESS, help='工作区路径')
    p_showcase_build.add_argument('--project', help='项目名称；默认读取 active project')
    p_showcase_build.add_argument('--output', help='输出目录；默认 user/showcase/<project>')
    p_showcase.set_defaults(func=cmd_showcase)

    # server
    p_server = sub.add_parser('server', help='配置服务器连接和本机 Ollama 隧道提示')
    srv_sub = p_server.add_subparsers(dest='server_action')
    srv_sub.required = True

    p_server_add = srv_sub.add_parser('add', help='添加一台服务器')
    p_server_add.add_argument('--name', required=True, help='服务器名称，如 tx04/lab/server1')
    p_server_add.add_argument('--host', required=True, help='服务器 IP 或域名')
    p_server_add.add_argument('--user', default='ubuntu', help='SSH 用户')
    p_server_add.add_argument('--port', type=int, default=22, help='SSH 端口')
    p_server_add.add_argument('--key', help='SSH 私钥路径')
    p_server_add.add_argument('--remote-workspace', help='远端 Partner workspace')
    p_server_add.add_argument('--disabled', action='store_true', help='保存但禁用')
    p_server_add.add_argument('--print-tunnel', action='store_true', help='保存后打印本地 Ollama 反向隧道命令')
    p_server_add.add_argument('--remote-port', type=int, default=11434, help='服务器侧监听端口')
    p_server_add.add_argument('--local-port', type=int, default=11434, help='本机 Ollama 端口')

    srv_sub.add_parser('list', help='列出服务器')

    p_server_remove = srv_sub.add_parser('remove', help='删除服务器')
    p_server_remove.add_argument('name', help='服务器名称')

    p_server_hint = srv_sub.add_parser('tunnel-hint', help='打印本地 Ollama 暴露给服务器的 SSH 反向隧道命令')
    p_server_hint.add_argument('name', help='服务器名称')
    p_server_hint.add_argument('--remote-port', type=int, default=11434, help='服务器侧监听端口')
    p_server_hint.add_argument('--local-port', type=int, default=11434, help='本机 Ollama 端口')
    p_server_hint.add_argument('--models', default='qwen3:1.7b,qwen3:4b,qwen2.5:7b', help='建议配置的模型列表')
    p_server_hint.add_argument('--mode', choices=['lite', 'project', 'all'], default='lite', help='建议配置的 Ollama 使用范围')
    p_server.set_defaults(func=cmd_server)

    # ollama
    p_ollama = sub.add_parser('ollama', help='配置可选 Ollama 本地/远程模型池')
    o_sub = p_ollama.add_subparsers(dest='ollama_action')
    o_sub.required = True

    p_ollama_setup = o_sub.add_parser('setup', help='交互式配置 Ollama endpoint 和使用范围')
    p_ollama_setup.add_argument('--workspace', '-w', default=argparse.SUPPRESS, help='工作区路径')
    p_ollama_setup.add_argument('--mode', choices=['off', 'lite', 'project', 'all'], help='Ollama 使用范围')
    p_ollama_setup.add_argument('--name', help='endpoint 名称，如 local/lab160/server1')
    p_ollama_setup.add_argument('--base-url', help='Ollama 地址，如 http://127.0.0.1:11434')
    p_ollama_setup.add_argument('--models', help='模型优先级，逗号分隔，如 qwen3:1.7b,qwen3:4b,qwen2.5:7b')

    p_ollama_add = o_sub.add_parser('add', help='添加一个 Ollama endpoint')
    p_ollama_add.add_argument('--workspace', '-w', default=argparse.SUPPRESS, help='工作区路径')
    p_ollama_add.add_argument('--name', help='endpoint 名称')
    p_ollama_add.add_argument('--base-url', required=True, help='Ollama 地址')
    p_ollama_add.add_argument('--models', default='qwen3:1.7b,qwen3:4b,qwen2.5:7b', help='模型优先级，逗号分隔')
    p_ollama_add.add_argument('--mode', choices=['lite', 'project', 'all'], help='添加后设置使用范围')
    p_ollama_add.add_argument('--location', choices=['local', 'server', 'tunnel', 'custom'], help='endpoint 位置元数据')
    p_ollama_add.add_argument('--server', help='关联的 server 名称')

    p_ollama_mode = o_sub.add_parser('mode', help='设置 Ollama 使用范围')
    p_ollama_mode.add_argument('mode', choices=['off', 'lite', 'project', 'all'])
    p_ollama_mode.add_argument('--workspace', '-w', default=argparse.SUPPRESS, help='工作区路径')

    p_ollama_disable = o_sub.add_parser('disable', help='关闭 Ollama，全部回主 API/Agent')
    p_ollama_disable.add_argument('--workspace', '-w', default=argparse.SUPPRESS, help='工作区路径')

    p_ollama_list = o_sub.add_parser('list', help='查看 Ollama pool 配置')
    p_ollama_list.add_argument('--workspace', '-w', default=argparse.SUPPRESS, help='工作区路径')

    p_ollama_test = o_sub.add_parser('test', help='探测当前可用 Ollama endpoint')
    p_ollama_test.add_argument('--workspace', '-w', default=argparse.SUPPRESS, help='工作区路径')
    p_ollama_test.add_argument('--purpose', choices=['classify', 'interaction', 'report', 'project', 'chat'], default='report')
    p_ollama.set_defaults(func=cmd_ollama)

    # ── New commands ──

    # queue clear — preserved as a dedicated subcommand
    p_queue = sub.add_parser('queue', help='管理任务队列')
    q_sub = p_queue.add_subparsers(dest='queue_action')
    q_sub.required = True
    p_queue_clear = q_sub.add_parser('clear', help='清空任务队列')
    p_queue_clear.set_defaults(func=_cmd_queue_clear)

    # config set
    p_config = sub.add_parser('config', help='修改运行时配置')
    c_sub = p_config.add_subparsers(dest='config_action')
    c_sub.required = True
    p_config_set = c_sub.add_parser('set', help='设置配置项')
    p_config_set.add_argument('key', help='配置键 (如 interval)')
    p_config_set.add_argument('value', help='配置值')
    p_config_set.set_defaults(func=_cmd_config_set)

    # onboard
    from .onboard import register_subparser as register_onboard
    register_onboard(sub)

    # gateway
    from .gateway import register_subparser as register_gateway
    register_gateway(sub)

    # world-model
    from .world_model_cli import register_subparser as register_world_model
    register_world_model(sub)

    # tui — moved to shells/frontend/tui/
    _shells_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'shells')
    if _shells_dir not in sys.path:
        sys.path.insert(0, _shells_dir)
    from frontend.tui import register_subparser as register_tui
    register_tui(sub)

    # agent management
    from .agent_cli import register_subparser as register_agent
    register_agent(sub)

    # benchmark (NatureBench 兼容)
    from .benchmark_cli import register_subparser as register_benchmark
    register_benchmark(sub)

    # default (no subcommand)
    parser.set_defaults(func=cmd_default)

    return parser


def main():
    """Main entry point: parse args and dispatch."""
    parser = build_parser()
    args = parser.parse_args()

    if getattr(args, 'show_help', False):
        cmd_help(args)
        return

    # When partner-manager starts an instance: --instance-id <id> --workspace <path>
    # No subcommand -> auto-start QQ bot for that instance
    if args.instance_id and args.command is None:
        _auto_start_instance(args.instance_id, args.workspace)
        return

    if hasattr(args, 'func'):
        args.func(args)
    else:
        cmd_default(args)
