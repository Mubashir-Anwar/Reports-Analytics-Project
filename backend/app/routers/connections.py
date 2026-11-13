"""Data adapters: list, save, test, move, delete."""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.connectors import sql as sql_connector
from backend.app.connectors.mongodb import _mongo_database_from_uri
from backend.app.connectors.profiles import _profile_credentials, load_connection_profiles
from backend.app.constants import ADAPTER_TYPES, CONNECTION_ID_PATTERN, ENV_KEY_PATTERN
from backend.app.security.secrets import _load_private_secrets, _save_private_secrets
from backend.app.storage.files import add_saved_folder, normalize_folder_path
from backend.app.storage.store import get_store


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the connections endpoints to the app."""
    reports_by_id = ctx.reports_by_id
    require_admin = ctx.require_admin
    state = ctx.state
    test_adapter_values = ctx.test_adapter_values
    test_mongo_connection = ctx.test_mongo_connection
    test_saved_adapter = ctx.test_saved_adapter

    @app.get("/api/admin/connections")
    def admin_list_connections(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        profiles = load_connection_profiles()
        result = []
        for profile in profiles:
            uri, database = _profile_credentials(profile)
            result.append({
                "id": profile["id"],
                "name": profile["name"],
                "type": profile.get("type", "mongodb"),
                "folder": profile.get("folder", ""),
                "database": database,
                "credentialSource": "Private local file" if profile.get("storage") == "file" else "Environment settings",
                "configured": bool(uri and database),
            })
        return {"connections": result}

    @app.post("/api/admin/connections")
    async def admin_save_connection(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="The connection profile must be a JSON object")
        name = str(payload.get("name", "")).strip()
        uri = str(payload.get("uri", "")).strip()
        database = str(payload.get("database", "")).strip()
        connection_id = str(payload.get("id", "")).strip().lower()
        kind = str(payload.get("type", "mongodb") or "mongodb").strip().lower()
        if kind not in ADAPTER_TYPES:
            raise HTTPException(status_code=422, detail=f"Unknown data adapter type: {kind}")
        if not name:
            raise HTTPException(status_code=422, detail="The data source name is required")
        if not connection_id:
            connection_id = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_-")[:40]
            if not connection_id or not connection_id[0].isalnum():
                connection_id = f"mongo_{connection_id}"[:40]
        if not CONNECTION_ID_PATTERN.fullmatch(connection_id):
            raise HTTPException(status_code=422, detail="No valid ID could be made from the data source name; use letters and numbers")
        # Optional repository folder (like a JasperReports Server "Data Source" folder); empty = top level.
        if "folder" in payload:
            raw_folder = str(payload.get("folder") or "").strip()
        else:  # an older client that does not know folders: keep the adapter where it is
            raw_folder = next((item.get("folder", "") for item in load_connection_profiles() if item["id"] == connection_id), "")
        try:
            adapter_folder = normalize_folder_path(raw_folder) if raw_folder else ""
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        # Backward compatibility for older admin pages that stored environment key names.
        if not uri and payload.get("uriEnv") and payload.get("databaseEnv"):
            legacy_profile = {
                "id": connection_id,
                "name": name,
                "storage": "env",
                "uriEnv": str(payload.get("uriEnv", "")).strip(),
                "databaseEnv": str(payload.get("databaseEnv", "")).strip(),
            }
            if not ENV_KEY_PATTERN.fullmatch(legacy_profile["uriEnv"]) or not ENV_KEY_PATTERN.fullmatch(legacy_profile["databaseEnv"]):
                raise HTTPException(status_code=422, detail="Give valid environment variable names")
            profile = legacy_profile
        elif kind in sql_connector.SQL_TYPES:
            if not sql_connector.valid_url(kind, uri):
                raise HTTPException(status_code=422, detail=sql_connector.url_hint(kind))
            database = sql_connector.database_from_url(uri)
            if not database:
                raise HTTPException(status_code=422, detail=f"The URL has no database name. {sql_connector.url_hint(kind)}")
            profile = {"id": connection_id, "name": name, "type": kind, "storage": "file", "database": database}
        else:
            if not uri.startswith(("mongodb://", "mongodb+srv://")):
                raise HTTPException(status_code=422, detail="Give a MongoDB URL that starts with mongodb:// or mongodb+srv://")
            database = database or _mongo_database_from_uri(uri)
            if not database:
                raise HTTPException(status_code=422, detail="The URL has no database name; enter the database name")
            profile = {"id": connection_id, "name": name, "type": "mongodb", "storage": "file", "database": database}
        if adapter_folder:
            profile["folder"] = adapter_folder
            add_saved_folder(adapter_folder)

        profiles = load_connection_profiles()
        replaced = False
        updated = []
        for item in profiles:
            if item["id"] == connection_id:
                updated.append(profile)
                replaced = True
            else:
                updated.append(item)
        if not replaced:
            updated.append(profile)

        if profile.get("storage") == "file":
            private = _load_private_secrets()
            private["mongo_connections"][connection_id] = {"uri": uri, "database": database}
            _save_private_secrets(private)
        else:
            private = _load_private_secrets()
            private["mongo_connections"].pop(connection_id, None)
            _save_private_secrets(private)
        get_store().write_json("connections.json", {"connections": updated})
        old_client = state["clients"].pop(connection_id, None)
        state["connection_signatures"].pop(connection_id, None)
        if old_client is not None:
            old_client.close()
        return {"connection": {"id": connection_id, "name": name, "type": profile.get("type", "mongodb"), "folder": adapter_folder,
                               "database": database, "configured": True}}

    @app.post("/api/admin/connections/test")
    async def admin_test_connection_values(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Send a JSON object for the connection test")
        uri = str(payload.get("uri", "")).strip()
        database = str(payload.get("database", "")).strip()
        connection_id = str(payload.get("id", "")).strip().lower()
        return test_adapter_values(str(payload.get("type", "mongodb") or "mongodb").lower(), uri, database, connection_id)

    @app.delete("/api/admin/connections/{connection_id}")
    def admin_delete_connection(connection_id: str, request: Request) -> dict[str, str]:
        require_admin(request)
        if connection_id == "default":
            raise HTTPException(status_code=400, detail="The default connection cannot be deleted")
        used_by = [item.report_id for item in reports_by_id.values() if item.connection_id == connection_id]
        if used_by:
            raise HTTPException(status_code=409, detail=f"Switch these reports to another connection first: {', '.join(used_by)}")
        profiles = [item for item in load_connection_profiles() if item["id"] != connection_id]
        get_store().write_json("connections.json", {"connections": profiles})
        private = _load_private_secrets()
        private["mongo_connections"].pop(connection_id, None)
        _save_private_secrets(private)
        old_client = state["clients"].pop(connection_id, None)
        state["connection_signatures"].pop(connection_id, None)
        if old_client is not None:
            old_client.close()
        return {"status": "deleted", "connectionId": connection_id}

    @app.put("/api/admin/connections/{connection_id}/folder")
    async def admin_move_connection(connection_id: str, request: Request) -> dict[str, Any]:
        """Move a data adapter into a repository folder (or back to the top level) without re-entering its URL."""
        require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        raw_folder = str((payload or {}).get("folder") or "").strip() if isinstance(payload, dict) else ""
        try:
            folder = normalize_folder_path(raw_folder) if raw_folder else ""
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        profiles = load_connection_profiles()
        target = next((item for item in profiles if item["id"] == connection_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"Data adapter not found: {connection_id}")
        if folder:
            target["folder"] = folder
            add_saved_folder(folder)
        else:
            target.pop("folder", None)
        get_store().write_json("connections.json", {"connections": profiles})
        return {"connectionId": connection_id, "folder": folder}

    @app.post("/api/admin/connections/{connection_id}/test")
    def admin_test_connection(connection_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
        if profile is None:
            raise HTTPException(status_code=404, detail="Connection ID is not registered")
        sql_result = test_saved_adapter(connection_id)  # SQL adapters; None for MongoDB
        if sql_result is not None:
            return sql_result
        uri, database = _profile_credentials(profile)
        if not uri or not database:
            if profile.get("storage") == "file":
                detail = "This data source has no saved MongoDB URL or database name"
            else:
                detail = f"Set {profile['uriEnv']} and {profile['databaseEnv']} in .env or the server environment"
            raise HTTPException(status_code=422, detail=detail)
        return test_mongo_connection(uri, database, connection_id)
