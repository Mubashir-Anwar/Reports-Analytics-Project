"""Import a Jaspersoft Studio .jrxml into this platform's draft report + band design.

Nothing here executes Java or Mongo. Expressions are *translated* by pattern into the
safe `$F{}` / `$P{}` / `$V{}` template language; anything unrecognised is dropped with a
warning so the person can fix it in the designer.
"""
from __future__ import annotations

import jexpr
import json
import re
import xml.etree.ElementTree as ET
from typing import Any

import designer_render

MAX_JRXML_BYTES = 3_000_000
TOKEN = re.compile(r"\$([FPV])\{([^{}]+)\}")
JAVA_TYPES = {
    "java.util.Date": "date", "java.sql.Date": "date", "java.sql.Timestamp": "date",
    "java.lang.Integer": "integer", "java.lang.Long": "integer", "java.lang.Short": "integer",
    "java.lang.Double": "number", "java.lang.Float": "number", "java.math.BigDecimal": "number",
    "java.lang.Boolean": "boolean",
}
CALC_MAP = {"Sum": "Sum", "Count": "Count", "Average": "Average", "Lowest": "Min", "Highest": "Max", "First": "First"}


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _kids(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node if _local(child.tag) == name]


def _kid(node: ET.Element, name: str) -> ET.Element | None:
    found = _kids(node, name)
    return found[0] if found else None


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_true(value: Any) -> bool:
    return str(value).lower() == "true"


# ------------------------------------------------------------------ expressions
def _split_plus(expression: str) -> list[str]:
    parts, current, depth, quote = [], [], 0, ""
    for char in expression:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char in "({":
            depth += 1
        elif char in ")}":
            depth -= 1
        elif char == "+" and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return parts


def translate_expression(expression: str, aliases: dict[str, tuple[str, str]], warnings: list[str], where: str) -> tuple[str, str]:
    """Return (template_expression, pattern). Unsupported Java becomes '' plus a warning."""
    source = (expression or "").strip()
    if not source:
        return "", ""

    def alias_token(match: re.Match) -> str:
        kind, name = match.group(1), match.group(2)
        if kind == "P" and name in aliases and aliases[name][0] in ("start", "end"):
            return "$P{" + aliases[name][1] + "}"
        return match.group(0)

    def clean(text: str) -> str:
        return TOKEN.sub(alias_token, text)

    pattern_call = re.fullmatch(
        r'new\s+java\.text\.(?:SimpleDateFormat|DecimalFormat)\(\s*"([^"]*)"\s*\)\.format\(\s*(\$[FPV]\{[^{}]+\})\s*\)', source)
    if pattern_call:
        return clean(pattern_call.group(2)), pattern_call.group(1)
    if TOKEN.fullmatch(source):
        return clean(source), ""
    literal = re.fullmatch(r'"((?:[^"\\]|\\.)*)"', source)
    if literal:
        return literal.group(1).replace('\\"', '"'), ""
    wrapped = re.fullmatch(r"String\.valueOf\((.+)\)", source) or re.fullmatch(r"(\$[FPV]\{[^{}]+\})\.toString\(\)", source)
    if wrapped:
        return translate_expression(wrapped.group(1), aliases, warnings, where)
    parts = _split_plus(source)
    if len(parts) > 1:
        out, has_literal, ok = [], False, True
        for part in parts:
            quoted = re.fullmatch(r'"((?:[^"\\]|\\.)*)"', part)
            if quoted:
                has_literal = True
                out.append(quoted.group(1).replace('\\"', '"'))
            elif TOKEN.fullmatch(part):
                out.append(clean(part))
            elif re.fullmatch(r"-?\d+(?:\.\d+)?", part):
                out.append(part)
            else:
                ok = False
                break
        if ok and has_literal:
            return "".join(out), ""
        if ok:
            return "=" + " + ".join(out), ""
    java_text = clean(source)
    if jexpr.check_syntax(java_text):
        return "=" + java_text, ""
    tokens = TOKEN.findall(source)
    warnings.append(f"{where}: the Java expression could not be translated; write it again in the designer: {source[:90]}")
    if tokens:
        kind, name = tokens[0]
        return clean(f"${kind}{{{name}}}"), ""
    return "", ""


