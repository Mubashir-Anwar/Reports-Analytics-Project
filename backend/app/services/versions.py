"""Report versions (every save of a server report is kept) and the repository revision that lets several
app instances notice each other's changes.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

import secrets
import time
from types import SimpleNamespace
from typing import Any

from backend.app.constants import REPORT_ID_PATTERN
from backend.app.db.entities import REPORT_VERSIONS, get_entities
from backend.app.definitions import discover_reports
from backend.app.logs import LOG
from backend.app.storage.store import get_store

__all__ = ['save_version', 'list_versions', 'get_version', 'bump_revision', 'refresh_if_changed']

MAX_VERSIONS = 50
REVISION_KEY = "meta/repository-revision.json"
REFRESH_EVERY_SECONDS = 3.0


def _versions(report_id: str) -> list[dict[str, Any]]:
    """Versions of a report from rs_report_versions, oldest first."""
    return get_entities().find(REPORT_VERSIONS, {"reportId": report_id}, order=("version", False))


def save_version(ctx: SimpleNamespace, report_id: str, config: dict[str, Any], action: str = "saved", user: str = "") -> int:
    """Keep this state of the report as a new version (the last MAX_VERSIONS are kept)."""
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        return 0
    entities = get_entities()
    try:
        existing = [item["version"] for item in _versions(report_id)]
        number = existing[-1] + 1 if existing else 1
        entities.put(REPORT_VERSIONS, {
            "reportId": report_id, "version": number, "savedAt": ctx.now_iso(), "action": action[:32], "user": user[:128],
            "folder": config.get("folder", "General"), "title": str(config.get("title", report_id))[:200], "definition": config,
        })
        for old in existing[:max(0, len(existing) + 1 - MAX_VERSIONS)]:
            entities.delete(REPORT_VERSIONS, report_id, old)
        return number
    except (OSError, ValueError):
        LOG.warning("A version of %s could not be saved", report_id)
        return 0


def list_versions(ctx: SimpleNamespace, report_id: str) -> list[dict[str, Any]]:
    return [{k: item.get(k) for k in ("version", "savedAt", "action", "user", "folder", "title")} for item in reversed(_versions(report_id))]


def get_version(ctx: SimpleNamespace, report_id: str, number: int) -> dict[str, Any] | None:
    return get_entities().get(REPORT_VERSIONS, report_id, int(number))


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
