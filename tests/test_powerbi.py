"""Power BI templates (.pbit) → Analytics dashboards: reading both report formats, matching tables to datasets,
converting visuals, measures, date levels and relationships, and refusing what cannot be read."""
import base64
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_access_rights import make_user, sign_in  # noqa: E402
from test_analytics import bi, make_dataset  # noqa: E402,F401
from test_workspace_and_server import client  # noqa: E402,F401

MODEL = {"model": {
    "tables": [
        {"name": "Orders", "columns": [
            {"name": "Branch", "dataType": "string"}, {"name": "Amount", "dataType": "double", "summarizeBy": "sum"},
            {"name": "Date", "dataType": "dateTime"}, {"type": "rowNumber", "name": "RowNumber-2662979B"}],
         "measures": [{"name": "Total Sales", "expression": "SUM ( Orders[Amount] )"},
                      {"name": "Orders count", "expression": ["COUNTROWS('Orders')"]},
                      {"name": "Fancy", "expression": "CALCULATE ( SUM ( Orders[Amount] ), ALL ( Orders ) )"}],
         "partitions": [{"source": {"type": "m", "expression": [
             "let", '    Source = Sql.Database("server", "sales"),', '    t = Source{[Schema="dbo",Item="orders"]}[Data]', "in t"]}}]},
        {"name": "Branches", "columns": [{"name": "Code", "dataType": "string"}, {"name": "Region", "dataType": "string"}]},
        {"name": "LocalDateTable_1234", "columns": [{"name": "Date", "dataType": "dateTime"}]},
    ],
    "relationships": [{"fromTable": "Orders", "fromColumn": "Branch", "toTable": "Branches", "toColumn": "code"},
                      {"fromTable": "Orders", "fromColumn": "Date", "toTable": "LocalDateTable_1234", "toColumn": "Date"}],
}}


def col(table, name):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def measure(table, name):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def total(table, name, function=0):
    return {"Aggregation": {"Expression": col(table, name), "Function": function}}


def month(table, name):
    return {"HierarchyLevel": {"Expression": {"Hierarchy": {"Expression": {"PropertyVariationSource": {
        "Expression": {"SourceRef": {"Entity": table}}, "Name": "Variation", "Property": name}}, "Hierarchy": "Date Hierarchy"}},
        "Level": "Month"}}


