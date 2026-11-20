"""Element properties like Jaspersoft Studio's Appearance and Text Field tabs: Position Type, Stretch Type,
Transparent, Print Repeated Values (with Print In First Whole Band / Print When Group Changes), Remove Line When
Blank, Text Adjust (Scale Font), Rotation, Paragraph, Pattern Expression, and the Format Pattern kinds."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.expressions.jexpr import DecimalFormatter  # noqa: E402
from backend.app.importers.jrxml import import_jrxml  # noqa: E402
from backend.app.rendering import engine as dr  # noqa: E402

COLUMNS = ["Branch", "Name", "Amount", "Currency", "Note"]


def field(expression, x=0, y=0, w=120, h=16, **extra):
    return {"type": "textField", "x": x, "y": y, "width": w, "height": h, "expression": expression, **extra}


def build(detail, height=20, groups=None, **bands):
    raw = {"page": {"height": 300}, "bands": {"detail": {"height": height, "elements": detail}, **bands}}
    if groups:
        raw["groups"] = groups
    return dr.validate_design(raw, COLUMNS, set())


def texts(design, rows):
    from reportlab import rl_config
    old, rl_config.pageCompression = rl_config.pageCompression, 0
    try:
        pdf = dr.render_design_pdf(design, "T", COLUMNS, rows, {}).decode("latin-1")
    finally:
        rl_config.pageCompression = old
    return pdf, re.findall(r"Tm \((.*?)\) Tj", pdf)


def engine(design, rows):
    return dr._Render(design, "T", COLUMNS, rows, {}, 1)


ROWS = [{"Branch": b, "Name": n, "Amount": a, "Currency": c, "Note": t} for b, n, a, c, t in [
    ("LHR", "Ali", 1500, "PKR", ""), ("LHR", "Sara", -20.5, "USD", "late"), ("KHI", "Dan", 7, "PKR", "")]]


def test_new_properties_are_kept_and_unknown_values_fall_back():
    d = build([field("$F{Name}", positionType="Float", stretchType="ContainerBottom", transparent=True, label="name box",
                     key="k1", printRepeatedValues=False, removeLineWhenBlank=True, printInFirstWholeBand=True,
                     printWhenGroupChanges="G", textAdjust="ScaleFont", rotation="Left", lineSpacing="Double",
                     leftIndent=4, tabStopWidth=30, patternExpression='"#,##0"'),
               field("$F{Name}", positionType="Sideways", rotation="Diagonal", lineSpacing="Triple")])
    first, second = d["bands"]["detail"]["elements"]
    assert (first["positionType"], first["stretchType"], first["transparent"], first["label"], first["key"]) == (
        "Float", "ContainerBottom", True, "name box", "k1")
    assert (first["printRepeatedValues"], first["removeLineWhenBlank"], first["printInFirstWholeBand"], first["printWhenGroupChanges"]) == (
        False, True, True, "G")
    assert (first["textAdjust"], first["stretch"], first["rotation"], first["lineSpacing"], first["leftIndent"]) == (
        "ScaleFont", False, "Left", "Double", 4)
    assert (second["positionType"], second["rotation"], second["lineSpacing"], second["printRepeatedValues"]) == ("", "None", "Single", True)
    # the old "stretch" flag still means Stretch Height
    old = build([field("$F{Name}", stretch=True)])["bands"]["detail"]["elements"][0]
    assert old["textAdjust"] == "StretchHeight" and old["stretch"] is True


def test_print_repeated_values_off_prints_a_value_once_per_run():
    d = build([field("$F{Branch}", printRepeatedValues=False), field("$F{Name}", x=130)])
    _pdf, found = texts(d, ROWS)
    assert found.count("LHR") == 1 and found.count("KHI") == 1 and found.count("Sara") == 1


def test_print_when_group_changes_prints_the_value_again():
    groups = [{"name": "G", "field": "Currency"}]
    rows = [{"Branch": "LHR", "Name": n, "Amount": 1, "Currency": c, "Note": ""} for n, c in (("a", "PKR"), ("b", "PKR"), ("c", "USD"))]
    plain = build([field("$F{Branch}", printRepeatedValues=False)], groups=groups)
    _pdf, found = texts(plain, rows)
    assert found.count("LHR") == 1
    again = build([field("$F{Branch}", printRepeatedValues=False, printWhenGroupChanges="G")], groups=groups)
    _pdf, found = texts(again, rows)
    assert found.count("LHR") == 2                       # first row, and the first row of the USD group


def test_print_in_first_whole_band_repeats_on_a_new_page():
    rows = [{"Branch": "LHR", "Name": f"n{i}", "Amount": i, "Currency": "PKR", "Note": ""} for i in range(40)]
    once = build([field("$F{Branch}", printRepeatedValues=False), field("$F{Name}", x=130)])
    pdf, found = texts(once, rows)
    pages = pdf.count("/Type /Page\n") + pdf.count("/Type /Page ")
    assert pages > 1 and found.count("LHR") == 1
    each_page = build([field("$F{Branch}", printRepeatedValues=False, printInFirstWholeBand=True), field("$F{Name}", x=130)])
    _pdf, found = texts(each_page, rows)
    assert found.count("LHR") == pages


def test_remove_line_when_blank_closes_the_line():
    detail = [field("$F{Name}"), field("$F{Note}", y=20, printWhenExpression='$F{Note} != ""', removeLineWhenBlank=True),
              field("$F{Amount}", y=40)]
    d = build(detail, height=60)
    eng = engine(d, ROWS)
    band = d["bands"]["detail"]
    height, boxes = eng.band_layout(band, ROWS[0])                      # no note: its 16 px line closes
    assert height == 44 and boxes[2][0] == 24
    height, boxes = eng.band_layout(band, ROWS[1])                      # a note: all three lines
    assert height == 60 and boxes[2][0] == 40
    # something else on that line keeps it open
    keep = build(detail + [field("$F{Branch}", x=130, y=20)], height=60)
    assert engine(keep, ROWS).band_layout(keep["bands"]["detail"], ROWS[0])[0] == 60


def test_position_and_stretch_types_follow_a_growing_text():
    long_text = "word " * 60
    detail = [field("$F{Note}", w=100, stretch=True),
              field("$F{Name}", y=20, positionType="FixRelativeToTop"),
              field("$F{Name}", y=20, x=200, positionType="Float"),
              field("$F{Name}", y=24, x=330, positionType="FixRelativeToBottom"),
              {"type": "rectangle", "x": 130, "y": 0, "width": 50, "height": 16, "stretchType": "NoStretch"},
              {"type": "rectangle", "x": 190, "y": 0, "width": 50, "height": 16, "stretchType": "RelativeToTallestObject"},
              {"type": "rectangle", "x": 250, "y": 0, "width": 50, "height": 10, "stretchType": "RelativeToBandHeight"},
              {"type": "rectangle", "x": 310, "y": 2, "width": 10, "height": 10, "stretchType": "ContainerBottom"}]
    d = build(detail, height=40)
    eng = engine(d, ROWS)
    height, boxes = eng.band_layout(d["bands"]["detail"], {**ROWS[0], "Note": long_text})
    grown = boxes[0][1] - 16
    assert grown > 20 and height == 40 + grown
    assert boxes[1][0] == 20                      # Fix Relative To Top stays
    assert boxes[2][0] == 20 + grown              # Float moves down
    assert boxes[3][0] == 24 + grown              # Fix Relative To Bottom keeps its distance to the bottom
    assert boxes[4][1] == 16                      # No Stretch
    assert boxes[5][1] == boxes[0][1]             # as tall as the growing text
    assert boxes[6][1] == 10 + grown              # grows as much as the band
    assert boxes[7][0] + boxes[7][1] == height    # reaches the band's bottom


def test_pattern_expression_chooses_the_pattern_per_record():
    d = build([field("$F{Amount}", pattern="#,##0", patternExpression='$F{Currency}.equals("USD") ? "$ #,##0.00;($ #,##0.00)" : "Rs #,##0"')])
    _pdf, found = texts(d, ROWS)
    assert [t.replace(chr(92), "") for t in found] == ["Rs 1,500", "($ 20.50)", "Rs 7"]


def test_number_patterns_from_the_format_dialog():
    assert DecimalFormatter("#,##0.00;(#,##0.00)").format(-1234.5) == "(1,234.50)"
    assert DecimalFormatter("0.00E0").format(12345) == "1.23E4"
    assert DecimalFormatter("0.00E0").format(0.000123) == "1.23E-4"
    assert DecimalFormatter("Rs #,##0.00").format(1500) == "Rs 1,500.00"
    assert dr._numeric_format("#,##0.00;(#,##0.00)") == '#,##0.00;"("#,##0.00")"'
    assert dr._numeric_format("0.00E0") == "0.00E+0"
    assert dr.is_date_pattern("MMM-yy") and dr.is_date_pattern("HH:mm") and not dr.is_date_pattern("#,##0.00")
    assert dr.format_value("2026-11-19T00:00:00", "MMM-yy") == "Nov-26"


def test_scale_font_rotation_transparent_and_paragraph_in_the_pdf():
    d = build([field("$F{Note}", w=60, h=14, textAdjust="ScaleFont", fontSize=12),
               field("$F{Name}", x=100, w=20, h=80, rotation="Left"),
               field("$F{Name}", x=150, w=80, h=16, backColor="#ff0000", transparent=True),
               field("$F{Note}", x=250, w=100, h=80, lineSpacing="Double", leftIndent=10)], height=90)
    pdf, found = texts(d, [{**ROWS[0], "Note": "a long note that does not fit"}])
    sizes = {float(size) for size in re.findall(r"/F\d+ ([\d.]+) Tf", pdf)}
    assert any(size < 10 for size in sizes)                    # scaled down to fit (10 is the page's default font)
    assert re.search(r"0 1 -1 0 [\d.]+ [\d.]+ cm", pdf)        # turned 90 degrees
    assert "1 0 0 rg" not in pdf                               # transparent: the red is not painted
    assert "Ali" in found


def test_scale_font_keeps_short_text_at_its_size():
    d = build([field("$F{Name}", w=120, h=16, textAdjust="ScaleFont", fontSize=12)])
    pdf, _found = texts(d, ROWS[:1])
    assert {float(size) for size in re.findall(r"/F\d+ ([\d.]+) Tf", pdf)} <= {10.0, 12.0}


JRXML = """<?xml version="1.0" encoding="UTF-8"?>
<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="r" pageWidth="595" pageHeight="842" columnWidth="555" leftMargin="20" rightMargin="20" topMargin="20" bottomMargin="20">
  <queryString language="MongoDbQuery"><![CDATA[{"collectionName": "orders", "findQuery": {}}]]></queryString>
  <field name="Branch" class="java.lang.String"/>
  <field name="Amount" class="java.lang.Double"/>
  <detail><band height="30">
    <textField textAdjust="ScaleFont" pattern="#,##0.00">
      <reportElement key="amount" positionType="Float" stretchType="RelativeToTallestObject" mode="Transparent" backcolor="#FF0000"
                     isPrintRepeatedValues="false" isRemoveLineWhenBlank="true" isPrintInFirstWholeBand="true" x="0" y="0" width="100" height="20">
        <printWhenExpression><![CDATA[$F{Amount} > 0]]></printWhenExpression>
      </reportElement>
      <box padding="4"/>
      <textElement rotation="Left"><paragraph lineSpacing="1_1_2" leftIndent="3" spacingBefore="2"/></textElement>
      <textFieldExpression><![CDATA[$F{Amount}]]></textFieldExpression>
      <patternExpression><![CDATA[$F{Amount} > 1000 ? "#,##0" : "#,##0.00"]]></patternExpression>
    </textField>
  </band></detail>
</jasperReport>"""


def test_jrxml_brings_the_properties_along():
    imported = import_jrxml(JRXML)
    element = imported["definition"]["design"]["bands"]["detail"]["elements"][0] if "definition" in imported else \
        imported["design"]["bands"]["detail"]["elements"][0]
    assert (element["positionType"], element["stretchType"], element["transparent"], element["key"]) == (
        "Float", "RelativeToTallestObject", True, "amount")
    assert (element["printRepeatedValues"], element["removeLineWhenBlank"], element["printInFirstWholeBand"]) == (False, True, True)
    assert (element["textAdjust"], element["rotation"], element["lineSpacing"], element["leftIndent"], element["padding"]) == (
        "ScaleFont", "Left", "1_1_2", 3, 4)
    assert element["printWhenExpression"] == "$F{Amount} > 0"
    assert element["patternExpression"] == '$F{Amount} > 1000 ? "#,##0" : "#,##0.00"'
