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

from backend.app.constants import (
    BOOTSTRAP_USERNAME,
    MIN_ACCESS_SECRET_LENGTH,
    RIGHT_LABELS,
    ROLE_TOKEN_MIN_LENGTH,
    SESSION_COOKIE,
    USER_RIGHTS,
)
from backend.app.logs import LOG
from backend.app.security import api_keys
from backend.app.security.access import client_address, enabled_areas
from backend.app.security.shared_state import is_revoked, replaced, replaced_and_over
from backend.app.security.users import (
    bootstrap_login_active,
    derived_role,
    find_user,
    is_super_admin,
    read_session_cookie,
    role_at_least,
    user_rights,
)

__all__ = ['_verify_signed_link', 'require_report_access', 'report_in_scope', 'unknown_report', 'require_admin', 'json_body', 'require_json_request',
           'current_session', 'require_session', 'require_right', 'session_view']


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
    """Who may list and run reports (/reports, /generate). Returns how access was granted (stored in the audit record).

    REPORTING_ACCESS_MODE=required (default) accepts any one of:
      * a signed-in user with the "Run reports" right (report URLs such as /generate; folder permissions still apply),
      * an API key in X-API-Key or "Authorization: Bearer mrk_..." (a client website's server; its folder limits apply),
      * a signed /generate link: Expires + Signature made with REPORTING_ACCESS_SECRET, or issued to an API key (KeyId),
      * X-Report-Token header equal to REPORTING_ACCESS_SECRET, or X-Admin-Token (automation).
    request.state.report_scope is set to a check that the caller may run a given report (see report_in_scope).
    REPORTING_ACCESS_MODE=open keeps the old behaviour for a private, network-protected setup.
    """
    request.state.report_scope = None
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
    bearer = request.headers.get("Authorization", "")
    presented = request.headers.get("X-API-Key", "") or (bearer[7:] if bearer[:7].lower() == "bearer " else "")
    if presented:
        key = api_keys.key_from_token(presented, client_address(request))
        if key is None:
            LOG.warning("Unknown, disabled or expired API key used for %s", request.url.path)
            raise HTTPException(status_code=401, detail="The API key is not valid, disabled, expired or not allowed from this address")
        wait = api_keys.over_limit(key)
        if wait:
            raise HTTPException(status_code=429, detail=f"Too many requests for this API key; try again in {wait} s",
                                headers={"Retry-After": str(wait)})
        request.state.report_scope = ("key", key)
        return f"api-key:{key['name']}"
    if report_file is not None and "Signature" in request.query_params:
        key_id = request.query_params.get("KeyId")
        if key_id is not None:
            key = api_keys.key_by_id(key_id, client_address(request))
            if key is None:
                raise HTTPException(status_code=403, detail="The API key of this link is disabled, expired or not allowed from this address")
            ctx._verify_signed_link(request, report_file, api_keys.link_secret())
            request.state.report_scope = ("key", key)
            return f"signed-link:{key['name']}"
        if not secret:
            raise HTTPException(status_code=403, detail="REPORTING_ACCESS_SECRET is not set on the server, so signed links cannot be checked")
        ctx._verify_signed_link(request, report_file, secret)
        return "signed-link"
    session = ctx.current_session(request)
    if session is not None:
        if "runner" not in session["rights"]:
            raise HTTPException(status_code=403, detail="Your account does not have the Run reports right")
        request.state.report_scope = ("user", session)
        return f"user:{session['username']}"
    LOG.warning("Report access denied for %s", request.url.path)
    raise HTTPException(
        status_code=401,
        detail="Report access needs a sign-in, an API key, a signed link or the X-Report-Token header",
    )


def report_in_scope(ctx: SimpleNamespace, request: Request, definition: Any) -> bool:
    """May the caller found by require_report_access run this report? (users: folder permissions; keys: their folders)"""
    scope = getattr(request.state, "report_scope", None)
    if scope is None:
        return True
    kind, who = scope
    if kind == "key":
        return api_keys.folder_allowed(who, ctx.report_folder(definition))
    from backend.app.security import permissions  # noqa: PLC0415 - avoid an import cycle at start-up
    return ctx.report_level(who, definition) >= permissions.RUN


def unknown_report(ctx: SimpleNamespace, request: Request, report_id: str, folder: str | None = None) -> HTTPException:
    """404 for a report ID that is not published (or outside the caller's folders), naming the closest IDs the
    caller may run, e.g. "KFS_FAL" -> "Did you mean FAL_KFS?". Only IDs the caller could run anyway are named."""
    import difflib  # noqa: PLC0415

    seen = [item for item in ctx.reports_by_id.values() if report_in_scope(ctx, request, item)]
    if folder is not None:
        others = sorted(item.folder for item in seen if item.report_id == report_id)
        if others:                                   # the ID is there, in other folders the caller may use
            return HTTPException(status_code=404, detail=f"Report ID '{report_id}' is not in the folder '{folder}'; "
                                                         f"it is in: {', '.join(others[:5])}")
    visible = sorted({item.report_id for item in seen})
    wanted = report_id.lower()
    folded = {rid.lower(): rid for rid in visible}
    close = difflib.get_close_matches(wanted, list(folded), n=3, cutoff=0.5)
    same_words = [rid for rid in visible if sorted(filter(None, rid.lower().replace("-", "_").split("_")))
                  == sorted(filter(None, wanted.replace("-", "_").split("_")))]
    names = list(dict.fromkeys(same_words + [folded[item] for item in close]))[:3]
    detail = f"Report ID '{report_id}' is not published"
    if names:
        detail += ". Did you mean " + " or ".join(names) + "?"
    elif visible:
        detail += " (the report IDs are on the Report Server and in GET /api/v1/reports)"
    return HTTPException(status_code=404, detail=detail)


