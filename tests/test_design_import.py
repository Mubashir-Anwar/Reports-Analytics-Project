"""File → Import design: PDF / picture → page, background and elements (backend/app/importers/design_import.py)."""
from __future__ import annotations

import base64
import io

import pytest

from backend.app.importers import design_import
from backend.app.importers.design_import import ImportProblem, import_design


def _data_url(raw: bytes, mime: str = "application/pdf") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _letter_pdf() -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=(595, 842))
    page.setFont("Helvetica-Bold", 18)
    page.setFillColor(HexColor("#1e3a8a"))
    page.drawString(60, 780, "Acme Traders")
    page.setFont("Times-Roman", 11)
    page.setFillColor(HexColor("#000000"))
    page.drawString(60, 700, "Dear {{CustomerName}},")
    page.drawString(60, 680, "Thank you for your order.")
    page.drawString(60, 666, "It will reach you soon.")
    page.setStrokeColor(HexColor("#94a3b8"))
    page.setLineWidth(1)
    page.line(60, 760, 535, 760)
    page.setFillColor(HexColor("#f1f5f9"))
    page.rect(60, 560, 200, 60, stroke=0, fill=1)
    page.showPage()
    page.drawString(60, 780, "Second page")
    page.save()
    return buffer.getvalue()


pdfplumber = pytest.importorskip("pdfplumber")


def test_pdf_rebuilds_text_lines_and_boxes():
    result = import_design(_data_url(_letter_pdf()), "letter.pdf")
    assert result["page"] == {"width": 595.0, "height": 842.0, "margins": {"top": 0, "right": 0, "bottom": 0, "left": 0}}
    assert result["pageCount"] == 2 and result["pageNumber"] == 1
    assert result["background"] == []
    by_type: dict[str, list] = {}
    for el in result["elements"]:
        by_type.setdefault(el["type"], []).append(el)
    title = next(e for e in by_type["staticText"] if e["text"] == "Acme Traders")
    assert title["bold"] and title["fontSize"] == 18 and title["color"] == "#1e3a8a"
    assert abs(title["x"] - 60) < 1 and abs(title["y"] - (842 - 780 - 18 * 0.75)) < 4
    body = next(e for e in by_type["staticText"] if e["text"].startswith("Thank you"))
    assert body["text"] == "Thank you for your order.\nIt will reach you soon."
    assert body["fontName"] == "Times New Roman" and body["lineSpacing"] == "Fixed"
    field = by_type["textField"][0]
    assert field["expression"] == "Dear $F{CustomerName},"
    assert result["fields"] == ["CustomerName"]
    assert any(e["type"] == "line" and e["width"] > 400 for e in result["elements"])
    assert any(e["type"] == "rectangle" and e["backColor"] == "#f1f5f9" for e in result["elements"])
    assert any("Page 1 of 2" in n for n in result["notes"])


def test_pdf_second_page_and_picture_mode():
    result = import_design(_data_url(_letter_pdf()), "letter.pdf", mode="picture", page_number=2)
    assert result["pageNumber"] == 2 and result["mode"] == "picture"
    picture = result["background"][0]
    assert picture["type"] == "image" and picture["src"].startswith("data:image/") and picture["width"] == 595.0
    text = result["elements"][0]
    assert text["text"] == "Second page" and text["backColor"] == "#ffffff"


def test_picture_without_text_on_top_has_only_the_background():
    result = import_design(_data_url(_letter_pdf()), "letter.pdf", mode="picture", text_on_top=False)
    assert result["elements"] == [] and len(result["background"]) == 1


def test_image_is_the_background_at_a4_width(monkeypatch):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 800), "white").save(buffer, "PNG")
    monkeypatch.setattr(design_import, "_ocr_lines", lambda image, notes: [("Certificate", (100.0, 100.0, 500.0, 160.0))])
    result = import_design(_data_url(buffer.getvalue(), "image/png"), "scan.png")
    assert result["kind"] == "image" and result["page"]["width"] == 842.0 and result["page"]["height"] == 561.3
    text = result["elements"][0]
    assert text["text"] == "Certificate" and text["backColor"] == "#ffffff"
    assert abs(text["x"] - 100 * 842 / 1200) < 0.2


@pytest.mark.parametrize(("raw", "name", "message"), [
    (b"", "x.pdf", "Choose a file"),
    (b"hello", "notes.txt", "not supported"),
    (b"%!PS-Adobe-3.0 EPSF", "logo.ai", "PDF Compatible"),
    (b"%PDF-1.4 broken", "bad.pdf", "could not be read"),
])
def test_bad_files_get_a_clear_message(raw, name, message):
    with pytest.raises(ImportProblem, match=message):
        import_design(_data_url(raw) if raw else "", name)


def test_too_many_pieces_are_cut_to_the_limit():
    elements = [{"type": "staticText", "text": str(i), "x": 1.0, "y": 1.0, "width": 5.0, "height": 5.0} for i in range(500)]
    notes: list[str] = []
    result = design_import._result(100, 100, elements, None, 1, 1, notes)
    assert len(result["elements"]) == design_import.MAX_ELEMENTS and "left out" in notes[0]


def _as_design(result: dict) -> dict:
    """What the designer's “Apply to the page” builds (frontend/src/ImportDesignDialog.jsx designFromImport)."""
    base = {"fontSize": 10, "fontName": "SansSerif", "bold": False, "italic": False, "underline": False, "align": "left",
            "vAlign": "top", "color": "#000000", "markup": "none"}
    place = lambda items: [{**base, "id": f"el-{i}-{e['type']}", **e} for i, e in enumerate(items)]  # noqa: E731
    height = result["page"]["height"]
    return {"version": 1, "page": {**result["page"], "sheetLayout": "grid"},
            "bands": {"background": {"height": height if result["background"] else 0, "elements": place(result["background"])},
                      "detail": {"height": height, "elements": place(result["elements"])}}}


@pytest.mark.parametrize("mode", ["elements", "picture"])
def test_imported_design_is_accepted_and_prints_the_field(mode):
    from backend.app.rendering import engine
    result = import_design(_data_url(_letter_pdf()), "letter.pdf", mode=mode)
    cleaned = engine.validate_design(_as_design(result), ["CustomerName"], set())
    pdf = engine.render_design_pdf(cleaned, "Letter", ["CustomerName"], [{"CustomerName": "Ali Khan"}], {})
    text = "".join(p.extract_text() or "" for p in pdfplumber.open(io.BytesIO(pdf)).pages)
    assert "Dear Ali Khan," in text and "Acme Traders" in text
