"""Report versions (every save of a server report is kept) and the repository revision that lets several
app instances notice each other's changes.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

import secrets
import time
from types import SimpleNamespace
from typing import Any

from backend.app.constants import REPORT_ID_PATTERN
from backend.app.db.entities import REPORT_VERSIONS_V2, SERVER_REPORTS, get_entities
from backend.app.definitions import discover_reports
from backend.app.logs import LOG
from backend.app.storage.files import report_definition_key
from backend.app.storage.routed import ghost_id
from backend.app.storage.store import get_store

__all__ = ['report_owner', 'save_version', 'list_versions', 'get_version', 'move_versions', 'bump_revision', 'refresh_if_changed']

MAX_VERSIONS = 50
REVISION_KEY = "meta/repository-revision.json"
REFRESH_EVERY_SECONDS = 3.0


def report_owner(report_id: str, folder: str) -> str:
    """The server_report_id of /folder/report_id; a report that is not (or no longer) on the server has a fixed
    stand-in id, so the versions and history of a deleted report are still found."""
    found = get_store().report_id_of(report_definition_key(report_id, folder))
    return found or ghost_id(folder, report_id)


def _gone(entities: Any, owner: str, cache: dict[str, bool]) -> bool:
    if owner not in cache:
        cache[owner] = entities.get(SERVER_REPORTS, owner) is None
    return cache[owner]


def _mine(rows: list[dict[str, Any]], owner: str, folder: str) -> list[dict[str, Any]]:
    """Rows of this report: its own id, or written for a report with the same ID and folder that is gone now."""
    entities, cache = get_entities(), {}
    return [row for row in rows if row.get("serverReportId") == owner
            or (row.get("folderPath") == folder and _gone(entities, row.get("serverReportId") or "", cache))]


def _versions(report_id: str, folder: str | None = None) -> list[dict[str, Any]]:
    """Versions of a report (report_versions, by server_report_id), oldest first. Without a folder: of every report
    with this ID."""
    rows = get_entities().find(REPORT_VERSIONS_V2, {"reportCode": report_id})
    if folder is not None:
        rows = _mine(rows, report_owner(report_id, folder), folder)
    return sorted(rows, key=lambda row: (row.get("savedAt") or "", row["versionNo"]))


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {"version": row["versionNo"], "savedAt": row.get("savedAt"), "action": row.get("action"), "user": row.get("savedBy"),
            "folder": row.get("folderPath") or "General", "title": row.get("title"), "definition": row.get("definition")}


def save_version(ctx: SimpleNamespace, report_id: str, config: dict[str, Any], action: str = "saved", user: str = "") -> int:
    """Keep this state of the report as a new version (the last MAX_VERSIONS of the report are kept)."""
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        return 0
    entities = get_entities()
    folder = str(config.get("folder") or "General")
    try:
        owner = report_owner(report_id, folder)
        mine = _versions(report_id, folder)
        number = max([item["versionNo"] for item in mine] or [0]) + 1     # counts on after a deleted report of the same name
        entities.put(REPORT_VERSIONS_V2, {
            "serverReportId": owner, "versionNo": number, "reportCode": report_id, "folderPath": folder,
            "savedAt": ctx.now_iso(), "action": action[:32], "savedBy": user[:128] or None,
            "title": str(config.get("title", report_id))[:200], "definition": config,
        })
        get_store().set_row_fields(report_definition_key(report_id, folder), currentVersion=number,
                                   publishedBy=user[:128] or None)
        mine = _versions(report_id, folder)
        for old in mine[:max(0, len(mine) - MAX_VERSIONS)]:
            entities.delete(REPORT_VERSIONS_V2, old["serverReportId"], old["versionNo"])
        return number
    except (OSError, ValueError):
        LOG.warning("A version of %s could not be saved", report_id)
        return 0


def list_versions(ctx: SimpleNamespace, report_id: str, folder: str | None = None) -> list[dict[str, Any]]:
    return [{k: v for k, v in _public(item).items() if k != "definition"} for item in reversed(_versions(report_id, folder))]


def get_version(ctx: SimpleNamespace, report_id: str, number: int, folder: str | None = None) -> dict[str, Any] | None:
    found = [item for item in _versions(report_id, folder) if item["versionNo"] == int(number)]
    return _public(found[-1]) if found else None


def move_versions(ctx: SimpleNamespace, report_id: str, old_folder: str, new_folder: str) -> None:
    """A moved report keeps its id, so its versions and history stay its own; the folder shown with them changes."""
    entities = get_entities()
    try:
        owner = report_owner(report_id, new_folder)
        for item in entities.find(REPORT_VERSIONS_V2, {"reportCode": report_id}):
            if item["serverReportId"] == owner and item.get("folderPath") == old_folder:
                entities.put(REPORT_VERSIONS_V2, {**item, "folderPath": new_folder})
    except (OSError, ValueError):
        LOG.warning("The versions of %s could not be moved to %s", report_id, new_folder)
    ctx.move_history(report_id, old_folder, new_folder)


def bump_revision(ctx: SimpleNamespace) -> None:
    """Mark the repository as changed (another instance reloads its report list when it sees a new revision)."""
    revision = secrets.token_hex(8)
    try:
        get_store().write_json(REVISION_KEY, {"revision": revision, "at": ctx.now_iso()})
        ctx.state["revision"] = revision
    except (OSError, ValueError):
        LOG.warning("The repository revision could not be saved")


def refresh_if_changed(ctx: SimpleNamespace) -> bool:
    """Reload the published reports when another instance changed them (checked at most every few seconds)."""
    now = time.monotonic()
    if now - ctx.state.get("revision_checked", 0.0) < REFRESH_EVERY_SECONDS:
        return False
    ctx.state["revision_checked"] = now
    try:
        current = (get_store().read_json(REVISION_KEY) or {}).get("revision")
    except (OSError, ValueError):
        return False
    if current == ctx.state.get("revision"):
        return False
    first = "revision" not in ctx.state
    ctx.state["revision"] = current
    if first:
        return False
    refreshed = discover_reports()
    ctx.reports_by_id.clear()
    ctx.reports_by_id.update(refreshed)
    LOG.info("Repository changed by another instance: %d reports reloaded", len(refreshed))
    return True
