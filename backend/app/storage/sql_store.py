"""The document store on any SQL database (PostgreSQL, MySQL, SQL Server, Oracle, SQLite): REPORTING_STORAGE=sql.

Same API as FileStore. Two tables: rs_documents (JSON text, binary files, folder markers) and rs_lines (logs).
JSON is kept as text, so it works the same on every database."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

import sqlalchemy as sa

from backend.app.db import model
from backend.app.db.sql import TEXT, TIME, apply_migrations, engine_for, metadata
from backend.app.storage.store import _clean_key, _prefix

documents = sa.Table(
    "rs_documents", metadata,
    sa.Column("doc_key", sa.Unicode(400), primary_key=True),
    sa.Column("kind", sa.Unicode(8), nullable=False),          # json | bytes | dir
    sa.Column("body", TEXT),
    sa.Column("data", sa.LargeBinary),
    sa.Column("private", sa.Boolean, nullable=False, default=False),
    sa.Column("updated_at", TIME, nullable=False),
)
lines_table = sa.Table(
    "rs_lines", metadata,
    sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.Identity(), primary_key=True),
    sa.Column("doc_key", sa.Unicode(400), nullable=False),
    sa.Column("line", TEXT, nullable=False),
    sa.Column("at", TIME, nullable=False),
    sa.Index("ix_rs_lines_key", "doc_key", "id"),
)

MIGRATIONS = [
    (1, "documents and lines", lambda connection: metadata.create_all(connection, tables=[documents, lines_table], checkfirst=True)),
]


def _now():
    return model.now().replace(tzinfo=None)


class SqlStore:
    backend = "sql"

    def __init__(self, url: str):
        self.url = url                       # identifies this store (shared sign-out state is cached per store)
        self.engine = engine_for(url)
        apply_migrations(self.engine, "documents", MIGRATIONS)

    def _one(self, query) -> Any:
        with self.engine.connect() as connection:
            return connection.execute(query).first()

    def _all(self, query) -> list[Any]:
        with self.engine.connect() as connection:
            return list(connection.execute(query))

    def _upsert(self, key: str, values: dict[str, Any]) -> None:
        values = {"body": None, "data": None, "private": False, **values, "updated_at": _now()}
        with self.engine.begin() as connection:
            done = connection.execute(documents.update().where(documents.c.doc_key == key).values(**values)).rowcount
            if not done:
                connection.execute(documents.insert().values(doc_key=key, **values))

    # ---- JSON documents
    def read_json(self, key: str, default: Any = None) -> Any:
        row = self._one(sa.select(documents.c.body).where(documents.c.doc_key == _clean_key(key), documents.c.kind == "json"))
        return default if row is None or row[0] is None else json.loads(row[0])

    def write_json(self, key: str, payload: Any, *, private: bool = False) -> None:
        self._upsert(_clean_key(key), {"kind": "json", "body": json.dumps(payload, ensure_ascii=False), "private": private})

    def read_many(self, prefix: str, suffix: str = ".json") -> list[tuple[str, Any]]:
        rows = self._all(sa.select(documents.c.doc_key, documents.c.body).where(
            documents.c.kind == "json", documents.c.doc_key.startswith(_prefix(prefix), autoescape=True),
            documents.c.doc_key.endswith(suffix, autoescape=True)).order_by(documents.c.doc_key))
        found = []
        for key, body in rows:
            try:
                found.append((key, json.loads(body) if body is not None else None))
            except ValueError:
                found.append((key, None))
        return found

    def exists(self, key: str) -> bool:
        return self._one(sa.select(documents.c.doc_key).where(documents.c.doc_key == _clean_key(key), documents.c.kind != "dir")) is not None

    def delete(self, key: str) -> bool:
        with self.engine.begin() as connection:
            return connection.execute(documents.delete().where(documents.c.doc_key == _clean_key(key), documents.c.kind != "dir")).rowcount > 0

    def keys(self, prefix: str = "", suffix: str = "") -> list[str]:
        rows = self._all(sa.select(documents.c.doc_key).where(
            documents.c.kind != "dir", documents.c.doc_key.startswith(_prefix(prefix), autoescape=True),
            documents.c.doc_key.endswith(suffix, autoescape=True)).order_by(documents.c.doc_key))
        return [row[0] for row in rows]

    def modified_at(self, key: str) -> datetime | None:
        row = self._one(sa.select(documents.c.updated_at).where(documents.c.doc_key == _clean_key(key)))
        return model.parse_time(row[0]) if row else None

    # ---- folders
    def dirs(self, prefix: str) -> list[str]:
        base = _prefix(prefix)
        rows = self._all(sa.select(documents.c.doc_key, documents.c.kind).where(documents.c.doc_key.startswith(base, autoescape=True)))
        found: set[str] = set()
        for key, kind in rows:
            parts = key[len(base):].strip("/").split("/")
            parts = parts if kind == "dir" else parts[:-1]
            found.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        return sorted(item for item in found if item)

    def make_dir(self, key: str) -> None:
        marker = _clean_key(key) + "/"
        if self._one(sa.select(documents.c.doc_key).where(documents.c.doc_key == marker)) is None:
            try:
                with self.engine.begin() as connection:
                    connection.execute(documents.insert().values(doc_key=marker, kind="dir", private=False, updated_at=_now()))
            except sa.exc.IntegrityError:
                pass                                    # made by another instance at the same moment

    def remove_dir(self, key: str, *, recursive: bool = False) -> bool:
        base = _clean_key(key) + "/"
        inside = documents.c.doc_key.startswith(base, autoescape=True)
        if not recursive and self._one(sa.select(documents.c.doc_key).where(inside, documents.c.kind != "dir").limit(1)):
            return False
        with self.engine.begin() as connection:
            connection.execute(documents.delete().where(inside))
        return True

    # ---- logs and binary files
    def append_line(self, key: str, line: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(lines_table.insert().values(doc_key=_clean_key(key), line=line.rstrip("\n"), at=_now()))

    def lines(self, key: str) -> Iterable[str]:
        rows = self._all(sa.select(lines_table.c.id, lines_table.c.line).where(lines_table.c.doc_key == _clean_key(key))
                         .order_by(lines_table.c.id.desc()).limit(5000))
        return [line for _id, line in reversed(rows)]

    def write_bytes(self, key: str, data: bytes) -> None:
        self._upsert(_clean_key(key), {"kind": "bytes", "data": data})

    def read_bytes(self, key: str) -> bytes | None:
        row = self._one(sa.select(documents.c.data).where(documents.c.doc_key == _clean_key(key), documents.c.kind == "bytes"))
        return bytes(row[0]) if row and row[0] is not None else None
