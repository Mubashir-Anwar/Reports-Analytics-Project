"""More data sources (connectors/sources.py): Files & Sheets, SQLite and DuckDB files, Redis, ClickHouse and
Elasticsearch, all queried with the same read-only SQL and $P{…} parameters as the SQL databases."""
import base64
import io
import json
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("openpyxl")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_workspace_and_server import ADMIN, client, token  # noqa: E402,F401

from backend.app.connectors import sources, sql  # noqa: E402
from backend.app.connectors.errors import ConnectorQueryError  # noqa: E402
from backend.app.queries.sql_query import prepare_sql  # noqa: E402


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def workbook() -> bytes:
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = "Orders"
    sheet.append(["Order Id", "Customer", "Total"])
    sheet.append([1, "Ali", 10.5])
    sheet.append([2, "Sara", 20])
    items = book.create_sheet("Items")
    items.append(["sku", "qty"])
    items.append(["A1", 3])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def run(kind, url, text, values=None):
    return sql.run_query(kind, url, prepare_sql(text), values or {}, max_rows=50, timeout_ms=10_000)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    return tmp_path


def test_files_become_tables(data_dir):
    sources.add_file("shop", "sales.csv", b64(b"id,city,amount\n1,Lahore,50\n2,Karachi,70\n3,Lahore,20\n"))
    sources.add_file("shop", "book.xlsx", b64(workbook()))
    info = sources.add_file("shop", "people.json", b64(b'[{"name": "A", "age": 3}, {"name": "B", "age": 5}]'))
    tables = {t["name"]: t for t in info["tables"]}
    assert set(tables) == {"sales", "book_orders", "book_items", "people"}
    assert tables["book_orders"]["columns"] == ["order_id", "customer", "total"] and tables["sales"]["rows"] == 3
    url = "files://local/shop"
    assert "4 tables" in sql.test("files", url)["message"]
    rows = run("files", url, "SELECT city, SUM(amount) AS total FROM sales WHERE amount >= $P{Min} AND $X{IN, city, Cities} GROUP BY city ORDER BY city",
               {"Min": 20, "Cities": ["Lahore", "Karachi"]})
    assert rows == [{"city": "Karachi", "total": 70}, {"city": "Lahore", "total": 70}]
    assert run("files", url, "SELECT customer FROM book_orders ORDER BY order_id") == [{"customer": "Ali"}, {"customer": "Sara"}]
    assert sql.describe("files", url, prepare_sql("SELECT name, age FROM people")) == ["name", "age"]
    assert len(sql.read_schema("files", url)["columns"]) == 10
    # one file less: its tables go
    assert {t["name"] for t in sources.remove_file("shop", "book.xlsx")["tables"]} == {"sales", "people"}


@pytest.mark.parametrize("text", ["SELECT * FROM read_csv('{p}')", "SELECT * FROM read_text('{p}')", "SELECT * FROM '{p}'"])
def test_files_cannot_read_other_files_of_the_server(data_dir, text):
    secret = data_dir / "private_secrets.json"
    secret.write_text('{"admin_token": "x"}', encoding="utf-8")
    sources.add_file("shop", "a.csv", b64(b"x\n1\n"))
    with pytest.raises(ConnectorQueryError):
        run("files", "files://local/shop", text.format(p=secret.as_posix()))


def test_sqlite_and_duckdb_files_are_read_only(data_dir, tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "shop.sqlite"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE c(id INTEGER PRIMARY KEY, name TEXT); CREATE TABLE o(id INTEGER PRIMARY KEY, cid INTEGER REFERENCES c(id), amt REAL);"
                      "INSERT INTO c VALUES (1, 'X'), (2, 'Y'); INSERT INTO o VALUES (1, 1, 5), (2, 2, 7);")
    con.commit()
    con.close()
    sources.put_database_file("lite", "sqlite", "shop.sqlite", b64(path.read_bytes()))
    url = "sqlite://local/lite"
    assert run("sqlite", url, "SELECT c.name, o.amt FROM o JOIN c ON c.id = o.cid WHERE o.amt > $P{A}", {"A": 6}) == [{"name": "Y", "amt": 7.0}]
    schema = sql.read_schema("sqlite", url)
    assert ("main", "o", "cid", "main", "c", "id") in schema["foreignKeys"]
    with pytest.raises(ConnectorQueryError):
        sources.put_database_file("lite", "sqlite", "fake.sqlite", b64(b"not a database"))

    import duckdb
    dpath = tmp_path_factory.mktemp("duck") / "x.duckdb"
    d = duckdb.connect(str(dpath))
    d.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v VARCHAR); INSERT INTO t VALUES (1, 'a'), (2, 'b')")
    d.close()
    sources.put_database_file("duck", "duckdb", "x.duckdb", b64(dpath.read_bytes()))
    assert run("duckdb", "duckdb://local/duck", "SELECT v FROM t WHERE id = $P{I}", {"I": 2}) == [{"v": "b"}]
    assert sql.read_schema("duckdb", "duckdb://local/duck")["primaryKeys"] == [("main", "t", "id")]


# ---------------------------------------------------------------- Redis (a fake client with the redis-py calls used)
REDIS = {"user:1": ("hash", {"name": "Ali", "age": "30"}), "user:2": ("hash", {"name": "Sara", "age": "25", "city": "Lahore"}),
         "order:9": ("string", '{"total": 12.5, "items": [1, 2]}'), "hits": ("string", "77")}


