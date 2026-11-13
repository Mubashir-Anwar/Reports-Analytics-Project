"""MySQL / MariaDB, SQL Server and Oracle adapters: placeholders, list filters, read-only sessions, Read Fields.

The drivers are replaced by small stand-ins that record what the connector sends; CI also runs the MySQL part
against a real MySQL when MYSQL_TEST_URL is set."""
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.connectors import sql as sql_connector  # noqa: E402
from backend.app.connectors.errors import ConnectorQueryError  # noqa: E402
from backend.app.queries.sql_query import prepare_sql  # noqa: E402
from test_workspace_and_server import ADMIN, client  # noqa: E402,F401

QUERY = "SELECT name, amount FROM orders WHERE placed >= $P{From} AND $X{IN, branch, Branches} AND note LIKE '50%'"


def test_placeholders_for_each_database():
    prepared = prepare_sql(QUERY)
    values = {"From": "2026-01-01", "Branches": ["KHI", "LHR"]}
    pg_sql, pg_params = prepared.bind(values)
    assert "branch = ANY(%(x" in pg_sql and "'50%%'" in pg_sql and pg_params["p_From"] == "2026-01-01"
    my_sql, my_params = prepared.bind(values, style="pyformat", lists="expand")
    assert "branch IN (%(x" in my_sql and "'50%%'" in my_sql and sorted(v for k, v in my_params.items() if k.startswith("x")) == ["KHI", "LHR"]
    ora_sql, ora_params = prepared.bind(values, style="named", lists="expand")
    assert ":p_From" in ora_sql and "branch IN (:x" in ora_sql and "'50%'" in ora_sql and len(ora_params) == 3
    empty_sql, _ = prepared.bind({"From": "2026-01-01"}, style="named", lists="expand")
    assert "(1 = 1)" in empty_sql  # an empty list does not filter
    with pytest.raises(ValueError, match="1000"):
        prepared.bind({"Branches": [str(i) for i in range(1001)]}, style="named", lists="expand")


@pytest.mark.parametrize("kind,url,ok", [
    ("mysql", "mysql://u:p@db:3306/shop", True), ("mysql", "mariadb://u:p@db/shop", True), ("mysql", "mysql://u:p@db/", False),
    ("sqlserver", "sqlserver://u:p@db:1433/Sales", True), ("sqlserver", "mssql://u:p@db/Sales", True),
    ("oracle", "oracle://u:p@db:1521/FREEPDB1", True), ("oracle", "postgresql://u:p@db/x", False),
])
def test_urls(kind, url, ok):
    assert sql_connector.valid_url(kind, url) is ok


def test_build_url_quotes_passwords():
    url = sql_connector.build_url("sqlserver", "db.local", None, "Sales", "rep", "p@ss:w/rd")
    assert url == "sqlserver://rep:p%40ss%3Aw%2Frd@db.local:1433/Sales"
    assert sql_connector._url_parts("sqlserver", url)["password"] == "p@ss:w/rd"


# ---------------------------------------------------------------- stand-in drivers
class Recorder:
    def __init__(self, rows=(("Ali", 10),), description=(("name",), ("amount",)), fail_on=None):
        self.statements, self.rows, self.description, self.fail_on = [], list(rows), description, fail_on
        self.rolled_back = self.closed = False
        self.kwargs = {}


def fake_module(rec: Recorder, kind: str):
    class Cursor:
        description = None

        def execute(self, sql, params=None):
            rec.statements.append((sql, params))
            if rec.fail_on and rec.fail_on in sql:
                raise RuntimeError(f"boom for {rec.kwargs.get('password', '')}")
            self.description = rec.description if sql.lstrip().upper().startswith(("SELECT", "WITH")) else None

        def fetchmany(self, n):
            return rec.rows[:n]

        def fetchone(self):
            return ("Server 1.0", "shop")

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Connection:
        version = "23.4"
        call_timeout = 0

        def cursor(self):
            return Cursor()

        def rollback(self):
            rec.rolled_back = True

        def close(self):
            rec.closed = True

    def connect(*_a, **kwargs):
        rec.kwargs = kwargs
        return Connection()

    module = types.SimpleNamespace(connect=connect, MySQLError=RuntimeError, defaults=types.SimpleNamespace(fetch_lobs=True))
    return module


@pytest.mark.parametrize("kind,url,session_sql", [
    ("mysql", "mysql://rep:secret@db:3306/shop", ["SET SESSION max_execution_time = 30000", "START TRANSACTION READ ONLY"]),
    ("sqlserver", "sqlserver://rep:secret@db:1433/shop", ["SET LOCK_TIMEOUT 30000"]),
    ("oracle", "oracle://rep:secret@db:1521/FREEPDB1", ["SET TRANSACTION READ ONLY"]),
])
def test_sessions_are_read_only_rolled_back_and_bound(monkeypatch, kind, url, session_sql):
    rec = Recorder()
    monkeypatch.setattr(sql_connector, "_driver", lambda k: fake_module(rec, k))
    rows = sql_connector.run_query(kind, url, prepare_sql(QUERY), {"From": "2026-01-01", "Branches": "KHI,LHR"}, max_rows=10, timeout_ms=30_000)
    assert rows == [{"name": "Ali", "amount": 10}]
    assert [s for s, _ in rec.statements[:len(session_sql)]] == session_sql
    query, params = rec.statements[-1]
    assert "KHI" in params.values() and "LHR" in params.values() and "KHI" not in query  # values are bound, not pasted
    assert rec.rolled_back and rec.closed
    assert rec.kwargs.get("password") == "secret"


