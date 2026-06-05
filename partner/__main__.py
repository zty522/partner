"""Support 'python -m partner' entry point (main module)."""

import argparse
import json
import os
import sys
import time

from partner.instance_root import resolve_instance_workspace

# Set UTF-8 encoding for cross-platform compatibility
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")


def _looks_like_instance_launch(argv: list[str]) -> bool:
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

    from partner.config import PartnerConfig, resolve_partner_config_path, save_partner_config_data
    from partner.core import Partner
    from partner.mind import set_file_push_callback, set_push_callback
    from partner.qq_official_bridge import QQQfficialBridge, QQMessageType
    from partner.restart_tracker import RestartTracker

    tracker = RestartTracker(workspace)
    tracker.record_restart()
    if tracker.should_stop():
        count = tracker.get_restart_count()
        print(
            f"Partner 实例 '{args.instance_id}' 在最近1小时内启动/重启 "
            f"{count} 次。可能是手动重启、部署重启或异常恢复；本次继续启动。"
            f"如需确认真实崩溃，请查看 10_logs/crash.log。"
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

    cfg = os.path.join(workspace, "00_config", "qq_config.json")
    if not os.path.exists(cfg):
        cfg = os.path.join(workspace, "qq_config.json")
    if os.path.exists(cfg):
        bridge = QQQfficialBridge(workspace)
        bridge.load_config_from_file(cfg)

        def _push_to_last_user(content: str):
            ctx_path = os.path.join(workspace, "state", "qq_user_context.json")
            try:
                with open(ctx_path, "r", encoding="utf-8") as f:
                    ctx = json.load(f)
            except Exception as exc:
                print(f"QQ proactive push skipped: no qq_user_context.json ({exc})")
                return False
            openid = ctx.get("openid")
            if not openid:
                print("QQ proactive push skipped: missing openid in qq_user_context.json")
                return False
            return bridge.send_proactive(openid, content, QQMessageType.PRIVATE, bypass_quiet=True)

        set_push_callback(_push_to_last_user)

        def _push_file_to_last_user(file_data: bytes, filename: str = "", caption: str = ""):
            ctx_path = os.path.join(workspace, "state", "qq_user_context.json")
            try:
                with open(ctx_path, "r", encoding="utf-8") as f:
                    ctx = json.load(f)
            except Exception as exc:
                print(f"QQ proactive file push skipped: no qq_user_context.json ({exc})")
                return False
            openid = ctx.get("openid")
            if not openid:
                print("QQ proactive file push skipped: missing openid in qq_user_context.json")
                return False
            text = caption or filename or "Partner 阶段汇报"
            return bridge.send_file_proactive(
                openid,
                file_data,
                4,
                QQMessageType.PRIVATE,
                text_content=text,
                file_name=filename,
            )

        set_file_push_callback(_push_file_to_last_user)
        try:
            bridge.start()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"QQ bridge failed, keeping Partner mind loop alive: {exc}")
        print("QQ bridge stopped or unavailable; Partner mind loop remains running.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            sys.exit(0)
        return

    print(f"Partner instance '{args.instance_id}': no qq_config.json found at {cfg}; running without QQ bridge.")
    try:
        while True:
            time.sleep(3600)
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
