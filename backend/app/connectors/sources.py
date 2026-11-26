"""More data sources for SQL reports, beside the four SQL servers in sql.py.

Every one is queried with the same read-only SQL (sql_query.prepare_sql), so reports, Analytics datasets, the Query
workspace and its builder work on all of them:

* Snowflake            - its own driver (snowflake-connector-python); a statement time limit per session.
* ClickHouse           - over its HTTP port with readonly=2 (the server itself refuses writes).
* Elasticsearch        - its SQL API (/_sql); read-only by nature.
* SQLite / DuckDB file - an uploaded database file, opened read-only.
* Files & Sheets       - uploaded CSV, Excel, JSON and Parquet files or a Google Sheet; each file (or sheet) becomes a
                         table that DuckDB queries in-process.
* Redis                - the keys are read (SCAN, HGETALL / GET: read commands only) into an in-memory DuckDB table per
                         key prefix ("user:1", "user:2" → table user), and the SQL runs there.

DuckDB runs with external access switched off (only its own source folder may be read) and its settings locked, so a
query cannot read other files on the server.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from backend.app.connectors.errors import ConnectorQueryError

PARAM = re.compile(r"%\((\w+)\)s")
FILE_KINDS = ("files", "gsheets")          # tables made from files (uploaded, or a Google Sheet)
UPLOAD_KINDS = ("files", "sqlite", "duckdb")
FILE_TYPES = {".csv": "csv", ".tsv": "csv", ".txt": "csv", ".json": "json", ".ndjson": "json", ".jsonl": "json",
              ".xlsx": "excel", ".xlsm": "excel", ".parquet": "parquet"}
DATABASE_FILES = {"sqlite": (".sqlite", ".sqlite3", ".db"), "duckdb": (".duckdb", ".ddb", ".db")}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_FILES = 40
SHEET_FRESH_SECONDS = 300                  # a Google Sheet is read again when its copy is older than this
REDIS_MAX_KEYS = 50_000                    # per table (key prefix)
REDIS_SCAN_KEYS = 20_000                   # keys looked at to list the tables
SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")


def store():
    from backend.app.storage.store import get_store
    return get_store()


def _plain_name(text: str, taken: set[str]) -> str:
    """A table name that needs no quotes: letters, digits and _ (made unique)."""
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", text).strip("_").lower() or "table"
    if name[0].isdigit():
        name = f"t_{name}"
    name, base, n = name[:60], name[:56], 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


# ------------------------------------------------------------------ uploaded files → tables (Parquet)
def source_key(source_id: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,80}", source_id):
        raise ConnectorQueryError("Invalid data source ID")
    return f"sources/{source_id}"


def manifest(source_id: str) -> dict[str, Any]:
    found = store().read_json(f"{source_key(source_id)}/manifest.json") or {}
    return found if isinstance(found, dict) else {}


def _save_manifest(source_id: str, data: dict[str, Any]) -> None:
    store().write_json(f"{source_key(source_id)}/manifest.json", data)


def _rows_to_parquet(con, header: list[str], rows: list[list[Any]]) -> bytes:
    """Rows of a sheet → a Parquet file (types found by DuckDB from all the values)."""
    import pandas as pd
    taken: set[str] = set()
    names = [_plain_name(str(h) if h not in (None, "") else f"column_{i + 1}", taken) for i, h in enumerate(header)]
    frame = pd.DataFrame([list(row[:len(names)]) + [None] * (len(names) - len(row)) for row in rows], columns=names)
    for column in frame.columns:            # mixed text and numbers in one column → text
        kinds = {type(v).__name__ for v in frame[column].dropna().head(5000)}
        if len(kinds - {"int", "float"}) > 0 and len(kinds) > 1:
            frame[column] = frame[column].map(lambda v: None if v is None or v != v else str(v))
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "t.parquet"
        con.register("sheet_rows", frame)
        con.execute(f"COPY (SELECT * FROM sheet_rows) TO '{_sql_path(target)}' (FORMAT parquet, COMPRESSION zstd)")
        con.unregister("sheet_rows")
        return target.read_bytes()


def _sql_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "''")


def _excel_tables(data: bytes) -> list[tuple[str, list[str], list[list[Any]]]]:
    from openpyxl import load_workbook
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - zip / xml errors of a broken file
        raise ConnectorQueryError(f"The Excel file cannot be read: {exc}") from None
    out = []
    for sheet in book.worksheets:
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
        rows = [row for row in rows if any(v not in (None, "") for v in row)]
        if not rows:
            continue
        header, body = rows[0], rows[1:]
        width = max(len(header), *(len(r) for r in body)) if body else len(header)
        while width and all((r[width - 1] if len(r) >= width else None) in (None, "") for r in [header, *body]):
            width -= 1
        out.append((sheet.title, [header[i] if i < len(header) else None for i in range(width)], [r[:width] for r in body]))
    book.close()
    return out


def _file_tables(con, filename: str, data: bytes, taken: set[str]) -> list[dict[str, Any]]:
    """One uploaded file → its tables as Parquet bytes: [{name, file, sheet, data}]."""
    kind = FILE_TYPES.get(Path(filename).suffix.lower())
    if kind is None:
        raise ConnectorQueryError(f"{filename}: use CSV, TSV, Excel (.xlsx), JSON / NDJSON or Parquet")
    stem = Path(filename).stem
    if kind == "excel":
        sheets = _excel_tables(data)
        if not sheets:
            raise ConnectorQueryError(f"{filename}: every sheet is empty")
        single = len(sheets) == 1
        return [{"name": _plain_name(stem if single else f"{stem}_{title}", taken), "file": filename, "sheet": title,
                 "data": _rows_to_parquet(con, header, body)} for title, header, body in sheets]
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / f"in{Path(filename).suffix.lower()}"
        source.write_bytes(data)
        target = Path(tmp) / "t.parquet"
        reader = {"csv": "read_csv_auto({p}, sample_size=-1)", "json": "read_json_auto({p}, sample_size=-1)",
                  "parquet": "read_parquet({p})"}[kind].format(p=f"'{_sql_path(source)}'")
        try:
            con.execute(f"COPY (SELECT * FROM {reader}) TO '{_sql_path(target)}' (FORMAT parquet, COMPRESSION zstd)")  # nosec B608
        except Exception as exc:  # noqa: BLE001 - DuckDB reports what is wrong with the file
            raise ConnectorQueryError(f"{filename} cannot be read: {str(exc).splitlines()[0][:300]}") from None
        return [{"name": _plain_name(stem, taken), "file": filename, "sheet": "", "data": target.read_bytes()}]


def _write_tables(source_id: str, tables: list[dict[str, Any]], extra: dict[str, Any]) -> dict[str, Any]:
    import duckdb
    key = source_key(source_id)
    for old in store().keys(f"{key}/tables/"):
        store().delete(old)
    listed = []
    con = duckdb.connect()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for table in tables:
                store().write_bytes(f"{key}/tables/{table['name']}.parquet", table["data"])
                path = Path(tmp) / f"{table['name']}.parquet"
                path.write_bytes(table["data"])
                rows = con.execute(f"SELECT count(*) FROM read_parquet('{_sql_path(path)}')").fetchone()[0]  # nosec B608
                columns = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{_sql_path(path)}')").fetchall()]  # nosec B608
                listed.append({"name": table["name"], "file": table["file"], "sheet": table["sheet"], "rows": rows, "columns": columns})
    finally:
        con.close()
    data = {**extra, "tables": listed, "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds")}
    _save_manifest(source_id, data)
    return data


def add_file(source_id: str, filename: str, content_b64: str) -> dict[str, Any]:
    """Upload one file into a Files & Sheets source (CSV, Excel, JSON, Parquet), or the database file of a SQLite /
    DuckDB source. A file with the same name is replaced."""
    import duckdb
    filename = Path(str(filename or "")).name.strip()
    if not filename or len(filename) > 160:
        raise ConnectorQueryError("Give the file name")
    try:
        data = base64.b64decode(content_b64 or "", validate=True)
    except ValueError:
        raise ConnectorQueryError("The file must be sent as base64") from None
    if not data:
        raise ConnectorQueryError(f"{filename} is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ConnectorQueryError(f"{filename} is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    key = source_key(source_id)
    current = manifest(source_id)
    files = [f for f in current.get("files", []) if f.get("name") != filename]
    if len(files) >= MAX_FILES:
        raise ConnectorQueryError(f"At most {MAX_FILES} files in one data source")
    store().write_bytes(f"{key}/files/{filename}", data)
    files.append({"name": filename, "bytes": len(data)})
    return _rebuild(source_id, files, duckdb)


def remove_file(source_id: str, filename: str) -> dict[str, Any]:
    import duckdb
    key = source_key(source_id)
    store().delete(f"{key}/files/{Path(filename).name}")
    files = [f for f in manifest(source_id).get("files", []) if f.get("name") != Path(filename).name]
    return _rebuild(source_id, files, duckdb)


def _rebuild(source_id: str, files: list[dict[str, Any]], duckdb) -> dict[str, Any]:
    key, taken, tables = source_key(source_id), set(), []
    con = duckdb.connect()
    try:
        for item in files:
            data = store().read_bytes(f"{key}/files/{item['name']}")
            if data is not None:
                tables.extend(_file_tables(con, item["name"], data, taken))
    finally:
        con.close()
    return _write_tables(source_id, tables, {"files": files})


def put_database_file(source_id: str, kind: str, filename: str, content_b64: str) -> dict[str, Any]:
    """The database file of a SQLite or DuckDB source (replaces the one before)."""
    filename = Path(str(filename or "")).name
    if not filename.lower().endswith(DATABASE_FILES[kind]):
        raise ConnectorQueryError(f"Upload a {kind} file ({', '.join(DATABASE_FILES[kind])})")
    data = base64.b64decode(content_b64 or "", validate=True)
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ConnectorQueryError(f"The file is empty or larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    if kind == "sqlite" and not data.startswith(b"SQLite format 3\x00"):
        raise ConnectorQueryError(f"{filename} is not a SQLite database")
    key = source_key(source_id)
    store().write_bytes(f"{key}/database", data)
    info = {"files": [{"name": filename, "bytes": len(data)}], "tables": [],
            "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds")}
    _save_manifest(source_id, info)
    return info


def remove_source(source_id: str) -> None:
    try:
        store().remove_dir(source_key(source_id), recursive=True)
    except (ConnectorQueryError, OSError):
        pass


def _local_copy(store_key: str, cache: Path) -> Path | None:
    """The stored file as a local file (DuckDB and SQLite read files): copied again when it changed."""
    stamp = store().modified_at(store_key)
    tag = hashlib.sha1(f"{store_key}|{stamp}".encode(), usedforsecurity=False).hexdigest()[:12]
    target = cache / f"{Path(store_key).name}.{tag}"
    if not target.exists():
        data = store().read_bytes(store_key)
        if data is None:
            return None
        cache.mkdir(parents=True, exist_ok=True)
        for old in cache.glob(f"{Path(store_key).name}.*"):
            old.unlink(missing_ok=True)
        part = target.with_suffix(".part")
        part.write_bytes(data)
        part.replace(target)
    return target


def _cache_dir(source_id: str) -> Path:
    return Path(tempfile.gettempdir()) / "reporting-sources" / source_id


# ------------------------------------------------------------------ Google Sheets
def sheet_id_of(link: str) -> str:
    found = SHEET_ID.search(link or "")
    if found:
        return found.group(1)
    return link if re.fullmatch(r"[A-Za-z0-9_-]{20,}", link or "") else ""


def refresh_sheet(sheet_id: str, *, force: bool = False) -> dict[str, Any]:
    """Read the Sheet again (Excel export of every tab) when the copy is older than SHEET_FRESH_SECONDS."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    import duckdb
    source_id = f"gsheet-{sheet_id.lower()[:60]}"
    current = manifest(source_id)
    if not force and current.get("checked", 0) > time.time() - SHEET_FRESH_SECONDS and current.get("tables"):
        return current
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    try:
        with urlopen(Request(url, headers={"User-Agent": "reporting-service"}), timeout=25) as response:  # nosec B310 - fixed https host
            data = response.read(MAX_UPLOAD_BYTES + 1)
            kind = response.headers.get("Content-Type", "")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        if current.get("tables"):
            return current                  # keep the last copy when Google cannot be reached
        reason = getattr(exc, "code", None) or getattr(exc, "reason", exc)
        raise ConnectorQueryError(f"The Google Sheet cannot be read ({reason}). Share it as “Anyone with the link: Viewer”.") from None
    if "html" in kind or not data.startswith(b"PK"):
        raise ConnectorQueryError("Google sent a sign-in page instead of the Sheet: share it as “Anyone with the link: Viewer”")
    con, taken = duckdb.connect(), set()
    try:
        tables = [{"name": _plain_name(title, taken), "file": "Google Sheet", "sheet": title, "data": _rows_to_parquet(con, header, body)}
                  for title, header, body in _excel_tables(data)]
    finally:
        con.close()
    if not tables:
        raise ConnectorQueryError("Every tab of the Google Sheet is empty")
    return _write_tables(source_id, tables, {"sheetId": sheet_id, "checked": time.time()})


