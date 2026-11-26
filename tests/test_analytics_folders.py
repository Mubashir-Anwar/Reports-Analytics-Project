"""Folders in Analytics: create, rename (its datasets, queries and subfolders go with it) and delete (moving what is
inside), with the folder permissions of the Analytics Server."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_access_rights import make_user, sign_in  # noqa: E402
from test_analytics import bi, make_dataset  # noqa: E402,F401
from test_workspace_and_server import client  # noqa: E402,F401


def folders(client):
    response = client.get("/api/analytics/folders")
    assert response.status_code == 200, response.text
    return response.json()["folders"]


def test_create_rename_and_delete(bi):
    assert "Analytics" in folders(bi)
    made = bi.post("/api/analytics/folders", json={"path": "Sales"})
    assert made.status_code == 200, made.text
    assert bi.post("/api/analytics/folders", json={"path": "Sales/North"}).status_code == 200
    assert bi.post("/api/analytics/folders", json={"path": "Sales"}).status_code == 409
    assert bi.post("/api/analytics/folders", json={"path": "Bad/../x"}).status_code == 422
    assert {"Sales", "Sales/North"} <= set(folders(bi))

    dataset = make_dataset(bi, "Orders", "orders")
    moved = bi.put(f"/api/analytics/datasets/{dataset['id']}", json={"folder": "Sales/North"})
    assert moved.status_code == 200, moved.text

    renamed = bi.post("/api/analytics/folders/rename", json={"path": "Sales", "newPath": "Revenue"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["moved"] == 1
    listed = folders(bi)
    assert {"Revenue", "Revenue/North"} <= set(listed) and "Sales" not in listed and "Sales/North" not in listed
    assert bi.get(f"/api/analytics/datasets/{dataset['id']}").json()["dataset"]["folder"] == "Revenue/North"
    assert bi.post("/api/analytics/folders/rename", json={"path": "Revenue", "newPath": "Revenue/Inner"}).status_code == 400
    assert bi.post("/api/analytics/folders/rename", json={"path": "Analytics", "newPath": "Other"}).status_code == 400

    # not empty: needs a folder to move the things to
    assert bi.request("DELETE", "/api/analytics/folders", json={"path": "Revenue"}).status_code == 409
    gone = bi.request("DELETE", "/api/analytics/folders", json={"path": "Revenue", "moveTo": "Analytics"})
    assert gone.status_code == 200, gone.text
    assert gone.json()["moved"] == 1
    assert not any(f.startswith("Revenue") for f in folders(bi))
    assert bi.get(f"/api/analytics/datasets/{dataset['id']}").json()["dataset"]["folder"] == "Analytics"


def test_changing_folders_needs_the_designer_or_admin_right(bi):
    assert bi.post("/api/analytics/folders", json={"path": "Team"}).status_code == 200
    make_user(bi, "ann", ["analytics"])
    sign_in(bi, "ann")
    assert "Team" in folders(bi)
    assert bi.post("/api/analytics/folders/rename", json={"path": "Team", "newPath": "Crew"}).status_code == 403
    assert bi.request("DELETE", "/api/analytics/folders", json={"path": "Team"}).status_code == 403
