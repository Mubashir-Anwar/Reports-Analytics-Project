"""The designer workspace: draft files, their publish status and folders.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from backend.app.constants import REPORT_ID_PATTERN, WORKSPACE_META
from backend.app.logs import LOG
from backend.app.storage.files import normalize_folder_path, workspace_key
from backend.app.storage.store import FileStore, get_store

__all__ = ['now_iso', 'strip_workspace_meta', 'workspace_file', 'ensure_workspace', 'workspace_entries', 'find_workspace_entry', 'workspace_summary', 'workspace_folders', 'sync_workspace_copy']


def now_iso(ctx: SimpleNamespace) -> str:
    # microseconds: "changed since publish" compares these strings, and a save can follow a publish within a second
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def strip_workspace_meta(ctx: SimpleNamespace, payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in (WORKSPACE_META, "workspace", "isNew")}


def workspace_file(ctx: SimpleNamespace, report_id: str, folder: str) -> str:
    """Storage key of a draft: workspace/<folder>/<id>.json"""
    return workspace_key(report_id, folder)


def ensure_workspace(ctx: SimpleNamespace) -> None:
    """First use: start the workspace with a copy of what is already on the server."""
    store = get_store()
    started = store.path("workspace").is_dir() if isinstance(store, FileStore) else bool(store.keys("workspace") or store.dirs("workspace"))
    if started:
        return
    store.make_dir("workspace")
    stamp = ctx.now_iso()
    for definition in ctx.reports_by_id.values():
        if definition.editor_config is None:
            continue
        payload = copy.deepcopy(definition.editor_config)
        try:
            folder = normalize_folder_path(payload.get("folder", "General"))
        except ValueError:
            folder = "General"
        payload["folder"] = folder
        payload[WORKSPACE_META] = {"createdAt": stamp, "updatedAt": stamp, "publishedAt": stamp, "publishedFolder": folder}
        store.write_json(ctx.workspace_file(definition.report_id, folder), payload)


def workspace_entries(ctx: SimpleNamespace) -> list[tuple[str, dict[str, Any]]]:
    """(storage key, draft) of every report in the designer workspace."""
    ctx.ensure_workspace()
    found: list[tuple[str, dict[str, Any]]] = []
    for key, data in get_store().read_many("workspace"):
        if data is None:
            LOG.warning("A workspace file could not be read: %s", key.rsplit("/", 1)[-1])
            continue
        if isinstance(data, dict) and REPORT_ID_PATTERN.fullmatch(str(data.get("id", ""))) and key.rsplit("/", 1)[-1] == f"{data['id']}.json":
            found.append((key, data))
    return found


def find_workspace_entry(ctx: SimpleNamespace, report_id: str) -> tuple[str, dict[str, Any]] | None:
    return next((item for item in ctx.workspace_entries() if item[1]["id"] == report_id), None)


def workspace_summary(ctx: SimpleNamespace, data: dict[str, Any]) -> dict[str, Any]:
    meta = data.get(WORKSPACE_META) or {}
    published_at = meta.get("publishedAt") or ""
    on_server = data["id"] in ctx.reports_by_id
    return {
        "id": data["id"], "title": data.get("title") or data["id"], "description": data.get("description", ""),
        "folder": data.get("folder", "General"), "updatedAt": meta.get("updatedAt", ""),
        "publishedAt": published_at if on_server else "", "publishedFolder": meta.get("publishedFolder", "") if on_server else "",
        "onServer": on_server,
        "changedSincePublish": bool(on_server and published_at and meta.get("updatedAt", "") > published_at),
    }


def workspace_folders(ctx: SimpleNamespace, entries: list[tuple[str, dict[str, Any]]]) -> list[str]:
    found = {"General"}
    for directory in get_store().dirs("workspace"):
        parts = directory.split("/")
        found.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    for _, data in entries:
        parts = str(data.get("folder", "General")).split("/")
        found.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return sorted(found, key=lambda item: (item.casefold(), item))


def sync_workspace_copy(ctx: SimpleNamespace, report_id: str, update: Any) -> None:
    """A change made on the server is also made in the designer's workspace copy, so the next publish keeps it."""
    entry = ctx.find_workspace_entry(report_id)
    if entry is None:
        return
    path, data = entry
    update(data)
    meta = dict(data.get(WORKSPACE_META) or {})
    stamp = ctx.now_iso()
    meta.update({"updatedAt": stamp, "publishedAt": stamp, "publishedFolder": data.get("folder", "General")})
    data[WORKSPACE_META] = meta
    get_store().write_json(path, data)
