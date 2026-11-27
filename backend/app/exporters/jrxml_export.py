"""Report definition (+ design) -> a .jrxml (JRXML reports 6.x), the reverse of importers/jrxml.py.

What maps one to one: page and margins, every band (groups included), Static Text, Text Field (pattern, stretch,
markup, paragraph, rotation), Line, Rectangle, Ellipse, Frame, Break, Image (small pictures embedded), parameters
(computed ones as isForPrompting="false"), fields, sort, groups, variables, calculated fields and the query (SQL, or a
MongoDB aggregation as a MongoDbQuery). What JRXML has no match for (charts, barcodes, tables, crosstabs, lists,
subreports, notes, the {"$paramIn": …} filter) is left out and listed in `warnings`, so nothing is lost silently.
"""
from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from typing import Any

from backend.app.rendering.engine import band_parts

NS = "http://jasperreports.sourceforge.net/jasperreports"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
TOKEN = re.compile(r"\$([FPV])\{([^{}]+)\}")
PARAM_CLASS = {"date": "java.util.Date", "string": "java.lang.String", "integer": "java.lang.Integer",
               "number": "java.math.BigDecimal", "boolean": "java.lang.Boolean"}
CALC_TO_JASPER = {"Sum": "Sum", "Count": "Count", "Average": "Average", "Min": "Lowest", "Max": "Highest", "First": "First"}
ALIGN = {"left": "Left", "center": "Center", "right": "Right", "justified": "Justified"}
VALIGN = {"top": "Top", "middle": "Middle", "bottom": "Bottom"}
BAND_ORDER = ("background", "title", "pageHeader", "columnHeader", "detail", "columnFooter", "pageFooter",
              "lastPageFooter", "summary", "noData")
SKIPPED_TYPES = {"chart": "chart", "spider": "spider chart", "barcode": "barcode / QR", "table": "table", "crosstab": "crosstab",
                 "list": "list", "subreport": "subreport", "note": "note"}
MAX_EMBEDDED_BASE64 = 60_000      # a Java string constant holds at most 65 535 bytes
DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|gif);base64,([A-Za-z0-9+/=]+)$")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> str:
    return str(int(round(_num(value, default))))


def java_string(text: str) -> str:
    """A Java string literal for any text."""
    escaped = (str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n").replace("\t", "\\t"))
    return f'"{escaped}"'


def _template_parts(text: str) -> list[tuple[str, str]]:
    """'Pay {=$F{Fee} * 2} to $F{Name}' -> [("text","Pay "), ("formula","$F{Fee} * 2"), ("text"," to "), ("token","$F{Name}")]."""
    parts: list[tuple[str, str]] = []
    source = str(text or "")
    position = 0
    while position < len(source):
        open_at = source.find("{=", position)
        chunk = source[position:] if open_at < 0 else source[position:open_at]
        last = 0
        for match in TOKEN.finditer(chunk):
            if match.start() > last:
                parts.append(("text", chunk[last:match.start()]))
            parts.append(("token", match.group(0)))
            last = match.end()
        if last < len(chunk):
            parts.append(("text", chunk[last:]))
        if open_at < 0:
            break
        depth, index = 0, open_at                          # the "{" of "{=" opens; tokens inside nest
        while index < len(source):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        parts.append(("formula", source[open_at + 2:index].strip()))
        position = index + 1
    return parts


class _Out:
    def __init__(self, calculated: set[str]) -> None:
        self.warnings: list[str] = []
        self.calculated = calculated
        self.needs_page_total = False

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)

    def refs(self, expression: str, where: str) -> str:
        """Our names -> Jasper's: calculated fields are variables; CURRENT_DATE and PAGE_COUNT have no classic variable."""
        def one(match: re.Match) -> str:
            kind, name = match.group(1), match.group(2)
            if kind == "F" and name in self.calculated:
                return "$V{" + name + "}"
            if kind == "V" and name == "CURRENT_DATE":
                return "new java.util.Date()"
            if kind == "V" and name == "PAGE_COUNT":
                self.warn(f"{where}: the page total (PAGE_COUNT) inside a longer expression shows the current page in JRXML; "
                          "use a separate Text Field with Evaluation Time = Report for “of Y”")
                return "$V{PAGE_NUMBER}"
            return match.group(0)
        return TOKEN.sub(one, expression)

    def expression(self, template: str, where: str) -> tuple[str, bool]:
        """Our text template / "=expression" -> (Java expression, evaluate at Report time)."""
        source = str(template or "")
        if source.strip() == "$V{PAGE_COUNT}":
            return "$V{PAGE_NUMBER}", True                      # "of Y": the page number evaluated when the report ends
        if source.lstrip().startswith("="):
            return self.refs(source.lstrip()[1:].strip(), where), False
        pieces = []
        for kind, value in _template_parts(source):
            if kind == "text":
                if value:
                    pieces.append(java_string(value))
            elif kind == "token":
                pieces.append(self.refs(value, where))
            else:
                pieces.append(f"({self.refs(value, where)})")
        if not pieces:
            return '""', False
        if len(pieces) == 1:
            return pieces[0], False
        if not any(p.startswith('"') for p in pieces):
            pieces.insert(0, '""')                               # "" + a + b joins as text, like the designer
        return " + ".join(pieces), False


