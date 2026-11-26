"""Analytics → Queries: write a query over the imported datasets (DAX or SQL), run it, keep it.

Every dataset the user may see is a table named like the dataset ("Sales"). Two languages:

* DAX (the common part of Power BI's query language), translated to SQL:
      EVALUATE 'Sales'
      EVALUATE FILTER('Sales', 'Sales'[Branch] = "KHI" && 'Sales'[Amount] > 100)
      EVALUATE TOPN(10, 'Sales', 'Sales'[Amount], DESC)
      EVALUATE SUMMARIZECOLUMNS('Sales'[Branch], "Total", SUM('Sales'[Amount]), "Orders", COUNTROWS('Sales'))
      EVALUATE SUMMARIZECOLUMNS('Sales'[Branch], FILTER('Sales', 'Sales'[Year] = 2026), "Avg", AVERAGE('Sales'[Amount]))
      EVALUATE ADDCOLUMNS(SUMMARIZE('Sales', 'Sales'[Branch]), "Share", DIVIDE(...))      (SELECTCOLUMNS, CALCULATETABLE,
      EVALUATE ROW("Total", SUM('Sales'[Amount]), "KHI", CALCULATE(SUM('Sales'[Amount]), 'Sales'[Branch] = "KHI"))   VALUES, DISTINCT)
      ORDER BY [Total] DESC
  Functions: SUM AVERAGE MIN MAX COUNT COUNTA COUNTROWS DISTINCTCOUNT MEDIAN CALCULATE DIVIDE IF SWITCH AND OR NOT
  BLANK ISBLANK TRUE FALSE YEAR MONTH DAY TODAY NOW DATE FORMAT UPPER LOWER LEN LEFT RIGHT MID TRIM CONCATENATE ROUND
  ABS INT RELATED and the operators + - * / & = == <> < > <= >= && || IN { … }. Columns of other datasets are reached
  through the Model's active relationships, like Power BI: SUMMARIZECOLUMNS('Branches'[Region], "Total",
  SUM('Sales'[Amount])) groups the sales by the region of their branch (the aggregated table is the base, the others
  are LEFT JOINed along the shortest path). The generated SQL is shown next to the result.
* SQL (DuckDB's SELECT), for anything else: joins, window functions, CTEs.

Queries run read-only in a sandbox: the connection can read only the datasets' files (no other file, no network,
no settings), one SELECT statement, the analytics time limit and row limit.
"""
from __future__ import annotations

import re
import time
from typing import Any

from backend.app.analytics import importer, settings
from backend.app.analytics.engine import QueryError, QueryTimeout, _execute, json_value

LANGUAGES = ("dax", "sql")


# ===================================================================== DAX → SQL
_TOKEN = re.compile(r"""
    (?P<space>\s+|--[^\n]*|//[^\n]*|/\*.*?\*/)
  | (?P<table>'(?:[^']|'')+')
  | (?P<column>\[(?:[^\]]|\]\])+\])
  | (?P<string>"(?:[^"]|"")*")
  | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<op>&&|\|\||<=|>=|<>|==|[-+*/&=<>(),{}^])
  | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
""", re.S | re.X)


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise QueryError(f"DAX: unexpected character {text[pos]!r} at position {pos + 1}")
        pos = match.end()
        kind = match.lastgroup
        if kind != "space":
            out.append((kind, match.group()))
    return out


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


AGGREGATES = {"SUM": "SUM", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX", "COUNT": "COUNT", "COUNTA": "COUNT",
              "DISTINCTCOUNT": "COUNT(DISTINCT", "MEDIAN": "MEDIAN", "COUNTROWS": "COUNT(*)"}
TABLE_FUNCTIONS = {"FILTER", "TOPN", "SUMMARIZECOLUMNS", "SUMMARIZE", "CALCULATETABLE", "SELECTCOLUMNS", "ADDCOLUMNS",
                   "VALUES", "DISTINCT", "ROW", "ALL"}


