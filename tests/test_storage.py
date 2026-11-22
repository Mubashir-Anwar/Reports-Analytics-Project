"""The document store (files or PostgreSQL), report versions and several app instances sharing one repository.

The PostgreSQL cases run when PG_TEST_URL points at a PostgreSQL server (CI starts one); each test gets its own schema."""
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import service_names as core  # noqa: E402
from backend.app.storage import store as store_module  # noqa: E402
from test_workspace_and_server import ADMIN, PUBLISHED, FakeMongoClient, client, login, token  # noqa: E402,F401

PG_URL = os.getenv("PG_TEST_URL", "")


@pytest.fixture
def pg_schema_url():
    if not PG_URL:
        pytest.skip("set PG_TEST_URL to test the PostgreSQL store")
    import psycopg
    schema = f"store_test_{secrets.token_hex(4)}"
    with psycopg.connect(PG_URL, autocommit=True) as connection:
        connection.execute(f"CREATE SCHEMA {schema}")
    separator = "&" if "?" in PG_URL else "?"
    yield f"{PG_URL}{separator}options={quote(f'-csearch_path={schema}')}"
    store_module._STORES.clear()
    with psycopg.connect(PG_URL, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture(params=["files", "postgresql"])
def store(request, tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    if request.param == "files":
        monkeypatch.setenv("REPORTING_STORAGE", "files")
    else:
        url = request.getfixturevalue("pg_schema_url")
        monkeypatch.setenv("REPORTING_STORAGE", "postgresql")
        monkeypatch.setenv("REPORTING_STORAGE_URL", url)
    return store_module.get_store()


def test_documents_folders_lines_and_files(store):
    assert store.read_json("reports/General/R1.json") is None and store.read_json("x.json", []) == []
    store.write_json("reports/General/R1.json", {"id": "R1", "n": 1})
    store.write_json("reports/Sales/East/R2.json", {"id": "R2"})
    store.write_json("users.json", {"users": [{"username": "a"}]}, private=True)
    assert store.read_json("reports/General/R1.json") == {"id": "R1", "n": 1}
    assert store.keys("reports") == ["reports/General/R1.json", "reports/Sales/East/R2.json"]
    assert [k for k, _ in store.read_many("reports")] == store.keys("reports")
    assert store.exists("users.json") and store.modified_at("users.json") is not None
    store.make_dir("reports/Empty/Inner")
    assert {"General", "Sales", "Sales/East", "Empty", "Empty/Inner"} <= set(store.dirs("reports"))
    assert store.remove_dir("reports/Sales") is False          # holds a report
    assert store.remove_dir("reports/Empty") is True           # only empty folders
    assert store.remove_dir("reports/Sales", recursive=True) is True
    assert store.keys("reports") == ["reports/General/R1.json"] and "Sales" not in store.dirs("reports")
    assert store.delete("reports/General/R1.json") and not store.delete("reports/General/R1.json")
    for i in range(3):
        store.append_line("audit/log.jsonl", f'{{"n": {i}}}')
    assert [line.strip() for line in store.lines("audit/log.jsonl")] == ['{"n": 0}', '{"n": 1}', '{"n": 2}']
    store.write_bytes("jobs/output/a.pdf", b"%PDF-1")
    assert store.read_bytes("jobs/output/a.pdf") == b"%PDF-1" and store.keys("jobs/output") == ["jobs/output/a.pdf"]
    with pytest.raises(ValueError):
        store.read_json("../outside.json")


def test_whole_service_on_the_postgresql_store(pg_schema_url, tmp_path, monkeypatch):
    """Publish, folders, users, history, versions and the audit log, all without the data directory."""
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path / "unused"))
    monkeypatch.setenv("REPORTING_STORAGE", "postgresql")
    monkeypatch.setenv("REPORTING_STORAGE_URL", pg_schema_url)
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", ADMIN)
    store = store_module.get_store()
    store.write_json("connections.json", {"connections": [{"id": "default", "name": "Main", "storage": "file", "database": "a"}]})
    store.write_json("private_secrets.json", {"admin_token": ADMIN, "mongo_connections": {"default": {"uri": "mongodb://main/a", "database": "a"}}}, private=True)
    monkeypatch.setattr(core.mongodb, "MongoClient", FakeMongoClient)
    monkeypatch.setattr(core.mongodb, "_execute_report_pipeline", lambda *_a, **_k: [{"Name": "a"}])
    app = TestClient(core.make_app())
    draft = {**PUBLISHED, "id": "PGR", "folder": "Sales"}
    assert app.put("/api/admin/workspace/PGR?create=true", headers=token(ADMIN), json=draft).status_code == 200
    assert app.post("/api/admin/workspace/PGR/publish", headers=token(ADMIN), json={}).status_code == 200
    assert store.read_json("reports/Sales/PGR.json")["id"] == "PGR"
    assert app.get("/generate/PGR.csv", headers={"X-Admin-Token": ADMIN}).status_code == 200
    assert login(app, "admin", ADMIN).status_code == 200
    versions = app.get("/api/server/reports/PGR/versions").json()["versions"]
    assert [v["version"] for v in versions] == [1] and versions[0]["action"] == "published"
    from backend.app.db import get_records
    assert get_records().runs(reportId="PGR")[0]["status"] == "success"      # the run log is in PostgreSQL too
    assert {e["action"] for e in get_records().events()} >= {"report.published", "auth.login"}
    assert not (tmp_path / "unused" / "reports" / "Sales").exists()


