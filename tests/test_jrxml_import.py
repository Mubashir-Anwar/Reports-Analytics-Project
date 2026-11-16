"""JRXML importer + design-based CSV/XLSX output. No MongoDB needed."""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import designer_render as dr  # noqa: E402
import jrxml_import  # noqa: E402
import reporting_core as core  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "abpl_accounts_opened.jrxml"


@pytest.fixture(scope="module")
def imported():
    return jrxml_import.import_jrxml(FIXTURE.read_text(encoding="utf-8"))


def test_import_parameters_and_columns(imported):
    report = imported["report"]
    names = {p["name"] for p in report["parameters"]}
    assert {"StartDate", "EndDate", "Branch", "Region", "AccountType"} <= names
    assert len(report["columns"]) == 9
    required = {p["name"] for p in report["parameters"] if p.get("required")}
    assert {"StartDate", "EndDate"} <= required


def test_import_design_page_and_sort(imported):
    design = imported["design"]
    assert design["page"].get("ignorePagination") is True
    assert design["bands"]["columnHeader"]["elements"]
    assert design["bands"]["detail"]["elements"]
    assert design.get("sort")


def test_optional_filter_empty_means_all(imported):
    pipeline = imported["report"]["querySource"]
    text = pipeline
    assert "$paramIn" in text
    resolved = core._resolve_pipeline_parameters(
        {"Branch": {"$paramIn": "Branch"}}, {"Branch": ""})
    assert resolved == {"Branch": {"$nin": []}}
    resolved = core._resolve_pipeline_parameters(
        {"Branch": {"$paramIn": "Branch"}}, {"Branch": "A, B"})
    assert resolved == {"Branch": {"$in": ["A", "B"]}}


def test_rejects_entities():
    with pytest.raises(Exception):
        jrxml_import.import_jrxml('<!DOCTYPE x [<!ENTITY a "b">]><jasperReport/>')


def test_design_csv_and_xlsx(imported):
    from openpyxl import load_workbook
    design, report = imported["design"], imported["report"]
    columns = report["columns"]
    rows = dr.sample_rows(columns, 5)
    csv_bytes = dr.render_design_csv(design, report["title"], columns, rows, {})
    assert csv_bytes.startswith(b"\xef\xbb\xbf")
    assert len(csv_bytes.decode("utf-8-sig").strip().splitlines()) == 6
    xlsx = dr.render_design_xlsx(design, report["title"], columns, rows, {})
    ws = load_workbook(io.BytesIO(xlsx)).active
    assert ws.max_row >= 6 and ws.freeze_panes


def test_design_pdf_single_page(imported):
    design, report = imported["design"], imported["report"]
    rows = dr.sample_rows(report["columns"], 60)
    pdf = dr.render_design_pdf(design, report["title"], report["columns"], rows, {})
    assert pdf.startswith(b"%PDF")
    assert pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") <= 2


MINI = """<?xml version="1.0"?>
<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="Mini" pageWidth="400" pageHeight="842" columnWidth="360" leftMargin="20" rightMargin="20" topMargin="20" bottomMargin="20">
 <parameter name="Identifier" class="java.lang.String"/>
 <parameter name="ProductCode" class="java.lang.String"/>
 <parameter name="CollectionName" class="java.lang.String" isForPrompting="false">
  <defaultValueExpression><![CDATA["Edith-"+$P{ProductCode}+"-Plans"]]></defaultValueExpression></parameter>
 <queryString language="MongoDbQuery"><![CDATA[{ 'collectionName' : $P{CollectionName}, 'findQuery' : { Identifier : { $eq: $P{Identifier}}, } }]]></queryString>
 <field name="Fields.A" class="java.lang.String"/>
 <detail><band height="30">
  <textField><reportElement x="0" y="0" width="200" height="20"/>
   <textFieldExpression><![CDATA[$F{Fields.A} == null ? "-" : new java.text.DecimalFormat("#,##0").format(Double.parseDouble($F{Fields.A}))]]></textFieldExpression></textField>
 </band></detail></jasperReport>"""


def test_find_query_and_collection_template():
    result = jrxml_import.import_jrxml(MINI.replace("'collectionName' : $P{CollectionName}", "'collectionName' : 'Plans-X'"))
    assert result["report"]["collection"] == "Plans-X"
    assert "$match" in result["report"]["querySource"]
    assert result["report"]["columns"] == ["Fields.A"]
    expr = result["design"]["bands"]["detail"]["elements"][0]["expression"]
    assert expr.startswith("=") and "DecimalFormat" in expr


