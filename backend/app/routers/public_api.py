"""Public API v1: what a client website or system uses (with an API key from Admin → API keys).

    GET  /api/v1/whoami                      the key: name, folders, rate limit, webhook
    GET  /api/v1/reports                     reports this key may run, with their parameters
    GET  /api/v1/reports/{id}                one report
    POST /api/v1/reports/{id}/run            run now; JSON in ({"format", "parameters"}), the file out
    POST /api/v1/reports/{id}/jobs           run in the background (big reports); 202 with the job
    GET  /api/v1/jobs/{id}                   the job's status
    GET  /api/v1/jobs/{id}/file              the finished file
    POST /api/v1/links                       a short-lived signed URL for a browser (routers/api_keys.py)
    GET  /api/v1/openapi.json                this API described as OpenAPI 3

Every call is checked like /generate: API key (X-API-Key or Authorization: Bearer), its folders, addresses,
expiry and requests per minute; a run is recorded in the run log (channel "api"). Errors are JSON {"detail": "…"}.
"""
from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.app.constants import OUTPUT_FORMATS, SIGNED_LINK_KEYS
from backend.app.definitions import validate_parameters
from backend.app.jobs import store as job_store

MEDIA = {"pdf": "application/pdf", "csv": "text/csv; charset=utf-8",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
MAX_ACTIVE_JOBS_PER_KEY = 5
PUBLIC_PREFIXES = ("/api/v1/", "/generate/", "/reports")


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the public API v1 to the app."""
    require_report_access = ctx.require_report_access
    report_in_scope = ctx.report_in_scope
    reports_by_id = ctx.reports_by_id
    json_body = ctx.json_body

    def report_view(definition: Any) -> dict[str, Any]:
        editor = definition.editor_config or {}
        return {
            "id": definition.report_id, "title": definition.title, "description": editor.get("description", ""),
            "folder": editor.get("folder", "General"), "formats": editor.get("formats", sorted(OUTPUT_FORMATS)),
            "defaultFormat": editor.get("defaultFormat", "pdf"),
            "parameters": [{
                "name": spec.name, "type": spec.kind, "label": spec.label or spec.name, "description": spec.description,
                "required": spec.required, "maxLength": spec.max_length,
                **({"allowedValues": list(spec.allowed_values)} if spec.allowed_values else {}),
                **({"default": spec.default.isoformat() if isinstance(spec.default, date) else spec.default} if spec.default is not None else {}),
            } for spec in definition.parameters if spec.prompt],
        }

    def visible_report(request: Request, report_id: str) -> Any:
        definition = reports_by_id.get(report_id)
        if definition is None or definition.editor_config is None or not report_in_scope(request, definition):
            raise HTTPException(status_code=404, detail="Report ID is not registered")
        return definition

    def run_request(payload: dict[str, Any], definition: Any) -> tuple[str, list[tuple[str, str]]]:
        output_format = str(payload.get("format") or (definition.editor_config or {}).get("defaultFormat", "pdf")).lower()
        if output_format not in OUTPUT_FORMATS:
            raise HTTPException(status_code=422, detail="format must be pdf, xlsx or csv")
        parameters = payload.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise HTTPException(status_code=422, detail="parameters must be an object such as {\"Branch\": \"LHR\"}")
        if any(str(name) in SIGNED_LINK_KEYS for name in parameters):
            raise HTTPException(status_code=422, detail=f"{', '.join(SIGNED_LINK_KEYS)} are reserved and cannot be parameters")
        pairs: list[tuple[str, str]] = []
        for name, value in parameters.items():
            for item in (value if isinstance(value, list) else [value]):
                pairs.append((str(name), "" if item is None else str(item)))
        on_behalf = str(payload.get("onBehalfOf") or "").strip()[:128]
        if on_behalf:
            pairs.append(("GeneratedBy", on_behalf))
        return output_format, pairs

    def caller_key(request: Request) -> dict[str, Any]:
        require_report_access(request)
        scope = getattr(request.state, "report_scope", None)
        if scope is None or scope[0] != "key":
            raise HTTPException(status_code=403, detail="Background jobs belong to an API key: send the X-API-Key header")
        return scope[1]

    def job_view(run: dict[str, Any]) -> dict[str, Any]:
        view = {name: run.get(name) for name in ("id", "reportId", "title", "format", "status", "rowCount", "error", "size",
                                                 "queuedAt", "startedAt", "finishedAt")}
        view["downloadUrl"] = f"/api/v1/jobs/{run['id']}/file" if run.get("status") == "success" and run.get("file") else None
        return view

    def owned_job(request: Request, job_id: str) -> dict[str, Any]:
        key = caller_key(request)
        run = next((item for item in job_store.load_runs() if item["id"] == job_id), None)
        if run is None or run.get("owner") != f"apikey:{key['id']}":
            raise HTTPException(status_code=404, detail="Job not found")
        return run

    @app.get("/api/v1/whoami", tags=["Public API v1"])
    def v1_whoami(request: Request) -> dict[str, Any]:
        """Check the credentials: which key (or user) this is and what it may do."""
        access = require_report_access(request)
        scope = getattr(request.state, "report_scope", None)
        if scope and scope[0] == "key":
            from backend.app.security.api_keys import rate_limit  # noqa: PLC0415
            key = scope[1]
            return {"type": "api-key", "id": key["id"], "name": key["name"], "folders": key.get("folders") or [],
                    "rateLimitPerMinute": rate_limit(key), "webhook": bool(key.get("webhookUrl")), "expiresAt": key.get("expiresAt")}
        if scope and scope[0] == "user":
            return {"type": "user", "name": scope[1]["username"], "rights": sorted(scope[1]["rights"])}
        return {"type": "token", "name": access}

    @app.get("/api/v1/reports", tags=["Public API v1"])
    def v1_reports(request: Request) -> dict[str, Any]:
        """The published reports these credentials may run, with their parameters."""
        require_report_access(request)
        items = [report_view(item) for item in reports_by_id.values() if item.editor_config is not None and report_in_scope(request, item)]
        return {"reports": sorted(items, key=lambda item: (item["folder"].casefold(), item["title"].casefold()))}

    @app.get("/api/v1/reports/{report_id}", tags=["Public API v1"])
    def v1_report(report_id: str, request: Request) -> dict[str, Any]:
        require_report_access(request)
        return report_view(visible_report(request, report_id))

    @app.post("/api/v1/reports/{report_id}/run", tags=["Public API v1"])
    async def v1_run(report_id: str, request: Request) -> Response:
        """Run now and return the file. Body: {"format": "pdf", "parameters": {"Identifier": "123"}, "onBehalfOf": "ali"}."""
        access = require_report_access(request)
        definition = visible_report(request, report_id)
        output_format, pairs = run_request(await json_body(request, optional=True), definition)
        return ctx.produce_report(request, f"{definition.report_id}.{output_format}", access, pairs)

    @app.post("/api/v1/reports/{report_id}/jobs", status_code=202, tags=["Public API v1"])
    async def v1_start_job(report_id: str, request: Request) -> JSONResponse:
        """Run in the background (big reports). Poll GET /api/v1/jobs/{id}, or get a webhook call when it is done."""
        key = caller_key(request)
        definition = visible_report(request, report_id)
        output_format, pairs = run_request(await json_body(request, optional=True), definition)
        if output_format not in (definition.editor_config or {}).get("formats", sorted(OUTPUT_FORMATS)):
            raise HTTPException(status_code=422, detail=f"The {output_format.upper()} format is not enabled for this report")
        try:
            validate_parameters(definition, pairs)                 # wrong parameters are reported now, not later
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        owner = f"apikey:{key['id']}"
        active = [run for run in job_store.load_runs() if run.get("owner") == owner and run.get("status") in ("queued", "running")]
        if len(active) >= MAX_ACTIVE_JOBS_PER_KEY:
            raise HTTPException(status_code=429, detail=f"This key already has {len(active)} reports waiting or running; try again when one is done")
        parameters = {name: value for name, value in pairs if name != "GeneratedBy"}
        run = ctx.job_runner.enqueue(definition.report_id, output_format, parameters, owner, "api", None)
        return JSONResponse(status_code=202, content={"job": job_view(run)}, headers={"Location": f"/api/v1/jobs/{run['id']}"})

    @app.get("/api/v1/jobs/{job_id}", tags=["Public API v1"])
    def v1_job(job_id: str, request: Request) -> dict[str, Any]:
        return {"job": job_view(owned_job(request, job_id))}

    @app.get("/api/v1/jobs/{job_id}/file", tags=["Public API v1"])
    def v1_job_file(job_id: str, request: Request) -> Response:
        run = owned_job(request, job_id)
        if run.get("status") != "success":
            raise HTTPException(status_code=409, detail=f"The job is {run.get('status')}; the file is ready when it is success")
        body = job_store.read_output(run)
        if body is None:
            raise HTTPException(status_code=410, detail="The file is no longer kept")
        stamp = re.sub(r"\D", "", str(run.get("finishedAt") or ""))[:14]
        return Response(body, media_type=MEDIA[run["format"]],
                        headers={"Content-Disposition": f'attachment; filename="{run["reportId"]}-{stamp}.{run["format"]}"'})

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    def v1_openapi() -> dict[str, Any]:
        """The public part of the API as OpenAPI 3 (always available, also when /docs is switched off)."""
        full = app.openapi()
        paths = {path: item for path, item in full.get("paths", {}).items() if path.startswith(PUBLIC_PREFIXES)}
        security = {"ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key",
                               "description": "An API key from Admin → API keys (mrk_…). Keep it on the server, never in a browser."}}
        for item in paths.values():
            for operation in item.values():
                operation["security"] = [{"ApiKey": []}]
        return {"openapi": full.get("openapi", "3.1.0"),
                "info": {"title": "Reporting service — public API", "version": "1.0",
                         "description": "Run published reports from a website or another system."},
                "paths": paths, "components": {**full.get("components", {}), "securitySchemes": security}}
