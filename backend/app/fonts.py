"""Own fonts (TrueType files) for reports: added in the Designer, used in the canvas, PDF and Excel.

The list is settings/fonts.json in the system database, each file is fonts/<id>.ttf there, so every server behind a load
balancer has the same fonts. A family ("Noto Naskh Arabic") can have up to four files: regular, bold, italic and bold
italic; a missing one prints with the regular file. Urdu, Arabic, Persian and Hebrew text is joined and put in right-to-left
order by rendering/rtl.py before it is drawn.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from backend.app.storage.store import get_store

KEY = "settings/fonts.json"
NAME_RE = re.compile(r"^[A-Za-z0-9][\w .\-]{0,59}$")
MAX_FONT_BYTES = 15_000_000
MAX_FONTS = 100
STYLES = ("regular", "bold", "italic", "boldItalic")
_CACHE: dict[str, Any] = {"at": 0.0, "value": None, "store": None}
_LOCK = threading.RLock()
TTL_SECONDS = 30


def list_fonts(fresh: bool = False) -> list[dict[str, Any]]:
    """[{id, family, style, fileName, bytes, rightToLeft, by, at}], family by family."""
    store = get_store()
    place = getattr(store, "url", None) or str(getattr(store, "root", ""))
    with _LOCK:
        if not fresh and _CACHE["value"] is not None and _CACHE["store"] == place and time.monotonic() - _CACHE["at"] < TTL_SECONDS:
            return list(_CACHE["value"])
    try:
        items = [item for item in (store.read_json(KEY) or {}).get("fonts") or [] if isinstance(item, dict) and item.get("id")]
    except Exception:  # noqa: BLE001 - a report must print without its own fonts rather than fail
        if fresh:
            raise
        items = []
    items.sort(key=lambda item: (str(item.get("family")).casefold(), STYLES.index(item["style"]) if item.get("style") in STYLES else 0))
    with _LOCK:
        _CACHE.update(at=time.monotonic(), value=items, store=place)
    return list(items)


def _save(items: list[dict[str, Any]]) -> None:
    get_store().write_json(KEY, {"fonts": items})
    with _LOCK:
        _CACHE.update(at=0.0, value=None)


def _read_font(raw: bytes):
    """A file → its ReportLab face; plain reasons for the formats a PDF cannot use."""
    if raw[:4] == b"OTTO":
        raise ValueError("This is an OpenType font with PostScript outlines (.otf); a PDF needs a TrueType font (.ttf)")
    if raw[:4] in (b"wOFF", b"wOF2"):
        raise ValueError("This is a web font (.woff); choose the .ttf file of the font")
    if raw[:4] == b"ttcf":
        raise ValueError("This is a font collection (.ttc); choose a single .ttf file")
    if raw[:4] not in (b"\x00\x01\x00\x00", b"true"):
        raise ValueError("Choose a TrueType font file (.ttf)")
    from reportlab.pdfbase.ttfonts import TTFont  # noqa: PLC0415
    try:
        return TTFont("check", io.BytesIO(raw)).face
    except Exception as exc:  # noqa: BLE001 - whatever is wrong inside the file
        raise ValueError(f"The font file could not be read: {str(exc)[:120]}") from None


def _text(value: Any) -> str:
    return value.decode("latin-1", "replace") if isinstance(value, bytes) else str(value or "")


def add_font(file: str, file_name: str = "", family: str = "", style: str = "", by: str = "") -> dict[str, Any]:
    """file: the .ttf as base64 (a data URL is fine). Family and style come from the file unless given."""
    data = str(file or "").split(",", 1)[-1] if str(file or "").startswith("data:") else str(file or "")
    if len(data) > MAX_FONT_BYTES * 4 // 3 + 8:
        raise ValueError(f"A font may be at most {MAX_FONT_BYTES // 1_000_000} MB")
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise ValueError("The font file could not be read") from None
    if not raw:
        raise ValueError("Choose a TrueType font file (.ttf)")
    face = _read_font(raw)
    own_style = _text(getattr(face, "styleName", "")).lower()
    bold, italic = "bold" in own_style, "italic" in own_style or "oblique" in own_style
    family = " ".join(str(family or "").split()) or " ".join(re.sub(r"[^\w .\-]", " ", _text(getattr(face, "familyName", ""))).split())
    family = family or re.sub(r"\.ttf$", "", file_name, flags=re.I)
    if not NAME_RE.fullmatch(family):
        raise ValueError("The font name needs English letters, digits, spaces, dots or - (at most 60)")
    from backend.app.rendering.engine import FONT_FAMILY  # noqa: PLC0415
    if family in FONT_FAMILY and family in ("SansSerif", "Serif", "Monospaced"):
        raise ValueError(f"{family} is a name of the designer itself; give the font another name")
    if style not in STYLES:
        style = "boldItalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    codes = getattr(face, "charToGlyph", {}) or {}
    font_id = hashlib.sha256(raw).hexdigest()[:24]
    with _LOCK:
        items = list_fonts(fresh=True)
        same = next((item for item in items if item["family"].casefold() == family.casefold()), None)
        family = same["family"] if same else family              # one spelling per family
        kept = [item for item in items if not (item["family"] == family and item.get("style") == style)]
        if any(item["id"] == font_id for item in kept):
            raise ValueError(f"This file is already there: {next(i['family'] for i in kept if i['id'] == font_id)}")
        if len(kept) >= MAX_FONTS:
            raise ValueError(f"At most {MAX_FONTS} font files")
        item = {"id": font_id, "family": family, "style": style, "fileName": re.sub(r"[\x00-\x1f]", "", str(file_name))[:120],
                "bytes": len(raw), "rightToLeft": 0x0628 in codes or 0x05D0 in codes, "urdu": all(c in codes for c in (0x0679, 0x06D2, 0x06BA)),
                "by": by, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        get_store().write_bytes(f"fonts/{font_id}.ttf", raw)
        _save([*kept, item])
        for old in items:
            if old not in kept and old["id"] != font_id:
                get_store().delete(f"fonts/{old['id']}.ttf")
    return item


def delete_font(font_id: str) -> dict[str, Any]:
    with _LOCK:
        items = list_fonts(fresh=True)
        found = next((item for item in items if item["id"] == font_id), None)
        if found is None:
            raise KeyError(font_id)
        _save([item for item in items if item["id"] != font_id])
        get_store().delete(f"fonts/{font_id}.ttf")
    return found


def font_file(font_id: str) -> bytes | None:
    if not re.fullmatch(r"[0-9a-f]{24}", font_id):
        return None
    return get_store().read_bytes(f"fonts/{font_id}.ttf")


def families() -> list[str]:
    return sorted({item["family"] for item in list_fonts()}, key=str.casefold)


def _register(font_id: str) -> str | None:
    """Make the file usable in PDFs of this process; its ReportLab name."""
    from reportlab.pdfbase import pdfmetrics  # noqa: PLC0415
    from reportlab.pdfbase.ttfonts import TTFont  # noqa: PLC0415
    name = f"own-{font_id}"
    with _LOCK:
        if name not in pdfmetrics.getRegisteredFontNames():
            raw = font_file(font_id)
            if raw is None:
                return None
            pdfmetrics.registerFont(TTFont(name, io.BytesIO(raw)))
    return name


def pdf_family(family: str) -> tuple[str, str, str, str] | None:
    """The four ReportLab names (regular, bold, italic, bold italic) of an own font, or None."""
    if family == SYSTEM_RTL:
        return _system_rtl()
    files = {item["style"]: item["id"] for item in list_fonts() if item["family"] == family}
    if not files:
        return None
    try:
        registered = {style: name for style, font_id in files.items() if (name := _register(font_id))}
        if not registered:
            return None
        regular = registered.get("regular") or next(iter(registered.values()))
        four = (regular, registered.get("bold") or regular, registered.get("italic") or regular,
                registered.get("boldItalic") or registered.get("bold") or registered.get("italic") or regular)
        from reportlab.lib.fonts import addMapping  # noqa: PLC0415
        for (bold, italic), name in zip(((0, 0), (1, 0), (0, 1), (1, 1)), four):
            addMapping(regular, bold, italic, name)
        return four
    except Exception:  # noqa: BLE001 - a broken file: the built-in font is used
        return None


def right_to_left_family() -> str | None:
    """The own font that stands in when Urdu / Arabic text has a font without those letters (Urdu ones first)."""
    items = [item for item in list_fonts() if item.get("rightToLeft") and item["style"] == "regular"]
    items.sort(key=lambda item: not item.get("urdu"))
    if items:
        return items[0]["family"]
    return SYSTEM_RTL if _system_rtl() else None


# No own font has Urdu / Arabic letters: a font of the machine itself, so the text never prints as black boxes.
SYSTEM_RTL = "system right-to-left"
_SYSTEM_FILES = (
    # (regular, bold); Windows: the Arial that a classic report designer and the browser use; then the fonts of the Docker image
    ("{windows}/arial.ttf", "{windows}/arialbd.ttf"),
    ("{windows}/tahoma.ttf", "{windows}/tahomabd.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"),
    ("/usr/share/fonts/google-noto/NotoNaskhArabic-Regular.ttf", "/usr/share/fonts/google-noto/NotoNaskhArabic-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", ""),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
)


def _system_rtl() -> tuple[str, str, str, str] | None:
    from reportlab.pdfbase import pdfmetrics  # noqa: PLC0415
    from reportlab.pdfbase.ttfonts import TTFont  # noqa: PLC0415
    with _LOCK:
        if "sys-rtl" not in _SYSTEM:
            _SYSTEM["sys-rtl"] = None
            windows = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts").replace("\\", "/")
            own = os.environ.get("REPORT_RTL_FONT_PATH", "").strip()
            for regular, bold in ((own, ""), *_SYSTEM_FILES):
                regular, bold = regular.format(windows=windows), bold.format(windows=windows)
                if not regular or not os.path.isfile(regular):
                    continue
                try:
                    font = TTFont("sys-rtl", regular)
                    if not all(code in font.face.charToGlyph for code in (0x0628, 0x0679, 0x06D2)):
                        continue                    # no Arabic, or no Urdu letters
                    pdfmetrics.registerFont(font)
                    heavy = "sys-rtl"
                    if bold and os.path.isfile(bold):
                        pdfmetrics.registerFont(TTFont("sys-rtl-bold", bold))
                        heavy = "sys-rtl-bold"
                    _SYSTEM["sys-rtl"] = ("sys-rtl", heavy, "sys-rtl", heavy)
                    break
                except Exception:  # noqa: BLE001,S112 - not a font a PDF can use: the next one
                    continue
        return _SYSTEM["sys-rtl"]


_SYSTEM: dict[str, tuple[str, str, str, str] | None] = {}
