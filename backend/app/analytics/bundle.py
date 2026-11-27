"""A dashboard as one file (.mdash): to download it from one server and upload it to another (or to another folder).

    {"format": "analysis-dashboard", "version": 1, "exportedAt": "…",
     "dashboard": {"id", "name", "folder", "description", "tiles", "filters"},
     "datasets": [{"id", "name", "folder", "description", "connectionId", "queryLanguage", "collection",
                   "querySource", "columns", "refreshEveryMinutes"}],        the datasets its visuals use
     "relationships": [{"fromDataset", "fromColumn", "toDataset", "toColumn"}]}   between those datasets

The file holds definitions, no data adapter addresses or passwords (a dataset names its data adapter by ID; on upload
another adapter can be chosen). Uploading creates the datasets (or reuses one with the same ID and the same query),
their relationships and the dashboard; the new datasets are then imported.

"With data" files also carry each dataset's imported records ("data": {"parquet": base64 of the Parquet file,
"rowCount"}), like a Power BI file carries its data: the dashboard opens on another server at once, even one without
the database. Such a dataset is not imported on upload; when its data adapter is missing it keeps the file's data
(refreshing it needs a data adapter of that ID, or edit the dataset and choose one)."""
from __future__ import annotations

import base64
import json
import secrets
from typing import Any

from backend.app.analytics import importer, settings, store

FORMAT = "analysis-dashboard"
VERSION = 1
MAX_FILE = 2_000_000                 # a file without data
MAX_DATA_BYTES = 15_000_000          # the Parquet files in a file "with data", together (about 20 MB as base64)
MAX_FILE_WITH_DATA = 22_000_000
SOURCE_FIELDS = ("connectionId", "queryLanguage", "collection", "querySource")
FIELD_LISTS = ("groupBy", "values", "filters", "columns")


def used_datasets(dashboard: dict[str, Any]) -> list[str]:
    """Every dataset a dashboard's visuals and filters name, in first-seen order."""
    seen: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.append(value)
    for tile in dashboard.get("tiles") or []:
        add(tile.get("dataset"))
        for part in FIELD_LISTS:
            for field in tile.get(part) or []:
                if isinstance(field, dict):
                    add(field.get("dataset"))
    for item in dashboard.get("filters") or []:
        if isinstance(item, dict):
            add(item.get("dataset"))
    return seen


def export(dashboard: dict[str, Any], with_data: bool = False) -> dict[str, Any]:
    ids = used_datasets(dashboard)
    datasets = [item for item in (store.dataset(i) for i in ids) if item is not None]
    inside = {item["id"] for item in datasets}
    data: dict[str, dict[str, Any]] = {}
    if with_data:
        total = 0
        for item in datasets:
            path = importer.file_path(item.get("file") or "") if item.get("file") else None
            if path is None or not path.exists():
                continue
            raw = path.read_bytes()
            total += len(raw)
            if total > MAX_DATA_BYTES:
                raise ValueError(f"The data of this dashboard is more than {MAX_DATA_BYTES // 1_000_000} MB: download the file without data "
                                 "(the other server imports it from the database), or summarise the datasets")
            data[item["id"]] = {"parquet": base64.b64encode(raw).decode("ascii"), "rowCount": int(item.get("rowCount") or 0)}
    out = {
        "format": FORMAT, "version": VERSION, "exportedAt": store.now(),
        "dashboard": {name: dashboard.get(name) for name in ("id", "name", "folder", "description", "tiles", "filters")},
        "datasets": [{name: item.get(name) for name in ("id", "name", "folder", "description", *SOURCE_FIELDS, "columns", "refreshEveryMinutes")}
                     for item in datasets],
        "relationships": [{name: rel.get(name) for name in ("fromDataset", "fromColumn", "toDataset", "toColumn")}
                          for rel in store.relationships() if rel["fromDataset"] in inside and rel["toDataset"] in inside],
    }
    if with_data:
        out["withData"] = True
        for item in out["datasets"]:
            if item["id"] in data:
                item["data"] = data[item["id"]]
    return out


