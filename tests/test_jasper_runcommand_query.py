"""A Jaspersoft Studio MongoDbQuery ({"runCommand": {"aggregate", "pipeline"}}) is accepted as the query text."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import service_names as core  # noqa: E402

RUN_COMMAND = """{
    "runCommand": {
        "aggregate": "Edith-KMBLZL-Workflows",
        "pipeline": [
          { "$match": { "Identifier": { "$eq": $P{Identifier} } } },
          { "$lookup": { "from": "Quantum-KMBLSPBranches", "localField": "Fields.KMBLZLKMBL_0009",
                         "foreignField": "Code", "as": "Fields.KMBLZLKMBL_0009" } },
          { "$project": {
              "Identifier": 1.0,
              "Fields.KMBLZLKMBL_0037": "$Fields.KMBLZLKMBL_0037",
              "Fields.KMBLZLKMBL_0866": { "$cond": { "if": { "$and": [
                  { "$ne": ["$Fields.KMBLZLKMBL_0866", null] }, { "$ne": ["$Fields.KMBLZLKMBL_0866", ""] } ] },
                  "then": { "$dateToString": { "format": "%d-%m-%Y", "date": { "$toDate": "$Fields.KMBLZLKMBL_0866" } } },
                  "else": null } },
              "Fields.KMBLZLKMBL_0009": { "$arrayElemAt": ["$Fields.KMBLZLKMBL_0009.Name", 0.0] }
          } }
        ]
    }
}"""


@pytest.mark.parametrize("language", ["mongosh", "aggregation-json", "javascript"])
def test_run_command_query_parses_in_every_text_mode(language):
    pipeline = core.parse_query_source(language, RUN_COMMAND)
    assert [next(iter(stage)) for stage in pipeline] == ["$match", "$lookup", "$project"]
    assert pipeline[0]["$match"]["Identifier"]["$eq"] == {"$param": "Identifier"}
    assert pipeline[2]["$project"]["Fields.KMBLZLKMBL_0866"]["$cond"]["else"] is None
    assert core.pipeline_parameter_names(pipeline) == ["Identifier"]


def test_split_gives_the_collection_and_ignores_other_styles():
    collection, literal = core.split_jasper_query(RUN_COMMAND)
    assert collection == "Edith-KMBLZL-Workflows" and literal.startswith("[")
    single_quotes = "{ runCommand: { aggregate: 'Orders', pipeline: [ { $match: {} } ], cursor: {} } }"
    assert core.split_jasper_query(single_quotes)[0] == "Orders"
    assert core.split_jasper_query('db.getCollection("x").aggregate([{"$match": {}}])') is None
    assert core.split_jasper_query('[{"$match": {}}]') is None


def test_report_takes_its_collection_from_the_run_command():
    definition = core._dynamic_report({
        "id": "KMBL_RUNCOMMAND", "title": "t", "collection": "", "connectionId": "default", "queryLanguage": "mongosh",
        "querySource": RUN_COMMAND, "columns": ["Identifier"],
        "parameters": [{"name": "Identifier", "type": "string", "required": True}],
    })
    assert definition.collection == "Edith-KMBLZL-Workflows"


def test_run_command_with_write_stage_is_still_refused():
    bad = '{"runCommand": {"aggregate": "x", "pipeline": [{"$match": {}}, {"$out": "y"}]}}'
    with pytest.raises(ValueError):
        core._dynamic_report({"id": "BAD", "title": "t", "collection": "", "connectionId": "default",
                              "queryLanguage": "mongosh", "querySource": bad, "columns": ["a"], "parameters": []})
