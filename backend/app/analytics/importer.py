"""Import a dataset: run its query on the data adapter and write the records into a Parquet file.

The records are read in batches from a database cursor and written line by line to a temporary NDJSON file, which
DuckDB then turns into a compressed Parquet file (types are detected from all the records). Memory stays small
whatever the size; the file is usually 5-20 times smaller than the data in the source database.

Nested MongoDB documents become dotted columns ("customer.city"); lists are kept as JSON text; ObjectId, Decimal128
and dates become text, numbers and timestamps. A new file gets a new name, so a dashboard reading the old file is
never disturbed; the old file is removed afterwards.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Iterator

from backend.app.analytics import settings
from backend.app.definitions import _dynamic_report

FILE_PATTERN = re.compile(r"^(ds_[a-f0-9]{12})-[0-9a-f]{8}\.parquet$")
MAX_COLUMNS = 400
MAX_DEPTH = 3
NUMBER_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
                "UHUGEINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL")


def definition_of(dataset: dict[str, Any]) -> Any:
    """The dataset's source as a runnable (in-memory) report definition, so every designer query language works."""
    return _dynamic_report({
        "id": dataset.get("id") or "dataset_test", "title": dataset.get("name") or "Dataset",
        "connectionId": dataset["connectionId"], "queryLanguage": dataset["queryLanguage"],
        "querySource": dataset["querySource"], "collection": dataset.get("collection") or "",
        "columns": ["_all"], "parameters": [], "folder": "General",
    })


def plain(value: Any) -> Any:
    """A JSON-friendly value (ObjectId → text, Decimal128 → number, bytes → base64)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "to_decimal"):                    # bson Decimal128
        return float(value.to_decimal())
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, (list, tuple)):
        return json.dumps([plain(item) for item in value], ensure_ascii=False, default=str)
    return str(value)


def flatten(row: dict[str, Any], prefix: str = "", depth: int = 0, out: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {} if out is None else out
    for key, value in row.items():
        name = f"{prefix}{key}"[:120]
        if isinstance(value, dict) and depth < MAX_DEPTH:
            flatten(value, f"{name}.", depth + 1, out)
        elif isinstance(value, dict):
            out[name] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            out[name] = plain(value)
    return out


def kind_of(duck_type: str) -> str:
    upper = duck_type.upper()
    if upper.startswith(NUMBER_TYPES):
        return "number"
    if upper.startswith(("DATE", "TIMESTAMP", "TIME")):
        return "date"
    if upper == "BOOLEAN":
        return "bool"
    return "text"


def connect() -> Any:
    import duckdb  # noqa: PLC0415 - only the analytics area needs it
    return duckdb.connect(":memory:", config={"memory_limit": settings.memory_limit(), "threads": settings.threads()})


def quote_path(path: Path) -> str:
    return "'" + str(path).replace("\\", "/").replace("'", "''") + "'"


def write_parquet(rows: Iterable[dict[str, Any]], target: Path, limit: int) -> int:
    """Write the records to `target` (Parquet). Returns how many; 0 records = no file."""
    temp = target.with_suffix(".ndjson.part")
    count = 0
    names: set[str] = set()
    try:
        with temp.open("w", encoding="utf-8") as handle:
            for row in rows:
                count += 1
                if count > limit:
                    raise ValueError(f"The dataset has more than {limit:,} records (ANALYTICS_IMPORT_MAX_ROWS); "
                                     "filter or summarise it in the query")
                flat = flatten(row if isinstance(row, dict) else {"value": row})
                names.update(flat)
                if len(names) > MAX_COLUMNS:
                    raise ValueError(f"The records have more than {MAX_COLUMNS} different fields; choose fields with $project")
                handle.write(json.dumps(flat, ensure_ascii=False, default=str))
                handle.write("\n")
        if count == 0:
            return 0
        con = connect()
        try:
            part = target.with_suffix(".parquet.part")
            con.execute(f"COPY (SELECT * FROM read_json({quote_path(temp)}, format='newline_delimited', union_by_name=true, "
                        f"sample_size=-1, maximum_object_size=67108864)) TO {quote_path(part)} (FORMAT parquet, COMPRESSION zstd)")
        finally:
            con.close()
        part.replace(target)
        return count
    finally:
        temp.unlink(missing_ok=True)
        target.with_suffix(".parquet.part").unlink(missing_ok=True)


def describe(path: Path) -> list[dict[str, Any]]:
    con = connect()
    try:
        found = con.execute(f"DESCRIBE SELECT * FROM read_parquet({quote_path(path)})").fetchall()
    finally:
        con.close()
    return [{"name": str(name), "type": str(kind)} for name, kind, *_ in found]


def merge_columns(found: list[dict[str, Any]], known: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The new file's columns with the model settings people made before (label, role, aggregation, format, hidden)."""
    before = {item["name"]: item for item in known or []}
    out = []
    for column in found:
        kind = kind_of(column["type"])
        old = before.get(column["name"], {})
        guessed_role = "measure" if kind == "number" and not re.search(r"(^|[._ ])(id|code|year|no|number)$", column["name"], re.I) else "dimension"
        out.append({"name": column["name"], "type": column["type"], "kind": kind,
                    "label": old.get("label") or column["name"], "role": old.get("role") or guessed_role,
                    "agg": old.get("agg") or ("sum" if kind == "number" else "count"),
                    "format": old.get("format", ""), "hidden": bool(old.get("hidden", False))})
    return out


