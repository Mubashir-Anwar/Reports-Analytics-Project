"""Read fallback: no silent wrong data, $unionWith/$replaceRoot, clear access errors.
Designer preview: PDF, Excel and CSV formats; Excel stays on one sheet."""
import io
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook
from pymongo.errors import OperationFailure

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import designer_render as dr  # noqa: E402
import reporting_core as core  # noqa: E402


class _Cursor(list):
    def limit(self, n):
        return _Cursor(self[:n])


class FakeDatabase:
    def __init__(self, collections, denied=()):
        self.name = "db"
        self.collections = collections
        self.denied = set(denied)

    def __getitem__(self, name):
        return FakeCollection(self, name)


class FakeCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    def find(self, query=None, projection=None, max_time_ms=None):
        if self.name in self.database.denied:
            raise OperationFailure("not authorized", code=13)
        docs = [dict(doc) for doc in self.database.collections.get(self.name, [])]
        if query:
            docs = [doc for doc in docs if core._matches_document(doc, query)]
        return _Cursor(docs)

    def aggregate(self, *_args, **_kwargs):
        raise OperationFailure("not authorized on db to execute command aggregate", code=13)


DB = {
    "Main": [{"_id": 1, "Name": "a", "extra": {"Score": 5}}],
    "Other": [{"_id": 2, "Name": "b", "extra": {"Score": 7}}],
}


def run(pipeline, denied=()):
    collection = FakeDatabase(DB, denied)["Main"]
    return core._execute_report_pipeline(collection, pipeline, max_time_ms=1000, allow_disk_use=False, max_rows=100)


def test_union_with_and_replace_root():
    rows = run([
        {"$unionWith": "Other"},
        {"$unionWith": {"coll": "Other", "pipeline": [{"$match": {"Name": "b"}}]}},
        {"$replaceRoot": {"newRoot": {"$mergeObjects": ["$$ROOT", "$extra"]}}},
        {"$project": {"Name": 1, "Score": 1, "_id": 0}},
    ])
    assert rows == [{"Name": "a", "Score": 5}, {"Name": "b", "Score": 7}, {"Name": "b", "Score": 7}]


def test_unknown_expression_operator_is_refused_not_silently_wrong():
    with pytest.raises(core.MongoReadAccessError, match=r"\$cond"):
        run([{"$addFields": {"Flag": {"$cond": [{"$gt": ["$_id", 0]}, "yes", "no"]}}}])
    # direct evaluation also refuses instead of returning {"$cond": ...} as data
    with pytest.raises(ValueError, match="does not support"):
        core._evaluate_expression({"a": 1}, {"$cond": [True, 1, 2]})


def test_unsupported_list_names_every_problem_once():
    problems = core._fallback_unsupported([
        {"$unionWith": {"coll": "Other", "pipeline": [{"$addFields": {"x": {"$map": {"input": "$a", "in": 1}}}}]}},
        {"$group": {"_id": "$a"}},
        {"$match": {"$expr": {"$eq": ["$a", 1]}}},
        {"$lookup": {"from": "Other", "pipeline": [], "as": "x"}},
    ])
    assert {"$map", "$group", "$expr", "$lookup (pipeline form)"} <= set(problems)
    assert len(problems) == len(set(problems))


def test_denied_collections_are_named():
    with pytest.raises(core.MongoReadAccessError) as error:
        run([{"$unionWith": "Other"}, {"$lookup": {"from": "Users", "localField": "a", "foreignField": "b", "as": "u"}}],
            denied={"Other", "Users"})
    message = str(error.value)
    assert "Other" in message and "Users" in message and "find" in message


def test_variables_other_than_root_are_refused():
    with pytest.raises(ValueError, match="variable"):
        core._evaluate_expression({"a": 1}, "$$NOW")
    assert core._evaluate_expression({"a": {"b": 2}}, "$$ROOT.a.b") == 2


def test_literal_is_not_evaluated():
    assert core._evaluate_expression({"a": 1}, {"$literal": {"$cond": 1}}) == {"$cond": 1}
    assert core._fallback_unsupported([{"$addFields": {"x": {"$literal": {"$cond": 1}}}}]) == []


# ---------------------------------------------------------------- designer preview formats
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", "t")
    return TestClient(core.make_app())


DESIGN = {
    "page": {"width": 3000, "height": 600, "ignorePagination": True},
    "bands": {
        "title": {"height": 30, "elements": [{"type": "staticText", "x": 0, "y": 0, "width": 300, "height": 20, "text": "Wide report"}]},
        "columnHeader": {"height": 20, "elements": [{"type": "staticText", "x": i * 100, "y": 0, "width": 100, "height": 18, "text": f"C{i}"} for i in range(25)]},
        "detail": {"height": 16, "elements": [{"type": "textField", "x": i * 100, "y": 0, "width": 100, "height": 14, "expression": f"$F{{C{i}}}"} for i in range(25)]},
    },
}
COLUMNS = [f"C{i}" for i in range(25)]


@pytest.mark.parametrize("fmt, magic", [("pdf", b"%PDF"), ("xlsx", b"PK"), ("csv", "C0".encode())])
def test_designer_render_formats(client, fmt, magic):
    rows = [{c: f"{c}-{r}" for c in COLUMNS} for r in range(120)]
    response = client.post("/api/admin/designer/render", headers={"X-Admin-Token": "t"},
                           json={"design": DESIGN, "columns": COLUMNS, "rows": rows, "format": fmt})
    assert response.status_code == 200, response.text
    assert response.content.lstrip(b"\xef\xbb\xbf").startswith(magic)


def test_excel_is_one_sheet_fitting_one_page_wide(client):
    rows = [{c: f"{c}-{r}" for c in COLUMNS} for r in range(500)]
    response = client.post("/api/admin/designer/render", headers={"X-Admin-Token": "t"},
                           json={"design": DESIGN, "columns": COLUMNS, "rows": rows, "format": "xlsx"})
    book = load_workbook(io.BytesIO(response.content))
    assert len(book.worksheets) == 1
    sheet = book.active
    assert sheet.max_row == 1 + 1 + 500  # title + header + every data row, no page splits
    assert sheet.page_setup.fitToWidth == 1 and sheet.page_setup.fitToHeight == 0
    assert sheet.sheet_properties.pageSetUpPr.fitToPage is True


def test_designer_render_rejects_unknown_format(client):
    response = client.post("/api/admin/designer/render", headers={"X-Admin-Token": "t"},
                           json={"design": DESIGN, "columns": COLUMNS, "format": "docx"})
    assert response.status_code == 422


def test_excel_preview_needs_detail_fields(client):
    design = {"page": {"width": 595, "height": 842}, "bands": {"title": {"height": 30, "elements": []}}}
    response = client.post("/api/admin/designer/render", headers={"X-Admin-Token": "t"},
                           json={"design": design, "columns": ["A"], "format": "xlsx"})
    assert response.status_code == 422 and "Detail" in response.json()["detail"]


def test_plain_xlsx_also_single_sheet_print_setup():
    data = core.render_xlsx("T", "", [], ("A",), [{"A": i} for i in range(50)], show_intro=False)
    sheet = load_workbook(io.BytesIO(data)).active
    assert sheet.page_setup.fitToWidth == 1 and sheet.page_setup.fitToHeight == 0


def test_large_custom_page_is_accepted():
    design = dr.validate_design({**DESIGN, "page": {"width": 14400, "height": 300}}, COLUMNS, set())
    assert design["page"]["width"] == 14400
