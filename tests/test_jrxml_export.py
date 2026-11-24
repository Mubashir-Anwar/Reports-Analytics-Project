"""Save as JRXML: the designer's report -> a Jaspersoft Studio .jrxml that our importer (and Studio) reads back. No MongoDB needed."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.exporters.jrxml_export import export_jrxml, java_string  # noqa: E402
from backend.app.importers import jrxml as jrxml_import  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "abpl_accounts_opened.jrxml"
NS = "{http://jasperreports.sourceforge.net/jasperreports}"


def _definition(**extra):
    base = {
        "id": "Sales_by_Branch", "title": "Sales by branch", "description": "Monthly sales",
        "collection": "sales", "queryLanguage": "mongosh",
        "querySource": 'db.sales.aggregate([{ "$match": { "Branch": "$P{Branch}" } }, { "$sort": { "Amount": -1 } }])',
        "parameters": [
            {"name": "Branch", "type": "string", "label": "Branch", "required": True, "default": "Karachi"},
            {"name": "Limit", "type": "integer", "default": 10},
            {"name": "Title", "type": "string", "prompt": False, "expression": '"Branch " + $P{Branch}'},
        ],
        "columns": ["Branch", "Amount", "Date"],
        "design": {
            "page": {"width": 595, "height": 842, "margins": {"top": 20, "right": 20, "bottom": 20, "left": 20}},
            "groups": [{"name": "BranchGroup", "field": "Branch"}],
            "variables": [{"name": "Total", "calculation": "Sum", "field": "Amount", "resetType": "Group", "resetGroup": "BranchGroup"},
                          {"name": "Biggest", "calculation": "Max", "field": "Amount"}],
            "sort": [{"field": "Amount", "order": "Descending"}],
            "bands": {
                "title": {"height": 40, "elements": [
                    {"id": "t1", "type": "staticText", "x": 0, "y": 0, "width": 300, "height": 30, "text": "Sales & <more>", "fontSize": 18, "bold": True, "align": "center"},
                    {"id": "t2", "type": "textField", "x": 300, "y": 0, "width": 255, "height": 20, "expression": "Printed {= $V{PAGE_NUMBER} + 0 } for $P{Branch}"},
                ]},
                "columnHeader": {"height": 20, "elements": [
                    {"id": "h1", "type": "staticText", "x": 0, "y": 0, "width": 200, "height": 20, "text": "Branch", "backColor": "#eeeeee", "borderWidth": 1},
                    {"id": "h2", "type": "line", "x": 0, "y": 19, "width": 555, "height": 0},
                ]},
                "detail": {"height": 18, "elements": [
                    {"id": "d1", "type": "textField", "x": 0, "y": 0, "width": 200, "height": 18, "expression": "$F{Branch}", "blankWhenNull": True},
                    {"id": "d2", "type": "textField", "x": 200, "y": 0, "width": 400, "height": 30, "expression": "$F{Amount}", "pattern": "#,##0.00", "align": "right"},
                    {"id": "d3", "type": "chart", "x": 0, "y": 0, "width": 100, "height": 18},
                ]},
                "pageFooter": {"height": 20, "elements": [
                    {"id": "f1", "type": "textField", "x": 0, "y": 0, "width": 100, "height": 20, "expression": "$V{PAGE_COUNT}"},
                    {"id": "f2", "type": "rectangle", "x": 120, "y": 0, "width": 50, "height": 20, "borderWidth": 2},
                ]},
                "groupHeaders": {"BranchGroup": {"height": 20, "elements": [
                    {"id": "g1", "type": "textField", "x": 0, "y": 0, "width": 200, "height": 20, "expression": "$F{Branch}"}]}},
                "groupFooters": {"BranchGroup": {"height": 20, "elements": [
                    {"id": "g2", "type": "textField", "x": 0, "y": 0, "width": 200, "height": 20, "expression": "$V{Total}"}]}},
            },
        },
    }
    base.update(extra)
    return base


@pytest.fixture(scope="module")
def exported():
    return export_jrxml(_definition())


def test_xml_is_well_formed_and_in_schema_order(exported):
    root = ET.fromstring(exported["xml"].split("\n", 2)[2])
    assert root.tag == NS + "jasperReport"
    assert root.get("columnWidth") == "555" and root.get("name") == "Sales_by_Branch"
    order = ["property", "parameter", "queryString", "field", "sortField", "variable", "group", "background", "title",
             "pageHeader", "columnHeader", "detail", "columnFooter", "pageFooter", "lastPageFooter", "summary", "noData"]
    tags = [child.tag.replace(NS, "") for child in root]
    assert tags == sorted(tags, key=order.index)
    assert exported["fileName"] == "Sales_by_Branch.jrxml"


def test_elements_and_expressions(exported):
    root = ET.fromstring(exported["xml"].split("\n", 2)[2])
    texts = [t.text for t in root.iter(NS + "text")]
    assert "Sales & <more>" in texts
    expressions = [e.text for e in root.iter(NS + "textFieldExpression")]
    assert '"Printed " + ($V{PAGE_NUMBER} + 0) + " for " + $P{Branch}' in expressions
    page_total = [tf for tf in root.iter(NS + "textField") if tf.get("evaluationTime") == "Report"]
    assert page_total and page_total[0].find(NS + "textFieldExpression").text == "$V{PAGE_NUMBER}"
    amount = [tf for tf in root.iter(NS + "textField") if tf.get("pattern") == "#,##0.00"][0]
    assert amount.find(NS + "reportElement").get("width") == "355"          # clamped to the column width
    assert amount.find(NS + "textElement").get("textAlignment") == "Right"
    detail = root.find(NS + "detail/" + NS + "band")
    assert detail.get("height") == "30"                                    # grown to hold its elements
    assert root.find(f".//{NS}rectangle/{NS}graphicElement/{NS}pen").get("lineWidth") == "2"
    group = root.find(NS + "group")
    assert group.get("name") == "BranchGroup" and group.find(NS + "groupExpression").text == "$F{Branch}"
    total = [v for v in root.iter(NS + "variable") if v.get("name") == "Total"][0]
    assert total.get("calculation") == "Sum" and total.get("resetGroup") == "BranchGroup"
    assert [v for v in root.iter(NS + "variable") if v.get("name") == "Biggest"][0].get("calculation") == "Highest"
    assert any("chart" in w for w in exported["warnings"])
    assert any("right margin" in w for w in exported["warnings"])


def test_parameters_and_query(exported):
    root = ET.fromstring(exported["xml"].split("\n", 2)[2])
    params = {p.get("name"): p for p in root.iter(NS + "parameter")}
    assert params["Branch"].find(NS + "defaultValueExpression").text == '"Karachi"'
    assert params["Limit"].get("class") == "java.lang.Integer"
    assert params["Title"].get("isForPrompting") == "false"
    query = root.find(NS + "queryString")
    assert query.get("language") == "MongoDbQuery"
    assert "'collectionName' : 'sales'" in query.text and '"$match"' in query.text


def test_round_trip_through_the_importer(exported):
    back = jrxml_import.import_jrxml(exported["xml"])
    report, design = back["report"], back["design"]
    assert report["collection"] == "sales"
    assert '"$sort"' in report["querySource"]
    assert {"Branch", "Limit", "Title"} <= {p["name"] for p in report["parameters"]}
    assert set(report["columns"]) >= {"Branch", "Amount", "Date"}
    assert [g["field"] for g in design["groups"]] == ["Branch"]
    assert {v["name"]: v["calculation"] for v in design["variables"]} == {"Total": "Sum", "Biggest": "Max"}
    assert len(design["bands"]["detail"]["elements"]) == 2                 # the chart was left out
    assert design["bands"]["groupFooters"]["BranchGroup"]["elements"]


def test_sql_report_and_collection_template():
    sql = export_jrxml(_definition(queryLanguage="sql", querySource="SELECT * FROM sales WHERE branch = $P{Branch}"))
    root = ET.fromstring(sql["xml"].split("\n", 2)[2])
    assert root.find(NS + "queryString").get("language") == "SQL"
    templated = export_jrxml(_definition(collection="sales_$P{Branch}_2026"))
    back = jrxml_import.import_jrxml(templated["xml"])
    assert back["report"]["collection"] == "sales_$P{Branch}_2026"


def test_fixture_round_trip():
    first = jrxml_import.import_jrxml(FIXTURE.read_text(encoding="utf-8"))
    definition = {**first["report"], "design": first["design"]}
    again = jrxml_import.import_jrxml(export_jrxml(definition)["xml"])
    assert {p["name"] for p in again["report"]["parameters"]} >= {p["name"] for p in first["report"]["parameters"]}
    assert again["report"]["collection"] == first["report"]["collection"]
    for band in ("columnHeader", "detail"):
        assert len(again["design"]["bands"][band]["elements"]) == len(first["design"]["bands"][band]["elements"])
    assert again["design"]["page"].get("ignorePagination") is True


def test_java_string_escapes():
    assert java_string('a "b"\n\\c') == '"a \\"b\\"\\n\\\\c"'


def test_studio_preview_and_letters_line_up():
    """Studio has no adapter called like ours (Preview stayed blank: "No MongoDB connection"); text sits 2 px in like
    the designer; Arial/Calibri become Jasper's SansSerif (bold kept, same letter widths as our PDF); no "null"."""
    definition = _definition(connectionId="local-cascade")
    definition["design"]["bands"]["noData"] = {"height": 40, "elements": []}           # shown but empty
    definition["design"]["bands"]["title"]["elements"][0]["fontName"] = "Calibri"
    root = ET.fromstring(export_jrxml(definition)["xml"].split("\n", 2)[2])
    props = {p.get("name"): p.get("value") for p in root.findall(f"{NS}property")}
    assert props["com.jaspersoft.studio.data.defaultdataadapter"] == "One Empty Record"
    assert props["com.mongoreporting.data.adapter"] == "local-cascade"
    assert root.get("whenNoDataType") == "AllSectionsNoDetail" and root.find(f"{NS}noData") is None
    title = root.find(f"{NS}title/{NS}band/{NS}staticText")
    assert title.find(f"{NS}textElement/{NS}font").get("fontName") == "SansSerif"
    box = title.find(f"{NS}box")
    assert box.get("leftPadding") == "2" and box.get("rightPadding") == "2" and box.get("bottomPadding") == "0"
    small = {"type": "staticText", "x": 0, "y": 0, "width": 100, "height": 14, "text": "x", "fontSize": 10}
    tight = ET.fromstring(export_jrxml({**definition, "design": {**definition["design"], "bands": {"title": {"height": 14, "elements": [small]}}}})["xml"].split("\n", 2)[2])
    assert tight.find(f"{NS}title/{NS}band/{NS}staticText/{NS}box").get("topPadding") == "1"   # one line must still fit
    assert all(tf.get("isBlankWhenNull") == "true" for tf in root.iter(f"{NS}textField"))
    back = jrxml_import.import_jrxml(export_jrxml(definition)["xml"]) if hasattr(jrxml_import, "import_jrxml") else None
    if back:
        assert back["hints"]["dataAdapter"] == "local-cascade"
