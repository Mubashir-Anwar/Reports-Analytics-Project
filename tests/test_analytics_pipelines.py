"""Pipelines (ETL / ELT) in Analytics: the Pipelines right, blocks and SQL / Python steps, trying them step by step,
saving them as a dataset that refreshes, and the sandbox of the SQL connection."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_access_rights import make_user, sign_in  # noqa: E402
from test_analytics import bi, make_dataset  # noqa: E402,F401
from test_workspace_and_server import client  # noqa: E402,F401


def blocks(orders, branches):
    return [
        {"kind": "source", "name": "orders", "dataset": orders},
        {"kind": "source", "name": "branches", "dataset": branches},
        {"kind": "filter", "name": "big", "input": "orders", "rules": [{"column": "Amount", "op": "gte", "value": 50}]},
        {"kind": "join", "name": "joined", "left": "big", "right": "branches", "how": "left", "on": [{"left": "Branch", "right": "Code"}]},
        {"kind": "group", "name": "by_region", "by": ["Region"], "aggs": [{"agg": "sum", "column": "Amount", "as": "total"},
                                                                          {"agg": "countrows", "as": "orders"}]},
        {"kind": "sort", "name": "ranked", "by": [{"column": "total", "dir": "desc"}]},
    ]


def run(client, steps, **extra):
    response = client.post("/api/analytics/pipelines/run", json={"steps": steps, **extra})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def two(bi):
    orders = make_dataset(bi, "Orders", "orders")["id"]
    branches = make_dataset(bi, "Branches", "branches")["id"]
    make_user(bi, "eli", ["analytics", "pipelines"])
    sign_in(bi, "eli")
    return orders, branches


def test_pipelines_need_their_right(bi):
    orders = make_dataset(bi, "Orders", "orders")["id"]
    assert bi.get("/api/analytics/info").json()["mayPipelines"] is False
    steps = [{"kind": "source", "name": "o", "dataset": orders}]
    assert bi.post("/api/analytics/pipelines/run", json={"steps": steps}).status_code == 403
    assert bi.post("/api/analytics/datasets", json={"name": "P", "sourceKind": "pipeline", "steps": steps}).status_code == 403


def test_blocks_join_group_and_sort(bi, two):
    orders, branches = two
    info = bi.get("/api/analytics/info").json()
    assert info["mayPipelines"] is True and "join" in info["pipeline"]["kinds"]
    result = run(bi, blocks(orders, branches))
    assert result["ok"] is True, result["error"]
    assert [s["name"] for s in result["steps"]] == ["orders", "branches", "big", "joined", "by_region", "ranked"]
    assert [s["rowCount"] for s in result["steps"]] == [4, 3, 3, 3, 2, 2]
    assert result["columns"] == ["Region", "total", "orders"]
    assert result["rows"][0] == ["Sindh", 200.0, 1] and result["rows"][1] == ["Punjab", 150.5, 2]
    joined = next(s for s in result["steps"] if s["name"] == "joined")
    assert "Region" in joined["columns"] and "Code" in joined["columns"]
    # up to one step only
    part = run(bi, blocks(orders, branches), until="big")
    assert [s["name"] for s in part["steps"]] == ["orders", "branches", "big"] and part["rowCount"] == 3
    # a wrong column names the step and the columns there are
    bad = blocks(orders, branches)
    bad[4]["aggs"][0]["column"] = "Amout"
    failed = run(bi, bad)
    assert failed["ok"] is False and failed["failedStep"] == "by_region" and "Amout" in failed["error"] and "Amount" in failed["error"]


def test_more_blocks_and_sql(bi, two):
    orders, _ = two
    steps = [
        {"kind": "source", "name": "o", "dataset": orders},
        {"kind": "select", "name": "s", "columns": [{"name": "Branch", "as": "branch"}, {"name": "Amount"}, {"name": "customer.tier", "as": "tier"}]},
        {"kind": "fill", "name": "f", "fills": [{"column": "tier", "value": "none"}]},
        {"kind": "cast", "name": "c", "casts": [{"column": "Amount", "to": "integer"}]},
        {"kind": "column", "name": "k", "column": "big", "expression": "Amount > 60"},
        {"kind": "dedupe", "name": "d", "columns": ["branch", "tier"]},
        {"kind": "union", "name": "u", "inputs": ["d", "k"]},
        {"kind": "filter", "name": "t", "match": "any", "rules": [{"column": "tier", "op": "contains", "value": "OLD"},
                                                                  {"column": "branch", "op": "in", "value": ["ISB"]}]},
        {"kind": "sql", "name": "q", "sql": "SELECT branch, count(*) AS n FROM t GROUP BY branch ORDER BY branch"},
    ]
    result = run(bi, steps)
    assert result["ok"] is True, result["error"]
    counts = {s["name"]: s["rowCount"] for s in result["steps"]}
    assert counts["d"] == 4 and counts["u"] == 8
    assert result["rows"] == [["ISB", 2], ["KHI", 2], ["LHR", 2]]


def test_the_sql_connection_reads_only_the_steps(bi, two, tmp_path):
    orders, _ = two
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    path = secret.as_posix()
    for sql in (f"SELECT * FROM read_csv('{path}')", f"SELECT * FROM read_parquet('{path}')"):
        result = run(bi, [{"kind": "source", "name": "o", "dataset": orders}, {"kind": "sql", "name": "x", "sql": sql}])
        assert result["ok"] is False and result["failedStep"] == "x"
    sneaky = run(bi, [{"kind": "source", "name": "o", "dataset": orders},
                      {"kind": "column", "name": "x", "column": "y", "expression": f"(SELECT count(*) FROM read_text('{path}'))"}])
    assert sneaky["ok"] is False and "only the earlier steps" in sneaky["error"]
    refused = bi.post("/api/analytics/pipelines/run", json={"steps": [{"kind": "source", "name": "o", "dataset": orders},
                                                                      {"kind": "sql", "name": "x", "sql": "DROP TABLE o"}]})
    assert refused.status_code == 422


def test_saved_pipeline_refreshes_and_protects_its_sources(bi, two):
    orders, branches = two
    made = bi.post("/api/analytics/datasets", json={"name": "Sales by region", "sourceKind": "pipeline", "steps": blocks(orders, branches)})
    assert made.status_code == 201, made.text
    ds = made.json()["dataset"]
    assert ds["status"] == "ready", ds.get("lastError")
    assert ds["rowCount"] == 2 and ds["sourceKind"] == "pipeline" and ds["inputs"] == [orders, branches]
    assert [s["name"] for s in ds["lastSteps"]] == ["orders", "branches", "big", "joined", "by_region", "ranked"]
    assert ds["steps"][2]["rules"][0]["column"] == "Amount"
    total = bi.post("/api/analytics/query", json={"dataset": ds["id"], "values": [{"column": "total", "agg": "sum"}]}).json()
    assert total["rows"][0][0] == pytest.approx(350.5)
    # a step that breaks keeps the last good data, says where it stopped
    steps = blocks(orders, branches)
    steps[2]["rules"][0]["column"] = "Nope"
    broken = bi.put(f"/api/analytics/datasets/{ds['id']}", json={"steps": steps}).json()["dataset"]
    assert broken["status"] == "ready" and broken["rowCount"] == 2 and "Nope" in broken["lastError"]
    assert broken["lastSteps"][-1]["name"] == "big" and broken["lastSteps"][-1]["error"]
    # it may not read itself; its sources may not be deleted while it uses them
    loop = bi.put(f"/api/analytics/datasets/{ds['id']}", json={"steps": [{"kind": "source", "name": "me", "dataset": ds["id"]}]})
    assert loop.status_code == 422 and "its own result" in loop.json()["detail"]
    sign_in(bi, "maya")
    used = bi.request("DELETE", f"/api/analytics/datasets/{branches}", json={})
    assert used.status_code == 409 and "Sales by region" in used.json()["detail"]


def test_python_steps_need_the_python_right(bi, two):
    orders, _ = two
    steps = [{"kind": "source", "name": "o", "dataset": orders},
             {"kind": "python", "name": "p", "script": "result = df.assign(twice=df['Amount'] * 2)"},
             {"kind": "sql", "name": "q", "sql": "SELECT sum(twice) AS t FROM p"}]
    refused = bi.post("/api/analytics/pipelines/run", json={"steps": steps})
    assert refused.status_code == 403 and "Python" in refused.json()["detail"]
    pytest.importorskip("pandas")
    make_user(bi, "pat", ["analytics", "pipelines", "python"])
    sign_in(bi, "pat")
    result = run(bi, steps)
    assert result["ok"] is True, result["error"]
    assert result["rows"] == [[pytest.approx((100 + 50.5 + 200 + 25) * 2)]]
