"""Analytics: machine learning visuals, and dashboards embedded in another website."""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.app.analytics import ml  # noqa: E402
from test_access_rights import ADMIN_HEADERS  # noqa: E402
from test_analytics import bi, make_dataset, query  # noqa: E402,F401
from test_workspace_and_server import client  # noqa: E402,F401

random.seed(7)
SALES = []
for n in range(400):
    qty = random.randint(1, 40)
    tier = "gold" if qty > 22 else "silver"
    SALES.append({"Qty": qty, "Tier": tier, "Branch": random.choice(["LHR", "KHI", "ISB"]), "Amount": 50 + 12 * qty + random.gauss(0, 6),
                  "Noise": random.random(), "Day": datetime(2024, 1, 1) + timedelta(days=n * 2)})
SALES[10]["Amount"] = 90_000


@pytest.fixture
def lab(bi):  # noqa: F811
    bi.sources["sales"] = SALES[:10] + SALES[11:]
    bi.sources["raw"] = SALES
    bi.ds = make_dataset(bi, "Sales", "sales")["id"]
    bi.raw = make_dataset(bi, "Raw", "raw")["id"]
    return bi


def ask(lab, model, features, target="", ds=None, **more):
    names = [*features, *([target] if target else [])]
    return query(lab, dataset=ds or lab.ds, detail=True, columns=[{"column": n} for n in names],
                 ml={"model": model, "features": features, "target": target, **more})["ml"]


@pytest.mark.parametrize("model", ["linear", "ridge", "poly", "tree", "forest", "knn"])
def test_regression_models(lab, model):
    out = ask(lab, model, ["Qty", "Noise", "Branch"], "Amount", predict={"Qty": 10, "Noise": 0.5, "Branch": "LHR"})
    assert out["task"] == "regression" and out["metrics"]["r2"] > 0.85, (model, out["metrics"])
    assert out["importance"][0]["name"] == "Qty" and abs(out["prediction"]["value"] - 170) < 60
    if model == "linear":
        assert abs(next(c for c in out["coefficients"] if c["name"] == "Qty")["perUnit"] - 12) < 0.5


@pytest.mark.parametrize("model", ["logistic", "tree", "forest", "knn"])
def test_classification_models(lab, model):
    out = ask(lab, model, ["Qty", "Branch"], "Tier", predict={"Qty": 35, "Branch": "KHI"})
    assert out["task"] == "classification" and out["metrics"]["accuracy"] > 0.9, (model, out["metrics"])
    assert out["prediction"]["value"] == "gold" and sum(map(sum, out["chart"]["matrix"])) == out["testRows"]


def test_groups_anomalies_correlation_and_forecast(lab):
    groups = ask(lab, "kmeans", ["Qty", "Amount"], params={"clusters": 3})
    assert groups["metrics"]["groups"] == 3 and sum(row[1] for row in groups["table"]["rows"]) == 399
    assert ask(lab, "pca", ["Qty", "Amount", "Noise"])["metrics"]["explained"] > 0.6
    odd = ask(lab, "anomaly", ["Amount"], ds=lab.raw, params={"sensitivity": 6})
    assert odd["metrics"]["found"] == 1 and odd["table"]["rows"][0][2] == 90_000
    corr = ask(lab, "correlation", ["Qty", "Amount", "Noise"])
    assert corr["table"]["rows"][0][:2] == ["Qty", "Amount"] and corr["metrics"]["strongest"] > 0.95
    ahead = query(lab, dataset=lab.ds, groupBy=[{"column": "Day", "bucket": "month"}], values=[{"column": "Qty", "agg": "sum"}],
                  ml={"model": "forecast", "params": {"horizon": 4, "bucket": "month"}})["ml"]
    assert len(ahead["table"]["rows"]) == 4 and ahead["table"]["rows"][0][0] == "2026-04"
    assert all(row[2] <= row[1] <= row[3] for row in ahead["table"]["rows"])


def test_it_says_what_is_wrong(lab):
    def error(**visual):
        response = lab.post("/api/analytics/query", json={"dataset": lab.ds, "detail": True, **visual})
        assert response.status_code == 422
        return response.json()["detail"]
    cols = [{"column": "Qty"}, {"column": "Tier"}]
    assert "not a number" in error(columns=cols, ml={"model": "linear", "features": ["Qty"], "target": "Tier"})
    assert "categories" in error(columns=[{"column": "Qty"}, {"column": "Noise"}], ml={"model": "logistic", "features": ["Qty"], "target": "Noise"})
    assert "must be one of" in error(columns=cols, ml={"model": "magic", "features": ["Qty"], "target": "Tier"})
    assert "choose the column" in error(columns=cols, ml={"model": "tree", "features": ["Qty"]})


def test_labels_after_the_last_period():
    assert ml._next_labels(["2025-11", "2025-12"], 2) == ["2026-01", "2026-02"]
    assert ml._next_labels(["2025-Q4"], 2) == ["2026-Q1", "2026-Q2"]
    assert ml._next_labels(["2025-12-22", "2025-12-29"], 1) == ["2026-01-05"] and ml._next_labels(["2025"], 1) == ["2026"]


