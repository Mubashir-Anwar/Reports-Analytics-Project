"""Embedding: a client website shows a report inside its own page.

    POST /api/v1/embed-tokens        (API key)   {"report", "parameters": {fixed}, "expiresInSeconds", "onBehalfOf"} -> token
    GET  /embed/view?token=…         the viewer page, framed by the website (<mrs-report> from /assets/embed/sdk.js)
    GET  /api/embed/report           (X-Embed-Token) title, formats and parameters (fixed ones locked)
    POST /api/embed/preview          (X-Embed-Token) {"parameters"} -> first 200 rows
    POST /api/embed/run              (X-Embed-Token) {"format", "parameters"} -> the file

The viewer may only be framed by the key's embedOrigins (Content-Security-Policy frame-ancestors). Every use
checks the key again (enabled, not expired, folder, requests per minute) and is recorded in the run log
(channel "embed").
"""
from __future__ import annotations

import html
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pymongo.errors import PyMongoError

from backend.app.connectors.errors import ConnectorQueryError, MongoReadAccessError
from backend.app.connectors.mongo_fallback import _row_value
from backend.app.constants import OUTPUT_FORMATS
from backend.app.definitions import _safe_value, clean_folder, resolve_default, run_form_rules, validate_parameters
from backend.app.security import api_keys, embed_tokens
from backend.app.security.headers import CSP

PREVIEW_ROWS = 200


def _frame_policy(origins: list[str]) -> str:
    """The normal page policy, except who may frame it (blob: frames show the PDF inside the viewer)."""
    allowed = " ".join(origins) if origins else "'none'"
    return CSP.replace("frame-ancestors 'self'", f"frame-ancestors {allowed}")


