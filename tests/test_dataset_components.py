"""Phase 3 palette: Table, Crosstab, List, Subreport, Note and the Spider chart."""
import csv
import io
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import designer_render as dr  # noqa: E402

COLUMNS = ["Branch", "Name", "Amount", "Month"]
ROWS = [{"Branch": f"B{i % 3}", "Name": f"Person {i}", "Amount": i * 10, "Month": f"M{i % 4}"} for i in range(60)]

TABLE = {
    "type": "table", "x": 0, "y": 0, "width": 400, "height": 60, "headerBackColor": "#e2e8f0", "zebraColor": "#f8fafc",
    "columns": [
        {"header": "Branch", "expression": "$F{Branch}", "width": 80, "footer": "text", "footerText": "Total"},
        {"header": "Name", "expression": "$F{Name}", "width": 200, "footer": "Count"},
        {"header": "Amount", "expression": "$F{Amount}", "width": 120, "pattern": "#,##0.00", "align": "right", "footer": "Sum"},
    ],
}
CROSSTAB = {"type": "crosstab", "x": 0, "y": 0, "width": 500, "height": 60, "rowField": "Branch", "columnField": "Month",
            "measureField": "Amount", "calculation": "Sum", "pattern": "#,##0"}


def build(summary=None, title=None, detail=None, groups=None, group_footer=None):
    raw = {"page": {"sheetLayout": "grid"}, "groups": groups or [], "bands": {
        "title": {"height": 40, "elements": title or []},
        "detail": {"height": 20 if detail else 0, "elements": detail or []},
        "summary": {"height": 120, "elements": summary or []},
    }}
    if group_footer:
        raw["bands"]["groupFooters"] = {groups[0]["name"]: group_footer}
    return dr.validate_design(raw, COLUMNS, {"Region"})


def csv_rows(design, rows=ROWS, params=None):
    text = dr.render_design_csv(design, "T", COLUMNS, rows, params or {}).decode("utf-8-sig")
    return [[cell for cell in line if cell != ""] for line in csv.reader(io.StringIO(text))]


def pdf_text_count(design, text, rows=ROWS):
    from reportlab import rl_config
    old = rl_config.pageCompression
    rl_config.pageCompression = 0
    try:
        pdf = dr.render_design_pdf(design, "T", COLUMNS, rows, {})
    finally:
        rl_config.pageCompression = old
    return pdf.count(f"({text}) Tj".encode()), pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ")


def test_table_prints_header_rows_and_footer_totals():
    lines = csv_rows(build(summary=[TABLE]))
    assert lines[0] == ["Branch", "Name", "Amount"]
    assert lines[1] == ["B0", "Person 0", "0.00"]
    assert len([line for line in lines if line and line[0] in ("B0", "B1", "B2")]) == 60
    assert lines[-1] == ["Total", "60", "17,700.00"]  # sum of 0..590 step 10


def test_table_footer_is_a_number_in_excel():
    design = build(summary=[TABLE])
    sheet = load_workbook(io.BytesIO(dr.render_design_xlsx(design, "T", COLUMNS, ROWS, {}))).active
    values = [c.value for row in sheet.iter_rows() for c in row if c.value is not None]
    assert 17700 in values and 60 in values


def test_long_table_repeats_its_header_on_every_page():
    rows = [{**r, "Name": f"Person {i} " + "with a long name " * 4} for i, r in enumerate(ROWS * 3)]
    design = build(summary=[{**TABLE, "columns": [{**c, "footer": "none"} for c in TABLE["columns"]]}])
    headers, pages = pdf_text_count(design, "Amount", rows)
    assert pages >= 3 and headers == pages


def test_table_filter_sort_and_group_scope():
    filtered = build(summary=[{**TABLE, "filterExpression": "$F{Branch}.equals(\"B1\")", "sortField": "Amount", "sortOrder": "Descending"}])
    lines = csv_rows(filtered)
    body = [line for line in lines if line and line[0] == "B1"]
    assert len(body) == 20 and body[0][2] == "580.00"
    grouped = build(groups=[{"name": "Branch", "field": "Branch"}],
                    group_footer={"height": 30, "elements": [{**TABLE, "scope": "group"}]})
    totals = [line for line in csv_rows(grouped) if line and line[0] == "Total"]
    assert [t[1] for t in totals] == ["20", "20", "20"]


def test_crosstab_cells_and_totals():
    lines = csv_rows(build(summary=[CROSSTAB]))
    assert lines[0] == ["Branch", "M0", "M1", "M2", "M3", "Total"]
    assert lines[-1][0] == "Total" and lines[-1][-1] == "17,700"
    by_branch = {line[0]: line for line in lines[1:4]}
    assert sum(int(v.replace(",", "")) for v in by_branch["B0"][1:5]) == int(by_branch["B0"][5].replace(",", ""))


