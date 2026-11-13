"""Report definitions: parameters, the saved JSON → ReportDefinition, the repository loader, parameter values."""
from __future__ import annotations

import base64
import copy
import importlib
import json
import math
import os
import pkgutil
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from bson import ObjectId

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
from backend.app.storage.files import normalize_folder_path, report_definitions_directory


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
    if not collection and not is_sql:
        raise ValueError("The MongoDB collection name is required")
    if not is_sql and (not COLLECTION_TEMPLATE.fullmatch(collection) or collection.lower().startswith("system.")):
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
            # Derived parameter (Jasper isForPrompting=false): computed from parameters defined above it.
            if not expression:
                raise ValueError(f"{name} is not asked from the user, so its expression (default value) is required")
            if not jexpr.check_syntax(expression):
                raise ValueError(f"The expression of {name} could not be understood: {expression[:80]}")
            for token_kind, ref in designer_render.tokens_in(expression):
                if token_kind != "P":
                    raise ValueError(f"The expression of {name} may use only $P{{...}} parameters")
                if ref not in {spec.name for spec in parameters}:
                    raise ValueError(f"$P{{{ref}}} in the expression of {name} must be defined before {name}")
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
        ))

    # A collection chosen by a URL parameter must be limited to a fixed list; otherwise a caller could
    # point the report at any collection in the database (e.g. a users collection).
    specs_by_name = {spec.name: spec for spec in parameters}
    for collection_parameter in re.findall(r"\$P\{([A-Za-z_][A-Za-z0-9_]*)\}", collection):
        spec = specs_by_name.get(collection_parameter)
        if spec is None:
            raise ValueError(f"Define the collection parameter {collection_parameter} in the report questions")
        if not spec.prompt:
            raise ValueError(f"The collection-name parameter {collection_parameter} cannot be computed; ask the user and give allowedValues")
        if not spec.allowed_values:
            raise ValueError(
                f"The collection name is built from the parameter {collection_parameter}; this parameter must have allowedValues "
                "(a list of allowed values)"
            )
        bad_values = [value for value in spec.allowed_values if not COLLECTION_PARAM_VALUE.fullmatch(str(value))]
        if bad_values:
            raise ValueError(
                f"The allowedValues of {collection_parameter} may contain only letters, numbers, _ and -: {bad_values[:3]}"
            )

    query_language = str(payload.get("queryLanguage", "aggregation-json")).lower()
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

    columns = payload.get("columns") or ([] if is_sql else _infer_pipeline_columns(pipeline))
    if is_sql and not columns:
        raise ValueError("Read the fields of the SQL query first (Setup → Read fields), or type the column names")
    if not isinstance(columns, list) or not columns or not all(isinstance(column, str) for column in columns):
        raise ValueError("No output columns found in a $project, $group or $count stage; list the fields under Output columns")
    formats = payload.get("formats", sorted(OUTPUT_FORMATS))
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
    display_names = [layout["columnLabels"][column].casefold() for column in columns]
    if len(display_names) != len(set(display_names)):
        raise ValueError("Give every exported column a different display name")

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
        return _resolve_pipeline_parameters(resolved, params)

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


def discover_reports() -> dict[str, ReportDefinition]:
    registry: dict[str, ReportDefinition] = {}
    if os.getenv("REPORTING_LOAD_BUILTIN_REPORTS", "false").lower() == "true":
        import reports
        for module_info in pkgutil.iter_modules(reports.__path__):
            if module_info.name.startswith("_"):
                continue
            module = importlib.import_module(f"reports.{module_info.name}")
            definition = getattr(module, "REPORT", None)
            if not isinstance(definition, ReportDefinition):
                raise RuntimeError(f"reports/{module_info.name}.py must define REPORT = ReportDefinition(...)")
            if definition.report_id in registry:
                raise RuntimeError(f"Duplicate report_id: {definition.report_id}")
            registry[definition.report_id] = definition
    directory = report_definitions_directory()
    if directory.exists():
        loaded_from: dict[str, Path] = {}
        for path in sorted(directory.rglob("*.json")):
            # One broken or hand-edited file must not take every other report (and the app) down.
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(payload, dict):
                    raise ValueError("A report definition must be a JSON object")
                definition = _dynamic_report(payload, path)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                LOG.error("Skipping invalid report definition %s: %s", path, exc)
                continue
            if definition.report_id in loaded_from:
                LOG.warning(
                    "Duplicate report ID %s in %s and %s; using %s",
                    definition.report_id, loaded_from[definition.report_id], path, path,
                )
            loaded_from[definition.report_id] = path
            registry[definition.report_id] = definition
    return registry


def parse_scalar(spec: ParameterSpec, raw: str) -> Any:
    if spec.kind == "date":
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f"{spec.name} must be in YYYY-MM-DD format") from None
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
    return value


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
                values[key] = parse_scalar(spec, str(spec.default))
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