# ------------------------------------------------------------------ styles
def _pen(node: ET.Element | None) -> tuple[float, str | None]:
    if node is None:
        return 0.0, None
    box_pen = _kid(node, "pen")
    candidates = [box_pen] + [_kid(node, n) for n in ("topPen", "leftPen", "bottomPen", "rightPen")]
    for pen in candidates:
        if pen is not None and _num(pen.get("lineWidth"), 0) > 0:
            return _num(pen.get("lineWidth")), pen.get("lineColor")
    return 0.0, None


def _collect_styles(root: ET.Element) -> dict[str, dict[str, Any]]:
    styles: dict[str, dict[str, Any]] = {}
    for style in _kids(root, "style"):
        width, color = _pen(_kid(style, "box"))
        styles[style.get("name", "")] = {
            "mode": style.get("mode", ""), "backColor": style.get("backcolor"), "color": style.get("forecolor"),
            "borderWidth": width, "borderColor": color, "fontSize": style.get("fontSize"),
            "bold": style.get("isBold"), "italic": style.get("isItalic"), "underline": style.get("isUnderline"),
            "fontName": style.get("fontName"), "markup": style.get("markup"),
            "hAlign": style.get("hTextAlign") or style.get("textAlignment"),
            "vAlign": style.get("vTextAlign") or style.get("verticalAlignment"),
        }
    return styles


class _Ctx:
    def __init__(self, styles, aliases, warnings):
        self.styles, self.aliases, self.warnings, self.counter = styles, aliases, warnings, 0

    def new_id(self) -> str:
        self.counter += 1
        return f"imp-{self.counter}"


_ALIGN = {"Left": "left", "Center": "center", "Right": "right", "Justified": "justified"}
_MARKUP = {"html": "html", "styled": "styled", "none": "none", "": "none"}
# Jasper font names that the designer offers directly; anything else falls back to SansSerif with a warning.
_FONT_ALIASES = {"DejaVu Sans": "SansSerif", "DejaVu Serif": "Serif", "DejaVu Sans Mono": "Monospaced", "Times-Roman": "Times New Roman",
                 "Courier": "Courier New", "Liberation Sans": "Arial", "Liberation Serif": "Times New Roman"}
_FONT_NAMES = {"SansSerif", "Arial", "Helvetica", "Verdana", "Tahoma", "Calibri", "Segoe UI", "Serif", "Times New Roman", "Georgia",
               "Monospaced", "Courier New"}


def _font_name(value: str | None, ctx: "_Ctx", band_name: str) -> str:
    if not value:
        return "SansSerif"
    name = _FONT_ALIASES.get(value, value)
    if name not in _FONT_NAMES:
        ctx.warnings.append(f"{band_name}: font '{value}' is not available; SansSerif is used instead")
        return "SansSerif"
    return name
_VALIGN = {"Top": "top", "Middle": "middle", "Bottom": "bottom"}