def _sub(parent: ET.Element, tag: str, attrs: dict[str, str] | None = None, text: str | None = None) -> ET.Element:
    node = ET.SubElement(parent, f"{{{NS}}}{tag}", {k: v for k, v in (attrs or {}).items() if v is not None})
    if text is not None:
        node.text = text
    return node


def _report_element(parent: ET.Element, el: dict[str, Any], out: _Out, where: str, width_limit: float, back: bool = True) -> None:
    x, y = _num(el.get("x")), _num(el.get("y"))
    width, height = max(_num(el.get("width"), 50), 1), max(_num(el.get("height"), 16), 0)
    if x + width > width_limit + 0.5:
        out.warn(f"{where}: an element went past the right margin; it was made narrower to fit the page")
        width = max(1.0, width_limit - x)
    attrs = {"x": _int(max(x, 0)), "y": _int(max(y, 0)), "width": _int(width), "height": _int(height), "uuid": str(uuid.uuid4())}
    if el.get("key"):
        attrs["key"] = str(el["key"])
    if el.get("color") and el.get("type") not in ("line",):
        attrs["forecolor"] = str(el["color"])
    if back and el.get("backColor"):
        attrs.update({"mode": "Opaque", "backcolor": str(el["backColor"])})
    elif el.get("transparent") or back:
        attrs["mode"] = "Transparent" if el.get("transparent") or not el.get("backColor") else None
    for name in ("positionType", "stretchType", "printWhenGroupChanges"):
        if el.get(name):
            attrs[name] = str(el[name])
    if el.get("printRepeatedValues") is False:
        attrs["isPrintRepeatedValues"] = "false"
    for ours, theirs in (("removeLineWhenBlank", "isRemoveLineWhenBlank"), ("printInFirstWholeBand", "isPrintInFirstWholeBand"),
                         ("printWhenDetailOverflows", "isPrintWhenDetailOverflows")):
        if el.get(ours):
            attrs[theirs] = "true"
    node = _sub(parent, "reportElement", attrs)
    if el.get("printWhenExpression"):
        _sub(node, "printWhenExpression", text=out.refs(str(el["printWhenExpression"]), where))


TEXT_PADDING = 2                  # the designer and our PDF keep text 2 px inside its box; the classic default is 0
STUDIO_ADAPTER = "One Empty Record"  # Studio's built-in adapter: Preview shows the layout until a real one is chosen
OUR_ADAPTER = "com.mongoreporting.data.adapter"


