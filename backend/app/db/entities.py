"""Typed tables for the service's own objects (accounts, API keys, folder permissions, report versions, history).

Each entity has real columns, the same on every backend:

    files    <data dir>/records/entities/<name>.json
    SQL      rs_<name> (SQL Server, Oracle, MySQL, PostgreSQL, SQLite)  — composite primary keys, indexes
    MongoDB  rs_<name> collection, _id built from the key, indexes on the lookup fields

JSON columns are stored as text everywhere, exactly as given (report definitions contain "$match"-style keys).
Fields a record has that the entity does not list go into an "extra" JSON column, so nothing is ever lost.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.db import model


@dataclass(frozen=True)
class Entity:
    name: str
    fields: dict[str, tuple[str, int | None, bool]]          # name -> (str|int|bool|time|json, size, required)
    key: tuple[str, ...]
    indexes: tuple[tuple[str, ...], ...] = field(default_factory=tuple)

    @property
    def table(self) -> str:
        return f"rs_{self.name}"


USERS = Entity("users", {
    "username": ("str", 64, True), "orgId": ("str", 64, True), "rights": ("json", None, False),
    "superAdmin": ("bool", None, False), "role": ("str", 16, False), "disabled": ("bool", None, False),
    "salt": ("str", 64, False), "hash": ("str", 128, False), "iterations": ("int", None, False),
    "sessionVersion": ("int", None, False), "createdAt": ("time", None, False), "createdBy": ("str", 128, False),
    "lastLoginAt": ("time", None, False),
}, key=("username",))

API_KEYS = Entity("api_keys", {
    "id": ("str", 32, True), "orgId": ("str", 64, True), "name": ("str", 64, True), "hash": ("str", 64, True),
    "folders": ("json", None, False), "allowedIps": ("json", None, False), "expiresAt": ("time", None, False),
    "disabled": ("bool", None, False), "createdBy": ("str", 128, False), "createdAt": ("time", None, False),
    "lastUsedAt": ("time", None, False),
}, key=("id",))

FOLDER_PERMISSIONS = Entity("folder_permissions", {
    "folder": ("str", 240, True), "orgId": ("str", 64, True), "users": ("json", None, False), "roles": ("json", None, False),
}, key=("folder",))

REPORT_VERSIONS = Entity("report_versions", {
    "reportId": ("str", 80, True), "version": ("int", None, True), "orgId": ("str", 64, True), "savedAt": ("time", None, False),
    "action": ("str", 32, False), "user": ("str", 128, False), "folder": ("str", 240, False), "title": ("str", 200, False),
    "definition": ("json", None, False),
}, key=("reportId", "version"))

REPORT_HISTORY = Entity("report_history", {
    "id": ("str", 32, True), "reportId": ("str", 80, True), "orgId": ("str", 64, True), "at": ("time", None, True),
    "action": ("str", 32, True), "user": ("str", 128, False), "detail": ("str", 300, False),
}, key=("id",), indexes=(("reportId", "at"),))

JOB_SCHEDULES = Entity("job_schedules", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "label": ("str", 120, False), "reportId": ("str", 80, True),
    "format": ("str", 8, True), "parameters": ("json", None, False), "recurrence": ("json", None, True), "timezone": ("str", 64, False),
    "enabled": ("bool", None, False), "keep": ("int", None, False), "email": ("json", None, False), "owner": ("str", 128, True),
    "createdAt": ("time", None, False), "updatedAt": ("time", None, False), "nextRunAt": ("time", None, False),
    "lastRun": ("json", None, False),
}, key=("id",), indexes=(("owner",), ("nextRunAt",)))

JOB_RUNS = Entity("job_runs", {
    "id": ("str", 40, True), "orgId": ("str", 64, True), "jobId": ("str", 40, False), "jobLabel": ("str", 120, False),
    "kind": ("str", 16, False), "reportId": ("str", 80, False), "title": ("str", 200, False), "format": ("str", 8, False),
    "parameters": ("json", None, False), "owner": ("str", 128, True), "status": ("str", 16, True), "queuedAt": ("time", None, False),
    "startedAt": ("time", None, False), "finishedAt": ("time", None, False), "rowCount": ("int", None, False),
    "error": ("str", 500, False), "file": ("str", 64, False), "size": ("int", None, False), "email": ("str", 500, False),
}, key=("id",), indexes=(("queuedAt",), ("jobId", "queuedAt"), ("owner", "status")))

ENTITIES = (USERS, API_KEYS, FOLDER_PERMISSIONS, REPORT_VERSIONS, REPORT_HISTORY, JOB_SCHEDULES, JOB_RUNS)


# ------------------------------------------------------------------ record <-> stored values
def encode(entity: Entity, record: dict[str, Any]) -> dict[str, Any]:
    """Known fields cleaned to their types; anything else goes to "extra"."""
    out: dict[str, Any] = {}
    record = {"orgId": model.org_id(), **record}
    for name, (kind, size, required) in entity.fields.items():
        value = record.get(name)
        if value is None or (value == "" and kind != "str"):
            if required:
                raise ValueError(f"{entity.name}.{name} is required")
            out[name] = None
        elif kind == "str":
            text = str(value)
            if required and not text:
                raise ValueError(f"{entity.name}.{name} is required")
            if size and len(text) > size:
                raise ValueError(f"{entity.name}.{name} is longer than {size} characters")
            out[name] = text or None          # Oracle stores '' as NULL: treat both the same everywhere
        elif kind == "int":
            out[name] = int(value)
        elif kind == "bool":
            out[name] = bool(value)
        elif kind == "time":
            out[name] = model.iso(value)
        else:
            out[name] = value
    extra = {name: value for name, value in record.items() if name not in entity.fields}
    out["extra"] = extra or None
    return out


def decode(entity: Entity, stored: dict[str, Any]) -> dict[str, Any]:
    """Back to the record the service uses: empty fields are left out (as in the older JSON documents)."""
    record = {name: stored.get(name) for name in entity.fields if stored.get(name) is not None}
    record.pop("orgId", None)
    record.update(stored.get("extra") or {})
    return record


def key_of(entity: Entity, record: dict[str, Any]) -> tuple:
    return tuple(record[name] for name in entity.key)


# ------------------------------------------------------------------ backends
class EntityBackend:
    def ensure(self) -> None:
        raise NotImplementedError

    def get(self, entity: Entity, *key: Any) -> dict[str, Any] | None:
        raise NotImplementedError

    def find(self, entity: Entity, where: dict[str, Any] | None = None, order: tuple[str, bool] | None = None,
             limit: int | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def put(self, entity: Entity, record: dict[str, Any]) -> None:
        raise NotImplementedError

    def delete(self, entity: Entity, *key: Any) -> bool:
        raise NotImplementedError

    def delete_where(self, entity: Entity, where: dict[str, Any]) -> int:
        raise NotImplementedError

    def replace_all(self, entity: Entity, records: list[dict[str, Any]]) -> None:
        """Make the stored set equal to these records (small lists: users, keys, permissions)."""
        wanted = {key_of(entity, encode(entity, item)): item for item in records}
        for current in self.find(entity):
            if key_of(entity, encode(entity, current)) not in wanted:
                self.delete(entity, *key_of(entity, encode(entity, current)))
        for item in wanted.values():
            self.put(entity, item)


_FILE_LOCK = threading.RLock()


class FileEntities(EntityBackend):
    def __init__(self, root: Path):
        self.folder = root / "records" / "entities"

    def ensure(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)

    def _load(self, entity: Entity) -> dict[str, dict[str, Any]]:
        path = self.folder / f"{entity.name}.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8")).get("items", {})

    def _save(self, entity: Entity, items: dict[str, dict[str, Any]]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{entity.name}.json"
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # accounts and key hashes: private
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"items": items}, stream, ensure_ascii=False, indent=1)
        temporary.replace(path)

    @staticmethod
    def _id(key: tuple) -> str:
        return "␟".join(str(part) for part in key)

    def get(self, entity, *key):
        with _FILE_LOCK:
            stored = self._load(entity).get(self._id(key))
        return decode(entity, stored) if stored else None

    def find(self, entity, where=None, order=None, limit=None):
        with _FILE_LOCK:
            rows = list(self._load(entity).values())
        rows = [row for row in rows if all(row.get(name) == value for name, value in (where or {}).items())]
        if order:
            rows.sort(key=lambda row: (row.get(order[0]) is None, row.get(order[0])), reverse=order[1])
        return [decode(entity, row) for row in rows[:limit] if row]

    def put(self, entity, record):
        stored = encode(entity, record)
        with _FILE_LOCK:
            items = self._load(entity)
            items[self._id(key_of(entity, stored))] = stored
            self._save(entity, items)

    def delete(self, entity, *key):
        with _FILE_LOCK:
            items = self._load(entity)
            found = items.pop(self._id(key), None)
            if found is not None:
                self._save(entity, items)
        return found is not None

    def delete_where(self, entity, where):
        with _FILE_LOCK:
            items = self._load(entity)
            kept = {k: v for k, v in items.items() if not all(v.get(n) == value for n, value in where.items())}
            if len(kept) != len(items):
                self._save(entity, kept)
        return len(items) - len(kept)


class SqlEntities(EntityBackend):
    def __init__(self, url: str):
        from backend.app.db import sql as sql_backend
        self.sql = sql_backend
        self.engine = sql_backend.engine_for(url)
        self.tables = {entity.name: sql_backend.entity_table(entity) for entity in ENTITIES}

    def ensure(self) -> None:
        self.sql.apply_migrations(self.engine, "entities", self.sql.ENTITY_MIGRATIONS)

    def _row(self, entity, stored):
        values = {}
        for name, (kind, _size, _required) in entity.fields.items():
            value = stored[name]
            if kind == "time":
                value = self.sql._db_time(value)
            elif kind == "json" and value is not None:
                value = json.dumps(value, ensure_ascii=False)
            values[self.sql._snake(name)] = value
        values["extra"] = json.dumps(stored["extra"], ensure_ascii=False) if stored.get("extra") else None
        return values

    def _record(self, entity, row):
        mapping = row._mapping
        stored = {}
        for name, (kind, _size, _required) in entity.fields.items():
            value = mapping[self.sql._snake(name)]
            if value is not None:
                if kind == "time":
                    value = model.iso(value)
                elif kind == "json":
                    value = json.loads(value)
                elif kind == "bool":
                    value = bool(value)
                elif kind == "int":
                    value = int(value)
            stored[name] = value
        stored["extra"] = json.loads(mapping["extra"]) if mapping["extra"] else None
        return decode(entity, stored)

    def _match(self, entity, table, values: dict[str, Any]):
        clauses = []
        for name, value in values.items():
            column = table.c[self.sql._snake(name)]
            kind = entity.fields[name][0]
            if kind == "time":
                value = self.sql._db_time(value)
            clauses.append(column.is_(None) if value is None else column == value)
        return clauses

    def get(self, entity, *key):
        table = self.tables[entity.name]
        query = self.sql.sa.select(table).where(*self._match(entity, table, dict(zip(entity.key, key))))
        with self.engine.connect() as connection:
            row = connection.execute(query).first()
        return self._record(entity, row) if row else None

    def find(self, entity, where=None, order=None, limit=None):
        table = self.tables[entity.name]
        query = self.sql.sa.select(table).where(*self._match(entity, table, where or {}))
        if order:
            column = table.c[self.sql._snake(order[0])]
            query = query.order_by(column.desc() if order[1] else column.asc())
        if limit:
            query = query.limit(limit)
        with self.engine.connect() as connection:
            return [self._record(entity, row) for row in connection.execute(query)]

    def put(self, entity, record):
        table = self.tables[entity.name]
        stored = encode(entity, record)
        values = self._row(entity, stored)
        key = dict(zip(entity.key, key_of(entity, stored)))
        for _attempt in range(3):
            try:
                with self.engine.begin() as connection:
                    changed = connection.execute(table.update().where(*self._match(entity, table, key)).values(**values)).rowcount
                    if not changed:
                        connection.execute(table.insert().values(**values))
                return
            except self.sql.sa.exc.IntegrityError:
                continue                       # inserted by another instance at the same moment: update it now

    def delete(self, entity, *key):
        table = self.tables[entity.name]
        with self.engine.begin() as connection:
            return connection.execute(table.delete().where(*self._match(entity, table, dict(zip(entity.key, key))))).rowcount > 0

    def delete_where(self, entity, where):
        table = self.tables[entity.name]
        with self.engine.begin() as connection:
            return connection.execute(table.delete().where(*self._match(entity, table, where))).rowcount


class MongoEntities(EntityBackend):
    def __init__(self, url: str):
        from backend.app.db.mongo import database_for
        self.db = database_for(url)

    def ensure(self) -> None:
        from pymongo import ASCENDING
        for entity in ENTITIES:
            collection = self.db[entity.table]
            for fields in ((entity.key,) if len(entity.key) > 1 else ()) + entity.indexes:
                collection.create_index([(name, ASCENDING) for name in fields], name=f"ix_{entity.table}_{'_'.join(fields)}"[:120])
        self.db["rs_schema_version"].update_one({"_id": "entities"}, {"$max": {"version": 2}, "$set": {"appliedAt": model.now()}}, upsert=True)

    @staticmethod
    def _id(key: tuple) -> str:
        return "␟".join(str(part) for part in key)

    @staticmethod
    def _doc(entity, stored):
        doc = dict(stored)
        for name, (kind, _size, _required) in entity.fields.items():
            if kind == "json" and doc.get(name) is not None:
                doc[name] = json.dumps(doc[name], ensure_ascii=False)     # text: keeps "$match"-style keys as they are
            elif kind == "time" and doc.get(name):
                doc[name] = model.parse_time(doc[name])
        if doc.get("extra"):
            doc["extra"] = json.dumps(doc["extra"], ensure_ascii=False)
        doc["_id"] = MongoEntities._id(key_of(entity, stored))
        return doc

    @staticmethod
    def _record(entity, doc):
        stored = {}
        for name, (kind, _size, _required) in entity.fields.items():
            value = doc.get(name)
            if value is not None:
                if kind == "json":
                    value = json.loads(value)
                elif kind == "time":
                    value = model.iso(value)
            stored[name] = value
        stored["extra"] = json.loads(doc["extra"]) if doc.get("extra") else None
        return decode(entity, stored)

    def _query(self, entity, where):
        query = {}
        for name, value in (where or {}).items():
            kind = entity.fields[name][0]
            query[name] = model.parse_time(value) if kind == "time" and value else value
        return query

    def get(self, entity, *key):
        doc = self.db[entity.table].find_one({"_id": self._id(key)})
        return self._record(entity, doc) if doc else None

    def find(self, entity, where=None, order=None, limit=None):
        from pymongo import ASCENDING, DESCENDING
        cursor = self.db[entity.table].find(self._query(entity, where))
        if order:
            cursor = cursor.sort(order[0], DESCENDING if order[1] else ASCENDING)
        if limit:
            cursor = cursor.limit(limit)
        return [self._record(entity, doc) for doc in cursor]

    def put(self, entity, record):
        doc = self._doc(entity, encode(entity, record))
        self.db[entity.table].replace_one({"_id": doc["_id"]}, doc, upsert=True)

    def delete(self, entity, *key):
        return self.db[entity.table].delete_one({"_id": self._id(key)}).deleted_count > 0

    def delete_where(self, entity, where):
        return self.db[entity.table].delete_many(self._query(entity, where)).deleted_count


# ------------------------------------------------------------------ access
_CACHE: dict[str, EntityBackend] = {}
_LOCK = threading.Lock()


def get_entities() -> EntityBackend:
    from backend.app.storage.files import data_directory
    from backend.app.storage.store import storage_settings
    backend, url = storage_settings()
    if backend == "files":
        found = FileEntities(data_directory())
        found.ensure()
        return found
    key = f"{backend}|{url}"
    with _LOCK:
        if key not in _CACHE:
            found = MongoEntities(url) if backend == "mongodb" else SqlEntities(url)
            found.ensure()
            _CACHE[key] = found
        return _CACHE[key]


def reset_cache() -> None:
    with _LOCK:
        _CACHE.clear()
