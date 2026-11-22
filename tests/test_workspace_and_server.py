"""Designer workspace (internal drafts + publish), /server login sessions and /admin user accounts."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import service_names as core  # noqa: E402

ADMIN = "admin-token-for-tests-0123456789"
DESIGNER = "designer-token-for-tests-0123456789"
PUBLISHED = {"id": "R1", "title": "Published one", "collection": "orders", "connectionId": "default", "parameters": [],
             "queryLanguage": "aggregation-json", "querySource": '[{"$project": {"Name": 1}}]', "columns": ["Name"],
             "formats": ["csv", "pdf"], "folder": "General"}


class _FakeAdmin:
    def command(self, *_a, **_k):
        return {"ok": 1}


class FakeMongoClient:
    def __init__(self, uri, **_kwargs):
        self.admin = _FakeAdmin()

    def __getitem__(self, _name):
        return {"orders": object()}

    def close(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("REPORTING_DESIGNER_TOKEN", DESIGNER)
    for key in ("REPORTING_ENABLED_AREAS", "REPORTING_ADMIN_ALLOWED_IPS", "REPORTING_SESSION_SECRET"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "connections.json").write_text(json.dumps({"connections": [
        {"id": "default", "name": "Main", "storage": "file", "database": "a"}]}), encoding="utf-8")
    (tmp_path / "private_secrets.json").write_text(json.dumps({"admin_token": ADMIN, "mongo_connections": {
        "default": {"uri": "mongodb://main/a", "database": "a"}}}), encoding="utf-8")
    (tmp_path / "reports" / "General").mkdir(parents=True)
    (tmp_path / "reports" / "General" / "R1.json").write_text(json.dumps(PUBLISHED), encoding="utf-8")
    monkeypatch.setattr(core.mongodb, "MongoClient", FakeMongoClient)
    monkeypatch.setattr(core.mongodb, "_execute_report_pipeline", lambda *_a, **_k: [{"Name": "a"}, {"Name": "b"}])
    return TestClient(core.make_app())


def token(value=DESIGNER):
    return {"X-Admin-Token": value}


def draft(report_id="D1", **extra):
    return {**PUBLISHED, "id": report_id, "title": "Draft", "folder": "Mine", **extra}


# ---------------------------------------------------------------- workspace
def test_workspace_starts_with_a_copy_of_the_server(client):
    listing = client.get("/api/admin/workspace", headers=token()).json()
    copied = {item["id"]: item for item in listing["reports"]}
    assert copied["R1"]["onServer"] and not copied["R1"]["changedSincePublish"]


def test_saving_in_the_designer_does_not_reach_the_server(client):
    saved = client.put("/api/admin/workspace/D1?create=true", headers=token(), json=draft())
    assert saved.status_code == 200 and saved.json()["readyToPublish"]
    assert saved.json()["report"]["onServer"] is False
    assert "D1" not in [item["id"] for item in client.get("/reports", headers=token(ADMIN)).json()["reports"]]
    assert client.get("/generate/D1.csv", headers=token(ADMIN)).status_code == 404
    # a second "new" report with the same ID is refused; a normal save overwrites
    assert client.put("/api/admin/workspace/D1?create=true", headers=token(), json=draft()).status_code == 409
    assert client.put("/api/admin/workspace/D1", headers=token(), json=draft(title="v2")).status_code == 200
    assert client.get("/api/admin/workspace/D1", headers=token()).json()["title"] == "v2"


def test_incomplete_draft_can_be_saved_but_says_why_it_cannot_be_published(client):
    response = client.put("/api/admin/workspace/D2?create=true", headers=token(),
                          json={"id": "D2", "title": "Half done", "folder": "General", "collection": "Orders"})
    assert response.status_code == 200
    assert response.json()["readyToPublish"] is False and response.json()["problem"]


def test_publish_copies_the_draft_to_the_server(client):
    client.put("/api/admin/workspace/D1?create=true", headers=token(), json=draft())
    published = client.post("/api/admin/workspace/D1/publish", headers=token(), json={"folder": "Finance/Monthly"})
    assert published.status_code == 200, published.text
    assert published.json()["workspace"]["onServer"] and published.json()["workspace"]["publishedFolder"] == "Finance/Monthly"
    assert client.get("/generate/D1.csv", headers=token(ADMIN)).status_code == 200
    assert "Finance/Monthly" in client.get("/api/admin/folders", headers=token()).json()["folders"]
    # editing again marks the draft as changed; publishing again needs replace=true
    client.put("/api/admin/workspace/D1", headers=token(), json=draft(title="Changed"))
    assert {i["id"]: i for i in client.get("/api/admin/workspace", headers=token()).json()["reports"]}["D1"]["changedSincePublish"]
    assert client.post("/api/admin/workspace/D1/publish", headers=token(), json={}).status_code == 409
    again = client.post("/api/admin/workspace/D1/publish", headers=token(), json={"replace": True})
    assert again.status_code == 200 and again.json()["replaced"]
    titles = {item["id"]: item["title"] for item in client.get("/reports", headers=token(ADMIN)).json()["reports"]}
    assert titles["D1"] == "Changed"
    assert "_workspace" not in json.dumps(client.get("/api/admin/reports/D1", headers=token()).json())


def test_a_save_from_an_older_copy_is_stopped_and_says_who_saved(client):
    first = client.put("/api/admin/workspace/D1?create=true", headers=token(), json=draft())
    base = first.json()["report"]["versionNo"]
    assert base == 1 and client.get("/api/admin/workspace/D1", headers=token()).json()["workspace"]["versionNo"] == 1
    other = client.put("/api/admin/workspace/D1", headers=token(ADMIN), json=draft(title="Theirs", baseVersion=base))
    assert other.status_code == 200 and other.json()["report"]["versionNo"] == 2
    mine = client.put("/api/admin/workspace/D1", headers=token(), json=draft(title="Mine", baseVersion=base))
    assert mine.status_code == 409
    detail = mine.json()["detail"]
    assert detail["code"] == "changed-by-someone-else" and detail["savedBy"] == "admin token" and detail["versionNo"] == 2
    assert client.get("/api/admin/workspace/D1", headers=token()).json()["title"] == "Theirs"            # nothing lost
    assert client.put("/api/admin/workspace/D1", headers=token(), json=draft(title="Mine", baseVersion=2)).status_code == 200
    # publishing does not count as a change of the design: the open copy can still be saved
    client.post("/api/admin/workspace/D1/publish", headers=token(), json={"folder": "Finance"})
    assert client.put("/api/admin/workspace/D1", headers=token(), json=draft(title="Again", baseVersion=3)).status_code == 200


def test_the_designer_says_who_else_is_editing(client):
    client.put("/api/admin/workspace/D1?create=true", headers=token(), json=draft())
    url = "/api/admin/workspace/D1/editing?folder=Mine"
    assert client.post(url, headers=token(), json={"open": True}).json()["editingBy"] is None
    theirs = client.post(url, headers=token(ADMIN), json={"open": True}).json()
    assert theirs["editingBy"]["user"] == "designer token" and theirs["versionNo"] == 1
    assert client.get("/api/admin/workspace/D1?folder=Mine", headers=token(ADMIN)).json()["workspace"]["editingBy"]["user"] == "designer token"
    client.post(url, headers=token(), json={"open": False})                      # closed: the mark goes
    assert client.post(url, headers=token(ADMIN), json={"open": True}).json()["editingBy"] is None
    assert client.post(url, headers=token(), json={"open": True}).json()["editingBy"]["user"] == "admin token"


def test_moving_a_draft_or_a_server_report_keeps_its_id(client):
    from backend.app.storage.store import get_store
    client.put("/api/admin/workspace/D1?create=true", headers=token(), json=draft())
    store = get_store()
    draft_id = store.report_id_of("workspace/Mine/D1.json")
    assert client.put("/api/admin/workspace/D1?folder=Mine", headers=token(), json=draft(folder="Other")).status_code == 200
    assert store.report_id_of("workspace/Other/D1.json") == draft_id and not store.exists("workspace/Mine/D1.json")
    server_id = store.report_id_of("reports/General/R1.json")
    history_before = len(client.get("/api/admin/reports/R1", headers=token()).json().get("history", []))
    client.post("/api/admin/workspace/R1/publish", headers=token(), json={"folder": "General", "replace": True})
    assert store.report_id_of("reports/General/R1.json") == server_id                                    # a republish keeps it
    row = store.report_row("reports/General/R1.json")
    assert row["currentVersion"] >= 1 and row["publishedBy"]
    assert history_before >= 0


def test_deleting_a_draft_keeps_the_server_copy(client):
    response = client.delete("/api/admin/workspace/R1", headers=token())
    assert response.json() == {"status": "deleted", "reportId": "R1", "stillOnServer": True}
    assert client.get("/generate/R1.csv", headers=token(ADMIN)).status_code == 200


def test_draft_preview_runs_without_publishing(client):
    body = {"definition": draft(), "parameters": {}, "connectionId": "default"}
    preview = client.post("/api/admin/drafts/preview", headers=token(), json=body)
    assert preview.status_code == 200 and preview.json()["rowCount"] == 2
    run = client.post("/api/admin/drafts/run", headers=token(), json={**body, "format": "csv"})
    assert run.status_code == 200 and b"Name" in run.content
    assert client.post("/api/admin/drafts/preview", headers=token(), json={"definition": {"id": "X"}}).status_code == 422


def test_workspace_folders(client):
    assert "Drafts/New" in client.post("/api/admin/workspace-folders", headers=token(), json={"path": "Drafts/New"}).json()["folders"]
    assert client.request("DELETE", "/api/admin/workspace-folders", headers=token(), json={"path": "Drafts/New"}).status_code == 200


# ---------------------------------------------------------------- /server login
def login(client, username, password):
    return client.post("/api/server/login", json={"username": username, "password": password})


def test_server_needs_login_and_logout_ends_the_session(client):
    assert client.get("/server").status_code == 200
    assert client.get("/api/server/repository").status_code == 401
    assert login(client, "admin", "wrong").status_code == 401
    signed_in = login(client, "admin", ADMIN)  # bootstrap: no admin user yet
    assert signed_in.status_code == 200 and signed_in.json()["bootstrap"]
    assert "httponly" in signed_in.headers["set-cookie"].lower()
    repository = client.get("/api/server/repository").json()
    assert "R1" in [item["id"] for item in repository["reports"]]
    run = client.post("/api/server/reports/R1/run", json={"format": "csv", "parameters": {}})
    assert run.status_code == 200 and b"Name" in run.content
    assert client.post("/api/server/reports/R1/run", json={"format": "xlsx"}).status_code == 422  # not enabled
    cookie = client.cookies.get(core.SESSION_COOKIE)
    assert client.post("/api/server/logout").status_code == 200
    assert client.get("/api/server/me").status_code == 401
    client.cookies.set(core.SESSION_COOKIE, cookie)          # a copied cookie is dead after logout
    assert client.get("/api/server/me").status_code == 401


def test_server_writes_must_be_json(client):
    login(client, "admin", ADMIN)
    response = client.post("/api/server/reports/R1/run", data={"format": "csv"})
    assert response.status_code == 415


def test_users_roles_and_session_revocation(client):
    admin = token(ADMIN)
    assert client.post("/api/admin/users", headers=token(DESIGNER), json={"username": "x", "email": "x@example.com", "password": "12345678"}).status_code == 403
    assert client.post("/api/admin/users", headers=admin, json={"username": "boss", "email": "boss@example.com", "password": "short", "role": "admin"}).status_code == 422
    assert client.post("/api/admin/users", headers=admin, json={"username": "boss", "email": "boss@example.com", "password": "boss-pass-1", "role": "admin"}).status_code == 200
    assert client.post("/api/admin/users", headers=admin, json={"username": "ali", "email": "ali@example.com", "password": "ali-pass-1", "role": "viewer"}).status_code == 200
    assert client.post("/api/admin/users", headers=admin, json={"username": "ALI", "email": "ALI@example.com", "password": "ali-pass-1"}).status_code == 409
    users = client.get("/api/admin/users", headers=admin).json()
    assert users["bootstrapLogin"] is False and "hash" not in json.dumps(users)
    assert login(client, "admin", ADMIN).status_code == 401           # bootstrap ends once an admin user exists

    assert login(client, "ali", "ali-pass-1").status_code == 200
    assert client.get("/api/server/repository").status_code == 200
    assert client.request("DELETE", "/api/server/reports/R1", json={}).status_code == 403   # viewer
    assert client.post("/api/server/folders", json={"path": "X"}).status_code == 403
    client.put("/api/admin/users/ali", headers=admin, json={"password": "new-pass-22"})
    assert client.get("/api/server/me").status_code == 401           # password change signs the user out
    assert login(client, "ali", "ali-pass-1").status_code == 401
    assert login(client, "ali", "new-pass-22").status_code == 200

    assert client.delete("/api/admin/users/boss", headers=admin).status_code == 409   # last admin stays
    assert client.put("/api/admin/users/boss", headers=admin, json={"role": "viewer"}).status_code == 409
    assert login(client, "boss", "boss-pass-1").json()["role"] == "admin"
    assert client.post("/api/server/folders", json={"path": "Team"}).status_code == 200
    assert client.request("DELETE", "/api/server/reports/R1", json={}).status_code == 200


def test_too_many_failed_logins_are_throttled(client):
    for _ in range(10):
        login(client, "nobody", "nope-nope")
    assert login(client, "admin", ADMIN).status_code == 429


def test_server_area_can_be_switched_off(tmp_path, monkeypatch, client):
    monkeypatch.setenv("REPORTING_ENABLED_AREAS", "runner,admin")
    off = TestClient(core.make_app())
    assert off.get("/server").status_code == 404
    assert off.get("/api/server/repository").status_code == 404
    # Sign-in stays on: it is how users reach /admin and /ui on this instance too.
    assert off.post("/api/server/login", json={"username": "admin", "password": ADMIN}).status_code == 200
    assert off.get("/admin").status_code == 200


def test_designer_server_and_admin_are_pages_of_the_react_build(client):
    for path in ("/designer", "/server", "/admin"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert '<div id="root"></div>' in page.text and "/assets/app/assets/" in page.text, path


def test_workspace_folder_with_drafts_is_moved_or_deleted_after_asking(client):
    client.put("/api/admin/workspace/D1?create=true", headers=token(), json=draft(folder="Letters/2026"))
    client.post("/api/admin/workspace-folders", headers=token(), json={"path": "Archive"})
    contents = client.get("/api/admin/workspace-folders/contents?path=Letters", headers=token()).json()
    assert contents["reports"] == ["Draft"] and contents["subfolders"] == ["Letters/2026"]
    assert client.request("DELETE", "/api/admin/workspace-folders", headers=token(), json={"path": "Letters"}).status_code == 409
    moved = client.request("DELETE", "/api/admin/workspace-folders", headers=token(), json={"path": "Letters", "moveTo": "Archive"})
    assert moved.status_code == 200, moved.text
    listing = client.get("/api/admin/workspace", headers=token()).json()
    assert {item["id"]: item["folder"] for item in listing["reports"]}["D1"] == "Archive/2026"
    assert "General" not in listing["folders"] and not any(item.startswith("Letters") for item in listing["folders"])
    gone = client.request("DELETE", "/api/admin/workspace-folders", headers=token(), json={"path": "Archive", "recursive": True})
    assert gone.status_code == 200 and "D1" not in [item["id"] for item in client.get("/api/admin/workspace", headers=token()).json()["reports"]]
