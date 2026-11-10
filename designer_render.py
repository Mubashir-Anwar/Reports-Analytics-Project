"""Band-based (Jasper-style) report design: validation and PDF rendering.

A design is plain JSON data. Nothing in it is ever executed: expressions are
`$F{field}` / `$P{param}` / `$V{variable}` tokens plus a tiny, AST-checked
arithmetic subset. Layout units are PDF points (1 pt = 1 px in the designer).
"""
from __future__ import annotations

import ast
import base64
import io
import math
import operator
import os
import re
from datetime import date, datetime
from typing import Any

import jexpr

BAND_KEYS = ("title", "pageHeader", "columnHeader", "detail", "columnFooter", "pageFooter", "summary")
ELEMENT_TYPES = {"staticText", "textField", "line", "rectangle", "image", "chart"}
CALCULATIONS = {"Sum", "Count", "Average", "Min", "Max", "First"}
RESET_TYPES = {"Report", "Group"}
CHART_TYPES = {"bar", "line", "pie"}
BUILTIN_VARIABLES = {"PAGE_NUMBER", "PAGE_COUNT", "REPORT_COUNT", "CURRENT_DATE"}
MAX_ELEMENTS = 400
MAX_ROWS_RENDER = 50_000
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TOKEN_RE = re.compile(r"\$([FPV])\{([^{}]+)\}")
IMAGE_RE = re.compile(r"^data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)$")

DEFAULT_PAGE = {"width": 595, "height": 842, "margins": {"top": 30, "right": 30, "bottom": 30, "left": 30}}


# ----------------------------------------------------------------- validation
def _num(value: Any, name: str, low: float, high: float, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} number hona chahiye") from None
    if not math.isfinite(number) or number < low or number > high:
        raise ValueError(f"{name} {low:g} se {high:g} ke darmiyan hona chahiye")
    return number


