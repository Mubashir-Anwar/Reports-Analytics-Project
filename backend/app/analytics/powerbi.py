"""Power BI templates (.pbit) → Analytics dashboards: a partial import of the report's pages and visuals.

A .pbit is a ZIP package with the data model's definition (DataModelSchema: tables, columns, measures, relationships)
and the report (Report/Layout, or Report/definition/... in the newer PBIR format) but no data. A .pbix has the same
report part but keeps its data model and data in Microsoft's closed VertiPaq format. For a .pbix the browser takes
only the report part out of the file (open_parts: no data is uploaded); the tables and columns are then known only
from the visuals' fields, so measures (their DAX), column types and relationships are missing. A template from Power
BI Desktop (File → Export → Power BI template) gives the full model.

What is converted: every page becomes a dashboard; cards, column/bar/line/area/pie/donut/scatter charts, tables,
matrices and slicers become visuals at about the same place; columns with their summarisation, date hierarchy levels
(year, quarter, month, day) and simple measures (SUM, AVERAGE, MIN, MAX, COUNT, COUNTA, DISTINCTCOUNT, MEDIAN of one
column, COUNTROWS of a table); relationships between tables. What is not: other DAX, Power Query steps, page and
report filters, formatting, text boxes, images and shapes, and visuals with no counterpart here (each one is reported).

Every Power BI table used by the chosen pages is matched to a dataset here: an existing one (its columns matched by
name, ignoring case) or a new one on one of this server's data adapters. The conversion builds the same bundle a
.mdash upload has, and bundle.install saves it."""
from __future__ import annotations

import base64
import binascii
import io
import json
import re
import zipfile
from typing import Any

from backend.app.analytics import bundle, store

MAX_FILE = 15_000_000              # the .pbit itself (a template has no data: usually well under 1 MB)
MAX_PART = 40_000_000              # one part of it, unpacked (a ZIP bomb stops here)
GRID_WIDTH = 1200                  # px of a dashboard on a usual screen: 12 columns
ROW_PX = 76                        # one dashboard row (64 px) and its gap (12 px)

# Power BI visual type → (visual here, options, how it differs)
VISUAL_TYPES: dict[str, tuple[str, dict[str, Any], str]] = {
    "card": ("kpi", {}, ""), "multiRowCard": ("kpi", {}, "a card with several values shows the first two"),
    "kpi": ("kpi", {}, "shown as a card without its target and trend"), "gauge": ("kpi", {}, "shown as a card"),
    "columnChart": ("column", {}, ""), "clusteredColumnChart": ("column", {}, ""),
    "stackedColumnChart": ("column", {"stacked": True}, ""),
    "hundredPercentStackedColumnChart": ("column", {"stacked": True}, "shown stacked, not as 100%"),
    "barChart": ("bar", {}, ""), "clusteredBarChart": ("bar", {}, ""), "stackedBarChart": ("bar", {"stacked": True}, ""),
    "hundredPercentStackedBarChart": ("bar", {"stacked": True}, "shown stacked, not as 100%"),
    "lineChart": ("line", {}, ""), "areaChart": ("area", {}, ""), "stackedAreaChart": ("area", {"stacked": True}, ""),
    "lineClusteredColumnComboChart": ("column", {}, "a combo chart: its lines are shown as columns"),
    "lineStackedColumnComboChart": ("column", {"stacked": True}, "a combo chart: its lines are shown as columns"),
    "ribbonChart": ("column", {"stacked": True}, "shown as a stacked column chart"),
    "waterfallChart": ("column", {}, "shown as a column chart"), "funnel": ("bar", {}, "shown as a bar chart"),
    "pieChart": ("pie", {}, ""), "donutChart": ("donut", {}, ""), "treemap": ("donut", {}, "shown as a donut chart"),
    "scatterChart": ("scatter", {}, ""),
    "tableEx": ("table", {}, ""), "table": ("table", {}, ""), "pivotTable": ("table", {}, "a matrix is shown as a table"),
    "matrix": ("table", {}, "a matrix is shown as a table"),
    "slicer": ("slicer", {}, ""), "advancedSlicerVisual": ("slicer", {}, ""),
}
DECORATIVE = {"textbox", "image", "shape", "basicShape", "actionButton", "pageNavigator", "bookmarkNavigator"}
CATEGORY_ROLES = ("Category", "Axis", "Rows", "Columns", "Details", "Group", "Series", "Legend")
VALUE_ROLES = ("Values", "Value", "Y", "ColumnY", "LineY", "Y2", "Data", "Indicator", "X", "Size", "Fields")
IGNORED_ROLES = ("Tooltips", "TargetValue", "MinValue", "MaxValue", "Goal", "TrendLine")
AGGREGATION_CODES = {0: "sum", 1: "avg", 2: "distinct", 3: "min", 4: "max", 5: "count", 6: "median"}
SUMMARIZE_BY = {"sum": "sum", "average": "avg", "min": "min", "max": "max", "count": "count",
                "distinctcount": "distinct", "median": "median"}
