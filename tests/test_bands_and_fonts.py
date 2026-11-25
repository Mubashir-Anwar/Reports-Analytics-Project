"""Several bands in one section, Java split expressions, pictures from the data, own fonts and Urdu text."""
import base64
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.expressions import jexpr  # noqa: E402
from backend.app.exporters.jrxml_export import export_jrxml  # noqa: E402
from backend.app.importers.jrxml import import_jrxml  # noqa: E402
from backend.app.rendering import engine, rtl, shaping  # noqa: E402
from test_access_rights import make_user, sign_in  # noqa: E402
from test_workspace_and_server import ADMIN, client  # noqa: E402,F401

HEADERS = {"X-Admin-Token": ADMIN}


def text(value, y=0, **more):
    return {"id": f"t{y}{value[:3]}", "type": "staticText", "x": 0, "y": y, "width": 300, "height": 16, "text": value, **more}


def band(*values, **more):
    return {"height": 20, "elements": [text(v) for v in values], **more}


DESIGN = {"bands": {
    "title": band("T1", more=[band("T2")]), "pageHeader": band("H1", more=[band("H2")]),
    "detail": band("D1", more=[band("D2", printWhenExpression='$F{a}.split(",").length > 1'), band("D3", printWhenExpression="$F{n} == 2")]),
    "summary": band("S1", more=[band("S2"), band("S3", printWhenExpression="1 == 2")]),
    "pageFooter": band("F1", more=[band("F2")])}}
ROWS = [{"a": "x", "n": 1}, {"a": "x,y", "n": 2}, {"a": None, "n": 3}]


def words(pdf: bytes) -> list[str]:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as file:
        return [w["text"] for page in file.pages for w in page.extract_words()]


def test_every_band_of_a_section_prints_in_order():
    design = engine.validate_design(DESIGN, ["a", "n"], set())
    out = words(engine.render_design_pdf(design, "t", ["a", "n"], ROWS, {}))
    # the third record has no "a": its condition cannot be answered, so that band is left out
    assert out == ["T1", "T2", "H1", "H2", "D1", "D1", "D2", "D3", "D1", "S1", "S2", "F1", "F2"]
    grid = engine.validate_design({**DESIGN, "page": {"sheetLayout": "grid"}}, ["a", "n"], set())
    assert engine.render_design_csv(grid, "t", ["a", "n"], ROWS, {}).decode("utf-8-sig").split() == out
    assert engine.validate_design(design, ["a", "n"], set())["bands"]["detail"] == design["bands"]["detail"]
    with pytest.raises(ValueError, match="detail 2"):
        engine.validate_design({"bands": {"detail": band("D", more=[band("x", printWhenExpression="$F{zz} == 1")])}}, ["a"], set())
    with pytest.raises(ValueError, match="60 bands"):
        engine.validate_design({"bands": {"detail": band("D", more=[band("x")] * 60)}}, ["a"], set())


JRXML = """<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="R" pageWidth="595" pageHeight="842"
 leftMargin="20" rightMargin="20" topMargin="20" bottomMargin="20">
 <queryString language="MongoDbQuery"><![CDATA[{collectionName: 'c', findQuery: {}, findFields: {a: 1, p: 1}}]]></queryString>
 <field name="a" class="java.lang.String"/><field name="p" class="java.lang.String"/>
 <detail>
  <band height="30"><textField><reportElement x="0" y="0" width="90" height="20"/><textElement><font fontName="Jameel Noori Nastaleeq"/></textElement>
   <textFieldExpression><![CDATA[$F{a}.split(",,,") == null || $F{a}.equals("") || $F{a}.equals(",,,") || ($F{a}.split(",,,").length <= 0) ? "" : $F{a}.split(",,,")[0]]]></textFieldExpression></textField></band>
  <band height="40"><printWhenExpression><![CDATA[$F{a} != null && $F{a}.split(",,,").length>1]]></printWhenExpression>
   <staticText><reportElement x="0" y="0" width="90" height="20"/><text><![CDATA[Second]]></text></staticText></band>
  <band height="50"><image><reportElement x="0" y="0" width="30" height="30"/>
   <imageExpression><![CDATA[$F{p} == null ? null : (new java.io.ByteArrayInputStream(org.apache.commons.codec.binary.Base64.decodeBase64($F{p}.split(",,,")[0].getBytes())))]]></imageExpression></image></band>
 </detail></jasperReport>"""


def png(colour=(255, 0, 0)) -> str:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (4, 4), colour).save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode()


