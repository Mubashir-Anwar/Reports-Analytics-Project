"""Browser security headers and the request size limit (production hardening).

* Every response: X-Content-Type-Options, Referrer-Policy, Permissions-Policy, X-Frame-Options (SAMEORIGIN: the
  designer previews PDFs in a frame of its own page).
* HTML pages: a Content-Security-Policy that allows only this site's own scripts (the React build has no inline
  script), inline styles, data:/blob: images and blob: frames (PDF preview). /docs and /redoc load Swagger from a CDN,
  so they get no CSP.
* HTTPS (directly, or X-Forwarded-Proto from a trusted proxy): Strict-Transport-Security when REPORTING_HSTS=true.
* Request bodies larger than REPORTING_MAX_BODY_MB (default 25) are refused with 413 before they are read."""
from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from backend.app.security.access import _in_networks, _parse_networks

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'self'")
NO_CSP_PATHS = ("/docs", "/redoc", "/openapi.json")


def max_body_bytes() -> int:
    try:
        return int(float(os.getenv("REPORTING_MAX_BODY_MB", "25")) * 1024 * 1024)
    except ValueError:
        return 25 * 1024 * 1024


def too_large(request: Request) -> JSONResponse | None:
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > max_body_bytes():
        return JSONResponse({"detail": f"The request is larger than {max_body_bytes() // (1024 * 1024)} MB"}, status_code=413)
    return None


def is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    proxies = _parse_networks(os.getenv("REPORTING_TRUSTED_PROXIES", ""), "REPORTING_TRUSTED_PROXIES")
    direct = request.client.host if request.client else ""
    return bool(proxies) and _in_networks(direct, proxies) and request.headers.get("x-forwarded-proto", "").lower() == "https"


def add_security_headers(request: Request, response: Response) -> Response:
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    if response.headers.get("content-type", "").startswith("text/html") and not request.url.path.startswith(NO_CSP_PATHS):
        headers.setdefault("Content-Security-Policy", CSP)
    if os.getenv("REPORTING_HSTS", "false").strip().lower() == "true" and is_https(request):
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
