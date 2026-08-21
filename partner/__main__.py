"""Support 'python -m partner' entry point (main module)."""

import argparse
import json
import os
import re
import sys
import time
import logging
from datetime import datetime

from partner.monitoring.instance_root import resolve_instance_workspace
from partner.workspace.workspace_layout import append_history, ensure_instance_layout

# Set UTF-8 encoding for cross-platform compatibility
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


KNOWN_CLI_COMMANDS = frozenset({
    "setup", "status", "help", "doctor", "start", "stop", "restart",
    "bot", "update", "instance", "showcase", "server", "ollama",
    "onboard", "gateway", "world-model", "wm", "tui",
    "queue", "config",
})


def _looks_like_instance_launch(argv: list[str]) -> bool:
    # If first arg is a known CLI command, it's NOT an instance launch
    if argv and argv[0] in KNOWN_CLI_COMMANDS:
        return False
    return any(arg == "--instance-id" or arg.startswith("--instance-id=") or
               arg == "--workspace" or arg.startswith("--workspace=")
               for arg in argv)


def _run_instance_mode(argv: list[str]):
    parser = argparse.ArgumentParser(prog="python -m partner")
    parser.add_argument("--instance-id", default=os.environ.get("PARTNER_INSTANCE_ID", "default"))
    parser.add_argument("--workspace", default=os.environ.get("PARTNER_WORKSPACE", ""))
    args = parser.parse_args(argv)

    if args.instance_id != "default" or args.workspace:
        os.environ["PARTNER_INSTANCE_ID"] = args.instance_id
    if args.workspace:
        os.environ["PARTNER_WORKSPACE"] = args.workspace

    workspace = args.workspace or str(resolve_instance_workspace(args.instance_id))
    ensure_instance_layout(workspace)
    # Check for stale PID file from a killed instance
    pid_file = os.path.join(workspace, "instance.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
        except (FileNotFoundError, ValueError, OSError):
            # Process is dead — remove stale PID and proceed
            try:
                os.remove(pid_file)
            except Exception:
                pass
            # Also clean stale lock if no process holds it
            lock_file = os.path.join(workspace, "state", "instance_runtime.lock")
            try:
                os.remove(lock_file)
            except Exception:
                pass
    try:
        from partner.monitoring.instance_lock import InstanceAlreadyRunning, acquire_instance_lock

        _instance_lock = acquire_instance_lock(workspace, args.instance_id)
    except InstanceAlreadyRunning as exc:
        print(f"Partner instance '{args.instance_id}' is already running; this duplicate start will exit. {exc}")
        return

    # Write PID file BEFORE any imports that may fail (e.g. shells/QQ bridge).
    # This ensures the GUI can detect the instance as running even when QQ is
    # not configured or shell imports fail due to missing cwd/PYTHONPATH.
    try:
        pid_path = os.path.join(workspace, "instance.pid")
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))
    except Exception as exc:
        print(f"Failed to write PID file: {exc}", flush=True)

    from partner.state.config import PartnerConfig, resolve_partner_config_path, save_partner_config_data, _config_root
    from partner.core.core import Partner
    from partner.monitoring.restart_tracker import RestartTracker

    tracker = RestartTracker(workspace)
    tracker.record_restart()

    # QQ bridge import is optional — the instance works fine without it.
    QQQfficialBridge = None
    QQMessageType = None
    try:
        from shells.frontend.qq_bot.qq_official_bridge import QQQfficialBridge as _QQB, QQMessageType as _QQT
        QQQfficialBridge = _QQB
        QQMessageType = _QQT
    except ImportError:
        pass

    from partner.mind import set_file_push_callback, set_push_callback
    if tracker.should_stop():
        count = tracker.get_restart_count()
        print(
            f"Partner 实例 '{args.instance_id}' 在最近1小时内启动/重启 "
            f"{count} 次。可能是手动重启、部署重启或异常恢复；本次继续启动。"
            f"如需确认真实崩溃，请查看日志。"
        )

    cfg_path = resolve_partner_config_path(workspace)
    if not os.path.exists(cfg_path):
        root_workspace = str(resolve_instance_workspace(args.instance_id).parent.parent)
        root_cfg_path = resolve_partner_config_path(root_workspace)
        if os.path.exists(root_cfg_path):
            try:
                with open(root_cfg_path, "r", encoding="utf-8") as f:
                    root_cfg = json.load(f)
                root_cfg.setdefault("workspace", {})
                root_cfg["workspace"]["path"] = workspace
                save_partner_config_data(workspace, root_cfg)
                cfg_path = resolve_partner_config_path(workspace)
                print(f"Recovered missing instance config from {root_cfg_path}")
            except Exception as exc:
                print(f"Failed to recover instance config from {root_cfg_path}: {exc}")
    partner_cfg = PartnerConfig.load(cfg_path)
    partner_cfg.workspace.path = workspace
    partner = Partner(partner_cfg)
    partner.start()
    partner.start_mind()

    # Auto-sync skills from central registry on startup
    try:
        from partner.skills.skill_center import sync_skills_to_instance
        count = sync_skills_to_instance(args.instance_id)
        print(f"Instance {args.instance_id} skills synced from central registry ({count} skills)", flush=True)
    except Exception as exc:
        print(f"Skill sync skipped: {exc}", flush=True)

    cfg = os.path.join(_config_root(workspace), "qq_config.json")
    _qq_instance_id = None
    if os.path.exists(cfg):
        try:
            with open(cfg) as _fh:
                _qq_cfg = json.load(_fh)
            _qq_instance_id = str(_qq_cfg.get("instance_id", "")).strip() or None
        except (json.JSONDecodeError, OSError):
            pass
    if _qq_instance_id and _qq_instance_id != args.instance_id:
        cfg = os.path.join(workspace, "qq_config.json")
    elif not os.path.exists(cfg):
        cfg = os.path.join(workspace, "qq_config.json")
    # ── 通用历史记录写入（所有实例都需要，无论有无 QQ） ──
    _qq_bridge = None  # may be set below if QQ config exists

    def _history_file_attachment(file_data: bytes, filename: str = "") -> dict | None:
        if not file_data:
            return None
        safe_name = os.path.basename(str(filename or "partner_file").strip()) or "partner_file"
        safe_name = re.sub(r'[<>:\"/\\|?*\x00-\x1f]+', "_", safe_name).strip(" ._") or "partner_file"
        stored_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{safe_name}"
        from partner.workspace.workspace_layout import outgoing_dir
        out_dir = outgoing_dir(workspace)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, stored_name)
        try:
            with open(path, "wb") as f:
                f.write(file_data)
        except Exception:
            return None
        return {
            "type": "file",
            "name": safe_name,
            "stored_name": stored_name,
            "size": len(file_data),
            "rel_path": os.path.relpath(path, workspace).replace("\\", "/"),
            "server_path": path,
        }

    def _append_proactive_history(content: str, openid: str = "", *, kind: str = "message", attachments: list | None = None):
        text = str(content or "").strip()
        if not text:
            return
        if text in {"思考中.......", "思考中......", "思考中……", "Thinking..."}:
            return
        row = {
            "role": "assistant",
            "content": text,
            "timestamp": datetime.now().isoformat(),
            "source": "qq",
            "channel": "proactive",
            "sender_id": "partner",
            "sender_name": "Partner",
            "target_id": openid,
            "group_id": "",
            "delivery": kind,
        }
        if attachments:
            row["attachments"] = attachments
        try:
            append_history(workspace, row, ("qq_chat_history.jsonl", "dialog_history.jsonl"))
        except Exception:
            pass
        # Also write to daily dialogue log (newest at top)
        if text.startswith("已停止「"):
            return
        try:
            from partner.workspace.workspace_manager import get_dialogue_path
            fpath = get_dialogue_path(workspace)
            ts = datetime.now().strftime("%H:%M:%S")
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            entry = f"[{ts}] [Partner] Partner\n  A: {text}\n\n"
            old_content = b""
            try:
                with open(fpath, "rb") as f:
                    old_content = f.read()
            except FileNotFoundError:
                pass
            with open(fpath, "wb") as f:
                f.write(entry.encode("utf-8"))
                f.write(old_content)
        except Exception:
            pass

    def _push_to_last_user(content: str):
        ctx_path = os.path.join(workspace, "state", "qq_user_context.json")
        try:
            with open(ctx_path, "r", encoding="utf-8") as f:
                ctx = json.load(f)
        except Exception:
            _append_proactive_history(content, "", kind="message")
            return True
        openid = (ctx.get("openid") or ctx.get("last_openid") or ctx.get("last_group_openid") or "").strip()
        if not openid:
            _append_proactive_history(content, "", kind="message")
            return True
        if openid in ("desktop_gui", "tui", "tui_user"):
            _append_proactive_history(content, openid, kind="message")
            return True
        # QQ user — send via bridge if available
        if _qq_bridge is not None:
            ok = _qq_bridge.send_proactive(openid, content, QQMessageType.PRIVATE, bypass_quiet=True)
            if ok:
                _append_proactive_history(content, openid, kind="message")
            return ok
        # No bridge — just write to history
        _append_proactive_history(content, openid, kind="message")
        return True

    set_push_callback(_push_to_last_user)

    def _push_file_to_last_user(file_data: bytes, filename: str = "", caption: str = ""):
        attachment = _history_file_attachment(file_data, filename)
        attachments = [attachment] if attachment else []
        ctx_path = os.path.join(workspace, "state", "qq_user_context.json")
        try:
            with open(ctx_path, "r", encoding="utf-8") as f:
                ctx = json.load(f)
        except Exception as exc:
            print(f"QQ proactive file push skipped: no qq_user_context.json ({exc})")
            _append_proactive_history(caption or filename or "Partner 阶段汇报", "", kind="file", attachments=attachments)
            return True
        openid = (ctx.get("openid") or ctx.get("last_openid") or ctx.get("last_group_openid") or "").strip()
        if not openid:
            print("QQ proactive file push skipped: missing openid in qq_user_context.json")
            _append_proactive_history(caption or filename or "Partner 阶段汇报", "", kind="file", attachments=attachments)
            return True
        text = caption or filename or "Partner 阶段汇报"
        if openid == "desktop_gui":
            _append_proactive_history(text, openid, kind="file", attachments=attachments)
            return True
        # QQ user — send via bridge if available
        if _qq_bridge is not None:
            ok = _qq_bridge.send_file_proactive(
                openid,
                file_data,
                4,
                QQMessageType.PRIVATE,
                text_content=text,
                file_name=filename,
            )
            if ok:
                _append_proactive_history(text, openid, kind="file", attachments=attachments)
            return ok
        # No bridge — just write to history
        _append_proactive_history(text, openid, kind="file", attachments=attachments)
        return True

    set_file_push_callback(_push_file_to_last_user)

    # ── QQ bridge setup（可选） ──
    if os.path.exists(cfg) and QQQfficialBridge is not None:
        _qq_bridge = QQQfficialBridge(workspace)
        _qq_bridge.load_config_from_file(cfg)
        try:
            _qq_bridge.start()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"QQ bridge failed, keeping Partner mind loop alive: {exc}")
        print("QQ bridge stopped or unavailable; Partner mind loop remains running.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            sys.exit(0)
        return

    print(f"Partner instance '{args.instance_id}': no qq_config.json found at {cfg}; running without QQ bridge.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        sys.exit(0)


def main():
    argv = sys.argv[1:]
    if _looks_like_instance_launch(argv):
        _run_instance_mode(argv)
        return

    from partner.cli import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
