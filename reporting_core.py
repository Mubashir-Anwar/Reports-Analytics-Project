from __future__ import annotations

import csv
import copy
import base64
import ast
from html import escape as html_escape
import ipaddress
import importlib
import io
import json
import logging
import math
import os
import pkgutil
import re
import secrets
import shutil
import time
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
from decimal import Decimal
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit

from bson import Int64, ObjectId
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import designer_render

LOG = logging.getLogger("mongo_reporting_service")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

OUTPUT_FORMATS = {"csv", "xlsx", "pdf"}
QUERY_LANGUAGES = {"aggregation-json", "mongosh", "javascript", "python"}
METADATA_KEYS = {"GeneratedBy", "GeneratedByCode", "Folder", "Generate"}
REPORT_PATH = re.compile(r"^([A-Za-z0-9_-]+)\.(csv|xlsx|pdf)$", re.IGNORECASE)
COLLECTION_TEMPLATE = re.compile(r"^(?:[A-Za-z0-9_.\-]|\$P\{[A-Za-z_][A-Za-z0-9_]*\}){1,160}$")
COLLECTION_PARAM_VALUE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
REPORT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
CONNECTION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ENV_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def data_directory() -> Path:
    configured = os.getenv("REPORTING_DATA_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parent / "data"


def report_definitions_directory() -> Path:
    return data_directory() / "reports"


def connections_file() -> Path:
    return data_directory() / "connections.json"


def folders_file() -> Path:
    return data_directory() / "folders.json"


def private_secrets_file() -> Path:
    return data_directory() / "private_secrets.json"


def report_audit_file() -> Path:
    return data_directory() / "audit" / "report-executions.jsonl"


def _append_report_audit(audit: dict[str, Any]) -> None:
    """Persist one audit event locally without requiring MongoDB write access."""
    path = report_audit_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(audit, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)


def _load_private_secrets() -> dict[str, Any]:
    path = private_secrets_file()
    if not path.exists():
        return {"admin_token": "", "mongo_connections": {}}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("private_secrets.json ka root JSON object hona chahiye")
    connections = raw.get("mongo_connections", {})
    if not isinstance(connections, dict):
        raise ValueError("private_secrets.json mein mongo_connections object hona chahiye")
    return {
        "admin_token": str(raw.get("admin_token", "")).strip(),
        "mongo_connections": connections,
    }


def _save_private_secrets(payload: dict[str, Any]) -> None:
    path = private_secrets_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # Restrictive mode is honored on Unix; Windows inherits the current user's ACL.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(serialized)
    temporary.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        LOG.debug("Could not set private secret file permissions on this platform")


def ensure_admin_token() -> str:
    """Use a configured token or create and persist one for a local first run."""
    configured = os.getenv("REPORTING_ADMIN_TOKEN", "").strip()
    stored = _load_private_secrets()
    token = configured or stored["admin_token"] or secrets.token_urlsafe(32)
    if not configured and token != stored["admin_token"]:
        stored["admin_token"] = token
        _save_private_secrets(stored)
        LOG.info("Generated a persistent admin token in the private secrets file")
    os.environ["REPORTING_ADMIN_TOKEN"] = token
    return token


def _mongo_database_from_uri(uri: str) -> str:
    try:
        return unquote(urlsplit(uri).path.lstrip("/"))
    except ValueError:
        return ""


def _profile_credentials(profile: dict[str, str]) -> tuple[str, str]:
    if profile.get("storage") == "file":
        saved = _load_private_secrets()["mongo_connections"].get(profile["id"], {})
        if not isinstance(saved, dict):
            return "", ""
        return str(saved.get("uri", "")).strip(), str(saved.get("database", profile.get("database", ""))).strip()
    return os.getenv(profile.get("uriEnv", ""), "").strip(), os.getenv(profile.get("databaseEnv", ""), "").strip()


def _safe_mongo_error(error: Exception, uri: str = "") -> str:
    detail = str(error) or error.__class__.__name__
    if uri:
        safe_uri = re.sub(r"(mongodb(?:\+srv)?://)[^@/\s]+@", r"\1***@", uri)
        detail = detail.replace(uri, safe_uri)
    return re.sub(r"mongodb(?:\+srv)?://[^@/\s]+@", "mongodb://***@", detail)


def _check_mongo_connection(uri: str, database: str = "") -> str:
    uri = uri.strip()
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise HTTPException(status_code=422, detail="MongoDB URL mongodb:// ya mongodb+srv:// se shuru honi chahiye")
    selected_database = database.strip() or _mongo_database_from_uri(uri)
    if not selected_database:
        raise HTTPException(status_code=422, detail="URL mein database name nahi mila; Database name bhi dein")
    client: MongoClient | None = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client[selected_database].command("ping")
        return selected_database
    except Exception as exc:
        detail = _safe_mongo_error(exc, uri)
        LOG.warning("MongoDB connection check failed: %s", detail)
        raise HTTPException(status_code=503, detail=f"MongoDB connection fail hui: {detail}") from None
    finally:
        if client is not None:
            client.close()


def normalize_folder_path(value: Any) -> str:
    folder = str(value or "General").strip().replace("\\", "/").strip("/") or "General"
    parts = folder.split("/")
    if len(folder) > 240 or any(
        part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}", part)
        for part in parts
    ):
        raise ValueError("Folder name mein letters, numbers, space, underscore aur hyphen use karein")
    return "/".join(parts)


def report_definition_path(report_id: str, folder: str) -> Path:
    return report_definitions_directory().joinpath(*folder.split("/"), f"{report_id}.json")