DAX_AGG = {"SUM": "sum", "SUMX": "", "AVERAGE": "avg", "MIN": "min", "MAX": "max", "COUNT": "count", "COUNTA": "count",
           "DISTINCTCOUNT": "distinct", "MEDIAN": "median"}
BUCKET_LEVELS = {"year": "year", "quarter": "quarter", "month": "month", "day": "day"}
KINDS = {"int64": ("BIGINT", "number"), "double": ("DOUBLE", "number"), "decimal": ("DOUBLE", "number"),
         "datetime": ("TIMESTAMP", "date"), "date": ("DATE", "date"), "boolean": ("BOOLEAN", "bool")}
SOURCES = (("Sql.Database", "SQL Server", "sql"), ("PostgreSQL.Database", "PostgreSQL", "sql"),
           ("MySQL.Database", "MySQL", "sql"), ("Oracle.Database", "Oracle", "sql"), ("Snowflake.", "Snowflake", "sql"),
           ("GoogleBigQuery.", "BigQuery", "sql"), ("Odbc.", "ODBC", "sql"), ("MongoDB.", "MongoDB", "mongodb"),
           ("Excel.Workbook", "Excel", ""), ("Csv.Document", "CSV", ""), ("Json.Document", "JSON", ""),
           ("SharePoint.", "SharePoint", ""), ("Web.Contents", "Web", ""), ("OData.Feed", "OData", ""))
AUTO_DATE_TABLE = re.compile(r"^(LocalDateTable|DateTableTemplate)_")
MEASURE = re.compile(r"^\s*(SUM|AVERAGE|MIN|MAX|COUNTA?|DISTINCTCOUNT|MEDIAN)\s*\(\s*('(?:[^']|'')+'|[\w ]*?)\s*\[([^\]]+)\]\s*\)\s*$", re.I)
COUNTROWS = re.compile(r"^\s*COUNTROWS\s*\(\s*('(?:[^']|'')+'|[\w ]+?)\s*\)\s*$", re.I)


# ------------------------------------------------------------------ reading the package
def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le")
    if len(raw) > 1 and raw[1:2] == b"\x00":                 # UTF-16 without a byte order mark (Report/Layout)
        return raw.decode("utf-16-le")
    return raw.decode("utf-8")


def _parse(raw: bytes, name: str) -> Any:
    if len(raw) > MAX_PART:
        raise ValueError(f"'{name}' in the file is too big")
    try:
        return json.loads(_decode(raw))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"'{name}' in the file could not be read") from None


class _Zip:
    """The parts of a whole package (.pbit)."""

    def __init__(self, archive: zipfile.ZipFile):
        self.archive = archive
        self.names = set(archive.namelist())

    def json(self, name: str) -> Any:
        info = self.archive.getinfo(name)
        if info.file_size > MAX_PART:
            raise ValueError(f"'{name}' in the file is too big")
        with self.archive.open(info) as handle:
            return _parse(handle.read(MAX_PART + 1), name)


class _Parts:
    """Some parts taken out of a package by the browser (.pbix: only the report, never the data)."""

    def __init__(self, parts: dict[str, bytes]):
        self.parts = parts
        self.names = set(parts)

    def json(self, name: str) -> Any:
        return _parse(self.parts[name], name)