def _convert_element(node: ET.Element, ctx: _Ctx, band_name: str, dx: float = 0, dy: float = 0, cell_style: str = "") -> dict[str, Any] | None:
    kind = _local(node.tag)
    if kind not in ("staticText", "textField", "line", "rectangle", "image"):
        if kind not in ("reportElement", "box", "textElement", "graphicElement", "property"):
            ctx.warnings.append(f"{band_name}: the '{kind}' element is not supported yet (skipped)")
        return None
    rep = _kid(node, "reportElement")
    if rep is None:
        return None
    style = ctx.styles.get(rep.get("style", ""), {})
    cell = ctx.styles.get(cell_style, {})
    text_el = _kid(node, "textElement")
    font = _kid(text_el, "font") if text_el is not None else None
    width, border_color = _pen(_kid(node, "box"))
    if not width:
        width, border_color = (style.get("borderWidth") or cell.get("borderWidth") or 0), (style.get("borderColor") or cell.get("borderColor"))
    mode = rep.get("mode") or style.get("mode") or cell.get("mode")
    back = rep.get("backcolor") or style.get("backColor") or cell.get("backColor")
    element: dict[str, Any] = {
        "id": ctx.new_id(), "type": kind,
        "x": _num(rep.get("x")) + dx, "y": _num(rep.get("y")) + dy,
        "width": max(_num(rep.get("width"), 50), 1), "height": max(_num(rep.get("height"), 16), 1),
        "fontSize": _num((font.get("size") if font is not None else None) or style.get("fontSize") or 10, 10),
        "bold": _is_true((font.get("isBold") if font is not None else None) or style.get("bold") or "false"),
        "italic": _is_true((font.get("isItalic") if font is not None else None) or style.get("italic") or "false"),
        "underline": _is_true((font.get("isUnderline") if font is not None else None) or style.get("underline") or "false"),
        "fontName": _font_name((font.get("fontName") if font is not None else None) or style.get("fontName"), ctx, band_name),
        "markup": _MARKUP.get(((text_el.get("markup") if text_el is not None else None) or node.get("markup") or style.get("markup") or "").lower(), "none"),
        "align": _ALIGN.get((text_el.get("textAlignment") or text_el.get("hTextAlign")) if text_el is not None else "", _ALIGN.get(style.get("hAlign") or "", "left")),
        "vAlign": _VALIGN.get((text_el.get("verticalAlignment") or text_el.get("vTextAlign")) if text_el is not None else "", _VALIGN.get(style.get("vAlign") or "", "top")),
        "color": rep.get("forecolor") or style.get("color") or "#000000",
        "backColor": back if (mode == "Opaque" and back) else None,
        "borderWidth": width, "borderColor": border_color or None,
    }
    if kind == "staticText":
        element["text"] = _text(_kid(node, "text"))
    elif kind == "textField":
        expr, pattern = translate_expression(_text(_kid(node, "textFieldExpression")), ctx.aliases, ctx.warnings, band_name)
        element.update({"expression": expr, "pattern": node.get("pattern") or pattern, "blankWhenNull": _is_true(node.get("isBlankWhenNull", "false"))})
        if node.get("textAdjust") == "StretchHeight":
            ctx.warnings.append(f"{band_name}: textAdjust=StretchHeight is not supported yet; the height stays fixed (long text may be cut off)")
    elif kind == "line":
        element.update({"borderColor": element["borderColor"] or element["color"], "borderWidth": width or 1, "height": max(_num(rep.get("height"), 0), 0.01)})
    elif kind == "image":
        element["src"] = ""
        ctx.warnings.append(f"{band_name}: the image resource was not imported; upload the image again in the designer")
    return element


def _convert_band(band: ET.Element | None, name: str, ctx: _Ctx, table_out: dict[str, Any]) -> dict[str, Any]:
    result = {"height": _num(band.get("height")) if band is not None else 0, "elements": []}
    if band is None:
        return result
    for child in band:
        tag = _local(child.tag)
        if tag == "componentElement":
            table = next((c for c in child.iter() if _local(c.tag) == "table"), None)
            rep = _kid(child, "reportElement")
            if table is None or rep is None:
                ctx.warnings.append(f"{name}: the componentElement is not a table (skipped)")
                continue
            if table_out:
                ctx.warnings.append(f"{name}: more than one table; only the first table was imported")
                continue
            table_out.update({"table": table, "x": _num(rep.get("x")), "y": _num(rep.get("y")), "band": name})
            result["height"] = max(0.0, _num(rep.get("y")))  # rows after the table are generated from columnHeader/detail bands
            if _num(band.get("height")) > _num(rep.get("y")) + _num(rep.get("height")) + 1:
                ctx.warnings.append(f"{name}: the part below the table was not imported")
            continue
        element = _convert_element(child, ctx, name)
        if element is not None:
            result["elements"].append(element)
    return result


