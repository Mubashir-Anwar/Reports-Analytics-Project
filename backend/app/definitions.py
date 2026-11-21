"""Report definitions: parameters, the saved JSON → ReportDefinition, the repository loader, parameter values."""
from __future__ import annotations

import base64
import copy
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from bson import ObjectId
from fastapi import HTTPException

from backend.app.constants import (
    COLLECTION_PARAM_VALUE,
    COLLECTION_TEMPLATE,
    CONNECTION_ID_PATTERN,
    METADATA_KEYS,
    OUTPUT_FORMATS,
    QUERY_LANGUAGES,
    REPORT_ID_PATTERN,
    SIGNED_LINK_KEYS,
)
from backend.app.expressions import jexpr
from backend.app.jobs.schedule import RELATIVE_RE, resolve_relative
from backend.app.logs import LOG
from backend.app.queries.mongo_query import (
    _date_boundary,
    _infer_pipeline_columns,
    _resolve_pipeline_parameters,
    _validate_pipeline_parameter_names,
    _validate_read_only_pipeline,
    parse_query_source,
    split_jasper_query,
)
from backend.app.queries.sql_query import prepare_sql
from backend.app.rendering import engine as designer_render
from backend.app.storage.files import normalize_folder_path


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    kind: str = "string"  # date, string, integer, boolean
    label: str = ""
    description: str = ""
    required: bool = False
    default: Any = None
    allowed_values: tuple[Any, ...] = ()
    max_length: int = 200
    # Jasper isForPrompting=false + defaultValueExpression: never asked, computed from other parameters.
    prompt: bool = True
    expression: str = ""
    # CNIC, account number…: the run log stores the value masked (12****89).
    sensitive: bool = False
    # Checks on the value: smallest / largest (numbers and dates; a date may be relative like {today}),
    # and for text a regular expression the whole value must match, with the message the user sees.
    min_value: Any = None
    max_value: Any = None
    pattern: str = ""
    pattern_message: str = ""


@dataclass(frozen=True)
class ReportDefinition:
    report_id: str
    title: str
    collection: str
    parameters: tuple[ParameterSpec, ...]
    columns: tuple[str, ...]
    pipeline_builder: Callable[[dict[str, Any]], list[dict[str, Any]]]
    connection_id: str = "default"
    editor_config: dict[str, Any] | None = None
    query_language: str = "mongosh"  # "sql" = a SQL data adapter runs `sql` instead of the pipeline
    sql: Any = None  # queries.sql_query.PreparedSql for SQL reports

    @property
    def folder(self) -> str:
        """The repository folder (like JasperReports Server, the same ID may be used again in another folder)."""
        try:
            return normalize_folder_path((self.editor_config or {}).get("folder", "General"))
        except ValueError:
            return "General"

    @property
    def ref(self) -> str:
        """What names one published report on the server: <folder>/<ID>."""
        return f"{self.folder}/{self.report_id}"


def report_ref(folder: str, report_id: str) -> str:
    return f"{folder}/{report_id}"


def clean_folder(value: Any) -> str | None:
    """A folder sent by a caller ("cascade/KMBLGL/Reports/", "/cascade/…") in the repository's form; "" or None = not given;
    an invalid folder is given back as "" so it can match nothing."""
    if value is None or not str(value).strip().strip("/\\"):
        return None
    try:
        return normalize_folder_path(value)
    except ValueError:
        return ""


class AmbiguousReport(HTTPException):
    """The same report ID is published in more than one folder and the caller did not say which one."""

    def __init__(self, report_id: str, folders: list[str]):
        self.report_id, self.folders = report_id, sorted(folders)
        shown = ", ".join(self.folders[:5]) + (" …" if len(self.folders) > 5 else "")
        super().__init__(status_code=409, detail=f"The report ID {report_id} is in more than one folder ({shown}); "
                                                 f"say which one with Folder (for example Folder={self.folders[0]})")


