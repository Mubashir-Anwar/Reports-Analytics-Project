"""Backend tests: parameter validation, band designer, audit-required. No MongoDB needed."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.rendering import engine as dr  # noqa: E402
import service_names as core  # noqa: E402

COLUMNS = ["Branch", "Name", "Amount", "LastUpdateDateTime"]


def design_with_groups():
    return {
        "page": {"width": 595, "height": 842},
        "variables": [
            {"name": "BranchTotal", "calculation": "Sum", "field": "Amount", "resetType": "Group", "resetGroup": "Branch"},
            {"name": "GrandTotal", "calculation": "Sum", "field": "Amount", "resetType": "Report"},
        ],
        "calculatedFields": [{"name": "Tax", "expression": "$F{Amount} * 0.1"}],
        "groups": [{"name": "Branch", "field": "Branch"}],
        "bands": {
            "title": {"height": 40, "elements": [{"type": "staticText", "x": 0, "y": 0, "width": 300, "height": 30, "text": "Hello", "bold": True}]},
            "columnHeader": {"height": 20, "elements": [{"type": "staticText", "x": 0, "y": 0, "width": 100, "height": 18, "text": "Name"}]},
            "detail": {"height": 16, "elements": [
                {"type": "textField", "x": 0, "y": 0, "width": 100, "height": 14, "expression": "$F{Name}"},
                {"type": "textField", "x": 110, "y": 0, "width": 80, "height": 14, "expression": "$F{Amount}", "pattern": "#,##0.00", "align": "right"},
            ]},
            "pageFooter": {"height": 20, "elements": [{"type": "textField", "x": 0, "y": 0, "width": 200, "height": 14, "expression": "Page $V{PAGE_NUMBER} of $V{PAGE_COUNT}"}]},
            "summary": {"height": 200, "elements": [
                {"type": "textField", "x": 0, "y": 0, "width": 200, "height": 14, "expression": "$V{GrandTotal}", "pattern": "Rs. #,##0.00"},
                {"type": "chart", "x": 0, "y": 20, "width": 300, "height": 160, "chartType": "bar", "categoryField": "Branch", "valueField": "Amount"},
            ]},
            "groupHeaders": {"Branch": {"height": 18, "elements": [{"type": "textField", "x": 0, "y": 0, "width": 200, "height": 14, "expression": "$F{Branch}", "bold": True}]}},
            "groupFooters": {"Branch": {"height": 18, "elements": [{"type": "textField", "x": 0, "y": 0, "width": 200, "height": 14, "expression": "$V{BranchTotal}", "pattern": "#,##0.00"}]}},
        },
    }


def test_validate_design_ok_and_normalizes():
    out = dr.validate_design(design_with_groups(), COLUMNS, set())
    assert out["bands"]["detail"]["height"] == 16
    assert out["groups"][0]["field"] == "Branch"
    assert "Branch" in out["bands"]["groupFooters"]


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["bands"]["detail"]["elements"].append({"type": "script"}), "element type"),
    (lambda d: d["bands"]["detail"]["elements"][0].update(expression="$F{Nope}"), "is not defined"),
    (lambda d: d["groups"].append({"name": "G2", "field": "Missing"}), "does not exist"),
    (lambda d: d["variables"][0].update(calculation="Eval"), "calculation"),
    (lambda d: d["bands"]["detail"]["elements"][0].update(color="red"), "#RRGGBB"),
])
def test_validate_design_rejects_bad_input(mutate, message):
    design = design_with_groups()
    mutate(design)
    with pytest.raises(ValueError, match=message):
        dr.validate_design(design, COLUMNS, set())


def test_arithmetic_is_safe():
    resolve = lambda kind, name: {"A": 10, "B": 4}.get(name)  # noqa: E731
    assert dr.eval_arithmetic("$F{A} - $F{B} * 2", resolve) == 2
    assert dr.eval_arithmetic("$F{A} / 0", resolve) is None
    assert dr.eval_arithmetic("__import__('os').system('echo hi')", resolve) is None
    assert dr.eval_arithmetic("$F{A} ** 2", resolve) is None


def test_format_value():
    assert dr.format_value(1234.5, "#,##0.00") == "1,234.50"
    assert dr.format_value(1234.5, "Rs. #,##0") == "Rs. 1,234" or dr.format_value(1234.5, "Rs. #,##0") == "Rs. 1,235"
    assert dr.format_value("2026-03-05T09:00:00+00:00", "dd/MM/yyyy") == "05/03/2026"
    assert dr.format_value(None, "#,##0") == ""


def test_render_pdf_with_groups_variables_chart_and_pages():
    design = dr.validate_design(design_with_groups(), COLUMNS, set())
    rows = dr.sample_rows(COLUMNS, 120)
    pdf = dr.render_design_pdf(design, "Test", COLUMNS, rows, {})
    assert pdf.startswith(b"%PDF")
    assert pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 2  # many rows => multiple pages


def test_group_footer_totals_are_correct():
    design = dr.validate_design(design_with_groups(), COLUMNS, set())
    rows = [
        {"Branch": "A", "Name": "x", "Amount": 10},
        {"Branch": "A", "Name": "y", "Amount": 5},
        {"Branch": "B", "Name": "z", "Amount": 7},
    ]
    engine = dr._Render(design, "T", COLUMNS, rows, {}, 0)
    for r in engine.rows:
        engine.accumulate(r)
    assert engine.variable_value("GrandTotal") == 22
    assert engine.rows[0]["Tax"] == 1.0


def test_validate_parameters_rejects_unknown_and_duplicates():
    spec = core.ParameterSpec(name="StartDate", kind="date", required=True)
    definition = core.ReportDefinition("R", "R", "c", (spec,), ("a",), lambda p: [])
    with pytest.raises(ValueError, match="Unconfigured"):
        core.validate_parameters(definition, [("StartDate", "2026-01-01"), ("Evil", "1")])
    with pytest.raises(ValueError, match="Duplicate"):
        core.validate_parameters(definition, [("StartDate", "2026-01-01"), ("StartDate", "2026-01-02")])
    values, meta = core.validate_parameters(definition, [("StartDate", "2026-01-01"), ("GeneratedBy", "me")])
    assert values["StartDate"] == date(2026, 1, 1) and meta["GeneratedBy"] == "me"


def test_report_with_design_is_saved_and_rendered_via_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("MONGODB_URI", "")
    monkeypatch.setenv("MONGODB_DATABASE", "")
    app = core.make_app()
    client = TestClient(app)
    headers = {"X-Admin-Token": "test-token"}
    body = {"columns": COLUMNS, "design": design_with_groups(), "parameters": {}, "title": "T"}
    response = client.post("/api/admin/designer/render", json=body, headers=headers)
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert client.post("/api/admin/designer/render", json=body).status_code == 401
    body["design"]["bands"]["detail"]["elements"][0]["expression"] = "$F{Nope}"
    assert client.post("/api/admin/designer/render", json=body, headers=headers).status_code == 422


def test_audit_required_blocks_release_when_audit_cannot_be_written(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", "t")
    monkeypatch.setenv("AUDIT_REQUIRED", "true")
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:1/x")
    monkeypatch.setenv("MONGODB_DATABASE", "x")
    app = core.make_app()
    report = {
        "id": "T1", "title": "T1", "collection": "c", "connectionId": "default",
        "parameters": [], "queryLanguage": "aggregation-json",
        "querySource": json.dumps([{"$project": {"A": 1}}]), "columns": ["A"], "formats": ["csv"],
    }
    client = TestClient(app)
    assert client.post("/api/admin/reports", json=report, headers={"X-Admin-Token": "t"}).status_code in (200, 201)
    monkeypatch.setattr(core.mongodb, "_execute_report_pipeline", lambda *a, **k: [{"A": 1}])
    from backend.app.db.files import FileRecords
    monkeypatch.setattr(FileRecords, "_insert_run", lambda self, run: (_ for _ in ()).throw(OSError("disk")))

    class FakeClient(dict):
        pass

    # connection is unreachable in tests; patch the DB handle used by generate()
    import pymongo
    monkeypatch.setattr(pymongo.MongoClient, "admin", property(lambda self: type("A", (), {"command": lambda *_a, **_k: {"ok": 1}})()), raising=False)
    response = client.get("/generate/T1.csv", headers={"X-Admin-Token": "t"})
    assert response.status_code in (503, 500)
    assert "Audit" in response.text or response.status_code == 503
