"""SQL backend: PostgreSQL, MySQL, SQL Server, Oracle or SQLite through SQLAlchemy Core (no ORM).

Every table starts with rs_ so it can live next to other tables. Columns use types every database has
(NVARCHAR/VARCHAR2 with lengths, large text for JSON, millisecond timestamps in UTC without a time zone).
Schema changes are numbered steps in MIGRATIONS; rs_schema_version remembers the last one applied.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import mssql, mysql, oracle

from backend.app.db import model
from backend.app.db.base import DuplicateSeq, RecordsBackend
from backend.app.db.urls import sqlalchemy_url

# Millisecond timestamps on every database (MySQL DATETIME and Oracle DATE would drop the milliseconds).
TIME = (sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=3), "mysql", "mariadb")
        .with_variant(mssql.DATETIME2(precision=3), "mssql")
        .with_variant(oracle.TIMESTAMP(), "oracle"))
# NVARCHAR(MAX) on SQL Server, CLOB on Oracle, LONGTEXT on MySQL (its TEXT stops at 64 KB), TEXT elsewhere
TEXT = sa.UnicodeText().with_variant(mysql.LONGTEXT(), "mysql", "mariadb")
# Files (report outputs): MySQL's BLOB also stops at 64 KB
BINARY = sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb")

metadata = sa.MetaData()

# Oracle reserves these but SQLAlchemy's Oracle word list does not have them, so columns with these names are
# always quoted (quoting a lower-case name changes nothing on the other databases).
ALWAYS_QUOTE = {"file", "access", "audit", "column", "level", "mode", "online", "rows", "session", "uid", "validate"}

schema_version = sa.Table(
    "rs_schema_version", metadata,
    sa.Column("component", sa.Unicode(32), primary_key=True),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("applied_at", TIME, nullable=False),
)


def _snake(name: str) -> str:
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in name)


def _columns(fields: dict[str, tuple[str, int | None, bool]], primary: str) -> list[sa.Column]:
    columns = []
    for name, (kind, size, required) in fields.items():
        column_type = {"str": sa.Unicode(size or 255), "int": sa.BigInteger(), "time": TIME, "json": TEXT}[kind]
        column = _snake(name)
        columns.append(sa.Column(column, column_type, primary_key=name == primary, nullable=not required and name != primary,
                                 quote=True if column in ALWAYS_QUOTE else None))
    return columns


report_runs = sa.Table("rs_report_runs", metadata, *_columns(model.RUN_FIELDS, "id"),
                       sa.Index("ix_rs_runs_started", "org_id", "started_at"),
                       sa.Index("ix_rs_runs_report", "report_id", "started_at"),
                       sa.Index("ix_rs_runs_actor", "actor", "started_at"),
                       sa.Index("ix_rs_runs_status", "status", "started_at"))

audit_events = sa.Table("rs_audit_events", metadata, *_columns(model.AUDIT_FIELDS, "id"),
                        sa.UniqueConstraint("seq", name="uq_rs_audit_seq"),
                        sa.Index("ix_rs_audit_at", "org_id", "at"),
                        sa.Index("ix_rs_audit_action", "action", "at"),
                        sa.Index("ix_rs_audit_actor", "actor", "at"))

# Numbered schema steps for the records component. Never change a step that has shipped; add a new one.
MIGRATIONS: list[tuple[int, str, Any]] = [
    (1, "report runs and audit events", lambda connection: metadata.create_all(
        connection, tables=[report_runs, audit_events], checkfirst=True)),
]

def entity_table(entity: Any) -> sa.Table:
    """The rs_<name> table of a typed entity (db/entities.py), built once."""
    if entity.table in metadata.tables:
        return metadata.tables[entity.table]
    kinds = {"str": None, "int": sa.BigInteger(), "bool": sa.Boolean(), "time": TIME, "json": TEXT}
    columns = []
    for name, (kind, size, required) in entity.fields.items():
        column_type = sa.Unicode(size or 255) if kind == "str" else kinds[kind]
        column = _snake(name)
        columns.append(sa.Column(column, column_type, primary_key=name in entity.key, nullable=not required,
                                 quote=True if column in ALWAYS_QUOTE else None))
    columns.append(sa.Column("extra", TEXT))
    indexes = [sa.Index(f"ix_{entity.table}_{'_'.join(_snake(f) for f in fields)}"[:30], *[_snake(f) for f in fields])
               for fields in entity.indexes]
    return sa.Table(entity.table, metadata, *columns, *indexes)


def _create_entities(connection: Any) -> None:
    from backend.app.db.entities import ENTITIES  # noqa: PLC0415 - entities import this module
    metadata.create_all(connection, tables=[entity_table(entity) for entity in ENTITIES], checkfirst=True)


# Numbered schema steps for the typed entities (accounts, keys, permissions, versions, history).
ENTITY_MIGRATIONS: list[tuple[int, str, Any]] = [
    (1, "users, API keys, folder permissions, report versions and history", _create_entities),
    (2, "schedules and job runs", _create_entities),      # create_all only adds the tables that are missing
]

_ENGINES: dict[str, sa.Engine] = {}
_ENGINES_LOCK = threading.Lock()


def engine_for(url: str) -> sa.Engine:
    """One pooled engine per URL for the whole process."""
    full = sqlalchemy_url(url)
    with _ENGINES_LOCK:
        if full not in _ENGINES:
            options: dict[str, Any] = {"pool_pre_ping": True}
            if full.startswith("sqlite"):
                options["connect_args"] = {"check_same_thread": False}
            _ENGINES[full] = sa.create_engine(full, **options)
        return _ENGINES[full]


def apply_migrations(engine: sa.Engine, component: str, steps: list[tuple[int, str, Any]]) -> int:
    """Run the steps newer than the stored version, each with its version update in one transaction."""
    metadata.create_all(engine, tables=[schema_version], checkfirst=True)
    with engine.begin() as connection:
        row = connection.execute(sa.select(schema_version.c.version).where(schema_version.c.component == component)).first()
    current = row[0] if row else 0
    for version, _title, step in steps:
        if version <= current:
            continue
        with engine.begin() as connection:
            step(connection)
            values = {"version": version, "applied_at": _db_time(model.now())}
            if current == 0:
                connection.execute(schema_version.insert().values(component=component, **values))
            else:
                connection.execute(schema_version.update().where(schema_version.c.component == component).values(**values))
        current = version
    return current


def _db_time(value: datetime | str | None) -> datetime | None:
    """Naive UTC for the database."""
    if value is None:
        return None
    return model.parse_time(value).astimezone(timezone.utc).replace(tzinfo=None)


class SqlRecords(RecordsBackend):
    name = "sql"

    def __init__(self, url: str):
        self.url = url
        self.engine = engine_for(url)

    def ensure_schema(self) -> None:
        apply_migrations(self.engine, "records", MIGRATIONS)

    def schema_version(self) -> int:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(sa.select(schema_version.c.version).where(schema_version.c.component == "records")).first()
            return row[0] if row else 0
        except sa.exc.DBAPIError:
            return 0

    # camelCase records <-> snake_case rows
    @staticmethod
    def _to_row(record: dict[str, Any], fields: dict) -> dict[str, Any]:
        row = {}
        for name, (kind, _size, _required) in fields.items():
            value = record.get(name)
            if kind == "time":
                value = _db_time(value)
            elif kind == "json" and value is not None:
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            row[_snake(name)] = value
        return row

    @staticmethod
    def _from_row(row: Any, fields: dict) -> dict[str, Any]:
        mapping = row._mapping
        record = {}
        for name, (kind, _size, _required) in fields.items():
            value = mapping[_snake(name)]
            if kind == "time":
                value = model.iso(value)
            elif kind == "json" and value is not None:
                value = json.loads(value)
            elif kind == "int" and value is not None:
                value = int(value)
            record[name] = value
        return record

    def _insert_run(self, run: dict[str, Any]) -> None:
        with self.engine.begin() as connection:
            connection.execute(report_runs.insert().values(**self._to_row(run, model.RUN_FIELDS)))

    def _find(self, kind, filters, since, until, limit, newest_first=True):
        table, fields, time_column = ((report_runs, model.RUN_FIELDS, "started_at") if kind == "runs"
                                      else (audit_events, model.AUDIT_FIELDS, "at"))
        query = sa.select(table)
        for key, value in filters.items():
            query = query.where(table.c[_snake(key)] == value)
        if since:
            query = query.where(table.c[time_column] >= _db_time(since))
        if until:
            query = query.where(table.c[time_column] < _db_time(until))
        order = [table.c[time_column], table.c.seq if kind == "events" else table.c.id]
        query = query.order_by(*[column.desc() if newest_first else column.asc() for column in order]).limit(limit)
        with self.engine.connect() as connection:
            return [self._from_row(row, fields) for row in connection.execute(query)]

    def _last_event(self) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(sa.select(audit_events).order_by(audit_events.c.seq.desc()).limit(1)).first()
        return self._from_row(row, model.AUDIT_FIELDS) if row else None

    def _insert_event(self, event: dict[str, Any]) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(audit_events.insert().values(**self._to_row(event, model.AUDIT_FIELDS)))
        except sa.exc.IntegrityError:
            raise DuplicateSeq from None
        except sa.exc.DBAPIError as exc:
            # SQL Server 1205, MySQL 1213, PostgreSQL 40P01/40001: the database chose this insert as a deadlock victim
            text = str(exc.orig).lower()
            if "deadlock" in text or "40001" in text or "could not serialize" in text:
                raise DuplicateSeq from None
            raise

    def _events_from(self, seq: int, limit: int) -> list[dict[str, Any]]:
        query = sa.select(audit_events).where(audit_events.c.seq >= seq).order_by(audit_events.c.seq).limit(limit)
        with self.engine.connect() as connection:
            return [self._from_row(row, model.AUDIT_FIELDS) for row in connection.execute(query)]
