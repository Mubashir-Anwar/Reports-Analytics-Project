"""Parameter checks (min / max / pattern), relative date defaults, and field display formats from Setup → Fields."""
from datetime import date, timedelta

import pytest

from backend.app.definitions import _dynamic_report, resolve_default, run_form_rules, validate_parameters
from backend.app.rendering import engine


def _report(parameters):
    return _dynamic_report({
        "id": "T", "title": "T", "collection": "c", "connectionId": "default", "queryLanguage": "mongosh",
        "querySource": 'db.getCollection("c").aggregate([{"$match": {"A": $P{Amount}, "D": $P{From}, "C": $P{Cnic}}}])',
        "columns": ["A"], "parameters": parameters,
    })


PARAMS = [
    {"name": "Amount", "type": "number", "label": "Amount", "min": 1, "max": 1000},
    {"name": "From", "type": "date", "label": "From", "default": "{today-7}", "max": "{today}"},
    {"name": "Cnic", "type": "string", "label": "CNIC", "pattern": r"\d{5}-\d{7}-\d", "patternMessage": "Type the CNIC like 12345-1234567-1"},
]


def test_values_inside_the_rules_are_accepted_and_the_relative_default_is_today_minus_7():
    report = _report(PARAMS)
    values, _ = validate_parameters(report, [("Amount", "50"), ("Cnic", "12345-1234567-1")])
    assert values["Amount"] == 50
    assert values["From"] == date.today() - timedelta(days=7)
    assert values["Cnic"] == "12345-1234567-1"


@pytest.mark.parametrize("pairs, message", [
    ([("Amount", "0")], "Amount must be at least 1"),
    ([("Amount", "5000")], "Amount must be at most 1000"),
    ([("From", (date.today() + timedelta(days=1)).isoformat())], "or earlier"),
    ([("Cnic", "123")], "Type the CNIC like 12345-1234567-1"),
])
def test_values_outside_the_rules_get_a_plain_message(pairs, message):
    with pytest.raises(ValueError, match=message):
        validate_parameters(_report(PARAMS), pairs)


@pytest.mark.parametrize("parameter, message", [
    ({"name": "Amount", "type": "number", "min": 10, "max": 1}, "larger than its maximum"),
    ({"name": "Amount", "type": "number", "min": "abc"}, "must be a number"),
    ({"name": "Amount", "type": "string", "min": 1}, "only be set for numbers and dates"),
    ({"name": "Amount", "type": "number", "pattern": "x"}, "only be set for text"),
    ({"name": "Cnic", "type": "string", "pattern": "(["}, "not a valid regular expression"),
    ({"name": "From", "type": "date", "max": "tomorrow"}, "YYYY-MM-DD"),
    ({"name": "Amount", "type": "number", "default": 5000, "max": 10}, "default value of Amount is not valid"),
])
def test_bad_rules_are_refused_when_saving(parameter, message):
    with pytest.raises(ValueError, match=message):
        _report([parameter])


def test_run_forms_get_real_dates_and_the_definition_keeps_the_relative_ones():
    report = _report(PARAMS)
    spec = {p.name: p for p in report.parameters}
    today = date(2026, 11, 21)
    assert resolve_default(spec["From"], today) == "2026-11-14"
    assert run_form_rules(spec["From"], today) == {"max": "2026-11-21"}
    assert spec["From"].default == "{today-7}" and spec["From"].max_value == "{today}"


def test_field_formats_are_kept_and_used_for_a_field_printed_on_its_own():
    design = {"fieldFormats": {"A": {"pattern": "#,##0.00", "label": "Amount (PKR)"}, "B": {}}, "bands": {"detail": {"height": 20, "elements": [
        {"id": "a", "type": "textField", "x": 0, "y": 0, "width": 100, "height": 16, "expression": "$F{A}"},
        {"id": "b", "type": "textField", "x": 100, "y": 0, "width": 100, "height": 16, "expression": "Total: $F{A}"},
    ]}}}
    cleaned = engine.validate_design(design, ["A", "B"], set())
    assert cleaned["fieldFormats"] == {"A": {"label": "Amount (PKR)", "pattern": "#,##0.00"}}
    pdf = engine.render_design_pdf(cleaned, "T", ["A", "B"], [{"A": 1234.5, "B": "x"}], {})
    assert pdf[:4] == b"%PDF"
    render = engine._Render(cleaned, "T", ["A", "B"], [{"A": 1234.5, "B": "x"}], {}, 1)
    first, second = cleaned["bands"]["detail"]["elements"]
    assert render.pattern_for(first, lambda kind, name: None) == "#,##0.00"
    assert render.pattern_for(second, lambda kind, name: None) == ""
