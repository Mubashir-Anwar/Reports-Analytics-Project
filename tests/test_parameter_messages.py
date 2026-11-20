"""A parameter the report has but that was left empty is not "undefined": the message says how to fill it."""
import pytest

from backend.app.definitions import _dynamic_report, validate_parameters


def _report(parameters):
    return _dynamic_report({
        "id": "T", "title": "T", "collection": "c", "connectionId": "default", "queryLanguage": "mongosh",
        "querySource": 'db.getCollection("c").aggregate([{"$match": {"Occupation": $P{OCCUPATION}}}])',
        "columns": ["Occupation"], "parameters": parameters,
    })


def test_an_empty_optional_parameter_says_it_is_empty_and_how_to_make_it_a_filter():
    report = _report([{"name": "OCCUPATION", "type": "string", "label": "OCCUPATION", "required": False}])
    with pytest.raises(ValueError, match=r'Parameter OCCUPATION is empty: .*"\$paramIn": "OCCUPATION"'):
        report.pipeline_builder(validate_parameters(report, [])[0])
    filled = report.pipeline_builder(validate_parameters(report, [("OCCUPATION", "Doctor")])[0])
    assert filled == [{"$match": {"Occupation": "Doctor"}}]


def test_a_parameter_missing_from_the_report_is_still_refused():
    with pytest.raises(ValueError, match="OCCUPATION"):
        _report([])
