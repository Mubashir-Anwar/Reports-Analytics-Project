"""Webhooks: tell a client website that a background report it asked for is finished.

Each API key may have a webhook URL and a secret. The call is

    POST <webhookUrl>
    Content-Type: application/json
    X-Reporting-Event: job.finished
    X-Reporting-Timestamp: 1794895000
    X-Reporting-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>

    {"event": "job.finished", "job": {"id", "reportId", "status", "rowCount", …, "downloadUrl": "/api/v1/jobs/<id>/file"}}

The website checks the signature (and that the timestamp is recent), then downloads the file with its API key.

Safety: only http/https; the host must not resolve to a private, loopback, link-local or reserved address
(REPORTING_WEBHOOK_ALLOW_PRIVATE=true allows it for a site on the same network); redirects are not followed;
5-second time-out; the URL is checked again at every call (the DNS answer may have changed).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from backend.app.logs import LOG

TIMEOUT_SECONDS = 5


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def check_url(url: str) -> str:
    """The URL if it may be called, else ValueError with the reason."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("The webhook URL must start with https:// (or http://) and name a host")
    if parsed.username or parsed.password:
        raise ValueError("Put no user name or password in the webhook URL; the call is signed instead")
    if len(url) > 500:
        raise ValueError("The webhook URL is too long")
    if os.getenv("REPORTING_WEBHOOK_ALLOW_PRIVATE", "").strip().lower() != "true":
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))}
        except OSError:
            raise ValueError(f"The webhook host {parsed.hostname} cannot be found") from None
        for address in addresses:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise ValueError(f"The webhook host {parsed.hostname} is an internal address ({ip}); "
                                 "set REPORTING_WEBHOOK_ALLOW_PRIVATE=true to allow sites on your own network")
    return str(url).strip()


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def deliver(url: str, secret: str, event: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """POST the event now. (ok, short result) — never raises."""
    try:
        target = check_url(url)
    except ValueError as exc:
        return False, str(exc)
    body = json.dumps({"event": event, **payload}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stamp = str(int(time.time()))
    request = urllib.request.Request(target, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "mongo-reporting-webhook/1", "X-Reporting-Event": event,
        "X-Reporting-Timestamp": stamp, "X-Reporting-Signature": signature(secret, stamp, body)})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as answer:  # noqa: S310 - scheme and host checked above
            return 200 <= answer.status < 300, f"HTTP {answer.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, type(exc).__name__


def deliver_later(url: str, secret: str, event: str, payload: dict[str, Any], on_done: Any = None) -> None:
    """Send in the background (one retry after 10 s); on_done(ok, result) is called at the end."""
    def work() -> None:
        ok, result = deliver(url, secret, event, payload)
        if not ok:
            time.sleep(10)
            ok, result = deliver(url, secret, event, payload)
        if not ok:
            LOG.warning("Webhook %s to %s failed: %s", event, urlparse(url).hostname, result)
        if on_done:
            on_done(ok, result)
    threading.Thread(target=work, name="webhook", daemon=True).start()
