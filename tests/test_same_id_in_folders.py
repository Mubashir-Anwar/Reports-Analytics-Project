"""A report is <folder>/<ID>: the same report ID can be published in several folders (like JasperReports Server).

/generate, the public API, signed links, the Report Server and the designer choose one with the folder; without a
folder an ID that is in several folders is refused (409) instead of running a report from the wrong folder."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_workspace_and_server import ADMIN, PUBLISHED, client, login, token  # noqa: E402,F401

ADMIN_HEADERS = token(ADMIN)


def publish(client, report_id, folder, title):
    """Save a draft in the workspace folder and publish it to the same server folder."""
    draft = {**PUBLISHED, "id": report_id, "title": title, "folder": folder, "formats": ["csv", "pdf"]}
    saved = client.put(f"/api/admin/workspace/{report_id}?create=true", headers=token(), json=draft)
    assert saved.status_code == 200, saved.text
    published = client.post(f"/api/admin/workspace/{report_id}/publish?folder={folder}", headers=token(), json={"folder": folder})
    assert published.status_code == 200, published.text
    return published.json()


def two_copies(client):
    publish(client, "AR", "cascade/KMBL/Reports", "KMBL approved")
    publish(client, "AR", "cascade/KMBLGL/Reports", "KMBLGL approved")


def test_the_same_id_is_published_in_two_folders(client):
    two_copies(client)
    reports = client.app.state.ctx.reports_by_id
    assert sorted(item.ref for item in reports.matching("AR")) == ["cascade/KMBL/Reports/AR", "cascade/KMBLGL/Reports/AR"]
    listed = [r for r in client.get("/api/admin/reports", headers=ADMIN_HEADERS).json()["reports"] if r["id"] == "AR"]
    assert sorted(r["folder"] for r in listed) == ["cascade/KMBL/Reports", "cascade/KMBLGL/Reports"]
    # the same folder twice is still a clash
    again = client.put("/api/admin/workspace/AR?create=true", headers=token(), json={**PUBLISHED, "id": "AR", "folder": "cascade/KMBL/Reports"})
    assert again.status_code == 409 and "cascade/KMBL/Reports" in again.json()["detail"]


def test_generate_picks_the_folder(client):
    two_copies(client)
    headers = ADMIN_HEADERS
    unclear = client.get("/generate/AR.csv", headers=headers)
    assert unclear.status_code == 409
    assert "cascade/KMBL/Reports" in unclear.json()["detail"] and "Folder=" in unclear.json()["detail"]
    for folder in ("cascade/KMBLGL/Reports/", "/cascade/KMBLGL/Reports", "cascade/KMBLGL/Reports"):
        response = client.get(f"/generate/AR.csv?Folder={folder}", headers=headers)
        assert response.status_code == 200, (folder, response.text)
    missing = client.get("/generate/AR.csv?Folder=cascade/Other", headers=headers)
    assert missing.status_code == 404 and "not in the folder" in missing.json()["detail"]


def test_public_api_and_api_key_folders(client):
    two_copies(client)
    made = client.post("/api/admin/api-keys", headers=ADMIN_HEADERS, json={"name": "GL site", "folders": ["cascade/KMBLGL"]}).json()
    gl = {"X-API-Key": made["secret"]}
    everything = {"X-API-Key": client.post("/api/admin/api-keys", headers=ADMIN_HEADERS, json={"name": "All"}).json()["secret"]}
    client.cookies.clear()
    # a key that sees only one of them needs no folder
    assert client.get("/generate/AR.csv", headers=gl).status_code == 200
    assert client.post("/api/v1/reports/AR/run", headers=gl, json={"format": "csv"}).status_code == 200
    assert client.get("/generate/AR.csv?Folder=cascade/KMBL/Reports", headers=gl).status_code == 404     # outside its folders
    # a key that sees both must say which one
    assert client.post("/api/v1/reports/AR/run", headers=everything, json={"format": "csv"}).status_code == 409
    ran = client.post("/api/v1/reports/AR/run", headers=everything, json={"format": "csv", "folder": "cascade/KMBL/Reports"})
    assert ran.status_code == 200, ran.text
    view = client.get("/api/v1/reports/AR?folder=cascade/KMBLGL/Reports", headers=everything).json()
    assert view["title"] == "KMBLGL approved" and view["folder"] == "cascade/KMBLGL/Reports"
    listed = [r for r in client.get("/api/v1/reports", headers=everything).json()["reports"] if r["id"] == "AR"]
    assert len(listed) == 2
    # background job and signed link remember the folder
    client.app.state.ctx.job_runner.inline = True
    job = client.post("/api/v1/reports/AR/jobs", headers=everything, json={"format": "csv", "folder": "cascade/KMBLGL/Reports"})
    assert job.status_code == 202, job.text
    assert job.json()["job"]["folder"] == "cascade/KMBLGL/Reports" and job.json()["job"]["status"] == "success"
    link = client.post("/api/v1/links", headers=everything, json={"report": "AR", "folder": "cascade/KMBL/Reports", "format": "csv"})
    assert link.status_code == 200, link.text
    assert "Folder=cascade%2FKMBL%2FReports" in link.json()["url"]
    assert client.get(link.json()["url"]).status_code == 200


def test_report_server_keeps_them_apart(client):
    two_copies(client)
    login(client, "admin", ADMIN)
    assert client.get("/api/server/reports/AR").status_code == 409
    kmbl = client.get("/api/server/reports/AR?folder=cascade/KMBL/Reports").json()
    gl = client.get("/api/server/reports/AR?folder=cascade/KMBLGL/Reports").json()
    assert (kmbl["title"], kmbl["ref"]) == ("KMBL approved", "cascade/KMBL/Reports/AR")
    assert (gl["title"], gl["ref"]) == ("KMBLGL approved", "cascade/KMBLGL/Reports/AR")
    # edit one: only its versions and history change
    edited = client.put("/api/server/reports/AR?folder=cascade/KMBLGL/Reports", json={"title": "GL renamed"})
    assert edited.status_code == 200, edited.text
    gl_versions = client.get("/api/server/reports/AR/versions?folder=cascade/KMBLGL/Reports").json()["versions"]
    kmbl_versions = client.get("/api/server/reports/AR/versions?folder=cascade/KMBL/Reports").json()["versions"]
    assert len(gl_versions) == 2 and len(kmbl_versions) == 1
    assert {v["folder"] for v in gl_versions} == {"cascade/KMBLGL/Reports"}
    first = gl_versions[-1]["version"]
    assert client.get(f"/api/server/reports/AR/versions/{kmbl_versions[0]['version']}?folder=cascade/KMBLGL/Reports").status_code == 404
    restored = client.post(f"/api/server/reports/AR/versions/{first}/restore?folder=cascade/KMBLGL/Reports", json={})
    assert restored.status_code == 200 and restored.json()["report"]["title"] == "KMBLGL approved"
    assert client.get("/api/server/reports/AR?folder=cascade/KMBL/Reports").json()["title"] == "KMBL approved"
    assert [h["action"] for h in client.get("/api/server/reports/AR?folder=cascade/KMBL/Reports").json()["history"]] == ["published"]
    # moving one into the folder of the other is refused; copying into another folder keeps the ID
    clash = client.post("/api/server/reports/AR/move?folder=cascade/KMBLGL/Reports", json={"folder": "cascade/KMBL/Reports"})
    assert clash.status_code == 409
    copied = client.post("/api/server/reports/AR/copy?folder=cascade/KMBL/Reports", json={"folder": "General"})
    assert copied.status_code == 200 and copied.json()["report"]["id"] == "AR" and copied.json()["report"]["folder"] == "General"
    same_folder = client.post("/api/server/reports/AR/copy?folder=cascade/KMBL/Reports", json={"folder": "cascade/KMBL/Reports"})
    assert same_folder.json()["report"]["id"] == "AR_COPY"
    # deleting one leaves the other
    assert client.request("DELETE", "/api/server/reports/AR?folder=cascade/KMBL/Reports", json={}).status_code == 200
    left = sorted(item.ref for item in client.app.state.ctx.reports_by_id.matching("AR"))
    assert left == ["General/AR", "cascade/KMBLGL/Reports/AR"]


def test_schedules_and_background_runs_remember_the_folder(client):
    two_copies(client)
    login(client, "admin", ADMIN)
    client.app.state.ctx.job_runner.inline = True
    run = client.post("/api/server/reports/AR/background?folder=cascade/KMBLGL/Reports", json={"format": "csv", "parameters": {}})
    assert run.status_code == 200 and run.json()["run"]["folder"] == "cascade/KMBLGL/Reports"
    body = {"label": "Daily", "reportId": "AR", "format": "csv", "recurrence": {"type": "daily", "time": "08:00"}, "parameters": {}}
    assert client.post("/api/server/schedules", json=body).status_code == 409                   # which AR?
    made = client.post("/api/server/schedules", json={**body, "folder": "cascade/KMBL/Reports"})
    assert made.status_code == 200, made.text
    job = made.json()["schedule"]
    assert job["folder"] == "cascade/KMBL/Reports" and job["title"] == "KMBL approved"
    listed = client.get("/api/server/schedules?reportId=AR&folder=cascade/KMBLGL/Reports").json()["schedules"]
    assert listed == []
    started = client.post(f"/api/server/schedules/{job['id']}/run", json={}).json()["run"]
    assert started["folder"] == "cascade/KMBL/Reports" and started["status"] == "success"


def test_workspace_drafts_with_the_same_id(client):
    two_copies(client)
    assert client.get("/api/admin/workspace/AR", headers=token()).status_code == 409
    gl = client.get("/api/admin/workspace/AR?folder=cascade/KMBLGL/Reports", headers=token()).json()
    assert gl["title"] == "KMBLGL approved" and gl["workspace"]["onServer"] is True
    # a save from the folder it was opened in changes that draft only
    changed = client.put("/api/admin/workspace/AR?folder=cascade/KMBLGL/Reports", headers=token(),
                         json={**PUBLISHED, "id": "AR", "title": "GL draft", "folder": "cascade/KMBLGL/Reports"})
    assert changed.status_code == 200, changed.text
    assert client.get("/api/admin/workspace/AR?folder=cascade/KMBL/Reports", headers=token()).json()["title"] == "KMBL approved"
    # moving a draft into the folder of the other one is refused
    moved = client.put("/api/admin/workspace/AR?folder=cascade/KMBLGL/Reports", headers=token(),
                       json={**PUBLISHED, "id": "AR", "folder": "cascade/KMBL/Reports"})
    assert moved.status_code == 409
    # publishing the GL draft replaces only the GL server copy
    replaced = client.post("/api/admin/workspace/AR/publish?folder=cascade/KMBLGL/Reports", headers=token(), json={"replace": True})
    assert replaced.status_code == 200, replaced.text
    titles = {item.folder: item.title for item in client.app.state.ctx.reports_by_id.matching("AR")}
    assert titles == {"cascade/KMBL/Reports": "KMBL approved", "cascade/KMBLGL/Reports": "GL draft"}


def test_subreport_prefers_its_own_folder(client):
    two_copies(client)
    ctx = client.app.state.ctx
    parent = ctx.reports_by_id.get("cascade/KMBLGL/Reports/AR")
    assert ctx.subreport_target(parent, "AR").folder == "cascade/KMBLGL/Reports"
    assert ctx.subreport_target("cascade/KMBL/Reports", "AR").folder == "cascade/KMBL/Reports"
    assert ctx.subreport_target("General", "R1").report_id == "R1"            # elsewhere: the only one with the ID