def test_an_ml_tile_is_saved_and_answered_in_a_dashboard(lab):
    tile = {"id": "m1", "type": "ml", "title": "Model", "dataset": lab.ds, "detail": True, "columns": [{"column": "Qty"}, {"column": "Amount"}],
            "ml": {"model": "linear", "features": ["Qty"], "target": "Amount"}, "x": 0, "y": 0, "w": 6, "h": 6}
    made = lab.post("/api/analytics/dashboards", json={"name": "ML", "tiles": [tile]})
    assert made.status_code == 201, made.text
    saved = made.json()["dashboard"]["tiles"][0]
    assert saved["ml"]["model"] == "linear"
    results = lab.post("/api/analytics/query-batch", json={"queries": [{**saved, "tileId": "m1"}],
                                                           "filters": [{"dataset": lab.ds, "column": "Branch", "op": "in", "values": ["LHR"]}]}).json()["results"]
    assert results[0]["ml"]["rows"] < 399 and results[0]["ml"]["chart"]["kind"] == "fit"


# ------------------------------------------------------------------ embedding
def test_a_dashboard_in_another_website(lab):
    tiles = [{"id": "c1", "type": "column", "dataset": lab.ds, "groupBy": [{"column": "Branch"}], "values": [{"column": "Qty", "agg": "sum"}],
              "x": 0, "y": 0, "w": 6, "h": 5},
             {"id": "s1", "type": "slicer", "dataset": lab.ds, "groupBy": [{"column": "Tier"}], "x": 6, "y": 0, "w": 3, "h": 5}]
    dash = lab.post("/api/analytics/dashboards", json={"name": "Sales", "folder": "Sales", "tiles": tiles}).json()["dashboard"]
    hidden = lab.post("/api/analytics/dashboards", json={"name": "HR", "folder": "HR", "tiles": []}).json()["dashboard"]
    lab.cookies.clear()
    key = lab.post("/api/admin/api-keys", headers=ADMIN_HEADERS, json={"name": "Portal", "folders": ["Sales"]}).json()
    head = {"X-API-Key": key["secret"]}
    assert lab.post("/api/v1/embed-tokens", headers=head, json={"dashboard": dash["id"]}).status_code == 422      # no website listed yet
    lab.put(f"/api/admin/api-keys/{key['key']['id']}", headers=ADMIN_HEADERS, json={"embedOrigins": ["https://portal.example.com"]})
    assert lab.post("/api/v1/embed-tokens", headers=head, json={"dashboard": hidden["id"]}).status_code == 404
    issued = lab.post("/api/v1/embed-tokens", headers=head, json={"dashboard": dash["id"], "filters": [{"column": "Branch", "op": "in", "values": ["LHR"]}]})
    assert issued.status_code == 200, issued.text
    token = {"X-Embed-Token": issued.json()["token"]}
    page = lab.get(issued.json()["viewUrl"])
    assert page.status_code == 200 and "frame-ancestors https://portal.example.com" in page.headers["content-security-policy"]
    assert "x-frame-options" not in page.headers
    view = lab.get("/api/embed/dashboard", headers=token).json()["dashboard"]
    assert [t["id"] for t in view["tiles"]] == ["c1", "s1"] and "owner" not in view

    def ask_embed(**body):
        return lab.post("/api/embed/dashboard/query", headers=token, json=body)
    results = ask_embed(tiles=["c1", "s1"]).json()["results"]
    assert [row[0] for row in results[0]["rows"]] == ["LHR"] and {row[0] for row in results[1]["rows"]} == {"gold", "silver"}
    everything = results[0]["rows"][0][1]
    gold = ask_embed(tiles=["c1"], filters=[{"dataset": lab.ds, "column": "Tier", "op": "in", "values": ["gold"], "fromTile": "s1"}]).json()["results"]
    assert gold[0]["rows"][0][1] < everything
    # neither the fixed filter nor other columns can be reached by the visitor
    other = ask_embed(tiles=["c1"], filters=[{"dataset": lab.ds, "column": "Noise", "op": "gt", "values": [0.5]}]).json()["results"]
    assert other[0]["rows"][0][1] == everything
    assert [r[0] for r in ask_embed(tiles=["c1"], filters=[{"dataset": lab.ds, "column": "Branch", "op": "in", "values": ["LHR", "KHI"], "fromTile": "c1"}])
            .json()["results"][0]["rows"]] == ["LHR"]
    assert ask_embed(tiles=["nope"]).status_code == 422
    assert lab.get("/api/embed/report", headers=token).status_code == 404
    lab.put(f"/api/admin/api-keys/{key['key']['id']}", headers=ADMIN_HEADERS, json={"disabled": True})
    assert ask_embed(tiles=["c1"]).status_code == 401
