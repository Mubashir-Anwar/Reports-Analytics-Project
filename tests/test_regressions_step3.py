"""Regression tests: bad row values must not crash a report, XLSX stays formula-free,
and the read-fallback $sort follows MongoDB's cross-type order. No MongoDB needed."""
import io
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bson import ObjectId
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.rendering import engine as dr  # noqa: E402
from backend.app.expressions import jexpr  # noqa: E402
import service_names as core  # noqa: E402

NO_REFS = lambda kind, name: None  # noqa: E731


# ---------------------------------------------------------------- expressions never crash
@pytest.mark.parametrize("expression", [
    "Math.sqrt(-1) + 1",
    '1.0e308 * 10.0 + "x"',
    "Math.pow(10.0, 400.0)",
    '"abc".substring(1, 2, 3)',  # wrong argument count -> TypeError inside the evaluator
])
def test_runtime_errors_become_blank_not_crash(expression):
    # Before the fix ValueError/OverflowError/TypeError escaped eval_expression and failed the whole report.
    dr.eval_expression(expression, NO_REFS)


def test_java_results_for_special_numbers():
    assert jexpr.evaluate('"" + Math.sqrt(-1)', NO_REFS) == "NaN"
    assert jexpr.evaluate('"" + (1.0e308 * 10.0)', NO_REFS) == "Infinity"
    assert jexpr.evaluate('"" + (-1.0e308 * 10.0)', NO_REFS) == "-Infinity"
    assert jexpr.evaluate("Math.pow(10.0, 400.0)", NO_REFS) == math.inf
    assert jexpr.evaluate("Math.pow(-10.0, 401.0)", NO_REFS) == -math.inf
    assert math.isnan(jexpr.evaluate("Math.pow(-8.0, 0.5)", NO_REFS))


def test_expression_errors_still_raise_jexpr_error():
    with pytest.raises(jexpr.JExprError):
        jexpr.evaluate("Math.sqrt(-1) > 1 ? 1 : $F{x}.trim()", NO_REFS)


def test_decimal_format_special_numbers():
    assert jexpr.DecimalFormatter("#,##0.00").format(math.inf) == "∞"
    assert jexpr.DecimalFormatter("Rs. #,##0.00").format(-math.inf) == "-Rs. ∞"
    assert jexpr.DecimalFormatter("#,##0.00").format(math.nan) == "NaN"
    assert dr.format_value(math.inf, "#,##0.00") == "∞"


def test_format_value_out_of_range_date_falls_back_to_text():
    # 10**15 ms is far beyond datetime's range; previously OverflowError escaped format_value.
    assert dr.format_value(10**15, "dd/MM/yyyy") == str(10**15)


def test_pdf_render_survives_bad_row_values():
    design = dr.validate_design({
        "page": {"width": 595, "height": 842},
        "bands": {"detail": {"height": 16, "elements": [
            {"type": "textField", "x": 0, "y": 0, "width": 200, "height": 14, "expression": "=Math.sqrt($F{Amount})"},
            {"type": "textField", "x": 210, "y": 0, "width": 100, "height": 14, "expression": "$F{Amount}", "pattern": "#,##0.00"},
        ]}},
    }, ["Amount"], set())
    pdf = dr.render_design_pdf(design, "t", ["Amount"], [{"Amount": -4}, {"Amount": float("inf")}, {"Amount": 9}], {})
    assert pdf.startswith(b"%PDF")


# ---------------------------------------------------------------- XLSX formula injection
FORMULA = '=HYPERLINK("http://example.invalid","click")'


def _first_sheet(data: bytes):
    return load_workbook(io.BytesIO(data)).active


def test_plain_xlsx_keeps_formula_like_text_literal():
    data = core.render_xlsx("T", "", [], ("Name",), [{"Name": FORMULA}], show_intro=False)
    sheet = _first_sheet(data)
    cells = [cell for row in sheet.iter_rows() for cell in row if cell.value == FORMULA]
    assert cells, "value must be kept exactly"
    assert all(cell.data_type == "s" for cell in cells)


def test_design_xlsx_keeps_formula_like_text_literal():
    design = dr.validate_design({
        "page": {"width": 595, "height": 842},
        "bands": {
            "columnHeader": {"height": 20, "elements": [{"type": "staticText", "x": 0, "y": 0, "width": 100, "height": 18, "text": "Name"}]},
            "detail": {"height": 16, "elements": [{"type": "textField", "x": 0, "y": 0, "width": 100, "height": 14, "expression": "$F{Name}"}]},
        },
    }, ["Name"], set())
    data = dr.render_design_xlsx(design, "T", ["Name"], [{"Name": FORMULA}, {"Name": "=1+1"}], {})
    sheet = _first_sheet(data)
    values = {cell.value: cell.data_type for row in sheet.iter_rows() for cell in row if cell.value}
    assert values[FORMULA] == "s"
    assert values["=1+1"] == "s"


# ---------------------------------------------------------------- read-fallback $sort
class _Cursor(list):
    def limit(self, _n):
        return self


class _Collection:
    name = "c"

    class database:  # noqa: N801 - mimics pymongo attribute
        name = "d"

    def __init__(self, docs):
        self.docs = docs

    def find(self, _filter, max_time_ms=None):
        return _Cursor(self.docs)


def _sorted_values(values, direction):
    docs = [{"v": value} if value is not ... else {} for value in values]
    out = core._run_read_pipeline(_Collection(docs), [{"$sort": {"v": direction}}], max_time_ms=1, max_rows=100)
    return [doc.get("v", "<missing>") for doc in out]


def test_sort_nulls_first_ascending_last_descending():
    assert _sorted_values([2, None, 1], 1) == [None, 1, 2]
    assert _sorted_values([2, None, 1], -1) == [2, 1, None]


def test_sort_mixed_types_uses_bson_order():
    oid = ObjectId("000000000000000000000001")
    when = datetime(2025, 1, 1, tzinfo=timezone.utc)
    shuffled = [when, True, "b", oid, 3, None, {"a": 1}, 1.5, "a"]
    assert _sorted_values(shuffled, 1) == [None, 1.5, 3, "a", "b", {"a": 1}, oid, True, when]


def test_sort_aware_and_naive_dates_do_not_crash():
    aware = datetime(2025, 1, 1, 5, 0, tzinfo=timezone(timedelta(hours=5)))  # 00:00 UTC
    naive_later = datetime(2025, 1, 1, 1, 0)  # naive values from pymongo are UTC
    assert _sorted_values([naive_later, aware], 1) == [aware, naive_later]


def test_sort_missing_equals_null_and_is_stable():
    docs = [{"k": 1, "v": None}, {"k": 2}, {"k": 3, "v": 0}, {"k": 4, "v": None}]
    out = core._run_read_pipeline(_Collection(docs), [{"$sort": {"v": 1}}], max_time_ms=1, max_rows=100)
    assert [doc["k"] for doc in out] == [1, 2, 4, 3]


def test_sort_arrays_use_min_ascending_and_max_descending():
    assert _sorted_values([[5, 1], [3], []], 1) == [[], [5, 1], [3]]
    assert _sorted_values([[5, 1], [3], []], -1) == [[5, 1], [3], []]


def test_sort_multiple_keys():
    docs = [{"a": 1, "b": "y"}, {"a": 2, "b": "x"}, {"a": 1, "b": "x"}]
    out = core._run_read_pipeline(_Collection(docs), [{"$sort": {"a": -1, "b": 1}}], max_time_ms=1, max_rows=100)
    assert [(doc["a"], doc["b"]) for doc in out] == [(2, "x"), (1, "x"), (1, "y")]
