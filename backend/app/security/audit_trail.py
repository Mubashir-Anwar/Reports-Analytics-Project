"""Which successful changes go into the audit log, by route. Recorded after the response, by the app middleware.

Accounts, API keys and sign-ins write their own, more detailed events (routers/users.py, api_keys.py, server_portal.py).
Draft autosaves (PUT /api/admin/workspace/…) are not audited on purpose: only publishing is.
"""
from __future__ import annotations

import json
from typing import Any

# (method, route template) -> action
ACTIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/server/folders"): "folder.created",
    ("DELETE", "/api/server/folders"): "folder.deleted",
    ("DELETE", "/api/server/reports/{report_id}"): "report.deleted",
    ("PUT", "/api/server/reports/{report_id}"): "report.changed",
    ("POST", "/api/server/reports/{report_id}/copy"): "report.copied",
    ("POST", "/api/server/reports/{report_id}/move"): "report.moved",
    ("POST", "/api/server/upload"): "report.uploaded",
    ("POST", "/api/server/reports/{report_id}/versions/{number}/restore"): "report.version_restored",
    ("PUT", "/api/server/permissions"): "permissions.changed",
    ("POST", "/api/server/schedules"): "schedule.created",
    ("PUT", "/api/server/schedules/{job_id}"): "schedule.changed",
    ("POST", "/api/server/schedules/{job_id}/enabled"): "schedule.enabled_changed",
    ("DELETE", "/api/server/schedules/{job_id}"): "schedule.deleted",
    ("DELETE", "/api/server/runs/{run_id}"): "run_output.deleted",
    ("POST", "/api/admin/connections"): "datasource.saved",
    ("DELETE", "/api/admin/connections/{connection_id}"): "datasource.deleted",
    ("PUT", "/api/admin/connections/{connection_id}/folder"): "datasource.folder_changed",
    ("POST", "/api/data-sources"): "datasource.saved",
    ("DELETE", "/api/data-sources/{connection_id}"): "datasource.deleted",
    ("PUT", "/api/data-sources/{connection_id}/folder"): "datasource.folder_changed",
    ("POST", "/api/admin/folders"): "folder.created",
    ("DELETE", "/api/admin/folders"): "folder.deleted",
    ("POST", "/api/admin/reports/import"): "report.imported",
    ("POST", "/api/admin/reports"): "report.saved",
    ("PUT", "/api/admin/reports/{report_id}"): "report.saved",
    ("DELETE", "/api/admin/reports/{report_id}"): "report.deleted",
    ("POST", "/api/admin/workspace/{report_id}/publish"): "report.published",
    ("DELETE", "/api/admin/workspace/{report_id}"): "draft.deleted",
}

# Fields of the JSON body that name what was changed (never values such as passwords or URLs).
_TARGET_FIELDS = ("path", "folder", "id", "reportId", "fileName", "newId", "name")
_DETAIL_FIELDS = ("folder", "recursive", "replace", "enabled", "newId", "format")


def describe(method: str, template: str, path_params: dict[str, Any], body: bytes) -> tuple[str, str, dict[str, Any]] | None:
    """(action, target, details) for a successful request worth auditing, or None."""
    action = ACTIONS.get((method, template))
    if action is None:
        return None
    payload: dict[str, Any] = {}
    if body and len(body) < 200_000:
        try:
            loaded = json.loads(body)
            payload = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            payload = {}
    target = "/".join(str(value) for value in path_params.values())
    if not target:
        target = next((str(payload[name]) for name in _TARGET_FIELDS if isinstance(payload.get(name), (str, int))), "")
    details = {name: payload[name] for name in _DETAIL_FIELDS if isinstance(payload.get(name), (str, int, bool))}
    if template == "/api/server/permissions" and isinstance(payload.get("rules"), dict):
        details["rules"] = payload["rules"]           # who got which level on the folder
    return action, target[:240], details
