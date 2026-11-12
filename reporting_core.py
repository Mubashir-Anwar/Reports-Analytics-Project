from __future__ import annotations

import csv
import copy
import base64
import ast
import hashlib
import hmac
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
from collections import deque
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
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
import jexpr

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG = logging.getLogger("mongo_reporting_service")
HTTP_LOG = logging.getLogger("mongo_reporting_service.http")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format=LOG_FORMAT)
LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (DEBUG|INFO|WARNING|ERROR|CRITICAL) \[([^\]]+)\] (.*)$")
LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_FILE_HANDLER: logging.Handler | None = None
# Areas that can be switched off per deployment (REPORTING_ENABLED_AREAS). /reports, /generate and /health are always on.
ALL_AREAS = ("runner", "admin", "designer", "server", "docs")
ROLE_TOKEN_MIN_LENGTH = 24
# /server login: users live in <data dir>/users.json (PBKDF2 hashes), sessions are signed cookies.
USER_ROLES = ("viewer", "designer", "admin")  # lowest to highest
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,63}$")
MIN_PASSWORD_LENGTH = 8
PASSWORD_ITERATIONS = 240_000
SESSION_COOKIE = "rs_session"
BOOTSTRAP_USERNAME = "admin"
WORKSPACE_META = "_workspace"  # designer-only bookkeeping inside a workspace draft; never published

SERVICE_VERSION = "0.3.0"
OUTPUT_FORMATS = {"csv", "xlsx", "pdf"}
QUERY_LANGUAGES = {"aggregation-json", "mongosh", "javascript", "python"}
METADATA_KEYS = {"GeneratedBy", "GeneratedByCode", "Folder", "Generate"}
# Signed /generate links carry these two query parameters; they are never report parameters.
SIGNED_LINK_KEYS = ("Expires", "Signature")
MIN_ACCESS_SECRET_LENGTH = 32
# Any of these means the request passed through a proxy, so "loopback client" proves nothing.
BOOTSTRAP_PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "via")
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


def workspace_directory() -> Path:
    """The designer's own saved drafts. Nothing here is on the server until it is published."""
    return data_directory() / "workspace"


def users_file() -> Path:
    return data_directory() / "users.json"


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


# ----------------------------------------------------------------- service log file
def log_file_path() -> Path:
    configured = os.getenv("REPORTING_LOG_FILE", "").strip()
    return Path(configured).expanduser().resolve() if configured else data_directory() / "logs" / "service.log"


def configure_file_logging() -> Path | None:
    """Write the service log to a rotating file (default <data dir>/logs/service.log).

    REPORTING_LOG_TO_FILE=false turns it off (e.g. when the platform collects stdout instead).
    Only the path of a request is logged, never its query string, headers or MongoDB URLs.
    """
    global _FILE_HANDLER
    level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    LOG.setLevel(level if level in LOG_LEVELS else "INFO")  # independent of how the host configured the root logger
    if os.getenv("REPORTING_LOG_TO_FILE", "true").strip().lower() == "false":
        return None
    path = log_file_path()
    if _FILE_HANDLER is not None and getattr(_FILE_HANDLER, "baseFilename", "") == str(path):
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=max(100_000, int(os.getenv("REPORTING_LOG_MAX_BYTES", "5000000"))),
            backupCount=max(1, int(os.getenv("REPORTING_LOG_BACKUPS", "5"))),
            encoding="utf-8",
        )
    except (OSError, ValueError):
        LOG.exception("Service log file could not be opened at %s", path)
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.setLevel(level if level in LOG_LEVELS else "INFO")
    for name in ("mongo_reporting_service", "uvicorn.error"):
        logger = logging.getLogger(name)
        if _FILE_HANDLER is not None:
            logger.removeHandler(_FILE_HANDLER)
        logger.addHandler(handler)
    if _FILE_HANDLER is not None:
        _FILE_HANDLER.close()
    _FILE_HANDLER = handler
    return path


def read_log_entries(limit: int = 300, min_level: str = "DEBUG", search: str = "") -> list[dict[str, str]]:
    """Newest-last log entries from the current log file; traceback lines stay with their entry."""
    path = log_file_path()
    if not path.exists():
        return []
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 4_000_000))
        text = stream.read().decode("utf-8", errors="replace")
    threshold = LOG_LEVELS.get(min_level.upper(), 10)
    needle = search.strip().lower()
    entries: deque[dict[str, str]] = deque(maxlen=max(1, min(limit, 5000)))
    current: dict[str, str] | None = None

    def flush() -> None:
        if current is None or LOG_LEVELS.get(current["level"], 20) < threshold:
            return
        if needle and needle not in (current["message"] + current["logger"]).lower():
            return
        entries.append(current)

    for line in text.splitlines():
        match = LOG_LINE.match(line)
        if match:
            flush()
            current = {"time": match.group(1), "level": match.group(2), "logger": match.group(3), "message": match.group(4)}
        elif current is not None:
            current["message"] += "\n" + line
    flush()
    return list(entries)


def read_audit_records(limit: int = 200) -> list[dict[str, Any]]:
    path = report_audit_file()
    if not path.exists():
        return []
    records: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 5000)))
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                records.append(item)
    return list(reversed(records))


# ----------------------------------------------------------------- deployment access policy
def enabled_areas() -> set[str]:
    raw = os.getenv("REPORTING_ENABLED_AREAS", "").strip()
    if not raw:
        return set(ALL_AREAS)
    areas = {item.strip().lower() for item in raw.split(",") if item.strip()}
    unknown = areas - set(ALL_AREAS)
    if unknown:
        LOG.warning("REPORTING_ENABLED_AREAS has unknown values: %s", ", ".join(sorted(unknown)))
    return areas & set(ALL_AREAS)


def path_area(path: str) -> str | None:
    """Which switchable area a request path belongs to (None = always available)."""
    if path == "/ui" or path.startswith("/ui/"):
        return "runner"
    if path == "/admin" or path.startswith("/admin/"):
        return "admin"
    if path == "/designer" or path.startswith("/designer/"):
        return "designer"
    if path == "/server" or path.startswith("/server/") or path.startswith("/api/server/"):
        return "server"
    if path.startswith("/api/admin/"):
        return "management-api"
    if path in ("/docs", "/redoc", "/openapi.json") or path.startswith("/docs/"):
        return "docs"
    return None


def _parse_networks(raw: str, setting: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            LOG.error("%s contains an invalid IP address or CIDR range: %s", setting, item)
    return networks


def _in_networks(address: str, networks: list) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def client_address(request: Request) -> str:
    """Caller IP. X-Forwarded-For is trusted only when the direct peer is in REPORTING_TRUSTED_PROXIES."""
    direct = request.client.host if request.client else ""
    proxies = _parse_networks(os.getenv("REPORTING_TRUSTED_PROXIES", ""), "REPORTING_TRUSTED_PROXIES")
    if not proxies or not _in_networks(direct, proxies):
        return direct
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    for hop in reversed(forwarded):
        if not _in_networks(hop, proxies):
            return hop
    return forwarded[0] if forwarded else direct


def management_ip_allowed(address: str) -> bool:
    raw = os.getenv("REPORTING_ADMIN_ALLOWED_IPS", "").strip()
    if not raw:
        return True
    return _in_networks(address, _parse_networks(raw, "REPORTING_ADMIN_ALLOWED_IPS"))


def _load_private_secrets() -> dict[str, Any]:
    path = private_secrets_file()
    if not path.exists():
        return {"admin_token": "", "mongo_connections": {}}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("The root of private_secrets.json must be a JSON object")
    connections = raw.get("mongo_connections", {})
    if not isinstance(connections, dict):
        raise ValueError("mongo_connections in private_secrets.json must be a JSON object")
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


def normalize_folder_path(value: Any) -> str:
    folder = str(value or "General").strip().replace("\\", "/").strip("/") or "General"
    parts = folder.split("/")
    if len(folder) > 240 or any(
        part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}", part)
        for part in parts
    ):
        raise ValueError("Use only letters, numbers, spaces, underscores and hyphens in a folder name")
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
            raise ValueError("folders in folders.json must be an array")
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


def add_saved_folder(folder: str) -> list[str]:
    """Register a folder (and its parents) in folders.json and create its directory."""
    folders = set(list_saved_folders())
    pieces = folder.split("/")
    folders.update("/".join(pieces[:index]) for index in range(1, len(pieces) + 1))
    _write_json_atomically(folders_file(), {"folders": sorted(folders, key=str.casefold)})
    report_definition_path(".folder", folder).parent.mkdir(parents=True, exist_ok=True)
    return list_saved_folders()


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


# ----------------------------------------------------------------- server users and login sessions
def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return salt, digest.hex()


def load_users() -> list[dict[str, Any]]:
    path = users_file()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    items = raw.get("users", []) if isinstance(raw, dict) else []
    return [item for item in items if isinstance(item, dict) and item.get("username")]


def save_users(users: list[dict[str, Any]]) -> None:
    path = users_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"users": users}, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def find_user(username: str) -> dict[str, Any] | None:
    wanted = username.strip().casefold()
    return next((item for item in load_users() if str(item["username"]).casefold() == wanted), None)


def check_user_password(user: dict[str, Any], password: str) -> bool:
    try:
        _, digest = _hash_password(password, str(user.get("salt", "")))
    except ValueError:
        return False
    return secrets.compare_digest(digest, str(user.get("hash", "")))


def bootstrap_login_active() -> bool:
    """Until an admin user exists, 'admin' can sign in to /server with the admin token as password."""
    return not any(item.get("role") == "admin" for item in load_users())


def role_at_least(role: str, minimum: str) -> bool:
    return role in USER_ROLES and USER_ROLES.index(role) >= USER_ROLES.index(minimum)


