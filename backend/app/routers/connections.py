"""Data sources (data adapters): list, save, test, move, delete.

A data source belongs to one part (area: designer, server, analytics, query) and works only there; a user
with that part's right connects and changes its data sources, the Admin right covers all of them. /api/admin/connections is the older address of the same endpoints."""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.connectors import sql as sql_connector
from backend.app.connectors.mongodb import _mongo_database_from_uri
from backend.app.connectors.profiles import AREA_NAMES, AREAS, DEFAULT_REMOVED, _profile_credentials, load_connection_profiles, usable
from backend.app.constants import ADAPTER_TYPES, CONNECTION_ID_PATTERN, ENV_KEY_PATTERN
from backend.app.security.secrets import _load_private_secrets, _save_private_secrets
from backend.app.storage.files import add_saved_folder, normalize_folder_path
from backend.app.storage.store import get_store


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the connections endpoints to the app."""
    require_admin = ctx.require_admin
    state = ctx.state
    test_adapter_values = ctx.test_adapter_values

    def area_of(value: Any) -> str:
        area = str(value or "").strip().lower()
        if area and area not in AREAS:
            raise HTTPException(status_code=422, detail=f"area must be one of {', '.join(AREAS)}")
        return area

    def managed(request: Request, connection_id: str) -> dict[str, Any]:
        """The data source this request changes, if the caller may (its part's right, or Admin)."""
        profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"Data adapter not found: {connection_id}")
        ctx.may_use_area(request, profile.get("area", ""))
        return profile

    def is_admin(request: Request) -> bool:
        return ctx.managed_areas(request) == set(AREAS)

    @app.get("/api/data-sources")
    @app.get("/api/admin/connections")
    def admin_list_connections(request: Request) -> dict[str, Any]:
        require_admin(request, "sources")
        area, mine = area_of(request.query_params.get("area")), ctx.managed_areas(request)
        ctx.may_use_area(request, area)
        profiles = [item for item in load_connection_profiles()
                    if (usable(item, area) if area else item.get("area", "") in {"", *mine})]
        uses = ctx.adapter_uses()
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
                "area": profile.get("area", ""),
                "usedBy": uses.get(profile["id"], {}).get("server", []),
                "uses": {key: value for key, value in uses.get(profile["id"], {}).items() if key in mine},
            })
        return {"connections": result}

    @app.post("/api/data-sources")
    @app.post("/api/admin/connections")
    async def admin_save_connection(request: Request) -> dict[str, Any]:
        require_admin(request, "sources")
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
        area, create = area_of(payload.get("area")), payload.get("create") is True
        ctx.may_use_area(request, area)
        current = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
        if create and current is not None:
            if current.get("area", "") == area:
                raise HTTPException(status_code=409, detail=f"There is already a data source with the ID {connection_id}; choose another name")
            connection_id, current = ctx.free_adapter_id(f"{connection_id[:30]}-{area}" if area else connection_id), None
        if current is None:
            if not area and not is_admin(request):
                raise HTTPException(status_code=422, detail=f"Say where the data source is for (area: {', '.join(AREAS)})")
        else:
            ctx.may_use_area(request, current.get("area", ""))
            if area and current.get("area", "") != area:
                if not current.get("area"):
                    raise HTTPException(status_code=409, detail=f"“{current['name']}” is used by every part; an administrator can give it one part")
                raise HTTPException(status_code=409, detail=f"The ID {connection_id} is a data source of {AREA_NAMES[current['area']]}; choose another name")
            area = current.get("area", "")
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
        if area:
            profile["area"] = area
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
        return {"connection": {"id": connection_id, "name": name, "type": profile.get("type", "mongodb"), "folder": adapter_folder, "area": area,
                               "database": database, "configured": True}}

    @app.post("/api/data-sources/test")
    @app.post("/api/admin/connections/test")
    async def admin_test_connection_values(request: Request) -> dict[str, Any]:
        require_admin(request, "sources")
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

    @app.delete("/api/data-sources/{connection_id}")
    @app.delete("/api/admin/connections/{connection_id}")
    def admin_delete_connection(connection_id: str, request: Request) -> dict[str, str]:
        require_admin(request, "sources")
        managed(request, connection_id)
        uses = ctx.adapter_uses().get(connection_id, {})
        used_by = uses.get("server", [])
        others = [f"{AREA_NAMES[key]}: {', '.join(names[:5])}" for key, names in uses.items() if key != "server"]
        if others and not used_by:
            raise HTTPException(status_code=409, detail=f"This data source is still used ({'; '.join(others)}); choose another one there first")
        if used_by:
            raise HTTPException(status_code=409, detail=f"Switch these reports (or their datasets) to another data adapter first: {', '.join(used_by)}")
        profiles = [item for item in load_connection_profiles() if item["id"] != connection_id]
        if connection_id == "default":             # MONGODB_URI in the environment must not bring it back
            get_store().write_json(DEFAULT_REMOVED, {"removed": True})
        get_store().write_json("connections.json", {"connections": profiles})
        private = _load_private_secrets()
        private["mongo_connections"].pop(connection_id, None)
        _save_private_secrets(private)
        old_client = state["clients"].pop(connection_id, None)
        state["connection_signatures"].pop(connection_id, None)
        if old_client is not None:
            old_client.close()
        return {"status": "deleted", "connectionId": connection_id}

    @app.put("/api/data-sources/{connection_id}/folder")
    @app.put("/api/admin/connections/{connection_id}/folder")
    async def admin_move_connection(connection_id: str, request: Request) -> dict[str, Any]:
        """Move a data adapter into a repository folder (or back to the top level) without re-entering its URL."""
        require_admin(request, "sources")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        raw_folder = str((payload or {}).get("folder") or "").strip() if isinstance(payload, dict) else ""
        try:
            folder = normalize_folder_path(raw_folder) if raw_folder else ""
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        managed(request, connection_id)
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

    @app.post("/api/data-sources/{connection_id}/test")
    @app.post("/api/admin/connections/{connection_id}/test")
    def admin_test_connection(connection_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "sources")
        managed(request, connection_id)
        return ctx.test_adapter_by_id(connection_id)

    # ------------------------------------------------------------- one part each
    @app.put("/api/data-sources/{connection_id}/area")
    async def move_to_area(connection_id: str, request: Request) -> dict[str, Any]:
        """Give a data source to one part (administrators). Not while another part still uses it."""
        require_admin(request, "admin")
        area = area_of((await ctx.json_body(request)).get("area"))
        if not area:
            raise HTTPException(status_code=422, detail=f"area must be one of {', '.join(AREAS)}")
        profile = managed(request, connection_id)
        elsewhere = {key: names for key, names in ctx.adapter_uses().get(connection_id, {}).items() if key != area}
        if elsewhere:
            where = "; ".join(f"{AREA_NAMES[key]}: {', '.join(names[:5])}" for key, names in elsewhere.items())
            raise HTTPException(status_code=409, detail=f"“{profile['name']}” is still used in {where}. Split it into one per part instead")
        profiles = load_connection_profiles()
        for item in profiles:
            if item["id"] == connection_id:
                item["area"] = area
        get_store().write_json("connections.json", {"connections": profiles})
        ctx.audit_event(request, "data_source.area", connection_id, {"area": area})
        return {"connectionId": connection_id, "area": area}

    @app.post("/api/data-sources/{connection_id}/split")
    def split(connection_id: str, request: Request) -> dict[str, Any]:
        """One data source that every part uses becomes a separate one per part (administrators)."""
        require_admin(request, "admin")
        managed(request, connection_id)
        ids = ctx.split_adapter(connection_id)
        ctx.audit_event(request, "data_source.split", connection_id, {"ids": ids})
        return {"connectionId": connection_id, "ids": ids}

    def may_take(request: Request, connection_id: str, area: str) -> dict[str, Any]:
        """Taking work from one part to another needs both rights; publishing (Designer to Server) only the Designer's."""
        if not area:
            raise HTTPException(status_code=422, detail=f"area must be one of {', '.join(AREAS)}")
        profile = managed(request, connection_id)
        if not (area == "server" and "designer" in ctx.managed_areas(request)):
            ctx.may_use_area(request, area)
        return profile

    @app.get("/api/data-sources/{connection_id}/targets")
    def targets(connection_id: str, request: Request, area: str = "") -> dict[str, Any]:
        """What can stand in for this data source in another part: the suggested one and all of the same kind."""
        require_admin(request, "sources")
        profile = may_take(request, connection_id, area_of(area))
        kind = profile.get("type", "mongodb")
        found = [{"id": item["id"], "name": item["name"], "type": kind, "folder": item.get("folder", ""), "database": _profile_credentials(item)[1]}
                 for item in load_connection_profiles() if usable(item, area_of(area)) and item.get("type", "mongodb") == kind]
        return {"id": connection_id, "name": profile["name"], "type": kind, "suggested": ctx.counterpart(connection_id, area_of(area)), "targets": found}

    @app.post("/api/data-sources/{connection_id}/copy")
    async def copy_to_area(connection_id: str, request: Request) -> dict[str, Any]:
        """Connect the same database in another part: a separate data source with its own name and password."""
        require_admin(request, "sources")
        payload = await ctx.json_body(request)
        area = area_of(payload.get("area"))
        profile = may_take(request, connection_id, area)
        new_id = ctx.copy_adapter(connection_id, area, str(payload.get("name") or ""))
        ctx.audit_event(request, "data_source.created", new_id, {"area": area, "from": connection_id})
        return {"connection": {"id": new_id, "name": str(payload.get("name") or profile["name"]), "type": profile.get("type", "mongodb"), "area": area}}

    @app.post("/api/data-sources/{connection_id}/use-in")
    async def use_in_area(connection_id: str, request: Request) -> dict[str, Any]:
        """Remember which data source stands in for this one in another part."""
        require_admin(request, "sources")
        payload = await ctx.json_body(request)
        area, other = area_of(payload.get("area")), str(payload.get("connectionId") or "")
        profile = may_take(request, connection_id, area)
        target = next((item for item in load_connection_profiles() if item["id"] == other and usable(item, area)), None)
        if target is None or target.get("type", "mongodb") != profile.get("type", "mongodb"):
            raise HTTPException(status_code=422, detail="Choose a data source of that part and of the same kind")
        ctx.remember_counterpart(connection_id, area, other)
        return {"connectionId": connection_id, "area": area, "use": other}
