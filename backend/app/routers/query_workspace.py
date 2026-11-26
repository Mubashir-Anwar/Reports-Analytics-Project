"""The Query workspace (/query) API: browse a data adapter and run read-only queries. Needs the Designer right.

    POST /api/query/mongo/collections   {connectionId, search, offset, limit}            -> {collections, total, more} (one page)
    POST /api/query/mongo/find          {connectionId, collection, filter, projection, sort, skip, limit}
    POST /api/query/mongo/aggregate     {connectionId, collection, stages: [text…], limit} (the first n stages: a preview)
    POST /api/query/mongo/fields        {connectionId, collection}                       -> fields in a sample
    POST /api/query/sql/schema          {connectionId}                                   -> schemas, tables, columns, keys
    POST /api/query/sql/run             {connectionId, sql, parameters, limit}           -> columns, rows
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool

from backend.app.connectors.errors import ConnectorQueryError
from backend.app.services import explorer

COLLECTION_NAME = re.compile(r"^[^\x00$][^\x00]{0,254}$")


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    require_admin = ctx.require_admin
    json_body = ctx.json_body

    def limit_ms() -> int:
        return min(int(getattr(ctx.settings, "max_time_ms", 30_000) or 30_000), 60_000)

    async def call(work: Callable[[], Any]) -> Any:
        try:
            return await run_in_threadpool(work)
        except HTTPException:
            raise
        except (ValueError, ConnectorQueryError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except PyMongoError as exc:
            from backend.app.connectors.mongodb import _safe_mongo_error
            raise HTTPException(status_code=422, detail=f"MongoDB: {_safe_mongo_error(exc)}") from None

    async def mongo(request: Request, need_collection: bool = True) -> tuple[Any, str, dict[str, Any]]:
        require_admin(request, "query")
        payload = await json_body(request)
        connection_id = str(payload.get("connectionId") or "").strip()
        if not connection_id:
            raise HTTPException(status_code=422, detail="Choose a data adapter")
        ctx.require_area(connection_id, "query")
        collection = str(payload.get("collection") or "")
        if need_collection and not COLLECTION_NAME.match(collection):
            raise HTTPException(status_code=422, detail="Choose a collection")
        client, database = await run_in_threadpool(ctx.get_connection, connection_id)
        return client[database], collection, payload

    @app.post("/api/query/mongo/collections")
    async def query_mongo_collections(request: Request) -> dict[str, Any]:
        db, _collection, payload = await mongo(request, need_collection=False)
        search = str(payload.get("search") or "")[:120]
        try:
            offset, limit = int(payload.get("offset") or 0), int(payload.get("limit") or 50)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="offset and limit are numbers") from None
        page = await call(lambda: explorer.mongo_collections(db, search=search, offset=offset, limit=limit))
        return {**page, "database": db.name, "search": search}

    @app.post("/api/query/mongo/find")
    async def query_mongo_find(request: Request) -> dict[str, Any]:
        db, collection, payload = await mongo(request)
        return await call(lambda: explorer.mongo_find(db, collection, payload, limit_ms()))

    @app.post("/api/query/mongo/aggregate")
    async def query_mongo_aggregate(request: Request) -> dict[str, Any]:
        db, collection, payload = await mongo(request)
        result = await call(lambda: explorer.mongo_aggregate(db, collection, payload, limit_ms()))
        result["code"] = await call(lambda: explorer.shell_code(collection, explorer.mongo_pipeline(payload.get("stages", []))))
        return result

    @app.post("/api/query/mongo/fields")
    async def query_mongo_fields(request: Request) -> dict[str, Any]:
        db, collection, _payload = await mongo(request)
        return await call(lambda: explorer.mongo_fields(db, collection, limit_ms()))

    async def sql(request: Request) -> tuple[str, str, dict[str, Any]]:
        require_admin(request, "query")
        payload = await json_body(request)
        connection_id = str(payload.get("connectionId") or "").strip()
        ctx.require_area(connection_id, "query")
        kind, url = ctx.sql_adapter(connection_id)
        return kind, url, payload

    @app.post("/api/query/sql/schema")
    async def query_sql_schema(request: Request) -> dict[str, Any]:
        kind, url, _payload = await sql(request)
        return await call(lambda: explorer.sql_schema(kind, url))

    @app.post("/api/query/sql/run")
    async def query_sql_run(request: Request) -> dict[str, Any]:
        kind, url, payload = await sql(request)
        return await call(lambda: explorer.sql_run(kind, url, payload, limit_ms()))
