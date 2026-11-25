"""Analytics (Phase 4): datasets imported to Parquet, the model (relationships), the query engine, dashboards,
rights, scheduled refresh and the public API v1."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.analytics import engine, importer, store  # noqa: E402
from test_access_rights import ADMIN_HEADERS, make_user, sign_in  # noqa: E402
from test_workspace_and_server import client  # noqa: E402,F401

ORDERS = [
    {"_id": "a1", "Branch": "LHR", "Amount": 100, "Date": datetime(2026, 1, 5), "customer": {"city": "Lahore", "tier": "gold"}},
    {"_id": "a2", "Branch": "LHR", "Amount": 50.5, "Date": datetime(2026, 1, 20), "customer": {"city": "Lahore", "tier": "silver"}},
    {"_id": "a3", "Branch": "KHI", "Amount": 200, "Date": datetime(2026, 2, 3), "customer": {"city": "Karachi", "tier": "gold"}},
    {"_id": "a4", "Branch": "ISB", "Amount": 25, "Date": datetime(2026, 3, 9), "customer": {"city": "Islamabad", "tier": "silver"},
     "tags": ["x", "y"]},
]
BRANCHES = [{"Code": "LHR", "Region": "Punjab"}, {"Code": "ISB", "Region": "Capital"}, {"Code": "KHI", "Region": "Sindh"}]


@pytest.fixture
def bi(client, monkeypatch, tmp_path):
    """Signed in as a modeller (Analytics + Designer); dataset sources answer from the lists above."""
    monkeypatch.setenv("REPORTING_ANALYTICS_DIR", str(tmp_path / "analytics"))
    ctx = client.app.state.ctx
    ctx.analytics_refresher.inline = True
    sources = {"orders": ORDERS, "branches": BRANCHES}

    def rows(definition, _parameters, _connection, *, limit, timeout_ms=None):
        yield from sources[definition.collection]

    monkeypatch.setattr(ctx, "iter_report_rows", rows)
    client.sources = sources
    make_user(client, "maya", ["analytics", "designer"])
    sign_in(client, "maya")
    return client


def make_dataset(client, name, collection):
    made = client.post("/api/analytics/datasets", json={
        "name": name, "connectionId": "default", "queryLanguage": "aggregation-json", "collection": collection,
        "querySource": '[{"$match": {}}]'})
    assert made.status_code == 201, made.text
    return made.json()["dataset"]


def query(client, **visual):
    response = client.post("/api/analytics/query", json=visual)
    assert response.status_code == 200, response.text
    return response.json()


def test_import_detects_columns_and_flattens(bi):
    ds = make_dataset(bi, "Orders", "orders")
    assert ds["status"] == "ready" and ds["rowCount"] == 4 and ds["sizeBytes"] > 0
    kinds = {c["name"]: (c["kind"], c["role"]) for c in ds["columns"]}
    assert kinds["Amount"] == ("number", "measure") and kinds["Date"][0] == "date" and kinds["Branch"] == ("text", "dimension")
    assert "customer.city" in kinds and kinds["tags"][0] == "text"
    assert ds["querySource"] and ds["connectionId"] == "default"          # a modeller sees the source
    rows = bi.get(f"/api/analytics/datasets/{ds['id']}/rows").json()
    assert rows["rowCount"] == 4 and "customer.city" in rows["columns"]
    log = bi.get(f"/api/analytics/datasets/{ds['id']}/refreshes").json()["refreshes"]
    assert log[0]["status"] == "success" and log[0]["rowCount"] == 4 and log[0]["trigger"] == "manual"


def test_every_designer_query_language(bi):
    """Datasets take the designer's MongoDB languages, Python included (read as data, never run)."""
    for language, source in (("python", 'pipeline = [\n    {"$match": {}},\n]'), ("javascript", "pipeline = [ { $match: {} } ];"),
                             ("mongosh", 'db.getCollection("orders").aggregate([ { $match: {} } ])')):
        made = bi.post("/api/analytics/datasets", json={"name": f"Orders {language}", "connectionId": "default",
                                                        "queryLanguage": language, "collection": "orders", "querySource": source})
        assert made.status_code == 201, made.text
        assert made.json()["dataset"]["status"] == "ready" and made.json()["dataset"]["rowCount"] == 4, (language, made.json()["dataset"].get("lastError"))
    assert "python" in bi.get("/api/analytics/info").json()["languages"]
    code = bi.post("/api/analytics/datasets", json={"name": "Bad", "connectionId": "default", "queryLanguage": "python",
                                                    "collection": "orders", "querySource": "import os\npipeline = []"})
    assert code.status_code == 201 and code.json()["dataset"]["status"] == "failed"          # refused, nothing executed


