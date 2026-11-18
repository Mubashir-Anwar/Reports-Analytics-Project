"""The product name shown everywhere (navigation bar, sign-in page, page titles, Postman files).

Default "Analysis"; a super admin can rename it in Admin → Branding (kept in the system database as
settings/branding.json, so every server behind a load balancer shows the same name)."""
from __future__ import annotations

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


def branding() -> dict[str, str]:
    """{"name", "tagline"}: the saved choice, else the defaults (read again at most every 30 seconds)."""
    with _LOCK:
        if _CACHE["value"] is not None and time.monotonic() - _CACHE["at"] < TTL_SECONDS:
            return dict(_CACHE["value"])
    try:
        saved = get_store().read_json(KEY) or {}
    except Exception:  # noqa: BLE001 - the name must never stop a page from opening
        saved = {}
    value = {"name": str(saved.get("name") or BRAND_NAME), "tagline": str(saved.get("tagline") or BRAND_TAGLINE)}
    with _LOCK:
        _CACHE.update(at=time.monotonic(), value=value)
    return dict(value)


def brand_name() -> str:
    return branding()["name"]


def save_branding(name: str, tagline: str = "") -> dict[str, str]:
    name, tagline = " ".join(str(name or "").split()), " ".join(str(tagline or "").split())
    if not NAME_RE.fullmatch(name):
        raise ValueError("The name needs 2 to 40 letters, digits, spaces or & . - ' ( ) +")
    if len(tagline) > 60 or "<" in tagline or ">" in tagline:
        raise ValueError("The tagline is at most 60 characters, without < or >")
    value = {"name": name, "tagline": tagline or BRAND_TAGLINE}
    get_store().write_json(KEY, value)
    with _LOCK:
        _CACHE.update(at=time.monotonic(), value=value)
    return dict(value)
