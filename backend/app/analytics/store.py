"""Analytics definitions in the system database (files, SQL or MongoDB: rs_analytics_* tables / collections).

    rs_analytics_datasets        one row per dataset: source (adapter + query), column model, refresh status
    rs_analytics_relationships   dataset A column = dataset B column (the model), used to join datasets in one visual
    rs_analytics_dashboards      tiles (visuals) and dashboard filters, as JSON
    rs_analytics_refreshes       one row per import (who, when, how many records, how long, error)

Everything a browser or API client sends is checked here (clean_*), so the stored JSON always has the shape the
query engine and the /analytics page expect.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from backend.app.analytics import ml, settings
from backend.app.db.entities import (
    ANALYTICS_DASHBOARDS,
    ANALYTICS_DATASETS,
    ANALYTICS_QUERIES,
    ANALYTICS_REFRESHES,
    ANALYTICS_RELATIONSHIPS,
    get_entities,
)
from backend.app.storage.files import normalize_folder_path

ID_PATTERN = re.compile(r"^(ds|rel|dash|rf|qry)_[a-f0-9]{12}$")
TILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
LANGUAGES = ("aggregation-json", "mongosh", "javascript", "python", "sql")      # the designer's query languages
KINDS = ("number", "text", "date", "bool")
ROLES = ("dimension", "measure")
AGGREGATIONS = ("sum", "avg", "min", "max", "count", "distinct", "countrows", "median")
BUCKETS = ("", "year", "quarter", "month", "week", "day")
OPERATORS = ("in", "not_in", "eq", "ne", "gt", "gte", "lt", "lte", "between", "contains", "starts", "is_null", "not_null")
VISUALS = ("kpi", "column", "bar", "line", "area", "pie", "donut", "scatter", "table", "slicer", "map", "ml")
MAX_TILES = 60
MAX_JSON = 200_000


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _text(value: Any, name: str, size: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{name} is required")
    if len(text) > size:
        raise ValueError(f"{name} may have at most {size} characters")
    return text


def _folder(value: Any) -> str:
    return normalize_folder_path(value or "Analytics")


# ------------------------------------------------------------------ datasets
def datasets() -> list[dict[str, Any]]:
    return sorted(get_entities().find(ANALYTICS_DATASETS), key=lambda item: item.get("name", "").lower())


def dataset(dataset_id: str) -> dict[str, Any] | None:
    if not ID_PATTERN.fullmatch(str(dataset_id or "")):
        return None
    return get_entities().get(ANALYTICS_DATASETS, dataset_id)


def save_dataset(record: dict[str, Any]) -> dict[str, Any]:
    get_entities().put(ANALYTICS_DATASETS, record)
    return record


def delete_dataset(dataset_id: str) -> None:
    get_entities().delete(ANALYTICS_DATASETS, dataset_id)
    for rel in relationships():
        if dataset_id in (rel["fromDataset"], rel["toDataset"]):
            get_entities().delete(ANALYTICS_RELATIONSHIPS, rel["id"])
    get_entities().delete_where(ANALYTICS_REFRESHES, {"datasetId": dataset_id})


def clean_source(payload: dict[str, Any], adapters: dict[str, str]) -> dict[str, Any]:
    """The source of a dataset: data adapter, query language, collection (MongoDB) and query."""
    connection_id = _text(payload.get("connectionId"), "Data adapter", 64, True)
    if connection_id not in adapters:
        raise ValueError(f"Data adapter '{connection_id}' is not a data source of Analytics (Analytics → Data sources)")
    language = str(payload.get("queryLanguage") or ("sql" if adapters[connection_id] != "mongodb" else "aggregation-json")).lower()
    if language not in LANGUAGES:
        raise ValueError(f"Query language must be one of {', '.join(LANGUAGES)}")
    if (language == "sql") != (adapters[connection_id] != "mongodb"):
        raise ValueError("A SQL data adapter needs a SQL query, a MongoDB adapter a MongoDB query")
    query = payload.get("querySource")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Write the query (querySource)")
    if len(query) > 100_000:
        raise ValueError("The query is longer than 100,000 characters")
    collection = _text(payload.get("collection"), "Collection", 200) if language != "sql" else ""
    return {"connectionId": connection_id, "queryLanguage": language, "querySource": query, "collection": collection}


def clean_dataset(payload: dict[str, Any], adapters: dict[str, str], current: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Send the dataset as a JSON object")
    merged = {**(current or {}), **payload}
    every = int(merged.get("refreshEveryMinutes") or 0)
    if every and every < settings.min_refresh_minutes():
        raise ValueError(f"A scheduled refresh runs at most every {settings.min_refresh_minutes()} minutes (or 0 = only by hand)")
    if every > 7 * 24 * 60:
        raise ValueError("Refresh at least once a week, or set 0 (only by hand)")
    if merged.get("sourceKind") == "pipeline":          # ETL / ELT steps over other datasets (pipeline.py)
        from backend.app.analytics import pipeline  # noqa: PLC0415
        clean = pipeline.clean_steps(merged.get("steps", merged.get("querySource")), dataset, (current or {}).get("id", ""))
        source = {"sourceKind": "pipeline", "inputs": clean["inputs"], "connectionId": pipeline.CONNECTION,
                  "queryLanguage": pipeline.LANGUAGE, "querySource": clean["steps"], "collection": ""}
    elif merged.get("sourceKind") == "python":            # made by a Python script from other datasets (pyscript.py)
        from backend.app.analytics import pyscript  # noqa: PLC0415
        clean = pyscript.clean_source({**merged, "script": merged.get("script", merged.get("querySource"))}, dataset,
                                      (current or {}).get("id", ""))
        # the script is kept as the dataset's query (querySource); "python" / "pandas" mark it in the required columns
        source = {"sourceKind": "python", "inputs": clean["inputs"], "connectionId": pyscript.CONNECTION,
                  "queryLanguage": pyscript.LANGUAGE, "querySource": clean["script"], "collection": ""}
    else:
        source = {**clean_source(merged, adapters), "sourceKind": "", "inputs": []}
    out = {"name": _text(merged.get("name"), "Name", 120, True), "folder": _folder(merged.get("folder")),
           "description": _text(merged.get("description"), "Description", 500), "refreshEveryMinutes": every, **source}
    if current is not None:
        out["columns"] = clean_columns(payload.get("columns"), current.get("columns") or []) if "columns" in payload else current.get("columns") or []
    return out


def clean_columns(sent: Any, known: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The model of a dataset's columns: only label, role, aggregation, format and hidden may be changed (the name,
    type and kind come from the imported file)."""
    if sent is None:
        return known
    if not isinstance(sent, list):
        raise ValueError("columns must be a list")
    by_name = {str(item.get("name")): item for item in sent if isinstance(item, dict)}
    out = []
    for column in known:
        change = by_name.get(column["name"], {})
        role = str(change.get("role", column.get("role", ""))).lower()
        agg = str(change.get("agg", column.get("agg", ""))).lower()
        out.append({**column,
                    "label": _text(change.get("label", column.get("label", "")), "Label", 120) or column["name"],
                    "role": role if role in ROLES else column.get("role", "dimension"),
                    "agg": agg if agg in AGGREGATIONS else column.get("agg", "sum"),
                    "format": _text(change.get("format", column.get("format", "")), "Format", 40),
                    "hidden": bool(change.get("hidden", column.get("hidden", False)))})
    return out


