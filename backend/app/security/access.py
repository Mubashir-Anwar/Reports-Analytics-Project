"""Deployment access policy: enabled areas, client IP (trusted proxies), admin IP allow-list."""
from __future__ import annotations

import ipaddress
import os

from fastapi import Request

from backend.app.constants import ALL_AREAS
from backend.app.logs import LOG


# ----------------------------------------------------------------- deployment access policy
def enabled_areas() -> set[str]:
    raw = os.getenv("REPORTING_ENABLED_AREAS", "").strip()
    if not raw:
        return set(ALL_AREAS)
    areas = {item.strip().lower() for item in raw.split(",") if item.strip()}
    unknown = areas - set(ALL_AREAS)
    if unknown:
        LOG.warning("REPORTING_ENABLED_AREAS has unknown values: %s", ", ".join(sorted(unknown)))
    return areas & set(ALL_AREAS)


def path_area(path: str) -> str | None:
    """Which switchable area a request path belongs to (None = always available)."""
    if path == "/admin" or path.startswith("/admin/"):
        return "admin"
    if path == "/designer" or path.startswith("/designer/"):
        return "designer"
    if path == "/query" or path.startswith(("/query/", "/api/query/")):
        return "designer"                  # the Query workspace belongs to the Designer
    if path in ("/login", "/api/server/login", "/api/server/logout", "/api/server/me"):
        return None                     # sign-in works whichever areas are switched on
    if path == "/server" or path.startswith("/server/") or path.startswith("/api/server/"):
        return "server"
    if path == "/analytics" or path.startswith(("/analytics/", "/api/analytics/", "/api/v1/analytics/")):
        return "analytics"
    if path.startswith("/api/admin/"):
        return "management-api"
    if path in ("/docs", "/redoc", "/openapi.json") or path.startswith("/docs/"):
        return "docs"
    return None


def _parse_networks(raw: str, setting: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            LOG.error("%s contains an invalid IP address or CIDR range: %s", setting, item)
    return networks


def _in_networks(address: str, networks: list) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def client_address(request: Request) -> str:
    """Caller IP. X-Forwarded-For is trusted only when the direct peer is in REPORTING_TRUSTED_PROXIES."""
    direct = request.client.host if request.client else ""
    proxies = _parse_networks(os.getenv("REPORTING_TRUSTED_PROXIES", ""), "REPORTING_TRUSTED_PROXIES")
    if not proxies or not _in_networks(direct, proxies):
        return direct
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    for hop in reversed(forwarded):
        if not _in_networks(hop, proxies):
            return hop
    return forwarded[0] if forwarded else direct


def management_ip_allowed(address: str) -> bool:
    raw = os.getenv("REPORTING_ADMIN_ALLOWED_IPS", "").strip()
    if not raw:
        return True
    return _in_networks(address, _parse_networks(raw, "REPORTING_ADMIN_ALLOWED_IPS"))
