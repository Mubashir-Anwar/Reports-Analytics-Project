"""The designer workspace: draft files, their publish status and folders.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.app.constants import REPORT_ID_PATTERN, WORKSPACE_META
from backend.app.logs import LOG
from backend.app.storage.files import _write_json_atomically, normalize_folder_path, workspace_directory

__all__ = ['now_iso', 'strip_workspace_meta', 'workspace_file', 'ensure_workspace', 'workspace_entries', 'find_workspace_entry', 'workspace_summary', 'workspace_folders', 'sync_workspace_copy']


def now_iso(ctx: SimpleNamespace) -> str:
    # microseconds: "changed since publish" compares these strings, and a save can follow a publish within a second
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def strip_workspace_meta(ctx: SimpleNamespace, payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in (WORKSPACE_META, "workspace", "isNew")}


def workspace_file(ctx: SimpleNamespace, report_id: str, folder: str) -> Path:
    return workspace_directory().joinpath(*folder.split("/"), f"{report_id}.json")


def ensure_workspace(ctx: SimpleNamespace) -> None:
    """First use: start the workspace with a copy of what is already on the server."""
    root = workspace_directory()
    if root.exists():
        return
    root.mkdir(parents=True, exist_ok=True)
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
        _write_json_atomically(ctx.workspace_file(definition.report_id, folder), payload)


def workspace_entries(ctx: SimpleNamespace) -> list[tuple[Path, dict[str, Any]]]:
    ctx.ensure_workspace()
    found: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(workspace_directory().rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            LOG.warning("A workspace file could not be read: %s", path.name)
            continue
        if isinstance(data, dict) and REPORT_ID_PATTERN.fullmatch(str(data.get("id", ""))) and path.stem == data["id"]:
            found.append((path, data))
    return found


def find_workspace_entry(ctx: SimpleNamespace, report_id: str) -> tuple[Path, dict[str, Any]] | None:
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


def workspace_folders(ctx: SimpleNamespace, entries: list[tuple[Path, dict[str, Any]]]) -> list[str]:
    found = {"General"}
    root = workspace_directory()
    for directory in root.rglob("*"):
        if directory.is_dir():
            parts = directory.relative_to(root).parts
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
    _write_json_atomically(path, data)