def _convert_table(info: dict[str, Any], ctx: _Ctx) -> dict[str, dict[str, Any]]:
    """jr:table -> columnHeader + detail bands (cells become elements positioned by column offset)."""
    table = info["table"]
    header = {"height": 0.0, "elements": []}
    detail = {"height": 0.0, "elements": []}
    offset = info["x"]
    for column in table:
        tag = _local(column.tag)
        if tag in ("tableHeader", "tableFooter"):
            ctx.warnings.append(f"table: {tag} was not imported")
            continue
        if tag == "columnGroup":
            ctx.warnings.append("table: columnGroup (merged header) was not imported")
            continue
        if tag != "column":
            continue
        width = _num(column.get("width"))
        for cell_tag, target, name in (("columnHeader", header, "table columnHeader"), ("detailCell", detail, "table detail")):
            cell = _kid(column, cell_tag)
            if cell is None:
                continue
            target["height"] = max(target["height"], _num(cell.get("height")))
            for child in cell:
                element = _convert_element(child, ctx, name, dx=offset, cell_style=cell.get("style", ""))
                if element is not None:
                    target["elements"].append(element)
        for extra in ("columnFooter", "groupHeader", "groupFooter", "tableHeader", "tableFooter"):
            if _kid(column, extra) is not None:
                ctx.warnings.append(f"table column: {extra} was not imported")
        offset += width
    return {"columnHeader": header, "detail": detail}


# ------------------------------------------------------------------ main entry
def _collection_template(expr: str) -> str:
    """'"Edith-"+$P{ProductCode}+"-PaymentPlans"' -> 'Edith-$P{ProductCode}-PaymentPlans' (else '')."""
    parts = _split_plus(expr.strip())
    out = []
    for part in parts:
        quoted = re.fullmatch(r'"([A-Za-z0-9_.\-]*)"', part.strip())
        token = re.fullmatch(r"\$P\{(\w+)\}", part.strip())
        if quoted:
            out.append(quoted.group(1))
        elif token:
            out.append(part.strip())
        else:
            return ""
    return "".join(out) if any("$P{" in p for p in out) or out else ""


