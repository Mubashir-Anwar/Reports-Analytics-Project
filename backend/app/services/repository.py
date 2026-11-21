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
from backend.app.definitions import AmbiguousReport, ReportDefinition, _dynamic_report, clean_folder, discover_reports, resolve_default, run_form_rules
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

__all__ = ['report_for_request', 'admin_report_payload', 'save_report_payload', 'folder_contents', 'delete_repository_folder', 'delete_published_report', 'published_or_404', 'design_summary', 'resource_details', 'free_report_id', 'published_report_summaries']


def report_for_request(ctx: SimpleNamespace, request: Any, report_id: str, visible: Any = None) -> ReportDefinition | None:
    """The published report a request means: the ID in the URL and the folder in ?folder= (or Folder=). Without a folder
    the only report with this ID (409 when it is in several folders)."""
    params = request.query_params
    return ctx.reports_by_id.find(report_id, clean_folder(params.get("folder") or params.get("Folder")), visible)


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


def save_report_payload(ctx: SimpleNamespace, payload: Any, expected_id: str | None = None, *, previous: ReportDefinition | None = None,
                        action: str = "saved", user: str = "") -> ReportDefinition:
    """Publish a definition into its folder. A report is <folder>/<ID>: the same ID may be in another folder.

    expected_id = this replaces a report with this ID; previous = the report being replaced (it may be in another
    folder: a move). Without expected_id a report already in the target folder is a 409."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="The report definition must be a JSON object")
    try:
        definition = _dynamic_report(payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if expected_id and definition.report_id != expected_id:
        raise HTTPException(status_code=400, detail="The report ID in the URL and in the definition must match")
    at_target = dict.get(ctx.reports_by_id, definition.ref)
    if at_target is not None and expected_id is None:
        raise HTTPException(status_code=409, detail=f"A report with the ID {definition.report_id} already exists in /{definition.folder}")
    if previous is not None and at_target is not None and at_target.ref != previous.ref:
        raise HTTPException(status_code=409, detail=f"/{definition.folder} already has a report with the ID {definition.report_id}")
    existing = previous if previous is not None else (at_target if expected_id else None)
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
    if existing is not None and existing.ref != definition.ref:
        ctx.move_versions(existing.report_id, existing.folder, definition.folder)     # its versions and history go with it
    ctx.save_version(definition.report_id, config, action, user)
    ctx.bump_revision()
    if existing is not None:
        ctx.reports_by_id.discard(existing)
    return ctx.reports_by_id.add(_dynamic_report(config, Path(key)))


def folder_contents(ctx: SimpleNamespace, folder: str) -> dict[str, list[str]]:
    """Subfolders, published reports, dashboards and data adapters inside a repository folder (at any depth)."""
    from backend.app.analytics import store as analytics_store  # noqa: PLC0415 - analytics is optional

    def inside(path: str) -> bool:
        return path == folder or path.startswith(f"{folder}/")
    return {
        "dashboards": sorted(item.get("name") or item["id"] for item in analytics_store.dashboards()
                             if inside(str(item.get("folder") or "Analytics"))),
        "subfolders": [item for item in list_saved_folders() if item.startswith(f"{folder}/")],
        "reports": sorted(item.report_id for item in ctx.reports_by_id.values() if item.editor_config and inside(item.folder)),
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
    for definition in [item for item in ctx.reports_by_id.values() if item.editor_config and
                       (item.folder == folder or item.folder.startswith(f"{folder}/"))]:
        store.delete(report_definition_key(definition.report_id, definition.folder))
        ctx.record_history(definition.report_id, "deleted", user or "admin", f"with the folder {folder}", folder=definition.folder)
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


def delete_published_report(ctx: SimpleNamespace, report_id: str, folder: str | None = None) -> dict[str, str]:
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        raise HTTPException(status_code=400, detail="Invalid report ID format")
    definition = ctx.reports_by_id.find(report_id, folder)
    if definition is None or definition.editor_config is None:
        raise HTTPException(status_code=404, detail="Saved report definition not found")
    key = report_definition_key(report_id, definition.folder)
    if not get_store().delete(key):
        raise HTTPException(status_code=404, detail="Saved report definition not found")
    ctx.bump_revision()
    refreshed = discover_reports()
    ctx.reports_by_id.clear()
    ctx.reports_by_id.update(refreshed)
    return {"status": "deleted", "reportId": report_id, "folder": definition.folder}


def published_or_404(ctx: SimpleNamespace, report_id: str, folder: str | None = None) -> ReportDefinition:
    definition = ctx.reports_by_id.find(report_id, folder)
    if definition is None or definition.editor_config is None:
        raise HTTPException(status_code=404, detail="This report is not published on the server")
    return definition


def design_summary(ctx: SimpleNamespace, design: Any) -> dict[str, Any]:
    """Bands, element counts and the subreports a design uses (for the resource page)."""
    if not isinstance(design, dict):
        return {"bands": [], "elements": 0, "subreports": [], "components": {}, "images": []}
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
    images = []
    for band_name, band in present:
        for el in band.get("elements") or []:
            if not isinstance(el, dict) or el.get("type") != "image":
                continue
            src = str(el.get("src") or "")
            kind = re.match(r"^data:image/(\w+);base64,", src)
            images.append({
                "band": band_name, "elementId": str(el.get("id", "")),
                "name": str(el.get("srcName") or "") or (f"image.{kind.group(1)}" if kind else ""),
                "url": str(el.get("srcUrl") or ""), "reference": str(el.get("reference") or ""),
                "format": kind.group(1) if kind else "", "bytes": (len(src) - kind.end()) * 3 // 4 if kind else 0,
                "missing": not src,               # an image without its picture prints as an empty box
                "size": f"{round(float(el.get('width') or 0))}×{round(float(el.get('height') or 0))}",
            })
    return {"bands": [name for name, _ in present], "elements": len(elements), "components": components,
            "subreports": sorted({str(el.get("reportId")) for el in elements if el.get("type") == "subreport" and el.get("reportId")}),
            "images": images, "page": design.get("page") or {}}


def _points_to(ctx: SimpleNamespace, parent: ReportDefinition, definition: ReportDefinition) -> bool:
    """Does a Subreport in `parent` that names this ID run this report (and not one with the same ID elsewhere)?"""
    try:
        return ctx.subreport_target(parent, definition.report_id) is definition
    except AmbiguousReport:
        return False


def resource_details(ctx: SimpleNamespace, definition: ReportDefinition, session: dict[str, Any]) -> dict[str, Any]:
    config = definition.editor_config or {}
    rules = load_rules()
    # editing and seeing the query need a designer role AND the Manage permission on the report's folder
    designer = role_at_least(session["role"], "designer") and ctx.report_level(session, definition, rules) >= MANAGE
    try:
        modified = get_store().modified_at(report_definition_key(definition.report_id, definition.folder))
    except (ValueError, OSError):
        modified = None
    size = len(json.dumps(config, ensure_ascii=False).encode("utf-8")) if config else None
    used_by = sorted(item.report_id for item in ctx.reports_by_id.values() if item.ref != definition.ref
                     and definition.report_id in ctx.design_summary((item.editor_config or {}).get("design")).get("subreports", [])
                     and _points_to(ctx, item, definition)
                     and ctx.report_level(session, item, rules) >= RUN)  # never name reports this user may not see
    return {
        "id": definition.report_id, "ref": definition.ref, "title": definition.title, "description": config.get("description", ""),
        "folder": definition.folder, "formats": config.get("formats", sorted(OUTPUT_FORMATS)),
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
        "history": list(reversed(ctx.read_history(definition.report_id, definition.folder)))[:50],
        "canEdit": designer, "canDelete": role_at_least(session["role"], "admin"),
    }


def free_report_id(ctx: SimpleNamespace, base: str, folder: str | None = None) -> str:
    """A new ID for a copy: free in the target folder (or anywhere, when no folder is given)."""
    base = re.sub(r"[^A-Za-z0-9_-]", "_", base)[:70] or "REPORT"
    candidate, number = f"{base}_COPY", 2
    while (f"{folder}/{candidate}" if folder else candidate) in ctx.reports_by_id:
        candidate, number = f"{base}_COPY{number}", number + 1
    return candidate


def published_report_summaries(ctx: SimpleNamespace) -> list[dict[str, Any]]:
    return [{
        "id": item.report_id,
        "ref": item.ref,
        "title": item.title,
        "description": (item.editor_config or {}).get("description", ""),
        "folder": item.folder,
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
            "default": resolve_default(p),
            "allowedValues": list(p.allowed_values),
            **run_form_rules(p),
        } for p in item.parameters if p.prompt],  # computed parameters are never asked
    } for item in ctx.reports_by_id.values()]