def test_resolve_collection_is_strict():
    assert core.resolve_collection("Edith-$P{ProductCode}-Plans", {"ProductCode": "HBL"}) == "Edith-HBL-Plans"
    for bad in ("a.b", "x$where", "../x", ""):
        with pytest.raises(ValueError):
            core.resolve_collection("E-$P{ProductCode}", {"ProductCode": bad})


def test_dotted_column_reads_nested_document():
    assert core._row_value({"Fields": {"A": "5"}}, "Fields.A") == "5"
    assert core._row_value({"Fields.A": "direct"}, "Fields.A") == "direct"
    assert core._row_value({}, "Fields.A") is None


def test_java_expression_renders_in_csv():
    result = jrxml_import.import_jrxml(MINI.replace("'collectionName' : $P{CollectionName}", "'collectionName' : 'Plans-X'"))
    rep, design = result["report"], result["design"]
    out = dr.render_design_csv(design, rep["title"], rep["columns"], [{"Fields.A": "1234567"}, {"Fields.A": None}], {}).decode("utf-8-sig")
    assert "1,234,567" in out


LETTER = """<?xml version="1.0" encoding="UTF-8"?>
<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="Letter" pageWidth="600" pageHeight="2000"
  columnWidth="560" leftMargin="20" rightMargin="20" topMargin="20" bottomMargin="20" isIgnorePagination="true">
  <subDataset name="Dataset1">
    <parameter name="Code" class="java.lang.String"/>
    <parameter name="CollectionName" class="java.lang.String" isForPrompting="false">
      <defaultValueExpression><![CDATA["Plans-"+$P{Code}]]></defaultValueExpression>
    </parameter>
    <queryString language="MongoDbQuery"><![CDATA[{"runCommand": {"aggregate": $P{CollectionName}, "pipeline": [
      {"$project": {"Name": "$Name", "No": "$No"}}]}}]]></queryString>
    <field name="Name" class="java.lang.String"/><field name="No" class="java.lang.Integer"/>
  </subDataset>
  <parameter name="Code" class="java.lang.String"/>
  <parameter name="CollectionName" class="java.lang.String" isForPrompting="false">
    <defaultValueExpression><![CDATA["Plans-"+$P{Code}]]></defaultValueExpression>
  </parameter>
  <queryString language="MongoDbQuery"><![CDATA[{"runCommand": {"aggregate": $P{CollectionName}, "pipeline": [{"$project": {"Name": "$Name"}}]}}]]></queryString>
  <field name="Name" class="java.lang.String"/>
  <title><band height="900">
    <staticText><reportElement x="0" y="0" width="300" height="20"/><text><![CDATA[ABOVE]]></text></staticText>
    <componentElement><reportElement x="0" y="100" width="300" height="30"/>
      <jr:table xmlns:jr="http://jasperreports.sourceforge.net/jasperreports/components">
        <datasetRun subDataset="Dataset1"/>
        <jr:column width="300"><jr:detailCell height="30">
          <textField><reportElement x="0" y="0" width="300" height="30"/><textFieldExpression><![CDATA[$F{No}]]></textFieldExpression></textField>
        </jr:detailCell></jr:column>
      </jr:table>
    </componentElement>
    <staticText><reportElement x="0" y="400" width="300" height="20"/><text><![CDATA[BELOW1]]></text></staticText>
    <textField><reportElement x="0" y="800" width="300" height="20"/><textFieldExpression><![CDATA[$F{Name}]]></textFieldExpression></textField>
  </band></title>
</jasperReport>"""


def test_table_inside_title_keeps_the_part_below_in_summary():
    """A letter laid out in one Title band with a table in the middle: nothing below the table may be lost."""
    out = jrxml_import.import_jrxml(LETTER)
    bands = out["design"]["bands"]
    assert [e.get("text") for e in bands["title"]["elements"]] == ["ABOVE"]
    assert bands["title"]["height"] == 100
    assert [e.get("expression") for e in bands["detail"]["elements"]] == ["$F{No}"]
    summary = bands["summary"]["elements"]
    assert summary[0]["text"] == "BELOW1" and summary[0]["y"] == 10          # starts right after the rows
    assert summary[1]["expression"] == "$F{Name}" and summary[1]["y"] == 410  # spacing between them is kept
    assert bands["summary"]["height"] >= 430
    assert any("moved to the Summary band" in w for w in out["warnings"])
    assert out["report"]["collection"] == "Plans-$P{Code}"
    rows = [{"Name": "N", "No": i} for i in range(3)]
    pdf = dr.render_design_pdf(out["design"], "L", out["report"]["columns"], rows, {"Code": "X"})
    assert pdf.startswith(b"%PDF")
