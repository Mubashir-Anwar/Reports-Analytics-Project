"""Pipelines (ETL / ELT) in Analytics: steps that take datasets, transform them and load the result as a dataset.

A pipeline is a list of named steps. Every step makes a table with its own name, which later steps can read:

    source    a dataset (the data adapters' records, already imported: the "E" and "L" of ELT)
    filter    keep the rows that match rules            select   keep, rename and order columns
    dedupe    drop repeated rows                        fill     fill empty values
    cast      change column types                       column   a new column from a SQL expression
    join      two steps on shared columns               group    one row per group, with totals
    sort      order (and keep the first N)              union    stack steps on top of each other
    sql       any DuckDB SELECT over earlier steps      python   a pandas script (pyscript.py) over earlier steps

The last step is the result. Saved, a pipeline is a dataset (sourceKind "pipeline"): refreshing it runs every step
again on the current data of its sources, by hand or on its schedule, like any dataset.

The blocks are turned into SQL here (names quoted, values escaped), so they and the SQL steps run in one DuckDB
connection that can read only the source datasets' files and its own temporary folder (no other files, no network,
no settings). Python steps run in their own process like Analytics Python scripts. A run stops after
ANALYTICS_PIPELINE_TIMEOUT_SECONDS (default 900); every step's result may have at most ANALYTICS_IMPORT_MAX_ROWS rows.
"""
from __future__ import annotations

import math
import re
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from backend.app.analytics import importer, settings
from backend.app.analytics.queries import _STRIP, check_sql, lit, q

CONNECTION, LANGUAGE = "pipeline", "steps"        # what a pipeline dataset has as its data adapter and query language
KINDS = ("source", "filter", "select", "dedupe", "fill", "cast", "column", "join", "group", "sort", "union", "sql", "python")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
MAX_STEPS = 30
OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "starts", "ends", "in", "is_null", "not_null")
TYPES = {"number": "DOUBLE", "integer": "BIGINT", "text": "VARCHAR", "date": "DATE", "datetime": "TIMESTAMP", "bool": "BOOLEAN"}
AGGS = ("sum", "avg", "min", "max", "count", "distinct", "median", "countrows", "first")
HOWS = ("inner", "left", "right", "full")


class PipelineError(ValueError):
    pass


def timeout_seconds() -> int:
    return settings._int("ANALYTICS_PIPELINE_TIMEOUT_SECONDS", 900, 10, 86_400)


# ===================================================================== checking what the browser sends
def _text(value: Any, what: str, size: int, required: bool = True) -> str:
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise PipelineError(f"{what} is missing")
    if len(text) > size:
        raise PipelineError(f"{what} may have at most {size:,} characters")
    return text


def _list(value: Any, what: str, most: int = 50) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PipelineError(f"{what} must be a list")
    if len(value) > most:
        raise PipelineError(f"{what} may have at most {most} items")
    return value


def _scalar(value: Any, what: str) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value if not isinstance(value, str) else value[:1000]
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise PipelineError(f"{what}: use a number, text, true/false or nothing")