def test_group_sum_buckets_filters_and_sort(bi):
    ds = make_dataset(bi, "Orders", "orders")["id"]
    result = query(bi, dataset=ds, groupBy=[{"column": "Branch"}], values=[{"column": "Amount", "agg": "sum"}])
    assert result["rows"] == [["KHI", 200], ["LHR", 150.5], ["ISB", 25]]       # biggest first
    assert result["columns"][1]["label"] == "Sum of Amount"
    months = query(bi, dataset=ds, groupBy=[{"column": "Date", "bucket": "month"}], values=[{"agg": "countrows", "column": ""}])
    assert months["rows"] == [["2026-01", 2], ["2026-02", 1], ["2026-03", 1]]  # in time order
    picked = query(bi, dataset=ds, groupBy=[{"column": "customer.tier"}], values=[{"column": "Amount", "agg": "avg"}],
                   filters=[{"column": "Branch", "op": "in", "values": ["LHR", "KHI"]}], sort=[{"by": "group:0", "dir": "asc"}])
    assert picked["rows"] == [["gold", 150], ["silver", 50.5]]
    found = query(bi, dataset=ds, values=[{"column": "Amount", "agg": "sum"}],
                  filters=[{"column": "customer.city", "op": "contains", "values": ["LAH"]},
                           {"column": "Date", "op": "gte", "values": ["2026-01-10"]}])
    assert found["rows"] == [[50.5]]
    # the value is bound, never part of the SQL
    safe = query(bi, dataset=ds, values=[{"agg": "countrows"}], filters=[{"column": "Branch", "op": "eq", "values": ["x'; DROP TABLE t; --"]}])
    assert safe["rows"] == [[0]]
    detail = query(bi, dataset=ds, detail=True, columns=[{"column": "Branch"}, {"column": "Amount"}], limit=2)
    assert detail["rowCount"] == 2 and detail["truncated"] is True


def test_bad_queries_are_refused(bi):
    ds = make_dataset(bi, "Orders", "orders")["id"]
    for visual, word in (({"groupBy": [{"column": 'Branch" FROM x --'}]}, "not a column"),
                         ({"values": [{"column": "Branch", "agg": "sum"}]}, "not a number"),
                         ({"values": [{"column": "Amount", "agg": "drop"}]}, "agg must be"),
                         ({}, "Add a field")):
        response = bi.post("/api/analytics/query", json={"dataset": ds, **visual})
        assert response.status_code == 422 and word in response.json()["detail"], response.text
    assert bi.post("/api/analytics/query", json={"dataset": "ds_000000000000", "values": [{"agg": "countrows"}]}).status_code == 422


def test_relationship_joins_two_datasets(bi):
    orders = make_dataset(bi, "Orders", "orders")["id"]
    branches = make_dataset(bi, "Branches", "branches")["id"]
    lonely = bi.post("/api/analytics/query", json={"dataset": orders, "groupBy": [{"dataset": branches, "column": "Region"}],
                                                   "values": [{"column": "Amount", "agg": "sum"}]})
    assert lonely.status_code == 422 and "relationship" in lonely.json()["detail"]
    rel = bi.post("/api/analytics/relationships", json={"fromDataset": orders, "fromColumn": "Branch", "toDataset": branches, "toColumn": "Code"})
    assert rel.status_code == 201, rel.text
    assert bi.post("/api/analytics/relationships", json={"fromDataset": branches, "fromColumn": "Code", "toDataset": orders,
                                                         "toColumn": "Branch"}).status_code == 422          # already related
    by_region = query(bi, dataset=orders, groupBy=[{"dataset": branches, "column": "Region"}], values=[{"column": "Amount", "agg": "sum"}])
    assert by_region["rows"] == [["Sindh", 200], ["Punjab", 150.5], ["Capital", 25]]
    # a filter on the other dataset
    punjab = query(bi, dataset=orders, values=[{"column": "Amount", "agg": "sum"}],
                   filters=[{"dataset": branches, "column": "Region", "op": "in", "values": ["Punjab"]}])
    assert punjab["rows"] == [[150.5]]
    assert bi.request("DELETE", f"/api/analytics/relationships/{rel.json()['relationship']['id']}", json={}).status_code == 200


