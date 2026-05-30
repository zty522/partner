"""Support 'python -m partner' entry point (main module)."""
import sys
import os

# Set UTF-8 encoding for cross-platform compatibility
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import sys, os, argparse
# Parse instance-specific args before falling through to CLI
parser = argparse.ArgumentParser()
parser.add_argument('--instance-id', default=os.environ.get('PARTNER_INSTANCE_ID', 'default'))
parser.add_argument('--workspace', default=os.environ.get('PARTNER_WORKSPACE', ''))
args, remaining = parser.parse_known_args()

# Set environment variables for the bridge to pick up
# ONLY when explicitly provided — not for default 'partner setup' CLI calls
if args.instance_id != 'default' or args.workspace:
    os.environ['PARTNER_INSTANCE_ID'] = args.instance_id
if args.workspace:
    os.environ['PARTNER_WORKSPACE'] = args.workspace

# If running with --instance-id/--workspace (manager or systemd), auto-start bridge
if args.workspace or ('PARTNER_INSTANCE_ID' in os.environ and args.instance_id != 'default'):
    workspace = args.workspace or os.path.join(os.path.expanduser("~"), ".partner", "instances", args.instance_id)

    # ── 重启计数器检查 ──────────────────────────────────────────
    from partner.restart_tracker import RestartTracker
    tracker = RestartTracker(workspace)
    tracker.record_restart()
    if tracker.should_stop():
        count = tracker.get_restart_count()
        print(
            f"Partner 实例 '{args.instance_id}' 在最近1小时内已经崩溃 "
            f"{count} 次，已停止自动恢复。请手动检查。"
        )
        sys.exit(2)

    # ── 启动桥接 ────────────────────────────────────────────────
    from partner.qq_official_bridge import QQQfficialBridge
    # Look for qq_config in 00_config/ first, then workspace root
    cfg = os.path.join(workspace, "00_config", "qq_config.json")
    if not os.path.exists(cfg):
        cfg = os.path.join(workspace, "qq_config.json")
    if os.path.exists(cfg):
        bridge = QQQfficialBridge(workspace)
        bridge.load_config_from_file(cfg)
        bridge.start()
    else:
        print(f"Partner instance '{args.instance_id}': no qq_config.json found at {cfg}")
        sys.exit(1)
else:
    # Normal CLI mode
    from partner.cli import main
    sys.argv = [sys.argv[0]] + remaining
    main()
