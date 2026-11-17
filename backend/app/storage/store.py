"""Where the service keeps its data: one small "document store" with four backends.

* files (default): the data directory (REPORTING_DATA_DIR), one JSON file per document, as before.
* postgresql: REPORTING_STORAGE=postgresql and REPORTING_STORAGE_URL=postgresql://… — every document is a row, so
  several app instances can share one repository (put a load balancer in front of them).
* sql: any SQL database the client already has — SQL Server, Oracle, MySQL, PostgreSQL (or SQLite) — see sql_store.py.
* mongodb: a MongoDB database — see mongo_store.py. A client needs only the database they already run.

Documents are addressed by keys that look like relative paths ("reports/General/R1.json", "users.json").
Everything that used to read or write files in the data directory goes through get_store()."""
from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from backend.app.storage.files import _write_json_atomically, data_directory

PRIVATE_MODE = 0o600


def _clean_key(key: str) -> str:
    path = PurePosixPath(str(key).replace("\\", "/"))
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts) or not path.parts:
        raise ValueError(f"Invalid storage key: {key!r}")
    return path.as_posix()


def _prefix(prefix: str) -> str:
    prefix = str(prefix).strip("/")
    return f"{_clean_key(prefix)}/" if prefix else ""


class FileStore:
    backend = "files"

    @property
    def root(self) -> Path:
        return data_directory()

    def path(self, key: str) -> Path:
        return self.root.joinpath(*_clean_key(key).split("/"))

    # ---- JSON documents
    def read_json(self, key: str, default: Any = None) -> Any:
        path = self.path(key)
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def write_json(self, key: str, payload: Any, *, private: bool = False) -> None:
        path = self.path(key)
        if not private:
            _write_json_atomically(path, payload)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)

    def read_many(self, prefix: str, suffix: str = ".json") -> list[tuple[str, Any]]:
        """(key, payload) of every document under prefix; unreadable files are returned with payload None."""
        found = []
        for key in self.keys(prefix, suffix):
            try:
                found.append((key, self.read_json(key)))
            except (OSError, ValueError):
                found.append((key, None))
        return found

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def delete(self, key: str) -> bool:
        path = self.path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def keys(self, prefix: str = "", suffix: str = "") -> list[str]:
        base = self.root.joinpath(*_prefix(prefix).strip("/").split("/")) if prefix else self.root
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob(f"*{suffix}") if p.is_file() and not p.name.endswith(".tmp"))

    def modified_at(self, key: str) -> datetime | None:
        path = self.path(key)
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if path.is_file() else None

    # ---- folders (directories; empty ones are kept)
    def dirs(self, prefix: str) -> list[str]:
        base = self.root.joinpath(*_prefix(prefix).strip("/").split("/"))
        if not base.is_dir():
            return []
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_dir())

    def has_dir(self, key: str) -> bool:
        return self.path(key).is_dir()

    def make_dir(self, key: str) -> None:
        self.path(key).mkdir(parents=True, exist_ok=True)

    def remove_dir(self, key: str, *, recursive: bool = False) -> bool:
        """Remove a folder. Without recursive only when it holds no documents (empty subfolders go too)."""
        path = self.path(key)
        if not path.is_dir():
            return True
        if any(p.is_file() for p in path.rglob("*")) and not recursive:
            return False
        shutil.rmtree(path)
        return True

    # ---- append-only logs and binary files
    def append_line(self, key: str, line: str) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_MODE)
        with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(line.rstrip("\n") + "\n")

    def lines(self, key: str) -> Iterable[str]:
        path = self.path(key)
        if not path.is_file():
            return []
        with path.open(encoding="utf-8", errors="replace") as stream:
            return [line for line in stream]

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_bytes(self, key: str) -> bytes | None:
        path = self.path(key)
        return path.read_bytes() if path.is_file() else None


