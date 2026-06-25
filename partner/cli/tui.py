"""Partner TUI — Terminal User Interface mode.

Usage:
    partner tui    Enter interactive TUI mode

A readline-based interactive shell with:
- Multi-line input (Shift+Enter for newline, Enter to send)
- Slash command autocompletion
- Real Event pipeline progress display from Partner's core engine
- Color-coded output
- Real inbox integration (desktop_inbox.jsonl)
- Real-time polling of active_plan.json, task_log.jsonl, qq_chat_history.jsonl,
  dialog_history.jsonl
- Auto-starts backend instance if not running
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime

from ..config import resolve_partner_config_path, workspace_has_partner_config
from .common import (
    C_RESET, C_BOLD, C_DIM, C_CYAN, C_GREEN, C_YELLOW, C_RED,
    _cli_txt,
    get_workspace, _resolve_runtime_workspace, _launch_instance,
)


# ── ANSI helpers ──

def _color(text: str, color_code: str) -> str:
    return f"{color_code}{text}{C_RESET}"


def _print_banner():
    print()
    print(f"  {_color('╔══════════════════════════════════════╗', C_CYAN)}")
    print(f"  {_color('║', C_CYAN)}     {_color('🤝 Partner Interactive TUI', C_BOLD)}     {_color('║', C_CYAN)}")
    print(f"  {_color('╚══════════════════════════════════════╝', C_CYAN)}")
    print()
    print(f"  {_color('Type /help for commands. Enter to send, Shift+Enter for newline.', C_DIM)}")
    print()


def _print_help():
    print()
    print(f"  {_color('Available Commands:', C_BOLD)}")
    print()
    cmds = [
        ("/help", "Show this help message"),
        ("/status", "Show Partner status"),
        ("/stop", "Stop the current task"),
        ("/clear", "Clear the screen"),
        ("/instances", "List all instances"),
        ("/tasks", "List pending tasks"),
        ("/history [n]", "Show last N conversation entries (default: 20)"),
        ("/quit", "Exit TUI mode"),
    ]
    for cmd, desc in cmds:
        print(f"    {_color(cmd, C_CYAN)}  {_color(desc, C_DIM)}")
    print()


def _get_workspace() -> str | None:
    return get_workspace()


def _show_status(workspace: str | None = None):
    workspace = workspace or _get_workspace()
    if not workspace:
        print(f"  {_color('❌ Partner not configured', C_RED)}")
        return

    state_dir = os.path.join(workspace, "state")
    plan_path = os.path.join(state_dir, "active_plan.json")
    queue_path = os.path.join(state_dir, "task_queue.json")

    print()
    print(f"  {_color('Status', C_BOLD)}")
    print(f"  {_color('──' * 20, C_DIM)}")
    print(f"  Workspace: {workspace}")

    # Check if bot is running
    pid_path = os.path.join(workspace, "state", "qq_bot.pid")
    if os.path.exists(pid_path):
        try:
            with open(pid_path) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            print(f"  QQ Bot: {_color('Running', C_GREEN)} (PID: {pid})")
        except (OSError, ValueError):
            print(f"  QQ Bot: {_color('Not running', C_RED)}")
    else:
        print(f"  QQ Bot: {_color('Not running', C_RED)}")

    # Active plan
    if os.path.exists(plan_path):
        try:
            with open(plan_path) as f:
                plan = json.load(f)
            status = plan.get("status", "idle")
            status_colors = {
                "idle": C_DIM,
                "planning": C_YELLOW,
                "active": C_GREEN,
                "completed": C_GREEN,
            }
            sc = status_colors.get(status, C_RESET)
            print(f"  Plan Status: {_color(status, sc)}")
            if plan.get("title"):
                print(f"  Plan Title: {plan.get('title')}")
            if plan.get("heartbeat_summary"):
                print(f"  Summary: {plan.get('heartbeat_summary')}")
            # Show phases
            phases = plan.get("phases", [])
            current_idx = plan.get("current_phase_index", 0)
            for i, phase in enumerate(phases):
                pstatus = phase.get("status", "pending")
                ptitle = phase.get("title") or phase.get("action", f"Phase {i+1}")
                icon = "●" if i == current_idx else "○"
                psc = C_GREEN if pstatus == "completed" else (C_YELLOW if i == current_idx else C_DIM)
                print(f"    {_color(icon, psc)} {_color(ptitle, psc)} [{pstatus}]")
        except Exception:
            pass

    # Pending tasks
    if os.path.exists(queue_path):
        try:
            with open(queue_path) as f:
                tasks = json.load(f)
            pending = sum(1 for t in tasks if isinstance(t, dict) and t.get("status") == "pending")
            print(f"  Pending Tasks: {pending}")
        except Exception:
            pass

    print()


def _show_tasks(workspace: str | None = None):
    workspace = workspace or _get_workspace()
    if not workspace:
        print(f"  {_color('❌ Partner not configured', C_RED)}")
        return

    queue_path = os.path.join(workspace, "state", "task_queue.json")
    if not os.path.exists(queue_path):
        print(f"  {_color('No task queue found', C_YELLOW)}")
        return

    try:
        with open(queue_path) as f:
            tasks = json.load(f)
        if not tasks:
            print(f"  {_color('No tasks in queue', C_DIM)}")
            return
        print()
        print(f"  {_color('Task Queue:', C_BOLD)}")
        for i, task in enumerate(tasks, 1):
            if isinstance(task, dict):
                status = task.get("status", "unknown")
                title = task.get("title") or task.get("description", "No title") or str(task.get("id", "?"))
                sc = C_GREEN if status == "completed" else (C_YELLOW if status == "pending" else C_DIM)
                print(f"  {i}. [{_color(status, sc)}] {title}")
        print()
    except Exception as e:
        print(f"  {_color(f'Error: {e}', C_RED)}")


def _show_instances():
    try:
        from .. import manager
        print()
        manager.print_instance_list()
        print()
    except Exception as e:
        print(f"  {_color(f'Error: {e}', C_RED)}")


# ── Recent conversation history display ──

def _show_recent_history(workspace: str, n: int = 20):
    """Display the last N messages from qq_chat_history.jsonl AND daily .log files."""
    all_entries: list[dict] = []
    seen: set[str] = set()

    # 1. Read qq_chat_history.jsonl (user messages only, no partner replies here)
    paths_to_check = [
        os.path.join(workspace, "state", "qq_chat_history.jsonl"),
        os.path.join(workspace, "dialogue", "qq_chat_history.jsonl"),
    ]
    for path in paths_to_check:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            ts = entry.get("timestamp") or entry.get("created_at", "")
                            content = entry.get("content") or entry.get("text", "")
                            if not content or not ts:
                                continue
                            key = f"{ts}|{str(content)[:80]}"
                            if key not in seen:
                                seen.add(key)
                                role = entry.get("role", "user") or "user"
                                all_entries.append({"role": role, "content": content, "timestamp": ts})
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass

    # 2. Read daily .log files (contain BOTH user Q: and partner A: messages)
    dialogue_dir = os.path.join(workspace, "dialogue")
    if os.path.isdir(dialogue_dir):
        try:
            log_files = sorted(
                [f for f in os.listdir(dialogue_dir) if f.endswith(".log")],
                reverse=True,
            )[:3]
        except Exception:
            log_files = []
        for log_file in log_files:
            log_path = os.path.join(dialogue_dir, log_file)
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue
            log_date = log_file.replace(".log", "")
            current_q = ""
            for line in lines:
                if line.startswith("  Q: "):
                    current_q = line[4:].strip()
                    key = f"{log_date}|Q:{current_q[:80]}"
                    if key not in seen:
                        seen.add(key)
                        all_entries.append({"role": "user", "content": current_q, "timestamp": log_date})
                elif line.startswith("  A: "):
                    a_text = line[4:].strip()
                    if a_text:
                        key = f"{log_date}|A:{a_text[:80]}"
                        if key not in seen:
                            seen.add(key)
                            all_entries.append({"role": "assistant", "content": a_text, "timestamp": log_date})

    if not all_entries:
        return

    # Sort by timestamp and take last N
    all_entries.sort(key=lambda e: str(e.get("timestamp") or ""))
    recent = all_entries[-n:]

    print(f"  {_color(f'── 最近 {len(recent)} 条消息 ──', C_DIM)}")
    for entry in recent:
        role = entry.get("role", "")
        content = entry.get("content", "")
        ts = (entry.get("timestamp") or "")[:19]
        if not content:
            continue
        display = str(content)[:120]
        if role in ("assistant", "bot", "partner"):
            print(f"  {_color(f'🤖 {ts}', C_DIM)} {display}")
        else:
            print(f"  {_color(f'👤 {ts}', C_DIM)} {display}")
    print(f"  {_color('─' * 48, C_DIM)}")
    print()


# ── Backend auto-start ──

_CREATION_FLAGS = 0
if sys.platform == "win32":
    _CREATION_FLAGS = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore


def _is_backend_running(workspace: str) -> tuple[bool, int | None]:
    """Check if a Partner backend instance is running for this workspace.
    
    Returns (running, pid_or_None).
    """
    # Check instance.pid
    pid_path = os.path.join(workspace, "instance.pid")
    if os.path.exists(pid_path):
        try:
            with open(pid_path) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True, pid
        except (OSError, ValueError, ProcessLookupError):
            pass

    # Check state/instance_*.pid files (for managed instances)
    state_dir = os.path.join(workspace, "state")
    if os.path.isdir(state_dir):
        for fname in os.listdir(state_dir):
            if fname.startswith("instance_") and fname.endswith(".pid"):
                try:
                    with open(os.path.join(state_dir, fname)) as f:
                        pid = int(f.read().strip())
                    os.kill(pid, 0)
                    return True, pid
                except (OSError, ValueError, ProcessLookupError):
                    pass

    # Check qq_bot.pid (legacy)
    qq_pid_path = os.path.join(state_dir, "qq_bot.pid")
    if os.path.exists(qq_pid_path):
        try:
            with open(qq_pid_path) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True, pid
        except (OSError, ValueError, ProcessLookupError):
            pass

    return False, None


def _ensure_backend(workspace: str) -> bool:
    """Ensure a Partner backend is running. Auto-start if needed.
    
    Returns True if backend is running (now or already was).
    """
    running, pid = _is_backend_running(workspace)
    if running:
        return True

    print(f"  {_color('🔧 Backend not running. Auto-starting...', C_YELLOW)}")

    # Determine instance_id from workspace basename
    ws_parent = os.path.basename(os.path.dirname(os.path.normpath(workspace)))
    if ws_parent == "instances":
        instance_id = os.path.basename(os.path.normpath(workspace))
    else:
        instance_id = os.path.basename(os.path.normpath(workspace))

    try:
        proc = _launch_instance(instance_id, workspace)
        if proc:
            print(f"  {_color(f'✅ Instance started (PID: {proc.pid})', C_GREEN)}")
            # Wait a few seconds for startup
            for i in range(5):
                time.sleep(1)
                running, pid = _is_backend_running(workspace)
                if running:
                    print(f"  {_color('✅ Backend is ready', C_GREEN)}")
                    return True
            print(f"  {_color('⚠ Backend started but not yet confirmed ready. Continuing...', C_YELLOW)}")
            return True
        else:
            print(f"  {_color('❌ Failed to auto-start backend.', C_RED)}")
            print(f"  {_color('   Start manually: partner gateway start', C_DIM)}")
            return False
    except Exception as e:
        print(f"  {_color(f'❌ Failed to start: {e}', C_RED)}")
        print(f"  {_color('   Start manually: partner gateway start', C_DIM)}")
        return False


# ── Slash command autocomplete ──

_COMMANDS = ["/help", "/status", "/stop", "/clear", "/instances", "/tasks", "/quit"]


def _try_complete(text: str, state: int) -> str | None:
    """readline completer."""
    import readline
    matches = [c for c in _COMMANDS if c.startswith(text)]
    if state < len(matches):
        return matches[state]
    return None


# ── Multi-line input ──

def _read_multiline() -> str:
    """Read a multi-line input. Enter sends, Shift+Enter (or \\) adds newline."""
    lines = []
    try:
        while True:
            line = input()
            if line.endswith("\\\\"):
                lines.append(line[:-1])
            else:
                lines.append(line)
                break
    except (EOFError, KeyboardInterrupt):
        print()
        if lines:
            return "\n".join(lines)
        return ""
    return "\n".join(lines)


# ── Real inbox writer ──

def _write_to_inbox(workspace: str, text: str):
    """Write a user message to desktop_inbox.jsonl AND qq_chat_history.jsonl.

    Format matches what gui_qt.py writes — the QQ bridge polls desktop_inbox.jsonl.
    The qq_chat_history.jsonl is consumed by the mind's event loop (USER_MESSAGE handler).
    Writing to BOTH ensures the mind picks up the message regardless of which pathway
    is active.
    """
    inbox_dir = os.path.join(workspace, "state")
    os.makedirs(inbox_dir, exist_ok=True)
    inbox_path = os.path.join(inbox_dir, "desktop_inbox.jsonl")

    entry = {
        "id": f"tui_{uuid.uuid4().hex[:12]}",
        "message_id": f"tui_{uuid.uuid4().hex[:12]}",
        "text": text[:4000],
        "display_text": text[:4000],
        "source": "tui",
        "channel": "tui",
        "sender_id": "tui",
        "sender_name": "\u7ec8\u7aef",
        "attachments": [],
        "created_at": datetime.now().isoformat(),
    }
    try:
        with open(inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except Exception as e:
        print(f"  {_color(f'Failed to write to inbox: {e}', C_RED)}")

    # ALSO write to qq_chat_history.jsonl (consumed by mind event loop)
    qq_entry = {
        "role": "user",
        "content": text[:4000],
        "timestamp": datetime.now().isoformat(),
        "source": "tui",
        "sender_id": "tui_user",
        "sender_name": "\u7ec8\u7aef\u7528\u6237",
    }
    qq_path = os.path.join(inbox_dir, "qq_chat_history.jsonl")
    try:
        with open(qq_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(qq_entry, ensure_ascii=False) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except Exception as e:
        print(f"  {_color(f'Failed to write to qq_chat_history: {e}', C_RED)}")

    return True


# ── Response waiter (blocking poll after sending a message) ──

_DEFAULT_TIMEOUT = 120


def _wait_for_response(workspace: str):
    """
    After writing to inbox, poll for real Event pipeline progress and final reply.

    Polls every 2s:
      - state/active_plan.json  -> plan phases, status, heartbeat
      - state/task_log.jsonl    -> event stream (step start/complete/error)
      - dialogue/qq_chat_history.jsonl & state/qq_chat_history.jsonl -> assistant reply
      - dialogue/dialog_history.jsonl -> fallback response source

    Displays:
      - ⏳ Event Pipeline: ✓ Step 1: search (12.3s) | ▶ Step 2: analyze (running, 5.1s) | ...
      - 思考中... animated indicator while waiting
      - Assistant reply once found

    Timeout: {_DEFAULT_TIMEOUT}s. Press Ctrl+C to cancel.
    """
    # First, ensure the backend is running
    _ensure_backend(workspace)

    state_dir = os.path.join(workspace, "state")
    plan_path = os.path.join(state_dir, "active_plan.json")
    task_log_path = os.path.join(state_dir, "task_log.jsonl")

    # ── Build list of qq_chat_history paths ──
    qq_hist_paths = []
    try:
        from ..workspace_layout import history_paths as _hp
        for path in _hp(workspace, "qq_chat_history.jsonl"):
            if path not in qq_hist_paths:
                qq_hist_paths.append(path)
    except Exception:
        pass
    # Also always check state/ directly (legacy location)
    state_qq = os.path.join(state_dir, "qq_chat_history.jsonl")
    if state_qq not in qq_hist_paths:
        qq_hist_paths.append(state_qq)

    # ── Build list of dialog_history.jsonl paths (fallback) ──
    dialog_hist_paths = []
    try:
        from ..workspace_layout import history_paths as _hp2
        for path in _hp2(workspace, "dialog_history.jsonl"):
            if path not in dialog_hist_paths:
                dialog_hist_paths.append(path)
    except Exception:
        pass
    state_dialog = os.path.join(state_dir, "dialog_history.jsonl")
    if state_dialog not in dialog_hist_paths:
        dialog_hist_paths.append(state_dialog)
    # Also check in dialogue/ directly
    dialogue_dir = os.path.join(workspace, "dialogue")
    dialogue_dialog = os.path.join(dialogue_dir, "dialog_history.jsonl")
    if dialogue_dialog not in dialog_hist_paths:
        dialog_hist_paths.append(dialogue_dialog)

    all_response_paths = qq_hist_paths + dialog_hist_paths

    # ── Initialise tracking state ──
    last_plan_mtime = 0
    last_task_log_pos = 0
    last_response_pos = {}  # path -> last position read
    printed_step_ids = set()  # track which plan phases / task log lines we already printed
    _last_printed_phases = set()  # (phase_id, status) tuples to detect changes

    # Track seen messages by (timestamp, content) tuple for better dedup
    seen_message_tuples: set[tuple[str, str]] = set()

    # Track phase timing info
    _phase_start_times: dict[str, float] = {}
    _step_start_times: dict[str, float] = {}

    # Count existing lines in qq_chat_history / dialog_history files
    for path in all_response_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    pos = 0
                    for line in f:
                        stripped = line.strip()
                        if stripped:
                            try:
                                entry = json.loads(stripped)
                                ts = entry.get("timestamp", entry.get("created_at", ""))
                                content = entry.get("content", entry.get("text", ""))
                                if ts and content:
                                    seen_message_tuples.add((ts, content))
                            except json.JSONDecodeError:
                                pass
                        pos = f.tell()
                    last_response_pos[path] = pos
            except Exception:
                pass
        else:
            last_response_pos[path] = 0

    # Record initial task_log position
    if os.path.exists(task_log_path):
        try:
            with open(task_log_path, "r", encoding="utf-8") as f:
                last_task_log_pos = f.tell()
        except Exception:
            pass

    # ── Initial plan phases snapshot (to avoid re-printing) ──
    if os.path.exists(plan_path):
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
            for phase in plan.get("phases", []):
                pid = phase.get("id") or phase.get("title") or phase.get("action", "")
                if pid:
                    printed_step_ids.add(pid)
        except Exception:
            pass

    # ── Polling loop ──
    start_time = time.time()
    thinking_frames = ["\u2581", "\u2582", "\u2583", "\u2584", "\u2585", "\u2586", "\u2587", "\u2588", "\u2587", "\u2586", "\u2585", "\u2584", "\u2583", "\u2582"]
    frame_idx = 0
    ev_pipeline_label = "\u23f3 Event Pipeline:"
    thinking_label = "\u601d\u8003\u4e2d..."
    _ico_active = "\u25b6"
    _ico_complete = "\u2713"
    _ico_failed = "\u2717"
    _ico_pending = "\u25cb"
    _ico_arrow = "\u2192"
    _ico_info = "\u2139"
    _ico_done = "\u2705"
    _ico_x = "\u274c"
    _ico_cancel = "\u23f9"
    _ico_timeout = "\u23f1"
    _ico_robot = "\U0001f916"
    _plan_ok = _ico_done + " \u8ba1\u5212\u5b8c\u6210!"
    _plan_fail = _ico_x + " \u8ba1\u5212\u5931\u8d25"
    _cancel_text = _ico_cancel + " \u5df2\u53d6\u6d88\u7b49\u5f85"
    _timeout_text = _ico_timeout + " \u8d85\u65f6\uff0cPartner \u672a\u5728120\u79d2\u5185\u54cd\u5e94"

    print(f"  {_color(thinking_label, C_YELLOW)}", end="", flush=True)

    try:
        while time.time() - start_time < _DEFAULT_TIMEOUT:
            time.sleep(2)

            pipeline_printed_this_cycle = False

            # ── 1. Check active_plan.json for phase progress ──
            if os.path.exists(plan_path):
                try:
                    mtime = os.path.getmtime(plan_path)
                    if mtime > last_plan_mtime:
                        last_plan_mtime = mtime
                        with open(plan_path, "r", encoding="utf-8") as f:
                            plan = json.load(f)

                        status = plan.get("status", "idle")
                        phases = plan.get("phases", [])
                        current_idx = plan.get("current_phase_index", 0)

                        # Build current phase signature
                        current_phases = set()
                        for i, phase in enumerate(phases):
                            pid = phase.get("id") or phase.get("title") or phase.get("action", "step_%d" % i)
                            pstatus = phase.get("status", "pending")
                            current_phases.add((pid, pstatus))

                        # Only print if phases changed
                        if phases and current_phases != _last_printed_phases:
                            _last_printed_phases = current_phases
                            # Clear the thinking line
                            print("\r" + " " * 60 + "\r", end="", flush=True)

                            parts = []
                            for i, phase in enumerate(phases):
                                pstatus = phase.get("status", "pending")
                                ptitle = phase.get("title") or phase.get("action", "Step %d" % (i + 1))
                                pid = phase.get("id") or ptitle

                                # Track phase timing
                                if pid not in _phase_start_times:
                                    _phase_start_times[pid] = time.time()

                                elapsed = time.time() - _phase_start_times.get(pid, time.time())
                                elapsed_str = "%.1fs" % elapsed

                                if i == current_idx and pstatus == "active":
                                    parts.append("%s %s (%s)" % (
                                        _color("\u25b6", C_YELLOW),
                                        ptitle,
                                        _color("running, " + elapsed_str, C_YELLOW),
                                    ))
                                elif pstatus == "completed":
                                    parts.append("%s %s (%s)" % (
                                        _color("\u2713", C_GREEN),
                                        ptitle,
                                        _color(elapsed_str, C_GREEN),
                                    ))
                                elif pstatus == "failed":
                                    parts.append("%s %s (%s)" % (
                                        _color("\u2717", C_RED),
                                        ptitle,
                                        elapsed_str,
                                    ))
                                else:
                                    parts.append("%s %s" % (
                                        _color("\u25cb", C_DIM),
                                        ptitle,
                                    ))

                                if pid:
                                    printed_step_ids.add(pid)

                            print("  %s %s" % (_color(ev_pipeline_label, C_CYAN), " | ".join(parts)))

                            summary = plan.get("heartbeat_summary", "")
                            if summary:
                                print("  %s %s" % (_color("\u2192", C_DIM), summary[:120]))

                            if status == "completed":
                                print("  %s" % _color("\u2705 \u8ba1\u5212\u5b8c\u6210!", C_GREEN))
                            elif status == "failed":
                                print("  %s" % _color("\u274c \u8ba1\u5212\u5931\u8d25", C_RED))

                            pipeline_printed_this_cycle = True

                except Exception:
                    pass

            # ── 2. Check task_log.jsonl for new event entries ──
            if os.path.exists(task_log_path):
                try:
                    with open(task_log_path, "r", encoding="utf-8") as f:
                        f.seek(last_task_log_pos)
                        new_lines = f.readlines()
                        last_task_log_pos = f.tell()

                    for line in new_lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            event = entry.get("event", "")
                            msg = entry.get("message", "")
                            line_id = entry.get("id") or entry.get("step_id") or ("%s:%s" % (event, msg))

                            if line_id in printed_step_ids:
                                continue
                            printed_step_ids.add(line_id)

                            # Track step timing
                            if event == "step_start" and line_id not in _step_start_times:
                                _step_start_times[line_id] = time.time()
                            step_elapsed = time.time() - _step_start_times.get(line_id, time.time())
                            elapsed_str = "%.1fs" % step_elapsed

                            # Clear thinking line before printing progress
                            print("\r" + " " * 60 + "\r", end="", flush=True)

                            if event == "step_complete":
                                print("  %s %s (%s)" % (_color("\u2713", C_GREEN), msg, _color(elapsed_str, C_GREEN)))
                            elif event == "step_start":
                                print("  %s %s (%s)" % (
                                    _color("\u25b6", C_YELLOW),
                                    msg,
                                    _color("running, " + elapsed_str, C_YELLOW),
                                ))
                            elif event == "error":
                                print("  %s %s" % (_color("\u2717", C_RED), msg))
                            elif event == "info":
                                print("  %s %s" % (_color("\u2139", C_DIM), msg))
                            else:
                                print("  %s %s" % (_color("\u2192", C_DIM), msg))

                        except json.JSONDecodeError:
                            pass
                except Exception:
                    pass

            # ── 3. Check response files (qq_chat_history + dialog_history) for assistant reply ──
            assistant_reply = None
            for resp_path in all_response_paths:
                pos = last_response_pos.get(resp_path, 0)
                if not os.path.exists(resp_path):
                    continue
                try:
                    with open(resp_path, "r", encoding="utf-8") as f:
                        f.seek(pos)
                        for line in f:
                            stripped = line.strip()
                            if not stripped:
                                continue
                            try:
                                entry = json.loads(stripped)
                                role = entry.get("role", "")
                                content = entry.get("content", "")
                                ts = entry.get("timestamp", entry.get("created_at", ""))

                                # Deduplicate using (timestamp, content) tuple
                                dedup_key = (ts, str(content))
                                if dedup_key in seen_message_tuples:
                                    continue
                                seen_message_tuples.add(dedup_key)

                                if role == "assistant" and content and entry.get("source") == "tui":
                                    # Skip known thinking placeholders
                                    stripped_content = str(content).strip()
                                    if stripped_content in {"\u601d\u8003\u4e2d.......", "\u601d\u8003\u4e2d......", "\u601d\u8003\u4e2d\u2026\u2026", "Thinking..."}:
                                        continue
                                    if not assistant_reply:
                                        assistant_reply = stripped_content
                                elif not role and entry.get("text"):
                                    # dialog_history.jsonl may use "text" instead of "content"
                                    text = entry.get("text", "")
                                    ts2 = entry.get("created_at", entry.get("timestamp", ""))
                                    entry_source = entry.get("source", "")
                                    if text and (ts2, text) not in seen_message_tuples and entry_source == "tui":
                                        seen_message_tuples.add((ts2, text))
                                        # Check if this looks like an assistant response
                                        if entry_source in ("assistant", "bot", "partner") or entry.get("sender") == "assistant":
                                            stripped_text = str(text).strip()
                                            if not assistant_reply:
                                                assistant_reply = stripped_text
                            except json.JSONDecodeError:
                                pass

                    # Update position
                    last_response_pos[resp_path] = f.tell()
                except Exception:
                    pass

            if assistant_reply:
                # Clear thinking line and display the reply
                print("\r" + " " * 60 + "\r", end="", flush=True)
                print("  %s %s" % (_color("\U0001f916 Partner:", C_GREEN), assistant_reply))
                print()
                return

            # ── 4. Update thinking animation (only if no pipeline/task log was printed) ──
            if not pipeline_printed_this_cycle:
                elapsed = int(time.time() - start_time)
                frame = thinking_frames[frame_idx % len(thinking_frames)]
                frame_idx += 1
                print("\r  %s" % _color("%s \u601d\u8003\u4e2d... (%ds)" % (frame, elapsed), C_YELLOW), end="", flush=True)

    except KeyboardInterrupt:
        print("\r" + " " * 60 + "\r", end="", flush=True)
        print("  %s" % _color("\u23f9 \u5df2\u53d6\u6d88\u7b49\u5f85", C_YELLOW))
        print()
        return

    # ── Timeout ──
    print("\r" + " " * 60 + "\r", end="", flush=True)
    print("  %s" % _color("⏱ 超时，Partner 未在120秒内响应", C_RED))
    print("  %s" % _color("💡 请检查:", C_DIM))
    print("    %s" % _color("- 实例是否运行: partner gateway status", C_DIM))
    print("    %s" % _color("- 实例日志: partner gateway logs", C_DIM))
    print("    %s" % _color("- 运行: partner gateway start", C_DIM))
    print()


# ── Progress monitor (background polling thread) ──

_last_task_log_pos = {}  # path -> last position read


class ProgressMonitor:
    """Poll active_plan.json and task_log.jsonl for progress updates."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.state_dir = os.path.join(workspace, "state")
        self.plan_path = os.path.join(self.state_dir, "active_plan.json")
        self.task_log_path = os.path.join(self.state_dir, "task_log.jsonl")
        self._last_plan_mtime = 0
        self._last_phases_snapshot = []
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _poll_loop(self):
        while self._running:
            try:
                self._check_plan_progress()
                self._check_task_log()
                self._check_chat_history()
            except Exception:
                pass
            time.sleep(2)

    def _check_plan_progress(self):
        if not os.path.exists(self.plan_path):
            return
        try:
            mtime = os.path.getmtime(self.plan_path)
            if mtime <= self._last_plan_mtime:
                return
            self._last_plan_mtime = mtime

            with open(self.plan_path, "r") as f:
                plan = json.load(f)

            phases = plan.get("phases", [])
            current_idx = plan.get("current_phase_index", 0)
            status = plan.get("status", "idle")

            if not phases:
                return

            # Format a compact progress line
            parts = []
            for i, phase in enumerate(phases):
                pstatus = phase.get("status", "pending")
                ptitle = phase.get("title") or phase.get("action", f"Step {i+1}")
                if i == current_idx and pstatus == "active":
                    parts.append(f"{_color('▶', C_YELLOW)} {ptitle}")
                elif pstatus == "completed":
                    parts.append(f"{_color('✓', C_GREEN)} {ptitle}")
                elif pstatus == "failed":
                    parts.append(f"{_color('✗', C_RED)} {ptitle}")
                else:
                    parts.append(f"{_color('○', C_DIM)} {ptitle}")

            summary = plan.get("heartbeat_summary", "")
            if summary and status != "idle":
                print(f"\r  {_color(f'[{status}]', C_CYAN)} {' │ '.join(parts)}")
                print(f"  {_color('→', C_DIM)} {summary[:120]}")

            if status == "completed":
                print(f"  {_color('✅ Plan completed!', C_GREEN)}")

        except Exception:
            pass

    def _check_task_log(self):
        if not os.path.exists(self.task_log_path):
            return
        try:
            last_pos = _last_task_log_pos.get(self.task_log_path, 0)
            with open(self.task_log_path, "r") as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                _last_task_log_pos[self.task_log_path] = f.tell()

            for line in new_lines:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        event = entry.get("event", "")
                        msg = entry.get("message", "")
                        if event == "step_complete":
                            print(f"  {_color('✓', C_GREEN)} {msg}")
                        elif event == "step_start":
                            print(f"  {_color('▶', C_YELLOW)} {msg}")
                        elif event == "error":
                            print(f"  {_color('✗', C_RED)} {msg}")
                        elif event == "info":
                            print(f"  {_color('ℹ', C_DIM)} {msg}")
                        else:
                            print(f"  {_color('→', C_DIM)} {msg}")
                    except json.JSONDecodeError:
                        print(f"  {_color(f'→ {line}', C_DIM)}")
        except Exception:
            pass

    def _check_chat_history(self):
        """Silently track position in qq_chat_history — no output."""
        qq_hist_path = os.path.join(self.state_dir, "qq_chat_history.jsonl")
        if not os.path.exists(qq_hist_path):
            return
        try:
            last_pos = _last_task_log_pos.get(qq_hist_path, 0)
            with open(qq_hist_path, "r") as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                _last_task_log_pos[qq_hist_path] = f.tell()
        except Exception:
            pass


