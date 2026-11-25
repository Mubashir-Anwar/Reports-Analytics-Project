"""The designer's workspace (drafts) and Publish to server."""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.connectors.profiles import load_connection_profiles
from backend.app.constants import REPORT_ID_PATTERN, WORKSPACE_META
from backend.app.db import model
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

    EDITING_FRESH_SECONDS = 90          # the designer says "still editing" every 30 s; older marks are ignored

    def editing_now(row: dict[str, Any] | None, me: str) -> dict[str, Any] | None:
        """Someone else who has this draft open in the designer right now."""
        if not row or not row.get("lockedBy") or row["lockedBy"] == me or not row.get("lockedAt"):
            return None
        age = (model.now() - model.parse_time(row["lockedAt"])).total_seconds()
        return {"user": row["lockedBy"], "since": row["lockedAt"]} if age < EDITING_FRESH_SECONDS else None

    def draft_state(key: str, me: str) -> dict[str, Any]:
        """Version number (for the save clash check) and who else is editing the draft."""
        row = get_store().report_row(key) or {}
        return {"versionNo": row.get("versionNo") or 0, "publishStatus": row.get("publishStatus") or "draft",
                "updatedBy": row.get("updatedBy") or "", "editingBy": editing_now(row, me)}

    def actor(request: Request) -> str:
        return str(getattr(request.state, "actor", "") or "designer")[:128]

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
        return {**strip_workspace_meta(entry[1]), "workspace": {**workspace_summary(entry[1]), **draft_state(entry[0], actor(request))}}

    @app.post("/api/admin/workspace/{report_id}/editing")
    async def workspace_editing(report_id: str, request: Request) -> dict[str, Any]:
        """The designer has this draft open ({"open": true}, every 30 s) or closed it ({"open": false}).
        Answers who else is editing it and its version number now."""
        require_admin(request, "designer")
        body = await json_body(request, optional=True)
        entry = find_workspace_entry(report_id, asked_folder(request))
        if entry is None:
            raise HTTPException(status_code=404, detail="This report is not in the designer workspace")
        me, store = actor(request), get_store()
        row = store.report_row(entry[0]) or {}
        other = editing_now(row, me)
        if body.get("open", True) is False:
            if row.get("lockedBy") == me:
                store.set_row_fields(entry[0], lockedBy=None, lockedAt=None)
        elif other is None:
            store.set_row_fields(entry[0], lockedBy=me, lockedAt=model.now())   # the first one keeps the mark
        return draft_state(entry[0], me)

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
        base = payload.pop("baseVersion", None)
        if existing is not None and base is not None:
            current = draft_state(existing[0], actor(request))
            if str(current["versionNo"]) != str(base):
                who = current["updatedBy"] or "someone"
                raise HTTPException(status_code=409, detail={
                    "code": "changed-by-someone-else", "versionNo": current["versionNo"], "savedBy": who,
                    "savedAt": (existing[1].get(WORKSPACE_META) or {}).get("updatedAt", ""),
                    "message": f"{who} saved this report after you opened it. Reload it to see their changes, or save yours as a copy"})
        meta = dict((existing[1].get(WORKSPACE_META) or {}) if existing else {"createdAt": now_iso()})
        meta["updatedAt"] = now_iso()
        meta["updatedBy"] = actor(request)
        clean = strip_workspace_meta(payload)
        clean["folder"] = folder
        switched = ctx.draft_connections(clean)                # opened from the server: data sources of the Designer instead
        clean[WORKSPACE_META] = meta
        target = workspace_file(report_id, folder)
        store = get_store()
        if existing is not None and existing[0] != target:
            store.move_json(existing[0], target, clean)                 # moved: the draft keeps its id
        else:
            store.write_json(target, clean)
        problem = ""
        try:
            _dynamic_report(strip_workspace_meta(clean))
        except (TypeError, ValueError, KeyError) as exc:
            problem = str(exc)
        return {"report": {**workspace_summary(clean), **draft_state(target, actor(request))}, "readyToPublish": not problem, "problem": problem,
                "connectionId": clean.get("connectionId", ""), "switchedConnections": switched}

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

    def drafts_in(folder: str) -> list[tuple[str, dict[str, Any]]]:
        return [(key, data) for key, data in workspace_entries()
                if (clean_folder(data.get("folder")) or "General") == folder or str(data.get("folder", "")).startswith(f"{folder}/")]

    def folder_path(raw: Any) -> str:
        try:
            folder = normalize_folder_path(raw or "")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if folder == "General":
            raise HTTPException(status_code=400, detail="General is the top level, not a folder")
        return folder

    @app.get("/api/admin/workspace-folders/contents")
    def workspace_folder_contents(path: str, request: Request) -> dict[str, Any]:
        """What deleting a workspace folder would touch: its subfolders and drafts (at any depth)."""
        require_admin(request, "designer")
        folder = folder_path(path)
        entries = workspace_entries()
        return {"folder": folder, "subfolders": [item for item in workspace_folders(entries) if item.startswith(f"{folder}/")],
                "reports": sorted(str(data.get("title") or data["id"]) for _, data in drafts_in(folder)),
                "published": sorted(str(data.get("title") or data["id"]) for _, data in drafts_in(folder) if workspace_summary(data)["onServer"]),
                "dashboards": [], "adapters": []}

    @app.delete("/api/admin/workspace-folders")
    async def workspace_delete_folder(request: Request) -> dict[str, Any]:
        """Delete a workspace folder. moveTo (a folder, '' = top level) first moves its drafts and subfolders there;
        recursive=true deletes the drafts in it (their published copies stay on the server)."""
        require_admin(request, "designer")
        payload = await json_body(request)
        folder = folder_path(payload.get("path", ""))
        raw_target = payload.get("moveTo")
        move_to = None if raw_target is None else (folder_path(raw_target) if str(raw_target).strip().strip("/") else "")
        store = get_store()
        drafts = drafts_in(folder)
        subfolders = [item for item in workspace_folders(workspace_entries()) if item.startswith(f"{folder}/")]
        if move_to is not None:
            if move_to == folder or move_to.startswith(f"{folder}/"):
                raise HTTPException(status_code=400, detail="Choose a folder outside the one you delete")

            def moved(path: str) -> str:
                return "/".join(part for part in (move_to, path[len(folder):].strip("/")) if part) or "General"
            taken = {(clean_folder(data.get("folder")) or "General", data["id"]) for _, data in workspace_entries()}
            clashes = [data["id"] for _, data in drafts if (moved(clean_folder(data.get("folder")) or "General"), data["id"]) in taken]
            if clashes:
                raise HTTPException(status_code=409, detail=f"The target folder already has: {', '.join(clashes[:5])}. Choose another folder")
            for sub in subfolders:
                store.make_dir("/".join(["workspace", *moved(sub).split("/")]))
            for key, data in drafts:
                data["folder"] = moved(clean_folder(data.get("folder")) or "General")
                store.move_json(key, workspace_file(data["id"], data["folder"]), data)
        elif drafts and payload.get("recursive") is not True:
            raise HTTPException(status_code=409, detail=f"The folder has {len(drafts)} report(s); confirm to delete them, or move them to another folder")
        else:
            for key, _ in drafts:
                store.delete(key)
        if not store.remove_dir("/".join(["workspace", *folder.split("/")]), recursive=True):
            raise HTTPException(status_code=409, detail="The folder could not be removed")
        LOG.info("Workspace folder %s deleted (%d drafts %s)", folder, len(drafts), f"moved to /{move_to}" if move_to is not None else "deleted")
        return {"deleted": folder, "folders": workspace_folders(workspace_entries()), "drafts": len(drafts),
                "subfolders": len(subfolders), "movedTo": move_to}

    def server_connections(payload: dict[str, Any], chosen: Any) -> None:
        """The draft reads from data sources of the Designer; on the server it reads from data sources of the server.
        `chosen` = {designer id: server id}; without it the one used last time, or one with the same connection."""
        chosen = chosen if isinstance(chosen, dict) else {}
        profiles = {item["id"]: item for item in load_connection_profiles()}
        ids: dict[str, str] = {}
        for connection_id in ctx.report_connection_ids(payload):
            source = profiles.get(connection_id)
            if source is None:
                continue                                       # save_report_payload says what is wrong
            picked = str(chosen.get(connection_id) or "").strip()
            if picked:
                target = profiles.get(picked)
                if target is None:
                    raise HTTPException(status_code=422, detail=f"There is no data source {picked} on the Report Server")
                ctx.require_area(picked, "server")
                if target.get("type", "mongodb") != source.get("type", "mongodb"):
                    raise HTTPException(status_code=422, detail=f"“{target['name']}” is another kind of database than “{source['name']}”")
                ctx.remember_counterpart(connection_id, "server", picked)
            ids[connection_id] = picked or ctx.counterpart(connection_id, "server")
            if not ids[connection_id]:
                raise HTTPException(status_code=422, detail=f"Choose the Report Server's data source in place of “{source['name']}” "
                                                            "(the Designer's data sources work only in the Designer)")
        ctx.switch_connections(payload, ids)

    @app.post("/api/admin/workspace/{report_id}/publish")
    async def workspace_publish(report_id: str, request: Request) -> dict[str, Any]:
        """Publish a workspace report to the server repository (like Jaspersoft Studio → JasperReports Server)."""
        role = require_admin(request, "designer")
        options = await json_body(request, optional=True)
        entry = find_workspace_entry(report_id, asked_folder(request))
        if entry is None:
            raise HTTPException(status_code=404, detail="Save the report in the designer first, then publish it")
        path, data = entry
        payload = copy.deepcopy(strip_workspace_meta(data))
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
        server_connections(payload, options.get("connections"))
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
        return {"report": admin_report_payload(saved), "replaced": existing is not None,
                "workspace": {**workspace_summary(data), **draft_state(path, actor(request))}}
