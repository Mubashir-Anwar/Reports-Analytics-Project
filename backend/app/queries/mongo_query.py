"""Query parsing: Mongo shell / JSON / JS / Python literals, classic runCommand, $P{} parameters, read-only checks."""
from __future__ import annotations

import ast
import json
import re
from datetime import date, datetime, time as daytime, timezone
from typing import Any

from bson import Int64, ObjectId

from backend.app.constants import COLLECTION_PARAM_VALUE, QUERY_LANGUAGES


def _date_boundary(value: date, boundary: str) -> datetime:
    end_time = daytime.max if boundary == "end" else daytime.min
    return datetime.combine(value, end_time, tzinfo=timezone.utc)


# $P!{X} (classic reports: paste the text into the query) is allowed only as a filter made of field names, values and these
# comparison operators — no $where, $expr, $function or anything else that could run code or read other data.
RAW_FILTER_OPERATORS = {
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$exists", "$regex", "$options", "$not", "$all",
    "$size", "$type", "$elemMatch", "$mod", "$and", "$or", "$nor",
}


def _raw_filter(name: str, text: str) -> Any:
    """The value of a $P!{name} parameter that looks like {…} or […]: parsed as a literal and checked."""
    if len(text) > 20_000:
        raise ValueError(f"$P!{{{name}}}: the filter text is longer than 20 000 characters")
    try:
        parsed = _parse_mongosh_literal(text.strip())
    except ValueError as exc:
        raise ValueError(f"$P!{{{name}}} must be a filter like {{'$in': ['A', 'B']}}; its value could not be read: {exc}") from None

    def check(node: Any, depth: int = 0) -> None:
        if depth > 8:
            raise ValueError(f"$P!{{{name}}}: the filter is nested too deeply")
        if isinstance(node, dict):
            if set(node) & {"$param", "$paramIn", "$literalDate", "$literalObjectId", "$literalInt64"}:
                raise ValueError(f"$P!{{{name}}}: parameters inside the pasted filter are not allowed")
            for key, value in node.items():
                if key.startswith("$") and key not in RAW_FILTER_OPERATORS:
                    raise ValueError(f"$P!{{{name}}}: {key} is not allowed in a pasted filter (only comparisons such as $in, $ne, $gt, $regex)")
                if not key.startswith("$") and not re.fullmatch(r"[A-Za-z_][\w.-]{0,199}", key):
                    raise ValueError(f"$P!{{{name}}}: {key!r} is not a field name")
                check(value, depth + 1)
        elif isinstance(node, list):
            if len(node) > 1000:
                raise ValueError(f"$P!{{{name}}}: at most 1000 values")
            for item in node:
                check(item, depth + 1)
        elif not (node is None or isinstance(node, (str, int, float, bool))):
            raise ValueError(f"$P!{{{name}}}: unsupported value {node!r}")
    check(parsed)
    return parsed