# ------------------------------------------------------------------ relationships (the model)
def relationships() -> list[dict[str, Any]]:
    return get_entities().find(ANALYTICS_RELATIONSHIPS)


def clean_relationship(payload: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    """{fromDataset, fromColumn, toDataset, toColumn, cardinality, active}. The "to" side is usually the list (one)."""
    if not isinstance(payload, dict):
        raise ValueError("Send the relationship as a JSON object")
    merged = {**(current or {}), **payload}
    sides = {}
    for side in ("from", "to"):
        item = dataset(str(merged.get(f"{side}Dataset", "")))
        if item is None:
            raise ValueError(f"Choose the {side} dataset")
        column = str(merged.get(f"{side}Column", ""))
        if column not in {col["name"] for col in item.get("columns") or []}:
            raise ValueError(f"'{column}' is not a column of {item['name']} (refresh the dataset first)")
        sides[side] = (item["id"], column)
    if sides["from"][0] == sides["to"][0]:
        raise ValueError("A relationship joins two different datasets")
    active = merged.get("active", True) is not False
    for rel in relationships():
        if current and rel["id"] == current.get("id"):
            continue
        pair = {(rel["fromDataset"], rel["toDataset"]), (rel["toDataset"], rel["fromDataset"])}
        if (sides["from"][0], sides["to"][0]) in pair and active and rel.get("active") is not False:
            raise ValueError("These two datasets already have an active relationship; make one of them inactive (or delete it) first")
    cardinality = str(merged.get("cardinality") or "many-to-one")
    if cardinality not in ("many-to-one", "one-to-one", "one-to-many", "many-to-many"):
        raise ValueError("cardinality must be many-to-one, one-to-one, one-to-many or many-to-many")
    return {"fromDataset": sides["from"][0], "fromColumn": sides["from"][1], "toDataset": sides["to"][0],
            "toColumn": sides["to"][1], "active": active, "cardinality": cardinality}


def relationship(rel_id: str) -> dict[str, Any] | None:
    return next((rel for rel in relationships() if rel["id"] == rel_id), None)


def save_relationship(record: dict[str, Any]) -> dict[str, Any]:
    get_entities().put(ANALYTICS_RELATIONSHIPS, record)
    return record


def delete_relationship(rel_id: str) -> bool:
    return bool(ID_PATTERN.fullmatch(rel_id)) and get_entities().delete(ANALYTICS_RELATIONSHIPS, rel_id)


# ------------------------------------------------------------------ dashboards
def dashboards() -> list[dict[str, Any]]:
    return sorted(get_entities().find(ANALYTICS_DASHBOARDS), key=lambda item: item.get("name", "").lower())


def dashboard(dashboard_id: str) -> dict[str, Any] | None:
    if not ID_PATTERN.fullmatch(str(dashboard_id or "")):
        return None
    return get_entities().get(ANALYTICS_DASHBOARDS, dashboard_id)


def save_dashboard(record: dict[str, Any]) -> dict[str, Any]:
    get_entities().put(ANALYTICS_DASHBOARDS, record)
    return record


def delete_dashboard(dashboard_id: str) -> bool:
    return get_entities().delete(ANALYTICS_DASHBOARDS, dashboard_id)


def _field(value: Any, where: str, *, agg: bool = False, bucket: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object like {{\"column\": \"Amount\"}}")
    out: dict[str, Any] = {"column": _text(value.get("column"), f"{where}.column", 120, not agg or value.get("agg") != "countrows")}
    if value.get("dataset"):
        out["dataset"] = _text(value.get("dataset"), f"{where}.dataset", 40)
    if agg:
        name = str(value.get("agg") or "sum").lower()
        if name not in AGGREGATIONS:
            raise ValueError(f"{where}.agg must be one of {', '.join(AGGREGATIONS)}")
        out["agg"] = name
    if bucket or value.get("bucket"):
        name = str(value.get("bucket") or "").lower()
        if name not in BUCKETS:
            raise ValueError(f"{where}.bucket must be one of year, quarter, month, week, day")
        if name:
            out["bucket"] = name
    if value.get("label"):
        out["label"] = _text(value.get("label"), f"{where}.label", 120)
    return out


def clean_filter(value: Any, where: str) -> dict[str, Any]:
    out = _field(value, where)
    op = str(value.get("op") or "in").lower()
    if op not in OPERATORS:
        raise ValueError(f"{where}.op must be one of {', '.join(OPERATORS)}")
    values = value.get("values", [])
    if not isinstance(values, list) or len(values) > 1000:
        raise ValueError(f"{where}.values must be a list of at most 1000 values")
    if any(isinstance(item, (dict, list)) for item in values):
        raise ValueError(f"{where}.values may hold only text, numbers, true/false or null")
    out.update({"op": op, "values": values})
    if value.get("fromTile") and TILE_ID.fullmatch(str(value["fromTile"])):
        out["fromTile"] = str(value["fromTile"])
    return out


def clean_visual(value: Any, where: str) -> dict[str, Any]:
    """The query part of a visual (also what POST /api/analytics/query receives)."""
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    group_by = value.get("groupBy") or []
    values = value.get("values") or []
    filters = value.get("filters") or []
    if not isinstance(group_by, list) or len(group_by) > 3:
        raise ValueError(f"{where}.groupBy must be a list of at most 3 fields")
    if not isinstance(values, list) or len(values) > 8:
        raise ValueError(f"{where}.values must be a list of at most 8 values")
    if not isinstance(filters, list) or len(filters) > 30:
        raise ValueError(f"{where}.filters must be a list of at most 30 filters")
    sort = value.get("sort") or []
    if not isinstance(sort, list) or len(sort) > 4:
        raise ValueError(f"{where}.sort must be a list of at most 4 items")
    clean_sort = []
    for index, item in enumerate(sort):
        by = str((item or {}).get("by", ""))
        if not re.fullmatch(r"(group|value|column):\d", by):
            raise ValueError(f"{where}.sort[{index}].by must be like group:0 or value:0")
        clean_sort.append({"by": by, "dir": "desc" if str(item.get("dir", "")).lower() == "desc" else "asc"})
    analysis = ml.clean(value.get("ml"), f"{where}.ml")
    limit = value.get("limit")
    detail = bool(value.get("detail"))
    columns = value.get("columns") or []
    if detail and (not isinstance(columns, list) or not columns or len(columns) > 40):
        raise ValueError(f"{where}.columns: list 1 to 40 columns to show without summarising")
    return {
        **({"ml": analysis} if analysis else {}),
        "dataset": _text(value.get("dataset"), f"{where}.dataset", 40, True),
        "groupBy": [_field(item, f"{where}.groupBy[{i}]", bucket=True) for i, item in enumerate(group_by)],
        "values": [_field(item, f"{where}.values[{i}]", agg=True) for i, item in enumerate(values)],
        "filters": [clean_filter(item, f"{where}.filters[{i}]") for i, item in enumerate(filters)],
        "sort": clean_sort, "limit": max(1, min(int(limit), settings.query_max_rows())) if limit else None,
        "detail": detail, "columns": [_field(item, f"{where}.columns[{i}]") for i, item in enumerate(columns)] if detail else [],
    }


LINK_KINDS = ("url", "email", "report", "dashboard")


def _clean_links(raw: Any, where: str) -> list[dict[str, str]]:
    """Links of a table visual (frontend analytics/links.js): which column, and what its values open."""
    if not isinstance(raw, list) or len(raw) > 10:
        raise ValueError(f"{where}: at most 10 links")
    links = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("kind") not in LINK_KINDS or not str(item.get("column") or "").strip():
            raise ValueError(f"{where}[{index}] needs a column and a kind ({', '.join(LINK_KINDS)})")
        link = {"column": str(item["column"]).strip()[:120], "kind": item["kind"]}
        for name, size in (("template", 2000), ("report", 80), ("folder", 300), ("parameter", 80), ("dashboard", 80), ("filter", 120), ("tooltip", 120)):
            value = re.sub(r"[\x00-\x1f]", "", str(item.get(name) or "")).strip()[:size]
            if value:
                link[name] = value
        template = link.get("template", "")
        if link["kind"] == "url" and template and not re.match(r"^(https?://|www\.|\{)", template, re.IGNORECASE):
            raise ValueError(f"{where}[{index}]: a web address starts with http:// or https://")
        if link.get("report") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", link["report"]):
            raise ValueError(f"{where}[{index}]: invalid report ID")
        if link.get("dashboard") and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", link["dashboard"]):
            raise ValueError(f"{where}[{index}]: invalid dashboard ID")
        links.append(link)
    return links


def clean_dashboard(payload: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Send the dashboard as a JSON object")
    if len(json.dumps(payload, default=str)) > MAX_JSON:
        raise ValueError("The dashboard is too big (at most 200 KB of definition)")
    merged = {**(current or {}), **payload}
    tiles = merged.get("tiles") or []
    if not isinstance(tiles, list) or len(tiles) > MAX_TILES:
        raise ValueError(f"A dashboard has at most {MAX_TILES} tiles")
    clean_tiles, seen = [], set()
    for index, tile in enumerate(tiles):
        where = f"tiles[{index}]"
        if not isinstance(tile, dict):
            raise ValueError(f"{where} must be an object")
        tile_id = str(tile.get("id") or "")
        if not TILE_ID.fullmatch(tile_id) or tile_id in seen:
            raise ValueError(f"{where}.id must be unique (letters, numbers, - and _)")
        seen.add(tile_id)
        kind = str(tile.get("type") or "")
        if kind not in VISUALS:
            raise ValueError(f"{where}.type must be one of {', '.join(VISUALS)}")
        options = tile.get("options") or {}
        if not isinstance(options, dict):
            raise ValueError(f"{where}.options must be an object")
        if "links" in options:
            options = {**options, "links": _clean_links(options["links"], f"{where}.options.links")}
        layout = {name: int(tile.get(name) or 0) for name in ("x", "y", "w", "h")}
        layout["w"] = max(1, min(12, layout["w"] or 4))
        layout["x"] = max(0, min(12 - layout["w"], layout["x"]))
        layout["y"] = max(0, min(1000, layout["y"]))
        layout["h"] = max(1, min(40, layout["h"] or 4))
        visual = clean_visual(tile, where) if tile.get("dataset") else {"dataset": "", "groupBy": [], "values": [], "filters": [],
                                                                         "sort": [], "limit": None, "detail": False, "columns": []}
        clean_tiles.append({"id": tile_id, "type": kind, "title": _text(tile.get("title"), f"{where}.title", 120),
                            **layout, **visual, "options": options})
    filters = merged.get("filters") or []
    if not isinstance(filters, list) or len(filters) > 30:
        raise ValueError("A dashboard has at most 30 filters")
    return {"name": _text(merged.get("name"), "Name", 120, True), "folder": _folder(merged.get("folder")),
            "description": _text(merged.get("description"), "Description", 500), "tiles": clean_tiles,
            "filters": [clean_filter(item, f"filters[{i}]") for i, item in enumerate(filters)]}


# ------------------------------------------------------------------ saved queries (Analytics → Queries)
def queries() -> list[dict[str, Any]]:
    return sorted(get_entities().find(ANALYTICS_QUERIES), key=lambda item: item.get("name", "").lower())


def saved_query(query_id: str) -> dict[str, Any] | None:
    if not ID_PATTERN.fullmatch(str(query_id or "")) or not str(query_id).startswith("qry_"):
        return None
    return get_entities().get(ANALYTICS_QUERIES, query_id)


def save_query(record: dict[str, Any]) -> dict[str, Any]:
    get_entities().put(ANALYTICS_QUERIES, record)
    return record


def delete_query(query_id: str) -> bool:
    return saved_query(query_id) is not None and get_entities().delete(ANALYTICS_QUERIES, query_id)


QUERY_LANGUAGES = ("dax", "sql", "source")


def clean_query(payload: Any, current: dict[str, Any] | None = None, adapters: dict[str, str] | None = None) -> dict[str, Any]:
    """A saved query: DAX or SQL over the datasets, or "source": a read-only query run on a data adapter itself
    (MongoDB: Mongo Shell, Aggregation JSON, Python or JavaScript pipeline; or the database's own SQL)."""
    if not isinstance(payload, dict):
        raise ValueError("Send the query as a JSON object")
    merged = {**(current or {}), **payload}
    language = str(merged.get("language") or "dax").lower()
    if language not in QUERY_LANGUAGES:
        raise ValueError("language must be dax, sql (over the datasets) or source (on a data adapter)")
    text = str(merged.get("text") or "")
    if not text.strip():
        raise ValueError("The query is empty")
    if len(text) > 100_000 or (language != "source" and len(text) > 20_000):
        raise ValueError("The query is too long (DAX/SQL: 20,000 characters, database queries: 100,000)")
    out = {"name": _text(merged.get("name"), "Name", 120, True), "folder": _folder(merged.get("folder")),
           "description": _text(merged.get("description"), "Description", 500), "language": language, "text": text}
    if language == "source":
        if adapters is None:
            raise ValueError("The data adapters could not be read")
        source = clean_source({"connectionId": merged.get("connectionId"), "queryLanguage": merged.get("queryLanguage"),
                               "collection": merged.get("collection"), "querySource": text}, adapters)
        out.update({"connectionId": source["connectionId"], "queryLanguage": source["queryLanguage"], "collection": source["collection"]})
    else:
        out.update({"connectionId": "", "queryLanguage": "", "collection": ""})
    return out


# ------------------------------------------------------------------ refresh log
def add_refresh(record: dict[str, Any]) -> None:
    get_entities().put(ANALYTICS_REFRESHES, record)


def refreshes(dataset_id: str, limit: int = 50) -> list[dict[str, Any]]:
    items = get_entities().find(ANALYTICS_REFRESHES, {"datasetId": dataset_id})
    items.sort(key=lambda item: item.get("startedAt") or "", reverse=True)
    for old in items[200:]:                            # keep the last 200 per dataset
        get_entities().delete(ANALYTICS_REFRESHES, old["id"])
    return items[:limit]
