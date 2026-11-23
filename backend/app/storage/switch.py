"""Admin → System database: choose where the service keeps its own data (users, reports, folders, logs…),
test it, copy the current data there and save the choice (used after the next restart).

Any direction works: files → SQL/MongoDB, SQL Server → MongoDB, MongoDB → PostgreSQL, …
The current data stays where it is (nothing is deleted), so switching back is always possible."""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

from backend.app.constants import PROJECT_ROOT
from backend.app.db import entities as ent, model
from backend.app.db.base import DuplicateSeq
from backend.app.db.urls import dialect_label, kind_of, redact

KINDS = {
    "sqlserver": {"label": "SQL Server", "family": "relational", "port": 1433},
    "postgresql": {"label": "PostgreSQL", "family": "relational", "port": 5432},
    "mysql": {"label": "MySQL / MariaDB", "family": "relational", "port": 3306},
    "oracle": {"label": "Oracle", "family": "relational", "port": 1521},
    "sqlite": {"label": "SQLite (one file, one instance)", "family": "relational", "port": None},
    "mongodb": {"label": "MongoDB", "family": "non-relational", "port": 27017},
}
SKIP_PREFIXES = ("logs/", "records/", "migrated/")
COPY_RUN_LIMIT = 2_000_000


def build_url(form: dict[str, Any]) -> str:
    """A storage URL from the Admin form, or form["url"] when the admin pasted one."""
    pasted = str(form.get("url", "")).strip()
    if pasted:
        if not kind_of(pasted):
            raise ValueError("The URL must start with sqlserver://, mssql+pyodbc://, postgresql://, mysql://, oracle://, sqlite:/// or mongodb://")
        return pasted
    kind = str(form.get("kind", "")).strip().lower()
    if kind not in KINDS:
        raise ValueError(f"Choose a database type: {', '.join(KINDS)}")
    database = str(form.get("database", "")).strip()
    if kind == "sqlite":
        if not database:
            raise ValueError("Give the SQLite file, e.g. data/reporting.db")
        return f"sqlite:///{database}"
    host = str(form.get("host", "")).strip() or "localhost"
    if not re.fullmatch(r"[A-Za-z0-9._\-\\\[\]:]+", host):
        raise ValueError("The host may contain letters, digits, dots, dashes, a backslash (SQL Server instance) and brackets (IPv6)")
    port = str(form.get("port", "")).strip()
    if port and not port.isdigit():
        raise ValueError("The port must be a number")
    if not database and kind != "oracle":
        raise ValueError("Give the database name (create an empty one first, e.g. reporting)")
    user = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    windows = kind == "sqlserver" and bool(form.get("windowsLogin"))
    credentials = "" if windows or not user else quote(user, safe="") + (f":{quote(password, safe='')}" if password else "") + "@"
    address = host.replace("\\", "%5C") + (f":{port}" if port else "")
    if kind == "sqlserver":
        if windows or form.get("odbc"):
            query = {"driver": str(form.get("driver") or "ODBC Driver 18 for SQL Server"), "TrustServerCertificate": "yes"}
            if windows:
                query["trusted_connection"] = "yes"
            return f"mssql+pyodbc://{'@' if windows else credentials}{address}/{database}?{urlencode(query)}"
        return f"sqlserver://{credentials}{address}/{database}"
    if kind == "oracle":
        service = str(form.get("service", "")).strip() or "XEPDB1"
        schema = (database or "REPORTING").upper()          # on Oracle the service's own "database" is a schema (user)
        from backend.app.db.sql import ORACLE_SCHEMA  # noqa: PLC0415
        if not ORACLE_SCHEMA.fullmatch(schema):
            raise ValueError("The Oracle schema is a name of letters, digits, _ $ # (starting with a letter), e.g. REPORTING")
        return f"oracle://{credentials}{address}/?{urlencode({'service_name': service, 'schema': schema})}"
    if kind == "mongodb":
        return f"mongodb://{credentials}{address}/{database}" + ("?authSource=admin" if credentials else "")
    return f"{kind}://{credentials}{address}/{database}"