def test_dashboard_batch_with_cross_filter(bi):
    ds = make_dataset(bi, "Orders", "orders")["id"]
    tiles = [{"id": "t1", "type": "column", "title": "By branch", "x": 0, "y": 0, "w": 6, "h": 4, "dataset": ds,
              "groupBy": [{"column": "Branch"}], "values": [{"column": "Amount", "agg": "sum"}]},
             {"id": "t2", "type": "kpi", "title": "Total", "x": 6, "y": 0, "w": 3, "h": 2, "dataset": ds,
              "values": [{"column": "Amount", "agg": "sum"}]}]
    made = bi.post("/api/analytics/dashboards", json={"name": "Sales", "tiles": tiles})
    assert made.status_code == 201, made.text
    dash = made.json()["dashboard"]
    assert dash["owner"] == "maya" and dash["mayChange"] and dash["folder"] == "Analytics"
    # a click on the LHR column filters the other visuals, not the one clicked
    click = [{"dataset": ds, "column": "Branch", "op": "in", "values": ["LHR"], "fromTile": "t1"}]
    results = bi.post("/api/analytics/query-batch", json={"queries": [{**t, "tileId": t["id"]} for t in dash["tiles"]],
                                                         "filters": click}).json()["results"]
    assert len(results[0]["rows"]) == 3 and results[1]["rows"] == [[150.5]]
    # a broken visual does not stop the others
    mixed = bi.post("/api/analytics/query-batch", json={"queries": [{"dataset": ds, "groupBy": [{"column": "Nope"}]},
                                                                   {"dataset": ds, "values": [{"agg": "countrows"}]}]}).json()["results"]
    assert "error" in mixed[0] and mixed[1]["rows"] == [[4]]
    assert bi.put(f"/api/analytics/dashboards/{dash['id']}", json={"tiles": [{"id": "t1", "type": "pyramid"}]}).status_code == 422
    changed = bi.put(f"/api/analytics/dashboards/{dash['id']}", json={"name": "Sales 2026"})
    assert changed.status_code == 200 and changed.json()["dashboard"]["name"] == "Sales 2026" and len(changed.json()["dashboard"]["tiles"]) == 2
    # the dataset is in use
    assert bi.request("DELETE", f"/api/analytics/datasets/{ds}", json={}).status_code == 409
    assert bi.request("DELETE", f"/api/analytics/dashboards/{dash['id']}", json={}).status_code == 200
    assert bi.request("DELETE", f"/api/analytics/datasets/{ds}", json={}).status_code == 200
    assert not list((Path(importer.settings.directory())).glob(f"{ds}-*.parquet"))


def test_rights(bi):
    ds = make_dataset(bi, "Orders", "orders")
    make_user(bi, "vic", ["analytics"])                    # analytics only: dashboards yes, datasets no
    sign_in(bi, "vic")
    seen = bi.get("/api/analytics/datasets").json()["datasets"]
    assert seen[0]["name"] == "Orders" and "querySource" not in seen[0]
    assert bi.get("/api/analytics/info").json()["mayModel"] is False
    assert bi.post("/api/analytics/datasets", json={"name": "x"}).status_code == 403
    assert bi.post(f"/api/analytics/datasets/{ds['id']}/refresh", json={}).status_code == 403
    mine = bi.post("/api/analytics/dashboards", json={"name": "Mine"}).json()["dashboard"]
    make_user(bi, "wes", ["analytics"])
    sign_in(bi, "wes")
    assert bi.put(f"/api/analytics/dashboards/{mine['id']}", json={"name": "Taken"}).status_code == 403
    make_user(bi, "nora", ["designer"])                    # no Analytics right at all
    sign_in(bi, "nora")
    assert bi.get("/api/analytics/dashboards").status_code == 403
    bi.post("/api/server/logout", json={})
    bi.cookies.clear()
    assert bi.get("/api/analytics/datasets").status_code == 401


def test_failed_import_and_scheduled_refresh(bi):
    ctx = bi.app.state.ctx
    ds = make_dataset(bi, "Orders", "orders")

    def broken(*_a, **_k):
        raise ValueError("database down")
        yield  # noqa: B901 - a generator

    original = ctx.iter_report_rows
    ctx.iter_report_rows = broken
    failed = bi.post(f"/api/analytics/datasets/{ds['id']}/refresh", json={}).json()["dataset"]
    assert failed["status"] == "ready" and failed["lastError"] == "database down"     # the old file is still used
    ctx.iter_report_rows = original
    changed = bi.put(f"/api/analytics/datasets/{ds['id']}", json={"refreshEveryMinutes": 5})
    assert changed.status_code == 422 and "15 minutes" in changed.json()["detail"]
    changed = bi.put(f"/api/analytics/datasets/{ds['id']}", json={"refreshEveryMinutes": 60}).json()["dataset"]
    assert changed["nextRefreshAt"]
    assert ctx.analytics_refresher.tick() == []                                      # not due yet
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert ctx.analytics_refresher.tick(later) == [ds["id"]]
    log = bi.get(f"/api/analytics/datasets/{ds['id']}/refreshes").json()["refreshes"]
    assert [item["trigger"] for item in log][:1] == ["schedule"] and {item["status"] for item in log} == {"success", "failed"}


def test_restart_recovery(bi):
    ds = make_dataset(bi, "Orders", "orders")
    store.save_dataset({**store.dataset(ds["id"]), "status": "refreshing"})
    bi.app.state.ctx.analytics_refresher.recover()
    again = store.dataset(ds["id"])
    assert again["status"] == "ready" and "restarted" in again["lastError"]


