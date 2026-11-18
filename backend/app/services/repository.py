"""The server repository: saving, folders, deleting, summaries and the /server resource page.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from backend.app.connectors.profiles import load_connection_profiles
from backend.app.constants import OUTPUT_FORMATS, REPORT_ID_PATTERN
from backend.app.definitions import ReportDefinition, _dynamic_report, discover_reports
from backend.app.logs import LOG
from backend.app.security.permissions import MANAGE, RUN, load_rules, remove_rules_under
from backend.app.security.users import role_at_least
from backend.app.storage.files import (
    list_saved_folders,
    normalize_folder_path,
    report_definition_key,
    save_folder_list,
)
from backend.app.storage.store import get_store

__all__ = ['admin_report_payload', 'save_report_payload', 'folder_contents', 'delete_repository_folder', 'delete_published_report', 'published_or_404', 'design_summary', 'resource_details', 'free_report_id', 'published_report_summaries']


def admin_report_payload(ctx: SimpleNamespace, definition: ReportDefinition) -> dict[str, Any]:
    if definition.editor_config is not None:
        return copy.deepcopy(definition.editor_config)
    return {
        "id": definition.report_id,
        "title": definition.title,
        "folder": "General",
        "connectionId": definition.connection_id,
        "collection": definition.collection,
        "parameters": [{
            "name": spec.name,
            "type": spec.kind,
            "label": spec.label,
            "description": spec.description,
            "required": spec.required,
            **({"default": spec.default} if spec.default is not None else {}),
            **({"allowedValues": list(spec.allowed_values)} if spec.allowed_values else {}),
        } for spec in definition.parameters],
        "columns": list(definition.columns),
        "pipeline": [],
    }


def save_report_payload(ctx: SimpleNamespace, payload: Any, expected_id: str | None = None, *, action: str = "saved", user: str = "") -> ReportDefinition:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="The report definition must be a JSON object")
    try:
        definition = _dynamic_report(payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if expected_id and definition.report_id != expected_id:
        raise HTTPException(status_code=400, detail="The report ID in the URL and in the definition must match")
    existing = ctx.reports_by_id.get(definition.report_id)
    if existing is not None and expected_id is None:
        raise HTTPException(status_code=409, detail="A report with this ID already exists")
    profile_ids = {item["id"] for item in load_connection_profiles()}
    if definition.connection_id not in profile_ids:
        raise HTTPException(status_code=422, detail=f"Unknown connectionId: {definition.connection_id}")
    store = get_store()
    key = report_definition_key(definition.report_id, definition.editor_config["folder"])
    config = definition.editor_config or payload
    store.write_json(key, config)
    if existing and existing.editor_config:
        previous_key = report_definition_key(existing.report_id, normalize_folder_path(existing.editor_config.get("folder", "General")))
        if previous_key != key:
            store.delete(previous_key)
    ctx.save_version(definition.report_id, config, action, user)
    ctx.bump_revision()
    ctx.reports_by_id[definition.report_id] = _dynamic_report(config, Path(key))
    return ctx.reports_by_id[definition.report_id]


def folder_contents(ctx: SimpleNamespace, folder: str) -> dict[str, list[str]]:
    """Subfolders, published reports, dashboards and data adapters inside a repository folder (at any depth)."""
    from backend.app.analytics import store as analytics_store  # noqa: PLC0415 - analytics is optional

    def inside(path: str) -> bool:
        return path == folder or path.startswith(f"{folder}/")
    return {
        "dashboards": sorted(item.get("name") or item["id"] for item in analytics_store.dashboards()
                             if inside(str(item.get("folder") or "Analytics"))),
        "subfolders": [item for item in list_saved_folders() if item.startswith(f"{folder}/")],
        "reports": sorted(item.report_id for item in ctx.reports_by_id.values()
                          if item.editor_config and inside(str(item.editor_config.get("folder", "General")))),
        "adapters": [item["name"] for item in load_connection_profiles() if inside(str(item.get("folder") or ""))],
    }


def delete_repository_folder(ctx: SimpleNamespace, folder: str, recursive: bool = False, user: str = "") -> dict[str, Any]:
    """Delete a folder. recursive=True (after the user confirmed it) also deletes its subfolders and the
    published reports in them, like JasperReports Server; data adapters must still be moved first."""
    if folder == "General":
        raise HTTPException(status_code=400, detail="The default General folder cannot be deleted")
    saved_folders = list_saved_folders()
    if folder not in saved_folders:
        raise HTTPException(status_code=404, detail="Folder not found")
    contents = ctx.folder_contents(folder)
    if contents["adapters"]:
        raise HTTPException(status_code=409, detail=f"Move or delete the data adapters in this folder first: {', '.join(contents['adapters'])}")
    if not recursive and (contents["subfolders"] or contents["reports"] or contents["dashboards"]):
        parts = []
        if contents["subfolders"]:
            parts.append(f"{len(contents['subfolders'])} subfolder(s)")
        if contents["reports"]:
            parts.append(f"{len(contents['reports'])} report(s): {', '.join(contents['reports'][:5])}")
        if contents["dashboards"]:
            parts.append(f"{len(contents['dashboards'])} dashboard(s)")
        raise HTTPException(status_code=409, detail=f"The folder is not empty ({' and '.join(parts)}); confirm to delete it with everything inside")
    store = get_store()
    for report_id in contents["reports"]:
        definition = ctx.reports_by_id[report_id]
        store.delete(report_definition_key(report_id, normalize_folder_path(definition.editor_config.get("folder", "General"))))
        ctx.record_history(report_id, "deleted", user or "admin", f"with the folder {folder}")
    if contents["dashboards"]:
        from backend.app.analytics import store as analytics_store  # noqa: PLC0415
        for item in analytics_store.dashboards():
            where = str(item.get("folder") or "Analytics")
            if where == folder or where.startswith(f"{folder}/"):
                analytics_store.delete_dashboard(item["id"])     # their datasets stay (other dashboards may use them)
    if not store.remove_dir("/".join(["reports", *folder.split("/")]), recursive=recursive):
        raise HTTPException(status_code=409, detail="The folder still has files in it; confirm to delete it with everything inside")
    removed = {folder, *contents["subfolders"]}
    save_folder_list([item for item in saved_folders if item not in removed])
    remove_rules_under(folder)
    ctx.bump_revision()
    if contents["reports"]:
        refreshed = discover_reports()
        ctx.reports_by_id.clear()
        ctx.reports_by_id.update(refreshed)
    LOG.info("Repository folder %s deleted (%d subfolders, %d reports) by %s", folder, len(contents["subfolders"]), len(contents["reports"]), user or "admin")
    return {"deleted": folder, "folders": list_saved_folders(), "subfoldersDeleted": len(contents["subfolders"]),
            "reportsDeleted": contents["reports"]}


def delete_published_report(ctx: SimpleNamespace, report_id: str) -> dict[str, str]:
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        raise HTTPException(status_code=400, detail="Invalid report ID format")
    definition = ctx.reports_by_id.get(report_id)
    if definition is None or definition.editor_config is None:
        raise HTTPException(status_code=404, detail="Saved report definition not found")
    key = report_definition_key(report_id, normalize_folder_path(definition.editor_config.get("folder", "General")))
    if not get_store().delete(key):
        raise HTTPException(status_code=404, detail="Saved report definition not found")
    ctx.bump_revision()
    refreshed = discover_reports()
    ctx.reports_by_id.clear()
    ctx.reports_by_id.update(refreshed)
    return {"status": "deleted", "reportId": report_id}


def published_or_404(ctx: SimpleNamespace, report_id: str) -> ReportDefinition:
    definition = ctx.reports_by_id.get(report_id)
    if definition is None or definition.editor_config is None:
        raise HTTPException(status_code=404, detail="This report is not published on the server")
    return definition


def design_summary(ctx: SimpleNamespace, design: Any) -> dict[str, Any]:
    """Bands, element counts and the subreports a design uses (for the resource page)."""
    if not isinstance(design, dict):
        return {"bands": [], "elements": 0, "subreports": [], "components": {}}
    bands = design.get("bands") or {}
    found: list[tuple[str, dict[str, Any]]] = []
    for key, band in bands.items():
        if key in ("groupHeaders", "groupFooters") and isinstance(band, dict):
            found += [(f"{key[:-1]} {name}", item) for name, item in band.items() if isinstance(item, dict)]
        elif isinstance(band, dict):
            found.append((key, band))
    present = [(name, band) for name, band in found if band.get("height") or band.get("elements")]
    elements = [el for _, band in present for el in band.get("elements") or [] if isinstance(el, dict)]
    components: dict[str, int] = {}
    for el in elements:
        components[str(el.get("type"))] = components.get(str(el.get("type")), 0) + 1
    return {"bands": [name for name, _ in present], "elements": len(elements), "components": components,
            "subreports": sorted({str(el.get("reportId")) for el in elements if el.get("type") == "subreport" and el.get("reportId")}),
            "page": design.get("page") or {}}


def resource_details(ctx: SimpleNamespace, definition: ReportDefinition, session: dict[str, Any]) -> dict[str, Any]:
    config = definition.editor_config or {}
    rules = load_rules()
    # editing and seeing the query need a designer role AND the Manage permission on the report's folder
    designer = role_at_least(session["role"], "designer") and ctx.report_level(session, definition, rules) >= MANAGE
    try:
        modified = get_store().modified_at(report_definition_key(definition.report_id, normalize_folder_path(config.get("folder", "General"))))
    except (ValueError, OSError):
        modified = None
    size = len(json.dumps(config, ensure_ascii=False).encode("utf-8")) if config else None
    used_by = sorted(item.report_id for item in ctx.reports_by_id.values() if item.report_id != definition.report_id
                     and definition.report_id in ctx.design_summary((item.editor_config or {}).get("design")).get("subreports", [])
                     and ctx.report_level(session, item, rules) >= RUN)  # never name reports this user may not see
    return {
        "id": definition.report_id, "title": definition.title, "description": config.get("description", ""),
        "folder": config.get("folder", "General"), "formats": config.get("formats", sorted(OUTPUT_FORMATS)),
        "defaultFormat": config.get("defaultFormat", "pdf"), "connection": ctx.connection_summary(definition.connection_id),
        "collection": config.get("collection", definition.collection), "columns": list(definition.columns),
        "parameters": [{"name": p.get("name"), "type": p.get("type", "string"), "label": p.get("label", p.get("name")),
                        "description": p.get("description", ""), "required": bool(p.get("required")),
                        "default": p.get("default"), "allowedValues": p.get("allowedValues") or [],
                        "computed": p.get("prompt") is False, "expression": p.get("expression", "")}
                       for p in config.get("parameters", []) if isinstance(p, dict)],
        # the query can reveal data structure: only designers and admins see it
        "queryLanguage": config.get("queryLanguage", "mongosh") if designer else None,
        "querySource": config.get("querySource", "") if designer else None,
        "design": ctx.design_summary(config.get("design")),
        "file": {"name": f"{definition.report_id}.mrd", "size": size,
                 "modifiedAt": modified.isoformat() if modified else ""},
        "usedBy": used_by,
        "history": list(reversed(ctx.read_history(definition.report_id)))[:50],
        "canEdit": designer, "canDelete": role_at_least(session["role"], "admin"),
    }


def free_report_id(ctx: SimpleNamespace, base: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]", "_", base)[:70] or "REPORT"
    candidate, number = f"{base}_COPY", 2
    while candidate in ctx.reports_by_id:
        candidate, number = f"{base}_COPY{number}", number + 1
    return candidate


def published_report_summaries(ctx: SimpleNamespace) -> list[dict[str, Any]]:
    return [{
        "id": item.report_id,
        "title": item.title,
        "description": (item.editor_config or {}).get("description", ""),
        "folder": (item.editor_config or {}).get("folder", "General"),
        "connectionId": item.connection_id,
        "collection": item.collection,
        "formats": (item.editor_config or {}).get("formats", sorted(OUTPUT_FORMATS)),
        "defaultFormat": (item.editor_config or {}).get("defaultFormat", "csv"),
        "parameters": [{
            "name": p.name,
            "label": p.label or p.name,
            "description": p.description,
            "type": p.kind,
            "required": p.required,
            "default": p.default,
            "allowedValues": list(p.allowed_values),
        } for p in item.parameters if p.prompt],  # computed parameters are never asked
    } for item in ctx.reports_by_id.values()]