def open_template(content: Any) -> dict[str, Any]:
    """{"model": the data model, "pages": [...]} from the uploaded file (base64 text)."""
    if not isinstance(content, str) or not content:
        raise ValueError("Send the .pbit file as base64 text in 'content'")
    if len(content) > MAX_FILE * 4 // 3 + 16:
        raise ValueError("The Power BI template is too big (at most 15 MB)")
    try:
        raw = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("'content' is not base64 text") from None
    if not raw.startswith(b"PK"):
        raise ValueError("This is not a Power BI template (.pbit)")
    try:
        package = _Zip(zipfile.ZipFile(io.BytesIO(raw)))
    except (zipfile.BadZipFile, ValueError):
        raise ValueError("This is not a Power BI template (.pbit): the package is damaged") from None
    if "DataModelSchema" not in package.names:
        if "DataModel" in package.names:
            raise ValueError("This is a Power BI report with its data (.pbix): import it as a .pbix file (only its report "
                             "part is read), or export a template in Power BI Desktop (File → Export → Power BI template)")
        raise ValueError("This is not a Power BI template (.pbit): it has no data model")
    return _read(package, (package.json("DataModelSchema") or {}).get("model") or {}, "pbit")


def open_parts(parts: Any) -> dict[str, Any]:
    """The report part of a .pbix, taken out of the file by the browser: {name: base64 of the unpacked part}."""
    if not isinstance(parts, dict) or not parts:
        raise ValueError("Send the report parts of the .pbix in 'parts'")
    clean, total = {}, 0
    for name, value in parts.items():
        if not isinstance(name, str) or not (name == "Report/Layout" or name.startswith("Report/definition/")) or len(parts) > 2000:
            raise ValueError("Only the report parts of a .pbix are read (Report/Layout or Report/definition/…)")
        try:
            raw = base64.b64decode(str(value), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f"'{name[:80]}' is not base64 text") from None
        total += len(raw)
        if total > MAX_PART:
            raise ValueError("The report part of the .pbix is too big")
        clean[name] = raw
    template = _read(_Parts(clean), {}, "pbix")
    template["model"] = model_from_pages(template["pages"])
    return template


def _read(package: Any, model: dict[str, Any], kind: str) -> dict[str, Any]:
    if "Report/Layout" in package.names:
        pages = _layout_pages(package.json("Report/Layout"))
    else:
        pages = _pbir_pages(package)
    if not pages:
        raise ValueError("The file has no report pages")
    return {"model": model, "pages": pages, "kind": kind}


def model_from_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """A data model made from the visuals' fields (a .pbix keeps its real model in a closed format): every table and
    column a visual uses, with a type guessed from how it is used; measures are named but their DAX is unknown."""
    found: dict[str, dict[str, Any]] = {}

    def table(name: str) -> dict[str, Any]:
        return found.setdefault(name, {"name": name, "columns": {}, "measures": {}})

    def visit(expr: Any, summarised: bool = False) -> None:
        if not isinstance(expr, dict):
            return
        if "Column" in expr:
            col = expr["Column"]
            name, entity = str(col.get("Property") or ""), _ref(col)
            if name and entity:
                columns = table(entity)["columns"]
                columns[name] = "double" if summarised else columns.get(name, "string")
        elif "Aggregation" in expr:
            code = (expr["Aggregation"] or {}).get("Function")
            visit((expr["Aggregation"] or {}).get("Expression"), summarised=code in (0, 1, 3, 4, 6))
        elif "Measure" in expr:
            entity, name = _ref(expr["Measure"]), str(expr["Measure"].get("Property") or "")
            if entity and name:
                table(entity)["measures"][name] = ""
        elif "HierarchyLevel" in expr:
            hierarchy = (((expr["HierarchyLevel"].get("Expression") or {}).get("Hierarchy")) or {}).get("Expression") or {}
            variation = hierarchy.get("PropertyVariationSource")
            if variation and _ref(variation) and variation.get("Property"):
                table(_ref(variation))["columns"][str(variation["Property"])] = "dateTime"
            elif (hierarchy.get("SourceRef") or {}).get("Entity") and expr["HierarchyLevel"].get("Level"):
                columns = table(hierarchy["SourceRef"]["Entity"])["columns"]
                columns.setdefault(str(expr["HierarchyLevel"]["Level"]), "string")

    for page in pages:
        for visual in page["visuals"]:
            for fields in (visual.get("roles") or {}).values():
                for expr in fields or []:
                    visit(expr)
    return {"tables": [{"name": item["name"], "columns": [{"name": c, "dataType": t} for c, t in item["columns"].items()],
                        "measures": [{"name": m, "expression": ""} for m in item["measures"]]} for item in found.values()],
            "relationships": []}