def test_dataset_deleted_during_import_stays_deleted(bi):
    ds = make_dataset(bi, "Orders", "orders")
    ctx = bi.app.state.ctx

    def rows_then_delete(*_a, **_k):
        store.delete_dataset(ds["id"])                     # someone deletes it while it is importing
        yield from ORDERS

    ctx.iter_report_rows = rows_then_delete
    assert bi.post(f"/api/analytics/datasets/{ds['id']}/refresh", json={}).status_code == 202
    assert store.dataset(ds["id"]) is None
    assert store.refreshes(ds["id"]) == []
    assert not list(importer.settings.directory().glob(f"{ds['id']}-*.parquet"))


def test_public_api_with_a_key(bi):
    ds = make_dataset(bi, "Orders", "orders")
    hidden = bi.post("/api/analytics/datasets", json={"name": "HR", "folder": "HR", "connectionId": "default",
                                                      "collection": "orders", "querySource": "[]"}).json()["dataset"]
    key = bi.post("/api/admin/api-keys", headers=ADMIN_HEADERS, json={"name": "Portal", "folders": ["Analytics"]}).json()["secret"]
    headers = {"X-API-Key": key}
    bi.cookies.clear()
    names = [item["name"] for item in bi.get("/api/v1/analytics/datasets", headers=headers).json()["datasets"]]
    assert names == ["Orders"]
    result = bi.post("/api/v1/analytics/query", headers=headers, json={"dataset": ds["id"], "values": [{"column": "Amount", "agg": "sum"}]})
    assert result.status_code == 200 and result.json()["rows"] == [[375.5]]
    assert bi.post("/api/v1/analytics/query", headers=headers, json={"dataset": hidden["id"], "values": [{"agg": "countrows"}]}).status_code == 404
    assert bi.post(f"/api/v1/analytics/datasets/{ds['id']}/refresh", headers=headers).status_code == 202
    assert bi.get("/api/v1/analytics/datasets").status_code in (401, 403)


def test_engine_names_are_quoted():
    assert engine.ident('a"b') == '"a""b"'
    assert importer.flatten({"a": {"b": {"c": {"d": {"e": 1}}}}}) == {"a.b.c.d": '{"e": 1}'}
    assert importer.kind_of("DECIMAL(18,2)") == "number" and importer.kind_of("TIMESTAMP WITH TIME ZONE") == "date"


# ------------------------------------------------------------------ dashboards on the Report Server (/server)
def sales_dashboard(client, folder="Analytics"):
    orders = make_dataset(client, "Orders", "orders")["id"]
    branches = make_dataset(client, "Branches", "branches")["id"]
    client.post("/api/analytics/relationships", json={"fromDataset": orders, "fromColumn": "Branch", "toDataset": branches, "toColumn": "Code"})
    tiles = [{"id": "t1", "type": "column", "title": "By region", "x": 0, "y": 0, "w": 6, "h": 4, "dataset": orders,
              "groupBy": [{"dataset": branches, "column": "Region"}], "values": [{"column": "Amount", "agg": "sum"}]},
             {"id": "t2", "type": "kpi", "title": "Total", "x": 6, "y": 0, "w": 3, "h": 2, "dataset": orders, "values": [{"column": "Amount", "agg": "sum"}]}]
    made = client.post("/api/analytics/dashboards", json={"name": "Sales", "folder": folder, "tiles": tiles})
    assert made.status_code == 201, made.text
    return made.json()["dashboard"], orders, branches


def test_dashboards_on_the_server(bi):
    dash, orders, branches = sales_dashboard(bi, "Sales")
    make_user(bi, "sam", ["server"])                               # Report Server only: sees and opens dashboards there
    sign_in(bi, "sam")
    repo = bi.get("/api/server/repository").json()
    assert [d["name"] for d in repo["dashboards"]] == ["Sales"] and "Sales" in repo["folders"]
    opened = bi.get(f"/api/server/dashboards/{dash['id']}").json()
    assert {d["id"] for d in opened["datasets"]} == {orders, branches} and "querySource" not in opened["datasets"][0]
    assert opened["dashboard"]["mayChange"] is False and opened["mayOpenAnalytics"] is False and len(opened["relationships"]) == 1
    queries = [{**t, "tileId": t["id"]} for t in opened["dashboard"]["tiles"]]
    results = bi.post(f"/api/server/dashboards/{dash['id']}/query-batch",
                      json={"queries": queries, "filters": [{"dataset": branches, "column": "Region", "op": "in", "values": ["Punjab"], "fromTile": "t1"}]}).json()["results"]
    assert results[1]["rows"] == [[150.5]]
    other = bi.post(f"/api/server/dashboards/{dash['id']}/query-batch",
                    json={"queries": [{"dataset": "ds_000000000000", "values": [{"agg": "countrows"}]}]})
    assert other.status_code == 403
    assert bi.get(f"/api/server/dashboards/{dash['id']}/file").status_code == 403           # a viewer may not download
    assert bi.request("DELETE", f"/api/server/dashboards/{dash['id']}", json={}).status_code == 403
    # folder permissions hide it, as they hide reports (in /analytics too)
    from backend.app.security import permissions
    permissions.save_rules_for("Sales", {"users": {"sam": "none", "maya": "none"}, "roles": {}})
    assert bi.get("/api/server/repository").json()["dashboards"] == []
    assert bi.get(f"/api/server/dashboards/{dash['id']}").status_code == 404
    sign_in(bi, "maya")
    assert bi.get("/api/analytics/dashboards").json()["dashboards"] == []
    assert bi.get(f"/api/analytics/dashboards/{dash['id']}").status_code == 404
    permissions.save_rules_for("Sales", {"users": {}, "roles": {}})


