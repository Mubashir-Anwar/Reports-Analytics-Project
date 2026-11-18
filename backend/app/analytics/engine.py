"""The query engine: a visual (fields to group by, values to summarise, filters) → DuckDB SQL over Parquet files.

Nothing a caller sends is put into the SQL as text except names that were checked against the dataset's columns
(and then quoted); every value is a bound parameter, cast to the column's own type. Fields of other datasets are
reached through the model's relationships (LEFT JOIN along the shortest path), so one visual can combine a MongoDB
dataset with a SQL Server one.

Every query has a memory limit (ANALYTICS_MEMORY_LIMIT; DuckDB spills to disk beyond it), a thread limit, a time
limit (ANALYTICS_QUERY_TIMEOUT_SECONDS, then it is interrupted) and a row limit (ANALYTICS_QUERY_MAX_ROWS).
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import date, datetime, time as clock_time
from decimal import Decimal
from typing import Any

from backend.app.analytics import importer, settings, store

TYPE_PATTERN = re.compile(r"^[A-Z0-9_(), ]{1,60}$")
AGG_SQL = {"sum": "SUM({c})", "avg": "AVG({c})", "min": "MIN({c})", "max": "MAX({c})", "count": "COUNT({c})",
           "distinct": "COUNT(DISTINCT {c})", "countrows": "COUNT(*)", "median": "MEDIAN({c})"}
AGG_LABEL = {"sum": "Sum of", "avg": "Average of", "min": "Min of", "max": "Max of", "count": "Count of",
             "distinct": "Distinct", "countrows": "Count of rows", "median": "Median of"}
NUMBER_ONLY = {"sum", "avg", "median"}
BUCKET_SQL = {
    "year": "strftime(TRY_CAST({c} AS TIMESTAMP), '%Y')",
    "quarter": "strftime(TRY_CAST({c} AS TIMESTAMP), '%Y') || '-Q' || CAST(quarter(TRY_CAST({c} AS TIMESTAMP)) AS VARCHAR)",
    "month": "strftime(TRY_CAST({c} AS TIMESTAMP), '%Y-%m')",
    "week": "strftime(date_trunc('week', TRY_CAST({c} AS TIMESTAMP)), '%Y-%m-%d')",
    "day": "strftime(TRY_CAST({c} AS TIMESTAMP), '%Y-%m-%d')",
}


class QueryError(ValueError):
    """The visual cannot be answered (unknown field, no path between datasets, …): shown to the user as is."""


class QueryTimeout(RuntimeError):
    pass


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, clock_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and value != value:          # NaN
        return None
    if isinstance(value, (bytes, bytearray)):
        return None
    return value


class Plan:
    """The datasets one query needs, their aliases and the joins between them."""

    def __init__(self, base: dict[str, Any], datasets: dict[str, dict[str, Any]], relationships: list[dict[str, Any]]):
        self.base = base
        self.datasets = datasets
        self.relationships = relationships
        self.alias = {base["id"]: "t0"}
        self.joins: list[str] = []
        self.columns = {item_id: {col["name"]: col for col in item.get("columns") or []} for item_id, item in datasets.items()}

    def path(self, target: str) -> list[tuple[str, str, str, str]] | None:
        """Shortest list of (from dataset, from column, to dataset, to column) steps from the base to target."""
        start = self.base["id"]
        queue: deque[tuple[str, list]] = deque([(start, [])])
        seen = {start}
        while queue:
            node, steps = queue.popleft()
            if node == target:
                return steps
            for rel in self.relationships:
                if rel.get("active") is False:
                    continue
                for a, ac, b, bc in ((rel["fromDataset"], rel["fromColumn"], rel["toDataset"], rel["toColumn"]),
                                     (rel["toDataset"], rel["toColumn"], rel["fromDataset"], rel["fromColumn"])):
                    if a == node and b not in seen and b in self.datasets:
                        seen.add(b)
                        queue.append((b, [*steps, (a, ac, b, bc)]))
        return None

    def reach(self, dataset_id: str) -> str | None:
        """The alias of a dataset, joining it in when needed (None: no relationship path)."""
        if dataset_id in self.alias:
            return self.alias[dataset_id]
        steps = self.path(dataset_id)
        if steps is None:
            return None
        for a, ac, b, bc in steps:
            if b in self.alias:
                continue
            target = self.datasets[b]
            path = importer.file_path(target.get("file", ""))
            if path is None or not path.exists():
                raise QueryError(f"The dataset '{target['name']}' has no data yet: refresh it first")
            self.alias[b] = f"t{len(self.alias)}"
            self.joins.append(f"LEFT JOIN read_parquet({importer.quote_path(path)}) AS {self.alias[b]} "
                              f"ON {self.alias[a]}.{ident(ac)} = {self.alias[b]}.{ident(bc)}")
        return self.alias[dataset_id]

    def column(self, field: dict[str, Any], *, optional: bool = False) -> tuple[str, dict[str, Any]] | None:
        """(SQL expression, column model) of a field; None for an unreachable optional field (a dashboard filter)."""
        dataset_id = field.get("dataset") or self.base["id"]
        if dataset_id not in self.datasets:
            if optional:
                return None
            raise QueryError(f"Unknown dataset '{dataset_id}'")
        column = self.columns[dataset_id].get(field.get("column", ""))
        if column is None:
            if optional:
                return None
            raise QueryError(f"'{field.get('column')}' is not a column of {self.datasets[dataset_id]['name']}")
        alias = self.reach(dataset_id)
        if alias is None:
            if optional:
                return None
            raise QueryError(f"{self.datasets[dataset_id]['name']} is not related to {self.base['name']}: "
                             "add a relationship in the Model")
        return f"{alias}.{ident(column['name'])}", column


def _group_expr(expr: str, field: dict[str, Any]) -> str:
    bucket = field.get("bucket")
    return BUCKET_SQL[bucket].format(c=expr) if bucket else expr


def _cast(column: dict[str, Any]) -> str:
    kind = str(column.get("type") or "VARCHAR").upper()
    return kind if TYPE_PATTERN.fullmatch(kind) else "VARCHAR"


def _filter_sql(plan: Plan, item: dict[str, Any], params: list[Any], optional: bool) -> str | None:
    found = plan.column(item, optional=optional)
    if found is None:
        return None
    expr, column = found
    op, values = item["op"], list(item.get("values") or [])
    if item.get("bucket"):
        expr, cast = _group_expr(expr, item), "VARCHAR"
    else:
        cast = _cast(column)

    def bind(value: Any) -> str:
        params.append(value if value is None or isinstance(value, (bool, int, float)) else str(value))
        return f"TRY_CAST(? AS {cast})"

    if op == "is_null":
        return f"{expr} IS NULL"
    if op == "not_null":
        return f"{expr} IS NOT NULL"
    if op in ("in", "not_in"):
        if not values:
            return None if op == "in" and optional else ("FALSE" if op == "in" else None)
        has_null = any(value is None for value in values)
        listed = [bind(value) for value in values if value is not None]
        parts = [f"{expr} IN ({', '.join(listed)})"] if listed else []
        if has_null:
            parts.append(f"{expr} IS NULL")
        sql = "(" + " OR ".join(parts) + ")"
        return sql if op == "in" else f"NOT {sql}"
    if not values:
        raise QueryError(f"The filter on {column['name']} needs a value")
    if op == "between":
        if len(values) != 2:
            raise QueryError(f"The 'between' filter on {column['name']} needs two values")
        return f"{expr} BETWEEN {bind(values[0])} AND {bind(values[1])}"
    if op in ("contains", "starts"):
        params.append(str(values[0]).lower())
        function = "contains" if op == "contains" else "starts_with"
        return f"{function}(lower(CAST({expr} AS VARCHAR)), ?)"
    symbol = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
    return f"{expr} {symbol} {bind(values[0])}"


def compile_query(visual: dict[str, Any], extra_filters: list[dict[str, Any]] | None = None) -> tuple[str, list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """(sql, parameters, result columns, ignored dashboard filters) for one visual."""
    every = {item["id"]: item for item in store.datasets()}
    base = every.get(visual["dataset"])
    if base is None:
        raise QueryError(f"Unknown dataset '{visual['dataset']}'")
    base_path = importer.file_path(base.get("file", ""))
    if base_path is None or not base_path.exists():
        raise QueryError(f"The dataset '{base['name']}' has no data yet: refresh it first")
    plan = Plan(base, every, store.relationships())
    params: list[Any] = []
    select, group, out_columns = [], [], []

    if visual.get("detail"):
        for index, field in enumerate(visual["columns"]):
            expr, column = plan.column(field)
            select.append(f"{expr} AS c{index}")
            out_columns.append({"name": column["name"], "label": field.get("label") or column.get("label") or column["name"],
                                "kind": column["kind"], "role": "column", "dataset": field.get("dataset") or base["id"]})
    else:
        for index, field in enumerate(visual["groupBy"]):
            expr, column = plan.column(field)
            grouped = _group_expr(expr, field)
            select.append(f"{grouped} AS g{index}")
            group.append(f"g{index}")
            out_columns.append({"name": column["name"], "label": field.get("label") or column.get("label") or column["name"],
                                "kind": "text" if field.get("bucket") else column["kind"], "role": "group",
                                "bucket": field.get("bucket", ""), "dataset": field.get("dataset") or base["id"]})
        for index, field in enumerate(visual["values"]):
            agg = field["agg"]
            if agg == "countrows":
                expr, column = "", {"name": "", "label": "rows", "kind": "number"}
            else:
                expr, column = plan.column(field)
                if agg in NUMBER_ONLY and column["kind"] != "number":
                    raise QueryError(f"'{column['name']}' is not a number: use count, distinct, min or max")
            select.append(f"{AGG_SQL[agg].format(c=expr)} AS v{index}")
            label = field.get("label") or (f"{AGG_LABEL[agg]} {column.get('label') or column['name']}" if agg != "countrows" else "Count of rows")
            out_columns.append({"name": column["name"], "label": label, "kind": "number" if agg != "min" and agg != "max" else column["kind"],
                                "role": "value", "agg": agg, "dataset": field.get("dataset") or base["id"]})
        if not select:
            raise QueryError("Add a field to group by or a value to summarise")

    where, ignored = [], []
    for item in visual.get("filters") or []:
        sql = _filter_sql(plan, item, params, optional=False)
        if sql:
            where.append(sql)
    for item in extra_filters or []:                 # dashboard filters: skipped for visuals they do not reach
        sql = _filter_sql(plan, item, params, optional=True)
        if sql:
            where.append(sql)
        elif item.get("op") != "in" or item.get("values"):
            ignored.append(item)

    order = []
    for item in visual.get("sort") or []:
        kind, _, number = item["by"].partition(":")
        name = {"group": "g", "value": "v", "column": "c"}[kind] + number
        if not any(part.endswith(f" AS {name}") for part in select):
            raise QueryError(f"Sort by {item['by']}: there is no such field in the visual")
        order.append(f"{name} {'DESC' if item['dir'] == 'desc' else 'ASC'} NULLS LAST")
    if not order and group:
        bucketed = any(field.get("bucket") for field in visual["groupBy"])
        order = ([f"{g} ASC NULLS LAST" for g in group] if bucketed or not visual["values"] else ["v0 DESC NULLS LAST"])
    limit = min(visual.get("limit") or settings.query_max_rows(), settings.query_max_rows())

    sql = f"SELECT {', '.join(select)} FROM read_parquet({importer.quote_path(base_path)}) AS t0"
    if plan.joins:
        sql += " " + " ".join(plan.joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group:
        sql += " GROUP BY " + ", ".join(group)
    if order:
        sql += " ORDER BY " + ", ".join(order)
    sql += f" LIMIT {limit + 1}"
    return sql, params, out_columns, ignored


def _execute(con: Any, sql: str, params: list[Any]) -> list[tuple]:
    timer = threading.Timer(settings.query_timeout_seconds(), con.interrupt)
    timer.start()
    try:
        return con.execute(sql, params).fetchall()
    except Exception as exc:  # noqa: BLE001 - DuckDB raises its own classes
        if type(exc).__name__ == "InterruptException":
            raise QueryTimeout(f"The visual took longer than {settings.query_timeout_seconds()} s; filter it or summarise more") from None
        raise
    finally:
        timer.cancel()


def run(visuals: list[dict[str, Any]], dashboard_filters: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Answer several visuals with one DuckDB connection (a dashboard asks for all its visuals at once).
    A visual that fails gets {"error": "…"}; the others still get their rows."""
    results = []
    con = importer.connect()
    try:
        for visual in visuals:
            started = time.monotonic()
            own = [item for item in dashboard_filters or [] if item.get("fromTile") != visual.get("tileId")]
            try:
                sql, params, columns, ignored = compile_query(visual, own)
                rows = _execute(con, sql, params)
            except (QueryError, QueryTimeout) as exc:
                results.append({"error": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001
                message = str(exc).split("\n")[0][:300]
                results.append({"error": f"The query could not run: {message}"})
                continue
            limit = min(visual.get("limit") or settings.query_max_rows(), settings.query_max_rows())
            results.append({"columns": columns, "rows": [[json_value(value) for value in row] for row in rows[:limit]],
                            "rowCount": min(len(rows), limit), "truncated": len(rows) > limit,
                            "ignoredFilters": ignored, "durationMs": int((time.monotonic() - started) * 1000)})
    finally:
        con.close()
    return results


def sample(dataset: dict[str, Any], limit: int = 50) -> dict[str, Any]:
    """The first records of an imported dataset."""
    path = importer.file_path(dataset.get("file", ""))
    if path is None or not path.exists():
        return {"columns": [], "rows": [], "rowCount": 0}
    con = importer.connect()
    try:
        cursor = con.execute(f"SELECT * FROM read_parquet({importer.quote_path(path)}) LIMIT {int(limit)}")
        names = [item[0] for item in cursor.description]
        rows = [[json_value(value) for value in row] for row in cursor.fetchall()]
    finally:
        con.close()
    return {"columns": names, "rows": rows, "rowCount": len(rows)}
