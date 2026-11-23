"""Admin → System database (choose, test, copy, save) and the Postman downloads; the "Did you mean …?" 404."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.storage import switch  # noqa: E402
from test_access_rights import ADMIN_HEADERS, make_user, publish, sign_in  # noqa: E402
from test_workspace_and_server import client  # noqa: E402,F401


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("# my settings\nREPORTING_DATA_DIR=./data\nREPORTING_STORAGE=files\n", encoding="utf-8")
    monkeypatch.setattr(switch, "env_path", lambda: path)
    return path


def test_build_url_for_each_database():
    assert switch.build_url({"kind": "postgresql", "host": "db", "port": "5432", "database": "reporting", "username": "rep",
                             "password": "p@ss:w/rd"}) == "postgresql://rep:p%40ss%3Aw%2Frd@db:5432/reporting"
    assert switch.build_url({"kind": "sqlserver", "host": "localhost", "database": "reporting", "windowsLogin": True}) == (
        "mssql+pyodbc://@localhost/reporting?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes&trusted_connection=yes")
    assert switch.build_url({"kind": "sqlserver", "host": "sql1", "port": "1433", "database": "r", "username": "u",
                             "password": "x"}) == "sqlserver://u:x@sql1:1433/r"
    assert switch.build_url({"kind": "mongodb", "host": "m", "database": "reporting_system"}) == "mongodb://m/reporting_system"
    assert switch.build_url({"kind": "oracle", "host": "o", "port": "1521", "service": "XEPDB1", "username": "u", "password": "p"}) == (
        "oracle://u:p@o:1521/?service_name=XEPDB1&schema=REPORTING")                 # the service's own schema
    assert switch.build_url({"kind": "oracle", "host": "o", "service": "ORCL", "database": "rpt_2", "username": "REPORTING"}) == (
        "oracle://REPORTING@o/?service_name=ORCL&schema=RPT_2")
    assert switch.build_url({"kind": "sqlite", "database": "x.db"}) == "sqlite:///x.db"
    assert switch.build_url({"url": "mysql://u:p@h/db"}) == "mysql://u:p@h/db"
    for bad in ({"kind": "excel"}, {"kind": "mysql", "host": "h"}, {"kind": "oracle", "host": "o", "database": "x; drop"}, {"kind": "mysql", "host": "h; drop", "database": "d"},
                {"url": "http://x"}):
        with pytest.raises(ValueError):
            switch.build_url(bad)


def test_only_a_super_admin_chooses_the_system_db(client, env_file):
    assert client.get("/api/admin/system-db", headers=ADMIN_HEADERS).json()["backend"] == "files"
    make_user(client, "adam", ["admin"])
    sign_in(client, "adam")
    view = client.get("/api/admin/system-db").json()
    assert view["youMayChange"] is False and view["kinds"]["mongodb"]["family"] == "non-relational"
    assert client.post("/api/admin/system-db/test", json={"kind": "sqlite", "database": "x.db"}).status_code == 403
    make_user(client, "dina", ["designer"])
    sign_in(client, "dina")
    assert client.get("/api/admin/system-db").status_code == 403


def test_test_copy_and_save_to_a_new_database(client, env_file, tmp_path):
    make_user(client, "boss", ["admin"], super_admin=True)
    publish(client, "SWITCH_ME", "Finance")
    sign_in(client, "boss")
    target = f"sqlite:///{(tmp_path / 'system.db').as_posix()}"
    checked = client.post("/api/admin/system-db/test", json={"url": target}).json()
    assert checked["ok"] and checked["empty"] and checked["label"] == "SQLite"
    bad = client.post("/api/admin/system-db/test", json={"kind": "postgresql", "host": "127.0.0.1", "port": "1",
                                                          "database": "nope", "username": "u", "password": "secret-pw"}).json()
    assert bad["ok"] is False and "secret-pw" not in bad["message"] and "secret-pw" not in bad["url"]

    done = client.post("/api/admin/system-db/switch", json={"url": target, "copy": True}).json()
    assert done["saved"] and done["copied"]["rows"] > 0 and done["target"]["label"] == "SQLite"
    text = env_file.read_text(encoding="utf-8")
    assert "REPORTING_STORAGE=sql\n" in text and f"REPORTING_STORAGE_URL={target}\n" in text
    assert "# my settings" in text and "REPORTING_DATA_DIR=./data" in text and text.count("REPORTING_STORAGE=") == 1
    assert list(env_file.parent.glob(".env.before-storage-*"))         # the old file is kept
    assert client.get("/api/admin/system-db").json()["pending"]["label"] == "SQLite"

    # what the new database holds: the users, the published report and the audit trail
    from backend.app.db import entities as ent
    from backend.app.db.sql import SqlRecords
    rows = ent.SqlEntities(target)
    assert {item["username"] for item in rows.find(ent.USERS_V2)} >= {"boss"}
    assert any(item["reportCode"] == "SWITCH_ME" for item in rows.find(ent.SERVER_REPORTS))      # same server_report_id as before
    assert SqlRecords(target).verify_chain()["ok"]
    again = client.post("/api/admin/system-db/test", json={"url": target}).json()
    assert again["ok"] and again["empty"] is False


def test_postman_downloads_for_admins(client):
    make_user(client, "boss", ["admin"], super_admin=True)
    sign_in(client, "boss")
    collection = client.get("/api/admin/postman/collection.json")
    assert collection.status_code == 200 and "attachment" in collection.headers["content-disposition"]
    assert collection.json()["item"]
    environment = client.get("/api/admin/postman/environment.json").json()
    values = {item["key"]: item["value"] for item in environment["values"]}
    assert values["baseUrl"] == "http://testserver" and values["username"] == "boss" and values["password"] == ""
    make_user(client, "dina", ["designer"])
    sign_in(client, "dina")
    assert client.get("/api/admin/postman/collection.json").status_code == 403


def test_unknown_report_id_suggests_the_right_one(client):
    publish(client, "LOAN_LETTER", "Letters")
    response = client.get("/generate/LETTER_LOAN.pdf", headers=ADMIN_HEADERS)
    assert response.status_code == 404 and "Did you mean LOAN_LETTER?" in response.json()["detail"]
    response = client.get("/generate/loan_leter.pdf", headers=ADMIN_HEADERS)
    assert "LOAN_LETTER" in response.json()["detail"]



def test_oracle_tables_go_into_the_reporting_schema():
    """schema= is taken out of the URL and set on every new connection (ALTER SESSION SET CURRENT_SCHEMA)."""
    from backend.app.db import sql
    assert sql.oracle_schema("oracle://u:p@o:1521/?service_name=X&schema=reporting") == ("oracle://u:p@o:1521/?service_name=X", "REPORTING")
    assert sql.oracle_schema("postgresql://u@h/db?schema=x") == ("postgresql://u@h/db?schema=x", "")      # only Oracle
    with pytest.raises(ValueError):
        sql.oracle_schema("oracle://u@o/?service_name=X&schema=a;b")
    engine = sql.engine_for("oracle://REPORTING:p@o.invalid:1521/?service_name=X&schema=REPORTING")   # no connection yet
    assert "schema" not in str(engine.url) and engine.url.query == {"service_name": "X"}
    ran = []

    class Cursor:
        def execute(self, text):
            ran.append(text)

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

    first = list(engine.pool.dispatch.connect)[0]                  # ours runs before SQLAlchemy's own steps
    first(Connection(), None)
    assert ran == ['ALTER SESSION SET CURRENT_SCHEMA = "REPORTING"']
    message = switch._clean_error(RuntimeError("ORA-01017: invalid username/password"), "oracle://u:secret@o/?schema=REPORTING")
    assert "CREATE USER REPORTING" in message and "secret" not in message