def title(text):
    return {"title": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{text}'"}}}}}]}


PAGES = [
    ("Overview", [
        ("card", {"Values": [measure("Orders", "Total Sales")]}, (0, 0, 320, 120), "Sales"),
        ("clusteredColumnChart", {"Category": [col("Branches", "Region")], "Y": [total("Orders", "Amount")]}, (320, 0, 640, 300), ""),
        ("slicer", {"Values": [col("Orders", "Branch")]}, (960, 0, 320, 300), ""),
        ("lineChart", {"Category": [month("Orders", "Date")], "Y": [measure("Orders", "Fancy")]}, (0, 320, 640, 300), "Trend"),
        ("map", {"Category": [col("Branches", "Region")]}, (640, 320, 640, 300), "Where"),
        ("textbox", {}, (0, 640, 300, 60), ""),
    ]),
    ("Detail", [
        ("tableEx", {"Values": [col("Orders", "Branch"), col("Orders", "Amount")]}, (0, 0, 1280, 400), "All orders"),
        ("areaChart", {"Category": [month("Orders", "Date")], "Y": [measure("Orders", "Orders count")]}, (0, 400, 1280, 300), ""),
    ]),
]


def utf16(value):
    return json.dumps(value).encode("utf-16-le")


def legacy_layout():
    sections = []
    for name, visuals in PAGES:
        containers = []
        for kind, roles, (x, y, w, h), text in visuals:
            selects, projections = [], {}
            for role, fields in roles.items():
                for number, field in enumerate(fields):
                    ref = f"{role}{number}"
                    legacy = json.loads(json.dumps(field).replace('"Entity": "Orders"', '"Source": "o"').replace('"Entity": "Branches"', '"Source": "b"'))
                    selects.append({**legacy, "Name": ref})
                    projections.setdefault(role, []).append({"queryRef": ref})
            single = {"visualType": kind, "projections": projections,
                      "prototypeQuery": {"Version": 2, "From": [{"Name": "o", "Entity": "Orders"}, {"Name": "b", "Entity": "Branches"}],
                                         "Select": selects}}
            if text:
                single["vcObjects"] = title(text)
            containers.append({"x": x, "y": y, "width": w, "height": h, "config": json.dumps({"name": kind, "singleVisual": single})})
        sections.append({"displayName": name, "width": 1280, "height": 720, "visualContainers": containers})
    return {"sections": sections}


def pbit(*, pbir=False, data_model=True, pages=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("Version", "1.28".encode("utf-16-le"))
        if data_model:
            archive.writestr("DataModelSchema", utf16(MODEL))
        else:
            archive.writestr("DataModel", b"\x00binary")
        if not pbir:
            archive.writestr("Report/Layout", utf16(legacy_layout()))
        else:
            chosen = PAGES if pages is None else [PAGES[i] for i in pages]
            root = "Report/definition/pages/"
            archive.writestr(root + "pages.json", json.dumps({"pageOrder": [f"p{i}" for i in range(len(chosen))]}))
            for number, (name, visuals) in enumerate(chosen):
                archive.writestr(f"{root}p{number}/page.json", json.dumps({"displayName": name, "width": 1280, "height": 720}))
                for v_number, (kind, roles, (x, y, w, h), text) in enumerate(visuals):
                    state = {role: {"projections": [{"field": field, "queryRef": f"{role}{i}"} for i, field in enumerate(fields)]}
                             for role, fields in roles.items()}
                    visual = {"visualType": kind, "query": {"queryState": state}}
                    if text:
                        visual["visualContainerObjects"] = title(text)
                    archive.writestr(f"{root}p{number}/visuals/v{v_number}/visual.json", json.dumps(
                        {"name": f"v{v_number}", "position": {"x": x, "y": y, "width": w, "height": h}, "visual": visual}))
    return base64.b64encode(out.getvalue()).decode()


def test_inspect_and_import_a_template(bi):
    branches = make_dataset(bi, "branches", "branches")["id"]           # matched to the table Branches by name
    content = pbit()
    seen = bi.post("/api/analytics/powerbi/inspect", json={"content": content, "name": "Sales.pbit"})
    assert seen.status_code == 200, seen.text
    info = seen.json()
    assert info["name"] == "Sales" and [p["name"] for p in info["pages"]] == ["Overview", "Detail"]
    overview = info["pages"][0]
    assert overview["visuals"] == 3 and overview["decorative"] == 1
    assert any("map" in note for note in overview["notes"]) and any("Fancy" in note for note in overview["notes"])
    by_table = {t["name"]: t for t in info["tables"]}
    assert set(by_table) == {"Orders", "Branches"}                      # no automatic date table
    assert by_table["Branches"]["suggestion"] == {"dataset": branches}
    assert by_table["Orders"]["source"] == {"kind": "SQL Server", "language": "sql", "item": "dbo.orders"}
    assert by_table["Orders"]["suggestion"]["collection"] == "orders"   # this server only has a MongoDB adapter
    assert {m["name"]: m["converted"] for m in by_table["Orders"]["measures"]} == {"Total Sales": True, "Orders count": True, "Fancy": False}
    assert info["relationships"] == 1

    tables = {"Orders": by_table["Orders"]["suggestion"], "Branches": {"dataset": branches}}
    made = bi.post("/api/analytics/powerbi/import", json={"content": content, "name": "Sales.pbit", "tables": tables, "folder": "PBI"})
    assert made.status_code == 201, made.text
    result = made.json()
    assert [d["name"] for d in result["dashboards"]] == ["Sales – Overview", "Sales – Detail"]
    assert len(result["created"]) == 1 and result["reused"] == [branches] and result["relationships"] == 1
    orders = result["created"][0]
    boards = [bi.get(f"/api/analytics/dashboards/{d['id']}").json()["dashboard"] for d in result["dashboards"]]
    assert all(board["folder"] == "PBI" for board in boards)
    card, column, slicer = boards[0]["tiles"]
    assert (card["type"], card["title"], card["values"]) == ("kpi", "Sales", [{"column": "Amount", "agg": "sum", "label": "Total Sales"}])
    assert column["type"] == "column" and column["dataset"] == orders
    assert column["groupBy"] == [{"column": "Region", "dataset": branches}]                  # the related table
    assert (column["x"], column["w"]) == (3, 6) and slicer["type"] == "slicer" and slicer["x"] == 9
    table, area = boards[1]["tiles"]
    assert table["detail"] is True and [c["column"] for c in table["columns"]] == ["Branch", "Amount"]
    assert area["groupBy"] == [{"column": "Date", "bucket": "month"}] and area["values"][0]["agg"] == "countrows"

    # the new dataset was imported, and the visual across the relationship answers
    answer = bi.post("/api/analytics/query", json={k: column[k] for k in ("dataset", "groupBy", "values")}).json()
    assert sorted(answer["rows"]) == [["Capital", 25], ["Punjab", 150.5], ["Sindh", 200]]
    assert bi.post("/api/analytics/query", json={k: area[k] for k in ("dataset", "groupBy", "values")}).json()["rows"] == \
        [["2026-01", 2], ["2026-02", 1], ["2026-03", 1]]
    log = bi.get(f"/api/analytics/datasets/{orders}/refreshes").json()["refreshes"]
    assert log[0]["trigger"] == "upload" and log[0]["status"] == "success"


def test_pbir_format_one_page_and_a_skipped_table(bi):
    content = pbit(pbir=True, pages=[0])
    info = bi.post("/api/analytics/powerbi/inspect", json={"content": content, "name": "New.pbit"}).json()
    assert [p["name"] for p in info["pages"]] == ["Overview"] and info["pages"][0]["visuals"] == 3
    tables = {"Orders": {"connectionId": "default", "queryLanguage": "aggregation-json", "collection": "orders",
                         "querySource": "[]"}, "Branches": {"skip": True}}
    made = bi.post("/api/analytics/powerbi/import", json={"content": content, "name": "New.pbit", "tables": tables, "pages": [0]})
    assert made.status_code == 201, made.text
    result = made.json()
    assert len(result["dashboards"]) == 1 and result["relationships"] == 0
    assert any("Branches has no dataset" in note for note in result["notes"])           # the column chart is left out
    board = bi.get(f"/api/analytics/dashboards/{result['dashboards'][0]['id']}").json()["dashboard"]
    assert [tile["type"] for tile in board["tiles"]] == ["kpi", "slicer"]


def test_columns_missing_in_the_chosen_dataset(bi):
    branches = make_dataset(bi, "Regions", "branches")["id"]
    orders = make_dataset(bi, "Orders", "orders")["id"]
    bi.sources["orders"] = [{"Branch": "LHR", "Value": 1}]
    assert bi.post(f"/api/analytics/datasets/{orders}/refresh", json={}).status_code in (200, 202)
    made = bi.post("/api/analytics/powerbi/import", json={"content": pbit(), "name": "x.pbit", "pages": [1],
                                                          "tables": {"Orders": {"dataset": orders}, "Branches": {"dataset": branches}}})
    assert made.status_code == 201, made.text
    notes = made.json()["notes"]
    assert any("no column 'Amount'" in note for note in notes)
    board = bi.get(f"/api/analytics/dashboards/{made.json()['dashboards'][0]['id']}").json()["dashboard"]
    assert [c["column"] for c in board["tiles"][0]["columns"]] == ["Branch"]


def test_files_that_are_not_templates(bi):
    def refused(content, word, path="inspect"):
        response = bi.post(f"/api/analytics/powerbi/{path}", json={"content": content, "name": "x"})
        assert response.status_code == 422 and word in response.json()["detail"], response.text
    refused(pbit(data_model=False), "File → Export → Power BI template")
    refused(base64.b64encode(b"hello").decode(), "not a Power BI template")
    refused("%%%", "not base64")
    refused(base64.b64encode(b"PK\x03\x04broken").decode(), "damaged")
    response = bi.post("/api/analytics/powerbi/import", json={"content": pbit(), "pages": [], "tables": {}})
    assert response.status_code == 422 and "at least one page" in response.json()["detail"]
    response = bi.post("/api/analytics/powerbi/import", json={"content": pbit(), "tables": {}})
    assert response.status_code == 422 and "No visual" in response.json()["detail"]
    assert bi.get("/api/analytics/dashboards").json()["dashboards"] == []               # nothing saved

    make_user(bi, "vera", ["analytics"])                                                # may look, not model
    sign_in(bi, "vera")
    assert bi.post("/api/analytics/powerbi/inspect", json={"content": pbit()}).status_code == 403


def pbix_parts(pbir=False):
    """What the browser sends for a .pbix: only the report parts, unpacked, as base64 (never the data model or data)."""
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(pbit(pbir=pbir))))
    return {name: base64.b64encode(archive.read(name)).decode() for name in archive.namelist()
            if name == "Report/Layout" or name.startswith("Report/definition/")}


