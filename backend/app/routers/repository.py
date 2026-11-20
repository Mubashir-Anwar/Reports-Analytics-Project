"""Server repository through the admin/designer token: reports, folders, import/export, preview and run."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from backend.app.definitions import _dynamic_report, clean_folder
from backend.app.mrd import parse_report_definition_xml, render_report_definition_xml
from backend.app.storage.files import add_saved_folder, list_saved_folders, normalize_folder_path


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the repository endpoints to the app."""
    admin_report_payload = ctx.admin_report_payload
    delete_published_report = ctx.delete_published_report
    delete_repository_folder = ctx.delete_repository_folder
    folder_contents = ctx.folder_contents
    preview_definition = ctx.preview_definition
    reports_by_id = ctx.reports_by_id
    require_admin = ctx.require_admin
    run_definition_file = ctx.run_definition_file
    save_report_payload = ctx.save_report_payload

    @app.get("/api/admin/reports")
    def admin_list_reports(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        return {"reports": [admin_report_payload(item) for item in sorted(reports_by_id.values(), key=lambda report: report.report_id)]}

    @app.get("/api/admin/folders")
    def admin_list_folders(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            return {"folders": list_saved_folders()}
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None

    @app.post("/api/admin/folders")
    async def admin_create_folder(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Send the folder as a JSON object")
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"folder": folder, "folders": add_saved_folder(folder)}

    @app.delete("/api/admin/folders")
    async def admin_delete_folder(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
            folder = normalize_folder_path(payload.get("path", "")) if isinstance(payload, dict) else ""
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc) or "Invalid folder path") from None
        except Exception:
            raise HTTPException(status_code=400, detail="Send the folder path as JSON") from None
        return delete_repository_folder(folder, payload.get("recursive") is True, request.state.actor)

    @app.get("/api/admin/folders/contents")
    def admin_folder_contents(path: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            folder = normalize_folder_path(path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"folder": folder, **folder_contents(folder)}

    @app.get("/api/admin/reports/{report_id}")
    def admin_get_report(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        definition = ctx.report_for_request(request, report_id)        # ?folder= when the ID is in several folders
        if definition is None:
            raise HTTPException(status_code=404, detail="Report ID is not registered")
        return admin_report_payload(definition)

    @app.get("/api/admin/reports/{report_id}/definition.xml")
    def admin_export_report_definition(report_id: str, request: Request) -> Response:
        require_admin(request, "designer")
        definition = ctx.report_for_request(request, report_id)
        if definition is None or definition.editor_config is None:
            raise HTTPException(status_code=404, detail="Saved report definition not found")
        body = render_report_definition_xml(admin_report_payload(definition))
        return Response(
            body,
            media_type="application/xml; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.report.xml"'},
        )

    @app.post("/api/admin/reports/import")
    async def admin_import_report_definition(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            request_payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send the import request as JSON") from None
        if not isinstance(request_payload, dict) or not isinstance(request_payload.get("xml"), str):
            raise HTTPException(status_code=400, detail="Send the content of the report definition XML file")
        try:
            definition_payload = parse_report_definition_xml(request_payload["xml"])
            definition_payload["connectionId"] = str(request_payload.get("connectionId", "")).strip()
            definition_payload["folder"] = normalize_folder_path(
                request_payload.get("folder") or definition_payload.get("folder", "General")
            )
            definition = _dynamic_report(definition_payload)
        except (ET.ParseError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"The XML file could not be read: {exc}") from None
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        existing = dict.get(reports_by_id, definition.ref)              # the same ID in another folder is another report
        replace_existing = request_payload.get("replace") is True
        if existing is not None and not replace_existing:
            raise HTTPException(status_code=409, detail=f"A report with this ID already exists in /{definition.folder}; confirm that it should be replaced")
        try:
            saved = save_report_payload(
                definition_payload,
                expected_id=definition.report_id if existing is not None else None,
                previous=existing,
            )
        except HTTPException:
            raise
        return {"report": admin_report_payload(saved), "replaced": existing is not None}

    @app.post("/api/admin/reports")
    async def admin_create_report(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return {"report": admin_report_payload(save_report_payload(payload))}

    @app.put("/api/admin/reports/{report_id}")
    async def admin_update_report(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        # ?folder= is the report being changed (the payload's folder may move it); without it the report in the
        # payload's folder, or the only report with this ID
        previous = ctx.report_for_request(request, report_id) if request.query_params.get("folder") else None
        if previous is None and isinstance(payload, dict):
            target = clean_folder(payload.get("folder")) or "General"
            previous = dict.get(reports_by_id, f"{target}/{report_id}") or reports_by_id.find(report_id)
        return {"report": admin_report_payload(save_report_payload(payload, report_id, previous=previous))}

    @app.delete("/api/admin/reports/{report_id}")
    def admin_delete_report(report_id: str, request: Request) -> dict[str, str]:
        require_admin(request)
        return delete_published_report(report_id, clean_folder(request.query_params.get("folder")))

    @app.post("/api/admin/reports/{report_id}/preview")
    async def admin_preview_report(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        definition = ctx.report_for_request(request, report_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Report ID is not registered")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return preview_definition(definition, payload, request)

    @app.post("/api/admin/reports/{report_id}/run")
    async def admin_run_report(report_id: str, request: Request) -> Response:
        """Run the full report from the admin/designer Preview and return the file (any format,
        any data adapter). Same rendering as /generate; recorded in the audit log."""
        require_admin(request, "designer")
        definition = ctx.report_for_request(request, report_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Report ID is not registered")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return run_definition_file(definition, payload, request.url.path, "admin-run", request=request)
