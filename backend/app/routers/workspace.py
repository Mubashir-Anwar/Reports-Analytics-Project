"""The designer's workspace (drafts) and Publish to server."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.constants import REPORT_ID_PATTERN, WORKSPACE_META
from backend.app.definitions import _dynamic_report, clean_folder
from backend.app.logs import LOG
from backend.app.storage.files import add_saved_folder, normalize_folder_path
from backend.app.storage.store import get_store


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the workspace endpoints to the app."""
    admin_report_payload = ctx.admin_report_payload
    ensure_workspace = ctx.ensure_workspace
    find_workspace_entry = ctx.find_workspace_entry
    json_body = ctx.json_body
    now_iso = ctx.now_iso
    record_history = ctx.record_history
    reports_by_id = ctx.reports_by_id
    require_admin = ctx.require_admin
    save_report_payload = ctx.save_report_payload
    strip_workspace_meta = ctx.strip_workspace_meta
    workspace_entries = ctx.workspace_entries
    workspace_file = ctx.workspace_file
    workspace_folders = ctx.workspace_folders
    workspace_summary = ctx.workspace_summary

    @app.get("/api/admin/workspace")
    def workspace_list(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        entries = workspace_entries()
        return {"reports": [workspace_summary(data) for _, data in entries], "folders": workspace_folders(entries)}

    def asked_folder(request: Request) -> str | None:
        """?folder= : which of the drafts with this ID (the same ID may be in several folders)."""
        return clean_folder(request.query_params.get("folder"))

    @app.get("/api/admin/workspace/{report_id}")
    def workspace_get(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        entry = find_workspace_entry(report_id, asked_folder(request))
        if entry is None:
            raise HTTPException(status_code=404, detail="This report is not in the designer workspace")
        return {**strip_workspace_meta(entry[1]), "workspace": workspace_summary(entry[1])}

    @app.put("/api/admin/workspace/{report_id}")
    async def workspace_save(report_id: str, request: Request, create: bool = False) -> dict[str, Any]:
        """Save a draft inside the designer. Drafts may be incomplete; nothing reaches the server."""
        require_admin(request, "designer")
        payload = await json_body(request)
        if not REPORT_ID_PATTERN.fullmatch(report_id) or str(payload.get("id", "")) != report_id:
            raise HTTPException(status_code=400, detail="Give the report a valid ID (letters, numbers, _ and -); it must match the URL")
        try:
            folder = normalize_folder_path(payload.get("folder", "General"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        # ?folder= is where the draft was opened from (a save may move it); the same ID may be in another folder
        in_target = find_workspace_entry(report_id, folder)
        if create:
            if in_target is not None:
                raise HTTPException(status_code=409, detail=f"A report with the ID {report_id} is already in your workspace folder {folder}; "
                                                            "choose another ID or folder")
            existing = None
        else:
            opened_from = asked_folder(request)
            existing = find_workspace_entry(report_id, opened_from) if opened_from is not None else (in_target or find_workspace_entry(report_id))
            if in_target is not None and existing is not None and in_target[0] != existing[0]:
                raise HTTPException(status_code=409, detail=f"Your workspace folder {folder} already has a report with the ID {report_id}; "
                                                            "choose another ID or folder")
        meta = dict((existing[1].get(WORKSPACE_META) or {}) if existing else {"createdAt": now_iso()})
        meta["updatedAt"] = now_iso()
        clean = strip_workspace_meta(payload)
        clean["folder"] = folder
        clean[WORKSPACE_META] = meta
        target = workspace_file(report_id, folder)
        store = get_store()
        store.write_json(target, clean)
        if existing is not None and existing[0] != target:
            store.delete(existing[0])
        problem = ""
        try:
            _dynamic_report(strip_workspace_meta(clean))
        except (TypeError, ValueError, KeyError) as exc:
            problem = str(exc)
        return {"report": workspace_summary(clean), "readyToPublish": not problem, "problem": problem}

    @app.delete("/api/admin/workspace/{report_id}")
    def workspace_delete(report_id: str, request: Request) -> dict[str, Any]:
        """Remove a draft from the workspace. A published copy on the server is not touched."""
        require_admin(request, "designer")
        entry = find_workspace_entry(report_id, asked_folder(request))
        if entry is None:
            raise HTTPException(status_code=404, detail="This report is not in the designer workspace")
        get_store().delete(entry[0])
        folder = clean_folder(entry[1].get("folder")) or "General"
        return {"status": "deleted", "reportId": report_id, "stillOnServer": f"{folder}/{report_id}" in reports_by_id}

    @app.post("/api/admin/workspace-folders")
    async def workspace_create_folder(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        ensure_workspace()
        get_store().make_dir("/".join(["workspace", *folder.split("/")]))
        return {"folder": folder, "folders": workspace_folders(workspace_entries())}

    @app.delete("/api/admin/workspace-folders")
    async def workspace_delete_folder(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if folder == "General":
            raise HTTPException(status_code=400, detail="The General folder cannot be deleted")
        if any(str(data.get("folder", "")) == folder or str(data.get("folder", "")).startswith(f"{folder}/") for _, data in workspace_entries()):
            raise HTTPException(status_code=409, detail="Move or delete the reports in this folder first")
        if not get_store().remove_dir("/".join(["workspace", *folder.split("/")])):
            raise HTTPException(status_code=409, detail="The folder is not empty")
        return {"deleted": folder, "folders": workspace_folders(workspace_entries())}

    @app.post("/api/admin/workspace/{report_id}/publish")
    async def workspace_publish(report_id: str, request: Request) -> dict[str, Any]:
        """Publish a workspace report to the server repository (like Jaspersoft Studio → JasperReports Server)."""
        role = require_admin(request, "designer")
        options = await json_body(request, optional=True)
        entry = find_workspace_entry(report_id, asked_folder(request))
        if entry is None:
            raise HTTPException(status_code=404, detail="Save the report in the designer first, then publish it")
        path, data = entry
        payload = strip_workspace_meta(data)
        last = (data.get(WORKSPACE_META) or {}).get("publishedFolder")
        if not options.get("folder") and last:
            payload["folder"] = last                                     # no folder given: where it was published last
        if options.get("folder"):
            try:
                payload["folder"] = normalize_folder_path(options["folder"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
        target = clean_folder(payload.get("folder")) or "General"
        existing = dict.get(reports_by_id, f"{target}/{report_id}")   # only the report in the target folder is replaced
        if existing is not None and options.get("replace") is not True:
            raise HTTPException(status_code=409, detail=f"{report_id} is already published in /{target}; confirm that the server copy should be replaced")
        saved = save_report_payload(payload, expected_id=report_id if existing is not None else None, previous=existing,
                                    action="published", user=request.state.actor or f"designer ({role})")
        folder = saved.editor_config.get("folder", "General") if saved.editor_config else payload.get("folder", "General")
        add_saved_folder(folder)
        meta = dict(data.get(WORKSPACE_META) or {})
        meta.update({"publishedAt": now_iso(), "publishedFolder": folder, "publishedBy": role})
        if meta.get("updatedAt", "") > meta["publishedAt"]:
            meta["updatedAt"] = meta["publishedAt"]
        data[WORKSPACE_META] = meta
        get_store().write_json(path, data)
        record_history(report_id, "published", request.state.actor or f"designer ({role})", f"{'replaced' if existing is not None else 'new'} in {folder}",
                       folder=saved.folder)
        LOG.info("Report %s published to the server folder %s", report_id, folder)
        return {"report": admin_report_payload(saved), "replaced": existing is not None, "workspace": workspace_summary(data)}