def clean_steps(raw: Any, find: Callable[[str], dict[str, Any] | None], own_id: str = "") -> dict[str, Any]:
    """The steps as stored, and the source datasets they read ({"steps", "inputs"})."""
    from backend.app.analytics import pyscript  # noqa: PLC0415
    steps = _list(raw, "steps", MAX_STEPS)
    if not steps:
        raise PipelineError("Add a step: start with a source (a dataset)")
    out: list[dict[str, Any]] = []
    names: list[str] = []
    inputs: list[str] = []

    def earlier(value: Any, what: str) -> str:
        name = str(value or "").strip()
        if not name and names:
            return names[-1]                          # the step before, by default
        if name not in names:
            raise PipelineError(f"{what}: “{name or '(empty)'}” is not an earlier step")
        return name

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise PipelineError(f"Step {index + 1} must be an object")
        kind = str(step.get("kind") or "")
        if kind not in KINDS:
            raise PipelineError(f"Step {index + 1}: kind must be one of {', '.join(KINDS)}")
        name = str(step.get("name") or "").strip()
        if not NAME.fullmatch(name):
            raise PipelineError(f"Step {index + 1}: a name like orders_clean (letters, digits, _; up to 40)")
        if name.casefold() in {n.casefold() for n in names}:
            raise PipelineError(f"Two steps are called “{name}”")
        where = f"Step “{name}”"
        clean: dict[str, Any] = {"name": name, "kind": kind, "note": _text(step.get("note"), "note", 300, False)}
        if kind == "source":
            dataset_id = str(step.get("dataset") or "")
            item = find(dataset_id)
            if item is None:
                raise PipelineError(f"{where}: choose a dataset")
            if own_id and (dataset_id == own_id or own_id in pyscript.depends_on(item, find)):
                raise PipelineError(f"{where}: “{item.get('name')}” is made from this pipeline; it cannot read its own result")
            clean["dataset"] = dataset_id
            if dataset_id not in inputs:
                inputs.append(dataset_id)
        elif not names:
            raise PipelineError("The first step must be a source (a dataset)")
        elif kind == "filter":
            clean["input"] = earlier(step.get("input"), where)
            clean["match"] = "any" if step.get("match") == "any" else "all"
            rules = []
            for rule in _list(step.get("rules"), f"{where}: rules", 30):
                op = str((rule or {}).get("op") or "eq")
                if op not in OPERATORS:
                    raise PipelineError(f"{where}: the operator must be one of {', '.join(OPERATORS)}")
                value = rule.get("value")
                value = [_scalar(v, where) for v in _list(value, f"{where}: values", 500)] if op == "in" else _scalar(value, where)
                rules.append({"column": _text(rule.get("column"), f"{where}: column", 120), "op": op, "value": value})
            if not rules:
                raise PipelineError(f"{where}: add a rule")
            clean["rules"] = rules
        elif kind == "select":
            clean["input"] = earlier(step.get("input"), where)
            clean["columns"] = [{"name": _text((c or {}).get("name"), f"{where}: column", 120),
                                 "as": _text((c or {}).get("as"), f"{where}: new name", 120, False)}
                                for c in _list(step.get("columns"), f"{where}: columns", 400)]
            if not clean["columns"]:
                raise PipelineError(f"{where}: choose the columns to keep")
        elif kind == "dedupe":
            clean["input"] = earlier(step.get("input"), where)
            clean["columns"] = [_text(c, f"{where}: column", 120) for c in _list(step.get("columns"), f"{where}: columns", 100)]
        elif kind == "fill":
            clean["input"] = earlier(step.get("input"), where)
            clean["fills"] = [{"column": _text((f or {}).get("column"), f"{where}: column", 120), "value": _scalar(f.get("value"), where)}
                              for f in _list(step.get("fills"), f"{where}: fills", 100)]
            if not clean["fills"]:
                raise PipelineError(f"{where}: choose a column to fill")
        elif kind == "cast":
            clean["input"] = earlier(step.get("input"), where)
            casts = []
            for item in _list(step.get("casts"), f"{where}: casts", 100):
                to = str((item or {}).get("to") or "")
                if to not in TYPES:
                    raise PipelineError(f"{where}: the type must be one of {', '.join(TYPES)}")
                casts.append({"column": _text(item.get("column"), f"{where}: column", 120), "to": to})
            if not casts:
                raise PipelineError(f"{where}: choose a column and its new type")
            clean["casts"] = casts
        elif kind == "column":
            clean["input"] = earlier(step.get("input"), where)
            clean["column"] = _text(step.get("column"), f"{where}: the new column's name", 120)
            expression = _text(step.get("expression"), f"{where}: the formula", 4000)
            if ";" in _STRIP.sub(" ", expression):
                raise PipelineError(f"{where}: the formula is one expression (no ;)")
            clean["expression"] = expression
        elif kind == "join":
            clean["left"] = earlier(step.get("left"), where)
            clean["right"] = earlier(step.get("right"), where)
            how = str(step.get("how") or "left")
            if how not in HOWS:
                raise PipelineError(f"{where}: the join must be one of {', '.join(HOWS)}")
            clean["how"] = how
            clean["on"] = [{"left": _text((p or {}).get("left"), f"{where}: left column", 120),
                            "right": _text((p or {}).get("right"), f"{where}: right column", 120)}
                           for p in _list(step.get("on"), f"{where}: columns to join on", 10)]
            if not clean["on"]:
                raise PipelineError(f"{where}: choose the columns to join on")
        elif kind == "group":
            clean["input"] = earlier(step.get("input"), where)
            clean["by"] = [_text(c, f"{where}: group-by column", 120) for c in _list(step.get("by"), f"{where}: group by", 20)]
            aggs = []
            for item in _list(step.get("aggs"), f"{where}: totals", 50):
                agg = str((item or {}).get("agg") or "sum")
                if agg not in AGGS:
                    raise PipelineError(f"{where}: the total must be one of {', '.join(AGGS)}")
                column = _text(item.get("column"), f"{where}: column", 120, agg != "countrows")
                aggs.append({"agg": agg, "column": column, "as": _text(item.get("as"), f"{where}: name", 120, False)})
            if not aggs and not clean["by"]:
                raise PipelineError(f"{where}: choose a group-by column or a total")
            clean["aggs"] = aggs
        elif kind == "sort":
            clean["input"] = earlier(step.get("input"), where)
            clean["by"] = [{"column": _text((s or {}).get("column"), f"{where}: column", 120), "dir": "desc" if (s or {}).get("dir") == "desc" else "asc"}
                           for s in _list(step.get("by"), f"{where}: sort", 10)]
            limit = step.get("limit")
            clean["limit"] = int(limit) if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0 else None
        elif kind == "union":
            parts = [earlier(p, where) for p in _list(step.get("inputs"), f"{where}: steps", 10)]
            if len(parts) < 2:
                raise PipelineError(f"{where}: choose two or more steps to stack")
            clean["inputs"] = parts
        elif kind == "sql":
            try:
                clean["sql"] = check_sql(_text(step.get("sql"), f"{where}: the SQL", 20_000))
            except ValueError as exc:
                raise PipelineError(f"{where}: {exc}") from None
        elif kind == "python":
            clean["script"] = _text(step.get("script"), f"{where}: the script", pyscript.MAX_SCRIPT).replace("\r\n", "\n")
            parts = [earlier(p, where) for p in _list(step.get("inputs"), f"{where}: steps", pyscript.MAX_INPUTS)]
            clean["inputs"] = parts or [names[-1]]
        out.append(clean)
        names.append(name)
    return {"steps": out, "inputs": inputs}


