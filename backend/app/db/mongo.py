"""MongoDB backend: the same records as collections (rs_report_runs, rs_audit_events) with validators and indexes.

The database comes from the URL path: mongodb://user:pw@host:27017/reporting (Atlas: mongodb+srv://…/db).
Each record is one document; a standalone server is enough (no multi-document transactions are needed:
every write touches one document, and the audit chain is protected by a unique index on seq).
"""
from __future__ import annotations

import threading
from datetime import timezone
from typing import Any
from urllib.parse import urlparse

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import CollectionInvalid, DuplicateKeyError

from backend.app.db import model
from backend.app.db.base import DuplicateSeq, RecordsBackend

_CLIENTS: dict[str, MongoClient] = {}
_CLIENTS_LOCK = threading.Lock()

_BSON = {"str": "string", "int": ["int", "long"], "time": "date", "json": ["object", "array", "string", "int", "long", "double", "bool"]}


def client_for(url: str) -> MongoClient:
    with _CLIENTS_LOCK:
        if url not in _CLIENTS:
            _CLIENTS[url] = MongoClient(url, serverSelectionTimeoutMS=8000, tz_aware=True, appname="mongo-reporting-system-db")
        return _CLIENTS[url]


def database_for(url: str):
    name = urlparse(url).path.strip("/").split("/")[0]
    if not name:
        raise ValueError("Put the database name in the MongoDB URL: mongodb://host:27017/reporting")
    return client_for(url)[name]


def _validator(fields: dict[str, tuple[str, int | None, bool]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for name, (kind, size, required) in fields.items():
        if name == "id":
            continue
        types = _BSON[kind]
        spec: dict[str, Any] = {"bsonType": types if required else ([types] if isinstance(types, str) else types) + ["null"]}
        if kind == "str" and size:
            spec["maxLength"] = size
        properties[name] = spec
    required = [name for name, (_k, _s, needed) in fields.items() if needed and name != "id"]
    return {"$jsonSchema": {"bsonType": "object", "required": required, "properties": properties}}


class MongoRecords(RecordsBackend):
    name = "mongodb"

    def __init__(self, url: str):
        self.url = url
        self.db = database_for(url)
        self.runs_collection = self.db["rs_report_runs"]
        self.events_collection = self.db["rs_audit_events"]

    def ensure_schema(self) -> None:
        for name, fields in (("rs_report_runs", model.RUN_FIELDS), ("rs_audit_events", model.AUDIT_FIELDS)):
            try:
                self.db.create_collection(name, validator=_validator(fields), validationLevel="strict")
            except CollectionInvalid:                       # already there: bring the validator up to date
                self.db.command("collMod", name, validator=_validator(fields), validationLevel="strict")
        runs, events = self.runs_collection, self.events_collection
        runs.create_index([("orgId", ASCENDING), ("startedAt", DESCENDING)], name="ix_rs_runs_started")
        runs.create_index([("reportId", ASCENDING), ("startedAt", DESCENDING)], name="ix_rs_runs_report")
        runs.create_index([("actor", ASCENDING), ("startedAt", DESCENDING)], name="ix_rs_runs_actor")
        runs.create_index([("status", ASCENDING), ("startedAt", DESCENDING)], name="ix_rs_runs_status")
        events.create_index([("seq", ASCENDING)], name="uq_rs_audit_seq", unique=True)
        events.create_index([("orgId", ASCENDING), ("at", DESCENDING)], name="ix_rs_audit_at")
        events.create_index([("action", ASCENDING), ("at", DESCENDING)], name="ix_rs_audit_action")
        events.create_index([("actor", ASCENDING), ("at", DESCENDING)], name="ix_rs_audit_actor")
        self.db["rs_schema_version"].update_one({"_id": "records"}, {"$max": {"version": model.SCHEMA_VERSION},
                                                                      "$set": {"appliedAt": model.now()}}, upsert=True)

    def schema_version(self) -> int:
        found = self.db["rs_schema_version"].find_one({"_id": "records"})
        return int(found["version"]) if found else 0

    @staticmethod
    def _to_doc(record: dict[str, Any], fields: dict) -> dict[str, Any]:
        doc: dict[str, Any] = {"_id": record["id"]}
        for name, (kind, _size, _required) in fields.items():
            if name == "id":
                continue
            value = record.get(name)
            doc[name] = model.parse_time(value) if kind == "time" and value else value
        return doc

    @staticmethod
    def _from_doc(doc: dict[str, Any], fields: dict) -> dict[str, Any]:
        record = {"id": doc["_id"]}
        for name, (kind, _size, _required) in fields.items():
            if name == "id":
                continue
            value = doc.get(name)
            if kind == "time" and value is not None:
                value = model.iso(value.astimezone(timezone.utc) if value.tzinfo else value)
            elif kind == "int" and value is not None:
                value = int(value)
            record[name] = value
        return record

    def _insert_run(self, run: dict[str, Any]) -> None:
        self.runs_collection.insert_one(self._to_doc(run, model.RUN_FIELDS))

    def _find(self, kind, filters, since, until, limit, newest_first=True):
        collection, fields, time_field = ((self.runs_collection, model.RUN_FIELDS, "startedAt") if kind == "runs"
                                          else (self.events_collection, model.AUDIT_FIELDS, "at"))
        query: dict[str, Any] = dict(filters)
        if since or until:
            query[time_field] = {**({"$gte": since} if since else {}), **({"$lt": until} if until else {})}
        direction = DESCENDING if newest_first else ASCENDING
        tie = "seq" if kind == "events" else "_id"            # same millisecond: the chain order, not the id's random end
        cursor = collection.find(query).sort([(time_field, direction), (tie, direction)]).limit(limit)
        return [self._from_doc(doc, fields) for doc in cursor]

    def _last_event(self) -> dict[str, Any] | None:
        doc = self.events_collection.find_one(sort=[("seq", DESCENDING)])
        return self._from_doc(doc, model.AUDIT_FIELDS) if doc else None

    def _insert_event(self, event: dict[str, Any]) -> None:
        try:
            self.events_collection.insert_one(self._to_doc(event, model.AUDIT_FIELDS))
        except DuplicateKeyError:
            raise DuplicateSeq from None

    def _events_from(self, seq: int, limit: int) -> list[dict[str, Any]]:
        cursor = self.events_collection.find({"seq": {"$gte": seq}}).sort("seq", ASCENDING).limit(limit)
        return [self._from_doc(doc, model.AUDIT_FIELDS) for doc in cursor]
