"""The system database (run log, audit log, …) on files, any SQL database or MongoDB.

It follows the storage setting, so one choice covers everything the service stores:

    REPORTING_STORAGE=files                                   (default) <data dir>/records/
    REPORTING_STORAGE=postgresql|sql  REPORTING_STORAGE_URL=  postgresql:// mysql:// sqlserver:// oracle:// sqlite:///
    REPORTING_STORAGE=mongodb         REPORTING_STORAGE_URL=  mongodb://host:27017/reporting

    from backend.app.db import get_records
    get_records().add_run({...});  get_records().audit("user.login", actor="ali")
"""
from __future__ import annotations

import threading

from backend.app.db.base import RecordsBackend

_CACHE: dict[str, RecordsBackend] = {}
_LOCK = threading.Lock()


def get_records() -> RecordsBackend:
    from backend.app.storage.store import storage_settings  # noqa: PLC0415 - avoid an import cycle
    backend, url = storage_settings()
    if backend == "files":
        from backend.app.db.files import FileRecords  # noqa: PLC0415
        records = FileRecords()
        records.ensure_schema()
        return records
    key = f"{backend}|{url}"
    with _LOCK:
        if key not in _CACHE:
            if backend == "mongodb":
                from backend.app.db.mongo import MongoRecords  # noqa: PLC0415
                records = MongoRecords(url)
            else:
                from backend.app.db.sql import SqlRecords  # noqa: PLC0415
                records = SqlRecords(url)
            records.ensure_schema()
            _CACHE[key] = records
        return _CACHE[key]


def reset_cache() -> None:
    """Tests and the migration tool: forget open backends."""
    with _LOCK:
        _CACHE.clear()


__all__ = ["get_records", "reset_cache", "RecordsBackend"]