def _title(objects: Any) -> str:
    try:
        value = objects["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
    except (KeyError, IndexError, TypeError):
        return ""
    return str(value).strip("'").replace("''", "'")[:120] if isinstance(value, str) else ""


def _with_entities(value: Any, aliases: dict[str, str]) -> Any:
    """The legacy layout names tables by an alias (SourceRef.Source): put the table name in, as PBIR has it."""
    if isinstance(value, dict):
        if "SourceRef" in value and isinstance(value["SourceRef"], dict) and "Source" in value["SourceRef"]:
            value = {**value, "SourceRef": {"Entity": aliases.get(value["SourceRef"]["Source"], value["SourceRef"]["Source"])}}
        return {key: _with_entities(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [_with_entities(item, aliases) for item in value]
    return value


def _layout_pages(layout: Any) -> list[dict[str, Any]]:
    pages = []
    for section in (layout or {}).get("sections") or []:
        visuals = []
        for container in section.get("visualContainers") or []:
            try:
                config = json.loads(container.get("config") or "{}")
            except json.JSONDecodeError:
                continue
            single = config.get("singleVisual")
            if not isinstance(single, dict):
                continue                                   # a group of visuals: its members are listed too
            query = single.get("prototypeQuery") or {}
            aliases = {item.get("Name"): item.get("Entity") for item in query.get("From") or [] if isinstance(item, dict)}
            selects = {item.get("Name"): _with_entities(item, aliases) for item in query.get("Select") or [] if isinstance(item, dict)}
            roles = {role: [selects[ref["queryRef"]] for ref in refs if isinstance(ref, dict) and ref.get("queryRef") in selects]
                     for role, refs in (single.get("projections") or {}).items() if isinstance(refs, list)}
            visuals.append({"type": str(single.get("visualType") or ""), "title": _title(single.get("vcObjects")),
                            **{name: float(container.get(name) or 0) for name in ("x", "y", "width", "height")}, "roles": roles})
        pages.append({"name": str(section.get("displayName") or section.get("name") or f"Page {len(pages) + 1}")[:100],
                      "width": float(section.get("width") or 1280), "height": float(section.get("height") or 720),
                      "visuals": visuals})
    return pages


def _pbir_pages(package: Any) -> list[dict[str, Any]]:
    names = package.names
    root = "Report/definition/pages/"
    folders = sorted({name[len(root):].split("/")[0] for name in names if name.startswith(root) and name.endswith("/page.json")})
    order_file = root + "pages.json"
    order = (package.json(order_file) or {}).get("pageOrder") if order_file in names else None
    if isinstance(order, list):
        folders = [item for item in order if item in folders] + [item for item in folders if item not in order]
    pages = []
    for folder in folders:
        page = package.json(f"{root}{folder}/page.json") or {}
        visuals = []
        for name in sorted(names):
            if not (name.startswith(f"{root}{folder}/visuals/") and name.endswith("/visual.json")):
                continue
            item = package.json(name) or {}
            visual = item.get("visual") or {}
            position = item.get("position") or {}
            state = ((visual.get("query") or {}).get("queryState")) or {}
            roles = {role: [proj.get("field") for proj in (part or {}).get("projections") or [] if isinstance(proj, dict)]
                     for role, part in state.items() if isinstance(part, dict)}
            visuals.append({"type": str(visual.get("visualType") or ""), "title": _title(visual.get("visualContainerObjects")),
                            **{key: float(position.get(key) or 0) for key in ("x", "y", "width", "height")}, "roles": roles})
        pages.append({"name": str(page.get("displayName") or folder)[:100], "width": float(page.get("width") or 1280),
                      "height": float(page.get("height") or 720), "visuals": visuals})
    return pages


# ------------------------------------------------------------------ the data model
def _expression(value: Any) -> str:
    return "\n".join(value) if isinstance(value, list) else str(value or "")


def _table_name(text: str) -> str:
    text = text.strip()
    return text[1:-1].replace("''", "'") if text.startswith("'") and text.endswith("'") else text


def tables(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The model's tables (without Power BI's automatic date tables): columns, measures and where the data came from."""
    out: dict[str, dict[str, Any]] = {}
    for table in model.get("tables") or []:
        name = str(table.get("name") or "")
        if not name or AUTO_DATE_TABLE.match(name):
            continue
        columns = []
        for column in table.get("columns") or []:
            if column.get("type") == "rowNumber" or not column.get("name") or str(column["name"]).startswith("RowNumber-"):
                continue
            duck, kind = KINDS.get(str(column.get("dataType") or "string").lower(), ("VARCHAR", "text"))
            columns.append({"name": str(column["name"]), "type": duck, "kind": kind,
                            "summarizeBy": str(column.get("summarizeBy") or "").lower()})
        measures = {}
        for measure in table.get("measures") or []:
            measures[str(measure.get("name"))] = _measure(_expression(measure.get("expression")), name)
        out[name] = {"name": name, "columns": columns, "measures": measures, "source": _source(table)}
    return out


def _measure(dax: str, home: str) -> dict[str, Any] | None:
    found = MEASURE.match(dax)
    if found:
        return {"table": _table_name(found.group(2)) or home, "column": found.group(3).strip(),
                "agg": DAX_AGG[found.group(1).upper()]}
    found = COUNTROWS.match(dax)
    if found:
        return {"table": _table_name(found.group(1)), "column": "", "agg": "countrows"}
    return None


def _source(table: dict[str, Any]) -> dict[str, str]:
    partition = (table.get("partitions") or [{}])[0]
    source = partition.get("source") or {}
    if source.get("type") == "calculated":
        return {"kind": "DAX table", "language": "", "item": ""}
    text = _expression(source.get("expression"))
    kind, language = "", ""
    for marker, label, lang in SOURCES:
        if marker in text:
            kind, language = label, lang
            break
    item = re.search(r'Item\s*=\s*"([^"]+)"', text) or re.search(r'Name\s*=\s*"([^"]+)"', text)
    schema = re.search(r'Schema\s*=\s*"([^"]+)"', text)
    name = (f"{schema.group(1)}." if schema else "") + item.group(1) if item else ""
    return {"kind": kind or ("Power Query" if text else ""), "language": language, "item": name[:200]}


# ------------------------------------------------------------------ visuals
def _ref(expr: dict[str, Any]) -> str:
    return str(((expr.get("Expression") or {}).get("SourceRef") or {}).get("Entity") or "")


def field(expr: Any, model_tables: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """(field, problem) from one Power BI field expression: {table, column, agg?, bucket?, plain?}."""
    if not isinstance(expr, dict):
        return None, "an unknown field"
    if "Column" in expr:
        col = expr["Column"]
        return {"table": _ref(col), "column": str(col.get("Property") or ""), "plain": True}, ""
    if "Aggregation" in expr:
        inner, problem = field((expr["Aggregation"] or {}).get("Expression"), model_tables)
        agg = AGGREGATION_CODES.get((expr["Aggregation"] or {}).get("Function"))
        if inner is None or not agg:
            return None, problem or "a summary that has no counterpart here"
        return {**inner, "agg": agg, "plain": False}, ""
    if "Measure" in expr:
        table, name = _ref(expr["Measure"]), str(expr["Measure"].get("Property") or "")
        converted = (model_tables.get(table) or {}).get("measures", {}).get(name)
        if converted is None:
            return None, f"the measure [{name}] (its DAX cannot be converted)"
        return {**converted, "label": name[:120], "plain": False}, ""
    if "HierarchyLevel" in expr:
        level = expr["HierarchyLevel"]
        hierarchy = ((level.get("Expression") or {}).get("Hierarchy")) or {}
        inner = hierarchy.get("Expression") or {}
        variation = inner.get("PropertyVariationSource")
        level_name = str(level.get("Level") or "")
        if variation:                                   # the automatic date hierarchy of a date column
            bucket = BUCKET_LEVELS.get(level_name.lower(), "")
            return {"table": _ref(variation), "column": str(variation.get("Property") or ""), "bucket": bucket, "plain": True}, ""
        return {"table": str((inner.get("SourceRef") or {}).get("Entity") or ""), "column": level_name, "plain": True}, ""
    return None, "a field kind that has no counterpart here"


def _default_agg(item: dict[str, Any], model_tables: dict[str, dict[str, Any]]) -> str:
    column = next((c for c in (model_tables.get(item["table"]) or {}).get("columns", []) if c["name"] == item["column"]), None)
    if column and SUMMARIZE_BY.get(column.get("summarizeBy", "")):
        return SUMMARIZE_BY[column["summarizeBy"]]
    return "sum" if column and column["kind"] == "number" else "count"


def convert_visual(visual: dict[str, Any], model_tables: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    """(the visual as Power BI tables and columns, notes) — None when it cannot be shown here."""
    kind = visual["type"]
    if kind in DECORATIVE:
        return None, []
    if kind not in VISUAL_TYPES:
        return None, [f"a {kind or 'custom'} visual has no counterpart here"]
    ours, options, note = VISUAL_TYPES[kind]
    notes = [note] if note else []
    dims: list[dict[str, Any]] = []
    values: list[dict[str, Any]] = []
    roles = visual.get("roles") or {}
    known = CATEGORY_ROLES + VALUE_ROLES + IGNORED_ROLES
    for role in CATEGORY_ROLES + VALUE_ROLES + tuple(r for r in roles if r not in known):
        for expr in roles.get(role) or []:
            item, problem = field(expr, model_tables)
            if item is None:
                notes.append(f"leaves out {problem}")
                continue
            plain = item.pop("plain")
            if role in CATEGORY_ROLES or (plain and ours in ("table", "slicer")):
                dims.append(item)
            else:
                if plain:
                    item["agg"] = _default_agg(item, model_tables)
                values.append(item)
    if ours == "slicer":
        dims, values = dims[:1], []
    if ours == "table" and not values:
        return {"type": "table", "detail": True, "columns": dims[:40], "groupBy": [], "values": [], "options": options}, notes
    if ours in ("pie", "donut"):
        dims, values = dims[:1], values[:1]
    if ours == "kpi":
        dims, values = [], values[:2]
    if ours == "scatter":
        dims, values = dims[:1], values[:2]
    if len(dims) > 3:
        notes.append(f"keeps the first 3 of its {len(dims)} groupings")
        dims = dims[:3]
    if ours in ("column", "bar", "line", "area") and len(dims) > 2:
        dims = dims[:2]
    if len(values) > 8:
        values = values[:8]
    if (ours == "slicer" and not dims) or (ours != "slicer" and not values):
        return None, notes + ["has nothing left to show"]
    return {"type": ours, "detail": False, "columns": [], "groupBy": dims, "values": values, "options": options}, notes


def _place(visual: dict[str, Any], page: dict[str, Any], kind: str) -> dict[str, int]:
    width = page["width"] or 1280
    scale = GRID_WIDTH / width
    w = max(1, min(12, round(visual["width"] / width * 12)))
    x = max(0, min(12 - w, round(visual["x"] / width * 12)))
    h = max(2 if kind in ("kpi", "slicer") else 3, round(visual["height"] * scale / ROW_PX))
    return {"x": x, "y": max(0, round(visual["y"] * scale / ROW_PX)), "w": w, "h": min(40, h)}


def _auto_title(spec: dict[str, Any]) -> str:
    names = [item.get("label") or item.get("column") or "Rows" for item in spec["values"]]
    groups = [item["column"] for item in spec["groupBy"]]
    if spec["type"] == "slicer":
        return groups[0] if groups else ""
    if spec["detail"]:
        return ", ".join(item["column"] for item in spec["columns"][:3])
    return (" and ".join(names[:2]) + (f" by {groups[0]}" if groups else ""))[:120]


# ------------------------------------------------------------------ what the dialog shows
def inspect(template: dict[str, Any], datasets: list[dict[str, Any]], adapters: dict[str, str]) -> dict[str, Any]:
    model_tables = tables(template["model"])
    pages, used_on = [], {}
    for index, page in enumerate(template["pages"]):
        converted, skipped, decorative = 0, [], 0
        for visual in page["visuals"]:
            spec, notes = convert_visual(visual, model_tables)
            if spec is None:
                if not notes:
                    decorative += 1
                else:
                    skipped.append(f"{visual['title'] or visual['type']}: {'; '.join(notes)}")
                continue
            converted += 1
            for item in spec["groupBy"] + spec["values"] + spec["columns"]:
                if item.get("table"):
                    used_on.setdefault(item["table"], set()).add(index)
            changed = [note for note in notes if note]
            if changed:
                skipped.append(f"{visual['title'] or visual['type']}: {'; '.join(changed)}")
        pages.append({"index": index, "name": page["name"], "visuals": converted, "decorative": decorative, "notes": skipped[:40]})
    by_name = {(item.get("name") or "").casefold(): item for item in datasets}
    sql_adapters = [key for key, kind in adapters.items() if kind != "mongodb"]
    mongo_adapters = [key for key, kind in adapters.items() if kind == "mongodb"]
    out_tables = []
    for name, table in model_tables.items():
        if name not in used_on:
            continue
        existing = by_name.get(name.casefold())
        source = table["source"]
        if existing:
            suggestion: dict[str, Any] = {"dataset": existing["id"]}
        else:
            suggestion = suggest_source(name, source, sql_adapters, mongo_adapters)
        out_tables.append({"name": name, "columns": [{"name": c["name"], "kind": c["kind"]} for c in table["columns"]],
                           "measures": [{"name": key, "converted": value is not None} for key, value in table["measures"].items()],
                           "source": source, "pages": sorted(used_on[name]), "suggestion": suggestion})
    relationships = [rel for rel in template["model"].get("relationships") or []
                     if rel.get("fromTable") in model_tables and rel.get("toTable") in model_tables]
    return {"pages": pages, "tables": out_tables, "relationships": len(relationships), "kind": template.get("kind", "pbit")}


def suggest_source(name: str, source: dict[str, str], sql_adapters: list[str], mongo_adapters: list[str]) -> dict[str, Any]:
    item = source.get("item") or name
    if (source.get("language") == "sql" and sql_adapters) or not mongo_adapters:
        if not sql_adapters:
            return {}
        table = ".".join(f'"{part}"' for part in item.split(".")) if not re.fullmatch(r"[\w.]+", item) else item
        return {"connectionId": sql_adapters[0], "queryLanguage": "sql", "collection": "", "querySource": f"SELECT *\nFROM {table}"}
    return {"connectionId": mongo_adapters[0], "queryLanguage": "aggregation-json", "collection": item.split(".")[-1],
            "querySource": '[\n  { "$match": {} }\n]'}


# ------------------------------------------------------------------ the import
def build(template: dict[str, Any], *, report_name: str, page_indexes: list[int], mapping: dict[str, Any]
          ) -> tuple[list[dict[str, Any]], list[str]]:
    """The bundles to install, one per chosen page (the first carries every dataset and the relationships), and notes.

    mapping: {table: {"dataset": id}} for a dataset here, {"connectionId", "queryLanguage", "collection",
    "querySource"} for a new one, or {"skip": true}."""
    model_tables = tables(template["model"])
    if not page_indexes:
        raise ValueError("Choose at least one page")
    for index in page_indexes:
        if not isinstance(index, int) or not 0 <= index < len(template["pages"]):
            raise ValueError(f"There is no page {index}")
    datasets: dict[str, dict[str, Any]] = {}
    column_names: dict[str, dict[str, str]] = {}           # table → {casefolded name: the dataset's column name}
    for number, (table, choice) in enumerate(mapping.items() if isinstance(mapping, dict) else []):
        if table not in model_tables or not isinstance(choice, dict) or choice.get("skip"):
            continue
        if choice.get("dataset"):
            found = store.dataset(str(choice["dataset"]))
            if found is None:
                raise ValueError(f"The dataset chosen for the table '{table}' is not here any more")
            datasets[table] = {**{key: found.get(key) for key in ("id", "name", "description", "refreshEveryMinutes")},
                               **{key: found.get(key) for key in bundle.SOURCE_FIELDS}, "columns": found.get("columns") or []}
            names = [column["name"] for column in found.get("columns") or []]
        else:
            source = {key: choice.get(key) for key in bundle.SOURCE_FIELDS}
            columns = [{key: column[key] for key in ("name", "type", "kind")} for column in model_tables[table]["columns"]]
            datasets[table] = {"id": f"pbi_{number}", "name": str(choice.get("name") or table)[:120],
                               "description": f"From the Power BI table {table}"[:500], **source, "columns": columns}
            names = [column["name"] for column in columns]
        column_names[table] = {name.casefold(): name for name in names}

    notes: list[str] = []
    boards = []
    used: set[str] = set()
    for index in page_indexes:
        page = template["pages"][index]
        tiles = []
        for visual in page["visuals"]:
            spec, _ = convert_visual(visual, model_tables)
            if spec is None:
                continue
            label = visual["title"] or visual["type"]
            fields = spec["groupBy"] + spec["values"] + spec["columns"]
            tables_used = [item["table"] for item in fields if item.get("table")]
            missing_tables = sorted({t for t in tables_used if t not in datasets})
            if missing_tables:
                notes.append(f"{page['name']} / {label}: left out (the table {', '.join(missing_tables)} has no dataset)")
                continue
            home = (spec["values"] or spec["groupBy"] or spec["columns"])[0]["table"]
            ok = True

            def mapped(item: dict[str, Any], *, label: str = label, home: str = home) -> dict[str, Any] | None:
                nonlocal ok
                out = {key: item[key] for key in ("agg", "bucket", "label") if item.get(key)}
                if item.get("agg") == "countrows" and not item.get("column"):
                    out["column"] = ""
                else:
                    real = column_names[item["table"]].get(item["column"].casefold())
                    if real is None:
                        notes.append(f"{page['name']} / {label}: the dataset for {item['table']} has no column '{item['column']}'")
                        ok = False
                        return None
                    out["column"] = real
                if item["table"] != home:
                    out["dataset"] = datasets[item["table"]]["id"]
                return out
            group_by = [m for m in (mapped(item) for item in spec["groupBy"]) if m]
            values = [m for m in (mapped(item) for item in spec["values"]) if m]
            columns = [m for m in (mapped(item) for item in spec["columns"]) if m]
            if not ok and not (values or columns or (spec["type"] == "slicer" and group_by)):
                continue
            used.update(tables_used)
            tile = {"id": f"t{len(tiles) + 1}", "type": spec["type"], "title": visual["title"] or _auto_title(spec),
                    **_place(visual, page, spec["type"]), "dataset": datasets[home]["id"], "groupBy": group_by,
                    "values": values, "filters": [], "detail": spec["detail"], "columns": columns, "options": spec["options"]}
            tiles.append(tile)
        if len(tiles) > store.MAX_TILES:
            notes.append(f"{page['name']}: only the first {store.MAX_TILES} of its {len(tiles)} visuals are kept")
            tiles = tiles[:store.MAX_TILES]
        name = page["name"] if len(page_indexes) == 1 and not report_name else f"{report_name} – {page['name']}" if report_name else page["name"]
        boards.append({"name": name[:120], "description": f"Imported from the Power BI {'report' if template.get('kind') == 'pbix' else 'template'} {report_name or ''}".strip()[:500],
                       "tiles": tiles, "filters": []})
    if not any(board["tiles"] for board in boards):
        raise ValueError("No visual of the chosen pages could be converted: check the datasets chosen for the tables")

    relationships = []
    for rel in template["model"].get("relationships") or []:
        a, b = rel.get("fromTable"), rel.get("toTable")
        if a in datasets and b in datasets and a in used and b in used:
            from_col = column_names[a].get(str(rel.get("fromColumn") or "").casefold())
            to_col = column_names[b].get(str(rel.get("toColumn") or "").casefold())
            if from_col and to_col:
                relationships.append({"fromDataset": datasets[a]["id"], "fromColumn": from_col,
                                      "toDataset": datasets[b]["id"], "toColumn": to_col})
    needed = [datasets[table] for table in datasets if table in used]
    bundles = [{"format": bundle.FORMAT, "version": bundle.VERSION, "dashboard": board,
                "datasets": needed if number == 0 else [], "relationships": relationships if number == 0 else []}
               for number, board in enumerate(boards)]
    return bundles, notes


def install(bundles: list[dict[str, Any]], *, folder: str, actor: str, adapters: dict[str, str]) -> dict[str, Any]:
    """Install the page bundles: the first creates the datasets and relationships, the others use them."""
    for board in bundles:                                   # every dashboard is checked before anything is saved
        store.clean_dashboard({**board["dashboard"], "folder": folder})
    dashboards, created, reused, warnings, new_records, relationships = [], [], [], [], [], 0
    ids: dict[str, str] = {}
    for item in bundles:
        result = bundle.install({**item, "dashboard": bundle._remap(item["dashboard"], ids)}, folder=folder, actor=actor,
                                adapters=adapters)
        ids.update(result["ids"])
        dashboards.append(result["dashboard"])
        created += result["created"]
        reused += [i for i in result["reused"] if i not in reused]
        warnings += result["warnings"]
        new_records += result["newDatasets"]
        relationships += result["relationships"]
    return {"dashboards": dashboards, "created": created, "reused": reused, "relationships": relationships,
            "warnings": warnings, "newDatasets": new_records}