def _text_padding(el: dict[str, Any]) -> dict[str, str]:
    """Left/right as in the designer (so letters start where they did); top only while one line still fits, and no
    bottom padding: classic prints nothing at all when the first line is taller than the room left inside the box."""
    pad = max(0.0, _num(el.get("padding"), TEXT_PADDING))
    line = _num(el.get("fontSize"), 10) * 1.3                 # one line of Arial / DejaVu Sans in classic reports
    room = max(_num(el.get("height"), 16) - line, 0.0)
    top = int(min(pad, room))
    return {"leftPadding": _int(pad), "rightPadding": _int(pad), "topPadding": str(top), "bottomPadding": "0"}


def _box(parent: ET.Element, el: dict[str, Any]) -> None:
    width = _num(el.get("borderWidth"))
    padding = el.get("padding")
    text = el.get("type") in ("staticText", "textField")
    if not width and padding is None and not text:
        return
    attrs = _text_padding(el) if text else {"padding": _int(padding) if padding is not None else None}
    box = _sub(parent, "box", attrs)
    if width:
        _sub(box, "pen", {"lineWidth": f"{width:g}", "lineColor": str(el.get("borderColor") or el.get("color") or "#000000")})


def _text_element(parent: ET.Element, el: dict[str, Any]) -> None:
    attrs = {"textAlignment": ALIGN.get(el.get("align") or "left", "Left"), "verticalAlignment": VALIGN.get(el.get("vAlign") or "top", "Top")}
    if el.get("rotation") in ("Left", "Right", "UpsideDown"):
        attrs["rotation"] = el["rotation"]
    if el.get("markup") in ("html", "styled"):
        attrs["markup"] = el["markup"]
    node = _sub(parent, "textElement", attrs)
    font = {"fontName": jasper_font(el.get("fontName")), "size": f"{_num(el.get('fontSize'), 10):g}"}
    for ours, theirs in (("bold", "isBold"), ("italic", "isItalic"), ("underline", "isUnderline")):
        if el.get(ours):
            font[theirs] = "true"
    _sub(node, "font", font)
    paragraph = {}
    if el.get("lineSpacing"):
        paragraph["lineSpacing"] = str(el["lineSpacing"])
    for name in ("lineSpacingSize", "firstLineIndent", "leftIndent", "rightIndent", "spacingBefore", "spacingAfter", "tabStopWidth"):
        if el.get(name) is not None:
            paragraph[name] = f"{_num(el[name]):g}" if name == "lineSpacingSize" else _int(el[name])
    if paragraph:
        _sub(node, "paragraph", paragraph)


def jasper_font(name: Any) -> str:
    """The font our PDF really uses, as a classic logical font: Arial, Calibri, Verdana … are drawn as Helvetica, Georgia
    as Times. classic has those three built in for PDF; a font it does not bundle (Arial, Calibri) loses bold and italic
    in the classic PDF and has other letter widths, so the text would not line up with the design."""
    from backend.app.rendering.engine import FONT_FAMILY  # noqa: PLC0415
    if name and str(name) not in FONT_FAMILY:
        return str(name)                                        # an own font: Studio needs it installed too
    family = FONT_FAMILY[str(name or "SansSerif")]
    return {"Helvetica": "SansSerif", "Times": "Serif", "Courier": "Monospaced"}[family]


def _graphic(parent: ET.Element, el: dict[str, Any], default_width: float = 1) -> None:
    width = _num(el.get("borderWidth"), default_width)
    graphic = _sub(parent, "graphicElement")
    _sub(graphic, "pen", {"lineWidth": f"{width:g}", "lineColor": str(el.get("borderColor") or el.get("color") or "#000000")})


def _hyperlink(node: ET.Element, el: dict[str, Any], out: _Out, where: str) -> None:
    """Hyperlink → hyperlinkType Reference (web / mailto:) or ReportExecution (another report)."""
    link = el.get("hyperlink")
    if not link:
        return
    node.set("hyperlinkTarget", "Blank")
    if link.get("type") == "report":
        node.set("hyperlinkType", "ReportExecution")
    else:
        node.set("hyperlinkType", "Reference")
        template = ("mailto:" if link.get("type") == "email" else "") + str(link.get("target") or "")
        _sub(node, "hyperlinkReferenceExpression", text=out.expression(template, where)[0])
    if link.get("tooltip"):
        _sub(node, "hyperlinkTooltipExpression", text=java_string(str(link["tooltip"])))
    if link.get("type") == "report":
        report = _sub(node, "hyperlinkParameter", {"name": "_report"})
        path = "/".join(part for part in (str(link.get("folder") or "").strip("/"), str(link.get("report") or "")) if part)
        _sub(report, "hyperlinkParameterExpression", text=java_string("/" + path))
        for item in link.get("parameters") or []:
            parameter = _sub(node, "hyperlinkParameter", {"name": str(item.get("name") or "")})
            _sub(parameter, "hyperlinkParameterExpression", text=out.expression(str(item.get("expression") or ""), where)[0])


