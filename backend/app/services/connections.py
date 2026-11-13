"""Database connections: cached clients, connection tests, the adapter of a run, error text.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from backend.app.connectors import mongodb, sql as sql_connector
from backend.app.connectors.errors import ConnectorQueryError
from backend.app.connectors.mongodb import _mongo_database_from_uri, _safe_mongo_error
from backend.app.connectors.profiles import _profile_credentials, load_connection_profiles
from backend.app.definitions import ReportDefinition
from backend.app.logs import LOG

__all__ = ['get_connection', 'test_mongo_connection', 'chosen_connection', 'access_error_detail', 'connection_summary',
           'adapter_kind', 'sql_adapter', 'test_adapter_values', 'test_saved_adapter']


def get_connection(ctx: SimpleNamespace, connection_id: str) -> tuple[MongoClient, str]:
    profiles = {item["id"]: item for item in load_connection_profiles()}
    profile = profiles.get(connection_id)
    if profile is None:
        raise HTTPException(status_code=400, detail=f"MongoDB connection ID is not registered: {connection_id}")
    if profile.get("type", "mongodb") != "mongodb":
        raise HTTPException(status_code=422, detail=f"Data adapter '{connection_id}' is a {profile['type']} database, not MongoDB; "
                                                    "this report needs a MongoDB adapter")
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
    client = ctx.state["clients"].get(connection_id)
    if client is not None and ctx.state["connection_signatures"].get(connection_id) != signature:
        client.close()
        ctx.state["clients"].pop(connection_id, None)
        ctx.state["connection_signatures"].pop(connection_id, None)
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
        ctx.state["clients"][connection_id] = client
        ctx.state["connection_signatures"][connection_id] = signature
    return client, database


def test_mongo_connection(ctx: SimpleNamespace, uri: str, database: str, connection_id: str) -> dict[str, Any]:
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

    for definition in ctx.reports_by_id.values():
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


def chosen_connection(ctx: SimpleNamespace, payload: dict[str, Any], definition: ReportDefinition) -> str:
    """Data adapter for an admin preview/run: the one picked in the UI, else the report's own."""
    requested = str(payload.get("connectionId") or "").strip()
    if not requested:
        return definition.connection_id
    if requested not in {item["id"] for item in load_connection_profiles()}:
        raise ValueError(f"Data adapter is not registered: {requested}")
    return requested


def access_error_detail(ctx: SimpleNamespace, exc: Exception, connection_id: str) -> str:
    detail = f"{exc} Data adapter: '{connection_id}'."
    if len(load_connection_profiles()) > 1:
        detail += " If these collections are in another database, choose that data adapter in Preview and run it again."
    return detail


def connection_summary(ctx: SimpleNamespace, connection_id: str) -> dict[str, Any]:
    profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
    if profile is None:
        return {"id": connection_id, "name": connection_id, "missing": True}
    return {"id": profile["id"], "name": profile["name"], "folder": profile.get("folder", ""), "storage": profile.get("storage", "env"),
            "database": profile.get("database", "") if profile.get("storage") == "file" else f"from ${profile.get('databaseEnv', '')}"}


def adapter_kind(ctx: SimpleNamespace, connection_id: str) -> str:
    """"mongodb" or a SQL kind (postgresql, mysql, sqlserver, oracle) (unknown adapters count as MongoDB: get_connection reports them)."""
    profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
    return (profile or {}).get("type", "mongodb")


def sql_adapter(ctx: SimpleNamespace, connection_id: str) -> tuple[str, str]:
    """(kind, url) of a SQL data adapter, or an HTTP error that says what is wrong."""
    profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
    if profile is None:
        raise HTTPException(status_code=400, detail=f"Data adapter is not registered: {connection_id}")
    kind = profile.get("type", "mongodb")
    if kind not in sql_connector.SQL_TYPES:
        raise HTTPException(status_code=422, detail=f"Data adapter '{connection_id}' is MongoDB; this report has a SQL query and needs a SQL adapter")
    url, _database = _profile_credentials(profile)
    if not url:
        raise HTTPException(status_code=503, detail=f"No connection URL is saved for data adapter '{connection_id}'")
    return kind, url


def test_adapter_values(ctx: SimpleNamespace, kind: str, uri: str, database: str, connection_id: str) -> dict[str, Any]:
    """Test connection values typed in the adapter wizard (any adapter kind)."""
    if kind in sql_connector.SQL_TYPES:
        if not sql_connector.valid_url(kind, uri):
            raise HTTPException(status_code=422, detail=sql_connector.url_hint(kind))
        try:
            return sql_connector.test(kind, uri)
        except ConnectorQueryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
    return ctx.test_mongo_connection(uri, database, connection_id)


def test_saved_adapter(ctx: SimpleNamespace, connection_id: str) -> dict[str, Any] | None:
    """Test a saved adapter by its ID (any adapter kind)."""
    if ctx.adapter_kind(connection_id) == "mongodb":
        return None  # the caller keeps its MongoDB test
    kind, url = ctx.sql_adapter(connection_id)
    try:
        return sql_connector.test(kind, url)
    except ConnectorQueryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
