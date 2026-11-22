"""API keys: one per client website or system that runs reports by URL.

A key looks like  mrk_<id>_<secret>.  Only a SHA-256 of the secret is stored (the secret is 256 random bits,
so a plain hash is enough), the full key is shown once when it is created.

The client's server sends it in a header (X-API-Key or Authorization: Bearer) and never puts it in a browser.
For a browser it asks for a short-lived signed link instead (POST /api/v1/links): that link carries KeyId,
Expires and Signature, so the key's limits (folders, expiry, disabled) still apply when it is opened.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any

from backend.app.security.access import _in_networks, _parse_networks

KEY = "api-keys.json"          # the older document; moved into rs_api_keys at start-up
TOKEN_PATTERN = re.compile(r"^mrk_([a-f0-9]{12})_([A-Za-z0-9_-]{30,64})$")
ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")
_TOUCH_SECONDS = 60
_last_touch: dict[str, float] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def load_keys() -> list[dict[str, Any]]:
    """API keys from api_keys, api_key_folders and api_key_networks (db/people.py), oldest first."""
    from backend.app.db.people import load_keys as load  # noqa: PLC0415
    items = load()
    return sorted((item for item in items if ID_PATTERN.fullmatch(str(item.get("id", "")))), key=lambda item: item.get("createdAt") or "")


def save_keys(keys: list[dict[str, Any]]) -> None:
    from backend.app.db.people import save_keys as save  # noqa: PLC0415
    save(keys)


def key_view(item: dict[str, Any]) -> dict[str, Any]:
    """What the admin page shows (never the hash)."""
    return {name: item.get(name) for name in ("id", "name", "folders", "allowedIps", "expiresAt", "disabled", "createdBy",
                                               "createdAt", "lastUsedAt", "webhookUrl", "ratePerMinute", "embedOrigins")} | {
        "prefix": f"mrk_{item['id']}_…", "webhookSecretSet": bool(item.get("webhookSecret")), "rateLimit": rate_limit(item)}


def create_key(name: str, folders: list[str], allowed_ips: list[str], expires_at: str | None, created_by: str) -> tuple[dict[str, Any], str]:
    key_id, secret = secrets.token_hex(6), secrets.token_urlsafe(32)
    record = {"id": key_id, "name": name, "hash": _digest(secret), "folders": folders, "allowedIps": allowed_ips,
              "expiresAt": expires_at, "disabled": False, "createdBy": created_by, "createdAt": _now(), "lastUsedAt": None}
    keys = load_keys()
    keys.append(record)
    save_keys(keys)
    return record, f"mrk_{key_id}_{secret}"


def _usable(item: dict[str, Any] | None, client_ip: str) -> dict[str, Any] | None:
    if item is None or item.get("disabled"):
        return None
    expires = item.get("expiresAt")
    if expires:
        try:
            if datetime.fromisoformat(str(expires).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                return None
        except ValueError:
            return None
    allowed = [str(value) for value in item.get("allowedIps") or []]
    if allowed and not _in_networks(client_ip, _parse_networks(",".join(allowed), "API key allowedIps")):
        return None
    return item


def _touch(item: dict[str, Any]) -> None:
    """Remember when the key was last used (at most once a minute per key, so runs do not rewrite the file)."""
    if time.monotonic() - _last_touch.get(item["id"], -_TOUCH_SECONDS) < _TOUCH_SECONDS:
        return
    _last_touch[item["id"]] = time.monotonic()
    from backend.app.db.people import touch_key  # noqa: PLC0415
    touch_key(item["id"], _now())


def key_from_token(token: str, client_ip: str) -> dict[str, Any] | None:
    """The active key for a full mrk_… value, or None."""
    match = TOKEN_PATTERN.fullmatch(token.strip())
    if not match:
        return None
    item = next((stored for stored in load_keys() if stored["id"] == match.group(1)), None)
    if item is None or not secrets.compare_digest(str(item.get("hash", "")), _digest(match.group(2))):
        return None
    item = _usable(item, client_ip)
    if item is not None:
        _touch(item)
    return item


def key_by_id(key_id: str, client_ip: str) -> dict[str, Any] | None:
    """The active key named in a signed link (the link's Signature proves it was issued for this key)."""
    if not ID_PATTERN.fullmatch(key_id or ""):
        return None
    item = _usable(next((stored for stored in load_keys() if stored["id"] == key_id), None), client_ip)
    if item is not None:
        _touch(item)
    return item


def key_for_owner(key_id: str) -> dict[str, Any] | None:
    """The key that owns a background run, if it is still enabled and not expired (no address check: the run
    happens on the server, not at the caller)."""
    item = next((stored for stored in load_keys() if stored["id"] == key_id), None)
    if item is None or item.get("disabled"):
        return None
    expires = item.get("expiresAt")
    if expires:
        try:
            if datetime.fromisoformat(str(expires).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                return None
        except ValueError:
            return None
    return item


# ------------------------------------------------------------------ requests per minute, per key (this instance)
_HITS: dict[str, list[float]] = {}
_HITS_LOCK = threading.Lock()


def rate_limit(item: dict[str, Any]) -> int:
    own = item.get("ratePerMinute")
    if isinstance(own, int) and own > 0:
        return own
    try:
        return max(0, int(os.getenv("REPORTING_API_RATE_PER_MINUTE", "120")))
    except ValueError:
        return 120


def over_limit(item: dict[str, Any]) -> int:
    """0 when the call may go ahead, else the seconds to wait (sliding one-minute window; limit 0 = off)."""
    limit = rate_limit(item)
    if not limit:
        return 0
    now = time.monotonic()
    with _HITS_LOCK:
        hits = [moment for moment in _HITS.get(item["id"], []) if now - moment < 60]
        if len(hits) >= limit:
            _HITS[item["id"]] = hits
            return max(1, int(60 - (now - hits[0])) + 1)
        hits.append(now)
        _HITS[item["id"]] = hits
    return 0


def link_secret() -> str:
    """Secret for signed links issued to API keys (separate from REPORTING_ACCESS_SECRET)."""
    from backend.app.security.users import _session_secret
    return hmac.new(_session_secret(), b"api-key-links-v1", hashlib.sha256).hexdigest()


def folder_allowed(item: dict[str, Any], folder: str) -> bool:
    """A key limited to folders may only run reports in those folders (and their subfolders)."""
    limits = [str(value).strip("/") for value in item.get("folders") or [] if str(value).strip("/")]
    if not limits:
        return True
    folder = str(folder or "").strip("/")
    return any(folder == limit or folder.startswith(limit + "/") for limit in limits)
