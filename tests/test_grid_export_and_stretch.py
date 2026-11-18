"""Jasper-style pixel-perfect Excel/CSV (headings, images, every band) and Stretch With Overflow."""
import base64
import csv
import io
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.rendering import engine as dr  # noqa: E402

COLUMNS = ["Name", "Address"]
LONG = "House 12, Street 5, Gulberg III, Lahore, Punjab, Pakistan, near the big mosque and the market"
ROWS = [{"Name": "Ali", "Address": LONG}, {"Name": "Sara", "Address": "Karachi"}]


def png(color):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def st(x, y, w, text, **extra):
    return {"type": "staticText", "x": x, "y": y, "width": w, "height": 16, "text": text, **extra}


def tf(x, y, w, expression, **extra):
    return {"type": "textField", "x": x, "y": y, "width": w, "height": 16, "expression": expression, **extra}


def design(layout="grid", stretch=True, images=1):
    title = [st(150, 10, 300, "Digital Loan Application", bold=True), {"type": "line", "x": 0, "y": 60, "width": 535, "height": 1}]
    title += [{"type": "image", "x": i * 60, "y": 0, "width": 50, "height": 50, "src": png(c)} for i, c in enumerate(["red", "green", "blue"][:images])]
    raw = {"page": {"sheetLayout": layout}, "bands": {
        "title": {"height": 70, "elements": title},
        "pageHeader": {"height": 20, "elements": [st(0, 0, 200, "Branch report header")]},
        "columnHeader": {"height": 20, "elements": [st(0, 0, 100, "Name", borderWidth=1), st(100, 0, 200, "Address", borderWidth=1)]},
        "detail": {"height": 18, "elements": [tf(0, 0, 100, "$F{Name}", borderWidth=1), tf(100, 0, 200, "$F{Address}", stretch=stretch, borderWidth=1)]},
        "summary": {"height": 20, "elements": [tf(0, 0, 200, "Total: $V{REPORT_COUNT}")]},
        "pageFooter": {"height": 20, "elements": [tf(400, 0, 135, "Page $V{PAGE_NUMBER}")]},
    }}
    return dr.validate_design(raw, COLUMNS, set())


def test_layout_and_stretch_are_validated():
    assert dr.validate_design({"bands": {}}, [], set())["page"]["sheetLayout"] == "table"
    d = design()
    assert d["page"]["sheetLayout"] == "grid"
    assert d["bands"]["detail"]["elements"][1]["stretch"] is True
    assert d["bands"]["title"]["elements"][1].get("stretch") is False  # a line never stretches


def test_grid_xlsx_keeps_headings_images_and_all_rows():
    sheet = load_workbook(io.BytesIO(dr.render_design_xlsx(design(images=3), "T", COLUMNS, ROWS, {}))).active
    values = [c.value for row in sheet.iter_rows() for c in row if c.value is not None]
    for expected in ("Digital Loan Application", "Branch report header", "Name", "Address", "Ali", LONG, "Sara", "Karachi", "Total: 2", "Page 1"):
        assert expected in values
    assert len(sheet._images) == 3  # every image, not just one
    assert sheet.merged_cells.ranges


def test_grid_csv_has_headings_in_layout_order():
    lines = list(csv.reader(io.StringIO(dr.render_design_csv(design(), "T", COLUMNS, ROWS, {}).decode("utf-8-sig"))))
    flat = [" ".join(v for v in line if v) for line in lines]
    assert flat[0] == "Digital Loan Application" and flat[1] == "Branch report header"
    assert flat[2] == "Name Address" and flat[3] == f"Ali {LONG}" and flat[-1] == "Page 1"


def test_table_layout_still_has_one_header_row_plus_title_and_page_header():
    sheet = load_workbook(io.BytesIO(dr.render_design_xlsx(design("table"), "T", COLUMNS, ROWS, {}))).active
    first_column = [sheet.cell(row=r, column=1).value for r in range(1, 6)]
    assert first_column[:4] == ["Digital Loan Application", "Branch report header", "Name", "Ali"]
    reader = list(csv.reader(io.StringIO(dr.render_design_csv(design("table"), "T", COLUMNS, ROWS, {}).decode("utf-8-sig"))))
    assert reader[0] == ["Name", "Address"]


def test_stretch_with_overflow_grows_the_band_only_when_needed():
    d = design()
    engine = dr._Render(d, "T", COLUMNS, ROWS, {}, 1)
    detail = d["bands"]["detail"]
    tall, boxes = engine.band_layout(detail, ROWS[0])
    assert tall > 18 and boxes[0][1] == boxes[1][1]  # the Name cell grows with the Address cell (same line)
    assert engine.band_height(detail, ROWS[1]) == 18
    fixed = design(stretch=False)
    assert dr._Render(fixed, "T", COLUMNS, ROWS, {}, 1).band_height(fixed["bands"]["detail"], ROWS[0]) == 18
    assert dr.render_design_pdf(d, "T", COLUMNS, ROWS, {}).startswith(b"%PDF")


@pytest.mark.parametrize("images", [1, 3])
def test_pdf_draws_every_image(images):
    pdf = dr.render_design_pdf(design(images=images), "T", COLUMNS, ROWS, {})
    assert pdf.count(b"/Subtype /Image") == images
