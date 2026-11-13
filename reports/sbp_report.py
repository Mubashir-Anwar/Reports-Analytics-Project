"""MCBCC SBP report definition backed by the supplied Mongo aggregation."""

import copy
import json
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

from backend.app.definitions import ParameterSpec, ReportDefinition

PIPELINE_PATH = Path(__file__).with_name("sbp_pipeline.json")
with PIPELINE_PATH.open("r", encoding="utf-8-sig") as pipeline_file:
    AGGREGATION_CONFIG: dict[str, Any] = json.load(pipeline_file)

try:
    RUN_COMMAND = AGGREGATION_CONFIG["runCommand"]
    COLLECTION = RUN_COMMAND["aggregate"]
    PIPELINE_TEMPLATE: list[dict[str, Any]] = RUN_COMMAND["pipeline"]
except (KeyError, TypeError) as exc:
    raise RuntimeError(
        "reports/sbp_pipeline.json needs runCommand.aggregate and runCommand.pipeline"
    ) from exc

# The report columns are the output fields in the pipeline's final $project.
OUTPUT_FIELDS = tuple(PIPELINE_TEMPLATE[-1]["$project"].keys())


def build_pipeline(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Copy the saved aggregation and inject the validated URL date values."""
    pipeline = copy.deepcopy(PIPELINE_TEMPLATE)
    start = datetime.combine(params["StartDate"], time.min, tzinfo=timezone.utc)
    end = datetime.combine(params["EndDate"], time.max, tzinfo=timezone.utc)

    for stage in pipeline:
        match = stage.get("$match")
        if isinstance(match, dict) and "LastUpdateDateTime" in match:
            date_range = match["LastUpdateDateTime"]
            date_range["$gte"] = start
            date_range["$lte"] = end
            break
    else:
        raise RuntimeError(
            "sbp_pipeline.json has no date $match stage on LastUpdateDateTime"
        )

    return pipeline


REPORT = ReportDefinition(
    report_id="MCBCC_SBP_REPORT",
    title="MCBCC SBP Report",
    collection=COLLECTION,
    parameters=(
        ParameterSpec(
            name="StartDate",
            kind="date",
            label="Start date",
            description="URL se report ki shuru ki tareekh; YYYY-MM-DD.",
            required=True,
        ),
        ParameterSpec(
            name="EndDate",
            kind="date",
            label="End date",
            description="URL se report ki aakhri tareekh; poora din shamil hoga.",
            required=True,
        ),
    ),
    columns=OUTPUT_FIELDS,
    pipeline_builder=build_pipeline,
)