def _session_secret() -> bytes:
    configured = os.getenv("REPORTING_SESSION_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    # Derived from the admin token: rotating the admin token signs every user out.
    return hmac.new(os.getenv("REPORTING_ADMIN_TOKEN", "").encode("utf-8"), b"server-session-v1", hashlib.sha256).digest()


def session_lifetime_seconds() -> int:
    try:
        hours = float(os.getenv("REPORTING_SESSION_HOURS", "8"))
    except ValueError:
        hours = 8
    return int(max(0.25, min(hours, 24 * 30)) * 3600)


def make_session_cookie(username: str, version: int, bootstrap: bool = False) -> str:
    body = {"u": username, "v": version, "exp": int(time.time()) + session_lifetime_seconds(), "n": secrets.token_hex(8)}
    if bootstrap:
        body["b"] = 1
    encoded = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_session_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def read_session_cookie(value: str) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    encoded, signature = value.rsplit(".", 1)
    expected = hmac.new(_session_secret(), encoded.encode("ascii", "ignore"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(signature, expected):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict) or int(body.get("exp", 0)) <= time.time():
        return None
    return body


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
    # Jasper isForPrompting=false + defaultValueExpression: never asked, computed from other parameters.
    prompt: bool = True
    expression: str = ""


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
                raise ValueError("An ISODate literal must be a string")
            try:
                parsed_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError(f"Invalid ISODate value: {raw_date}") from None
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            return parsed_date.astimezone(timezone.utc)
        if set(node) == {"$literalObjectId"}:
            try:
                return ObjectId(str(node["$literalObjectId"]))
            except Exception:
                raise ValueError("Invalid ObjectId literal") from None
        if set(node) == {"$literalInt64"}:
            try:
                return Int64(int(node["$literalInt64"]))
            except (ValueError, TypeError, OverflowError):
                raise ValueError("Invalid NumberLong literal") from None
        if set(node) == {"$paramIn"}:
            # Optional multi-value filter: comma separated values -> {"$in": [...]}, empty -> match everything.
            raw_value = params.get(str(node["$paramIn"]))
            items = [
                piece.strip().strip("'\"").strip()
                for piece in str(raw_value if raw_value is not None else "").split(",")
            ]
            items = [item for item in items if item]
            if len(items) > 200 or any(len(item) > 120 for item in items):
                raise ValueError(f"{node['$paramIn']}: at most 200 values (each up to 120 characters)")
            return {"$in": items} if items else {"$nin": []}
        if "$param" in node and set(node).issubset({"$param", "boundary"}):
            name = str(node["$param"])
            if name not in params:
                raise ValueError(f"Pipeline parameter is not defined: {name}")
            value = params[name]
            if isinstance(value, date) and not isinstance(value, datetime):
                boundary = str(node.get("boundary", "start"))
                if boundary not in {"start", "end"}:
                    raise ValueError(f"The boundary of {name} must be start or end")
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
            raise ValueError(f"Parameter {match.group(1)} is not valid in a collection name (only letters, numbers, _ and -)")
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


def _mongo_sort_key(value: Any, descending: bool = False) -> tuple:
    """Sort key following MongoDB's cross-type BSON order (no collation).

    MinKey < empty array < null/missing < numbers < strings < objects < arrays < binary < ObjectId
    < booleans < dates < timestamps < regex. Every key is (type rank, comparable), so mixed-type
    fields never raise TypeError. A non-empty array sorts by its smallest element ascending and by
    its largest element descending, like MongoDB.
    """
    if isinstance(value, list):
        if not value:
            return (0,)
        keys = [_mongo_sort_key(item, descending) for item in value]
        return max(keys) if descending else min(keys)
    if value is None:
        return (1,)
    if isinstance(value, bool):  # before int: bool is an int subclass in Python
        return (8, value)
    if isinstance(value, (int, float, Decimal)):
        if (isinstance(value, float) and math.isnan(value)) or (isinstance(value, Decimal) and value.is_nan()):
            return (2, -math.inf, 0)  # MongoDB sorts NaN below every other number
        return (2, value, 1)
    if hasattr(value, "to_decimal"):  # bson Decimal128
        number = value.to_decimal()
        return (2, -math.inf, 0) if number.is_nan() else (2, number, 1)
    if isinstance(value, str):
        return (3, value)
    if isinstance(value, dict):
        return (4, tuple((str(key), _mongo_sort_key(item)) for key, item in value.items()))
    if isinstance(value, (bytes, bytearray)):
        return (6, bytes(value))
    if isinstance(value, ObjectId):
        return (7, value.binary)
    if isinstance(value, datetime):
        moment = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
        return (9, moment)
    if isinstance(value, date):
        return (9, datetime(value.year, value.month, value.day))
    return (12, type(value).__name__, repr(value))


def _mongo_date(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, daytime.min, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise ValueError("Invalid numeric date in the $toDate fallback") from None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"The $toDate fallback could not parse the ISO date: {value}") from None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    raise ValueError(f"The $toDate fallback does not support this value type: {type(value).__name__}")


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
            raise ValueError(f"The read fallback does not support the match operator: {operator}")
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


# What the Python read fallback can run. Anything else must fail loudly: evaluating an unknown
# operator as a plain object would silently put wrong values into a report.
FALLBACK_STAGES = {"$match", "$addFields", "$set", "$lookup", "$project", "$limit", "$sort", "$skip",
                   "$unionWith", "$replaceRoot", "$replaceWith"}
FALLBACK_EXPRESSION_OPERATORS = {"$toDate", "$arrayElemAt", "$ifNull", "$mergeObjects", "$literal"}
FALLBACK_MATCH_OPERATORS = {"$and", "$or", "$nor", "$eq", "$ne", "$in", "$nin", "$gte", "$gt", "$lte", "$lt", "$exists"}


def _expression_operators(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.startswith("$"):
                found.add(key)
                if key == "$literal":
                    continue
            _expression_operators(value, found)
    elif isinstance(node, list):
        for item in node:
            _expression_operators(item, found)


def _fallback_unsupported(pipeline: list[Any]) -> list[str]:
    """Stages/operators in `pipeline` (and $unionWith sub-pipelines) the read fallback cannot run."""
    problems: set[str] = set()
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            problems.add("(stage with more than one operator)")
            continue
        operator, spec = next(iter(stage.items()))
        if operator not in FALLBACK_STAGES:
            problems.add(operator)
            continue
        used: set[str] = set()
        if operator in {"$addFields", "$set", "$project", "$replaceRoot", "$replaceWith"}:
            _expression_operators(spec, used)
            problems.update(op for op in used if op not in FALLBACK_EXPRESSION_OPERATORS)
        elif operator == "$match":
            _expression_operators(spec, used)
            problems.update(op for op in used if op not in FALLBACK_MATCH_OPERATORS)
        elif operator == "$lookup" and not {"from", "localField", "foreignField", "as"}.issubset(spec):
            problems.add("$lookup (pipeline form)")
        elif operator == "$unionWith" and isinstance(spec, dict):
            problems.update(_fallback_unsupported(spec.get("pipeline", [])))
    return sorted(problems)


def _referenced_collections(pipeline: list[Any]) -> list[str]:
    names: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for operator in ("$lookup", "$graphLookup"):
                spec = node.get(operator)
                if isinstance(spec, dict) and isinstance(spec.get("from"), str):
                    names.append(spec["from"])
            union = node.get("$unionWith")
            if isinstance(union, str):
                names.append(union)
            elif isinstance(union, dict) and isinstance(union.get("coll"), str):
                names.append(union["coll"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(pipeline)
    return list(dict.fromkeys(names))


def _denied_collections(collection: Any, pipeline: list[Any], max_time_ms: int) -> list[str]:
    """Collections of this report that the MongoDB user cannot read with find (error code 13)."""
    denied: list[str] = []
    for name in [collection.name, *_referenced_collections(pipeline)]:
        try:
            next(iter(collection.database[name].find({}, {"_id": 1}, max_time_ms=max_time_ms).limit(1)), None)
        except PyMongoError as error:
            if getattr(error, "code", None) == 13:
                denied.append(name)
            else:
                raise
    return list(dict.fromkeys(denied))


def _evaluate_expression(document: dict[str, Any], expression: Any) -> Any:
    if isinstance(expression, str) and expression.startswith("$$"):
        name, _, rest = expression[2:].partition(".")
        if name not in {"ROOT", "CURRENT"}:
            raise ValueError(f"The read fallback does not support the variable: $${name}")
        return _path_value(document, rest) if rest else document
    if isinstance(expression, str) and expression.startswith("$"):
        return _path_value(document, expression[1:])
    if isinstance(expression, list):
        return [_evaluate_expression(document, item) for item in expression]
    if isinstance(expression, dict):
        operators = [key for key in expression if isinstance(key, str) and key.startswith("$")]
        if operators and (len(expression) != 1 or operators[0] not in FALLBACK_EXPRESSION_OPERATORS):
            raise ValueError(f"The read fallback does not support the expression operator(s): {', '.join(operators)}")
        if len(expression) == 1 and "$literal" in expression:
            return copy.deepcopy(expression["$literal"])
        if len(expression) == 1 and "$mergeObjects" in expression:
            parts = expression["$mergeObjects"]
            merged: dict[str, Any] = {}
            for part in parts if isinstance(parts, list) else [parts]:
                value = _evaluate_expression(document, part)
                if value is None:
                    continue
                if not isinstance(value, dict):
                    raise ValueError("The $mergeObjects fallback accepts objects only")
                merged.update(value)
            return merged
        if len(expression) == 1 and "$toDate" in expression:
            return _mongo_date(_evaluate_expression(document, expression["$toDate"]))
        if len(expression) == 1 and "$arrayElemAt" in expression:
            operands = expression["$arrayElemAt"]
            if not isinstance(operands, list) or len(operands) != 2:
                raise ValueError("The $arrayElemAt fallback needs two operands")
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
                raise ValueError("The $ifNull fallback needs an array")
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
    unsupported = _fallback_unsupported(pipeline)
    if unsupported:
        raise ValueError("The read fallback does not support these stages/operators: " + ", ".join(unsupported))
    scan_limit = min(max(max_rows * 10, 20_000), 100_000)
    return _run_fallback_stages(collection, pipeline, max_time_ms=max_time_ms, scan_limit=scan_limit)


def _fallback_source(collection: Any, initial_filter: dict[str, Any], *, max_time_ms: int, scan_limit: int) -> list[dict[str, Any]]:
    try:
        documents = list(collection.find(initial_filter, max_time_ms=max_time_ms).limit(scan_limit + 1))
    except PyMongoError as read_error:
        if getattr(read_error, "code", None) == 13:
            raise MongoReadAccessError(
                f"MongoDB rejected find/read on the collection '{collection.database.name}.{collection.name}' "
                "for this connection."
            ) from None
        raise
    if len(documents) > scan_limit:
        raise ValueError(
            f"There is no aggregate permission and the read fallback reached its safety limit of {scan_limit:,} source documents. "
            "Narrow the pipeline with $match stages first."
        )
    return documents


def _run_fallback_stages(collection: Any, pipeline: list[dict[str, Any]], *, max_time_ms: int, scan_limit: int) -> list[dict[str, Any]]:
    # Push only an initial $match to MongoDB find(); keep it in the local pipeline
    # too so the fallback remains correct for the supported Mongo match semantics.
    initial_filter: dict[str, Any] = {}
    if pipeline and "$match" in pipeline[0]:
        initial_filter = pipeline[0]["$match"]
    documents = _fallback_source(collection, initial_filter, max_time_ms=max_time_ms, scan_limit=scan_limit)

    for stage in pipeline:
        operator, specification = next(iter(stage.items()))
        if operator == "$match":
            documents = [doc for doc in documents if _matches_document(doc, specification)]
        elif operator == "$unionWith":
            spec = {"coll": specification} if isinstance(specification, str) else specification
            other = collection.database[str(spec["coll"])]
            documents = documents + _run_fallback_stages(
                other, list(spec.get("pipeline") or []), max_time_ms=max_time_ms, scan_limit=scan_limit
            )
            if len(documents) > scan_limit:
                raise ValueError(f"After $unionWith the read fallback went past its safety limit of {scan_limit:,} documents")
        elif operator in {"$replaceRoot", "$replaceWith"}:
            new_root = specification.get("newRoot") if operator == "$replaceRoot" else specification
            replaced: list[dict[str, Any]] = []
            for document in documents:
                value = _evaluate_expression(document, new_root)
                if not isinstance(value, dict):
                    raise ValueError(f"The result of {operator} must be an object")
                replaced.append(value)
            documents = replaced
        elif operator in {"$addFields", "$set"}:
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
                raise ValueError("The read fallback supports only localField/foreignField $lookup")
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
                            f"MongoDB rejected find/read on the $lookup collection '{foreign_collection.database.name}.{foreign_collection.name}' "
                            "for this connection."
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
            # Stable sorts from the last key to the first give MongoDB's multi-key order.
            for field, direction in reversed(list(specification.items())):
                descending = float(direction) < 0
                documents.sort(
                    key=lambda doc, field=field, descending=descending: _mongo_sort_key(_path_value(doc, field), descending),
                    reverse=descending,
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
                raise ValueError("The query has a /* comment without a closing */")
            for cursor in range(index, end + 2):
                if output[cursor] not in "\r\n":
                    output[cursor] = " "
            index = end + 2
            continue
        index += 1
    return "".join(output)


def _extract_literal_array(source: str, start: int) -> tuple[str, int]:
    if start >= len(source) or source[start] != "[":
        raise ValueError("The pipeline literal must start with [")
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
                raise ValueError("The brackets in the pipeline do not match")
            if not stack:
                return source[start:index + 1], index + 1
    raise ValueError("The closing ] of the pipeline array is missing")


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
            raise ValueError(f"Unsupported Mongo Shell syntax near: {snippet!r}")
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
            raise ValueError(f"Mongo Shell literal: expected token{expected}")
        token = tokens[cursor]
        cursor += 1
        return token

    def parse_value() -> Any:
        kind, value = take()
        if value == "[":
            result = []
            while not peek("]"):
                if cursor >= len(tokens):
                    raise ValueError("The closing ] of a Mongo Shell array is missing")
                result.append(parse_value())
                if peek(","):
                    take(",")
                    if peek("]"):
                        break
                elif not peek("]"):
                    raise ValueError("A comma is expected between Mongo Shell array values")
            take("]")
            return result
        if value == "{":
            result: dict[str, Any] = {}
            while not peek("}"):
                if cursor >= len(tokens):
                    raise ValueError("The closing } of a Mongo Shell object is missing")
                key_kind, raw_key = take()
                if key_kind == "string":
                    key = ast.literal_eval(raw_key)
                elif key_kind == "identifier":
                    key = raw_key
                else:
                    raise ValueError("A Mongo Shell object key must be quoted text or a simple name")
                take(":")
                result[str(key)] = parse_value()
                if peek(","):
                    take(",")
                    if peek("}"):
                        break
                elif not peek("}"):
                    raise ValueError("A comma is expected between Mongo Shell object properties")
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
                        raise ValueError(f"{value} needs a valid integer") from None
                if value in {"ISODate", "Date"} and isinstance(argument, str):
                    return {"$literalDate": argument}
                if value == "ObjectId" and isinstance(argument, str) and re.fullmatch(r"[0-9a-fA-F]{24}", argument):
                    return {"$literalObjectId": argument}
                raise ValueError(f"The Mongo Shell constructor {value}(...) is not a supported literal")
            raise ValueError(f"The Mongo Shell value {value!r} is not a supported literal")
        raise ValueError(f"The Mongo Shell token {value!r} is not supported")

    result = parse_value()
    if cursor != len(tokens):
        raise ValueError(f"Extra token after the Mongo Shell literal: {tokens[cursor][1]!r}")
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


_JASPER_AGGREGATE = re.compile(r"""['"]?aggregate['"]?\s*:\s*(?:"([^"]+)"|'([^']+)'|(\$P\{[A-Za-z_][A-Za-z0-9_]*\}))""")
_JASPER_PIPELINE = re.compile(r"""['"]?pipeline['"]?\s*:\s*\[""")
_JASPER_FIND = re.compile(r"""['"]?(?:findQuery|collectionName)['"]?\s*:""")


def split_jasper_query(source: Any) -> tuple[str, str] | None:
    """Jaspersoft Studio MongoDbQuery text → (collection, pipeline literal), or None for other styles.

    Accepts { runCommand: { aggregate: 'coll', pipeline: [...] } } (also without the runCommand
    wrapper) and { collectionName: 'coll', findQuery: {...} }. Nothing is executed.
    """
    if not isinstance(source, str):
        return None
    text = _strip_query_comments(source).strip()
    if not text.startswith("{"):
        return None
    aggregate = _JASPER_AGGREGATE.search(text)
    pipeline = _JASPER_PIPELINE.search(text)
    if aggregate and pipeline:
        literal, _end = _extract_literal_array(text, pipeline.end() - 1)  # other keys (cursor, allowDiskUse) are not needed
        return aggregate.group(1) or aggregate.group(2) or aggregate.group(3), literal
    if _JASPER_FIND.search(text):
        import jrxml_import
        return jrxml_import._extract_query(text)
    return None


def pipeline_parameter_names(pipeline: Any) -> list[str]:
    """$P{...} names used in a parsed pipeline, in order of first use."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("$param") if "$param" in node else node.get("$paramIn") if set(node) == {"$paramIn"} else None
            if name is not None:
                if str(name) not in found:
                    found.append(str(name))
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(pipeline)
    return found


def parse_query_source(language: str, source: str) -> list[dict[str, Any]]:
    """Convert supported data-only editor styles to a Mongo aggregation pipeline."""
    language = str(language or "aggregation-json").lower()
    if language not in QUERY_LANGUAGES:
        raise ValueError("The query mode must be aggregation-json, mongosh, javascript or python")
    if not isinstance(source, str) or len(source) > 2_000_000:
        raise ValueError("The query text must be smaller than 2 MB")
    if language == "python":
        try:
            module = ast.parse(_replace_jasper_parameters(source), mode="exec")
            if len(module.body) != 1:
                raise ValueError("In Python mode give only a pipeline = [...] literal assignment")
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
                raise ValueError("In Python mode give only a pipeline = [...] literal assignment; no code is executed")
            pipeline = ast.literal_eval(value_node)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"The Python pipeline could not be read: {exc}") from None
    elif (jasper := split_jasper_query(source)) is not None:
        # Jaspersoft Studio MongoDbQuery ({runCommand: {aggregate, pipeline}}) works in every text mode.
        pipeline = _parse_mongosh_literal(jasper[1])
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
                raise ValueError(f"In {language} mode give the pipeline in the {expected} literal format")
            else:
                array_start = uncommented.find("[", match.end())
                if array_start < 0:
                    raise ValueError("The [ of the pipeline literal is missing")
                literal, array_end = _extract_literal_array(uncommented, array_start)
                remainder = uncommented[array_end:].strip()
                valid_remainder = re.fullmatch(r"\)\s*;?", remainder) if language == "mongosh" else re.fullmatch(r";?", remainder)
                if not valid_remainder:
                    raise ValueError("Extra executable code after the pipeline literal is not allowed")
        if language in {"mongosh", "javascript"}:
            pipeline = _parse_mongosh_literal(literal)
        else:
            try:
                pipeline = json.loads(literal)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Pipeline JSON line {exc.lineno}, column {exc.colno}: {exc.msg}. For Mongo Shell syntax choose the query style 'Mongo Shell'.") from None
    if not isinstance(pipeline, list) or not pipeline or not all(isinstance(stage, dict) for stage in pipeline):
        raise ValueError("The pipeline must be a non-empty array of stage objects")
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
                raise ValueError(f"Define the query parameter {pipeline['$paramIn']} in the report questions first")
            return
        if "$param" in pipeline and set(pipeline).issubset({"$param", "boundary"}):
            name = str(pipeline["$param"])
            if name not in allowed_names:
                raise ValueError(f"Define the query parameter {name} in the report questions first")
            if "boundary" in pipeline and pipeline["boundary"] not in {"start", "end"}:
                raise ValueError(f"The boundary of parameter {name} must be 'start' or 'end'")
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
                raise ValueError("A reporting pipeline must be read-only; $out and $merge are not allowed")
            if key in {"$function", "$accumulator", "$where"}:
                raise ValueError("$function, $accumulator and $where (which run JavaScript) are not allowed in a pipeline")
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
        raise ValueError("A canvas object position must be a JSON object")
    result: dict[str, float] = {}
    for key, fallback in default.items():
        value = raw.get(key, fallback)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Canvas position {key} must be a valid number")
        result[key] = round(float(value), 2)
    if result["x"] < 0 or result["y"] < 0 or result["w"] < 24 or result["h"] < 20:
        raise ValueError("A canvas object must be inside the page and at least 24x20 pixels")
    if result["x"] + result["w"] > 794 or result["y"] + result["h"] > 1123:
        raise ValueError("A canvas object cannot go outside the A4 page")
    return result


def _dynamic_report(payload: dict[str, Any], source_path: Path | None = None) -> ReportDefinition:
    report_id = str(payload.get("id", "")).strip()
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        raise ValueError("Use only letters, numbers, underscores and hyphens in a report ID")
    title = str(payload.get("title", report_id)).strip() or report_id
    collection = str(payload.get("collection", "")).strip()
    connection_id = str(payload.get("connectionId", "default")).strip()
    folder = normalize_folder_path(payload.get("folder", "General"))
    if not collection and str(payload.get("queryLanguage", "")).lower() != "python":
        jasper = split_jasper_query(payload.get("querySource"))  # a Jasper runCommand names its collection
        collection = jasper[0].strip() if jasper else ""
    if not collection:
        raise ValueError("The MongoDB collection name is required")
    if not COLLECTION_TEMPLATE.fullmatch(collection) or collection.lower().startswith("system."):
        raise ValueError("A collection name may contain only letters, numbers, _ - . and $P{parameter}")
    if not CONNECTION_ID_PATTERN.fullmatch(connection_id):
        raise ValueError("Invalid connection ID format")

    raw_parameters = payload.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raise ValueError("parameters must be a JSON array")
    parameters: list[ParameterSpec] = []
    seen_names: set[str] = set()
    for raw in raw_parameters:
        if not isinstance(raw, dict):
            raise ValueError("Each parameter must be a JSON object")
        name = str(raw.get("name", "")).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            raise ValueError(f"Invalid parameter name: {name}")
        if name in SIGNED_LINK_KEYS:
            raise ValueError(f"The parameter name {name} is reserved for signed links; choose another name")
        if name in seen_names:
            raise ValueError(f"Duplicate parameter name: {name}")
        seen_names.add(name)
        kind = str(raw.get("type", raw.get("kind", "string"))).lower()
        if kind not in {"date", "string", "integer", "number", "boolean"}:
            raise ValueError(f"The type of {name} must be date, string, integer, number or boolean")
        allowed = raw.get("allowedValues", [])
        if not isinstance(allowed, list):
            raise ValueError(f"{name}.allowedValues must be a JSON array")
        prompt = raw.get("prompt", True) is not False
        expression = str(raw.get("expression", "") or "").strip()
        if not prompt:
            # Derived parameter (Jasper isForPrompting=false): computed from parameters defined above it.
            if not expression:
                raise ValueError(f"{name} is not asked from the user, so its expression (default value) is required")
            if not jexpr.check_syntax(expression):
                raise ValueError(f"The expression of {name} could not be understood: {expression[:80]}")
            for token_kind, ref in designer_render.tokens_in(expression):
                if token_kind != "P":
                    raise ValueError(f"The expression of {name} may use only $P{{...}} parameters")
                if ref not in {spec.name for spec in parameters}:
                    raise ValueError(f"$P{{{ref}}} in the expression of {name} must be defined before {name}")
        parameters.append(ParameterSpec(
            name=name,
            kind=kind,
            label=str(raw.get("label", name)),
            description=str(raw.get("description", "")),
            required=bool(raw.get("required", False)),
            default=raw.get("default"),
            allowed_values=tuple(allowed),
            max_length=min(max(int(raw.get("maxLength", 200)), 1), 2000),
            prompt=prompt,
            expression=expression[:2000] if not prompt else "",
        ))

    # A collection chosen by a URL parameter must be limited to a fixed list; otherwise a caller could
    # point the report at any collection in the database (e.g. a users collection).
    specs_by_name = {spec.name: spec for spec in parameters}
    for collection_parameter in re.findall(r"\$P\{([A-Za-z_][A-Za-z0-9_]*)\}", collection):
        spec = specs_by_name.get(collection_parameter)
        if spec is None:
            raise ValueError(f"Define the collection parameter {collection_parameter} in the report questions")
        if not spec.prompt:
            raise ValueError(f"The collection-name parameter {collection_parameter} cannot be computed; ask the user and give allowedValues")
        if not spec.allowed_values:
            raise ValueError(
                f"The collection name is built from the parameter {collection_parameter}; this parameter must have allowedValues "
                "(a list of allowed values)"
            )
        bad_values = [value for value in spec.allowed_values if not COLLECTION_PARAM_VALUE.fullmatch(str(value))]
        if bad_values:
            raise ValueError(
                f"The allowedValues of {collection_parameter} may contain only letters, numbers, _ and -: {bad_values[:3]}"
            )

    query_language = str(payload.get("queryLanguage", "aggregation-json")).lower()
    query_source = payload.get("querySource")
    if query_language not in QUERY_LANGUAGES:
        raise ValueError("queryLanguage must be aggregation-json, mongosh, javascript or python")
    if "querySource" in payload and not isinstance(query_source, str):
        raise ValueError("querySource must be a string")
    pipeline = parse_query_source(query_language, query_source) if isinstance(query_source, str) else payload.get("pipeline")
    if not isinstance(pipeline, list) or not pipeline or not all(isinstance(stage, dict) for stage in pipeline):
        raise ValueError("pipeline must be a non-empty JSON array of stage objects")
    _validate_pipeline_parameter_names(pipeline, seen_names)
    _validate_read_only_pipeline(pipeline)
    date_range = payload.get("dateRange")
    if date_range is not None:
        if not isinstance(date_range, dict):
            raise ValueError("dateRange must be a JSON object")
        for key in ("field", "startParameter", "endParameter"):
            if not str(date_range.get(key, "")).strip():
                raise ValueError(f"dateRange.{key} is required")
        names = {spec.name: spec for spec in parameters}
        if names.get(date_range["startParameter"], ParameterSpec("_missing")).kind != "date":
            raise ValueError("The startParameter of dateRange must be of type date")
        if names.get(date_range["endParameter"], ParameterSpec("_missing")).kind != "date":
            raise ValueError("The endParameter of dateRange must be of type date")

    columns = payload.get("columns") or _infer_pipeline_columns(pipeline)
    if not isinstance(columns, list) or not columns or not all(isinstance(column, str) for column in columns):
        raise ValueError("No output columns found in a $project, $group or $count stage; list the fields under Output columns")
    formats = payload.get("formats", sorted(OUTPUT_FORMATS))
    if not isinstance(formats, list) or not formats or any(not isinstance(value, str) or value.lower() not in OUTPUT_FORMATS for value in formats):
        raise ValueError("Report formats must be one or more of csv, xlsx and pdf")
    formats = list(dict.fromkeys(value.lower() for value in formats))
    default_format = str(payload.get("defaultFormat", formats[0])).lower()
    if default_format not in formats:
        raise ValueError("The default format must be one of the enabled report formats")
    raw_layout = payload.get("layout", {})
    if not isinstance(raw_layout, dict):
        raise ValueError("layout must be a JSON object")
    raw_labels = raw_layout.get("columnLabels", {})
    if not isinstance(raw_labels, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in raw_labels.items()):
        raise ValueError("layout.columnLabels must be a JSON object of field names and labels")
    layout = {
        "title": str(raw_layout.get("title", title))[:200],
        "intro": str(raw_layout.get("intro", payload.get("description", "")))[:2000],
        "showTitle": raw_layout.get("showTitle", True) is True,
        "showIntro": raw_layout.get("showIntro", True) is True,
        "columnLabels": {column: (str(raw_labels.get(column, column)).strip() or column)[:200] for column in columns},
    }
    paragraphs = raw_layout.get("paragraphs", [])
    if not isinstance(paragraphs, list) or len(paragraphs) > 20 or any(not isinstance(text, str) or len(text) > 2000 for text in paragraphs):
        raise ValueError("The report canvas allows at most 20 text blocks of up to 2000 characters each")
    layout["paragraphs"] = paragraphs
    raw_positions = raw_layout.get("positions", {})
    if not isinstance(raw_positions, dict):
        raise ValueError("layout.positions must be a JSON object")
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
        raise ValueError("The canvas allows at most 100 URL value fields")
    parameter_fields: list[dict[str, Any]] = []
    seen_field_ids: set[str] = set()
    for index, field in enumerate(raw_parameter_fields):
        if not isinstance(field, dict):
            raise ValueError("Each canvas URL value field must be a JSON object")
        field_id = str(field.get("id", f"field-{index + 1}"))
        name = str(field.get("name", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", field_id) or field_id in seen_field_ids:
            raise ValueError("Each canvas URL value field ID must be unique and valid")
        if name not in parameter_names:
            raise ValueError(f"The question of a canvas URL value field is not defined: {name}")
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
            raise ValueError("The canvas image must be PNG/JPEG and smaller than 2 MB")
        try:
            base64.b64decode(image_match.group(2), validate=True)
        except ValueError:
            raise ValueError("Invalid canvas image data") from None
        layout["imageDataUrl"] = image_data
    raw_design = payload.get("design")
    design = None
    if raw_design:
        design = designer_render.validate_design(raw_design, columns, parameter_names)
    display_names = [layout["columnLabels"][column].casefold() for column in columns]
    if len(display_names) != len(set(display_names)):
        raise ValueError("Give every exported column a different display name")

    def build_pipeline(params: dict[str, Any]) -> list[dict[str, Any]]:
        resolved = copy.deepcopy(pipeline)
        if date_range:
            start_name = str(date_range["startParameter"])
            end_name = str(date_range["endParameter"])
            start_value = params.get(start_name)
            end_value = params.get(end_name)
            if not isinstance(start_value, date) or not isinstance(end_value, date):
                raise ValueError("dateRange needs its start/end parameters")
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
                raise ValueError(f"The pipeline has no $match on the dateRange field: {field}")
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
                **({"prompt": False, "expression": item.expression} if not item.prompt else {}),
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
            raise ValueError("connections in connections.json must be an array")
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
            folder = str(item.get("folder", "") or "").strip()
            if folder:
                try:
                    profile["folder"] = normalize_folder_path(folder)
                except ValueError:
                    LOG.warning("Data adapter %s has an invalid folder; it is shown at the top level", profile["id"])
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
        loaded_from: dict[str, Path] = {}
        for path in sorted(directory.rglob("*.json")):
            # One broken or hand-edited file must not take every other report (and the app) down.
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(payload, dict):
                    raise ValueError("A report definition must be a JSON object")
                definition = _dynamic_report(payload, path)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                LOG.error("Skipping invalid report definition %s: %s", path, exc)
                continue
            if definition.report_id in loaded_from:
                LOG.warning(
                    "Duplicate report ID %s in %s and %s; using %s",
                    definition.report_id, loaded_from[definition.report_id], path, path,
                )
            loaded_from[definition.report_id] = path
            registry[definition.report_id] = definition
    return registry


def parse_scalar(spec: ParameterSpec, raw: str) -> Any:
    if spec.kind == "date":
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f"{spec.name} must be in YYYY-MM-DD format") from None
    if spec.kind == "integer":
        try:
            value: Any = int(raw)
        except ValueError:
            raise ValueError(f"{spec.name} must be a whole number") from None
    elif spec.kind == "number":
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{spec.name} must be a number") from None
        if not math.isfinite(value):
            raise ValueError(f"{spec.name} must be a finite number")
    elif spec.kind == "boolean":
        mapping = {"1": True, "true": True, "0": False, "false": False}
        try:
            value = mapping[raw.lower()]
        except KeyError:
            raise ValueError(f"{spec.name} must be 1/0 or true/false") from None
    else:
        value = raw
        if len(value) > spec.max_length:
            raise ValueError(f"{spec.name} is too long")
    if spec.allowed_values and value not in spec.allowed_values:
        raise ValueError(f"The value of {spec.name} is not in the allowed list")
    return value


def validate_parameters(definition: ReportDefinition, query_pairs: list[tuple[str, str]]) -> tuple[dict[str, Any], dict[str, str]]:
    keys = [key for key, _ in query_pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"Duplicate query parameter(s): {', '.join(duplicates)}")
    raw = dict(query_pairs)
    metadata = {key: raw.pop(key, "") for key in METADATA_KEYS}
    specs = {spec.name: spec for spec in definition.parameters if spec.prompt}
    computed = [spec for spec in definition.parameters if not spec.prompt]
    supplied_computed = sorted(set(raw) & {spec.name for spec in computed})
    if supplied_computed:
        raise ValueError(f"These parameters are calculated by the report; do not send them in the URL: {', '.join(supplied_computed)}")
    unknown = sorted(set(raw) - set(specs))
    if unknown:
        raise ValueError(f"Unconfigured parameter(s): {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for key, spec in specs.items():
        if key not in raw or raw[key] == "":
            if spec.required and spec.default is None:
                raise ValueError(f"{key} is a required parameter")
            if spec.default is not None:
                values[key] = parse_scalar(spec, str(spec.default))
            continue
        values[key] = parse_scalar(spec, raw[key])
    date_values = [
        values[spec.name]
        for spec in definition.parameters
        if spec.kind == "date" and spec.prompt and spec.name in values
    ]
    if len(date_values) >= 2 and date_values[0] > date_values[1]:
        raise ValueError("The start date must be on or before the end date")
    for spec in computed:
        values[spec.name] = _compute_parameter(spec, values)
    return values, metadata


def _compute_parameter(spec: ParameterSpec, values: dict[str, Any]) -> Any:
    """Evaluate a derived parameter's Java-style expression (e.g. new GregorianCalendar(...).getTime())."""
    try:
        result = jexpr.evaluate(spec.expression, lambda kind, name: values.get(name) if kind == "P" else None)
    except jexpr.JExprError as error:
        raise ValueError(f"Parameter {spec.name} could not be calculated: {error}") from None
    if result is None:
        return None
    try:
        if spec.kind == "date":
            if isinstance(result, jexpr.Calendar):
                result = result.moment
            moment = jexpr.to_datetime(result)
            # Times are UTC, like the rest of the service (MongoDB stores dates in UTC).
            return moment.replace(tzinfo=timezone.utc)
        if spec.kind == "integer":
            return int(jexpr.to_number(result))
        if spec.kind == "number":
            return float(jexpr.to_number(result))
        if spec.kind == "boolean":
            if not isinstance(result, bool):
                raise jexpr.JExprError("true/false expected")
            return result
        return jexpr.java_str(result)
    except (jexpr.JExprError, ValueError, OverflowError) as error:
        raise ValueError(f"The value of parameter {spec.name} is not a valid {spec.kind}: {error}") from None


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
    writer = csv.writer(buffer)
    writer.writerow([designer_render.csv_safe(name) for name in display_columns])
    for row in rows:
        writer.writerow([designer_render.csv_safe(_export_cell_value(row.get(key, ""))) for key in columns])
    return buffer.getvalue().encode("utf-8-sig")


def render_xlsx(title: str, intro: str, paragraphs: list[str], columns: tuple[str, ...], rows: list[dict[str, Any]], column_labels: dict[str, str] | None = None, show_title: bool = True, show_intro: bool = True) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("Install openpyxl for XLSX output") from exc
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
    designer_render.single_sheet_print_setup(sheet, heading_row)
    designer_render.neutralize_formulas(sheet)
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
        raise RuntimeError("Install reportlab for PDF output") from exc
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
        # ReportLab has no clipRect(); clip with a rectangular path so long text stays inside its box.
        clip = canvas.beginPath()
        clip.rect(x, page_height - y - height, width, height)
        canvas.clipPath(clip, stroke=0, fill=0)
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
        raise ValueError("The report definition XML is larger than 5 MB")
    root = ET.fromstring(raw_xml)
    if root.tag != "mongoReportDefinition" or root.attrib.get("version") != "1":
        raise ValueError("This is not a supported Mongo report definition XML file")
    payload = root.find("definition")
    if payload is None or payload.attrib.get("encoding") != "json" or not payload.text:
        raise ValueError("No saved configuration was found inside the XML report definition")
    definition = json.loads(payload.text)
    if not isinstance(definition, dict):
        raise ValueError("No report definition object was found in the XML file")
    return definition


MEDIA_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}


def render_report_output(definition: ReportDefinition, parameters: dict[str, Any], rows: list[dict[str, Any]], output_format: str,
                         subreport_loader: Any = None) -> tuple[bytes, str]:
    """Turn report rows into the requested file. Shared by /generate and the admin 'Run report'.
    `subreport_loader` fills the design's Subreport elements (each runs its own published report's query)."""
    token = designer_render.SUBREPORT_LOADER.set(subreport_loader)
    try:
        return _render_report_output(definition, parameters, rows, output_format)
    finally:
        designer_render.SUBREPORT_LOADER.reset(token)


def _render_report_output(definition: ReportDefinition, parameters: dict[str, Any], rows: list[dict[str, Any]], output_format: str) -> tuple[bytes, str]:
    layout = (definition.editor_config or {}).get("layout", {})
    display_title = str(layout.get("title") or definition.title)
    display_intro = str(layout.get("intro") or "")
    display_paragraphs = layout.get("paragraphs", [])
    image_data_url = str(layout.get("imageDataUrl") or "")
    show_title = layout.get("showTitle", True) is True
    show_intro = layout.get("showIntro", True) is True
    column_labels = layout.get("columnLabels", {})
    date_parameter_names = [spec.name for spec in definition.parameters if spec.kind == "date" and spec.prompt]
    date_range = " to ".join(str(parameters[name]) for name in date_parameter_names[:2] if name in parameters)
    band_design = (definition.editor_config or {}).get("design")
    design_rows = [{key: _safe_value(value) for key, value in row.items()} for row in rows]
    design_parameters = {key: (value.isoformat() if isinstance(value, date) else value) for key, value in parameters.items()}
    use_design_table = bool(band_design) and designer_render.has_tabular_layout(band_design)
    if output_format == "csv":
        body = (designer_render.render_design_csv(band_design, display_title, list(definition.columns), design_rows, design_parameters)
                if use_design_table else render_csv(definition.columns, rows, column_labels))
    elif output_format == "xlsx":
        body = (designer_render.render_design_xlsx(band_design, display_title, list(definition.columns), design_rows, design_parameters)
                if use_design_table else
                render_xlsx(display_title, display_intro, display_paragraphs, definition.columns, rows, column_labels, show_title, show_intro))
    elif band_design:
        body = designer_render.render_design_pdf(band_design, display_title, list(definition.columns), design_rows, design_parameters, definition.report_id)
    else:
        body = render_pdf(
            display_title, display_intro, display_paragraphs, image_data_url,
            definition.report_id, definition.columns, rows, date_range,
            column_labels, show_title, show_intro, layout.get("positions", {}),
            layout.get("parameterFields", []), parameters,
        )
    return body, MEDIA_TYPES[output_format]


def make_app() -> FastAPI:
    settings = Settings.from_env()
    initialize_data_directory()
    log_path = configure_file_logging()
    LOG.info("Reporting service %s starting; data directory %s; log file %s", SERVICE_VERSION, data_directory(), log_path or "off")
    ensure_admin_token()
    designer_token = os.getenv("REPORTING_DESIGNER_TOKEN", "").strip()
    if designer_token and len(designer_token) < ROLE_TOKEN_MIN_LENGTH:
        LOG.warning("REPORTING_DESIGNER_TOKEN is ignored: it must be at least %d characters", ROLE_TOKEN_MIN_LENGTH)
    LOG.info("Enabled areas: %s", ", ".join(sorted(enabled_areas())) or "none")
    reports_by_id = discover_reports()
    if os.getenv("REPORTING_ACCESS_MODE", "required").strip().lower() == "open":
        LOG.warning("REPORTING_ACCESS_MODE=open: /generate and /reports need no credentials; keep this service off public networks")
    elif not os.getenv("REPORTING_ACCESS_SECRET", "").strip():
        LOG.warning("REPORTING_ACCESS_SECRET is not set: /generate and /reports accept only the admin token")
    state: dict[str, Any] = {"clients": {}, "connection_signatures": {}, "revoked_sessions": set(), "login_failures": {}}

    def get_connection(connection_id: str) -> tuple[MongoClient, str]:
        profiles = {item["id"]: item for item in load_connection_profiles()}
        profile = profiles.get(connection_id)
        if profile is None:
            raise HTTPException(status_code=400, detail=f"MongoDB connection ID is not registered: {connection_id}")
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

    def _verify_signed_link(request: Request, report_file: str, secret: str) -> None:
        """Check `Signature` = hex HMAC-SHA256(secret, "/generate/<file>?<query before &Signature>")."""
        try:
            raw_query = request.scope.get("query_string", b"").decode("ascii")
        except UnicodeDecodeError:
            raise HTTPException(status_code=403, detail="The signed link query is not encoded correctly") from None
        parts = raw_query.split("&") if raw_query else []
        signature_parts = [part for part in parts if part.split("=", 1)[0] == "Signature"]
        if len(signature_parts) != 1 or parts[-1] != signature_parts[0]:
            raise HTTPException(status_code=403, detail="Signature must appear once, at the end of the link")
        provided = signature_parts[0].split("=", 1)[1] if "=" in signature_parts[0] else ""
        unsigned_query = "&".join(parts[:-1])
        message = f"/generate/{report_file}" + (f"?{unsigned_query}" if unsigned_query else "")
        expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(provided.lower(), expected):
            raise HTTPException(status_code=403, detail="The signed link Signature is not valid (the link was changed or the secret is wrong)")
        expires_values = request.query_params.getlist("Expires")
        if len(expires_values) != 1 or not expires_values[0].isdigit():
            raise HTTPException(status_code=403, detail="A signed link needs one Expires value (Unix seconds)")
        expires = int(expires_values[0])
        now = int(time.time())
        max_lifetime = int(os.getenv("REPORTING_SIGNED_LINK_MAX_SECONDS", "3600"))
        if expires <= now:
            raise HTTPException(status_code=403, detail="The signed link has expired")
        if expires - now > max_lifetime:
            raise HTTPException(status_code=403, detail=f"A signed link cannot be valid for more than {max_lifetime} seconds")

    def require_report_access(request: Request, report_file: str | None = None) -> str:
        """Who may list and run reports. Returns how access was granted (stored in the audit record).

        REPORTING_ACCESS_MODE=required (default) accepts any one of:
          * X-Report-Token header equal to REPORTING_ACCESS_SECRET (website server calls the service),
          * a signed /generate link (Expires + Signature made with REPORTING_ACCESS_SECRET),
          * X-Admin-Token (the admin and runner pages).
        REPORTING_ACCESS_MODE=open keeps the old behaviour for a private, network-protected setup.
        """
        mode = os.getenv("REPORTING_ACCESS_MODE", "required").strip().lower()
        if mode == "open":
            return "open"
        if mode != "required":
            raise HTTPException(status_code=503, detail="REPORTING_ACCESS_MODE must be 'required' or 'open'")
        secret = os.getenv("REPORTING_ACCESS_SECRET", "").strip()
        if secret and len(secret) < MIN_ACCESS_SECRET_LENGTH:
            raise HTTPException(
                status_code=503,
                detail=f"REPORTING_ACCESS_SECRET must be at least {MIN_ACCESS_SECRET_LENGTH} characters long",
            )
        report_token = request.headers.get("X-Report-Token", "")
        if secret and report_token and secrets.compare_digest(report_token, secret):
            return "report-token"
        admin_token = os.getenv("REPORTING_ADMIN_TOKEN", "")
        admin_header = request.headers.get("X-Admin-Token", "")
        if admin_token and admin_header and secrets.compare_digest(admin_header, admin_token):
            return "admin-token"
        if report_file is not None and "Signature" in request.query_params:
            if not secret:
                raise HTTPException(status_code=403, detail="REPORTING_ACCESS_SECRET is not set on the server, so signed links cannot be checked")
            _verify_signed_link(request, report_file, secret)
            return "signed-link"
        LOG.warning("Report access denied for %s", request.url.path)
        raise HTTPException(
            status_code=401,
            detail="Report access needs a signed link, the X-Report-Token header or the admin token",
        )

    def chosen_connection(payload: dict[str, Any], definition: ReportDefinition) -> str:
        """Data adapter for an admin preview/run: the one picked in the UI, else the report's own."""
        requested = str(payload.get("connectionId") or "").strip()
        if not requested:
            return definition.connection_id
        if requested not in {item["id"] for item in load_connection_profiles()}:
            raise ValueError(f"Data adapter is not registered: {requested}")
        return requested

    def admin_parameters(payload: dict[str, Any], definition: ReportDefinition) -> dict[str, Any]:
        raw_params = payload.get("parameters", {})
        if not isinstance(raw_params, dict):
            raise ValueError("parameters must be a JSON object")
        prompted = {spec.name for spec in definition.parameters if spec.prompt}
        # computed parameters are calculated on the server; ignore any value the UI sends for them
        pairs = [(str(key), str(value)) for key, value in raw_params.items() if key in prompted or key in METADATA_KEYS]
        parameters, _ = validate_parameters(definition, pairs)
        return parameters

    def run_report_rows(definition: ReportDefinition, parameters: dict[str, Any], connection_id: str, *, max_rows: int, limit_rows: bool) -> list[dict[str, Any]]:
        pipeline = [*definition.pipeline_builder(parameters), {"$limit": max_rows if limit_rows else max_rows + 1}]
        client, database = get_connection(connection_id)
        rows = _execute_report_pipeline(
            client[database][resolve_collection(definition.collection, parameters)],
            pipeline,
            max_time_ms=settings.max_time_ms,
            allow_disk_use=settings.allow_disk_use,
            max_rows=max_rows,
        )
        if not limit_rows and len(rows) > max_rows:
            raise HTTPException(status_code=413, detail=f"The report is larger than the row limit ({max_rows})")
        return rows

    def subreport_loader() -> Any:
        """Loader for Subreport elements during one render: runs a published report with the given parameters.
        Results are cached per (report, parameters); at most 200 different runs per report."""
        cache: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}

        def load(report_id: str, values: dict[str, Any]) -> dict[str, Any]:
            key = (report_id, tuple(sorted((str(k), str(v)) for k, v in values.items())))
            if key in cache:
                return cache[key]
            if len(cache) >= 200:
                raise ValueError("more than 200 different subreport runs in one report")
            definition = reports_by_id.get(report_id)
            if definition is None:
                raise ValueError("this report is not published on the server")
            design = (definition.editor_config or {}).get("design")
            if not design:
                raise ValueError("this report has no band design (open it in the designer and publish it)")
            prompted = {spec.name for spec in definition.parameters if spec.prompt}
            parameters, _ = validate_parameters(definition, [(k, str(v)) for k, v in values.items() if k in prompted])
            found = run_report_rows(definition, parameters, definition.connection_id, max_rows=min(settings.max_rows, 5000), limit_rows=True)
            columns = list(definition.columns)
            sub_rows = [{column: _safe_value(_row_value(row, column)) for column in columns} for row in found]
            sub_parameters = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in parameters.items()}
            validated = designer_render.validate_design(design, columns, {spec.name for spec in definition.parameters} | set(sub_parameters))
            cache[key] = {"design": validated, "title": definition.title, "columns": columns, "rows": sub_rows, "parameters": sub_parameters}
            return cache[key]
        return load

    def access_error_detail(exc: Exception, connection_id: str) -> str:
        detail = f"{exc} Data adapter: '{connection_id}'."
        if len(load_connection_profiles()) > 1:
            detail += " If these collections are in another database, choose that data adapter in Preview and run it again."
        return detail

    def require_admin(request: Request, role: str = "admin") -> str:
        """Check the caller's token for a management endpoint and return the caller's role.

        role="admin": only REPORTING_ADMIN_TOKEN (data sources, deleting, logs).
        role="designer": REPORTING_ADMIN_TOKEN or REPORTING_DESIGNER_TOKEN (build, preview and run reports).
        An endpoint whose area is switched off with REPORTING_ENABLED_AREAS answers 404.
        """
        areas = enabled_areas()
        if (role == "admin" and "admin" not in areas) or (role == "designer" and not areas & {"admin", "designer"}):
            raise HTTPException(status_code=404, detail="Not Found")
        expected = os.getenv("REPORTING_ADMIN_TOKEN", "")
        if not expected:
            raise HTTPException(status_code=503, detail="Set the REPORTING_ADMIN_TOKEN environment variable to use the management API")
        provided = request.headers.get("X-Admin-Token", "")
        if provided and secrets.compare_digest(provided, expected):
            return "admin"
        designer_token = os.getenv("REPORTING_DESIGNER_TOKEN", "").strip()
        if len(designer_token) >= ROLE_TOKEN_MIN_LENGTH and provided and secrets.compare_digest(provided, designer_token):
            if role == "designer":
                return "designer"
            raise HTTPException(status_code=403, detail="This action needs the admin token; the designer token cannot change data sources, delete items or read logs")
        raise HTTPException(status_code=401, detail="The admin token is missing or wrong")

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

    areas = enabled_areas()
    docs_on = "docs" in areas
    app = FastAPI(
        title="MongoDB Reporting Service", version=SERVICE_VERSION, lifespan=lifespan,
        docs_url="/docs" if docs_on else None, redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    ui_directory = Path(__file__).resolve().parent / "ui"
    app.mount("/assets", StaticFiles(directory=str(ui_directory)), name="assets")

    @app.middleware("http")
    async def access_policy_and_request_log(request: Request, call_next):
        """Per-path deployment policy (areas switched off, management IP allowlist) and one log line per request."""
        started = time.monotonic()
        path = request.url.path
        area = path_area(path)
        client = client_address(request)
        current_areas = enabled_areas()
        blocked: Response | None = None
        if area in ("runner", "admin", "designer", "server", "docs") and area not in current_areas:
            blocked = JSONResponse({"detail": "Not Found"}, status_code=404)
        elif area == "management-api" and not current_areas & {"admin", "designer"}:
            blocked = JSONResponse({"detail": "Not Found"}, status_code=404)
        elif area in ("admin", "designer", "management-api") and not management_ip_allowed(client):
            HTTP_LOG.warning("Management access refused for %s from %s (not in REPORTING_ADMIN_ALLOWED_IPS)", path, client)
            blocked = JSONResponse({"detail": "Access from this network address is not allowed"}, status_code=403)
        if blocked is not None:
            response = blocked
        else:
            try:
                response = await call_next(request)
            except Exception:
                HTTP_LOG.exception("Unhandled error on %s %s", request.method, path)
                raise
        duration = int((time.monotonic() - started) * 1000)
        if not path.startswith("/assets/") or response.status_code >= 400:
            status = response.status_code
            # 503 = a MongoDB/data source problem, already explained in its own log line; 500 = a real bug.
            level = logging.ERROR if status >= 500 and status != 503 else logging.WARNING if status >= 400 and status != 404 else logging.INFO
            HTTP_LOG.log(level, "%s %s -> %s in %d ms (client %s)", request.method, path, response.status_code, duration, client)
        return response

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse(url="/ui")

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def report_ui() -> HTMLResponse:
        return HTMLResponse((ui_directory / "index.html").read_text(encoding="utf-8"))

    def react_page(name: str) -> HTMLResponse:
        """/designer, /server and /admin are pages of one React build (frontend/ -> ui/app)."""
        page = ui_directory / "app" / name
        if not page.exists():
            raise HTTPException(status_code=404, detail="The web app is not built. Run `npm install && npm run build` in frontend/")
        return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    def admin_ui() -> HTMLResponse:
        return react_page("admin.html")

    @app.get("/api/admin/bootstrap", include_in_schema=False)
    def local_admin_bootstrap(request: Request) -> JSONResponse:
        """Allow the local browser UI to pick up its token without exposing it remotely.

        Off unless REPORTING_LOCAL_BOOTSTRAP=true. The loopback + Host checks alone are not enough:
        behind a reverse proxy on the same machine every request arrives from 127.0.0.1, and the
        Host header is either the proxy's upstream (e.g. 127.0.0.1:8000) or set by the caller.
        """
        unavailable = HTTPException(status_code=404, detail="Local admin bootstrap is available only on this computer")
        if os.getenv("REPORTING_LOCAL_BOOTSTRAP", "false").strip().lower() != "true":
            raise unavailable
        if any(request.headers.get(name) for name in BOOTSTRAP_PROXY_HEADERS):
            raise unavailable
        allowed_hosts = {"localhost", "127.0.0.1", "::1"}
        host = (request.url.hostname or "").lower()
        client_host = request.client.host if request.client else ""
        try:
            local_client = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            local_client = False
        if host not in allowed_hosts or not local_client:
            raise unavailable
        token = os.getenv("REPORTING_ADMIN_TOKEN", "").strip()
        if not token:
            raise HTTPException(status_code=503, detail="The local admin token could not be initialised")
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
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send the query parse request as JSON") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="The query parse request must be a JSON object")
        try:
            pipeline = parse_query_source(payload.get("language", "aggregation-json"), payload.get("source", ""))
            if "parameters" in payload:
                raw_parameters = payload["parameters"]
                if not isinstance(raw_parameters, list) or any(not isinstance(item, dict) for item in raw_parameters):
                    raise ValueError("Query parameters must be a list of JSON objects")
                parameter_names = {str(item.get("name", "")) for item in raw_parameters}
                _validate_pipeline_parameter_names(pipeline, parameter_names)
            _validate_read_only_pipeline(pipeline)
            jasper = None if payload.get("language") == "python" else split_jasper_query(payload.get("source", ""))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"pipeline": pipeline, "stageCount": len(pipeline), "parameterNames": pipeline_parameter_names(pipeline),
                "collection": jasper[0] if jasper else ""}

    def save_report_payload(payload: Any, expected_id: str | None = None) -> ReportDefinition:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="The report definition must be a JSON object")
        try:
            definition = _dynamic_report(payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if expected_id and definition.report_id != expected_id:
            raise HTTPException(status_code=400, detail="The report ID in the URL and in the definition must match")
        existing = reports_by_id.get(definition.report_id)
        if existing is not None and expected_id is None:
            raise HTTPException(status_code=409, detail="A report with this ID already exists")
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
        require_admin(request, "designer")
        return {"reports": [admin_report_payload(item) for item in sorted(reports_by_id.values(), key=lambda report: report.report_id)]}

    @app.get("/api/admin/folders")
    def admin_list_folders(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            return {"folders": list_saved_folders()}
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None

    @app.post("/api/admin/folders")
    async def admin_create_folder(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Send the folder as a JSON object")
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"folder": folder, "folders": add_saved_folder(folder)}

    @app.delete("/api/admin/folders")
    async def admin_delete_folder(request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            payload = await request.json()
            folder = normalize_folder_path(payload.get("path", "")) if isinstance(payload, dict) else ""
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc) or "Invalid folder path") from None
        except Exception:
            raise HTTPException(status_code=400, detail="Send the folder path as JSON") from None
        return delete_repository_folder(folder, payload.get("recursive") is True, "designer (admin token)")

    @app.get("/api/admin/folders/contents")
    def admin_folder_contents(path: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            folder = normalize_folder_path(path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"folder": folder, **folder_contents(folder)}

    def folder_contents(folder: str) -> dict[str, list[str]]:
        """Subfolders, published reports and data adapters inside a repository folder (at any depth)."""
        def inside(path: str) -> bool:
            return path == folder or path.startswith(f"{folder}/")
        return {
            "subfolders": [item for item in list_saved_folders() if item.startswith(f"{folder}/")],
            "reports": sorted(item.report_id for item in reports_by_id.values()
                              if item.editor_config and inside(str(item.editor_config.get("folder", "General")))),
            "adapters": [item["name"] for item in load_connection_profiles() if inside(str(item.get("folder") or ""))],
        }

    def delete_repository_folder(folder: str, recursive: bool = False, user: str = "") -> dict[str, Any]:
        """Delete a folder. recursive=True (after the user confirmed it) also deletes its subfolders and the
        published reports in them, like JasperReports Server; data adapters must still be moved first."""
        if folder == "General":
            raise HTTPException(status_code=400, detail="The default General folder cannot be deleted")
        saved_folders = list_saved_folders()
        if folder not in saved_folders:
            raise HTTPException(status_code=404, detail="Folder not found")
        contents = folder_contents(folder)
        if contents["adapters"]:
            raise HTTPException(status_code=409, detail=f"Move or delete the data adapters in this folder first: {', '.join(contents['adapters'])}")
        if not recursive and (contents["subfolders"] or contents["reports"]):
            parts = []
            if contents["subfolders"]:
                parts.append(f"{len(contents['subfolders'])} subfolder(s)")
            if contents["reports"]:
                parts.append(f"{len(contents['reports'])} report(s): {', '.join(contents['reports'][:5])}")
            raise HTTPException(status_code=409, detail=f"The folder is not empty ({' and '.join(parts)}); confirm to delete it with everything inside")
        for report_id in contents["reports"]:
            definition = reports_by_id[report_id]
            path = report_definition_path(report_id, normalize_folder_path(definition.editor_config.get("folder", "General")))
            if path.exists():
                path.unlink()
            record_history(report_id, "deleted", user or "admin", f"with the folder {folder}")
        root = report_definitions_directory().resolve()
        directory = report_definition_path(".folder", folder).parent.resolve()
        if directory.exists() and directory != root and root in directory.parents:
            leftovers = [p for p in directory.rglob("*") if p.is_file()]
            if leftovers and not recursive:
                raise HTTPException(status_code=409, detail="The folder still has files in it; confirm to delete it with everything inside")
            shutil.rmtree(directory)
        removed = {folder, *contents["subfolders"]}
        _write_json_atomically(folders_file(), {"folders": [item for item in saved_folders if item not in removed]})
        if contents["reports"]:
            refreshed = discover_reports()
            reports_by_id.clear()
            reports_by_id.update(refreshed)
        LOG.info("Repository folder %s deleted (%d subfolders, %d reports) by %s", folder, len(contents["subfolders"]), len(contents["reports"]), user or "admin")
        return {"deleted": folder, "folders": list_saved_folders(), "subfoldersDeleted": len(contents["subfolders"]),
                "reportsDeleted": contents["reports"]}

    @app.get("/api/admin/reports/{report_id}")
    def admin_get_report(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        definition = reports_by_id.get(report_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Report ID is not registered")
        return admin_report_payload(definition)

    @app.get("/api/admin/reports/{report_id}/definition.xml")
    def admin_export_report_definition(report_id: str, request: Request) -> Response:
        require_admin(request, "designer")
        definition = reports_by_id.get(report_id)
        if definition is None or definition.editor_config is None:
            raise HTTPException(status_code=404, detail="Saved report definition not found")
        body = render_report_definition_xml(admin_report_payload(definition))
        return Response(
            body,
            media_type="application/xml; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.report.xml"'},
        )

    @app.post("/api/admin/reports/import")
    async def admin_import_report_definition(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            request_payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send the import request as JSON") from None
        if not isinstance(request_payload, dict) or not isinstance(request_payload.get("xml"), str):
            raise HTTPException(status_code=400, detail="Send the content of the report definition XML file")
        try:
            definition_payload = parse_report_definition_xml(request_payload["xml"])
            definition_payload["connectionId"] = str(request_payload.get("connectionId", "")).strip()
            definition_payload["folder"] = normalize_folder_path(
                request_payload.get("folder") or definition_payload.get("folder", "General")
            )
            definition = _dynamic_report(definition_payload)
        except (ET.ParseError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"The XML file could not be read: {exc}") from None
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        existing = reports_by_id.get(definition.report_id)
        replace_existing = request_payload.get("replace") is True
        if existing is not None and not replace_existing:
            raise HTTPException(status_code=409, detail="A report with this ID already exists; confirm that it should be replaced")
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
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return {"report": admin_report_payload(save_report_payload(payload))}

    @app.put("/api/admin/reports/{report_id}")
    async def admin_update_report(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return {"report": admin_report_payload(save_report_payload(payload, report_id))}

    @app.delete("/api/admin/reports/{report_id}")
    def admin_delete_report(report_id: str, request: Request) -> dict[str, str]:
        require_admin(request)
        return delete_published_report(report_id)

    def delete_published_report(report_id: str) -> dict[str, str]:
        if not REPORT_ID_PATTERN.fullmatch(report_id):
            raise HTTPException(status_code=400, detail="Invalid report ID format")
        definition = reports_by_id.get(report_id)
        if definition is None or definition.editor_config is None:
            raise HTTPException(status_code=404, detail="Saved report definition not found")
        path = report_definition_path(
            report_id,
            normalize_folder_path(definition.editor_config.get("folder", "General")),
        )
        if not path.exists():
            raise HTTPException(status_code=404, detail="Saved report definition not found")
        path.unlink()
        refreshed = discover_reports()
        reports_by_id.clear()
        reports_by_id.update(refreshed)
        return {"status": "deleted", "reportId": report_id}

    @app.post("/api/admin/reports/{report_id}/preview")
    async def admin_preview_report(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        definition = reports_by_id.get(report_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Report ID is not registered")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return preview_definition(definition, payload)

    @app.post("/api/admin/reports/{report_id}/run")
    async def admin_run_report(report_id: str, request: Request) -> Response:
        """Run the full report from the admin/designer Preview and return the file (any format,
        any data adapter). Same rendering as /generate; recorded in the audit log."""
        require_admin(request, "designer")
        definition = reports_by_id.get(report_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Report ID is not registered")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return run_definition_file(definition, payload, request.url.path, "admin-run")

    def draft_definition(payload: Any) -> ReportDefinition:
        """A designer draft (not published) turned into a runnable definition, in memory only."""
        raw = payload.get("definition") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="Send the draft report as 'definition'")
        try:
            return _dynamic_report(strip_workspace_meta(raw))
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=f"The draft is not complete yet: {exc}") from None

    @app.post("/api/admin/drafts/preview")
    async def admin_preview_draft(request: Request) -> dict[str, Any]:
        """Live preview of a workspace draft without publishing it to the server."""
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return preview_definition(draft_definition(payload), payload)

    @app.post("/api/admin/drafts/run")
    async def admin_run_draft(request: Request) -> Response:
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return run_definition_file(draft_definition(payload), payload, request.url.path, "designer-draft")

    def preview_definition(definition: ReportDefinition, payload: Any) -> dict[str, Any]:
        connection_id = definition.connection_id
        try:
            if not isinstance(payload, dict):
                raise ValueError("The request must be a JSON object")
            connection_id = chosen_connection(payload, definition)
            parameters = admin_parameters(payload, definition)
            rows = run_report_rows(definition, parameters, connection_id, max_rows=min(settings.max_rows, 20), limit_rows=True)
            safe_rows = [{key: _safe_value(_row_value(row, key)) for key in definition.columns} for row in rows]
            layout = (definition.editor_config or {}).get("layout", {})
            return {"columns": list(definition.columns), "labels": layout.get("columnLabels", {}), "layout": layout,
                    "rows": safe_rows, "rowCount": len(safe_rows), "connectionId": connection_id}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except MongoReadAccessError as exc:
            LOG.warning("Admin preview cannot read report collections with the configured MongoDB identity")
            raise HTTPException(status_code=503, detail=access_error_detail(exc, connection_id)) from None
        except PyMongoError as exc:
            LOG.exception("Admin report preview failed")
            raise HTTPException(status_code=503, detail="The MongoDB preview query failed; see the service log (Logs tab)") from None

    def run_definition_file(definition: ReportDefinition, payload: Any, path: str, access: str, *,
                            fixed_connection: bool = False, user: str = "") -> Response:
        """Run a report in full and return the file. fixed_connection=True (the /server portal)
        always uses the report's own data adapter and only its enabled formats."""
        started = time.monotonic()
        audit: dict[str, Any] = {"requestedAt": datetime.now(timezone.utc), "path": path, "reportId": definition.report_id,
                                 "status": "failed", "rowCount": 0, "access": access}
        if user:
            audit["GeneratedBy"] = user
        connection_id = definition.connection_id
        try:
            if not isinstance(payload, dict):
                raise ValueError("The request must be a JSON object")
            output_format = str(payload.get("format", "pdf")).lower()
            if output_format not in OUTPUT_FORMATS:
                raise ValueError("format must be pdf, xlsx or csv")
            if fixed_connection:
                enabled_formats = (definition.editor_config or {}).get("formats", sorted(OUTPUT_FORMATS))
                if output_format not in enabled_formats:
                    raise ValueError(f"The {output_format.upper()} format is not enabled for this report")
            else:
                connection_id = chosen_connection(payload, definition)
            parameters = admin_parameters(payload, definition)
            audit.update({"format": output_format, "connectionId": connection_id,
                          "parameters": {k: v.isoformat() if isinstance(v, date) else v for k, v in parameters.items()}})
            rows = run_report_rows(definition, parameters, connection_id, max_rows=settings.max_rows, limit_rows=False)
            rows = [{key: _row_value(row, key) for key in definition.columns} for row in rows]
            body, media_type = render_report_output(definition, parameters, rows, output_format, subreport_loader())
            audit.update({"status": "success", "rowCount": len(rows)})
            return Response(body, media_type=media_type,
                            headers={"Content-Disposition": f'attachment; filename="{definition.report_id}.{output_format}"'})
        except HTTPException as exc:
            audit["errorCode"] = str(exc.status_code)
            raise
        except ValueError as exc:
            audit["errorCode"] = "validation_error"
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except MongoReadAccessError as exc:
            audit["errorCode"] = "mongodb_read_access_unavailable"
            raise HTTPException(status_code=503, detail=access_error_detail(exc, connection_id)) from None
        except PyMongoError:
            audit["errorCode"] = "mongodb_error"
            LOG.exception("Admin report run failed")
            raise HTTPException(status_code=503, detail="The MongoDB report query failed; see the service log (Logs tab)") from None
        finally:
            audit["durationMs"] = int((time.monotonic() - started) * 1000)
            try:
                _append_report_audit(audit)
            except OSError:
                LOG.exception("Admin run audit record could not be saved")

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
        else:
            if not uri.startswith(("mongodb://", "mongodb+srv://")):
                raise HTTPException(status_code=422, detail="Give a MongoDB URL that starts with mongodb:// or mongodb+srv://")
            database = database or _mongo_database_from_uri(uri)
            if not database:
                raise HTTPException(status_code=422, detail="The URL has no database name; enter the database name")
            profile = {"id": connection_id, "name": name, "storage": "file", "database": database}
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
        _write_json_atomically(connections_file(), {"connections": updated})
        old_client = state["clients"].pop(connection_id, None)
        state["connection_signatures"].pop(connection_id, None)
        if old_client is not None:
            old_client.close()
        return {"connection": {"id": connection_id, "name": name, "folder": adapter_folder, "database": database, "configured": True}}

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
        return test_mongo_connection(uri, database, connection_id)

    @app.delete("/api/admin/connections/{connection_id}")
    def admin_delete_connection(connection_id: str, request: Request) -> dict[str, str]:
        require_admin(request)
        if connection_id == "default":
            raise HTTPException(status_code=400, detail="The default connection cannot be deleted")
        used_by = [item.report_id for item in reports_by_id.values() if item.connection_id == connection_id]
        if used_by:
            raise HTTPException(status_code=409, detail=f"Switch these reports to another connection first: {', '.join(used_by)}")
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
        _write_json_atomically(connections_file(), {"connections": profiles})
        return {"connectionId": connection_id, "folder": folder}

    @app.post("/api/admin/connections/{connection_id}/test")
    def admin_test_connection(connection_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
        if profile is None:
            raise HTTPException(status_code=404, detail="Connection ID is not registered")
        uri, database = _profile_credentials(profile)
        if not uri or not database:
            if profile.get("storage") == "file":
                detail = "This data source has no saved MongoDB URL or database name"
            else:
                detail = f"Set {profile['uriEnv']} and {profile['databaseEnv']} in .env or the server environment"
            raise HTTPException(status_code=422, detail=detail)
        return test_mongo_connection(uri, database, connection_id)

    @app.post("/api/admin/jrxml/import")
    async def admin_import_jrxml(request: Request) -> dict[str, Any]:
        """Convert a Jaspersoft Studio .jrxml into a draft report + design. Nothing is saved or executed."""
        require_admin(request, "designer")
        try:
            payload = await request.json()
            xml_text = payload.get("xml") if isinstance(payload, dict) else None
            if not isinstance(xml_text, str) or not xml_text.strip():
                raise ValueError("Send the jrxml text as 'xml' in the JSON body")
            import jrxml_import
            return jrxml_import.import_jrxml(xml_text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/designer", response_class=HTMLResponse, include_in_schema=False)
    def designer_ui() -> HTMLResponse:
        return react_page("index.html")

    @app.post("/api/admin/designer/render")
    async def admin_designer_render(request: Request) -> Response:
        """Render a design for live preview as PDF, Excel or CSV. Uses supplied rows, else synthetic sample rows."""
        require_admin(request, "designer")
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("The request must be a JSON object")
            output_format = str(payload.get("format", "pdf")).lower()
            if output_format not in OUTPUT_FORMATS:
                raise ValueError("format must be pdf, xlsx or csv")
            columns = payload.get("columns", [])
            if not isinstance(columns, list) or not all(isinstance(item, str) for item in columns):
                raise ValueError("columns must be a list of strings")
            raw_parameters = payload.get("parameters", {})
            if not isinstance(raw_parameters, dict):
                raise ValueError("parameters must be a JSON object")
            design = designer_render.validate_design(payload.get("design"), columns, {str(k) for k in raw_parameters})
            rows = payload.get("rows")
            if rows is None:
                rows = designer_render.sample_rows(columns)
            if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
                raise ValueError("rows must be a list of objects")
            title = str(payload.get("title", "Report"))
            rows = rows[:2000]
            token = designer_render.SUBREPORT_LOADER.set(subreport_loader())  # Subreports run their published report
            try:
                if output_format == "pdf":
                    body = designer_render.render_design_pdf(design, title, columns, rows, raw_parameters, str(payload.get("reportId", "")))
                    media_type = "application/pdf"
                else:
                    if not designer_render.has_tabular_layout(design):
                        raise ValueError("Excel/CSV output needs a Text Field or Static Text in the Detail band, "
                                         "or the Excel / CSV layout 'Page layout' (Report properties)")
                    renderer = designer_render.render_design_xlsx if output_format == "xlsx" else designer_render.render_design_csv
                    body = renderer(design, title, columns, rows, raw_parameters)
                    media_type = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  "csv": "text/csv; charset=utf-8"}[output_format]
            finally:
                designer_render.SUBREPORT_LOADER.reset(token)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from None
        return Response(body, media_type=media_type, headers={"Content-Disposition": f'inline; filename="preview.{output_format}"'})

    # ------------------------------------------------------------ designer workspace (internal drafts)
    # The designer saves here. Only "Publish to server" copies a report into the server repository
    # (data/reports), which is what /server, /ui, /reports and /generate serve.
    def now_iso() -> str:
        # microseconds: "changed since publish" compares these strings, and a save can follow a publish within a second
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def strip_workspace_meta(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if key not in (WORKSPACE_META, "workspace", "isNew")}

    def workspace_file(report_id: str, folder: str) -> Path:
        return workspace_directory().joinpath(*folder.split("/"), f"{report_id}.json")

    def ensure_workspace() -> None:
        """First use: start the workspace with a copy of what is already on the server."""
        root = workspace_directory()
        if root.exists():
            return
        root.mkdir(parents=True, exist_ok=True)
        stamp = now_iso()
        for definition in reports_by_id.values():
            if definition.editor_config is None:
                continue
            payload = copy.deepcopy(definition.editor_config)
            try:
                folder = normalize_folder_path(payload.get("folder", "General"))
            except ValueError:
                folder = "General"
            payload["folder"] = folder
            payload[WORKSPACE_META] = {"createdAt": stamp, "updatedAt": stamp, "publishedAt": stamp, "publishedFolder": folder}
            _write_json_atomically(workspace_file(definition.report_id, folder), payload)

    def workspace_entries() -> list[tuple[Path, dict[str, Any]]]:
        ensure_workspace()
        found: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(workspace_directory().rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                LOG.warning("A workspace file could not be read: %s", path.name)
                continue
            if isinstance(data, dict) and REPORT_ID_PATTERN.fullmatch(str(data.get("id", ""))) and path.stem == data["id"]:
                found.append((path, data))
        return found

    def find_workspace_entry(report_id: str) -> tuple[Path, dict[str, Any]] | None:
        return next((item for item in workspace_entries() if item[1]["id"] == report_id), None)

    def workspace_summary(data: dict[str, Any]) -> dict[str, Any]:
        meta = data.get(WORKSPACE_META) or {}
        published_at = meta.get("publishedAt") or ""
        on_server = data["id"] in reports_by_id
        return {
            "id": data["id"], "title": data.get("title") or data["id"], "description": data.get("description", ""),
            "folder": data.get("folder", "General"), "updatedAt": meta.get("updatedAt", ""),
            "publishedAt": published_at if on_server else "", "publishedFolder": meta.get("publishedFolder", "") if on_server else "",
            "onServer": on_server,
            "changedSincePublish": bool(on_server and published_at and meta.get("updatedAt", "") > published_at),
        }

    def workspace_folders(entries: list[tuple[Path, dict[str, Any]]]) -> list[str]:
        found = {"General"}
        root = workspace_directory()
        for directory in root.rglob("*"):
            if directory.is_dir():
                parts = directory.relative_to(root).parts
                found.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
        for _, data in entries:
            parts = str(data.get("folder", "General")).split("/")
            found.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
        return sorted(found, key=lambda item: (item.casefold(), item))

    async def json_body(request: Request, *, optional: bool = False) -> dict[str, Any]:
        try:
            raw = await request.body()
            payload = json.loads(raw) if raw.strip() else ({} if optional else None)
        except ValueError:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Send a JSON object")
        return payload

    @app.get("/api/admin/workspace")
    def workspace_list(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        entries = workspace_entries()
        return {"reports": [workspace_summary(data) for _, data in entries], "folders": workspace_folders(entries)}

    @app.get("/api/admin/workspace/{report_id}")
    def workspace_get(report_id: str, request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        entry = find_workspace_entry(report_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="This report is not in the designer workspace")
        return {**strip_workspace_meta(entry[1]), "workspace": workspace_summary(entry[1])}

    @app.put("/api/admin/workspace/{report_id}")
    async def workspace_save(report_id: str, request: Request, create: bool = False) -> dict[str, Any]:
        """Save a draft inside the designer. Drafts may be incomplete; nothing reaches the server."""
        require_admin(request, "designer")
        payload = await json_body(request)
        if not REPORT_ID_PATTERN.fullmatch(report_id) or str(payload.get("id", "")) != report_id:
            raise HTTPException(status_code=400, detail="Give the report a valid ID (letters, numbers, _ and -); it must match the URL")
        try:
            folder = normalize_folder_path(payload.get("folder", "General"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        existing = find_workspace_entry(report_id)
        if existing is not None and create:
            raise HTTPException(status_code=409, detail=f"A report with the ID {report_id} is already in your workspace; choose another ID")
        meta = dict((existing[1].get(WORKSPACE_META) or {}) if existing else {"createdAt": now_iso()})
        meta["updatedAt"] = now_iso()
        clean = strip_workspace_meta(payload)
        clean["folder"] = folder
        clean[WORKSPACE_META] = meta
        target = workspace_file(report_id, folder)
        _write_json_atomically(target, clean)
        if existing is not None and existing[0] != target and existing[0].exists():
            existing[0].unlink()
        problem = ""
        try:
            _dynamic_report(strip_workspace_meta(clean))
        except (TypeError, ValueError, KeyError) as exc:
            problem = str(exc)
        return {"report": workspace_summary(clean), "readyToPublish": not problem, "problem": problem}

    @app.delete("/api/admin/workspace/{report_id}")
    def workspace_delete(report_id: str, request: Request) -> dict[str, Any]:
        """Remove a draft from the workspace. A published copy on the server is not touched."""
        require_admin(request, "designer")
        entry = find_workspace_entry(report_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="This report is not in the designer workspace")
        entry[0].unlink()
        return {"status": "deleted", "reportId": report_id, "stillOnServer": report_id in reports_by_id}

    @app.post("/api/admin/workspace-folders")
    async def workspace_create_folder(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        ensure_workspace()
        workspace_directory().joinpath(*folder.split("/")).mkdir(parents=True, exist_ok=True)
        return {"folder": folder, "folders": workspace_folders(workspace_entries())}

    @app.delete("/api/admin/workspace-folders")
    async def workspace_delete_folder(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if folder == "General":
            raise HTTPException(status_code=400, detail="The General folder cannot be deleted")
        directory = workspace_directory().joinpath(*folder.split("/"))
        if any(str(data.get("folder", "")) == folder or str(data.get("folder", "")).startswith(f"{folder}/") for _, data in workspace_entries()):
            raise HTTPException(status_code=409, detail="Move or delete the reports in this folder first")
        if directory.exists():
            try:
                for child in sorted(directory.rglob("*"), reverse=True):
                    child.rmdir()
                directory.rmdir()
            except OSError:
                raise HTTPException(status_code=409, detail="The folder is not empty") from None
        return {"deleted": folder, "folders": workspace_folders(workspace_entries())}

    @app.post("/api/admin/workspace/{report_id}/publish")
    async def workspace_publish(report_id: str, request: Request) -> dict[str, Any]:
        """Publish a workspace report to the server repository (like Jaspersoft Studio → JasperReports Server)."""
        role = require_admin(request, "designer")
        options = await json_body(request, optional=True)
        entry = find_workspace_entry(report_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Save the report in the designer first, then publish it")
        path, data = entry
        payload = strip_workspace_meta(data)
        if options.get("folder"):
            try:
                payload["folder"] = normalize_folder_path(options["folder"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
        existing = reports_by_id.get(report_id)
        if existing is not None and options.get("replace") is not True:
            raise HTTPException(status_code=409, detail=f"{report_id} is already published on the server; confirm that the server copy should be replaced")
        saved = save_report_payload(payload, expected_id=report_id if existing is not None else None)
        folder = saved.editor_config.get("folder", "General") if saved.editor_config else payload.get("folder", "General")
        add_saved_folder(folder)
        meta = dict(data.get(WORKSPACE_META) or {})
        meta.update({"publishedAt": now_iso(), "publishedFolder": folder, "publishedBy": role})
        if meta.get("updatedAt", "") > meta["publishedAt"]:
            meta["updatedAt"] = meta["publishedAt"]
        data[WORKSPACE_META] = meta
        _write_json_atomically(path, data)
        record_history(report_id, "published", f"designer ({role})", f"{'replaced' if existing is not None else 'new'} in {folder}")
        LOG.info("Report %s published to the server folder %s", report_id, folder)
        return {"report": admin_report_payload(saved), "replaced": existing is not None, "workspace": workspace_summary(data)}

    # ------------------------------------------------------------ /server: repository portal with login
    def require_json_request(request: Request) -> None:
        """Cookie-authenticated writes must be JSON: a cross-site HTML form cannot send that."""
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            raise HTTPException(status_code=415, detail="Send the request as application/json")

    def current_session(request: Request) -> dict[str, Any] | None:
        body = read_session_cookie(request.cookies.get(SESSION_COOKIE, ""))
        if body is None or body.get("n") in state["revoked_sessions"]:
            return None
        if body.get("b"):
            if not bootstrap_login_active():
                return None
            return {"username": BOOTSTRAP_USERNAME, "role": "admin", "bootstrap": True, "nonce": body.get("n")}
        user = find_user(str(body.get("u", "")))
        if user is None or user.get("disabled") or int(user.get("sessionVersion", 0)) != int(body.get("v", -1)):
            return None
        return {"username": user["username"], "role": user.get("role", "viewer"), "bootstrap": False, "nonce": body.get("n")}

    def require_session(request: Request, minimum: str = "viewer") -> dict[str, Any]:
        session = current_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Sign in to the report server first")
        if not role_at_least(session["role"], minimum):
            raise HTTPException(status_code=403, detail=f"This needs a {minimum} account; you are signed in as {session['role']}")
        if request.method not in ("GET", "HEAD"):
            require_json_request(request)
        return session

    def session_view(session: dict[str, Any]) -> dict[str, Any]:
        return {"username": session["username"], "role": session["role"], "bootstrap": session["bootstrap"]}

    @app.get("/server", response_class=HTMLResponse, include_in_schema=False)
    def server_ui() -> HTMLResponse:
        return react_page("server.html")

    @app.post("/api/server/login")
    async def server_login(request: Request) -> JSONResponse:
        require_json_request(request)
        payload = await json_body(request)
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        client = client_address(request)
        window = time.time() - 600
        failures = [stamp for stamp in state["login_failures"].get(client, []) if stamp > window]
        state["login_failures"][client] = failures
        if len(failures) >= 10:
            raise HTTPException(status_code=429, detail="Too many failed sign-ins; wait a few minutes and try again")
        user = find_user(username) if username else None
        session_user, version, bootstrap = "", 0, False
        if user is not None and not user.get("disabled") and password and check_user_password(user, password):
            session_user, version = user["username"], int(user.get("sessionVersion", 0))
        elif user is None and username.casefold() == BOOTSTRAP_USERNAME and bootstrap_login_active():
            admin_token = os.getenv("REPORTING_ADMIN_TOKEN", "")
            if admin_token and password and secrets.compare_digest(password, admin_token):
                session_user, bootstrap = BOOTSTRAP_USERNAME, True
        if not session_user:
            failures.append(time.time())
            LOG.warning("Failed /server sign-in for user %r from %s", username[:64], client)
            raise HTTPException(status_code=401, detail="Wrong username or password")
        state["login_failures"].pop(client, None)
        if user is not None and not bootstrap:
            users = load_users()
            for item in users:
                if item["username"] == user["username"]:
                    item["lastLoginAt"] = now_iso()
            save_users(users)
        role = "admin" if bootstrap else str(user.get("role", "viewer"))
        LOG.info("User %s signed in to /server (%s)", session_user, role)
        response = JSONResponse({"username": session_user, "role": role, "bootstrap": bootstrap})
        secure = request.url.scheme == "https" or os.getenv("REPORTING_SECURE_COOKIES", "false").strip().lower() == "true"
        response.set_cookie(SESSION_COOKIE, make_session_cookie(session_user, version, bootstrap), max_age=session_lifetime_seconds(),
                            httponly=True, samesite="strict", secure=secure, path="/")
        return response

    @app.post("/api/server/logout")
    def server_logout(request: Request) -> JSONResponse:
        session = current_session(request)
        if session is not None:
            state["revoked_sessions"].add(session["nonce"])
            LOG.info("User %s signed out of /server", session["username"])
        response = JSONResponse({"status": "signed-out"})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/server/me")
    def server_me(request: Request) -> dict[str, Any]:
        return session_view(require_session(request))

    @app.get("/api/server/repository")
    def server_repository(request: Request) -> dict[str, Any]:
        session = require_session(request)
        try:
            folders = list_saved_folders()
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None
        return {"folders": folders, "reports": sorted(published_report_summaries(), key=lambda item: item["title"].casefold()),
                "user": session_view(session)}

    @app.post("/api/server/reports/{report_id}/run")
    async def server_run_report(report_id: str, request: Request) -> Response:
        session = require_session(request)
        definition = reports_by_id.get(report_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="This report is not published on the server")
        payload = await json_body(request)
        return run_definition_file(definition, payload, request.url.path, "server-session",
                                   fixed_connection=True, user=session["username"])

    @app.post("/api/server/folders")
    async def server_create_folder(request: Request) -> dict[str, Any]:
        require_session(request, "designer")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"folder": folder, "folders": add_saved_folder(folder)}

    @app.delete("/api/server/folders")
    async def server_delete_folder(request: Request) -> dict[str, Any]:
        session = require_session(request, "admin")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("path", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return delete_repository_folder(folder, payload.get("recursive") is True, session["username"])

    @app.get("/api/server/folders/contents")
    def server_folder_contents(path: str, request: Request) -> dict[str, Any]:
        """What deleting a folder would remove (shown in the confirmation)."""
        require_session(request)
        try:
            folder = normalize_folder_path(path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"folder": folder, **folder_contents(folder)}

    @app.delete("/api/server/reports/{report_id}")
    def server_delete_report(report_id: str, request: Request) -> dict[str, str]:
        session = require_session(request, "admin")
        result = delete_published_report(report_id)
        record_history(report_id, "deleted", session["username"])
        LOG.info("Report %s removed from the server by %s", report_id, session["username"])
        return result

    # ------------------------------------------------------------ /server: resources (like JasperReports Server)
    def history_file(report_id: str) -> Path:
        return data_directory() / "history" / f"{report_id}.json"

    def read_history(report_id: str) -> list[dict[str, Any]]:
        path = history_file(report_id)
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (OSError, ValueError):
            return []
        return items if isinstance(items, list) else []

    def record_history(report_id: str, action: str, user: str, detail: str = "") -> None:
        """What happened to a server resource (published, edited, copied, moved, uploaded, deleted); last 200 entries."""
        if not REPORT_ID_PATTERN.fullmatch(report_id):
            return
        items = read_history(report_id)
        items.append({"at": now_iso(), "action": action, "user": user, "detail": detail[:300]})
        try:
            _write_json_atomically(history_file(report_id), items[-200:])
        except OSError:
            LOG.warning("The history of %s could not be written", report_id)

    def published_or_404(report_id: str) -> ReportDefinition:
        definition = reports_by_id.get(report_id)
        if definition is None or definition.editor_config is None:
            raise HTTPException(status_code=404, detail="This report is not published on the server")
        return definition

    def design_summary(design: Any) -> dict[str, Any]:
        """Bands, element counts and the subreports a design uses (for the resource page)."""
        if not isinstance(design, dict):
            return {"bands": [], "elements": 0, "subreports": [], "components": {}}
        bands = design.get("bands") or {}
        found: list[tuple[str, dict[str, Any]]] = []
        for key, band in bands.items():
            if key in ("groupHeaders", "groupFooters") and isinstance(band, dict):
                found += [(f"{key[:-1]} {name}", item) for name, item in band.items() if isinstance(item, dict)]
            elif isinstance(band, dict):
                found.append((key, band))
        present = [(name, band) for name, band in found if band.get("height") or band.get("elements")]
        elements = [el for _, band in present for el in band.get("elements") or [] if isinstance(el, dict)]
        components: dict[str, int] = {}
        for el in elements:
            components[str(el.get("type"))] = components.get(str(el.get("type")), 0) + 1
        return {"bands": [name for name, _ in present], "elements": len(elements), "components": components,
                "subreports": sorted({str(el.get("reportId")) for el in elements if el.get("type") == "subreport" and el.get("reportId")}),
                "page": design.get("page") or {}}

    def connection_summary(connection_id: str) -> dict[str, Any]:
        profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
        if profile is None:
            return {"id": connection_id, "name": connection_id, "missing": True}
        return {"id": profile["id"], "name": profile["name"], "folder": profile.get("folder", ""), "storage": profile.get("storage", "env"),
                "database": profile.get("database", "") if profile.get("storage") == "file" else f"from ${profile.get('databaseEnv', '')}"}

    def resource_details(definition: ReportDefinition, session: dict[str, Any]) -> dict[str, Any]:
        config = definition.editor_config or {}
        designer = role_at_least(session["role"], "designer")
        try:
            path = report_definition_path(definition.report_id, normalize_folder_path(config.get("folder", "General")))
            stat = path.stat() if path.exists() else None
        except (ValueError, OSError):
            stat = None
        used_by = sorted(item.report_id for item in reports_by_id.values() if item.report_id != definition.report_id
                         and definition.report_id in design_summary((item.editor_config or {}).get("design")).get("subreports", []))
        return {
            "id": definition.report_id, "title": definition.title, "description": config.get("description", ""),
            "folder": config.get("folder", "General"), "formats": config.get("formats", sorted(OUTPUT_FORMATS)),
            "defaultFormat": config.get("defaultFormat", "pdf"), "connection": connection_summary(definition.connection_id),
            "collection": config.get("collection", definition.collection), "columns": list(definition.columns),
            "parameters": [{"name": p.get("name"), "type": p.get("type", "string"), "label": p.get("label", p.get("name")),
                            "description": p.get("description", ""), "required": bool(p.get("required")),
                            "default": p.get("default"), "allowedValues": p.get("allowedValues") or [],
                            "computed": p.get("prompt") is False, "expression": p.get("expression", "")}
                           for p in config.get("parameters", []) if isinstance(p, dict)],
            # the query can reveal data structure: only designers and admins see it
            "queryLanguage": config.get("queryLanguage", "mongosh") if designer else None,
            "querySource": config.get("querySource", "") if designer else None,
            "design": design_summary(config.get("design")),
            "file": {"name": f"{definition.report_id}.mrd", "size": stat.st_size if stat else None,
                     "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else ""},
            "usedBy": used_by,
            "history": list(reversed(read_history(definition.report_id)))[:50],
            "canEdit": designer, "canDelete": role_at_least(session["role"], "admin"),
        }

    def sync_workspace_copy(report_id: str, update: Any) -> None:
        """A change made on the server is also made in the designer's workspace copy, so the next publish keeps it."""
        entry = find_workspace_entry(report_id)
        if entry is None:
            return
        path, data = entry
        update(data)
        meta = dict(data.get(WORKSPACE_META) or {})
        stamp = now_iso()
        meta.update({"updatedAt": stamp, "publishedAt": stamp, "publishedFolder": data.get("folder", "General")})
        data[WORKSPACE_META] = meta
        _write_json_atomically(path, data)

    def free_report_id(base: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_-]", "_", base)[:70] or "REPORT"
        candidate, number = f"{base}_COPY", 2
        while candidate in reports_by_id:
            candidate, number = f"{base}_COPY{number}", number + 1
        return candidate

    @app.get("/api/server/reports/{report_id}")
    def server_report_details(report_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request)
        return resource_details(published_or_404(report_id), session)

    @app.put("/api/server/reports/{report_id}")
    async def server_edit_report(report_id: str, request: Request) -> dict[str, Any]:
        """Edit a published report's properties: title, description, formats, data adapter, the questions
        (label, help, required, default, allowed values) and, for designers, the query."""
        session = require_session(request, "designer")
        definition = published_or_404(report_id)
        changes = await json_body(request)
        payload = copy.deepcopy(definition.editor_config)
        for key in ("title", "description"):
            if key in changes:
                payload[key] = str(changes[key] or "")[:300 if key == "title" else 2000]
        if "formats" in changes:
            formats = [f for f in changes["formats"] if f in OUTPUT_FORMATS] if isinstance(changes["formats"], list) else []
            if not formats:
                raise HTTPException(status_code=422, detail="Enable at least one output format")
            payload["formats"] = formats
        if "defaultFormat" in changes:
            payload["defaultFormat"] = str(changes["defaultFormat"])
        if payload.get("defaultFormat") not in payload.get("formats", sorted(OUTPUT_FORMATS)):
            payload["defaultFormat"] = payload.get("formats", ["pdf"])[0]
        if "connectionId" in changes:
            payload["connectionId"] = str(changes["connectionId"] or "").strip()
        if isinstance(changes.get("parameters"), list):
            edits = {str(item.get("name")): item for item in changes["parameters"] if isinstance(item, dict)}
            for parameter in payload.get("parameters", []):
                edit = edits.get(str(parameter.get("name")))
                if not edit or parameter.get("prompt") is False:
                    continue
                for key in ("label", "description", "required", "default", "allowedValues"):
                    if key in edit:
                        parameter[key] = edit[key]
                if parameter.get("default") in ("", None):
                    parameter.pop("default", None)
        if "querySource" in changes:
            payload["querySource"] = str(changes["querySource"] or "")
            if changes.get("queryLanguage") in ("mongosh", "json", "python"):
                payload["queryLanguage"] = changes["queryLanguage"]
            if "collection" in changes:
                payload["collection"] = str(changes["collection"] or "").strip()
        saved = save_report_payload(payload, expected_id=report_id)
        changed = sorted(k for k in changes if k in ("title", "description", "formats", "defaultFormat", "connectionId", "parameters", "querySource"))
        sync_workspace_copy(report_id, lambda data: data.update({k: copy.deepcopy(saved.editor_config.get(k)) for k in
                                                                ("title", "description", "formats", "defaultFormat", "connectionId",
                                                                 "parameters", "querySource", "queryLanguage", "collection")
                                                                if k in saved.editor_config}))
        record_history(report_id, "edited", session["username"], ", ".join(changed))
        return resource_details(saved, session)

    @app.post("/api/server/reports/{report_id}/copy")
    async def server_copy_report(report_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        definition = published_or_404(report_id)
        body = await json_body(request, optional=True)
        payload = copy.deepcopy(definition.editor_config)
        try:
            payload["folder"] = normalize_folder_path(body.get("folder") or payload.get("folder", "General"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        new_id = str(body.get("newId") or "").strip() or free_report_id(report_id)
        if new_id in reports_by_id:
            raise HTTPException(status_code=409, detail=f"A report with the ID {new_id} already exists")
        payload["id"] = new_id
        payload["title"] = str(body.get("title") or f"{definition.title} (copy)")[:300]
        saved = save_report_payload(payload)
        add_saved_folder(payload["folder"])
        record_history(new_id, "copied", session["username"], f"from {report_id}")
        return {"report": resource_details(saved, session)}

    @app.post("/api/server/reports/{report_id}/move")
    async def server_move_report(report_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request, "designer")
        definition = published_or_404(report_id)
        body = await json_body(request)
        try:
            folder = normalize_folder_path(body.get("folder", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        before = definition.editor_config.get("folder", "General")
        payload = {**copy.deepcopy(definition.editor_config), "folder": folder}
        saved = save_report_payload(payload, expected_id=report_id)
        add_saved_folder(folder)
        record_history(report_id, "moved", session["username"], f"{before} → {folder}")
        return {"report": resource_details(saved, session)}

    @app.get("/api/server/reports/{report_id}/file")
    def server_download_report(report_id: str, request: Request) -> Response:
        """The whole report (query, parameters, data adapter, band design, images) as one .mrd file."""
        require_session(request, "designer")
        definition = published_or_404(report_id)
        return Response(render_report_definition_xml(admin_report_payload(definition)), media_type="application/xml; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{report_id}.mrd"'})

    @app.post("/api/server/upload")
    async def server_upload_report(request: Request) -> dict[str, Any]:
        """Add a resource: a .mrd file (or a Jaspersoft Studio .jrxml) is published into a folder."""
        session = require_session(request, "designer")
        body = await json_body(request)
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPException(status_code=400, detail="Choose a .mrd or .jrxml file")
        if len(content.encode("utf-8")) > 12_000_000:
            raise HTTPException(status_code=413, detail="The file is larger than 12 MB")
        warnings: list[str] = []
        try:
            root_tag = ET.fromstring(content).tag.split("}")[-1]
            if root_tag == "jasperReport":
                import jrxml_import
                imported = jrxml_import.import_jrxml(content)
                payload = {**imported["report"], "design": imported["design"]}
                warnings = list(imported.get("warnings") or [])
            else:
                payload = parse_report_definition_xml(content)
        except (ET.ParseError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"The file could not be read: {exc}") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if body.get("newId"):
            payload["id"] = str(body["newId"]).strip()
        if body.get("connectionId"):
            payload["connectionId"] = str(body["connectionId"]).strip()
        if body.get("collection"):
            payload["collection"] = str(body["collection"]).strip()
        try:
            payload["folder"] = normalize_folder_path(body.get("folder") or payload.get("folder") or "General")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        report_id = str(payload.get("id", ""))
        exists = report_id in reports_by_id
        if exists and body.get("replace") is not True:
            raise HTTPException(status_code=409, detail=f"{report_id} is already on the server; tick “Replace” or give it a new ID")
        saved = save_report_payload(payload, expected_id=report_id if exists else None)
        add_saved_folder(payload["folder"])
        record_history(report_id, "uploaded", session["username"], f"{'replaced' if exists else 'new'} in {payload['folder']}")
        return {"report": resource_details(saved, session), "replaced": exists, "warnings": warnings}

    @app.get("/api/server/connections")
    def server_connections(request: Request) -> dict[str, Any]:
        require_session(request, "designer")
        return {"connections": [connection_summary(item["id"]) for item in load_connection_profiles()]}

    # ------------------------------------------------------------ /admin: server user accounts
    def user_view(user: dict[str, Any]) -> dict[str, Any]:
        return {"username": user["username"], "role": user.get("role", "viewer"), "disabled": bool(user.get("disabled")),
                "createdAt": user.get("createdAt", ""), "lastLoginAt": user.get("lastLoginAt", "")}

    def valid_role(value: Any) -> str:
        role = str(value or "").strip().lower()
        if role not in USER_ROLES:
            raise HTTPException(status_code=422, detail="role must be viewer, designer or admin")
        return role

    def valid_password(value: Any) -> str:
        password = str(value or "")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(status_code=422, detail=f"The password needs at least {MIN_PASSWORD_LENGTH} characters")
        return password

    def admins_left(users: list[dict[str, Any]]) -> int:
        return sum(1 for item in users if item.get("role") == "admin" and not item.get("disabled"))

    @app.get("/api/admin/users")
    def admin_list_users(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {"users": [user_view(item) for item in load_users()], "bootstrapLogin": bootstrap_login_active(),
                "roles": list(USER_ROLES)}

    @app.post("/api/admin/users")
    async def admin_create_user(request: Request) -> dict[str, Any]:
        require_admin(request)
        payload = await json_body(request)
        username = str(payload.get("username", "")).strip()
        if not USERNAME_PATTERN.fullmatch(username):
            raise HTTPException(status_code=422, detail="Username: 2-64 letters, numbers, dot, @, _ or -")
        role = valid_role(payload.get("role", "viewer"))
        salt, digest = _hash_password(valid_password(payload.get("password")))
        users = load_users()
        if any(str(item["username"]).casefold() == username.casefold() for item in users):
            raise HTTPException(status_code=409, detail="A user with this name already exists")
        users.append({"username": username, "role": role, "salt": salt, "hash": digest, "sessionVersion": 0,
                      "createdAt": now_iso()})
        save_users(users)
        LOG.info("Server user %s created (%s)", username, role)
        return {"user": user_view(users[-1])}

    @app.put("/api/admin/users/{username}")
    async def admin_update_user(username: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        payload = await json_body(request)
        users = load_users()
        user = next((item for item in users if str(item["username"]).casefold() == username.casefold()), None)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        before = admins_left(users)
        if "role" in payload:
            user["role"] = valid_role(payload["role"])
        if "disabled" in payload:
            user["disabled"] = bool(payload["disabled"])
        if payload.get("password"):
            user["salt"], user["hash"] = _hash_password(valid_password(payload["password"]))
        if before and not admins_left(users):
            raise HTTPException(status_code=409, detail="Keep at least one active admin user")
        user["sessionVersion"] = int(user.get("sessionVersion", 0)) + 1  # signs this user out everywhere
        save_users(users)
        return {"user": user_view(user)}

    @app.delete("/api/admin/users/{username}")
    def admin_delete_user(username: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        users = load_users()
        remaining = [item for item in users if str(item["username"]).casefold() != username.casefold()]
        if len(remaining) == len(users):
            raise HTTPException(status_code=404, detail="User not found")
        if admins_left(users) and not admins_left(remaining):
            raise HTTPException(status_code=409, detail="Keep at least one active admin user")
        save_users(remaining)
        return {"status": "deleted", "username": username}

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            _, database = get_connection("default")
            return JSONResponse({"status": "ok", "version": SERVICE_VERSION, "connection": "default", "database": database})
        except HTTPException as exc:
            status = "not_configured" if exc.status_code == 503 and "set " in str(exc.detail).lower() else "unavailable"
            return JSONResponse({"status": status, "version": SERVICE_VERSION, "detail": exc.detail}, status_code=exc.status_code)

    @app.get("/reports/connections/{connection_id}/status")
    def report_connection_status(connection_id: str, request: Request) -> dict[str, Any]:
        """Is the data adapter of the report the user picked reachable? Used by the /ui runner."""
        require_report_access(request)
        profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"Data adapter is not registered: {connection_id}")
        try:
            _, database = get_connection(connection_id)
        except HTTPException as exc:
            return {"status": "unavailable", "connectionId": connection_id, "name": profile.get("name", connection_id),
                    "detail": str(exc.detail)}
        return {"status": "ok", "connectionId": connection_id, "name": profile.get("name", connection_id), "database": database}

    @app.get("/api/admin/whoami")
    def admin_whoami(request: Request) -> dict[str, Any]:
        role = require_admin(request, "designer")
        return {"role": role, "version": SERVICE_VERSION, "areas": sorted(enabled_areas())}

    @app.get("/api/admin/logs")
    def admin_logs(request: Request, lines: int = 300, level: str = "INFO", search: str = "") -> dict[str, Any]:
        require_admin(request)
        if level.upper() not in LOG_LEVELS:
            raise HTTPException(status_code=422, detail="level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        path = log_file_path()
        return {"file": path.name, "enabled": _FILE_HANDLER is not None,
                "entries": read_log_entries(lines, level, search[:200])}

    @app.get("/api/admin/logs/download")
    def admin_logs_download(request: Request) -> Response:
        require_admin(request)
        path = log_file_path()
        if not path.exists():
            raise HTTPException(status_code=404, detail="No log file has been written yet")
        return Response(path.read_bytes(), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{path.name}"'})

    @app.get("/api/admin/audit")
    def admin_audit(request: Request, limit: int = 200) -> dict[str, Any]:
        require_admin(request)
        return {"records": read_audit_records(limit)}

    @app.get("/reports")
    def list_reports(request: Request) -> dict[str, Any]:
        require_report_access(request)
        return {"reports": published_report_summaries()}

    def published_report_summaries() -> list[dict[str, Any]]:
        return [{
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
            } for p in item.parameters if p.prompt],  # computed parameters are never asked
        } for item in reports_by_id.values()]

    @app.get("/generate/{report_file}")
    def generate(report_file: str, request: Request) -> Response:
        # Checked before anything else, so an unauthenticated caller learns nothing (not even
        # whether a report ID exists) and cannot fill the audit file.
        access = require_report_access(request, report_file)
        started = time.monotonic()
        audit: dict[str, Any] = {
            "requestedAt": datetime.now(timezone.utc),
            "path": request.url.path,
            "status": "failed",
            "rowCount": 0,
            "access": access,
        }
        query_pairs = [(key, value) for key, value in request.query_params.multi_items() if key not in SIGNED_LINK_KEYS]
        query_map = dict(query_pairs)
        for key in METADATA_KEYS:
            if key in query_map:
                audit[key] = query_map[key]
        definition: ReportDefinition | None = None
        audit_saved = False
        try:
            match = REPORT_PATH.fullmatch(report_file)
            if not match:
                raise HTTPException(status_code=400, detail="The path must be /generate/<REPORT_ID>.<pdf|xlsx|csv>")
            report_id, output_format = match.group(1), match.group(2).lower()
            definition = reports_by_id.get(report_id)
            if definition is None:
                raise HTTPException(status_code=404, detail="Report ID is not registered")
            enabled_formats = (definition.editor_config or {}).get("formats", sorted(OUTPUT_FORMATS))
            if output_format not in enabled_formats:
                raise HTTPException(status_code=400, detail=f"The {output_format.upper()} format is not enabled for this report")
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
                raise HTTPException(status_code=413, detail=f"The report is larger than the row limit ({settings.max_rows})")
            rows = [{key: _row_value(row, key) for key in definition.columns} for row in rows]
            body, media_type = render_report_output(definition, parameters, rows, output_format, subreport_loader())
            audit.update({"status": "success", "rowCount": len(rows)})
            if settings.audit_required:
                # Fail closed: the report is released only after its audit record is stored.
                audit["durationMs"] = int((time.monotonic() - started) * 1000)
                try:
                    _append_report_audit(audit)
                    audit_saved = True
                except OSError:
                    LOG.exception("Required audit record could not be saved")
                    raise HTTPException(status_code=503, detail="The audit record could not be saved; because AUDIT_REQUIRED is on, the report was not released") from None
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
            raise HTTPException(status_code=503, detail="The MongoDB report query failed") from None
        except RuntimeError as exc:
            audit["errorCode"] = "export_dependency_missing"
            audit["errorMessage"] = str(exc)[:300]
            raise HTTPException(status_code=501, detail=str(exc)) from None
        except Exception as exc:
            audit["errorCode"] = "internal_error"
            audit["errorMessage"] = type(exc).__name__
            LOG.exception("Unexpected report execution error")
            raise HTTPException(status_code=500, detail="The report could not be generated") from None
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