class PostgresStore:
    """The same API on two tables: reporting_documents (JSON, binary files, folders) and reporting_lines (logs)."""
    backend = "postgresql"

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS reporting_documents (
        key text PRIMARY KEY,
        kind text NOT NULL DEFAULT 'json',     -- json | bytes | dir
        body jsonb,
        data bytea,
        private boolean NOT NULL DEFAULT false,
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS reporting_lines (
        id bigserial PRIMARY KEY,
        key text NOT NULL,
        line text NOT NULL,
        at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS reporting_lines_key ON reporting_lines (key, id);
    """

    def __init__(self, url: str):
        import psycopg
        from psycopg.types.json import Jsonb
        self.psycopg, self.Jsonb, self.url = psycopg, Jsonb, url
        self.lock = threading.RLock()
        self.connection = None
        with self.lock:
            self._execute(self.SCHEMA)

    def _connect(self):
        if self.connection is None or self.connection.closed:
            self.connection = self.psycopg.connect(self.url, autocommit=True, connect_timeout=10)
        return self.connection

    def _execute(self, sql: str, params: Any = None, fetch: str = ""):
        with self.lock:
            for attempt in range(2):
                try:
                    with self._connect().cursor() as cursor:
                        cursor.execute(sql, params)
                        if fetch == "one":
                            return cursor.fetchone()
                        if fetch == "all":
                            return cursor.fetchall()
                        return None
                except self.psycopg.OperationalError:
                    self.connection = None  # reconnect once (database restarted, idle connection dropped)
                    if attempt:
                        raise
        return None

    def read_json(self, key: str, default: Any = None) -> Any:
        row = self._execute("SELECT body FROM reporting_documents WHERE key = %s AND kind = 'json'", (_clean_key(key),), "one")
        return default if row is None else row[0]

    def write_json(self, key: str, payload: Any, *, private: bool = False) -> None:
        self._execute(
            "INSERT INTO reporting_documents (key, kind, body, private, updated_at) VALUES (%s, 'json', %s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET kind = 'json', body = EXCLUDED.body, data = NULL, private = EXCLUDED.private, updated_at = now()",
            (_clean_key(key), self.Jsonb(payload), private))

    def read_many(self, prefix: str, suffix: str = ".json") -> list[tuple[str, Any]]:
        rows = self._execute("SELECT key, body FROM reporting_documents WHERE kind = 'json' AND key LIKE %s AND key LIKE %s ORDER BY key",
                             (_like(_prefix(prefix)) + "%", "%" + _like(suffix)), "all")
        return [(key, body) for key, body in rows or []]

    def exists(self, key: str) -> bool:
        return self._execute("SELECT 1 FROM reporting_documents WHERE key = %s AND kind <> 'dir'", (_clean_key(key),), "one") is not None

    def delete(self, key: str) -> bool:
        row = self._execute("DELETE FROM reporting_documents WHERE key = %s AND kind <> 'dir' RETURNING key", (_clean_key(key),), "one")
        return row is not None

    def keys(self, prefix: str = "", suffix: str = "") -> list[str]:
        rows = self._execute("SELECT key FROM reporting_documents WHERE kind <> 'dir' AND key LIKE %s AND key LIKE %s ORDER BY key",
                             (_like(_prefix(prefix)) + "%", "%" + _like(suffix)), "all")
        return [row[0] for row in rows or []]

    def modified_at(self, key: str) -> datetime | None:
        row = self._execute("SELECT updated_at FROM reporting_documents WHERE key = %s", (_clean_key(key),), "one")
        return row[0] if row else None

    def dirs(self, prefix: str) -> list[str]:
        base = _prefix(prefix)
        rows = self._execute("SELECT key, kind FROM reporting_documents WHERE key LIKE %s", (_like(base) + "%",), "all")
        found: set[str] = set()
        for key, kind in rows or []:
            parts = key[len(base):].strip("/").split("/")
            parts = parts if kind == "dir" else parts[:-1]
            found.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        return sorted(found)

    def has_dir(self, key: str) -> bool:
        return self._execute("SELECT 1 FROM reporting_documents WHERE key = %s", (_clean_key(key) + "/",), "one") is not None

    def make_dir(self, key: str) -> None:
        self._execute("INSERT INTO reporting_documents (key, kind) VALUES (%s, 'dir') ON CONFLICT (key) DO NOTHING", (_clean_key(key) + "/",))

    def remove_dir(self, key: str, *, recursive: bool = False) -> bool:
        base = _clean_key(key) + "/"
        if not recursive and self._execute("SELECT 1 FROM reporting_documents WHERE kind <> 'dir' AND key LIKE %s LIMIT 1", (_like(base) + "%",), "one"):
            return False
        self._execute("DELETE FROM reporting_documents WHERE key LIKE %s", (_like(base) + "%",))
        return True

    def append_line(self, key: str, line: str) -> None:
        self._execute("INSERT INTO reporting_lines (key, line) VALUES (%s, %s)", (_clean_key(key), line.rstrip("\n")))

    def lines(self, key: str) -> Iterable[str]:
        rows = self._execute("SELECT line FROM (SELECT id, line FROM reporting_lines WHERE key = %s ORDER BY id DESC LIMIT 5000) AS t ORDER BY id",
                             (_clean_key(key),), "all")
        return [row[0] for row in rows or []]

    def write_bytes(self, key: str, data: bytes) -> None:
        self._execute(
            "INSERT INTO reporting_documents (key, kind, data, updated_at) VALUES (%s, 'bytes', %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET kind = 'bytes', data = EXCLUDED.data, body = NULL, updated_at = now()",
            (_clean_key(key), data))

    def read_bytes(self, key: str) -> bytes | None:
        row = self._execute("SELECT data FROM reporting_documents WHERE key = %s AND kind = 'bytes'", (_clean_key(key),), "one")
        return bytes(row[0]) if row and row[0] is not None else None


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_STORES: dict[str, Any] = {}
_STORES_LOCK = threading.Lock()

BACKENDS = ("files", "postgresql", "sql", "mongodb")


def storage_settings() -> tuple[str, str]:
    """(backend, url) from REPORTING_STORAGE and REPORTING_STORAGE_URL."""
    backend = os.getenv("REPORTING_STORAGE", "files").strip().lower() or "files"
    backend = {"file": "files", "postgres": "postgresql", "mongo": "mongodb"}.get(backend, backend)
    if backend not in BACKENDS:
        raise RuntimeError(f"REPORTING_STORAGE must be one of {', '.join(BACKENDS)}, not {backend!r}")
    url = os.getenv("REPORTING_STORAGE_URL", "").strip()
    if backend == "postgresql" and not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("REPORTING_STORAGE=postgresql needs REPORTING_STORAGE_URL=postgresql://user:password@host/database")
    if backend == "sql" and not url:
        raise RuntimeError("REPORTING_STORAGE=sql needs REPORTING_STORAGE_URL, e.g. sqlserver://user:password@host:1433/reporting, "
                           "oracle://user:password@host:1521/?service_name=XEPDB1, mysql://… or postgresql://…")
    if backend == "mongodb" and not url.startswith(("mongodb://", "mongodb+srv://")):
        raise RuntimeError("REPORTING_STORAGE=mongodb needs REPORTING_STORAGE_URL=mongodb://user:password@host:27017/reporting_system")
    return backend, url


def get_store() -> Any:
    """The document store for the configured backend (one shared instance per URL)."""
    backend, url = storage_settings()
    if backend == "files":
        return FileStore()
    with _STORES_LOCK:
        cache_key = f"{backend}|{url}"
        if cache_key not in _STORES:
            if backend == "postgresql":
                _STORES[cache_key] = PostgresStore(url)
            elif backend == "sql":
                from backend.app.storage.sql_store import SqlStore  # noqa: PLC0415 - optional backend
                _STORES[cache_key] = SqlStore(url)
            else:
                from backend.app.storage.mongo_store import MongoStore  # noqa: PLC0415 - optional backend
                _STORES[cache_key] = MongoStore(url)
        return _STORES[cache_key]
