"""Phase 1 palette and properties: Ellipse, Frame, Break, Barcode, Print When, Evaluation Time, borders, padding."""
import csv
import io
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.rendering import engine as dr  # noqa: E402

COLUMNS = ["Name", "Amount"]
ROWS = [{"Name": "A", "Amount": 100}, {"Name": "B", "Amount": 300}]
TOTAL = [{"name": "Amount_TOTAL", "calculation": "Sum", "field": "Amount", "resetType": "Report"}]


def tf(x, y, w, expression, **extra):
    return {"type": "textField", "x": x, "y": y, "width": w, "height": 16, "expression": expression, **extra}


def build(detail, layout="grid", title=None, footer=None):
    raw = {"page": {"sheetLayout": layout}, "variables": TOTAL, "bands": {
        "title": {"height": 70, "elements": title or []},
        "detail": {"height": 40, "elements": detail},
        "pageFooter": {"height": 20, "elements": footer or []},
    }}
    return dr.validate_design(raw, COLUMNS, {"Id"})


def pages(pdf: bytes) -> int:
    return pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ")


def test_new_element_types_validate_with_their_settings():
    d = build([], title=[
        {"type": "ellipse", "x": 0, "y": 0, "width": 60, "height": 40, "backColor": "#ffcc00", "borderWidth": 1},
        {"type": "frame", "x": 70, "y": 0, "width": 200, "height": 60, "borderWidth": 1, "lineStyle": "dashed"},
        {"type": "barcode", "x": 300, "y": 0, "width": 60, "height": 60, "barcodeType": "qr", "expression": "LOAN-$P{Id}"},
        {"type": "barcode", "x": 380, "y": 0, "width": 150, "height": 50, "barcodeType": "nope", "expression": "X"},
        {"type": "break", "x": 0, "y": 65, "width": 100, "height": 1},
    ])
    ellipse, frame, qr, fallback, brk = d["bands"]["title"]["elements"]
    assert (ellipse["type"], frame["lineStyle"], qr["barcodeType"], fallback["barcodeType"], brk["type"]) == ("ellipse", "dashed", "qr", "qr", "break")
    assert frame["padding"] == 2 and frame["borders"] is None and frame["printWhenExpression"] == ""


@pytest.mark.parametrize("bad", [
    {"type": "barcode", "expression": "$F{Missing}"},
    {"type": "textField", "expression": "x", "printWhenExpression": "$F{Missing} > 1"},
    {"type": "textField", "expression": "x", "printWhenExpression": "(("},
])
def test_bad_expressions_are_refused(bad):
    with pytest.raises(ValueError):
        build([{"x": 0, "y": 0, "width": 50, "height": 16, **bad}])


def test_all_new_elements_render_in_pdf_excel_and_csv():
    d = build(
        [tf(0, 0, 100, "$F{Name}", borders={"bottom": 1}, padding=4)],
        title=[
            {"type": "ellipse", "x": 0, "y": 0, "width": 60, "height": 40, "backColor": "#ffcc00", "borderWidth": 1},
            {"type": "frame", "x": 70, "y": 0, "width": 200, "height": 60, "borderWidth": 1, "lineStyle": "dotted"},
            {"type": "barcode", "x": 300, "y": 0, "width": 60, "height": 60, "barcodeType": "qr", "expression": "LOAN-$P{Id}"},
            {"type": "barcode", "x": 380, "y": 0, "width": 150, "height": 50, "barcodeType": "code128", "expression": "ABC12345"},
            {"type": "barcode", "x": 380, "y": 55, "width": 150, "height": 14, "barcodeType": "ean13", "expression": "not digits"},
        ])
    assert dr.render_design_pdf(d, "T", COLUMNS, ROWS, {"Id": "7"}).startswith(b"%PDF")
    sheet = load_workbook(io.BytesIO(dr.render_design_xlsx(d, "T", COLUMNS, ROWS, {"Id": "7"}))).active
    values = [c.value for row in sheet.iter_rows() for c in row if c.value is not None]
    assert {"LOAN-7", "ABC12345", "A", "B"} <= set(values)
    assert "LOAN-7" in dr.render_design_csv(d, "T", COLUMNS, ROWS, {"Id": "7"}).decode("utf-8-sig")


def test_break_puts_each_record_on_its_own_page():
    with_break = build([tf(0, 0, 100, "$F{Name}"), {"type": "break", "x": 0, "y": 30, "width": 100, "height": 1}])
    without = build([tf(0, 0, 100, "$F{Name}")])
    assert pages(dr.render_design_pdf(with_break, "T", COLUMNS, ROWS, {})) == 2
    assert pages(dr.render_design_pdf(without, "T", COLUMNS, ROWS, {})) == 1


def test_print_when_hides_an_element():
    d = build([tf(0, 0, 100, "$F{Name}"), {"type": "staticText", "x": 200, "y": 0, "width": 80, "height": 16, "text": "BIG",
                                            "printWhenExpression": "$F{Amount} > 150"}], layout="grid")
    lines = list(csv.reader(io.StringIO(dr.render_design_csv(d, "T", COLUMNS, ROWS, {}).decode("utf-8-sig"))))
    assert [" ".join(v for v in line if v) for line in lines] == ["A", "B BIG"]


def test_percentage_and_page_x_of_y_use_report_end_values():
    d = build([tf(0, 0, 100, "$F{Name}"), tf(100, 0, 100, "=$F{Amount} / $V{Amount_TOTAL}", pattern="0.00%", evaluationTime="Auto"),
               {"type": "break", "x": 0, "y": 30, "width": 100, "height": 1}],
              footer=[tf(300, 0, 200, "Page $V{PAGE_NUMBER} of $V{PAGE_COUNT}")])
    text = dr.render_design_csv(d, "T", COLUMNS, ROWS, {}).decode("utf-8-sig")
    assert "25.00%" in text and "75.00%" in text
    sheet = load_workbook(io.BytesIO(dr.render_design_xlsx(d, "T", COLUMNS, ROWS, {}))).active
    cell = next(c for row in sheet.iter_rows() for c in row if c.value == 0.25)
    assert cell.number_format == "0.00%"
    assert "Page 2 of 2" in text
    engine = dr._Render(d, "T", COLUMNS, ROWS, {}, 1)
    element = d["bands"]["detail"]["elements"][1]
    engine.accumulate(ROWS[0])
    assert engine.evaluate(element, ROWS[0]) == "100.00%"  # no report-end values yet: running total
    engine.final = {"Amount_TOTAL": 400.0}
    assert engine.evaluate(element, ROWS[0]) == "25.00%"