def test_versions_restore_and_history(client):
    assert client.put("/api/admin/workspace/V1?create=true", headers=token(), json={**PUBLISHED, "id": "V1", "title": "First"}).status_code == 200
    assert client.post("/api/admin/workspace/V1/publish", headers=token(), json={}).status_code == 200
    assert login(client, "admin", ADMIN).status_code == 200
    assert client.put("/api/server/reports/V1", json={"title": "Second"}).status_code == 200
    versions = client.get("/api/server/reports/V1/versions").json()["versions"]
    assert [(v["version"], v["title"], v["action"]) for v in versions] == [(2, "Second", "edited"), (1, "First", "published")]
    assert versions[0]["user"] == "admin"
    old = client.get("/api/server/reports/V1/versions/1").json()
    assert old["definition"]["title"] == "First"
    restored = client.post("/api/server/reports/V1/versions/1/restore", json={})
    assert restored.status_code == 200 and restored.json()["report"]["title"] == "First"
    assert [v["version"] for v in restored.json()["versions"]] == [3, 2, 1]
    assert client.get("/api/admin/workspace/V1", headers=token()).json()["title"] == "First"  # the designer copy follows
    history = client.get("/api/server/reports/V1").json()["history"]
    assert history[0]["action"] == "restored" and history[0]["detail"] == "version 1"
    assert client.get("/api/server/reports/V1/versions/99").status_code == 404
    assert client.post("/api/server/reports/NOPE/versions/1/restore", json={}).status_code == 404


def test_versions_need_a_designer(client):
    admin = {"X-Admin-Token": ADMIN}
    client.post("/api/admin/users", headers=admin, json={"username": "viewer9", "email": "viewer9@example.com", "password": "Passw0rd-long-enough", "role": "viewer"})
    assert login(client, "viewer9", "Passw0rd-long-enough").status_code == 200
    assert client.get("/api/server/reports/R1/versions").status_code == 403


