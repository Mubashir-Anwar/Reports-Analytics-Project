"""Sub-datasets (Jasper subDataset + datasetRun): a Table or Crosstab with its own query, fields and parameters."""
import csv
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.connectors import sql as sql_connector  # noqa: E402
from backend.app.rendering import engine as dr  # noqa: E402
from test_workspace_and_server import client, token  # noqa: E402,F401

COLUMNS = ["Branch", "Total"]
ROWS = [{"Branch": "KHI", "Total": 30}, {"Branch": "LHR", "Total": 70}]
DATASET = {"name": "Orders", "queryLanguage": "sql", "querySource": "SELECT item, qty FROM orders WHERE branch = $P{Branch}",
           "fields": ["item", "qty"], "parameters": [{"name": "Branch", "type": "string"}, {"name": "Year", "type": "integer"}]}
TABLE = {"type": "table", "x": 0, "y": 0, "width": 200, "height": 50, "datasetName": "Orders",
         "columns": [{"header": "Item", "expression": "$F{item}", "width": 120},
                     {"header": "Qty", "expression": "$F{qty}", "width": 80, "footer": "Sum"}]}


def design(table=TABLE, detail=False, datasets=(DATASET,)):
    band = {"height": 50, "elements": [table]}
    bands = {"detail": band} if detail else {"summary": band}
    return dr.validate_design({"page": {"sheetLayout": "grid"}, "datasets": list(datasets), "bands": bands}, COLUMNS, {"Year"})


def csv_lines(validated, loader, params=None):
    token_ = dr.DATASET_LOADER.set(loader)
    try:
        text = dr.render_design_csv(validated, "T", COLUMNS, ROWS, params or {}).decode("utf-8-sig")
    finally:
        dr.DATASET_LOADER.reset(token_)
    return [[c for c in line if c != ""] for line in csv.reader(io.StringIO(text))]


def test_table_reads_its_own_dataset_with_mapped_and_report_parameters():
    calls = []

    def loader(dataset, values):
        calls.append((dataset["name"], values))
        return [{"item": f"{values['Branch']}-pen", "qty": 2, "other": "x"}, {"item": f"{values['Branch']}-ink", "qty": 3}]
    table = {**TABLE, "datasetParameters": [{"name": "Branch", "expression": "$F{Branch}"}]}
    lines = csv_lines(design(table, detail=True), loader, {"Year": 2025})
    assert ["KHI-pen", "2"] in lines and ["LHR-ink", "3"] in lines
    assert lines.count(["5"]) == 2  # the Sum footer of each record's table
    # Branch is mapped from the current record; Year is not mapped, so it comes from the report parameter
    assert calls == [("Orders", {"Branch": "KHI", "Year": 2025}), ("Orders", {"Branch": "LHR", "Year": 2025})]


def test_without_a_loader_the_designer_shows_sample_records():
    lines = csv_lines(design(), None)
    assert lines[0] == ["Item", "Qty"] and len(lines) >= 7


def test_a_failing_dataset_prints_a_message_instead_of_failing_the_report():
    def loader(dataset, values):
        raise ValueError("relation orders does not exist")
    text = " ".join(" ".join(line) for line in csv_lines(design(), loader))
    assert "Dataset Orders: relation orders does not exist" in text


def test_validation_uses_the_dataset_fields():
    with pytest.raises(ValueError, match="does not exist"):
        design({**TABLE, "datasetName": "Missing"})
    with pytest.raises(ValueError, match=r"\$F\{Branch\}"):  # a main-report field is not a field of the dataset
        design({**TABLE, "columns": [{"header": "B", "expression": "$F{Branch}", "width": 50}]})
    with pytest.raises(ValueError, match="dataset's parameters"):
        design({**TABLE, "datasetParameters": [{"name": "Nope", "expression": "$F{Branch}"}]})
    with pytest.raises(ValueError, match="unique name"):
        design(datasets=(DATASET, DATASET))
    with pytest.raises(ValueError, match="fields"):
        design(datasets=({**DATASET, "fields": []},))
    cross = {"type": "crosstab", "x": 0, "y": 0, "width": 300, "height": 50, "datasetName": "Orders",
             "rowField": "item", "columnField": "item", "measureField": "qty"}
    assert design(cross)["bands"]["summary"]["elements"][0]["datasetName"] == "Orders"


def test_sub_dataset_through_publish_and_generate(client, monkeypatch):
    monkeypatch.setattr(sql_connector, "test", lambda kind, url: {"ok": True, "message": "ok", "database": "shop"})

    def run_query(kind, url, prepared, values, *, max_rows, timeout_ms):
        if "FROM orders" in prepared.source:
            return [{"item": f"pen-{values.get('Branch')}", "qty": 4}]
        return [{"Branch": "KHI", "Total": 30}]
    monkeypatch.setattr(sql_connector, "run_query", run_query)
    admin = token("admin-token-for-tests-0123456789")
    assert client.post("/api/admin/connections", headers=admin,
                       json={"type": "postgresql", "name": "Shop PG", "uri": "postgresql://r:pw@db.local/shop"}).status_code == 200
    table = {**TABLE, "datasetParameters": [{"name": "Branch", "expression": "$F{Branch}"}]}
    report = {"id": "SUBDS", "title": "Sub datasets", "folder": "General", "connectionId": "shop_pg", "queryLanguage": "sql",
              "querySource": "SELECT branch AS \"Branch\", total AS \"Total\" FROM branches", "columns": COLUMNS,
              "parameters": [{"name": "Year", "type": "integer"}], "formats": ["csv", "pdf"],
              "design": {"page": {"sheetLayout": "grid"}, "datasets": [DATASET], "bands": {"detail": {"height": 50, "elements": [table]}}}}
    assert client.put("/api/admin/workspace/SUBDS?create=true", headers=token(), json=report).status_code == 200
    assert client.post("/api/admin/workspace/SUBDS/publish", headers=token(), json={}).status_code == 200
    out = client.get("/generate/SUBDS.csv?Year=2025", headers=admin)
    assert out.status_code == 200 and "pen-KHI" in out.text

    # a dataset query is checked like a report query: data-changing SQL is refused when saving
    bad = {**report, "id": "SUBDS_BAD", "design": {**report["design"], "datasets": [{**DATASET, "querySource": "DELETE FROM orders"}]}}
    assert client.put("/api/admin/workspace/SUBDS_BAD?create=true", headers=token(), json=bad).status_code == 200  # a draft may be unfinished
    refused = client.post("/api/admin/workspace/SUBDS_BAD/publish", headers=token(), json={})
    assert refused.status_code == 422 and "Dataset Orders" in refused.text
