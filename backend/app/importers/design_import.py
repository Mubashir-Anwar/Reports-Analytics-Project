"""Import an existing design (PDF, Illustrator .ai, Word, PNG/JPEG scan) as a report layout.

Two ways (``mode``):

* ``elements`` (PDF / AI / Word): every piece of text becomes a Static Text at the same place, size, weight and
  colour; lines, boxes and pictures become line / rectangle / image elements. Sharp and fully editable.
* ``picture``: the page is put in the Background band exactly as it looks, and (``text_on_top``) each piece of
  text is laid over it as an editable box whose background is the picture's own colour there, so it can later be
  turned into a field. Images (scans, photos) always use this way; their text is found with OCR.

Placeholders such as ``{{Name}}``, ``«Name»``, ``<<Name>>`` or ``$F{Name}`` become Text Fields of that field.
Nothing is saved: the result is a design the designer applies. Packages: pdfplumber (+ pypdfium2) for PDF,
rapidocr + onnxruntime for OCR, Pillow; Word files are turned into PDF by MS Word (Windows) or LibreOffice.
"""
from __future__ import annotations

import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
from statistics import median
from typing import Any

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_ELEMENTS = 380                       # the design allows 400; room for the user's own fields
MAX_PICTURE_BYTES = 2_000_000            # one image element may hold 2.5 MB
PICTURE_DPI = 150
WORD_TIMEOUT = 120

PLACEHOLDER_RE = re.compile(
    r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}|«\s*([A-Za-z_][\w.]*)\s*»|<<\s*([A-Za-z_][\w.]*)\s*>>|\$F\{\s*([A-Za-z_][\w.]*)\s*\}")
FIELD_NAME_RE = re.compile(r"^[A-Za-z_][\w.]{0,79}$")


class ImportProblem(ValueError):
    """A message for the user: what is wrong with the file and what to do."""


# ------------------------------------------------------------------ entry point
def import_design(data_url: str, name: str = "", mode: str = "", page_number: int = 1, text_on_top: bool = True) -> dict[str, Any]:
    raw = _decode(data_url)
    kind = _kind_of(raw, name)
    notes: list[str] = []
    if kind == "word":
        raw = _word_to_pdf(raw, name)
        notes.append("The Word file was turned into PDF first (by MS Word / LibreOffice); fonts it does not have are replaced.")
        kind = "pdf"
    if kind == "pdf":
        mode = mode if mode in ("elements", "picture") else "elements"
        result = _from_pdf(raw, mode, page_number, text_on_top, notes)
    else:
        result = _from_image(raw, text_on_top, notes)
        mode = "picture"
    result.update({"kind": kind if kind != "pdf" or not name.lower().endswith(".ai") else "illustrator", "mode": mode, "notes": notes})
    return result