def test_jrxml_with_several_detail_bands_and_java_split():
    made = import_jrxml(JRXML)
    detail = made["design"]["bands"]["detail"]
    assert [detail["height"], *[b["height"] for b in detail["more"]]] == [30, 40, 50]
    assert detail["more"][0]["printWhenExpression"].endswith("length>1")
    assert made["warnings"] == ["Font 'Jameel Noori Nastaleeq' is not on this server: add its .ttf under Fonts and it is used; until then SansSerif prints"]
    assert detail["elements"][0]["fontName"] == "Jameel Noori Nastaleeq"
    design = engine.validate_design(made["design"], ["a", "p"], set())
    pdf = engine.render_design_pdf(design, "t", ["a", "p"], [{"a": "one,,,two", "p": png() + ",,,x"}, {"a": "solo", "p": "not a picture"}], {})
    assert words(pdf) == ["one", "Second", "solo"]
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as file:
        assert sum(len(page.images) for page in file.pages) == 1
    again = import_jrxml(export_jrxml({**made["report"], "design": made["design"]})["xml"])["design"]["bands"]["detail"]
    assert again["elements"][0]["fontName"] == "Jameel Noori Nastaleeq"
    assert [b.get("printWhenExpression") for b in again["more"]] == [b.get("printWhenExpression") for b in detail["more"]]
    assert again["more"][1]["elements"][0]["expression"] == detail["more"][1]["elements"][0]["expression"]


def test_sections_with_one_band_in_jasper_are_joined():
    out = export_jrxml({"id": "R", "design": DESIGN, "columns": ["a", "n"], "querySource": ""})
    assert out["xml"].count("<summary>") == 1 and "1 == 2" in out["xml"]
    assert any("3 bands were joined" in w for w in out["warnings"])


def test_array_length():
    def value(source, a):
        return jexpr.evaluate(source, lambda kind, name: a)
    assert value('$F{a}.split(",").length', "1,2,3") == 3
    assert value('$F{a}.split(",").length > 1 ? "many" : "one"', "1") == "one"
    with pytest.raises(jexpr.JExprError):
        value("$F{a}.length", "text")
    assert not jexpr.check_syntax("$F{a}.size2")


def test_urdu_is_joined_and_put_in_order():
    assert rtl.shape("بب ا") == "ﺑﺐ ﺍ"                        # initial + final, alone
    assert rtl.shape("سلا") == "ﺳﻼ"                               # lam-alef is one letter
    assert rtl.shape("abc") == "abc"
    assert rtl.shape("با", lambda ch: False) == "با"                         # forms the font does not have stay plain letters
    assert rtl.visual("ab") == "ab"
    assert rtl.visual("بت 12,500 (X)") == "(X) 12,500 تب"
    assert rtl.visual("Name بت end") == "Name تب end"


def test_runs_in_drawing_order():
    assert rtl.runs("ab") == [("ab", False)] and rtl.runs("") == []
    assert rtl.runs("بت 12,500 (X)") == [(")", True), ("X", False), (" (", True), ("12,500", False), ("بت ", True)]


def test_urdu_is_shaped_by_the_tables_of_the_font():
    """Marks and ligatures of the font: their places and widths are what HarfBuzz says."""
    pytest.importorskip("uharfbuzz")
    path = next((p for p in (Path("C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")) if p.exists()), None)
    if path is None:
        pytest.skip("no font with Arabic letters on this machine")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    pdfmetrics.registerFont(TTFont("shape-test", str(path)))
    assert shaping.available("shape-test") and not shaping.available("Helvetica")
    [(font, drawn, wide)] = shaping.parts("بَب", "shape-test", 10, "Helvetica")
    assert font == "shape-test" and len(drawn) == 3 and any(d.x_offset or d.y_offset for d in drawn.__shapeData__)
    alone = shaping.width("ب", "shape-test", 10, "Helvetica")
    assert 0 < wide < 2 * alone                                        # joined letters are narrower than two single ones
    assert [p[0] for p in shaping.parts("ب Ω漢", "shape-test", 10, "Helvetica")][0] == "Helvetica"       # not in the font
    lines = shaping.wrap("بت بت بت بت", "shape-test", 10, 2.5 * shaping.width("بت", "shape-test", 10, "Helvetica"), "Helvetica", 1)
    assert lines[0] == "بت" and " ".join(lines) == "بت بت بت بت" and len(lines) == 3


def test_every_glyph_is_drawn_at_its_own_height():
    from reportlab.pdfbase.ttfonts import ShapeData, ShapedStr

    class Canvas(list):
        def drawString(self, x, y, text):  # noqa: N802 - the ReportLab name
            self.append((x, y, str(text), [d.y_offset for d in text.__shapeData__]))
    page = Canvas()
    heights = [0, -100, 200, 200, 0]
    shaping.draw(page, 10, 50, ShapedStr("abcde", shapeData=[ShapeData(i, 500, 0, 0, h, 500) for i, h in enumerate(heights)]), 10)
    assert page == [(10, 50, "a", [0]), (15, 49, "b", [0]), (20, 52, "cd", [0, 0]), (30, 50, "e", [0])]


