"""Formatted text in one Static Text / Text Field: bold, italic, underline, colour and highlight on some words, with
fields, parameters, variables and {= … } formulas inside the paragraph (backend/app/rendering/engine.py)."""
import base64
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.rendering import engine as dr  # noqa: E402

COLUMNS = ["Name", "Fee"]
ROWS = [{"Name": "Ali <b>& Sons</b>", "Fee": 1500}]


def design(element):
    return {"page": {}, "bands": {"detail": {"height": 40, "elements": [{"x": 0, "y": 0, "width": 400, "height": 30, **element}]}}}


def first(design_):
    return dr.validate_design(design_, COLUMNS, {"Due"})["bands"]["detail"]["elements"][0]


def test_template_parts_find_formulas_with_braces_and_quotes():
    parts = dr.template_parts('Pay {=$F{Fee} * 2} by {= $F{Fee} > 5 ? "}" : "no"} end {=open')
    assert parts == [("text", "Pay "), ("formula", "$F{Fee} * 2"), ("text", " by "), ("formula", '$F{Fee} > 5 ? "}" : "no"'),
                     ("text", " end {=open")]
    # formatted text: the formula is read without tags and entities
    assert dr.template_parts("{=<b>$F{Fee}</b> &gt; 1}", formatted=True) == [("formula", "$F{Fee} > 1")]


def test_values_are_escaped_in_formatted_text_only():
    resolve = lambda kind, name: ROWS[0].get(name) if kind == "F" else "1 Dec"  # noqa: E731
    html = dr.fill_template("<b>Dear</b> $F{Name}, pay <u>{=$F{Fee} * 2}</u> by $P{Due}", resolve, "", formatted=True)
    assert html == "<b>Dear</b> Ali &lt;b&gt;&amp; Sons&lt;/b&gt;, pay <u>3000</u> by 1 Dec"
    assert dr.fill_template("Dear $F{Name} {=$F{Fee} + 1}", resolve, "#,##0", formatted=False) == "Dear Ali <b>& Sons</b> 1,501"


def test_a_wrong_formula_inside_the_text_is_refused_on_save():
    with pytest.raises(ValueError, match="formula"):
        first(design({"type": "textField", "markup": "html", "expression": "Total: {=$F{Fee} * }"}))
    with pytest.raises(ValueError, match="not defined"):
        first(design({"type": "textField", "markup": "html", "expression": "Total: {=$F{Nope} * 2}"}))
    # long formatted paragraphs are kept whole
    long = "<b>word</b> " * 900
    assert first(design({"type": "staticText", "markup": "html", "text": long}))["text"] == long


def test_highlight_and_colour_go_to_the_pdf_paragraph():
    out = dr.markup_to_paragraph('a <mark>hi</mark> <span style="background-color:#ffff00;color:#ff0000">x</span>', "html")
    assert out == 'a <font backColor="#ffff00">hi</font> <font color="#ff0000" backColor="#ffff00">x</font>'
    assert dr.markup_to_text("<b>Dear</b> <mark>Ali</mark>", "html") == "Dear Ali"


def test_formatted_text_field_prints_values_in_pdf_and_excel():
    pdfplumber = pytest.importorskip("pdfplumber")
    validated = dr.validate_design(design({"type": "textField", "markup": "html",
                                           "expression": "Dear <b>$F{Name}</b>, your fee is <u>{=$F{Fee} * 2}</u> by $P{Due}."}),
                                   COLUMNS, {"Due"})
    pdf = dr.render_design_pdf(validated, "T", COLUMNS, ROWS, {"Due": "1 Dec"})
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        page = doc.pages[0]
        text = page.extract_text()
        bold = {c["text"] for c in page.chars if "Bold" in c["fontname"]}
    assert "Dear Ali <b>& Sons</b>, your fee is 3000 by 1 Dec." in text
    assert {"A", "l", "i", "&", "S"} <= bold                     # the field's value is bold, the words around it are not
    csv = dr.render_design_csv(validated, "T", COLUMNS, ROWS, {"Due": "1 Dec"}).decode("utf-8-sig")
    assert "Dear Ali <b>& Sons</b>, your fee is 3000 by 1 Dec." in csv


def test_import_keeps_a_line_with_bold_words_as_one_formatted_text():
    pytest.importorskip("pdfplumber")
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    from backend.app.importers.design_import import import_design

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=(595, 842))
    x = 60
    for font, color, words in (("Helvetica", "#000000", "Your fee of "), ("Helvetica-Bold", "#000000", "Rs 5,000"),
                               ("Helvetica", "#000000", " is due for "), ("Helvetica", "#cc0000", "{{StudentName}}"),
                               ("Helvetica", "#000000", " this month.")):
        page.setFont(font, 11)
        page.setFillColor(HexColor(color))
        page.drawString(x, 700, words)
        x += page.stringWidth(words, font, 11)
    page.save()
    url = "data:application/pdf;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    result = import_design(url, "fee.pdf")
    texts = [e for e in result["elements"] if e["type"] in ("staticText", "textField")]
    assert len(texts) == 1
    field = texts[0]
    assert field["type"] == "textField" and field["markup"] == "html" and result["fields"] == ["StudentName"]
    assert field["expression"] == ('Your fee of <b>Rs 5,000</b> is due for <span style="color:#cc0000">$F{StudentName}</span>'
                                   " this month.")