class FakePipe:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda key, *a, **k: self.calls.append((name, key))

    def execute(self):
        return [REDIS[key][0] if name == "type" else REDIS[key][1] for name, key in self.calls]


class FakeRedis:
    def scan_iter(self, match="*", count=0):
        import fnmatch
        return [key for key in REDIS if fnmatch.fnmatch(key, match)]

    def pipeline(self, transaction=False):
        return FakePipe()

    def close(self):
        pass


def test_redis_key_prefixes_are_tables(monkeypatch):
    monkeypatch.setattr(sources, "_redis_client", lambda url: FakeRedis())
    url = "redis://localhost:6379/0"
    assert "3 key prefixes" in sql.test("redis", url)["message"]
    rows = run("redis", url, "SELECT name, CAST(age AS INT) AS age, city FROM user WHERE name <> $P{N} ORDER BY name", {"N": "x"})
    assert rows == [{"name": "Ali", "age": 30, "city": None}, {"name": "Sara", "age": 25, "city": "Lahore"}]
    assert run("redis", url, 'SELECT _id, total FROM "order"') == [{"_id": "9", "total": 12.5}]
    assert run("redis", url, "SELECT value FROM keys") == [{"value": "77"}]


# ---------------------------------------------------------------- ClickHouse and Elasticsearch over HTTP (a stub server)
@pytest.fixture
def http_stub():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply({"version": {"number": "8.15.0"}, "cluster_name": "demo"})

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"] or 0)).decode()
            seen.append((self.path.split("?")[0], body, {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}))
            if self.path.startswith("/_sql"):
                if "cursor" in json.loads(body):
                    return self.reply({"rows": [[3, "c"]]})
                return self.reply({"columns": [{"name": "id"}, {"name": "v"}], "rows": [[1, "a"], [2, "b"]], "cursor": "C1"})
            self.reply({"meta": [{"name": "x"}, {"name": "y"}], "data": [[1, "a"]]})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], seen
    server.shutdown()


def test_clickhouse_binds_typed_parameters_and_runs_readonly(http_stub):
    port, seen = http_stub
    rows = run("clickhouse", f"clickhouse://u:p@127.0.0.1:{port}/db1",
               "SELECT x, y FROM t WHERE y = $P{Y} AND x > $P{X} AND $X{IN, y, L} AND s LIKE '50%'", {"Y": "a", "X": 0, "L": ["a", "b"]})
    assert rows == [{"x": 1, "y": "a"}]
    _path, body, settings = seen[-1]
    assert "{p_Y:String}" in body and "{p_X:Int64}" in body and "LIKE '50%'" in body
    assert settings["readonly"] == "2" and settings["database"] == "db1" and settings["param_p_Y"] == "a"


def test_elasticsearch_sql_pages_and_parameters(http_stub):
    port, seen = http_stub
    url = f"elasticsearch://u:p@127.0.0.1:{port}"
    assert "Elasticsearch 8.15.0" in sql.test("elasticsearch", url)["message"]
    assert run("elasticsearch", url, "SELECT id, v FROM idx WHERE v = $P{V}", {"V": "a"}) == [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 3, "v": "c"}]
    first = json.loads([body for path, body, _ in seen if path == "/_sql" and "query" in body][-1])
    assert first["query"].endswith("WHERE v = ?") and first["params"] == ["a"]


# ---------------------------------------------------------------- the API: upload, save, test, query
def test_upload_save_and_query_a_files_source(client):
    headers = token(ADMIN)
    up = client.post("/api/data-sources/sheets-demo/files", headers=headers, json={"kind": "files", "name": "sales.csv", "file": b64(b"city,amount\nLahore,5\nKarachi,7\n")})
    assert up.status_code == 200, up.text
    assert [t["name"] for t in up.json()["tables"]] == ["sales"]
    bad = client.post("/api/data-sources/sheets-demo/files", headers=headers, json={"kind": "files", "name": "x.exe", "file": b64(b"MZ")})
    assert bad.status_code == 422
    saved = client.post("/api/data-sources", headers=headers, json={"type": "files", "id": "sheets-demo", "name": "Sheets demo", "uri": "files://local/sheets-demo"})
    assert saved.status_code == 200, saved.text
    assert client.post("/api/data-sources", headers=headers, json={"type": "files", "id": "other", "name": "Other", "uri": "files://local/sheets-demo"}).status_code == 422
    tested = client.post("/api/data-sources/sheets-demo/test", headers=headers)
    assert tested.status_code == 200 and "1 tables (sales)" in tested.json()["message"]
    assert client.get("/api/data-sources/sheets-demo/files", headers=headers).json()["files"][0]["name"] == "sales.csv"
    status = client.get("/api/admin/system/status", headers=headers).json()
    assert {d["type"] for d in status["drivers"]} >= {"files", "redis", "snowflake", "sqlite"}
    assert all("group" in c for c in status["checks"])
    gone = client.delete("/api/data-sources/sheets-demo", headers=headers)
    assert gone.status_code == 200, gone.text
    assert client.get("/api/data-sources/sheets-demo/files", headers=headers).json()["files"] == []


def test_google_sheet_link():
    assert sources.sheet_id_of("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=0") == "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    assert sources.sheet_id_of("https://example.com/x") == ""
    assert sql.valid_url("gsheets", "gsheets://docs.google.com/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    assert sql.valid_url("snowflake", "snowflake://u:p@xy12345/SALES/PUBLIC?warehouse=WH")
    assert sql.valid_url("redis", "rediss://:pw@cache:6380") and not sql.valid_url("redis", "http://cache")
