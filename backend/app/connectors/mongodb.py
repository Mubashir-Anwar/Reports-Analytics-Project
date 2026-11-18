"""MongoDB connector: database names, safe error text, connection checks, running a pipeline."""
from __future__ import annotations

import re
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from backend.app.connectors.errors import MongoReadAccessError
from backend.app.connectors.mongo_fallback import _denied_collections, _fallback_unsupported, _run_read_pipeline
from backend.app.logs import LOG


def _mongo_database_from_uri(uri: str) -> str:
    try:
        return unquote(urlsplit(uri).path.lstrip("/"))
    except ValueError:
        return ""


def _safe_mongo_error(error: Exception, uri: str = "") -> str:
    detail = str(error) or error.__class__.__name__
    if uri:
        safe_uri = re.sub(r"(mongodb(?:\+srv)?://)[^@/\s]+@", r"\1***@", uri)
        detail = detail.replace(uri, safe_uri)
    return re.sub(r"mongodb(?:\+srv)?://[^@/\s]+@", "mongodb://***@", detail)


def _check_mongo_connection(uri: str, database: str = "") -> str:
    uri = uri.strip()
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise HTTPException(status_code=422, detail="The MongoDB URL must start with mongodb:// or mongodb+srv://")
    selected_database = database.strip() or _mongo_database_from_uri(uri)
    if not selected_database:
        raise HTTPException(status_code=422, detail="The URL has no database name; enter the database name as well")
    client: MongoClient | None = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client[selected_database].command("ping")
        return selected_database
    except Exception as exc:
        detail = _safe_mongo_error(exc, uri)
        LOG.warning("MongoDB connection check failed: %s", detail)
        raise HTTPException(status_code=503, detail=f"MongoDB connection failed: {detail}") from None
    finally:
        if client is not None:
            client.close()


def iter_report_pipeline(collection: Any, pipeline: list[dict[str, Any]], *, max_time_ms: int, allow_disk_use: bool,
                         batch_size: int = 2000) -> Iterator[dict[str, Any]]:
    """Records of a pipeline read in batches from a cursor (big exports): only one batch is in memory at a time.
    No read fallback here: a user without the aggregate permission gets the normal (limited) run instead."""
    try:
        cursor = collection.aggregate(pipeline, maxTimeMS=max_time_ms, allowDiskUse=allow_disk_use, batchSize=batch_size)
    except PyMongoError as error:
        if getattr(error, "code", None) == 13:
            raise MongoReadAccessError("Big exports need the MongoDB 'aggregate' permission on the report's collections") from None
        raise
    with cursor:
        yield from cursor


def _execute_report_pipeline(
    collection: Any,
    pipeline: list[dict[str, Any]],
    *,
    max_time_ms: int,
    allow_disk_use: bool,
    max_rows: int,
) -> list[dict[str, Any]]:
    try:
        return list(collection.aggregate(
            pipeline,
            maxTimeMS=max_time_ms,
            allowDiskUse=allow_disk_use,
        ))
    except PyMongoError as aggregate_error:
        if getattr(aggregate_error, "code", None) != 13:
            raise
        LOG.warning("MongoDB denied aggregate (code 13); checking read access before the find/read fallback")
        # MongoDB needs 'find' on every collection an aggregate touches ($lookup/$unionWith too),
        # so name the collections this user cannot read: that is what the DBA has to grant.
        denied = _denied_collections(collection, pipeline, max_time_ms)
        if denied:
            raise MongoReadAccessError(
                "MongoDB rejected the aggregate because this connection's user cannot read (find) these collections: "
                + ", ".join(denied)
                + ". Ask the DBA to grant 'find' (the read role) on these collections; the report then runs in MongoDB itself."
            )
        unsupported = _fallback_unsupported(pipeline)
        if unsupported:
            raise MongoReadAccessError(
                "MongoDB stopped this user from running aggregate (permission, code 13), and the Python read fallback cannot run "
                "these parts of the pipeline: " + ", ".join(unsupported)
                + ". Fix: ask the DBA to give this MongoDB user aggregate permission on the report's collections (the built-in 'read' role)."
            )
        try:
            rows = _run_read_pipeline(
                collection, pipeline, max_time_ms=max_time_ms, max_rows=max_rows
            )
            LOG.info("MongoDB report pipeline completed with bounded find/read fallback")
            return rows
        except PyMongoError as read_error:
            if getattr(read_error, "code", None) == 13:
                raise MongoReadAccessError(
                    "MongoDB rejected both aggregate and find for the current connection. "
                    "The report data cannot be read with this account's current access."
                ) from None
            raise
