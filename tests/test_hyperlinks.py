"""Hyperlinks of report elements: web and e-mail addresses and other reports, in PDF and Excel, and from .jrxml."""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.importers.jrxml import import_jrxml  # noqa: E402
from backend.app.rendering import engine  # noqa: E402
from test_workspace_and_server import ADMIN, client  # noqa: E402,F401

COLUMNS = ["City", "Email", "Branch"]
ROWS = [{"City": "Lahore", "Email": "ali@example.com", "Branch": "B 1"}, {"City": "Karachi", "Email": "not an address", "Branch": "B2"}]


def design_with(*links):
    elements = [{"id": f"e{i}", "type": "textField", "x": 0, "y": 0 + 0 * i, "width": 100, "height": 16, "expression": "$F{City}", "hyperlink": link}
                for i, link in enumerate(links)]
    for i, element in enumerate(elements):
        element["x"] = i * 110
    return engine.validate_design({"bands": {"detail": {"height": 20, "elements": elements}}}, COLUMNS, {"Region"})


def links_in(pdf: bytes) -> list[str]:
    import pdfplumber  # noqa: PLC0415
    with pdfplumber.open(io.BytesIO(pdf)) as file:
        return [link["uri"] for page in file.pages for link in page.hyperlinks]


def test_web_email_and_report_links_in_the_pdf():
    design = design_with({"type": "url", "target": "https://maps.example.com/?q=$F{City}", "tooltip": "Map"},
                         {"type": "email", "target": "$F{Email}"},
                         {"type": "report", "report": "BRANCH_DETAIL", "folder": "Sales", "parameters": [{"name": "Branch", "expression": "$F{Branch}"}]})
    token = engine.LINK_BASE.set("http://reports.local:8000/")
    try:
        found = links_in(engine.render_design_pdf(design, "t", COLUMNS, ROWS, {}))
    finally:
        engine.LINK_BASE.reset(token)
    assert "https://maps.example.com/?q=Lahore" in found and "https://maps.example.com/?q=Karachi" in found
    assert "mailto:ali@example.com" in found and not any("not an address" in item for item in found)   # a bad address: no link
    assert "http://reports.local:8000/generate/BRANCH_DETAIL.pdf?Branch=B+1&Folder=Sales&View=inline" in found


def test_only_web_addresses_are_linked():
    design = design_with({"type": "url", "target": "javascript:alert(1)"}, {"type": "url", "target": "www.example.com/$F{City}"},
                         {"type": "url", "target": "file:///etc/passwd"})
    found = links_in(engine.render_design_pdf(design, "t", COLUMNS, ROWS[:1], {}))
    assert found == ["https://www.example.com/Lahore"]


def test_hyperlinks_are_checked():
    with pytest.raises(ValueError, match="not defined"):
        design_with({"type": "url", "target": "https://x/$F{Nope}"})
    with pytest.raises(ValueError, match="choose the report"):
        design_with({"type": "report", "report": "../etc"})
    with pytest.raises(ValueError, match="parameter name"):
        design_with({"type": "report", "report": "R1", "parameters": [{"name": "a b", "expression": "1"}]})
    with pytest.raises(ValueError, match="address"):
        design_with({"type": "url", "target": ""})
    assert design_with({"type": "other"})["bands"]["detail"]["elements"][0]["hyperlink"] is None


def test_excel_cells_get_links():
    from openpyxl import load_workbook  # noqa: PLC0415
    design = design_with({"type": "url", "target": "https://maps.example.com/?q=$F{City}", "tooltip": "Map"})
    book = load_workbook(io.BytesIO(engine.render_grid_xlsx(design, "t", COLUMNS, ROWS, {})))
    cells = [cell for row in book.active.iter_rows() for cell in row if cell.hyperlink]
    assert [cell.hyperlink.target for cell in cells] == ["https://maps.example.com/?q=Lahore", "https://maps.example.com/?q=Karachi"]
    assert cells[0].value == "Lahore" and cells[0].hyperlink.tooltip == "Map"


