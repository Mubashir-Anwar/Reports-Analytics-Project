"""Derived (isForPrompting=false) parameters, choosing a data adapter for preview/run, and the admin run endpoint."""
import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jexpr  # noqa: E402
import jrxml_import  # noqa: E402
import reporting_core as core  # noqa: E402

# The exact expressions from a Jaspersoft Studio report.
ADJUSTED_START = """new java.util.GregorianCalendar(
    $P{StartDate}.getYear() + 1900,
    $P{StartDate}.getMonth(),
    $P{StartDate}.getDate(),
    0, 0, 0
).getTime()"""
ADJUSTED_END = ADJUSTED_START.replace("StartDate", "EndDate").replace("0, 0, 0", "23, 59, 59")

PARAMETERS = [
    {"name": "StartDate", "type": "date", "required": True},
    {"name": "EndDate", "type": "date", "required": True},
    {"name": "AdjustedStartDate", "type": "date", "prompt": False, "expression": ADJUSTED_START},
    {"name": "AdjustedEndDate", "type": "date", "prompt": False, "expression": ADJUSTED_END},
]
QUERY = ('db.getCollection("orders").aggregate([{ $match: { LastUpdateDateTime: '
         '{ $gte: $P{AdjustedStartDate}, $lte: $P{AdjustedEndDate} } } }, { $project: { Name: 1 } }])')
REPORT = {"id": "DERIVED", "title": "Derived", "collection": "orders", "connectionId": "default",
          "parameters": PARAMETERS, "queryLanguage": "mongosh", "querySource": QUERY, "columns": ["Name"],
          "formats": ["csv", "xlsx", "pdf"]}


# ---------------------------------------------------------------- jexpr date support
def test_java_date_getters_and_gregorian_calendar():
    resolve = lambda kind, name: {"D": date(2025, 10, 27)}.get(name)  # noqa: E731
    assert jexpr.evaluate("$P{D}.getYear() + 1900", resolve) == 2025
    assert jexpr.evaluate("$P{D}.getMonth()", resolve) == 9  # Java months are 0-based
    assert jexpr.evaluate("$P{D}.getDate()", resolve) == 27
    assert jexpr.evaluate(ADJUSTED_START.replace("StartDate", "D"), resolve) == datetime(2025, 10, 27, 0, 0, 0)
    assert jexpr.evaluate(ADJUSTED_END.replace("EndDate", "D"), resolve) == datetime(2025, 10, 27, 23, 59, 59)


def test_gregorian_calendar_is_lenient_like_java():
    # month 12 -> January next year; day 0 -> last day of previous month
    assert jexpr.evaluate("new GregorianCalendar(2025, 12, 1).getTime()", lambda *_: None) == datetime(2026, 1, 1)
    assert jexpr.evaluate("new GregorianCalendar(2025, 2, 0).getTime()", lambda *_: None) == datetime(2025, 2, 28)


# ---------------------------------------------------------------- definitions
def test_derived_parameters_are_computed_and_used_in_pipeline():
    definition = core._dynamic_report(REPORT)
    values, _ = core.validate_parameters(definition, [("StartDate", "2025-10-27"), ("EndDate", "2025-10-31")])
    assert values["AdjustedStartDate"] == datetime(2025, 10, 27, 0, 0, 0, tzinfo=timezone.utc)
    assert values["AdjustedEndDate"] == datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    match = definition.pipeline_builder(values)[0]["$match"]["LastUpdateDateTime"]
    assert match == {"$gte": values["AdjustedStartDate"], "$lte": values["AdjustedEndDate"]}


def test_derived_parameter_cannot_be_sent_in_url():
    definition = core._dynamic_report(REPORT)
    with pytest.raises(ValueError, match="calculate"):
        core.validate_parameters(definition, [("StartDate", "2025-10-27"), ("EndDate", "2025-10-31"), ("AdjustedStartDate", "2000-01-01")])


