"""Authentication / Origin / CSRF / session scaffold (M1 / Section 10).

Source of truth for HTTP-side identity.  The ``/login`` endpoint
materialises a session from a Bearer token (issued by the operator);
``/logout`` revokes it.  All other endpoints require a valid session.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
from typing import Any

try:
    from flask import request, jsonify, abort, session
    _HAVE_FLASK = True
except Exception:
    _HAVE_FLASK = False


_log = logging.getLogger("partner.web.auth")


DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = (
    "http://127.0.0.1:8765",
    "http://localhost:8765",
)


_SECRET_CACHE: str | None = None


def _secret() -> bytes:
    global _SECRET_CACHE
    s = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("PARTNER_WEB_AUTH_SECRET") or ""
    if not s:
        # Bootstrapping only — production deployments MUST set FLASK_SECRET_KEY.
        # Cache the random bootstrap secret so issue_token() and install_auth()
        # agree within one process; otherwise every call mints a fresh secret
        # and a just-issued token can never verify.
        if _SECRET_CACHE is None:
            _SECRET_CACHE = secrets.token_hex(32)
        s = _SECRET_CACHE
    return s.encode("utf-8")


def issue_token(*, subject_id: str, allowed_instances: list[str],
                 display_name: str = "") -> str:
    """Operator-facing token issuer.

    Returns a signed token: ``{subject_id}|{display_name}|{allowed_csv}|{hmac}``.
    """
    allowed_csv = ",".join(sorted(allowed_instances))
    payload = f"{subject_id}|{display_name}|{allowed_csv}".encode("utf-8")
    sig = hmac.new(_secret(), payload, "sha256").hexdigest()[:32]
    return f"{subject_id}|{display_name}|{allowed_csv}|{sig}"


def verify_token(token: str) -> dict | None:
    try:
        subject_id, display_name, allowed_csv, sig = token.split("|", 3)
    except ValueError:
        return None
    payload = f"{subject_id}|{display_name}|{allowed_csv}".encode("utf-8")
    expected = hmac.new(_secret(), payload, "sha256").hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    return {
        "subject_id": subject_id,
        "display_name": display_name or subject_id,
        "allowed_instances": [s.strip() for s in allowed_csv.split(",") if s.strip()],
    }


def install_auth(app: Any) -> None:
    if not _HAVE_FLASK:
        return
    # Flask's Config pre-populates SECRET_KEY with None, so setdefault()
    # never overwrites it — the session then reports "no secret key was
    # set".  Assign explicitly (and only when unset) instead.
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _secret().decode("utf-8")

    @app.before_request
    def _csrf():
        # CSRF: only non-GET requests must carry a custom header that
        # browser forms cannot forge.
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        token = request.headers.get("X-CSRF", "")
        if not token or token != session.get("csrf_token", ""):
            # Exception: /login carries an operator token via Authorization.
            if request.path == "/login":
                return None
            abort(403, description="missing or invalid CSRF token")

    @app.get("/login")
    @app.post("/login")
    def login() -> Any:
        # POST: exchange a Bearer token for a session.
        if request.method == "POST":
            auth = request.headers.get("Authorization", "")
            token = auth[len("Bearer "):] if auth.startswith("Bearer ") else (
                request.json or {}).get("token", "")
            if not token:
                abort(400, description="missing token")
            claims = verify_token(token)
            if not claims:
                abort(401, description="invalid token")
            session["subject"] = claims
            session["csrf_token"] = secrets.token_urlsafe(16)
            return jsonify({"subject": claims, "csrf_token": session["csrf_token"]})
        # GET: redirect to the static frontend login page.
        from flask import redirect
        return redirect("/static/index.html#/login", code=302)

    @app.post("/logout")
    def logout() -> Any:
        session.clear()
        return jsonify({"status": "logged_out"})
