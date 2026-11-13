"""Helpers for the /admin user accounts endpoints.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from backend.app.constants import MIN_PASSWORD_LENGTH, USER_ROLES

__all__ = ['user_view', 'valid_role', 'valid_password', 'admins_left']


def user_view(ctx: SimpleNamespace, user: dict[str, Any]) -> dict[str, Any]:
    return {"username": user["username"], "role": user.get("role", "viewer"), "disabled": bool(user.get("disabled")),
            "createdAt": user.get("createdAt", ""), "lastLoginAt": user.get("lastLoginAt", "")}


def valid_role(ctx: SimpleNamespace, value: Any) -> str:
    role = str(value or "").strip().lower()
    if role not in USER_ROLES:
        raise HTTPException(status_code=422, detail="role must be viewer, designer or admin")
    return role


def valid_password(ctx: SimpleNamespace, value: Any) -> str:
    password = str(value or "")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=422, detail=f"The password needs at least {MIN_PASSWORD_LENGTH} characters")
    return password


def admins_left(ctx: SimpleNamespace, users: list[dict[str, Any]]) -> int:
    return sum(1 for item in users if item.get("role") == "admin" and not item.get("disabled"))
