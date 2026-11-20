"""Java-subset expression engine, using real expressions from the user's reports."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.expressions import jexpr  # noqa: E402


def run(text, fields=None, params=None):
    fields, params = fields or {}, params or {}
    return jexpr.evaluate(text, lambda k, n: (fields if k == "F" else params).get(n))


def test_kfs_decimal_format_with_null_guard():
    expr = ('$F{DCF018} == null || $F{DCF018}.equals("") ? null : '
            'new DecimalFormat("#,##0").format(Double.parseDouble($F{DCF018}))')
    assert run(expr, {"DCF018": "1234567"}) == "1,234,567"
    assert run(expr, {"DCF018": ""}) is None
    assert run(expr, {"DCF018": None}) is None


def test_kfs_arithmetic_and_literal_suffix():
    expr = 'new DecimalFormat("#,##0").format(Double.parseDouble($F{A}) - new Double($F{B}) - new Double($F{C}))'
    assert run(expr, {"A": "1000", "B": "100", "C": "50"}) == "850"
    assert run('new java.text.DecimalFormat("PKR #,##0*.").format(new Double($F{R}) * 12)', {"R": "1000"}) == "PKR 12,000*."


def test_string_concat_substring_and_null_guards():
    expr = '($F{a} == null ? "" : $F{a}) + " " + ($F{b} == null ? "" : $F{b})'
    assert run(expr, {"a": "x", "b": None}) == "x "
    card = '"(" + $F{c}.substring(0,4) + "xxxxxx" + $F{c}.substring(10,14) + ")"'
    assert run(card, {"c": "1234567890123456"}) == "(1234xxxxxx1234)"
    assert run('"Year " + $F{n} + " (PKR)"', {"n": 3}) == "Year 3 (PKR)"


def test_nested_ternary_status_map():
    expr = '$F{Status} == 0 ? "InProgress" : $F{Status} == 1 ? "Completed" : $F{Status} == 12 ? "ManuallyDisbursed" : "Unknown"'
    assert run(expr, {"Status": 0}) == "InProgress"
    assert run(expr, {"Status": 1}) == "Completed"
    assert run(expr, {"Status": 12.0}) == "ManuallyDisbursed"
    assert run(expr, {"Status": 99}) == "Unknown"


def test_hbl_date_parse_and_format():
    expr = ('($F{d} != null && !$F{d}.trim().isEmpty() && $F{d}.trim().length() > 10) ? '
            'new java.text.SimpleDateFormat("dd-MM-yyyy HH:mm:ss").format('
            'new java.text.SimpleDateFormat("yyyy-MM-dd\'T\'HH:mm:ss.SSS\'Z\'").parse($F{d}.trim())) : ""')
    assert run(expr, {"d": "2026-03-05T14:07:09.000Z"}) == "05-03-2026 14:07:09"
    assert run(expr, {"d": ""}) == ""
    assert run(expr, {"d": None}) == ""


def test_header_with_params_and_today():
    expr = ('"From Date:  " + new java.text.SimpleDateFormat("dd-MM-yyyy").format($P{StartDate}) + '
            '"   Printed Date : " + new java.text.SimpleDateFormat("dd-MM-yyyy").format(new java.util.Date())')
    out = run(expr, params={"StartDate": datetime(2026, 1, 2)})
    assert out.startswith("From Date:  02-01-2026   Printed Date : ")


def test_yes_no_and_short_circuit():
    assert run('$F{x}.equals("Y") ? "Yes" : "No"', {"x": "Y"}) == "Yes"
    # && must short-circuit so the substring is never evaluated
    assert run('$F{x} != null && $F{x}.substring(5,9).equals("a")', {"x": None}) is False


def test_decimal_and_date_patterns():
    assert jexpr.DecimalFormatter("#,##0.00").format(1234.5) == "1,234.50"
    assert jexpr.DecimalFormatter("0.##").format(2.5) == "2.5"
    assert jexpr.DecimalFormatter("Rs. #,##0.00").format(-5) == "-Rs. 5.00"
    assert jexpr.DateFormatter("MMM-yyyy").format(datetime(2026, 11, 3)) == "Nov-2026"
    assert jexpr.DateFormatter("EEEE, d MMMM yyyy hh:mm a").format(datetime(2026, 11, 9, 15, 4)) == "Monday, 9 November 2026 03:04 PM"


def test_lookup_lists_maps_and_indexing():
    """$lookup results come back as lists of documents: $F{Region}[0].get("Name") (Groovy-style reports)."""
    row = {"Region": [{"Code": "R1", "Name": "North"}], "segment": [], "Detail": {"Reasons": ["Bad CNIC"]},
           "Codes": ["DDESales"], "basic": [{"Name": "Other than Basic Card"}]}
    assert run('$F{Region}[0].get("Name")', row) == "North"
    assert run('$F{Region}.get(0)["Code"]', row) == "R1"
    assert run('$F{Detail}.get("Reasons")[0]', row) == "Bad CNIC"
    assert run('$F{Codes}[0] == "DDESales" ? "Data Entry Sales" : "-"', row) == "Data Entry Sales"
    assert run('$F{Codes}[-1]', row) == "DDESales"
    assert run('$F{segment}[0]', row) is None                    # Groovy: past the end -> null
    with pytest.raises(jexpr.JExprError):
        run('$F{segment}[0].get("Name")', row)                   # null.get() -> blank cell, not a crash
    with pytest.raises(jexpr.JExprError):
        run('$F{segment}.get(0)', row)                           # Java List.get is strict
    basic = ('$F{basic} != null && $F{basic}.size() > 0 ? $F{basic}[0].get("Name").equals("Create Basic Card Only") ? "B"'
             ' : $F{basic}[0].get("Name").equals("Other than Basic Card") ? "S" : "" : ""')
    assert run(basic, row) == "S"
    assert run(basic, {"basic": []}) == ""
    assert run('$F{Detail}.containsKey("Reasons") && !$F{segment}.isEmpty() == false', row) is True
    assert run('$F{Codes}.contains("DDESales")', row) is True


def test_split_and_comments():
    assert run('$F{h}.split(":")[0].trim()', {"h": "0123 : Main"}) == "0123"
    assert run('$F{h}.split(":")[1]', {"h": "E77:Ali Khan"}) == "Ali Khan"
    assert run('"a,b,,".split(",").size()', {}) == 2                # trailing empty strings dropped, like Java
    assert run('"a,b,,".split(",", -1).size()', {}) == 4
    assert run('"".split(",").size()', {}) == 1
    assert run('// an old version\n$F{h} /* trailing */', {"h": "x"}) == "x"
    assert run('"http://x"', {}) == "http://x"                       # // inside a string is text


@pytest.mark.parametrize("bad", [
    "$F{m}.getClass()", "$F{m}.keySet()", "$F{m}.put(\"a\", 1)", "$F{a}[0", "$F{n}[0]",
    "System.exit(0)", "$F{a}.getClass()", "Runtime.getRuntime()", "new java.io.File(\"x\")",
    "__import__('os')", "$F{a}.length", "(1+", "new Object()",
])
def test_rejects_unsafe_or_unsupported(bad):
    with pytest.raises(jexpr.JExprError):
        run(bad, {"a": "x", "m": {"k": "v"}, "n": 5})


def test_java_time_and_groovy_single_quoted_text():
    stamp = {"Done": "2026-03-05T14:07:00+05:00", "Name": "Ali Khan12"}
    assert run('java.time.OffsetDateTime.parse($F{Done}).format(java.time.format.DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm"))',
               stamp) == "05/03/2026 14:07"
    assert run('java.time.LocalDate.parse("2026-03-05").plusDays(1).format(java.time.format.DateTimeFormatter.ofPattern("dd-MM-yyyy"))') == "06-03-2026"
    assert run(r"$F{Name}.replaceAll('(?<=\\D)\\d+$', '')", stamp) == "Ali Khan"
    assert run("'a' + 'bc'") == "abc"


def test_limits():
    with pytest.raises(jexpr.JExprError):
        jexpr.compile_expression("(" * 200 + "1" + ")" * 200)
    with pytest.raises(jexpr.JExprError):
        jexpr.compile_expression("1+" * 3000 + "1")
