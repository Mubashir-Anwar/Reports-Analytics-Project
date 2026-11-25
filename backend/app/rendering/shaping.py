"""Letter joining with HarfBuzz (the uharfbuzz package): what the font's own tables say, so Nastaliq works too.

ReportLab can draw shaped text (ShapedStr: one character per glyph, with its advance and offsets); this makes such text
from a line in logical order. Without uharfbuzz, or with a font that is not TrueType, rtl.shape() / rtl.visual() are used.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from itertools import groupby

from reportlab.pdfbase import pdfmetrics, ttfonts

from backend.app.rendering import rtl

_LOCK = threading.RLock()            # a glyph without a character gets one, once per font


def available(name: str) -> bool:
    return bool(getattr(pdfmetrics.getFont(name), "shapable", False)) and hasattr(ttfonts, "ShapedStr")


@lru_cache(maxsize=4096)
def _shape(name: str, text: str, right: bool) -> ttfonts.ShapedStr:
    import uharfbuzz
    font = pdfmetrics.getFont(name)
    face = font.face
    with _LOCK:
        hb = font.hbFont()
        buffer = uharfbuzz.Buffer()
        buffer.add_str(text)
        buffer.guess_segment_properties()
        buffer.direction = "rtl" if right else "ltr"
        uharfbuzz.shape(hb, buffer)
        chars, data = [], []
        for info, place in zip(buffer.glyph_infos, buffer.glyph_positions):
            gid = info.codepoint
            code = next((c for c in face.glyphToChar.get(gid, ()) if c != 0xA0), None)
            if code is None:
                code = font.hbAddPrivate(f"gid{gid}", gid, font.pdfScale(hb.get_glyph_h_advance(gid)))
            chars.append(chr(code))
            data.append(ttfonts.ShapeData(info.cluster, font.pdfScale(place.x_advance), 0, font.pdfScale(place.x_offset),
                                          font.pdfScale(place.y_offset), face.charWidths[code]))
    return ttfonts.ShapedStr("".join(chars), shapeData=data)


def parts(line: str, name: str, size: float, fallback: str) -> list[tuple[str, str, float]]:
    """One line as it is drawn, left to right: [(font, text for drawString, width)]. Latin words and numbers the font does
    not have are drawn with `fallback`."""
    out = []
    glyphs = pdfmetrics.getFont(name).face.charToGlyph
    for text, right in rtl.runs(line):
        if not right and any(ord(ch) not in glyphs and not ch.isspace() for ch in text):
            out.append((fallback, text, pdfmetrics.stringWidth(text, fallback, size)))
            continue
        shaped = _shape(name, text, right)
        out.append((name, shaped, sum(item.x_advance for item in shaped.__shapeData__) * size / 1000))
    return out


def width(line: str, name: str, size: float, fallback: str) -> float:
    return sum(part[2] for part in parts(line, name, size, fallback))


def wrap(text: str, name: str, size: float, room: float, fallback: str, first: float) -> list[str]:
    """Lines (logical order) not wider than `room` (the first one than `first`); a word that is wider has its own line."""
    lines = [""]
    for word in text.split(" "):
        longer = f"{lines[-1]} {word}" if lines[-1] else word
        if lines[-1].strip() and width(longer, name, size, fallback) > (first if len(lines) == 1 else room):
            lines.append(word)
        else:
            lines[-1] = longer
    return lines


def draw(canvas, x: float, y: float, text: str, size: float) -> None:
    """drawString for a part of parts(). Glyphs of one height are drawn together at their own baseline: ReportLab's text
    rise loses its place when the height changes twice in a row, as it does in Nastaliq."""
    if not isinstance(text, ttfonts.ShapedStr):
        canvas.drawString(x, y, text)
        return
    data = text.__shapeData__
    start = 0
    for height, group in groupby(data, key=lambda item: item.y_offset):
        items = list(group)
        end = start + len(items)
        canvas.drawString(x, y + height * size / 1000, ttfonts.ShapedStr(str(text)[start:end], shapeData=[i._replace(y_offset=0) for i in items]))
        x += sum(item.x_advance for item in items) * size / 1000
        start = end


def line_metrics(name: str) -> tuple[float, float]:
    """(height above the baseline, line height) as a share of the font size; Nastaliq lines need far more than 1.2."""
    face = pdfmetrics.getFont(name).face
    above, height = face.ascent / 1000, (face.ascent - face.descent) / 1000
    return (above, min(height, 4)) if height > 1.25 else (1.0, 1.2)
