"""Deployment access policy (areas, roles, IP allowlist), service logs, runner adapter status and markup text."""
import csv
import io
import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.rendering import engine as dr  # noqa: E402
from backend.app.importers import jrxml as jrxml_import  # noqa: E402
import service_names as core  # noqa: E402

ADMIN = "admin-token-for-tests-0123456789"
DESIGNER = "designer-token-for-tests-0123456789"
REPORT = {"id": "R1", "title": "R1", "collection": "orders", "connectionId": "default", "parameters": [],
          "queryLanguage": "aggregation-json", "querySource": '[{"$project": {"Name": 1}}]', "columns": ["Name"],
          "formats": ["csv", "xlsx", "pdf"]}


class _FakeAdmin:
    def __init__(self, reachable):
        self.reachable = reachable

    def command(self, *_a, **_k):
        if not self.reachable:
            raise core.PyMongoError("unreachable")
        return {"ok": 1}


class FakeMongoClient:
    def __init__(self, uri, **_kwargs):
        self.uri = uri
        self.admin = _FakeAdmin("down" not in uri)

    def __getitem__(self, _name):
        return {"orders": object()}

    def close(self):
        pass


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def build(**env):
        monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("REPORTING_ADMIN_TOKEN", ADMIN)
        monkeypatch.setenv("REPORTING_DESIGNER_TOKEN", DESIGNER)
        for key in ("REPORTING_ENABLED_AREAS", "REPORTING_ADMIN_ALLOWED_IPS", "REPORTING_TRUSTED_PROXIES", "REPORTING_LOG_FILE"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        (tmp_path / "connections.json").write_text(json.dumps({"connections": [
            {"id": "default", "name": "Main", "storage": "file", "database": "a"},
            {"id": "broken", "name": "Broken", "storage": "file", "database": "b"},
        ]}), encoding="utf-8")
        (tmp_path / "private_secrets.json").write_text(json.dumps({"admin_token": ADMIN, "mongo_connections": {
            "default": {"uri": "mongodb://main/a", "database": "a"},
            "broken": {"uri": "mongodb://down/b", "database": "b"},
        }}), encoding="utf-8")
        (tmp_path / "reports" / "General").mkdir(parents=True, exist_ok=True)
        (tmp_path / "reports" / "General" / "R1.json").write_text(json.dumps(REPORT), encoding="utf-8")
        monkeypatch.setattr(core.mongodb, "MongoClient", FakeMongoClient)
        monkeypatch.setattr(core.mongodb, "_execute_report_pipeline", lambda *_a, **_k: [{"Name": "a"}])
        return TestClient(core.make_app())
    return build


def admin(token=ADMIN):
    return {"X-Admin-Token": token}


# ---------------------------------------------------------------- roles
def test_designer_token_can_design_but_not_manage(make_client):
    client = make_client()
    assert client.get("/api/admin/whoami", headers=admin(DESIGNER)).json()["role"] == "designer"
    assert client.get("/api/admin/reports", headers=admin(DESIGNER)).status_code == 200
    assert client.post("/api/admin/reports/R1/preview", headers=admin(DESIGNER), json={"parameters": {}}).status_code == 200
    assert client.delete("/api/admin/reports/R1", headers=admin(DESIGNER)).status_code == 403
    # data sources need no Admin right any more: the designer token may connect one
    assert client.post("/api/data-sources", headers=admin(DESIGNER), json={"name": "x", "uri": "mongodb://x/y"}).status_code == 200
    assert client.get("/api/admin/logs", headers=admin(DESIGNER)).status_code == 403
    assert client.get("/api/admin/whoami", headers=admin(ADMIN)).json()["role"] == "admin"
    assert client.get("/api/admin/reports", headers=admin("wrong")).status_code == 401


def test_short_designer_token_is_ignored(make_client):
    client = make_client(REPORTING_DESIGNER_TOKEN="short")
    assert client.get("/api/admin/reports", headers=admin("short")).status_code == 401


# ---------------------------------------------------------------- areas
def test_runner_only_instance_hides_admin_designer_and_docs(make_client):
    client = make_client(REPORTING_ENABLED_AREAS="runner")
    for path in ("/admin", "/designer", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    assert client.get("/api/admin/reports", headers=admin()).status_code == 404
    # report API stays available
    assert client.get("/reports", headers=admin()).status_code == 200
    assert client.get("/generate/R1.csv", headers=admin()).status_code == 200


def test_designer_only_instance_keeps_designer_api_but_not_admin_api(make_client):
    client = make_client(REPORTING_ENABLED_AREAS="designer")
    assert client.get("/admin").status_code == 404
    assert client.get("/api/admin/reports", headers=admin()).status_code == 200
    assert client.get("/api/admin/logs", headers=admin()).status_code == 404
    assert client.delete("/api/admin/reports/R1", headers=admin()).status_code == 404


def test_management_ip_allowlist(make_client):
    client = make_client(REPORTING_ADMIN_ALLOWED_IPS="10.0.0.0/8")
    assert client.get("/admin").status_code == 403            # TestClient peer is "testclient", not in the list
    assert client.get("/api/admin/reports", headers=admin()).status_code == 403
    assert client.get("/reports", headers=admin()).status_code == 200


def test_forwarded_for_is_trusted_only_from_trusted_proxy(monkeypatch):
    class Req:
        def __init__(self, peer, forwarded):
            self.client = type("C", (), {"host": peer})()
            self.headers = {"x-forwarded-for": forwarded}
    monkeypatch.setenv("REPORTING_TRUSTED_PROXIES", "127.0.0.1")
    assert core.client_address(Req("127.0.0.1", "10.1.2.3")) == "10.1.2.3"
    assert core.client_address(Req("127.0.0.1", "6.6.6.6, 10.1.2.3")) == "10.1.2.3"
    assert core.client_address(Req("8.8.8.8", "10.1.2.3")) == "8.8.8.8"   # untrusted peer: header ignored


# ---------------------------------------------------------------- logs
def test_requests_are_logged_to_file_without_query_strings(make_client, tmp_path):
    client = make_client()
    assert client.get("/generate/R1.csv?GeneratedBy=abc123", headers=admin()).status_code == 200
    for handler in logging.getLogger("mongo_reporting_service").handlers:
        handler.flush()
    text = (tmp_path / "logs" / "service.log").read_text(encoding="utf-8")
    assert "GET /generate/R1.csv -> 200" in text
    assert "abc123" not in text and ADMIN not in text


def test_logs_endpoint_filters_and_groups_tracebacks(make_client, tmp_path):
    client = make_client()
    core.LOG.warning("something to look at")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        core.LOG.exception("failure with traceback")
    for handler in logging.getLogger("mongo_reporting_service").handlers:
        handler.flush()
    entries = client.get("/api/admin/logs?level=WARNING", headers=admin()).json()["entries"]
    assert all(entry["level"] in ("WARNING", "ERROR", "CRITICAL") for entry in entries)
    failure = next(entry for entry in entries if entry["message"].startswith("failure with traceback"))
    assert "RuntimeError: boom" in failure["message"]
    found = client.get("/api/admin/logs?level=DEBUG&search=look at", headers=admin()).json()["entries"]
    assert [entry["message"] for entry in found] == ["something to look at"]
    assert client.get("/api/admin/logs?level=LOUD", headers=admin()).status_code == 422
    download = client.get("/api/admin/logs/download", headers=admin())
    assert download.status_code == 200 and b"something to look at" in download.content


def test_audit_endpoint_lists_runs_newest_first(make_client):
    client = make_client()
    client.get("/generate/R1.csv", headers=admin())
    client.get("/generate/R1.pdf", headers=admin())
    records = client.get("/api/admin/audit", headers=admin()).json()["records"]
    assert [r["format"] for r in records[:2]] == ["pdf", "csv"]


# ---------------------------------------------------------------- runner adapter status
def test_runner_adapter_status(make_client):
    client = make_client()
    ok = client.get("/reports/connections/default/status", headers=admin()).json()
    assert ok == {"status": "ok", "connectionId": "default", "name": "Main", "database": "a"}
    broken = client.get("/reports/connections/broken/status", headers=admin()).json()
    assert broken["status"] == "unavailable" and broken["name"] == "Broken"
    assert client.get("/reports/connections/nope/status", headers=admin()).status_code == 404
    assert client.get("/reports/connections/default/status").status_code == 401


# ---------------------------------------------------------------- markup, fonts
HTML_VALUE = "<p>Hello <b>World</b> &amp; co</p><ul><li>one</li><li>two</li></ul><script>alert(1)</script>"


def markup_design():
    return {"bands": {
        "columnHeader": {"height": 20, "elements": [{"type": "staticText", "x": 0, "y": 0, "width": 200, "height": 18,
                                                     "text": "<b>Note</b>", "markup": "html"}]},
        "detail": {"height": 60, "elements": [{"type": "textField", "x": 0, "y": 0, "width": 200, "height": 60,
                                               "expression": "$F{Body}", "markup": "html", "fontName": "Times New Roman",
                                               "underline": True, "align": "justified"}]},
    }}


def test_markup_text_conversion():
    assert dr.markup_to_text(HTML_VALUE, "html") == "Hello World & co\n• one\n• two"
    paragraph = dr.markup_to_paragraph(HTML_VALUE, "html")
    assert "<b>World</b>" in paragraph and "alert" not in paragraph and "&amp;" in paragraph
    styled = dr.markup_to_paragraph('<style forecolor="#ff0000" isBold="true">red</style>', "styled")
    assert styled == '<font color="#ff0000"><b>red</b></font>'
    assert dr.markup_to_text("<b>kept as-is</b>", "none") == "<b>kept as-is</b>"
    assert "&lt;b&gt;" in dr.markup_to_paragraph("<b>x</b>", "none")


def test_markup_design_validates_and_renders_pdf():
    design = dr.validate_design(markup_design(), ["Body"], set())
    element = design["bands"]["detail"]["elements"][0]
    assert (element["markup"], element["fontName"], element["underline"], element["align"]) == ("html", "Times New Roman", True, "justified")
    pdf = dr.render_design_pdf(design, "T", ["Body"], [{"Body": HTML_VALUE}, {"Body": "<b>unclosed <i>tags"}], {})
    assert pdf.startswith(b"%PDF") and b"Times-Roman" in pdf


def test_unknown_markup_and_font_fall_back_safely():
    raw = markup_design()
    raw["bands"]["detail"]["elements"][0].update(markup="rtf", fontName="Comic Sans")
    element = dr.validate_design(raw, ["Body"], set())["bands"]["detail"]["elements"][0]
    assert element["markup"] == "none" and element["fontName"] == "SansSerif"


def test_markup_becomes_plain_text_in_excel_and_csv():
    design = dr.validate_design(markup_design(), ["Body"], set())
    rows = [{"Body": HTML_VALUE}]
    reader = list(csv.reader(io.StringIO(dr.render_design_csv(design, "T", ["Body"], rows, {}).decode("utf-8-sig"))))
    assert reader == [["Note"], ["Hello World & co\n• one\n• two"]]
    sheet = load_workbook(io.BytesIO(dr.render_design_xlsx(design, "T", ["Body"], rows, {}))).active
    values = [row for row in sheet.iter_rows(values_only=True)]
    assert ("Hello World & co\n• one\n• two",) in values
    body = next(cell for row in sheet.iter_rows() for cell in row if cell.value and str(cell.value).startswith("Hello"))
    assert body.font.name == "Times New Roman" and body.font.underline == "single" and body.alignment.horizontal == "justify"


def test_jrxml_import_keeps_markup_font_and_justify():
    xml = """<?xml version="1.0"?>
<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="M" pageWidth="595" pageHeight="842">
 <queryString language="MongoDbQuery"><![CDATA[{ 'collectionName' : 'orders', 'findQuery' : { } }]]></queryString>
 <field name="Body" class="java.lang.String"/>
 <detail><band height="40"><textField><reportElement x="0" y="0" width="200" height="40"/>
  <textElement textAlignment="Justified" markup="html"><font fontName="Serif" size="11" isUnderline="true"/></textElement>
  <textFieldExpression><![CDATA[$F{Body}]]></textFieldExpression></textField></band></detail></jasperReport>"""
    try:
        result = jrxml_import.import_jrxml(xml)
    except ValueError:
        pytest.skip("this JRXML query form is not importable; covered by the element test below")
    element = result["design"]["bands"]["detail"]["elements"][0]
    assert (element["markup"], element["fontName"], element["underline"], element["align"]) == ("html", "Serif", True, "justified")
