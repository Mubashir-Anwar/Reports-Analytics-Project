"""API keys for client websites (/admin → API keys) and the link endpoint they call.

How a website runs a report:
  * server to server:  GET /generate/<ID>.pdf?Param=…   with header  X-API-Key: mrk_…
  * for a browser:     POST /api/v1/links  (X-API-Key)  → a signed /generate URL valid for a few minutes,
                       which the website puts in a link, a button or an <iframe>. The key never reaches the browser.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request

from backend.app.constants import OUTPUT_FORMATS, SIGNED_LINK_KEYS
from backend.app.definitions import validate_parameters
from backend.app.logs import LOG
from backend.app.security import api_keys, embed_tokens, webhooks
from backend.app.storage.files import normalize_folder_path

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@-]{1,63}$")


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the API key endpoints to the app."""
    json_body = ctx.json_body
    require_admin = ctx.require_admin
    require_report_access = ctx.require_report_access
    report_in_scope = ctx.report_in_scope
    reports_by_id = ctx.reports_by_id

    def clean_folders(value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise HTTPException(status_code=422, detail="folders must be a list of folder paths (empty = every folder)")
        try:
            return sorted({normalize_folder_path(str(item)) for item in value if str(item).strip("/ ")})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    def clean_ips(value: Any) -> list[str]:
        import ipaddress  # noqa: PLC0415
        if value in (None, ""):
            return []
        items = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else None
        if items is None:
            raise HTTPException(status_code=422, detail="allowedIps must be a list such as [\"203.0.113.10\", \"10.0.0.0/8\"]")
        for item in items:
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Not an IP address or range: {item}") from None
        return items

    def clean_expiry(value: Any) -> str | None:
        if value in (None, "", 0):
            return None
        try:
            days = int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="expiresInDays must be a whole number of days") from None
        if not 1 <= days <= 3650:
            raise HTTPException(status_code=422, detail="expiresInDays must be between 1 and 3650")
        return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")

    @app.get("/api/admin/api-keys")
    def list_api_keys(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {"keys": [api_keys.key_view(item) for item in api_keys.load_keys()]}

    @app.post("/api/admin/api-keys")
    async def create_api_key(request: Request) -> dict[str, Any]:
        require_admin(request)
        payload = await json_body(request)
        name = str(payload.get("name", "")).strip()
        if not NAME_PATTERN.fullmatch(name):
            raise HTTPException(status_code=422, detail="Name: 2-64 letters, numbers, spaces, dot, @, _ or - (e.g. the client website)")
        if any(item.get("name", "").casefold() == name.casefold() for item in api_keys.load_keys()):
            raise HTTPException(status_code=409, detail="A key with this name already exists")
        record, full_key = api_keys.create_key(name, clean_folders(payload.get("folders")), clean_ips(payload.get("allowedIps")),
                                               clean_expiry(payload.get("expiresInDays")), request.state.actor)
        LOG.info("API key %s (%s) created by %s", record["id"], name, request.state.actor)
        ctx.audit_event(request, "apikey.created", record["id"], {"name": name, "folders": record["folders"],
                                                                  "allowedIps": record["allowedIps"], "expiresAt": record["expiresAt"]})
        return {"key": api_keys.key_view(record), "secret": full_key,
                "note": "Copy this key now: it is shown only once. Keep it on the website's server, never in a browser."}

    @app.put("/api/admin/api-keys/{key_id}")
    async def update_api_key(key_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        payload = await json_body(request)
        keys = api_keys.load_keys()
        item = next((stored for stored in keys if stored["id"] == key_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="API key not found")
        if "disabled" in payload:
            item["disabled"] = bool(payload["disabled"])
        if "folders" in payload:
            item["folders"] = clean_folders(payload["folders"])
        if "allowedIps" in payload:
            item["allowedIps"] = clean_ips(payload["allowedIps"])
        if "expiresInDays" in payload:
            item["expiresAt"] = clean_expiry(payload["expiresInDays"])
        new_secret = None
        if "webhookUrl" in payload:
            url = str(payload.get("webhookUrl") or "").strip()
            if url:
                try:
                    item["webhookUrl"] = webhooks.check_url(url)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from None
                if not item.get("webhookSecret") or payload.get("rotateWebhookSecret") is True:
                    item["webhookSecret"] = new_secret = webhooks.new_secret()
            else:
                item.pop("webhookUrl", None)
                item.pop("webhookSecret", None)
        if "embedOrigins" in payload:
            try:
                origins = embed_tokens.clean_origins(payload.get("embedOrigins"))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            if origins:
                item["embedOrigins"] = origins
            else:
                item.pop("embedOrigins", None)
        if "ratePerMinute" in payload:
            rate = payload.get("ratePerMinute")
            if rate in (None, "", 0):
                item.pop("ratePerMinute", None)
            elif isinstance(rate, int) and not isinstance(rate, bool) and 1 <= rate <= 100000:
                item["ratePerMinute"] = rate
            else:
                raise HTTPException(status_code=422, detail="ratePerMinute must be a whole number from 1 to 100000 (empty = the default)")
        api_keys.save_keys(keys)
        LOG.info("API key %s changed by %s", key_id, request.state.actor)
        ctx.audit_event(request, "apikey.changed", key_id, {name: payload[name] for name in ("disabled", "folders", "allowedIps", "expiresInDays",
                                                                              "embedOrigins", "ratePerMinute", "webhookUrl") if name in payload})
        result: dict[str, Any] = {"key": api_keys.key_view(item)}
        if new_secret:
            result["webhookSecret"] = new_secret
            result["note"] = "Copy the webhook secret now: it is shown only once. The website uses it to check X-Reporting-Signature."
        return result

    @app.delete("/api/admin/api-keys/{key_id}")
    def delete_api_key(key_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        keys = api_keys.load_keys()
        remaining = [item for item in keys if item["id"] != key_id]
        if len(remaining) == len(keys):
            raise HTTPException(status_code=404, detail="API key not found")
        api_keys.save_keys(remaining)
        LOG.info("API key %s deleted by %s", key_id, request.state.actor)
        ctx.audit_event(request, "apikey.deleted", key_id)
        return {"status": "deleted", "id": key_id}

    @app.post("/api/v1/links")
    async def create_signed_link(request: Request) -> dict[str, Any]:
        """For a client website's server: a short-lived URL that opens one report with fixed parameters.

        Body: {"report": "KFS_FAL", "format": "pdf", "parameters": {"Identifier": "123"}, "expiresInSeconds": 300}
        The parameters are part of the signature, so the person who opens the link cannot change them.
        """
        access = require_report_access(request)
        scope = getattr(request.state, "report_scope", None)
        if scope is None or scope[0] != "key":
            raise HTTPException(status_code=403, detail="Signed links are issued to API keys: send the X-API-Key header")
        key = scope[1]
        payload = await json_body(request)
        report_id = str(payload.get("report", "")).strip()
        output_format = str(payload.get("format", "pdf")).strip().lower()
        definition = reports_by_id.get(report_id)
        if definition is None or not report_in_scope(request, definition):
            raise HTTPException(status_code=404, detail="Report ID is not registered")
        if output_format not in OUTPUT_FORMATS:
            raise HTTPException(status_code=422, detail="format must be pdf, xlsx or csv")
        parameters = payload.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise HTTPException(status_code=422, detail="parameters must be an object such as {\"Identifier\": \"123\"}")
        if any(str(name) in SIGNED_LINK_KEYS for name in parameters):
            raise HTTPException(status_code=422, detail=f"{', '.join(SIGNED_LINK_KEYS)} are reserved and cannot be parameters")
        max_lifetime = int(os.getenv("REPORTING_SIGNED_LINK_MAX_SECONDS", "3600"))
        try:
            lifetime = int(payload.get("expiresInSeconds", 300))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="expiresInSeconds must be a number") from None
        lifetime = max(30, min(lifetime, max_lifetime))
        pairs = []
        for name, value in parameters.items():
            values = value if isinstance(value, list) else [value]
            pairs.extend((str(name), "" if item is None else str(item)) for item in values)
        try:
            validate_parameters(definition, pairs)          # tell the website now, not when someone opens the link
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        expires = int(time.time()) + lifetime
        pairs += [("KeyId", key["id"]), ("Expires", str(expires))]
        path = f"/generate/{quote(report_id)}.{output_format}"
        query = urlencode(pairs, quote_via=quote)
        signature = hmac.new(api_keys.link_secret().encode("utf-8"), f"{path}?{query}".encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{path}?{query}&Signature={signature}"
        LOG.info("Signed link for %s issued to %s (%s s)", report_id, access, lifetime)
        return {"url": url, "expiresAt": datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec="seconds")}
