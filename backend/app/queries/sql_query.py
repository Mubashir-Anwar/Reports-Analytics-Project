"""SQL report queries (classic designer style) for the SQL connectors.

* `$P{Name}`               → a bound parameter (never pasted into the SQL text)
* `$X{IN, column, Name}`    → `column = ANY(<list>)` (PostgreSQL) or `column IN (…)`; an empty list = no filter
* `$X{NOTIN, column, Name}` → `NOT (column = ANY(<list>))`; empty = no filter
* `$P!{Name}`               → refused: it would paste text into the SQL

Only one read-only statement is allowed (SELECT / WITH … SELECT / VALUES / TABLE). The connector also runs it in a
read-only transaction with a time limit, so this check is the first of two safety layers, not the only one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PARAM = re.compile(r"\$P\{([A-Za-z_][A-Za-z0-9_]*)\}")
PARAM_LITERAL = re.compile(r"\$P!\{")
X_IN = re.compile(r"\$X\{\s*(IN|NOTIN)\s*,\s*([^,{}]+?)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}", re.IGNORECASE)
FIRST_WORDS = {"select", "with", "values", "table"}
# statements or clauses that change data, lock rows or run code — refused anywhere outside strings and identifiers
# (Words that are also common column or function names — comment, replace, load — are not listed.)
FORBIDDEN = {
    "insert", "update", "delete", "merge", "upsert", "drop", "alter", "create", "truncate", "grant", "revoke",
    "copy", "call", "execute", "exec", "prepare", "deallocate", "lock", "vacuum", "cluster", "reindex",
    "refresh", "listen", "notify", "unlisten", "set", "reset", "discard", "begin", "commit", "rollback", "savepoint",
    "into",
}


@dataclass(frozen=True)
class PreparedSql:
    """A checked query: the SQL with placeholders and the parameters it uses."""
    source: str
    parts: tuple[Any, ...]  # str pieces, ("P", name) and ("X", negate, column, name) tokens
    parameter_names: tuple[str, ...] = field(default=())

    def bind(self, values: dict[str, Any], style: str = "pyformat", lists: str = "any") -> tuple[str, dict[str, Any]]:
        """SQL text for the driver and the values to bind.
        style: "pyformat" (%(name)s: psycopg, PyMySQL, pymssql), "named" (:name: Oracle, SQLite) or "dollar" ($name: DuckDB).
        lists: "any" (column = ANY(array): PostgreSQL) or "expand" (column IN (:a, :b, …): the others)."""
        pyformat = style == "pyformat"
        placeholder = (lambda name: f"%({name})s") if pyformat else (lambda name: f"${name}") if style == "dollar" else (lambda name: f":{name}")
        sql, params = [], {}
        for index, part in enumerate(self.parts):
            if isinstance(part, str):
                sql.append(part.replace("%", "%%") if pyformat else part)
            elif part[0] == "P":
                key = f"p_{part[1]}"
                params[key] = values.get(part[1])
                sql.append(placeholder(key))
            else:
                _, negate, column, name = part
                items = _as_list(values.get(name))
                if not items:
                    sql.append("(1 = 1)")  # Classic reports: an empty $X{IN} list does not filter
                    continue
                key = f"x{index}_{name}"
                if lists == "any":
                    params[key] = items
                    condition = f"{column} = ANY({placeholder(key)})"
                else:
                    if len(items) > 1000:
                        raise ValueError(f"$X{{IN, {column}, {name}}}: at most 1000 values")
                    keys = [f"{key}_{i}" for i in range(len(items))]
                    params.update(zip(keys, items))
                    condition = f"{column} IN ({', '.join(placeholder(k) for k in keys)})"
                sql.append(f"(NOT ({condition}))" if negate else f"({condition})")
        return "".join(sql), params


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _scan(source: str) -> list[tuple[str, str]]:
    """Split SQL into ("code" | "string" | "ident" | "comment", text) pieces, honouring quotes, $$-strings and comments."""
    pieces, i, n, start = [], 0, len(source), 0

    def flush(kind: str, end: int) -> None:
        if end > start:
            pieces.append((kind, source[start:end]))

    while i < n:
        ch = source[i]
        two = source[i:i + 2]
        if two == "--":
            flush("code", i)
            end = source.find("\n", i)
            end = n if end < 0 else end
            pieces.append(("comment", source[i:end]))
            i = start = end
            continue
        if two == "/*":
            flush("code", i)
            end = source.find("*/", i + 2)
            if end < 0:
                raise ValueError("A /* comment is not closed")
            pieces.append(("comment", source[i:end + 2]))
            i = start = end + 2
            continue
        if ch in ("'", '"'):
            flush("code", i)
            j = i + 1
            while True:
                j = source.find(ch, j)
                if j < 0:
                    raise ValueError("A quote in the SQL is not closed")
                if source[j + 1:j + 2] == ch:  # doubled quote = escaped
                    j += 2
                    continue
                break
            pieces.append(("string" if ch == "'" else "ident", source[i:j + 1]))
            i = start = j + 1
            continue
        dollar = re.match(r"\$([A-Za-z_]\w*)?\$", source[i:]) if ch == "$" and source[i + 1:i + 2] not in ("P", "X") else None
        if dollar:
            tag = dollar.group(0)
            end = source.find(tag, i + len(tag))
            if end < 0:
                raise ValueError("A $$ string in the SQL is not closed")
            flush("code", i)
            pieces.append(("string", source[i:end + len(tag)]))
            i = start = end + len(tag)
            continue
        i += 1
    flush("code", n)
    return pieces


def prepare_sql(source: Any) -> PreparedSql:
    """Check a SQL report query and turn its $P{} / $X{} references into bound parameters."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Write the SQL query (a SELECT statement)")
    if len(source) > 200_000:
        raise ValueError("The SQL query is longer than 200 000 characters")
    pieces = _scan(source)
    code = " ".join(text for kind, text in pieces if kind == "code")
    if PARAM_LITERAL.search(code):
        raise ValueError("$P!{…} (text pasted into the SQL) is not allowed; use $P{…} or $X{IN, column, Param}")
    stripped = re.sub(r"\$[PX]\{[^{}]*\}", " ", code)
    statements = [chunk for chunk in stripped.split(";") if chunk.strip()]
    if len(statements) != 1:
        raise ValueError("Give exactly one SQL statement (no ; between statements)")
    words = re.findall(r"[A-Za-z_]+", stripped.lower())
    if not words or words[0] not in FIRST_WORDS:
        raise ValueError("The SQL query must start with SELECT or WITH (reports only read data)")
    bad = sorted(set(words) & FORBIDDEN)
    if bad:
        raise ValueError(f"Reports may only read data: {', '.join(word.upper() for word in bad)} is not allowed in the query")
    if re.search(r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b", stripped, re.IGNORECASE):
        raise ValueError("FOR UPDATE / FOR SHARE (row locks) is not allowed in a report query")

    parts: list[Any] = []
    names: list[str] = []
    for kind, text in pieces:
        if kind != "code":
            parts.append(text)
            continue
        position = 0
        for match in re.finditer(rf"{PARAM.pattern}|{X_IN.pattern}", text, re.IGNORECASE):
            parts.append(text[position:match.start()])
            if match.group(1):
                parts.append(("P", match.group(1)))
                names.append(match.group(1))
            else:
                column = match.group(3).strip()
                if not re.fullmatch(r'[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)?|"[^"]+"(\."[^"]+")?', column):
                    raise ValueError(f"$X{{…}}: “{column}” must be a column name")
                parts.append(("X", match.group(2).upper() == "NOTIN", column, match.group(4)))
                names.append(match.group(4))
            position = match.end()
        parts.append(text[position:])
    # a trailing ; is harmless; drop it so the query can be wrapped (SELECT * FROM (<query>) …)
    while parts and isinstance(parts[-1], str) and not parts[-1].strip():
        parts.pop()
    if parts and isinstance(parts[-1], str):
        parts[-1] = parts[-1].rstrip().rstrip(";")
    return PreparedSql(source=source, parts=tuple(p for p in parts if p != ""), parameter_names=tuple(dict.fromkeys(names)))
