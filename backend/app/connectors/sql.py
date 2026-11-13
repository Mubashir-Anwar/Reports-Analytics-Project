"""SQL data adapters. PostgreSQL today (psycopg 3); MySQL, SQL Server and Oracle follow the same functions.

Safety: every query runs in a READ ONLY transaction with a statement time limit, after
queries.sql_query.prepare_sql() has refused anything that is not one read-only statement. Values are always bound
as parameters, never pasted into the SQL. Connection errors are reported without the password.
"""
from __future__ import annotations

import re
import uuid
from datetime import time as daytime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urlsplit

from backend.app.connectors.errors import ConnectorQueryError
from backend.app.queries.sql_query import PreparedSql

SQL_TYPES = {"postgresql"}
URL_PREFIXES = {"postgresql": ("postgresql://", "postgres://")}
CONNECT_TIMEOUT_SECONDS = 8


def _driver():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - requirements.txt installs it
        raise ConnectorQueryError("The PostgreSQL driver is not installed: pip install \"psycopg[binary]\"") from exc
    return psycopg


def valid_url(kind: str, url: str) -> bool:
    return url.startswith(URL_PREFIXES.get(kind, ()))


def database_from_url(url: str) -> str:
    return unquote(urlsplit(url).path.lstrip("/").split("/")[0]) if url else ""


def safe_error(exc: Exception, url: str = "") -> str:
    """The driver's message without the password (and without the URL's user:password part)."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    parts = urlsplit(url) if url else None
    if parts and parts.password:
        text = text.replace(parts.password, "***").replace(unquote(parts.password), "***")
    return re.sub(r"(postgres(?:ql)?://[^:/@\s]+):[^@\s]+@", r"\1:***@", text)[:400]


def _connect(url: str, *, timeout_ms: int):
    psycopg = _driver()
    try:
        connection = psycopg.connect(
            url, connect_timeout=CONNECT_TIMEOUT_SECONDS, autocommit=False,
            options=f"-c statement_timeout={max(1000, int(timeout_ms))} -c default_transaction_read_only=on",
        )
    except psycopg.Error as exc:
        raise ConnectorQueryError(f"Cannot connect to PostgreSQL: {safe_error(exc, url)}") from None
    connection.read_only = True
    return connection


def _plain(value: Any) -> Any:
    """Database values → values the report renderer and the exports understand."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2**53 else float(value)
    if isinstance(value, (uuid.UUID, daytime, timedelta)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


def test(kind: str, url: str) -> dict[str, Any]:
    """Connect, run SELECT version(), report the server version and database."""
    if kind not in SQL_TYPES:
        raise ConnectorQueryError(f"Unknown SQL data adapter type: {kind}")
    with _connect(url, timeout_ms=10_000) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version(), current_database()")
        version, database = cursor.fetchone()
    return {"ok": True, "message": f"Connected: {str(version).split(',')[0]} · database {database}", "database": database}


def run_query(kind: str, url: str, prepared: PreparedSql, values: dict[str, Any], *, max_rows: int, timeout_ms: int) -> list[dict[str, Any]]:
    """Run the report query read-only; returns at most max_rows + 1 rows (so the caller can tell "too many")."""
    sql, params = prepared.bind(values)
    psycopg = _driver()
    try:
        with _connect(url, timeout_ms=timeout_ms) as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            if cursor.description is None:
                raise ConnectorQueryError("The query returned no result set (it must be a SELECT)")
            names = [column.name for column in cursor.description]
            rows = cursor.fetchmany(max_rows + 1)
            connection.rollback()
    except psycopg.Error as exc:
        raise ConnectorQueryError(f"The SQL query failed: {safe_error(exc, url)}") from None
    return [{name: _plain(value) for name, value in zip(names, row)} for row in rows]


def describe(kind: str, url: str, prepared: PreparedSql, values: dict[str, Any] | None = None) -> list[str]:
    """The result columns without reading data (Jasper "Read Fields"): the query wrapped with LIMIT 0."""
    sql, params = prepared.bind(values or {})
    psycopg = _driver()
    try:
        with _connect(url, timeout_ms=15_000) as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM ({sql}) AS report_query LIMIT 0", params)
            names = [column.name for column in cursor.description or []]
            connection.rollback()
    except psycopg.Error as exc:
        raise ConnectorQueryError(f"The fields could not be read: {safe_error(exc, url)}") from None
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConnectorQueryError(f"Give every column a different name (AS …): {', '.join(duplicates)}")
    return names
