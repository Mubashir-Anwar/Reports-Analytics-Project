"""Embed tokens: let a client website show one report inside its own page, without a sign-in on this server.

The website's server asks for a token with its API key (POST /api/v1/embed-tokens) and puts it in its page
(<mrs-report token="…">, <mrs-dashboard token="…"> or an iframe of /embed/view?token=…). The token is

    emb_<base64url(JSON)>.<hex HMAC-SHA256>

    {"k": key id, "r": report id, "p": {fixed parameters}, "x": expiry (unix s), "u": the website's user, "n": nonce}
    for a dashboard also {"d": dashboard id, "f": [fixed filters]}

It is signed with a server secret (derived from REPORTING_SESSION_SECRET), lives at most an hour, and is
checked again on every use: the key must still be enabled and allowed to run the report's folder, and the page
may only be framed by the websites listed for the key (embedOrigins).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import urlparse

MAX_LIFETIME = 3600
DEFAULT_LIFETIME = 900
TOKEN_PATTERN = re.compile(r"^emb_([A-Za-z0-9_-]{10,6000})\.([0-9a-f]{64})$")


def _secret() -> bytes:
    from backend.app.security.users import _session_secret  # noqa: PLC0415
    return hmac.new(_session_secret(), b"embed-tokens-v1", hashlib.sha256).digest()


def issue(key_id: str, report_id: str, fixed: dict[str, Any], lifetime: int, on_behalf_of: str = "", *,
          dashboard: str = "", filters: list[dict[str, Any]] | None = None) -> tuple[str, int]:
    expires = int(time.time()) + max(60, min(int(lifetime), MAX_LIFETIME))
    body = {"k": key_id, "r": report_id, "p": fixed, "x": expires, "n": secrets.token_hex(6)}
    if dashboard:
        body.update({"d": dashboard, "f": filters or []})
    if on_behalf_of:
        body["u"] = on_behalf_of[:128]
    encoded = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"emb_{encoded}.{signature}", expires


def read(token: str) -> dict[str, Any]:
    """The token's content, or ValueError (malformed, changed or expired)."""
    match = TOKEN_PATTERN.fullmatch(str(token or "").strip())
    if not match:
        raise ValueError("The embed token is missing or malformed")
    expected = hmac.new(_secret(), match.group(1).encode("ascii"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected, match.group(2)):
        raise ValueError("The embed token is not valid (it was changed, or made by another server)")
    try:
        body = json.loads(base64.urlsafe_b64decode(match.group(1) + "=" * (-len(match.group(1)) % 4)))
    except ValueError:
        raise ValueError("The embed token is not valid") from None
    if not isinstance(body, dict) or not isinstance(body.get("p"), dict) or int(body.get("x", 0)) <= time.time():
        raise ValueError("The embed token has expired; the website must ask for a new one")
    return body


def clean_origins(value: Any) -> list[str]:
    """Websites that may show embedded reports: scheme://host[:port], nothing more."""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("embedOrigins must be a list such as [\"https://portal.example.com\"]")
    origins = []
    for item in value:
        text = str(item).strip().rstrip("/")
        parsed = urlparse(text)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.path or parsed.query or parsed.username:
            raise ValueError(f"Not a website origin (use https://host or https://host:port): {item}")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname):
            raise ValueError(f"Not a valid host name: {item}")
        origins.append(f"{parsed.scheme}://{parsed.hostname}{f':{parsed.port}' if parsed.port else ''}")
    return sorted(set(origins))[:20]
