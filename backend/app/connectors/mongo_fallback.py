"""In-process evaluation of simple pipeline stages (used when the server refuses a stage)."""
from __future__ import annotations

import copy
import json
import math
from datetime import date, datetime, time as daytime, timezone
from decimal import Decimal
from typing import Any

from bson import ObjectId
from pymongo.errors import PyMongoError

from backend.app.connectors.errors import MongoReadAccessError


def _path_values(value: Any, path: str) -> list[Any]:
    """Resolve a dotted Mongo field path, including paths through arrays."""
    parts = path.split(".") if path else []

    def walk(current: Any, remaining: list[str]) -> list[Any]:
        if not remaining:
            return [current]
        if isinstance(current, list):
            found: list[Any] = []
            for item in current:
                found.extend(walk(item, remaining))
            return found
        if not isinstance(current, dict) or remaining[0] not in current:
            return []
        return walk(current[remaining[0]], remaining[1:])

    return walk(value, parts)


def _row_value(row: dict[str, Any], key: str) -> Any:
    """Column value; dotted names like 'Fields.HBLIPF194' reach into nested documents."""
    if key in row:
        return row[key]
    if "." in key:
        return _path_value(row, key)
    return None


def _path_value(value: Any, path: str) -> Any:
    parts = path.split(".") if path else []
    missing = object()

    def walk(current: Any, remaining: list[str]) -> Any:
        if not remaining:
            return current
        if isinstance(current, list):
            found = [walk(item, remaining) for item in current]
            return [item for item in found if item is not missing]
        if not isinstance(current, dict) or remaining[0] not in current:
            return missing
        return walk(current[remaining[0]], remaining[1:])

    result = walk(value, parts)
    return None if result is missing else result


def _flatten_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        flattened: list[Any] = []
        for item in value:
            flattened.extend(_flatten_values(item))
        return flattened
    return [value]


def _mongo_sort_key(value: Any, descending: bool = False) -> tuple:
    """Sort key following MongoDB's cross-type BSON order (no collation).

    MinKey < empty array < null/missing < numbers < strings < objects < arrays < binary < ObjectId
    < booleans < dates < timestamps < regex. Every key is (type rank, comparable), so mixed-type
    fields never raise TypeError. A non-empty array sorts by its smallest element ascending and by
    its largest element descending, like MongoDB.
    """
    if isinstance(value, list):
        if not value:
            return (0,)
        keys = [_mongo_sort_key(item, descending) for item in value]
        return max(keys) if descending else min(keys)
    if value is None:
        return (1,)
    if isinstance(value, bool):  # before int: bool is an int subclass in Python
        return (8, value)
    if isinstance(value, (int, float, Decimal)):
        if (isinstance(value, float) and math.isnan(value)) or (isinstance(value, Decimal) and value.is_nan()):
            return (2, -math.inf, 0)  # MongoDB sorts NaN below every other number
        return (2, value, 1)
    if hasattr(value, "to_decimal"):  # bson Decimal128
        number = value.to_decimal()
        return (2, -math.inf, 0) if number.is_nan() else (2, number, 1)
    if isinstance(value, str):
        return (3, value)
    if isinstance(value, dict):
        return (4, tuple((str(key), _mongo_sort_key(item)) for key, item in value.items()))
    if isinstance(value, (bytes, bytearray)):
        return (6, bytes(value))
    if isinstance(value, ObjectId):
        return (7, value.binary)
    if isinstance(value, datetime):
        moment = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
        return (9, moment)
    if isinstance(value, date):
        return (9, datetime(value.year, value.month, value.day))
    return (12, type(value).__name__, repr(value))


def _mongo_date(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, daytime.min, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise ValueError("Invalid numeric date in the $toDate fallback") from None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"The $toDate fallback could not parse the ISO date: {value}") from None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    raise ValueError(f"The $toDate fallback does not support this value type: {type(value).__name__}")


def _mongo_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, list) and not isinstance(expected, list):
        return any(_mongo_equal(item, expected) for item in actual)
    if isinstance(actual, datetime) and isinstance(expected, datetime):
        left = actual if actual.tzinfo else actual.replace(tzinfo=timezone.utc)
        right = expected if expected.tzinfo else expected.replace(tzinfo=timezone.utc)
        return left == right
    return actual == expected