def test_derived_parameter_validation():
    bad = lambda p: core._dynamic_report({**REPORT, "parameters": PARAMETERS[:2] + [p]})  # noqa: E731
    with pytest.raises(ValueError, match="expression"):
        bad({"name": "X", "type": "date", "prompt": False})
    with pytest.raises(ValueError, match="could not be understood"):
        bad({"name": "X", "type": "date", "prompt": False, "expression": "new Object()"})
    with pytest.raises(ValueError, match="not a parameter of this report"):  # any order is fine, unknown names are not
        bad({"name": "X", "type": "date", "prompt": False, "expression": "$P{Later}"})
    with pytest.raises(ValueError, match=r"\$P"):
        bad({"name": "X", "type": "string", "prompt": False, "expression": "$F{Name}"})


def test_derived_parameter_round_trips_in_saved_definition():
    saved = core._dynamic_report(REPORT).editor_config["parameters"]
    assert saved[2]["prompt"] is False and "GregorianCalendar" in saved[2]["expression"]
    assert "prompt" not in saved[0]


def test_missing_input_gives_clear_error():
    definition = core._dynamic_report({**REPORT, "parameters": [
        {"name": "StartDate", "type": "date"},
        {"name": "AdjustedStartDate", "type": "date", "prompt": False, "expression": ADJUSTED_START},
    ], "querySource": QUERY.replace("$P{AdjustedEndDate}", "$P{AdjustedStartDate}")})
    with pytest.raises(ValueError, match="AdjustedStartDate could not be calculated"):
        core.validate_parameters(definition, [])


def test_jrxml_import_keeps_unrecognised_computed_parameter():
    xml = """<?xml version="1.0"?>
<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="D" pageWidth="595" pageHeight="842">
 <parameter name="Amount" class="java.lang.Double"/>
 <parameter name="Doubled" class="java.lang.Double" isForPrompting="false">
  <defaultValueExpression><![CDATA[$P{Amount} * 2]]></defaultValueExpression></parameter>
 <queryString language="MongoDbQuery"><![CDATA[{ 'collectionName' : 'orders', 'findQuery' : { Amount : { $gte: $P{Doubled}} } }]]></queryString>
 <field name="Name" class="java.lang.String"/>
 <detail><band height="20"><textField><reportElement x="0" y="0" width="100" height="20"/>
  <textFieldExpression><![CDATA[$F{Name}]]></textFieldExpression></textField></band></detail></jasperReport>"""
    result = jrxml_import.import_jrxml(xml)
    doubled = next(p for p in result["report"]["parameters"] if p["name"] == "Doubled")
    assert doubled["prompt"] is False and doubled["expression"] == "$P{Amount} * 2"


# ---------------------------------------------------------------- endpoints: adapter choice + run
class _FakeAdmin:
    def command(self, *_a, **_k):
        return {"ok": 1}


class FakeMongoClient:
    instances = []

    def __init__(self, uri, **_kwargs):
        self.uri = uri
        self.admin = _FakeAdmin()
        FakeMongoClient.instances.append(uri)

    def __getitem__(self, _name):
        return {"orders": object()}

    def close(self):
        pass


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", "t")
    (tmp_path / "connections.json").write_text(json.dumps({"connections": [
        {"id": "default", "name": "Albaraka", "storage": "file", "database": "a"},
        {"id": "telenor", "name": "Telenor", "storage": "file", "database": "t"},
    ]}), encoding="utf-8")
    (tmp_path / "private_secrets.json").write_text(json.dumps({"admin_token": "t", "mongo_connections": {
        "default": {"uri": "mongodb://albaraka/a", "database": "a"},
        "telenor": {"uri": "mongodb://telenor/t", "database": "t"},
    }}), encoding="utf-8")
    (tmp_path / "reports" / "General").mkdir(parents=True)
    (tmp_path / "reports" / "General" / "DERIVED.json").write_text(json.dumps(REPORT), encoding="utf-8")
    FakeMongoClient.instances = []
    monkeypatch.setattr(core.mongodb, "MongoClient", FakeMongoClient)
    seen = {}

    def fake_execute(collection, pipeline, **_kwargs):
        seen["pipeline"] = pipeline
        return [{"Name": f"row{i}"} for i in range(30)]

    monkeypatch.setattr(core.mongodb, "_execute_report_pipeline", fake_execute)
    audits = []
    monkeypatch.setattr(core.audit, "_append_report_audit", audits.append)
    client = TestClient(core.make_app())
    client.seen, client.audits = seen, audits
    return client


