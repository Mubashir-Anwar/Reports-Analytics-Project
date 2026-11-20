"""The model's relationships, checked against the data before they are saved.

check()   - how well two columns join: rows, distinct keys and blanks on each side, how many keys of one side are found
            on the other, and the cardinality the data has (one-to-one, many-to-one, one-to-many, many-to-many).
suggest() - pairs of columns that look like keys of each other (same name, "BranchId" next to a dataset "Branch",
            "WorkflowIdentifier" next to "Identifier"), checked on the data and ranked by how many keys match.

Both read only the datasets' Parquet files in a throw-away DuckDB connection; nothing is changed.
"""
from __future__ import annotations

import re
from typing import Any

from backend.app.analytics import importer
from backend.app.analytics.engine import QueryError, _execute, ident

CARDINALITIES = ("many-to-one", "one-to-one", "one-to-many", "many-to-many")
MAX_CANDIDATES = 20


def _file(item: dict[str, Any]) -> str:
    path = importer.file_path(item.get("file") or "") if item.get("file") else None
    if item.get("status") != "ready" or path is None or not path.exists():
        raise QueryError(f"The dataset '{item.get('name')}' has no data yet: refresh it first")
    return importer.quote_path(path)


def _column(item: dict[str, Any], name: str) -> dict[str, Any]:
    for column in item.get("columns") or []:
        if column["name"] == name:
            return column
    raise QueryError(f"'{name}' is not a column of {item.get('name')} (refresh the dataset first)")


def check(left: dict[str, Any], left_column: str, right: dict[str, Any], right_column: str, con: Any = None) -> dict[str, Any]:
    """How the two columns join (keys compared as text, so 42 and "42" match)."""
    a_col, b_col = _column(left, left_column), _column(right, right_column)
    own = con is None
    con = con or importer.connect()
    try:
        a, b = _file(left), _file(right)
        ka = f"NULLIF(TRIM(CAST({ident(left_column)} AS VARCHAR)), '')"
        kb = f"NULLIF(TRIM(CAST({ident(right_column)} AS VARCHAR)), '')"
        sql = f"""
            WITH a AS (SELECT {ka} AS k FROM read_parquet({a})), b AS (SELECT {kb} AS k FROM read_parquet({b})),
                 da AS (SELECT DISTINCT k FROM a WHERE k IS NOT NULL), db AS (SELECT DISTINCT k FROM b WHERE k IS NOT NULL)
            SELECT (SELECT COUNT(*) FROM a), (SELECT COUNT(k) FROM a), (SELECT COUNT(*) FROM da),
                   (SELECT COUNT(*) FROM b), (SELECT COUNT(k) FROM b), (SELECT COUNT(*) FROM db),
                   (SELECT COUNT(*) FROM da JOIN db USING (k)),
                   (SELECT list(k) FROM (SELECT k FROM da EXCEPT SELECT k FROM db LIMIT 5))"""
        try:
            row = _execute(con, sql, [])[0]
        except QueryError:
            raise
        except Exception as exc:  # noqa: BLE001 - DuckDB's own errors
            raise QueryError(f"The columns could not be compared: {str(exc).splitlines()[0][:300]}") from None
    finally:
        if own:
            con.close()
    rows_a, filled_a, distinct_a, rows_b, filled_b, distinct_b, matched, missing = row
    unique_a = filled_a > 0 and distinct_a == filled_a
    unique_b = filled_b > 0 and distinct_b == filled_b
    cardinality = ("one-to-one" if unique_a and unique_b else "many-to-one" if unique_b
                   else "one-to-many" if unique_a else "many-to-many")
    pct_a = round(100 * matched / distinct_a, 1) if distinct_a else 0.0
    pct_b = round(100 * matched / distinct_b, 1) if distinct_b else 0.0
    advice = []
    if matched == 0:
        advice.append("No key of one side is found on the other: these columns do not join (wrong columns, or different formats such as 042 and 42).")
    elif pct_a < 90:
        advice.append(f"Only {pct_a}% of the keys of {left['name']} are found in {right['name']}: the others show as (blank) when the two are combined.")
    if cardinality == "many-to-many":
        advice.append("Both sides repeat their keys (many-to-many): totals of a visual may count rows twice. Usually the side that should be "
                      "the list (branches, products, customers) has duplicates; remove them in its query ($group / DISTINCT).")
    if (a_col.get("kind") or "") != (b_col.get("kind") or "") and a_col.get("kind") and b_col.get("kind"):
        advice.append(f"The columns have different types ({a_col.get('kind')} and {b_col.get('kind')}); they are compared as text.")
    if filled_a < rows_a:
        advice.append(f"{rows_a - filled_a:,} rows of {left['name']} have no {left_column}.")
    return {
        "from": {"dataset": left["id"], "column": left_column, "rows": rows_a, "blanks": rows_a - filled_a, "distinct": distinct_a, "unique": unique_a},
        "to": {"dataset": right["id"], "column": right_column, "rows": rows_b, "blanks": rows_b - filled_b, "distinct": distinct_b, "unique": unique_b},
        "matchedKeys": matched, "fromMatchedPct": pct_a, "toMatchedPct": pct_b, "cardinality": cardinality,
        "missingExamples": [str(v) for v in (missing or [])], "advice": advice,
        "quality": "good" if matched and pct_a >= 90 and cardinality != "many-to-many" else "check" if matched else "bad",
    }


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _pairs(left: dict[str, Any], right: dict[str, Any]) -> list[tuple[str, str, int]]:
    """Columns of left and right that look like the same key: (left column, right column, score)."""
    found = []
    lname, rname = _norm(left["name"]), _norm(right["name"])
    for a in left.get("columns") or []:
        for b in right.get("columns") or []:
            na, nb = _norm(a["name"].split(".")[-1]), _norm(b["name"].split(".")[-1])
            if not na or not nb or a.get("kind") == "bool" or b.get("kind") == "bool":
                continue
            score = 0
            if na == nb and (na.endswith(("id", "code", "key", "no", "number", "identifier")) or len(na) >= 4):
                score = 3 if na.endswith(("id", "code", "key", "identifier")) else 2
            elif nb in ("id", "code", "key", "identifier") and na in (rname + nb, rname.rstrip("s") + nb):
                score = 3                                         # Orders.BranchId -> Branch.Id
            elif na in ("id", "code", "key", "identifier") and nb in (lname + na, lname.rstrip("s") + na):
                score = 3
            elif len(nb) >= 6 and na.endswith(nb) or len(na) >= 6 and nb.endswith(na):
                score = 1                                         # WorkflowIdentifier -> Identifier
            if score:
                found.append((a["name"], b["name"], score))
    return found


