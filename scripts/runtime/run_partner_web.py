#!/usr/bin/env python3
"""Start the loopback Partner Observatory and print a local access token."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Partner web observatory")
    parser.add_argument("--workspace", default=os.environ.get(
        "PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("This launcher only binds to loopback")
    os.environ["PARTNER_WEB_LOCAL_AUTOLOGIN"] = "1"
    os.environ["PARTNER_WORKSPACE"] = str(Path(args.workspace).resolve())
    from partner.web.app import create_app
    url = f"http://{args.host}:{args.port}/"
    print("\nPartner Observatory 已准备好", flush=True)
    print("访问地址：" + url, flush=True)
    print("已启用 loopback 本机会话；不生成或打印访问凭据。\n", flush=True)
    create_app(workspace_root=os.environ["PARTNER_WORKSPACE"]).run(
        host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
