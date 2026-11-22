"""Schema v2 step 2 through the API: e-mail / phone on accounts, "already taken", teams in folder permissions,
who can see what, forgotten passwords (e-mailed link and admin approval) and My account."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_access_rights import ADMIN_HEADERS, PASSWORD, make_user, publish, sign_in  # noqa: E402
from test_workspace_and_server import client, login  # noqa: E402,F401


def test_new_user_needs_an_email_and_names_must_be_free(client):
    no_mail = client.post("/api/admin/users", headers=ADMIN_HEADERS, json={"username": "ali", "password": PASSWORD, "rights": ["runner"]})
    assert no_mail.status_code == 422 and "e-mail" in no_mail.json()["detail"]
    made = client.post("/api/admin/users", headers=ADMIN_HEADERS, json={
        "username": "ali", "password": PASSWORD, "rights": ["runner"], "email": "Ali@Example.com", "phone": "+92 300 1234567", "fullName": "Ali Khan"})
    assert made.status_code == 200 and made.json()["user"]["fullName"] == "Ali Khan" and made.json()["user"]["mustChangePassword"]
    same_mail = client.post("/api/admin/users", headers=ADMIN_HEADERS, json={"username": "ali2", "password": PASSWORD, "email": "ali@example.com"})
    assert same_mail.status_code == 409 and same_mail.json()["detail"]["field"] == "email"
    same_phone = client.post("/api/admin/users", headers=ADMIN_HEADERS, json={"username": "ali3", "password": PASSWORD, "email": "a3@example.com",
                                                                               "phone": "+923001234567"})
    assert same_phone.status_code == 409 and same_phone.json()["detail"]["field"] == "phone"
    answer = client.get("/api/admin/users/available?username=ALI&email=new@example.com&phone=%2B92%20300%201234567", headers=ADMIN_HEADERS).json()
    assert answer == {"username": {"taken": True}, "email": {"taken": False}, "phone": {"taken": True}}
    assert client.get("/api/admin/users/available?email=nope", headers=ADMIN_HEADERS).json()["email"]["problem"]
    assert client.get("/api/admin/users/available?email=ali@example.com&exceptUser=ali", headers=ADMIN_HEADERS).json()["email"]["taken"] is False
    # the sign-in error stays the same for a wrong password and an unknown user
    assert login(client, "ali", "wrong-password").json()["detail"] == login(client, "nobody", "wrong-password").json()["detail"]
    assert login(client, "ali", PASSWORD).json()["mustChangePassword"] is True


def test_teams_get_folder_permissions_and_the_access_report_explains(client):
    make_user(client, "boss", ["admin"], super_admin=True)
    make_user(client, "ali", ["server", "runner"])
    make_user(client, "sara", ["server", "runner"])
    publish(client, "R9", "Finance")
    team = client.post("/api/admin/user-groups", headers=ADMIN_HEADERS, json={"name": "Finance team", "members": ["ali"]}).json()["group"]
    assert team["members"] == ["ali"]
    assert client.post("/api/admin/user-groups", headers=ADMIN_HEADERS, json={"name": "finance TEAM"}).status_code == 409
    sign_in(client, "boss")
    saved = client.put("/api/server/permissions", json={"path": "Finance", "rules": {"groups": {team["groupId"]: "none"}}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["rules"]["groups"] == {team["groupId"]: "none"} and saved.json()["groups"][0]["name"] == "Finance team"
    report = client.get("/api/admin/access-report").json()["rows"]
    row = {(item["username"], item["folder"]): item for item in report}
    assert row[("ali", "Finance")]["level"] == "none" and row[("ali", "Finance")]["why"] == "team rule on /Finance"
    assert row[("sara", "Finance")]["level"] == "run"
    sign_in(client, "ali")
    assert "Finance" not in [item["path"] for item in client.get("/api/server/repository").json()["folders"]]
    sign_in(client, "boss")
    assert client.request("DELETE", f"/api/admin/user-groups/{team['groupId']}", json={}).status_code == 200
    assert client.get("/api/server/permissions?path=Finance").json()["rules"]["groups"] == {}


def test_forgot_password_with_admin_approval(client):
    make_user(client, "boss", ["admin"], super_admin=True)
    make_user(client, "ali", ["runner"])
    assert client.get("/api/server/password-help").json() == {"emailLink": False, "adminApproval": True}
    unknown = client.post("/api/server/forgot-password", json={"username": "nobody", "method": "admin_approval"}).json()
    known = client.post("/api/server/forgot-password", json={"username": "ali@example.com", "method": "email_link"}).json()
    assert unknown == known                                     # nobody learns which accounts exist
    sign_in(client, "boss")
    waiting = client.get("/api/admin/password-resets").json()
    assert waiting["waiting"] == 1 and waiting["requests"][0]["username"] == "ali"
    approved = client.post(f"/api/admin/password-resets/{waiting['requests'][0]['resetId']}/approve", json={}).json()
    token = approved["link"].split("reset=")[1]
    assert client.post("/api/server/reset-password", json={"token": token, "password": "short"}).status_code == 422
    assert client.post("/api/server/reset-password", json={"token": token, "password": "A-new-passw0rd"}).json()["username"] == "ali"
    assert client.post("/api/server/reset-password", json={"token": token, "password": "Another-passw0rd"}).status_code == 400   # once
    client.cookies.clear()
    assert login(client, "ali", "A-new-passw0rd").status_code == 200 and login(client, "ali", PASSWORD).status_code == 401
    assert client.post("/api/server/reset-password", json={"token": "made-up", "password": "A-new-passw0rd"}).status_code == 400


def test_forgot_password_by_emailed_link(client, monkeypatch):
    make_user(client, "ali", ["runner"])
    sent = []
    from backend.app.jobs import mailer
    monkeypatch.setenv("REPORTING_PUBLIC_URL", "https://reports.example.com/")
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, attachment=None: sent.append((to, text)))
    assert client.get("/api/server/password-help").json()["emailLink"] is True
    client.post("/api/server/forgot-password", json={"username": "ALI", "method": "email_link"}, headers={"Host": "evil.example"})
    assert sent[0][0] == ["ali@example.com"] and "https://reports.example.com/login?reset=" in sent[0][1] and "evil" not in sent[0][1]
    token = sent[0][1].split("reset=")[1].split()[0]
    assert client.post("/api/server/reset-password", json={"token": token, "password": "Mailed-passw0rd"}).status_code == 200


def test_my_account(client):
    make_user(client, "ali", ["runner"])
    make_user(client, "sara", ["runner"])
    sign_in(client, "ali")
    me = client.get("/api/server/account").json()
    assert me["username"] == "ali" and me["email"] == "ali@example.com"
    assert client.put("/api/server/account", json={"email": "sara@example.com"}).status_code == 409
    assert client.put("/api/server/account", json={"fullName": "Ali K", "phone": "0300 1234567"}).json()["fullName"] == "Ali K"
    assert client.post("/api/server/account/password", json={"currentPassword": "wrong", "newPassword": "New-passw0rd-1"}).status_code == 403
    assert client.post("/api/server/account/password", json={"currentPassword": PASSWORD, "newPassword": "New-passw0rd-1"}).status_code == 200
    assert client.get("/api/server/account").json()["mustChangePassword"] is False           # still signed in
    client.cookies.clear()
    assert login(client, "ali", "New-passw0rd-1").json()["mustChangePassword"] is False