HEADERS = {"X-Admin-Token": "t"}
ANSWERS = {"StartDate": "2025-10-27", "EndDate": "2025-10-31"}


def test_preview_runs_on_chosen_adapter(service):
    response = service.post("/api/admin/reports/DERIVED/preview", headers=HEADERS, json={"parameters": ANSWERS, "connectionId": "telenor"})
    assert response.status_code == 200, response.text
    assert response.json()["connectionId"] == "telenor"
    assert "mongodb://telenor/t" in FakeMongoClient.instances
    assert response.json()["rowCount"] == 30 or response.json()["rowCount"] <= 30
    assert service.seen["pipeline"][-1] == {"$limit": 20}


def test_preview_defaults_to_report_adapter_and_rejects_unknown(service):
    assert service.post("/api/admin/reports/DERIVED/preview", headers=HEADERS, json={"parameters": ANSWERS}).json()["connectionId"] == "default"
    unknown = service.post("/api/admin/reports/DERIVED/preview", headers=HEADERS, json={"parameters": ANSWERS, "connectionId": "nope"})
    assert unknown.status_code == 422


def test_preview_ignores_values_sent_for_computed_parameters(service):
    response = service.post("/api/admin/reports/DERIVED/preview", headers=HEADERS,
                            json={"parameters": {**ANSWERS, "AdjustedStartDate": "1999-01-01"}})
    assert response.status_code == 200
    match = service.seen["pipeline"][0]["$match"]["LastUpdateDateTime"]
    assert match["$gte"] == datetime(2025, 10, 27, tzinfo=timezone.utc)


@pytest.mark.parametrize("fmt, magic", [("pdf", b"%PDF"), ("xlsx", b"PK"), ("csv", b"Name")])
def test_run_report_returns_full_file_on_chosen_adapter(service, fmt, magic):
    response = service.post("/api/admin/reports/DERIVED/run", headers=HEADERS,
                            json={"parameters": ANSWERS, "connectionId": "telenor", "format": fmt})
    assert response.status_code == 200, response.text
    assert response.content.lstrip(b"\xef\xbb\xbf").startswith(magic)
    audit = service.audits[-1]
    assert audit["status"] == "success" and audit["connectionId"] == "telenor" and audit["access"] == "admin-run"
    assert audit["rowCount"] == 30


def test_run_report_excel_has_all_rows_on_one_sheet(service):
    response = service.post("/api/admin/reports/DERIVED/run", headers=HEADERS, json={"parameters": ANSWERS, "format": "xlsx"})
    book = load_workbook(io.BytesIO(response.content))
    assert len(book.worksheets) == 1
    values = [row[0] for row in book.active.iter_rows(values_only=True)]
    assert sum(1 for v in values if isinstance(v, str) and v.startswith("row")) == 30


def test_run_report_requires_admin(service):
    assert service.post("/api/admin/reports/DERIVED/run", json={"parameters": ANSWERS}).status_code == 401


def test_runner_list_hides_computed_parameters(service):
    reports = service.get("/reports", headers=HEADERS).json()["reports"]
    listed = next(r for r in reports if r["id"] == "DERIVED")["parameters"]
    assert [p["name"] for p in listed] == ["StartDate", "EndDate"]


def test_generate_computes_derived_parameters(service):
    response = service.get("/generate/DERIVED.csv?StartDate=2025-10-27&EndDate=2025-10-31", headers=HEADERS)
    assert response.status_code == 200, response.text
    match = service.seen["pipeline"][0]["$match"]["LastUpdateDateTime"]
    assert match["$lte"] == datetime(2025, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
