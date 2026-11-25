"""A data source belongs to one part (Designer, Server, Analytics, Query) and works only there."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.connectors.profiles import load_connection_profiles  # noqa: E402
from backend.app.security.secrets import _load_private_secrets  # noqa: E402
from test_access_rights import make_user, sign_in  # noqa: E402
from test_workspace_and_server import ADMIN, PUBLISHED, client, token  # noqa: E402,F401

ADMIN_HEADERS = {"X-Admin-Token": ADMIN}
URI = "mongodb://127.0.0.1:1/sales"


def add(client, area, name="Sales", uri=URI, **more):
    body = {"name": name, "id": name.lower(), "uri": uri, "create": True, "area": area, **more}
    return client.post("/api/data-sources", headers=ADMIN_HEADERS, json=body)


def ids(client, area=""):
    found = client.get(f"/api/data-sources{'?area=' + area if area else ''}", headers=ADMIN_HEADERS).json()["connections"]
    return {item["id"] for item in found}


@pytest.fixture
def four(client):
    """One "Sales" data source per part, all made under the same name."""
    out = {area: add(client, area).json()["connection"]["id"] for area in ("designer", "server", "analytics", "query")}
    assert len(set(out.values())) == 4 and out["designer"] == "sales" and out["server"] == "sales-server"
    return out


def test_each_part_sees_its_own(client, four):
    for area, own in four.items():
        assert ids(client, area) == {"default", own}                 # "default" is from before: shared
    assert ids(client) == {"default", *four.values()}
    assert add(client, "designer").status_code == 409                # the same name twice in one part
    assert add(client, "kitchen").status_code == 422
    assert {c["id"] for c in client.get("/api/admin/connections?area=query", headers=ADMIN_HEADERS).json()["connections"]} == {"default", four["query"]}


def test_rights_go_by_part(client, four):
    make_user(client, "quinn", ["query"])
    make_user(client, "ana", ["analytics"])
    make_user(client, "dee", ["designer", "analytics"])
    sign_in(client, "quinn")
    assert {c["id"] for c in client.get("/api/data-sources").json()["connections"]} == {"default", four["query"]}
    assert client.get("/api/data-sources?area=server").status_code == 403
    assert client.post(f"/api/data-sources/{four['server']}/test", json={}).status_code == 403
    assert client.request("DELETE", f"/api/data-sources/{four['server']}", json={}).status_code == 403
    assert client.post("/api/data-sources", json={"id": four["server"], "name": "Mine", "uri": URI}).status_code == 403   # not by overwriting it
    assert client.post(f"/api/data-sources/{four['query']}/copy", json={"area": "server"}).status_code == 403
    assert client.put(f"/api/data-sources/{four['query']}/area", json={"area": "server"}).status_code == 403
    sign_in(client, "ana")
    assert client.get("/api/data-sources?area=analytics").status_code == 403         # datasets need the Designer right too
    sign_in(client, "dee")
    assert {c["id"] for c in client.get("/api/data-sources").json()["connections"]} == {"default", four["designer"], four["analytics"]}
    assert client.post(f"/api/data-sources/{four['designer']}/copy", json={"area": "server"}).status_code == 200    # publishing


def test_a_part_refuses_the_others(client, four):
    h = token()
    other = client.post("/api/query/mongo/collections", headers=h, json={"connectionId": four["server"]})
    assert other.status_code == 422 and "belongs to the Report Server" in other.json()["detail"]
    draft = {"definition": {**PUBLISHED, "id": "D1", "connectionId": four["query"]}, "parameters": {}}
    assert "belongs to Query" in client.post("/api/admin/drafts/preview", headers=h, json=draft).json()["detail"]
    draft["definition"] = {**PUBLISHED, "id": "D1", "design": {"bands": {}, "datasets": [
        {"name": "L", "connectionId": four["analytics"], "queryLanguage": "aggregation-json", "collection": "c", "querySource": "[{\"$match\": {}}]", "fields": ["a"]}]}}
    assert "belongs to Analytics" in client.post("/api/admin/drafts/preview", headers=h, json=draft).json()["detail"]
    assert client.post("/api/admin/reports/R1/preview", headers=h, json={"parameters": {}, "connectionId": four["server"]}).status_code == 422
    new = client.post("/api/admin/reports", headers=h, json={**PUBLISHED, "id": "NEW1", "connectionId": four["designer"]})
    assert new.status_code == 422 and "belongs to the Designer" in new.json()["detail"]
    sign_in_admin(client)
    assert {c["id"] for c in client.get("/api/server/connections").json()["connections"]} == {"default", four["server"]}
    assert client.put("/api/server/reports/R1", json={"connectionId": four["designer"]}).status_code == 422
    assert {a["id"] for a in client.get("/api/analytics/info").json()["adapters"]} == {"default", four["analytics"]}
    home = client.get("/api/home").json()
    assert [home[part]["dataSources"] for part in four] == [2, 2, 2, 2]


def sign_in_admin(client):
    make_user(client, "chief", ["designer", "server", "analytics", "query", "admin"])
    sign_in(client, "chief")


def draft(client, report_id, connection_id, **more):
    saved = client.put(f"/api/admin/workspace/{report_id}?create=true", headers=token(),
                       json={**PUBLISHED, "id": report_id, "connectionId": connection_id, **more})
    assert saved.status_code == 200, saved.text
    return saved.json()


def published(client, report_id):
    return client.get(f"/api/admin/reports/{report_id}", headers=token()).json()["connectionId"]


def test_publishing_switches_to_the_servers_own(client):
    mine = add(client, "designer").json()["connection"]["id"]
    draft(client, "P1", mine)
    path = "/api/admin/workspace/P1/publish"
    refused = client.post(path, headers=token(), json={})
    assert refused.status_code == 422 and "Choose the Report Server" in refused.json()["detail"]
    theirs = add(client, "server", "Live", "mongodb://127.0.0.1:1/live").json()["connection"]["id"]
    assert client.post(path, headers=token(), json={"connections": {mine: mine}}).status_code == 422
    assert client.post(path, headers=token(), json={"connections": {mine: "nope"}}).status_code == 422
    assert client.post(path, headers=token(), json={"connections": {mine: theirs}}).status_code == 200
    assert published(client, "P1") == theirs
    assert client.get("/api/admin/workspace/P1", headers=token()).json()["connectionId"] == mine      # the draft keeps its own
    assert client.post(path, headers=token(), json={"replace": True}).status_code == 200              # remembered
    assert published(client, "P1") == theirs
    # deleting says who uses it
    assert "P1" in client.delete(f"/api/data-sources/{theirs}", headers=ADMIN_HEADERS).json()["detail"]
    assert client.delete(f"/api/data-sources/{mine}", headers=ADMIN_HEADERS).status_code == 409
    # opened from the server again: the Designer's one, not the server's
    client.delete("/api/admin/workspace/P1", headers=token())
    again = draft(client, "P1", theirs)
    assert again["switchedConnections"] == {theirs: mine} and again["connectionId"] == mine


def test_the_same_database_is_found_or_copied(client):
    mine = add(client, "designer").json()["connection"]["id"]
    h = ADMIN_HEADERS
    asked = client.get(f"/api/data-sources/{mine}/targets?area=server", headers=h).json()
    assert asked["suggested"] == "" and [t["id"] for t in asked["targets"]] == ["default"]
    copy = client.post(f"/api/data-sources/{mine}/copy", headers=h, json={"area": "query"}).json()["connection"]
    assert copy["area"] == "query" and copy["id"] != mine
    assert client.get(f"/api/data-sources/{mine}/targets?area=query", headers=h).json()["suggested"] == copy["id"]
    assert client.get(f"/api/data-sources/{copy['id']}/targets?area=designer", headers=h).json()["suggested"] == mine   # the way back
    same = add(client, "analytics", "Other").json()["connection"]["id"]
    assert client.get(f"/api/data-sources/{mine}/targets?area=analytics", headers=h).json()["suggested"] == same         # the same connection
    secrets = _load_private_secrets()["mongo_connections"]
    assert secrets[copy["id"]]["uri"] == URI
    client.post("/api/data-sources", headers=h, json={"id": copy["id"], "name": "Sales", "uri": "mongodb://127.0.0.1:1/changed"})
    assert _load_private_secrets()["mongo_connections"][mine]["uri"] == URI                      # nothing in common from then on
    assert client.delete(f"/api/data-sources/{copy['id']}", headers=h).status_code == 200
    assert mine in ids(client, "designer")


def test_an_older_shared_one_gets_one_part_or_is_split(client):
    h = ADMIN_HEADERS
    old = client.post("/api/data-sources", headers=h, json={"name": "Old", "uri": "mongodb://127.0.0.1:1/old"}).json()["connection"]["id"]
    assert all(old in ids(client, area) for area in ("designer", "server", "analytics", "query"))
    assert client.post("/api/data-sources", headers=h, json={"id": old, "name": "Old", "uri": URI, "area": "query"}).status_code == 409
    draft(client, "S1", old)
    assert client.post("/api/admin/workspace/S1/publish", headers=token(), json={}).status_code == 200
    assert client.put(f"/api/data-sources/{old}/area", headers=h, json={"area": "designer"}).status_code == 409    # the server uses it
    parts = client.post(f"/api/data-sources/{old}/split", headers=h, json={}).json()["ids"]
    assert parts["server"] == old and len(set(parts.values())) == 4
    for area, own in parts.items():
        assert ids(client, area) == {"default", own}
    assert published(client, "S1") == old
    assert client.get("/api/admin/workspace/S1", headers=token()).json()["connectionId"] == parts["designer"]
    assert client.post("/api/admin/workspace/S1/publish", headers=token(), json={"replace": True}).status_code == 200
    assert published(client, "S1") == old
    assert client.post(f"/api/data-sources/{old}/split", headers=h, json={}).status_code == 409
    unused = client.post("/api/data-sources", headers=h, json={"name": "Spare", "uri": URI}).json()["connection"]["id"]
    assert client.put(f"/api/data-sources/{unused}/area", headers=h, json={"area": "query"}).status_code == 200
    assert next(p for p in load_connection_profiles() if p["id"] == unused)["area"] == "query"


def test_publishing_without_the_adapter_of_the_report(client):
    """An imported report names an adapter that is not here: the message says so; a page of fixed text needs none."""
    draft = {"id": "GONE", "title": "Gone", "folder": "General", "connectionId": "gone", "collection": "c", "queryLanguage": "mongosh",
             "querySource": "[{\"$project\": {\"a\": 1}}]", "parameters": [], "columns": ["a"], "formats": ["pdf"], "defaultFormat": "pdf"}
    assert client.put("/api/admin/workspace/GONE?create=true", headers=token(), json=draft).status_code == 200
    refused = client.post("/api/admin/workspace/GONE/publish", headers=token(), json={})
    assert refused.status_code == 422 and "not on this server" in refused.json()["detail"]
    empty = client.put("/api/admin/workspace/GONE", headers=token(), json={**draft, "connectionId": ""}).json()
    assert not empty["readyToPublish"] and "Choose a data adapter" in empty["problem"]
    letter = {**draft, "id": "LETTER", "collection": "", "querySource": "", "columns": [], "connectionId": "",
              "design": {"bands": {"title": {"height": 30, "elements": [{"id": "t", "type": "staticText", "x": 0, "y": 0, "width": 90, "height": 20, "text": "Hi"}]}}}}
    assert client.put("/api/admin/workspace/LETTER?create=true", headers=token(), json=letter).status_code == 200
    done = client.post("/api/admin/workspace/LETTER/publish", headers=token(), json={})
    assert done.status_code == 200, done.text