# ------------------------------------------------------------------ DuckDB-backed connections
class _Cursor:
    """A DB-API cursor over another one, with a hook that runs before each query (load tables, start the timer)."""

    def __init__(self, owner: "DuckConnection"):
        self.owner = owner
        self.inner = owner.con.cursor()
        self.description = None

    def execute(self, sql: str, params: dict[str, Any] | None = None):
        self.owner.before(sql)
        timer = threading.Timer(self.owner.timeout_ms / 1000, self.owner.con.interrupt)
        timer.start()
        try:
            self.inner.execute(sql, params or {})
        except Exception as exc:  # noqa: BLE001
            if "INTERRUPT" in str(exc).upper():
                raise ConnectorQueryError(f"The query took longer than {self.owner.timeout_ms // 1000} s") from None
            raise
        finally:
            timer.cancel()
        self.description = self.inner.description
        return self

    def fetchmany(self, size: int = 1000):
        return self.inner.fetchmany(size)

    def fetchone(self):
        return self.inner.fetchone()

    def close(self) -> None:
        self.inner.close()


class DuckConnection:
    """In-process DuckDB for files, Google Sheets, Redis and DuckDB files. External access is off: only `allowed`
    (the source's own cache folder) may be read, and the settings are locked."""

    def __init__(self, timeout_ms: int, *, path: Path | None = None, allowed: Path | None = None):
        import duckdb
        self.timeout_ms = timeout_ms
        self.con = duckdb.connect(str(path) if path else ":memory:", read_only=bool(path),
                                  config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
        self.loader = None
        self.allowed = allowed

    def lock(self) -> None:
        if self.allowed is not None:
            folder = _sql_path(self.allowed).rstrip("/") + "/"
            self.con.execute(f"SET allowed_directories = ['{folder}']")
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")

    def before(self, sql: str) -> None:
        if self.loader is not None:
            self.loader(self, sql)

    def cursor(self):
        return _Cursor(self)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.con.close()


def open_files(kind: str, url: str, timeout_ms: int) -> DuckConnection:
    parts = urlsplit(url)
    target = unquote(parts.path.lstrip("/"))
    if kind == "gsheets":
        info = refresh_sheet(target)
        source_id = f"gsheet-{target.lower()[:60]}"
    else:
        source_id, info = target, manifest(target)
    if not info.get("tables"):
        raise ConnectorQueryError("This data source has no files yet: upload CSV, Excel, JSON or Parquet files")
    cache = _cache_dir(source_id)
    duck = DuckConnection(timeout_ms, allowed=cache)
    for table in info["tables"]:
        local = _local_copy(f"{source_key(source_id)}/tables/{table['name']}.parquet", cache)
        if local is not None:
            duck.con.execute(f"CREATE VIEW \"{table['name']}\" AS SELECT * FROM read_parquet('{_sql_path(local)}')")  # nosec B608 - plain names
    duck.lock()
    return duck


def open_database_file(kind: str, url: str, timeout_ms: int):
    source_id = unquote(urlsplit(url).path.lstrip("/"))
    local = _local_copy(f"{source_key(source_id)}/database", _cache_dir(source_id))
    if local is None:
        raise ConnectorQueryError(f"Upload the {'SQLite' if kind == 'sqlite' else 'DuckDB'} database file first")
    if kind == "duckdb":
        duck = DuckConnection(timeout_ms, path=local)
        duck.lock()
        return duck
    import sqlite3
    connection = sqlite3.connect(f"file:{local.as_posix()}?mode=ro", uri=True, timeout=5, check_same_thread=False)
    connection.execute("PRAGMA query_only = 1")
    deadline = time.monotonic() + timeout_ms / 1000
    connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20_000)
    return connection