def test_dashboard_file_round_trip(bi):
    dash, orders, branches = sales_dashboard(bi)
    exported = bi.get(f"/api/analytics/dashboards/{dash['id']}/file")
    assert exported.status_code == 200 and ".mdash" in exported.headers["content-disposition"]
    content = exported.json()
    assert content["format"] == "analysis-dashboard" and {d["id"] for d in content["datasets"]} == {orders, branches}
    assert len(content["relationships"]) == 1 and "rows" not in str(content["datasets"][0].keys())
    make_user(bi, "dina", ["server", "designer", "analytics"])
    sign_in(bi, "dina")
    # the same datasets are on this server: they are used again, not copied
    again = bi.post("/api/server/dashboards/upload", json={"content": exported.text, "folder": "Copies"})
    assert again.status_code == 201, again.text
    assert again.json()["reused"] == [orders, branches] and again.json()["created"] == [] and again.json()["dashboard"]["folder"] == "Copies"
    # on a server without them (deleted here): new datasets, the relationship and the dashboard are made and imported
    for ds in (orders, branches):
        assert bi.request("DELETE", f"/api/analytics/datasets/{ds}?force=true", json={}).status_code == 200
    made = bi.post("/api/server/dashboards/upload", json={"content": exported.text, "folder": "Moved/Sales"}).json()
    assert len(made["created"]) == 2 and made["relationships"] == 1
    new_orders = next(i for i in made["created"] if store.dataset(i)["name"] == "Orders")
    new_branches = next(i for i in made["created"] if i != new_orders)
    tile = made["dashboard"]["tiles"][0]
    assert tile["dataset"] == new_orders and tile["groupBy"][0]["dataset"] == new_branches
    assert store.dataset(new_orders)["status"] == "ready" and store.dataset(new_orders)["folder"] == "Moved/Sales"
    shown = bi.post(f"/api/server/dashboards/{made['dashboard']['id']}/query-batch",
                    json={"queries": [{**t, "tileId": t["id"]} for t in made["dashboard"]["tiles"]]}).json()["results"]
    assert shown[0]["rows"] == [["Sindh", 200], ["Punjab", 150.5], ["Capital", 25]]
    # wrong files and adapters
    assert bi.post("/api/server/dashboards/upload", json={"content": "{}", "folder": "X"}).status_code == 422
    zipped = bi.post("/api/server/dashboards/upload", json={"content": "PK power bi", "folder": "X"})
    assert zipped.status_code == 422 and "Power BI" in zipped.json()["detail"]
    other = {**content, "datasets": [{**content["datasets"][0], "id": "ds_111111111111", "connectionId": "nowhere"}]}
    refused = bi.post("/api/server/dashboards/upload", json={"content": other, "folder": "X"})
    assert refused.status_code == 422 and "nowhere" in refused.json()["detail"]
    fixed = bi.post("/api/server/dashboards/upload", json={"content": other, "folder": "X", "connectionId": "default"})
    assert fixed.status_code == 201, fixed.text
    # move, download as the manager
    moved = bi.post(f"/api/server/dashboards/{made['dashboard']['id']}/move", json={"folder": "Archive"})
    assert moved.status_code == 200 and moved.json()["dashboard"]["folder"] == "Archive"
    assert bi.get(f"/api/server/dashboards/{made['dashboard']['id']}/file").status_code == 200


def test_deleting_a_folder_deletes_its_dashboards(bi):
    dash, orders, _ = sales_dashboard(bi, "Team/Sales")
    make_user(bi, "ada", ["server", "admin"])
    sign_in(bi, "ada")
    contents = bi.get("/api/server/folders/contents?path=Team").json()
    assert contents["dashboards"] == ["Sales"]
    assert bi.request("DELETE", "/api/server/folders", json={"path": "Team"}).status_code == 409   # not empty: confirm first
    assert bi.request("DELETE", "/api/server/folders", json={"path": "Team", "recursive": True}).status_code == 200
    assert store.dashboard(dash["id"]) is None and store.dataset(orders) is not None