def describe(url: str) -> dict[str, Any]:
    kind = kind_of(url)
    if kind == "mongodb":
        return {"backend": "mongodb", "label": "MongoDB", "family": "non-relational", "url": redact(url)}
    if kind == "sql":
        return {"backend": "sql", "label": dialect_label(url), "family": "relational", "url": redact(url)}
    return {"backend": "files", "label": "Files in the data folder", "family": "files", "url": ""}


def current() -> dict[str, Any]:
    from backend.app.security.secrets import ENV_FILE_KEYS  # noqa: PLC0415
    from backend.app.storage.store import storage_settings  # noqa: PLC0415
    backend, url = storage_settings()
    info = describe(url) if backend != "files" else describe("")
    if backend == "postgresql":
        info.update(backend="postgresql", label="PostgreSQL (older layout)")
    saved = _read_env_file().get("REPORTING_STORAGE_URL", "")
    pending = None
    if saved and saved != url and kind_of(saved):
        pending = describe(saved)
    managed_outside = "REPORTING_STORAGE" in os.environ and "REPORTING_STORAGE" not in ENV_FILE_KEYS
    return {**info, "pending": pending, "managedByEnvironment": managed_outside, "kinds": KINDS,
            "envFile": str(env_path())}


def _target(url: str):
    from backend.app.storage.migrate import entities_for, target_for  # noqa: PLC0415
    backend, store, records = target_for(url)
    records.ensure_schema()
    return backend, store, records, entities_for(records)


def test(url: str) -> dict[str, Any]:
    """Connect, create the tables/collections if needed and say what is already there."""
    try:
        _, store, records, entities = _target(url)
        store.write_json("meta/connection-test.json", {"at": model.now().isoformat()})
        store.read_json("meta/connection-test.json")
        store.delete("meta/connection-test.json")
        existing = {
            "users": len(entities.find(ent.USERS_V2)) or len(entities.find(ent.USERS)),
            "reports": len(entities.find(ent.SERVER_REPORTS)) or len(entities.find(ent.REPORTS)),
            "runs": len(records.runs(limit=5000)),
            "auditEvents": 1 if records._last_event() else 0,
        }
    except Exception as exc:  # noqa: BLE001 - every driver has its own errors; the admin needs the message
        return {"ok": False, **describe(url), "message": _clean_error(exc, url)}
    empty = not any(existing.values())
    return {"ok": True, **describe(url), "existing": existing, "empty": empty,
            "message": "Connected. The database is empty: the current data can be copied into it." if empty else
                       "Connected. This database already has data of the reporting service."}


ORACLE_HELP = ("Create the schema first (as a DBA): CREATE USER REPORTING IDENTIFIED BY <password>; "
               "GRANT CREATE SESSION, CREATE TABLE TO REPORTING; ALTER USER REPORTING QUOTA UNLIMITED ON USERS; "
               "then connect as REPORTING.")


def _clean_error(exc: Exception, url: str) -> str:
    text = str(exc).splitlines()[0][:400] if str(exc) else type(exc).__name__
    if url.lower().startswith("oracle") and re.search(r"ORA-(01435|01918|01031|01950|01045|01017)", text):
        text = f"{text} — {ORACLE_HELP}"
    password = re.search(r"//[^:/@]+:([^@]*)@", url)
    if password and password.group(1):
        text = text.replace(password.group(1), "***")
    return text


