"""Custom functions (Formulas → Custom Functions): Name(input, ...) defined in the design, usable in every expression."""
import pytest

from backend.app.expressions import jexpr
from backend.app.rendering import engine

FUNCTIONS = [
    {"name": "FullName", "params": ["first", "last"], "body": 'first + " " + last'},
    {"name": "WithTax", "params": ["amount", "rate"], "body": "amount + amount * rate / 100"},
    {"name": "Loop", "params": ["x"], "body": "Loop(x)"},
]


def _values(kind, name):
    return {"First": "Ali", "Last": "Khan", "Amount": 1000}.get(name)


def test_custom_function_calls_and_nesting():
    jexpr.use_functions(FUNCTIONS)
    assert jexpr.evaluate("FullName($F{First}, $F{Last}).toUpperCase()", _values) == "ALI KHAN"
    assert jexpr.evaluate("WithTax($F{Amount}, 17)", _values) == 1170
    assert jexpr.evaluate('FullName("Tax", "" + WithTax(100, 10))', _values) == "Tax 110"
    assert jexpr.called_functions("FullName($F{First}, WithTax(1, 2))") == {"FullName", "WithTax"}


@pytest.mark.parametrize("expression, message", [
    ("Loop(1)", "too deeply"),
    ("Nope(1)", "Unknown function Nope()"),
    ("WithTax(1)", "needs 2 value(s)"),
])
def test_custom_function_errors(expression, message):
    jexpr.use_functions(FUNCTIONS)
    with pytest.raises(jexpr.JExprError, match=message.replace("(", r"\(").replace(")", r"\)")):
        jexpr.evaluate(expression, _values)


@pytest.mark.parametrize("item, message", [
    ({"name": "lower", "params": [], "body": "1"}, "capital letter"),
    ({"name": "Bad", "params": ["X"], "body": "1"}, "small letter"),
    ({"name": "Bad", "params": ["x", "x"], "body": "x"}, "same name"),
    ({"name": "Bad", "params": ["x"], "body": ""}, "empty"),
    ({"name": "Bad", "params": ["x"], "body": "x +"}, "ends too early"),
    ({"name": "Bad", "params": ["x"], "body": "y"}, "Unknown name"),
])
def test_bad_function_definitions(item, message):
    with pytest.raises(jexpr.JExprError, match=message):
        jexpr.compile_functions([item])


def test_design_keeps_functions_and_checks_calls():
    design = {"functions": [FUNCTIONS[1]], "bands": {"detail": {"height": 20, "elements": [
        {"type": "textField", "x": 0, "y": 0, "width": 100, "height": 16, "expression": "=WithTax($F{Amount}, 17)"}]}}}
    cleaned = engine.validate_design(design, ["Amount"], set())
    assert [f["name"] for f in cleaned["functions"]] == ["WithTax"]

    design["bands"]["detail"]["elements"][0]["expression"] = "=Missing($F{Amount})"
    with pytest.raises(ValueError, match=r"Missing\(\) is not defined"):
        engine.validate_design(design, ["Amount"], set())