def test_crosstab_wider_than_its_box_continues_below():
    narrow = build(summary=[{**CROSSTAB, "width": 240, "rowHeaderWidth": 60, "cellWidth": 60}])
    headers = [line for line in csv_rows(narrow) if line and line[0] == "Branch"]
    assert len(headers) == 2  # M0..M2, then M3 + Total


def test_list_repeats_its_contents_per_record():
    box = {"type": "list", "x": 0, "y": 0, "width": 300, "height": 20, "filterExpression": "$F{Amount} >= 500"}
    inner = {"type": "textField", "x": 4, "y": 2, "width": 200, "height": 16, "expression": "Name: $F{Name}"}
    after = {"type": "staticText", "x": 0, "y": 30, "width": 100, "height": 16, "text": "END"}
    lines = csv_rows(build(summary=[box, inner, after]))
    names = [line[0] for line in lines if line and line[0].startswith("Name:")]
    assert names == [f"Name: Person {i}" for i in range(50, 60)]
    assert lines[-1] == ["END"]


def test_note_is_never_printed():
    design = build(summary=[{"type": "note", "x": 0, "y": 0, "width": 100, "height": 30, "text": "designer note"}, TABLE])
    assert not any("designer note" in cell for line in csv_rows(design) for cell in line)
    assert pdf_text_count(design, "designer note")[0] == 0


def test_subreport_runs_with_mapped_parameters():
    sub_design = dr.validate_design({"bands": {
        "title": {"height": 16, "elements": [{"type": "textField", "x": 0, "y": 0, "width": 200, "height": 16, "expression": "Orders of $P{Who}"}]},
        "detail": {"height": 16, "elements": [{"type": "textField", "x": 10, "y": 0, "width": 100, "height": 16, "expression": "$F{Item}"}]},
    }}, ["Item"], {"Who"})
    calls = []

    def loader(report_id, params):
        calls.append((report_id, params))
        return {"design": sub_design, "title": "Sub", "columns": ["Item"], "rows": [{"Item": f"{params['Who']}-{n}"} for n in range(2)],
                "parameters": params}

    sub = {"type": "subreport", "x": 50, "y": 0, "width": 300, "height": 40, "reportId": "ORDERS",
           "parameters": [{"name": "Who", "expression": "$F{Name}"}]}
    design = build(detail=[{"type": "textField", "x": 0, "y": 0, "width": 50, "height": 16, "expression": "$F{Name}"}, {**sub, "y": 18}])
    token = dr.SUBREPORT_LOADER.set(loader)
    try:
        lines = csv_rows(design, ROWS[:3])
        assert dr.render_design_pdf(design, "T", COLUMNS, ROWS[:3], {}).startswith(b"%PDF")
    finally:
        dr.SUBREPORT_LOADER.reset(token)
    flat = [cell for line in lines for cell in line]
    assert flat[:4] == ["Person 0", "Orders of Person 0", "Person 0-0", "Person 0-1"]
    assert ("ORDERS", {"Who": "Person 2"}) in calls


def test_subreport_without_loader_or_with_error_prints_a_message():
    sub = {"type": "subreport", "x": 0, "y": 0, "width": 300, "height": 20, "reportId": "X"}
    assert any("filled from its own query" in c for line in csv_rows(build(summary=[sub])) for c in line)

    def failing(_id, _params):
        raise ValueError("this report is not published on the server")
    token = dr.SUBREPORT_LOADER.set(failing)
    try:
        assert any("not published" in c for line in csv_rows(build(summary=[sub])) for c in line)
    finally:
        dr.SUBREPORT_LOADER.reset(token)


@pytest.mark.parametrize("bad", [
    {**TABLE, "columns": [{"header": "x", "expression": "$F{Missing}"}]},
    {**CROSSTAB, "rowField": "Missing"},
    {**CROSSTAB, "calculation": "Sum", "measureField": ""},
    {**TABLE, "filterExpression": "$F{Nope} > 1"},
    {"type": "subreport", "x": 0, "y": 0, "width": 10, "height": 10, "parameters": [{"name": "bad name", "expression": ""}]},
])
def test_invalid_components_are_refused(bad):
    with pytest.raises(ValueError):
        build(summary=[bad])


def test_spider_chart_and_grid_only_designs_export():
    chart = {"type": "chart", "x": 0, "y": 0, "width": 300, "height": 200, "chartType": "spider", "categoryField": "Month",
             "valueField": "Amount", "aggregate": "Sum"}
    design = build(summary=[chart, {**TABLE, "y": 0, "x": 0}])
    assert dr.render_design_pdf(design, "T", COLUMNS, ROWS, {}).startswith(b"%PDF")
    assert dr.has_tabular_layout(design)  # only a Table in the Summary: Excel/CSV still work in page layout
