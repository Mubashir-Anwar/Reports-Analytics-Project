"""Admin, Server and Designer share one list of data adapters of every kind."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.connectors import sql as sql_connector  # noqa: E402
from test_workspace_and_server import ADMIN, PUBLISHED, client, login, setup_login, token  # noqa: E402,F401

ADMIN_HEADERS = {"X-Admin-Token": ADMIN}


def add_pg(client):
    saved = client.post("/api/admin/connections", headers=ADMIN_HEADERS,
                        json={"type": "postgresql", "name": "Finance PG", "uri": "postgresql://r:pw@db.local/fin", "folder": ""})
    assert saved.status_code == 200
    return saved.json()["connection"]["id"]


def test_every_kind_is_listed_with_its_usage(client, monkeypatch):
    pg = add_pg(client)
    dataset = {"name": "Lines", "connectionId": pg, "queryLanguage": "sql", "querySource": "SELECT a FROM t", "fields": ["a"]}
    report = {**PUBLISHED, "id": "MIXED", "design": {"datasets": [dataset], "bands": {}}}
    client.put("/api/admin/workspace/MIXED?create=true", headers=token(), json=report)
    assert client.post("/api/admin/workspace/MIXED/publish", headers=token(), json={}).status_code == 200
    listed = {c["id"]: c for c in client.get("/api/admin/connections", headers=ADMIN_HEADERS).json()["connections"]}
    assert listed[pg]["type"] == "postgresql" and listed[pg]["usedBy"] == ["MIXED"]
    assert "MIXED" in listed["default"]["usedBy"] and "R1" in listed["default"]["usedBy"]
    blocked = client.delete(f"/api/admin/connections/{pg}", headers=ADMIN_HEADERS)
    assert blocked.status_code == 409 and "MIXED" in blocked.json()["detail"]   # used by a dataset only


def test_server_shows_the_kind_tests_and_refuses_a_wrong_kind(client, monkeypatch):
    pg = add_pg(client)
    monkeypatch.setattr(sql_connector, "test", lambda kind, url: {"ok": True, "message": f"Connected {kind}", "database": "fin"})
    assert setup_login(client).status_code == 200
    adapters = {c["id"]: c for c in client.get("/api/server/connections").json()["connections"]}
    assert adapters[pg]["type"] == "postgresql" and adapters["default"]["type"] == "mongodb" and adapters[pg]["configured"]
    assert client.get("/api/server/reports/R1").json()["connection"]["type"] == "mongodb"
    assert client.post(f"/api/server/connections/{pg}/test", json={}).json()["message"] == "Connected postgresql"
    wrong = client.put("/api/server/reports/R1", json={"connectionId": pg})   # a MongoDB query on PostgreSQL
    assert wrong.status_code == 422 and "MongoDB" in wrong.json()["detail"]
    client.post("/api/admin/users", headers=ADMIN_HEADERS, json={"username": "v1", "email": "v1@example.com", "password": "Passw0rd-long-enough", "role": "viewer"})
    client.post("/api/server/logout", json={})
    assert login(client, "v1", "Passw0rd-long-enough").status_code == 200
    assert client.post(f"/api/server/connections/{pg}/test", json={}).status_code == 403


def test_production_checklist_is_admin_only(client):
    assert client.get("/api/admin/system/status", headers=token()).status_code == 403   # designer token
    status = client.get("/api/admin/system/status", headers=ADMIN_HEADERS).json()
    ids = {c["id"]: c for c in status["checks"]}
    assert {"storage", "secrets", "session", "admin-user", "https", "docs", "scheduler", "email"} <= set(ids)
    assert ids["secrets"]["ok"] is False and "REPORTING_SECRETS_KEY" in ids["secrets"]["hint"]
    assert status["storage"] == "files"
