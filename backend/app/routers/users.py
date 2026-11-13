"""/admin → Users: server accounts and roles."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.constants import USER_ROLES, USERNAME_PATTERN
from backend.app.logs import LOG
from backend.app.security.users import _hash_password, bootstrap_login_active, load_users, save_users


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the users endpoints to the app."""
    admins_left = ctx.admins_left
    json_body = ctx.json_body
    now_iso = ctx.now_iso
    require_admin = ctx.require_admin
    user_view = ctx.user_view
    valid_password = ctx.valid_password
    valid_role = ctx.valid_role

    @app.get("/api/admin/users")
    def admin_list_users(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {"users": [user_view(item) for item in load_users()], "bootstrapLogin": bootstrap_login_active(),
                "roles": list(USER_ROLES)}

    @app.post("/api/admin/users")
    async def admin_create_user(request: Request) -> dict[str, Any]:
        require_admin(request)
        payload = await json_body(request)
        username = str(payload.get("username", "")).strip()
        if not USERNAME_PATTERN.fullmatch(username):
            raise HTTPException(status_code=422, detail="Username: 2-64 letters, numbers, dot, @, _ or -")
        role = valid_role(payload.get("role", "viewer"))
        salt, digest = _hash_password(valid_password(payload.get("password")))
        users = load_users()
        if any(str(item["username"]).casefold() == username.casefold() for item in users):
            raise HTTPException(status_code=409, detail="A user with this name already exists")
        users.append({"username": username, "role": role, "salt": salt, "hash": digest, "sessionVersion": 0,
                      "createdAt": now_iso()})
        save_users(users)
        LOG.info("Server user %s created (%s)", username, role)
        return {"user": user_view(users[-1])}

    @app.put("/api/admin/users/{username}")
    async def admin_update_user(username: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        payload = await json_body(request)
        users = load_users()
        user = next((item for item in users if str(item["username"]).casefold() == username.casefold()), None)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        before = admins_left(users)
        if "role" in payload:
            user["role"] = valid_role(payload["role"])
        if "disabled" in payload:
            user["disabled"] = bool(payload["disabled"])
        if payload.get("password"):
            user["salt"], user["hash"] = _hash_password(valid_password(payload["password"]))
        if before and not admins_left(users):
            raise HTTPException(status_code=409, detail="Keep at least one active admin user")
        user["sessionVersion"] = int(user.get("sessionVersion", 0)) + 1  # signs this user out everywhere
        save_users(users)
        return {"user": user_view(user)}

    @app.delete("/api/admin/users/{username}")
    def admin_delete_user(username: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        users = load_users()
        remaining = [item for item in users if str(item["username"]).casefold() != username.casefold()]
        if len(remaining) == len(users):
            raise HTTPException(status_code=404, detail="User not found")
        if admins_left(users) and not admins_left(remaining):
            raise HTTPException(status_code=409, detail="Keep at least one active admin user")
        save_users(remaining)
        return {"status": "deleted", "username": username}