def test_a_second_instance_sees_published_changes(client, monkeypatch):
    """Two app instances on one repository (same data directory or database): a publish on one appears on the other."""
    from backend.app.services import versions
    monkeypatch.setattr(versions, "REFRESH_EVERY_SECONDS", 0.0)
    other = TestClient(core.make_app())
    assert other.get("/api/admin/reports", headers=token()).status_code == 200
    assert "N2" not in [r["id"] for r in other.get("/api/admin/reports", headers=token()).json()["reports"]]
    client.put("/api/admin/workspace/N2?create=true", headers=token(), json={**PUBLISHED, "id": "N2"})
    assert client.post("/api/admin/workspace/N2/publish", headers=token(), json={}).status_code == 200
    time.sleep(0.01)
    assert "N2" in [r["id"] for r in other.get("/api/admin/reports", headers=token()).json()["reports"]]
    assert other.get("/generate/N2.csv", headers={"X-Admin-Token": ADMIN}).status_code == 200


def test_migrate_the_data_directory_into_postgresql(client, pg_schema_url):
    """client = a file data directory with a published report and users; copy it all into PostgreSQL."""
    import io

    from backend.app.storage.migrate import migrate
    assert login(client, "admin", ADMIN).status_code == 200
    client.get("/generate/R1.csv", headers={"X-Admin-Token": ADMIN})  # one run record
    from backend.app.db.sql import SqlRecords
    target, records = store_module.PostgresStore(pg_schema_url), SqlRecords(pg_schema_url)
    counts = migrate(target, out=io.StringIO(), records=records)
    assert counts["json"] >= 3 and counts["runs"] >= 1 and counts["events"] >= 1
    assert records.runs(reportId="R1")[0]["status"] == "success" and records.verify_chain()["ok"]
    assert target.read_json("reports/General/R1.json")["id"] == "R1"
    from backend.app.db import entities as ent
    names = {row["secretName"] for row in ent.SqlEntities(pg_schema_url).find(ent.SECRETS)}
    assert "admin_token" in names                                  # copied as rows of the secrets table
    assert "General" in target.dirs("reports")
    again = migrate(target, out=io.StringIO(), records=records)
    assert again["json"] == 0 and again["runs"] == 0 and again["events"] == 0 and again["skipped"] >= counts["json"]


def test_security_headers_ready_and_size_limit(client, monkeypatch):
    page = client.get("/designer")
    assert page.headers["x-content-type-options"] == "nosniff" and page.headers["x-frame-options"] == "SAMEORIGIN"
    assert "script-src 'self'" in page.headers["content-security-policy"] and "frame-src 'self' blob:" in page.headers["content-security-policy"]
    assert "content-security-policy" not in client.get("/health").headers  # JSON, not a page
    assert "strict-transport-security" not in page.headers
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["storage"] == "files" and ready.json()["scheduler"] in ("on", "off")
    monkeypatch.setenv("REPORTING_MAX_BODY_MB", "0.001")
    big = client.post("/api/admin/drafts/preview", headers={**token(), "Content-Type": "application/json"}, content=b"{" + b" " * 5000 + b"}")
    assert big.status_code == 413
    monkeypatch.delenv("REPORTING_MAX_BODY_MB")
    monkeypatch.setenv("REPORTING_HSTS", "true")
    assert "strict-transport-security" not in client.get("/designer").headers  # plain HTTP, no trusted proxy
    assert "strict-transport-security" not in client.get("/designer", headers={"X-Forwarded-Proto": "https"}).headers  # untrusted
    assert client.get("https://testserver/designer").headers.get("strict-transport-security", "").startswith("max-age=")


def test_sign_out_and_sign_in_limit_hold_on_every_instance(client):
    other = TestClient(core.make_app())
    assert login(client, "admin", ADMIN).status_code == 200
    cookie = client.cookies.get("reporting_session") or next(iter(client.cookies.values()))
    other.cookies.set(next(iter(client.cookies.keys())), cookie)
    assert other.get("/api/server/me").status_code == 200
    client.post("/api/server/logout", json={})
    assert other.get("/api/server/me").status_code == 401          # signed out everywhere
    for _ in range(10):
        login(client, "admin", "wrong")
    assert login(other, "admin", ADMIN).status_code == 429          # the limit is shared too
