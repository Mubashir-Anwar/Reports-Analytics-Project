"""Report versions (every save of a server report is kept) and the repository revision that lets several
app instances notice each other's changes.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

import secrets
import time
from types import SimpleNamespace
from typing import Any

from backend.app.constants import REPORT_ID_PATTERN
from backend.app.definitions import discover_reports
from backend.app.logs import LOG
from backend.app.storage.store import get_store

__all__ = ['save_version', 'list_versions', 'get_version', 'bump_revision', 'refresh_if_changed']

MAX_VERSIONS = 50
REVISION_KEY = "meta/repository-revision.json"
REFRESH_EVERY_SECONDS = 3.0


def _version_keys(report_id: str) -> list[tuple[int, str]]:
    found = []
    for key in get_store().keys(f"versions/{report_id}", ".json"):
        stem = key.rsplit("/", 1)[-1][:-5]
        if stem.isdigit():
            found.append((int(stem), key))
    return sorted(found)


def save_version(ctx: SimpleNamespace, report_id: str, config: dict[str, Any], action: str = "saved", user: str = "") -> int:
    """Keep this state of the report as a new version (the last MAX_VERSIONS are kept)."""
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        return 0
    store = get_store()
    try:
        existing = _version_keys(report_id)
        number = existing[-1][0] + 1 if existing else 1
        store.write_json(f"versions/{report_id}/{number:05d}.json", {
            "version": number, "savedAt": ctx.now_iso(), "action": action, "user": user,
            "folder": config.get("folder", "General"), "title": config.get("title", report_id), "definition": config,
        })
        for _, old in existing[:max(0, len(existing) + 1 - MAX_VERSIONS)]:
            store.delete(old)
        return number
    except (OSError, ValueError):
        LOG.warning("A version of %s could not be saved", report_id)
        return 0


def list_versions(ctx: SimpleNamespace, report_id: str) -> list[dict[str, Any]]:
    store = get_store()
    items = []
    for _, key in reversed(_version_keys(report_id)):
        data = store.read_json(key) or {}
        items.append({k: data.get(k) for k in ("version", "savedAt", "action", "user", "folder", "title")})
    return items


def get_version(ctx: SimpleNamespace, report_id: str, number: int) -> dict[str, Any] | None:
    return get_store().read_json(f"versions/{report_id}/{int(number):05d}.json")


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