def _page(title: str, body: str, origins: list[str], status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><link rel='stylesheet' href='/assets/embed/viewer.css'></head>"
        f"<body>{body}<script src='/assets/embed/viewer.js' defer></script></body></html>",
        status_code=status, headers={"Content-Security-Policy": _frame_policy(origins), "Cache-Control": "no-store",
                                     "Referrer-Policy": "no-referrer"})


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the embedding endpoints to the app."""
    require_report_access = ctx.require_report_access
    report_in_scope = ctx.report_in_scope
    reports_by_id = ctx.reports_by_id
    json_body = ctx.json_body

    # ---------------------------------------------------------------- the website's server asks for a token
    @app.post("/api/v1/embed-tokens", tags=["Public API v1"])
    async def create_embed_token(request: Request) -> dict[str, Any]:
        """For a website's server: a short-lived token that shows one report in its page. Body:
        {"report": "KFS_FAL", "folder": "cascade/KMBL/Reports", "parameters": {"Identifier": "123"}, "expiresInSeconds": 900,
        "onBehalfOf": "ali"} ("folder" only when the same ID is published in several folders).
        The given parameters are fixed (the viewer shows them locked); the others the visitor may fill in."""
        require_report_access(request)
        scope = getattr(request.state, "report_scope", None)
        if scope is None or scope[0] != "key":
            raise HTTPException(status_code=403, detail="Embed tokens are issued to API keys: send the X-API-Key header")
        key = scope[1]
        if not key.get("embedOrigins"):
            raise HTTPException(status_code=422, detail="List the websites that may show reports for this key first (Admin → API keys → Embedding)")
        payload = await json_body(request)
        folder = clean_folder(payload.get("folder"))
        definition = reports_by_id.find(str(payload.get("report", "")), folder, lambda item: report_in_scope(request, item))
        if definition is None or definition.editor_config is None:
            raise ctx.unknown_report(request, str(payload.get("report", "")), folder)
        fixed = payload.get("parameters") or {}
        if not isinstance(fixed, dict):
            raise HTTPException(status_code=422, detail="parameters must be an object of fixed values")
        prompted = {spec.name for spec in definition.parameters if spec.prompt}
        unknown = sorted(set(fixed) - prompted)
        if unknown:
            raise HTTPException(status_code=422, detail=f"Not a parameter of this report: {', '.join(unknown)}")
        try:
            lifetime = int(payload.get("expiresInSeconds", embed_tokens.DEFAULT_LIFETIME))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="expiresInSeconds must be a number") from None
        token, expires = embed_tokens.issue(key["id"], definition.ref, {str(k): v for k, v in fixed.items()}, lifetime,
                                            str(payload.get("onBehalfOf") or ""))
        ctx.audit_event(request, "embed.token_issued", definition.report_id, {"fixed": sorted(fixed), "expires": expires},
                        actor=key["name"], actor_type="api-key")
        return {"token": token, "expiresAt": datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec="seconds"),
                "viewUrl": f"/embed/view?token={token}", "sdk": "/assets/embed/sdk.js"}

    # ---------------------------------------------------------------- checking a token on every use
    def embedded(request: Request, token: str) -> tuple[dict[str, Any], Any, dict[str, Any]]:
        """(key, report, token body) or HTTPException."""
        try:
            body = embed_tokens.read(token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from None
        key = api_keys.key_for_owner(str(body.get("k", "")))
        if key is None:
            raise HTTPException(status_code=401, detail="The website's API key is disabled or expired")
        wait = api_keys.over_limit(key)
        if wait:
            raise HTTPException(status_code=429, detail=f"Too many requests; try again in {wait} s", headers={"Retry-After": str(wait)})
        request.state.report_scope = ("key", key)
        try:
            definition = reports_by_id.get(str(body.get("r", "")))      # <folder>/<ID> (older tokens: the ID)
        except HTTPException:
            definition = None
        if definition is None or definition.editor_config is None or not report_in_scope(request, definition):
            raise HTTPException(status_code=404, detail="This report is no longer available")
        return key, definition, body

    def header_token(request: Request) -> str:
        return request.headers.get("X-Embed-Token", "")

    def merged_pairs(definition: Any, body: dict[str, Any], given: Any) -> list[tuple[str, str]]:
        """The visitor's values for the open parameters, then the fixed ones from the token (they always win)."""
        if given is not None and not isinstance(given, dict):
            raise HTTPException(status_code=422, detail="parameters must be an object")
        fixed = body["p"]
        prompted = {spec.name for spec in definition.parameters if spec.prompt}
        pairs: list[tuple[str, str]] = []
        for name, value in (given or {}).items():
            if name in fixed or name not in prompted:
                continue
            for item in (value if isinstance(value, list) else [value]):
                pairs.append((str(name), "" if item is None else str(item)))
        for name, value in fixed.items():
            for item in (value if isinstance(value, list) else [value]):
                pairs.append((str(name), "" if item is None else str(item)))
        return pairs

    # ---------------------------------------------------------------- the viewer page
    @app.get("/embed/view", include_in_schema=False)
    def embed_view(request: Request, token: str = "") -> HTMLResponse:
        try:
            key, definition, _body = embedded(request, token)
        except HTTPException as exc:
            message = html.escape(str(exc.detail))
            return _page("Report not available", f"<main class='mrs-empty'><h1>Report not available</h1><p>{message}</p></main>", [], exc.status_code)
        origins = key.get("embedOrigins") or []
        if not origins:
            return _page("Embedding not allowed", "<main class='mrs-empty'><h1>Embedding is not set up</h1>"
                         "<p>The administrator has not listed any website for this key.</p></main>", [], 403)
        return _page(definition.title, f"<div id='mrs' data-token='{html.escape(token, quote=True)}'></div>", origins)

    # ---------------------------------------------------------------- what the viewer calls
    @app.get("/api/embed/report", include_in_schema=False)
    def embed_report(request: Request) -> dict[str, Any]:
        _key, definition, body = embedded(request, header_token(request))
        editor = definition.editor_config or {}
        fixed = body["p"]
        parameters = []
        for spec in definition.parameters:
            if not spec.prompt:
                continue
            default = resolve_default(spec)
            default = default.isoformat() if isinstance(default, date) else default
            parameters.append({"name": spec.name, "type": spec.kind, "label": spec.label or spec.name, "description": spec.description,
                               "required": spec.required, "allowedValues": list(spec.allowed_values) or None,
                               "locked": spec.name in fixed, "value": fixed.get(spec.name, default), **run_form_rules(spec)})
        return {"id": definition.report_id, "title": definition.title, "description": editor.get("description", ""),
                "formats": editor.get("formats", sorted(OUTPUT_FORMATS)), "parameters": parameters,
                "expiresAt": datetime.fromtimestamp(int(body["x"]), timezone.utc).isoformat(timespec="seconds")}

    @app.post("/api/embed/preview", include_in_schema=False)
    async def embed_preview(request: Request) -> dict[str, Any]:
        key, definition, body = embedded(request, header_token(request))
        payload = await json_body(request, optional=True)
        pairs = merged_pairs(definition, body, payload.get("parameters"))
        started, clock = datetime.now(timezone.utc), time.monotonic()
        common = {"channel": "embed", "actor": key["name"], "actor_type": "api-key", "started_at": started, "definition": definition,
                  "request": request, "on_behalf_of": str(body.get("u") or "")}
        try:
            parameters, _ = validate_parameters(definition, pairs)
            rows = ctx.run_report_rows(definition, parameters, definition.connection_id,
                                       max_rows=min(ctx.settings.max_rows, PREVIEW_ROWS), limit_rows=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except (ConnectorQueryError, MongoReadAccessError, PyMongoError):
            ctx.log_run(status="failed", error_code="query_failed", parameters=dict(pairs), duration_ms=int((time.monotonic() - clock) * 1000), **common)
            raise HTTPException(status_code=503, detail="The report's data source could not be read; try again later") from None
        safe = [{column: _safe_value(_row_value(row, column)) for column in definition.columns} for row in rows]
        ctx.log_run(status="success", parameters=parameters, row_count=len(safe), duration_ms=int((time.monotonic() - clock) * 1000), **common)
        labels = ((definition.editor_config or {}).get("layout") or {}).get("columnLabels", {})
        return {"columns": list(definition.columns), "labels": labels, "rows": safe, "rowCount": len(safe), "limit": PREVIEW_ROWS}

    @app.post("/api/embed/run", include_in_schema=False)
    async def embed_run(request: Request) -> Response:
        key, definition, body = embedded(request, header_token(request))
        payload = await json_body(request, optional=True)
        output_format = str(payload.get("format") or "pdf").lower()
        if output_format not in OUTPUT_FORMATS:
            raise HTTPException(status_code=422, detail="format must be pdf, xlsx or csv")
        pairs = merged_pairs(definition, body, payload.get("parameters"))
        if body.get("u"):
            pairs.append(("GeneratedBy", str(body["u"])))
        pairs.append(("Folder", definition.folder))
        return ctx.produce_report(request, f"{definition.report_id}.{output_format}", f"embed:{key['name']}", pairs)