def font_file() -> bytes:
    import reportlab
    return (Path(reportlab.__file__).parent / "fonts" / "VeraBd.ttf").read_bytes()


def test_own_fonts(client):
    raw = font_file()
    made = client.post("/api/admin/fonts", headers=HEADERS, json={"file": "data:font/ttf;base64," + base64.b64encode(raw).decode(), "fileName": "VeraBd.ttf",
                                                                   "family": "My Vera"})
    assert made.status_code == 200, made.text
    font = made.json()["font"]
    assert font["family"] == "My Vera" and font["style"] == "bold"
    got = client.get(f"/api/admin/fonts/{font['id']}/file", headers=HEADERS)
    assert got.content == raw and got.headers["content-type"] == "font/ttf"
    assert client.get("/api/admin/fonts/../../secrets/file", headers=HEADERS).status_code == 404
    for bad, words_ in ((b"OTTO1234", "PostScript"), (b"wOF2....", "web font"), (b"hello", "TrueType"), (b"\x00\x01\x00\x00junk", "could not be read")):
        refused = client.post("/api/admin/fonts", headers=HEADERS, json={"file": base64.b64encode(bad).decode(), "family": "Bad"})
        assert refused.status_code == 422 and words_ in refused.json()["detail"]
    named = client.post("/api/admin/fonts", headers=HEADERS, json={"file": base64.b64encode(raw).decode(), "family": "<b>"})
    assert named.status_code == 422
    design = engine.validate_design({"bands": {"detail": band("Hello")}}, [], set())
    design["bands"]["detail"]["elements"][0]["fontName"] = "My Vera"
    assert engine.validate_design(design, [], set())["bands"]["detail"]["elements"][0]["fontName"] == "My Vera"
    pdf = engine.render_design_pdf(design, "t", [], [{}], {})
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as file:
        assert "Vera" in file.pages[0].chars[0]["fontname"]
    make_user(client, "dee", ["designer"])
    make_user(client, "vee", ["server"])
    sign_in(client, "vee")
    assert client.get("/api/admin/fonts").status_code == 403
    sign_in(client, "dee")
    assert [f["id"] for f in client.get("/api/admin/fonts").json()["fonts"]] == [font["id"]]
    assert client.request("DELETE", f"/api/admin/fonts/{font['id']}", json={}).status_code == 403     # not added by dee
    assert client.request("DELETE", f"/api/admin/fonts/{font['id']}", headers=HEADERS, json={}).json()["fonts"] == []
    assert client.get(f"/api/admin/fonts/{font['id']}/file", headers=HEADERS).status_code == 404
    assert words(engine.render_design_pdf(design, "t", [], [{}], {})) == ["Hello"]


def test_saved_format_patterns(client):
    def call(method, path="", **body):
        return client.request(method, "/api/admin/patterns" + path, json=body, headers=HEADERS)
    made = call("POST", name="Bank  amount", pattern=" Rs #,##0.00;(Rs #,##0.00) ")
    assert made.status_code == 200, made.text
    item = made.json()["pattern"]
    assert (item["name"], item["pattern"], item["kind"]) == ("Bank amount", "Rs #,##0.00;(Rs #,##0.00)", "number")
    assert call("POST", name="Day", pattern="dd MMM yyyy 'at' HH:mm").json()["pattern"]["kind"] == "date"
    for name, pattern in (("bank AMOUNT", "0"), ("", "0"), ("<b>", "0"), ("n", ""), ("n", "???"), ("n", "'0'"), ("n", "dd of MMM"), ("n", "0" * 61)):
        assert call("POST", name=name, pattern=pattern).status_code == 422, (name, pattern)
    changed = call("PUT", "/" + item["id"], name="Bank amount", pattern="#,##0")
    assert changed.json()["pattern"]["id"] == item["id"] and len(changed.json()["patterns"]) == 2
    assert call("PUT", "/nope", name="x", pattern="0").status_code == 404
    make_user(client, "dee", ["designer"])
    make_user(client, "vee", ["server"])
    sign_in(client, "vee")
    assert client.get("/api/admin/patterns").status_code == 403
    sign_in(client, "dee")
    assert client.request("DELETE", "/api/admin/patterns/" + item["id"], json={}).status_code == 403                 # not added by dee
    own = client.post("/api/admin/patterns", json={"name": "Mine", "pattern": "0.0%"}).json()["pattern"]
    assert client.request("DELETE", "/api/admin/patterns/" + own["id"], json={}).status_code == 200
    assert [p["name"] for p in call("DELETE", "/" + item["id"]).json()["patterns"]] == ["Day"]