def _decode(data_url: str) -> bytes:
    if not isinstance(data_url, str) or not data_url.strip():
        raise ImportProblem("Choose a file to import (PDF, Illustrator .ai, Word .docx, PNG or JPEG)")
    payload = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError:
        raise ImportProblem("The file could not be read (it was not sent as base64)") from None
    if not raw:
        raise ImportProblem("The file is empty")
    if len(raw) > MAX_FILE_BYTES:
        raise ImportProblem(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB. Save a smaller copy (fewer pages, smaller pictures) and import that.")
    return raw


def _kind_of(raw: bytes, name: str) -> str:
    lower = name.lower()
    if raw[:1024].find(b"%PDF") != -1:
        return "pdf"                                   # PDF, and Illustrator files saved "PDF compatible"
    if raw.startswith(b"PK") and lower.endswith((".docx", ".docm", ".dotx", ".odt", ".rtf")) or raw.startswith(b"{\\rtf"):
        return "word"
    if raw.startswith(b"\xd0\xcf\x11\xe0") and lower.endswith((".doc", ".dot")):
        return "word"
    if raw.startswith(b"PK"):
        if lower.endswith((".docx", ".odt")) or b"word/" in raw[:4000]:
            return "word"
        raise ImportProblem("This is a ZIP-type file that is not a Word document. Import a PDF, .ai, .docx, PNG or JPEG.")
    if raw.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM")) or raw[8:12] == b"WEBP":
        return "image"
    if raw.startswith(b"%!PS") or lower.endswith((".ai", ".eps")):
        raise ImportProblem("This Illustrator file was saved without “Create PDF Compatible File”. In Illustrator use "
                            "File → Save As with that option on, or File → Save a Copy as PDF, and import that file.")
    if lower.endswith(".svg") or raw.lstrip()[:5] in (b"<?xml", b"<svg "):
        raise ImportProblem("SVG is not supported yet. Export it as PDF (or PNG) and import that.")
    raise ImportProblem("This file type is not supported. Import a PDF, Illustrator .ai, Word .docx, PNG or JPEG.")


# ------------------------------------------------------------------ Word → PDF
def _word_to_pdf(raw: bytes, name: str) -> bytes:
    suffix = os.path.splitext(name)[1].lower() or ".docx"
    if suffix not in (".docx", ".doc", ".docm", ".dotx", ".dot", ".odt", ".rtf"):
        suffix = ".docx"
    with tempfile.TemporaryDirectory(prefix="design-import-") as folder:
        source = os.path.join(folder, "document" + suffix)
        target = os.path.join(folder, "document.pdf")
        with open(source, "wb") as handle:
            handle.write(raw)
        office = shutil.which("soffice") or shutil.which("libreoffice")
        tried = []
        if os.name == "nt":
            tried.append("MS Word")
            _word_com(source, target)
        if not os.path.exists(target) and office:
            tried.append("LibreOffice")
            try:
                subprocess.run([office, "--headless", "--convert-to", "pdf", "--outdir", folder, source],
                               capture_output=True, timeout=WORD_TIMEOUT, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if not os.path.exists(target):
            how = " and ".join(tried) if tried else "MS Word or LibreOffice"
            raise ImportProblem(f"The Word file could not be turned into PDF here (tried: {how}). "
                                "Open it in Word, use File → Save As → PDF, and import the PDF.")
        with open(target, "rb") as handle:
            return handle.read()


_WORD_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
try {
  $word.Visible = $false; $word.DisplayAlerts = 0
  $doc = $word.Documents.Open($env:IMPORT_SOURCE, $false, $true)
  $doc.SaveAs([ref]$env:IMPORT_TARGET, [ref]17)
  $doc.Close($false)
} finally { $word.Quit() }
"""


def _word_com(source: str, target: str) -> None:
    """MS Word (when installed) saves the file as PDF. Paths go in environment variables, never in the script."""
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        return
    env = {**os.environ, "IMPORT_SOURCE": source, "IMPORT_TARGET": target}
    try:
        subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", _WORD_SCRIPT], env=env,
                       capture_output=True, timeout=WORD_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ------------------------------------------------------------------ PDF
def _from_pdf(raw: bytes, mode: str, page_number: int, text_on_top: bool, notes: list[str]) -> dict[str, Any]:
    try:
        import pdfplumber
    except ImportError:
        raise ImportProblem("PDF import needs the pdfplumber package: pip install -r requirements.txt") from None
    try:
        pdf = pdfplumber.open(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 - any parser failure is a bad file for the user
        if "password" in str(exc).lower() or "encrypt" in str(exc).lower():
            raise ImportProblem("The PDF is password protected. Save a copy without the password and import that.") from None
        raise ImportProblem("The PDF could not be read. Open it and save (or print) it again as PDF, then import that.") from None
    with pdf:
        count = len(pdf.pages)
        if not count:
            raise ImportProblem("The PDF has no pages")
        index = min(max(int(page_number or 1), 1), count) - 1
        page = pdf.pages[index]
        width, height = float(page.width), float(page.height)
        if count > 1:
            notes.append(f"Page {index + 1} of {count} was imported. Choose another page to import that one.")
        chars = [c for c in page.chars if c.get("text") and c.get("upright", True)]
        if len(chars) != len([c for c in page.chars if c.get("text")]):
            notes.append("Turned (rotated) text was left out; add it again with the Rotation option.")
        segments = _text_segments(chars)
        picture = None
        if mode == "picture":
            image = page.to_image(resolution=PICTURE_DPI).original.convert("RGB")
            picture = _picture_element(image, width, height)
            elements = [_text_element(s, image, width) for s in _paragraphs(segments)] if text_on_top else []
        else:
            if not chars:
                notes.append("This PDF has no text inside (it is a scan). Import it in “Exact picture” way, or as a PNG/JPEG to read the text with OCR.")
            elements = _shapes(page, width, height, notes) + _pictures(page, notes)
            elements += [_text_element(s, None, width) for s in _paragraphs(segments)]
    return _result(width, height, elements, picture, count, index + 1, notes)


def _color(value: Any) -> str | None:
    """pdfplumber colour (gray / RGB / CMYK tuple, 0..1) → #rrggbb."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = (value,)
    try:
        parts = [float(v) for v in value]
    except (TypeError, ValueError):
        return None                                    # pattern / named colour spaces
    if len(parts) == 1:
        r = g = b = parts[0]
    elif len(parts) == 3:
        r, g, b = parts
    elif len(parts) == 4:
        c, m, y, k = parts
        r, g, b = (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
    else:
        return None
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in (r, g, b))


_FAMILIES = (("times", "Times New Roman"), ("georgia", "Georgia"), ("garamond", "Serif"), ("cambria", "Serif"),
             ("courier", "Courier New"), ("consol", "Monospaced"), ("mono", "Monospaced"), ("calibri", "Calibri"),
             ("verdana", "Verdana"), ("tahoma", "Tahoma"), ("segoe", "Segoe UI"), ("arial", "Arial"),
             ("helvetica", "Helvetica"))


def _font(fontname: str) -> tuple[str, bool, bool]:
    name = (fontname or "").split("+", 1)[-1].lower()
    bold = bool(re.search(r"bold|black|heavy|semibold|demi|,b\b", name))
    italic = bool(re.search(r"italic|oblique|,i\b", name))
    for key, family in _FAMILIES:
        if key in name:
            return family, bold, italic
    if "serif" in name and "sans" not in name:
        return "Serif", bold, italic
    return "SansSerif", bold, italic


def _text_segments(chars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Characters → runs on one line with one style; a wide gap (a column / tab) starts a new run."""
    rows: list[list[dict[str, Any]]] = []
    for ch in sorted(chars, key=lambda c: (round(float(c["top"]), 1), float(c["x0"]))):
        size = float(ch.get("size") or 10)
        for row in rows:
            if abs(float(row[0]["top"]) - float(ch["top"])) < size * 0.4:
                row.append(ch)
                break
        else:
            rows.append([ch])
    segments: list[dict[str, Any]] = []
    for row in rows:
        row.sort(key=lambda c: float(c["x0"]))
        current: dict[str, Any] | None = None
        for ch in row:
            size = float(ch.get("size") or 10)
            style = (ch.get("fontname"), round(size, 1), _color(ch.get("non_stroking_color")))
            gap = float(ch["x0"]) - current["x1"] if current else 0.0
            if current and (style != current["style"] and ch["text"].strip() or gap > size * 1.2):
                segments.append(current)
                current = None
            if current is None:
                if not ch["text"].strip():
                    continue
                current = {"text": "", "x0": float(ch["x0"]), "x1": float(ch["x1"]), "top": float(ch["top"]),
                           "bottom": float(ch["bottom"]), "size": size, "style": style}
            elif gap > size * 0.12 and not current["text"].endswith(" ") and ch["text"] != " ":
                current["text"] += " "
            current["text"] += ch["text"]
            current["x1"] = max(current["x1"], float(ch["x1"]))
            current["top"] = min(current["top"], float(ch["top"]))
            current["bottom"] = max(current["bottom"], float(ch["bottom"]))
        if current:
            segments.append(current)
    for s in segments:
        s["text"] = s["text"].rstrip()
    return [s for s in segments if s["text"]]


def _paragraphs(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lines under each other with the same left edge and style become one multi-line box (fewer, easier boxes)."""
    blocks: list[dict[str, Any]] = []
    for s in sorted(segments, key=lambda s: (s["top"], s["x0"])):
        for b in reversed(blocks[-12:]):
            gap = s["top"] - b["last_top"]
            if (b["style"] == s["style"] and abs(b["x0"] - s["x0"]) < 1.5 and 0 < gap < s["size"] * 1.8
                    and not PLACEHOLDER_RE.search(s["text"]) and not PLACEHOLDER_RE.search(b["text"])):
                b["text"] += "\n" + s["text"]
                b["gaps"].append(gap)
                b["last_top"] = s["top"]
                b["x1"] = max(b["x1"], s["x1"])
                b["bottom"] = max(b["bottom"], s["bottom"])
                break
        else:
            blocks.append({**s, "last_top": s["top"], "gaps": []})
    return blocks


def _sample(image: Any, box: tuple[float, float, float, float], scale: float) -> tuple[str, str]:
    """(background, text colour) of a box on the picture: the ring around it, and its darkest pixels."""
    x0, top, x1, bottom = (round(v * scale) for v in box)
    w, h = image.size
    x0, top, x1, bottom = max(0, x0 - 2), max(0, top - 2), min(w - 1, x1 + 2), min(h - 1, bottom + 2)
    if x1 <= x0 or bottom <= top:
        return "#ffffff", "#000000"
    ring = []
    for x in range(x0, x1 + 1, max(1, (x1 - x0) // 40)):
        ring += [image.getpixel((x, top)), image.getpixel((x, bottom))]
    for y in range(top, bottom + 1, max(1, (bottom - top) // 10)):
        ring += [image.getpixel((x0, y)), image.getpixel((x1, y))]
    back = tuple(int(median(p[i] for p in ring)) for i in range(3))
    inside = image.crop((x0, top, x1, bottom)).convert("RGB").resize((max(1, (x1 - x0) // 2), max(1, (bottom - top) // 2)))
    flat = inside.tobytes()
    pixels = sorted((tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)), key=lambda p: -sum(abs(p[i] - back[i]) for i in range(3)))
    ink = pixels[: max(1, len(pixels) // 12)]
    text = tuple(int(median(p[i] for p in ink)) for i in range(3))
    return "#%02x%02x%02x" % back, "#%02x%02x%02x" % text


def _text_element(block: dict[str, Any], image: Any | None, page_width: float) -> dict[str, Any]:
    fontname, size, color = block["style"]
    family, bold, italic = _font(fontname or "")
    lines = block["text"].count("\n") + 1
    spacing = median(block["gaps"]) if block["gaps"] else size * 1.2
    width = (block["x1"] - block["x0"]) * 1.1 + 4
    element = {
        "type": "staticText", "text": block["text"], "x": round(block["x0"], 1), "y": round(block["top"] - size * 0.08, 1),
        "width": round(min(width, page_width - block["x0"]), 1), "height": round(spacing * (lines - 1) + size * 1.3, 1),
        "fontSize": round(size * 2) / 2, "fontName": family, "bold": bold, "italic": italic, "color": color or "#000000",
        "padding": 0,
    }
    if lines > 1:
        element.update({"lineSpacing": "Fixed", "lineSpacingSize": round(spacing, 1)})
    if image is not None:
        back, ink = _sample(image, (block["x0"], block["top"], block["x1"], block["bottom"]), image.size[0] / page_width)
        element["backColor"] = back
        if not color or block.get("ocr"):
            element["color"] = ink
    return _with_placeholders(element)


def _with_placeholders(element: dict[str, Any]) -> dict[str, Any]:
    """"Dear {{Name}}," → Text Field "Dear $F{Name},"."""
    text = element["text"]
    if not PLACEHOLDER_RE.search(text):
        return element
    fields: list[str] = []

    def to_token(match: re.Match[str]) -> str:
        field = next(g for g in match.groups() if g)
        fields.append(field)
        return f"$F{{{field}}}"

    expression = PLACEHOLDER_RE.sub(to_token, text)     # the engine fills $F{…} inside plain text
    element = {k: v for k, v in element.items() if k != "text"}
    element.update({"type": "textField", "expression": expression, "pattern": "", "blankWhenNull": True,
                    "stretch": False, "fields": fields})
    return element


def _shapes(page: Any, width: float, height: float, notes: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in page.rects:
        x0, top, x1, bottom = float(r["x0"]), float(r["top"]), float(r["x1"]), float(r["bottom"])
        w, h = x1 - x0, bottom - top
        fill = _color(r.get("non_stroking_color")) if r.get("fill") else None
        stroke = _color(r.get("stroking_color")) if r.get("stroke") else None
        if w >= width * 0.98 and h >= height * 0.98 and fill in (None, "#ffffff") and not stroke:
            continue                                   # the white page itself
        if (w <= 1.6 or h <= 1.6) and (fill or stroke):
            out.append(_line(x0, top, w, h, fill or stroke, max(0.5, min(w, h) or 0.5)))
            continue
        if not fill and not stroke:
            continue
        out.append({"type": "rectangle", "x": round(x0, 1), "y": round(top, 1), "width": round(w, 1), "height": round(h, 1),
                    "backColor": fill, "borderColor": stroke or fill, "borderWidth": round(float(r.get("linewidth") or 0), 2) if stroke else 0})
    for line in page.lines:
        x0, top, x1, bottom = float(line["x0"]), float(line["top"]), float(line["x1"]), float(line["bottom"])
        if abs(x1 - x0) > 1 and abs(bottom - top) > 1:
            continue                                   # slanted: not a line element
        out.append(_line(x0, top, x1 - x0, bottom - top, _color(line.get("stroking_color")) or "#000000",
                         float(line.get("linewidth") or 1)))
    if len(page.curves) > 2:
        notes.append(f"{len(page.curves)} curved shapes (logos, round corners, drawings) were left out. "
                     "Import with “Exact picture” to keep them exactly.")
    return out


def _line(x: float, y: float, w: float, h: float, color: str | None, thickness: float) -> dict[str, Any]:
    vertical = h > w
    return {"type": "line", "x": round(x, 1), "y": round(y, 1), "width": round(max(w, 1), 1) if not vertical else 1,
            "height": 1 if not vertical else round(max(h, 1), 1), "borderColor": color or "#000000",
            "borderWidth": round(max(0.25, thickness), 2)}


def _pictures(page: Any, notes: list[str]) -> list[dict[str, Any]]:
    out = []
    for item in page.images[:20]:
        x0, top, x1, bottom = (float(item[k]) for k in ("x0", "top", "x1", "bottom"))
        x0, top = max(0.0, x0), max(0.0, top)
        x1, bottom = min(float(page.width), x1), min(float(page.height), bottom)
        if x1 - x0 < 4 or bottom - top < 4:
            continue
        try:
            image = page.crop((x0, top, x1, bottom)).to_image(resolution=PICTURE_DPI * 1.5).original.convert("RGB")
        except Exception:  # noqa: BLE001 - a picture that cannot be drawn is skipped
            continue
        out.append({"type": "image", "x": round(x0, 1), "y": round(top, 1), "width": round(x1 - x0, 1),
                    "height": round(bottom - top, 1), "src": _data_url(image), "scaleImage": "FillFrame"})
    if len(page.images) > 20:
        notes.append("Only the first 20 pictures were imported.")
    return out


def _data_url(image: Any) -> str:
    """PNG, or JPEG when the PNG is too big; large pictures are made smaller."""
    if max(image.size) > 2400:
        image = image.copy()
        image.thumbnail((2400, 2400))
    for kind, options in (("PNG", {"optimize": True}), ("JPEG", {"quality": 88}), ("JPEG", {"quality": 72})):
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, kind, **options)
        if buffer.tell() <= MAX_PICTURE_BYTES:
            break
        if kind == "JPEG" and options["quality"] == 72 and max(image.size) > 1200:
            image = image.copy()
            image.thumbnail((1600, 1600))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=72)
    mime = "image/png" if kind == "PNG" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _picture_element(image: Any, width: float, height: float) -> dict[str, Any]:
    return {"type": "image", "x": 0, "y": 0, "width": round(width, 1), "height": round(height, 1),
            "src": _data_url(image), "scaleImage": "FillFrame", "locked": True}


# ------------------------------------------------------------------ images (OCR)
_OCR: Any = None


def _ocr_engine() -> Any:
    global _OCR
    if _OCR is None:
        import logging

        from rapidocr import RapidOCR  # import inside: heavy, and optional
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)   # no INFO lines in the service log
        _OCR = RapidOCR()
    return _OCR


def _from_image(raw: bytes, text_on_top: bool, notes: list[str]) -> dict[str, Any]:
    from PIL import Image, ImageOps
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except Exception:  # noqa: BLE001
        raise ImportProblem("The picture could not be opened. Save it again as PNG or JPEG and import that.") from None
    px_w, px_h = image.size
    if px_w < 50 or px_h < 50:
        raise ImportProblem("The picture is too small to use as a page design")
    width = 842.0 if px_w > px_h else 595.0             # A4 width, landscape or portrait; height keeps the shape
    height = round(px_h * width / px_w, 1)
    elements: list[dict[str, Any]] = []
    if text_on_top:
        lines = _ocr_lines(image, notes)
        scale = width / px_w
        for text, (x0, top, x1, bottom) in lines:
            size = max(5.0, (bottom - top) * scale / 1.2)    # an OCR box is about 1.1–1.3 × the font size
            block = {"text": text, "x0": x0 * scale, "x1": x1 * scale, "top": top * scale, "bottom": bottom * scale,
                     "size": size, "style": (None, round(size, 1), None), "gaps": [], "ocr": True}
            element = _text_element(block, image, width)
            element["y"] = round(top * scale, 1)
            element["height"] = round(max((bottom - top) * scale, size * 1.2), 1)
            elements.append(element)
        if lines:
            notes.append(f"{len(lines)} pieces of text were read with OCR. Check the spelling: OCR can misread letters. "
                         "Urdu text is not read; it stays in the picture.")
    return _result(width, height, elements, _picture_element(image, width, height), 1, 1, notes)


def _ocr_lines(image: Any, notes: list[str]) -> list[tuple[str, tuple[float, float, float, float]]]:
    try:
        engine = _ocr_engine()
    except ImportError:
        notes.append("Text was not read: OCR needs the rapidocr and onnxruntime packages (pip install -r requirements.txt). The picture was imported.")
        return []
    except Exception as exc:  # noqa: BLE001 - a missing model download and similar
        notes.append(f"Text was not read: the OCR engine could not start ({type(exc).__name__}). The picture was imported.")
        return []
    import numpy
    result = engine(numpy.asarray(image))
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or texts is None:
        return []
    out = []
    for box, text, score in zip(boxes, texts, scores if scores is not None else [1.0] * len(texts), strict=False):
        text = str(text).strip()
        if not text or float(score) < 0.5:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        out.append((text, (min(xs), min(ys), max(xs), max(ys))))
    return out


# ------------------------------------------------------------------ result
def _result(width: float, height: float, elements: list[dict[str, Any]], picture: dict[str, Any] | None,
            count: int, page: int, notes: list[str]) -> dict[str, Any]:
    room = MAX_ELEMENTS - (1 if picture else 0)
    if len(elements) > room:
        texts = [e for e in elements if e["type"] in ("staticText", "textField")]
        others = sorted((e for e in elements if e not in texts), key=lambda e: -(e["width"] * e["height"]))
        kept = texts[:room] + others[: max(0, room - len(texts))]
        notes.append(f"The page has {len(elements)} pieces; the {len(elements) - len(kept)} smallest were left out "
                     f"(a report may have {MAX_ELEMENTS + 20} elements).")
        elements = [e for e in elements if any(e is k for k in kept)]
    fields: list[str] = []
    for element in elements:
        for field in element.pop("fields", []):
            if FIELD_NAME_RE.match(field) and field not in fields:
                fields.append(field)
    for element in elements:                           # inside the page, at least 1pt big
        element["x"] = max(0.0, min(element["x"], width - 1))
        element["y"] = max(0.0, min(element["y"], height - 1))
        element["width"] = max(1.0, min(element["width"], width - element["x"]))
        element["height"] = max(1.0, min(element["height"], height - element["y"]))
    if fields:
        notes.append("Fields found in the design: " + ", ".join(fields) + ". They were added to Setup → Fields.")
    return {
        "page": {"width": round(width, 1), "height": round(height, 1), "margins": {"top": 0, "right": 0, "bottom": 0, "left": 0}},
        "background": [picture] if picture else [],
        "elements": elements,
        "fields": fields,
        "pageCount": count,
        "pageNumber": page,
    }