def read(content: Any) -> dict[str, Any]:
    """The bundle from the uploaded text (or an already parsed object), checked for its shape."""
    if isinstance(content, str):
        if content.startswith("PK"):                    # a ZIP package, as .pbix / .pbit files are
            raise ValueError("This is a ZIP package (a .pbix or .pbit file?), not a dashboard file (.mdash): "
                             "open .pbix / .pbit files with Dashboards → Import")
        if len(content) > MAX_FILE_WITH_DATA or (len(content) > MAX_FILE and '"parquet"' not in content[:MAX_FILE]):
            raise ValueError("The dashboard file is too big (at most 2 MB, or 20 MB with data)")
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            raise ValueError("This is not a dashboard file (.mdash): it is not JSON") from None
    if not isinstance(content, dict) or content.get("format") != FORMAT:
        raise ValueError("This is not a dashboard file (.mdash)")
    if int(content.get("version") or 0) > VERSION:
        raise ValueError("This dashboard file was made by a newer version of the service; update this server first")
    if not isinstance(content.get("dashboard"), dict):
        raise ValueError("The dashboard file has no dashboard")
    for part in ("datasets", "relationships"):
        if not isinstance(content.get(part) or [], list):
            raise ValueError(f"'{part}' in the dashboard file must be a list")
    return content


def _columns(raw: Any) -> list[dict[str, Any]]:
    """Columns from a file: only the model settings, checked like the column editor checks them."""
    basic = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and 0 < len(item["name"]) <= 200:
            kind = item.get("kind") if item.get("kind") in store.KINDS else "text"
            basic.append({"name": item["name"], "type": str(item.get("type") or "VARCHAR")[:40], "kind": kind,
                          "role": "measure" if kind == "number" else "dimension", "agg": "sum" if kind == "number" else "count"})
    return store.clean_columns(raw if isinstance(raw, list) else [], basic)


def _remap(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: (ids.get(item, item) if key == "dataset" and isinstance(item, str) else _remap(item, ids)) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, ids) for item in value]
    return value