def file_path(name: str) -> Path | None:
    return settings.directory() / name if name and FILE_PATTERN.fullmatch(name) else None


def run_import(ctx: SimpleNamespace, dataset: dict[str, Any]) -> dict[str, Any]:
    """Import now (in the calling thread). Returns {"file", "rowCount", "sizeBytes", "columns", "durationMs"}."""
    if dataset.get("sourceKind") == "python":           # a Python script over other datasets: run it again
        from backend.app.analytics import pyscript, store  # noqa: PLC0415
        return pyscript.run_import(dataset, store.dataset)
    started = time.monotonic()
    definition = definition_of(dataset)
    name = f"{dataset['id']}-{secrets.token_hex(4)}.parquet"
    target = settings.directory() / name
    rows: Iterator[dict[str, Any]] = ctx.iter_report_rows(definition, {}, definition.connection_id,
                                                          limit=settings.import_max_rows() + 1,
                                                          timeout_ms=settings.import_max_time_ms())
    try:
        count = write_parquet(rows, target, settings.import_max_rows())
    finally:
        close = getattr(rows, "close", None)
        if close:
            close()
    columns = merge_columns(describe(target), dataset.get("columns") or []) if count else []
    return {"file": name if count else "", "rowCount": count, "sizeBytes": target.stat().st_size if count else 0,
            "columns": columns, "durationMs": int((time.monotonic() - started) * 1000)}


def preview(ctx: SimpleNamespace, source: dict[str, Any], limit: int = 20) -> dict[str, Any]:
    """The first records of a source (no file): to test the query while making a dataset."""
    definition = definition_of(source)
    rows = ctx.iter_report_rows(definition, {}, definition.connection_id, limit=10_000_000, timeout_ms=ctx.settings.max_time_ms)
    found = []
    try:
        for row in rows:
            found.append(flatten(row if isinstance(row, dict) else {"value": row}))
            if len(found) >= limit:
                break
    finally:
        close = getattr(rows, "close", None)
        if close:
            close()
    columns: list[str] = []
    for row in found:
        columns.extend(name for name in row if name not in columns)
    return {"columns": columns, "rows": [[row.get(name) for name in columns] for row in found], "rowCount": len(found)}


def remove_old_files(dataset_id: str, keep: str) -> None:
    for path in settings.directory().glob(f"{dataset_id}-*.parquet"):
        if path.name != keep:
            try:
                path.unlink()
            except OSError:                              # still open somewhere (Windows): removed next time
                pass
