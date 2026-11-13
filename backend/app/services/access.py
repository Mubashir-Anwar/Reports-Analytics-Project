"""Folder permissions applied to the /server endpoints, schedules and subreports (see security/permissions.py).

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from backend.app.constants import BOOTSTRAP_USERNAME
from backend.app.definitions import ReportDefinition
from backend.app.security import permissions
from backend.app.security.users import find_user

__all__ = ['report_folder', 'folder_level', 'report_level', 'require_report', 'require_folder', 'session_for_user',
           'report_allowed_for']


def report_folder(ctx: SimpleNamespace, definition: ReportDefinition) -> str:
    return str((definition.editor_config or {}).get("folder", "General"))


def folder_level(ctx: SimpleNamespace, session: dict[str, Any], folder: str, rules: dict | None = None) -> int:
    return permissions.level(session, folder, rules)


def report_level(ctx: SimpleNamespace, session: dict[str, Any], definition: ReportDefinition, rules: dict | None = None) -> int:
    return permissions.level(session, ctx.report_folder(definition), rules)


def require_report(ctx: SimpleNamespace, session: dict[str, Any], report_id: str, needed: int = permissions.RUN) -> ReportDefinition:
    """The published report if the user may use it at `needed`; a hidden report looks like a missing one (404)."""
    definition = ctx.reports_by_id.get(report_id)
    if definition is None or definition.editor_config is None:
        raise HTTPException(status_code=404, detail="This report is not published on the server")
    have = ctx.report_level(session, definition)
    if have < permissions.RUN:
        raise HTTPException(status_code=404, detail="This report is not published on the server")
    if have < needed:
        raise HTTPException(status_code=403, detail=f"You may only run the reports in /{ctx.report_folder(definition)}; changing them needs the Manage permission")
    return definition


def require_folder(ctx: SimpleNamespace, session: dict[str, Any], folder: str, needed: int = permissions.MANAGE) -> None:
    have = permissions.level(session, folder)
    if have < needed:
        word = "see" if needed == permissions.RUN else "change"
        raise HTTPException(status_code=403, detail=f"You may not {word} the folder /{folder or ''} (ask an administrator for the permission)")


def session_for_user(ctx: SimpleNamespace, username: str) -> dict[str, Any] | None:
    """The user's current role, for work done later on their behalf (schedules). None = the user is gone or disabled."""
    if username == BOOTSTRAP_USERNAME:
        return {"username": username, "role": "admin"}
    user = find_user(username)
    if user is None or user.get("disabled"):
        return None
    return {"username": user["username"], "role": user.get("role", "viewer")}


def report_allowed_for(ctx: SimpleNamespace, session: dict[str, Any] | None):
    """A check for subreports: may this user run the report with this ID? (None session = no limit: token access.)"""
    if session is None:
        return None
    rules = permissions.load_rules()

    def allowed(report_id: str) -> bool:
        definition = ctx.reports_by_id.get(report_id)
        return definition is not None and ctx.report_level(session, definition, rules) >= permissions.RUN
    return allowed
