"""Top-level Flask app factory (M1 / Section 10).

Registers auth, api routes, and the static frontend (under
``partner/web/static``).  Binds to 127.0.0.1 by default; do not
expose publicly without a reverse proxy that enforces Origin checks.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

_log = logging.getLogger("partner.web.app")


def create_app(*, workspace_root: str | None = None) -> Any:
    try:
        from flask import Flask
    except ImportError:
        raise RuntimeError("Flask not installed; run `pip install flask`")
    app = Flask(
        "partner_web",
        static_folder=str(Path(__file__).parent / "static"),
        static_url_path="/static",
    )
    workspace = workspace_root or os.environ.get("PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace")
    if workspace:
        app.config["PARTNER_WORKSPACE"] = workspace

    from partner.web.auth import install_auth
    from partner.web.api import register_api_routes
    install_auth(app)
    register_api_routes(app)

    @app.get("/")
    def root():
        return app.send_static_file("index.html")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "workspace": workspace}

    return app


def main() -> None:
    """CLI entry point.

    Binds to 127.0.0.1 by default.  A non-loopback host (e.g. 0.0.0.0) is
    refused unless PARTNER_WEB_ALLOW_PUBLIC is explicitly set, so the web
    console cannot silently become publicly reachable.
    """
    host = os.environ.get("PARTNER_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("PARTNER_WEB_PORT", "8765"))
    allow_public = os.environ.get("PARTNER_WEB_ALLOW_PUBLIC", "").strip().lower() in ("1", "true", "yes", "on")
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not allow_public:
        raise SystemExit(
            f"refusing to bind non-loopback host {host!r}; set PARTNER_WEB_ALLOW_PUBLIC=1 to opt in"
        )
    create_app().run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