class _Dax:
    """A small recursive-descent parser that writes DuckDB SQL while it reads."""

    SRC = "\x00SRC\x00"                      # where the base table (or the base joined with related ones) goes

    def __init__(self, text: str, tables: set[str], relationships: list[tuple[str, str, str, str]] | None = None):
        self.toks = _tokens(text)
        self.i = 0
        self.tables = {name.casefold(): name for name in tables}
        self.relationships = relationships or []
        self.base: str | None = None          # the table used as a table (EVALUATE 'Sales', FILTER('Sales', …))
        self.first: str | None = None         # the first table named anywhere
        self.aggregated: list[str] = []       # tables named inside SUM(…), COUNTROWS(…), …
        self.refs: list[tuple[str, str]] = []
        self.depth = 0                        # > 0 inside an aggregation

    # ---------------------------------------------------------------- tokens
    def peek(self, offset: int = 0) -> tuple[str, str]:
        index = self.i + offset
        return self.toks[index] if index < len(self.toks) else ("end", "")

    def take(self, value: str | None = None, kind: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if (value is not None and tok[1].upper() != value.upper()) or (kind is not None and tok[0] != kind):
            want = value or kind
            raise QueryError(f"DAX: expected {want} but found {tok[1] or 'the end'}")
        self.i += 1
        return tok

    def at(self, value: str) -> bool:
        return self.peek()[1].upper() == value.upper()

    # ---------------------------------------------------------------- names
    def table_name(self, raw: str, as_table: bool = False) -> str:
        name = raw[1:-1].replace("''", "'") if raw.startswith("'") else raw
        found = self.tables.get(name.casefold())
        if found is None:
            known = ", ".join(sorted(self.tables.values())) or "none"
            raise QueryError(f"DAX: there is no dataset called '{name}' (datasets: {known})")
        self.first = self.first or found
        if self.depth:
            self.aggregated.append(found)
        if as_table:
            if self.base is None:
                self.base = found
            elif self.base != found:
                raise QueryError(f"DAX: '{self.base}' and '{found}' are both used as a table; a DAX query reads one table and "
                                 f"its related ones (use '{found}'[Column] for a related column, or write SQL with a JOIN)")
        return found

    def is_table_token(self) -> bool:
        kind, value = self.peek()
        if kind == "table":
            return self.peek(1)[0] != "column"
        return kind == "name" and value.casefold() in self.tables and self.peek(1)[1] != "(" and self.peek(1)[0] != "column"

    # ---------------------------------------------------------------- query
    def query(self) -> str:
        if self.at("DEFINE"):
            raise QueryError("DAX: DEFINE is not supported yet; put the measure in the query (\"Name\", SUM(...))")
        self.take("EVALUATE")
        sql = self.table_expr()
        if self.at("ORDER"):
            self.take("ORDER")
            self.take("BY")
            parts = []
            while True:
                expr = self.expr(outer=True)
                direction = "ASC"
                if self.at("ASC") or self.at("DESC"):
                    direction = self.take()[1].upper()
                parts.append(f"{expr} {direction} NULLS LAST")
                if not self.at(","):
                    break
                self.take(",")
            sql = f"SELECT * FROM ({sql}) AS o ORDER BY {', '.join(parts)}"
        if self.peek()[0] != "end":
            raise QueryError(f"DAX: unexpected {self.peek()[1]!r} after the query (one EVALUATE per query)")
        return self.finish(sql)

    # ---------------------------------------------------------------- the base table and its related tables
    MARK = re.compile("\x01([^\x1f]*)\x1f([^\x01]*)\x01")
    HIDDEN = "\u27e6"                          # helper columns of related tables: 'Branches⟦Region⟧'

    def path(self, start: str, goal: str) -> list[tuple[str, str, str, str]] | None:
        queue, seen = [(start, [])], {start}
        while queue:
            node, steps = queue.pop(0)
            if node == goal:
                return steps
            for a, ac, b, bc in self.relationships:
                for x, xc, y, yc in ((a, ac, b, bc), (b, bc, a, ac)):
                    if x == node and y not in seen:
                        seen.add(y)
                        queue.append((y, [*steps, (x, xc, y, yc)]))
        return None

    def hidden(self, table: str, column: str) -> str:
        return q(f"{table}{self.HIDDEN}{column}\u27e7")

    def finish(self, sql: str) -> str:
        base = self.base or (self.aggregated[0] if self.aggregated else self.first)
        foreign = list(dict.fromkeys(t for t, _ in self.refs if t != base))
        sql = self.MARK.sub(lambda m: q(m.group(2)) if m.group(1) == base else self.hidden(m.group(1), m.group(2)), sql)
        if base is None:
            return sql
        if not foreign:
            return sql.replace(self.SRC, q(base))
        alias = {base: "t0"}
        joins: list[str] = []
        for table in foreign:
            steps = self.path(base, table)
            if steps is None:
                raise QueryError(f"DAX: '{table}' is not related to '{base}'. Relate them in the Model (Analytics → Model), "
                                 f"or write SQL with a JOIN")
            for a, ac, b, bc in steps:
                if b in alias:
                    continue
                alias[b] = f"t{len(alias)}"
                joins.append(f"LEFT JOIN {q(b)} AS {alias[b]} ON {alias[a]}.{q(ac)} = {alias[b]}.{q(bc)}")
        extra = [f"{alias[t]}.{q(c)} AS {self.hidden(t, c)}" for t, c in dict.fromkeys(self.refs) if t != base]
        source = f"(SELECT t0.*, {', '.join(extra)} FROM {q(base)} AS t0 {' '.join(joins)}) AS {q(base)}"
        # the helper columns of the related tables are not part of the answer
        return f"SELECT COLUMNS(lambda c: NOT contains(c, '{self.HIDDEN}')) FROM ({sql.replace(self.SRC, source)}) AS m"

    def alias_of(self, column: str, others: list[str]) -> str:
        """The output name of a grouped column: its own name ("Region"), or "Branches Region" when two have it."""
        found = self.MARK.fullmatch(column)
        if not found:
            return column
        same = [o for o in others if (m := self.MARK.fullmatch(o)) and m.group(2) == found.group(2)]
        return q(found.group(2) if len(same) <= 1 else f"{found.group(1)} {found.group(2)}")

    def table_expr(self) -> str:
        kind, value = self.peek()
        if self.is_table_token():
            self.take()
            self.table_name(value, as_table=True)
            return f"SELECT * FROM {self.SRC}"
        if kind == "op" and value == "(":
            self.take("(")
            inner = self.table_expr()
            self.take(")")
            return inner
        if kind != "name" or value.upper() not in TABLE_FUNCTIONS:
            raise QueryError(f"DAX: expected a table (a dataset name or FILTER, SUMMARIZECOLUMNS, TOPN…) but found {value or 'the end'}")
        name = value.upper()
        self.take()
        self.take("(")
        sql = getattr(self, f"fn_{name.lower()}")()
        self.take(")")
        return sql

    def comma(self) -> bool:
        if self.at(","):
            self.take(",")
            return True
        return False

    def fn_filter(self) -> str:
        table = self.table_expr()
        self.take(",")
        return f"SELECT * FROM ({table}) AS f WHERE {self.expr()}"

    def fn_calculatetable(self) -> str:
        table = self.table_expr()
        conditions = []
        while self.comma():
            conditions.append(self.filter_arg())
        return f"SELECT * FROM ({table}) AS f" + (f" WHERE {' AND '.join(conditions)}" if conditions else "")

    def filter_arg(self) -> str:
        """A filter argument: a condition, or FILTER(table, condition) (its condition is used)."""
        if self.at("FILTER") and self.peek(1)[1] == "(":
            self.take("FILTER")
            self.take("(")
            self.table_expr()
            self.take(",")
            condition = self.expr()
            self.take(")")
            return f"({condition})"
        return f"({self.expr()})"

    def fn_topn(self) -> str:
        count = self.expr()
        self.take(",")
        table = self.table_expr()
        order = []
        while self.comma():
            expr = self.expr()
            direction = "DESC"
            if self.comma():
                word = self.take()[1].upper()
                direction = "ASC" if word in ("ASC", "1", "TRUE") else "DESC"
            order.append(f"{expr} {direction} NULLS LAST")
        return f"SELECT * FROM ({table}) AS t" + (f" ORDER BY {', '.join(order)}" if order else "") + f" LIMIT {count}"

    def named_values(self) -> list[str]:
        """The "Name", expression pairs at the end of SUMMARIZE / SUMMARIZECOLUMNS / ADDCOLUMNS / ROW."""
        out = []
        while self.peek()[0] == "string":
            label = self.take()[1][1:-1].replace('""', '"')
            self.take(",")
            out.append(f"{self.expr()} AS {q(label)}")
            if not self.comma():
                break
        return out

    def fn_summarizecolumns(self) -> str:
        groups: list[str] = []
        filters: list[str] = []
        values: list[str] = []
        while True:
            kind, value = self.peek()
            if kind == "string":
                values = self.named_values()
                break
            if kind == "column" or (kind in ("table", "name") and self.peek(1)[0] == "column"):
                groups.append(self.column_ref())
            elif kind == "name" and value.upper() in ("FILTER", "CALCULATETABLE", "TREATAS", "KEEPFILTERS"):
                filters.append(self.filter_arg())
            else:
                raise QueryError(f"DAX: SUMMARIZECOLUMNS takes columns, filters and \"Name\", expression pairs; found {value}")
            if not self.comma():
                break
        if self.first is None:
            raise QueryError("DAX: SUMMARIZECOLUMNS needs a column or a measure on a dataset")
        select = [f"{g} AS {self.alias_of(g, groups)}" for g in groups] + values
        sql = f"SELECT {', '.join(select)} FROM {self.SRC}"
        if filters:
            sql += f" WHERE {' AND '.join(filters)}"
        if groups:
            sql += f" GROUP BY {', '.join(groups)}"
        return sql

    def fn_summarize(self) -> str:
        table = self.table_expr()
        groups: list[str] = []
        values: list[str] = []
        while self.comma():
            if self.peek()[0] == "string":
                values = self.named_values()
                break
            groups.append(self.column_ref())
        select = [f"{g} AS {self.alias_of(g, groups)}" for g in groups] + values
        return f"SELECT {', '.join(select) or 'COUNT(*) AS rows'} FROM ({table}) AS s" + (f" GROUP BY {', '.join(groups)}" if groups else "")

    def fn_selectcolumns(self) -> str:
        table = self.table_expr()
        self.take(",")
        return f"SELECT {', '.join(self.named_values())} FROM ({table}) AS s"

    def fn_addcolumns(self) -> str:
        table = self.table_expr()
        self.take(",")
        return f"SELECT *, {', '.join(self.named_values())} FROM ({table}) AS s"

    def fn_values(self) -> str:
        if self.is_table_token():
            return self.table_expr()
        column = self.column_ref()
        if self.first is None:
            raise QueryError("DAX: name the table with the column, like VALUES('Sales'[Branch])")
        return f"SELECT DISTINCT {column} AS {self.alias_of(column, [column])} FROM {self.SRC}"

    fn_distinct = fn_values

    def fn_all(self) -> str:
        return self.fn_values()

    def fn_row(self) -> str:
        values = self.named_values()
        return f"SELECT {', '.join(values)}" + (f" FROM {self.SRC}" if self.first else "")

    # ---------------------------------------------------------------- scalar expressions
    def column_ref(self) -> str:
        kind, value = self.peek()
        table = None
        if kind in ("table", "name") and self.peek(1)[0] == "column":
            self.take()
            table = self.table_name(value)
            kind, value = self.peek()
        if kind != "column":
            raise QueryError(f"DAX: expected a column like 'Sales'[Amount] but found {value or 'the end'}")
        self.take()
        column = value[1:-1].replace("]]", "]")
        if table is None:
            return q(column)                   # [Total]: a column of the result, or of the table being read
        self.refs.append((table, column))
        return f"\x01{table}\x1f{column}\x01"

    BINARY = [("||",), ("&&",), ("=", "==", "<>", "<", ">", "<=", ">=", "IN"), ("&",), ("+", "-"), ("*", "/"), ("^",)]

    def expr(self, level: int = 0, outer: bool = False) -> str:
        if level == len(self.BINARY):
            return self.unary(outer)
        left = self.expr(level + 1, outer)
        while self.peek()[1].upper() in self.BINARY[level] and self.peek()[0] in ("op", "name"):
            op = self.take()[1].upper()
            if op == "IN":
                self.take("{")
                items = [self.expr()]
                while self.comma():
                    items.append(self.expr())
                self.take("}")
                left = f"({left} IN ({', '.join(items)}))"
                continue
            right = self.expr(level + 1, outer)
            if op in ("=", "==", "<>") and "NULL" in (left, right):
                other = right if left == "NULL" else left
                left = f"({other} IS {'NOT ' if op == '<>' else ''}NULL)"
                continue
            sql_op = {"||": "OR", "&&": "AND", "==": "=", "&": "||", "^": "**"}.get(op, op)
            if op == "&":
                left = f"(CAST({left} AS VARCHAR) || CAST({right} AS VARCHAR))"
            elif op == "/":
                left = f"({left} / NULLIF({right}, 0))"
            else:
                left = f"({left} {sql_op} {right})"
        return left

    def unary(self, outer: bool) -> str:
        if self.at("-"):
            self.take("-")
            return f"(-{self.unary(outer)})"
        if self.at("+"):
            self.take("+")
            return self.unary(outer)
        if self.at("NOT") and self.peek(1)[1] != "(":
            self.take("NOT")
            return f"(NOT {self.unary(outer)})"
        return self.primary(outer)

    def primary(self, outer: bool) -> str:
        kind, value = self.peek()
        if kind == "number":
            self.take()
            return value
        if kind == "string":
            self.take()
            return lit(value[1:-1].replace('""', '"'))
        if kind == "op" and value == "(":
            self.take("(")
            inner = self.expr()
            self.take(")")
            return f"({inner})"
        if kind == "op" and value == "{":
            raise QueryError("DAX: a list { … } is only allowed after IN")
        if kind == "column" or (kind in ("table", "name") and self.peek(1)[0] == "column"):
            return self.column_ref()
        if kind == "name" and self.peek(1)[1] == "(":
            return self.call(value.upper())
        if kind == "name" and value.upper() in ("TRUE", "FALSE"):
            self.take()
            return value.upper()
        raise QueryError(f"DAX: unexpected {value or 'end of the query'}")

    def call(self, name: str) -> str:
        self.take()
        self.take("(")
        out = self._call(name)
        self.take(")")
        return out

    def _call(self, name: str) -> str:
        if name == "RELATED":
            return self.column_ref()             # a column of a related dataset (the Model's relationship is followed)
        if name in AGGREGATES:
            self.depth += 1
            try:
                if name == "COUNTROWS":
                    if not self.at(")"):
                        self.table_expr_for_count()
                    return "COUNT(*)"
                column = self.expr()
            finally:
                self.depth -= 1
            return f"COUNT(DISTINCT {column})" if name == "DISTINCTCOUNT" else f"{AGGREGATES[name]}({column})"
        if name == "CALCULATE":
            self.depth += 1
            try:
                measure = self.expr()
            finally:
                self.depth -= 1
            conditions = []
            while self.comma():
                conditions.append(self.filter_arg())
            if not conditions:
                return measure
            match = re.fullmatch(r"(COUNT|SUM|AVG|MIN|MAX|MEDIAN)\((.*)\)", measure, re.S)
            if not match:
                raise QueryError("DAX: CALCULATE needs an aggregation first, like CALCULATE(SUM('T'[x]), 'T'[y] = \"a\")")
            return f"{measure} FILTER (WHERE {' AND '.join(conditions)})"
        args = [] if self.at(")") else [self.expr()]
        while self.comma():
            args.append(self.expr())
        simple = {"UPPER": "UPPER", "LOWER": "LOWER", "LEN": "LENGTH", "TRIM": "TRIM", "ABS": "ABS", "YEAR": "YEAR",
                  "MONTH": "MONTH", "DAY": "DAY", "INT": "FLOOR", "ISBLANK": None}
        if name == "ISBLANK":
            return f"({args[0]} IS NULL)"
        if name in simple and len(args) == 1:
            return f"{simple[name]}({args[0]})"
        if name == "BLANK" and not args:
            return "NULL"
        if name in ("TRUE", "FALSE") and not args:
            return name
        if name == "TODAY" and not args:
            return "CURRENT_DATE"
        if name == "NOW" and not args:
            return "CURRENT_TIMESTAMP"
        if name == "DATE" and len(args) == 3:
            return f"MAKE_DATE({args[0]}, {args[1]}, {args[2]})"
        if name == "DIVIDE" and len(args) in (2, 3):
            alternate = args[2] if len(args) == 3 else "NULL"
            return f"COALESCE({args[0]} / NULLIF({args[1]}, 0), {alternate})"
        if name == "IF" and len(args) in (2, 3):
            return f"(CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2] if len(args) == 3 else 'NULL'} END)"
        if name == "SWITCH" and len(args) >= 3:
            subject, rest = args[0], args[1:]
            pairs = [f"WHEN {rest[i]} THEN {rest[i + 1]}" for i in range(0, len(rest) - 1, 2)]
            otherwise = rest[-1] if len(rest) % 2 else "NULL"
            if subject == "TRUE":
                return f"(CASE {' '.join(pairs)} ELSE {otherwise} END)"
            return f"(CASE {subject} {' '.join(pairs)} ELSE {otherwise} END)"
        if name in ("AND", "OR") and len(args) == 2:
            return f"({args[0]} {name} {args[1]})"
        if name == "NOT" and len(args) == 1:
            return f"(NOT {args[0]})"
        if name == "ROUND" and len(args) in (1, 2):
            return f"ROUND({args[0]}, {args[1] if len(args) == 2 else 0})"
        if name == "LEFT" and len(args) in (1, 2):
            return f"LEFT(CAST({args[0]} AS VARCHAR), {args[1] if len(args) == 2 else 1})"
        if name == "RIGHT" and len(args) in (1, 2):
            return f"RIGHT(CAST({args[0]} AS VARCHAR), {args[1] if len(args) == 2 else 1})"
        if name == "MID" and len(args) == 3:
            return f"SUBSTRING(CAST({args[0]} AS VARCHAR), {args[1]}, {args[2]})"
        if name == "CONCATENATE" and len(args) == 2:
            return f"(CAST({args[0]} AS VARCHAR) || CAST({args[1]} AS VARCHAR))"
        if name == "FORMAT" and len(args) == 2:
            return f"CAST({args[0]} AS VARCHAR)"
        raise QueryError(f"DAX: {name}() with {len(args)} argument(s) is not supported here (write SQL for it)")

    def table_expr_for_count(self) -> None:
        if self.is_table_token():
            self.table_name(self.take()[1])
        else:
            self.table_expr()


def dax_to_sql(text: str, tables: set[str], relationships: list[tuple[str, str, str, str]] | None = None) -> str:
    """relationships: (from dataset name, column, to dataset name, column) of the active relationships."""
    if not str(text or "").strip():
        raise QueryError("Write a query first, e.g. EVALUATE 'Sales'")
    return _Dax(text, tables, relationships).query()


# ===================================================================== SQL checks
_STRIP = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", re.S)


def check_sql(text: str) -> str:
    sql = str(text or "").strip().rstrip(";").strip()
    if not sql:
        raise QueryError("Write a query first, e.g. SELECT * FROM \"Sales\" LIMIT 10")
    bare = _STRIP.sub(" ", sql)
    if ";" in bare:
        raise QueryError("SQL: one statement per query")
    if not re.match(r"^\s*(SELECT|WITH|FROM|VALUES|SUMMARIZE|PIVOT|UNPIVOT|DESCRIBE)\b", bare, re.I):
        raise QueryError("SQL: only reading queries (SELECT … / WITH …) can be run here")
    return sql


# ===================================================================== running
def _register(con: Any, datasets: list[dict[str, Any]]) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    paths: list[str] = []
    for item in datasets:
        path = importer.file_path(item.get("file") or "") if item.get("file") else None
        if item.get("status") != "ready" or path is None or not path.exists():
            continue
        quoted = importer.quote_path(path)
        for name in {item["name"], item["id"]}:
            if name.casefold() in {n.casefold() for n in names}:
                continue
            con.execute(f"CREATE TEMP VIEW {q(name)} AS SELECT * FROM read_parquet({quoted})")
            names.add(name)
        paths.append(quoted)
    # the sandbox: from here on only these files can be read, nothing written, no setting changed
    con.execute(f"SET allowed_paths=[{', '.join(paths)}]")
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    return names, paths


def run(language: str, text: str, datasets: list[dict[str, Any]], limit: int | None = None,
        relationships: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run one query over the given datasets (those the user may see). {columns, rows, sql, rowCount, truncated, durationMs}."""
    language = str(language or "dax").lower()
    if language not in LANGUAGES:
        raise QueryError("language must be dax or sql")
    if len(str(text or "")) > 20_000:
        raise QueryError("The query may have at most 20,000 characters")
    max_rows = min(int(limit or settings.query_max_rows()), settings.query_max_rows())
    con = importer.connect()
    try:
        tables, _ = _register(con, datasets)
        names = {item["id"]: item["name"] for item in datasets if item["name"] in tables}
        links = [(names[r["fromDataset"]], r["fromColumn"], names[r["toDataset"]], r["toColumn"]) for r in relationships or []
                 if r.get("active") is not False and r["fromDataset"] in names and r["toDataset"] in names]
        sql = dax_to_sql(text, tables, links) if language == "dax" else check_sql(text)
        started = time.monotonic()
        wrapped = f"SELECT * FROM ({sql}) AS result LIMIT {max_rows + 1}"
        try:
            cursor_rows = _execute(con, wrapped, [])
            description = con.description or []
        except QueryTimeout:
            raise
        except QueryError:
            raise
        except Exception as exc:  # noqa: BLE001 - DuckDB's own errors: shown to the writer of the query
            message = str(exc).split("\n")[0][:400]
            if "Permission Error" in message:
                message = "Only the datasets can be read in a query (no files, no settings)"
            raise QueryError(f"The query could not run: {message}") from None
        columns = [{"name": item[0], "type": str(item[1])} for item in description]
        return {"language": language, "sql": sql, "columns": columns,
                "rows": [[json_value(value) for value in row] for row in cursor_rows[:max_rows]],
                "rowCount": min(len(cursor_rows), max_rows), "truncated": len(cursor_rows) > max_rows,
                "durationMs": int((time.monotonic() - started) * 1000), "tables": sorted(tables)}
    finally:
        con.close()
