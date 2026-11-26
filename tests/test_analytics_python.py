"""Python scripts in Analytics: the Python right, trying a script, saving it as a dataset that refreshes, and the
guard rails of the script process (no network, no server files, no programs, a time limit)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pandas")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_access_rights import make_user, sign_in  # noqa: E402
from test_analytics import bi, make_dataset  # noqa: E402,F401
from test_workspace_and_server import client  # noqa: E402,F401

CLEAN = '''result = df[df["Amount"] > 30].copy()
result["Amount"] = result["Amount"] * 2
result["Branch"] = result["Branch"].str.lower()
print("kept", len(result))
'''


def run(client, script, inputs):
    response = client.post("/api/analytics/python/run", json={"script": script, "inputs": inputs})
    assert response.status_code == 200, response.text
    return response.json()


def test_scripts_need_the_python_right(bi):
    orders = make_dataset(bi, "Orders", "orders")["id"]
    assert bi.get("/api/analytics/info").json()["mayPython"] is False
    refused = bi.post("/api/analytics/python/run", json={"script": CLEAN, "inputs": [orders]})
    assert refused.status_code == 403 and "Python" in refused.json()["detail"]
    refused = bi.post("/api/analytics/datasets", json={"name": "Clean", "sourceKind": "python", "script": CLEAN, "inputs": [orders]})
    assert refused.status_code == 403

    make_user(bi, "pia", ["analytics", "python"])            # no Designer right: Python datasets only
    sign_in(bi, "pia")
    info = bi.get("/api/analytics/info").json()
    assert info["mayPython"] is True and info["mayModel"] is False and info["python"]["available"] is True
    result = run(bi, CLEAN, [orders])
    assert result["ok"] is True, result
    assert result["rowCount"] == 3 and result["output"].strip() == "kept 3"
    branch, amount = result["columns"].index("Branch"), result["columns"].index("Amount")
    assert sorted(row[branch] for row in result["rows"]) == ["khi", "lhr", "lhr"]
    assert sum(row[amount] for row in result["rows"]) == pytest.approx((100 + 50.5 + 200) * 2)
    plain = bi.post("/api/analytics/datasets", json={"name": "Raw", "connectionId": "default", "queryLanguage": "aggregation-json",
                                                     "collection": "orders", "querySource": "[]"})
    assert plain.status_code == 403                          # a database dataset still needs Designer or Admin


def test_a_python_dataset_is_saved_refreshed_and_queried(bi):
    orders = make_dataset(bi, "Orders", "orders")["id"]
    make_user(bi, "pia", ["analytics", "python"])
    sign_in(bi, "pia")
    made = bi.post("/api/analytics/datasets", json={"name": "Clean orders", "sourceKind": "python", "script": CLEAN, "inputs": [orders]})
    assert made.status_code == 201, made.text
    ds = made.json()["dataset"]
    assert ds["status"] == "ready", ds.get("lastError")
    assert ds["rowCount"] == 3 and ds["sourceKind"] == "python" and ds["script"] == CLEAN and ds["inputs"] == [orders]
    kinds = {c["name"]: c["kind"] for c in ds["columns"]}
    assert kinds["Amount"] == "number" and kinds["Date"] == "date" and kinds["Branch"] == "text"
    total = bi.post("/api/analytics/query", json={"dataset": ds["id"], "values": [{"column": "Amount", "agg": "sum"}]}).json()
    assert total["rows"][0][0] == pytest.approx((100 + 50.5 + 200) * 2)

    # the source changes: a refresh runs the script again on the new data of its input
    bi.sources["orders"].append({"_id": "a5", "Branch": "LHR", "Amount": 1000, "Date": ORDERS_DATE, "customer": {}})
    try:
        sign_in(bi, "maya")
        assert bi.post(f"/api/analytics/datasets/{orders}/refresh", json={}).status_code == 202
        sign_in(bi, "pia")
        assert bi.post(f"/api/analytics/datasets/{ds['id']}/refresh", json={}).status_code == 202
        assert bi.get(f"/api/analytics/datasets/{ds['id']}").json()["dataset"]["rowCount"] == 4
    finally:
        bi.sources["orders"].pop()

    # a script that fails keeps the last good data and says why
    changed = bi.put(f"/api/analytics/datasets/{ds['id']}", json={"script": "result = df['Nope']"})
    assert changed.status_code == 200, changed.text
    after = changed.json()["dataset"]
    assert after["status"] == "ready" and "Nope" in after["lastError"] and after["rowCount"] == 4

    # it cannot read itself, and its input cannot be deleted while it is used
    loop = bi.put(f"/api/analytics/datasets/{ds['id']}", json={"inputs": [ds["id"]]})
    assert loop.status_code == 422 and "own result" in loop.json()["detail"]
    sign_in(bi, "maya")
    used = bi.request("DELETE", f"/api/analytics/datasets/{orders}", json={})
    assert used.status_code == 409 and "Clean orders" in used.json()["detail"]
    # a modeller without the Python right sees the script but may not change it
    seen = bi.get(f"/api/analytics/datasets/{ds['id']}").json()["dataset"]
    assert seen["script"] == "result = df['Nope']"
    assert bi.put(f"/api/analytics/datasets/{ds['id']}", json={"script": CLEAN}).status_code == 403


ORDERS_DATE = __import__("datetime").datetime(2026, 4, 1)


@pytest.mark.parametrize("script, refused", [
    ("import socket\nsocket.socket()", "socket"),
    ("import urllib.request\nurllib.request.urlopen('http://127.0.0.1:9')", "urllib"),
    ("import os\nos.system('echo hi')", "os.system"),
    ("import subprocess\nsubprocess.run(['cmd'])", "subprocess"),
    ("open(r'%s').read()" % (Path(__file__).resolve().parent.parent / "requirements.txt"), "reading files"),
    ("open('../escape.txt', 'w').write('x')", "writing files"),
    ("import os\nprint(os.listdir(r'%s'))" % Path(__file__).resolve().parent, "listing"),
    ("import ctypes\nctypes.CDLL('kernel32' if __import__('os').name == 'nt' else 'libc.so.6')", "ctypes"),
])
def test_the_script_process_is_fenced_in(bi, script, refused):
    orders = make_dataset(bi, "Orders", "orders")["id"]
    make_user(bi, "pia", ["analytics", "python"])
    sign_in(bi, "pia")
    result = run(bi, script, [orders])
    assert result["ok"] is False and "Not allowed" in result["error"] and refused in result["error"], result["error"]


def test_no_secrets_in_the_environment_and_a_time_limit(bi, monkeypatch):
    orders = make_dataset(bi, "Orders", "orders")["id"]
    make_user(bi, "pia", ["analytics", "python"])
    sign_in(bi, "pia")
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN_TEST_SECRET", "s3cret-value")
    result = run(bi, "import os\nprint(sorted(os.environ))\nresult = df", [orders])
    assert result["ok"] is True and "REPORTING" not in result["output"] and "s3cret" not in result["output"]
    result = run(bi, "from sklearn.cluster import KMeans\ndf['group'] = KMeans(2, n_init=2).fit_predict(df[['Amount']])\nresult = df",
                 [orders])
    assert result["ok"] is True and "group" in result["columns"], result["error"]
    monkeypatch.setenv("ANALYTICS_PYTHON_TIMEOUT_SECONDS", "5")
    slow = bi.post("/api/analytics/python/run", json={"script": "while True:\n    pass", "inputs": [orders]})
    assert slow.status_code == 422 and "longer than 5 s" in slow.json()["detail"]
    bad = run(bi, "result = df[", [orders])
    assert bad["ok"] is False and bad["error"].startswith("line 1")