def list_saved_folders() -> list[str]:
    found = {"General"}
    path = folders_file()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        items = raw.get("folders", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            raise ValueError("folders.json mein folders array honi chahiye")
        for item in items:
            normalized = normalize_folder_path(item)
            pieces = normalized.split("/")
            found.update("/".join(pieces[:index]) for index in range(1, len(pieces) + 1))
    directory = report_definitions_directory()
    if directory.exists():
        for definition in directory.rglob("*.json"):
            relative_parent = definition.parent.relative_to(directory)
            if str(relative_parent) == ".":
                continue
            pieces = list(relative_parent.parts)
            found.update("/".join(pieces[:index]) for index in range(1, len(pieces) + 1))
    return sorted(found, key=lambda item: (item.casefold(), item))


def initialize_data_directory() -> None:
    """Copy bundled starter definitions into a new persistent data mount once."""
    target = data_directory()
    bundled = Path(__file__).resolve().parent / "data"
    target_reports = target / "reports"
    target_reports.mkdir(parents=True, exist_ok=True)
    # Migrate existing flat definitions before copying starter definitions, so
    # a previously edited report takes precedence over the bundled starter.
    for legacy_path in list(target_reports.glob("*.json")):
        try:
            payload = json.loads(legacy_path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                continue
            report_id = str(payload.get("id", "")).strip()
            folder = normalize_folder_path(payload.get("folder", "General"))
            if not REPORT_ID_PATTERN.fullmatch(report_id):
                continue
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        payload["folder"] = folder
        destination = report_definition_path(report_id, folder)
        if destination != legacy_path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                _write_json_atomically(destination, payload)
            legacy_path.unlink()
        else:
            _write_json_atomically(legacy_path, payload)
    bundled_reports = bundled / "reports"
    if bundled_reports.exists():
        for source in bundled_reports.rglob("*.json"):
            destination = target_reports / source.relative_to(bundled_reports)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(source, destination)
    source_connections = bundled / "connections.json"
    destination_connections = target / "connections.json"
    if source_connections.exists() and not destination_connections.exists():
        shutil.copy2(source_connections, destination_connections)


def _write_json_atomically(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_local_env_file() -> None:
    """Load simple KEY=VALUE settings without requiring python-dotenv."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    kind: str = "string"  # date, string, integer, boolean
    label: str = ""
    description: str = ""
    required: bool = False
    default: Any = None
    allowed_values: tuple[Any, ...] = ()
    max_length: int = 200


@dataclass(frozen=True)
class ReportDefinition:
    report_id: str
    title: str
    collection: str
    parameters: tuple[ParameterSpec, ...]
    columns: tuple[str, ...]
    pipeline_builder: Callable[[dict[str, Any]], list[dict[str, Any]]]
    connection_id: str = "default"
    editor_config: dict[str, Any] | None = None


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    mongo_database: str
    audit_database: str
    audit_collection: str
    max_time_ms: int
    max_rows: int
    allow_disk_use: bool
    audit_to_mongodb: bool
    audit_required: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_local_env_file()
        uri = os.getenv("MONGODB_URI", "")
        database = os.getenv("MONGODB_DATABASE", "")
        if not uri or not database:
            # Permit importing the app without configured secrets; first DB request returns a clear error.
            LOG.warning("MONGODB_URI/MONGODB_DATABASE are not configured; report execution is disabled")
        return cls(
            mongo_uri=uri,
            mongo_database=database,
            audit_database=os.getenv("AUDIT_DATABASE", database),
            audit_collection=os.getenv("AUDIT_COLLECTION", "report_execution_audit"),
            max_time_ms=int(os.getenv("REPORT_MAX_TIME_MS", "30000")),
            max_rows=int(os.getenv("REPORT_MAX_ROWS", "10000")),
            allow_disk_use=os.getenv("REPORT_ALLOW_DISK_USE", "false").lower() == "true",
            audit_to_mongodb=os.getenv("AUDIT_TO_MONGODB", "false").lower() == "true",
            audit_required=os.getenv("AUDIT_REQUIRED", "false").lower() == "true",
        )


def _date_boundary(value: date, boundary: str) -> datetime:
    end_time = daytime.max if boundary == "end" else daytime.min
    return datetime.combine(value, end_time, tzinfo=timezone.utc)


def _resolve_pipeline_parameters(node: Any, params: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if set(node) == {"$literalDate"}:
            raw_date = node["$literalDate"]
            if not isinstance(raw_date, str):
                raise ValueError("ISODate literal string honi chahiye")
            try:
                parsed_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError(f"ISODate value valid nahi: {raw_date}") from None
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            return parsed_date.astimezone(timezone.utc)
        if set(node) == {"$literalObjectId"}:
            try:
                return ObjectId(str(node["$literalObjectId"]))
            except Exception:
                raise ValueError("ObjectId literal valid nahi") from None
        if set(node) == {"$literalInt64"}:
            try:
                return Int64(int(node["$literalInt64"]))
            except (ValueError, TypeError, OverflowError):
                raise ValueError("NumberLong literal valid nahi") from None
        if set(node) == {"$paramIn"}:
            # Optional multi-value filter: comma separated values -> {"$in": [...]}, empty -> match everything.
            raw_value = params.get(str(node["$paramIn"]))
            items = [
                piece.strip().strip("'\"").strip()
                for piece in str(raw_value if raw_value is not None else "").split(",")
            ]
            items = [item for item in items if item]
            if len(items) > 200 or any(len(item) > 120 for item in items):
                raise ValueError(f"{node['$paramIn']}: maximum 200 values (har value 120 characters tak)")
            return {"$in": items} if items else {"$nin": []}
        if "$param" in node and set(node).issubset({"$param", "boundary"}):
            name = str(node["$param"])
            if name not in params:
                raise ValueError(f"Pipeline parameter define nahi hai: {name}")
            value = params[name]
            if isinstance(value, date) and not isinstance(value, datetime):
                boundary = str(node.get("boundary", "start"))
                if boundary not in {"start", "end"}:
                    raise ValueError(f"{name} ke boundary ki value start ya end honi chahiye")
                return _date_boundary(value, boundary)
            return value
        return {key: _resolve_pipeline_parameters(value, params) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_pipeline_parameters(item, params) for item in node]
    return node


class MongoReadAccessError(RuntimeError):
    """The current MongoDB identity cannot read a collection needed by a report."""


def _path_values(value: Any, path: str) -> list[Any]:
    """Resolve a dotted Mongo field path, including paths through arrays."""
    parts = path.split(".") if path else []

    def walk(current: Any, remaining: list[str]) -> list[Any]:
        if not remaining:
            return [current]
        if isinstance(current, list):
            found: list[Any] = []
            for item in current:
                found.extend(walk(item, remaining))
            return found
        if not isinstance(current, dict) or remaining[0] not in current:
            return []
        return walk(current[remaining[0]], remaining[1:])

    return walk(value, parts)


def resolve_collection(template: str, parameters: dict[str, Any]) -> str:
    """Fill $P{name} in a collection template; values are strictly charset-checked."""
    def fill(match: re.Match) -> str:
        value = str(parameters.get(match.group(1), "")).strip()
        if not COLLECTION_PARAM_VALUE.fullmatch(value):
            raise ValueError(f"Parameter {match.group(1)} collection naam ke liye sahi nahi (sirf letters, numbers, _ -)")
        return value
    return re.sub(r"\$P\{([A-Za-z_][A-Za-z0-9_]*)\}", fill, template)


def _row_value(row: dict[str, Any], key: str) -> Any:
    """Column value; dotted names like 'Fields.HBLIPF194' reach into nested documents."""
    if key in row:
        return row[key]
    if "." in key:
        return _path_value(row, key)
    return None


def _path_value(value: Any, path: str) -> Any:
    parts = path.split(".") if path else []
    missing = object()

    def walk(current: Any, remaining: list[str]) -> Any:
        if not remaining:
            return current
        if isinstance(current, list):
            found = [walk(item, remaining) for item in current]
            return [item for item in found if item is not missing]
        if not isinstance(current, dict) or remaining[0] not in current:
            return missing
        return walk(current[remaining[0]], remaining[1:])

    result = walk(value, parts)
    return None if result is missing else result


def _flatten_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        flattened: list[Any] = []
        for item in value:
            flattened.extend(_flatten_values(item))
        return flattened
    return [value]


def _mongo_date(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, daytime.min, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise ValueError("$toDate fallback mein numeric date valid nahi") from None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"$toDate fallback ISO date parse nahi kar saka: {value}") from None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    raise ValueError(f"$toDate fallback is value type ko support nahi karta: {type(value).__name__}")


def _mongo_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, list) and not isinstance(expected, list):
        return any(_mongo_equal(item, expected) for item in actual)
    if isinstance(actual, datetime) and isinstance(expected, datetime):
        left = actual if actual.tzinfo else actual.replace(tzinfo=timezone.utc)
        right = expected if expected.tzinfo else expected.replace(tzinfo=timezone.utc)
        return left == right
    return actual == expected


def _mongo_compare(actual: Any, expected: Any, operator: str) -> bool:
    if operator in {"$eq", "$ne", "$in", "$nin"}:
        if operator == "$eq":
            return _mongo_equal(actual, expected)
        if operator == "$ne":
            return not _mongo_equal(actual, expected)
        candidates = expected if isinstance(expected, list) else [expected]
        matched = any(_mongo_equal(actual, candidate) for candidate in candidates)
        return matched if operator == "$in" else not matched
    candidates = actual if isinstance(actual, list) else [actual]
    for candidate in candidates:
        try:
            if operator == "$gte" and candidate >= expected:
                return True
            if operator == "$gt" and candidate > expected:
                return True
            if operator == "$lte" and candidate <= expected:
                return True
            if operator == "$lt" and candidate < expected:
                return True
        except TypeError:
            continue
    return False


def _matches_field(actual: Any, condition: Any, *, exists: bool = True) -> bool:
    if not isinstance(condition, dict) or not any(str(key).startswith("$") for key in condition):
        return _mongo_equal(actual, condition)
    for operator, expected in condition.items():
        if operator == "$exists":
            if exists != bool(expected):
                return False
            continue
        if operator not in {"$eq", "$ne", "$in", "$nin", "$gte", "$gt", "$lte", "$lt"}:
            raise ValueError(f"Read fallback mein match operator support nahi: {operator}")
        if not _mongo_compare(actual, expected, operator):
            return False
    return True


def _matches_document(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$and":
            if not all(_matches_document(document, item) for item in condition):
                return False
        elif key == "$or":
            if not any(_matches_document(document, item) for item in condition):
                return False
        elif key == "$nor":
            if any(_matches_document(document, item) for item in condition):
                return False
        else:
            actual_values = _path_values(document, key)
            if not actual_values:
                if not _matches_field(None, condition, exists=False):
                    return False
            elif not any(_matches_field(value, condition) for value in actual_values):
                return False
    return True


def _evaluate_expression(document: dict[str, Any], expression: Any) -> Any:
    if isinstance(expression, str) and expression.startswith("$"):
        return _path_value(document, expression[1:])
    if isinstance(expression, list):
        return [_evaluate_expression(document, item) for item in expression]
    if isinstance(expression, dict):
        if len(expression) == 1 and "$toDate" in expression:
            return _mongo_date(_evaluate_expression(document, expression["$toDate"]))
        if len(expression) == 1 and "$arrayElemAt" in expression:
            operands = expression["$arrayElemAt"]
            if not isinstance(operands, list) or len(operands) != 2:
                raise ValueError("$arrayElemAt fallback ko do operands chahiye")
            values = _evaluate_expression(document, operands[0])
            index = _evaluate_expression(document, operands[1])
            if not isinstance(values, list):
                return None
            try:
                return values[int(index)]
            except (IndexError, TypeError, ValueError):
                return None
        if len(expression) == 1 and "$ifNull" in expression:
            choices = expression["$ifNull"]
            if not isinstance(choices, list):
                raise ValueError("$ifNull fallback ko array chahiye")
            for choice in choices:
                value = _evaluate_expression(document, choice)
                if value is not None:
                    return value
            return None
        return {key: _evaluate_expression(document, value) for key, value in expression.items()}
    return expression


def _lookup_key(value: Any) -> Any:
    try:
        hash(value)
        return (type(value).__name__, value)
    except TypeError:
        return (type(value).__name__, json.dumps(value, sort_keys=True, default=str))


def _run_read_pipeline(
    collection: Any,
    pipeline: list[dict[str, Any]],
    *,
    max_time_ms: int,
    max_rows: int,
) -> list[dict[str, Any]]:
    """Run the report's safe aggregation subset using collection find/read access."""
    if any(not isinstance(stage, dict) or len(stage) != 1 for stage in pipeline):
        raise ValueError("Read fallback mein har aggregation stage ka single operator hona chahiye")
    allowed = {"$match", "$addFields", "$lookup", "$project", "$limit", "$sort", "$skip"}
    unsupported = [next(iter(stage)) for stage in pipeline if next(iter(stage)) not in allowed]
    if unsupported:
        raise ValueError("MongoDB aggregate permission nahi; read fallback in stages ko support nahi karta: " + ", ".join(unsupported))

    # Push only an initial $match to MongoDB find(); keep it in the local pipeline
    # too so the fallback remains correct for the supported Mongo match semantics.
    initial_filter: dict[str, Any] = {}
    if pipeline and "$match" in pipeline[0]:
        initial_filter = pipeline[0]["$match"]
    scan_limit = min(max(max_rows * 10, 20_000), 100_000)
    try:
        cursor = collection.find(initial_filter, max_time_ms=max_time_ms).limit(scan_limit + 1)
        documents = list(cursor)
    except PyMongoError as read_error:
        if getattr(read_error, "code", None) == 13:
            raise MongoReadAccessError(
                f"MongoDB ne report collection '{collection.database.name}.{collection.name}' par "
                "find/read ko is connection se reject kiya."
            ) from None
        raise
    if len(documents) > scan_limit:
        raise ValueError(
            f"Aggregate permission nahi aur read fallback {scan_limit:,} source documents ki safety limit tak pohanch gaya. "
            "Pipeline ko pehle $match stages se zyada narrow karein."
        )

    for stage in pipeline:
        operator, specification = next(iter(stage.items()))
        if operator == "$match":
            documents = [doc for doc in documents if _matches_document(doc, specification)]
        elif operator == "$addFields":
            updated: list[dict[str, Any]] = []
            for document in documents:
                item = copy.deepcopy(document)
                for field, expression in specification.items():
                    item[field] = _evaluate_expression(item, expression)
                updated.append(item)
            documents = updated
        elif operator == "$lookup":
            required = {"from", "localField", "foreignField", "as"}
            if not required.issubset(specification):
                raise ValueError("Read fallback sirf localField/foreignField $lookup ko support karta hai")
            local_field = str(specification["localField"])
            foreign_field = str(specification["foreignField"])
            foreign_collection = collection.database[str(specification["from"])]
            local_values: list[Any] = []
            seen: set[Any] = set()
            for document in documents:
                for value in _flatten_values(_path_value(document, local_field)):
                    key = _lookup_key(value)
                    if value is not None and key not in seen:
                        seen.add(key)
                        local_values.append(value)
            lookup_map: dict[Any, list[dict[str, Any]]] = {}
            # Keep each $in query modest so larger reports do not create an oversized command.
            for offset in range(0, len(local_values), 500):
                value_batch = local_values[offset : offset + 500]
                try:
                    foreign_cursor = foreign_collection.find(
                        {foreign_field: {"$in": value_batch}}, max_time_ms=max_time_ms
                    )
                    for foreign_document in foreign_cursor:
                        for value in _flatten_values(_path_value(foreign_document, foreign_field)):
                            lookup_map.setdefault(_lookup_key(value), []).append(foreign_document)
                except PyMongoError as read_error:
                    if getattr(read_error, "code", None) == 13:
                        raise MongoReadAccessError(
                            f"MongoDB ne $lookup collection '{foreign_collection.database.name}.{foreign_collection.name}' "
                            "par find/read ko is connection se reject kiya."
                        ) from None
                    raise
            for document in documents:
                values = _flatten_values(_path_value(document, local_field))
                joined: list[dict[str, Any]] = []
                for value in values:
                    joined.extend(lookup_map.get(_lookup_key(value), []))
                document[str(specification["as"])] = joined
        elif operator == "$project":
            include_mode = any(value in (1, 1.0, True) for value in specification.values())
            projected: list[dict[str, Any]] = []
            for document in documents:
                item: dict[str, Any] = {}
                if include_mode and specification.get("_id", 1) not in (0, 0.0, False):
                    if "_id" in document:
                        item["_id"] = document["_id"]
                for field, expression in specification.items():
                    is_scalar_flag = not isinstance(expression, (dict, list))
                    if is_scalar_flag and expression in (0, 0.0, False):
                        continue
                    if is_scalar_flag and expression in (1, 1.0, True):
                        value = _path_value(document, field)
                    else:
                        value = _evaluate_expression(document, expression)
                    if value is not None or (isinstance(expression, (dict, list)) and value is None):
                        item[field] = value
                projected.append(item)
            documents = projected
        elif operator == "$limit":
            documents = documents[: max(0, int(specification))]
        elif operator == "$skip":
            documents = documents[max(0, int(specification)) :]
        elif operator == "$sort":
            for field, direction in reversed(list(specification.items())):
                documents.sort(
                    key=lambda doc: (_path_value(doc, field) is None, _path_value(doc, field)),
                    reverse=float(direction) < 0,
                )
    return documents


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
        LOG.warning("MongoDB denied aggregate (code 13); trying bounded find/read fallback")
        try:
            rows = _run_read_pipeline(
                collection, pipeline, max_time_ms=max_time_ms, max_rows=max_rows
            )
            LOG.info("MongoDB report pipeline completed with bounded find/read fallback")
            return rows
        except PyMongoError as read_error:
            if getattr(read_error, "code", None) == 13:
                raise MongoReadAccessError(
                    "MongoDB aggregate aur find dono current connection se reject hue. "
                    "Is account ke maujooda access ke andar report ka data read nahi ho sakta."
                ) from None
            raise


def _strip_query_comments(source: str, python: bool = False) -> str:
    """Remove JS/JSON comments without altering quoted strings or line positions."""
    if python:
        return source  # Python comments are parsed natively by ast.
    output = list(source)
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'", '`'}:
            quote = char
            index += 1
            continue
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            for cursor in range(index, end):
                output[cursor] = " "
            index = end
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise ValueError("Query comment ka /* hai, lekin */ band nahi mila")
            for cursor in range(index, end + 2):
                if output[cursor] not in "\r\n":
                    output[cursor] = " "
            index = end + 2
            continue
        index += 1
    return "".join(output)


def _extract_literal_array(source: str, start: int) -> tuple[str, int]:
    if start >= len(source) or source[start] != "[":
        raise ValueError("Pipeline literal ka start [ se hona chahiye")
    stack = ["]"]
    quote: str | None = None
    escaped = False
    for index in range(start + 1, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char == "[":
            stack.append("]")
        elif char == "{":
            stack.append("}")
        elif char in {"]", "}"}:
            if not stack or char != stack.pop():
                raise ValueError("Pipeline mein brackets ka pair nahi mil raha")
            if not stack:
                return source[start:index + 1], index + 1
    raise ValueError("Pipeline array ka closing ] nahi mila")


def _parse_mongosh_literal(source: str) -> Any:
    """Parse a safe Mongo Shell object/array literal subset without evaluating JS."""
    token_pattern = re.compile(
        r"\s*(?:(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
        r"|(?P<parameter>\$P!?\{[A-Za-z_][A-Za-z0-9_]*\})"
        r"|(?P<number>-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
        r"|(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)"
        r"|(?P<punct>[{}\[\]:,()]))"
    )
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(source):
        if not source[position:].strip():
            break
        match = token_pattern.match(source, position)
        if not match:
            snippet = source[position:position + 20].splitlines()[0]
            raise ValueError(f"Mongo Shell literal mein unsupported syntax qareeb: {snippet!r}")
        kind = next(key for key, value in match.groupdict().items() if value is not None)
        tokens.append((kind, match.group(kind)))
        position = match.end()
    cursor = 0

    def peek(value: str | None = None) -> bool:
        return cursor < len(tokens) and (value is None or tokens[cursor][1] == value)

    def take(value: str | None = None) -> tuple[str, str]:
        nonlocal cursor
        if not peek(value):
            expected = f" {value!r}" if value is not None else ""
            raise ValueError(f"Mongo Shell literal mein token{expected} expected hai")
        token = tokens[cursor]
        cursor += 1
        return token

    def parse_value() -> Any:
        kind, value = take()
        if value == "[":
            result = []
            while not peek("]"):
                if cursor >= len(tokens):
                    raise ValueError("Mongo Shell array ka closing ] nahi mila")
                result.append(parse_value())
                if peek(","):
                    take(",")
                    if peek("]"):
                        break
                elif not peek("]"):
                    raise ValueError("Mongo Shell array values ke darmiyan comma expected hai")
            take("]")
            return result
        if value == "{":
            result: dict[str, Any] = {}
            while not peek("}"):
                if cursor >= len(tokens):
                    raise ValueError("Mongo Shell object ka closing } nahi mila")
                key_kind, raw_key = take()
                if key_kind == "string":
                    key = ast.literal_eval(raw_key)
                elif key_kind == "identifier":
                    key = raw_key
                else:
                    raise ValueError("Mongo Shell object key quoted text ya simple naam hona chahiye")
                take(":")
                result[str(key)] = parse_value()
                if peek(","):
                    take(",")
                    if peek("}"):
                        break
                elif not peek("}"):
                    raise ValueError("Mongo Shell object properties ke darmiyan comma expected hai")
            take("}")
            return result
        if kind == "string":
            return ast.literal_eval(value)
        if kind == "parameter":
            name = re.fullmatch(r"\$P!?\{([A-Za-z_][A-Za-z0-9_]*)\}", value).group(1)
            # Jasper's $P! raw substitution is deliberately reduced to a typed scalar.
            return {"$param": name}
        if kind == "number":
            if any(char in value for char in ".eE"):
                return float(value)
            return int(value)
        if kind == "identifier":
            if value in {"true", "false", "null"}:
                return {"true": True, "false": False, "null": None}[value]
            if peek("("):
                take("(")
                argument = parse_value()
                take(")")
                if value in {"NumberInt", "NumberLong"} and isinstance(argument, (int, float, str)) and not isinstance(argument, bool):
                    try:
                        integer = int(argument)
                        if isinstance(argument, float) and integer != argument:
                            raise ValueError
                        limit = 2**31 if value == "NumberInt" else 2**63
                        if not -limit <= integer < limit:
                            raise ValueError
                        return {"$literalInt64": integer} if value == "NumberLong" else integer
                    except (ValueError, OverflowError):
                        raise ValueError(f"{value} ko valid integer chahiye") from None
                if value in {"ISODate", "Date"} and isinstance(argument, str):
                    return {"$literalDate": argument}
                if value == "ObjectId" and isinstance(argument, str) and re.fullmatch(r"[0-9a-fA-F]{24}", argument):
                    return {"$literalObjectId": argument}
                raise ValueError(f"Mongo Shell constructor {value}(...) supported literal list mein nahi")
            raise ValueError(f"Mongo Shell value {value!r} supported literal list mein nahi")
        raise ValueError(f"Mongo Shell literal token {value!r} supported nahi")

    result = parse_value()
    if cursor != len(tokens):
        raise ValueError(f"Mongo Shell literal ke baad extra token {tokens[cursor][1]!r}")
    return result


def _replace_jasper_parameters(source: str) -> str:
    """Translate unquoted Jasper parameter references into safe typed markers."""
    output: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    pattern = re.compile(r"\$P!?\{([A-Za-z_][A-Za-z0-9_]*)\}")
    while index < len(source):
        char = source[index]
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            output.append(char)
            index += 1
            continue
        match = pattern.match(source, index)
        if match:
            output.append(json.dumps({"$param": match.group(1)}))
            index = match.end()
            continue
        output.append(char)
        index += 1
    return "".join(output)


def parse_query_source(language: str, source: str) -> list[dict[str, Any]]:
    """Convert supported data-only editor styles to a Mongo aggregation pipeline."""
    language = str(language or "aggregation-json").lower()
    if language not in QUERY_LANGUAGES:
        raise ValueError("Query mode aggregation-json, mongosh, javascript ya python ho sakta hai")
    if not isinstance(source, str) or len(source) > 2_000_000:
        raise ValueError("Query text 2 MB se chhota hona chahiye")
    if language == "python":
        try:
            module = ast.parse(_replace_jasper_parameters(source), mode="exec")
            if len(module.body) != 1:
                raise ValueError("Python mode mein sirf pipeline = [...] literal assignment dein")
            statement = module.body[0]
            if isinstance(statement, ast.Assign):
                valid_target = len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id == "pipeline"
                value_node = statement.value
            elif isinstance(statement, ast.AnnAssign):
                valid_target = isinstance(statement.target, ast.Name) and statement.target.id == "pipeline"
                value_node = statement.value
            else:
                valid_target = False
                value_node = None
            if not valid_target or value_node is None:
                raise ValueError("Python mode mein sirf pipeline = [...] literal assignment dein; code execute nahi hota")
            pipeline = ast.literal_eval(value_node)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"Python pipeline read nahi hui: {exc}") from None
    else:
        uncommented = _strip_query_comments(source)
        if language == "aggregation-json":
            literal = _replace_jasper_parameters(uncommented.strip())
        else:
            if language == "mongosh":
                raw_source = uncommented.strip()
                match = None if raw_source.startswith("[") else re.match(
                    r"\s*db(?:(?:\.getCollection\s*\(\s*(?:\"[^\"]*\"|'[^']*')\s*\))|(?:\.[A-Za-z_][A-Za-z0-9_]*))\.aggregate\s*\(",
                    uncommented,
                )
            else:
                match = re.match(r"\s*(?:(?:const|let|var)\s+)?pipeline\s*=\s*", uncommented)
            if language == "mongosh" and uncommented.strip().startswith("["):
                literal = uncommented.strip()
                remainder = ""
            elif not match:
                expected = "db.collection.aggregate([...])" if language == "mongosh" else "pipeline = [...]"
                raise ValueError(f"{language} mode mein {expected} literal format dein")
            else:
                array_start = uncommented.find("[", match.end())
                if array_start < 0:
                    raise ValueError("Pipeline literal ka [ nahi mila")
                literal, array_end = _extract_literal_array(uncommented, array_start)
                remainder = uncommented[array_end:].strip()
                valid_remainder = re.fullmatch(r"\)\s*;?", remainder) if language == "mongosh" else re.fullmatch(r";?", remainder)
                if not valid_remainder:
                    raise ValueError("Pipeline literal ke baad extra executable code allowed nahi")
        if language in {"mongosh", "javascript"}:
            pipeline = _parse_mongosh_literal(literal)
        else:
            try:
                pipeline = json.loads(literal)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Pipeline JSON line {exc.lineno}, column {exc.colno}: {exc.msg}. Mongo Shell syntax ke liye Query style 'Mongo Shell' chunein.") from None
    if not isinstance(pipeline, list) or not pipeline or not all(isinstance(stage, dict) for stage in pipeline):
        raise ValueError("Pipeline non-empty array of stage objects honi chahiye")
    _infer_parameter_date_boundaries(pipeline)
    return pipeline


def _infer_parameter_date_boundaries(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"$gte", "$gt", "$lte", "$lt"} and isinstance(value, dict) and set(value) == {"$param"}:
                value["boundary"] = "end" if key in {"$lte", "$lt"} else "start"
            _infer_parameter_date_boundaries(value)
    elif isinstance(node, list):
        for value in node:
            _infer_parameter_date_boundaries(value)


def _validate_pipeline_parameter_names(pipeline: Any, allowed_names: set[str]) -> None:
    if isinstance(pipeline, dict):
        if set(pipeline) == {"$paramIn"}:
            if str(pipeline["$paramIn"]) not in allowed_names:
                raise ValueError(f"Query parameter {pipeline['$paramIn']} ko pehle report questions mein define karein")
            return
        if "$param" in pipeline and set(pipeline).issubset({"$param", "boundary"}):
            name = str(pipeline["$param"])
            if name not in allowed_names:
                raise ValueError(f"Query parameter {name} ko pehle report questions mein define karein")
            if "boundary" in pipeline and pipeline["boundary"] not in {"start", "end"}:
                raise ValueError(f"{name} parameter ka boundary 'start' ya 'end' hona chahiye")
            return
        for value in pipeline.values():
            _validate_pipeline_parameter_names(value, allowed_names)
    elif isinstance(pipeline, list):
        for value in pipeline:
            _validate_pipeline_parameter_names(value, allowed_names)


def _validate_read_only_pipeline(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"$out", "$merge"}:
                raise ValueError("Reporting pipeline read-only honi chahiye; $out aur $merge allowed nahi")
            if key in {"$function", "$accumulator", "$where"}:
                raise ValueError("Pipeline mein JavaScript execute karne wale $function, $accumulator aur $where allowed nahi")
            _validate_read_only_pipeline(value)
    elif isinstance(node, list):
        for value in node:
            _validate_read_only_pipeline(value)


def _infer_pipeline_columns(pipeline: list[dict[str, Any]]) -> list[str]:
    """Infer the result fields from the last stage that defines an output shape."""
    for stage in reversed(pipeline):
        projection = stage.get("$project")
        if isinstance(projection, dict):
            return [
                key for key, value in projection.items()
                if not (key == "_id" and value in (0, False))
            ]
        group = stage.get("$group")
        if isinstance(group, dict):
            return list(group.keys())
        count_field = stage.get("$count")
        if isinstance(count_field, str) and count_field:
            return [count_field]
    return []


def _normalize_canvas_position(raw: Any, default: dict[str, float]) -> dict[str, float]:
    if raw is None:
        return dict(default)
    if not isinstance(raw, dict):
        raise ValueError("Canvas object position JSON object hona chahiye")
    result: dict[str, float] = {}
    for key, fallback in default.items():
        value = raw.get(key, fallback)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Canvas position {key} valid number hona chahiye")
        result[key] = round(float(value), 2)
    if result["x"] < 0 or result["y"] < 0 or result["w"] < 24 or result["h"] < 20:
        raise ValueError("Canvas object page ke andar ho aur kam az kam 24x20 pixels ka ho")
    if result["x"] + result["w"] > 794 or result["y"] + result["h"] > 1123:
        raise ValueError("Canvas object A4 page ke bahar nahi ja sakta")
    return result


def _dynamic_report(payload: dict[str, Any], source_path: Path | None = None) -> ReportDefinition:
    report_id = str(payload.get("id", "")).strip()
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        raise ValueError("Report ID mein sirf letters, numbers, underscore aur hyphen use karein")
    title = str(payload.get("title", report_id)).strip() or report_id
    collection = str(payload.get("collection", "")).strip()
    connection_id = str(payload.get("connectionId", "default")).strip()
    folder = normalize_folder_path(payload.get("folder", "General"))
    if not collection:
        raise ValueError("MongoDB collection ka naam lazmi hai")
    if not COLLECTION_TEMPLATE.fullmatch(collection) or collection.lower().startswith("system."):
        raise ValueError("Collection naam mein sirf letters, numbers, _ - . aur $P{parameter} allowed hain")
    if not CONNECTION_ID_PATTERN.fullmatch(connection_id):
        raise ValueError("Connection ID format ghalat hai")

    raw_parameters = payload.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raise ValueError("parameters JSON array honi chahiye")
    parameters: list[ParameterSpec] = []
    seen_names: set[str] = set()
    for raw in raw_parameters:
        if not isinstance(raw, dict):
            raise ValueError("Har parameter ek JSON object hona chahiye")
        name = str(raw.get("name", "")).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            raise ValueError(f"Parameter name format ghalat hai: {name}")
        if name in seen_names:
            raise ValueError(f"Duplicate parameter name: {name}")
        seen_names.add(name)
        kind = str(raw.get("type", raw.get("kind", "string"))).lower()
        if kind not in {"date", "string", "integer", "number", "boolean"}:
            raise ValueError(f"{name} ka type date/string/integer/number/boolean hona chahiye")
        allowed = raw.get("allowedValues", [])
        if not isinstance(allowed, list):
            raise ValueError(f"{name}.allowedValues JSON array honi chahiye")
        parameters.append(ParameterSpec(
            name=name,
            kind=kind,
            label=str(raw.get("label", name)),
            description=str(raw.get("description", "")),
            required=bool(raw.get("required", False)),
            default=raw.get("default"),
            allowed_values=tuple(allowed),
            max_length=min(max(int(raw.get("maxLength", 200)), 1), 2000),
        ))

    query_language = str(payload.get("queryLanguage", "aggregation-json")).lower()
    query_source = payload.get("querySource")
    if query_language not in QUERY_LANGUAGES:
        raise ValueError("queryLanguage aggregation-json, mongosh, javascript ya python hona chahiye")
    if "querySource" in payload and not isinstance(query_source, str):
        raise ValueError("querySource string hona chahiye")
    pipeline = parse_query_source(query_language, query_source) if isinstance(query_source, str) else payload.get("pipeline")
    if not isinstance(pipeline, list) or not pipeline or not all(isinstance(stage, dict) for stage in pipeline):
        raise ValueError("pipeline non-empty JSON array of stage objects honi chahiye")
    _validate_pipeline_parameter_names(pipeline, seen_names)
    _validate_read_only_pipeline(pipeline)
    date_range = payload.get("dateRange")
    if date_range is not None:
        if not isinstance(date_range, dict):
            raise ValueError("dateRange JSON object hona chahiye")
        for key in ("field", "startParameter", "endParameter"):
            if not str(date_range.get(key, "")).strip():
                raise ValueError(f"dateRange.{key} lazmi hai")
        names = {spec.name: spec for spec in parameters}
        if names.get(date_range["startParameter"], ParameterSpec("_missing")).kind != "date":
            raise ValueError("dateRange ka startParameter date type hona chahiye")
        if names.get(date_range["endParameter"], ParameterSpec("_missing")).kind != "date":
            raise ValueError("dateRange ka endParameter date type hona chahiye")

    columns = payload.get("columns") or _infer_pipeline_columns(pipeline)
    if not isinstance(columns, list) or not columns or not all(isinstance(column, str) for column in columns):
        raise ValueError("$project, $group ya $count stage se output columns nahi milay; Output columns mein fields ki list dein")
    formats = payload.get("formats", sorted(OUTPUT_FORMATS))
    if not isinstance(formats, list) or not formats or any(not isinstance(value, str) or value.lower() not in OUTPUT_FORMATS for value in formats):
        raise ValueError("Report formats csv, xlsx ya pdf mein se ek ya zyada honay chahiye")
    formats = list(dict.fromkeys(value.lower() for value in formats))
    default_format = str(payload.get("defaultFormat", formats[0])).lower()
    if default_format not in formats:
        raise ValueError("Default format, enabled report formats mein se hona chahiye")
    raw_layout = payload.get("layout", {})
    if not isinstance(raw_layout, dict):
        raise ValueError("layout JSON object hona chahiye")
    raw_labels = raw_layout.get("columnLabels", {})
    if not isinstance(raw_labels, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in raw_labels.items()):
        raise ValueError("layout.columnLabels field names aur labels ka JSON object hona chahiye")
    layout = {
        "title": str(raw_layout.get("title", title))[:200],
        "intro": str(raw_layout.get("intro", payload.get("description", "")))[:2000],
        "showTitle": raw_layout.get("showTitle", True) is True,
        "showIntro": raw_layout.get("showIntro", True) is True,
        "columnLabels": {column: (str(raw_labels.get(column, column)).strip() or column)[:200] for column in columns},
    }
    paragraphs = raw_layout.get("paragraphs", [])
    if not isinstance(paragraphs, list) or len(paragraphs) > 20 or any(not isinstance(text, str) or len(text) > 2000 for text in paragraphs):
        raise ValueError("Report canvas mein maximum 20 text blocks aur har block mein 2000 characters tak rakhein")
    layout["paragraphs"] = paragraphs
    raw_positions = raw_layout.get("positions", {})
    if not isinstance(raw_positions, dict):
        raise ValueError("layout.positions JSON object hona chahiye")
    default_positions = {
        "title": {"x": 64, "y": 70, "w": 666, "h": 54},
        "intro": {"x": 64, "y": 132, "w": 666, "h": 62},
        "image": {"x": 64, "y": 210, "w": 280, "h": 170},
        "table": {"x": 64, "y": 650, "w": 666, "h": 390},
    }
    for index in range(len(paragraphs)):
        default_positions[f"paragraph-{index}"] = {"x": 64, "y": 220 + index * 96, "w": 666, "h": 78}
    layout["positions"] = {
        key: _normalize_canvas_position(raw_positions.get(key), default)
        for key, default in default_positions.items()
    }
    parameter_names = {item.name for item in parameters}
    raw_parameter_fields = raw_layout.get("parameterFields", [])
    if not isinstance(raw_parameter_fields, list) or len(raw_parameter_fields) > 100:
        raise ValueError("Canvas mein maximum 100 URL value fields rakh sakte hain")
    parameter_fields: list[dict[str, Any]] = []
    seen_field_ids: set[str] = set()
    for index, field in enumerate(raw_parameter_fields):
        if not isinstance(field, dict):
            raise ValueError("Har canvas URL value field JSON object hona chahiye")
        field_id = str(field.get("id", f"field-{index + 1}"))
        name = str(field.get("name", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", field_id) or field_id in seen_field_ids:
            raise ValueError("Canvas URL value field ID unique aur valid hona chahiye")
        if name not in parameter_names:
            raise ValueError(f"Canvas URL value field ka question defined nahi: {name}")
        seen_field_ids.add(field_id)
        field_position = _normalize_canvas_position(
            field.get("position"), {"x": 90, "y": 245 + index * 42, "w": 210, "h": 34}
        )
        parameter_fields.append({"id": field_id, "name": name, "position": field_position})
    layout["parameterFields"] = parameter_fields
    image_data = raw_layout.get("imageDataUrl", "")
    if image_data:
        image_match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", image_data) if isinstance(image_data, str) else None
        if not image_match or len(image_data) > 3_000_000:
            raise ValueError("Canvas image PNG/JPEG ho aur 2 MB se chhoti ho")
        try:
            base64.b64decode(image_match.group(2), validate=True)
        except ValueError:
            raise ValueError("Canvas image data valid nahi") from None
        layout["imageDataUrl"] = image_data
    raw_design = payload.get("design")
    design = None
    if raw_design:
        design = designer_render.validate_design(raw_design, columns, parameter_names)
    display_names = [layout["columnLabels"][column].casefold() for column in columns]
    if len(display_names) != len(set(display_names)):
        raise ValueError("Har exported column ka display name alag rakhein")

    def build_pipeline(params: dict[str, Any]) -> list[dict[str, Any]]:
        resolved = copy.deepcopy(pipeline)
        if date_range:
            start_name = str(date_range["startParameter"])
            end_name = str(date_range["endParameter"])
            start_value = params.get(start_name)
            end_value = params.get(end_name)
            if not isinstance(start_value, date) or not isinstance(end_value, date):
                raise ValueError("dateRange ke start/end parameters dena lazmi hai")
            field = str(date_range["field"])

            def apply_date_range(match_clause: Any) -> bool:
                """Find the configured field within nested $and/$or match clauses."""
                if isinstance(match_clause, dict):
                    condition = match_clause.get(field)
                    if isinstance(condition, dict):
                        condition["$gte"] = _date_boundary(start_value, "start")
                        condition["$lte"] = _date_boundary(end_value, "end")
                        return True
                    for child in match_clause.values():
                        if apply_date_range(child):
                            return True
                elif isinstance(match_clause, list):
                    for child in match_clause:
                        if apply_date_range(child):
                            return True
                return False

            for stage in resolved:
                match = stage.get("$match")
                if apply_date_range(match):
                    break
            else:
                raise ValueError(f"Pipeline mein dateRange field ka $match nahi mila: {field}")
        return _resolve_pipeline_parameters(resolved, params)

    normalized = {
        "id": report_id,
        "title": title,
        "description": str(payload.get("description", "")),
        "folder": folder,
        "connectionId": connection_id,
        "collection": collection,
        "parameters": [
            {
                "name": item.name,
                "type": item.kind,
                "label": item.label,
                "description": item.description,
                "required": item.required,
                **({"default": item.default} if item.default is not None else {}),
                **({"allowedValues": list(item.allowed_values)} if item.allowed_values else {}),
                "maxLength": item.max_length,
            }
            for item in parameters
        ],
        "queryLanguage": query_language,
        **({"querySource": query_source} if isinstance(query_source, str) else {}),
        "pipeline": pipeline,
        "columns": columns,
        "formats": formats,
        "defaultFormat": default_format,
        "layout": layout,
        **({"dateRange": date_range} if date_range else {}),
        **({"design": design} if design else {}),
    }
    return ReportDefinition(
        report_id=report_id,
        title=title,
        collection=collection,
        parameters=tuple(parameters),
        columns=tuple(columns),
        pipeline_builder=build_pipeline,
        connection_id=connection_id,
        editor_config=normalized,
    )


def load_connection_profiles() -> list[dict[str, str]]:
    path = connections_file()
    profiles: list[dict[str, str]] = []
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        items = raw.get("connections", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            raise ValueError("connections.json mein connections array honi chahiye")
        for item in items:
            if not isinstance(item, dict):
                continue
            profile = {
                "id": str(item.get("id", "")).strip(),
                "name": str(item.get("name", "")).strip(),
            }
            storage = str(item.get("storage", "env")).strip().lower()
            if storage == "file":
                profile["storage"] = "file"
                profile["database"] = str(item.get("database", "")).strip()
                valid = CONNECTION_ID_PATTERN.fullmatch(profile["id"]) and profile["name"]
            else:
                profile["storage"] = "env"
                profile["uriEnv"] = str(item.get("uriEnv", "")).strip()
                profile["databaseEnv"] = str(item.get("databaseEnv", "")).strip()
                valid = (
                    CONNECTION_ID_PATTERN.fullmatch(profile["id"])
                    and profile["name"]
                    and ENV_KEY_PATTERN.fullmatch(profile["uriEnv"])
                    and ENV_KEY_PATTERN.fullmatch(profile["databaseEnv"])
                )
            if valid:
                profiles.append(profile)
    if not any(item["id"] == "default" for item in profiles):
        profiles.insert(0, {
            "id": "default",
            "name": "Default MongoDB",
            "storage": "env",
            "uriEnv": "MONGODB_URI",
            "databaseEnv": "MONGODB_DATABASE",
        })
    return profiles


def discover_reports() -> dict[str, ReportDefinition]:
    registry: dict[str, ReportDefinition] = {}
    if os.getenv("REPORTING_LOAD_BUILTIN_REPORTS", "false").lower() == "true":
        import reports
        for module_info in pkgutil.iter_modules(reports.__path__):
            if module_info.name.startswith("_"):
                continue
            module = importlib.import_module(f"reports.{module_info.name}")
            definition = getattr(module, "REPORT", None)
            if not isinstance(definition, ReportDefinition):
                raise RuntimeError(f"reports/{module_info.name}.py must define REPORT = ReportDefinition(...)")
            if definition.report_id in registry:
                raise RuntimeError(f"Duplicate report_id: {definition.report_id}")
            registry[definition.report_id] = definition
    directory = report_definitions_directory()
    if directory.exists():
        for path in sorted(directory.rglob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            definition = _dynamic_report(payload, path)
            registry[definition.report_id] = definition
    return registry


def parse_scalar(spec: ParameterSpec, raw: str) -> Any:
    if spec.kind == "date":
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f"{spec.name} YYYY-MM-DD format mein hona chahiye") from None
    if spec.kind == "integer":
        try:
            value: Any = int(raw)
        except ValueError:
            raise ValueError(f"{spec.name} integer hona chahiye") from None
    elif spec.kind == "number":
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{spec.name} number hona chahiye") from None
        if not math.isfinite(value):
            raise ValueError(f"{spec.name} finite number hona chahiye")
    elif spec.kind == "boolean":
        mapping = {"1": True, "true": True, "0": False, "false": False}
        try:
            value = mapping[raw.lower()]
        except KeyError:
            raise ValueError(f"{spec.name} ki value 1/0 ya true/false honi chahiye") from None
    else:
        value = raw
        if len(value) > spec.max_length:
            raise ValueError(f"{spec.name} zyada lamba hai")
    if spec.allowed_values and value not in spec.allowed_values:
        raise ValueError(f"{spec.name} ki value allowed list mein nahi")
    return value


def validate_parameters(definition: ReportDefinition, query_pairs: list[tuple[str, str]]) -> tuple[dict[str, Any], dict[str, str]]:
    keys = [key for key, _ in query_pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"Duplicate query parameter(s): {', '.join(duplicates)}")
    raw = dict(query_pairs)
    metadata = {key: raw.pop(key, "") for key in METADATA_KEYS}
    specs = {spec.name: spec for spec in definition.parameters}
    unknown = sorted(set(raw) - set(specs))
    if unknown:
        raise ValueError(f"Unconfigured parameter(s): {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for key, spec in specs.items():
        if key not in raw or raw[key] == "":
            if spec.required and spec.default is None:
                raise ValueError(f"{key} lazmi parameter hai")
            if spec.default is not None:
                values[key] = parse_scalar(spec, str(spec.default))
            continue
        values[key] = parse_scalar(spec, raw[key])
    date_values = [
        values[spec.name]
        for spec in definition.parameters
        if spec.kind == "date" and spec.name in values
    ]
    if len(date_values) >= 2 and date_values[0] > date_values[1]:
        raise ValueError("Start date, end date se pehle ya usi din honi chahiye")
    return values, metadata


def _safe_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value


def _export_cell_value(value: Any) -> Any:
    value = _safe_value(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def render_csv(columns: tuple[str, ...], rows: list[dict[str, Any]], column_labels: dict[str, str] | None = None) -> bytes:
    labels = column_labels or {}
    buffer = io.StringIO(newline="")
    display_columns = [labels.get(key, key) for key in columns]
    writer = csv.DictWriter(buffer, fieldnames=display_columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({labels.get(key, key): _export_cell_value(row.get(key, "")) for key in columns})
    return buffer.getvalue().encode("utf-8-sig")


def render_xlsx(title: str, intro: str, paragraphs: list[str], columns: tuple[str, ...], rows: list[dict[str, Any]], column_labels: dict[str, str] | None = None, show_title: bool = True, show_intro: bool = True) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("XLSX ke liye openpyxl install karein") from exc
    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    if show_title:
        sheet.append([title])
    for text in [intro if show_intro else "", *paragraphs]:
        if text:
            sheet.append([text])
    labels = column_labels or {}
    display_columns = [labels.get(key, key) for key in columns]
    heading_row = sheet.max_row + 1
    sheet.append(display_columns)
    for cell in sheet[heading_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17324D")
    for row in rows:
        sheet.append([_export_cell_value(row.get(key, "")) for key in columns])
    sheet.freeze_panes = f"A{heading_row + 1}"
    if sheet.max_column:
        sheet.auto_filter.ref = f"A{heading_row}:{get_column_letter(sheet.max_column)}{sheet.max_row}"
        for index, name in enumerate(display_columns, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = min(max(len(name) + 3, 12), 36)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def render_pdf(title: str, intro: str, paragraphs: list[str], image_data_url: str, report_id: str, columns: tuple[str, ...], rows: list[dict[str, Any]], date_range: str, column_labels: dict[str, str] | None = None, show_title: bool = True, show_intro: bool = True, positions: dict[str, Any] | None = None, parameter_fields: list[dict[str, Any]] | None = None, parameter_values: dict[str, Any] | None = None) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Indenter, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("PDF ke liye reportlab install karein") from exc
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=8 * mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("CanvasCell", parent=styles["BodyText"], fontName="Helvetica", fontSize=7, leading=8.5, spaceAfter=0))
    styles.add(ParagraphStyle("CanvasHeaderCell", parent=styles["CanvasCell"], fontName="Helvetica-Bold", textColor=colors.white))
    styles.add(ParagraphStyle("CanvasValue", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=13, textColor=colors.HexColor("#a9441c"), spaceAfter=0))
    position_map = positions or {
        "title": {"x": 64, "y": 70, "w": 666, "h": 54},
        "intro": {"x": 64, "y": 132, "w": 666, "h": 62},
        "image": {"x": 64, "y": 210, "w": 280, "h": 170},
        "table": {"x": 64, "y": 650, "w": 666, "h": 390},
    }
    page_width, page_height = A4
    scale = page_width / 794
    labels = column_labels or {}
    data = [[Paragraph(html_escape(str(labels.get(value, value))), styles["CanvasHeaderCell"]) for value in columns]]
    data.extend([[Paragraph(html_escape(str(_export_cell_value(row.get(key, "")))), styles["CanvasCell"]) for key in columns] for row in rows])
    table_position = position_map.get("table", {"x": 64, "y": 650, "w": 666, "h": 390})
    column_width = max(24, float(table_position.get("w", 666))) * scale / max(1, len(columns))
    table = Table(data, colWidths=[column_width] * len(columns), repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))

    def draw_text(canvas: Any, text: str, position: dict[str, Any], style: Any) -> None:
        if not text:
            return
        x = float(position.get("x", 0)) * scale
        y = float(position.get("y", 0)) * scale
        width = float(position.get("w", 200)) * scale
        height = float(position.get("h", 40)) * scale
        safe_text = html_escape(text).replace("\n", "<br/>")
        paragraph = Paragraph(safe_text, style)
        _, paragraph_height = paragraph.wrap(width, height)
        canvas.saveState()
        canvas.clipRect(x, page_height - y - height, width, height, stroke=0, fill=0)
        paragraph.drawOn(canvas, x, page_height - y - min(paragraph_height, height))
        canvas.restoreState()

    def draw_canvas(canvas: Any) -> None:
        canvas.saveState()
        if image_data_url:
            try:
                image_bytes = base64.b64decode(image_data_url.split(",", 1)[1], validate=True)
                reader = ImageReader(io.BytesIO(image_bytes))
                image_width, image_height = reader.getSize()
                image_position = position_map.get("image", {"x": 64, "y": 210, "w": 280, "h": 170})
                x, y = float(image_position["x"]) * scale, float(image_position["y"]) * scale
                width, height = float(image_position["w"]) * scale, float(image_position["h"]) * scale
                ratio = min(width / image_width, height / image_height)
                draw_width, draw_height = image_width * ratio, image_height * ratio
                canvas.drawImage(reader, x + (width - draw_width) / 2, page_height - y - height + (height - draw_height) / 2,
                                 width=draw_width, height=draw_height, mask="auto")
            except (ValueError, IndexError, OSError):
                LOG.exception("Canvas image could not be rendered")
        if show_title:
            draw_text(canvas, title, position_map.get("title", {}), styles["Title"])
        if show_intro:
            draw_text(canvas, intro, position_map.get("intro", {}), styles["Normal"])
        for index, text in enumerate(paragraphs):
            draw_text(canvas, text, position_map.get(f"paragraph-{index}", {"x": 64, "y": 220 + index * 96, "w": 666, "h": 78}), styles["Normal"])
        for field in parameter_fields or []:
            value = (parameter_values or {}).get(field.get("name", ""), "")
            value = _safe_value(value)
            position = field.get("position", {"x": 90, "y": 245, "w": 210, "h": 34})
            x, y = float(position["x"]) * scale, float(position["y"]) * scale
            width, height = float(position["w"]) * scale, float(position["h"]) * scale
            canvas.setFillColor(colors.HexColor("#fff8e8")); canvas.setStrokeColor(colors.HexColor("#bf542e"))
            canvas.roundRect(x, page_height - y - height, width, height, 3, stroke=1, fill=1)
            draw_text(canvas, str(value), position, styles["CanvasValue"])
        canvas.restoreState()

    def draw_footer(canvas: Any, _doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#667788"))
        canvas.drawString(12 * mm, 5 * mm, f"{report_id} · {date_range}")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(page_width - 12 * mm, 5 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()
    table_top = max(0, float(table_position.get("y", 650))) * scale
    table_left = max(0, float(table_position.get("x", 64))) * scale
    def draw_first_page(canvas: Any, doc_context: Any) -> None:
        draw_canvas(canvas)
        draw_footer(canvas, doc_context)

    doc.build([Spacer(1, table_top), Indenter(left=table_left), table, Indenter(left=-table_left)], onFirstPage=draw_first_page, onLaterPages=draw_footer)
    return output.getvalue()


def render_report_definition_xml(definition: dict[str, Any]) -> bytes:
    """Package the complete saved definition as an importable XML template."""
    root = ET.Element("mongoReportDefinition", {
        "version": "1",
        "reportId": str(definition.get("id", "")),
        "title": str(definition.get("title", "")),
        "folder": str(definition.get("folder", "General")),
    })
    payload = ET.SubElement(root, "definition", {"encoding": "json"})
    payload.text = json.dumps(definition, ensure_ascii=False, separators=(",", ":"))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_report_definition_xml(raw_xml: str) -> dict[str, Any]:
    if len(raw_xml.encode("utf-8")) > 5_000_000:
        raise ValueError("Report definition XML 5 MB se bara hai")
    root = ET.fromstring(raw_xml)
    if root.tag != "mongoReportDefinition" or root.attrib.get("version") != "1":
        raise ValueError("Yeh supported Mongo report definition XML file nahi hai")
    payload = root.find("definition")
    if payload is None or payload.attrib.get("encoding") != "json" or not payload.text:
        raise ValueError("XML report definition ke andar saved configuration nahi mili")
    definition = json.loads(payload.text)
    if not isinstance(definition, dict):
        raise ValueError("XML file mein report definition object nahi mila")
    return definition


def make_app() -> FastAPI:
    settings = Settings.from_env()
    initialize_data_directory()
    ensure_admin_token()
    reports_by_id = discover_reports()
    state: dict[str, Any] = {"clients": {}, "connection_signatures": {}}

    def get_connection(connection_id: str) -> tuple[MongoClient, str]:
        profiles = {item["id"]: item for item in load_connection_profiles()}
        profile = profiles.get(connection_id)
        if profile is None:
            raise HTTPException(status_code=400, detail=f"MongoDB connection ID registered nahi: {connection_id}")
        uri, database = _profile_credentials(profile)
        if not uri or not database:
            if profile.get("storage") == "file":
                detail = f"Connection '{connection_id}' ke liye MongoDB URL/database private settings file mein nahi mila"
            else:
                detail = f"Connection '{connection_id}' ke liye {profile['uriEnv']} aur {profile['databaseEnv']} set karein"
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
            candidate = MongoClient(uri, serverSelectionTimeoutMS=5000)
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
            f"URI path database '{uri_database}' se mukhtalif explicit database '{selected_database}' target ho rahi hai."
            if uri_database and database_override and uri_database != database_override else ""
        )
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise HTTPException(status_code=422, detail="MongoDB URL mongodb:// ya mongodb+srv:// se shuru honi chahiye")
        if not selected_database:
            raise HTTPException(status_code=422, detail="URL mein database name nahi mila; Database name bhi dein")

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
            client = MongoClient(uri, serverSelectionTimeoutMS=5000)
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
                        f"MongoDB server reachable, lekin current URL se '{selected_database}.{current_collection}' "
                        "collection ka find/read reject hua. Connection test aur report dono isi saved URL ko use karte hain."
                        + identity_detail + database_detail
                    ),
                ) from None
            detail = _safe_mongo_error(exc, uri)
            LOG.warning("MongoDB connection/read check failed: %s", detail)
            raise HTTPException(status_code=503, detail=f"MongoDB connection/read check fail hui: {detail}") from None
        finally:
            if client is not None:
                client.close()

    def require_admin(request: Request) -> None:
        expected = os.getenv("REPORTING_ADMIN_TOKEN", "")
        if not expected:
            raise HTTPException(status_code=503, detail="Admin UI/API ke liye REPORTING_ADMIN_TOKEN environment variable set karein")
        if not secrets.compare_digest(request.headers.get("X-Admin-Token", ""), expected):
            raise HTTPException(status_code=401, detail="Admin token ghalat ya missing hai")

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

    app = FastAPI(title="MongoDB Reporting Service", version="0.1.0", lifespan=lifespan)
    ui_directory = Path(__file__).resolve().parent / "ui"
    app.mount("/assets", StaticFiles(directory=str(ui_directory)), name="assets")

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse(url="/ui")

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def report_ui() -> HTMLResponse:
        return HTMLResponse((ui_directory / "index.html").read_text(encoding="utf-8"))

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    def admin_ui() -> HTMLResponse:
        return HTMLResponse((ui_directory / "admin.html").read_text(encoding="utf-8"))

    @app.get("/api/admin/bootstrap", include_in_schema=False)
    def local_admin_bootstrap(request: Request) -> JSONResponse:
        """Allow the local browser UI to pick up its token without exposing it remotely."""
        allowed_hosts = {"localhost", "127.0.0.1", "::1"}
        host = (request.url.hostname or "").lower()
        client_host = request.client.host if request.client else ""
        try:
            local_client = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            local_client = False
        if host not in allowed_hosts or not local_client:
            raise HTTPException(status_code=404, detail="Local admin bootstrap is available only on this computer")
        token = os.getenv("REPORTING_ADMIN_TOKEN", "").strip()
        if not token:
            raise HTTPException(status_code=503, detail="Local admin token initialize nahi ho saka")
        return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})

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

    @app.post("/api/admin/query/parse")
    async def admin_parse_query(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Query parse request JSON format mein bhejein") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Query parse request object hona chahiye")
        try:
            pipeline = parse_query_source(payload.get("language", "aggregation-json"), payload.get("source", ""))
            if "parameters" in payload:
                raw_parameters = payload["parameters"]
                if not isinstance(raw_parameters, list) or any(not isinstance(item, dict) for item in raw_parameters):
                    raise ValueError("Query parameters JSON object list honi chahiye")
                parameter_names = {str(item.get("name", "")) for item in raw_parameters}
                _validate_pipeline_parameter_names(pipeline, parameter_names)
            _validate_read_only_pipeline(pipeline)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"pipeline": pipeline, "stageCount": len(pipeline)}

    def save_report_payload(payload: Any, expected_id: str | None = None) -> ReportDefinition:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Report definition JSON object honi chahiye")
        try:
            definition = _dynamic_report(payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if expected_id and definition.report_id != expected_id:
            raise HTTPException(status_code=400, detail="URL report ID aur definition ID same honi chahiye")
        existing = reports_by_id.get(definition.report_id)
        if existing is not None and expected_id is None:
            raise HTTPException(status_code=409, detail="Is report ID ka pehle se report maujood hai")
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

    @app.get("/api/admin/reports")
    def admin_list_reports(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {"reports": [admin_report_payload(item) for item in sorted(reports_by_id.values(), key=lambda report: report.report_id)]}

    @app.get("/api/admin/folders")
    def admin_list_folders(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            return {"folders": list_saved_folders()}
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None

    @app.post("/api/admin/folders")
    async def admin_create_folder(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Valid JSON body bhejein") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Folder object JSON format mein bhejein")
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        folders = set(list_saved_folders())
        pieces = folder.split("/")
        folders.update("/".join(pieces[:index]) for index in range(1, len(pieces) + 1))
        _write_json_atomically(folders_file(), {"folders": sorted(folders, key=str.casefold)})
        report_definition_path(".folder", folder).parent.mkdir(parents=True, exist_ok=True)
        return {"folder": folder, "folders": list_saved_folders()}

    @app.delete("/api/admin/folders")
    async def admin_delete_folder(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
            folder = normalize_folder_path(payload.get("path", "")) if isinstance(payload, dict) else ""
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc) or "Folder path ghalat hai") from None
        except Exception:
            raise HTTPException(status_code=400, detail="Folder path JSON mein bhejein") from None
        if folder == "General":
            raise HTTPException(status_code=400, detail="General default folder delete nahi ho sakta")
        saved_folders = list_saved_folders()
        if folder not in saved_folders:
            raise HTTPException(status_code=404, detail="Folder nahi mila")
        if any(item.startswith(f"{folder}/") for item in saved_folders):
            raise HTTPException(status_code=409, detail="Pehle is folder ke andar maujood subfolders delete karein")
        if any(
            item.editor_config
            and str(item.editor_config.get("folder", "General")) == folder
            for item in reports_by_id.values()
        ):
            raise HTTPException(status_code=409, detail="Pehle is folder ki reports delete ya doosre folder mein move karein")
        directory = report_definition_path(".folder", folder).parent
        if directory.exists():
            try:
                directory.rmdir()
            except OSError:
                raise HTTPException(status_code=409, detail="Folder khali nahi; pehle uski files hata dein") from None
        remaining = [item for item in saved_folders if item != folder]
        _write_json_atomically(folders_file(), {"folders": remaining})
        return {"deleted": folder, "folders": list_saved_folders()}

    @app.get("/api/admin/reports/{report_id}")
    def admin_get_report(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        definition = reports_by_id.get(report_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Report ID registered nahi hai")
        return admin_report_payload(definition)

    @app.get("/api/admin/reports/{report_id}/definition.xml")
    def admin_export_report_definition(report_id: str, request: Request) -> Response:
        require_admin(request)
        definition = reports_by_id.get(report_id)
        if definition is None or definition.editor_config is None:
            raise HTTPException(status_code=404, detail="Saved report definition nahi mili")
        body = render_report_definition_xml(admin_report_payload(definition))
        return Response(
            body,
            media_type="application/xml; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.report.xml"'},
        )

    @app.post("/api/admin/reports/import")
    async def admin_import_report_definition(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            request_payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Import request JSON format mein bhejein") from None
        if not isinstance(request_payload, dict) or not isinstance(request_payload.get("xml"), str):
            raise HTTPException(status_code=400, detail="Report definition XML file ka content bhejein")
        try:
            definition_payload = parse_report_definition_xml(request_payload["xml"])
            definition_payload["connectionId"] = str(request_payload.get("connectionId", "")).strip()
            definition_payload["folder"] = normalize_folder_path(
                request_payload.get("folder") or definition_payload.get("folder", "General")
            )
            definition = _dynamic_report(definition_payload)
        except (ET.ParseError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"XML file read nahi hui: {exc}") from None
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        existing = reports_by_id.get(definition.report_id)
        replace_existing = request_payload.get("replace") is True
        if existing is not None and not replace_existing:
            raise HTTPException(status_code=409, detail="Is ID ki report pehle se maujood hai; replace confirm karein")
        try:
            saved = save_report_payload(
                definition_payload,
                expected_id=definition.report_id if existing is not None else None,
            )
        except HTTPException:
            raise
        return {"report": admin_report_payload(saved), "replaced": existing is not None}

    @app.post("/api/admin/reports")
    async def admin_create_report(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Valid JSON body bhejein") from None
        return {"report": admin_report_payload(save_report_payload(payload))}

    @app.put("/api/admin/reports/{report_id}")
    async def admin_update_report(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Valid JSON body bhejein") from None
        return {"report": admin_report_payload(save_report_payload(payload, report_id))}

    @app.delete("/api/admin/reports/{report_id}")
    def admin_delete_report(report_id: str, request: Request) -> dict[str, str]:
        require_admin(request)
        if not REPORT_ID_PATTERN.fullmatch(report_id):
            raise HTTPException(status_code=400, detail="Report ID format ghalat hai")
        definition = reports_by_id.get(report_id)
        if definition is None or definition.editor_config is None:
            raise HTTPException(status_code=404, detail="Saved report definition nahi mili")
        path = report_definition_path(
            report_id,
            normalize_folder_path(definition.editor_config.get("folder", "General")),
        )
        if not path.exists():
            raise HTTPException(status_code=404, detail="Saved report definition nahi mili")
        path.unlink()
        refreshed = discover_reports()
        reports_by_id.clear()
        reports_by_id.update(refreshed)
        return {"status": "deleted", "reportId": report_id}

    @app.post("/api/admin/reports/{report_id}/preview")
    async def admin_preview_report(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        definition = reports_by_id.get(report_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Report ID registered nahi hai")
        try:
            payload = await request.json()
            raw_params = payload.get("parameters", {})
            if not isinstance(raw_params, dict):
                raise ValueError("parameters JSON object hona chahiye")
            pairs = [(str(key), str(value)) for key, value in raw_params.items()]
            parameters, _ = validate_parameters(definition, pairs)
            pipeline = definition.pipeline_builder(parameters)
            pipeline = [*pipeline, {"$limit": min(settings.max_rows, 20)}]
            client, database = get_connection(definition.connection_id)
            rows = _execute_report_pipeline(
                client[database][resolve_collection(definition.collection, parameters)],
                pipeline,
                max_time_ms=settings.max_time_ms,
                allow_disk_use=settings.allow_disk_use,
                max_rows=min(settings.max_rows, 20),
            )
            safe_rows = [{key: _safe_value(_row_value(row, key)) for key in definition.columns} for row in rows]
            layout = (definition.editor_config or {}).get("layout", {})
            return {"columns": list(definition.columns), "labels": layout.get("columnLabels", {}), "layout": layout, "rows": safe_rows, "rowCount": len(safe_rows)}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except MongoReadAccessError as exc:
            LOG.warning("Admin preview cannot read report collections with the configured MongoDB identity")
            raise HTTPException(
                status_code=503,
                detail=f"{exc} Report data source ID: '{definition.connection_id}'.",
            ) from None
        except PyMongoError as exc:
            LOG.exception("Admin report preview failed")
            raise HTTPException(status_code=503, detail="MongoDB preview query fail hui; server log dekhein") from None

    @app.get("/api/admin/connections")
    def admin_list_connections(request: Request) -> dict[str, Any]:
        require_admin(request)
        profiles = load_connection_profiles()
        result = []
        for profile in profiles:
            uri, database = _profile_credentials(profile)
            result.append({
                "id": profile["id"],
                "name": profile["name"],
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
            raise HTTPException(status_code=400, detail="Valid JSON body bhejein") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Connection profile JSON object honi chahiye")
        name = str(payload.get("name", "")).strip()
        uri = str(payload.get("uri", "")).strip()
        database = str(payload.get("database", "")).strip()
        connection_id = str(payload.get("id", "")).strip().lower()
        if not name:
            raise HTTPException(status_code=422, detail="Data source ka naam lazmi hai")
        if not connection_id:
            connection_id = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_-")[:40]
            if not connection_id or not connection_id[0].isalnum():
                connection_id = f"mongo_{connection_id}"[:40]
        if not CONNECTION_ID_PATTERN.fullmatch(connection_id):
            raise HTTPException(status_code=422, detail="Data source name se valid ID nahi bani; letters aur numbers use karein")

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
                raise HTTPException(status_code=422, detail="Valid environment variable names dein")
            profile = legacy_profile
        else:
            if not uri.startswith(("mongodb://", "mongodb+srv://")):
                raise HTTPException(status_code=422, detail="MongoDB URL dein jo mongodb:// ya mongodb+srv:// se shuru ho")
            database = database or _mongo_database_from_uri(uri)
            if not database:
                raise HTTPException(status_code=422, detail="URL mein database name nahi mila; Database name dein")
            profile = {"id": connection_id, "name": name, "storage": "file", "database": database}

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
        _write_json_atomically(connections_file(), {"connections": updated})
        old_client = state["clients"].pop(connection_id, None)
        state["connection_signatures"].pop(connection_id, None)
        if old_client is not None:
            old_client.close()
        return {"connection": {"id": connection_id, "name": name, "database": database, "configured": True}}

    @app.post("/api/admin/connections/test")
    async def admin_test_connection_values(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Valid JSON body bhejein") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Connection test ke liye JSON object bhejein")
        uri = str(payload.get("uri", "")).strip()
        database = str(payload.get("database", "")).strip()
        connection_id = str(payload.get("id", "")).strip().lower()
        return test_mongo_connection(uri, database, connection_id)

    @app.delete("/api/admin/connections/{connection_id}")
    def admin_delete_connection(connection_id: str, request: Request) -> dict[str, str]:
        require_admin(request)
        if connection_id == "default":
            raise HTTPException(status_code=400, detail="Default connection delete nahi ho sakti")
        used_by = [item.report_id for item in reports_by_id.values() if item.connection_id == connection_id]
        if used_by:
            raise HTTPException(status_code=409, detail=f"Is connection ko pehle in reports se badlein: {', '.join(used_by)}")
        profiles = [item for item in load_connection_profiles() if item["id"] != connection_id]
        _write_json_atomically(connections_file(), {"connections": profiles})
        private = _load_private_secrets()
        private["mongo_connections"].pop(connection_id, None)
        _save_private_secrets(private)
        old_client = state["clients"].pop(connection_id, None)
        state["connection_signatures"].pop(connection_id, None)
        if old_client is not None:
            old_client.close()
        return {"status": "deleted", "connectionId": connection_id}

    @app.post("/api/admin/connections/{connection_id}/test")
    def admin_test_connection(connection_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
        if profile is None:
            raise HTTPException(status_code=404, detail="Connection ID registered nahi hai")
        uri, database = _profile_credentials(profile)
        if not uri or not database:
            if profile.get("storage") == "file":
                detail = "Is data source mein MongoDB URL ya database name save nahi hai"
            else:
                detail = f".env ya server environment mein {profile['uriEnv']} aur {profile['databaseEnv']} set karein"
            raise HTTPException(status_code=422, detail=detail)
        return test_mongo_connection(uri, database, connection_id)

    @app.post("/api/admin/jrxml/import")
    async def admin_import_jrxml(request: Request) -> dict[str, Any]:
        """Convert a Jaspersoft Studio .jrxml into a draft report + design. Nothing is saved or executed."""
        require_admin(request)
        try:
            payload = await request.json()
            xml_text = payload.get("xml") if isinstance(payload, dict) else None
            if not isinstance(xml_text, str) or not xml_text.strip():
                raise ValueError("JSON body mein 'xml' (jrxml text) dein")
            import jrxml_import
            return jrxml_import.import_jrxml(xml_text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/designer", response_class=HTMLResponse, include_in_schema=False)
    def designer_ui() -> HTMLResponse:
        page = ui_directory / "designer" / "index.html"
        if not page.exists():
            raise HTTPException(status_code=404, detail="Designer build maujood nahi. frontend/ mein `npm install && npm run build` chalayen")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @app.post("/api/admin/designer/render")
    async def admin_designer_render(request: Request) -> Response:
        """Render a design to PDF for live preview. Uses supplied rows, else synthetic sample rows."""
        require_admin(request)
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Request JSON object honi chahiye")
            columns = payload.get("columns", [])
            if not isinstance(columns, list) or not all(isinstance(item, str) for item in columns):
                raise ValueError("columns string list honi chahiye")
            raw_parameters = payload.get("parameters", {})
            if not isinstance(raw_parameters, dict):
                raise ValueError("parameters JSON object hona chahiye")
            design = designer_render.validate_design(payload.get("design"), columns, {str(k) for k in raw_parameters})
            rows = payload.get("rows")
            if rows is None:
                rows = designer_render.sample_rows(columns)
            if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
                raise ValueError("rows objects ki list honi chahiye")
            body = designer_render.render_design_pdf(
                design, str(payload.get("title", "Report")), columns, rows[:2000], raw_parameters, str(payload.get("reportId", "")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from None
        return Response(body, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="preview.pdf"'})

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            _, database = get_connection("default")
            return JSONResponse({"status": "ok", "connection": "default", "database": database})
        except HTTPException as exc:
            status = "not_configured" if "set karein" in str(exc.detail) else "unavailable"
            return JSONResponse({"status": status, "detail": exc.detail}, status_code=exc.status_code)

    @app.get("/reports")
    def list_reports() -> dict[str, Any]:
        return {"reports": [{
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
            } for p in item.parameters],
        } for item in reports_by_id.values()]}

    @app.get("/generate/{report_file}")
    def generate(report_file: str, request: Request) -> Response:
        started = time.monotonic()
        audit: dict[str, Any] = {
            "requestedAt": datetime.now(timezone.utc),
            "path": request.url.path,
            "status": "failed",
            "rowCount": 0,
        }
        query_pairs = request.query_params.multi_items()
        query_map = dict(query_pairs)
        for key in METADATA_KEYS:
            if key in query_map:
                audit[key] = query_map[key]
        definition: ReportDefinition | None = None
        audit_saved = False
        try:
            match = REPORT_PATH.fullmatch(report_file)
            if not match:
                raise HTTPException(status_code=400, detail="Path /generate/<REPORT_ID>.<pdf|xlsx|csv> hona chahiye")
            report_id, output_format = match.group(1), match.group(2).lower()
            definition = reports_by_id.get(report_id)
            if definition is None:
                raise HTTPException(status_code=404, detail="Report ID registered nahi hai")
            enabled_formats = (definition.editor_config or {}).get("formats", sorted(OUTPUT_FORMATS))
            if output_format not in enabled_formats:
                raise HTTPException(status_code=400, detail=f"{output_format.upper()} format is report ke liye enabled nahi hai")
            audit.update({"reportId": report_id, "format": output_format})
            parameters, metadata = validate_parameters(definition, query_pairs)
            audit.update(metadata)
            audit["parameters"] = {key: value.isoformat() if isinstance(value, date) else value for key, value in parameters.items()}
            pipeline = definition.pipeline_builder(parameters)
            # aggregate() returns a CommandCursor, which has no limit() method.
            # Cap the number of returned documents in MongoDB's pipeline instead.
            pipeline = [*pipeline, {"$limit": settings.max_rows + 1}]
            client, database = get_connection(definition.connection_id)
            collection = client[database][resolve_collection(definition.collection, parameters)]
            rows = _execute_report_pipeline(
                collection,
                pipeline,
                max_time_ms=settings.max_time_ms,
                allow_disk_use=settings.allow_disk_use,
                max_rows=settings.max_rows,
            )
            if len(rows) > settings.max_rows:
                raise HTTPException(status_code=413, detail=f"Report max row limit ({settings.max_rows}) se bari hai")
            rows = [{key: _row_value(row, key) for key in definition.columns} for row in rows]
            layout = (definition.editor_config or {}).get("layout", {})
            display_title = str(layout.get("title") or definition.title)
            display_intro = str(layout.get("intro") or "")
            display_paragraphs = layout.get("paragraphs", [])
            image_data_url = str(layout.get("imageDataUrl") or "")
            show_title = layout.get("showTitle", True) is True
            show_intro = layout.get("showIntro", True) is True
            column_labels = layout.get("columnLabels", {})
            date_parameter_names = [
                spec.name for spec in definition.parameters if spec.kind == "date"
            ]
            date_range = " to ".join(
                str(parameters[name])
                for name in date_parameter_names[:2]
                if name in parameters
            )
            band_design = (definition.editor_config or {}).get("design")
            use_design_table = bool(band_design) and designer_render.has_tabular_layout(band_design)
            if use_design_table:
                design_rows = [{key: _safe_value(value) for key, value in row.items()} for row in rows]
                design_parameters = {key: (value.isoformat() if isinstance(value, date) else value) for key, value in parameters.items()}
            if output_format == "csv" and use_design_table:
                body, media_type = designer_render.render_design_csv(band_design, display_title, list(definition.columns), design_rows, design_parameters), "text/csv; charset=utf-8"
            elif output_format == "xlsx" and use_design_table:
                body, media_type = designer_render.render_design_xlsx(band_design, display_title, list(definition.columns), design_rows, design_parameters), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif output_format == "csv":
                body, media_type = render_csv(definition.columns, rows, column_labels), "text/csv; charset=utf-8"
            elif output_format == "xlsx":
                body, media_type = render_xlsx(display_title, display_intro, display_paragraphs, definition.columns, rows, column_labels, show_title, show_intro), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                band_design = (definition.editor_config or {}).get("design")
                if band_design:
                    design_rows = [{key: _safe_value(value) for key, value in row.items()} for row in rows]
                    body, media_type = designer_render.render_design_pdf(
                        band_design, display_title, list(definition.columns), design_rows,
                        {key: (value.isoformat() if isinstance(value, date) else value) for key, value in parameters.items()},
                        definition.report_id,
                    ), "application/pdf"
                else:
                    body, media_type = render_pdf(
                        display_title, display_intro, display_paragraphs, image_data_url,
                        definition.report_id, definition.columns, rows, date_range,
                        column_labels, show_title, show_intro, layout.get("positions", {}),
                        layout.get("parameterFields", []), parameters,
                    ), "application/pdf"
            audit.update({"status": "success", "rowCount": len(rows)})
            if settings.audit_required:
                # Fail closed: the report is released only after its audit record is stored.
                audit["durationMs"] = int((time.monotonic() - started) * 1000)
                try:
                    _append_report_audit(audit)
                    audit_saved = True
                except OSError:
                    LOG.exception("Required audit record could not be saved")
                    raise HTTPException(status_code=503, detail="Audit record save nahi ho saka; AUDIT_REQUIRED ki wajah se report release nahi hui") from None
            filename = f"{definition.report_id}.{output_format}"
            return Response(body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        except HTTPException as exc:
            audit["errorCode"] = str(exc.status_code)
            audit["errorMessage"] = str(exc.detail)[:300]
            raise
        except ValueError as exc:
            audit["errorCode"] = "validation_error"
            audit["errorMessage"] = str(exc)[:300]
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except MongoReadAccessError as exc:
            audit["errorCode"] = "mongodb_read_access_unavailable"
            audit["errorMessage"] = str(exc)[:300]
            LOG.warning("MongoDB report cannot be read with the configured account")
            raise HTTPException(
                status_code=503,
                detail=f"{exc} Report data source ID: '{definition.connection_id}'.",
            ) from None
        except PyMongoError as exc:
            audit["errorCode"] = "mongodb_error"
            audit["errorMessage"] = type(exc).__name__
            LOG.exception("MongoDB report execution failed")
            raise HTTPException(status_code=503, detail="MongoDB report query fail hui") from None
        except RuntimeError as exc:
            audit["errorCode"] = "export_dependency_missing"
            audit["errorMessage"] = str(exc)[:300]
            raise HTTPException(status_code=501, detail=str(exc)) from None
        except Exception as exc:
            audit["errorCode"] = "internal_error"
            audit["errorMessage"] = type(exc).__name__
            LOG.exception("Unexpected report execution error")
            raise HTTPException(status_code=500, detail="Report generate nahi ho saki") from None
        finally:
            audit["durationMs"] = int((time.monotonic() - started) * 1000)
            if audit.get("reportId") and not audit_saved:
                try:
                    _append_report_audit(audit)
                except OSError:
                    LOG.exception("Local report audit record could not be saved")
                if settings.audit_to_mongodb and settings.audit_database:
                    try:
                        audit_client, _ = get_connection("default")
                        audit_client[settings.audit_database][settings.audit_collection].insert_one(audit)
                    except Exception:
                        # Optional MongoDB audit storage must never block a report
                        # generated with a read-only reporting identity.
                        LOG.exception("Optional MongoDB report audit write failed")

    return app


app = make_app()
