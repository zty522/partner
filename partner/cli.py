"""Partner CLI - minimal, because Partner talks through your agent.

Usage:
    partner setup              First-time configuration
    partner setup --status     Check configuration status
    partner                    Start talking (opens your agent)
"""

import argparse
import json
import os
# Force UTF-8 for subprocess pipes (prevents GBK errors on Chinese Windows);
# must be set before any subprocess import or call
os.environ.setdefault("PYTHONUTF8", "1")
import sys
import glob
import shutil
from datetime import datetime
from pathlib import Path

from .i18n import lang, t, reload as i18n_reload


def get_workspace() -> str:
    """Get configured workspace path (delegates to setup.find_workspace).
    Checks PARTNER_WORKSPACE env var first (for multi-instance)."""
    # Multi-instance: env var takes priority
    env_ws = os.environ.get('PARTNER_WORKSPACE', '')
    if env_ws and os.path.isdir(env_ws):
        return env_ws
    from .setup import find_workspace as _fw
    return _fw()


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
        print(t("cli.not_configured"))
        return
    platform = args.platform
    action = args.action
    foreground = getattr(args, 'foreground', False)
    if action == "start":
        _bot_start(workspace, platform, foreground=foreground)
    elif action == "stop":
        _bot_stop(workspace, platform)


