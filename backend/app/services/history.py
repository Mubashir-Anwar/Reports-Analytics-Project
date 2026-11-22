"""History of each server report (published, edited, copied, moved, uploaded, deleted).

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.app.constants import REPORT_ID_PATTERN
from backend.app.db import model
from backend.app.db.entities import REPORT_HISTORY_V2, get_entities
from backend.app.logs import LOG

__all__ = ['history_file', 'read_history', 'record_history', 'move_history']


KEEP = 200


def history_file(ctx: SimpleNamespace, report_id: str) -> str:
    """The older document (moved into report_history at start-up)."""
    return f"history/{report_id}.json"


def _rows(report_id: str, folder: str | None) -> list[dict[str, Any]]:
    """History rows of /folder/report_id (by its server_report_id; see versions._mine), oldest first."""
    from backend.app.services.versions import _mine, report_owner  # noqa: PLC0415
    rows = get_entities().find(REPORT_HISTORY_V2, {"reportCode": report_id})
    if folder is not None:
        rows = _mine(rows, report_owner(report_id, folder), folder)
    return sorted(rows, key=lambda row: (row.get("happenedAt") or "", row["historyId"]))


def read_history(ctx: SimpleNamespace, report_id: str, folder: str | None = None) -> list[dict[str, Any]]:
    """Oldest first: [{at, action, user, detail}] of the report with this ID in this folder."""
    try:
        rows = _rows(report_id, folder)
    except (OSError, ValueError):
        return []
    return [{"at": row.get("happenedAt"), "action": row.get("action"), "user": row.get("userName") or "", "detail": row.get("detail") or ""}
            for row in rows]


def record_history(ctx: SimpleNamespace, report_id: str, action: str, user: str, detail: str = "", folder: str | None = None) -> None:
    """What happened to a server resource (published, edited, copied, moved, uploaded, deleted); last 200 entries."""
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        return
    from backend.app.services.versions import report_owner  # noqa: PLC0415
    entities = get_entities()
    folder = folder or "General"
    try:
        entities.put(REPORT_HISTORY_V2, {"historyId": model.new_id(), "serverReportId": report_owner(report_id, folder),
                                         "reportCode": report_id, "folderPath": folder, "happenedAt": model.now(),
                                         "action": action[:32], "userName": (user or "")[:128] or None, "detail": (detail or "")[:300] or None})
        rows = _rows(report_id, folder)
        for old in rows[:max(0, len(rows) - KEEP)]:
            entities.delete(REPORT_HISTORY_V2, old["historyId"])
    except (OSError, ValueError):
        LOG.warning("The history of %s could not be written", report_id)


def move_history(ctx: SimpleNamespace, report_id: str, old_folder: str, new_folder: str) -> None:
    """The history of a moved report stays its own (same id); the folder shown with it changes."""
    from backend.app.services.versions import report_owner  # noqa: PLC0415
    entities = get_entities()
    try:
        owner = report_owner(report_id, new_folder)
        for row in entities.find(REPORT_HISTORY_V2, {"reportCode": report_id}):
            if row.get("serverReportId") == owner and row.get("folderPath") == old_folder:
                entities.put(REPORT_HISTORY_V2, {**row, "folderPath": new_folder})
    except (OSError, ValueError):
        LOG.warning("The history of %s could not be moved to %s", report_id, new_folder)
