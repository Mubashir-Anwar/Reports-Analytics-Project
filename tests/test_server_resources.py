"""Phase 4: /server resources — details, edit, copy / move, .mrd download and upload, history, roles."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import service_names as core  # noqa: E402
from test_workspace_and_server import ADMIN, PUBLISHED, client, draft, login, token  # noqa: E402,F401

WITH_PARAM = {**PUBLISHED, "id": "R2", "title": "With question", "folder": "Finance",
              "parameters": [{"name": "Branch", "type": "string", "label": "Branch", "required": False}],
              "querySource": '[{"$match": {"Branch": {"$param": "Branch"}}}, {"$project": {"Name": 1}}]',
              "design": {"bands": {"detail": {"height": 20, "elements": [
                  {"type": "subreport", "x": 0, "y": 0, "width": 100, "height": 20, "reportId": "R1"}]}}}}


def users(client):
    admin = token(ADMIN)
    client.post("/api/admin/users", headers=admin, json={"username": "boss", "email": "boss@example.com", "password": "boss-pass-1", "role": "admin"})
    client.post("/api/admin/users", headers=admin, json={"username": "dee", "email": "dee@example.com", "password": "dee-pass-12", "role": "designer"})
    client.post("/api/admin/users", headers=admin, json={"username": "vic", "email": "vic@example.com", "password": "vic-pass-12", "role": "viewer"})


def publish(client, payload):
    client.put(f"/api/admin/workspace/{payload['id']}?create=true", headers=token(), json=payload)
    assert client.post(f"/api/admin/workspace/{payload['id']}/publish", headers=token(), json={"folder": payload["folder"]}).status_code == 200


def test_details_show_parameters_data_source_design_and_history(client):
    publish(client, WITH_PARAM)
    users(client)
    login(client, "dee", "dee-pass-12")
    info = client.get("/api/server/reports/R2").json()
    assert info["folder"] == "Finance" and info["connection"]["name"] == "Main" and info["connection"]["database"] == "a"
    assert info["parameters"][0]["name"] == "Branch" and info["querySource"].startswith("[")
    assert info["design"]["subreports"] == ["R1"] and info["history"][0]["action"] == "published"
    assert client.get("/api/server/reports/R1").json()["usedBy"] == ["R2"]
    login(client, "vic", "vic-pass-12")
    viewer = client.get("/api/server/reports/R2").json()
    assert viewer["querySource"] is None and viewer["canEdit"] is False   # viewers do not see the query


def test_edit_properties_updates_server_and_workspace_copy(client):
    publish(client, WITH_PARAM)
    users(client)
    login(client, "vic", "vic-pass-12")
    assert client.put("/api/server/reports/R2", json={"title": "x"}).status_code == 403
    login(client, "dee", "dee-pass-12")
    edited = client.put("/api/server/reports/R2", json={
        "title": "Branch report", "formats": ["pdf"], "parameters": [{"name": "Branch", "label": "Which branch", "required": True,
                                                                     "allowedValues": ["North", "South"]}]})
    assert edited.status_code == 200
    body = edited.json()
    assert body["title"] == "Branch report" and body["formats"] == ["pdf"] and body["defaultFormat"] == "pdf"
    assert body["parameters"][0]["label"] == "Which branch" and body["parameters"][0]["allowedValues"] == ["North", "South"]
    assert body["history"][0]["action"] == "edited" and body["history"][0]["user"] == "dee"
    workspace = client.get("/api/admin/workspace/R2", headers=token()).json()
    assert workspace["title"] == "Branch report" and workspace["parameters"][0]["required"] is True
    assert client.put("/api/server/reports/R2", json={"formats": []}).status_code == 422
    assert client.put("/api/server/reports/R2", json={"connectionId": "nope"}).status_code == 422


def test_copy_move_download_and_upload(client):
    users(client)
    login(client, "dee", "dee-pass-12")
    copied = client.post("/api/server/reports/R1/copy", json={"folder": "Archive"})          # another folder: the ID stays
    assert copied.status_code == 200 and copied.json()["report"]["ref"] == "Archive/R1"
    assert client.get("/api/server/reports/R1").status_code == 409                          # now in two folders: say which
    same = client.post("/api/server/reports/R1/copy?folder=General", json={"folder": "General"})
    assert same.status_code == 200 and same.json()["report"]["id"] == "R1_COPY"
    assert client.post("/api/server/reports/R1/copy?folder=General", json={"newId": "R1_COPY"}).status_code == 409
    moved = client.post("/api/server/reports/R1_COPY/move", json={"folder": "Finance/Old"})
    assert moved.json()["report"]["folder"] == "Finance/Old"
    repository = client.get("/api/server/repository").json()
    assert "Finance/Old" in repository["folders"] and {"R1", "R1_COPY"} <= {r["id"] for r in repository["reports"]}

    file = client.get("/api/server/reports/R1/file?folder=General")
    assert file.status_code == 200 and file.headers["content-disposition"].endswith('R1.mrd"')
    content = file.text
    assert "mongoReportDefinition" in content
    assert client.post("/api/server/upload", json={"content": content, "folder": "Archive"}).status_code == 409  # already there
    uploaded = client.post("/api/server/upload", json={"content": content, "folder": "Imported", "newId": "R1_UP"})
    assert uploaded.status_code == 200 and uploaded.json()["report"]["folder"] == "Imported"
    replaced = client.post("/api/server/upload", json={"content": content, "folder": "General", "replace": True})
    assert replaced.json()["replaced"] is True
    assert client.post("/api/server/upload", json={"content": "<nope/>"}).status_code == 422
    assert client.post("/api/server/upload", json={"content": "not xml"}).status_code == 422
    history = client.get("/api/server/reports/R1_COPY").json()["history"]
    assert [h["action"] for h in history] == ["moved", "copied"]


def test_viewer_cannot_change_or_download_and_designer_cannot_delete(client):
    users(client)
    login(client, "vic", "vic-pass-12")
    assert client.get("/api/server/reports/R1/file").status_code == 403
    assert client.post("/api/server/reports/R1/copy", json={}).status_code == 403
    assert client.post("/api/server/upload", json={"content": "x"}).status_code == 403
    assert client.get("/api/server/connections").status_code == 403
    login(client, "dee", "dee-pass-12")
    assert client.get("/api/server/connections").json()["connections"][0]["id"] == "default"
    assert client.request("DELETE", "/api/server/reports/R1", json={}).status_code == 403
    assert client.get("/api/server/reports/NOPE").status_code == 404
    login(client, "boss", "boss-pass-1")
    assert client.request("DELETE", "/api/server/reports/R1", json={}).status_code == 200
    assert client.app.state.ctx.read_history("R1")[-1]["action"] == "deleted"          # kept in rs_report_history


def test_upload_a_jasper_jrxml(client):
    users(client)
    login(client, "dee", "dee-pass-12")
    jrxml = ('<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="J1" pageWidth="595" pageHeight="842" '
             'columnWidth="555" leftMargin="20" rightMargin="20" topMargin="20" bottomMargin="20">'
             '<queryString language="MongoDbQuery"><![CDATA[{"collectionName": "orders", "findQuery": {}}]]></queryString>'
             '<field name="Name" class="java.lang.String"/><detail><band height="20"><textField><reportElement x="0" y="0" width="100" height="20"/>'
             '<textFieldExpression><![CDATA[$F{Name}]]></textFieldExpression></textField></band></detail></jasperReport>')
    wrong_adapter = client.post("/api/server/upload", json={"content": jrxml, "folder": "Jasper", "connectionId": "nope"})
    assert wrong_adapter.status_code == 422
    done = client.post("/api/server/upload", json={"content": jrxml, "folder": "Jasper", "connectionId": "default"})
    assert done.status_code == 200, done.text
    assert done.json()["report"]["columns"] == ["Name"]


def test_folder_with_subfolders_and_reports_is_deleted_only_after_confirming(client):
    users(client)
    login(client, "boss", "boss-pass-1")
    for path in ("Deep/A/B/C", "Deep/A/D"):
        assert client.post("/api/server/folders", json={"path": path}).status_code == 200
    assert client.post("/api/server/reports/R1/copy", json={"folder": "Deep/A/B", "newId": "DEEP1"}).status_code == 200
    contents = client.get("/api/server/folders/contents?path=Deep").json()
    assert set(contents["subfolders"]) == {"Deep/A", "Deep/A/B", "Deep/A/B/C", "Deep/A/D"} and contents["reports"] == ["DEEP1"]
    refused = client.request("DELETE", "/api/server/folders", json={"path": "Deep"})
    assert refused.status_code == 409 and "4 subfolder" in refused.json()["detail"] and "DEEP1" in refused.json()["detail"]
    done = client.request("DELETE", "/api/server/folders", json={"path": "Deep", "recursive": True})
    assert done.status_code == 200 and done.json()["reportsDeleted"] == ["DEEP1"] and done.json()["subfoldersDeleted"] == 4
    repository = client.get("/api/server/repository").json()
    assert not any(f.startswith("Deep") for f in repository["folders"]) and "DEEP1" not in {r["id"] for r in repository["reports"]}
    assert not (Path(core.data_directory()) / "reports" / "Deep").exists()
    assert client.request("DELETE", "/api/server/folders", json={"path": "General", "recursive": True}).status_code == 400
    # the designer (admin token) can do the same; a designer account cannot
    client.post("/api/server/folders", json={"path": "Old/Empty/Nested"})
    assert client.request("DELETE", "/api/admin/folders", headers=token(ADMIN), json={"path": "Old"}).status_code == 409
    assert client.request("DELETE", "/api/admin/folders", headers=token(ADMIN), json={"path": "Old", "recursive": True}).status_code == 200
    login(client, "dee", "dee-pass-12")
    client.post("/api/server/folders", json={"path": "Mine"})
    assert client.request("DELETE", "/api/server/folders", json={"path": "Mine", "recursive": True}).status_code == 403