def _mongo_compare(actual: Any, expected: Any, operator: str) -> bool:
    if operator in {"$eq", "$ne", "$in", "$nin"}:
        if operator == "$eq":
            return _mongo_equal(actual, expected)
        if operator == "$ne":
            return not _mongo_equal(actual, expected)
        candidates = expected if isinstance(expected, list) else [expected]
        matched = any(_mongo_equal(actual, candidate) for candidate in candidates)
        return matched if operator == "$in" else not matched
    candidates = actual if isinstance(actual, list) else [actual]
    for candidate in candidates:
        try:
            if operator == "$gte" and candidate >= expected:
                return True
            if operator == "$gt" and candidate > expected:
                return True
            if operator == "$lte" and candidate <= expected:
                return True
            if operator == "$lt" and candidate < expected:
                return True
        except TypeError:
            continue
    return False


def _matches_field(actual: Any, condition: Any, *, exists: bool = True) -> bool:
    if not isinstance(condition, dict) or not any(str(key).startswith("$") for key in condition):
        return _mongo_equal(actual, condition)
    for operator, expected in condition.items():
        if operator == "$exists":
            if exists != bool(expected):
                return False
            continue
        if operator not in {"$eq", "$ne", "$in", "$nin", "$gte", "$gt", "$lte", "$lt"}:
            raise ValueError(f"The read fallback does not support the match operator: {operator}")
        if not _mongo_compare(actual, expected, operator):
            return False
    return True


