"""/admin → Users: accounts and what each may open (rights), super admins.

Rules:
  * an account with the Admin right manages accounts that are not administrators;
  * only a super admin (or the admin token) may create, change or delete an administrator or super admin,
    or give someone the Admin right / make them super admin;
  * at least one active super admin always remains.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.constants import RIGHT_LABELS, USER_RIGHTS, USER_ROLES, USERNAME_PATTERN
from backend.app.logs import LOG
from backend.app.routers.accounts import check_contact
from backend.app.security.users import (
    delete_user,
    find_user,
    is_permanent_super_admin,
    is_super_admin,
    is_temporary,
    load_users,
    needs_first_admin,
    save_user,
    set_user_password,
    user_rights,
    users_lock,
)

# Old clients send role instead of rights.
ROLE_RIGHTS = {"viewer": ["server", "runner"], "designer": ["designer", "query", "server", "runner"]}


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the users endpoints to the app."""
    json_body = ctx.json_body
    now_iso = ctx.now_iso
    require_admin = ctx.require_admin
    super_admins_left = ctx.super_admins_left
    user_view = ctx.user_view
    valid_password = ctx.valid_password
    valid_rights = ctx.valid_rights
    valid_role = ctx.valid_role

    def caller_is_super(request: Request) -> bool:
        session = getattr(request.state, "session", None)
        return session is None or bool(session.get("superAdmin"))   # None = the admin token

    def is_administrator(user: dict[str, Any]) -> bool:
        return is_super_admin(user) or "admin" in user_rights(user)

    def apply_access(user: dict[str, Any], payload: dict[str, Any]) -> None:
        if "rights" in payload or "superAdmin" in payload:
            was_super = is_super_admin(user)            # read before "rights" is set (older accounts: role admin)
            user["rights"] = valid_rights(payload.get("rights", sorted(user_rights(user))))
            user["superAdmin"] = bool(payload.get("superAdmin", was_super))
        elif "role" in payload:
            role = valid_role(payload["role"])
            user["superAdmin"] = role == "admin"
            user["rights"] = list(USER_RIGHTS) if role == "admin" else ROLE_RIGHTS[role]
        user.pop("role", None)

    def guard_administrators(request: Request, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
        if caller_is_super(request):
            return
        touched = [item for item in (before, after) if item is not None and is_administrator(item)]
        if touched:
            raise HTTPException(status_code=403, detail="Only a super admin may create, change or delete administrators")

    def end_setup_admins(request: Request, users: list[dict[str, Any]], admin: dict[str, Any]) -> list[str]:
        """Once a lasting super admin is made (or helped back in) the setup admin goes, whether its time is over or not."""
        if not is_permanent_super_admin(admin):
            return []
        gone = [item["username"] for item in users if is_temporary(item)]
        users[:] = [item for item in users if not is_temporary(item)]
        for username in gone:
            LOG.info("Setup admin %s removed: a lasting super admin exists", username)
            ctx.audit_event(request, "user.deleted", username, {"reason": "setup admin replaced by a new admin"})
        return gone

    @app.get("/api/admin/users")
    def admin_list_users(request: Request, search: str = "", right: str = "", status: str = "", offset: int = 0,
                         limit: int = 0) -> dict[str, Any]:
        """All accounts, or (with limit) one page of them: search in username, name, e-mail and phone; filter by a
        right or a status (admin, disabled, never, locked-out names are not shown here). total = matches before paging."""
        require_admin(request)
        views = [user_view(item) for item in load_users()]
        needle = search.strip().casefold()
        if needle:
            views = [v for v in views if any(needle in str(v.get(f) or "").casefold() for f in ("username", "fullName", "email", "phone"))]
        if right:
            views = [v for v in views if right in v["rights"] or (right == "admin" and v["superAdmin"])]
        if status == "disabled":
            views = [v for v in views if v["disabled"]]
        elif status == "active":
            views = [v for v in views if not v["disabled"]]
        elif status == "never":
            views = [v for v in views if not v.get("lastLoginAt")]
        elif status == "super":
            views = [v for v in views if v["superAdmin"]]
        views.sort(key=lambda v: (not v["superAdmin"], str(v["username"]).casefold()))
        total = len(views)
        if limit > 0:
            offset = max(0, offset)
            views = views[offset:offset + min(limit, 200)]
        return {"users": views, "total": total, "offset": max(0, offset), "needsFirstAdmin": needs_first_admin(),
                "roles": list(USER_ROLES), "rights": [{"id": right, "label": RIGHT_LABELS[right]} for right in USER_RIGHTS],
                "youAreSuperAdmin": caller_is_super(request)}

    @app.post("/api/admin/users")
    async def admin_create_user(request: Request) -> dict[str, Any]:
        require_admin(request)
        payload = await json_body(request)
        username = str(payload.get("username", "")).strip()
        if not USERNAME_PATTERN.fullmatch(username):
            raise HTTPException(status_code=422, detail="Username: 2-64 letters, numbers, dot, @, _ or -")
        with users_lock():
            return create_user(request, payload, username)

    def create_user(request: Request, payload: dict[str, Any], username: str) -> dict[str, Any]:
        """The rest of admin_create_user, while holding the accounts lock (two admins cannot take one name)."""
        if find_user(username) is not None:
            raise HTTPException(status_code=409, detail={"field": "username", "message": "A user with this name already exists"})
        new_user: dict[str, Any] = {"username": username, "sessionVersion": 0, "createdAt": now_iso(),
                                    "createdBy": getattr(request.state, "actor", ""), "mustChangePassword": True,
                                    **check_contact(payload, required_email=payload.get("requireEmail", True) is not False)}
        apply_access(new_user, payload if ("rights" in payload or "superAdmin" in payload or "role" in payload) else {"role": "viewer"})
        guard_administrators(request, None, new_user)
        set_user_password(new_user, valid_password(payload.get("password")))
        users = load_users()
        users.append(new_user)
        removed = end_setup_admins(request, users, new_user)
        save_user(new_user, by=getattr(request.state, "actor", ""))
        for gone in removed:
            delete_user(gone)
        LOG.info("User %s created by %s (rights %s%s)", username, getattr(request.state, "actor", "?"),
                 ",".join(new_user["rights"]), ", super admin" if new_user["superAdmin"] else "")
        ctx.audit_event(request, "user.created", username, {"rights": new_user["rights"], "superAdmin": new_user["superAdmin"]})
        return {"user": user_view(new_user), "removedSetupAdmins": removed}

    @app.put("/api/admin/users/{username}")
    async def admin_update_user(username: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        payload = await json_body(request)
        users = load_users()
        user = next((item for item in users if str(item["username"]).casefold() == username.casefold()), None)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        if is_temporary(user) and ("rights" in payload or "superAdmin" in payload or "role" in payload):
            raise HTTPException(status_code=409, detail="The setup admin is temporary: create your own admin instead of changing it")
        before, super_admins_before = dict(user), super_admins_left(users)
        apply_access(user, payload)
        user.update(check_contact({key: payload[key] for key in ("fullName", "email", "phone") if key in payload},
                                  required_email=False, username=user["username"]))
        if "disabled" in payload:
            user["disabled"] = bool(payload["disabled"])
        if payload.get("password"):
            set_user_password(user, valid_password(payload["password"]))
            user["mustChangePassword"] = True                          # set by an admin: the user picks their own next
        guard_administrators(request, before, user)
        if super_admins_before and not super_admins_left(users):
            raise HTTPException(status_code=409, detail="Keep at least one active super admin")
        user["sessionVersion"] = int(user.get("sessionVersion", 0)) + 1  # signs this user out everywhere
        removed = end_setup_admins(request, users, user)
        save_user(user, by=getattr(request.state, "actor", ""))
        for gone in removed:
            delete_user(gone)
        LOG.info("User %s changed by %s", user["username"], getattr(request.state, "actor", "?"))
        change: dict[str, Any] = {}
        if sorted(user_rights(before)) != sorted(user_rights(user)):
            change["rights"] = {"from": sorted(user_rights(before)), "to": sorted(user_rights(user))}
        if is_super_admin(before) != is_super_admin(user):
            change["superAdmin"] = is_super_admin(user)
        if bool(before.get("disabled")) != bool(user.get("disabled")):
            change["disabled"] = bool(user.get("disabled"))
        if payload.get("password"):
            change["passwordReset"] = True
        ctx.audit_event(request, "user.changed", user["username"], change)
        return {"user": user_view(user), "removedSetupAdmins": removed}

    @app.delete("/api/admin/users/{username}")
    def admin_delete_user(username: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        users = load_users()
        target = next((item for item in users if str(item["username"]).casefold() == username.casefold()), None)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        guard_administrators(request, target, None)
        remaining = [item for item in users if item is not target]
        if super_admins_left(users) and not super_admins_left(remaining):
            raise HTTPException(status_code=409, detail="Keep at least one active super admin")
        delete_user(target["username"])
        LOG.info("User %s deleted by %s", target["username"], getattr(request.state, "actor", "?"))
        ctx.audit_event(request, "user.deleted", target["username"])
        return {"status": "deleted", "username": username}
