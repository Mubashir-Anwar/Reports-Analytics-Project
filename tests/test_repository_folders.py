"""Repository tree: nested folders, data adapters saved inside folders, moving adapters, folder delete rules."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_deploy_logs_markup import ADMIN, DESIGNER, admin, make_client  # noqa: E402,F401  (fixture re-export)


def adapters(client):
    return {item["id"]: item for item in client.get("/api/admin/connections", headers=admin()).json()["connections"]}


def test_nested_folders_and_adapter_saved_in_a_folder(make_client, tmp_path):
    client = make_client()
    response = client.post("/api/admin/connections", headers=admin(), json={
        "name": "KMBL UAT MongoDB", "uri": "mongodb://main/kmbl", "folder": "cascade/KMBLGL/Data Source"})
    assert response.status_code == 200, response.text
    assert response.json()["connection"]["folder"] == "cascade/KMBLGL/Data Source"
    # the folder and all its parents now exist
    folders = client.get("/api/admin/folders", headers=admin()).json()["folders"]
    assert {"cascade", "cascade/KMBLGL", "cascade/KMBLGL/Data Source"} <= set(folders)
    listed = adapters(client)["kmbl_uat_mongodb"]
    assert listed["folder"] == "cascade/KMBLGL/Data Source"
    assert adapters(client)["default"]["folder"] == ""  # top-level adapters have no folder
    saved = json.loads((tmp_path / "connections.json").read_text(encoding="utf-8"))
    assert any(item.get("folder") == "cascade/KMBLGL/Data Source" for item in saved["connections"])
    # the secret URL never appears in the public list
    assert "mongodb://" not in json.dumps(client.get("/api/admin/connections", headers=admin()).json())


def test_any_adapter_can_run_a_report_wherever_it_is(make_client):
    client = make_client()
    client.post("/api/admin/connections", headers=admin(), json={"name": "Folder adapter", "uri": "mongodb://main/f", "folder": "cascade/Data Source"})
    result = client.post("/api/admin/reports/R1/preview", headers=admin(), json={"parameters": {}, "connectionId": "folder_adapter"})
    assert result.status_code == 200, result.text
    assert result.json()["connectionId"] == "folder_adapter"


def test_move_adapter_between_folders_keeps_its_url(make_client, tmp_path):
    client = make_client()
    assert client.put("/api/admin/connections/broken/folder", headers=admin(), json={"folder": "cascade/KMBLSP/Data Source"}).status_code == 200
    assert adapters(client)["broken"]["folder"] == "cascade/KMBLSP/Data Source"
    secrets = json.loads((tmp_path / "private_secrets.json").read_text(encoding="utf-8"))
    assert secrets["mongo_connections"]["broken"]["uri"] == "mongodb://down/b"
    # back to the top level
    assert client.put("/api/admin/connections/broken/folder", headers=admin(), json={"folder": ""}).status_code == 200
    assert adapters(client)["broken"]["folder"] == ""
    assert client.put("/api/admin/connections/nope/folder", headers=admin(), json={"folder": "x"}).status_code == 404
    assert client.put("/api/admin/connections/broken/folder", headers=admin(), json={"folder": "../etc"}).status_code == 422
    # moving adapters is admin-only, like saving them
    assert client.put("/api/admin/connections/broken/folder", headers=admin(DESIGNER), json={"folder": "x"}).status_code == 403


def test_update_without_folder_field_keeps_the_folder(make_client):
    client = make_client()
    client.post("/api/admin/connections", headers=admin(), json={"id": "kmbl", "name": "KMBL", "uri": "mongodb://main/k", "folder": "cascade/Data Source"})
    client.post("/api/admin/connections", headers=admin(), json={"id": "kmbl", "name": "KMBL", "uri": "mongodb://main/k2"})
    assert adapters(client)["kmbl"]["folder"] == "cascade/Data Source"


def test_folder_with_adapter_cannot_be_deleted(make_client):
    client = make_client()
    client.post("/api/admin/connections", headers=admin(), json={"id": "kmbl", "name": "KMBL", "uri": "mongodb://main/k", "folder": "cascade/Data Source"})
    blocked = client.request("DELETE", "/api/admin/folders", headers=admin(), json={"path": "cascade/Data Source"})
    assert blocked.status_code == 409 and "KMBL" in blocked.json()["detail"]
    client.put("/api/admin/connections/kmbl/folder", headers=admin(), json={"folder": ""})
    assert client.request("DELETE", "/api/admin/folders", headers=admin(), json={"path": "cascade/Data Source"}).status_code == 200
    # a parent with subfolders still needs them removed first
    client.post("/api/admin/folders", headers=admin(), json={"path": "cascade/KMBLGL/Reports"})
    assert client.request("DELETE", "/api/admin/folders", headers=admin(), json={"path": "cascade"}).status_code == 409


def test_bad_adapter_folder_in_file_falls_back_to_top_level(make_client, tmp_path):
    client = make_client()
    data = json.loads((tmp_path / "connections.json").read_text(encoding="utf-8"))
    data["connections"][1]["folder"] = "../../outside"
    (tmp_path / "connections.json").write_text(json.dumps(data), encoding="utf-8")
    assert adapters(client)["broken"]["folder"] == ""


def test_deleting_a_folder_can_move_everything_to_another_folder(make_client):
    client = make_client()
    client.post("/api/admin/connections", headers=admin(), json={"id": "kmbl", "name": "KMBL", "uri": "mongodb://main/k", "folder": "Old/Data"})
    client.post("/api/admin/folders", headers=admin(), json={"path": "Old/Empty"})
    client.post("/api/admin/folders", headers=admin(), json={"path": "Keep"})
    done = client.request("DELETE", "/api/admin/folders", headers=admin(), json={"path": "Old", "moveTo": "Keep"})
    assert done.status_code == 200, done.text
    assert adapters(client)["kmbl"]["folder"] == "Keep/Data"
    folders = client.get("/api/admin/folders", headers=admin()).json()["folders"]
    assert "Keep/Empty" in folders and not any(item.startswith("Old") for item in folders)
    # into a folder inside the one being deleted: refused
    client.post("/api/admin/folders", headers=admin(), json={"path": "Keep/In"})
    inside = client.request("DELETE", "/api/admin/folders", headers=admin(), json={"path": "Keep", "moveTo": "Keep/In"})
    assert inside.status_code == 400


def test_deleting_everything_keeps_the_adapters_at_the_top_level(make_client):
    client = make_client()
    client.post("/api/admin/connections", headers=admin(), json={"id": "kmbl", "name": "KMBL", "uri": "mongodb://main/k", "folder": "Gone"})
    assert client.request("DELETE", "/api/admin/folders", headers=admin(), json={"path": "Gone", "recursive": True}).status_code == 200
    assert "kmbl" in adapters(client) and not adapters(client)["kmbl"].get("folder")


def test_general_is_the_top_level_not_a_folder(make_client):
    client = make_client()
    assert "General" not in client.get("/api/admin/folders", headers=admin()).json()["folders"]