def _element(parent: ET.Element, el: dict[str, Any], out: _Out, where: str, width_limit: float) -> None:
    kind = el.get("type")
    if kind in SKIPPED_TYPES:
        out.warn(f"{where}: the {SKIPPED_TYPES[kind]} has no JRXML match and was left out; add it again in the other designer")
        return
    if kind == "staticText" and (TOKEN.search(str(el.get("text") or "")) or el.get("hyperlink")):  # classic static text has no link
        kind = "textField"                                 # text with values in it needs an expression in classic reports
        el = {**el, "expression": el.get("text")}
    if kind == "staticText":
        node = _sub(parent, "staticText")
        _report_element(node, el, out, where, width_limit)
        _box(node, el)
        _text_element(node, el)
        _sub(node, "text", text=str(el.get("text") or ""))
    elif kind == "textField":
        expression, at_report = out.expression(str(el.get("expression") or ""), where)
        # like the designer: an empty value prints nothing (classic would print "null")
        attrs = {"isBlankWhenNull": "false" if el.get("blankWhenNull") is False else "true", "pattern": str(el["pattern"]) if el.get("pattern") else None,
                 "evaluationTime": "Report" if at_report else (str(el["evaluationTime"]) if el.get("evaluationTime") else None)}
        if el.get("textAdjust") in ("StretchHeight", "ScaleFont") or el.get("stretch"):
            attrs["textAdjust"] = el.get("textAdjust") if el.get("textAdjust") in ("StretchHeight", "ScaleFont") else "StretchHeight"
        node = _sub(parent, "textField", attrs)
        _report_element(node, el, out, where, width_limit)
        _box(node, el)
        _text_element(node, el)
        _sub(node, "textFieldExpression", text=expression)
        if el.get("patternExpression"):
            _sub(node, "patternExpression", text=out.refs(str(el["patternExpression"]), where))
        _hyperlink(node, el, out, where)
    elif kind == "line":
        node = _sub(parent, "line")
        _report_element(node, {**el, "height": max(_num(el.get("height")), 0)}, out, where, width_limit, back=False)
        _graphic(node, el)
    elif kind in ("rectangle", "ellipse"):
        attrs = {"radius": _int(el["radius"])} if kind == "rectangle" and el.get("radius") else {}
        node = _sub(parent, kind, attrs)
        _report_element(node, el, out, where, width_limit)
        _graphic(node, el, 1 if _num(el.get("borderWidth")) else 0)
    elif kind == "frame":
        node = _sub(parent, "frame")
        _report_element(node, el, out, where, width_limit)
        _box(node, el)
    elif kind == "break":
        node = _sub(parent, "break", {"type": "Column" if el.get("breakType") == "Column" else None})
        _report_element(node, {**el, "height": 1, "width": max(_num(el.get("width"), 50), 1)}, out, where, width_limit, back=False)
    elif kind == "image":
        node = _sub(parent, "image", {"scaleImage": "RetainShape", "hAlign": "Center", "vAlign": "Middle"})
        _report_element(node, el, out, where, width_limit, back=False)
        _box(node, el)
        source = str(el.get("src") or "")
        found = DATA_URL.match(source)
        if el.get("expression"):
            expression, _late = out.expression(el["expression"], where)
        elif found and len(found.group(2)) <= MAX_EMBEDDED_BASE64:
            expression = ("new java.io.ByteArrayInputStream(java.util.Base64.getDecoder().decode("
                          + java_string(found.group(2)) + "))")
        else:
            name = str(el.get("srcName") or el.get("reference") or "image.png").strip('"')
            expression = java_string(name)
            out.warn(f"{where}: the picture '{name}' is too big to put inside the .jrxml; save it next to the .jrxml with that name")
        _sub(node, "imageExpression", text=expression)
        _hyperlink(node, el, out, where)
    else:
        out.warn(f"{where}: an element of type '{kind}' was left out")