def _resolve_pipeline_parameters(node: Any, params: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if set(node) == {"$literalDate"}:
            raw_date = node["$literalDate"]
            if not isinstance(raw_date, str):
                raise ValueError("An ISODate literal must be a string")
            try:
                parsed_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError(f"Invalid ISODate value: {raw_date}") from None
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            return parsed_date.astimezone(timezone.utc)
        if set(node) == {"$literalObjectId"}:
            try:
                return ObjectId(str(node["$literalObjectId"]))
            except Exception:
                raise ValueError("Invalid ObjectId literal") from None
        if set(node) == {"$literalInt64"}:
            try:
                return Int64(int(node["$literalInt64"]))
            except (ValueError, TypeError, OverflowError):
                raise ValueError("Invalid NumberLong literal") from None
        if set(node) == {"$paramIn"}:
            # Optional multi-value filter: comma separated values -> {"$in": [...]}, empty -> match everything.
            raw_value = params.get(str(node["$paramIn"]))
            items = [
                piece.strip().strip("'\"").strip()
                for piece in str(raw_value if raw_value is not None else "").split(",")
            ]
            items = [item for item in items if item]
            if len(items) > 200 or any(len(item) > 120 for item in items):
                raise ValueError(f"{node['$paramIn']}: at most 200 values (each up to 120 characters)")
            return {"$in": items} if items else {"$nin": []}
        if "$param" in node and set(node).issubset({"$param", "boundary", "raw"}):
            name = str(node["$param"])
            if name not in params:
                raise ValueError(f"Pipeline parameter is not defined: {name}")
            value = params[name]
            if node.get("raw") and isinstance(value, str) and value.strip()[:1] in ("{", "["):
                return _raw_filter(name, value)
            if isinstance(value, date) and not isinstance(value, datetime):
                boundary = str(node.get("boundary", "start"))
                if boundary not in {"start", "end"}:
                    raise ValueError(f"The boundary of {name} must be start or end")
                return _date_boundary(value, boundary)
            return value
        return {key: _resolve_pipeline_parameters(value, params) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_pipeline_parameters(item, params) for item in node]
    return node


def resolve_collection(template: str, parameters: dict[str, Any]) -> str:
    """Fill $P{name} in a collection template; values are strictly charset-checked."""
    def fill(match: re.Match) -> str:
        value = str(parameters.get(match.group(1), "")).strip()
        if not COLLECTION_PARAM_VALUE.fullmatch(value):
            raise ValueError(f"Parameter {match.group(1)} is not valid in a collection name (only letters, numbers, _ and -)")
        return value
    return re.sub(r"\$P\{([A-Za-z_][A-Za-z0-9_]*)\}", fill, template)


def _strip_query_comments(source: str, python: bool = False) -> str:
    """Remove JS/JSON comments without altering quoted strings or line positions."""
    if python:
        return source  # Python comments are parsed natively by ast.
    output = list(source)
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'", '`'}:
            quote = char
            index += 1
            continue
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            for cursor in range(index, end):
                output[cursor] = " "
            index = end
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise ValueError("The query has a /* comment without a closing */")
            for cursor in range(index, end + 2):
                if output[cursor] not in "\r\n":
                    output[cursor] = " "
            index = end + 2
            continue
        index += 1
    return "".join(output)


def _extract_literal_array(source: str, start: int) -> tuple[str, int]:
    if start >= len(source) or source[start] != "[":
        raise ValueError("The pipeline literal must start with [")
    stack = ["]"]
    quote: str | None = None
    escaped = False
    for index in range(start + 1, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char == "[":
            stack.append("]")
        elif char == "{":
            stack.append("}")
        elif char in {"]", "}"}:
            if not stack or char != stack.pop():
                raise ValueError("The brackets in the pipeline do not match")
            if not stack:
                return source[start:index + 1], index + 1
    raise ValueError("The closing ] of the pipeline array is missing")


class QuerySyntaxError(ValueError):
    """A mistake in the query text with its place: line (1 = first line of the query), column and the text around it.
    `insert` is set when one character is missing there (a comma), so an importer can repair it."""

    def __init__(self, message: str, line: int, column: int, near: str, insert: tuple[int, str] | None = None):
        super().__init__(f"{message} (query line {line}, column {column}, near: {near})")
        self.line, self.column, self.near, self.insert = line, column, near, insert


def _place(source: str, position: int, first_line: int = 1) -> tuple[int, int, str]:
    position = max(0, min(position, len(source)))
    line = source.count("\n", 0, position) + first_line
    column = position - (source.rfind("\n", 0, position) + 1) + 1
    before = source[max(0, position - 40):position].split("\n")[-1].lstrip()
    after = source[position:position + 40].split("\n")[0].rstrip()
    return line, column, f"{before} >>> {after}".strip()


def _parse_mongosh_literal(source: str, first_line: int = 1) -> Any:
    """Parse a safe Mongo Shell object/array literal subset without evaluating JS."""
    token_pattern = re.compile(
        r"\s*(?:(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
        r"|(?P<parameter>\$P!?\{[A-Za-z_][A-Za-z0-9_]*\})"
        r"|(?P<number>-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
        r"|(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)"
        r"|(?P<punct>[{}\[\]:,()]))"
    )
    def fail(message: str, at: int, insert: tuple[int, str] | None = None) -> None:
        raise QuerySyntaxError(message, *_place(source, at, first_line), insert=insert)

    tokens: list[tuple[str, str, int]] = []
    position = 0
    while position < len(source):
        if not source[position:].strip():
            break
        match = token_pattern.match(source, position)
        if not match:
            start = position + len(source[position:]) - len(source[position:].lstrip())
            fail("This text is not allowed in a Mongo Shell query (only data: text in quotes, numbers, true/false/null, "
                 "$P{…}, ISODate(…), ObjectId(…), NumberLong(…))", start)
        kind = next(key for key, value in match.groupdict().items() if value is not None)
        tokens.append((kind, match.group(kind), match.start(kind)))
        position = match.end()
    cursor = 0

    def at_cursor() -> int:
        return tokens[cursor][2] if cursor < len(tokens) else len(source)

    def after_previous() -> int:
        kind, value, start = tokens[cursor - 1]
        return start + len(value)

    def peek(value: str | None = None) -> bool:
        return cursor < len(tokens) and (value is None or tokens[cursor][1] == value)

    def take(value: str | None = None) -> tuple[str, str]:
        nonlocal cursor
        if not peek(value):
            if value == ":":
                fail(f"A colon : is missing after the field name {tokens[cursor - 1][1]}", after_previous(), (after_previous(), ":"))
            expected = f"{value!r}" if value is not None else "a value"
            found = f"{tokens[cursor][1]!r}" if cursor < len(tokens) else "the end of the query"
            fail(f"Expected {expected} but found {found}", at_cursor())
        token = tokens[cursor]
        cursor += 1
        return token[0], token[1]

    def parse_value() -> Any:
        kind, value = take()
        if value == "[":
            opened = tokens[cursor - 1][2]
            result = []
            while not peek("]"):
                if cursor >= len(tokens):
                    fail("The [ opened here is never closed with ]", opened)
                result.append(parse_value())
                if peek(","):
                    take(",")
                    if peek("]"):
                        break
                elif not peek("]"):
                    fail("A comma is missing between two values of a list [ … ]: add , at the end of the value before", after_previous(), (after_previous(), ","))
            take("]")
            return result
        if value == "{":
            opened = tokens[cursor - 1][2]
            result: dict[str, Any] = {}
            while not peek("}"):
                if cursor >= len(tokens):
                    fail("The { opened here is never closed with }", opened)
                key_kind, raw_key = take()
                if key_kind == "string":
                    key = ast.literal_eval(raw_key)
                elif key_kind == "identifier":
                    key = raw_key
                else:
                    fail(f"A field name must be text in quotes (e.g. \"Branch\"), not {raw_key!r}", tokens[cursor - 1][2])
                take(":")
                result[str(key)] = parse_value()
                if peek(","):
                    take(",")
                    if peek("}"):
                        break
                elif not peek("}"):
                    fail(f"A comma is missing after the field \"{key}\": add , at the end of that line", after_previous(), (after_previous(), ","))
            take("}")
            return result
        if kind == "string":
            return ast.literal_eval(value)
        if kind == "parameter":
            name = re.fullmatch(r"\$P!?\{([A-Za-z_][A-Za-z0-9_]*)\}", value).group(1)
            # the classic $P!{} pastes text; here it becomes a value that may be a checked filter (see _raw_filter)
            return {"$param": name, "raw": True} if value.startswith("$P!") else {"$param": name}
        if kind == "number":
            if any(char in value for char in ".eE"):
                return float(value)
            return int(value)
        if kind == "identifier":
            if value in {"true", "false", "null"}:
                return {"true": True, "false": False, "null": None}[value]
            if peek("("):
                take("(")
                argument = parse_value()
                take(")")
                if value in {"NumberInt", "NumberLong"} and isinstance(argument, (int, float, str)) and not isinstance(argument, bool):
                    try:
                        integer = int(argument)
                        if isinstance(argument, float) and integer != argument:
                            raise ValueError
                        limit = 2**31 if value == "NumberInt" else 2**63
                        if not -limit <= integer < limit:
                            raise ValueError
                        return {"$literalInt64": integer} if value == "NumberLong" else integer
                    except (ValueError, OverflowError):
                        raise ValueError(f"{value} needs a valid integer") from None
                if value in {"ISODate", "Date"} and isinstance(argument, str):
                    return {"$literalDate": argument}
                if value == "ObjectId" and isinstance(argument, str) and re.fullmatch(r"[0-9a-fA-F]{24}", argument):
                    return {"$literalObjectId": argument}
                fail(f"{value}(...) is not supported here; use ISODate(\"2024-01-31\"), ObjectId(\"…24 hex…\"), NumberInt(5) or NumberLong(5)", tokens[cursor - 1][2])
            fail(f"{value} is not a value: put text in quotes (\"{value}\"), or use true, false or null", tokens[cursor - 1][2])
        fail(f"{value!r} cannot stand here; a value is expected", tokens[cursor - 1][2])

    result = parse_value()
    if cursor != len(tokens):
        fail(f"The pipeline already ended before {tokens[cursor][1]!r}: remove the extra text, or a ] or }} too many before it", tokens[cursor][2])
    return result


def _replace_jasper_parameters(source: str) -> str:
    """Translate unquoted classic parameter references into safe typed markers."""
    output: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    pattern = re.compile(r"\$P!?\{([A-Za-z_][A-Za-z0-9_]*)\}")
    while index < len(source):
        char = source[index]
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            output.append(char)
            index += 1
            continue
        match = pattern.match(source, index)
        if match:
            output.append(json.dumps({"$param": match.group(1), "raw": True} if match.group(0).startswith("$P!") else {"$param": match.group(1)}))
            index = match.end()
            continue
        output.append(char)
        index += 1
    return "".join(output)


_JASPER_AGGREGATE = re.compile(r"""['"]?aggregate['"]?\s*:\s*(?:"([^"]+)"|'([^']+)'|(\$P\{[A-Za-z_][A-Za-z0-9_]*\}))""")
_JASPER_PIPELINE = re.compile(r"""['"]?pipeline['"]?\s*:\s*\[""")
_JASPER_FIND = re.compile(r"""['"]?(?:findQuery|collectionName)['"]?\s*:""")


def split_jasper_query(source: Any) -> tuple[str, str] | None:
    """a classic report designer MongoDbQuery text → (collection, pipeline literal), or None for other styles.

    Accepts { runCommand: { aggregate: 'coll', pipeline: [...] } } (also without the runCommand
    wrapper) and { collectionName: 'coll', findQuery: {...} }. Nothing is executed.
    """
    if not isinstance(source, str):
        return None
    text = _strip_query_comments(source).strip()
    if not text.startswith("{"):
        return None
    aggregate = _JASPER_AGGREGATE.search(text)
    pipeline = _JASPER_PIPELINE.search(text)
    if aggregate and pipeline:
        literal, _end = _extract_literal_array(text, pipeline.end() - 1)  # other keys (cursor, allowDiskUse) are not needed
        return aggregate.group(1) or aggregate.group(2) or aggregate.group(3), literal
    if _JASPER_FIND.search(text):
        from backend.app.importers import jrxml as jrxml_import
        return jrxml_import._extract_query(text)
    return None


def pipeline_parameter_names(pipeline: Any) -> list[str]:
    """$P{...} names used in a parsed pipeline, in order of first use."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("$param") if "$param" in node else node.get("$paramIn") if set(node) == {"$paramIn"} else None
            if name is not None:
                if str(name) not in found:
                    found.append(str(name))
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(pipeline)
    return found


def _unrecognised_query_message(language: str, text: str) -> str:
    """Say what the query looks like and what to do, instead of only "wrong format"."""
    stripped = text.strip()
    if not stripped:
        return "The query is empty: write the MongoDB pipeline, e.g. db.getCollection(\"Orders\").aggregate([ { \"$match\": {} } ])"
    if re.match(r"(?is)(select|with)\b.*\bfrom\b|select\b", stripped):
        return ("This looks like a SQL query, but the query language is MongoDB. Choose Query language \"SQL\" and a SQL "
                "data adapter (PostgreSQL, MySQL, SQL Server or Oracle)")
    if re.match(r"(?s)\s*db(\.getCollection\s*\([^)]*\)|\.[A-Za-z_][\w]*)\.find(One)?\s*\(", stripped):
        return ("Reports use aggregate, not find: write db.getCollection(\"X\").aggregate([ { \"$match\": { … } } ]) — "
                "the find filter goes into $match and the projection into $project")
    if stripped.startswith("{"):
        return "This is one stage, not a pipeline: put the stages in square brackets, e.g. [ { \"$match\": { … } } ]"
    if language == "mongosh":
        return ("The query is not in Mongo Shell form. Write db.getCollection(\"Orders\").aggregate([ … ]) or paste only "
                "the pipeline [ … ]")
    return "Write the pipeline as pipeline = [ … ] (or choose Query language \"Mongo Shell\" and paste [ … ])"


def parse_query_source(language: str, source: str) -> list[dict[str, Any]]:
    """Convert supported data-only editor styles to a Mongo aggregation pipeline."""
    language = str(language or "aggregation-json").lower()
    if language not in QUERY_LANGUAGES:
        raise ValueError("The query mode must be aggregation-json, mongosh, javascript or python")
    if not isinstance(source, str) or len(source) > 2_000_000:
        raise ValueError("The query text must be smaller than 2 MB")
    if language == "python":
        try:
            module = ast.parse(_replace_jasper_parameters(source), mode="exec")
            if len(module.body) != 1:
                raise ValueError("In Python mode give only a pipeline = [...] literal assignment")
            statement = module.body[0]
            if isinstance(statement, ast.Assign):
                valid_target = len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id == "pipeline"
                value_node = statement.value
            elif isinstance(statement, ast.AnnAssign):
                valid_target = isinstance(statement.target, ast.Name) and statement.target.id == "pipeline"
                value_node = statement.value
            else:
                valid_target = False
                value_node = None
            if not valid_target or value_node is None:
                raise ValueError("In Python mode give only a pipeline = [...] literal assignment; no code is executed")
            pipeline = ast.literal_eval(value_node)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"The Python pipeline could not be read: {exc}") from None
    elif (jasper := split_jasper_query(source)) is not None:
        # a classic report designer MongoDbQuery ({runCommand: {aggregate, pipeline}}) works in every text mode.
        uncommented = _strip_query_comments(source)
        found_at = uncommented.find(jasper[1])
        pipeline = _parse_mongosh_literal(jasper[1], uncommented.count("\n", 0, found_at) + 1 if found_at >= 0 else 1)
    else:
        uncommented = _strip_query_comments(source)
        if language == "aggregation-json":
            literal = _replace_jasper_parameters(uncommented.strip())
        else:
            if language == "mongosh":
                raw_source = uncommented.strip()
                match = None if raw_source.startswith("[") else re.match(
                    r"\s*db(?:(?:\.getCollection\s*\(\s*(?:\"[^\"]*\"|'[^']*')\s*\))|(?:\.[A-Za-z_][A-Za-z0-9_]*))\.aggregate\s*\(",
                    uncommented,
                )
            else:
                match = re.match(r"\s*(?:(?:const|let|var)\s+)?pipeline\s*=\s*", uncommented)
            if language == "mongosh" and uncommented.strip().startswith("["):
                literal = uncommented.strip()
                array_start = uncommented.find("[")
                remainder = ""
            elif not match:
                raise ValueError(_unrecognised_query_message(language, uncommented))
            else:
                array_start = uncommented.find("[", match.end())
                if array_start < 0:
                    raise ValueError("The [ of the pipeline literal is missing")
                literal, array_end = _extract_literal_array(uncommented, array_start)
                remainder = uncommented[array_end:].strip()
                valid_remainder = re.fullmatch(r"\)\s*;?", remainder) if language == "mongosh" else re.fullmatch(r";?", remainder)
                if not valid_remainder:
                    raise ValueError("Extra executable code after the pipeline literal is not allowed")
        if language in {"mongosh", "javascript"}:
            pipeline = _parse_mongosh_literal(literal, uncommented.count("\n", 0, array_start) + 1)
        else:
            try:
                pipeline = json.loads(literal)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Pipeline JSON line {exc.lineno}, column {exc.colno}: {exc.msg}. For Mongo Shell syntax choose the query style 'Mongo Shell'.") from None
    if not isinstance(pipeline, list) or not pipeline or not all(isinstance(stage, dict) for stage in pipeline):
        raise ValueError("The pipeline must be a non-empty array of stage objects")
    _infer_parameter_date_boundaries(pipeline)
    return pipeline


def _infer_parameter_date_boundaries(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"$gte", "$gt", "$lte", "$lt"} and isinstance(value, dict) and set(value) == {"$param"}:
                value["boundary"] = "end" if key in {"$lte", "$lt"} else "start"
            _infer_parameter_date_boundaries(value)
    elif isinstance(node, list):
        for value in node:
            _infer_parameter_date_boundaries(value)


def _validate_pipeline_parameter_names(pipeline: Any, allowed_names: set[str]) -> None:
    if isinstance(pipeline, dict):
        if set(pipeline) == {"$paramIn"}:
            if str(pipeline["$paramIn"]) not in allowed_names:
                raise ValueError(f"Define the query parameter {pipeline['$paramIn']} in the report questions first")
            return
        if "$param" in pipeline and set(pipeline).issubset({"$param", "boundary", "raw"}):
            name = str(pipeline["$param"])
            if name not in allowed_names:
                raise ValueError(f"Define the query parameter {name} in the report questions first")
            if "boundary" in pipeline and pipeline["boundary"] not in {"start", "end"}:
                raise ValueError(f"The boundary of parameter {name} must be 'start' or 'end'")
            return
        for value in pipeline.values():
            _validate_pipeline_parameter_names(value, allowed_names)
    elif isinstance(pipeline, list):
        for value in pipeline:
            _validate_pipeline_parameter_names(value, allowed_names)


# Stages that show server internals (other users' operations, sessions, plans, index use) or follow changes:
# never needed for a report, so refused like writes.
SERVER_INFO_STAGES = {
    "$currentOp", "$listSessions", "$listLocalSessions", "$planCacheStats", "$indexStats", "$collStats",
    "$changeStream", "$changeStreamSplitLargeEvent", "$listSearchIndexes", "$listSampledQueries", "$querySettings",
    "$shardedDataDistribution", "$listClusterCatalog", "$listCachedAndActiveUsers",
}
COLLECTION_STAGES = {"$lookup": "from", "$graphLookup": "from", "$unionWith": "coll"}


def _check_other_collection(stage: str, value: Any) -> None:
    """$lookup / $graphLookup / $unionWith may read another collection of the same database, but not system.*
    collections and not another database."""
    target = value if isinstance(value, str) else value.get(COLLECTION_STAGES[stage]) if isinstance(value, dict) else None
    if isinstance(target, dict) or (isinstance(value, dict) and "db" in value):
        raise ValueError(f"{stage} may only read a collection of the same database")
    if isinstance(target, str) and target.lower().startswith("system."):
        raise ValueError(f"{stage} may not read system collections")


def _validate_read_only_pipeline(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"$out", "$merge"}:
                raise ValueError("A reporting pipeline must be read-only; $out and $merge are not allowed")
            if key in {"$function", "$accumulator", "$where"}:
                raise ValueError("$function, $accumulator and $where (which run JavaScript) are not allowed in a pipeline")
            if key in SERVER_INFO_STAGES:
                raise ValueError(f"{key} shows server information and is not allowed in a report pipeline")
            if key in COLLECTION_STAGES:
                _check_other_collection(key, value)
            _validate_read_only_pipeline(value)
    elif isinstance(node, list):
        for value in node:
            _validate_read_only_pipeline(value)


def _infer_pipeline_columns(pipeline: list[dict[str, Any]]) -> list[str]:
    """Infer the result fields by following the stages that shape the output.

    $project/$group/$count/$facet set the shape; later $addFields/$set/$lookup/$unset change it.
    Returns [] when the shape cannot be known (no shaping stage, or $replaceRoot and the like).
    """
    columns: list[str] | None = None

    def add(name: str) -> None:
        if columns is not None and name and name not in columns:
            columns.append(name)

    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            continue
        (key, value), = stage.items()
        if key == "$project" and isinstance(value, dict):
            kept = {k: v for k, v in value.items() if not (k == "_id" and v in (0, False))}
            if kept and all(v in (0, False) for v in kept.values()):     # exclusion projection
                if columns is not None:
                    columns = [c for c in columns if c not in kept and c.split(".")[0] not in kept]
            else:
                columns = list(kept)
        elif key == "$group" and isinstance(value, dict):
            columns = list(value)
        elif key == "$count" and isinstance(value, str) and value:
            columns = [value]
        elif key == "$facet" and isinstance(value, dict):
            columns = list(value)
        elif key == "$sortByCount":
            columns = ["_id", "count"]
        elif key in ("$addFields", "$set") and isinstance(value, dict):
            for name in value:
                add(name.split(".")[0])
        elif key == "$lookup" and isinstance(value, dict) and isinstance(value.get("as"), str):
            add(value["as"].split(".")[0])
        elif key == "$unset" and columns is not None:
            names = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            columns = [c for c in columns if c not in names]
        elif key in ("$replaceRoot", "$replaceWith", "$bucket", "$bucketAuto"):
            columns = None
    return columns or []
