"""Shared service state and helpers (formerly the top of make_app())."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from backend.app.config import Settings
from backend.app.connectors import mongodb
from backend.app.connectors.errors import MongoReadAccessError
from backend.app.connectors.mongo_fallback import _row_value
from backend.app.connectors.mongodb import _mongo_database_from_uri, _safe_mongo_error
from backend.app.connectors.profiles import _profile_credentials, load_connection_profiles
from backend.app.constants import (
    BOOTSTRAP_USERNAME,
    METADATA_KEYS,
    MIN_ACCESS_SECRET_LENGTH,
    MIN_PASSWORD_LENGTH,
    OUTPUT_FORMATS,
    PROJECT_ROOT,
    REPORT_ID_PATTERN,
    ROLE_TOKEN_MIN_LENGTH,
    SERVICE_VERSION,
    SESSION_COOKIE,
    USER_ROLES,
    WORKSPACE_META,
)
from backend.app.definitions import ReportDefinition, _dynamic_report, _safe_value, discover_reports, validate_parameters
from backend.app.logs import LOG, configure_file_logging
from backend.app.queries.mongo_query import resolve_collection
from backend.app.rendering import engine as designer_render
from backend.app.rendering.outputs import render_report_output
from backend.app.security.access import enabled_areas
from backend.app.security.secrets import ensure_admin_token
from backend.app.security.users import bootstrap_login_active, find_user, read_session_cookie, role_at_least
from backend.app.storage import audit as audit_store
from backend.app.storage.files import (
    _write_json_atomically,
    data_directory,
    folders_file,
    initialize_data_directory,
    list_saved_folders,
    normalize_folder_path,
    report_definition_path,
    report_definitions_directory,
    workspace_directory,
)


def create_context() -> SimpleNamespace:
    """Start-up work and the state shared by every endpoint: settings, the repository cache, connection
    clients, and the helper functions the routers call (each router unpacks the ones it needs)."""
    settings = Settings.from_env()
    initialize_data_directory()
    log_path = configure_file_logging()
    LOG.info("Reporting service %s starting; data directory %s; log file %s", SERVICE_VERSION, data_directory(), log_path or "off")
    ensure_admin_token()
    designer_token = os.getenv("REPORTING_DESIGNER_TOKEN", "").strip()
    if designer_token and len(designer_token) < ROLE_TOKEN_MIN_LENGTH:
        LOG.warning("REPORTING_DESIGNER_TOKEN is ignored: it must be at least %d characters", ROLE_TOKEN_MIN_LENGTH)
    LOG.info("Enabled areas: %s", ", ".join(sorted(enabled_areas())) or "none")
    reports_by_id = discover_reports()
    if os.getenv("REPORTING_ACCESS_MODE", "required").strip().lower() == "open":
        LOG.warning("REPORTING_ACCESS_MODE=open: /generate and /reports need no credentials; keep this service off public networks")
    elif not os.getenv("REPORTING_ACCESS_SECRET", "").strip():
        LOG.warning("REPORTING_ACCESS_SECRET is not set: /generate and /reports accept only the admin token")
    state: dict[str, Any] = {"clients": {}, "connection_signatures": {}, "revoked_sessions": set(), "login_failures": {}}

    def get_connection(connection_id: str) -> tuple[MongoClient, str]:
        profiles = {item["id"]: item for item in load_connection_profiles()}
        profile = profiles.get(connection_id)
        if profile is None:
            raise HTTPException(status_code=400, detail=f"MongoDB connection ID is not registered: {connection_id}")
        uri, database = _profile_credentials(profile)
        if not uri or not database:
            if profile.get("storage") == "file":
                detail = f"No MongoDB URL/database was found for connection '{connection_id}' in the private settings file"
            else:
                detail = f"Set {profile['uriEnv']} and {profile['databaseEnv']} for connection '{connection_id}'"
            raise HTTPException(
                status_code=503,
                detail=detail,
            )
        signature = (uri, database)
        client = state["clients"].get(connection_id)
        if client is not None and state["connection_signatures"].get(connection_id) != signature:
            client.close()
            state["clients"].pop(connection_id, None)
            state["connection_signatures"].pop(connection_id, None)
            client = None
        if client is None:
            candidate = mongodb.MongoClient(uri, serverSelectionTimeoutMS=5000)
            try:
                candidate.admin.command("ping")
            except PyMongoError:
                candidate.close()
                LOG.exception("MongoDB connection '%s' ping failed", connection_id)
                raise HTTPException(status_code=503, detail=f"MongoDB connection '{connection_id}' unavailable") from None
            client = candidate
            state["clients"][connection_id] = client
            state["connection_signatures"][connection_id] = signature
        return client, database

    def test_mongo_connection(uri: str, database: str, connection_id: str) -> dict[str, Any]:
        """Check reachability and find access for collections used by saved reports."""
        uri = uri.strip()
        uri_database = _mongo_database_from_uri(uri)
        database_override = database.strip()
        selected_database = database_override or uri_database
        database_warning = (
            f"The explicit database '{selected_database}' differs from the database '{uri_database}' in the URL path."
            if uri_database and database_override and uri_database != database_override else ""
        )
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise HTTPException(status_code=422, detail="The MongoDB URL must start with mongodb:// or mongodb+srv://")
        if not selected_database:
            raise HTTPException(status_code=422, detail="The URL has no database name; enter the database name as well")

        collections: set[str] = set()

        def collect_references(node: Any) -> None:
            if isinstance(node, dict):
                for operator in ("$lookup", "$graphLookup"):
                    stage = node.get(operator)
                    if isinstance(stage, dict) and isinstance(stage.get("from"), str):
                        collections.add(stage["from"])
                union = node.get("$unionWith")
                if isinstance(union, str):
                    collections.add(union)
                elif isinstance(union, dict) and isinstance(union.get("coll"), str):
                    collections.add(union["coll"])
                for child in node.values():
                    collect_references(child)
            elif isinstance(node, list):
                for child in node:
                    collect_references(child)

        for definition in reports_by_id.values():
            if definition.connection_id != connection_id:
                continue
            if "$P{" not in definition.collection:
                collections.add(definition.collection)
            collect_references((definition.editor_config or {}).get("pipeline", []))

        client: MongoClient | None = None
        current_collection = ""
        authenticated_users: list[str] = []
        identity_reported = False
        try:
            client = mongodb.MongoClient(uri, serverSelectionTimeoutMS=5000)
            client.admin.command("ping")
            try:
                status = client.admin.command({"connectionStatus": 1, "showPrivileges": False})
                auth_info = status.get("authInfo", {})
                authenticated_users = [
                    f"{item.get('user', '')}@{item.get('db', '')}"
                    for item in auth_info.get("authenticatedUsers", [])
                    if item.get("user") and item.get("db")
                ]
                identity_reported = True
            except PyMongoError:
                LOG.debug("MongoDB connectionStatus was unavailable during the admin connection check")
            checked: list[str] = []
            for current_collection in sorted(collections):
                cursor = client[selected_database][current_collection].find(
                    {}, {"_id": 1}, max_time_ms=5000
                ).limit(1)
                next(cursor, None)
                checked.append(current_collection)
            return {
                "status": "ok",
                "connectionId": connection_id,
                "database": selected_database,
                "uriDatabase": uri_database,
                "databaseWarning": database_warning,
                "readChecked": checked,
                "authenticatedUsers": authenticated_users,
                "identityReported": identity_reported,
                "message": (
                    "MongoDB server reachable; report collection read access verified"
                    if checked else
                    "MongoDB server reachable; no saved report is using this data source yet, so collection read access was not checked"
                ),
            }
        except PyMongoError as exc:
            if getattr(exc, "code", None) == 13 and current_collection:
                identity_detail = (
                    f" MongoDB authenticated user(s): {', '.join(authenticated_users)}."
                    if authenticated_users else
                    " MongoDB connection reports no authenticated user."
                    if identity_reported else
                    " MongoDB authenticated identity could not be read by connectionStatus."
                )
                database_detail = f" {database_warning}" if database_warning else ""
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"The MongoDB server is reachable, but find/read on the collection '{selected_database}.{current_collection}' "
                        "was rejected for the current URL. The connection test and the report use this same saved URL."
                        + identity_detail + database_detail
                    ),
                ) from None
            detail = _safe_mongo_error(exc, uri)
            LOG.warning("MongoDB connection/read check failed: %s", detail)
            raise HTTPException(status_code=503, detail=f"MongoDB connection/read check failed: {detail}") from None
        finally:
            if client is not None:
                client.close()

    def _verify_signed_link(request: Request, report_file: str, secret: str) -> None:
        """Check `Signature` = hex HMAC-SHA256(secret, "/generate/<file>?<query before &Signature>")."""
        try:
            raw_query = request.scope.get("query_string", b"").decode("ascii")
        except UnicodeDecodeError:
            raise HTTPException(status_code=403, detail="The signed link query is not encoded correctly") from None
        parts = raw_query.split("&") if raw_query else []
        signature_parts = [part for part in parts if part.split("=", 1)[0] == "Signature"]
        if len(signature_parts) != 1 or parts[-1] != signature_parts[0]:
            raise HTTPException(status_code=403, detail="Signature must appear once, at the end of the link")
        provided = signature_parts[0].split("=", 1)[1] if "=" in signature_parts[0] else ""
        unsigned_query = "&".join(parts[:-1])
        message = f"/generate/{report_file}" + (f"?{unsigned_query}" if unsigned_query else "")
        expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(provided.lower(), expected):
            raise HTTPException(status_code=403, detail="The signed link Signature is not valid (the link was changed or the secret is wrong)")
        expires_values = request.query_params.getlist("Expires")
        if len(expires_values) != 1 or not expires_values[0].isdigit():
            raise HTTPException(status_code=403, detail="A signed link needs one Expires value (Unix seconds)")
        expires = int(expires_values[0])
        now = int(time.time())
        max_lifetime = int(os.getenv("REPORTING_SIGNED_LINK_MAX_SECONDS", "3600"))
        if expires <= now:
            raise HTTPException(status_code=403, detail="The signed link has expired")
        if expires - now > max_lifetime:
            raise HTTPException(status_code=403, detail=f"A signed link cannot be valid for more than {max_lifetime} seconds")

    def require_report_access(request: Request, report_file: str | None = None) -> str:
        """Who may list and run reports. Returns how access was granted (stored in the audit record).

        REPORTING_ACCESS_MODE=required (default) accepts any one of:
          * X-Report-Token header equal to REPORTING_ACCESS_SECRET (website server calls the service),
          * a signed /generate link (Expires + Signature made with REPORTING_ACCESS_SECRET),
          * X-Admin-Token (the admin and runner pages).
        REPORTING_ACCESS_MODE=open keeps the old behaviour for a private, network-protected setup.
        """
        mode = os.getenv("REPORTING_ACCESS_MODE", "required").strip().lower()
        if mode == "open":
            return "open"
        if mode != "required":
            raise HTTPException(status_code=503, detail="REPORTING_ACCESS_MODE must be 'required' or 'open'")
        secret = os.getenv("REPORTING_ACCESS_SECRET", "").strip()
        if secret and len(secret) < MIN_ACCESS_SECRET_LENGTH:
            raise HTTPException(
                status_code=503,
                detail=f"REPORTING_ACCESS_SECRET must be at least {MIN_ACCESS_SECRET_LENGTH} characters long",
            )
        report_token = request.headers.get("X-Report-Token", "")
        if secret and report_token and secrets.compare_digest(report_token, secret):
            return "report-token"
        admin_token = os.getenv("REPORTING_ADMIN_TOKEN", "")
        admin_header = request.headers.get("X-Admin-Token", "")
        if admin_token and admin_header and secrets.compare_digest(admin_header, admin_token):
            return "admin-token"
        if report_file is not None and "Signature" in request.query_params:
            if not secret:
                raise HTTPException(status_code=403, detail="REPORTING_ACCESS_SECRET is not set on the server, so signed links cannot be checked")
            _verify_signed_link(request, report_file, secret)
            return "signed-link"
        LOG.warning("Report access denied for %s", request.url.path)
        raise HTTPException(
            status_code=401,
            detail="Report access needs a signed link, the X-Report-Token header or the admin token",
        )

    def chosen_connection(payload: dict[str, Any], definition: ReportDefinition) -> str:
        """Data adapter for an admin preview/run: the one picked in the UI, else the report's own."""
        requested = str(payload.get("connectionId") or "").strip()
        if not requested:
            return definition.connection_id
        if requested not in {item["id"] for item in load_connection_profiles()}:
            raise ValueError(f"Data adapter is not registered: {requested}")
        return requested

    def admin_parameters(payload: dict[str, Any], definition: ReportDefinition) -> dict[str, Any]:
        raw_params = payload.get("parameters", {})
        if not isinstance(raw_params, dict):
            raise ValueError("parameters must be a JSON object")
        prompted = {spec.name for spec in definition.parameters if spec.prompt}
        # computed parameters are calculated on the server; ignore any value the UI sends for them
        pairs = [(str(key), str(value)) for key, value in raw_params.items() if key in prompted or key in METADATA_KEYS]
        parameters, _ = validate_parameters(definition, pairs)
        return parameters

    def run_report_rows(definition: ReportDefinition, parameters: dict[str, Any], connection_id: str, *, max_rows: int, limit_rows: bool) -> list[dict[str, Any]]:
        pipeline = [*definition.pipeline_builder(parameters), {"$limit": max_rows if limit_rows else max_rows + 1}]
        client, database = get_connection(connection_id)
        rows = mongodb._execute_report_pipeline(
            client[database][resolve_collection(definition.collection, parameters)],
            pipeline,
            max_time_ms=settings.max_time_ms,
            allow_disk_use=settings.allow_disk_use,
            max_rows=max_rows,
        )
        if not limit_rows and len(rows) > max_rows:
            raise HTTPException(status_code=413, detail=f"The report is larger than the row limit ({max_rows})")
        return rows

    def subreport_loader() -> Any:
        """Loader for Subreport elements during one render: runs a published report with the given parameters.
        Results are cached per (report, parameters); at most 200 different runs per report."""
        cache: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}

        def load(report_id: str, values: dict[str, Any]) -> dict[str, Any]:
            key = (report_id, tuple(sorted((str(k), str(v)) for k, v in values.items())))
            if key in cache:
                return cache[key]
            if len(cache) >= 200:
                raise ValueError("more than 200 different subreport runs in one report")
            definition = reports_by_id.get(report_id)
            if definition is None:
                raise ValueError("this report is not published on the server")
            design = (definition.editor_config or {}).get("design")
            if not design:
                raise ValueError("this report has no band design (open it in the designer and publish it)")
            prompted = {spec.name for spec in definition.parameters if spec.prompt}
            parameters, _ = validate_parameters(definition, [(k, str(v)) for k, v in values.items() if k in prompted])
            found = run_report_rows(definition, parameters, definition.connection_id, max_rows=min(settings.max_rows, 5000), limit_rows=True)
            columns = list(definition.columns)
            sub_rows = [{column: _safe_value(_row_value(row, column)) for column in columns} for row in found]
            sub_parameters = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in parameters.items()}
            validated = designer_render.validate_design(design, columns, {spec.name for spec in definition.parameters} | set(sub_parameters))
            cache[key] = {"design": validated, "title": definition.title, "columns": columns, "rows": sub_rows, "parameters": sub_parameters}
            return cache[key]
        return load

    def access_error_detail(exc: Exception, connection_id: str) -> str:
        detail = f"{exc} Data adapter: '{connection_id}'."
        if len(load_connection_profiles()) > 1:
            detail += " If these collections are in another database, choose that data adapter in Preview and run it again."
        return detail

    def require_admin(request: Request, role: str = "admin") -> str:
        """Check the caller's token for a management endpoint and return the caller's role.

        role="admin": only REPORTING_ADMIN_TOKEN (data sources, deleting, logs).
        role="designer": REPORTING_ADMIN_TOKEN or REPORTING_DESIGNER_TOKEN (build, preview and run reports).
        An endpoint whose area is switched off with REPORTING_ENABLED_AREAS answers 404.
        """
        areas = enabled_areas()
        if (role == "admin" and "admin" not in areas) or (role == "designer" and not areas & {"admin", "designer"}):
            raise HTTPException(status_code=404, detail="Not Found")
        expected = os.getenv("REPORTING_ADMIN_TOKEN", "")
        if not expected:
            raise HTTPException(status_code=503, detail="Set the REPORTING_ADMIN_TOKEN environment variable to use the management API")
        provided = request.headers.get("X-Admin-Token", "")
        if provided and secrets.compare_digest(provided, expected):
            return "admin"
        designer_token = os.getenv("REPORTING_DESIGNER_TOKEN", "").strip()
        if len(designer_token) >= ROLE_TOKEN_MIN_LENGTH and provided and secrets.compare_digest(provided, designer_token):
            if role == "designer":
                return "designer"
            raise HTTPException(status_code=403, detail="This action needs the admin token; the designer token cannot change data sources, delete items or read logs")
        raise HTTPException(status_code=401, detail="The admin token is missing or wrong")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            get_connection("default")
            LOG.info("Default MongoDB connection ready")
        except HTTPException as exc:
            LOG.warning("Default MongoDB connection unavailable: %s", exc.detail)
        yield
        for client in state["clients"].values():
            client.close()

    areas = enabled_areas()
    docs_on = "docs" in areas
    ui_directory = PROJECT_ROOT / "ui"

    def react_page(name: str) -> HTMLResponse:
        """/designer, /server and /admin are pages of one React build (frontend/ -> ui/app)."""
        page = ui_directory / "app" / name
        if not page.exists():
            raise HTTPException(status_code=404, detail="The web app is not built. Run `npm install && npm run build` in frontend/")
        return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

    def admin_report_payload(definition: ReportDefinition) -> dict[str, Any]:
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

    def save_report_payload(payload: Any, expected_id: str | None = None) -> ReportDefinition:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="The report definition must be a JSON object")
        try:
            definition = _dynamic_report(payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if expected_id and definition.report_id != expected_id:
            raise HTTPException(status_code=400, detail="The report ID in the URL and in the definition must match")
        existing = reports_by_id.get(definition.report_id)
        if existing is not None and expected_id is None:
            raise HTTPException(status_code=409, detail="A report with this ID already exists")
        profile_ids = {item["id"] for item in load_connection_profiles()}
        if definition.connection_id not in profile_ids:
            raise HTTPException(status_code=422, detail=f"Unknown connectionId: {definition.connection_id}")
        path = report_definition_path(definition.report_id, definition.editor_config["folder"])
        _write_json_atomically(path, definition.editor_config or payload)
        if existing and existing.editor_config:
            previous_path = report_definition_path(
                existing.report_id,
                normalize_folder_path(existing.editor_config.get("folder", "General")),
            )
            if previous_path != path and previous_path.exists():
                previous_path.unlink()
        reports_by_id[definition.report_id] = _dynamic_report(definition.editor_config or payload, path)
        return reports_by_id[definition.report_id]

    def folder_contents(folder: str) -> dict[str, list[str]]:
        """Subfolders, published reports and data adapters inside a repository folder (at any depth)."""
        def inside(path: str) -> bool:
            return path == folder or path.startswith(f"{folder}/")
        return {
            "subfolders": [item for item in list_saved_folders() if item.startswith(f"{folder}/")],
            "reports": sorted(item.report_id for item in reports_by_id.values()
                              if item.editor_config and inside(str(item.editor_config.get("folder", "General")))),
            "adapters": [item["name"] for item in load_connection_profiles() if inside(str(item.get("folder") or ""))],
        }

    def delete_repository_folder(folder: str, recursive: bool = False, user: str = "") -> dict[str, Any]:
        """Delete a folder. recursive=True (after the user confirmed it) also deletes its subfolders and the
        published reports in them, like JasperReports Server; data adapters must still be moved first."""
        if folder == "General":
            raise HTTPException(status_code=400, detail="The default General folder cannot be deleted")
        saved_folders = list_saved_folders()
        if folder not in saved_folders:
            raise HTTPException(status_code=404, detail="Folder not found")
        contents = folder_contents(folder)
        if contents["adapters"]:
            raise HTTPException(status_code=409, detail=f"Move or delete the data adapters in this folder first: {', '.join(contents['adapters'])}")
        if not recursive and (contents["subfolders"] or contents["reports"]):
            parts = []
            if contents["subfolders"]:
                parts.append(f"{len(contents['subfolders'])} subfolder(s)")
            if contents["reports"]:
                parts.append(f"{len(contents['reports'])} report(s): {', '.join(contents['reports'][:5])}")
            raise HTTPException(status_code=409, detail=f"The folder is not empty ({' and '.join(parts)}); confirm to delete it with everything inside")
        for report_id in contents["reports"]:
            definition = reports_by_id[report_id]
            path = report_definition_path(report_id, normalize_folder_path(definition.editor_config.get("folder", "General")))
            if path.exists():
                path.unlink()
            record_history(report_id, "deleted", user or "admin", f"with the folder {folder}")
        root = report_definitions_directory().resolve()
        directory = report_definition_path(".folder", folder).parent.resolve()
        if directory.exists() and directory != root and root in directory.parents:
            leftovers = [p for p in directory.rglob("*") if p.is_file()]
            if leftovers and not recursive:
                raise HTTPException(status_code=409, detail="The folder still has files in it; confirm to delete it with everything inside")
            shutil.rmtree(directory)
        removed = {folder, *contents["subfolders"]}
        _write_json_atomically(folders_file(), {"folders": [item for item in saved_folders if item not in removed]})
        if contents["reports"]:
            refreshed = discover_reports()
            reports_by_id.clear()
            reports_by_id.update(refreshed)
        LOG.info("Repository folder %s deleted (%d subfolders, %d reports) by %s", folder, len(contents["subfolders"]), len(contents["reports"]), user or "admin")
        return {"deleted": folder, "folders": list_saved_folders(), "subfoldersDeleted": len(contents["subfolders"]),
                "reportsDeleted": contents["reports"]}

    def delete_published_report(report_id: str) -> dict[str, str]:
        if not REPORT_ID_PATTERN.fullmatch(report_id):
            raise HTTPException(status_code=400, detail="Invalid report ID format")
        definition = reports_by_id.get(report_id)
        if definition is None or definition.editor_config is None:
            raise HTTPException(status_code=404, detail="Saved report definition not found")
        path = report_definition_path(
            report_id,
            normalize_folder_path(definition.editor_config.get("folder", "General")),
        )
        if not path.exists():
            raise HTTPException(status_code=404, detail="Saved report definition not found")
        path.unlink()
        refreshed = discover_reports()
        reports_by_id.clear()
        reports_by_id.update(refreshed)
        return {"status": "deleted", "reportId": report_id}

    def draft_definition(payload: Any) -> ReportDefinition:
        """A designer draft (not published) turned into a runnable definition, in memory only."""
        raw = payload.get("definition") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="Send the draft report as 'definition'")
        try:
            return _dynamic_report(strip_workspace_meta(raw))
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=f"The draft is not complete yet: {exc}") from None

    def preview_definition(definition: ReportDefinition, payload: Any) -> dict[str, Any]:
        connection_id = definition.connection_id
        try:
            if not isinstance(payload, dict):
                raise ValueError("The request must be a JSON object")
            connection_id = chosen_connection(payload, definition)
            parameters = admin_parameters(payload, definition)
            rows = run_report_rows(definition, parameters, connection_id, max_rows=min(settings.max_rows, 20), limit_rows=True)
            safe_rows = [{key: _safe_value(_row_value(row, key)) for key in definition.columns} for row in rows]
            layout = (definition.editor_config or {}).get("layout", {})
            return {"columns": list(definition.columns), "labels": layout.get("columnLabels", {}), "layout": layout,
                    "rows": safe_rows, "rowCount": len(safe_rows), "connectionId": connection_id}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except MongoReadAccessError as exc:
            LOG.warning("Admin preview cannot read report collections with the configured MongoDB identity")
            raise HTTPException(status_code=503, detail=access_error_detail(exc, connection_id)) from None
        except PyMongoError:
            LOG.exception("Admin report preview failed")
            raise HTTPException(status_code=503, detail="The MongoDB preview query failed; see the service log (Logs tab)") from None

    def run_definition_file(definition: ReportDefinition, payload: Any, path: str, access: str, *,
                            fixed_connection: bool = False, user: str = "") -> Response:
        """Run a report in full and return the file. fixed_connection=True (the /server portal)
        always uses the report's own data adapter and only its enabled formats."""
        started = time.monotonic()
        audit: dict[str, Any] = {"requestedAt": datetime.now(timezone.utc), "path": path, "reportId": definition.report_id,
                                 "status": "failed", "rowCount": 0, "access": access}
        if user:
            audit["GeneratedBy"] = user
        connection_id = definition.connection_id
        try:
            if not isinstance(payload, dict):
                raise ValueError("The request must be a JSON object")
            output_format = str(payload.get("format", "pdf")).lower()
            if output_format not in OUTPUT_FORMATS:
                raise ValueError("format must be pdf, xlsx or csv")
            if fixed_connection:
                enabled_formats = (definition.editor_config or {}).get("formats", sorted(OUTPUT_FORMATS))
                if output_format not in enabled_formats:
                    raise ValueError(f"The {output_format.upper()} format is not enabled for this report")
            else:
                connection_id = chosen_connection(payload, definition)
            parameters = admin_parameters(payload, definition)
            audit.update({"format": output_format, "connectionId": connection_id,
                          "parameters": {k: v.isoformat() if isinstance(v, date) else v for k, v in parameters.items()}})
            rows = run_report_rows(definition, parameters, connection_id, max_rows=settings.max_rows, limit_rows=False)
            rows = [{key: _row_value(row, key) for key in definition.columns} for row in rows]
            body, media_type = render_report_output(definition, parameters, rows, output_format, subreport_loader())
            audit.update({"status": "success", "rowCount": len(rows)})
            return Response(body, media_type=media_type,
                            headers={"Content-Disposition": f'attachment; filename="{definition.report_id}.{output_format}"'})
        except HTTPException as exc:
            audit["errorCode"] = str(exc.status_code)
            raise
        except ValueError as exc:
            audit["errorCode"] = "validation_error"
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except MongoReadAccessError as exc:
            audit["errorCode"] = "mongodb_read_access_unavailable"
            raise HTTPException(status_code=503, detail=access_error_detail(exc, connection_id)) from None
        except PyMongoError:
            audit["errorCode"] = "mongodb_error"
            LOG.exception("Admin report run failed")
            raise HTTPException(status_code=503, detail="The MongoDB report query failed; see the service log (Logs tab)") from None
        finally:
            audit["durationMs"] = int((time.monotonic() - started) * 1000)
            try:
                audit_store._append_report_audit(audit)
            except OSError:
                LOG.exception("Admin run audit record could not be saved")

    # ------------------------------------------------------------ designer workspace (internal drafts)
    # The designer saves here. Only "Publish to server" copies a report into the server repository
    # (data/reports), which is what /server, /ui, /reports and /generate serve.
    def now_iso() -> str:
        # microseconds: "changed since publish" compares these strings, and a save can follow a publish within a second
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def strip_workspace_meta(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if key not in (WORKSPACE_META, "workspace", "isNew")}

    def workspace_file(report_id: str, folder: str) -> Path:
        return workspace_directory().joinpath(*folder.split("/"), f"{report_id}.json")

    def ensure_workspace() -> None:
        """First use: start the workspace with a copy of what is already on the server."""
        root = workspace_directory()
        if root.exists():
            return
        root.mkdir(parents=True, exist_ok=True)
        stamp = now_iso()
        for definition in reports_by_id.values():
            if definition.editor_config is None:
                continue
            payload = copy.deepcopy(definition.editor_config)
            try:
                folder = normalize_folder_path(payload.get("folder", "General"))
            except ValueError:
                folder = "General"
            payload["folder"] = folder
            payload[WORKSPACE_META] = {"createdAt": stamp, "updatedAt": stamp, "publishedAt": stamp, "publishedFolder": folder}
            _write_json_atomically(workspace_file(definition.report_id, folder), payload)

    def workspace_entries() -> list[tuple[Path, dict[str, Any]]]:
        ensure_workspace()
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

    def find_workspace_entry(report_id: str) -> tuple[Path, dict[str, Any]] | None:
        return next((item for item in workspace_entries() if item[1]["id"] == report_id), None)

    def workspace_summary(data: dict[str, Any]) -> dict[str, Any]:
        meta = data.get(WORKSPACE_META) or {}
        published_at = meta.get("publishedAt") or ""
        on_server = data["id"] in reports_by_id
        return {
            "id": data["id"], "title": data.get("title") or data["id"], "description": data.get("description", ""),
            "folder": data.get("folder", "General"), "updatedAt": meta.get("updatedAt", ""),
            "publishedAt": published_at if on_server else "", "publishedFolder": meta.get("publishedFolder", "") if on_server else "",
            "onServer": on_server,
            "changedSincePublish": bool(on_server and published_at and meta.get("updatedAt", "") > published_at),
        }

    def workspace_folders(entries: list[tuple[Path, dict[str, Any]]]) -> list[str]:
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

    async def json_body(request: Request, *, optional: bool = False) -> dict[str, Any]:
        try:
            raw = await request.body()
            payload = json.loads(raw) if raw.strip() else ({} if optional else None)
        except ValueError:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Send a JSON object")
        return payload

    # ------------------------------------------------------------ /server: repository portal with login
    def require_json_request(request: Request) -> None:
        """Cookie-authenticated writes must be JSON: a cross-site HTML form cannot send that."""
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            raise HTTPException(status_code=415, detail="Send the request as application/json")

    def current_session(request: Request) -> dict[str, Any] | None:
        body = read_session_cookie(request.cookies.get(SESSION_COOKIE, ""))
        if body is None or body.get("n") in state["revoked_sessions"]:
            return None
        if body.get("b"):
            if not bootstrap_login_active():
                return None
            return {"username": BOOTSTRAP_USERNAME, "role": "admin", "bootstrap": True, "nonce": body.get("n")}
        user = find_user(str(body.get("u", "")))
        if user is None or user.get("disabled") or int(user.get("sessionVersion", 0)) != int(body.get("v", -1)):
            return None
        return {"username": user["username"], "role": user.get("role", "viewer"), "bootstrap": False, "nonce": body.get("n")}

    def require_session(request: Request, minimum: str = "viewer") -> dict[str, Any]:
        session = current_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Sign in to the report server first")
        if not role_at_least(session["role"], minimum):
            raise HTTPException(status_code=403, detail=f"This needs a {minimum} account; you are signed in as {session['role']}")
        if request.method not in ("GET", "HEAD"):
            require_json_request(request)
        return session

    def session_view(session: dict[str, Any]) -> dict[str, Any]:
        return {"username": session["username"], "role": session["role"], "bootstrap": session["bootstrap"]}

    # ------------------------------------------------------------ /server: resources (like JasperReports Server)
    def history_file(report_id: str) -> Path:
        return data_directory() / "history" / f"{report_id}.json"

    def read_history(report_id: str) -> list[dict[str, Any]]:
        path = history_file(report_id)
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (OSError, ValueError):
            return []
        return items if isinstance(items, list) else []

    def record_history(report_id: str, action: str, user: str, detail: str = "") -> None:
        """What happened to a server resource (published, edited, copied, moved, uploaded, deleted); last 200 entries."""
        if not REPORT_ID_PATTERN.fullmatch(report_id):
            return
        items = read_history(report_id)
        items.append({"at": now_iso(), "action": action, "user": user, "detail": detail[:300]})
        try:
            _write_json_atomically(history_file(report_id), items[-200:])
        except OSError:
            LOG.warning("The history of %s could not be written", report_id)

    def published_or_404(report_id: str) -> ReportDefinition:
        definition = reports_by_id.get(report_id)
        if definition is None or definition.editor_config is None:
            raise HTTPException(status_code=404, detail="This report is not published on the server")
        return definition

    def design_summary(design: Any) -> dict[str, Any]:
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

    def connection_summary(connection_id: str) -> dict[str, Any]:
        profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
        if profile is None:
            return {"id": connection_id, "name": connection_id, "missing": True}
        return {"id": profile["id"], "name": profile["name"], "folder": profile.get("folder", ""), "storage": profile.get("storage", "env"),
                "database": profile.get("database", "") if profile.get("storage") == "file" else f"from ${profile.get('databaseEnv', '')}"}

    def resource_details(definition: ReportDefinition, session: dict[str, Any]) -> dict[str, Any]:
        config = definition.editor_config or {}
        designer = role_at_least(session["role"], "designer")
        try:
            path = report_definition_path(definition.report_id, normalize_folder_path(config.get("folder", "General")))
            stat = path.stat() if path.exists() else None
        except (ValueError, OSError):
            stat = None
        used_by = sorted(item.report_id for item in reports_by_id.values() if item.report_id != definition.report_id
                         and definition.report_id in design_summary((item.editor_config or {}).get("design")).get("subreports", []))
        return {
            "id": definition.report_id, "title": definition.title, "description": config.get("description", ""),
            "folder": config.get("folder", "General"), "formats": config.get("formats", sorted(OUTPUT_FORMATS)),
            "defaultFormat": config.get("defaultFormat", "pdf"), "connection": connection_summary(definition.connection_id),
            "collection": config.get("collection", definition.collection), "columns": list(definition.columns),
            "parameters": [{"name": p.get("name"), "type": p.get("type", "string"), "label": p.get("label", p.get("name")),
                            "description": p.get("description", ""), "required": bool(p.get("required")),
                            "default": p.get("default"), "allowedValues": p.get("allowedValues") or [],
                            "computed": p.get("prompt") is False, "expression": p.get("expression", "")}
                           for p in config.get("parameters", []) if isinstance(p, dict)],
            # the query can reveal data structure: only designers and admins see it
            "queryLanguage": config.get("queryLanguage", "mongosh") if designer else None,
            "querySource": config.get("querySource", "") if designer else None,
            "design": design_summary(config.get("design")),
            "file": {"name": f"{definition.report_id}.mrd", "size": stat.st_size if stat else None,
                     "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else ""},
            "usedBy": used_by,
            "history": list(reversed(read_history(definition.report_id)))[:50],
            "canEdit": designer, "canDelete": role_at_least(session["role"], "admin"),
        }

    def sync_workspace_copy(report_id: str, update: Any) -> None:
        """A change made on the server is also made in the designer's workspace copy, so the next publish keeps it."""
        entry = find_workspace_entry(report_id)
        if entry is None:
            return
        path, data = entry
        update(data)
        meta = dict(data.get(WORKSPACE_META) or {})
        stamp = now_iso()
        meta.update({"updatedAt": stamp, "publishedAt": stamp, "publishedFolder": data.get("folder", "General")})
        data[WORKSPACE_META] = meta
        _write_json_atomically(path, data)

    def free_report_id(base: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_-]", "_", base)[:70] or "REPORT"
        candidate, number = f"{base}_COPY", 2
        while candidate in reports_by_id:
            candidate, number = f"{base}_COPY{number}", number + 1
        return candidate

    # ------------------------------------------------------------ /admin: server user accounts
    def user_view(user: dict[str, Any]) -> dict[str, Any]:
        return {"username": user["username"], "role": user.get("role", "viewer"), "disabled": bool(user.get("disabled")),
                "createdAt": user.get("createdAt", ""), "lastLoginAt": user.get("lastLoginAt", "")}

    def valid_role(value: Any) -> str:
        role = str(value or "").strip().lower()
        if role not in USER_ROLES:
            raise HTTPException(status_code=422, detail="role must be viewer, designer or admin")
        return role

    def valid_password(value: Any) -> str:
        password = str(value or "")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(status_code=422, detail=f"The password needs at least {MIN_PASSWORD_LENGTH} characters")
        return password

    def admins_left(users: list[dict[str, Any]]) -> int:
        return sum(1 for item in users if item.get("role") == "admin" and not item.get("disabled"))

    def published_report_summaries() -> list[dict[str, Any]]:
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
        } for item in reports_by_id.values()]
    return SimpleNamespace(**{name: value for name, value in locals().items() if not name.startswith('__')})
