"""Folder permissions on /server: who sees, runs and changes which reports (like JasperReports Server)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_workspace_and_server import ADMIN, PUBLISHED, client, login, token  # noqa: E402,F401

PASSWORD = "Passw0rd-long-enough"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN}


def publish(client, report_id, folder, **extra):
    draft = {**PUBLISHED, "id": report_id, "title": report_id, "folder": folder, **extra}
    assert client.put(f"/api/admin/workspace/{report_id}?create=true", headers=token(), json=draft).status_code == 200
    assert client.post(f"/api/admin/workspace/{report_id}/publish", headers=token(), json={}).status_code == 200


def as_user(client, username):
    client.post("/api/server/logout", json={})
    client.cookies.clear()
    assert login(client, username, ADMIN if username == "admin" else PASSWORD).status_code == 200


def set_rules(client, path, users=None, roles=None):
    as_user(client, "admin")
    response = client.put("/api/server/permissions", json={"path": path, "rules": {"users": users or {}, "roles": roles or {}}})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def repo(client):
    for name, role in (("ali", "viewer"), ("sara", "viewer"), ("dan", "designer")):
        assert client.post("/api/admin/users", headers=ADMIN_HEADERS, json={"username": name, "password": PASSWORD, "role": role}).status_code in (200, 201)
    publish(client, "FIN", "Finance")
    publish(client, "SECRET", "Finance/Secret")
    publish(client, "HR", "HR")
    return client


MINE = {"FIN", "HR", "R1", "SECRET"}


def visible(client):
    data = client.get("/api/server/repository").json()
    return sorted(r["id"] for r in data["reports"] if r["id"] in MINE), data


def test_without_rules_everything_is_as_before(repo):
    as_user(repo, "ali")
    ids, data = visible(repo)
    assert ids == ["FIN", "HR", "R1", "SECRET"] and {r["access"] for r in data["reports"]} == {"run"}
    as_user(repo, "dan")
    assert {r["access"] for r in visible(repo)[1]["reports"]} == {"manage"}


def test_teams_see_only_their_folders(repo):
    set_rules(repo, "", roles={"viewer": "none"})                    # viewers see nothing by default …
    set_rules(repo, "Finance", users={"ali": "run"})                 # … ali sees Finance
    set_rules(repo, "Finance/Secret", users={"ali": "none"})         # … but not its Secret subfolder
    set_rules(repo, "HR", users={"sara": "run"})

    as_user(repo, "ali")
    ids, data = visible(repo)
    assert ids == ["FIN"] and "HR" not in data["folders"] and "Finance/Secret" not in data["folders"]
    assert repo.post("/api/server/reports/FIN/run", json={"format": "csv"}).status_code == 200
    for hidden in ("HR", "SECRET", "R1"):                             # hidden reports look like missing ones
        assert repo.get(f"/api/server/reports/{hidden}").status_code == 404
        assert repo.post(f"/api/server/reports/{hidden}/run", json={"format": "csv"}).status_code == 404
        assert repo.post(f"/api/server/reports/{hidden}/background", json={"format": "csv"}).status_code == 404
    assert repo.get("/api/server/folders/contents?path=HR").status_code == 403

    as_user(repo, "sara")
    assert visible(repo)[0] == ["HR"]
    as_user(repo, "admin")
    assert visible(repo)[0] == ["FIN", "HR", "R1", "SECRET"]          # administrators always see everything


def test_viewer_can_never_get_manage(repo):
    set_rules(repo, "Finance", users={"ali": "manage"})
    as_user(repo, "ali")
    assert {r["access"] for r in visible(repo)[1]["reports"] if r["id"] == "FIN"} == {"run"}
    assert repo.put("/api/server/reports/FIN", json={"title": "x"}).status_code == 403   # viewer role


def test_designer_with_run_only_cannot_change(repo):
    set_rules(repo, "Finance", roles={"designer": "run"})
    as_user(repo, "dan")
    assert repo.get("/api/server/reports/FIN").json()["access"] == "run"
    assert repo.put("/api/server/reports/FIN", json={"title": "x"}).status_code == 403
    assert repo.get("/api/server/reports/FIN/versions").status_code == 403
    assert repo.get("/api/server/reports/FIN/file").status_code == 403
    assert repo.post("/api/server/reports/HR/move", json={"folder": "Finance"}).status_code == 403
    assert repo.post("/api/server/reports/HR/copy", json={"folder": "Finance"}).status_code == 403
    assert repo.post("/api/server/folders", json={"path": "Finance/New"}).status_code == 403
    copied = repo.post("/api/server/reports/FIN/copy", json={"folder": "HR"})            # run on the source is enough to copy out
    assert copied.status_code == 200 and copied.json()["report"]["folder"] == "HR"
    assert copied.json()["report"]["id"] == "FIN"                   # another folder: the copy keeps its ID (HR/FIN)
    mrd = repo.get("/api/server/reports/HR/file")
    assert mrd.status_code == 200
    upload = repo.post("/api/server/upload", json={"fileName": "x.mrd", "content": mrd.text, "folder": "Finance", "newId": "UP1"})
    assert upload.status_code == 403
    replace = repo.post("/api/server/upload", json={"fileName": "x.mrd", "content": mrd.text, "folder": "Finance", "newId": "FIN", "replace": True})
    assert replace.status_code == 403                               # cannot overwrite Finance/FIN, which it may not manage
    own = repo.post("/api/server/upload", json={"fileName": "x.mrd", "content": mrd.text, "folder": "HR", "newId": "FIN", "replace": True})
    assert own.status_code == 200                                   # HR/FIN is its own copy; Finance/FIN is untouched
    assert repo.get("/api/server/reports/FIN?folder=Finance").json()["folder"] == "Finance"


def test_user_rule_wins_over_role_rule_and_nearest_folder_wins(repo):
    data = set_rules(repo, "Finance", users={"ali": "none"}, roles={"viewer": "run"})
    by_user = {row["username"]: row for row in data["effective"]}
    assert by_user["ali"]["level"] == "none" and by_user["ali"]["why"] == "user rule on /Finance"
    assert by_user["sara"]["level"] == "run" and by_user["dan"]["why"] == "default for designer"
    secret = set_rules(repo, "Finance/Secret", roles={"viewer": "run"})
    assert {r["username"]: r["level"] for r in secret["effective"]}["ali"] == "run"   # the nearer folder wins
    assert secret["inherited"][0]["folder"] == "Finance"


def test_permission_admin_api(repo):
    as_user(repo, "dan")
    assert repo.get("/api/server/permissions?path=Finance").status_code == 403
    assert repo.put("/api/server/permissions", json={"path": "Finance", "rules": {}}).status_code == 403
    as_user(repo, "admin")
    bad = [{"users": {"nobody": "run"}}, {"users": {"ali": "owner"}}, {"roles": {"admin": "none"}}]
    for rules in bad:
        assert repo.put("/api/server/permissions", json={"path": "Finance", "rules": rules}).status_code == 422
    set_rules(repo, "Finance/Secret", users={"ali": "none"})
    assert repo.request("DELETE", "/api/server/folders", json={"path": "Finance", "recursive": True}).status_code == 200
    assert repo.get("/api/server/permissions?path=Finance/Secret").json()["rules"] == {"users": {}, "roles": {}}


def test_subreports_and_schedules_respect_permissions(repo):
    ctx = repo.app.state.ctx
    runner = ctx.job_runner
    runner.inline = True
    set_rules(repo, "Finance/Secret", users={"ali": "none"})
    allowed = ctx.report_allowed_for({"username": "ali", "role": "viewer"})
    load = ctx.subreport_loader(allowed)
    with pytest.raises(ValueError, match="not published"):
        load("SECRET", {})
    assert ctx.report_allowed_for(None) is None                         # token access (/generate): no folder limits

    as_user(repo, "dan")
    job = repo.post("/api/server/schedules", json={"reportId": "FIN", "format": "csv", "recurrence": {"type": "daily", "time": "08:00"}})
    assert job.status_code == 200
    assert repo.post("/api/server/schedules", json={"reportId": "SECRET", "format": "csv",
                                                    "recurrence": {"type": "daily", "time": "08:00"}}).status_code == 200  # dan may (default)
    set_rules(repo, "Finance", users={"dan": "none"})                     # later the permission is taken away
    runs = runner.tick(datetime.now(timezone.utc) + timedelta(days=2))
    assert runs and all(r["status"] == "failed" and "may no longer run" in r["error"] for r in runs)
    as_user(repo, "dan")
    assert repo.post(f"/api/server/schedules/{job.json()['schedule']['id']}/run", json={}).status_code == 404


def test_resource_page_follows_the_permission(repo):
    set_rules(repo, "Finance", roles={"designer": "run"})
    as_user(repo, "dan")
    page = repo.get("/api/server/reports/FIN").json()
    assert page["canEdit"] is False and page["querySource"] is None and page["access"] == "run"
    assert repo.get("/api/server/reports/HR").json()["canEdit"] is True
