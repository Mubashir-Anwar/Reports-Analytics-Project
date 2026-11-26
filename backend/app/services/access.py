"""Folder permissions applied to the /server endpoints, schedules and subreports (see security/permissions.py).

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from backend.app.constants import BOOTSTRAP_USERNAME
from backend.app.definitions import ReportDefinition
from backend.app.security import permissions
from backend.app.security.users import derived_role, find_user, is_expired, is_super_admin, user_rights

__all__ = ['report_folder', 'folder_level', 'report_level', 'require_report', 'require_folder', 'session_for_user',
           'report_allowed_for']


def report_folder(ctx: SimpleNamespace, definition: ReportDefinition) -> str:
    return str((definition.editor_config or {}).get("folder", "General"))


def folder_level(ctx: SimpleNamespace, session: dict[str, Any], folder: str, rules: dict | None = None) -> int:
    return permissions.level(session, folder, rules)


def report_level(ctx: SimpleNamespace, session: dict[str, Any], definition: ReportDefinition, rules: dict | None = None) -> int:
    return permissions.level(session, ctx.report_folder(definition), rules)


# On the Report Server reports are for the Designer or Server right, dashboards for the Analytics right (an admin sees both).
def sees_reports(session: dict[str, Any]) -> bool:
    return bool(set(session.get("rights") or ()) & {"designer", "server", "admin"})


def sees_dashboards(session: dict[str, Any]) -> bool:
    return bool(set(session.get("rights") or ()) & {"analytics", "admin"})


def require_server_reports(session: dict[str, Any]) -> None:
    if not sees_reports(session):
        raise HTTPException(status_code=403, detail="Reports on the Report Server need the Designer or Server right (ask an administrator)")


def require_server_dashboards(session: dict[str, Any]) -> None:
    if not sees_dashboards(session):
        raise HTTPException(status_code=403, detail="Dashboards on the Report Server need the Analytics right (ask an administrator)")


def require_report(ctx: SimpleNamespace, session: dict[str, Any], report_id: str, needed: int = permissions.RUN,
                   folder: str | None = None) -> ReportDefinition:
    """The published report if the user may use it at `needed`; a hidden report looks like a missing one (404).
    `folder` chooses between reports with the same ID; without it the only one this user can see is taken."""
    rules = permissions.load_rules()
    definition = ctx.reports_by_id.find(report_id, folder, lambda item: ctx.report_level(session, item, rules) >= permissions.RUN)
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
    if username.startswith("apikey:"):
        from backend.app.security import api_keys  # noqa: PLC0415
        key = api_keys.key_for_owner(username[7:])
        # folder limits of the key are checked by the runner; inside them the key may run every report
        return {"username": username, "role": "admin", "rights": {"runner"}, "apiKey": key} if key else None
    user = find_user(username)
    if user is None and username == BOOTSTRAP_USERNAME:
        return {"username": username, "role": "admin", "rights": {"server", "runner", "admin", "designer"}}
    if user is None or user.get("disabled") or is_expired(user):
        return None
    rights = user_rights(user)
    if "server" not in rights:                 # the Report Server right was taken away: the schedule stops running
        return None
    return {"username": user["username"], "role": derived_role(rights, is_super_admin(user)), "rights": rights}


def report_allowed_for(ctx: SimpleNamespace, session: dict[str, Any] | None):
    """A check for subreports: may this user run the report with this ID? (None session = no limit: token access.)"""
    if session is None:
        return None
    rules = permissions.load_rules()

    def allowed(definition: ReportDefinition) -> bool:
        return ctx.report_level(session, definition, rules) >= permissions.RUN
    return allowed