def _bot_stop(workspace, platform, quiet=False):
    pid_path = os.path.join(workspace, "state", f"{platform}_bot.pid")
    label = {"qq": "QQ"}.get(platform, platform)
    if not os.path.exists(pid_path):
        print(t("cli.bot_not_running", label=label))
        return
    try:
        with open(pid_path) as f:
            pid = int(f.read().strip())
        os.kill(pid, 15)
        os.remove(pid_path)
        print(t("cli.bot_stopped", label=label, pid=pid))

        # Also kill watchdog for this workspace
        import subprocess as _sp
        try:
            _sp.run(
                ["pkill", "-f", f"bot_watchdog.py {workspace}"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

        if not quiet:
            _print_commands()
    except ProcessLookupError:
        os.remove(pid_path)
        print(t("cli.bot_process_gone", label=label))
        if not quiet:
            _print_commands()
    except Exception as e:
        print(t("cli.stop_failed", error=str(e)))
    if quiet:
        print()  # blank line for spacing


def _bot_start(workspace, platform, quiet=False, foreground=False):
    import subprocess
    import time
    label = {"qq": "QQ"}.get(platform, platform)
    pp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if platform == "qq":
        # Multi-instance: check 00_config/ first, then workspace root
        cfg = os.path.join(workspace, "00_config", "qq_config.json")
        if not os.path.exists(cfg):
            cfg = os.path.join(workspace, "qq_config.json")
        if not os.path.exists(cfg):
            print(t("cli.qq_not_configured"))
            return
        
        # Check and auto-install dependencies
        try:
            import aiohttp
        except ImportError:
            print(t("cli.missing_dep", dep="aiohttp"))
            yn = input(t("cli.auto_install")).strip().lower()
            if yn != "n":
                print(t("cli.installing", dep="aiohttp"))
                r = subprocess.run([sys.executable, "-m", "pip", "install", "aiohttp>=3.8"],
                                   capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    print(t("cli.install_ok", dep="aiohttp"))
                else:
                    print(t("cli.install_fail", error=r.stderr[:100]))
                    print(t("cli.install_manual", dep="aiohttp"))
                    return
            else:
                print(t("cli.install_skip", dep="aiohttp"))
                return
        log = os.path.join(workspace, "logs", "qq_bot.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)

        cmd = [sys.executable, "-c",
            f"import sys; sys.path.insert(0,'{pp}'); from partner.qq_official_bridge import QQQfficialBridge; b=QQQfficialBridge('{workspace}'); b.load_config_from_file('{cfg}'); b.start()"]

        # Escape backslashes in paths for -c strings (Windows: C:\Users → C:/Users)
        # Without this, \U, \P etc. get interpreted as Unicode escapes by Python -c
        cmd[2] = cmd[2].replace("\\", "/")

        if foreground:
            # Foreground mode: start bot, wait, write PID if alive
            print(t("cli.connecting_bot"))
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, start_new_session=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
                # Wait up to 6s for connection, then check if alive
                import time as _t
                _t.sleep(6)
                if proc.poll() is None:
                    # Process is still running = connected successfully
                    pidf = os.path.join(workspace, "state", "qq_bot.pid")
                    os.makedirs(os.path.dirname(pidf), exist_ok=True)
                    with open(pidf, "w") as f:
                        f.write(str(proc.pid))
                    print(t("cli.bot_connected", pid=proc.pid))
                else:
                    # Process exited — get output for error
                    out = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
                    print(out[:500] if out else t("cli.bot_connect_failed"))
                    sys.exit(1)
            except Exception as e:
                print(t("cli.start_error", error=str(e)))
                sys.exit(1)
            return

        proc = subprocess.Popen(cmd, stdout=open(log,"w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,)
        pidf = os.path.join(workspace, "state", "qq_bot.pid")
        os.makedirs(os.path.dirname(pidf), exist_ok=True)
        with open(pidf, "w") as f:
            f.write(str(proc.pid))
        print(t("cli.bot_background", label=label, pid=proc.pid))
        print(t("cli.log_at", log=log))
        print(t("cli.stop_hint"))

        # Start watchdog (process monitor + auto-restart)
        watchdog_script = os.path.join(workspace, "scripts", "bot_watchdog.py")
        if os.path.exists(watchdog_script):
            watchdog_log = os.path.join(workspace, "logs", "watchdog.log")
            subprocess.Popen(
                [sys.executable, watchdog_script, workspace],
                stdout=open(watchdog_log, "a"), stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            print(t("cli.watchdog_started"))
        else:
            print(t("cli.watchdog_missing", path=watchdog_script))

        if not quiet:
            _print_commands()
    else:
        print(t("cli.unknown_platform", platform=platform))


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
        workspace = _fw()
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
        print(t("cli.bot_restarting"))
        _bot_stop(workspace, "qq", quiet=True)
        _bot_start(workspace, "qq", quiet=True)
    else:
        print(t("cli.bot_not_running_skip"))
    print()

    # 6. Check and report current work state
    print(f"{C_YELLOW}➜ Checking work state...{C_RESET}")
    if workspace:
        state_dir = os.path.join(workspace, "state")
        plan_path = os.path.join(state_dir, "active_plan.json")
        queue_path = os.path.join(state_dir, "task_queue.json")

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

        # Mind self-pulse — no external cron needed
        print(f"   🧠 Mind自脉冲: 15分钟（无需外部 cron）")
    else:
        print(f"   {t('cli.workspace_not_found')}")
        # 没工作区 → 询问是否运行 setup
        _tty = None
        try:
            _tty = open("/dev/tty", "r")
        except OSError:
            pass
        if _tty:
            try:
                print(f"  {C_CYAN}{t('cli.ttysetup_prompt')}{C_RESET}", end="", flush=True)
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
    print(f"  {C_BOLD}Commands:{C_RESET}")
    print(f"    {C_DIM}partner status       Check Partner status{C_RESET}")
    print(f"    {C_DIM}partner setup        Reconfigure{C_RESET}")
    print(f"    {C_DIM}partner bot start qq Start QQ bot{C_RESET}")
    print(f"    {C_DIM}partner bot stop qq  Stop QQ bot{C_RESET}")
    print(f"    {C_DIM}partner update       Update to latest version{C_RESET}")
    print(f"    {C_DIM}partner queue clear  Clear task queue{C_RESET}")
    print()

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
                print(f"\n  {C_CYAN}{t('cli.ttysetup_redetect')}{C_RESET}", end="", flush=True)
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
            print(f"\n  {t('cli.ttysetup_detected')}")


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
        print(t("cli.not_configured"))
        return

    state_dir = os.path.join(workspace, "state")

    # Clear task queue
    queue_path = os.path.join(state_dir, "task_queue.json")
    try:
        with open(queue_path, 'w', encoding='utf-8') as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        print(t("cli.queue_cleared"))
    except Exception as e:
        print(t("cli.queue_clear_failed", error=str(e)))
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
        print(t("cli.plan_reset"))
    except Exception as e:
        print(t("cli.plan_reset_failed", error=str(e)))

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
        print(t("cli.not_configured"))
        return

    cfg_path = os.path.join(workspace, "partner_config.json")
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        print(t("cli.config_read_failed", error=str(e)))
        return

    key = args.key
    value = args.value

    if key == "interval":
        try:
            minutes = int(value)
            if minutes < 1:
                print(t("cli.interval_invalid"))
                return
            if "scheduler" not in cfg:
                cfg["scheduler"] = {}
            cfg["scheduler"]["interval_minutes"] = minutes
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            print(t("cli.interval_set", minutes=minutes))
            print(t("cli.interval_restart_hint"))
        except ValueError:
            print(t("cli.value_must_be_number"))
            return

    _print_commands()


def cmd_log(args):
    """查看研究时间线"""
    from .recorder import Recorder
    ws = get_workspace()
    if not ws:
        print("❌ 未找到工作区")
        return 1
    rec = Recorder(ws)
    if args.list:
        projects = rec.get_projects()
        if not projects:
            print("📂 没有项目记录")
        else:
            print(f"📂 项目列表 ({len(projects)}):")
            for p in projects:
                tl = rec.get_timeline(p, 1)
                last_action = tl[0].get("action", "") if tl else ""
                print(f"  • {p}" + (f" — {last_action[:40]}" if last_action else ""))
        return 0
    projects = [args.project] if args.project else rec.get_projects()
    if not projects:
        print("❌ 未指定项目且没有项目记录。使用 partner log -p <项目名>")
        return 1
    for proj in projects:
        entries = rec.get_timeline(proj, args.limit)
        if not entries:
            print(f"📋 {proj}: 无记录")
            continue
        print(f"\n📋 {proj} (最近 {len(entries)} 条):")
        print("-" * 60)
        for e in entries:
            ts = e.get("timestamp", "")[11:19]  # HH:MM:SS
            action = e.get("action", "")
            hypothesis = e.get("hypothesis", "")
            result = e.get("result", "")
            reflection = e.get("reflection", "")
            next_step = e.get("next", "")

            parts = [f"[{ts}]"]
            if action:
                parts.append(f"📌 {action}")
            if hypothesis:
                parts.append(f"💡 {hypothesis[:60]}")
            if result:
                parts.append(f"📊 {str(result)[:60]}")
            if reflection:
                parts.append(f"🔍 {reflection[:60]}")
            if next_step:
                parts.append(f"➡️ {next_step[:60]}")
            print(" | ".join(parts))
    return 0


def cmd_usage(args):
    """查看 Token 用量统计"""
    from .token_tracker import TokenTracker
    ws = get_workspace()
    if not ws:
        print("❌ 未找到工作区")
        return 1
    tracker = TokenTracker(workspace=ws, instance_id=args.instance or "default")
    stats = tracker.query(period=args.period, project=args.project, instance=args.instance)
    print(tracker.format_report(stats))
    return 0


def cmd_migrate_records(args):
    """将旧版 workspace 文件迁移到 20_records/ 结构"""
    from .recorder import Recorder
    ws = get_workspace()
    if not ws:
        print("❌ 未找到工作区")
        return 1
    rec = Recorder(ws)
    state_dir = os.path.join(ws, "state")
    logs_dir = os.path.join(ws, "logs")

    # 创建 10_logs/state/ 目录
    logs_state = os.path.join(ws, "10_logs", "state")
    os.makedirs(logs_state, exist_ok=True)

    migration_log = []

    # 1. 归档旧 active_plan 和 plan_archive
    src = os.path.join(ws, "active_plan.json")
    if os.path.exists(src):
        dst = os.path.join(rec._archives_dir, f"active_plan_{datetime.now().strftime('%Y%m%d')}.json")
        shutil.move(src, dst)
        migration_log.append(f"📄 {src} → {dst}")

    for f in glob.glob(os.path.join(state_dir, "plan_archive_*.json")):
        dst = os.path.join(rec._archives_dir, os.path.basename(f))
        shutil.move(f, dst)
        migration_log.append(f"📄 {f} → {dst}")

    # 2. 中间状态文件 → 10_logs/state/
    state_files = ["_cycle_context.json", "capability_registry.json", "cpe_registry.json",
                   "events.json", "last_state.json", "notifier_config.json", "task_queue.json",
                   "idea_records.jsonl", "current_task.md"]
    for fname in state_files:
        fpath = os.path.join(state_dir, fname)
        if os.path.exists(fpath):
            dst = os.path.join(logs_state, fname)
            shutil.move(fpath, dst)
            migration_log.append(f"📄 {fpath} → {dst}")

    # 3. 移除 state → 20_records 符号链接
    symlink = os.path.join(ws, "state")
    if os.path.islink(symlink):
        os.unlink(symlink)
        migration_log.append(f"🔗 移除符号链接 {symlink}")

    # 4. 如有 knowledge.json/timeline 等 → 转换到项目记录
    if os.path.isdir(state_dir):
        kpath = os.path.join(state_dir, "knowledge.json")
        if os.path.exists(kpath):
            try:
                with open(kpath) as f:
                    old_knowledge = json.load(f)
                if isinstance(old_knowledge, list):
                    for entry in old_knowledge:
                        cat = entry.get("category", entry.get("source", "default"))
                        rec.add_knowledge(cat, entry.get("type", "auto"),
                                         entry.get("title", entry.get("content", str(entry)[:200])))
                migration_log.append(f"📄 knowledge.json → 按类别分布到项目记录")
            except Exception as e:
                migration_log.append(f"⚠️ knowledge.json 转换失败: {e}")

    # 5. 写迁移报告
    report_path = os.path.join(ws, "MIGRATION.md")
    with open(report_path, "w") as f:
        f.write(f"# Workspace 迁移报告\n\n")
        f.write(f"迁移时间: {datetime.now().isoformat()}\n\n")
        f.write(f"## 迁移操作\n\n")
        for item in migration_log:
            f.write(f"- {item}\n")
        f.write(f"\n---\n")
        f.write(f"✅ 迁移完成。旧版 workspace 文件已归档到 20_records/archived_plans/ 和 10_logs/state/。\n")

    print(f"✅ 迁移完成！共 {len(migration_log)} 项操作")
    for item in migration_log:
        print(f"  {item}")
    return 0


def main():
    # ── First-run language detection ──
    config_dir = Path.home() / ".partner"
    config_path = config_dir / "config.json"
    detected_lang = None
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            detected_lang = cfg.get("language", None)
        except (json.JSONDecodeError, OSError):
            pass
    if not detected_lang:
        # Auto-default to English when no TTY (systemd, cron, etc.)
        if not sys.stdin.isatty():
            detected_lang = "en"
            config_dir.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({"language": detected_lang}, f, indent=2)
        else:
            print()
            print(f"  {C_BOLD}{C_CYAN}{t('cli.lang_prompt_welcome')}{C_RESET}")
            print(f"  {t('cli.lang_prompt_option')}")
            choice = input("  Choose [1/2]: ").strip()
            lang_code = "zh" if choice == "2" else "en"
            config_dir.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({"language": lang_code}, f, indent=2)
            print(f"  {t('cli.lang_selected', lang=lang_code)}")
            print()

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
    p_bot.add_argument('--foreground', action='store_true', help='前台模式（供GUI调用）')
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

    # log
    p_log = sub.add_parser('log', help='查看研究时间线')
    p_log.add_argument('--project', '-p', default='', help='项目名')
    p_log.add_argument('--limit', '-l', type=int, default=10, help='显示条数')
    p_log.add_argument('--list', action='store_true', help='列出所有项目')
    p_log.set_defaults(func=cmd_log)

    # usage
    p_usage = sub.add_parser('usage', help='查看 Token 用量统计')
    p_usage.add_argument('period', nargs='?', default='day',
                        choices=['day', 'week', 'month'],
                        help='统计周期 (day/week/month)')
    p_usage.add_argument('--project', '-p', default='', help='按项目筛选')
    p_usage.add_argument('--instance', '-i', default='', help='按实例筛选')
    p_usage.set_defaults(func=cmd_usage)

    # migrate-records
    p_migrate = sub.add_parser('migrate-records', help='将旧版 workspace 文件迁移到 20_records/ 结构')
    p_migrate.set_defaults(func=cmd_migrate_records)

    # default
    parser.set_defaults(func=cmd_default)
    
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        cmd_default(args)


if __name__ == '__main__':
    main()