class ReportRegistry(dict):
    """The published reports, keyed by <folder>/<ID>.

    Looking up a plain ID still works while that ID is used in one folder only; when it is in several folders the
    lookup raises AmbiguousReport (409), so a report is never taken from the wrong folder. Pass the folder (find)
    or the full <folder>/<ID> to choose."""

    def add(self, definition: ReportDefinition) -> ReportDefinition:
        dict.__setitem__(self, definition.ref, definition)
        return definition

    def discard(self, definition: ReportDefinition) -> None:
        dict.pop(self, definition.ref, None)

    def matching(self, report_id: str) -> list[ReportDefinition]:
        return [item for item in dict.values(self) if item.report_id == report_id]

    def find(self, report_id: str, folder: str | None = None, visible: Callable[[ReportDefinition], bool] | None = None) -> ReportDefinition | None:
        """The report with this ID in this folder; without a folder the only one with this ID (among the visible ones)."""
        if folder is not None:
            found = dict.get(self, report_ref(folder, report_id)) if folder else None
            return found if found is not None and (visible is None or visible(found)) else None
        candidates = [item for item in self.matching(report_id) if visible is None or visible(item)]
        if len(candidates) > 1:
            raise AmbiguousReport(report_id, [item.folder for item in candidates])
        return candidates[0] if candidates else None

    def get(self, key: Any, default: Any = None) -> Any:
        key = str(key)
        if "/" in key:
            return dict.get(self, key, default)
        found = self.find(key)
        return default if found is None else found

    def __contains__(self, key: object) -> bool:
        key = str(key)
        return dict.__contains__(self, key) if "/" in key else bool(self.matching(key))

    def __getitem__(self, key: Any) -> ReportDefinition:
        found = self.get(key)
        if found is None:
            raise KeyError(key)
        return found

    def __setitem__(self, key: Any, value: ReportDefinition) -> None:
        self.add(value)

    def update(self, other: Any = (), **extra: Any) -> None:            # keys are always the reports' own refs
        for item in (other.values() if isinstance(other, dict) else [v for _, v in other]):
            self.add(item)


def _normalize_canvas_position(raw: Any, default: dict[str, float]) -> dict[str, float]:
    if raw is None:
        return dict(default)
    if not isinstance(raw, dict):
        raise ValueError("A canvas object position must be a JSON object")
    result: dict[str, float] = {}
    for key, fallback in default.items():
        value = raw.get(key, fallback)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Canvas position {key} must be a valid number")
        result[key] = round(float(value), 2)
    if result["x"] < 0 or result["y"] < 0 or result["w"] < 24 or result["h"] < 20:
        raise ValueError("A canvas object must be inside the page and at least 24x20 pixels")
    if result["x"] + result["w"] > 794 or result["y"] + result["h"] > 1123:
        raise ValueError("A canvas object cannot go outside the A4 page")
    return result