def test_read_fields_wrappers_and_sql_server_fallback(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(sql_connector, "_driver", lambda k: fake_module(rec, k))
    assert sql_connector.describe("oracle", "oracle://u:p@db/X", prepare_sql("SELECT a FROM t")) == ["name", "amount"]
    assert rec.statements[-1][0] == "SELECT * FROM (SELECT a FROM t) report_query WHERE 1 = 0"
    rec2 = Recorder(fail_on="TOP 0")
    monkeypatch.setattr(sql_connector, "_driver", lambda k: fake_module(rec2, k))
    names = sql_connector.describe("sqlserver", "sqlserver://u:p@db/X", prepare_sql("WITH c AS (SELECT 1 AS a) SELECT a FROM c ORDER BY a"))
    assert names == ["name", "amount"] and rec2.statements[-1][0].startswith("WITH c AS")


def test_errors_hide_the_password(monkeypatch):
    rec = Recorder(fail_on="FROM orders")
    monkeypatch.setattr(sql_connector, "_driver", lambda k: fake_module(rec, k))
    with pytest.raises(ConnectorQueryError) as caught:
        sql_connector.run_query("mysql", "mysql://rep:topsecret@db/shop", prepare_sql("SELECT * FROM orders"), {}, max_rows=5, timeout_ms=5000)
    assert "topsecret" not in str(caught.value) and "***" in str(caught.value)


def test_new_adapter_types_through_the_api(client, monkeypatch):
    monkeypatch.setattr(sql_connector, "test", lambda kind, url: {"ok": True, "message": f"Connected {kind}", "database": "Sales"})
    admin = {"X-Admin-Token": ADMIN}
    for kind, url in (("mysql", "mysql://r:pw@db/shop"), ("sqlserver", "sqlserver://r:pw@db/Sales"), ("oracle", "oracle://r:pw@db/FREEPDB1")):
        assert client.post("/api/admin/connections/test", headers=admin, json={"type": kind, "uri": url}).json()["message"] == f"Connected {kind}"
        saved = client.post("/api/admin/connections", headers=admin, json={"type": kind, "name": f"My {kind}", "uri": url})
        assert saved.status_code == 200 and saved.json()["connection"]["type"] == kind
    bad = client.post("/api/admin/connections/test", headers=admin, json={"type": "oracle", "uri": "mysql://x@y/z"})
    assert bad.status_code == 422 and "oracle://" in bad.json()["detail"]


# ---------------------------------------------------------------- a real MySQL (MYSQL_TEST_URL, set in CI)
MYSQL_URL = os.getenv("MYSQL_TEST_URL", "")


@pytest.mark.skipif(not MYSQL_URL, reason="set MYSQL_TEST_URL to run against a real MySQL")
def test_real_mysql_reads_and_refuses_writes():
    pymysql = pytest.importorskip("pymysql")
    parts = sql_connector._url_parts("mysql", MYSQL_URL)
    setup = pymysql.connect(host=parts["host"], port=parts["port"], user=parts["user"], password=parts["password"], database=parts["database"], autocommit=True)
    with setup.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS report_orders")
        cursor.execute("CREATE TABLE report_orders (id INT PRIMARY KEY, branch VARCHAR(10), amount DECIMAL(10,2))")
        cursor.execute("INSERT INTO report_orders VALUES (1, 'KHI', 10.50), (2, 'LHR', 20), (3, 'ISB', 5)")
    setup.close()
    query = prepare_sql("SELECT id, branch, amount FROM report_orders WHERE amount >= $P{Min} AND $X{IN, branch, B} ORDER BY id")
    rows = sql_connector.run_query("mysql", MYSQL_URL, query, {"Min": 6, "B": ["KHI", "LHR"]}, max_rows=10, timeout_ms=5000)
    assert rows == [{"id": 1, "branch": "KHI", "amount": 10.5}, {"id": 2, "branch": "LHR", "amount": 20}]
    assert sql_connector.describe("mysql", MYSQL_URL, query) == ["id", "branch", "amount"]
    assert "MySQL" in sql_connector.test("mysql", MYSQL_URL)["message"]
    # past the query check, the READ ONLY transaction still refuses a write
    sneaky = prepare_sql("SELECT 1")
    object.__setattr__(sneaky, "parts", ("UPDATE report_orders SET amount = 0",))
    with pytest.raises(ConnectorQueryError):
        sql_connector.run_query("mysql", MYSQL_URL, sneaky, {}, max_rows=10, timeout_ms=5000)
    check = sql_connector.run_query("mysql", MYSQL_URL, prepare_sql("SELECT SUM(amount) AS total FROM report_orders"), {}, max_rows=1, timeout_ms=5000)
    assert check == [{"total": 35.5}]