def _color(value: Any, name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not COLOR_RE.fullmatch(value):
        raise ValueError(f"{name} #RRGGBB format mein hona chahiye")
    return value


def tokens_in(expression: str) -> list[tuple[str, str]]:
    return TOKEN_RE.findall(expression or "")


def validate_design(design: Any, columns: list[str], parameter_names: set[str]) -> dict[str, Any]:
    """Return a normalized copy of `design` or raise ValueError (Roman Urdu message)."""
    if not isinstance(design, dict):
        raise ValueError("design JSON object hona chahiye")
    raw_page = design.get("page") or {}
    margins = raw_page.get("margins") or {}
    page = {
        "width": _num(raw_page.get("width"), "page.width", 100, 14400, 595),
        "height": _num(raw_page.get("height"), "page.height", 100, 14400, 842),
        "margins": {side: _num(margins.get(side), f"margins.{side}", 0, 300, 30)
                    for side in ("top", "right", "bottom", "left")},
        "ignorePagination": raw_page.get("ignorePagination") is True,
    }

    field_names = set(columns)
    calculated: list[dict[str, str]] = []
    for item in design.get("calculatedFields", []) or []:
        name = str(item.get("name", "")) if isinstance(item, dict) else ""
        if not NAME_RE.fullmatch(name) or name in field_names:
            raise ValueError(f"Calculated field ka naam durust aur unique hona chahiye: {name!r}")
        calculated.append({"name": name, "expression": str(item.get("expression", ""))[:500]})
        field_names.add(name)

    groups: list[dict[str, str]] = []
    for item in design.get("groups", []) or []:
        name = str(item.get("name", "")) if isinstance(item, dict) else ""
        field = str(item.get("field", "")) if isinstance(item, dict) else ""
        if not NAME_RE.fullmatch(name) or any(g["name"] == name for g in groups):
            raise ValueError(f"Group ka naam durust aur unique hona chahiye: {name!r}")
        if field not in field_names:
            raise ValueError(f"Group '{name}' ka field maujood nahi: {field!r}")
        groups.append({"name": name, "field": field})
    if len(groups) > 5:
        raise ValueError("Maximum 5 groups allowed hain")

    variables: list[dict[str, Any]] = []
    for item in design.get("variables", []) or []:
        if not isinstance(item, dict):
            raise ValueError("Har variable JSON object hona chahiye")
        name = str(item.get("name", ""))
        if not NAME_RE.fullmatch(name) or name in BUILTIN_VARIABLES or any(v["name"] == name for v in variables):
            raise ValueError(f"Variable ka naam durust aur unique hona chahiye: {name!r}")
        calculation = str(item.get("calculation", "Sum"))
        if calculation not in CALCULATIONS:
            raise ValueError(f"Variable '{name}' ki calculation {', '.join(sorted(CALCULATIONS))} mein se ho")
        field = str(item.get("field", ""))
        if calculation != "Count" and field not in field_names:
            raise ValueError(f"Variable '{name}' ka field maujood nahi: {field!r}")
        reset = str(item.get("resetType", "Report"))
        if reset not in RESET_TYPES:
            raise ValueError(f"Variable '{name}' ka resetType Report ya Group ho")
        reset_group = str(item.get("resetGroup", ""))
        if reset == "Group" and not any(g["name"] == reset_group for g in groups):
            raise ValueError(f"Variable '{name}' ka resetGroup maujood group nahi")
        variables.append({"name": name, "calculation": calculation, "field": field,
                          "resetType": reset, "resetGroup": reset_group if reset == "Group" else ""})
    variable_names = {v["name"] for v in variables} | BUILTIN_VARIABLES
    sort_spec: list[dict[str, str]] = []
    for item in design.get("sort", []) or []:
        if not isinstance(item, dict) or str(item.get("field", "")) not in field_names:
            raise ValueError("sort ka field maujood nahi")
        sort_spec.append({"field": str(item["field"]), "order": "Descending" if item.get("order") == "Descending" else "Ascending"})

    counter = {"elements": 0, "image_bytes": 0}

    def check_expression(expression: str, where: str) -> None:
        for kind, name in tokens_in(expression):
            known = {"F": field_names, "P": parameter_names, "V": variable_names}[kind]
            if name not in known:
                raise ValueError(f"{where}: ${kind}{{{name}}} define nahi hai")

    def clean_element(raw: Any, band_name: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError(f"{band_name}: har element JSON object hona chahiye")
        counter["elements"] += 1
        if counter["elements"] > MAX_ELEMENTS:
            raise ValueError(f"Maximum {MAX_ELEMENTS} elements allowed hain")
        kind = str(raw.get("type", ""))
        if kind not in ELEMENT_TYPES:
            raise ValueError(f"{band_name}: element type ghalat hai: {kind!r}")
        element: dict[str, Any] = {
            "id": re.sub(r"[^A-Za-z0-9_-]", "", str(raw.get("id", "")))[:80] or f"el-{counter['elements']}",
            "type": kind,
            "x": _num(raw.get("x"), "x", -2000, 15000, 0),
            "y": _num(raw.get("y"), "y", -2000, 15000, 0),
            "width": _num(raw.get("width"), "width", 1, 15000, 100),
            "height": _num(raw.get("height"), "height", 0, 5000, 20),
            "fontSize": _num(raw.get("fontSize"), "fontSize", 4, 96, 10),
            "bold": raw.get("bold") is True,
            "italic": raw.get("italic") is True,
            "align": raw.get("align") if raw.get("align") in ("left", "center", "right") else "left",
            "vAlign": raw.get("vAlign") if raw.get("vAlign") in ("top", "middle", "bottom") else "top",
            "color": _color(raw.get("color"), "color") or "#000000",
            "backColor": _color(raw.get("backColor"), "backColor"),
            "borderColor": _color(raw.get("borderColor"), "borderColor"),
            "borderWidth": _num(raw.get("borderWidth"), "borderWidth", 0, 20, 0),
        }
        if kind != "line" and element["height"] < 1:
            element["height"] = 1.0
        if kind == "staticText":
            element["text"] = str(raw.get("text", ""))[:2000]
        elif kind == "textField":
            element["expression"] = str(raw.get("expression", ""))[:1000]
            element["pattern"] = str(raw.get("pattern", ""))[:60]
            element["blankWhenNull"] = raw.get("blankWhenNull", True) is True
            check_expression(element["expression"], f"{band_name} textField")
        elif kind == "image":
            src = str(raw.get("src", ""))
            if src:
                match = IMAGE_RE.fullmatch(src)
                if not match:
                    raise ValueError(f"{band_name}: image PNG/JPEG data URL ho")
                try:
                    size = len(base64.b64decode(match.group(2), validate=True))
                except ValueError:
                    raise ValueError(f"{band_name}: image data valid nahi") from None
                counter["image_bytes"] += size
                if size > 1_500_000 or counter["image_bytes"] > 3_000_000:
                    raise ValueError("Images ka total size 3 MB se kam rakhein (har image 1.5 MB tak)")
            element["src"] = src
        elif kind == "chart":
            chart_type = str(raw.get("chartType", "bar"))
            if chart_type not in CHART_TYPES:
                raise ValueError(f"{band_name}: chartType bar, line ya pie ho")
            category, value = str(raw.get("categoryField", "")), str(raw.get("valueField", ""))
            aggregate = str(raw.get("aggregate", "Sum"))
            if category not in field_names or (aggregate != "Count" and value not in field_names):
                raise ValueError(f"{band_name}: chart ke categoryField/valueField maujood nahi")
            if aggregate not in {"Sum", "Count", "Average"}:
                raise ValueError(f"{band_name}: chart aggregate Sum, Count ya Average ho")
            element.update({"chartType": chart_type, "categoryField": category, "valueField": value,
                            "aggregate": aggregate, "title": str(raw.get("title", ""))[:120]})
        elif kind == "line":
            element["height"] = _num(raw.get("height"), "height", 0, 5000, 0) or 0.01
        return element

    def clean_band(raw: Any, name: str) -> dict[str, Any]:
        if raw is None:
            return {"height": 0, "elements": []}
        if not isinstance(raw, dict):
            raise ValueError(f"Band '{name}' JSON object hona chahiye")
        height = _num(raw.get("height"), f"{name}.height", 0, 15000, 0)
        elements = raw.get("elements", []) or []
        if not isinstance(elements, list):
            raise ValueError(f"Band '{name}' ke elements list hon")
        return {"height": height, "elements": [clean_element(item, name) for item in elements]}

    raw_bands = design.get("bands") or {}
    if not isinstance(raw_bands, dict):
        raise ValueError("design.bands JSON object hona chahiye")
    bands: dict[str, Any] = {key: clean_band(raw_bands.get(key), key) for key in BAND_KEYS}
    for side in ("groupHeaders", "groupFooters"):
        mapping = raw_bands.get(side) or {}
        if not isinstance(mapping, dict):
            raise ValueError(f"design.bands.{side} JSON object hona chahiye")
        bands[side] = {g["name"]: clean_band(mapping.get(g["name"]), f"{side}.{g['name']}") for g in groups}

    for item in calculated:
        check_expression(item["expression"], f"calculated field {item['name']}")

    return {"version": 1, "page": page, "calculatedFields": calculated, "groups": groups,
            "variables": variables, "sort": sort_spec, "bands": bands}


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
        raise ValueError("Expression mein sirf numbers, tokens aur + - * / % allowed hain")

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


def format_value(value: Any, pattern: str) -> str:
    if value is None:
        return ""
    if pattern:
        try:
            if re.search(r"yyyy|dd|MM|HH|hh", pattern):
                if isinstance(value, (datetime, date)) or _parse_datetime(value) is not None:
                    return jexpr.DateFormatter(pattern).format(value)
            elif re.search(r"[#0]", pattern) and not isinstance(value, bool) and _to_number(value) is not None:
                return jexpr.DecimalFormatter(pattern).format(value)
        except jexpr.JExprError:
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
            return ("ReportFont",) * 4
        except Exception:  # noqa: BLE001 - fall back to built-in fonts
            pass
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"


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

    def evaluate(self, element: dict[str, Any], row: dict[str, Any] | None) -> str:
        expression = element.get("expression", "")
        pattern = element.get("pattern", "")
        found = TOKEN_RE.fullmatch(expression.strip())
        if expression.lstrip().startswith("="):
            value: Any = eval_expression(expression.lstrip()[1:], lambda k, n: self.resolve(k, n, row))
            text = format_value(value, pattern)
        elif found:
            text = format_value(self.resolve(found.group(1), found.group(2), row), pattern)
        else:
            text = TOKEN_RE.sub(lambda m: format_value(self.resolve(m.group(1), m.group(2), row), pattern), expression)
        if text == "" and not element.get("blankWhenNull", True):
            return "null"
        return text

    # ---- drawing
    def font(self, element: dict[str, Any]) -> str:
        regular, bold, italic, both = self.fonts
        return both if element["bold"] and element["italic"] else bold if element["bold"] else italic if element["italic"] else regular

    def draw_band(self, canvas, band: dict[str, Any], top: float, row: dict[str, Any] | None, scope_rows=None) -> None:
        left = self.page["margins"]["left"]
        for element in band["elements"]:
            self.draw_element(canvas, element, left + element["x"], top + element["y"], row, scope_rows)

    def draw_element(self, canvas, element, x, y_top, row, scope_rows) -> None:
        height_page = self.page["height"]
        w, h = element["width"], element["height"]
        y = height_page - y_top - h  # bottom-left origin
        kind = element["type"]
        if kind == "rectangle" or (kind in ("staticText", "textField") and (element["backColor"] or element["borderWidth"])):
            canvas.saveState()
            if element["backColor"]:
                canvas.setFillColor(self.hex(element["backColor"]))
            if element["borderWidth"]:
                canvas.setStrokeColor(self.hex(element["borderColor"] or "#000000"))
                canvas.setLineWidth(element["borderWidth"])
            canvas.rect(x, y, w, h, stroke=1 if element["borderWidth"] else 0, fill=1 if element["backColor"] else 0)
            canvas.restoreState()
        if kind in ("staticText", "textField"):
            text = element.get("text", "") if kind == "staticText" else self.evaluate(element, row)
            self.draw_text(canvas, element, text, x, y, w, h)
        elif kind == "line":
            canvas.saveState()
            canvas.setStrokeColor(self.hex(element["borderColor"] or element["color"]))
            canvas.setLineWidth(element["borderWidth"] or 1)
            canvas.line(x, y + h, x + w, y)
            canvas.restoreState()
        elif kind == "image" and element.get("src"):
            from reportlab.lib.utils import ImageReader
            data = base64.b64decode(IMAGE_RE.fullmatch(element["src"]).group(2))
            canvas.drawImage(ImageReader(io.BytesIO(data)), x, y, w, h, preserveAspectRatio=True, anchor="c", mask="auto")
        elif kind == "chart":
            self.draw_chart(canvas, element, x, y, w, h)

    def draw_text(self, canvas, element, text, x, y, w, h) -> None:
        from reportlab.lib.utils import simpleSplit
        size, name, pad = element["fontSize"], self.font(element), 2
        lines: list[str] = []
        for paragraph in str(text).split("\n"):
            lines.extend(simpleSplit(paragraph, name, size, max(w - 2 * pad, 1)) or [""])
        leading = size * 1.2
        canvas.saveState()
        path = canvas.beginPath()
        path.rect(x, y, w, h)
        canvas.clipPath(path, stroke=0, fill=0)
        canvas.setFillColor(self.hex(element["color"]))
        canvas.setFont(name, size)
        block = len(lines) * leading
        if element.get("vAlign") == "middle":
            top_edge = y + (h + block) / 2
        elif element.get("vAlign") == "bottom":
            top_edge = y + block + pad
        else:
            top_edge = y + h - pad
        baseline = top_edge - size
        for line in lines:
            if baseline < y - size or baseline > y + h + size:
                baseline -= leading
                continue
            if element["align"] == "right":
                canvas.drawRightString(x + w - pad, baseline, line)
            elif element["align"] == "center":
                canvas.drawCentredString(x + w / 2, baseline, line)
            else:
                canvas.drawString(x + pad, baseline, line)
            baseline -= leading
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
            chart = VerticalBarChart() if element["chartType"] == "bar" else HorizontalLineChart()
            chart.x, chart.y, chart.width, chart.height = 40, 28, w - 55, h - 44 - title_h
            chart.data = [values]
            chart.categoryAxis.categoryNames = labels
            chart.categoryAxis.labels.fontSize = 7
            chart.categoryAxis.labels.angle = 30 if len(labels) > 5 else 0
            chart.categoryAxis.labels.boxAnchor = "ne" if len(labels) > 5 else "n"
            chart.valueAxis.labels.fontSize = 7
            chart.valueAxis.valueMin = min(0, min(values))
            series = self.hex(element["color"] if element["color"] != "#000000" else "#2563eb")
            if element["chartType"] == "bar":
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
        page_footer = self.bands["pageFooter"]["height"]
        column_footer = self.bands["columnFooter"]["height"]
        bottom_limit = height - margins["bottom"] - page_footer - column_footer
        state = {"y": top_limit, "first": True}

        def start_page(first: bool) -> None:
            self.page_number += 1
            self.pages_drawn += 1
            state["y"] = top_limit
            if first and self.bands["title"]["height"]:
                self.draw_band(canvas, self.bands["title"], state["y"], None)
                state["y"] += self.bands["title"]["height"]
            for key in ("pageHeader", "columnHeader"):
                self.draw_band(canvas, self.bands[key], state["y"], None)
                state["y"] += self.bands[key]["height"]

        def end_page() -> None:
            self.draw_band(canvas, self.bands["columnFooter"], bottom_limit, None)
            self.draw_band(canvas, self.bands["pageFooter"], height - margins["bottom"] - page_footer, None)
            canvas.showPage()

        def place(band: dict[str, Any], row: dict[str, Any] | None) -> None:
            if not band["height"] and not band["elements"]:
                return
            if state["y"] + band["height"] > bottom_limit and state["y"] > top_limit:
                end_page()
                start_page(False)
            self.draw_band(canvas, band, state["y"], row)
            state["y"] += band["height"]

        start_page(True)
        previous: list[Any] | None = None
        previous_row: dict[str, Any] | None = None
        rows = self.rows
        if self.groups:
            rows = sorted(rows, key=lambda r: tuple("" if r.get(g["field"]) is None else str(r.get(g["field"])) for g in self.groups))
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
            if previous is not None:
                for level in range(len(self.groups) - 1, changed_from - 1, -1):
                    name = self.groups[level]["name"]
                    place(self.bands["groupFooters"][name], previous_row)
                    self.reset_variables(name)
            for level in range(changed_from, len(self.groups)):
                place(self.bands["groupHeaders"][self.groups[level]["name"]], row)
            self.report_count += 1
            self.accumulate(row)
            place(self.bands["detail"], row)
            previous, previous_row = current, row
        if previous is not None:
            for level in range(len(self.groups) - 1, -1, -1):
                place(self.bands["groupFooters"][self.groups[level]["name"]], previous_row)
        place(self.bands["summary"], None)
        self.content_end = state["y"]
        end_page()


def render_design_pdf(design: dict[str, Any], title: str, columns: list[str], rows: list[dict[str, Any]],
                      parameters: dict[str, Any], report_id: str = "") -> bytes:
    """Render a validated design. Two passes so $V{PAGE_COUNT} is the real total."""
    try:
        from reportlab.pdfgen import canvas as pdf_canvas
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PDF export needs: pip install reportlab") from exc
    if len(rows) > MAX_ROWS_RENDER:
        raise ValueError(f"Designer PDF {MAX_ROWS_RENDER:,} rows tak support karta hai")
    if design["page"].get("ignorePagination"):
        # Jasper "Ignore pagination": one page as tall as the content. Measure first, then draw.
        probe = {**design, "page": {**design["page"], "height": 14400}}
        measure = _Render(probe, title, columns, rows, parameters, 1)
        measure.run(pdf_canvas.Canvas(io.BytesIO(), pagesize=(probe["page"]["width"], 14400)))
        if measure.pages_drawn == 1:
            extra = design["bands"]["columnFooter"]["height"] + design["bands"]["pageFooter"]["height"]
            height = min(14400, max(100, measure.content_end + extra + design["page"]["margins"]["bottom"]))
            design = {**design, "page": {**design["page"], "height": height}}
    size = (design["page"]["width"], design["page"]["height"])
    total = 0
    for _ in range(2):
        buffer = io.BytesIO()
        canvas = pdf_canvas.Canvas(buffer, pagesize=size)
        canvas.setTitle(title or report_id or "Report")
        engine = _Render(design, title, columns, rows, parameters, total)
        engine.run(canvas)
        canvas.save()
        total = engine.pages_drawn
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
            label = match.get("text") if match["type"] == "staticText" else match.get("expression", "")
        else:
            tokens = tokens_in(element.get("expression", ""))
            label = tokens[0][1] if tokens else (element.get("text") or "")
        columns.append({"label": str(label or ""), "element": element, "header": match})
    return columns


def has_tabular_layout(design: dict[str, Any]) -> bool:
    return any(e["type"] in ("textField", "staticText") for e in design["bands"]["detail"]["elements"])


def _numeric_format(pattern: str) -> str | None:
    match = re.match(r"^(.*?)([#0,]+(?:\.[#0]+)?)(.*)$", pattern or "")
    if not match:
        return None
    prefix, core, suffix = match.groups()
    return (f'"{prefix}"' if prefix else "") + core + (f'"{suffix}"' if suffix else "")


def _cell_value(engine: "_Render", element: dict[str, Any], row: dict[str, Any]) -> tuple[Any, str | None]:
    if element["type"] == "staticText":
        return element.get("text", ""), None
    expression = element.get("expression", "")
    pattern = element.get("pattern", "")
    found = TOKEN_RE.fullmatch(expression.strip())
    raw: Any = None
    if found:
        raw = engine.resolve(found.group(1), found.group(2), row)
    elif expression.lstrip().startswith("="):
        raw = eval_expression(expression.lstrip()[1:], lambda k, n: engine.resolve(k, n, row))
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and (not pattern or _numeric_format(pattern)) and not re.search(r"yyyy|dd|MM", pattern or ""):
        return raw, _numeric_format(pattern)
    return engine.evaluate(element, row), None


def _tabular(design, title, columns, rows, parameters):
    engine = _Render(design, title, columns, rows, parameters, 1)
    layout = _tabular_columns(design)
    data = []
    for row in engine.rows:
        engine.report_count += 1
        engine.accumulate(row)
        data.append([_cell_value(engine, col["element"], row) for col in layout])
    return engine, layout, data


def render_design_csv(design, title, columns, rows, parameters) -> bytes:
    import csv
    _engine, layout, data = _tabular(design, title, columns, rows, parameters)
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow([col["label"] for col in layout])
    for record in data:
        writer.writerow(["" if value is None else value for value, _fmt in record])
    return out.getvalue().encode("utf-8-sig")


def render_design_xlsx(design, title, columns, rows, parameters) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("XLSX export needs: pip install openpyxl") from exc
    engine, layout, data = _tabular(design, title, columns, rows, parameters)
    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    last_col = max(len(layout), 1)

    def style_cell(cell, element) -> None:
        cell.font = Font(name="Calibri", size=max(6, min(element["fontSize"], 40)), bold=element["bold"], italic=element["italic"],
                         color=element["color"].lstrip("#"))
        cell.alignment = Alignment(horizontal=element["align"], vertical={"top": "top", "middle": "center", "bottom": "bottom"}[element.get("vAlign", "top")], wrap_text=True)
        if element["backColor"]:
            cell.fill = PatternFill("solid", start_color=element["backColor"].lstrip("#"), end_color=element["backColor"].lstrip("#"))
        if element["borderWidth"]:
            side = Side(style="thin", color=(element["borderColor"] or "#000000").lstrip("#"))
            cell.border = Border(left=side, right=side, top=side, bottom=side)

    row_number = 1
    title_elements = sorted((e for e in design["bands"]["title"]["elements"] if e["type"] in ("staticText", "textField")), key=lambda e: (e["y"], e["x"]))
    for element in title_elements:
        text = element.get("text", "") if element["type"] == "staticText" else engine.evaluate(element, None)
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
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
