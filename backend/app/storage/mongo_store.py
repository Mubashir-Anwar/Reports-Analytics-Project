"""The document store on MongoDB: REPORTING_STORAGE=mongodb, REPORTING_STORAGE_URL=mongodb://…/reporting.

Same API as FileStore. Collections rs_documents ({_id: key, kind, body, data, private, updatedAt}) and rs_lines.
The JSON body is kept as text: report definitions contain MongoDB pipelines whose "$match"-style keys cannot be
stored as field names safely, and text keeps the document exactly as it was written."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable

import gridfs
from bson.binary import Binary
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from backend.app.db import model
from backend.app.db.mongo import database_for
from backend.app.storage.routed import RoutedStore
from backend.app.storage.store import _clean_key, _prefix


def _starts(prefix: str) -> dict[str, Any]:
    return {"$regex": "^" + re.escape(prefix)}


class _MongoDocuments:
    backend = "mongodb"

    INLINE_LIMIT = 15 * 1024 * 1024    # bigger files (large report outputs) go to GridFS: a document holds 16 MB

    def __init__(self, url: str):
        self.url = url                       # identifies this store (shared sign-out state is cached per store)
        db = database_for(url)
        self.files = gridfs.GridFSBucket(db, bucket_name="rs_files")
        self.documents = db["rs_documents"]
        self.lines_collection = db["rs_lines"]
        self.documents.create_index([("kind", ASCENDING), ("_id", ASCENDING)], name="ix_rs_documents_kind")
        self.lines_collection.create_index([("key", ASCENDING), ("_id", ASCENDING)], name="ix_rs_lines_key")
        db["rs_schema_version"].update_one({"_id": "documents"}, {"$max": {"version": 1}, "$set": {"appliedAt": model.now()}}, upsert=True)

    def _put(self, key: str, values: dict[str, Any]) -> None:
        self.documents.replace_one({"_id": key}, {"_id": key, "body": None, "data": None, "private": False, **values,
                                                   "updatedAt": model.now()}, upsert=True)

    def read_json(self, key: str, default: Any = None) -> Any:
        doc = self.documents.find_one({"_id": _clean_key(key), "kind": "json"}, {"body": 1})
        return default if doc is None or doc.get("body") is None else json.loads(doc["body"])

    def write_json(self, key: str, payload: Any, *, private: bool = False) -> None:
        self._put(_clean_key(key), {"kind": "json", "body": json.dumps(payload, ensure_ascii=False), "private": private})

    def read_many(self, prefix: str, suffix: str = ".json") -> list[tuple[str, Any]]:
        found = []
        for doc in self.documents.find({"_id": _starts(_prefix(prefix)), "kind": "json"}, {"body": 1}).sort("_id", ASCENDING):
            if not doc["_id"].endswith(suffix):
                continue
            try:
                found.append((doc["_id"], json.loads(doc["body"]) if doc.get("body") is not None else None))
            except ValueError:
                found.append((doc["_id"], None))
        return found

    def exists(self, key: str) -> bool:
        return self.documents.count_documents({"_id": _clean_key(key), "kind": {"$ne": "dir"}}, limit=1) > 0

    def delete(self, key: str) -> bool:
        doc = self.documents.find_one_and_delete({"_id": _clean_key(key), "kind": {"$ne": "dir"}}, {"gridfsId": 1})
        if doc and doc.get("gridfsId") is not None:
            self.files.delete(doc["gridfsId"])
        return doc is not None

    def keys(self, prefix: str = "", suffix: str = "") -> list[str]:
        cursor = self.documents.find({"_id": _starts(_prefix(prefix)), "kind": {"$ne": "dir"}}, {"_id": 1}).sort("_id", ASCENDING)
        return [doc["_id"] for doc in cursor if doc["_id"].endswith(suffix)]

    def modified_at(self, key: str) -> datetime | None:
        doc = self.documents.find_one({"_id": _clean_key(key)}, {"updatedAt": 1})
        return model.parse_time(doc["updatedAt"]) if doc else None

    def dirs(self, prefix: str) -> list[str]:
        base = _prefix(prefix)
        found: set[str] = set()
        for doc in self.documents.find({"_id": _starts(base)}, {"kind": 1}):
            parts = doc["_id"][len(base):].strip("/").split("/")
            parts = parts if doc.get("kind") == "dir" else parts[:-1]
            found.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        return sorted(item for item in found if item)

    def has_dir(self, key: str) -> bool:
        return self.documents.count_documents({"_id": _clean_key(key) + "/"}, limit=1) > 0

    def make_dir(self, key: str) -> None:
        try:
            self.documents.insert_one({"_id": _clean_key(key) + "/", "kind": "dir", "private": False, "updatedAt": model.now()})
        except DuplicateKeyError:
            pass

    def remove_dir(self, key: str, *, recursive: bool = False) -> bool:
        inside = _starts(_clean_key(key) + "/")
        if not recursive and self.documents.count_documents({"_id": inside, "kind": {"$ne": "dir"}}, limit=1):
            return False
        self.documents.delete_many({"_id": inside})
        return True

    def append_line(self, key: str, line: str) -> None:
        self.lines_collection.insert_one({"key": _clean_key(key), "line": line.rstrip("\n"), "at": model.now()})

    def delete_lines(self, key: str) -> int:
        return self.lines_collection.delete_many({"key": _clean_key(key)}).deleted_count

    def lines(self, key: str) -> Iterable[str]:
        cursor = self.lines_collection.find({"key": _clean_key(key)}, {"line": 1}).sort("_id", DESCENDING).limit(5000)
        return [doc["line"] for doc in reversed(list(cursor))]

    def write_bytes(self, key: str, data: bytes) -> None:
        key = _clean_key(key)
        old = self.documents.find_one({"_id": key}, {"gridfsId": 1})
        if len(data) <= self.INLINE_LIMIT:
            self._put(key, {"kind": "bytes", "data": Binary(data)})
        else:
            file_id = self.files.upload_from_stream(key, data)
            self._put(key, {"kind": "bytes", "gridfsId": file_id})
        if old and old.get("gridfsId") is not None:
            self.files.delete(old["gridfsId"])

    def read_bytes(self, key: str) -> bytes | None:
        doc = self.documents.find_one({"_id": _clean_key(key), "kind": "bytes"}, {"data": 1, "gridfsId": 1})
        if not doc:
            return None
        if doc.get("gridfsId") is not None:
            return self.files.open_download_stream(doc["gridfsId"]).read()
        return bytes(doc["data"]) if doc.get("data") is not None else None


class MongoStore(RoutedStore, _MongoDocuments):
    """The document store on MongoDB, with reports, drafts, folders and data adapters in their own collections."""
