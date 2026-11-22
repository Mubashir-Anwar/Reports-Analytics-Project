"""/server portal API (login session): repository, run, resources, copy/move, upload/download, folders."""
from __future__ import annotations

import copy
import json
import os
import secrets
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from typing import Any

from defusedxml import ElementTree as SafeET
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.app.analytics import store as analytics_store
from backend.app.connectors.profiles import load_connection_profiles
from backend.app.constants import BOOTSTRAP_USERNAME, OUTPUT_FORMATS, REPORT_ID_PATTERN, SESSION_COOKIE, USER_RIGHTS, WORKSPACE_META
from backend.app.definitions import clean_folder
from backend.app.logs import LOG
from backend.app.mrd import parse_report_definition_xml, render_report_definition_xml
from backend.app.security import permissions, shared_state
from backend.app.security.access import client_address, enabled_areas
from backend.app.security.users import (
    bootstrap_login_active,
    check_user_password,
    derived_role,
    find_user,
    is_super_admin,
    load_users,
    make_session_cookie,
    password_needs_upgrade,
    read_session_cookie,
    save_users,
    session_lifetime_seconds,
    set_user_password,
    user_rights,
)
from backend.app.storage.files import add_saved_folder, list_saved_folders, move_target, normalize_folder_path


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the server portal endpoints to the app."""
    admin_report_payload = ctx.admin_report_payload
    connection_summary = ctx.connection_summary
    current_session = ctx.current_session
    delete_published_report = ctx.delete_published_report
    delete_repository_folder = ctx.delete_repository_folder
    folder_contents = ctx.folder_contents
    free_report_id = ctx.free_report_id
    json_body = ctx.json_body
    now_iso = ctx.now_iso
    published_report_summaries = ctx.published_report_summaries
    record_history = ctx.record_history
    reports_by_id = ctx.reports_by_id
    require_json_request = ctx.require_json_request
    require_session = ctx.require_session
    resource_details = ctx.resource_details
    run_definition_file = ctx.run_definition_file
    save_report_payload = ctx.save_report_payload
    session_view = ctx.session_view
    sync_workspace_copy = ctx.sync_workspace_copy

    @app.post("/api/server/login")
    async def server_login(request: Request) -> JSONResponse:
        require_json_request(request)
        payload = await json_body(request)
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        client = client_address(request)
        if shared_state.recent_failures(client) >= 10:  # counted on every instance
            raise HTTPException(status_code=429, detail="Too many failed sign-ins; wait a few minutes and try again")
        user = find_user(username) if username else None
        session_user, version, bootstrap = "", 0, False
        if user is not None and not user.get("disabled") and password and check_user_password(user, password):
            session_user, version = user["username"], int(user.get("sessionVersion", 0))
        elif user is None and username.casefold() == BOOTSTRAP_USERNAME and bootstrap_login_active():
            admin_token = os.getenv("REPORTING_ADMIN_TOKEN", "")
            if admin_token and password and secrets.compare_digest(password, admin_token):
                session_user, bootstrap = BOOTSTRAP_USERNAME, True
        if not session_user:
            shared_state.record_failure(client)
            LOG.warning("Failed /server sign-in for user %r from %s", username[:64], client)
            ctx.audit_event(request, "auth.login_failed", username[:64], {"reason": "wrong username or password"},
                            actor=username[:64] or "?", actor_type="anonymous")
            raise HTTPException(status_code=401, detail="Wrong username or password")
        shared_state.clear_failures(client)
        if user is not None and not bootstrap:
            from backend.app.db.people import put_user  # noqa: PLC0415
            user["lastLoginAt"] = now_iso()
            if password_needs_upgrade(user):  # older, weaker hash: replace it now that we know the password
                set_user_password(user, password)
            put_user(user)                    # only this account's row changes
        rights = sorted(USER_RIGHTS) if bootstrap else sorted(user_rights(user))
        super_admin = bootstrap or is_super_admin(user)
        role = derived_role(set(rights), super_admin)
        LOG.info("User %s signed in (%s)", session_user, ", ".join(rights) or "no rights")
        ctx.audit_event(request, "auth.login", session_user, {"firstSetup": bootstrap, "rights": rights},
                        actor=session_user, actor_type="user")
        cookie = make_session_cookie(session_user, version, bootstrap)
        nonce = (read_session_cookie(cookie) or {}).get("n", "")
        agent = shared_state.describe_agent(request.headers.get("user-agent", ""), client)
        previous = shared_state.claim_session(session_user, nonce, agent, session_lifetime_seconds())
        if previous:
            LOG.info("User %s signed in on %s; the session on %s ends in %ss", session_user, agent, previous["agent"], shared_state.grace_seconds())
            ctx.audit_event(request, "auth.session_replaced", session_user, {"old": previous["agent"], "new": agent},
                            actor=session_user, actor_type="user")
        response = JSONResponse({"username": session_user, "role": role, "bootstrap": bootstrap, "rights": rights,
                                 "superAdmin": super_admin,
                                 "mustChangePassword": bool(user and not bootstrap and user.get("mustChangePassword")),
                                 "replacedOther": {"by": previous["agent"], "seconds": shared_state.grace_seconds()} if previous else None})
        secure = request.url.scheme == "https" or os.getenv("REPORTING_SECURE_COOKIES", "false").strip().lower() == "true"
        response.set_cookie(SESSION_COOKIE, cookie, max_age=session_lifetime_seconds(),
                            httponly=True, samesite="strict", secure=secure, path="/")
        return response

    @app.post("/api/server/logout")
    def server_logout(request: Request) -> JSONResponse:
        session = current_session(request)
        if session is not None:
            shared_state.revoke_session(session["nonce"], session_lifetime_seconds())  # on every instance
            shared_state.release_session(session["username"], session["nonce"])
            LOG.info("User %s signed out of /server", session["username"])
            ctx.audit_event(request, "auth.logout", session["username"], actor=session["username"], actor_type="user")
        response = JSONResponse({"status": "signed-out"})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/server/me")
    def server_me(request: Request) -> dict[str, Any]:
        """Who is signed in and which parts they may open (every page's app menu asks this)."""
        session = current_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Sign in first")
        return session_view(session)

    @app.get("/api/server/repository")
    def server_repository(request: Request) -> dict[str, Any]:
        """Only the folders and reports this user may see (folder permissions); each with the user's access."""
        session = require_session(request)
        try:
            folders = list_saved_folders()
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None
        rules = permissions.load_rules()
        reports = []
        for item in published_report_summaries():
            have = permissions.level(session, item["folder"], rules)
            if have >= permissions.RUN:
                reports.append({**item, "access": permissions.NAMES[have]})
        dashboards = []
        if "analytics" in enabled_areas():                 # dashboards live in the same folders, with the same permissions
            for item in analytics_store.dashboards():
                folder = item.get("folder") or "Analytics"
                have = permissions.level(session, folder, rules)
                if have >= permissions.RUN:
                    dashboards.append({"id": item["id"], "name": item.get("name", ""), "folder": folder, "description": item.get("description") or "",
                                       "tiles": len(item.get("tiles") or []), "owner": item.get("owner"), "updatedAt": item.get("updatedAt"),
                                       "access": permissions.NAMES[have]})
        seen_folders = {r["folder"] for r in reports} | {d["folder"] for d in dashboards}
        folders = sorted((set(folders) | {d["folder"] for d in dashboards}) - {"General"})   # General = the top level
        visible = [f for f in folders if permissions.level(session, f, rules) >= permissions.RUN
                   or any(s == f or s.startswith(f"{f}/") for s in seen_folders)]
        return {"folders": visible, "reports": sorted(reports, key=lambda item: item["title"].casefold()),
                "dashboards": sorted(dashboards, key=lambda item: item["name"].casefold()),
                "user": session_view(session), "folderAccess": {f: permissions.NAMES[permissions.level(session, f, rules)] for f in visible}}

    def report_at(session: dict[str, Any], report_id: str, request: Request, needed: int) -> Any:
        """The report in ?folder= (the same ID may be used in several folders), if this user may use it at `needed`."""
        return ctx.require_report(session, report_id, needed, folder=clean_folder(request.query_params.get("folder")))

    @app.post("/api/server/reports/{report_id}/run")
    async def server_run_report(report_id: str, request: Request) -> Response:
        session = require_session(request)
        definition = report_at(session, report_id, request, permissions.RUN)
        payload = await json_body(request)
        return run_definition_file(definition, payload, request.url.path, "server-session",
                                   fixed_connection=True, user=session["username"], session=session, request=request)

    @app.post("/api/server/folders")
    async def server_create_folder(request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        ctx.require_folder(session, folder.rsplit("/", 1)[0] if "/" in folder else "", permissions.MANAGE)
        return {"folder": folder, "folders": add_saved_folder(folder)}

    @app.delete("/api/server/folders")
    async def server_delete_folder(request: Request) -> dict[str, Any]:
        session = require_session(request, "admin")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("path", ""))
            move_to = move_target(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if move_to:
            ctx.require_folder(session, move_to, permissions.MANAGE)
        return delete_repository_folder(folder, payload.get("recursive") is True, session["username"], move_to)

    @app.get("/api/server/folders/contents")
    def server_folder_contents(path: str, request: Request) -> dict[str, Any]:
        """What deleting a folder would remove (shown in the confirmation)."""
        session = require_session(request)
        try:
            folder = normalize_folder_path(path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        ctx.require_folder(session, folder, permissions.RUN)
        return {"folder": folder, **folder_contents(folder)}

    @app.delete("/api/server/reports/{report_id}")
    def server_delete_report(report_id: str, request: Request) -> dict[str, str]:
        session = require_session(request, "admin")
        definition = report_at(session, report_id, request, permissions.MANAGE)
        result = delete_published_report(report_id, definition.folder)
        record_history(report_id, "deleted", session["username"], folder=definition.folder)
        LOG.info("Report %s removed from the server by %s", report_id, session["username"])
        return result

    @app.get("/api/server/reports/{report_id}")
    def server_report_details(report_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request)
        definition = report_at(session, report_id, request, permissions.RUN)
        return {**resource_details(definition, session), "access": permissions.NAMES[ctx.report_level(session, definition)]}

    @app.put("/api/server/reports/{report_id}")
    async def server_edit_report(report_id: str, request: Request) -> dict[str, Any]:
        """Edit a published report's properties: title, description, formats, data adapter, the questions
        (label, help, required, default, allowed values) and, for designers, the query."""
        session = require_session(request, "designer")
        definition = report_at(session, report_id, request, permissions.MANAGE)
        changes = await json_body(request)
        payload = copy.deepcopy(definition.editor_config)
        for key in ("title", "description"):
            if key in changes:
                payload[key] = str(changes[key] or "")[:300 if key == "title" else 2000]
        if "formats" in changes:
            formats = [f for f in changes["formats"] if f in OUTPUT_FORMATS] if isinstance(changes["formats"], list) else []
            if not formats:
                raise HTTPException(status_code=422, detail="Enable at least one output format")
            payload["formats"] = formats
        if "defaultFormat" in changes:
            payload["defaultFormat"] = str(changes["defaultFormat"])
        if payload.get("defaultFormat") not in payload.get("formats", sorted(OUTPUT_FORMATS)):
            payload["defaultFormat"] = payload.get("formats", ["pdf"])[0]
        if "connectionId" in changes:
            payload["connectionId"] = str(changes["connectionId"] or "").strip()
            wants_sql = str(payload.get("queryLanguage", "")).lower() == "sql"
            if (ctx.adapter_kind(payload["connectionId"]) != "mongodb") != wants_sql:
                raise HTTPException(status_code=422, detail="Choose a data adapter of the same kind as the query: "
                                                            + ("a SQL database" if wants_sql else "MongoDB"))
        if isinstance(changes.get("parameters"), list):
            edits = {str(item.get("name")): item for item in changes["parameters"] if isinstance(item, dict)}
            for parameter in payload.get("parameters", []):
                edit = edits.get(str(parameter.get("name")))
                if not edit or parameter.get("prompt") is False:
                    continue
                for key in ("label", "description", "required", "default", "allowedValues"):
                    if key in edit:
                        parameter[key] = edit[key]
                if parameter.get("default") in ("", None):
                    parameter.pop("default", None)
        if "querySource" in changes:
            payload["querySource"] = str(changes["querySource"] or "")
            if changes.get("queryLanguage") in ("mongosh", "json", "python"):
                payload["queryLanguage"] = changes["queryLanguage"]
            if "collection" in changes:
                payload["collection"] = str(changes["collection"] or "").strip()
        saved = save_report_payload(payload, expected_id=report_id, previous=definition, action="edited", user=session["username"])
        changed = sorted(k for k in changes if k in ("title", "description", "formats", "defaultFormat", "connectionId", "parameters", "querySource"))
        sync_workspace_copy(saved, lambda data: data.update({k: copy.deepcopy(saved.editor_config.get(k)) for k in
                                                                ("title", "description", "formats", "defaultFormat", "connectionId",
                                                                 "parameters", "querySource", "queryLanguage", "collection")
                                                                if k in saved.editor_config}))
        record_history(report_id, "edited", session["username"], ", ".join(changed), folder=saved.folder)
        return resource_details(saved, session)

    @app.post("/api/server/reports/{report_id}/copy")
    async def server_copy_report(report_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        definition = report_at(session, report_id, request, permissions.RUN)
        body = await json_body(request, optional=True)
        payload = copy.deepcopy(definition.editor_config)
        try:
            payload["folder"] = normalize_folder_path(body.get("folder") or payload.get("folder", "General"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        ctx.require_folder(session, payload["folder"], permissions.MANAGE)
        target = payload["folder"]
        # into another folder the copy keeps its ID (like JasperReports Server); in the same folder it needs a new one
        keep_id = target != definition.folder and f"{target}/{report_id}" not in reports_by_id
        new_id = str(body.get("newId") or "").strip() or (report_id if keep_id else free_report_id(report_id, target))
        if f"{target}/{new_id}" in reports_by_id:
            raise HTTPException(status_code=409, detail=f"/{target} already has a report with the ID {new_id}")
        payload["id"] = new_id
        payload["title"] = str(body.get("title") or (definition.title if new_id == report_id else f"{definition.title} (copy)"))[:300]
        saved = save_report_payload(payload, action="copied", user=session["username"])
        add_saved_folder(payload["folder"])
        record_history(new_id, "copied", session["username"], f"from /{definition.ref}", folder=saved.folder)
        return {"report": resource_details(saved, session)}

    @app.post("/api/server/reports/{report_id}/move")
    async def server_move_report(report_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        definition = report_at(session, report_id, request, permissions.MANAGE)
        body = await json_body(request)
        try:
            folder = normalize_folder_path(body.get("folder", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        ctx.require_folder(session, folder, permissions.MANAGE)
        before = definition.folder
        payload = {**copy.deepcopy(definition.editor_config), "folder": folder}
        saved = save_report_payload(payload, expected_id=report_id, previous=definition, action="moved", user=session["username"])
        add_saved_folder(folder)
        record_history(report_id, "moved", session["username"], f"{before} → {folder}", folder=saved.folder)
        return {"report": resource_details(saved, session)}

    @app.get("/api/server/reports/{report_id}/file")
    def server_download_report(report_id: str, request: Request) -> Response:
        """The whole report (query, parameters, data adapter, band design, images) as one .mrd file."""
        session = require_session(request, "designer")
        definition = report_at(session, report_id, request, permissions.MANAGE)  # the file holds the query
        return Response(render_report_definition_xml(admin_report_payload(definition)), media_type="application/xml; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{report_id}.mrd"'})

    @app.post("/api/server/upload")
    async def server_upload_report(request: Request) -> dict[str, Any]:
        """Add a resource: a .mrd file (or a Jaspersoft Studio .jrxml) is published into a folder."""
        session = require_session(request, "designer")
        body = await json_body(request)
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPException(status_code=400, detail="Choose a .mrd or .jrxml file")
        if len(content.encode("utf-8")) > 12_000_000:
            raise HTTPException(status_code=413, detail="The file is larger than 12 MB")
        warnings: list[str] = []
        try:
            root_tag = SafeET.fromstring(content, forbid_dtd=True).tag.split("}")[-1]
            if root_tag == "jasperReport":
                from backend.app.importers import jrxml as jrxml_import
                imported = jrxml_import.import_jrxml(content, body.get("resources"))   # + its image files
                payload = {**imported["report"], "design": imported["design"]}
                warnings = list(imported.get("warnings") or [])
            else:
                payload = parse_report_definition_xml(content)
        except (ET.ParseError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"The file could not be read: {exc}") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if body.get("newId"):
            payload["id"] = str(body["newId"]).strip()
        if body.get("connectionId"):
            payload["connectionId"] = str(body["connectionId"]).strip()
        if body.get("collection"):
            payload["collection"] = str(body["collection"]).strip()
        try:
            payload["folder"] = normalize_folder_path(body.get("folder") or payload.get("folder") or "General")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        report_id = str(payload.get("id", ""))
        ctx.require_folder(session, payload["folder"], permissions.MANAGE)
        existing = dict.get(reports_by_id, f"{payload['folder']}/{report_id}")   # the same ID in another folder is another report
        exists = existing is not None
        if exists and body.get("replace") is not True:
            raise HTTPException(status_code=409, detail=f"{report_id} is already in /{payload['folder']}; tick “Replace” or give it a new ID")
        saved = save_report_payload(payload, expected_id=report_id if exists else None, previous=existing, action="uploaded", user=session["username"])
        add_saved_folder(payload["folder"])
        record_history(report_id, "uploaded", session["username"], f"{'replaced' if exists else 'new'} in {payload['folder']}", folder=saved.folder)
        return {"report": resource_details(saved, session), "replaced": exists, "warnings": warnings}

    @app.get("/api/server/connections")
    def server_connections(request: Request) -> dict[str, Any]:
        require_session(request, "designer")
        return {"connections": [connection_summary(item["id"]) for item in load_connection_profiles()]}

    # ---------------------------------------------------------------- versions (every save of a server report)
    @app.get("/api/server/reports/{report_id}/versions")
    def server_report_versions(report_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        definition = report_at(session, report_id, request, permissions.MANAGE)
        return {"versions": ctx.list_versions(report_id, definition.folder)}

    @app.get("/api/server/reports/{report_id}/versions/{number}")
    def server_report_version(report_id: str, number: int, request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        definition = report_at(session, report_id, request, permissions.MANAGE)
        found = ctx.get_version(report_id, number, definition.folder) if REPORT_ID_PATTERN.fullmatch(report_id) else None
        if not found:
            raise HTTPException(status_code=404, detail="Version not found")
        return found

    @app.post("/api/server/reports/{report_id}/versions/{number}/restore")
    def server_restore_version(report_id: str, number: int, request: Request) -> dict[str, Any]:
        """Make an older version the current one again (as a new version; nothing is lost)."""
        session = require_session(request, "designer")
        require_json_request(request)
        current = report_at(session, report_id, request, permissions.MANAGE)
        found = ctx.get_version(report_id, number, current.folder)
        if not found or not isinstance(found.get("definition"), dict):
            raise HTTPException(status_code=404, detail="Version not found")
        payload = copy.deepcopy(found["definition"])
        payload["folder"] = current.folder  # restoring does not move it
        saved = save_report_payload(payload, expected_id=report_id, previous=current, action=f"restored version {number}", user=session["username"])

        def replace_draft(data: dict[str, Any]) -> None:
            meta = data.get(WORKSPACE_META)
            data.clear()
            data.update(copy.deepcopy(saved.editor_config))
            if meta:
                data[WORKSPACE_META] = meta
        sync_workspace_copy(saved, replace_draft)
        record_history(report_id, "restored", session["username"], f"version {number}", folder=saved.folder)
        return {"report": resource_details(saved, session), "versions": ctx.list_versions(report_id, saved.folder)}


    # ---------------------------------------------------------------- folder permissions (administrators)
    @app.get("/api/server/permissions")
    def server_get_permissions(request: Request, path: str = "") -> dict[str, Any]:
        """The rules on one folder ("" = the root), what applies there from parent folders, and every user's result."""
        require_session(request, "admin")
        try:
            folder = normalize_folder_path(path) if path else ""
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        rules = permissions.load_rules()
        entry = {"users": {}, "roles": {}, "groups": {}, **rules.get(folder, {})}
        effective = []
        for user in load_users():
            if user.get("disabled"):
                continue
            rights = user_rights(user)
            role = derived_role(rights, is_super_admin(user))
            if "server" in rights:
                level, why = permissions.explain({"username": user["username"], "role": role}, folder, rules)
            else:
                level, why = permissions.NONE, "no Report Server right"
            effective.append({"username": user["username"], "role": role, "level": permissions.NAMES[level], "why": why})
        inherited = [{"folder": f, **rules[f]} for f in permissions.folder_chain(folder)[1:] if f in rules]
        from backend.app.db.people import load_groups  # noqa: PLC0415
        return {"folder": folder, "rules": entry, "inherited": inherited, "effective": effective,
                "users": sorted(u["username"] for u in load_users()),
                "groups": [{"groupId": g["groupId"], "name": g["name"], "members": g["members"]} for g in load_groups()]}

    @app.put("/api/server/permissions")
    async def server_set_permissions(request: Request) -> dict[str, Any]:
        session = require_session(request, "admin")
        require_json_request(request)
        body = await json_body(request)
        try:
            folder = normalize_folder_path(body["path"]) if body.get("path") else ""
            entry = permissions.clean_entry(body.get("rules"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        known = {str(u["username"]).casefold() for u in load_users()}
        unknown = sorted(name for name in entry["users"] if name not in known)
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown user(s): {', '.join(unknown)}")
        permissions.save_rules_for(folder, entry, by=session["username"])
        LOG.info("Permissions of /%s changed by %s: %s", folder, session["username"], entry)
        return server_get_permissions(request, folder)


    @app.post("/api/server/connections/{connection_id}/test")
    def server_test_connection(connection_id: str, request: Request) -> dict[str, Any]:
        """Test a data adapter from a report's Data Source tab (designers and admins; no password is shown)."""
        require_session(request, "designer")
        require_json_request(request)
        return ctx.test_adapter_by_id(connection_id)