def suggest(datasets: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ready = [d for d in datasets if d.get("status") == "ready" and d.get("file")]
    related = {frozenset((r["fromDataset"], r["toDataset"])) for r in relationships}
    candidates = []
    for i, left in enumerate(ready):
        for right in ready[i + 1:]:
            if frozenset((left["id"], right["id"])) in related:
                continue
            candidates.extend((score, left, a, right, b) for a, b, score in _pairs(left, right))
    candidates.sort(key=lambda item: -item[0])
    out = []
    con = importer.connect()
    try:
        for score, left, a, right, b in candidates[:MAX_CANDIDATES]:
            try:
                result = check(left, a, right, b, con)
            except QueryError:
                continue
            if not result["matchedKeys"]:
                continue
            # the "one" side (the list) goes to the right, like Power BI's many-to-one
            if result["cardinality"] == "one-to-many":
                result = check(right, b, left, a, con)
                left, a, right, b = right, b, left, a
            out.append({"fromDataset": left["id"], "fromColumn": a, "toDataset": right["id"], "toColumn": b,
                        "fromName": left["name"], "toName": right["name"], "score": score, **{k: result[k] for k in (
                            "cardinality", "fromMatchedPct", "toMatchedPct", "matchedKeys", "quality", "advice")}})
    finally:
        con.close()
    out.sort(key=lambda s: (s["quality"] != "good", -s["fromMatchedPct"], -s["score"]))
    best: dict[frozenset, dict[str, Any]] = {}
    for item in out:                                              # one suggestion per pair of datasets
        best.setdefault(frozenset((item["fromDataset"], item["toDataset"])), item)
    return list(best.values())
