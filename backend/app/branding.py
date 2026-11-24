"""The product name shown everywhere (navigation bar, sign-in page, page titles, Postman files).

Default "Analysis"; a super admin can rename it in Admin → Branding (kept in the system database as
settings/branding.json, so every server behind a load balancer shows the same name). The same place keeps an own
logo (PNG, JPEG, WebP or SVG, at most MAX_LOGO_BYTES); /brand/logo serves it, or the built-in mark when there is none."""
from __future__ import annotations

import base64
import hashlib
import re
import threading
import time
from typing import Any

from backend.app.constants import BRAND_NAME, BRAND_TAGLINE
from backend.app.storage.store import get_store

KEY = "settings/branding.json"
NAME_RE = re.compile(r"^[\w &.\-'()+]{2,40}$")
_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
_LOCK = threading.Lock()
TTL_SECONDS = 30
MAX_LOGO_BYTES = 300_000
LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp", "image/svg+xml": "svg"}
DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|webp|svg\+xml));base64,([A-Za-z0-9+/=\s]+)$")


def _saved() -> dict[str, Any]:
    try:
        return get_store().read_json(KEY) or {}
    except Exception:  # noqa: BLE001 - the name must never stop a page from opening
        return {}


def branding() -> dict[str, str]:
    """{"name", "tagline", "logo"}: the saved choice, else the defaults (read again at most every 30 seconds).
    logo is the address of the own logo (with a version, so browsers fetch a new one at once), or "" for the built-in one."""
    with _LOCK:
        if _CACHE["value"] is not None and time.monotonic() - _CACHE["at"] < TTL_SECONDS:
            return dict(_CACHE["value"])
    saved = _saved()
    logo = saved.get("logo") or {}
    value = {"name": str(saved.get("name") or BRAND_NAME), "tagline": str(saved.get("tagline") or BRAND_TAGLINE),
             "logo": f"/brand/logo?v={logo['version']}" if logo.get("version") else ""}
    with _LOCK:
        _CACHE.update(at=time.monotonic(), value=value)
    return dict(value)


def brand_name() -> str:
    return branding()["name"]


def logo_file() -> tuple[bytes, str] | None:
    """The own logo as (bytes, media type), or None."""
    logo = _saved().get("logo") or {}
    if not logo.get("data") or logo.get("type") not in LOGO_TYPES:
        return None
    try:
        return base64.b64decode(logo["data"]), logo["type"]
    except ValueError:
        return None


def clean_logo(data_url: str) -> dict[str, str]:
    """A picture sent as a data URL → what is kept. Refuses other types, big files and SVG with scripts."""
    match = DATA_URL_RE.fullmatch(str(data_url or "").strip())
    if not match:
        raise ValueError("Choose a PNG, JPEG, WebP or SVG picture")
    kind = match.group(1)
    try:
        raw = base64.b64decode("".join(match.group(2).split()), validate=True)
    except ValueError:
        raise ValueError("The picture could not be read") from None
    if not raw or len(raw) > MAX_LOGO_BYTES:
        raise ValueError(f"The logo may be at most {MAX_LOGO_BYTES // 1000} KB")
    if kind == "image/svg+xml":
        text = raw.decode("utf-8", "replace").lower()
        if "<svg" not in text or re.search(r"<script|\bon[a-z]+\s*=|javascript:|<foreignobject|<iframe|<embed|<object", text):
            raise ValueError("This SVG has scripts or embedded pages; save it as a plain SVG or PNG")
    signatures = {"image/png": raw.startswith(b"\x89PNG"), "image/jpeg": raw.startswith(b"\xff\xd8"),
                  "image/webp": raw[:4] == b"RIFF" and raw[8:12] == b"WEBP", "image/svg+xml": True}
    if not signatures[kind]:
        raise ValueError("The file is not the picture type it says it is")
    return {"type": kind, "data": base64.b64encode(raw).decode("ascii"), "version": hashlib.sha256(raw).hexdigest()[:12]}


def save_branding(name: str, tagline: str = "", logo: Any = ...) -> dict[str, str]:
    """logo: a data URL (a new logo), None or "" (back to the built-in one), or left out (keep it)."""
    name, tagline = " ".join(str(name or "").split()), " ".join(str(tagline or "").split())
    if not NAME_RE.fullmatch(name):
        raise ValueError("The name needs 2 to 40 letters, digits, spaces or & . - ' ( ) +")
    if len(tagline) > 60 or "<" in tagline or ">" in tagline:
        raise ValueError("The tagline is at most 60 characters, without < or >")
    stored: dict[str, Any] = {"name": name, "tagline": tagline or BRAND_TAGLINE}
    if logo is ...:
        if _saved().get("logo"):
            stored["logo"] = _saved()["logo"]
    elif logo:
        stored["logo"] = clean_logo(logo)
    get_store().write_json(KEY, stored)
    with _LOCK:
        _CACHE.update(at=0.0, value=None)
    return branding()
