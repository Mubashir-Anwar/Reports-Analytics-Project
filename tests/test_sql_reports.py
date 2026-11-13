"""SQL data adapters (PostgreSQL): query checks, report definitions, the API flow, and — when PG_TEST_URL points at a
PostgreSQL server — real queries, including the read-only guarantee.

Local: start any PostgreSQL and set PG_TEST_URL=postgresql://user@127.0.0.1:5432/postgres. CI starts one."""
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.connectors import sql as sql_connector  # noqa: E402
from backend.app.connectors.errors import ConnectorQueryError  # noqa: E402
from backend.app.definitions import _dynamic_report  # noqa: E402
from backend.app.queries.sql_query import prepare_sql  # noqa: E402
from test_workspace_and_server import client, token  # noqa: E402,F401

PG_URL = os.getenv("PG_TEST_URL", "")
needs_pg = pytest.mark.skipif(not PG_URL, reason="set PG_TEST_URL to run against a real PostgreSQL")


# ---------------------------------------------------------------- query checks (no database)
def test_parameters_become_bound_values():
    prepared = prepare_sql("SELECT name FROM t WHERE branch = $P{Branch} AND d >= $P{Start} AND note = '$P{NotAParam}' -- $P{Comment}")
    assert prepared.parameter_names == ("Branch", "Start")
    sql, params = prepared.bind({"Branch": "KHI", "Start": date(2026, 1, 1)})
    assert "%(p_Branch)s" in sql and "%(p_Start)s" in sql and "'$P{NotAParam}'" in sql
    assert params == {"p_Branch": "KHI", "p_Start": date(2026, 1, 1)}


def test_x_in_lists_and_empty_list_means_no_filter():
    prepared = prepare_sql("SELECT * FROM t WHERE $X{IN, branch, Branches} AND $X{NOTIN, t.status, Skip}")
    sql, params = prepared.bind({"Branches": "KHI, LHR", "Skip": ""})
    assert "(branch = ANY(%(x1_Branches)s))" in sql and "(1 = 1)" in sql
    assert params == {"x1_Branches": ["KHI", "LHR"]}


def test_percent_signs_and_trailing_semicolon():
    sql, _ = prepare_sql("SELECT name FROM t WHERE name LIKE 'A%';").bind({})
    assert sql.endswith("LIKE 'A%%'") and ";" not in sql


@pytest.mark.parametrize("bad, message", [
    ("DELETE FROM t", "start with SELECT"),
    ("SELECT 1; DROP TABLE t", "exactly one"),
    ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "DELETE"),
    ("SELECT * INTO copy_t FROM t", "INTO"),
    ("SELECT * FROM t FOR UPDATE", "UPDATE"),
    ("SELECT * FROM t FOR SHARE", "FOR SHARE"),
    ("SELECT * FROM t WHERE a = $P!{Raw}", "$P!"),
    ("SELECT 'unclosed", "quote"),
    ("", "Write the SQL"),
])
def test_unsafe_or_broken_queries_are_refused(bad, message):
    with pytest.raises(ValueError, match=message.replace("$", r"\$")):
        prepare_sql(bad)


def test_words_inside_strings_and_identifiers_are_fine():
    prepare_sql('SELECT "update" AS u, replace(name, \'delete\', \'\') AS comment FROM t')


def test_sql_report_definition():
    base = {"id": "SQL1", "connectionId": "pg", "queryLanguage": "sql", "columns": ["name"],
            "querySource": "SELECT name FROM t WHERE branch = $P{Branch}",
            "parameters": [{"name": "Branch", "type": "string"}]}
    definition = _dynamic_report(base)
    assert definition.query_language == "sql" and definition.collection == "" and definition.sql.parameter_names == ("Branch",)
    with pytest.raises(ValueError, match="Define the parameter"):
        _dynamic_report({**base, "parameters": []})
    with pytest.raises(ValueError, match="Read the fields"):
        _dynamic_report({**base, "columns": []})


def test_passwords_never_appear_in_errors():
    text = sql_connector.safe_error(Exception('connection to "postgresql://bob:s3cr3t@db:5432/x" failed: s3cr3t'), "postgresql://bob:s3cr3t@db:5432/x")
    assert "s3cr3t" not in text


