"""Band-based (Jasper-style) report design: validation and PDF rendering.

A design is plain JSON data. Nothing in it is ever executed: expressions are
`$F{field}` / `$P{param}` / `$V{variable}` tokens plus a tiny, AST-checked
arithmetic subset. Layout units are PDF points (1 pt = 1 px in the designer).
"""
from __future__ import annotations

import ast
import base64
import bisect
import contextvars
import io
import math
import operator
import os
import re
from datetime import date, datetime
from typing import Any, Iterable

from backend.app.expressions import jexpr

BAND_KEYS = ("title", "pageHeader", "columnHeader", "detail", "columnFooter", "pageFooter", "lastPageFooter",
             "summary", "noData", "background")


def band_present(band: dict[str, Any] | None) -> bool:
    """Like a band that exists in a JRXML: it has a height or elements. Absent bands are not printed."""
    return bool(band and (band.get("height") or band.get("elements")))
ELEMENT_TYPES = {"staticText", "textField", "line", "rectangle", "image", "chart", "ellipse", "frame", "break", "barcode",
                 "table", "crosstab", "list", "subreport", "note"}
# Elements that print many records and make their band grow (Jasper components fed by a dataset).
DATASET_TYPES = {"table", "crosstab", "list", "subreport"}
TABLE_FOOTERS = {"none", "text", "Sum", "Count", "Average", "Min", "Max"}
CROSSTAB_CALCULATIONS = {"Sum", "Count", "Average", "Min", "Max"}
MAX_SUBREPORT_DEPTH = 2
# Sub-datasets (Jasper subDataset): a Table or Crosstab can read its own query instead of the report's records.
DATASET_COMPONENTS = {"table", "crosstab"}
MAX_DATASETS = 10
DATASET_LANGUAGES = {"aggregation-json", "mongosh", "javascript", "python", "sql"}
DATASET_PARAMETER_TYPES = {"string", "integer", "number", "date", "boolean"}
# Jasper "When No Data Type". Auto = the No Data band when there is one, else all sections without Detail.
NO_DATA_TYPES = {"Auto", "NoPages", "BlankPage", "AllSectionsNoDetail", "NoDataSection"}
BARCODE_TYPES = {"qr", "code128", "code39", "ean13"}
LINE_STYLES = {"solid": None, "dashed": (4, 3), "dotted": (1, 2)}
EVALUATION_TIMES = {"Now", "Report", "Auto"}
# Jaspersoft Studio → Appearance / Text Field properties
POSITION_TYPES = {"Float", "FixRelativeToTop", "FixRelativeToBottom"}
STRETCH_TYPES = {"NoStretch", "RelativeToTallestObject", "RelativeToBandHeight", "ContainerHeight", "ContainerBottom"}
TEXT_ADJUSTS = {"CutText", "StretchHeight", "ScaleFont"}
ROTATIONS = {"None", "Left", "Right", "UpsideDown"}
LINE_SPACINGS = {"Single", "1_1_2", "Double", "AtLeast", "Fixed", "Proportional"}
BOX_TYPES = {"rectangle", "ellipse", "frame"}
CALCULATIONS = {"Sum", "Count", "Average", "Min", "Max", "First"}
RESET_TYPES = {"Report", "Group"}
CHART_TYPES = {"bar", "line", "pie", "spider"}
BUILTIN_VARIABLES = {"PAGE_NUMBER", "PAGE_COUNT", "REPORT_COUNT", "CURRENT_DATE"}
MAX_ELEMENTS = 400
MAX_ROWS_RENDER = 50_000
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TOKEN_RE = re.compile(r"\$([FPV])\{([^{}]+)\}")
IMAGE_RE = re.compile(r"^data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)$")

DEFAULT_PAGE = {"width": 595, "height": 842, "margins": {"top": 30, "right": 30, "bottom": 30, "left": 30}}

# Font names as offered in the designer. PDF uses the matching built-in family; Excel keeps the real name.
FONT_FAMILY = {
    "SansSerif": "Helvetica", "Arial": "Helvetica", "Helvetica": "Helvetica", "Verdana": "Helvetica", "Tahoma": "Helvetica",
    "Calibri": "Helvetica", "Segoe UI": "Helvetica", "Serif": "Times", "Times New Roman": "Times", "Georgia": "Times",
    "Monospaced": "Courier", "Courier New": "Courier",
}
FONT_NAMES = set(FONT_FAMILY)
EXCEL_FONT = {"SansSerif": "Arial", "Serif": "Times New Roman", "Monospaced": "Courier New", "Helvetica": "Arial"}
PDF_FAMILIES = {
    "Helvetica": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
    "Times": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "Courier": ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"),
}
# Jasper "markup": none = text as-is; html = a safe subset of HTML; styled = Jasper <style ...> tags (plus the HTML subset).
MARKUPS = {"none", "html", "styled"}
_HTML_FONT_SIZES = {"1": 8, "2": 10, "3": 12, "4": 14, "5": 18, "6": 24, "7": 36}
_CSS_COLOR = re.compile(r"^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|[a-zA-Z]{3,20})$")
_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr", "table"}
_HEADING_SIZE = {"h1": 2.0, "h2": 1.5, "h3": 1.17, "h4": 1.0, "h5": 0.83, "h6": 0.67}


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _css_size(value: str, base: float) -> float | None:
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(px|pt|em|%)?\s*$", value or "")
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2) or "px"
    size = number * base if unit == "em" else number * base / 100 if unit == "%" else number
    return max(4.0, min(size, 96.0))


class _MarkupParser:
    """Turn HTML / Jasper styled text into ReportLab paragraph markup (for PDF) or plain text (for Excel/CSV).

    Only formatting survives: bold, italic, underline, strike, sub/superscript, colour, size, font, line
    breaks, paragraphs and lists. Scripts, links, images, tables' structure and every other tag are dropped
    (their text is kept), so data from MongoDB cannot inject anything into the document.
    """

    def __init__(self, base_size: float, styled: bool, plain: bool):
        from html.parser import HTMLParser

        self.base_size, self.styled, self.plain = base_size, styled, plain
        self.out: list[str] = []
        self.stack: list[tuple[str, list[str]]] = []
        self.lists: list[list[Any]] = []
        self.skip = 0
        parser = HTMLParser(convert_charrefs=True)
        parser.handle_starttag = self.start
        parser.handle_startendtag = lambda tag, attrs: self.start(tag, attrs, void=True)
        parser.handle_endtag = self.end
        parser.handle_data = self.data
        self.parser = parser

    # ---- helpers
    def _break(self) -> None:
        if self.out and self.out[-1] not in ("<br/>", "\n"):
            self.out.append("\n" if self.plain else "<br/>")

    def _font_tag(self, color: str | None = None, size: float | None = None, face: str | None = None) -> str:
        attrs = []
        if color and _CSS_COLOR.fullmatch(color):
            attrs.append(f'color="{color}"')
        if size:
            attrs.append(f'size="{size:g}"')
        if face and face in FONT_FAMILY:
            attrs.append(f'face="{PDF_FAMILIES[FONT_FAMILY[face]][0]}"')
        return f"<font {' '.join(attrs)}>" if attrs else ""

    def _open(self, tag: str, openings: list[str]) -> None:
        closings = []
        for opening in openings:
            if not opening:
                continue
            if not self.plain:
                self.out.append(opening)
            closings.insert(0, "</" + opening[1:].split(" ", 1)[0].rstrip(">") + ">")
        self.stack.append((tag, closings))

    def _style_openings(self, color=None, size=None, face=None, bold=False, italic=False, underline=False, strike=False) -> list[str]:
        openings = [self._font_tag(color, size, face)]
        openings += ["<b>"] if bold else []
        openings += ["<i>"] if italic else []
        openings += ["<u>"] if underline else []
        openings += ["<strike>"] if strike else []
        return openings

    # ---- parser callbacks
    def start(self, tag: str, attrs: list[tuple[str, str | None]], void: bool = False) -> None:
        tag = tag.lower()
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag in ("script", "style") and not (tag == "style" and self.styled):
            if not void:
                self.skip += 1
                self.stack.append((tag, []))
            return
        if tag == "br":
            self.out.append("\n" if self.plain else "<br/>")
            return
        if tag in ("hr",):
            self._break()
            return
        if void:
            return
        openings: list[str] = []
        if tag in ("b", "strong"):
            openings = ["<b>"]
        elif tag in ("i", "em", "cite"):
            openings = ["<i>"]
        elif tag in ("u", "ins"):
            openings = ["<u>"]
        elif tag in ("s", "strike", "del"):
            openings = ["<strike>"]
        elif tag in ("sup", "sub"):
            openings = ["<super>" if tag == "sup" else "<sub>"]
        elif tag in _HEADING_SIZE:
            self._break()
            openings = [self._font_tag(size=max(4.0, min(96.0, self.base_size * _HEADING_SIZE[tag]))), "<b>"]
        elif tag in _BLOCK_TAGS:
            self._break()
            if tag in ("ul", "ol"):
                self.lists.append([tag, 0])
        elif tag == "li":
            self._break()
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                self.data(f"{self.lists[-1][1]}. ")
            else:
                self.data("• ")
        elif tag in ("td", "th"):
            if self.out and self.out[-1] not in ("<br/>", "\n"):
                self.data("  ")
        elif tag == "font":
            size = values.get("size", "")
            points = _HTML_FONT_SIZES.get(size.strip()) if size.strip() in _HTML_FONT_SIZES else _css_size(size, self.base_size)
            openings = [self._font_tag(values.get("color"), points, values.get("face"))]
        elif tag == "span" or tag == "style":
            if tag == "style":
                truthy = lambda name: values.get(name.lower(), "").lower() == "true"  # noqa: E731
                openings = self._style_openings(
                    values.get("forecolor"), _css_size(values.get("size", ""), self.base_size) if values.get("size") else None,
                    values.get("fontname"), truthy("isBold"), truthy("isItalic"), truthy("isUnderline"), truthy("isStrikeThrough"),
                )
            else:
                css = {}
                for rule in values.get("style", "").split(";"):
                    if ":" in rule:
                        key, value = rule.split(":", 1)
                        css[key.strip().lower()] = value.strip()
                face = css.get("font-family", "").split(",")[0].strip().strip("'\"")
                decoration = css.get("text-decoration", "")
                openings = self._style_openings(
                    css.get("color"), _css_size(css.get("font-size", ""), self.base_size) if css.get("font-size") else None, face,
                    css.get("font-weight", "") in ("bold", "bolder", "600", "700", "800", "900"), css.get("font-style") == "italic",
                    "underline" in decoration, "line-through" in decoration,
                )
        self._open(tag, openings)

    def end(self, tag: str) -> None:
        tag = tag.lower()
        if not any(item[0] == tag for item in self.stack):
            return
        while self.stack:
            name, closings = self.stack.pop()
            if name in ("script", "style") and (name != "style" or not self.styled):
                self.skip = max(0, self.skip - 1)
            if not self.plain:
                self.out.extend(closings)
            if name in ("ul", "ol") and self.lists:
                self.lists.pop()
            if name in _BLOCK_TAGS or name in _HEADING_SIZE:
                self._break()
            if name == tag:
                break

    def data(self, text: str) -> None:
        if self.skip:
            return
        if not self.plain:
            text = re.sub(r"[\r\n\t ]+", " ", text)
            self.out.append(_xml_escape(text))
        else:
            self.out.append(re.sub(r"[\r\n\t ]+", " ", text))

    def convert(self, source: str) -> str:
        self.parser.feed(source)
        self.parser.close()
        while self.stack:
            _name, closings = self.stack.pop()
            if not self.plain:
                self.out.extend(closings)
        result = "".join(self.out)
        if self.plain:
            lines = [line.strip() for line in result.split("\n")]
            return "\n".join(lines).strip("\n")
        return re.sub(r"^(<br/>)+|(<br/>)+$", "", result)


def markup_to_paragraph(text: Any, markup: str, base_size: float = 10) -> str:
    """ReportLab paragraph markup for a text in the given markup mode (plain text is escaped)."""
    source = "" if text is None else str(text)
    if markup not in ("html", "styled"):
        return "<br/>".join(_xml_escape(line) for line in source.split("\n"))
    return _MarkupParser(base_size, markup == "styled", plain=False).convert(source)


def markup_to_text(text: Any, markup: str) -> str:
    """Plain text of an HTML/styled value, for Excel and CSV (tags removed, entities decoded, line breaks kept)."""
    source = "" if text is None else str(text)
    if markup not in ("html", "styled"):
        return source
    return _MarkupParser(10, markup == "styled", plain=True).convert(source)


