"""Report Server -> Export ZIP (folders, index, permissions) and safe names."""
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.app.services.export_zip import safe_folder, safe_name  # noqa: E402
from test_access_rights import make_user, publish, sign_in  # noqa: E402
from test_workspace_and_server import client, setup_login  # noqa: E402,F401


def export(client, query=""):  # noqa: F811
    response = client.get(f"/api/server/export{query}")
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_zip_has_every_item_in_its_server_folder(client):  # noqa: F811
    publish(client, "HR1", "HR/Monthly")
    client.cookies.clear()
    setup_login(client)
    made = export(client)
    assert made.testzip() is None
    names = set(made.namelist())
    assert {"server/R1.mrd", "server/HR/Monthly/HR1.mrd", "index.html", "index.csv", "manifest.json", "README.txt"} <= names
    items = {i["id"]: i for i in json.loads(made.read("manifest.json"))["items"]}
    assert items["HR1"]["serverFolder"] == "/HR/Monthly" and items["R1"]["serverFolder"] == "/"
    rows = list(csv.DictReader(io.StringIO(made.read("index.csv").decode("utf-8-sig"))))
    assert {row["File in this ZIP"] for row in rows} == {"server/R1.mrd", "server/HR/Monthly/HR1.mrd"}
    assert b"mongoReportDefinition" in made.read("server/HR/Monthly/HR1.mrd")
    assert b"mongodb://" not in b"".join(made.read(n) for n in names)              # no data source address
    assert "server/R1.mrd" not in export(client, "?folder=HR").namelist()
    assert not json.loads(export(client, "?reports=false").read("manifest.json"))["items"]


def test_zip_only_holds_what_the_user_may_manage(client):  # noqa: F811
    publish(client, "HR1", "HR")
    make_user(client, "dina", ["designer", "server"])
    make_user(client, "vera", ["server"])
    client.cookies.clear()
    setup_login(client)
    assert client.put("/api/server/permissions", json={"path": "HR", "rules": {"users": {"dina": "run"}, "roles": {}}}).status_code == 200
    sign_in(client, "dina")
    assert "server/HR/HR1.mrd" not in export(client).namelist()
    sign_in(client, "vera")
    assert client.get("/api/server/export").status_code == 403


def test_safe_names():
    assert safe_name("a/b:c*") == "a_b_c_" and safe_name("con") == "_con" and safe_name("..") == "item"
    assert safe_folder("../x/..") == "folder/x/folder" and safe_folder("General") == ""