# ------------------------------------------------------------------ /admin → Analytics
def test_admin_controls_analytics(bi):
    dash, orders, branches = sales_dashboard(bi)
    stray = importer.settings.directory() / "ds_abcdefabcdef-0badc0de.parquet"
    stray.write_bytes(b"x" * 100)                                       # left by a crash: no dataset uses it
    # Branches is only in a visual's group-by (a related dataset): still in use, so the modeller's delete refuses too
    assert bi.request("DELETE", f"/api/analytics/datasets/{branches}", json={}).status_code == 409
    assert bi.get("/api/admin/analytics").status_code == 403           # maya: Analytics + Designer, not Admin
    make_user(bi, "ada", ["admin"])                                    # Admin only: no Analytics right needed here
    sign_in(bi, "ada")
    seen = bi.get("/api/admin/analytics").json()
    assert seen["totals"]["datasets"] == 2 and seen["totals"]["dashboards"] == 1 and seen["totals"]["rows"] == 7
    assert seen["storage"]["strayFiles"] == 1 and seen["storage"]["files"] == 3 and seen["limits"]["queryMaxRows"] > 0
    by_name = {item["name"]: item for item in seen["datasets"]}
    assert by_name["Orders"]["dashboards"] == ["Sales"] and by_name["Orders"]["querySource"]
    assert seen["dashboards"][0]["owner"] == "maya" and seen["dashboards"][0]["ownerExists"] is True
    assert seen["recentImports"] and seen["recentImports"][0]["status"] == "success"
    # import now, change and pause the schedule
    assert bi.post(f"/api/admin/analytics/datasets/{orders}/refresh", json={}).status_code == 202
    assert bi.put(f"/api/admin/analytics/datasets/{orders}/schedule", json={"refreshEveryMinutes": 5}).status_code == 422
    planned = bi.put(f"/api/admin/analytics/datasets/{orders}/schedule", json={"refreshEveryMinutes": 60}).json()["dataset"]
    assert planned["refreshEveryMinutes"] == 60 and planned["nextRefreshAt"]
    paused = bi.put(f"/api/admin/analytics/datasets/{orders}/schedule", json={"refreshEveryMinutes": 0}).json()["dataset"]
    assert paused["refreshEveryMinutes"] == 0 and not paused["nextRefreshAt"] and store.dataset(orders)["querySource"]
    # someone left: their dashboard gets a new owner
    assert bi.put(f"/api/admin/analytics/dashboards/{dash['id']}/owner", json={"owner": "ghost"}).status_code == 422
    assert bi.put(f"/api/admin/analytics/dashboards/{dash['id']}/owner", json={"owner": "ada"}).json()["dashboard"]["owner"] == "ada"
    # clean up the stray file; delete a dataset in use only with force
    cleaned = bi.post("/api/admin/analytics/cleanup", json={}).json()
    assert cleaned["removed"] == 1 and cleaned["freedBytes"] == 100 and not stray.exists()
    assert bi.request("DELETE", f"/api/admin/analytics/datasets/{branches}", json={}).status_code == 409
    gone = bi.request("DELETE", f"/api/admin/analytics/datasets/{branches}?force=true", json={}).json()
    assert gone["dashboardsAffected"] == ["Sales"] and store.dataset(branches) is None
    events = [e["action"] for e in bi.get("/api/admin/audit-events?limit=50").json()["events"]]
    assert {"analytics.dashboard.owner_changed", "analytics.files.cleaned", "analytics.dataset.deleted"} <= set(events)