def test_pbix_report_part(bi):
    orders = make_dataset(bi, "Orders", "orders")["id"]
    branches = make_dataset(bi, "Branches", "branches")["id"]
    for pbir in (False, True):
        info = bi.post("/api/analytics/powerbi/inspect", json={"parts": pbix_parts(pbir), "name": "Sales.pbix"})
        assert info.status_code == 200, info.text
        info = info.json()
        assert info["kind"] == "pbix" and info["name"] == "Sales" and info["relationships"] == 0
        by_table = {t["name"]: t for t in info["tables"]}
        # tables and columns come from the visuals' fields; measures are named but have no DAX
        assert {c["name"]: c["kind"] for c in by_table["Orders"]["columns"]} == {"Amount": "number", "Branch": "text", "Date": "date"}
        assert all(not m["converted"] for m in by_table["Orders"]["measures"])
        assert by_table["Orders"]["suggestion"] == {"dataset": orders}
        assert info["pages"][0]["visuals"] == 2                            # the card on a measure is left out

    # with a relationship made here, the visual across two tables works
    assert bi.post("/api/analytics/relationships", json={"fromDataset": orders, "fromColumn": "Branch",
                                                         "toDataset": branches, "toColumn": "Code"}).status_code == 201
    made = bi.post("/api/analytics/powerbi/import", json={"parts": pbix_parts(), "name": "Sales.pbix", "pages": [0, 1],
                                                          "tables": {"Orders": {"dataset": orders}, "Branches": {"dataset": branches}}})
    assert made.status_code == 201, made.text
    result = made.json()
    assert [d["name"] for d in result["dashboards"]] == ["Sales – Overview", "Sales – Detail"] and result["created"] == []
    board = bi.get(f"/api/analytics/dashboards/{result['dashboards'][0]['id']}").json()["dashboard"]
    column = board["tiles"][0]
    assert column["type"] == "column" and column["groupBy"] == [{"column": "Region", "dataset": branches}]
    answer = bi.post("/api/analytics/query", json={k: column[k] for k in ("dataset", "groupBy", "values")}).json()
    assert sorted(answer["rows"]) == [["Capital", 25], ["Punjab", 150.5], ["Sindh", 200]]


def test_pbix_parts_are_checked(bi):
    def refused(parts, word):
        response = bi.post("/api/analytics/powerbi/inspect", json={"parts": parts, "name": "x.pbix"})
        assert response.status_code == 422 and word in response.json()["detail"], response.text
    refused({"DataModel": base64.b64encode(b"x").decode()}, "Only the report parts")
    refused({"Report/Layout": "%%%"}, "not base64")
    refused({"Report/Layout": base64.b64encode(b"not json").decode()}, "could not be read")
    refused({"Report/Layout": base64.b64encode(json.dumps({"sections": []}).encode("utf-16-le")).decode()}, "no report pages")
    refused({}, "report parts")