# ── Main TUI loop ──

def cmd_tui(args):
    """Enter interactive TUI mode."""
    import readline

    readline.set_completer(_try_complete)
    readline.parse_and_bind("tab: complete")

    _print_banner()

    workspace_root = _get_workspace()
    if not workspace_root:
        print(f"  {_color('⚠ No workspace configured.', C_YELLOW)}")
        print(f"  {_color('Run: partner setup or partner onboard', C_DIM)}")
        print()
        return

    # ── List available instances and let user select ──
    instances_dir = os.path.join(workspace_root, "instances")
    available_instances = {}
    if os.path.isdir(instances_dir):
        for entry in sorted(os.listdir(instances_dir)):
            inst_path = os.path.join(instances_dir, entry)
            if os.path.isdir(inst_path):
                available_instances[entry] = inst_path

    instance_workspace = None
    if len(available_instances) == 0:
        print(f"  {_color('⚠ No instances found.', C_YELLOW)}")
        print(f"  {_color('Create one with: partner setup', C_DIM)}")
        print()
        return
    elif len(available_instances) == 1:
        inst_id = next(iter(available_instances.keys()))
        instance_workspace = available_instances[inst_id]
        print(f"  {_color(f'Instance: {inst_id} ({instance_workspace})', C_DIM)}")
    else:
        print(f"  {_color('Available instances:', C_CYAN)}")
        ids = sorted(available_instances.keys())
        for i, inst_id in enumerate(ids, 1):
            print(f"    {i}. {inst_id}")
        print()
        try:
            choice = input(f"  {_color('Select instance [1]:', C_CYAN)} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print(f"  {_color('Goodbye!', C_GREEN)}")
            return
        if not choice:
            choice = "1"
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(ids):
                inst_id = ids[idx]
            else:
                inst_id = ids[0]
        except ValueError:
            inst_id = choice if choice in available_instances else ids[0]
        instance_workspace = available_instances[inst_id]
        print(f"  {_color(f'Instance: {inst_id} ({instance_workspace})', C_DIM)}")

    if instance_workspace:
        inbox_path = os.path.join(instance_workspace, "state", "desktop_inbox.jsonl")
        print(f"  {_color(f'Inbox: {inbox_path}', C_DIM)}")
        print()

        # Show recent conversation history
        _show_recent_history(instance_workspace, 20)

    # Start background progress monitor
    monitor = None
    if instance_workspace:
        monitor = ProgressMonitor(instance_workspace)
        monitor.start()
        _show_status(instance_workspace)

    try:
        while True:
            try:
                prompt = f"{_color('partner> ', C_CYAN)}"
                line = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print(f"\n  {_color('Goodbye!', C_GREEN)}")
                break

            if not line:
                continue

            if line.startswith("/"):
                cmd = line.lower().split()[0]
                rest = line[len(cmd):].strip()

                if cmd == "/quit" or cmd == "/exit":
                    print(f"  {_color('Goodbye!', C_GREEN)}")
                    break
                elif cmd == "/help":
                    _print_help()
                elif cmd == "/status":
                    _show_status(instance_workspace)
                elif cmd == "/stop":
                    print(f"  {_color('Stopping current task...', C_YELLOW)}")
                    if instance_workspace:
                        plan_path = os.path.join(instance_workspace, "state", "active_plan.json")
                        if os.path.exists(plan_path):
                            try:
                                plan = {
                                    "status": "idle",
                                    "title": "",
                                    "goal": "",
                                    "created_at": datetime.now().isoformat(),
                                    "current_phase_index": 0,
                                    "phases": [],
                                    "last_heartbeat": datetime.now().isoformat(),
                                    "heartbeat_summary": "Stopped by user via TUI",
                                }
                                with open(plan_path, "w", encoding="utf-8") as f:
                                    json.dump(plan, f, indent=2, ensure_ascii=False)
                                print(f"  {_color('✅ Task stopped', C_GREEN)}")
                            except Exception as e:
                                print(f"  {_color(f'Error: {e}', C_RED)}")
                elif cmd == "/clear":
                    # Clear screen using ANSI escape
                    print("\033[2J\033[H", end="")
                    _print_banner()
                    if instance_workspace:
                        inst_name = os.path.basename(os.path.normpath(instance_workspace))
                        ws_parent = os.path.basename(os.path.dirname(os.path.normpath(instance_workspace)))
                        if ws_parent == "instances":
                            print(f"  {_color(f'Instance: {inst_name} ({instance_workspace})', C_DIM)}")
                        else:
                            print(f"  {_color(f'Workspace: {instance_workspace}', C_DIM)}")
                        print()
                elif cmd == "/instances":
                    _show_instances()
                elif cmd == "/tasks":
                    _show_tasks(instance_workspace)
                else:
                    print(f"  {_color(f'Unknown command: {cmd}', C_RED)}")
                    print(f"  {_color('Type /help for available commands', C_DIM)}")
            else:
                # Regular text input — send as a message to Partner via inbox
                if instance_workspace:
                    ok = _write_to_inbox(instance_workspace, line)
                    if ok:
                        truncated = line[:100] + ("..." if len(line) > 100 else "")
                        print(f"  {_color('📨 Message sent', C_GREEN)}")
                        print(f"  {_color(truncated, C_DIM)}")
                        print(f"  {_color('⏳ 处理中... (Ctrl+C 取消)', C_DIM)}")
                        print()
                        # Stop background monitor to avoid concurrent stdout writes
                        if monitor:
                            monitor.stop()
                        # Wait for pipeline progress and response
                        _wait_for_response(instance_workspace)
                        # Restart background monitor
                        if monitor:
                            monitor.start()
                    else:
                        print(f"  {_color('📨 Message queued (inbox write failed, but continuing)', C_YELLOW)}")
                        print()
                else:
                    print(f"  {_color('❌ No workspace configured. Run partner status or partner onboard first.', C_RED)}")
                    print()
    finally:
        if monitor:
            monitor.stop()


def register_subparser(sub):
    """Register the 'tui' subcommand."""
    p = sub.add_parser("tui", help=_cli_txt("交互式终端模式", "Interactive terminal mode"))
    p.set_defaults(func=cmd_tui)
