"""Report properties like Jaspersoft Studio's: When No Data Type, Title / Summary on a new page,
Float column footer, the dataset Filter expression, and the Source tab's validate endpoint."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import designer_render as dr  # noqa: E402
from test_workspace_and_server import client, token  # noqa: E402,F401

COLUMNS = ["Name", "Amount"]
ROWS = [{"Name": f"P{i}", "Amount": i} for i in range(5)]


def text(value, y=0, **extra):
    return {"type": "staticText", "x": 0, "y": y, "width": 200, "height": 16, "text": value, **extra}


def design(page=None, **extra):
    return dr.validate_design({"page": page or {}, "bands": {
        "title": {"height": 20, "elements": [text("TITLE")]},
        "pageHeader": {"height": 20, "elements": [text("HEADER")]},
        "detail": {"height": 20, "elements": [{"type": "textField", "x": 0, "y": 0, "width": 200, "height": 16, "expression": "$F{Name}"}]},
        "columnFooter": {"height": 20, "elements": [text("COLFOOT")]},
        "pageFooter": {"height": 20, "elements": [text("FOOTER")]},
        "summary": {"height": 20, "elements": [text("SUMMARY")]},
        "noData": {"height": 20, "elements": [text("NODATA")]},
    }, **extra}, COLUMNS, {"Min"})


def pages(d, rows=ROWS, params=None):
    """Text of every page of the PDF (pageCompression off, so the text is readable)."""
    from reportlab import rl_config
    old, rl_config.pageCompression = rl_config.pageCompression, 0
    try:
        pdf = dr.render_design_pdf(d, "T", COLUMNS, rows, params or {}).decode("latin-1")
    finally:
        rl_config.pageCompression = old
    found = re.findall(r"Tm \((.*?)\) Tj", pdf)
    return pdf.count("/Type /Page\n") + pdf.count("/Type /Page "), found


@pytest.mark.parametrize("mode, expected", [
    ("Auto", ["NODATA"]), ("NoDataSection", ["NODATA"]), ("NoPages", []), ("BlankPage", []),
])
def test_when_no_data_type(mode, expected):
    count, found = pages(design({"whenNoDataType": mode}), rows=[])
    assert count == 1 and found == expected


def test_all_sections_no_detail_prints_the_frame_without_rows():
    _, found = pages(design({"whenNoDataType": "AllSectionsNoDetail"}), rows=[])
    assert "TITLE" in found and "SUMMARY" in found and "NODATA" not in found


def test_title_and_summary_on_their_own_pages():
    count, found = pages(design({"titleNewPage": True, "summaryNewPage": True}))
    assert count == 3
    title_page_end = found.index("HEADER")
    assert found[:title_page_end] == ["TITLE"]           # no page header/footer on the title page
    assert found.count("SUMMARY") == 1 and found.index("SUMMARY") > found.index("P4")


def test_float_column_footer_follows_the_last_row():
    from reportlab import rl_config
    old, rl_config.pageCompression = rl_config.pageCompression, 0
    try:
        fixed = dr.render_design_pdf(design(), "T", COLUMNS, ROWS, {}).decode("latin-1")
        floating = dr.render_design_pdf(design({"floatColumnFooter": True}), "T", COLUMNS, ROWS, {}).decode("latin-1")
    finally:
        rl_config.pageCompression = old
    def y_of(pdf, label):
        return float(re.search(rf"1 0 0 1 [\d.]+ ([\d.]+) Tm \({label}\) Tj", pdf).group(1))
    assert y_of(floating, "COLFOOT") > y_of(fixed, "COLFOOT") + 100   # much higher on the page (PDF y grows upwards)


def test_dataset_filter_expression():
    filtered = design(filterExpression="$F{Amount} >= $P{Min}")
    _, found = pages(filtered, params={"Min": 3})
    assert [t for t in found if t.startswith("P")] == ["P3", "P4"]
    with pytest.raises(ValueError):
        design(filterExpression="$F{Missing} > 1")
    with pytest.raises(ValueError):
        design(filterExpression="((")


def test_validate_endpoint(client):
    good = {"id": "SRC1", "columns": ["Name"], "parameters": [], "design": {"bands": {"detail": {"height": 20, "elements": [
        {"type": "textField", "x": 0, "y": 0, "width": 100, "height": 16, "expression": "$F{Name}"}]}}}}
    ok = client.post("/api/admin/designer/validate", headers=token(), json={"definition": good})
    assert ok.status_code == 200 and ok.json()["design"]["bands"]["detail"]["elements"][0]["expression"] == "$F{Name}"
    bad = {**good, "design": {"bands": {"detail": {"height": 20, "elements": [{"type": "nope"}]}}}}
    refused = client.post("/api/admin/designer/validate", headers=token(), json={"definition": bad})
    assert refused.status_code == 422 and "invalid element type" in refused.json()["detail"]
    assert client.post("/api/admin/designer/validate", headers=token(), json={"definition": {**good, "id": "bad id"}}).status_code == 422
    assert client.post("/api/admin/designer/validate", headers=token(), json={"definition": "x"}).status_code == 422


def test_ignore_pagination_with_more_than_one_tall_page_uses_tall_pages():
    """Thousands of rows: one PDF page cannot hold them (14 400 pt max). They must continue on a few tall pages,
    not on the design's own short page (a 200 pt page made ~400 pages in the KMBL report)."""
    import re as _re
    from backend.app.rendering import engine
    design = engine.validate_design({
        "page": {"width": 600, "height": 200, "ignorePagination": True, "margins": {"top": 10, "bottom": 10, "left": 10, "right": 10}},
        "bands": {"columnHeader": {"height": 20, "elements": [{"type": "staticText", "x": 0, "y": 0, "width": 100, "height": 20, "text": "H"}]},
                  "detail": {"height": 30, "elements": [{"type": "textField", "x": 0, "y": 0, "width": 100, "height": 30, "expression": "$F{A}"}]}},
    }, ["A"], set())
    pdf = engine.render_design_pdf(design, "T", ["A"], [{"A": i} for i in range(2000)], {})
    pages = len(_re.findall(rb"/Type\s*/Page[^s]", pdf))
    assert 4 <= pages <= 6, pages                           # ~60 000 pt of rows / 14 400 pt per page
    assert _re.search(rb"/MediaBox \[ ?0 0 600 14400 ?\]", pdf)
