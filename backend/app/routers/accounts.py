"""People and access (schema v2 step 2): name / e-mail / phone checks, teams, "who can see what", forgotten
passwords and My account.

Forgot password has two ways, both rows in password_resets:
  * email_link      a link that works once for 30 minutes, e-mailed to the account's address. Needs SMTP_* and
                    REPORTING_PUBLIC_URL (the link is never built from the request's Host header, which a caller
                    could fake to have the link point at their own site).
  * admin_approval  the request waits in Admin → Users; an administrator approves it and gets a one-time link
                    (24 hours) to pass on, or rejects it.
The answer to "forgot password" is always the same, so nobody can find out which accounts exist.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.constants import USERNAME_PATTERN
from backend.app.db import entities as ent, model, people
from backend.app.logs import LOG
from backend.app.security import permissions
from backend.app.security.access import client_address
from backend.app.security.users import check_user_password, derived_role, is_super_admin, load_users, set_user_password, user_rights

EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9 ()-]{5,30}$")
LINK_MINUTES = 30
APPROVED_LINK_HOURS = 24
GENERIC_ANSWER = ("If the account exists, the request was sent. With an e-mailed link, check your mail (it works once, "
                  "for 30 minutes); otherwise an administrator will contact you.")


def check_contact(payload: dict[str, Any], *, required_email: bool, username: str = "") -> dict[str, str | None]:
    """fullName / email / phone from a form, checked (format, and not used by another account)."""
    out: dict[str, str | None] = {}
    if "fullName" in payload:
        out["fullName"] = str(payload.get("fullName") or "").strip()[:120] or None
    if "email" in payload or required_email:
        email = str(payload.get("email") or "").strip()
        if not email and required_email:
            raise HTTPException(status_code=422, detail="Give the user's e-mail address (password reset links go there)")
        if email and (len(email) > 254 or not EMAIL_PATTERN.fullmatch(email)):
            raise HTTPException(status_code=422, detail="This e-mail address does not look right")
        if email and people.taken("email", email, except_username=username):
            raise HTTPException(status_code=409, detail={"field": "email", "message": "Another account already uses this e-mail address"})
        out["email"] = email or None
    if "phone" in payload:
        phone = str(payload.get("phone") or "").strip()
        if phone and not PHONE_PATTERN.fullmatch(phone):
            raise HTTPException(status_code=422, detail="Phone: digits, spaces, ( ) and - only, e.g. +92 300 1234567")
        if phone and people.taken("phone", phone, except_username=username):
            raise HTTPException(status_code=409, detail={"field": "phone", "message": "Another account already uses this phone number"})
        out["phone"] = phone or None
    return out


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_url() -> str:
    return os.getenv("REPORTING_PUBLIC_URL", "").strip().rstrip("/")


def email_links_possible() -> bool:
    from backend.app.jobs import mailer  # noqa: PLC0415
    return bool(_public_url()) and mailer.configured()


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    json_body = ctx.json_body
    require_admin = ctx.require_admin
    require_json_request = ctx.require_json_request
    current_session = ctx.current_session

    def actor(request: Request) -> str:
        return str(getattr(request.state, "actor", "") or "admin")[:128]

    # ---------------------------------------------------------------- "already taken" while typing
    @app.get("/api/admin/users/available")
    def users_available(request: Request, username: str = "", email: str = "", phone: str = "", exceptUser: str = "") -> dict[str, Any]:
        """New user / edit user: which of these values another account already has (for administrators only)."""
        require_admin(request)
        answer: dict[str, Any] = {}
        for field, value in (("username", username), ("email", email), ("phone", phone)):
            if value.strip():
                answer[field] = {"taken": people.taken(field, value, except_username=exceptUser)}
        if username.strip() and not USERNAME_PATTERN.fullmatch(username.strip()):
            answer["username"] = {**answer.get("username", {}), "problem": "2-64 letters, numbers, dot, @, _ or -"}
        if email.strip() and not EMAIL_PATTERN.fullmatch(email.strip()):
            answer["email"] = {**answer.get("email", {}), "problem": "does not look like an e-mail address"}
        return answer

    # ---------------------------------------------------------------- teams (user_groups)
    @app.get("/api/admin/user-groups")
    def groups_list(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {"groups": people.load_groups()}

    async def group_payload(request: Request) -> tuple[str, str, list[str]]:
        body = await json_body(request)
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 64:
            raise HTTPException(status_code=422, detail="Give the team a name (at most 64 characters)")
        members = body.get("members") or []
        if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
            raise HTTPException(status_code=422, detail="members must be a list of usernames")
        return name, str(body.get("description") or "").strip()[:300], members

    @app.post("/api/admin/user-groups")
    async def groups_create(request: Request) -> dict[str, Any]:
        require_admin(request)
        name, description, members = await group_payload(request)
        try:
            group = people.save_group(name, description, members, by=actor(request))
        except ValueError as exc:
            raise HTTPException(status_code=409 if "already" in str(exc) else 422, detail=str(exc)) from None
        ctx.audit_event(request, "group.created", name, {"members": group["members"]})
        return {"group": group}

    @app.put("/api/admin/user-groups/{group_id}")
    async def groups_update(group_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        name, description, members = await group_payload(request)
        try:
            group = people.save_group(name, description, members, group_id=group_id, by=actor(request))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=409 if "already" in str(exc) else 422, detail=str(exc)) from None
        ctx.audit_event(request, "group.changed", name, {"members": group["members"]})
        return {"group": group}

    @app.delete("/api/admin/user-groups/{group_id}")
    def groups_delete(group_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        if not people.delete_group(group_id):
            raise HTTPException(status_code=404, detail="This team does not exist")
        ctx.audit_event(request, "group.deleted", group_id)
        return {"status": "deleted", "groupId": group_id}

    # ---------------------------------------------------------------- who can see what
    @app.get("/api/admin/access-report")
    def access_report(request: Request) -> dict[str, Any]:
        """Every active user on every server folder that has a rule (and the root): level and why."""
        require_admin(request)
        from backend.app.storage.store import get_store  # noqa: PLC0415
        rules = permissions.load_rules()
        folders = sorted({"", *get_store().dirs("reports")}, key=str.casefold)
        rows = []
        for user in load_users():
            if user.get("disabled"):
                continue
            rights = user_rights(user)
            role = derived_role(rights, is_super_admin(user))
            for folder in folders:
                if "server" in rights or role == "admin":
                    level, why = permissions.explain({"username": user["username"], "role": role}, folder, rules)
                else:
                    level, why = permissions.NONE, "no Report Server right"
                rows.append({"username": user["username"], "fullName": user.get("fullName") or "", "role": role,
                             "folder": folder, "level": permissions.NAMES[level], "why": why})
        return {"rows": rows, "folders": folders}

    # ---------------------------------------------------------------- forgot password
    @app.get("/api/server/password-help")
    def password_help() -> dict[str, Any]:
        """Which ways the sign-in page may offer."""
        return {"emailLink": email_links_possible(), "adminApproval": True}

    @app.post("/api/server/forgot-password")
    async def forgot_password(request: Request) -> dict[str, Any]:
        require_json_request(request)
        body = await json_body(request)
        who = str(body.get("username") or "").strip()[:254]
        method = "email_link" if body.get("method") == "email_link" else "admin_approval"
        client = client_address(request)
        from backend.app.security import shared_state  # noqa: PLC0415
        if shared_state.recent_failures(f"reset:{client}") >= 5:
            raise HTTPException(status_code=429, detail="Too many requests; wait a few minutes and try again")
        shared_state.record_failure(f"reset:{client}")         # every request counts, found or not
        row = people.find_user_row(who) if who else None
        if row is None and who and "@" in who:
            row = next((item for item in ent.get_entities().find(ent.USERS_V2) if str(item.get("email") or "").casefold() == who.casefold()), None)
        if row is None or row.get("isDisabled"):
            LOG.info("Password reset asked for an unknown or disabled account from %s", client)
            return {"message": GENERIC_ANSWER}
        db = ent.get_entities()
        for waiting in db.find(ent.PASSWORD_RESETS, {"userId": row["userId"], "status": "waiting"}):
            db.put(ent.PASSWORD_RESETS, {**waiting, "status": "expired"})     # only the newest request counts
        reset = {"resetId": model.new_id(), "userId": row["userId"], "requestedAt": model.now(), "clientIp": client[:64]}
        if method == "email_link" and email_links_possible() and row.get("email"):
            token = secrets.token_urlsafe(32)
            db.put(ent.PASSWORD_RESETS, {**reset, "method": "email_link", "status": "waiting", "tokenHash": _digest(token),
                                         "expiresAt": model.now() + timedelta(minutes=LINK_MINUTES)})
            from backend.app.jobs import mailer  # noqa: PLC0415
            try:
                mailer.send([row["email"]], "Reset your password",
                            f"Someone (hopefully you) asked to reset the password of {row['username']}.\n\n"
                            f"Open this link within {LINK_MINUTES} minutes to choose a new one (it works once):\n"
                            f"{_public_url()}/login?reset={token}\n\nIf it was not you, ignore this message.")
            except Exception as exc:  # noqa: BLE001 - the mail server's own errors
                LOG.error("The password reset e-mail for %s could not be sent: %s", row["username"], exc)
        else:
            db.put(ent.PASSWORD_RESETS, {**reset, "method": "admin_approval", "status": "waiting"})
        ctx.audit_event(request, "auth.password_reset_requested", row["username"], {"method": method}, actor=row["username"], actor_type="anonymous")
        return {"message": GENERIC_ANSWER}

    @app.get("/api/admin/password-resets")
    def resets_list(request: Request) -> dict[str, Any]:
        require_admin(request)
        names = {row["userId"]: row for row in ent.get_entities().find(ent.USERS_V2)}
        items = []
        for row in ent.get_entities().find(ent.PASSWORD_RESETS, order=("requestedAt", True), limit=200):
            user = names.get(row["userId"])
            if user is None:
                continue
            items.append({key: row.get(key) for key in ("resetId", "method", "status", "requestedAt", "expiresAt", "decidedBy", "decidedAt",
                                                        "usedAt", "clientIp")} | {"username": user["username"], "fullName": user.get("fullName") or "",
                                                                                  "email": user.get("email") or ""})
        return {"requests": items, "waiting": sum(1 for item in items if item["status"] == "waiting" and item["method"] == "admin_approval")}

    @app.post("/api/admin/password-resets/{reset_id}/{decision}")
    def resets_decide(reset_id: str, decision: str, request: Request) -> dict[str, Any]:
        """approve: answers a one-time link (24 hours) to pass on to the user; reject: the request is closed."""
        require_admin(request)
        if decision not in ("approve", "reject"):
            raise HTTPException(status_code=404, detail="Use approve or reject")
        db = ent.get_entities()
        row = db.get(ent.PASSWORD_RESETS, reset_id)
        if row is None or row["status"] != "waiting" or row["method"] != "admin_approval":
            raise HTTPException(status_code=409, detail="This request is not waiting any more")
        user = db.get(ent.USERS_V2, row["userId"])
        if user is None:
            raise HTTPException(status_code=404, detail="The account is gone")
        if is_super_admin({"superAdmin": user.get("isSuperAdmin")}) and not (getattr(request.state, "session", None) or {"superAdmin": True}).get("superAdmin", True):
            raise HTTPException(status_code=403, detail="Only a super admin may approve this for a super admin")
        done = {**row, "decidedBy": actor(request), "decidedAt": model.now()}
        if decision == "reject":
            db.put(ent.PASSWORD_RESETS, {**done, "status": "rejected"})
            ctx.audit_event(request, "auth.password_reset_rejected", user["username"])
            return {"status": "rejected"}
        token = secrets.token_urlsafe(32)
        db.put(ent.PASSWORD_RESETS, {**done, "status": "approved", "tokenHash": _digest(token),
                                     "expiresAt": model.now() + timedelta(hours=APPROVED_LINK_HOURS)})
        ctx.audit_event(request, "auth.password_reset_approved", user["username"])
        base = _public_url() or str(request.base_url).rstrip("/")   # shown to the admin only, never e-mailed
        return {"status": "approved", "link": f"{base}/login?reset={token}", "hours": APPROVED_LINK_HOURS, "username": user["username"]}

    @app.post("/api/server/reset-password")
    async def reset_password(request: Request) -> dict[str, Any]:
        """Set a new password with the token of an e-mailed or approved link (works once)."""
        require_json_request(request)
        body = await json_body(request)
        token, password = str(body.get("token") or ""), str(body.get("password") or "")
        db = ent.get_entities()
        wanted = _digest(token) if token else ""
        row = next((item for item in db.find(ent.PASSWORD_RESETS) if wanted and secrets.compare_digest(str(item.get("tokenHash") or ""), wanted)), None)
        usable = row is not None and row["status"] in ("waiting", "approved") and row.get("expiresAt") and \
            model.parse_time(row["expiresAt"]) > model.now()
        if not usable:
            raise HTTPException(status_code=400, detail="This link does not work any more. Ask for a new one")
        user = next((item for item in load_users() if item.get("userId") == row["userId"]), None)
        if user is None or user.get("disabled"):
            raise HTTPException(status_code=400, detail="This link does not work any more. Ask for a new one")
        set_user_password(user, ctx.valid_password(password))
        user.update({"passwordChangedAt": model.now(), "mustChangePassword": False,
                     "sessionVersion": int(user.get("sessionVersion", 0)) + 1})          # signs every old session out
        people.put_user(user)
        db.put(ent.PASSWORD_RESETS, {**row, "status": "used", "usedAt": model.now()})
        ctx.audit_event(request, "auth.password_reset_used", user["username"], {"method": row["method"]},
                        actor=user["username"], actor_type="user")
        return {"status": "changed", "username": user["username"]}

    # ---------------------------------------------------------------- My account
    def me(request: Request) -> dict[str, Any]:
        session = current_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Sign in with your own account first")
        user = next((item for item in load_users() if str(item["username"]).casefold() == str(session["username"]).casefold()), None)
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in first")
        return user

    def account_view(user: dict[str, Any]) -> dict[str, Any]:
        return {"username": user["username"], "fullName": user.get("fullName") or "", "email": user.get("email") or "",
                "phone": user.get("phone") or "", "rights": sorted(user_rights(user)), "superAdmin": is_super_admin(user),
                "groups": [group["name"] for group in people.load_groups() if user["username"] in group["members"]],
                "passwordChangedAt": user.get("passwordChangedAt") or "", "mustChangePassword": bool(user.get("mustChangePassword")),
                "lastLoginAt": user.get("lastLoginAt") or ""}

    @app.get("/api/server/account")
    def account_get(request: Request) -> dict[str, Any]:
        return account_view(me(request))

    @app.put("/api/server/account")
    async def account_update(request: Request) -> dict[str, Any]:
        """Your own name, e-mail and phone."""
        require_json_request(request)
        user = me(request)
        body = await json_body(request)
        user.update(check_contact({key: body[key] for key in ("fullName", "email", "phone") if key in body},
                                  required_email=False, username=user["username"]))
        if not user.get("email"):
            raise HTTPException(status_code=422, detail="Keep an e-mail address on your account (password reset links go there)")
        people.put_user(user)
        return account_view(user)

    @app.post("/api/server/account/password")
    async def account_password(request: Request) -> dict[str, Any]:
        """Change your own password: the current one first."""
        require_json_request(request)
        user = me(request)
        body = await json_body(request)
        if not check_user_password(user, str(body.get("currentPassword") or "")):
            raise HTTPException(status_code=403, detail="The current password is not right")
        new = ctx.valid_password(body.get("newPassword"))
        if check_user_password(user, new):
            raise HTTPException(status_code=422, detail="Choose a password different from the current one")
        set_user_password(user, new)
        user.update({"passwordChangedAt": model.now(), "mustChangePassword": False})
        people.put_user(user)                                  # this session stays signed in (same session version)
        ctx.audit_event(request, "user.password_changed", user["username"], actor=user["username"], actor_type="user")
        return {"status": "changed"}
