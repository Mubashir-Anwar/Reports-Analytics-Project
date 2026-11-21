"""A report without data adapter, collection or query (a letter / certificate of fixed text) runs and prints once."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.definitions import _dynamic_report
from backend.app.services import runs


def _static(**extra):
    return {"id": "WELCOME_LETTER", "title": "Welcome letter", "connectionId": "", "collection": "", "querySource": "",
            "parameters": [{"name": "Name", "type": "string"}], "design": {}, **extra}


def test_no_query_is_a_no_data_report():
    definition = _dynamic_report(_static())
    assert definition.query_language == "none" and definition.collection == "" and definition.columns == ()
    assert definition.connection_id == "default"
    assert definition.editor_config["queryLanguage"] == "none"


def test_none_can_be_chosen_and_keeps_typed_fields():
    definition = _dynamic_report(_static(queryLanguage="none", querySource="ignored", columns=["Name"]))
    assert definition.query_language == "none" and definition.columns == ("Name",)


def test_no_data_report_reads_nothing_and_prints_once():
    ctx = SimpleNamespace(settings=SimpleNamespace(export_max_time_ms=1000))   # no database: touching it would fail
    definition = _dynamic_report(_static())
    assert runs.run_report_rows(ctx, definition, {}, "default", max_rows=10, limit_rows=True) == [{}]
    assert list(runs.iter_report_rows(ctx, definition, {}, "default", limit=10)) == [{}]


def test_a_collection_without_query_is_still_an_error():
    with pytest.raises(ValueError, match="(?i)pipeline"):
        _dynamic_report(_static(collection="Orders", connectionId="default"))


def test_an_empty_sql_query_is_a_no_data_report_too():
    definition = _dynamic_report(_static(queryLanguage="sql"))
    assert definition.query_language == "none" and definition.columns == ()


def test_no_design_and_no_query_is_still_a_broken_report():
    with pytest.raises(ValueError, match="(?i)collection"):
        _dynamic_report({"id": "BROKEN"})
