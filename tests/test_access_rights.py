"""Rights per account (Admin, Designer, Report Server, Analytics, Run reports), super admins, API keys and signed links.

The bug this guards against: a designer-only user could open /admin, /server and the runner, because those
pages used a shared admin token (handed to every local browser) instead of the signed-in user.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.security.users import save_users  # noqa: E402
from test_workspace_and_server import ADMIN, client, login, token  # noqa: E402,F401

PASSWORD = "Passw0rd-long-enough"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN}


def make_user(client, name, rights, super_admin=False, headers=ADMIN_HEADERS):
    return client.post("/api/admin/users", headers=headers,
                       json={"username": name, "password": PASSWORD, "rights": rights, "superAdmin": super_admin})


def sign_in(client, name):
    client.post("/api/server/logout", json={})
    client.cookies.clear()
    assert login(client, name, PASSWORD).status_code == 200


def publish(client, report_id, folder, parameters=()):
    draft = {"id": report_id, "title": report_id, "collection": "orders", "connectionId": "default", "parameters": list(parameters),
             "queryLanguage": "aggregation-json", "querySource": '[{"$project": {"Name": 1}}]', "columns": ["Name"],
             "formats": ["csv", "pdf"], "folder": folder}
    assert client.put(f"/api/admin/workspace/{report_id}?create=true", headers=token(), json=draft).status_code == 200
    assert client.post(f"/api/admin/workspace/{report_id}/publish", headers=token(), json={}).status_code == 200


# ------------------------------------------------------------------ pages
def test_signed_out_pages_go_to_sign_in(client):
    client.cookies.clear()
    for page in ("/designer", "/admin", "/ui", "/analytics"):
        response = client.get(page, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == f"/login?next=%2F{page[1:]}"
    assert client.get("/login").status_code == 200
    assert client.get("/server").status_code == 200                  # the sign-in form


def test_designer_only_user_sees_only_the_designer(client):
    assert make_user(client, "dina", ["designer"]).status_code == 200
    sign_in(client, "dina")
    assert client.get("/designer").status_code == 200
    for page in ("/admin", "/server", "/ui", "/analytics"):
        response = client.get(page)
        assert response.status_code == 403 and "No access" in response.text and "/designer" in response.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/designer"
    me = client.get("/api/server/me").json()
    assert me["rights"] == ["designer"] and me["superAdmin"] is False


def test_designer_only_user_cannot_use_other_apis(client):
    make_user(client, "dina", ["designer"])
    sign_in(client, "dina")
    assert client.get("/api/admin/workspace").status_code == 200       # the designer's own API works with the sign-in
    assert client.get("/api/admin/users").status_code == 403           # admin
    assert client.get("/api/admin/api-keys").status_code == 403
    assert client.get("/api/admin/logs").status_code == 403
    assert client.get("/api/server/repository").status_code == 403    # report server
    assert client.get("/reports").status_code == 403                   # run reports
    assert client.get("/generate/R1.csv").status_code == 403


def test_each_right_opens_its_own_part(client):
    make_user(client, "sam", ["server"])
    make_user(client, "rana", ["runner"])
    make_user(client, "ana", ["analytics"])
    make_user(client, "adam", ["admin"])
    sign_in(client, "sam")
    assert client.get("/server").status_code == 200 and client.get("/api/server/repository").status_code == 200
    assert client.get("/api/admin/workspace").status_code == 403 and client.get("/ui").status_code == 403
    sign_in(client, "rana")
    assert client.get("/ui").status_code == 200
    assert client.get("/reports").status_code == 200
    assert client.get("/generate/R1.csv").status_code == 200
    assert client.get("/server").status_code == 403 and client.get("/api/server/repository").status_code == 403
    sign_in(client, "ana")
    assert client.get("/analytics").status_code == 200 and client.get("/designer").status_code == 403
    sign_in(client, "adam")
    assert client.get("/admin").status_code == 200 and client.get("/api/admin/users").status_code == 200
    assert client.get("/designer").status_code == 403


def test_runner_sees_only_reports_its_folder_permissions_allow(client):
    publish(client, "HR1", "HR")
    make_user(client, "rana", ["runner", "server"])
    client.cookies.clear()
    assert login(client, "admin", ADMIN).status_code == 200            # first-setup sign-in = super admin
    assert client.put("/api/server/permissions", json={"path": "HR", "rules": {"users": {"rana": "none"}, "roles": {}}}).status_code == 200
    sign_in(client, "rana")
    ids = {item["id"] for item in client.get("/reports").json()["reports"]}
    assert "R1" in ids and "HR1" not in ids
    assert client.get("/generate/HR1.csv").status_code == 404           # hidden like a missing report


def test_writes_with_a_sign_in_must_be_json(client):
    make_user(client, "dina", ["designer"])
    sign_in(client, "dina")
    response = client.post("/api/admin/workspace-folders", content=b"path=X", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 415


# ------------------------------------------------------------------ super admins
def test_only_super_admin_manages_administrators(client):
    make_user(client, "boss", [], super_admin=True)
    make_user(client, "adam", ["admin"])
    sign_in(client, "adam")
    assert make_user(client, "dina", ["designer"], headers={}).status_code == 200          # normal accounts: yes
    assert make_user(client, "adam2", ["admin"], headers={}).status_code == 403           # another administrator: no
    assert make_user(client, "boss2", [], super_admin=True, headers={}).status_code == 403
    assert client.put("/api/admin/users/boss", json={"disabled": True}).status_code == 403
    assert client.request("DELETE", "/api/admin/users/boss", json={}).status_code == 403
    assert client.put("/api/admin/users/dina", json={"rights": ["designer", "admin"]}).status_code == 403   # no promoting
    assert client.put("/api/admin/users/dina", json={"rights": ["designer", "server"]}).status_code == 200
    sign_in(client, "boss")
    assert make_user(client, "boss2", [], super_admin=True, headers={}).status_code == 200  # several super admins
    assert client.put("/api/admin/users/adam", json={"rights": ["admin", "designer"]}).status_code == 200
    users = {item["username"]: item for item in client.get("/api/admin/users").json()["users"]}
    assert users["boss"]["superAdmin"] and users["boss"]["rights"] == ["admin", "analytics", "designer", "runner", "server"]
    assert users["adam"]["rights"] == ["admin", "designer"]


def test_the_last_super_admin_stays(client):
    make_user(client, "boss", [], super_admin=True)
    assert client.put("/api/admin/users/boss", headers=ADMIN_HEADERS, json={"superAdmin": False, "rights": ["admin"]}).status_code == 409
    assert client.put("/api/admin/users/boss", headers=ADMIN_HEADERS, json={"disabled": True}).status_code == 409
    assert client.delete("/api/admin/users/boss", headers=ADMIN_HEADERS).status_code == 409
    make_user(client, "boss2", [], super_admin=True)
    assert client.delete("/api/admin/users/boss", headers=ADMIN_HEADERS).status_code == 200


def test_unknown_rights_are_refused(client):
    assert make_user(client, "x1", ["designer", "root"]).status_code == 422
    assert make_user(client, "x2", "designer").status_code == 422


def test_accounts_saved_before_rights_keep_working(client):
    """users.json from older versions has only role: admin = super admin, designer = designer+server+runner."""
    from backend.app.security.users import _hash_password
    salt, digest = _hash_password(PASSWORD)
    old = {"salt": salt, "hash": digest, "iterations": 600_000, "sessionVersion": 0}
    save_users([{"username": "olda", "role": "admin", **old}, {"username": "oldd", "role": "designer", **old},
                {"username": "oldv", "role": "viewer", **old}])
    users = {item["username"]: item for item in client.get("/api/admin/users", headers=ADMIN_HEADERS).json()["users"]}
    assert users["olda"]["superAdmin"] is True
    assert users["oldd"]["rights"] == ["designer", "runner", "server"] and users["oldv"]["rights"] == ["runner", "server"]
    sign_in(client, "oldd")
    assert client.get("/designer").status_code == 200 and client.get("/admin").status_code == 403


def test_a_schedule_stops_when_its_owner_loses_the_server_right(client):
    ctx = client.app.state.ctx
    make_user(client, "sam", ["server", "runner"])
    assert ctx.session_for_user("sam")["role"] == "viewer"
    client.put("/api/admin/users/sam", headers=ADMIN_HEADERS, json={"rights": ["runner"]})
    assert ctx.session_for_user("sam") is None


# ------------------------------------------------------------------ API keys and signed links
@pytest.fixture
def key(client):
    publish(client, "HR1", "HR")
    response = client.post("/api/admin/api-keys", headers=ADMIN_HEADERS, json={"name": "Client site", "folders": ["General"]})
    assert response.status_code == 200
    body = response.json()
    assert body["secret"].startswith("mrk_") and "hash" not in body["key"]
    return body


def test_api_key_runs_reports_only_in_its_folders(client, key):
    client.cookies.clear()
    headers = {"X-API-Key": key["secret"]}
    assert {item["id"] for item in client.get("/reports", headers=headers).json()["reports"]} == {"R1"}
    assert client.get("/generate/R1.csv", headers=headers).status_code == 200
    assert client.get("/generate/R1.csv", headers={"Authorization": f"Bearer {key['secret']}"}).status_code == 200
    assert client.get("/generate/HR1.csv", headers=headers).status_code == 404
    assert client.get("/generate/R1.csv", headers={"X-API-Key": key["secret"][:-2] + "xx"}).status_code == 401
    listed = client.get("/api/admin/api-keys", headers=ADMIN_HEADERS).json()["keys"]
    assert listed[0]["lastUsedAt"] and "hash" not in listed[0]


def test_disabled_or_expired_or_wrong_address_key_is_refused(client, key):
    client.cookies.clear()
    key_id = key["key"]["id"]
    headers = {"X-API-Key": key["secret"]}
    client.put(f"/api/admin/api-keys/{key_id}", headers=ADMIN_HEADERS, json={"disabled": True})
    assert client.get("/reports", headers=headers).status_code == 401
    client.put(f"/api/admin/api-keys/{key_id}", headers=ADMIN_HEADERS, json={"disabled": False, "allowedIps": ["203.0.113.0/24"]})
    assert client.get("/reports", headers=headers).status_code == 401      # TestClient is not in that range
    client.put(f"/api/admin/api-keys/{key_id}", headers=ADMIN_HEADERS, json={"allowedIps": []})
    assert client.get("/reports", headers=headers).status_code == 200
    assert client.delete(f"/api/admin/api-keys/{key_id}", headers=ADMIN_HEADERS).status_code == 200
    assert client.get("/reports", headers=headers).status_code == 401


def test_signed_link_for_a_browser(client, key):
    client.cookies.clear()
    headers = {"X-API-Key": key["secret"]}
    publish(client, "P1", "General", [{"name": "Branch", "type": "string"}])
    assert client.post("/api/v1/links", headers=headers, json={"report": "R1", "parameters": {"Branch": "1"}}).status_code == 422
    issued = client.post("/api/v1/links", headers=headers, json={"report": "P1", "format": "csv", "parameters": {"Branch": "101"}})
    assert issued.status_code == 200, issued.text
    url = issued.json()["url"]
    assert "KeyId=" in url and "Signature=" in url and key["secret"] not in url
    assert client.get(url).status_code == 200                                   # no header: the link itself is the access
    assert client.get(url.replace("Branch=101", "Branch=999")).status_code == 403  # parameters cannot be changed
    assert client.get(url + "&Branch=999").status_code == 403                    # nor added after the signature
    assert client.post("/api/v1/links", headers=headers, json={"report": "HR1", "format": "csv"}).status_code == 404
    client.put(f"/api/admin/api-keys/{key['key']['id']}", headers=ADMIN_HEADERS, json={"disabled": True})
    assert client.get(url).status_code == 403                                   # revoking the key ends its links


def test_signed_links_are_only_for_api_keys(client):
    make_user(client, "rana", ["runner"])
    sign_in(client, "rana")
    assert client.post("/api/v1/links", json={"report": "R1"}).status_code == 403


def test_only_admins_manage_api_keys(client):
    make_user(client, "dina", ["designer", "server", "runner"])
    sign_in(client, "dina")
    assert client.post("/api/admin/api-keys", json={"name": "x site"}).status_code == 403


def test_a_website_proxy_with_its_key_opens_the_runner_page(client, key):
    """Grizzly-style proxy: the website's server adds X-API-Key; its visitors are not signed in here."""
    client.cookies.clear()
    assert client.get("/ui", headers={"X-API-Key": key["secret"]}, follow_redirects=False).status_code == 200
    assert client.get("/ui", headers={"X-API-Key": "mrk_000000000000_" + "x" * 40}, follow_redirects=False).status_code == 303
    assert client.get("/ui", follow_redirects=False).status_code == 303
