"""History of each server report (published, edited, copied, moved, uploaded, deleted).

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.app.constants import REPORT_ID_PATTERN
from backend.app.db import model
from backend.app.db.entities import REPORT_HISTORY, get_entities
from backend.app.logs import LOG

__all__ = ['history_file', 'read_history', 'record_history']


KEEP = 200


def history_file(ctx: SimpleNamespace, report_id: str) -> str:
    """The older document (moved into rs_report_history at start-up)."""
    return f"history/{report_id}.json"


def read_history(ctx: SimpleNamespace, report_id: str) -> list[dict[str, Any]]:
    """Oldest first: [{at, action, user, detail}]."""
    try:
        rows = get_entities().find(REPORT_HISTORY, {"reportId": report_id}, order=("id", False))
    except (OSError, ValueError):
        return []
    return [{"at": row.get("at"), "action": row.get("action"), "user": row.get("user", ""), "detail": row.get("detail", "")} for row in rows]


def record_history(ctx: SimpleNamespace, report_id: str, action: str, user: str, detail: str = "") -> None:
    """What happened to a server resource (published, edited, copied, moved, uploaded, deleted); last 200 entries."""
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        return
    entities = get_entities()
    try:
        entities.put(REPORT_HISTORY, {"id": model.new_id(), "reportId": report_id, "at": model.now(), "action": action[:32],
                                      "user": (user or "")[:128], "detail": (detail or "")[:300]})
        rows = entities.find(REPORT_HISTORY, {"reportId": report_id}, order=("id", True))
        for old in rows[KEEP:]:
            entities.delete(REPORT_HISTORY, old["id"])
    except (OSError, ValueError):
        LOG.warning("The history of %s could not be written", report_id)
