"""The Query workspace (/query): look inside a data adapter and try queries, read-only.

MongoDB: the collections, their documents (filter / projection / sort, page by page), an aggregation run stage by
stage (Studio 3T style: the output after any stage), and the fields found in a sample of documents.
SQL: the schema (tables, views, columns, primary and foreign keys) and one read-only SELECT with its rows.

The same safety as reports: nothing here writes ($out / $merge / JavaScript are refused; SQL must be one read-only
statement and runs in a read-only transaction that is rolled back), every call has a time limit and a row limit.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from bson import json_util
from bson.json_util import JSONMode, JSONOptions

from backend.app.queries import mongo_query as mq

MAX_DOCS = 500
MAX_SQL_ROWS = 5000
SAMPLE_SIZE = 300
EXTENDED = JSONOptions(json_mode=JSONMode.RELAXED, tz_aware=True)
SYSTEM_COLLECTION = re.compile(r"^system\.")


def to_json(value: Any) -> Any:
    """BSON → JSON that still says what it was: {"$oid": …}, {"$date": …}, {"$numberDecimal": …} (Extended JSON)."""
    return json.loads(json_util.dumps(value, json_options=EXTENDED))


def parse_shell(text: str, what: str, default: Any) -> Any:
    """A Mongo Shell literal typed by the user ({ age: { $gt: 5 } }, ObjectId("…"), ISODate("…")) → a Python value."""
    source = mq._strip_query_comments(str(text or "")).strip()
    if not source:
        return default
    if len(source) > 200_000:
        raise ValueError(f"The {what} is too long")
    try:
        value = mq._parse_mongosh_literal(source)
        value = mq._resolve_pipeline_parameters(value, {})
    except ValueError as exc:
        raise ValueError(f"{what[:1].upper()}{what[1:]}: {exc}") from None
    mq._validate_read_only_pipeline(value)
    return value


def _limit(value: Any, default: int, top: int) -> int:
    try:
        return max(1, min(int(value), top))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ MongoDB
def mongo_collections(db: Any, max_time_ms: int = 5000) -> list[dict[str, Any]]:
    out = []
    for info in db.list_collections():
        name = info.get("name", "")
        if not name or SYSTEM_COLLECTION.match(name):
            continue
        kind = info.get("type", "collection")
        item = {"name": name, "type": kind, "count": None}
        if kind == "collection" and len(out) < 300:
            try:
                item["count"] = db[name].estimated_document_count(maxTimeMS=max_time_ms)
            except Exception:  # noqa: BLE001 - no read right on this one: still listed
                item["count"] = None
        out.append(item)
    return sorted(out, key=lambda c: c["name"].lower())


def mongo_find(db: Any, collection: str, payload: dict[str, Any], max_time_ms: int) -> dict[str, Any]:
    filter_ = parse_shell(payload.get("filter"), "filter", {})
    projection = parse_shell(payload.get("projection"), "projection", None)
    sort = parse_shell(payload.get("sort"), "sort", None)
    for name, value in (("filter", filter_), ("projection", projection), ("sort", sort)):
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"The {name} must be an object {{ … }}")
    skip = max(0, int(payload.get("skip") or 0))
    limit = _limit(payload.get("limit"), 50, MAX_DOCS)
    started = time.perf_counter()
    cursor = db[collection].find(filter_, projection or None, max_time_ms=max_time_ms).skip(skip).limit(limit)
    if sort:
        cursor = cursor.sort(list(sort.items()))
    docs = list(cursor)
    ms = round((time.perf_counter() - started) * 1000)
    total = None
    if payload.get("count", True):
        try:
            total = db[collection].count_documents(filter_, maxTimeMS=min(max_time_ms, 8000))
        except Exception:  # noqa: BLE001 - too slow to count: the page still shows
            total = None
    return {"documents": to_json(docs), "total": total, "skip": skip, "limit": limit, "ms": ms}


def mongo_pipeline(stages: Any) -> list[dict[str, Any]]:
    """The stages as the UI sends them: [{ $match: … }, …] as one text, or a list of stage texts / objects."""
    if isinstance(stages, str):
        value = parse_shell(stages, "pipeline", [])
        if isinstance(value, dict):
            value = [value]
    elif isinstance(stages, list):
        value = []
        for index, stage in enumerate(stages, 1):
            item = parse_shell(stage, f"stage {index}", None) if isinstance(stage, str) else stage
            if item is None:
                continue
            if not isinstance(item, dict) or len(item) != 1 or not next(iter(item)).startswith("$"):
                raise ValueError(f"Stage {index} must be one {{ $operator: … }} object")
            value.append(item)
    else:
        raise ValueError("Send the pipeline as a list of stages")
    if not isinstance(value, list) or not all(isinstance(s, dict) for s in value):
        raise ValueError("The pipeline must be a list of { $stage: … } objects")
    mq._validate_read_only_pipeline(value)
    return value


def mongo_aggregate(db: Any, collection: str, payload: dict[str, Any], max_time_ms: int) -> dict[str, Any]:
    pipeline = mongo_pipeline(payload.get("stages", payload.get("pipeline", [])))
    limit = _limit(payload.get("limit"), 50, MAX_DOCS)
    started = time.perf_counter()
    docs = list(db[collection].aggregate(pipeline + [{"$limit": limit + 1}], maxTimeMS=max_time_ms, allowDiskUse=False))
    ms = round((time.perf_counter() - started) * 1000)
    return {"documents": to_json(docs[:limit]), "more": len(docs) > limit, "stageCount": len(pipeline), "ms": ms}


def _walk(value: Any, path: str, found: dict[str, dict[str, Any]], depth: int = 0) -> None:
    kind = _bson_type(value)
    entry = found.setdefault(path, {"path": path, "types": {}, "count": 0, "example": None})
    entry["count"] += 1
    entry["types"][kind] = entry["types"].get(kind, 0) + 1
    if entry["example"] is None and kind not in ("object", "array", "null"):
        entry["example"] = to_json(value)
    if depth >= 6:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _walk(child, f"{path}.{key}", found, depth + 1)
    elif isinstance(value, list):
        for child in value[:20]:
            if isinstance(child, dict):
                for key, grandchild in child.items():
                    _walk(grandchild, f"{path}.{key}", found, depth + 1)


def _bson_type(value: Any) -> str:
    from datetime import datetime

    from bson import Decimal128, ObjectId
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "double"
    if isinstance(value, Decimal128):
        return "decimal"
    if isinstance(value, str):
        return "string"
    if isinstance(value, datetime):
        return "date"
    if isinstance(value, ObjectId):
        return "objectId"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def mongo_fields(db: Any, collection: str, max_time_ms: int) -> dict[str, Any]:
    """The fields in a random sample of documents: path, types (how often), and an example value."""
    docs = list(db[collection].aggregate([{"$sample": {"size": SAMPLE_SIZE}}], maxTimeMS=max_time_ms))
    found: dict[str, dict[str, Any]] = {}
    for doc in docs:
        for key, value in doc.items():
            _walk(value, key, found)
    fields = sorted(found.values(), key=lambda f: (f["path"].count("."), f["path"].lower()) if f["path"] != "_id" else (-1, ""))
    for field in fields:
        field["share"] = round(field["count"] / len(docs), 3) if docs else 0
    return {"sampled": len(docs), "fields": fields[:2000]}


def shell_code(collection: str, pipeline: list[dict[str, Any]]) -> str:
    """The pipeline as Mongo Shell code (for a report's query, or to paste in mongosh)."""
    body = json.dumps(to_json(pipeline), indent=2, ensure_ascii=False)
    return f'db.getCollection("{collection}").aggregate({body})'


# ------------------------------------------------------------------ SQL
def sql_schema(kind: str, url: str) -> dict[str, Any]:
    from backend.app.connectors import sql as sql_connector
    raw = sql_connector.read_schema(kind, url)
    tables: dict[tuple[str, str], dict[str, Any]] = {}
    for schema, table, table_type, column, data_type, nullable, _position in raw["columns"]:
        key = (str(schema or ""), str(table))
        item = tables.setdefault(key, {"schema": key[0], "name": key[1], "kind": "view" if "VIEW" in str(table_type).upper() else "table", "columns": []})
        item["columns"].append({"name": str(column), "type": str(data_type or "").lower(),
                                "nullable": str(nullable).upper() in ("YES", "Y", "TRUE", "1"), "pk": False})
    for schema, table, column in raw["primaryKeys"]:
        item = tables.get((str(schema or ""), str(table)))
        for col in (item or {}).get("columns", []):
            if col["name"] == str(column):
                col["pk"] = True
    links = []
    for schema, table, column, ref_schema, ref_table, ref_column in raw["foreignKeys"]:
        links.append({"schema": str(schema or ""), "table": str(table), "column": str(column),
                      "refSchema": str(ref_schema or ""), "refTable": str(ref_table), "refColumn": str(ref_column)})
        item = tables.get((str(schema or ""), str(table)))
        for col in (item or {}).get("columns", []):
            if col["name"] == str(column):
                col["ref"] = f"{ref_table}.{ref_column}"
    schemas: dict[str, list[dict[str, Any]]] = {}
    for item in sorted(tables.values(), key=lambda t: (t["schema"].lower(), t["kind"], t["name"].lower())):
        schemas.setdefault(item["schema"], []).append(item)
    return {"kind": kind, "schemas": [{"name": name, "tables": items} for name, items in schemas.items()],
            "foreignKeys": links, "truncated": raw.get("truncated", False)}


def sql_run(kind: str, url: str, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
    from backend.app.connectors import sql as sql_connector
    from backend.app.queries.sql_query import prepare_sql
    prepared = prepare_sql(payload.get("sql"))
    values = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
    missing = [name for name in prepared.parameter_names if name not in values]
    values = {**{name: None for name in missing}, **values}
    limit = _limit(payload.get("limit"), 200, MAX_SQL_ROWS)
    started = time.perf_counter()
    columns, rows = sql_connector.run_table(kind, url, prepared, values, max_rows=limit, timeout_ms=timeout_ms)
    ms = round((time.perf_counter() - started) * 1000)
    return {"columns": columns, "rows": [[_cell(v) for v in row] for row in rows[:limit]], "more": len(rows) > limit,
            "ms": ms, "parameters": sorted(prepared.parameter_names)}


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