def _parts(band: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The section's bands that exist (Detail 1, Detail 2, …), in print order."""
    return [part for part in band_parts(band) if _num(part.get("height")) > 0 or part.get("elements")]


def _stacked(band: dict[str, Any], out: _Out, where: str) -> dict[str, Any]:
    """classic has one band in this section: ours are put below one another in it, each band's own Print When
    Expression on its elements."""
    parts = _parts(band)
    if len(parts) < 2:
        return parts[0] if parts else band
    out.warn(f"{where}: JRXML has one band here; the {len(parts)} bands were joined into one")
    top, elements = 0.0, []
    for part in parts:
        condition = part.get("printWhenExpression") or ""
        for el in part.get("elements") or []:
            own = el.get("printWhenExpression") or ""
            both = f"({condition}) && ({own})" if condition and own else condition or own
            elements.append({**el, "y": _num(el.get("y")) + top, "printWhenExpression": both})
        top += _num(part.get("height"))
    return {"height": top, "elements": elements}


def _band(parent: ET.Element, band: dict[str, Any] | None, out: _Out, where: str, width_limit: float) -> None:
    band = band or {}
    elements = [el for el in band.get("elements") or [] if isinstance(el, dict)]
    height = _num(band.get("height"))
    bottom = max((_num(el.get("y")) + _num(el.get("height")) for el in elements), default=0)
    if bottom > height + 0.5:
        height = bottom                                     # classic refuses elements that reach outside their band
    node = _sub(parent, "band", {"height": _int(height), "splitType": "Stretch"})
    if band.get("printWhenExpression"):
        expression, _late = out.expression("=" + band["printWhenExpression"], f"{where} band print when")
        _sub(node, "printWhenExpression", text=expression)
    for el in elements:
        _element(node, el, out, where, width_limit)


def _has_band(band: dict[str, Any] | None) -> bool:
    return bool(_parts(band))


def _mongo_query(definition: dict[str, Any], out: _Out, root: ET.Element) -> str:
    from backend.app.queries import mongo_query as core
    source = str(definition.get("querySource") or "").strip()
    language = definition.get("queryLanguage") or "mongosh"
    if not source:
        return ""
    if language == "mongosh":
        text = core._strip_query_comments(source)
        start = text.find("[")
        if start < 0:
            out.warn("The MongoDB query has no aggregation pipeline ([...]); it was left out")
            return ""
        literal, _end = core._extract_literal_array(text, start)
    else:
        literal = source
    if "$paramIn" in literal:
        out.warn('The query uses {"$paramIn": …} (a comma-separated filter); JRXML has no such operator. '
                 "Change it in Studio, e.g. with $P!{…} and a computed parameter")
    collection = str(definition.get("collection") or "")
    if "$P{" in collection:
        # like importers/jrxml.py reads it back: a computed parameter holds the collection name
        pieces = [java_string(p) if not TOKEN.fullmatch(p) else p for p in re.split(r"(\$P\{\w+\})", collection) if p]
        param = _sub(root, "parameter", {"name": "CollectionName", "class": "java.lang.String", "isForPrompting": "false"})
        _sub(param, "defaultValueExpression", text=" + ".join(pieces))
        name = "$P{CollectionName}"
    else:
        name = "'" + collection.replace("'", "\\'") + "'"
    return "{\n  'collectionName' : " + name + ",\n  'aggregate' : " + literal + "\n}"


def export_jrxml(definition: dict[str, Any]) -> dict[str, Any]:
    """Return {"xml": text, "fileName": "ID.jrxml", "warnings": [...]} for a report definition with its design."""
    design = definition.get("design") or {}
    page = design.get("page") or {}
    margins = page.get("margins") or {}
    width, height = _num(page.get("width"), 595), _num(page.get("height"), 842)
    left, right = _num(margins.get("left"), 30), _num(margins.get("right"), 30)
    column_width = max(width - left - right, 1)
    bands = design.get("bands") or {}
    calculated = [c for c in design.get("calculatedFields") or [] if isinstance(c, dict) and c.get("name")]
    out = _Out({c["name"] for c in calculated})
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(definition.get("id") or definition.get("title") or "Report")) or "Report"

    ET.register_namespace("", NS)
    ET.register_namespace("xsi", XSI)
    # an empty No Data band would make classic print a blank page when the query has no rows
    no_data = any(part.get("elements") for part in band_parts(bands.get("noData")))
    root = ET.Element(f"{{{NS}}}jasperReport", {
        f"{{{XSI}}}schemaLocation": f"{NS} http://jasperreports.sourceforge.net/xsd/jasperreport.xsd",
        "name": name, "pageWidth": _int(width), "pageHeight": _int(height),
        "orientation": "Landscape" if width > height else "Portrait",
        "whenNoDataType": "NoDataSection" if no_data else "AllSectionsNoDetail",
        "columnWidth": _int(column_width), "leftMargin": _int(left), "rightMargin": _int(right),
        "topMargin": _int(margins.get("top"), 30), "bottomMargin": _int(margins.get("bottom"), 30), "uuid": str(uuid.uuid4()),
    })
    if page.get("ignorePagination"):
        root.set("isIgnorePagination", "true")
    # Studio has no adapter called like ours: naming it would stop Preview ("No MongoDB connection", a blank page).
    # Start on Studio's "One Empty Record" (the layout shows); our data source name is kept for the way back.
    for prop, value in (("com.jaspersoft.studio.data.defaultdataadapter", STUDIO_ADAPTER),
                        (OUR_ADAPTER, definition.get("connectionId")),
                        ("com.jaspersoft.studio.report.description", definition.get("description")),
                        ("com.jaspersoft.studio.unit.", "pixel")):
        if value:
            _sub(root, "property", {"name": prop, "value": str(value)})

    # parameters (the collection-name one, if any, is added by _mongo_query right after these)
    for param in definition.get("parameters") or []:
        kind = str(param.get("type") or "string")
        attrs = {"name": str(param.get("name")), "class": PARAM_CLASS.get(kind, "java.lang.String")}
        prompt = param.get("prompt", True) is not False
        if not prompt:
            attrs["isForPrompting"] = "false"
        node = _sub(root, "parameter", attrs)
        label = str(param.get("label") or "")
        description = str(param.get("description") or "")
        if (label and label != param.get("name")) or description:
            _sub(node, "parameterDescription", text=" — ".join(p for p in (label, description) if p))
        if not prompt and param.get("expression"):
            _sub(node, "defaultValueExpression", text=str(param["expression"]))
        elif param.get("default") not in (None, ""):
            value = param["default"]
            if kind == "string":
                _sub(node, "defaultValueExpression", text=java_string(value))
            elif kind == "boolean":
                _sub(node, "defaultValueExpression", text="Boolean.TRUE" if value in (True, "true", "1") else "Boolean.FALSE")
            elif kind == "integer":
                _sub(node, "defaultValueExpression", text=f"Integer.valueOf({int(_num(value))})")
            elif kind == "number":
                _sub(node, "defaultValueExpression", text=f'new java.math.BigDecimal("{_num(value):g}")')
            else:
                out.warn(f"Parameter {param.get('name')}: its default date was not exported; set it in Studio if needed")
        if param.get("allowedValues"):
            out.warn(f"Parameter {param.get('name')}: the list of allowed values was not exported (JRXML has none)")

    language = definition.get("queryLanguage") or "mongosh"
    if language == "sql":
        query, query_language = str(definition.get("querySource") or ""), "SQL"
    elif language in ("mongosh", "aggregation-json"):
        query, query_language = _mongo_query(definition, out, root), "MongoDbQuery"
    else:
        query, query_language = "", ""
        out.warn(f"The query language '{language}' has no JRXML match; the query was left out")
    if query:
        _sub(root, "queryString", {"language": query_language}, text=query)

    calc_names = {c["name"] for c in calculated}
    for column in definition.get("columns") or []:
        column_name = column.get("name") if isinstance(column, dict) else column
        if column_name and column_name not in calc_names:
            _sub(root, "field", {"name": str(column_name), "class": "java.lang.Object"})
    if query_language == "MongoDbQuery":
        out.warn("In the other designer choose a MongoDB data adapter (Preview → data adapter) to see real rows; "
                 "until then Preview uses “One Empty Record” and shows the layout with empty values")
    if definition.get("columns"):
        out.warn("Fields are exported as java.lang.Object; set each field's class in Studio (e.g. java.math.BigDecimal) "
                 "where a number or date pattern must format it")
    for sort in design.get("sort") or []:
        if sort.get("field"):
            _sub(root, "sortField", {"name": str(sort["field"]), "order": "Descending" if sort.get("order") == "Descending" else None})

    for calc in calculated:
        node = _sub(root, "variable", {"name": calc["name"], "class": "java.lang.Object", "calculation": "Nothing"})
        expression, _late = out.expression("=" + str(calc.get("expression") or "null").lstrip("="), f"calculated field {calc['name']}")
        _sub(node, "variableExpression", text=expression)
    for var in design.get("variables") or []:
        calc = CALC_TO_JASPER.get(var.get("calculation") or "", "")
        if not calc or not var.get("name"):
            out.warn(f"Variable {var.get('name')}: its calculation was not exported")
            continue
        field = "$F{" + str(var.get("field")) + "}" if var.get("field") else "Integer.valueOf(1)"
        if calc in ("Sum", "Average"):
            klass, expression = "java.lang.Double", f"{field} == null ? null : Double.valueOf(String.valueOf({field}))"
        elif calc == "Count":
            klass, expression = "java.lang.Integer", field
        elif calc in ("Lowest", "Highest"):
            klass, expression = "java.lang.Comparable", f"(java.lang.Comparable) {field}"
        else:
            klass, expression = "java.lang.Object", field
        attrs = {"name": str(var["name"]), "class": klass, "calculation": calc}
        if var.get("resetType") == "Group" and var.get("resetGroup"):
            attrs.update({"resetType": "Group", "resetGroup": str(var["resetGroup"])})
        node = _sub(root, "variable", attrs)
        _sub(node, "variableExpression", text=out.refs(expression, f"variable {var['name']}"))

    for group in design.get("groups") or []:
        gname = str(group.get("name") or "")
        if not gname or not group.get("field"):
            continue
        node = _sub(root, "group", {"name": gname})
        _sub(node, "groupExpression", text="$F{" + str(group["field"]) + "}")
        for holder, pool in (("groupHeader", "groupHeaders"), ("groupFooter", "groupFooters")):
            band = (bands.get(pool) or {}).get(gname)
            if _has_band(band):
                section = _sub(node, holder)
                for part in _parts(band):
                    _band(section, part, out, f"{gname} {holder}", column_width)

    for key in BAND_ORDER:
        band = bands.get(key)
        if not _has_band(band) or (key == "noData" and not no_data):
            continue
        holder = _sub(root, key)
        for number, part in enumerate(_parts(band) if key == "detail" else [_stacked(band, out, key)], start=1):
            _band(holder, part, out, key if number == 1 else f"{key} {number}", column_width)

    for skipped in ("functions", "datasets"):
        if design.get(skipped):
            out.warn(f"The design's {skipped} were not exported (JRXML keeps them differently)")

    ET.indent(root, space="\t")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<!-- Exported from the report designer (JRXML 6.x) -->\n' + ET.tostring(root, encoding="unicode") + "\n"
    return {"xml": xml, "fileName": f"{name}.jrxml", "warnings": out.warnings}