def install(bundle: dict[str, Any], *, folder: str, actor: str, adapters: dict[str, str], connection_id: str = "",
            replace: bool = False) -> dict[str, Any]:
    """Create the datasets, relationships and dashboard of a bundle. Returns what was made and which datasets need
    an import. Raises ValueError (nothing is saved) when a dataset or the dashboard is not valid."""
    if connection_id and connection_id not in adapters:
        raise ValueError(f"There is no data adapter '{connection_id}' on this server")
    when = store.now()
    ids: dict[str, str] = {}
    new_records, reused, warnings = [], [], []
    files: list[tuple[dict[str, Any], dict[str, Any]]] = []          # datasets whose records come with the file
    missing = sorted({str(item.get("connectionId")) for item in bundle.get("datasets") or []
                      if isinstance(item, dict) and not connection_id and item.get("connectionId") not in adapters and not _has_data(item)})
    if missing:
        raise ValueError(f"This server has no data adapter {', '.join(missing)}: choose one of its data adapters for the datasets")
    for index, raw in enumerate(bundle.get("datasets") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"datasets[{index}] must be an object")
        source = {**raw, "folder": folder, "connectionId": connection_id or raw.get("connectionId")}
        known = adapters
        if _has_data(raw) and source["connectionId"] not in adapters:      # its data comes with the file: no adapter needed now
            known = {**adapters, str(source["connectionId"]): "postgresql" if str(raw.get("queryLanguage")) == "sql" else "mongodb"}
        try:
            clean = store.clean_dataset(source, known)
        except ValueError as exc:
            raise ValueError(f"Dataset '{raw.get('name', index)}': {exc}") from None
        old_id = str(raw.get("id") or "")
        existing = store.dataset(old_id) if store.ID_PATTERN.fullmatch(old_id) else None
        if existing is not None and all(existing.get(name) == clean[name] for name in SOURCE_FIELDS):
            ids[old_id] = old_id                       # the same dataset is already here: use it
            reused.append(old_id)
            continue
        record = {"id": store.new_id("ds"), **clean, "columns": _columns(raw.get("columns")), "status": "new", "rowCount": 0,
                  "sizeBytes": 0, "createdBy": actor, "createdAt": when, "updatedBy": actor, "updatedAt": when}
        if _has_data(raw):
            files.append((record, raw["data"]))
        else:
            new_records.append(record)                 # imported from its data adapter after the upload
        if old_id:
            ids[old_id] = record["id"]

    raw_board = bundle["dashboard"]
    board = _remap({name: raw_board.get(name) for name in ("name", "description", "tiles", "filters")}, ids)
    board["folder"] = folder
    clean_board = store.clean_dashboard(board)                 # checked before anything is saved
    unknown = [i for i in used_datasets(clean_board) if i not in ids.values() and store.dataset(i) is None]
    if unknown:
        warnings.append(f"{len(unknown)} visual dataset(s) are not in the file; those visuals show an error until fixed")

    written = [_write_data(record, data) for record, data in files]   # checked (a real Parquet file) before anything is saved
    for record in new_records:
        store.save_dataset(record)
    for record in written:
        store.save_dataset(record)
    relationships = 0
    for raw in bundle.get("relationships") or []:
        if not isinstance(raw, dict):
            continue
        try:
            rel = store.clean_relationship({**raw, "fromDataset": ids.get(raw.get("fromDataset"), raw.get("fromDataset")),
                                            "toDataset": ids.get(raw.get("toDataset"), raw.get("toDataset"))})
        except ValueError:
            continue                                   # already related here, or a column is missing: keep what exists
        store.save_relationship({"id": store.new_id("rel"), **rel, "createdBy": actor, "createdAt": when})
        relationships += 1

    old_board_id = str(raw_board.get("id") or "")
    current = store.dashboard(old_board_id) if replace and store.ID_PATTERN.fullmatch(old_board_id) else None
    record = {**(current or {}), "id": current["id"] if current else store.new_id("dash"), **clean_board,
              "owner": (current or {}).get("owner") or actor, "createdAt": (current or {}).get("createdAt") or when,
              "updatedBy": actor, "updatedAt": when}
    store.save_dashboard(record)
    return {"dashboard": record, "created": [item["id"] for item in [*new_records, *written]], "reused": reused,
            "relationships": relationships, "replaced": current is not None, "warnings": warnings,
            "newDatasets": new_records, "ids": ids, "withData": [item["id"] for item in written]}


def _has_data(raw: Any) -> bool:
    return isinstance(raw, dict) and isinstance(raw.get("data"), dict) and isinstance(raw["data"].get("parquet"), str)


def _write_data(record: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Write a dataset's records from the file as its Parquet file; the dataset is then ready, no import needed."""
    try:
        raw = base64.b64decode(data["parquet"], validate=True)
    except (ValueError, TypeError):
        raise ValueError(f"Dataset '{record['name']}': its data in the file is damaged") from None
    if not raw.startswith(b"PAR1") or len(raw) > MAX_DATA_BYTES:
        raise ValueError(f"Dataset '{record['name']}': its data in the file is not a Parquet file")
    name = f"{record['id']}-{secrets.token_hex(4)}.parquet"
    target = settings.directory() / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    try:
        found = importer.describe(target)
        con = importer.connect()
        try:
            count = int(con.execute(f"SELECT COUNT(*) FROM read_parquet({importer.quote_path(target)})").fetchone()[0])
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - not readable: nothing of it is kept
        target.unlink(missing_ok=True)
        raise ValueError(f"Dataset '{record['name']}': its data in the file could not be read") from None
    return {**record, "status": "ready", "file": name, "rowCount": count, "sizeBytes": len(raw),
            "columns": importer.merge_columns(found, record.get("columns") or []), "refreshedAt": record["createdAt"]}
