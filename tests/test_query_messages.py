"""Query mistakes get a message that says what the text looks like and what to do."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.queries.mongo_query import parse_query_source  # noqa: E402


@pytest.mark.parametrize("language,source,expected", [
    ("mongosh", "SELECT branch, amount FROM orders", 'Choose Query language "SQL"'),
    ("mongosh", "with t as (select 1) select * from t", 'Choose Query language "SQL"'),
    ("mongosh", 'db.Orders.find({"Status": "Open"})', "Reports use aggregate, not find"),
    ("mongosh", 'db.getCollection("Orders").findOne({})', "Reports use aggregate, not find"),
    ("mongosh", '{"$match": {}}', "one stage, not a pipeline"),
    ("mongosh", "orders where amount > 5", "not in Mongo Shell form"),
    ("javascript", "x = 1", "pipeline = ["),
    ("mongosh", "   ", "The query is empty"),
])
def test_unreadable_queries_explain_themselves(language, source, expected):
    with pytest.raises(ValueError) as caught:
        parse_query_source(language, source)
    assert expected in str(caught.value)


def test_good_queries_still_parse():
    assert parse_query_source("mongosh", '[{"$match": {}}]') == [{"$match": {}}]
    assert parse_query_source("mongosh", 'db.getCollection("Orders").aggregate([{"$project": {"a": 1}}])') == [{"$project": {"a": 1}}]