def has_python(steps: Any) -> bool:
    return isinstance(steps, list) and any(isinstance(s, dict) and s.get("kind") == "python" for s in steps)


# ===================================================================== blocks → SQL
def _value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return lit(str(value))


def _like(value: Any) -> str:
    return str(value if value is not None else "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _condition(col: str, op: str, value: Any) -> str:
    text = f"CAST({col} AS VARCHAR)"
    if op in ("eq", "gt", "gte", "lt", "lte"):
        return f"{col} {dict(eq='=', gt='>', gte='>=', lt='<', lte='<=')[op]} {_value(value)}"
    if op == "ne":
        return f"{col} IS DISTINCT FROM {_value(value)}"
    if op in ("contains", "starts", "ends"):
        pattern = {"contains": "%{}%", "starts": "{}%", "ends": "%{}"}[op].format(_like(value))
        return f"{text} ILIKE {lit(pattern)} ESCAPE '\'"
    if op == "in":
        return f"{col} IN ({', '.join(_value(v) for v in value or []) or 'NULL'})"
    return f"{col} IS NULL" if op == "is_null" else f"{col} IS NOT NULL"


def block_sql(step: dict[str, Any], columns: Callable[[str], list[str]]) -> str:
    """The SELECT a block step stands for. `columns(table)` lists an earlier step's columns."""
    kind = step["kind"]
    where = f"Step “{step['name']}”"

    def need(table: str, *names: str) -> None:
        have = columns(table)
        for name in names:
            if name not in have:
                raise PipelineError(f"{where}: “{name}” is not a column of {table} ({', '.join(have[:12])}{'…' if len(have) > 12 else ''})")

    if kind == "filter":
        need(step["input"], *[r["column"] for r in step["rules"]])
        parts = []
        for rule in step["rules"]:
            col, op, value = q(rule["column"]), rule["op"], rule["value"]
            parts.append(_condition(col, op, value))
        joiner = " OR " if step["match"] == "any" else " AND "
        return f"SELECT * FROM {q(step['input'])} WHERE {joiner.join(f'({p})' for p in parts)}"
    if kind == "select":
        need(step["input"], *[c["name"] for c in step["columns"]])
        picked = ", ".join(f"{q(c['name'])} AS {q(c['as'] or c['name'])}" for c in step["columns"])
        return f"SELECT {picked} FROM {q(step['input'])}"
    if kind == "dedupe":
        need(step["input"], *step["columns"])
        if not step["columns"]:
            return f"SELECT DISTINCT * FROM {q(step['input'])}"
        return f"SELECT DISTINCT ON ({', '.join(q(c) for c in step['columns'])}) * FROM {q(step['input'])}"
    if kind == "fill":
        need(step["input"], *[f["column"] for f in step["fills"]])
        replace = ", ".join(f"COALESCE({q(f['column'])}, {_value(f['value'])}) AS {q(f['column'])}" for f in step["fills"])
        return f"SELECT * REPLACE ({replace}) FROM {q(step['input'])}"
    if kind == "cast":
        need(step["input"], *[c["column"] for c in step["casts"]])
        replace = ", ".join(f"TRY_CAST({q(c['column'])} AS {TYPES[c['to']]}) AS {q(c['column'])}" for c in step["casts"])
        return f"SELECT * REPLACE ({replace}) FROM {q(step['input'])}"
    if kind == "column":
        if step["column"] in columns(step["input"]):
            return f"SELECT * REPLACE (({step['expression']}) AS {q(step['column'])}) FROM {q(step['input'])}"
        return f"SELECT *, ({step['expression']}) AS {q(step['column'])} FROM {q(step['input'])}"
    if kind == "join":
        left, right = step["left"], step["right"]
        need(left, *[p["left"] for p in step["on"]])
        need(right, *[p["right"] for p in step["on"]])
        same_keys = {p["right"]: p["left"] for p in step["on"] if p["left"] == p["right"]}
        picks = []
        for name in columns(left):
            if name in same_keys.values() and step["how"] in ("right", "full"):
                picks.append(f"COALESCE(l.{q(name)}, r.{q(name)}) AS {q(name)}")
            else:
                picks.append(f"l.{q(name)}")
        taken = set(columns(left))
        for name in columns(right):
            if name in same_keys:
                continue
            alias = name if name not in taken else f"{right}_{name}"
            while alias in taken:
                alias = f"{alias}_2"
            taken.add(alias)
            picks.append(f"r.{q(name)} AS {q(alias)}")
        condition = " AND ".join(f"l.{q(p['left'])} = r.{q(p['right'])}" for p in step["on"])
        return (f"SELECT {', '.join(picks)} FROM {q(left)} AS l {step['how'].upper()} JOIN {q(right)} AS r ON {condition}")
    if kind == "group":
        need(step["input"], *step["by"], *[a["column"] for a in step["aggs"] if a["agg"] != "countrows"])
        parts = [q(c) for c in step["by"]]
        for item in step["aggs"]:
            col = q(item["column"]) if item["column"] else ""
            sql = {"sum": f"SUM({col})", "avg": f"AVG({col})", "min": f"MIN({col})", "max": f"MAX({col})", "count": f"COUNT({col})",
                   "distinct": f"COUNT(DISTINCT {col})", "median": f"MEDIAN({col})", "countrows": "COUNT(*)", "first": f"FIRST({col})"}[item["agg"]]
            parts.append(f"{sql} AS {q(item['as'] or (item['agg'] + ('_' + item['column'] if item['column'] else '')))}")
        group = f" GROUP BY {', '.join(q(c) for c in step['by'])}" if step["by"] else ""
        return f"SELECT {', '.join(parts)} FROM {q(step['input'])}{group}"
    if kind == "sort":
        need(step["input"], *[s["column"] for s in step["by"]])
        keys = ", ".join(f"{q(s['column'])} {s['dir'].upper()} NULLS LAST" for s in step["by"])
        order = f" ORDER BY {keys}" if step["by"] else ""
        limit = f" LIMIT {step['limit']}" if step["limit"] else ""
        return f"SELECT * FROM {q(step['input'])}{order}{limit}"
    if kind == "union":
        return " UNION ALL BY NAME ".join(f"SELECT * FROM {q(name)}" for name in step["inputs"])
    if kind == "sql":
        return step["sql"]
    raise PipelineError(f"{where}: cannot be turned into SQL")


# ===================================================================== running
def run(steps: list[dict[str, Any]], find: Callable[[str], dict[str, Any] | None], *, until: str = "",
        preview_rows: int = 50, output: Path | None = None) -> dict[str, Any]:
    """Run the (cleaned) steps up to `until` (default: all). Returns {"ok", "error", "failedStep", "steps": [{"name",
    "kind", "rowCount", "columns", "durationMs", "error", "output"}], "columns", "rowCount", "rows", "durationMs"}.
    With `output`, the last step's table is written there as a Parquet file."""
    from backend.app.analytics import pyscript  # noqa: PLC0415
    started = time.monotonic()
    deadline = started + timeout_seconds()
    max_rows = settings.import_max_rows()
    work = Path(tempfile.mkdtemp(prefix="rs-pl-"))
    report: dict[str, Any] = {"ok": False, "error": "", "failedStep": "", "steps": [], "columns": [], "rowCount": 0, "rows": []}
    con = importer.connect()
    try:
        sources = {}
        for step in steps:
            if step["kind"] == "source":
                item = find(step["dataset"])
                path = importer.file_path((item or {}).get("file") or "")
                if item is None or path is None or not path.exists():
                    name = (item or {}).get("name") or step["dataset"]
                    raise PipelineError(f"Step “{step['name']}”: the dataset “{name}” has no data yet; refresh it first")
                sources[step["name"]] = path
        # the sandbox: only the sources' files and this run's folder; no network, no other files, no settings
        con.execute(f"SET temp_directory={importer.quote_path(work / 'spill')}")
        con.execute(f"SET allowed_paths=[{', '.join(importer.quote_path(p) for p in sources.values())}]")
        con.execute(f"SET allowed_directories=[{importer.quote_path(work)}]")
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")

        def columns(table: str) -> list[str]:
            return [str(row[0]) for row in con.execute(f"DESCRIBE {q(table)}").fetchall()]

        def execute(sql: str) -> None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise PipelineError(f"The pipeline ran longer than {timeout_seconds()} s (ANALYTICS_PIPELINE_TIMEOUT_SECONDS)")
            timer = threading.Timer(left, con.interrupt)
            timer.start()
            try:
                con.execute(sql)
            except Exception as exc:  # noqa: BLE001 - DuckDB's own errors go back to the writer of the step
                if type(exc).__name__ == "InterruptException":
                    raise PipelineError(f"The pipeline ran longer than {timeout_seconds()} s (ANALYTICS_PIPELINE_TIMEOUT_SECONDS)") from None
                message = str(exc).split("\n")[0][:400]
                if "Permission Error" in message:
                    message = "only the earlier steps can be read (no files, no settings)"
                raise PipelineError(message) from None
            finally:
                timer.cancel()

        last = ""
        for index, step in enumerate(steps):
            began = time.monotonic()
            info: dict[str, Any] = {"name": step["name"], "kind": step["kind"], "rowCount": 0, "columns": [], "durationMs": 0, "error": ""}
            report["steps"].append(info)
            table = q(step["name"])
            try:
                if step["kind"] == "source":
                    execute(f"CREATE TEMP TABLE {table} AS SELECT * FROM read_parquet({importer.quote_path(sources[step['name']])})")
                elif step["kind"] == "python":
                    folder = work / f"py{index}"
                    folder.mkdir()
                    frames = [{"name": name, "id": name, "frame": con.execute(f"SELECT * FROM {q(name)}").df()} for name in step["inputs"]]
                    result = pyscript.execute(step["script"], frames, folder)
                    info["output"] = result["output"][:20_000]
                    if not result["ok"]:
                        raise PipelineError(result["error"])
                    if result["produced"].stat().st_size == 0:
                        raise PipelineError("the script left no rows")
                    execute(f"CREATE TEMP TABLE {table} AS SELECT * FROM {pyscript.ndjson_source(con, result['produced'], result['dates'])}")
                else:
                    execute(f"CREATE TEMP TABLE {table} AS {block_sql(step, columns)}")
                info["rowCount"] = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                info["columns"] = columns(step["name"])
                if info["rowCount"] > max_rows:
                    raise PipelineError(f"{info['rowCount']:,} rows; at most {max_rows:,} (ANALYTICS_IMPORT_MAX_ROWS)")
            except PipelineError as exc:
                info["error"] = str(exc)
                report.update({"error": f"Step “{step['name']}”: {exc}" if not str(exc).startswith("Step “") else str(exc),
                               "failedStep": step["name"]})
                return report
            except ValueError as exc:                     # the Python step's time limit
                info["error"] = str(exc)
                report.update({"error": f"Step “{step['name']}”: {exc}", "failedStep": step["name"]})
                return report
            finally:
                info["durationMs"] = int((time.monotonic() - began) * 1000)
            last = step["name"]
            if until and step["name"] == until:
                break
        cursor = con.execute(f"SELECT * FROM {q(last)} LIMIT {int(preview_rows)}")
        report["columns"] = [item[0] for item in cursor.description]
        report["rows"] = [[importer.plain(value) for value in row] for row in cursor.fetchall()]
        report["rowCount"] = report["steps"][-1]["rowCount"]
        if output is not None and report["rowCount"]:
            part = work / "out.parquet"
            execute(f"COPY {q(last)} TO {importer.quote_path(part)} (FORMAT parquet, COMPRESSION zstd)")
            shutil.move(str(part), str(output))
        report["ok"] = True
        return report
    except PipelineError as exc:
        report["error"] = str(exc)
        return report
    finally:
        con.close()
        report["durationMs"] = int((time.monotonic() - started) * 1000)
        shutil.rmtree(work, ignore_errors=True)


def run_import(dataset: dict[str, Any], find: Callable[[str], dict[str, Any] | None]) -> dict[str, Any]:
    """Refresh a pipeline dataset: run every step on the current data (the importer's result shape, plus "steps")."""
    started = time.monotonic()
    steps = dataset.get("querySource")
    if not isinstance(steps, list):
        raise ValueError("The pipeline has no steps")
    name = f"{dataset['id']}-{secrets.token_hex(4)}.parquet"
    target = settings.directory() / name
    result = run(steps, find, preview_rows=0, output=target)
    stats = [{k: s.get(k) for k in ("name", "kind", "rowCount", "durationMs", "error")} for s in result["steps"]]
    if not result["ok"]:
        raise PipelineStepError(result["error"][:450], stats)
    if not result["rowCount"]:
        return {"file": "", "rowCount": 0, "sizeBytes": 0, "columns": [], "durationMs": int((time.monotonic() - started) * 1000), "steps": stats}
    columns = importer.merge_columns(importer.describe(target), dataset.get("columns") or [])
    return {"file": name, "rowCount": result["rowCount"], "sizeBytes": target.stat().st_size, "columns": columns,
            "durationMs": int((time.monotonic() - started) * 1000), "steps": stats}


class PipelineStepError(ValueError):
    """A refresh that failed in a step; `steps` says how far it came."""

    def __init__(self, message: str, steps: list[dict[str, Any]]):
        super().__init__(message)
        self.steps = steps