# ------------------------------------------------------------------ Redis → DuckDB tables
def _redis_client(url: str):
    import redis
    return redis.Redis.from_url(url, socket_connect_timeout=8, socket_timeout=20, decode_responses=True)


def _redis_table(key: str) -> str:
    return key.split(":", 1)[0] if ":" in key else "keys"


def redis_tables(url: str) -> dict[str, int]:
    """Key prefixes (= tables) and how many keys were seen of each (SCAN of up to REDIS_SCAN_KEYS keys)."""
    client = _redis_client(url)
    seen: dict[str, int] = {}
    try:
        for n, key in enumerate(client.scan_iter(count=1000)):
            if n >= REDIS_SCAN_KEYS:
                break
            seen[_redis_table(key)] = seen.get(_redis_table(key), 0) + 1
    finally:
        client.close()
    return seen


def _redis_value(raw: str) -> Any:
    text = raw.strip() if isinstance(raw, str) else raw
    if isinstance(text, str) and text[:1] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            return raw
    return raw


def _redis_rows(client, prefix: str) -> list[dict[str, Any]]:
    pattern = "*" if prefix == "keys" else f"{prefix}:*"
    keys = []
    for key in client.scan_iter(match=pattern, count=1000):
        if prefix == "keys" and ":" in key:
            continue
        keys.append(key)
        if len(keys) >= REDIS_MAX_KEYS:
            break
    rows: list[dict[str, Any]] = []
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        pipe = client.pipeline(transaction=False)
        for key in chunk:
            pipe.type(key)
        kinds = pipe.execute()
        pipe = client.pipeline(transaction=False)
        for key, kind in zip(chunk, kinds):
            {"hash": pipe.hgetall, "string": pipe.get, "list": lambda k: pipe.lrange(k, 0, 999),
             "set": pipe.smembers, "zset": lambda k: pipe.zrange(k, 0, 999, withscores=True)}.get(kind, pipe.type)(key)
        for key, kind, value in zip(chunk, kinds, pipe.execute()):
            row: dict[str, Any] = {"_key": key, "_id": key.split(":", 1)[1] if ":" in key else key, "_type": kind}
            if kind == "hash":
                row.update({field: _redis_value(v) for field, v in value.items()})
            elif kind == "string":
                parsed = _redis_value(value)
                row.update(parsed if isinstance(parsed, dict) else {"value": parsed})
            elif kind in ("list", "set"):
                row["value"] = json.dumps(sorted(value) if kind == "set" else value, ensure_ascii=False)
            elif kind == "zset":
                row["value"] = json.dumps([[member, score] for member, score in value], ensure_ascii=False)
            rows.append(row)
    return rows


