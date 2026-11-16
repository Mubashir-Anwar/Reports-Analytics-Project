"""Helpers for the /admin user accounts endpoints.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from backend.app.constants import MIN_PASSWORD_LENGTH, USER_RIGHTS, USER_ROLES
from backend.app.security.users import derived_role, is_super_admin, user_rights

__all__ = ['user_view', 'valid_role', 'valid_rights', 'valid_password', 'admins_left', 'super_admins_left']


def user_view(ctx: SimpleNamespace, user: dict[str, Any]) -> dict[str, Any]:
    rights, super_admin = user_rights(user), is_super_admin(user)
    return {"username": user["username"], "role": derived_role(rights, super_admin), "rights": sorted(rights),
            "superAdmin": super_admin, "disabled": bool(user.get("disabled")),
            "createdAt": user.get("createdAt", ""), "lastLoginAt": user.get("lastLoginAt", "")}


def valid_role(ctx: SimpleNamespace, value: Any) -> str:
    role = str(value or "").strip().lower()
    if role not in USER_ROLES:
        raise HTTPException(status_code=422, detail="role must be viewer, designer or admin")
    return role


def valid_rights(ctx: SimpleNamespace, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise HTTPException(status_code=422, detail="rights must be a list such as [\"designer\", \"server\"]")
    unknown = sorted(set(value) - set(USER_RIGHTS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown rights: {', '.join(unknown)} (use {', '.join(USER_RIGHTS)})")
    return [right for right in USER_RIGHTS if right in value]


def valid_password(ctx: SimpleNamespace, value: Any) -> str:
    password = str(value or "")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=422, detail=f"The password needs at least {MIN_PASSWORD_LENGTH} characters")
    return password


def super_admins_left(ctx: SimpleNamespace, users: list[dict[str, Any]]) -> int:
    return sum(1 for item in users if is_super_admin(item) and not item.get("disabled"))


def admins_left(ctx: SimpleNamespace, users: list[dict[str, Any]]) -> int:
    """Kept for older callers: active super admins."""
    return super_admins_left(ctx, users)
