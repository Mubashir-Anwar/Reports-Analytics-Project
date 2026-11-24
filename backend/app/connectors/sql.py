"""SQL data adapters: PostgreSQL (psycopg 3), MySQL / MariaDB (PyMySQL), SQL Server (pymssql) and Oracle (python-oracledb).

Safety, the same for every database:
* queries.sql_query.prepare_sql() refuses anything that is not one read-only statement;
* values are always bound as parameters, never pasted into the SQL;
* the query runs in a transaction that is read-only where the database supports it (PostgreSQL, MySQL, Oracle)
  and is always rolled back afterwards (SQL Server: so a write that slipped through is undone);
* a time limit per query, and at most max_rows + 1 rows are fetched;
* connection errors are reported without the password.
Give the adapter a database user that may only SELECT; that is the strongest protection.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import time as daytime, timedelta
from decimal import Decimal
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, unquote, urlsplit

from backend.app.connectors.errors import ConnectorQueryError
from backend.app.queries.sql_query import PreparedSql

CONNECT_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class Dialect:
    kind: str
    label: str
    prefixes: tuple[str, ...]
    default_port: int
    module: str
    pip: str
    paramstyle: str        # "pyformat" (%(name)s) or "named" (:name)
    list_style: str        # "any" (= ANY(array)) or "expand" (IN (a, b, c))
    describe_sql: Callable[[str], str]


# The Read Fields wrappers below put the report's own SQL (already checked by prepare_sql: one read-only statement,
# values bound as parameters) inside a query that returns no rows; nothing from a request is pasted in.
DIALECTS: dict[str, Dialect] = {
    "postgresql": Dialect("postgresql", "PostgreSQL", ("postgresql://", "postgres://"), 5432, "psycopg", 'psycopg[binary]',
                          "pyformat", "any", lambda sql: f"SELECT * FROM ({sql}) AS report_query LIMIT 0"),  # nosec B608
    "mysql": Dialect("mysql", "MySQL / MariaDB", ("mysql://", "mariadb://"), 3306, "pymysql", "PyMySQL",
                     "pyformat", "expand", lambda sql: f"SELECT * FROM ({sql}) AS report_query LIMIT 0"),  # nosec B608
    "sqlserver": Dialect("sqlserver", "SQL Server", ("sqlserver://", "mssql://"), 1433, "pymssql", "pymssql",
                         "pyformat", "expand", lambda sql: f"SELECT TOP 0 * FROM ({sql}) AS report_query"),  # nosec B608
    "oracle": Dialect("oracle", "Oracle", ("oracle://",), 1521, "oracledb", "oracledb",
                      "named", "expand", lambda sql: f"SELECT * FROM ({sql}) report_query WHERE 1 = 0"),  # nosec B608
}
SQL_TYPES = set(DIALECTS)
URL_PREFIXES = {kind: d.prefixes for kind, d in DIALECTS.items()}


def dialect(kind: str) -> Dialect:
    found = DIALECTS.get(kind)
    if found is None:
        raise ConnectorQueryError(f"Unknown SQL data adapter type: {kind}")
    return found


def _driver(kind: str = "postgresql"):
    spec = dialect(kind)
    try:
        return __import__(spec.module)
    except ImportError as exc:  # pragma: no cover - requirements.txt installs them
        raise ConnectorQueryError(f"The {spec.label} driver is not installed: pip install \"{spec.pip}\"") from exc


def valid_url(kind: str, url: str) -> bool:
    if not url.startswith(URL_PREFIXES.get(kind, ())):
        return False
    parts = urlsplit(url)
    return bool(parts.hostname) and (kind == "postgresql" or bool(database_from_url(url)))


def database_from_url(url: str) -> str:
    return unquote(urlsplit(url).path.lstrip("/").split("/")[0]) if url else ""


def safe_error(exc: Exception, url: str = "") -> str:
    """The driver's message without the password (and without the URL's user:password part)."""
    args = getattr(exc, "args", ())
    if len(args) == 2 and isinstance(args[0], int) and isinstance(args[1], (bytes, str)):
        # PyMySQL / pymssql: (error number, message); pymssql's message is bytes with DB-Lib lines after it
        message = args[1].decode("utf-8", "replace") if isinstance(args[1], bytes) else args[1]
        text = f"{message.strip().splitlines()[0].split('DB-Lib error')[0].strip() or 'error'} ({args[0]})"
    else:
        text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    parts = urlsplit(url) if url else None
    if parts and parts.password:
        text = text.replace(parts.password, "***").replace(unquote(parts.password), "***")
    return re.sub(r"([a-z]+://[^:/@\s]+):[^@\s]+@", r"\1:***@", text)[:400]


def _url_parts(kind: str, url: str) -> dict[str, Any]:
    parts = urlsplit(url)
    query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    return {
        "host": parts.hostname or "localhost", "port": parts.port or dialect(kind).default_port,
        "user": unquote(parts.username or ""), "password": unquote(parts.password or ""),
        "database": database_from_url(url), "query": query,
    }


# ----------------------------------------------------------------- connections (read-only, time limit)
class _Session:
    """One connection prepared for a report query; closing always rolls back."""

    def __init__(self, kind: str, url: str, timeout_ms: int):
        self.kind, self.url = kind, url
        self.module = _driver(kind)
        timeout_ms = max(1000, int(timeout_ms))
        try:
            self.connection = getattr(self, f"_open_{kind}")(timeout_ms)
        except ConnectorQueryError:
            raise
        except Exception as exc:  # noqa: BLE001 - every driver has its own error classes
            raise ConnectorQueryError(f"Cannot connect to {dialect(kind).label}: {safe_error(exc, url)}") from None

    def _open_postgresql(self, timeout_ms: int):
        connection = self.module.connect(
            self.url, connect_timeout=CONNECT_TIMEOUT_SECONDS, autocommit=False,
            options=f"-c statement_timeout={timeout_ms} -c default_transaction_read_only=on",
        )
        connection.read_only = True
        return connection

    def _open_mysql(self, timeout_ms: int):
        p = _url_parts("mysql", self.url)
        ssl = {"ssl": {}} if p["query"].get("ssl", "").lower() in ("1", "true", "required") else {}
        connection = self.module.connect(
            host=p["host"], port=p["port"], user=p["user"], password=p["password"], database=p["database"],
            connect_timeout=CONNECT_TIMEOUT_SECONDS, read_timeout=max(5, timeout_ms // 1000 + 5), charset="utf8mb4",
            autocommit=False, **ssl,
        )
        with connection.cursor() as cursor:
            try:
                cursor.execute(f"SET SESSION max_execution_time = {timeout_ms}")         # MySQL 5.7.8+
            except self.module.MySQLError:
                cursor.execute(f"SET SESSION max_statement_time = {timeout_ms / 1000:.3f}")  # MariaDB
            cursor.execute("START TRANSACTION READ ONLY")
        return connection

    def _open_sqlserver(self, timeout_ms: int):
        p = _url_parts("sqlserver", self.url)
        connection = self.module.connect(
            server=p["host"], port=str(p["port"]), user=p["user"], password=p["password"], database=p["database"],
            login_timeout=CONNECT_TIMEOUT_SECONDS, timeout=max(1, timeout_ms // 1000), autocommit=False,
            appname="mongo-reporting-service",
        )
        with connection.cursor() as cursor:
            cursor.execute("SET LOCK_TIMEOUT %d" % timeout_ms)  # noqa: UP031 - an int, not user text
        return connection

    def _open_oracle(self, timeout_ms: int):
        p = _url_parts("oracle", self.url)
        self.module.defaults.fetch_lobs = False  # CLOB/BLOB as str/bytes
        connection = self.module.connect(user=p["user"], password=p["password"],
                                         dsn=f"{p['host']}:{p['port']}/{p['database']}", tcp_connect_timeout=CONNECT_TIMEOUT_SECONDS)
        connection.call_timeout = timeout_ms
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        return connection

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        try:
            self.connection.rollback()
        except Exception:  # noqa: BLE001,S110 - the connection may already be broken
            pass
        try:
            self.connection.close()
        except Exception:  # noqa: BLE001,S110
            pass

    def execute(self, sql: str, params: dict[str, Any], what: str):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
        except ConnectorQueryError:
            raise
        except Exception as exc:  # noqa: BLE001
            cursor.close()
            raise ConnectorQueryError(f"{what}: {safe_error(exc, self.url)}") from None
        return cursor


def _plain(value: Any) -> Any:
    """Database values → values the report renderer and the exports understand."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2**53 else float(value)
    if isinstance(value, (uuid.UUID, daytime, timedelta)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


def _names(cursor) -> list[str]:
    return [str(column[0]) for column in cursor.description or []]


def _bind(kind: str, prepared: PreparedSql, values: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    spec = dialect(kind)
    return prepared.bind(values, style=spec.paramstyle, lists=spec.list_style)


TEST_QUERIES = {
    "postgresql": "SELECT version(), current_database()",
    "mysql": "SELECT CONCAT('MySQL ', VERSION()), DATABASE()",
    "sqlserver": "SELECT CONCAT('SQL Server ', CAST(SERVERPROPERTY('ProductVersion') AS varchar(40))), DB_NAME()",
    "oracle": "SELECT 'Oracle', SYS_CONTEXT('USERENV', 'DB_NAME') FROM dual",
}


def test(kind: str, url: str) -> dict[str, Any]:
    """Connect and ask for the server version and the database name."""
    dialect(kind)
    with _Session(kind, url, 10_000) as session:
        cursor = session.execute(TEST_QUERIES[kind], {}, "The test query failed")
        version, database = cursor.fetchone()
        cursor.close()
        if kind == "oracle":
            version = f"Oracle {session.connection.version}"
    return {"ok": True, "message": f"Connected: {str(version).split(',')[0]} · database {database}", "database": str(database or "")}


def run_query(kind: str, url: str, prepared: PreparedSql, values: dict[str, Any], *, max_rows: int, timeout_ms: int) -> list[dict[str, Any]]:
    """Run the report query read-only; returns at most max_rows + 1 rows (so the caller can tell "too many")."""
    sql, params = _bind(kind, prepared, values)
    with _Session(kind, url, timeout_ms) as session:
        cursor = session.execute(sql, params, "The SQL query failed")
        try:
            if cursor.description is None:
                raise ConnectorQueryError("The query returned no result set (it must be a SELECT)")
            names = _names(cursor)
            rows = cursor.fetchmany(max_rows + 1)
        except ConnectorQueryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorQueryError(f"The SQL query failed: {safe_error(exc, url)}") from None
        finally:
            cursor.close()
    return [{name: _plain(value) for name, value in zip(names, row)} for row in rows]


def iter_query(kind: str, url: str, prepared: PreparedSql, values: dict[str, Any], *, timeout_ms: int,
               batch_size: int = 2000) -> Iterator[dict[str, Any]]:
    """The query's rows in batches (big exports): a server-side cursor where the driver needs one (PostgreSQL,
    MySQL), so the database sends the rows as they are read instead of all at once."""
    sql, params = _bind(kind, prepared, values)
    with _Session(kind, url, timeout_ms) as session:
        try:
            if kind == "postgresql":
                cursor = session.connection.cursor(name="report_export")       # psycopg: a server-side cursor
                cursor.itersize = batch_size
                cursor.execute(sql, params)
            elif kind == "mysql":
                cursor = session.connection.cursor(session.module.cursors.SSCursor)  # PyMySQL: unbuffered
                cursor.execute(sql, params)
            else:
                cursor = session.execute(sql, params, "The SQL query failed")
                if kind == "oracle":
                    cursor.arraysize = batch_size
        except ConnectorQueryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorQueryError(f"The SQL query failed: {safe_error(exc, url)}") from None
        try:
            if cursor.description is None:
                raise ConnectorQueryError("The query returned no result set (it must be a SELECT)")
            names = _names(cursor)
            while True:
                try:
                    batch = cursor.fetchmany(batch_size)
                except Exception as exc:  # noqa: BLE001
                    raise ConnectorQueryError(f"The SQL query failed: {safe_error(exc, url)}") from None
                if not batch:
                    break
                for row in batch:
                    yield {name: _plain(value) for name, value in zip(names, row)}
        finally:
            try:
                cursor.close()
            except Exception:  # noqa: BLE001,S110 - closing a broken cursor
                pass


def describe(kind: str, url: str, prepared: PreparedSql, values: dict[str, Any] | None = None) -> list[str]:
    """The result columns without reading data (Jasper "Read Fields"): the query wrapped so it returns no rows.
    SQL Server cannot wrap a WITH query or an ORDER BY without TOP; then the query itself runs and no row is read."""
    sql, params = _bind(kind, prepared, values or {})
    spec = dialect(kind)
    with _Session(kind, url, 15_000) as session:
        try:
            cursor = session.execute(spec.describe_sql(sql), params, "The fields could not be read")
        except ConnectorQueryError:
            if kind != "sqlserver":
                raise
            session.connection.rollback()
            cursor = session.execute(sql, params, "The fields could not be read")
        names = _names(cursor)
        cursor.close()
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConnectorQueryError(f"Give every column a different name (AS …): {', '.join(duplicates)}")
    return names


def run_table(kind: str, url: str, prepared: PreparedSql, values: dict[str, Any], *, max_rows: int,
              timeout_ms: int) -> tuple[list[str], list[list[Any]]]:
    """Like run_query, but the column names as the database gives them (repeats too) and each row as a list:
    what the Query workspace shows. At most max_rows + 1 rows."""
    sql, params = _bind(kind, prepared, values)
    with _Session(kind, url, timeout_ms) as session:
        cursor = session.execute(sql, params, "The SQL query failed")
        try:
            if cursor.description is None:
                raise ConnectorQueryError("The query returned no result set (it must be a SELECT)")
            names = _names(cursor)
            rows = cursor.fetchmany(max_rows + 1)
        except ConnectorQueryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorQueryError(f"The SQL query failed: {safe_error(exc, url)}") from None
        finally:
            cursor.close()
    return names, [[_plain(value) for value in row] for row in rows]


# The schema of a database from its catalog views (fixed text: nothing from a request is put in). Each query gives
# columns: (schema, table, table type, column, data type, nullable, position); primary keys: (schema, table, column);
# foreign keys: (schema, table, column, referenced schema, referenced table, referenced column).
_INFO_COLUMNS = """SELECT c.table_schema, c.table_name, t.table_type, c.column_name, c.data_type, c.is_nullable, c.ordinal_position
FROM information_schema.columns c JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE {where} ORDER BY c.table_schema, c.table_name, c.ordinal_position"""
_INFO_PKS = """SELECT k.table_schema, k.table_name, k.column_name FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage k ON k.constraint_name = tc.constraint_name AND k.table_schema = tc.table_schema AND k.table_name = tc.table_name
WHERE tc.constraint_type = 'PRIMARY KEY' AND {where}"""
SCHEMA_SQL: dict[str, dict[str, str]] = {
    "postgresql": {
        "columns": _INFO_COLUMNS.format(where="c.table_schema NOT IN ('pg_catalog', 'information_schema') AND c.table_schema NOT LIKE 'pg_toast%'"),
        "primaryKeys": _INFO_PKS.format(where="k.table_schema NOT IN ('pg_catalog', 'information_schema')"),
        "foreignKeys": """SELECT k1.table_schema, k1.table_name, k1.column_name, k2.table_schema, k2.table_name, k2.column_name
FROM information_schema.referential_constraints rc
JOIN information_schema.key_column_usage k1 ON k1.constraint_name = rc.constraint_name AND k1.constraint_schema = rc.constraint_schema
JOIN information_schema.key_column_usage k2 ON k2.constraint_name = rc.unique_constraint_name AND k2.constraint_schema = rc.unique_constraint_schema
 AND k2.ordinal_position = k1.position_in_unique_constraint""",
    },
    "mysql": {
        "columns": _INFO_COLUMNS.format(where="c.table_schema = DATABASE()"),
        "primaryKeys": _INFO_PKS.format(where="k.table_schema = DATABASE()"),
        "foreignKeys": """SELECT table_schema, table_name, column_name, referenced_table_schema, referenced_table_name, referenced_column_name
FROM information_schema.key_column_usage WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL""",
    },
    "sqlserver": {
        "columns": _INFO_COLUMNS.format(where="c.table_schema NOT IN ('sys', 'INFORMATION_SCHEMA')"),
        "primaryKeys": _INFO_PKS.format(where="1 = 1"),
        "foreignKeys": """SELECT k1.TABLE_SCHEMA, k1.TABLE_NAME, k1.COLUMN_NAME, k2.TABLE_SCHEMA, k2.TABLE_NAME, k2.COLUMN_NAME
FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE k1 ON k1.CONSTRAINT_NAME = rc.CONSTRAINT_NAME AND k1.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE k2 ON k2.CONSTRAINT_NAME = rc.UNIQUE_CONSTRAINT_NAME AND k2.CONSTRAINT_SCHEMA = rc.UNIQUE_CONSTRAINT_SCHEMA
 AND k2.ORDINAL_POSITION = k1.ORDINAL_POSITION""",
    },
    "oracle": {
        "columns": """SELECT USER, c.table_name, CASE WHEN v.view_name IS NULL THEN 'BASE TABLE' ELSE 'VIEW' END, c.column_name, c.data_type,
 c.nullable, c.column_id FROM user_tab_columns c LEFT JOIN user_views v ON v.view_name = c.table_name ORDER BY c.table_name, c.column_id""",
        "primaryKeys": """SELECT USER, cc.table_name, cc.column_name FROM user_constraints uc
JOIN user_cons_columns cc ON cc.constraint_name = uc.constraint_name WHERE uc.constraint_type = 'P'""",
        "foreignKeys": """SELECT USER, a.table_name, a.column_name, USER, b.table_name, b.column_name FROM user_constraints c
JOIN user_cons_columns a ON a.constraint_name = c.constraint_name
JOIN user_cons_columns b ON b.constraint_name = c.r_constraint_name AND b.position = a.position WHERE c.constraint_type = 'R'""",
    },
}
SCHEMA_MAX_COLUMNS = 50_000


def read_schema(kind: str, url: str) -> dict[str, Any]:
    """Tables, views, columns, primary and foreign keys the adapter's user can see (read-only, 30 s)."""
    queries = SCHEMA_SQL[dialect(kind).kind]
    out: dict[str, Any] = {"truncated": False}
    with _Session(kind, url, 30_000) as session:
        for part in ("columns", "primaryKeys", "foreignKeys"):
            try:
                cursor = session.execute(queries[part], {}, "The schema could not be read")
            except ConnectorQueryError:
                if part == "columns":
                    raise
                session.connection.rollback()   # keys are a bonus: a user without the right still sees the tables
                out[part] = []
                continue
            try:
                rows = cursor.fetchmany(SCHEMA_MAX_COLUMNS + 1)
            finally:
                cursor.close()
            if len(rows) > SCHEMA_MAX_COLUMNS:
                out["truncated"] = True
                rows = rows[:SCHEMA_MAX_COLUMNS]
            out[part] = [tuple(row) for row in rows]
    return out


def url_hint(kind: str) -> str:
    spec = dialect(kind)
    return f"Give a {spec.label} URL: {spec.prefixes[0]}user:password@host:{spec.default_port}/{'service_name' if kind == 'oracle' else 'database'}"


def build_url(kind: str, host: str, port: int | str | None, database: str, user: str, password: str, ssl: bool = False) -> str:
    """The URL the wizard sends (kept here so tests and docs agree with the UI)."""
    from urllib.parse import quote
    spec = dialect(kind)
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user else ""
    suffix = ("?sslmode=require" if kind == "postgresql" else "?ssl=true") if ssl and kind in ("postgresql", "mysql") else ""
    return f"{spec.prefixes[0]}{auth}{host}:{port or spec.default_port}/{quote(database, safe='')}{suffix}"
