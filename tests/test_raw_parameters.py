"""$P!{X} in a MongoDB query (Jasper: paste text into the query), as in the KMBL Enquiry report:
a computed parameter builds {'$in': [...]} from what the user typed. It must work, and must not let anyone inject code."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.definitions import _dynamic_report, validate_parameters  # noqa: E402
from backend.app.expressions import jexpr  # noqa: E402

# exactly the expression of the KMBL Enquiry report (as it is after reading its JSON)
PRODUCT_EXPRESSION = (
    r"""$P{ProductCodeKMBL} == null || $P{ProductCodeKMBL}.trim().equals("") """ "\n"
    r"""? "{'$ne': 'Product'}" """ "\n"
    r""": "{'$in': ['" + $P{ProductCodeKMBL}.replace("\"", "").trim().replaceAll("\\s*,\\s*", "', '") + "']}" """
)
REPORT = {
    "id": "KMBL_Enquiry_Report", "title": "KMBL Enquiry Report", "connectionId": "default", "collection": "Edith-KMBLSP-Workflows",
    "queryLanguage": "mongosh", "columns": ["Identifier"],
    "querySource": 'db.getCollection("Edith-KMBLSP-Workflows").aggregate([{"$match": {"$and": ['
                   '{"LastUpdateDateTime": {"$gte": $P{StartDate}, "$lte": $P{EndDate}}}, {"ProductCode" : $P!{Product_1}},]}},'
                   '{"$project": {"Identifier": 1}}])',
    # the computed parameter comes BEFORE the one it uses, as in the .mrd file
    "parameters": [
        {"name": "StartDate", "type": "date", "required": True}, {"name": "EndDate", "type": "date", "required": True},
        {"name": "Product_1", "type": "string", "required": False, "prompt": False, "expression": PRODUCT_EXPRESSION},
        {"name": "ProductCodeKMBL", "type": "string", "required": False},
    ],
}


def product_filter(typed):
    definition = _dynamic_report(REPORT)
    pairs = [("StartDate", "2026-01-01"), ("EndDate", "2026-01-31")] + ([("ProductCodeKMBL", typed)] if typed is not None else [])
    parameters, _ = validate_parameters(definition, pairs)
    pipeline = definition.pipeline_builder(parameters)
    return pipeline[0]["$match"]["$and"][1]["ProductCode"]


def test_product_codes_become_a_real_filter():
    assert product_filter("KMBLSP, KMBLGL") == {"$in": ["KMBLSP", "KMBLGL"]}
    assert product_filter(" KMBLSP ,KMBLZL ") == {"$in": ["KMBLSP", "KMBLZL"]}
    assert product_filter("KMBLSP") == {"$in": ["KMBLSP"]}
    assert product_filter(None) == {"$ne": "Product"}       # optional: empty = every product
    assert product_filter("   ") == {"$ne": "Product"}


@pytest.mark.parametrize("typed", [
    "A'], '$where': ['sleep(5000)",                    # balanced: {'$in': ['A'], '$where': ['sleep(5000)']}
    "A'], '$expr': ['1",
    "A'], '$function': ['x",
    "A'], 'other.field': {'$where': ['x",
    "A'], '$where': 'sleep(5000)",                      # unbalanced: cannot even be read
])
def test_typed_text_cannot_inject_operators(typed):
    with pytest.raises(ValueError, match=r"not allowed|could not be read"):
        product_filter(typed)


def test_plain_values_stay_values():
    params = [*REPORT["parameters"][:2], {"name": "Product_1", "type": "string", "required": False, "prompt": False, "expression": '"KMBLSP"'},
              REPORT["parameters"][3]]
    definition = _dynamic_report({**REPORT, "parameters": params})
    parameters, _ = validate_parameters(definition, [("StartDate", "2026-01-01"), ("EndDate", "2026-01-31")])
    assert definition.pipeline_builder(parameters)[0]["$match"]["$and"][1]["ProductCode"] == "KMBLSP"
    assert parameters["StartDate"] == date(2026, 1, 1)


def test_computed_parameters_in_any_order_but_no_loops():
    loop = [{"name": "A", "type": "string", "prompt": False, "expression": '$P{B} + "x"'},
            {"name": "B", "type": "string", "prompt": False, "expression": '$P{A} + "y"'}]
    with pytest.raises(ValueError, match="loop"):
        _dynamic_report({**REPORT, "parameters": [*REPORT["parameters"][:2], *loop]})
    with pytest.raises(ValueError, match="not a parameter"):
        _dynamic_report({**REPORT, "parameters": [*REPORT["parameters"][:2],
                                                  {"name": "C", "type": "string", "prompt": False, "expression": "$P{Nope}"}]})


def test_java_regex_string_methods():
    run = lambda text: jexpr.evaluate(text, lambda kind, name: None)  # noqa: E731
    assert run(r'"A ,  B,C".replaceAll("\\s*,\\s*", "|")') == "A|B|C"
    assert run(r'"2026-01-31".replaceAll("(\\d+)-(\\d+)-(\\d+)", "$3/$2/$1")') == "31/01/2026"
    assert run(r'"a.b.c".replaceFirst("\\.", "-")') == "a-b.c"
    assert run(r'"KMBL".matches("[A-Z]+")') is True and run(r'"KMBL1".matches("[A-Z]+")') is False