def _dynamic_report(payload: dict[str, Any], source_path: Path | None = None) -> ReportDefinition:
    report_id = str(payload.get("id", "")).strip()
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        raise ValueError("Use only letters, numbers, underscores and hyphens in a report ID")
    title = str(payload.get("title", report_id)).strip() or report_id
    collection = str(payload.get("collection", "")).strip()
    connection_id = str(payload.get("connectionId", "default")).strip()
    folder = normalize_folder_path(payload.get("folder", "General"))
    is_sql = str(payload.get("queryLanguage", "")).lower() == "sql"
    if is_sql:
        collection = ""  # a SQL query names its own tables
    elif not collection and str(payload.get("queryLanguage", "")).lower() != "python":
        jasper = split_jasper_query(payload.get("querySource"))  # a Jasper runCommand names its collection
        collection = jasper[0].strip() if jasper else ""
    # No data (a letter / certificate / form of fixed text): it has a design but no collection or query; it prints once.
    # A file with neither a design nor a query is a broken report, not a page of fixed text.
    no_data = str(payload.get("queryLanguage", "")).lower() == "none" or (
        isinstance(payload.get("design"), dict) and not collection and not str(payload.get("querySource") or "").strip() and not payload.get("pipeline"))
    if no_data:
        collection, connection_id, is_sql = "", connection_id or "default", False
    if not collection and not is_sql and not no_data:
        raise ValueError("The MongoDB collection name is required")
    if not is_sql and not no_data and (not COLLECTION_TEMPLATE.fullmatch(collection) or collection.lower().startswith("system.")):
        raise ValueError("A collection name may contain only letters, numbers, _ - . and $P{parameter}")
    if not CONNECTION_ID_PATTERN.fullmatch(connection_id):
        raise ValueError("Invalid connection ID format")

    raw_parameters = payload.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raise ValueError("parameters must be a JSON array")
    parameters: list[ParameterSpec] = []
    seen_names: set[str] = set()
    for raw in raw_parameters:
        if not isinstance(raw, dict):
            raise ValueError("Each parameter must be a JSON object")
        name = str(raw.get("name", "")).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            raise ValueError(f"Invalid parameter name: {name}")
        if name in SIGNED_LINK_KEYS:
            raise ValueError(f"The parameter name {name} is reserved for signed links; choose another name")
        if name in seen_names:
            raise ValueError(f"Duplicate parameter name: {name}")
        seen_names.add(name)
        kind = str(raw.get("type", raw.get("kind", "string"))).lower()
        if kind not in {"date", "string", "integer", "number", "boolean"}:
            raise ValueError(f"The type of {name} must be date, string, integer, number or boolean")
        allowed = raw.get("allowedValues", [])
        if not isinstance(allowed, list):
            raise ValueError(f"{name}.allowedValues must be a JSON array")
        prompt = raw.get("prompt", True) is not False
        expression = str(raw.get("expression", "") or "").strip()
        if not prompt:
            # Derived parameter (Jasper isForPrompting=false): computed from other parameters (any order, no loops).
            if not expression:
                raise ValueError(f"{name} is not asked from the user, so its expression (default value) is required")
            if not jexpr.check_syntax(expression):
                raise ValueError(f"The expression of {name} could not be understood: {expression[:80]}")
            for token_kind, _ref in designer_render.tokens_in(expression):
                if token_kind != "P":
                    raise ValueError(f"The expression of {name} may use only $P{{...}} parameters")
        rules = _parameter_rules(name, kind, raw)
        parameters.append(ParameterSpec(
            name=name,
            kind=kind,
            label=str(raw.get("label", name)),
            description=str(raw.get("description", "")),
            required=bool(raw.get("required", False)),
            default=raw.get("default"),
            allowed_values=tuple(allowed),
            max_length=min(max(int(raw.get("maxLength", 200)), 1), 2000),
            prompt=prompt,
            expression=expression[:2000] if not prompt else "",
            sensitive=raw.get("sensitive") is True,
            **rules,
        ))
        if prompt and raw.get("default") not in (None, ""):
            try:
                parse_scalar(parameters[-1], str(resolve_default(parameters[-1])))
            except ValueError as exc:
                raise ValueError(f"The default value of {name} is not valid: {exc}") from None

    parameters = _order_computed(parameters)

    # A collection chosen by a parameter must not reach any collection in the database (e.g. a users collection).
    # With fixed text around it (Edith-$P{ProductCode}-PaymentPlans) a typed value is safe: it is limited to
    # letters, numbers, _ and - (no dots, so no system.* names) and the result keeps the fixed shape.
    # A name that is only a parameter ($P{Collection}) has no shape, so it needs a fixed list (allowedValues).
    specs_by_name = {spec.name: spec for spec in parameters}
    fixed_text = re.sub(r"\$P\{[A-Za-z_][A-Za-z0-9_]*\}", "", collection)
    shaped = len(re.sub(r"[^A-Za-z0-9]", "", fixed_text)) >= 3
    for collection_parameter in re.findall(r"\$P\{([A-Za-z_][A-Za-z0-9_]*)\}", collection):
        spec = specs_by_name.get(collection_parameter)
        if spec is None:
            raise ValueError(f"Define the collection parameter {collection_parameter} in the report questions")
        if not spec.prompt:
            raise ValueError(f"The collection-name parameter {collection_parameter} cannot be computed; ask the user and give allowedValues")
        if not spec.allowed_values and not shaped:
            raise ValueError(
                f"The collection name is built from the parameter {collection_parameter}; this parameter must have allowedValues "
                "(a list of allowed values), or the collection name needs fixed text around it (e.g. Orders-$P{Branch})"
            )
        bad_values = [value for value in spec.allowed_values if not COLLECTION_PARAM_VALUE.fullmatch(str(value))]
        if bad_values:
            raise ValueError(
                f"The allowedValues of {collection_parameter} may contain only letters, numbers, _ and -: {bad_values[:3]}"
            )

    query_language = "none" if no_data else str(payload.get("queryLanguage", "aggregation-json")).lower()
    query_source = payload.get("querySource")
    if query_language not in QUERY_LANGUAGES:
        raise ValueError("queryLanguage must be aggregation-json, mongosh, javascript, python or sql")
    if "querySource" in payload and not isinstance(query_source, str):
        raise ValueError("querySource must be a string")
    prepared_sql = None
    if is_sql:
        prepared_sql = prepare_sql(query_source)
        unknown = [name for name in prepared_sql.parameter_names if name not in seen_names]
        if unknown:
            raise ValueError(f"Define the parameter(s) used in the SQL query: {', '.join(unknown)}")
        pipeline: list[dict[str, Any]] = []
    elif no_data:
        pipeline = []
    else:
        pipeline = parse_query_source(query_language, query_source) if isinstance(query_source, str) else payload.get("pipeline")
        if not isinstance(pipeline, list) or not pipeline or not all(isinstance(stage, dict) for stage in pipeline):
            raise ValueError("pipeline must be a non-empty JSON array of stage objects")
        _validate_pipeline_parameter_names(pipeline, seen_names)
        _validate_read_only_pipeline(pipeline)
    date_range = payload.get("dateRange")
    if date_range is not None and is_sql:
        raise ValueError("dateRange is for MongoDB reports; in SQL write the dates into the WHERE clause with $P{…}")
    if date_range is not None:
        if not isinstance(date_range, dict):
            raise ValueError("dateRange must be a JSON object")
        for key in ("field", "startParameter", "endParameter"):
            if not str(date_range.get(key, "")).strip():
                raise ValueError(f"dateRange.{key} is required")
        names = {spec.name: spec for spec in parameters}
        if names.get(date_range["startParameter"], ParameterSpec("_missing")).kind != "date":
            raise ValueError("The startParameter of dateRange must be of type date")
        if names.get(date_range["endParameter"], ParameterSpec("_missing")).kind != "date":
            raise ValueError("The endParameter of dateRange must be of type date")

    columns = payload.get("columns") or ([] if is_sql or no_data else _infer_pipeline_columns(pipeline))
    if is_sql and not columns:
        raise ValueError("Read the fields of the SQL query first (Setup → Read fields), or type the column names")
    if not isinstance(columns, list) or (not columns and not no_data) or not all(isinstance(column, str) for column in columns):
        raise ValueError("No output columns found in a $project, $group or $count stage; list the fields under Output columns")
    columns = list(dict.fromkeys(column.strip() for column in columns if column.strip()))  # the same field twice is one column
    formats =payload.get("formats", sorted(OUTPUT_FORMATS))
    if not isinstance(formats, list) or not formats or any(not isinstance(value, str) or value.lower() not in OUTPUT_FORMATS for value in formats):
        raise ValueError("Report formats must be one or more of csv, xlsx and pdf")
    formats = list(dict.fromkeys(value.lower() for value in formats))
    default_format = str(payload.get("defaultFormat", formats[0])).lower()
    if default_format not in formats:
        raise ValueError("The default format must be one of the enabled report formats")
    raw_layout = payload.get("layout", {})
    if not isinstance(raw_layout, dict):
        raise ValueError("layout must be a JSON object")
    raw_labels = raw_layout.get("columnLabels", {})
    if not isinstance(raw_labels, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in raw_labels.items()):
        raise ValueError("layout.columnLabels must be a JSON object of field names and labels")
    layout = {
        "title": str(raw_layout.get("title", title))[:200],
        "intro": str(raw_layout.get("intro", payload.get("description", "")))[:2000],
        "showTitle": raw_layout.get("showTitle", True) is True,
        "showIntro": raw_layout.get("showIntro", True) is True,
        "columnLabels": {column: (str(raw_labels.get(column, column)).strip() or column)[:200] for column in columns},
    }
    paragraphs = raw_layout.get("paragraphs", [])
    if not isinstance(paragraphs, list) or len(paragraphs) > 20 or any(not isinstance(text, str) or len(text) > 2000 for text in paragraphs):
        raise ValueError("The report canvas allows at most 20 text blocks of up to 2000 characters each")
    layout["paragraphs"] = paragraphs
    raw_positions = raw_layout.get("positions", {})
    if not isinstance(raw_positions, dict):
        raise ValueError("layout.positions must be a JSON object")
    default_positions = {
        "title": {"x": 64, "y": 70, "w": 666, "h": 54},
        "intro": {"x": 64, "y": 132, "w": 666, "h": 62},
        "image": {"x": 64, "y": 210, "w": 280, "h": 170},
        "table": {"x": 64, "y": 650, "w": 666, "h": 390},
    }
    for index in range(len(paragraphs)):
        default_positions[f"paragraph-{index}"] = {"x": 64, "y": 220 + index * 96, "w": 666, "h": 78}
    layout["positions"] = {
        key: _normalize_canvas_position(raw_positions.get(key), default)
        for key, default in default_positions.items()
    }
    parameter_names = {item.name for item in parameters}
    raw_parameter_fields = raw_layout.get("parameterFields", [])
    if not isinstance(raw_parameter_fields, list) or len(raw_parameter_fields) > 100:
        raise ValueError("The canvas allows at most 100 URL value fields")
    parameter_fields: list[dict[str, Any]] = []
    seen_field_ids: set[str] = set()
    for index, field in enumerate(raw_parameter_fields):
        if not isinstance(field, dict):
            raise ValueError("Each canvas URL value field must be a JSON object")
        field_id = str(field.get("id", f"field-{index + 1}"))
        name = str(field.get("name", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", field_id) or field_id in seen_field_ids:
            raise ValueError("Each canvas URL value field ID must be unique and valid")
        if name not in parameter_names:
            raise ValueError(f"The question of a canvas URL value field is not defined: {name}")
        seen_field_ids.add(field_id)
        field_position = _normalize_canvas_position(
            field.get("position"), {"x": 90, "y": 245 + index * 42, "w": 210, "h": 34}
        )
        parameter_fields.append({"id": field_id, "name": name, "position": field_position})
    layout["parameterFields"] = parameter_fields
    image_data = raw_layout.get("imageDataUrl", "")
    if image_data:
        image_match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", image_data) if isinstance(image_data, str) else None
        if not image_match or len(image_data) > 3_000_000:
            raise ValueError("The canvas image must be PNG/JPEG and smaller than 2 MB")
        try:
            base64.b64decode(image_match.group(2), validate=True)
        except ValueError:
            raise ValueError("Invalid canvas image data") from None
        layout["imageDataUrl"] = image_data
    raw_design = payload.get("design")
    design = None
    if raw_design:
        design = designer_render.validate_design(raw_design, columns, parameter_names)
        for dataset in design.get("datasets", []):  # each sub-dataset query must be valid and read-only too
            try:
                compile_dataset(dataset, report_id, connection_id)
            except ValueError as exc:
                raise ValueError(f"Dataset {dataset['name']}: {exc}") from None
    # Excel/CSV headers must differ: "amount" next to "Amount" (or two fields given the same label) becomes "amount (2)"
    taken: set[str] = set()
    for column in columns:
        label = layout["columnLabels"][column]
        unique, number = label, 2
        while unique.casefold() in taken:
            unique, number = f"{label} ({number})", number + 1
        taken.add(unique.casefold())
        layout["columnLabels"][column] = unique

    def build_pipeline(params: dict[str, Any]) -> list[dict[str, Any]]:
        resolved = copy.deepcopy(pipeline)
        if date_range:
            start_name = str(date_range["startParameter"])
            end_name = str(date_range["endParameter"])
            start_value = params.get(start_name)
            end_value = params.get(end_name)
            if not isinstance(start_value, date) or not isinstance(end_value, date):
                raise ValueError("dateRange needs its start/end parameters")
            field = str(date_range["field"])

            def apply_date_range(match_clause: Any) -> bool:
                """Find the configured field within nested $and/$or match clauses."""
                if isinstance(match_clause, dict):
                    condition = match_clause.get(field)
                    if isinstance(condition, dict):
                        condition["$gte"] = _date_boundary(start_value, "start")
                        condition["$lte"] = _date_boundary(end_value, "end")
                        return True
                    for child in match_clause.values():
                        if apply_date_range(child):
                            return True
                elif isinstance(match_clause, list):
                    for child in match_clause:
                        if apply_date_range(child):
                            return True
                return False

            for stage in resolved:
                match = stage.get("$match")
                if apply_date_range(match):
                    break
            else:
                raise ValueError(f"The pipeline has no $match on the dateRange field: {field}")
        try:
            return _resolve_pipeline_parameters(resolved, params)
        except ValueError as exc:
            # a parameter the report has, left empty, is not "undefined": say how to fill it or make it optional
            name = str(exc).removeprefix("Pipeline parameter is not defined: ")
            if name != str(exc) and name in parameter_names:
                raise ValueError(f'Parameter {name} is empty: type a value for it, or use {{"$paramIn": "{name}"}} '
                                 "in the query for an optional filter (empty = all records)") from None
            raise

    normalized = {
        "id": report_id,
        "title": title,
        "description": str(payload.get("description", "")),
        "folder": folder,
        "connectionId": connection_id,
        "collection": collection,
        "parameters": [
            {
                "name": item.name,
                "type": item.kind,
                "label": item.label,
                "description": item.description,
                "required": item.required,
                **({"default": item.default} if item.default is not None else {}),
                **({"allowedValues": list(item.allowed_values)} if item.allowed_values else {}),
                "maxLength": item.max_length,
                **({"prompt": False, "expression": item.expression} if not item.prompt else {}),
                **({"sensitive": True} if item.sensitive else {}),
                **parameter_rules_payload(item),
            }
            for item in parameters
        ],
        "queryLanguage": query_language,
        **({"querySource": query_source} if isinstance(query_source, str) else {}),
        "pipeline": pipeline,
        "columns": columns,
        "formats": formats,
        "defaultFormat": default_format,
        "layout": layout,
        **({"dateRange": date_range} if date_range else {}),
        **({"design": design} if design else {}),
    }
    return ReportDefinition(
        report_id=report_id,
        title=title,
        collection=collection,
        parameters=tuple(parameters),
        columns=tuple(columns),
        pipeline_builder=build_pipeline,
        connection_id=connection_id,
        editor_config=normalized,
        query_language=query_language,
        sql=prepared_sql,
    )


def compile_dataset(dataset: dict[str, Any], report_id: str, connection_id: str) -> ReportDefinition:
    """A sub-dataset (from a validated design) as a small runnable definition: same query checks as a report
    (read-only, parameters defined). Its parameters are never asked from the user; the component passes them."""
    return _dynamic_report({
        "id": f"{report_id}__{dataset['name']}"[:80],
        "title": dataset["name"],
        "collection": dataset.get("collection", ""),
        "connectionId": dataset.get("connectionId") or connection_id,
        "queryLanguage": dataset["queryLanguage"],
        "querySource": dataset["querySource"],
        "columns": list(dataset["fields"]),
        "parameters": [{"name": p["name"], "type": p["type"], "required": False} for p in dataset["parameters"]],
    })


def discover_reports() -> ReportRegistry:
    registry = ReportRegistry()
    from backend.app.storage.store import get_store
    loaded_from: dict[str, str] = {}
    for key, payload in get_store().read_many("reports"):
        # One broken or hand-edited file must not take every other report (and the app) down.
        try:
            if not isinstance(payload, dict):
                raise ValueError("A report definition must be a JSON object")
            definition = _dynamic_report(payload, Path(key))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            LOG.error("Skipping invalid report definition %s: %s", key, exc)
            continue
        if definition.ref in loaded_from:
            LOG.warning("Report %s is stored twice (%s and %s); using %s", definition.ref, loaded_from[definition.ref], key, key)
        loaded_from[definition.ref] = key
        registry.add(definition)
    return registry


def _bound(name: str, kind: str, which: str, value: Any) -> Any:
    """A min / max as stored: a number for integer/number, an ISO date or a relative date ({today-7}) for dates."""
    if value in (None, ""):
        return None
    if kind in ("integer", "number"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"The {which} of {name} must be a number") from None
        if not math.isfinite(number):
            raise ValueError(f"The {which} of {name} must be a finite number")
        return int(number) if kind == "integer" and number.is_integer() else number
    if kind == "date":
        text = str(value).strip()
        if RELATIVE_RE.fullmatch(text):
            return text
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            raise ValueError(f"The {which} of {name} must be a date (YYYY-MM-DD) or like {{today}}, {{monthStart}}, {{today-30}}") from None
    raise ValueError(f"{name}: a {which} can only be set for numbers and dates")


def _parameter_rules(name: str, kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """min / max / pattern / patternMessage of one parameter, checked."""
    min_value = _bound(name, kind, "minimum", raw.get("min"))
    max_value = _bound(name, kind, "maximum", raw.get("max"))
    if kind in ("integer", "number") and min_value is not None and max_value is not None and min_value > max_value:
        raise ValueError(f"The minimum of {name} is larger than its maximum")
    pattern = str(raw.get("pattern", "") or "").strip()
    if pattern:
        if kind != "string":
            raise ValueError(f"{name}: a pattern can only be set for text parameters")
        if len(pattern) > 200:
            raise ValueError(f"The pattern of {name} is too long")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"The pattern of {name} is not a valid regular expression: {exc}") from None
    message = str(raw.get("patternMessage", "") or "").strip()[:200]
    return {"min_value": min_value, "max_value": max_value, "pattern": pattern, "pattern_message": message if pattern else ""}


def parameter_rules_payload(spec: ParameterSpec) -> dict[str, Any]:
    """The checks of a parameter for JSON (only the ones that are set)."""
    return {
        **({"min": spec.min_value} if spec.min_value is not None else {}),
        **({"max": spec.max_value} if spec.max_value is not None else {}),
        **({"pattern": spec.pattern} if spec.pattern else {}),
        **({"patternMessage": spec.pattern_message} if spec.pattern_message else {}),
    }


def resolve_default(spec: ParameterSpec, today: date | None = None) -> Any:
    """The default value to use now: a relative date ({today}, {monthStart}, {today-7}…) becomes a real date."""
    if spec.kind == "date" and isinstance(spec.default, str):
        return resolve_relative(spec.default, today or date.today())
    return spec.default


def run_form_rules(spec: ParameterSpec, today: date | None = None) -> dict[str, Any]:
    """The checks for a run form, with relative date limits turned into real dates."""
    rules = parameter_rules_payload(spec)
    if spec.kind == "date":
        for key in ("min", "max"):
            if key in rules:
                rules[key] = resolve_relative(rules[key], today or date.today())
    return rules


def parse_scalar(spec: ParameterSpec, raw: str) -> Any:
    if spec.kind == "date":
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f"{spec.name} must be in YYYY-MM-DD format") from None
        _check_rules(spec, day)
        return day
    if spec.kind == "integer":
        try:
            value: Any = int(raw)
        except ValueError:
            raise ValueError(f"{spec.name} must be a whole number") from None
    elif spec.kind == "number":
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{spec.name} must be a number") from None
        if not math.isfinite(value):
            raise ValueError(f"{spec.name} must be a finite number")
    elif spec.kind == "boolean":
        mapping = {"1": True, "true": True, "0": False, "false": False}
        try:
            value = mapping[raw.lower()]
        except KeyError:
            raise ValueError(f"{spec.name} must be 1/0 or true/false") from None
    else:
        value = raw
        if len(value) > spec.max_length:
            raise ValueError(f"{spec.name} is too long")
    if spec.allowed_values and value not in spec.allowed_values:
        raise ValueError(f"The value of {spec.name} is not in the allowed list")
    _check_rules(spec, value)
    return value


def _check_rules(spec: ParameterSpec, value: Any) -> None:
    label = spec.label or spec.name
    if spec.kind in ("integer", "number", "date"):
        def bound(raw: Any) -> Any:
            if raw is None:
                return None
            return date.fromisoformat(str(resolve_relative(raw, date.today()))) if spec.kind == "date" else raw
        low, high = bound(spec.min_value), bound(spec.max_value)
        if low is not None and value < low:
            raise ValueError(f"{label} must be {low.isoformat()} or later" if spec.kind == "date" else f"{label} must be at least {low}")
        if high is not None and value > high:
            raise ValueError(f"{label} must be {high.isoformat()} or earlier" if spec.kind == "date" else f"{label} must be at most {high}")
    if spec.pattern and isinstance(value, str) and value and not re.fullmatch(spec.pattern, value):
        raise ValueError(spec.pattern_message or f"{label} is not in the right format")


def validate_parameters(definition: ReportDefinition, query_pairs: list[tuple[str, str]]) -> tuple[dict[str, Any], dict[str, str]]:
    keys = [key for key, _ in query_pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"Duplicate query parameter(s): {', '.join(duplicates)}")
    raw = dict(query_pairs)
    metadata = {key: raw.pop(key, "") for key in METADATA_KEYS}
    specs = {spec.name: spec for spec in definition.parameters if spec.prompt}
    computed = [spec for spec in definition.parameters if not spec.prompt]
    supplied_computed = sorted(set(raw) & {spec.name for spec in computed})
    if supplied_computed:
        raise ValueError(f"These parameters are calculated by the report; do not send them in the URL: {', '.join(supplied_computed)}")
    unknown = sorted(set(raw) - set(specs))
    if unknown:
        raise ValueError(f"Unconfigured parameter(s): {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for key, spec in specs.items():
        if key not in raw or raw[key] == "":
            if spec.required and spec.default is None:
                raise ValueError(f"{key} is a required parameter")
            if spec.default is not None:
                values[key] = parse_scalar(spec, str(resolve_default(spec)))
            continue
        values[key] = parse_scalar(spec, raw[key])
    date_values = [
        values[spec.name]
        for spec in definition.parameters
        if spec.kind == "date" and spec.prompt and spec.name in values
    ]
    if len(date_values) >= 2 and date_values[0] > date_values[1]:
        raise ValueError("The start date must be on or before the end date")
    for spec in computed:
        values[spec.name] = _compute_parameter(spec, values)
    return values, metadata


def _order_computed(parameters: list[ParameterSpec]) -> list[ParameterSpec]:
    """Asked parameters keep their order; computed ones follow, each after the computed ones it uses (like Jasper,
    the order in the list does not matter). Unknown references and loops are refused."""
    names = {spec.name for spec in parameters}
    computed = {spec.name: spec for spec in parameters if not spec.prompt}
    needs: dict[str, set[str]] = {}
    for spec in computed.values():
        refs = {ref for kind, ref in designer_render.tokens_in(spec.expression) if kind == "P"}
        unknown = sorted(refs - names)
        if unknown:
            raise ValueError(f"$P{{{unknown[0]}}} in the expression of {spec.name} is not a parameter of this report")
        if spec.name in refs:
            raise ValueError(f"The expression of {spec.name} uses {spec.name} itself")
        needs[spec.name] = refs & set(computed)
    ordered: list[ParameterSpec] = []
    done: set[str] = set()
    while len(done) < len(computed):
        ready = [name for name in computed if name not in done and needs[name] <= done]
        if not ready:
            loop = sorted(name for name in computed if name not in done)
            raise ValueError(f"These computed parameters depend on each other in a loop: {', '.join(loop)}")
        for name in ready:  # keep the original order among those that are ready
            done.add(name)
            ordered.append(computed[name])
    return [spec for spec in parameters if spec.prompt] + ordered


def _compute_parameter(spec: ParameterSpec, values: dict[str, Any]) -> Any:
    """Evaluate a derived parameter's Java-style expression (e.g. new GregorianCalendar(...).getTime())."""
    try:
        result = jexpr.evaluate(spec.expression, lambda kind, name: values.get(name) if kind == "P" else None)
    except jexpr.JExprError as error:
        raise ValueError(f"Parameter {spec.name} could not be calculated: {error}") from None
    if result is None:
        return None
    try:
        if spec.kind == "date":
            if isinstance(result, jexpr.Calendar):
                result = result.moment
            moment = jexpr.to_datetime(result)
            # Times are UTC, like the rest of the service (MongoDB stores dates in UTC).
            return moment.replace(tzinfo=timezone.utc)
        if spec.kind == "integer":
            return int(jexpr.to_number(result))
        if spec.kind == "number":
            return float(jexpr.to_number(result))
        if spec.kind == "boolean":
            if not isinstance(result, bool):
                raise jexpr.JExprError("true/false expected")
            return result
        return jexpr.java_str(result)
    except (jexpr.JExprError, ValueError, OverflowError) as error:
        raise ValueError(f"The value of parameter {spec.name} is not a valid {spec.kind}: {error}") from None


def _safe_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value
