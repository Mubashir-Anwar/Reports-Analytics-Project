"""History of each server report (published, edited, copied, moved, uploaded, deleted).

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.app.constants import REPORT_ID_PATTERN
from backend.app.logs import LOG
from backend.app.storage.store import get_store

__all__ = ['history_file', 'read_history', 'record_history']


def history_file(ctx: SimpleNamespace, report_id: str) -> str:
    return f"history/{report_id}.json"


def read_history(ctx: SimpleNamespace, report_id: str) -> list[dict[str, Any]]:
    try:
        items = get_store().read_json(ctx.history_file(report_id), [])
    except (OSError, ValueError):
        return []
    return items if isinstance(items, list) else []


def record_history(ctx: SimpleNamespace, report_id: str, action: str, user: str, detail: str = "") -> None:
    """What happened to a server resource (published, edited, copied, moved, uploaded, deleted); last 200 entries."""
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        return
    items = ctx.read_history(report_id)
    items.append({"at": ctx.now_iso(), "action": action, "user": user, "detail": detail[:300]})
    try:
        get_store().write_json(ctx.history_file(report_id), items[-200:])
    except OSError:
        LOG.warning("The history of %s could not be written", report_id)
