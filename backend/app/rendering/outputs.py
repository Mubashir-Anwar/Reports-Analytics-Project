"""Output files: simple table PDF/XLSX/CSV and the band-designer outputs (render_report_output)."""
from __future__ import annotations

import base64
import csv
import io
import json
from datetime import date
from html import escape as html_escape
from typing import Any

from backend.app.definitions import ReportDefinition, _safe_value
from backend.app.logs import LOG
from backend.app.rendering import engine as designer_render


def _export_cell_value(value: Any) -> Any:
    value = _safe_value(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def render_csv(columns: tuple[str, ...], rows: list[dict[str, Any]], column_labels: dict[str, str] | None = None) -> bytes:
    labels = column_labels or {}
    buffer = io.StringIO(newline="")
    display_columns = [labels.get(key, key) for key in columns]
    writer = csv.writer(buffer)
    writer.writerow([designer_render.csv_safe(name) for name in display_columns])
    for row in rows:
        writer.writerow([designer_render.csv_safe(_export_cell_value(row.get(key, ""))) for key in columns])
    return buffer.getvalue().encode("utf-8-sig")


def render_xlsx(title: str, intro: str, paragraphs: list[str], columns: tuple[str, ...], rows: list[dict[str, Any]], column_labels: dict[str, str] | None = None, show_title: bool = True, show_intro: bool = True) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("Install openpyxl for XLSX output") from exc
    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    if show_title:
        sheet.append([title])
    for text in [intro if show_intro else "", *paragraphs]:
        if text:
            sheet.append([text])
    labels = column_labels or {}
    display_columns = [labels.get(key, key) for key in columns]
    heading_row = sheet.max_row + 1
    sheet.append(display_columns)
    for cell in sheet[heading_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17324D")
    for row in rows:
        sheet.append([_export_cell_value(row.get(key, "")) for key in columns])
    sheet.freeze_panes = f"A{heading_row + 1}"
    if sheet.max_column:
        sheet.auto_filter.ref = f"A{heading_row}:{get_column_letter(sheet.max_column)}{sheet.max_row}"
        for index, name in enumerate(display_columns, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = min(max(len(name) + 3, 12), 36)
    designer_render.single_sheet_print_setup(sheet, heading_row)
    designer_render.neutralize_formulas(sheet)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def render_pdf(title: str, intro: str, paragraphs: list[str], image_data_url: str, report_id: str, columns: tuple[str, ...], rows: list[dict[str, Any]], date_range: str, column_labels: dict[str, str] | None = None, show_title: bool = True, show_intro: bool = True, positions: dict[str, Any] | None = None, parameter_fields: list[dict[str, Any]] | None = None, parameter_values: dict[str, Any] | None = None) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Indenter, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("Install reportlab for PDF output") from exc
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=8 * mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("CanvasCell", parent=styles["BodyText"], fontName="Helvetica", fontSize=7, leading=8.5, spaceAfter=0))
    styles.add(ParagraphStyle("CanvasHeaderCell", parent=styles["CanvasCell"], fontName="Helvetica-Bold", textColor=colors.white))
    styles.add(ParagraphStyle("CanvasValue", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=13, textColor=colors.HexColor("#a9441c"), spaceAfter=0))
    position_map = positions or {
        "title": {"x": 64, "y": 70, "w": 666, "h": 54},
        "intro": {"x": 64, "y": 132, "w": 666, "h": 62},
        "image": {"x": 64, "y": 210, "w": 280, "h": 170},
        "table": {"x": 64, "y": 650, "w": 666, "h": 390},
    }
    page_width, page_height = A4
    scale = page_width / 794
    labels = column_labels or {}
    data = [[Paragraph(html_escape(str(labels.get(value, value))), styles["CanvasHeaderCell"]) for value in columns]]
    data.extend([[Paragraph(html_escape(str(_export_cell_value(row.get(key, "")))), styles["CanvasCell"]) for key in columns] for row in rows])
    table_position = position_map.get("table", {"x": 64, "y": 650, "w": 666, "h": 390})
    column_width = max(24, float(table_position.get("w", 666))) * scale / max(1, len(columns))
    table = Table(data, colWidths=[column_width] * len(columns), repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))

    def draw_text(canvas: Any, text: str, position: dict[str, Any], style: Any) -> None:
        if not text:
            return
        x = float(position.get("x", 0)) * scale
        y = float(position.get("y", 0)) * scale
        width = float(position.get("w", 200)) * scale
        height = float(position.get("h", 40)) * scale
        safe_text = html_escape(text).replace("\n", "<br/>")
        paragraph = Paragraph(safe_text, style)
        _, paragraph_height = paragraph.wrap(width, height)
        canvas.saveState()
        # ReportLab has no clipRect(); clip with a rectangular path so long text stays inside its box.
        clip = canvas.beginPath()
        clip.rect(x, page_height - y - height, width, height)
        canvas.clipPath(clip, stroke=0, fill=0)
        paragraph.drawOn(canvas, x, page_height - y - min(paragraph_height, height))
        canvas.restoreState()

    def draw_canvas(canvas: Any) -> None:
        canvas.saveState()
        if image_data_url:
            try:
                image_bytes = base64.b64decode(image_data_url.split(",", 1)[1], validate=True)
                reader = ImageReader(io.BytesIO(image_bytes))
                image_width, image_height = reader.getSize()
                image_position = position_map.get("image", {"x": 64, "y": 210, "w": 280, "h": 170})
                x, y = float(image_position["x"]) * scale, float(image_position["y"]) * scale
                width, height = float(image_position["w"]) * scale, float(image_position["h"]) * scale
                ratio = min(width / image_width, height / image_height)
                draw_width, draw_height = image_width * ratio, image_height * ratio
                canvas.drawImage(reader, x + (width - draw_width) / 2, page_height - y - height + (height - draw_height) / 2,
                                 width=draw_width, height=draw_height, mask="auto")
            except (ValueError, IndexError, OSError):
                LOG.exception("Canvas image could not be rendered")
        if show_title:
            draw_text(canvas, title, position_map.get("title", {}), styles["Title"])
        if show_intro:
            draw_text(canvas, intro, position_map.get("intro", {}), styles["Normal"])
        for index, text in enumerate(paragraphs):
            draw_text(canvas, text, position_map.get(f"paragraph-{index}", {"x": 64, "y": 220 + index * 96, "w": 666, "h": 78}), styles["Normal"])
        for field in parameter_fields or []:
            value = (parameter_values or {}).get(field.get("name", ""), "")
            value = _safe_value(value)
            position = field.get("position", {"x": 90, "y": 245, "w": 210, "h": 34})
            x, y = float(position["x"]) * scale, float(position["y"]) * scale
            width, height = float(position["w"]) * scale, float(position["h"]) * scale
            canvas.setFillColor(colors.HexColor("#fff8e8")); canvas.setStrokeColor(colors.HexColor("#bf542e"))
            canvas.roundRect(x, page_height - y - height, width, height, 3, stroke=1, fill=1)
            draw_text(canvas, str(value), position, styles["CanvasValue"])
        canvas.restoreState()

    def draw_footer(canvas: Any, _doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#667788"))
        canvas.drawString(12 * mm, 5 * mm, f"{report_id} · {date_range}")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(page_width - 12 * mm, 5 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()
    table_top = max(0, float(table_position.get("y", 650))) * scale
    table_left = max(0, float(table_position.get("x", 64))) * scale
    def draw_first_page(canvas: Any, doc_context: Any) -> None:
        draw_canvas(canvas)
        draw_footer(canvas, doc_context)

    doc.build([Spacer(1, table_top), Indenter(left=table_left), table, Indenter(left=-table_left)], onFirstPage=draw_first_page, onLaterPages=draw_footer)
    return output.getvalue()


MEDIA_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}


def render_report_output(definition: ReportDefinition, parameters: dict[str, Any], rows: list[dict[str, Any]], output_format: str,
                         subreport_loader: Any = None, dataset_loader: Any = None, link_base: str = "") -> tuple[bytes, str]:
    """Turn report rows into the requested file. Shared by /generate and the admin 'Run report'.
    `subreport_loader` fills the design's Subreport elements (each runs its own published report's query);
    `dataset_loader` fills Tables / Crosstabs that have their own dataset; `link_base` is this server's address for
    hyperlinks that open another report (REPORTING_PUBLIC_URL wins)."""
    token = designer_render.SUBREPORT_LOADER.set(subreport_loader)
    dataset_token = designer_render.DATASET_LOADER.set(dataset_loader)
    link_token = designer_render.LINK_BASE.set(link_base)
    try:
        return _render_report_output(definition, parameters, rows, output_format)
    finally:
        designer_render.LINK_BASE.reset(link_token)
        designer_render.DATASET_LOADER.reset(dataset_token)
        designer_render.SUBREPORT_LOADER.reset(token)


def _render_report_output(definition: ReportDefinition, parameters: dict[str, Any], rows: list[dict[str, Any]], output_format: str) -> tuple[bytes, str]:
    layout = (definition.editor_config or {}).get("layout", {})
    display_title = str(layout.get("title") or definition.title)
    display_intro = str(layout.get("intro") or "")
    display_paragraphs = layout.get("paragraphs", [])
    image_data_url = str(layout.get("imageDataUrl") or "")
    show_title = layout.get("showTitle", True) is True
    show_intro = layout.get("showIntro", True) is True
    column_labels = layout.get("columnLabels", {})
    date_parameter_names = [spec.name for spec in definition.parameters if spec.kind == "date" and spec.prompt]
    date_range = " to ".join(str(parameters[name]) for name in date_parameter_names[:2] if name in parameters)
    band_design = (definition.editor_config or {}).get("design")
    design_rows = [{key: _safe_value(value) for key, value in row.items()} for row in rows]
    design_parameters = {key: (value.isoformat() if isinstance(value, date) else value) for key, value in parameters.items()}
    use_design_table = bool(band_design) and designer_render.has_tabular_layout(band_design)
    if output_format == "csv":
        body = (designer_render.render_design_csv(band_design, display_title, list(definition.columns), design_rows, design_parameters)
                if use_design_table else render_csv(definition.columns, rows, column_labels))
    elif output_format == "xlsx":
        body = (designer_render.render_design_xlsx(band_design, display_title, list(definition.columns), design_rows, design_parameters)
                if use_design_table else
                render_xlsx(display_title, display_intro, display_paragraphs, definition.columns, rows, column_labels, show_title, show_intro))
    elif band_design:
        body = designer_render.render_design_pdf(band_design, display_title, list(definition.columns), design_rows, design_parameters, definition.report_id)
    else:
        body = render_pdf(
            display_title, display_intro, display_paragraphs, image_data_url,
            definition.report_id, definition.columns, rows, date_range,
            column_labels, show_title, show_intro, layout.get("positions", {}),
            layout.get("parameterFields", []), parameters,
        )
    return body, MEDIA_TYPES[output_format]