def run_q(client, text, language="dax"):
    response = client.post("/api/analytics/queries/run", json={"language": language, "text": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_dax_and_sql_queries_run_and_are_saved(bi):
    make_dataset(bi, "Orders", "orders")
    whole = run_q(bi, "EVALUATE 'Orders'")
    assert whole["rowCount"] == 4 and "Amount" in [c["name"] for c in whole["columns"]]
    by_branch = run_q(bi, """EVALUATE SUMMARIZECOLUMNS('Orders'[Branch], "Total", SUM('Orders'[Amount]), "Orders", COUNTROWS('Orders'))
                             ORDER BY [Total] DESC""")
    assert [c["name"] for c in by_branch["columns"]] == ["Branch", "Total", "Orders"]
    assert by_branch["rows"] == [["KHI", 200, 1], ["LHR", 150.5, 2], ["ISB", 25, 1]]
    filtered = run_q(bi, "EVALUATE FILTER('Orders', 'Orders'[Branch] IN {\"LHR\", \"ISB\"} && 'Orders'[Amount] >= 50)")
    assert filtered["rowCount"] == 2
    top = run_q(bi, "EVALUATE TOPN(1, 'Orders', 'Orders'[Amount], DESC)")
    assert top["rows"][0][[c["name"] for c in top["columns"]].index("Branch")] == "KHI"
    totals = run_q(bi, """EVALUATE ROW("All", SUM(Orders[Amount]), "LHR", CALCULATE(SUM(Orders[Amount]), Orders[Branch] = "LHR"),
                                    "Share", DIVIDE(CALCULATE(SUM(Orders[Amount]), Orders[Branch] = "LHR"), SUM(Orders[Amount])))""")
    assert totals["rows"][0][:2] == [375.5, 150.5] and round(totals["rows"][0][2], 3) == 0.401
    gold = run_q(bi, """EVALUATE SUMMARIZECOLUMNS('Orders'[Branch], FILTER('Orders', 'Orders'[customer.tier] = "gold"), "N", COUNTROWS('Orders'))
                        ORDER BY 'Orders'[Branch]""")
    assert gold["rows"] == [["KHI", 1], ["LHR", 1]] and "WHERE" in gold["sql"]
    sql = run_q(bi, 'SELECT Branch, SUM(Amount) AS total FROM "Orders" GROUP BY Branch ORDER BY total', "sql")
    assert sql["rows"][0] == ["ISB", 25]
    for text, language, word in (("EVALUATE 'Nope'", "dax", "no dataset called"),
                                 ("EVALUATE SUMMARIZECOLUMNS('Orders'[Branch], \"x\", FOO('Orders'[Amount]))", "dax", "FOO"),
                                 ("DROP TABLE x", "sql", "only reading"),
                                 ("SELECT 1; SELECT 2", "sql", "one statement"),
                                 ("SELECT * FROM read_csv('C:/Windows/win.ini')", "sql", "Only the datasets"),
                                 ("SELECT * FROM 'C:/Windows/win.ini'", "sql", "could not run")):
        refused = bi.post("/api/analytics/queries/run", json={"language": language, "text": text})
        assert refused.status_code == 422 and word in refused.json()["detail"], (text, refused.text)
    saved = bi.post("/api/analytics/queries", json={"name": "By branch", "language": "dax",
                                                     "text": "EVALUATE SUMMARIZECOLUMNS('Orders'[Branch], \"T\", SUM('Orders'[Amount]))"})
    assert saved.status_code == 200, saved.text
    qid = saved.json()["query"]["id"]
    assert [q["name"] for q in bi.get("/api/analytics/queries").json()["queries"]] == ["By branch"]
    assert bi.put(f"/api/analytics/queries/{qid}", json={"name": "Branch totals"}).json()["query"]["name"] == "Branch totals"
    make_user(bi, "viv", ["analytics"])                    # a viewer runs saved queries, but does not write them
    sign_in(bi, "viv")
    assert bi.post(f"/api/analytics/queries/{qid}/run", json={}).json()["rowCount"] == 3
    assert bi.post("/api/analytics/queries/run", json={"text": "EVALUATE 'Orders'"}).status_code == 403
    assert bi.delete(f"/api/analytics/queries/{qid}").status_code == 403
    sign_in(bi, "maya")
    assert bi.delete(f"/api/analytics/queries/{qid}").json()["deleted"] == qid
    assert bi.get(f"/api/analytics/queries/{qid}").status_code == 404


def test_database_queries_mongo_shell_json_and_python_run_and_are_saved(bi):
    """language "source": a read-only MongoDB pipeline (any designer style) run on the data adapter itself."""
    styles = {
        "mongosh": 'db.getCollection("orders").aggregate([ { $match: { Branch: "LHR" } } ])',
        "aggregation-json": '[{"$match": {"Branch": "LHR"}}]',
        "python": 'pipeline = [{"$match": {"Branch": "LHR"}}]',
    }
    for dialect, text in styles.items():
        ran = bi.post("/api/analytics/queries/run", json={"language": "source", "connectionId": "default",
                                                           "queryLanguage": dialect, "collection": "orders", "text": text})
        assert ran.status_code == 200, (dialect, ran.text)
        body = ran.json()
        assert body["rowCount"] == 4 and body["queryLanguage"] == dialect   # the fake source ignores the $match
        assert {"name": "Amount", "type": "number"} in body["columns"]
    writes = bi.post("/api/analytics/queries/run", json={"language": "source", "connectionId": "default", "queryLanguage": "aggregation-json",
                                                          "collection": "orders", "text": '[{"$out": "stolen"}]'})
    assert writes.status_code == 422 and "$out" in writes.json()["detail"]
    broken = bi.post("/api/analytics/queries/run", json={"language": "source", "connectionId": "default", "queryLanguage": "mongosh",
                                                          "collection": "orders", "text": '[ { "$match": { "a": 1 } } { "$limit": 5 } ]'})
    assert broken.status_code == 422 and "query line 1" in broken.json()["detail"]

    saved = bi.post("/api/analytics/queries", json={"name": "LHR orders", "language": "source", "connectionId": "default",
                                                     "queryLanguage": "python", "collection": "orders", "text": styles["python"]})
    assert saved.status_code == 200, saved.text
    item = saved.json()["query"]
    assert (item["language"], item["queryLanguage"], item["collection"]) == ("source", "python", "orders")
    again = bi.get(f"/api/analytics/queries/{item['id']}").json()["query"]
    assert again["connectionId"] == "default" and again["text"] == styles["python"]
    assert bi.post(f"/api/analytics/queries/{item['id']}/run", json={}).json()["rowCount"] == 4
    make_user(bi, "vic", ["analytics"])                    # a viewer may not read the database directly
    sign_in(bi, "vic")
    assert bi.post(f"/api/analytics/queries/{item['id']}/run", json={}).status_code == 403
    sign_in(bi, "maya")
    no_adapter = bi.post("/api/analytics/queries", json={"name": "x", "language": "source", "connectionId": "nope",
                                                          "queryLanguage": "mongosh", "text": "[]"})
    assert no_adapter.status_code == 422 and "not a data source of Analytics" in no_adapter.json()["detail"]


def test_model_check_suggest_update_and_dax_across_related_tables(bi):
    orders = make_dataset(bi, "Orders", "orders")["id"]
    branches = make_dataset(bi, "Branches", "branches")["id"]
    checked = bi.post("/api/analytics/relationships/check", json={"fromDataset": orders, "fromColumn": "Branch",
                                                                   "toDataset": branches, "toColumn": "Code"})
    assert checked.status_code == 200, checked.text
    info = checked.json()
    assert info["cardinality"] == "many-to-one" and info["fromMatchedPct"] == 100 and info["quality"] == "good"
    assert info["from"]["distinct"] == 3 and info["to"]["unique"] is True
    wrong = bi.post("/api/analytics/relationships/check", json={"fromDataset": orders, "fromColumn": "Amount",
                                                                 "toDataset": branches, "toColumn": "Code"}).json()
    assert wrong["matchedKeys"] == 0 and wrong["quality"] == "bad" and "do not join" in wrong["advice"][0]

    # DAX before the relationship: a clear message where to fix it
    lonely = bi.post("/api/analytics/queries/run", json={"language": "dax", "text":
                     "EVALUATE SUMMARIZECOLUMNS('Branches'[Region], \"Total\", SUM('Orders'[Amount]))"})
    assert lonely.status_code == 422 and "not related" in lonely.json()["detail"] and "Model" in lonely.json()["detail"]

    rel = bi.post("/api/analytics/relationships", json={"fromDataset": orders, "fromColumn": "Branch", "toDataset": branches,
                                                         "toColumn": "Code", "cardinality": "many-to-one"}).json()["relationship"]
    assert rel["cardinality"] == "many-to-one" and rel["active"] is True
    by_region = run_q(bi, "EVALUATE SUMMARIZECOLUMNS('Branches'[Region], \"Total\", SUM('Orders'[Amount]), \"Rows\", COUNTROWS('Orders')) ORDER BY [Total] DESC")
    assert [c["name"] for c in by_region["columns"]] == ["Region", "Total", "Rows"]
    assert by_region["rows"] == [["Sindh", 200, 1], ["Punjab", 150.5, 2], ["Capital", 25, 1]]
    related = run_q(bi, "EVALUATE FILTER('Orders', RELATED('Branches'[Region]) = \"Punjab\")")
    assert related["rowCount"] == 2 and not any("⟦" in c["name"] for c in related["columns"])
    added = run_q(bi, "EVALUATE ADDCOLUMNS('Orders', \"Region\", RELATED('Branches'[Region]))")
    assert "Region" in [c["name"] for c in added["columns"]]

    # inactive: not followed any more
    changed = bi.put(f"/api/analytics/relationships/{rel['id']}", json={"active": False})
    assert changed.status_code == 200 and changed.json()["relationship"]["active"] is False
    assert bi.post("/api/analytics/queries/run", json={"language": "dax", "text": "EVALUATE FILTER('Orders', RELATED('Branches'[Region]) = \"Punjab\")"}).status_code == 422
    assert bi.put(f"/api/analytics/relationships/{rel['id']}", json={"cardinality": "sideways"}).status_code == 422
    bi.put(f"/api/analytics/relationships/{rel['id']}", json={"active": True})

    # suggestions: same-named key columns, checked on the data
    bi.sources["visits"] = [{"Code": "LHR", "Visits": 3}, {"Code": "KHI", "Visits": 1}]
    make_dataset(bi, "Visits", "visits")
    suggested = bi.get("/api/analytics/relationships/suggest").json()["suggestions"]
    pair = [s for s in suggested if {s["fromName"], s["toName"]} == {"Branches", "Visits"}]
    assert pair and pair[0]["fromColumn"] == "Code" and pair[0]["matchedKeys"] == 2