def require_admin(ctx: SimpleNamespace, request: Request, role: str = "admin") -> str:
    """Check the caller of a management endpoint (/api/admin/...) and return "admin" or "designer".

    A signed-in user needs the right for the area: role="admin" needs the Admin right; role="designer" needs
    the Designer right (an administrator may also use these). Automation may still send X-Admin-Token
    (REPORTING_ADMIN_TOKEN, full access) or REPORTING_DESIGNER_TOKEN (designer endpoints only).
    request.state.actor names the caller for history records.
    An endpoint whose area is switched off with REPORTING_ENABLED_AREAS answers 404.
    """
    areas = enabled_areas()
    if (role == "admin" and "admin" not in areas) or (role == "designer" and not areas & {"admin", "designer"}):
        raise HTTPException(status_code=404, detail="Not Found")
    provided = request.headers.get("X-Admin-Token", "")
    if provided:
        expected = os.getenv("REPORTING_ADMIN_TOKEN", "")
        if expected and secrets.compare_digest(provided, expected):
            request.state.actor = "admin token"
            request.state.session = None
            return "admin"
        designer_token = os.getenv("REPORTING_DESIGNER_TOKEN", "").strip()
        if len(designer_token) >= ROLE_TOKEN_MIN_LENGTH and secrets.compare_digest(provided, designer_token):
            if role == "designer":
                request.state.actor = "designer token"
                request.state.session = None
                return "designer"
            raise HTTPException(status_code=403, detail="This action needs the admin token; the designer token cannot change data sources, delete items or read logs")
        raise HTTPException(status_code=401, detail="The admin token is missing or wrong")
    session = ctx.current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in first (your session has ended or you are not signed in)")
    rights = session["rights"]
    if role == "admin" and "admin" not in rights:
        raise HTTPException(status_code=403, detail="Your account does not have the Admin right")
    if role == "designer" and not rights & {"designer", "admin"}:
        raise HTTPException(status_code=403, detail="Your account does not have the Designer right")
    if request.method not in ("GET", "HEAD"):
        ctx.require_json_request(request)
    request.state.actor = session["username"]
    request.state.session = session
    return "admin" if "admin" in rights else "designer"


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
    """The signed-in user with their rights; role is viewer/designer/admin for folder permissions."""
    body = read_session_cookie(request.cookies.get(SESSION_COOKIE, ""))
    if body is None or is_revoked(body.get("n")) or replaced_and_over(body.get("n")):
        return None
    if body.get("b"):
        if not bootstrap_login_active():
            return None
        return {"username": BOOTSTRAP_USERNAME, "role": "admin", "rights": set(USER_RIGHTS), "superAdmin": True,
                "bootstrap": True, "nonce": body.get("n")}
    user = find_user(str(body.get("u", "")))
    if user is None or user.get("disabled") or int(user.get("sessionVersion", 0)) != int(body.get("v", -1)):
        return None
    rights, super_admin = user_rights(user), is_super_admin(user)
    return {"username": user["username"], "role": derived_role(rights, super_admin), "rights": rights, "superAdmin": super_admin,
            "bootstrap": False, "nonce": body.get("n")}


def require_right(ctx: SimpleNamespace, request: Request, right: str) -> dict[str, Any]:
    """A signed-in user who has `right` (401 when signed out, 403 without the right)."""
    session = ctx.current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in first")
    if right not in session["rights"]:
        raise HTTPException(status_code=403, detail=f"Your account does not have the {RIGHT_LABELS.get(right, right)} right")
    return session


def require_session(ctx: SimpleNamespace, request: Request, minimum: str = "viewer") -> dict[str, Any]:
    """A /server user: needs the Report Server right, then the minimum role (folder permissions do the rest)."""
    session = ctx.current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in to the report server first")
    if "server" not in session["rights"]:
        raise HTTPException(status_code=403, detail="Your account does not have the Report Server right")
    if not role_at_least(session["role"], minimum):
        raise HTTPException(status_code=403, detail=f"This needs a {minimum} account; you are signed in as {session['role']}")
    if request.method not in ("GET", "HEAD"):
        ctx.require_json_request(request)
    return session


def session_view(ctx: SimpleNamespace, session: dict[str, Any]) -> dict[str, Any]:
    return {"username": session["username"], "role": session["role"], "bootstrap": session["bootstrap"],
            "rights": sorted(session["rights"]), "superAdmin": session["superAdmin"], "replaced": replaced_notice(session)}


def replaced_notice(session: dict[str, Any]) -> dict[str, Any] | None:
    """{secondsLeft, by} while this browser's session is being replaced by a sign-in somewhere else."""
    import time  # noqa: PLC0415
    item = replaced(session.get("nonce"))
    if not item:
        return None
    return {"secondsLeft": max(0, round(item["logoutAt"] - time.time())), "by": item.get("agent", "")}
