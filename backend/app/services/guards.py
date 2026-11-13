"""Who may call what: report access (signed links / token), admin and designer tokens, /server sessions, JSON bodies.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, Request

from backend.app.constants import BOOTSTRAP_USERNAME, MIN_ACCESS_SECRET_LENGTH, ROLE_TOKEN_MIN_LENGTH, SESSION_COOKIE
from backend.app.logs import LOG
from backend.app.security.access import enabled_areas
from backend.app.security.users import bootstrap_login_active, find_user, read_session_cookie, role_at_least

__all__ = ['_verify_signed_link', 'require_report_access', 'require_admin', 'json_body', 'require_json_request', 'current_session', 'require_session', 'session_view']


def _verify_signed_link(ctx: SimpleNamespace, request: Request, report_file: str, secret: str) -> None:
    """Check `Signature` = hex HMAC-SHA256(secret, "/generate/<file>?<query before &Signature>")."""
    try:
        raw_query = request.scope.get("query_string", b"").decode("ascii")
    except UnicodeDecodeError:
        raise HTTPException(status_code=403, detail="The signed link query is not encoded correctly") from None
    parts = raw_query.split("&") if raw_query else []
    signature_parts = [part for part in parts if part.split("=", 1)[0] == "Signature"]
    if len(signature_parts) != 1 or parts[-1] != signature_parts[0]:
        raise HTTPException(status_code=403, detail="Signature must appear once, at the end of the link")
    provided = signature_parts[0].split("=", 1)[1] if "=" in signature_parts[0] else ""
    unsigned_query = "&".join(parts[:-1])
    message = f"/generate/{report_file}" + (f"?{unsigned_query}" if unsigned_query else "")
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(provided.lower(), expected):
        raise HTTPException(status_code=403, detail="The signed link Signature is not valid (the link was changed or the secret is wrong)")
    expires_values = request.query_params.getlist("Expires")
    if len(expires_values) != 1 or not expires_values[0].isdigit():
        raise HTTPException(status_code=403, detail="A signed link needs one Expires value (Unix seconds)")
    expires = int(expires_values[0])
    now = int(time.time())
    max_lifetime = int(os.getenv("REPORTING_SIGNED_LINK_MAX_SECONDS", "3600"))
    if expires <= now:
        raise HTTPException(status_code=403, detail="The signed link has expired")
    if expires - now > max_lifetime:
        raise HTTPException(status_code=403, detail=f"A signed link cannot be valid for more than {max_lifetime} seconds")


def require_report_access(ctx: SimpleNamespace, request: Request, report_file: str | None = None) -> str:
    """Who may list and run reports. Returns how access was granted (stored in the audit record).

    REPORTING_ACCESS_MODE=required (default) accepts any one of:
      * X-Report-Token header equal to REPORTING_ACCESS_SECRET (website server calls the service),
      * a signed /generate link (Expires + Signature made with REPORTING_ACCESS_SECRET),
      * X-Admin-Token (the admin and runner pages).
    REPORTING_ACCESS_MODE=open keeps the old behaviour for a private, network-protected setup.
    """
    mode = os.getenv("REPORTING_ACCESS_MODE", "required").strip().lower()
    if mode == "open":
        return "open"
    if mode != "required":
        raise HTTPException(status_code=503, detail="REPORTING_ACCESS_MODE must be 'required' or 'open'")
    secret = os.getenv("REPORTING_ACCESS_SECRET", "").strip()
    if secret and len(secret) < MIN_ACCESS_SECRET_LENGTH:
        raise HTTPException(
            status_code=503,
            detail=f"REPORTING_ACCESS_SECRET must be at least {MIN_ACCESS_SECRET_LENGTH} characters long",
        )
    report_token = request.headers.get("X-Report-Token", "")
    if secret and report_token and secrets.compare_digest(report_token, secret):
        return "report-token"
    admin_token = os.getenv("REPORTING_ADMIN_TOKEN", "")
    admin_header = request.headers.get("X-Admin-Token", "")
    if admin_token and admin_header and secrets.compare_digest(admin_header, admin_token):
        return "admin-token"
    if report_file is not None and "Signature" in request.query_params:
        if not secret:
            raise HTTPException(status_code=403, detail="REPORTING_ACCESS_SECRET is not set on the server, so signed links cannot be checked")
        ctx._verify_signed_link(request, report_file, secret)
        return "signed-link"
    LOG.warning("Report access denied for %s", request.url.path)
    raise HTTPException(
        status_code=401,
        detail="Report access needs a signed link, the X-Report-Token header or the admin token",
    )


def require_admin(ctx: SimpleNamespace, request: Request, role: str = "admin") -> str:
    """Check the caller's token for a management endpoint and return the caller's role.

    role="admin": only REPORTING_ADMIN_TOKEN (data sources, deleting, logs).
    role="designer": REPORTING_ADMIN_TOKEN or REPORTING_DESIGNER_TOKEN (build, preview and run reports).
    An endpoint whose area is switched off with REPORTING_ENABLED_AREAS answers 404.
    """
    areas = enabled_areas()
    if (role == "admin" and "admin" not in areas) or (role == "designer" and not areas & {"admin", "designer"}):
        raise HTTPException(status_code=404, detail="Not Found")
    expected = os.getenv("REPORTING_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Set the REPORTING_ADMIN_TOKEN environment variable to use the management API")
    provided = request.headers.get("X-Admin-Token", "")
    if provided and secrets.compare_digest(provided, expected):
        return "admin"
    designer_token = os.getenv("REPORTING_DESIGNER_TOKEN", "").strip()
    if len(designer_token) >= ROLE_TOKEN_MIN_LENGTH and provided and secrets.compare_digest(provided, designer_token):
        if role == "designer":
            return "designer"
        raise HTTPException(status_code=403, detail="This action needs the admin token; the designer token cannot change data sources, delete items or read logs")
    raise HTTPException(status_code=401, detail="The admin token is missing or wrong")


async def json_body(ctx: SimpleNamespace, request: Request, *, optional: bool = False) -> dict[str, Any]:
    try:
        raw = await request.body()
        payload = json.loads(raw) if raw.strip() else ({} if optional else None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Send a JSON object")
    return payload


def require_json_request(ctx: SimpleNamespace, request: Request) -> None:
    """Cookie-authenticated writes must be JSON: a cross-site HTML form cannot send that."""
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise HTTPException(status_code=415, detail="Send the request as application/json")


def current_session(ctx: SimpleNamespace, request: Request) -> dict[str, Any] | None:
    body = read_session_cookie(request.cookies.get(SESSION_COOKIE, ""))
    if body is None or body.get("n") in ctx.state["revoked_sessions"]:
        return None
    if body.get("b"):
        if not bootstrap_login_active():
            return None
        return {"username": BOOTSTRAP_USERNAME, "role": "admin", "bootstrap": True, "nonce": body.get("n")}
    user = find_user(str(body.get("u", "")))
    if user is None or user.get("disabled") or int(user.get("sessionVersion", 0)) != int(body.get("v", -1)):
        return None
    return {"username": user["username"], "role": user.get("role", "viewer"), "bootstrap": False, "nonce": body.get("n")}


def require_session(ctx: SimpleNamespace, request: Request, minimum: str = "viewer") -> dict[str, Any]:
    session = ctx.current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in to the report server first")
    if not role_at_least(session["role"], minimum):
        raise HTTPException(status_code=403, detail=f"This needs a {minimum} account; you are signed in as {session['role']}")
    if request.method not in ("GET", "HEAD"):
        ctx.require_json_request(request)
    return session


def session_view(ctx: SimpleNamespace, session: dict[str, Any]) -> dict[str, Any]:
    return {"username": session["username"], "role": session["role"], "bootstrap": session["bootstrap"]}