# ---------------------------------------------------------------- API flow with a stand-in database
def test_postgres_adapter_and_report_through_the_api(client, monkeypatch):
    calls = []
    monkeypatch.setattr(sql_connector, "test", lambda kind, url: {"ok": True, "message": "Connected: PostgreSQL 16", "database": "shop"})
    monkeypatch.setattr(sql_connector, "describe", lambda kind, url, prepared, values=None: ["name", "amount"])

    def run_query(kind, url, prepared, values, *, max_rows, timeout_ms):
        calls.append((kind, url, values))
        return [{"name": "Ali", "amount": 10}, {"name": "Sara", "amount": 20}]
    monkeypatch.setattr(sql_connector, "run_query", run_query)

    admin = token("admin-token-for-tests-0123456789")
    url = "postgresql://report:pw-123@db.local:5432/shop"
    assert client.post("/api/admin/connections/test", headers=admin, json={"type": "postgresql", "uri": url}).json()["ok"]
    assert client.post("/api/admin/connections/test", headers=admin, json={"type": "postgresql", "uri": "mysql://x"}).status_code == 422
    saved = client.post("/api/admin/connections", headers=admin, json={"type": "postgresql", "name": "Shop PG", "uri": url})
    assert saved.status_code == 200 and saved.json()["connection"]["type"] == "postgresql"
    listed = {c["id"]: c for c in client.get("/api/admin/connections", headers=admin).json()["connections"]}
    assert listed["shop_pg"]["type"] == "postgresql" and listed["shop_pg"]["database"] == "shop"
    assert "pw-123" not in str(listed)

    fields = client.post("/api/admin/query/fields", headers=token(), json={"connectionId": "shop_pg", "querySource": "SELECT name, amount FROM orders WHERE branch = $P{Branch}"})
    assert fields.json() == {"columns": ["name", "amount"], "parameterNames": ["Branch"]}
    assert client.post("/api/admin/query/fields", headers=token(), json={"connectionId": "default", "querySource": "SELECT 1"}).status_code == 422

    report = {"id": "PG_REPORT", "title": "PG", "folder": "General", "connectionId": "shop_pg", "queryLanguage": "sql",
              "querySource": "SELECT name, amount FROM orders WHERE branch = $P{Branch}", "columns": ["name", "amount"],
              "parameters": [{"name": "Branch", "type": "string", "required": True}], "formats": ["csv", "pdf"]}
    preview = client.post("/api/admin/drafts/preview", headers=token(), json={"definition": report, "parameters": {"Branch": "KHI"}})
    assert preview.status_code == 200 and preview.json()["rows"][0] == {"name": "Ali", "amount": 10}
    client.put("/api/admin/workspace/PG_REPORT?create=true", headers=token(), json=report)
    assert client.post("/api/admin/workspace/PG_REPORT/publish", headers=token(), json={}).status_code == 200
    csv = client.get("/generate/PG_REPORT.csv?Branch=LHR", headers=admin)
    assert csv.status_code == 200 and "Sara" in csv.text
    assert calls[-1] == ("postgresql", url, {"Branch": "LHR"})

    # a SQL report on a MongoDB adapter (and the other way round) is a clear error, not a crash
    wrong = client.post("/api/admin/drafts/preview", headers=token(), json={"definition": {**report, "connectionId": "default"}, "parameters": {"Branch": "KHI"}})
    assert wrong.status_code == 422 and "SQL" in wrong.json()["detail"]

    def failing(*_args, **_kwargs):
        raise ConnectorQueryError('The SQL query failed: relation "orders" does not exist')
    monkeypatch.setattr(sql_connector, "run_query", failing)
    failed = client.get("/generate/PG_REPORT.csv?Branch=LHR", headers=admin)
    assert failed.status_code == 503 and "orders" not in failed.text   # public endpoint: no database details
    designer_view = client.post("/api/admin/drafts/preview", headers=token(), json={"definition": report, "parameters": {"Branch": "KHI"}})
    assert designer_view.status_code == 503 and "does not exist" in designer_view.json()["detail"]


# ---------------------------------------------------------------- a real PostgreSQL (PG_TEST_URL)
@pytest.fixture
def pg_table():
    import psycopg
    with psycopg.connect(PG_URL, autocommit=True) as connection:
        connection.execute("DROP TABLE IF EXISTS report_test_orders; DROP SEQUENCE IF EXISTS report_test_seq")
        connection.execute("CREATE TABLE report_test_orders (id serial, name text, branch text, amount numeric(12,2), placed date)")
        connection.execute("CREATE SEQUENCE report_test_seq")
        connection.execute("INSERT INTO report_test_orders (name, branch, amount, placed) VALUES "
                           "('Ali','KHI',10.50,'2026-01-05'),('Sara','LHR',20,'2026-02-01'),('Omar','KHI',5,'2026-03-01')")
    yield
    with psycopg.connect(PG_URL, autocommit=True) as connection:
        connection.execute("DROP TABLE IF EXISTS report_test_orders; DROP SEQUENCE IF EXISTS report_test_seq")


@needs_pg
def test_real_postgres_query_parameters_and_types(pg_table):
    assert sql_connector.test("postgresql", PG_URL)["message"].startswith("Connected: PostgreSQL")
    prepared = prepare_sql("SELECT name, amount, placed FROM report_test_orders WHERE placed >= $P{From} AND $X{IN, branch, Branches} ORDER BY id")
    assert sql_connector.describe("postgresql", PG_URL, prepared) == ["name", "amount", "placed"]
    rows = sql_connector.run_query("postgresql", PG_URL, prepared, {"From": date(2026, 1, 1), "Branches": "KHI"}, max_rows=10, timeout_ms=5000)
    assert rows == [{"name": "Ali", "amount": 10.5, "placed": date(2026, 1, 5)}, {"name": "Omar", "amount": 5, "placed": date(2026, 3, 1)}]
    everything = sql_connector.run_query("postgresql", PG_URL, prepared, {"From": date(2020, 1, 1), "Branches": ""}, max_rows=1, timeout_ms=5000)
    assert len(everything) == 2  # max_rows + 1, so the caller can see that there are more


@needs_pg
def test_real_postgres_is_read_only_even_past_the_checks(pg_table):
    # nextval() writes; it passes the keyword check but the read-only transaction stops it
    prepared = prepare_sql("SELECT nextval('report_test_seq') AS n")
    with pytest.raises(ConnectorQueryError, match="read-only"):
        sql_connector.run_query("postgresql", PG_URL, prepared, {}, max_rows=5, timeout_ms=5000)


@needs_pg
def test_real_postgres_time_limit_and_safe_errors(pg_table):
    with pytest.raises(ConnectorQueryError, match="statement timeout|canceling statement"):
        sql_connector.run_query("postgresql", PG_URL, prepare_sql("SELECT pg_sleep(3)"), {}, max_rows=1, timeout_ms=1000)
    with pytest.raises(ConnectorQueryError, match="does not exist"):
        sql_connector.run_query("postgresql", PG_URL, prepare_sql("SELECT * FROM no_such_table"), {}, max_rows=1, timeout_ms=5000)