def _matches_document(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$and":
            if not all(_matches_document(document, item) for item in condition):
                return False
        elif key == "$or":
            if not any(_matches_document(document, item) for item in condition):
                return False
        elif key == "$nor":
            if any(_matches_document(document, item) for item in condition):
                return False
        else:
            actual_values = _path_values(document, key)
            if not actual_values:
                if not _matches_field(None, condition, exists=False):
                    return False
            elif not any(_matches_field(value, condition) for value in actual_values):
                return False
    return True


# What the Python read fallback can run. Anything else must fail loudly: evaluating an unknown
# operator as a plain object would silently put wrong values into a report.
FALLBACK_STAGES = {"$match", "$addFields", "$set", "$lookup", "$project", "$limit", "$sort", "$skip",
                   "$unionWith", "$replaceRoot", "$replaceWith"}
FALLBACK_EXPRESSION_OPERATORS = {"$toDate", "$arrayElemAt", "$ifNull", "$mergeObjects", "$literal"}
FALLBACK_MATCH_OPERATORS = {"$and", "$or", "$nor", "$eq", "$ne", "$in", "$nin", "$gte", "$gt", "$lte", "$lt", "$exists"}


def _expression_operators(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.startswith("$"):
                found.add(key)
                if key == "$literal":
                    continue
            _expression_operators(value, found)
    elif isinstance(node, list):
        for item in node:
            _expression_operators(item, found)


def _fallback_unsupported(pipeline: list[Any]) -> list[str]:
    """Stages/operators in `pipeline` (and $unionWith sub-pipelines) the read fallback cannot run."""
    problems: set[str] = set()
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            problems.add("(stage with more than one operator)")
            continue
        operator, spec = next(iter(stage.items()))
        if operator not in FALLBACK_STAGES:
            problems.add(operator)
            continue
        used: set[str] = set()
        if operator in {"$addFields", "$set", "$project", "$replaceRoot", "$replaceWith"}:
            _expression_operators(spec, used)
            problems.update(op for op in used if op not in FALLBACK_EXPRESSION_OPERATORS)
        elif operator == "$match":
            _expression_operators(spec, used)
            problems.update(op for op in used if op not in FALLBACK_MATCH_OPERATORS)
        elif operator == "$lookup" and not {"from", "localField", "foreignField", "as"}.issubset(spec):
            problems.add("$lookup (pipeline form)")
        elif operator == "$unionWith" and isinstance(spec, dict):
            problems.update(_fallback_unsupported(spec.get("pipeline", [])))
    return sorted(problems)


def _referenced_collections(pipeline: list[Any]) -> list[str]:
    names: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for operator in ("$lookup", "$graphLookup"):
                spec = node.get(operator)
                if isinstance(spec, dict) and isinstance(spec.get("from"), str):
                    names.append(spec["from"])
            union = node.get("$unionWith")
            if isinstance(union, str):
                names.append(union)
            elif isinstance(union, dict) and isinstance(union.get("coll"), str):
                names.append(union["coll"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(pipeline)
    return list(dict.fromkeys(names))


def _denied_collections(collection: Any, pipeline: list[Any], max_time_ms: int) -> list[str]:
    """Collections of this report that the MongoDB user cannot read with find (error code 13)."""
    denied: list[str] = []
    for name in [collection.name, *_referenced_collections(pipeline)]:
        try:
            next(iter(collection.database[name].find({}, {"_id": 1}, max_time_ms=max_time_ms).limit(1)), None)
        except PyMongoError as error:
            if getattr(error, "code", None) == 13:
                denied.append(name)
            else:
                raise
    return list(dict.fromkeys(denied))


def _evaluate_expression(document: dict[str, Any], expression: Any) -> Any:
    if isinstance(expression, str) and expression.startswith("$$"):
        name, _, rest = expression[2:].partition(".")
        if name not in {"ROOT", "CURRENT"}:
            raise ValueError(f"The read fallback does not support the variable: $${name}")
        return _path_value(document, rest) if rest else document
    if isinstance(expression, str) and expression.startswith("$"):
        return _path_value(document, expression[1:])
    if isinstance(expression, list):
        return [_evaluate_expression(document, item) for item in expression]
    if isinstance(expression, dict):
        operators = [key for key in expression if isinstance(key, str) and key.startswith("$")]
        if operators and (len(expression) != 1 or operators[0] not in FALLBACK_EXPRESSION_OPERATORS):
            raise ValueError(f"The read fallback does not support the expression operator(s): {', '.join(operators)}")
        if len(expression) == 1 and "$literal" in expression:
            return copy.deepcopy(expression["$literal"])
        if len(expression) == 1 and "$mergeObjects" in expression:
            parts = expression["$mergeObjects"]
            merged: dict[str, Any] = {}
            for part in parts if isinstance(parts, list) else [parts]:
                value = _evaluate_expression(document, part)
                if value is None:
                    continue
                if not isinstance(value, dict):
                    raise ValueError("The $mergeObjects fallback accepts objects only")
                merged.update(value)
            return merged
        if len(expression) == 1 and "$toDate" in expression:
            return _mongo_date(_evaluate_expression(document, expression["$toDate"]))
        if len(expression) == 1 and "$arrayElemAt" in expression:
            operands = expression["$arrayElemAt"]
            if not isinstance(operands, list) or len(operands) != 2:
                raise ValueError("The $arrayElemAt fallback needs two operands")
            values = _evaluate_expression(document, operands[0])
            index = _evaluate_expression(document, operands[1])
            if not isinstance(values, list):
                return None
            try:
                return values[int(index)]
            except (IndexError, TypeError, ValueError):
                return None
        if len(expression) == 1 and "$ifNull" in expression:
            choices = expression["$ifNull"]
            if not isinstance(choices, list):
                raise ValueError("The $ifNull fallback needs an array")
            for choice in choices:
                value = _evaluate_expression(document, choice)
                if value is not None:
                    return value
            return None
        return {key: _evaluate_expression(document, value) for key, value in expression.items()}
    return expression


def _lookup_key(value: Any) -> Any:
    try:
        hash(value)
        return (type(value).__name__, value)
    except TypeError:
        return (type(value).__name__, json.dumps(value, sort_keys=True, default=str))


def _run_read_pipeline(
    collection: Any,
    pipeline: list[dict[str, Any]],
    *,
    max_time_ms: int,
    max_rows: int,
) -> list[dict[str, Any]]:
    """Run the report's safe aggregation subset using collection find/read access."""
    unsupported = _fallback_unsupported(pipeline)
    if unsupported:
        raise ValueError("The read fallback does not support these stages/operators: " + ", ".join(unsupported))
    scan_limit = min(max(max_rows * 10, 20_000), 100_000)
    return _run_fallback_stages(collection, pipeline, max_time_ms=max_time_ms, scan_limit=scan_limit)


def _fallback_source(collection: Any, initial_filter: dict[str, Any], *, max_time_ms: int, scan_limit: int) -> list[dict[str, Any]]:
    try:
        documents = list(collection.find(initial_filter, max_time_ms=max_time_ms).limit(scan_limit + 1))
    except PyMongoError as read_error:
        if getattr(read_error, "code", None) == 13:
            raise MongoReadAccessError(
                f"MongoDB rejected find/read on the collection '{collection.database.name}.{collection.name}' "
                "for this connection."
            ) from None
        raise
    if len(documents) > scan_limit:
        raise ValueError(
            f"There is no aggregate permission and the read fallback reached its safety limit of {scan_limit:,} source documents. "
            "Narrow the pipeline with $match stages first."
        )
    return documents


def _run_fallback_stages(collection: Any, pipeline: list[dict[str, Any]], *, max_time_ms: int, scan_limit: int) -> list[dict[str, Any]]:
    # Push only an initial $match to MongoDB find(); keep it in the local pipeline
    # too so the fallback remains correct for the supported Mongo match semantics.
    initial_filter: dict[str, Any] = {}
    if pipeline and "$match" in pipeline[0]:
        initial_filter = pipeline[0]["$match"]
    documents = _fallback_source(collection, initial_filter, max_time_ms=max_time_ms, scan_limit=scan_limit)

    for stage in pipeline:
        operator, specification = next(iter(stage.items()))
        if operator == "$match":
            documents = [doc for doc in documents if _matches_document(doc, specification)]
        elif operator == "$unionWith":
            spec = {"coll": specification} if isinstance(specification, str) else specification
            other = collection.database[str(spec["coll"])]
            documents = documents + _run_fallback_stages(
                other, list(spec.get("pipeline") or []), max_time_ms=max_time_ms, scan_limit=scan_limit
            )
            if len(documents) > scan_limit:
                raise ValueError(f"After $unionWith the read fallback went past its safety limit of {scan_limit:,} documents")
        elif operator in {"$replaceRoot", "$replaceWith"}:
            new_root = specification.get("newRoot") if operator == "$replaceRoot" else specification
            replaced: list[dict[str, Any]] = []
            for document in documents:
                value = _evaluate_expression(document, new_root)
                if not isinstance(value, dict):
                    raise ValueError(f"The result of {operator} must be an object")
                replaced.append(value)
            documents = replaced
        elif operator in {"$addFields", "$set"}:
            updated: list[dict[str, Any]] = []
            for document in documents:
                item = copy.deepcopy(document)
                for field, expression in specification.items():
                    item[field] = _evaluate_expression(item, expression)
                updated.append(item)
            documents = updated
        elif operator == "$lookup":
            required = {"from", "localField", "foreignField", "as"}
            if not required.issubset(specification):
                raise ValueError("The read fallback supports only localField/foreignField $lookup")
            local_field = str(specification["localField"])
            foreign_field = str(specification["foreignField"])
            foreign_collection = collection.database[str(specification["from"])]
            local_values: list[Any] = []
            seen: set[Any] = set()
            for document in documents:
                for value in _flatten_values(_path_value(document, local_field)):
                    key = _lookup_key(value)
                    if value is not None and key not in seen:
                        seen.add(key)
                        local_values.append(value)
            lookup_map: dict[Any, list[dict[str, Any]]] = {}
            # Keep each $in query modest so larger reports do not create an oversized command.
            for offset in range(0, len(local_values), 500):
                value_batch = local_values[offset : offset + 500]
                try:
                    foreign_cursor = foreign_collection.find(
                        {foreign_field: {"$in": value_batch}}, max_time_ms=max_time_ms
                    )
                    for foreign_document in foreign_cursor:
                        for value in _flatten_values(_path_value(foreign_document, foreign_field)):
                            lookup_map.setdefault(_lookup_key(value), []).append(foreign_document)
                except PyMongoError as read_error:
                    if getattr(read_error, "code", None) == 13:
                        raise MongoReadAccessError(
                            f"MongoDB rejected find/read on the $lookup collection '{foreign_collection.database.name}.{foreign_collection.name}' "
                            "for this connection."
                        ) from None
                    raise
            for document in documents:
                values = _flatten_values(_path_value(document, local_field))
                joined: list[dict[str, Any]] = []
                for value in values:
                    joined.extend(lookup_map.get(_lookup_key(value), []))
                document[str(specification["as"])] = joined
        elif operator == "$project":
            include_mode = any(value in (1, 1.0, True) for value in specification.values())
            projected: list[dict[str, Any]] = []
            for document in documents:
                item: dict[str, Any] = {}
                if include_mode and specification.get("_id", 1) not in (0, 0.0, False):
                    if "_id" in document:
                        item["_id"] = document["_id"]
                for field, expression in specification.items():
                    is_scalar_flag = not isinstance(expression, (dict, list))
                    if is_scalar_flag and expression in (0, 0.0, False):
                        continue
                    if is_scalar_flag and expression in (1, 1.0, True):
                        value = _path_value(document, field)
                    else:
                        value = _evaluate_expression(document, expression)
                    if value is not None or (isinstance(expression, (dict, list)) and value is None):
                        item[field] = value
                projected.append(item)
            documents = projected
        elif operator == "$limit":
            documents = documents[: max(0, int(specification))]
        elif operator == "$skip":
            documents = documents[max(0, int(specification)) :]
        elif operator == "$sort":
            # Stable sorts from the last key to the first give MongoDB's multi-key order.
            for field, direction in reversed(list(specification.items())):
                descending = float(direction) < 0
                documents.sort(
                    key=lambda doc, field=field, descending=descending: _mongo_sort_key(_path_value(doc, field), descending),
                    reverse=descending,
                )
    return documents