def test_jrxml_hyperlinks_are_imported():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="r" pageWidth="595" pageHeight="842" columnWidth="555">
  <queryString language="MongoDbQuery"><![CDATA[{ collectionName : 'orders', findQuery : { } }]]></queryString>
  <field name="City" class="java.lang.String"/>
  <field name="Branch" class="java.lang.String"/>
  <detail><band height="20">
    <textField hyperlinkType="Reference">
      <reportElement x="0" y="0" width="100" height="16"/>
      <textFieldExpression><![CDATA[$F{City}]]></textFieldExpression>
      <hyperlinkReferenceExpression><![CDATA["https://maps.example.com/?q=" + $F{City}]]></hyperlinkReferenceExpression>
      <hyperlinkTooltipExpression><![CDATA["Open the map"]]></hyperlinkTooltipExpression>
    </textField>
    <textField hyperlinkType="Reference">
      <reportElement x="100" y="0" width="100" height="16"/>
      <textFieldExpression><![CDATA[$F{City}]]></textFieldExpression>
      <hyperlinkReferenceExpression><![CDATA["mailto:sales@example.com"]]></hyperlinkReferenceExpression>
    </textField>
    <textField hyperlinkType="ReportExecution">
      <reportElement x="200" y="0" width="100" height="16"/>
      <textFieldExpression><![CDATA[$F{Branch}]]></textFieldExpression>
      <hyperlinkParameter name="_report"><hyperlinkParameterExpression><![CDATA["/reports/sales/BRANCH_DETAIL"]]></hyperlinkParameterExpression></hyperlinkParameter>
      <hyperlinkParameter name="Branch"><hyperlinkParameterExpression><![CDATA[$F{Branch}]]></hyperlinkParameterExpression></hyperlinkParameter>
    </textField>
    <textField hyperlinkType="LocalPage">
      <reportElement x="300" y="0" width="100" height="16"/>
      <textFieldExpression><![CDATA[$F{City}]]></textFieldExpression>
    </textField>
  </band></detail>
</jasperReport>"""
    made = import_jrxml(xml)
    links = [element.get("hyperlink") for element in made["design"]["bands"]["detail"]["elements"]]
    assert links[0] == {"type": "url", "target": "https://maps.example.com/?q=$F{City}", "tooltip": "Open the map"}
    assert links[1] == {"type": "email", "target": "sales@example.com", "tooltip": ""}
    assert links[2] == {"type": "report", "report": "BRANCH_DETAIL", "folder": "", "format": "pdf", "parameters": [{"name": "Branch", "expression": "$F{Branch}"}], "tooltip": ""}
    assert links[3] is None and any("LocalPage" in warning for warning in made["warnings"])
    engine.validate_design(made["design"], ["City", "Branch"], set())


def test_a_report_link_opens_the_pdf_in_the_browser(client):  # noqa: F811
    headers = {"X-Admin-Token": ADMIN}
    shown = client.get("/generate/R1.pdf?View=inline", headers=headers)
    assert shown.status_code == 200, shown.text
    assert shown.headers["content-disposition"].startswith("inline;")
    assert client.get("/generate/R1.pdf", headers=headers).headers["content-disposition"].startswith("attachment;")
    assert client.get("/generate/R1.csv?View=inline", headers=headers).headers["content-disposition"].startswith("attachment;")


def test_jrxml_export_keeps_the_hyperlinks():
    from backend.app.exporters.jrxml_export import export_jrxml  # noqa: PLC0415
    design = {"bands": {"detail": {"height": 20, "elements": [
        {"id": "a", "type": "textField", "x": 0, "y": 0, "width": 100, "height": 16, "expression": "$F{City}",
         "hyperlink": {"type": "url", "target": "https://maps.example.com/?q=$F{City}", "tooltip": "Map"}},
        {"id": "b", "type": "staticText", "x": 110, "y": 0, "width": 100, "height": 16, "text": "Write to us",
         "hyperlink": {"type": "email", "target": "sales@example.com"}},
        {"id": "c", "type": "textField", "x": 220, "y": 0, "width": 100, "height": 16, "expression": "$F{Branch}",
         "hyperlink": {"type": "report", "report": "BRANCH_DETAIL", "folder": "Sales", "parameters": [{"name": "Branch", "expression": "$F{Branch}"}]}}]}}}
    out = export_jrxml({"id": "R", "design": design, "columns": ["City", "Branch"],
                        "querySource": "", "collection": "orders"})["xml"]
    assert 'hyperlinkType="Reference"' in out and 'hyperlinkType="ReportExecution"' in out and "mailto:sales@example.com" in out
    assert '"/Sales/BRANCH_DETAIL"' in out


def test_dashboard_table_links_are_checked():
    from backend.app.analytics.store import clean_dashboard  # noqa: PLC0415

    def board(*links):
        return clean_dashboard({"name": "B", "tiles": [{"id": "t1", "type": "table", "options": {"links": list(links)}}]})["tiles"][0]["options"]["links"]
    links = board({"column": "City", "kind": "url", "template": "https://maps.example.com/?q={City}", "extra": "dropped"},
                  {"column": "Branch", "kind": "report", "report": "BRANCH_DETAIL", "parameter": "Branch"},
                  {"column": "Region", "kind": "dashboard", "dashboard": "regions", "tooltip": "Open\x07 region"})
    assert links[0] == {"column": "City", "kind": "url", "template": "https://maps.example.com/?q={City}"}
    assert links[2]["tooltip"] == "Open region"
    for bad in ({"column": "City", "kind": "script"}, {"column": "", "kind": "url"}, {"column": "C", "kind": "url", "template": "javascript:alert(1)"},
                {"column": "C", "kind": "report", "report": "../x"}):
        with pytest.raises(ValueError):
            board(bad)