def _balanced_object(text: str, start: int) -> str:
    """Return the balanced {...} (or a bare number) starting at `start`; '' if none."""
    number = re.match(r"\s*(\d+)", text[start:])
    if number and text[start:].lstrip()[:1].isdigit():
        return number.group(1)
    begin = text.find("{", start)
    if begin < 0 or text[start:begin].strip():
        return ""
    depth, quote, escaped = 0, None, False
    for index in range(begin, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return re.sub(r",(\s*[}\]])", r"\1", text[begin:index + 1])
    return ""


def _extract_query(query_text: str) -> tuple[str, str]:
    """Return (collection, pipeline_literal) from a MongoDbQuery runCommand or db.x.aggregate([...])."""
    import reporting_core as core
    text = core._strip_query_comments(query_text).strip()
    collection = ""
    match = re.search(r'"aggregate"\s*:\s*(?:"([^"]+)"|(\$P\{\w+\}))', text)
    if match and '"pipeline"' in text:
        collection = match.group(1) or match.group(2)
        start = text.index("[", text.index('"pipeline"'))
    elif re.search(r"""['"]?findQuery['"]?\s*:""", text) or re.search(r"""['"]?collectionName['"]?\s*:""", text):
        # JasperReports Mongo find style: { collectionName: 'x', findQuery: {...}, sort/limit/fields optional }
        name = re.search(r"""['"]?collectionName['"]?\s*:\s*['"]([^'"]+)['"]""", text)
        collection = name.group(1) if name else ""
        stages = []
        for key, stage in (("findQuery", "$match"), ("sort", "$sort"), ("limit", "$limit"), ("fields", "$project"), ("projection", "$project")):
            found = re.search(r"""['"]?""" + key + r"""['"]?\s*:\s*""", text)
            if not found:
                continue
            body = _balanced_object(text, found.end())
            if body:
                stages.append("{ " + stage + ": " + body + " }")
        if not stages:
            raise ValueError("findQuery/collectionName was found but the query body is missing")
        return collection, "[" + ", ".join(stages) + "]"
    else:
        shell = re.search(r'db\.getCollection\(\s*["\']([^"\']+)["\']\s*\)|db\.([A-Za-z_]\w*)\.aggregate', text)
        if shell:
            collection = shell.group(1) or shell.group(2)
        start = text.find("[")
        if start < 0:
            raise ValueError("The query has no aggregation pipeline ([...]). Only MongoDbQuery with an aggregate pipeline is supported for now")
    literal, _end = core._extract_literal_array(text, start)
    return collection, literal


def import_jrxml(xml_text: str) -> dict[str, Any]:
    import reporting_core as core

    if len(xml_text.encode("utf-8")) > MAX_JRXML_BYTES:
        raise ValueError("The JRXML is larger than 3 MB")
    if re.search(r"<!DOCTYPE|<!ENTITY", xml_text, re.IGNORECASE):
        raise ValueError("DOCTYPE/ENTITY is not allowed in a JRXML (security)")
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as exc:
        raise ValueError(f"The JRXML is not valid XML: {exc}") from None
    if _local(root.tag) != "jasperReport":
        raise ValueError("This does not look like a JasperReports .jrxml file (the root must be <jasperReport>)")

    warnings: list[str] = []
    styles = _collect_styles(root)

    # --- parameters: prompting ones become questions; derived ones become aliases
    aliases: dict[str, tuple[str, str]] = {}
    collection_templates: dict[str, str] = {}
    user_params: list[dict[str, Any]] = []
    for param in _kids(root, "parameter"):
        name = param.get("name", "")
        if name in {"REPORT_CONNECTION", "REPORT_PARAMETERS_MAP"} or name.startswith("REPORT_") or name.startswith("JASPER_"):
            continue
        expr = _text(_kid(param, "defaultValueExpression"))
        if param.get("isForPrompting", "true").lower() == "false":
            greg = re.search(r"GregorianCalendar\(\s*\$P\{(\w+)\}\.getYear\(\)\s*\+\s*1900\s*,\s*\$P\{\1\}\.getMonth\(\)\s*,\s*\$P\{\1\}\.getDate\(\)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", expr)
            member = re.search(r"\$P!\{(\w+)\}", expr)
            if greg and tuple(greg.group(i) for i in (2, 3, 4)) == ("0", "0", "0"):
                aliases[name] = ("start", greg.group(1))
            elif greg and tuple(greg.group(i) for i in (2, 3, 4)) == ("23", "59", "59"):
                aliases[name] = ("end", greg.group(1))
            elif member and "$in" in expr:
                aliases[name] = ("in", member.group(1))
            elif _collection_template(expr):
                collection_templates[name] = _collection_template(expr)
            elif expr and jexpr.check_syntax(expr) and all(kind == "P" for kind, _ in TOKEN.findall(expr)):
                # Keep it as a computed parameter: the service evaluates the same Java-style expression.
                user_params.append({"name": name, "type": JAVA_TYPES.get(param.get("class", ""), "string"), "label": name,
                                    "required": False, "prompt": False, "expression": expr})
            else:
                warnings.append(f"Parameter {name}: the default expression could not be translated (it stays empty if the query uses it)")
            continue
        user_params.append({"name": name, "type": JAVA_TYPES.get(param.get("class", ""), "string"), "label": name, "required": False})
    date_bases = {base for kind, base in aliases.values() if kind in ("start", "end")}
    filter_bases = {base for kind, base in aliases.values() if kind == "in"}
    for item in user_params:
        if item["name"] in date_bases:
            item["required"] = True
        if item["name"] in filter_bases:
            item["maxLength"] = 2000
            item["description"] = "Comma-separated values (empty = all)"

    # --- dataset: the table's subDataset query if present, else the main query
    table_node = next((c for c in root.iter() if _local(c.tag) == "table"), None)
    dataset = root
    if table_node is not None:
        run = next((c for c in table_node.iter() if _local(c.tag) == "datasetRun"), None)
        sub_name = run.get("subDataset") if run is not None else None
        sub = next((d for d in _kids(root, "subDataset") if d.get("name") == sub_name), None)
        if sub is not None and _kid(sub, "queryString") is not None:
            dataset = sub
            # sub-dataset parameters can carry the same derived expressions
            for param in _kids(sub, "parameter"):
                name = param.get("name", "")
                if name not in aliases and param.get("isForPrompting", "true").lower() == "false":
                    expr = _text(_kid(param, "defaultValueExpression"))
                    member = re.search(r"\$P!\{(\w+)\}", expr)
                    greg = re.search(r"GregorianCalendar\(\s*\$P\{(\w+)\}\.getYear\(\)[^)]*?,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", expr, re.DOTALL)
                    if greg:
                        aliases[name] = ("start" if greg.group(2) == "0" else "end", greg.group(1))
                    elif member and "$in" in expr:
                        aliases[name] = ("in", member.group(1))
    query_node = _kid(dataset, "queryString")
    if query_node is None:
        raise ValueError("The JRXML has no <queryString>; only MongoDB aggregation reports can be imported for now")
    if query_node.get("language", "MongoDbQuery") not in ("MongoDbQuery", "mongodb", ""):
        warnings.append(f"The query language '{query_node.get('language')}' is not MongoDB; the import may fail")
    collection, literal = _extract_query(_text(query_node))
    placeholder = re.fullmatch(r"\$P\{(\w+)\}", collection)
    if placeholder:
        collection = collection_templates.get(placeholder.group(1), "")
        if not collection:
            warnings.append("The collection name comes from a parameter that could not be translated; fill it in on the Setup tab")
    literal = re.sub(
        r"\$P!?\{([A-Za-z_]\w*)\}",
        lambda m: ("$P{" + aliases[m.group(1)][1] + "}" if aliases[m.group(1)][0] in ("start", "end")
                   else '{"$paramIn": "' + aliases[m.group(1)][1] + '"}') if m.group(1) in aliases else m.group(0),
        literal,
    )
    pipeline = core.parse_query_source("mongosh", literal)
    param_names = {p["name"] for p in user_params}
    core._validate_pipeline_parameter_names(pipeline, param_names)
    core._validate_read_only_pipeline(pipeline)
    columns = core._infer_pipeline_columns(pipeline)
    columns_from_design = not columns
    if not collection:
        warnings.append("The collection name was not found in the query; fill it in on the Setup tab")
    for name in re.findall(r"\$P\{([A-Za-z_]\w*)\}", collection):
        warnings.append(f"The collection name is built from the parameter {name}; give {name} its allowedValues (a list of allowed values) before saving")

    # --- design
    ctx = _Ctx(styles, aliases, warnings)
    table_info: dict[str, Any] = {}
    bands: dict[str, Any] = {}
    for key in ("title", "pageHeader", "columnHeader", "detail", "columnFooter", "pageFooter", "lastPageFooter",
                "summary", "noData", "background"):
        holder = _kid(root, key)
        band = _kid(holder, "band") if holder is not None else None
        bands[key] = _convert_band(band, key, ctx, table_info)
    if table_info:
        generated = _convert_table(table_info, ctx)
        for key in ("columnHeader", "detail"):
            if bands[key]["elements"]:
                warnings.append(f"The {key} band already had elements; the table elements were added to them")
            bands[key]["elements"].extend(generated[key]["elements"])
            bands[key]["height"] = max(bands[key]["height"], generated[key]["height"])
    if (bands["noData"]["height"] or bands["noData"]["elements"]) and root.get("whenNoDataType") not in (None, "NoDataSection"):
        warnings.append("The No Data band is printed whenever the report has no rows (whenNoDataType is treated as NoDataSection)")

    groups: list[dict[str, str]] = []
    group_headers: dict[str, Any] = {}
    group_footers: dict[str, Any] = {}
    for group in _kids(root, "group"):
        gname = group.get("name", "")
        expr, _pattern = translate_expression(_text(_kid(group, "groupExpression")), aliases, warnings, f"group {gname}")
        token = TOKEN.fullmatch(expr)
        if not token or token.group(1) != "F":
            warnings.append(f"Group {gname}: only a $F{{field}} group expression is supported; the group was skipped")
            continue
        groups.append({"name": gname, "field": token.group(2)})
        for holder_name, out in (("groupHeader", group_headers), ("groupFooter", group_footers)):
            holder = _kid(group, holder_name)
            out[gname] = _convert_band(_kid(holder, "band") if holder is not None else None, f"{holder_name}:{gname}", ctx, {})
    variables: list[dict[str, Any]] = []
    for var in _kids(root, "variable"):
        vname = var.get("name", "")
        if vname in designer_render.BUILTIN_VARIABLES or vname.endswith("_COUNT") or vname.startswith("PAGE_") or vname.startswith("COLUMN_"):
            continue
        calc = CALC_MAP.get(var.get("calculation", "Nothing"))
        expr, _p = translate_expression(_text(_kid(var, "variableExpression")), aliases, warnings, f"variable {vname}")
        token = TOKEN.fullmatch(expr) if expr else None
        if not calc or (calc != "Count" and not token):
            warnings.append(f"Variable {vname}: the calculation/expression is not supported (skipped)")
            continue
        reset = var.get("resetType", "Report")
        reset_group = var.get("resetGroup", "")
        variables.append({"name": vname, "calculation": calc, "field": token.group(2) if token else "", "resetType": "Group" if reset == "Group" else "Report",
                          "resetGroup": reset_group if reset == "Group" else ""})

    sort = []
    for sf in _kids(dataset, "sortField"):
        sort.append({"field": sf.get("name", ""), "order": "Descending" if sf.get("order") == "Descending" else "Ascending"})

    design = {
        "version": 1,
        "page": {
            "width": _num(root.get("pageWidth"), 595), "height": _num(root.get("pageHeight"), 842),
            "margins": {"top": _num(root.get("topMargin"), 30), "right": _num(root.get("rightMargin"), 30),
                        "bottom": _num(root.get("bottomMargin"), 30), "left": _num(root.get("leftMargin"), 30)},
            "ignorePagination": _is_true(root.get("isIgnorePagination", "false")),
        },
        "calculatedFields": [], "groups": groups, "variables": variables, "sort": sort,
        "bands": {**bands, "groupHeaders": group_headers, "groupFooters": group_footers},
    }

    # fields used by the design but missing from the query output -> add (value will be empty)
    used = set()
    for band_key in list(bands) + ["groupHeaders", "groupFooters"]:
        pool = design["bands"][band_key]
        for band in (pool.values() if band_key.startswith("group") else [pool]):
            for el in band["elements"]:
                for kind, name in TOKEN.findall(el.get("expression", "")):
                    if kind == "F":
                        used.add(name)
    for name in sorted(used - set(columns)):
        columns.append(name)
        if columns_from_design:
            continue
        warnings.append(f"Field '{name}' is not in the query output; it will be empty in the report")
    design["sort"] = [s for s in sort if s["field"] in columns] if sort else []
    if len(design["sort"]) != len(sort):
        warnings.append("Some sortField fields are not in the query output; the sort was skipped")
    design["groups"] = [g for g in design["groups"] if g["field"] in columns]
    design = designer_render.validate_design(design, columns, param_names)

    report_id = re.sub(r"[^A-Za-z0-9_-]", "_", root.get("name", "IMPORTED_REPORT"))[:80] or "IMPORTED_REPORT"
    if not re.match(r"[A-Za-z0-9]", report_id):
        report_id = "R" + report_id
    properties = {p.get("name"): p.get("value") for p in _kids(root, "property")}
    unit = properties.get("ireport.jasperserver.reportUnit", "")
    folder = "General"
    if unit.count("/") >= 2:
        try:
            folder = core.normalize_folder_path(unit.strip("/").rsplit("/", 1)[0])
        except ValueError:
            folder = "General"
    report = {
        "id": report_id, "title": report_id.replace("_", " "), "description": "", "folder": folder,
        "connectionId": "default", "collection": collection,
        "queryLanguage": "mongosh",
        "querySource": 'db.getCollection("' + collection + '").aggregate(' + literal + ")",
        "parameters": user_params, "columns": columns,
        "formats": ["csv", "xlsx", "pdf"], "defaultFormat": "xlsx",
    }
    unique_warnings = list(dict.fromkeys(warnings))  # same message from many cells -> once
    return {
        "report": report, "design": design, "warnings": unique_warnings,
        "hints": {"dataAdapter": properties.get("com.jaspersoft.studio.data.defaultdataadapter", ""), "jasperName": root.get("name", "")},
    }