# ----------------------------------------------------------------- validation
def _num(value: Any, name: str, low: float, high: float, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(number) or number < low or number > high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return number


def _color(value: Any, name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not COLOR_RE.fullmatch(value):
        raise ValueError(f"{name} must be in #RRGGBB format")
    return value


def tokens_in(expression: str) -> list[tuple[str, str]]:
    return TOKEN_RE.findall(expression or "")


def validate_design(design: Any, columns: list[str], parameter_names: set[str]) -> dict[str, Any]:
    """Return a normalized copy of `design` or raise ValueError (English message)."""
    if not isinstance(design, dict):
        raise ValueError("design must be a JSON object")
    raw_page = design.get("page") or {}
    margins = raw_page.get("margins") or {}
    page = {
        "width": _num(raw_page.get("width"), "page.width", 100, 14400, 595),
        "height": _num(raw_page.get("height"), "page.height", 100, 14400, 842),
        "margins": {side: _num(margins.get(side), f"margins.{side}", 0, 300, 30)
                    for side in ("top", "right", "bottom", "left")},
        "ignorePagination": raw_page.get("ignorePagination") is True,
        # Excel/CSV: "grid" = the whole page laid out in cells like Jasper's XLSX exporter (headings, images,
        # every band); "table" = one header row and one row per record (best for data analysis).
        "sheetLayout": raw_page.get("sheetLayout") if raw_page.get("sheetLayout") in ("grid", "mixed") else "table",
        # designer only: vertical guide lines (x in px from the left margin), like Jaspersoft Studio's ruler guides
        # Report properties (Jaspersoft Studio: Report → Properties)
        "whenNoDataType": raw_page.get("whenNoDataType") if raw_page.get("whenNoDataType") in NO_DATA_TYPES else "Auto",
        "titleNewPage": raw_page.get("titleNewPage") is True,
        "summaryNewPage": raw_page.get("summaryNewPage") is True,
        "floatColumnFooter": raw_page.get("floatColumnFooter") is True,
        "guides": sorted({round(float(value), 1) for value in (raw_page.get("guides") if isinstance(raw_page.get("guides"), list) else [])[:50]
                          if isinstance(value, (int, float)) and not isinstance(value, bool)
                          and math.isfinite(value) and -300 <= value <= 15000}),
    }

    field_names = set(columns)
    calculated: list[dict[str, str]] = []
    for item in design.get("calculatedFields", []) or []:
        name = str(item.get("name", "")) if isinstance(item, dict) else ""
        if not NAME_RE.fullmatch(name) or name in field_names:
            raise ValueError(f"A calculated field needs a valid, unique name: {name!r}")
        calculated.append({"name": name, "expression": str(item.get("expression", ""))[:500]})
        field_names.add(name)

    groups: list[dict[str, str]] = []
    for item in design.get("groups", []) or []:
        name = str(item.get("name", "")) if isinstance(item, dict) else ""
        field = str(item.get("field", "")) if isinstance(item, dict) else ""
        if not NAME_RE.fullmatch(name) or any(g["name"] == name for g in groups):
            raise ValueError(f"A group needs a valid, unique name: {name!r}")
        if field not in field_names:
            raise ValueError(f"The field of group '{name}' does not exist: {field!r}")
        groups.append({"name": name, "field": field})
    if len(groups) > 5:
        raise ValueError("At most 5 groups are allowed")

    variables: list[dict[str, Any]] = []
    for item in design.get("variables", []) or []:
        if not isinstance(item, dict):
            raise ValueError("Each variable must be a JSON object")
        name = str(item.get("name", ""))
        if not NAME_RE.fullmatch(name) or name in BUILTIN_VARIABLES or any(v["name"] == name for v in variables):
            raise ValueError(f"A variable needs a valid, unique name: {name!r}")
        calculation = str(item.get("calculation", "Sum"))
        if calculation not in CALCULATIONS:
            raise ValueError(f"The calculation of variable '{name}' must be one of {', '.join(sorted(CALCULATIONS))}")
        field = str(item.get("field", ""))
        if calculation != "Count" and field not in field_names:
            raise ValueError(f"The field of variable '{name}' does not exist: {field!r}")
        reset = str(item.get("resetType", "Report"))
        if reset not in RESET_TYPES:
            raise ValueError(f"The resetType of variable '{name}' must be Report or Group")
        reset_group = str(item.get("resetGroup", ""))
        if reset == "Group" and not any(g["name"] == reset_group for g in groups):
            raise ValueError(f"The resetGroup of variable '{name}' is not an existing group")
        variables.append({"name": name, "calculation": calculation, "field": field,
                          "resetType": reset, "resetGroup": reset_group if reset == "Group" else ""})
    variable_names = {v["name"] for v in variables} | BUILTIN_VARIABLES
    sort_spec: list[dict[str, str]] = []
    for item in design.get("sort", []) or []:
        if not isinstance(item, dict) or str(item.get("field", "")) not in field_names:
            raise ValueError("The sort field does not exist")
        sort_spec.append({"field": str(item["field"]), "order": "Descending" if item.get("order") == "Descending" else "Ascending"})

    datasets = _clean_datasets(design.get("datasets"))
    datasets_by_name = {d["name"]: d for d in datasets}

    counter = {"elements": 0, "image_bytes": 0}

    def check_expression(expression: str, where: str, fields: set[str] | None = None) -> None:
        for kind, name in tokens_in(expression):
            known = {"F": field_names if fields is None else fields, "P": parameter_names, "V": variable_names}[kind]
            if name not in known:
                raise ValueError(f"{where}: ${kind}{{{name}}} is not defined")

    def clean_element(raw: Any, band_name: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError(f"{band_name}: each element must be a JSON object")
        counter["elements"] += 1
        if counter["elements"] > MAX_ELEMENTS:
            raise ValueError(f"At most {MAX_ELEMENTS} elements are allowed")
        kind = str(raw.get("type", ""))
        if kind not in ELEMENT_TYPES:
            raise ValueError(f"{band_name}: invalid element type: {kind!r}")
        element: dict[str, Any] = {
            "id": re.sub(r"[^A-Za-z0-9_-]", "", str(raw.get("id", "")))[:80] or f"el-{counter['elements']}",
            "type": kind,
            "x": _num(raw.get("x"), "x", -2000, 15000, 0),
            "y": _num(raw.get("y"), "y", -2000, 15000, 0),
            "width": _num(raw.get("width"), "width", 1, 15000, 100),
            "height": _num(raw.get("height"), "height", 0, 5000, 20),
            "fontSize": _num(raw.get("fontSize"), "fontSize", 4, 96, 10),
            "fontName": raw.get("fontName") if raw.get("fontName") in FONT_NAMES else "SansSerif",
            "bold": raw.get("bold") is True,
            "italic": raw.get("italic") is True,
            "underline": raw.get("underline") is True,
            "markup": raw.get("markup") if raw.get("markup") in MARKUPS else "none",
            "align": raw.get("align") if raw.get("align") in ("left", "center", "right", "justified") else "left",
            "vAlign": raw.get("vAlign") if raw.get("vAlign") in ("top", "middle", "bottom") else "top",
            "color": _color(raw.get("color"), "color") or "#000000",
            "backColor": _color(raw.get("backColor"), "backColor"),
            "borderColor": _color(raw.get("borderColor"), "borderColor"),
            "borderWidth": _num(raw.get("borderWidth"), "borderWidth", 0, 20, 0),
            # Jasper "Stretch With Overflow" (textAdjust=StretchHeight): long text makes the element and its band taller
            "stretch": raw.get("stretch") is True and kind in ("staticText", "textField"),
            # designer only: a locked element cannot be moved or resized on the canvas
            "locked": raw.get("locked") is True,
        }
        if kind != "line" and element["height"] < 1:
            element["height"] = 1.0
        # Appearance / Advanced (Jaspersoft Studio property tabs)
        element["padding"] = _num(raw.get("padding"), "padding", 0, 50, 2)
        element["lineStyle"] = raw.get("lineStyle") if raw.get("lineStyle") in LINE_STYLES else "solid"
        raw_borders = raw.get("borders")
        if isinstance(raw_borders, dict) and any(raw_borders.get(side) for side in ("top", "right", "bottom", "left")):
            element["borders"] = {side: _num(raw_borders.get(side), f"borders.{side}", 0, 20, 0) for side in ("top", "right", "bottom", "left")}
        else:
            element["borders"] = None
        print_when = str(raw.get("printWhenExpression", "") or "").strip()[:500]
        if print_when:
            if not jexpr.check_syntax(print_when):
                raise ValueError(f"{band_name}: the Print When expression could not be understood: {print_when[:60]}")
            check_expression(print_when, f"{band_name} Print When")
        element["printWhenExpression"] = print_when
        # Appearance: Position Type / Stretch Type ("" = the earlier automatic behaviour: float below a growing
        # text, grow with a text on the same line), Transparent, Label / Key, print details (Jasper names)
        element["positionType"] = raw.get("positionType") if raw.get("positionType") in POSITION_TYPES else ""
        element["stretchType"] = raw.get("stretchType") if raw.get("stretchType") in STRETCH_TYPES else ""
        element["transparent"] = raw.get("transparent") is True
        element["label"] = re.sub(r"[\x00-\x1f]", "", str(raw.get("label") or ""))[:120]
        element["key"] = re.sub(r"[\x00-\x1f]", "", str(raw.get("key") or ""))[:120]
        element["printRepeatedValues"] = raw.get("printRepeatedValues", True) is not False
        element["removeLineWhenBlank"] = raw.get("removeLineWhenBlank") is True
        element["printInFirstWholeBand"] = raw.get("printInFirstWholeBand") is True
        element["printWhenDetailOverflows"] = raw.get("printWhenDetailOverflows") is True
        element["printWhenGroupChanges"] = str(raw.get("printWhenGroupChanges") or "")[:80]
        if kind in ("staticText", "textField"):
            # Text Field / Static Text: Text Adjust, Rotation and Paragraph
            adjust = raw.get("textAdjust")
            if adjust not in TEXT_ADJUSTS:
                adjust = "StretchHeight" if element["stretch"] else "CutText"
            element["textAdjust"] = adjust
            element["stretch"] = adjust == "StretchHeight"
            element["rotation"] = raw.get("rotation") if raw.get("rotation") in ROTATIONS else "None"
            element["lineSpacing"] = raw.get("lineSpacing") if raw.get("lineSpacing") in LINE_SPACINGS else "Single"
            element["lineSpacingSize"] = _num(raw.get("lineSpacingSize"), "lineSpacingSize", 0, 500, 1)
            for name in ("firstLineIndent", "leftIndent", "rightIndent", "spacingBefore", "spacingAfter"):
                element[name] = _num(raw.get(name), name, -500 if name == "firstLineIndent" else 0, 500, 0)
            element["tabStopWidth"] = _num(raw.get("tabStopWidth"), "tabStopWidth", 1, 500, 40)
        if kind == "staticText":
            element["text"] = str(raw.get("text", ""))[:2000]
        elif kind == "textField":
            element["expression"] = str(raw.get("expression", ""))[:1000]
            element["pattern"] = str(raw.get("pattern", ""))[:60]
            # Pattern Expression: a pattern that depends on the record, e.g. $F{Currency} == "USD" ? "$ #,##0.00" : "Rs #,##0"
            pattern_expression = str(raw.get("patternExpression", "") or "").strip()[:500]
            if pattern_expression:
                if not jexpr.check_syntax(pattern_expression):
                    raise ValueError(f"{band_name}: the Pattern Expression could not be understood: {pattern_expression[:60]}")
                check_expression(pattern_expression, f"{band_name} Pattern Expression")
            element["patternExpression"] = pattern_expression
            element["blankWhenNull"] = raw.get("blankWhenNull", True) is True
            # Jasper Evaluation Time: Now = while filling; Report/Auto = variables take their final (report-end) value
            element["evaluationTime"] = raw.get("evaluationTime") if raw.get("evaluationTime") in EVALUATION_TIMES else "Now"
            check_expression(element["expression"], f"{band_name} textField")
        elif kind == "barcode":
            element["barcodeType"] = raw.get("barcodeType") if raw.get("barcodeType") in BARCODE_TYPES else "qr"
            element["expression"] = str(raw.get("expression", ""))[:1000]
            element["showText"] = raw.get("showText", True) is not False
            element["evaluationTime"] = "Now"
            check_expression(element["expression"], f"{band_name} barcode")
        elif kind == "image":
            src = str(raw.get("src", ""))
            if src:
                match = IMAGE_RE.fullmatch(src)
                if not match:
                    raise ValueError(f"{band_name}: an image must be a PNG/JPEG data URL")
                try:
                    size = len(base64.b64decode(match.group(2), validate=True))
                except ValueError:
                    raise ValueError(f"{band_name}: invalid image data") from None
                counter["image_bytes"] += size
                if size > 2_500_000 or counter["image_bytes"] > 8_000_000:
                    raise ValueError("Keep the images under 8 MB in total (up to 2.5 MB each)")
            element["src"] = src
            # where the picture came from, shown on the server's resource page (the bytes are in src)
            element["srcName"] = re.sub(r"[\x00-\x1f]", "", str(raw.get("srcName") or ""))[:200]
            url = str(raw.get("srcUrl") or "").strip()
            element["srcUrl"] = url[:500] if re.match(r"^https?://", url, re.I) else ""
            element["reference"] = re.sub(r"[\x00-\x1f]", "", str(raw.get("reference") or ""))[:300]
        elif kind == "chart":
            chart_type = str(raw.get("chartType", "bar"))
            if chart_type not in CHART_TYPES:
                raise ValueError(f"{band_name}: chartType must be bar, line, pie or spider")
            category, value = str(raw.get("categoryField", "")), str(raw.get("valueField", ""))
            aggregate = str(raw.get("aggregate", "Sum"))
            if category not in field_names or (aggregate != "Count" and value not in field_names):
                raise ValueError(f"{band_name}: the chart categoryField/valueField does not exist")
            if aggregate not in {"Sum", "Count", "Average"}:
                raise ValueError(f"{band_name}: the chart aggregate must be Sum, Count or Average")
            element.update({"chartType": chart_type, "categoryField": category, "valueField": value,
                            "aggregate": aggregate, "title": str(raw.get("title", ""))[:120]})
        elif kind == "line":
            element["height"] = _num(raw.get("height"), "height", 0, 5000, 0) or 0.01
        elif kind == "note":
            element["text"] = str(raw.get("text", ""))[:2000]
        elif kind in DATASET_TYPES:
            where = f"{band_name} {kind}"
            own = field_names  # the fields this component's expressions may use
            dataset_name = str(raw.get("datasetName", "") or "") if kind in DATASET_COMPONENTS else ""
            element["datasetName"] = dataset_name
            element["datasetParameters"] = []
            if dataset_name:
                dataset = datasets_by_name.get(dataset_name)
                if dataset is None:
                    raise ValueError(f"{where}: the dataset {dataset_name!r} does not exist (Dataset and Query -> Datasets)")
                own = set(dataset["fields"])
                declared = {p["name"] for p in dataset["parameters"]}
                for item in (raw.get("datasetParameters") or [])[:30]:
                    if not isinstance(item, dict) or str(item.get("name", "")) not in declared:
                        raise ValueError(f"{where}: each dataset parameter must be one of the dataset's parameters")
                    expression = str(item.get("expression", "") or "")[:500]
                    check_expression(expression, f"{where} dataset parameter {item['name']}")  # main report scope
                    element["datasetParameters"].append({"name": str(item["name"]), "expression": expression})
            element["scope"] = "group" if raw.get("scope") == "group" and not dataset_name else "report"
            element["filterExpression"] = str(raw.get("filterExpression", "") or "").strip()[:500]
            if element["filterExpression"]:
                if not jexpr.check_syntax(element["filterExpression"]):
                    raise ValueError(f"{where}: the filter expression could not be understood")
                check_expression(element["filterExpression"], f"{where} filter", own)
            sort_field = str(raw.get("sortField", "") or "")
            if sort_field and sort_field not in own:
                raise ValueError(f"{where}: the sort field {sort_field} does not exist")
            element["sortField"] = sort_field
            element["sortOrder"] = "Descending" if raw.get("sortOrder") == "Descending" else "Ascending"
            if kind in ("table", "crosstab"):
                element["headerBackColor"] = _color(raw.get("headerBackColor"), "headerBackColor") or None
                element["headerColor"] = _color(raw.get("headerColor"), "headerColor") or "#000000"
                element["gridColor"] = _color(raw.get("gridColor"), "gridColor") or "#94a3b8"
                element["gridWidth"] = _num(raw.get("gridWidth"), "gridWidth", 0, 5, 0.5)
                element["rowHeight"] = _num(raw.get("rowHeight"), "rowHeight", 4, 300, 16)
                element["headerHeight"] = _num(raw.get("headerHeight"), "headerHeight", 0, 300, 18)
                element["repeatHeader"] = raw.get("repeatHeader", True) is not False
            if kind == "table":
                raw_columns = raw.get("columns") or []
                if not isinstance(raw_columns, list) or len(raw_columns) > 40:
                    raise ValueError(f"{where}: give at most 40 columns")
                columns_out = []
                for index, col in enumerate(raw_columns):
                    if not isinstance(col, dict):
                        raise ValueError(f"{where}: each column must be a JSON object")
                    expression = str(col.get("expression", ""))[:1000]
                    check_expression(expression, f"{where} column {index + 1}", own)
                    footer = col.get("footer") if col.get("footer") in TABLE_FOOTERS else "none"
                    columns_out.append({
                        "header": str(col.get("header", ""))[:200],
                        "expression": expression,
                        "width": _num(col.get("width"), f"{where} column width", 5, 5000, 80),
                        "pattern": str(col.get("pattern", "") or "")[:60],
                        "align": col.get("align") if col.get("align") in ("left", "center", "right") else "left",
                        "footer": footer,
                        "footerText": str(col.get("footerText", "") or "")[:200],
                    })
                element["columns"] = columns_out
                element["footerHeight"] = _num(raw.get("footerHeight"), "footerHeight", 0, 300, 18)
                element["zebraColor"] = _color(raw.get("zebraColor"), "zebraColor")
            elif kind == "crosstab":
                row_field, column_field = str(raw.get("rowField", "")), str(raw.get("columnField", ""))
                measure = str(raw.get("measureField", "") or "")
                calculation = raw.get("calculation") if raw.get("calculation") in CROSSTAB_CALCULATIONS else "Sum"
                if row_field not in own or column_field not in own:
                    raise ValueError(f"{where}: choose existing fields for the rows and the columns")
                if calculation != "Count" and measure not in own:
                    raise ValueError(f"{where}: choose an existing measure field (or the Count calculation)")
                element.update({
                    "rowField": row_field, "columnField": column_field, "measureField": measure, "calculation": calculation,
                    "pattern": str(raw.get("pattern", "") or "")[:60],
                    "rowHeaderWidth": _num(raw.get("rowHeaderWidth"), "rowHeaderWidth", 20, 3000, 100),
                    "cellWidth": _num(raw.get("cellWidth"), "cellWidth", 15, 2000, 60),
                    "cornerText": str(raw.get("cornerText", "") or "")[:200],
                    "totalBackColor": _color(raw.get("totalBackColor"), "totalBackColor"),
                    "showRowTotals": raw.get("showRowTotals", True) is not False,
                    "showColumnTotals": raw.get("showColumnTotals", True) is not False,
                })
            elif kind == "subreport":
                report_id = re.sub(r"[^A-Za-z0-9_.-]", "", str(raw.get("reportId", "")))[:120]
                mapped = []
                for item in (raw.get("parameters") or [])[:30]:
                    if not isinstance(item, dict) or not NAME_RE.fullmatch(str(item.get("name", ""))):
                        raise ValueError(f"{where}: each subreport parameter needs a valid name")
                    expression = str(item.get("expression", ""))[:500]
                    check_expression(expression, f"{where} parameter {item['name']}")
                    mapped.append({"name": str(item["name"]), "expression": expression})
                element.update({"reportId": report_id, "parameters": mapped, "showTitle": raw.get("showTitle", True) is not False})
        return element

    def clean_band(raw: Any, name: str) -> dict[str, Any]:
        if raw is None:
            return {"height": 0, "elements": []}
        if not isinstance(raw, dict):
            raise ValueError(f"Band '{name}' must be a JSON object")
        height = _num(raw.get("height"), f"{name}.height", 0, 15000, 0)
        elements = raw.get("elements", []) or []
        if not isinstance(elements, list):
            raise ValueError(f"The elements of band '{name}' must be a list")
        return {"height": height, "elements": [clean_element(item, name) for item in elements]}

    raw_bands = design.get("bands") or {}
    if not isinstance(raw_bands, dict):
        raise ValueError("design.bands must be a JSON object")
    bands: dict[str, Any] = {key: clean_band(raw_bands.get(key), key) for key in BAND_KEYS}
    for side in ("groupHeaders", "groupFooters"):
        mapping = raw_bands.get(side) or {}
        if not isinstance(mapping, dict):
            raise ValueError(f"design.bands.{side} must be a JSON object")
        bands[side] = {g["name"]: clean_band(mapping.get(g["name"]), f"{side}.{g['name']}") for g in groups}

    for item in calculated:
        check_expression(item["expression"], f"calculated field {item['name']}")

    # Dataset filter expression (Jasper: Dataset → Filter Expression): only records for which it is true are printed
    filter_expression = str(design.get("filterExpression", "") or "").strip()[:1000]
    if filter_expression:
        if not jexpr.check_syntax(filter_expression):
            raise ValueError(f"The report filter expression could not be understood: {filter_expression[:60]}")
        check_expression(filter_expression, "Report filter expression")

    return {"version": 1, "page": page, "calculatedFields": calculated, "groups": groups,
            "variables": variables, "sort": sort_spec, "filterExpression": filter_expression, "datasets": datasets,
            "bands": bands}


def _clean_datasets(raw: Any) -> list[dict[str, Any]]:
    """Jasper sub-datasets: a name, an optional own data adapter, a query, its fields and its parameters.
    The query itself is checked (read-only, parameters defined) by definitions.compile_dataset when it runs or is saved."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_DATASETS:
        raise ValueError(f"datasets must be a list of at most {MAX_DATASETS} datasets")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each dataset must be a JSON object")
        name = str(item.get("name", ""))
        if not NAME_RE.fullmatch(name) or any(d["name"] == name for d in out):
            raise ValueError(f"A dataset needs a valid, unique name: {name!r}")
        language = str(item.get("queryLanguage", "mongosh")).lower()
        if language not in DATASET_LANGUAGES:
            raise ValueError(f"Dataset {name}: unknown query language {language!r}")
        connection = str(item.get("connectionId", "") or "").strip()
        if connection and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", connection):
            raise ValueError(f"Dataset {name}: invalid data adapter ID")
        fields = item.get("fields") or []
        if (not isinstance(fields, list) or not fields or len(fields) > 200
                or not all(isinstance(f, str) and 0 < len(f) <= 200 for f in fields)):
            raise ValueError(f"Dataset {name}: read its fields first (at least one field)")
        params: list[dict[str, str]] = []
        for p in (item.get("parameters") or [])[:30]:
            p_name = str(p.get("name", "")) if isinstance(p, dict) else ""
            if not NAME_RE.fullmatch(p_name) or any(x["name"] == p_name for x in params):
                raise ValueError(f"Dataset {name}: each parameter needs a valid, unique name")
            p_type = str(p.get("type", "string")).lower()
            params.append({"name": p_name, "type": p_type if p_type in DATASET_PARAMETER_TYPES else "string"})
        out.append({
            "name": name, "connectionId": connection, "queryLanguage": language,
            "collection": "" if language == "sql" else str(item.get("collection", "") or "").strip()[:200],
            "querySource": str(item.get("querySource", "") or "")[:100_000],
            "fields": list(dict.fromkeys(fields)), "parameters": params,
        })
    return out


# ----------------------------------------------------------------- expressions
_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.Mod: operator.mod}


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def eval_expression(expression: str, resolve) -> Any:
    """Java-subset expression (see jexpr.py). Any failure -> None (cell stays blank)."""
    try:
        return jexpr.evaluate(expression, resolve)
    except jexpr.JExprError:
        return None


def eval_arithmetic(expression: str, resolve) -> float | None:
    """Evaluate +,-,*,/,% over numbers/tokens. Anything else is rejected."""
    values: list[float | None] = []

    def substitute(match: re.Match) -> str:
        values.append(_to_number(resolve(match.group(1), match.group(2))))
        return f"_v{len(values) - 1}"

    source = TOKEN_RE.sub(substitute, expression)
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError:
        return None

    def walk(node: ast.AST) -> float | None:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name) and re.fullmatch(r"_v\d+", node.id):
            return values[int(node.id[2:])]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            inner = walk(node.operand)
            return None if inner is None else (-inner if isinstance(node.op, ast.USub) else inner)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left, right = walk(node.left), walk(node.right)
            if left is None or right is None:
                return None
            try:
                return _BIN_OPS[type(node.op)](left, right)
            except ZeroDivisionError:
                return None
        raise ValueError("An expression may contain only numbers, tokens and + - * / %")

    try:
        return walk(tree)
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _string_width(text: str, font: str, size: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    return stringWidth(text, font, size)


def is_date_pattern(pattern: str) -> bool:
    """SimpleDateFormat (dd/MM/yyyy, MMM-yy, HH:mm) rather than DecimalFormat (#,##0.00, 0.00E0)."""
    bare = re.sub(r"'[^']*'", "", pattern or "")
    return bool(re.search(r"[yMdHhmsEakS]", bare)) and not re.search(r"[#0]", bare)


def format_value(value: Any, pattern: str) -> str:
    if value is None:
        return ""
    if pattern:
        try:
            if is_date_pattern(pattern):
                if isinstance(value, (datetime, date)) or _parse_datetime(value) is not None:
                    return jexpr.DateFormatter(pattern).format(value)
            elif re.search(r"[#0]", pattern) and not isinstance(value, bool) and _to_number(value) is not None:
                return jexpr.DecimalFormatter(pattern).format(value)
        except (jexpr.JExprError, ArithmeticError, ValueError, TypeError):
            # e.g. a date in milliseconds outside datetime's range: fall back to the plain text below
            pass
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}" if abs(value) < 1e15 else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


# ----------------------------------------------------------------- sample data
def sample_rows(columns: list[str], count: int = 24) -> list[dict[str, Any]]:
    rows = []
    for index in range(count):
        row: dict[str, Any] = {}
        for column in columns:
            lowered = column.lower()
            if re.search(r"date|time", lowered):
                row[column] = f"2026-{(index % 12) + 1:02d}-{(index % 27) + 1:02d}T09:30:00+00:00"
            elif re.search(r"amount|total|count|number|size|rate|no_of|qty|price|balance", lowered):
                row[column] = round(1000 + (index * 7919) % 90000 + index / 7, 2)
            else:
                row[column] = f"{column}-{index // 8 + 1}" if index % 2 == 0 else f"{column}-{index % 3 + 1}"
        rows.append(row)
    rows.sort(key=lambda r: tuple(str(v) for v in r.values()))
    return rows


# ----------------------------------------------------------------- rendering
def _font_names() -> tuple[str, str, str, str]:
    """Return (regular, bold, italic, bold-italic); optional TTF via REPORT_PDF_FONT_PATH."""
    path = os.getenv("REPORT_PDF_FONT_PATH", "").strip()
    if path and os.path.exists(path):
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            if "ReportFont" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("ReportFont", path))
                from reportlab.lib.fonts import addMapping
                for bold in (0, 1):
                    for italic in (0, 1):
                        addMapping("ReportFont", bold, italic, "ReportFont")
            return ("ReportFont",) * 4
        except Exception:  # noqa: BLE001 - fall back to built-in fonts
            pass
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"


# Set by the service around a render: loader(report_id, parameters) -> {"design", "title", "columns", "rows", "parameters"}
# for Subreport elements (the subreport runs its own query). Without a loader a subreport prints a placeholder.
SUBREPORT_LOADER: contextvars.ContextVar = contextvars.ContextVar("subreport_loader", default=None)
# Set by the service around a render: loader(dataset, parameter_values) -> list of records, for Tables / Crosstabs
# with their own dataset. Without a loader (designer preview without a data adapter) they show sample records.
DATASET_LOADER: contextvars.ContextVar = contextvars.ContextVar("dataset_loader", default=None)


class DatasetError(Exception):
    """A sub-dataset could not be read; the component prints the message instead of failing the report."""
# Not printed by themselves: break (splits the band), note (designer only), and components that expand into rows.
NOT_DRAWN = {"break", "note"} | DATASET_TYPES


def _cell(kind: str, x: float, y: float, w: float, h: float, base: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """A generated table / crosstab cell with every property the drawing and export code reads."""
    cell = {"id": "", "type": kind, "x": x, "y": y, "width": w, "height": h, "fontSize": base.get("fontSize", 9),
            "fontName": base.get("fontName", "SansSerif"), "bold": False, "italic": False, "underline": False, "markup": "none",
            "align": "left", "vAlign": "middle", "color": base.get("color") or "#000000", "backColor": None, "borderColor": None,
            "borderWidth": 0, "stretch": False, "padding": 2, "lineStyle": "solid", "borders": None, "printWhenExpression": "",
            "locked": False}
    if kind == "textField":
        cell.update({"expression": "", "pattern": "", "blankWhenNull": True, "evaluationTime": "Now"})
    else:
        cell["text"] = ""
    cell.update(extra)
    if cell.get("text") is None:
        cell["text"] = ""
    return cell


def _aggregate(calculation: str, values: list[Any]) -> Any:
    if calculation == "Count":
        return sum(1 for v in values if v is not None and v != "")
    numbers = [n for n in (_to_number(v) for v in values) if n is not None]
    if not numbers:
        return None
    if calculation == "Sum":
        return sum(numbers)
    if calculation == "Average":
        return sum(numbers) / len(numbers)
    return min(numbers) if calculation == "Min" else max(numbers)


def _hashable(value: Any) -> Any:
    return value if isinstance(value, (str, int, float, bool, date, datetime, type(None))) else str(value)


def _sort_key(value: Any) -> tuple:
    """Numbers in numeric order, then dates, then text; empty values last."""
    if value is None or value == "":
        return (3, "")
    if isinstance(value, bool):
        return (2, str(value))
    if isinstance(value, (int, float)):
        return (0, value)
    if isinstance(value, (date, datetime)):
        return (1, value.isoformat())
    return (2, str(value).lower())


def _contained(box: dict[str, Any], band: dict[str, Any]) -> list[dict[str, Any]]:
    """Elements of the band that lie inside `box` (the contents of a List)."""
    return [e for e in band["elements"] if e is not box and e["x"] >= box["x"] - 0.5 and e["y"] >= box["y"] - 0.5
            and e["x"] + e["width"] <= box["x"] + box["width"] + 0.5 and e["y"] + e["height"] <= box["y"] + box["height"] + 0.5]


class _Render:
    def __init__(self, design, title, columns, rows, parameters, total_pages):
        from reportlab.lib.colors import HexColor
        self.design, self.title = design, title
        self.columns, self.parameters = columns, parameters
        self.total_pages = total_pages
        self.hex = HexColor
        self.fonts = _font_names()
        self.page = design["page"]
        self.bands = design["bands"]
        self.groups = design["groups"]
        self.variables = design["variables"]
        self.calculated = design["calculatedFields"]
        self.rows = [self._with_calculated(row) for row in rows]
        if design.get("filterExpression"):
            kept = []
            for row in self.rows:
                def resolve(kind, name, row=row):
                    return row.get(name) if kind == "F" else parameters.get(name) if kind == "P" else None
                try:
                    if jexpr.evaluate(design["filterExpression"], resolve):
                        kept.append(row)
                except jexpr.JExprError:
                    pass  # a record the filter cannot evaluate is left out, like a false filter
            self.rows = kept
        for spec in reversed(design.get("sort", [])):
            present = [r for r in self.rows if r.get(spec["field"]) is not None]
            missing = [r for r in self.rows if r.get(spec["field"]) is None]
            try:
                present.sort(key=lambda r, f=spec["field"]: r[f], reverse=spec["order"] == "Descending")
            except TypeError:
                present.sort(key=lambda r, f=spec["field"]: str(r[f]), reverse=spec["order"] == "Descending")
            self.rows = present + missing
        self.state: dict[str, dict[str, Any]] = {}
        for var in self.variables:
            self.state[var["name"]] = {"sum": 0.0, "count": 0, "min": None, "max": None, "first": None}
        self.page_number = 0
        self.report_count = 0
        self.pages_drawn = 0
        self.content_end = 0.0
        self.today = datetime.now().date()
        self.final: dict[str, Any] = {}  # report-end variable values (Evaluation Time Report/Auto)
        self.depth = 0  # subreport nesting level
        self.early_exit = False  # the report printed only the No Data band or a blank page
        self.x_offset = 0.0  # a subreport's bands are shifted to the subreport element's x
        self.repeat_memo: dict[str, tuple[Any, int]] = {}  # element id -> (last printed value, page) for Print Repeated Values
        self.changed_groups: set[str] = set()  # groups that changed at the current record

    # ---- data helpers
    def _with_calculated(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        for item in self.calculated:
            row[item["name"]] = eval_expression(item["expression"], lambda k, n: row.get(n) if k == "F" else None)
        return row

    def reset_variables(self, group_name: str | None) -> None:
        for var in self.variables:
            if group_name is None and var["resetType"] == "Report" or group_name and var["resetGroup"] == group_name:
                self.state[var["name"]] = {"sum": 0.0, "count": 0, "min": None, "max": None, "first": None}

    def accumulate(self, row: dict[str, Any]) -> None:
        for var in self.variables:
            state = self.state[var["name"]]
            raw = row.get(var["field"]) if var["field"] else None
            number = _to_number(raw)
            if var["calculation"] == "Count":
                counted = not var["field"] or raw is not None
            else:
                counted = number is not None
            state["count"] += 1 if counted else 0
            if number is not None:
                state["sum"] += number
                state["min"] = number if state["min"] is None else min(state["min"], number)
                state["max"] = number if state["max"] is None else max(state["max"], number)
            if state["first"] is None:
                state["first"] = raw

    def variable_value(self, name: str) -> Any:
        if name == "PAGE_NUMBER":
            return self.page_number
        if name == "PAGE_COUNT":
            return self.total_pages or self.page_number
        if name == "REPORT_COUNT":
            return self.report_count
        if name == "CURRENT_DATE":
            return self.today
        var = next((v for v in self.variables if v["name"] == name), None)
        if var is None:
            return None
        state = self.state[name]
        calc = var["calculation"]
        if calc == "Sum":
            return state["sum"]
        if calc == "Count":
            return state["count"]
        if calc == "Average":
            return state["sum"] / state["count"] if state["count"] else None
        return {"Min": state["min"], "Max": state["max"], "First": state["first"]}[calc]

    def resolve(self, kind: str, name: str, row: dict[str, Any] | None) -> Any:
        if kind == "F":
            return (row or {}).get(name)
        if kind == "P":
            return self.parameters.get(name)
        return self.variable_value(name)

    def resolver(self, element: dict[str, Any], row: dict[str, Any] | None):
        """Token resolver for one element. With Evaluation Time Report/Auto, $V{...} gives the report's final value
        (e.g. a percentage of the grand total, or "Page X of Y")."""
        late = element.get("evaluationTime") in ("Report", "Auto") and bool(self.final)

        def resolve(kind: str, name: str) -> Any:
            if late and kind == "V" and name in self.final:
                return self.final[name]
            return self.resolve(kind, name, row)
        return resolve

    def final_values(self) -> dict[str, Any]:
        names = [v["name"] for v in self.variables] + ["REPORT_COUNT", "PAGE_COUNT", "PAGE_NUMBER", "CURRENT_DATE"]
        return {name: self.variable_value(name) for name in names}

    def print_when(self, element: dict[str, Any], row: dict[str, Any] | None) -> bool:
        """Jasper "Print When Expression": the element is printed only when it is true."""
        expression = element.get("printWhenExpression")
        if not expression:
            return True
        try:
            return bool(jexpr.evaluate(expression, self.resolver(element, row)))
        except jexpr.JExprError:
            return True

    def shown(self, element: dict[str, Any], row: dict[str, Any] | None) -> bool:
        """Is the element printed for this record? Print When Expression, then Jasper's "Print Repeated Values"
        (off: a value equal to the one printed before is left out), unless it is the first whole band on a new
        page ("Print In First Whole Band") or its group just changed ("Print When Group Changes")."""
        if not self.print_when(element, row):
            return False
        if element.get("printRepeatedValues", True):
            return True
        memo = self.repeat_memo.get(element["id"])
        if memo is None or memo[0] != self.repeat_key(element, row):
            return True
        if element.get("printInFirstWholeBand") and memo[1] != self.page_number:
            return True
        return bool(element.get("printWhenGroupChanges")) and element["printWhenGroupChanges"] in self.changed_groups

    def repeat_key(self, element: dict[str, Any], row: dict[str, Any] | None) -> Any:
        if element["type"] in ("textField", "barcode"):
            return self.evaluate(element, row)
        return element.get("text", "") if element["type"] == "staticText" else "static"

    def printing(self, element: dict[str, Any], row: dict[str, Any] | None) -> bool:
        """shown(), and remember the printed value for "Print Repeated Values"."""
        if not self.shown(element, row):
            return False
        if not element.get("printRepeatedValues", True):
            self.repeat_memo[element["id"]] = (self.repeat_key(element, row), self.page_number)
        return True

    def pattern_for(self, element: dict[str, Any], resolve) -> str:
        """The Pattern, or what the Pattern Expression gives for this record."""
        pattern = element.get("pattern", "")
        if element.get("patternExpression"):
            try:
                dynamic = jexpr.evaluate(element["patternExpression"], resolve)
            except jexpr.JExprError:
                dynamic = None
            if dynamic not in (None, ""):
                pattern = str(dynamic)[:60]
        return pattern

    def evaluate(self, element: dict[str, Any], row: dict[str, Any] | None) -> str:
        expression = element.get("expression", "")
        found = TOKEN_RE.fullmatch(expression.strip())
        resolve = self.resolver(element, row)
        pattern = self.pattern_for(element, resolve)
        if expression.lstrip().startswith("="):
            value: Any = eval_expression(expression.lstrip()[1:], resolve)
            text = format_value(value, pattern)
        elif found:
            text = format_value(resolve(found.group(1), found.group(2)), pattern)
        else:
            text = TOKEN_RE.sub(lambda m: format_value(resolve(m.group(1), m.group(2)), pattern), expression)
        if text == "" and not element.get("blankWhenNull", True):
            return "null"
        return text

    # ---- drawing
    def family(self, element: dict[str, Any]) -> tuple[str, str, str, str]:
        """Regular/bold/italic/bold-italic PDF fonts for the element's font name (a configured TTF wins)."""
        if self.fonts[0] == "ReportFont":
            return self.fonts
        return PDF_FAMILIES[FONT_FAMILY.get(element.get("fontName", "SansSerif"), "Helvetica")]

    def font(self, element: dict[str, Any]) -> str:
        regular, bold, italic, both = self.family(element)
        return both if element["bold"] and element["italic"] else bold if element["bold"] else italic if element["italic"] else regular

    def element_text(self, element: dict[str, Any], row: dict[str, Any] | None) -> str:
        return element.get("text", "") if element["type"] == "staticText" else self.evaluate(element, row)

    @staticmethod
    def leading(element: dict[str, Any], size: float) -> float:
        """Paragraph → Line Spacing (Jasper): Single, 1½, Double, Proportional (× size), Fixed / At Least (points)."""
        base, amount = size * 1.2, element.get("lineSpacingSize", 1) or 1
        spacing = element.get("lineSpacing", "Single")
        if spacing == "1_1_2":
            return base * 1.5
        if spacing == "Double":
            return base * 2
        if spacing == "Proportional":
            return base * amount
        if spacing == "Fixed":
            return max(amount, 1)
        if spacing == "AtLeast":
            return max(base, amount)
        return base

    def plain_lines(self, element: dict[str, Any], text: str, size: float, width: float) -> list[tuple[str, float, bool, bool]]:
        """Wrapped lines as (text, indent, first line of a paragraph, last line of a paragraph):
        Left / Right Indent narrow the box, First Line Indent moves each paragraph's first line, tabs jump to
        the next Tab Stop."""
        from reportlab.lib.utils import simpleSplit
        name = self.font(element)
        left, right, first = element.get("leftIndent", 0), element.get("rightIndent", 0), element.get("firstLineIndent", 0)
        tab = element.get("tabStopWidth", 40) or 40
        space = max(_string_width(" ", name, size), 0.1)
        room = max(width - left - right, 1)
        lines: list[tuple[str, float, bool, bool]] = []
        for paragraph in str(text).split("\n"):
            if "\t" in paragraph:
                out = ""
                for index, part in enumerate(paragraph.split("\t")):
                    if index:
                        used = _string_width(out, name, size)
                        next_stop = (used // tab + 1) * tab
                        out += " " * max(1, round((next_stop - used) / space))
                    out += part
                paragraph = out
            head = simpleSplit(paragraph, name, size, max(room - first, 1)) or [""]
            rest = paragraph[len(head[0]):].lstrip(" ") if len(head) > 1 else ""
            tail = simpleSplit(rest, name, size, room) if rest else []
            parts = [head[0], *tail]
            for index, line in enumerate(parts):
                lines.append((line, left + (first if index == 0 else 0), index == 0, index == len(parts) - 1))
        return lines

    def paragraph_style(self, element: dict[str, Any], size: float):
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
        from reportlab.lib.styles import ParagraphStyle
        return ParagraphStyle(
            "element", fontName=self.family(element)[0], fontSize=size, leading=self.leading(element, size),
            textColor=self.hex(element.get("color") or "#000000"),
            alignment={"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT, "justified": TA_JUSTIFY}[element["align"]],
            leftIndent=element.get("leftIndent", 0), rightIndent=element.get("rightIndent", 0),
            firstLineIndent=element.get("firstLineIndent", 0),
            spaceBefore=element.get("spacingBefore", 0), spaceAfter=element.get("spacingAfter", 0),
        )

    def block_height(self, element: dict[str, Any], text: str, size: float, width: float) -> float:
        """Height of the text block (without the padding) at this font size."""
        if element.get("markup", "none") != "none" or element["align"] == "justified":
            from reportlab.platypus import Paragraph
            style = self.paragraph_style(element, size)
            try:
                _w, block = Paragraph(markup_to_paragraph(text, element.get("markup", "none"), size), style).wrap(width, 1_000_000)
            except Exception:  # noqa: BLE001 - malformed markup: measure the plain text
                block = 0.0
            return block + style.spaceBefore + style.spaceAfter
        lines = self.plain_lines(element, text, size, width)
        paragraphs = sum(1 for line in lines if line[2])
        extra = paragraphs * (element.get("spacingBefore", 0) + element.get("spacingAfter", 0))
        return len(lines) * self.leading(element, size) + extra

    def text_height(self, element: dict[str, Any], text: str) -> float:
        """Height the text needs inside the element's width (same wrapping as draw_text / draw_paragraph)."""
        if element.get("rotation", "None") in ("Left", "Right"):
            return element["height"]  # turned text runs along the height: the box does not grow
        pad = element.get("padding", 2)
        return self.block_height(element, text, element["fontSize"], max(element["width"] - 2 * pad, 1)) + 2 * pad

    def band_layout(self, band: dict[str, Any], row: dict[str, Any] | None) -> tuple[float, list[tuple[float, float]]]:
        """(band height, [(y, height) per element]) after "Stretch With Overflow".

        Like Jasper: a stretching text grows to fit its value; elements on the same line grow with it
        (stretchType RelativeToTallestObject), elements below it move down (positionType Float) and the band grows.
        """
        elements = band["elements"]
        boxes = [(e["y"], e["height"]) for e in elements]
        growth: list[tuple[float, float, float]] = []  # (top, original bottom, extra height)
        blanks = [e for e in elements if e.get("removeLineWhenBlank") and e["type"] not in NOT_DRAWN]
        hidden = {id(e) for e in blanks if not self.shown(e, row)}
        for index, element in enumerate(elements):
            if not element.get("stretch") or id(element) in hidden:
                continue
            needed = self.text_height(element, self.element_text(element, row))
            if needed > element["height"] + 0.5:
                boxes[index] = (element["y"], needed)
                growth.append((element["y"], element["y"] + element["height"], needed - element["height"]))
        # "Remove Line When Blank": a hidden element's line closes up when nothing else printed is on it
        removed: list[tuple[float, float]] = []
        for element in blanks:
            if id(element) not in hidden or element["height"] <= 0:
                continue
            top, bottom = element["y"], element["y"] + element["height"]
            if not any(other is not element and id(other) not in hidden and other["type"] not in NOT_DRAWN
                       and other["y"] < bottom and other["y"] + max(other["height"], 0.01) > top for other in elements):
                removed.append((top, bottom))
        removed.sort()
        merged: list[list[float]] = []
        for top, bottom in removed:
            if merged and top <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], bottom)
            else:
                merged.append([top, bottom])

        def closed_above(y: float) -> float:
            return sum(max(0.0, min(bottom, y) - top) for top, bottom in merged)

        special = any(e.get("positionType") or e.get("stretchType") for e in elements)
        if not growth and not merged and not special:
            return band["height"], boxes
        result = []
        later: list[int] = []  # depend on the band's final height
        for index, ((y, h), element) in enumerate(zip(boxes, elements)):
            position, stretch = element.get("positionType", ""), element.get("stretchType", "")
            if stretch == "RelativeToTallestObject":
                for top, bottom, g in growth:
                    if top < element["y"] + element["height"] and bottom > element["y"]:
                        h = max(h, bottom + g - element["y"])
            elif not stretch and not element.get("stretch"):  # automatic: grow with a text on the same line
                extra_same_line = max((g for top, _b, g in growth if abs(top - element["y"]) <= 1), default=0.0)
                if extra_same_line and element["type"] in ("staticText", "textField", "rectangle", "line"):
                    if element["type"] != "line" or element["height"] > 1:  # a horizontal line keeps its height
                        h = element["height"] + extra_same_line
            if position == "FixRelativeToTop":
                shift = 0.0
            else:  # Float, or automatic: below a growing element it moves down
                shift = max((g for _top, bottom, g in growth if element["y"] >= bottom - 0.5), default=0.0)
            result.append((y + shift - closed_above(element["y"]), h))
            if position == "FixRelativeToBottom" or stretch in ("RelativeToBandHeight", "ContainerHeight", "ContainerBottom"):
                later.append(index)
        original_bottom = max((e["y"] + e["height"] for e in elements), default=0.0)
        trailing = max(0.0, band["height"] - original_bottom)
        closed = closed_above(float("inf"))
        shown_boxes = [box for box, element in zip(result, elements) if id(element) not in hidden]
        height = max(band["height"] - closed, max((y + h for y, h in shown_boxes), default=0.0) + trailing)
        grown = height - band["height"]
        for index in later:
            element, (y, h) = elements[index], result[index]
            if element.get("positionType") == "FixRelativeToBottom":
                y = element["y"] + grown           # keeps its distance from the band's bottom
            if element.get("stretchType") in ("RelativeToBandHeight", "ContainerHeight"):
                h = element["height"] + max(grown, 0.0)
            elif element.get("stretchType") == "ContainerBottom":
                h = max(element["height"], height - y)
            result[index] = (y, h)
        return height, result

    def band_height(self, band: dict[str, Any], row: dict[str, Any] | None) -> float:
        changes = any(e.get("stretch") or e.get("removeLineWhenBlank") or e.get("positionType") or e.get("stretchType") for e in band["elements"])
        return self.band_layout(band, row)[0] if changes else band["height"]

    def draw_band(self, canvas, band: dict[str, Any], top: float, row: dict[str, Any] | None, scope_rows=None, engine=None) -> None:
        eng = engine or self  # a subreport's bands are evaluated by the subreport's own engine
        left = eng.page["margins"]["left"] + eng.x_offset
        _height, boxes = eng.band_layout(band, row)
        for element, (y, h) in zip(band["elements"], boxes):
            if element["type"] in NOT_DRAWN or not eng.printing(element, row):
                continue
            eng.draw_element(canvas, element, left + element["x"], top + y, row, scope_rows, h)

    # ---- dataset components (Table, Crosstab, List, Subreport): they expand into many printed rows
    def raw_value(self, expression: str, row: dict[str, Any] | None) -> Any:
        """The value of a column expression (a single token keeps its type, so numbers can be totalled)."""
        resolve = lambda kind, name: self.resolve(kind, name, row)  # noqa: E731
        found = TOKEN_RE.fullmatch(expression.strip())
        if found:
            return resolve(found.group(1), found.group(2))
        if expression.lstrip().startswith("="):
            return eval_expression(expression.lstrip()[1:], resolve)
        return TOKEN_RE.sub(lambda m: format_value(resolve(m.group(1), m.group(2)), ""), expression)

    def dataset_rows(self, element: dict[str, Any], row: dict[str, Any] | None, level: int | None) -> list[dict[str, Any]]:
        """Records of a component: its own dataset's records, all report rows, or (scope "group") only the rows of the
        current group; then the filter expression and the sort order."""
        rows = self.rows
        if element.get("datasetName"):
            rows = self.subdataset_rows(element, row)
        elif element.get("scope") == "group" and level is not None and row is not None and self.groups:
            keys = [g["field"] for g in self.groups[:level + 1]]
            rows = [r for r in rows if all(r.get(k) == row.get(k) for k in keys)]
        expression = element.get("filterExpression")
        if expression:
            kept = []
            for item in rows:
                try:
                    if jexpr.evaluate(expression, lambda kind, name, item=item: self.resolve(kind, name, item)):
                        kept.append(item)
                except jexpr.JExprError:
                    pass
            rows = kept
        field = element.get("sortField")
        if field:
            present = [r for r in rows if r.get(field) is not None]
            missing = [r for r in rows if r.get(field) is None]
            present.sort(key=lambda r: _sort_key(r.get(field)), reverse=element.get("sortOrder") == "Descending")
            rows = present + missing
        return rows

    def subdataset_rows(self, element: dict[str, Any], row: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Run the component's sub-dataset (Jasper datasetRun). Parameters: the mapped expressions; a dataset
        parameter that is not mapped takes the report parameter of the same name, when there is one."""
        dataset = next((d for d in self.design.get("datasets", []) if d["name"] == element["datasetName"]), None)
        if dataset is None:
            raise DatasetError(f"the dataset {element['datasetName']} does not exist")
        loader = DATASET_LOADER.get()
        if loader is None:
            return sample_rows(dataset["fields"], 6)
        values: dict[str, Any] = {}
        mapped = {item["name"]: item["expression"] for item in element.get("datasetParameters", [])}
        for spec in dataset["parameters"]:
            if mapped.get(spec["name"]):
                value = self.raw_value(mapped[spec["name"]], row)
            else:
                value = self.parameters.get(spec["name"])
            if isinstance(value, (date, datetime)):
                value = value.isoformat()
            if value is not None and value != "":
                values[spec["name"]] = value
        try:
            found = loader(dataset, values)
        except DatasetError:
            raise
        except Exception as exc:  # noqa: BLE001 - shown in the report instead of failing the whole report
            raise DatasetError(str(exc)) from None
        return [{field: item.get(field) for field in dataset["fields"]} for item in found]

    def expand(self, element, band, row, level):
        """Segments to print for a component: (kind, band, row, engine, level); kind "header" repeats on new pages."""
        kind = element["type"]
        if kind in DATASET_COMPONENTS and element.get("datasetName"):
            try:
                segments = list(self._table_segments(element, row, level) if kind == "table"
                                else self._crosstab_segments(element, row, level))
            except DatasetError as exc:
                text = f"Dataset {element['datasetName']}: {exc}"
                segments = [("row", {"height": 14, "elements": [
                    _cell("staticText", element["x"], 0, element["width"], 14, element, text=text, italic=True,
                          color="#b91c1c")]}, None, self, None)]
            yield from segments
        elif kind == "table":
            yield from self._table_segments(element, row, level)
        elif kind == "crosstab":
            yield from self._crosstab_segments(element, row, level)
        elif kind == "list":
            yield from self._list_segments(element, band, row, level)
        elif kind == "subreport":
            yield from self._subreport_segments(element, row)

    def _table_segments(self, table, row, level):
        data = self.dataset_rows(table, row, level)
        columns = table.get("columns") or []
        if not columns:
            return
        lefts, x = [], table["x"]
        for col in columns:
            lefts.append(x)
            x += col["width"]
        grid = {"borderWidth": table["gridWidth"], "borderColor": table["gridColor"]} if table["gridWidth"] else {}
        if table["headerHeight"] and any(col["header"] for col in columns):
            yield ("header", {"height": table["headerHeight"], "elements": [
                _cell("staticText", lefts[i], 0, col["width"], table["headerHeight"], table, text=col["header"], bold=True,
                      align=col["align"], backColor=table.get("headerBackColor"), color=table.get("headerColor") or "#000000",
                      stretch=True, **grid)
                for i, col in enumerate(columns)]}, None, self, None)
        for index, item in enumerate(data):
            zebra = table.get("zebraColor") if index % 2 else None
            yield ("row", {"height": table["rowHeight"], "elements": [
                _cell("textField", lefts[i], 0, col["width"], table["rowHeight"], table, expression=col["expression"],
                      pattern=col["pattern"], align=col["align"], stretch=True, backColor=zebra, **grid)
                for i, col in enumerate(columns)]}, item, self, None)
        if table.get("footerHeight") and any(col["footer"] != "none" for col in columns):
            cells = []
            for i, col in enumerate(columns):
                value, text = None, ""
                if col["footer"] == "text":
                    text = col["footerText"]
                elif col["footer"] != "none":
                    raw = [self.raw_value(col["expression"], item) for item in data]
                    value = _aggregate(col["footer"], raw)
                    text = format_value(value, col["pattern"] if col["footer"] != "Count" else "")
                cells.append(_cell("staticText", lefts[i], 0, col["width"], table["footerHeight"], table, text=text, bold=True,
                                   align=col["align"], backColor=table.get("headerBackColor"), stretch=True,
                                   _value=value, _pattern=col["pattern"] if col["footer"] != "Count" else "", **grid))
            yield ("footer", {"height": table["footerHeight"], "elements": cells}, None, self, None)

    def _crosstab_segments(self, cross, row, level):
        data = self.dataset_rows(cross, row, level)
        calc, measure = cross["calculation"], cross["measureField"]
        cells: dict[tuple[Any, Any], list[Any]] = {}
        row_values: dict[Any, None] = {}
        col_values: dict[Any, None] = {}
        for item in data:
            r_key, c_key = _hashable(item.get(cross["rowField"])), _hashable(item.get(cross["columnField"]))
            row_values.setdefault(r_key)
            col_values.setdefault(c_key)
            cells.setdefault((r_key, c_key), []).append(1 if calc == "Count" else item.get(measure))
        rows_sorted = sorted(row_values, key=_sort_key)
        cols_sorted = sorted(col_values, key=_sort_key)
        total = object()
        columns = cols_sorted + ([total] if cross["showRowTotals"] and cols_sorted else [])
        grid = {"borderWidth": cross["gridWidth"], "borderColor": cross["gridColor"]} if cross["gridWidth"] else {}
        rh_w, cell_w, rh = cross["rowHeaderWidth"], cross["cellWidth"], cross["rowHeight"]
        per_block = max(1, int((cross["width"] - rh_w) // cell_w))
        label = lambda v: format_value(v, "") if v not in (None, "") else "(empty)"  # noqa: E731
        pattern = cross["pattern"] if calc != "Count" else ""

        def value_of(r_keys, c_keys):
            values = [v for r in r_keys for c in c_keys for v in cells.get((r, c), [])]
            return _aggregate(calc, values) if values else None

        def number_cell(x, height, value, back=None, bold=False):
            return _cell("staticText", x, 0, cell_w, height, cross, text=format_value(value, pattern), align="right",
                         backColor=back, bold=bold, _value=value, _pattern=pattern, **grid)

        if not columns:
            return
        for start in range(0, len(columns), per_block):
            block = columns[start:start + per_block]
            if start:
                yield ("row", {"height": 8, "elements": []}, None, self, None)  # gap before the next block of columns
            head_h = cross["headerHeight"] or rh
            header = [_cell("staticText", cross["x"], 0, rh_w, head_h, cross, text=cross["cornerText"] or cross["rowField"],
                            bold=True, backColor=cross.get("headerBackColor"), color=cross.get("headerColor") or "#000000", **grid)]
            for i, col in enumerate(block):
                header.append(_cell("staticText", cross["x"] + rh_w + i * cell_w, 0, cell_w, head_h, cross,
                                    text="Total" if col is total else label(col), bold=True, align="center", stretch=True,
                                    backColor=cross.get("totalBackColor") if col is total else cross.get("headerBackColor"),
                                    color=cross.get("headerColor") or "#000000", **grid))
            yield ("header", {"height": head_h, "elements": header}, None, self, None)
            for r_key in rows_sorted:
                elements = [_cell("staticText", cross["x"], 0, rh_w, rh, cross, text=label(r_key), bold=True, stretch=True,
                                  backColor=cross.get("headerBackColor"), **grid)]
                for i, col in enumerate(block):
                    keys = cols_sorted if col is total else [col]
                    elements.append(number_cell(cross["x"] + rh_w + i * cell_w, rh, value_of([r_key], keys),
                                                cross.get("totalBackColor") if col is total else None, col is total))
                yield ("row", {"height": rh, "elements": elements}, None, self, None)
            if cross["showColumnTotals"] and rows_sorted:
                elements = [_cell("staticText", cross["x"], 0, rh_w, rh, cross, text="Total", bold=True,
                                  backColor=cross.get("totalBackColor"), **grid)]
                for i, col in enumerate(block):
                    keys = cols_sorted if col is total else [col]
                    elements.append(number_cell(cross["x"] + rh_w + i * cell_w, rh, value_of(rows_sorted, keys),
                                                cross.get("totalBackColor"), True))
                yield ("row", {"height": rh, "elements": elements}, None, self, None)

    def _list_segments(self, lst, band, row, level):
        """Jasper List: the elements drawn inside the List box are printed once per record."""
        inside = _contained(lst, band)
        template = [{**e, "y": e["y"] - lst["y"]} for e in inside]
        if lst["backColor"] or lst["borderWidth"] or lst.get("borders"):
            template.insert(0, {**lst, "type": "rectangle", "y": 0, "printWhenExpression": ""})
        cell = {"height": lst["height"], "elements": template}
        for item in self.dataset_rows(lst, row, level):
            yield ("row", cell, item, self, None)

    def _subreport_segments(self, sub, row):
        def message(text):
            return ("row", {"height": max(sub["height"], 14), "elements": [
                _cell("staticText", sub["x"], 0, sub["width"], max(sub["height"], 14), sub, text=text, italic=True,
                      color="#64748b")]}, None, self, None)
        loader = SUBREPORT_LOADER.get()
        if not sub.get("reportId"):
            yield message("Subreport: choose a report in its properties")
            return
        if self.depth >= MAX_SUBREPORT_DEPTH:
            yield message(f"Subreport {sub['reportId']}: subreports can be nested {MAX_SUBREPORT_DEPTH} levels deep")
            return
        if loader is None:
            yield message(f"Subreport {sub['reportId']} — filled from its own query when the report runs")
            return
        params: dict[str, Any] = {}
        for item in sub.get("parameters", []):
            value = self.raw_value(item["expression"], row) if item["expression"] else None
            if isinstance(value, (date, datetime)):
                value = value.isoformat()
            if value is not None and value != "":
                params[item["name"]] = value
        try:
            loaded = loader(sub["reportId"], params)
        except Exception as exc:  # noqa: BLE001 - shown in the report instead of failing the whole report
            yield message(f"Subreport {sub['reportId']}: {exc}")
            return
        child = _Render({**loaded["design"], "page": self.page}, loaded.get("title", ""), loaded["columns"], loaded["rows"],
                        loaded.get("parameters", params), self.total_pages)
        child.depth = self.depth + 1
        child.x_offset = self.x_offset + sub["x"]
        child.page_number = self.page_number
        bands = child.bands
        if not child.rows:
            if band_present(bands.get("noData")):
                yield ("row", bands["noData"], None, child, None)
            return
        if sub.get("showTitle", True) and band_present(bands["title"]):
            yield ("row", bands["title"], None, child, None)
        if band_present(bands["columnHeader"]):
            yield ("header", bands["columnHeader"], None, child, None)
        for band_item, band_row, band_level in child.sequence():
            yield ("row", band_item, band_row, child, band_level)
        for key in ("columnFooter", "summary"):
            if band_present(bands[key]):
                yield ("footer", bands[key], None, child, None)

    def sequence(self):
        """The group headers, Detail rows and group footers in print order (variables are updated as it goes)."""
        previous: list[Any] | None = None
        previous_row: dict[str, Any] | None = None
        rows = self.rows
        if self.groups:
            rows = sorted(rows, key=lambda r: tuple("" if r.get(g["field"]) is None else str(r.get(g["field"])) for g in self.groups))
        detail_level = len(self.groups) - 1 if self.groups else None
        for row in rows:
            current = [row.get(g["field"]) for g in self.groups]
            changed_from = len(self.groups)
            if previous is None:
                changed_from = 0
            else:
                for level, (old, new) in enumerate(zip(previous, current)):
                    if old != new:
                        changed_from = level
                        break
            self.changed_groups = {g["name"] for g in self.groups[changed_from:]}
            if previous is not None:
                for level in range(len(self.groups) - 1, changed_from - 1, -1):
                    name = self.groups[level]["name"]
                    yield self.bands["groupFooters"][name], previous_row, level
                    self.reset_variables(name)
            for level in range(changed_from, len(self.groups)):
                yield self.bands["groupHeaders"][self.groups[level]["name"]], row, level
            self.report_count += 1
            self.accumulate(row)
            yield self.bands["detail"], row, detail_level
            previous, previous_row = current, row
        if previous is not None:
            for level in range(len(self.groups) - 1, -1, -1):
                yield self.bands["groupFooters"][self.groups[level]["name"]], previous_row, level

    def stroke(self, canvas, element, width=None) -> None:
        canvas.setStrokeColor(self.hex(element.get("borderColor") or element.get("color") or "#000000"))
        canvas.setLineWidth(width if width is not None else (element.get("borderWidth") or 1))
        dash = LINE_STYLES.get(element.get("lineStyle", "solid"))
        if dash:
            canvas.setDash(*dash)

    def draw_element(self, canvas, element, x, y_top, row, scope_rows, height=None) -> None:
        height_page = self.page["height"]
        w, h = element["width"], element["height"] if height is None else height
        y = height_page - y_top - h  # bottom-left origin
        kind = element["type"]
        back = None if element.get("transparent") else element["backColor"]  # Appearance → Transparent
        if kind in ("rectangle", "frame", "ellipse") or (kind in ("staticText", "textField", "barcode") and (back or element["borderWidth"])):
            canvas.saveState()
            if back:
                canvas.setFillColor(self.hex(back))
            if element["borderWidth"]:
                self.stroke(canvas, element)
            shape = canvas.ellipse if kind == "ellipse" else None
            if shape:
                canvas.ellipse(x, y, x + w, y + h, stroke=1 if element["borderWidth"] else 0, fill=1 if back else 0)
            else:
                canvas.rect(x, y, w, h, stroke=1 if element["borderWidth"] else 0, fill=1 if back else 0)
            canvas.restoreState()
        if element.get("borders"):  # a separate border per side (Jasper Borders tab)
            canvas.saveState()
            sides = element["borders"]
            for side, (x1, y1, x2, y2) in {"top": (x, y + h, x + w, y + h), "bottom": (x, y, x + w, y),
                                           "left": (x, y, x, y + h), "right": (x + w, y, x + w, y + h)}.items():
                if sides.get(side):
                    self.stroke(canvas, element, sides[side])
                    canvas.line(x1, y1, x2, y2)
            canvas.restoreState()
        if kind in ("staticText", "textField"):
            text = element.get("text", "") if kind == "staticText" else self.evaluate(element, row)
            self.draw_text(canvas, element, text, x, y, w, h)
        elif kind == "barcode":
            self.draw_barcode(canvas, element, self.evaluate(element, row), x, y, w, h)
        elif kind == "line":
            canvas.saveState()
            self.stroke(canvas, element)
            canvas.line(x, y + h, x + w, y)
            canvas.restoreState()
        elif kind == "image" and element.get("src"):
            from reportlab.lib.utils import ImageReader
            data = base64.b64decode(IMAGE_RE.fullmatch(element["src"]).group(2))
            canvas.drawImage(ImageReader(io.BytesIO(data)), x, y, w, h, preserveAspectRatio=True, anchor="c", mask="auto")
        elif kind == "chart":
            self.draw_chart(canvas, element, x, y, w, h)

    def draw_barcode(self, canvas, element, value: str, x, y, w, h) -> None:
        """QR code or a 1-D barcode (Code 128, Code 39, EAN-13) of the evaluated value, scaled into the box."""
        value = str(value or "").strip()
        if not value:
            return
        from reportlab.graphics import renderPDF
        from reportlab.graphics.shapes import Drawing
        kind = element.get("barcodeType", "qr")
        try:
            if kind == "qr":
                from reportlab.graphics.barcode.qr import QrCodeWidget
                widget = QrCodeWidget(value)
                bx1, by1, bx2, by2 = widget.getBounds()
                side = min(w, h)
                drawing = Drawing(side, side, transform=[side / (bx2 - bx1), 0, 0, side / (by2 - by1), 0, 0])
                drawing.add(widget)
                renderPDF.draw(drawing, canvas, x + (w - side) / 2, y + (h - side) / 2)
                return
            from reportlab.graphics.barcode import createBarcodeDrawing
            name = {"code128": "Code128", "code39": "Standard39", "ean13": "EAN13"}[kind]
            text_room = element["fontSize"] + 2 if element.get("showText", True) else 0
            drawing = createBarcodeDrawing(name, value=value, humanReadable=False, barHeight=max(h - text_room, 4))
            scale = min(w / max(drawing.width, 1), 1.0) if drawing.width > w else 1.0
            drawing.scale(scale, 1)
            renderPDF.draw(drawing, canvas, x + (w - drawing.width * scale) / 2, y + text_room)
            if text_room:
                canvas.saveState()
                canvas.setFont(self.font(element), element["fontSize"])
                canvas.setFillColor(self.hex(element["color"]))
                canvas.drawCentredString(x + w / 2, y + 1, value)
                canvas.restoreState()
        except Exception:  # noqa: BLE001 - e.g. an EAN-13 value that is not 12/13 digits: show the text instead
            self.draw_text(canvas, {**element, "markup": "none", "align": "center"}, value, x, y, w, h)

    def draw_paragraph(self, canvas, element, text, x, y, w, h) -> None:
        """Markup (HTML / styled) or justified text: laid out with a ReportLab Paragraph inside the element box."""
        from reportlab.platypus import Paragraph
        size, pad = element["fontSize"], element.get("padding", 2)
        body = markup_to_paragraph(text, element.get("markup", "none"), size)
        for flag, tag in (("underline", "u"), ("italic", "i"), ("bold", "b")):
            if element.get(flag):
                body = f"<{tag}>{body}</{tag}>"
        style = self.paragraph_style(element, size)
        try:
            paragraph = Paragraph(body, style)
            _width, block = paragraph.wrap(max(w - 2 * pad, 1), 1_000_000)
        except Exception:  # noqa: BLE001 - malformed markup: fall back to the plain text
            paragraph = Paragraph(markup_to_paragraph(markup_to_text(text, element.get("markup", "none")), "none"), style)
            _width, block = paragraph.wrap(max(w - 2 * pad, 1), 1_000_000)
        block += style.spaceBefore + style.spaceAfter
        if element.get("vAlign") == "middle":
            bottom = y + (h - block) / 2
        elif element.get("vAlign") == "bottom":
            bottom = y + pad
        else:
            bottom = y + h - pad - block
        canvas.saveState()
        path = canvas.beginPath()
        path.rect(x, y, w, h)
        canvas.clipPath(path, stroke=0, fill=0)
        paragraph.drawOn(canvas, x + pad, bottom + style.spaceAfter)
        canvas.restoreState()

    def draw_text(self, canvas, element, text, x, y, w, h) -> None:
        rotation = element.get("rotation", "None")
        if rotation in ("Left", "Right", "UpsideDown"):
            # Text Field → Rotation: turn the canvas and draw the text in the turned box
            canvas.saveState()
            if rotation == "Left":        # reads from bottom to top
                canvas.translate(x + w, y)
                canvas.rotate(90)
                box_w, box_h = h, w
            elif rotation == "Right":     # reads from top to bottom
                canvas.translate(x, y + h)
                canvas.rotate(-90)
                box_w, box_h = h, w
            else:
                canvas.translate(x + w, y + h)
                canvas.rotate(180)
                box_w, box_h = w, h
            self.draw_text(canvas, {**element, "rotation": "None", "width": box_w, "height": box_h}, text, 0, 0, box_w, box_h)
            canvas.restoreState()
            return
        if element.get("textAdjust") == "ScaleFont":
            # Text Adjust → Scale Font: a smaller font until the whole text fits in the box
            pad = element.get("padding", 2)
            size = element["fontSize"]
            while size > 4 and self.block_height(element, text, size, max(w - 2 * pad, 1)) > h - 2 * pad + 0.5:
                size = max(4, size - 0.5)
            element = {**element, "fontSize": size}
        if element.get("markup", "none") != "none" or element["align"] == "justified":
            self.draw_paragraph(canvas, element, text, x, y, w, h)
            return
        size, name, pad = element["fontSize"], self.font(element), element.get("padding", 2)
        lines = self.plain_lines(element, text, size, max(w - 2 * pad, 1))
        leading = self.leading(element, size)
        before, after = element.get("spacingBefore", 0), element.get("spacingAfter", 0)
        right_indent = element.get("rightIndent", 0)
        canvas.saveState()
        path = canvas.beginPath()
        path.rect(x, y, w, h)
        canvas.clipPath(path, stroke=0, fill=0)
        canvas.setFillColor(self.hex(element["color"]))
        canvas.setFont(name, size)
        block = self.block_height(element, text, size, max(w - 2 * pad, 1))
        if element.get("vAlign") == "middle":
            top_edge = y + (h + block) / 2
        elif element.get("vAlign") == "bottom":
            top_edge = y + block + pad
        else:
            top_edge = y + h - pad
        # the first baseline sits one font size below the top (Single spacing: as before); bigger spacing adds room above
        cursor = top_edge - size - (leading - size * 1.2 if leading > size * 1.2 else 0)
        for line, indent, first, last in lines:
            if first:
                cursor -= before
            baseline = cursor
            cursor -= leading
            if last:
                cursor -= after
            if baseline < y - size or baseline > y + h + size:
                continue
            line_width = canvas.stringWidth(line, name, size)
            if element["align"] == "right":
                start = x + w - pad - right_indent - line_width
            elif element["align"] == "center":
                start = x + indent + (w - indent - right_indent - line_width) / 2
            else:
                start = x + pad + indent
            canvas.drawString(start, baseline, line)
            if element.get("underline") and line:
                canvas.setStrokeColor(self.hex(element["color"]))
                canvas.setLineWidth(max(0.5, size / 18))
                canvas.line(start, baseline - size * 0.12, start + line_width, baseline - size * 0.12)
        canvas.restoreState()

    def chart_data(self, element) -> tuple[list[str], list[float]]:
        buckets: dict[str, list[float]] = {}
        for row in self.rows:
            label = format_value(row.get(element["categoryField"]), "") or "-"
            number = 1.0 if element["aggregate"] == "Count" else _to_number(row.get(element["valueField"]))
            if number is not None:
                buckets.setdefault(label, []).append(number)
        labels = list(buckets)[:12]
        if element["aggregate"] == "Average":
            values = [sum(buckets[k]) / len(buckets[k]) for k in labels]
        else:
            values = [sum(buckets[k]) for k in labels]
        return labels, values

    def draw_chart(self, canvas, element, x, y, w, h) -> None:
        from reportlab.graphics import renderPDF
        from reportlab.graphics.charts.barcharts import VerticalBarChart
        from reportlab.graphics.charts.linecharts import HorizontalLineChart
        from reportlab.graphics.charts.piecharts import Pie
        from reportlab.graphics.shapes import Drawing, String
        labels, values = self.chart_data(element)
        drawing = Drawing(w, h)
        title_h = 16 if element.get("title") else 0
        if element.get("title"):
            drawing.add(String(w / 2, h - 12, element["title"], textAnchor="middle", fontName=self.fonts[1], fontSize=9))
        if not values:
            drawing.add(String(w / 2, h / 2, "No data", textAnchor="middle", fontName=self.fonts[0], fontSize=9))
        elif element["chartType"] == "spider" and len(values) >= 3:
            from reportlab.graphics.charts.spider import SpiderChart
            spider = SpiderChart()
            side = min(w, h - title_h) - 30
            spider.x, spider.y, spider.width, spider.height = (w - side) / 2, 12, side, side
            spider.data, spider.labels = [values], labels
            spider.strands[0].strokeColor = self.hex(element["color"] if element["color"] != "#000000" else "#2563eb")
            spider.strands[0].fillColor = None
            spider.strands[0].strokeWidth = 1.5
            spider.strandLabels.fontSize = 6
            spider.spokeLabels.fontName, spider.spokeLabels.fontSize = self.fonts[0], 7
            drawing.add(spider)
        elif element["chartType"] == "pie":
            pie = Pie()
            side = min(w, h - title_h) - 20
            pie.x, pie.y, pie.width, pie.height = (w - side) / 2, 8, side, side
            pie.data, pie.labels = values, labels
            pie.slices.fontName, pie.slices.fontSize = self.fonts[0], 7
            palette = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d", "#ea580c", "#4b5563", "#0d9488", "#9333ea"]
            for index in range(len(values)):
                pie.slices[index].fillColor = self.hex(palette[index % len(palette)])
            drawing.add(pie)
        else:
            chart = VerticalBarChart() if element["chartType"] in ("bar", "spider") else HorizontalLineChart()
            chart.x, chart.y, chart.width, chart.height = 40, 28, w - 55, h - 44 - title_h
            chart.data = [values]
            chart.categoryAxis.categoryNames = labels
            chart.categoryAxis.labels.fontSize = 7
            chart.categoryAxis.labels.angle = 30 if len(labels) > 5 else 0
            chart.categoryAxis.labels.boxAnchor = "ne" if len(labels) > 5 else "n"
            chart.valueAxis.labels.fontSize = 7
            chart.valueAxis.valueMin = min(0, min(values))
            series = self.hex(element["color"] if element["color"] != "#000000" else "#2563eb")
            if element["chartType"] in ("bar", "spider"):  # a spider chart needs 3+ categories; fewer: bars
                chart.bars[0].fillColor = series
            else:
                chart.lines[0].strokeColor = series
                chart.lines[0].strokeWidth = 2
            drawing.add(chart)
        renderPDF.draw(drawing, canvas, x, y)

    # ---- flow
    def run(self, canvas_factory):
        margins = self.page["margins"]
        top_limit, height = margins["top"], self.page["height"]
        canvas = canvas_factory
        bands = self.bands
        page_footer = bands["pageFooter"]["height"]
        column_footer = bands["columnFooter"]["height"]
        bottom_limit = height - margins["bottom"] - page_footer - column_footer
        last_footer = bands.get("lastPageFooter")
        has_last_footer = band_present(last_footer)
        state = {"y": top_limit, "first": True}

        # Jasper "No Data" section: with no rows, only the No Data band is printed.
        no_data = self.page.get("whenNoDataType", "Auto")
        if not self.rows and (no_data in ("NoPages", "BlankPage") or (no_data == "NoDataSection" and not band_present(bands.get("noData")))):
            # Jasper "No Pages" / "Blank Page": nothing is printed (a PDF still needs one empty page)
            self.page_number += 1
            self.pages_drawn += 1
            self.content_end = top_limit
            self.early_exit = True
            canvas.showPage()
            return
        if not self.rows and no_data in ("Auto", "NoDataSection") and band_present(bands.get("noData")):
            self.page_number += 1
            self.pages_drawn += 1
            self.draw_band(canvas, bands["noData"], top_limit, None)
            self.content_end = top_limit + bands["noData"]["height"]
            self.early_exit = True
            canvas.showPage()
            return

        def start_page(first: bool) -> None:
            self.page_number += 1
            self.pages_drawn += 1
            state["y"] = top_limit
            if band_present(bands.get("background")):
                self.draw_band(canvas, bands["background"], top_limit, None)  # behind everything else on the page
            if first and band_present(self.bands["title"]):
                before = self.pages_drawn
                place(self.bands["title"], None)  # a Table in the Title can run over several pages
                if self.page.get("titleNewPage"):
                    canvas.showPage()  # Jasper "Title on a new page": the title page has no page header or footer
                    start_page(False)
                    return
                if self.pages_drawn != before:
                    return  # the later pages already printed their page and column headers
            for key in ("pageHeader", "columnHeader"):
                self.draw_band(canvas, self.bands[key], state["y"], None)
                state["y"] += self.band_height(self.bands[key], None)
            # a Table / Crosstab that continues on this page prints its header again
            for header, header_row, header_engine in state.get("repeat") or []:
                self.draw_band(canvas, header, state["y"], header_row, engine=header_engine)
                state["y"] += header_engine.band_height(header, header_row)

        def end_page(last: bool = False) -> None:
            # On the last page the Last Page Footer (when there is one) replaces the Page Footer.
            footer = last_footer if last and has_last_footer else bands["pageFooter"]
            footer_top = height - margins["bottom"] - footer["height"]
            # "Float column footer": right under the last Detail row instead of at the bottom of the page
            column_top = state["y"] if self.page.get("floatColumnFooter") else footer_top - column_footer
            self.draw_band(canvas, bands["columnFooter"], column_top, None)
            self.draw_band(canvas, footer, footer_top, None)
            canvas.showPage()

        def place(band: dict[str, Any], row: dict[str, Any] | None, level: int | None = None, engine: "_Render | None" = None,
                  keep: float = 0.0) -> None:
            eng = engine or self
            if not band["height"] and not band["elements"]:
                return
            breaks = sorted(e["y"] for e in band["elements"] if e["type"] == "break" and eng.print_when(e, row))
            if breaks:
                # Jasper Break element: what is above it stays on this page, what is below it starts a new page.
                cut = breaks[0]
                above = {"height": cut, "elements": [e for e in band["elements"] if e["type"] != "break" and e["y"] < cut]}
                below = {"height": max(0.0, band["height"] - cut),
                         "elements": [{**e, "y": e["y"] - cut} for e in band["elements"] if e["y"] >= cut and not (e["type"] == "break" and e["y"] == cut)]}
                place(above, row, level, engine)
                state["break"] = True
                if below["elements"]:  # empty space below a break does not open a page of its own
                    place(below, row, level, engine)
                return
            components = [e for e in band["elements"] if e["type"] in DATASET_TYPES and eng.print_when(e, row)]
            if components:
                place_component(band, row, level, eng, min(components, key=lambda e: (e["y"], e["x"])))
                return
            if state.pop("break", False) and state["y"] > top_limit:
                end_page()
                start_page(False)
            needed = eng.band_height(band, row)  # taller than designed when a text stretches with overflow
            if state["y"] + needed + keep > bottom_limit and state["y"] > top_limit:
                end_page()
                start_page(False)
            self.draw_band(canvas, band, state["y"], row, engine=engine)
            state["y"] += needed

        def place_component(band, row, level, eng, component) -> None:
            """Print the band in three parts: what is above / beside the component, its rows, what is below it."""
            bottom = component["y"] + component["height"]
            inside = {id(e) for e in _contained(component, band)} if component["type"] == "list" else set()
            rest = [e for e in band["elements"] if e is not component and id(e) not in inside]
            above = [e for e in rest if e["type"] not in DATASET_TYPES and e["y"] < bottom]
            below = [{**e, "y": max(0.0, e["y"] - bottom)} for e in rest if e["y"] >= bottom or (e["type"] in DATASET_TYPES and e["y"] < bottom)]
            if above or component["y"] > 0:
                place({"height": component["y"], "elements": above}, row, level, eng)
            outer = state.get("repeat")
            for kind, segment, segment_row, segment_engine, segment_level in eng.expand(component, band, row, level):
                if kind == "header":
                    place(segment, segment_row, segment_level, segment_engine, keep=component.get("rowHeight", 0) or 0)
                    state["repeat"] = [(segment, segment_row, segment_engine)] if component.get("repeatHeader", True) else None
                else:
                    if kind == "footer":
                        state["repeat"] = outer
                    place(segment, segment_row, segment_level, segment_engine)
            state["repeat"] = outer
            if below or band["height"] > bottom:
                place({"height": max(0.0, band["height"] - bottom), "elements": below}, row, level, eng)

        start_page(True)
        for band_item, band_row, band_level in self.sequence():
            place(band_item, band_row, band_level)
        if self.page.get("summaryNewPage") and band_present(self.bands["summary"]) and state["y"] > top_limit:
            end_page()  # Jasper "Summary on a new page"
            start_page(False)
        place(self.bands["summary"], None)
        if has_last_footer:
            last_limit = height - margins["bottom"] - last_footer["height"] - column_footer
            if state["y"] > last_limit and state["y"] > top_limit:  # a taller last footer needs its own page
                end_page()
                start_page(False)
        self.content_end = state["y"]
        end_page(last=True)


def render_design_pdf(design: dict[str, Any], title: str, columns: list[str], rows: list[dict[str, Any]],
                      parameters: dict[str, Any], report_id: str = "") -> bytes:
    """Render a validated design. Two passes so $V{PAGE_COUNT} is the real total."""
    try:
        from reportlab.pdfgen import canvas as pdf_canvas
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PDF export needs: pip install reportlab") from exc
    if len(rows) > MAX_ROWS_RENDER:
        raise ValueError(f"The designer PDF supports up to {MAX_ROWS_RENDER:,} rows")
    if design["page"].get("ignorePagination"):
        # Jasper "Ignore pagination": one page as tall as the content. Measure first, then draw.
        probe = {**design, "page": {**design["page"], "height": 14400}}
        measure = _Render(probe, title, columns, rows, parameters, 1)
        measure.run(pdf_canvas.Canvas(io.BytesIO(), pagesize=(probe["page"]["width"], 14400)))
        if measure.pages_drawn == 1:
            footer = design["bands"]["lastPageFooter"] if band_present(design["bands"].get("lastPageFooter")) else design["bands"]["pageFooter"]
            extra = design["bands"]["columnFooter"]["height"] + footer["height"]
            height = min(14400, max(100, measure.content_end + extra + design["page"]["margins"]["bottom"]))
            design = {**design, "page": {**design["page"], "height": height}}
        else:
            # Taller than the largest PDF page (14 400 pt ≈ 5 m): continue on as few maximum-height pages as possible
            # instead of falling back to the design's own (often very short) page height.
            design = probe
    size = (design["page"]["width"], design["page"]["height"])
    total = 0
    final: dict[str, Any] = {}
    for _ in range(2):
        buffer = io.BytesIO()
        canvas = pdf_canvas.Canvas(buffer, pagesize=size)
        canvas.setTitle(title or report_id or "Report")
        engine = _Render(design, title, columns, rows, parameters, total)
        engine.final = final  # second pass: the first pass's report-end values (Evaluation Time Report/Auto)
        engine.run(canvas)
        canvas.save()
        total = engine.pages_drawn
        final = engine.final_values()
    return buffer.getvalue()


# ----------------------------------------------------------------- tabular (CSV / XLSX) from a design
def _tabular_columns(design: dict[str, Any]) -> list[dict[str, Any]]:
    """Pair each detail text element with the column-header element above it (by horizontal overlap)."""
    detail = sorted((e for e in design["bands"]["detail"]["elements"] if e["type"] in ("textField", "staticText")), key=lambda e: e["x"])
    headers = sorted((e for e in design["bands"]["columnHeader"]["elements"] if e["type"] in ("staticText", "textField")), key=lambda e: e["x"])
    columns = []
    for element in detail:
        centre = element["x"] + element["width"] / 2
        match = next((h for h in headers if h["x"] - 0.5 <= centre <= h["x"] + h["width"] + 0.5), None)
        if match is not None:
            label = markup_to_text(match.get("text"), match.get("markup", "none")) if match["type"] == "staticText" else match.get("expression", "")
        else:
            tokens = tokens_in(element.get("expression", ""))
            label = tokens[0][1] if tokens else (element.get("text") or "")
        columns.append({"label": str(label or ""), "element": element, "header": match})
    return columns


def has_tabular_layout(design: dict[str, Any]) -> bool:
    """Excel/CSV can be made from the design: text in Detail, or (page layout) any printed element in any band,
    e.g. a Table or Crosstab in the Summary."""
    if any(e["type"] in ("textField", "staticText") for e in design["bands"]["detail"]["elements"]):
        return True
    if design["page"].get("sheetLayout") != "grid":
        return False
    bands = [design["bands"][key] for key in BAND_KEYS] + [b for side in ("groupHeaders", "groupFooters") for b in design["bands"][side].values()]
    return any(e["type"] not in ("break", "note") for band in bands for e in band["elements"])


def _numeric_format(pattern: str) -> str | None:
    """Java DecimalFormat pattern → Excel number format (prefix/suffix quoted; 0.00E0 → 0.00E+0; a; negative part)."""
    if ";" in (pattern or ""):
        positive, negative = pattern.split(";", 1)
        first = _numeric_format(positive)
        second = _numeric_format(negative)
        return f"{first};{second}" if first and second else first
    match = re.match(r"^(.*?)([#0,]+(?:\.[#0]+)?)(E0+)?(.*)$", pattern or "")
    if not match:
        return None
    prefix, core, exponent, suffix = match.groups()
    core += f"E+{exponent[1:]}" if exponent else ""
    if suffix.strip() == "%":  # a percent pattern: Excel multiplies by 100 like Java's DecimalFormat
        return (f'"{prefix}"' if prefix else "") + core + suffix
    return (f'"{prefix}"' if prefix else "") + core + (f'"{suffix}"' if suffix else "")


def _cell_value(engine: "_Render", element: dict[str, Any], row: dict[str, Any]) -> tuple[Any, str | None]:
    if element["type"] == "staticText":
        return markup_to_text(element.get("text", ""), element.get("markup", "none")), None
    expression = element.get("expression", "")
    found = TOKEN_RE.fullmatch(expression.strip())
    raw: Any = None
    resolve = engine.resolver(element, row)
    pattern = engine.pattern_for(element, resolve)
    if found:
        raw = resolve(found.group(1), found.group(2))
    elif expression.lstrip().startswith("="):
        raw = eval_expression(expression.lstrip()[1:], resolve)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and (not pattern or _numeric_format(pattern)) and not is_date_pattern(pattern):
        return raw, _numeric_format(pattern)
    return markup_to_text(engine.evaluate(element, row), element.get("markup", "none")), None


def _report_end_values(design, title, columns, rows, parameters) -> dict[str, Any]:
    """Final variable values for elements with Evaluation Time Report/Auto (Excel/CSV are filled in one pass)."""
    if not any(e.get("evaluationTime") in ("Report", "Auto") for key in BAND_KEYS for e in design["bands"][key]["elements"]) \
            and not any(e.get("evaluationTime") in ("Report", "Auto") for side in ("groupHeaders", "groupFooters")
                        for band in design["bands"][side].values() for e in band["elements"]):
        return {}
    engine = _Render(design, title, columns, rows, parameters, 1)
    for row in engine.rows:
        engine.report_count += 1
        engine.accumulate(row)
    engine.page_number = 1
    return engine.final_values()


def _tabular(design, title, columns, rows, parameters):
    engine = _Render(design, title, columns, rows, parameters, 1)
    engine.final = _report_end_values(design, title, columns, rows, parameters)
    layout = _tabular_columns(design)
    data = []
    for row in engine.rows:
        engine.report_count += 1
        engine.accumulate(row)
        data.append([_cell_value(engine, col["element"], row) for col in layout])
    engine.page_number = engine.total_pages = 1      # one sheet: headings say "Page 1 of 1", not "Page 0 of 0"
    return engine, layout, data


# ----------------------------------------------------------------- grid (pixel-perfect) Excel / CSV
class _NullCanvas:
    """Stands in for the PDF canvas while the report is filled for the grid exporters."""
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _GridCollector(_Render):
    """Fills the report like the PDF (groups, variables, stretch) on one endless page and records every
    printed element with its absolute position, instead of drawing it."""

    def __init__(self, design, title, columns, rows, parameters):
        endless = {**design, "page": {**design["page"], "height": 10**9}}
        super().__init__(endless, title, columns, rows, parameters, 1)
        self.final = _report_end_values(design, title, columns, rows, parameters)
        self.items: list[dict[str, Any]] = []
        self.cursor = 0.0
        self.skip = {id(design["bands"]["pageFooter"]), id(design["bands"]["columnFooter"]),
                     id(design["bands"].get("lastPageFooter")), id(design["bands"].get("background"))}

    def draw_band(self, canvas, band, top, row, scope_rows=None, engine=None) -> None:
        if id(band) in self.skip:
            return
        self.record(band, row, engine)

    def record(self, band, row, engine=None) -> None:
        eng = engine or self
        if not band_present(band):
            return
        height, boxes = eng.band_layout(band, row)
        for element, (y, h) in zip(band["elements"], boxes):
            if element["type"] in NOT_DRAWN or not eng.printing(element, row):
                continue
            item = {"element": element, "x": element["x"] + eng.x_offset, "y": self.cursor + y, "w": element["width"], "h": h}
            if element["type"] == "barcode":  # the sheet gets the barcode's value as text
                item["value"], item["format"] = eng.evaluate(element, row or {}), None
            elif element["type"] in ("staticText", "textField"):
                if element.get("_value") is not None and not isinstance(element["_value"], bool):  # a table total / crosstab cell
                    item["value"], item["format"] = element["_value"], _numeric_format(element.get("_pattern", ""))
                    item["text"] = element.get("text", "")
                elif element["type"] == "staticText":
                    item["value"], item["format"] = markup_to_text(element.get("text", ""), element.get("markup", "none")), None
                else:
                    item["value"], item["format"] = _cell_value(eng, element, row or {})
                    item["text"] = markup_to_text(eng.evaluate(element, row or {}), element.get("markup", "none"))  # CSV: as printed
            elif element["type"] == "image" and not element.get("src"):
                continue
            self.items.append(item)
        self.cursor += height

    def collect(self) -> list[dict[str, Any]]:
        self.run(_NullCanvas())
        bands = self.bands
        if not self.early_exit:
            self.skip.clear()
            self.total_pages = self.page_number  # one sheet: "Page X of Y" shows the last page of Y
            self.record(bands["columnFooter"], None)
            footer = bands["lastPageFooter"] if band_present(bands.get("lastPageFooter")) else bands["pageFooter"]
            self.record(footer, None)
        return self.items


def _edges(values: Iterable[float]) -> list[float]:
    """Sorted cell boundaries; edges closer than 2pt are merged so elements placed 1-2pt apart share a row/column
    and no hair-thin rows/columns appear."""
    edges: list[float] = []
    for value in sorted(set(round(v, 1) for v in values)):
        if not edges or value - edges[-1] >= 2.0:
            edges.append(value)
    return edges


def _grid(design, title, columns, rows, parameters):
    items = _GridCollector(design, title, columns, rows, parameters).collect()
    xs = _edges([0.0] + [i["x"] for i in items] + [i["x"] + i["w"] for i in items])
    ys = _edges([0.0] + [i["y"] for i in items] + [i["y"] + i["h"] for i in items])

    def index(edges: list[float], value: float) -> int:
        position = bisect.bisect_right(edges, round(value, 1) + 0.5) - 1
        return max(0, min(position, len(edges) - 1))

    for item in items:
        item["c1"], item["r1"] = index(xs, item["x"]), index(ys, item["y"])
        item["c2"] = max(item["c1"], index(xs, item["x"] + item["w"]) - 1)
        item["r2"] = max(item["r1"], index(ys, item["y"] + item["h"]) - 1)
    return items, xs, ys


def render_grid_csv(design, title, columns, rows, parameters) -> bytes:
    """Jasper-style CSV: every printed text (title, headings, details, totals) in its grid position."""
    import csv
    items, xs, _ys = _grid(design, title, columns, rows, parameters)
    cells: dict[tuple[int, int], Any] = {}
    for item in items:
        shown = item.get("text", item.get("value"))  # formatted like the PDF (e.g. 25.00%)
        if "value" in item and (item["r1"], item["c1"]) not in cells and str(shown if shown is not None else "").strip():
            cells[(item["r1"], item["c1"])] = shown
    used_columns = sorted({c for _r, c in cells})
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    for r in sorted({r for r, _c in cells}):
        writer.writerow(["" if cells.get((r, c)) is None else csv_safe(cells.get((r, c))) for c in used_columns])
    return out.getvalue().encode("utf-8-sig")


def render_grid_xlsx(design, title, columns, rows, parameters) -> bytes:
    """Jasper-style XLSX: the page layout in cells (merged where an element spans several), with fonts,
    colours, borders, lines and images — so the sheet looks like the PDF, headings included."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    items, xs, ys = _grid(design, title, columns, rows, parameters)
    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    sheet.sheet_view.showGridLines = False
    for c in range(1, len(xs)):
        sheet.column_dimensions[get_column_letter(c)].width = max(0.5, (xs[c] - xs[c - 1]) / 5.25)
    for r in range(1, len(ys)):
        sheet.row_dimensions[r].height = max(1.0, ys[r] - ys[r - 1])
    _paint_grid(sheet, items, ys)
    sheet.page_setup.orientation = "landscape" if design["page"]["width"] > design["page"]["height"] else "portrait"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    neutralize_formulas(sheet)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


HEAD_BANDS = ("title", "pageHeader", "columnHeader")


def render_mixed_xlsx(design, title, columns, rows, parameters) -> bytes:
    """Headings like the PDF, data as a table. The Title, Page Header and Column Header are placed exactly as designed
    (merged cells, fonts, colours, borders, images: e.g. a report-number box on the right); then every record is one
    row in the columns of the Detail band, with a filter on the heading row and the panes frozen below it, so the data
    can still be sorted and filtered in Excel."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    bands = design["bands"]
    every = [bands[key] for key in BAND_KEYS if bands.get(key)] + [b for side in ("groupHeaders", "groupFooters") for b in bands[side].values()]
    collector = _GridCollector(design, title, columns, rows, parameters)
    collector.skip = {id(band) for band in every if not any(band is bands[key] for key in HEAD_BANDS)}
    collector.run(_NullCanvas())                      # records only the heading bands (the rest is skipped cheaply)
    head = collector.items
    engine, layout, data = _tabular(design, title, columns, rows, parameters)
    detail = [col["element"] for col in layout]
    # every heading element gets its own exact cell edges (pixel-perfect, e.g. "Report ID:" and its value side by
    # side in a box on the right); a data column that a heading edge cuts through is merged across those cells
    xs = _edges([0.0] + [e["x"] for e in detail] + [e["x"] + e["width"] for e in detail]
                + [i["x"] for i in head] + [i["x"] + i["w"] for i in head])
    ys = _edges([0.0] + [i["y"] for i in head] + [i["y"] + i["h"] for i in head])

    def index(edges: list[float], value: float) -> int:
        position = bisect.bisect_right(edges, round(value, 1) + 0.5) - 1
        return max(0, min(position, len(edges) - 1))

    for item in head:                                  # each heading element covers the data columns under it
        item["c1"], item["r1"] = index(xs, item["x"]), index(ys, item["y"])
        item["c2"] = max(item["c1"], index(xs, item["x"] + item["w"]) - 1)
        item["r2"] = max(item["r1"], index(ys, item["y"] + item["h"]) - 1)
    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    for c in range(1, len(xs)):
        sheet.column_dimensions[get_column_letter(c)].width = max(1.0, (xs[c] - xs[c - 1]) / 5.25)
    for r in range(1, len(ys)):
        sheet.row_dimensions[r].height = max(1.0, ys[r] - ys[r - 1])
    _paint_grid(sheet, head, ys)
    header_ids = {id(e) for e in bands["columnHeader"]["elements"]}
    heading_rows = [item["r2"] + 1 for item in head if id(item["element"]) in header_ids]
    first_data = max(len(ys), 1)                      # the row below the headings (1-based)
    places = [index(xs, e["x"]) + 1 for e in detail]
    ends = [max(place, index(xs, e["x"] + e["width"])) for place, e in zip(places, detail)]
    for offset, record in enumerate(data):
        row_number = first_data + offset
        for place, end, element, (value, number_format) in zip(places, ends, detail, record):
            cell = sheet.cell(row=row_number, column=place, value=value)
            _xlsx_style(cell, element)
            if number_format:
                cell.number_format = number_format
            if end > place:                            # same size on every row, so Excel can still sort
                for extra in range(place + 1, end + 1):
                    _xlsx_style(sheet.cell(row=row_number, column=extra), element)
                sheet.merge_cells(start_row=row_number, start_column=place, end_row=row_number, end_column=end)
    if heading_rows and detail:
        header_row = max(heading_rows)
        last = get_column_letter(max(ends))
        sheet.auto_filter.ref = f"{get_column_letter(min(places))}{header_row}:{last}{max(header_row, first_data + len(data) - 1)}"
        sheet.freeze_panes = sheet.cell(row=first_data, column=1)
        single_sheet_print_setup(sheet, header_row)
    neutralize_formulas(sheet)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def _paint_grid(sheet, items: list[dict[str, Any]], ys: list[float], row_offset: int = 0) -> None:
    """Put grid items (with r1/c1/r2/c2) on the sheet: fills, borders, lines, texts (merged across their span) and
    images. row_offset moves everything down (rows above are used for something else)."""
    from openpyxl.styles import Border, PatternFill, Side
    from openpyxl.utils import get_column_letter
    if row_offset:
        items = [{**item, "r1": item["r1"] + row_offset, "r2": item["r2"] + row_offset} for item in items]
        ys = [ys[0]] * row_offset + ys
    occupied: set[tuple[int, int]] = set()
    images = 0

    def side_for(element):
        return Side(style="thin" if (element.get("borderWidth") or 1) <= 1 else "medium", color=(element.get("borderColor") or element.get("color") or "#000000").lstrip("#"))

    def add_border(r1, c1, r2, c2, **sides):
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                cell = sheet.cell(row=r + 1, column=c + 1)
                current = cell.border
                cell.border = Border(left=sides.get("left") if c == c1 and "left" in sides else current.left,
                                     right=sides.get("right") if c == c2 and "right" in sides else current.right,
                                     top=sides.get("top") if r == r1 and "top" in sides else current.top,
                                     bottom=sides.get("bottom") if r == r2 and "bottom" in sides else current.bottom)

    # backgrounds first (rectangles and filled text boxes), then lines, texts and images on top
    for item in items:
        element = item["element"]
        if element["type"] in ("rectangle", "ellipse", "frame", "staticText", "textField", "barcode") and element.get("backColor"):
            fill = PatternFill("solid", start_color=element["backColor"].lstrip("#"), end_color=element["backColor"].lstrip("#"))
            for r in range(item["r1"], item["r2"] + 1):
                for c in range(item["c1"], item["c2"] + 1):
                    sheet.cell(row=r + 1, column=c + 1).fill = fill
        if element["type"] in ("rectangle", "ellipse", "frame", "staticText", "textField", "barcode") and element.get("borderWidth"):
            side = side_for(element)
            add_border(item["r1"], item["c1"], item["r2"], item["c2"], left=side, right=side, top=side, bottom=side)
        if element.get("borders"):
            sides = {name: side_for({**element, "borderWidth": width}) for name, width in element["borders"].items() if width}
            if sides:
                add_border(item["r1"], item["c1"], item["r2"], item["c2"], **sides)
        if element["type"] == "line":
            side = side_for(element)
            if item["w"] >= item["h"]:  # horizontal: the top edge of the row it starts on
                row_index = bisect.bisect_left(ys, round(item["y"], 1) - 0.5)
                if row_index < len(ys) - 1:
                    add_border(row_index, item["c1"], row_index, item["c2"], top=side)
                elif row_index > 0:
                    add_border(row_index - 1, item["c1"], row_index - 1, item["c2"], bottom=side)
            else:  # vertical: the left edge of the column it starts on
                add_border(item["r1"], item["c1"], item["r2"], item["c1"], left=side)
    for item in items:
        element = item["element"]
        if "value" in item:
            r1, c1, r2, c2 = item["r1"], item["c1"], item["r2"], item["c2"]
            span = {(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)}
            if (r1, c1) in occupied:
                continue
            cell = sheet.cell(row=r1 + 1, column=c1 + 1, value=item["value"])
            _xlsx_style(cell, {**element, "backColor": None, "borderWidth": 0})
            if element.get("backColor"):
                cell.fill = PatternFill("solid", start_color=element["backColor"].lstrip("#"), end_color=element["backColor"].lstrip("#"))
            if item.get("format"):
                cell.number_format = item["format"]
            if len(span) > 1 and not (span & occupied):
                sheet.merge_cells(start_row=r1 + 1, start_column=c1 + 1, end_row=r2 + 1, end_column=c2 + 1)
                occupied |= span
            else:
                occupied.add((r1, c1))
        elif element["type"] == "image" and images < 500:
            try:
                from openpyxl.drawing.image import Image as SheetImage
                data = base64.b64decode(IMAGE_RE.fullmatch(element["src"]).group(2))
                picture = SheetImage(io.BytesIO(data))
                ratio = min(item["w"] / max(picture.width * 0.75, 1), item["h"] / max(picture.height * 0.75, 1))
                picture.width, picture.height = picture.width * ratio, picture.height * ratio  # points → pixels via 0.75
                sheet.add_image(picture, f"{get_column_letter(item['c1'] + 1)}{item['r1'] + 1}")
                images += 1
            except Exception:  # noqa: BLE001 - Pillow missing or unreadable image: leave the cells empty
                pass


EXCEL_ROTATION = {"Left": 90, "Right": 180}  # Excel: 90 = up, 180 = down (upside down is not possible)


def _xlsx_style(cell, element) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    font_name = element.get("fontName", "SansSerif")
    cell.font = Font(name=EXCEL_FONT.get(font_name, font_name), size=max(6, min(element["fontSize"], 40)), bold=element["bold"],
                     italic=element["italic"], underline="single" if element.get("underline") else None,
                     color=element["color"].lstrip("#"))
    horizontal = "justify" if element["align"] == "justified" else element["align"]
    cell.alignment = Alignment(horizontal=horizontal, vertical={"top": "top", "middle": "center", "bottom": "bottom"}[element.get("vAlign", "top")], wrap_text=True,
                               text_rotation=EXCEL_ROTATION.get(element.get("rotation", "None"), 0))
    if element["backColor"] and not element.get("transparent"):
        cell.fill = PatternFill("solid", start_color=element["backColor"].lstrip("#"), end_color=element["backColor"].lstrip("#"))
    if element["borderWidth"]:
        side = Side(style="thin", color=(element["borderColor"] or "#000000").lstrip("#"))
        cell.border = Border(left=side, right=side, top=side, bottom=side)


def render_design_csv(design, title, columns, rows, parameters) -> bytes:
    import csv
    if design["page"].get("sheetLayout") == "grid":
        return render_grid_csv(design, title, columns, rows, parameters)
    _engine, layout, data = _tabular(design, title, columns, rows, parameters)
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow([csv_safe(col["label"]) for col in layout])
    for record in data:
        writer.writerow(["" if value is None else csv_safe(value) for value, _fmt in record])
    return out.getvalue().encode("utf-8-sig")


CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> Any:
    """Prefix text that a spreadsheet would run as a formula with an apostrophe (OWASP CSV injection).

    Only strings change; real numbers (e.g. -5) stay numbers. Text such as "-5" from MongoDB is
    still prefixed, because Excel would otherwise evaluate it.
    """
    if isinstance(value, str) and value.startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def neutralize_formulas(sheet) -> None:
    """Keep every cell literal text/number: openpyxl turns any string starting with '=' into a live formula.

    Report data comes from MongoDB, so a value such as '=HYPERLINK(...)' must never execute in Excel.
    No renderer here writes formulas on purpose, so all formula cells are switched back to strings.
    """
    for row in sheet.iter_rows():
        for cell in row:
            if cell.data_type == "f":
                cell.data_type = "s"


def single_sheet_print_setup(sheet, header_row: int) -> None:
    """All data stays on one worksheet; when printed it fits one page wide (rows continue down)
    and the column header repeats, instead of being split into PDF-style pages."""
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.page_setup.orientation = "landscape"
    sheet.print_title_rows = f"{header_row}:{header_row}"


def render_design_xlsx(design, title, columns, rows, parameters) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("XLSX export needs: pip install openpyxl") from exc
    if design["page"].get("sheetLayout") == "grid":
        return render_grid_xlsx(design, title, columns, rows, parameters)
    if design["page"].get("sheetLayout") == "mixed":
        return render_mixed_xlsx(design, title, columns, rows, parameters)
    engine, layout, data = _tabular(design, title, columns, rows, parameters)
    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    last_col = max(len(layout), 1)

    def style_cell(cell, element) -> None:
        font_name = element.get("fontName", "SansSerif")
        cell.font = Font(name=EXCEL_FONT.get(font_name, font_name), size=max(6, min(element["fontSize"], 40)), bold=element["bold"],
                         italic=element["italic"], underline="single" if element.get("underline") else None,
                         color=element["color"].lstrip("#"))
        horizontal = "justify" if element["align"] == "justified" else element["align"]
        cell.alignment = Alignment(horizontal=horizontal, vertical={"top": "top", "middle": "center", "bottom": "bottom"}[element.get("vAlign", "top")], wrap_text=True,
                                   text_rotation=EXCEL_ROTATION.get(element.get("rotation", "None"), 0))
        if element["backColor"] and not element.get("transparent"):
            cell.fill = PatternFill("solid", start_color=element["backColor"].lstrip("#"), end_color=element["backColor"].lstrip("#"))
        if element["borderWidth"]:
            side = Side(style="thin", color=(element["borderColor"] or "#000000").lstrip("#"))
            cell.border = Border(left=side, right=side, top=side, bottom=side)

    row_number = 1
    # headings above the table: the Title band, then the Page Header band (each text on its own row)
    title_elements = [e for key in ("title", "pageHeader")
                      for e in sorted((e for e in design["bands"][key]["elements"] if e["type"] in ("staticText", "textField")), key=lambda e: (e["y"], e["x"]))]
    for element in title_elements:
        text = element.get("text", "") if element["type"] == "staticText" else engine.evaluate(element, None)
        text = markup_to_text(text, element.get("markup", "none"))
        if not str(text).strip():
            continue
        cell = sheet.cell(row=row_number, column=1, value=text)
        style_cell(cell, {**element, "backColor": element["backColor"], "borderWidth": 0})
        if last_col > 1:
            sheet.merge_cells(start_row=row_number, start_column=1, end_row=row_number, end_column=last_col)
        sheet.row_dimensions[row_number].height = max(18, min(element["height"], 80) * 0.75)
        row_number += 1
    header_row = row_number
    for index, col in enumerate(layout, start=1):
        cell = sheet.cell(row=header_row, column=index, value=col["label"])
        style_cell(cell, col["header"] or {**col["element"], "bold": True, "backColor": "#e2e8f0", "borderWidth": 0.5, "borderColor": "#000000"})
        sheet.column_dimensions[get_column_letter(index)].width = max(8, min(60, col["element"]["width"] / 6.2))
    sheet.row_dimensions[header_row].height = max(16, design["bands"]["columnHeader"]["height"] * 0.75)
    for record in data:
        row_number += 1
        for index, (value, number_format) in enumerate(record, start=1):
            cell = sheet.cell(row=row_number, column=index, value=value)
            style_cell(cell, layout[index - 1]["element"])
            if number_format:
                cell.number_format = number_format
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)
    single_sheet_print_setup(sheet, header_row)
    neutralize_formulas(sheet)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