def open_redis(url: str, timeout_ms: int) -> DuckConnection:
    tables = redis_tables(url)
    duck = DuckConnection(timeout_ms)
    loaded: set[str] = set()

    def load(owner: DuckConnection, sql: str) -> None:
        import pandas as pd
        words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql)}
        if "information_schema" in words or "duckdb_constraints" in words:
            wanted = set(tables)
        else:
            wanted = {name for name in tables if name.lower() in words}
        wanted -= loaded
        if not wanted:
            return
        client = _redis_client(url)
        try:
            for name in sorted(wanted):
                rows = _redis_rows(client, name)
                frame = pd.json_normalize(rows, max_level=1, sep=".") if rows else pd.DataFrame({"_key": []})
                for column in frame.columns:
                    if frame[column].map(lambda v: isinstance(v, (list, dict))).any():
                        frame[column] = frame[column].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                owner.con.register("redis_rows", frame)
                owner.con.execute(f"CREATE TABLE \"{name.replace(chr(34), '')}\" AS SELECT * FROM redis_rows")  # nosec B608
                owner.con.unregister("redis_rows")
                loaded.add(name)
        finally:
            client.close()

    duck.loader = load
    duck.lock()
    return duck


# ------------------------------------------------------------------ HTTP databases (ClickHouse, Elasticsearch)
def _http(method: str, url: str, body: bytes | None, headers: dict[str, str], timeout: float) -> Any:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
    try:
        with urlopen(Request(url, data=body, method=method, headers=headers), timeout=timeout) as response:  # nosec B310 - the adapter's own server
            return json.loads(response.read() or b"null")
    except HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(text)
            text = (detail.get("error") or {}).get("reason") if isinstance(detail.get("error"), dict) else detail.get("error") or detail.get("exception") or text
        except ValueError:
            pass
        raise ConnectorQueryError(str(text).strip().splitlines()[0][:400] if str(text).strip() else f"HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise ConnectorQueryError(f"Cannot connect: {getattr(exc, 'reason', exc)}") from None


def _auth_header(user: str, password: str) -> dict[str, str]:
    if not user:
        return {}
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class _ListCursor:
    """A cursor over rows already received (ClickHouse) or read page by page (Elasticsearch)."""

    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rows: list[list[Any]] = []
        self.more = None

    def fetchmany(self, size: int = 1000):
        while len(self.rows) < size and self.more:
            page = self.more()
            self.rows.extend(page)
            if not page:
                self.more = None
        out, self.rows = self.rows[:size], self.rows[size:]
        return [tuple(r) for r in out]

    def fetchone(self):
        rows = self.fetchmany(1)
        return rows[0] if rows else None

    def close(self) -> None:
        self.rows, self.more = [], None


def _ch_type(value: Any) -> str:
    if isinstance(value, bool):
        return "Bool"
    if isinstance(value, int):
        return "Int64"
    if isinstance(value, float):
        return "Float64"
    if isinstance(value, datetime):
        return "DateTime64(3)"
    if isinstance(value, date):
        return "Date"
    return "Nullable(String)" if value is None else "String"


class ClickHouseConnection:
    def __init__(self, url: str, timeout_ms: int):
        parts = urlsplit(url)
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        secure = query.get("secure", "").lower() in ("1", "true", "yes")
        self.base = f"{'https' if secure else 'http'}://{parts.hostname}:{parts.port or (8443 if secure else 8123)}/"
        self.database = unquote(parts.path.lstrip("/")) or "default"
        self.headers = {"X-ClickHouse-User": unquote(parts.username or "default"), "X-ClickHouse-Key": unquote(parts.password or "")}
        self.timeout_ms = timeout_ms
        self.execute_raw("SELECT 1")          # connect now: errors belong to "Cannot connect"

    def execute_raw(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        names: dict[str, str] = {}

        def placeholder(match: re.Match) -> str:
            name = match.group(1)
            names[name] = _ch_type(params.get(name))
            return "{" + f"{name}:{names[name]}" + "}"
        text = PARAM.sub(placeholder, sql).replace("%%", "%")
        settings = {"database": self.database, "readonly": "2", "max_execution_time": str(max(1, self.timeout_ms // 1000)),
                    "default_format": "JSONCompact", "output_format_json_quote_64bit_integers": "0"}
        for name in names:
            value = params.get(name)
            settings[f"param_{name}"] = "\\N" if value is None else (value.isoformat(sep=" ") if isinstance(value, datetime) else str(value).lower() if isinstance(value, bool) else str(value))
        return _http("POST", self.base + "?" + urlencode(settings), text.encode(), self.headers, self.timeout_ms / 1000 + 5) or {}

    def cursor(self):
        connection = self

        class Cursor(_ListCursor):
            def execute(self, sql: str, params: dict[str, Any] | None = None):
                result = connection.execute_raw(sql, params)
                self.description = [(c["name"], c.get("type")) for c in result.get("meta", [])] or None
                self.rows = list(result.get("data", []))
                return self
        return Cursor(self)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class ElasticConnection:
    def __init__(self, url: str, timeout_ms: int):
        parts = urlsplit(url)
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        secure = query.get("secure", "").lower() in ("1", "true", "yes")
        self.base = f"{'https' if secure else 'http'}://{parts.hostname}:{parts.port or 9200}"
        self.headers = {"Content-Type": "application/json", **_auth_header(unquote(parts.username or ""), unquote(parts.password or ""))}
        self.timeout_ms = timeout_ms
        info = _http("GET", self.base + "/", None, self.headers, 10) or {}
        self.version = (info.get("version") or {}).get("number", "")
        self.cluster = info.get("cluster_name", "")

    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        return _http("POST", self.base + "/_sql?format=json", json.dumps(body).encode(), self.headers, self.timeout_ms / 1000 + 5) or {}

    def cursor(self):
        connection = self

        class Cursor(_ListCursor):
            def execute(self, sql: str, params: dict[str, Any] | None = None):
                values: list[Any] = []

                def placeholder(match: re.Match) -> str:
                    values.append((params or {}).get(match.group(1)))
                    return "?"
                text = PARAM.sub(placeholder, sql).replace("%%", "%")
                body: dict[str, Any] = {"query": text, "fetch_size": 1000, "request_timeout": f"{max(1, connection.timeout_ms // 1000)}s",
                                        "field_multi_value_leniency": True}
                if values:
                    body["params"] = [v.isoformat() if isinstance(v, (date, datetime)) else v for v in values]
                result = connection.post(body)
                self.description = [(c["name"], c.get("type")) for c in result.get("columns", [])] or None
                self.rows = list(result.get("rows", []))
                cursor_id = result.get("cursor")

                def more():
                    nonlocal cursor_id
                    if not cursor_id:
                        return []
                    page = connection.post({"cursor": cursor_id})
                    cursor_id = page.get("cursor")
                    return page.get("rows", [])
                self.more = more if cursor_id else None
                return self
        return Cursor(self)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def elastic_schema(connection: ElasticConnection) -> list[tuple]:
    """(schema, table, type, column, data type, nullable, position) of every index the user can see (SHOW TABLES/COLUMNS)."""
    out: list[tuple] = []
    tables = connection.post({"query": "SHOW TABLES"}).get("rows", [])
    for row in tables[:300]:
        catalog, name, table_type = row[0], row[1], row[2]
        if str(name).startswith("."):
            continue
        quoted = '"' + str(name).replace('"', '""') + '"'
        columns = connection.post({"query": f"SHOW COLUMNS FROM {quoted}"}).get("rows", [])  # nosec B608 - names from the server
        for position, column in enumerate(columns, start=1):
            out.append((catalog or "", name, "VIEW" if "ALIAS" in str(table_type).upper() else "BASE TABLE", column[0], column[1], "YES", position))
    return out


# ------------------------------------------------------------------ Snowflake
def open_snowflake(module, url: str, timeout_ms: int):
    parts = urlsplit(url)
    path = [unquote(p) for p in parts.path.strip("/").split("/") if p]
    query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    options: dict[str, Any] = {
        "account": parts.hostname, "user": unquote(parts.username or ""), "password": unquote(parts.password or ""),
        "database": path[0] if path else None, "schema": path[1] if len(path) > 1 else None,
        "warehouse": query.get("warehouse") or None, "role": query.get("role") or None,
        "login_timeout": 15, "network_timeout": max(30, timeout_ms // 1000 + 10), "application": "ReportingService",
        "session_parameters": {"STATEMENT_TIMEOUT_IN_SECONDS": max(1, timeout_ms // 1000), "QUERY_TAG": "reporting (read-only)"},
        "client_session_keep_alive": False, "autocommit": False,
    }
    return module.connect(**{k: v for k, v in options.items() if v is not None})