def copy_current_to(url: str, *, overwrite: bool = False) -> dict[str, int]:
    """Copy documents, typed rows, run log and audit trail of the running service into the target."""
    from backend.app.db import get_records  # noqa: PLC0415
    from backend.app.storage.store import get_store  # noqa: PLC0415
    source, source_records, source_entities = get_store(), get_records(), ent.get_entities()
    _, target, target_records, target_entities = _target(url)
    counts = {"documents": 0, "files": 0, "lines": 0, "folders": 0, "rows": 0, "runs": 0, "events": 0, "skipped": 0}
    for entity in ent.ENTITIES:          # users, keys, versions, schedules, folders, reports and drafts (ids kept), data sources
        for row in source_entities.find(entity):
            if overwrite or target_entities.get(entity, *ent.key_of(entity, ent.encode(entity, row))) is None:
                target_entities.put(entity, row)
                counts["rows"] += 1
            else:
                counts["skipped"] += 1
    for area in ("reports", "workspace"):
        if source.has_dir(area):
            target.make_dir(area)
        for relative in source.dirs(area):
            target.make_dir(f"{area}/{relative}")        # already a row (copied above) unless the source had none
            counts["folders"] += 1
    for key in source.keys(""):
        if key.startswith(SKIP_PREFIXES) or source.report_id_of(key):
            continue                     # reports/, workspace/, connections.json, folders.json become typed rows in the target
        if key.endswith(".jsonl"):
            if list(target.lines(key)):
                counts["skipped"] += 1
                continue
            for line in source.lines(key):
                if line.strip():
                    target.append_line(key, line)
                    counts["lines"] += 1
            continue
        if target.exists(key) and not overwrite:
            counts["skipped"] += 1
            continue
        if key.endswith(".json"):
            try:
                target.write_json(key, source.read_json(key), private=key.rsplit("/", 1)[-1] in ("users.json", "private_secrets.json", "api-keys.json"))
                counts["documents"] += 1
            except ValueError:
                counts["skipped"] += 1
        else:
            target.write_bytes(key, source.read_bytes(key) or b"")
            counts["files"] += 1
    if not target_records.runs(limit=1):
        for run in source_records._find("runs", {}, None, None, COPY_RUN_LIMIT, newest_first=False):
            target_records._insert_run(model.clean(run, model.RUN_FIELDS))
            counts["runs"] += 1
    if target_records._last_event() is None:
        seq = 1
        while True:
            chunk = source_records._events_from(seq, 1000)
            if not chunk:
                break
            for event in chunk:
                try:
                    target_records._insert_event(model.clean(event, model.AUDIT_FIELDS))
                    counts["events"] += 1
                except DuplicateSeq:
                    counts["skipped"] += 1
            seq = chunk[-1]["seq"] + 1
    return counts


def env_path():
    """The .env file the service reads at start-up (tests point this elsewhere)."""
    return PROJECT_ROOT / ".env"


def _read_env_file() -> dict[str, str]:
    path = env_path()
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
    return values


def save_choice(url: str) -> dict[str, Any]:
    """Write REPORTING_STORAGE / REPORTING_STORAGE_URL into .env (a copy of the old file is kept next to it).
    When the setting comes from the real environment (Docker, a Windows service…), say what to change instead."""
    backend = describe(url)["backend"]
    lines_to_set = {"REPORTING_STORAGE": backend, "REPORTING_STORAGE_URL": url}
    info = current()
    if info["managedByEnvironment"]:
        return {"saved": False, "settings": {"REPORTING_STORAGE": backend, "REPORTING_STORAGE_URL": redact(url)},
                "message": "REPORTING_STORAGE is set by the server's environment (not by .env): change it there and restart."}
    path = env_path()
    old = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f".env.before-storage-{stamp}"))
    out, done = [], set()
    for line in old:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in lines_to_set:
            if key not in done:
                out.append(f"{key}={lines_to_set[key]}")
                done.add(key)
            continue
        out.append(line)
    for key, value in lines_to_set.items():
        if key not in done:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"saved": True, "file": str(path), "settings": {"REPORTING_STORAGE": backend, "REPORTING_STORAGE_URL": redact(url)},
            "message": "Saved. Restart the reporting service to use the new database."}
