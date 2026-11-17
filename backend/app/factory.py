"""The application factory (formerly make_app())."""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.app.constants import SERVICE_VERSION
from backend.app.context import create_context
from backend.app.logs import HTTP_LOG
from backend.app.routers import (
    api_keys,
    connections,
    designer,
    generate,
    jobs,
    pages,
    repository,
    server_portal,
    system,
    users,
    workspace,
)
from backend.app.security import audit_trail
from backend.app.security.access import client_address, enabled_areas, management_ip_allowed, path_area
from backend.app.security.headers import add_security_headers, too_large


def make_app() -> FastAPI:
    """Build the FastAPI application: shared context, the app with its middleware, then every router."""
    ctx = create_context()
    docs_on = ctx.docs_on
    lifespan = ctx.lifespan
    ui_directory = ctx.ui_directory
    app = FastAPI(
        title="MongoDB Reporting Service", version=SERVICE_VERSION, lifespan=lifespan,
        docs_url="/docs" if docs_on else None, redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    app.state.ctx = ctx  # tests and tools reach the shared context (e.g. the job runner) here
    app.mount("/assets", StaticFiles(directory=str(ui_directory)), name="assets")

    def _audit_change(request: Request, body: bytes) -> None:
        route = request.scope.get("route")
        found = audit_trail.describe(request.method, getattr(route, "path", ""), dict(request.path_params), body)
        if found:
            action, target, details = found
            ctx.audit_event(request, action, target, details)

    @app.middleware("http")
    async def access_policy_and_request_log(request: Request, call_next):
        """Per-path deployment policy (areas switched off, management IP allowlist) and one log line per request."""
        started = time.monotonic()
        path = request.url.path
        area = path_area(path)
        client = client_address(request)
        current_areas = enabled_areas()
        blocked: Response | None = None
        if area in ("runner", "admin", "designer", "server", "analytics", "docs") and area not in current_areas:
            blocked = JSONResponse({"detail": "Not Found"}, status_code=404)
        elif area == "management-api" and not current_areas & {"admin", "designer"}:
            blocked = JSONResponse({"detail": "Not Found"}, status_code=404)
        elif area in ("admin", "designer", "management-api") and not management_ip_allowed(client):
            HTTP_LOG.warning("Management access refused for %s from %s (not in REPORTING_ADMIN_ALLOWED_IPS)", path, client)
            blocked = JSONResponse({"detail": "Access from this network address is not allowed"}, status_code=403)
        if blocked is None:
            blocked = too_large(request)
        if blocked is None and not path.startswith("/assets/"):
            ctx.refresh_if_changed()  # another instance may have published or deleted reports
        audited_body = b""
        if blocked is None and request.method in ("POST", "PUT", "DELETE") and path.startswith("/api/"):
            audited_body = await request.body()      # kept for the audit target (folder path, report id); never stored whole
        if blocked is not None:
            response = blocked
        else:
            try:
                response = await call_next(request)
            except Exception:
                HTTP_LOG.exception("Unhandled error on %s %s", request.method, path)
                raise
            if response.status_code < 400 and request.method in ("POST", "PUT", "DELETE"):
                _audit_change(request, audited_body)
        add_security_headers(request, response)
        duration = int((time.monotonic() - started) * 1000)
        if not path.startswith("/assets/") or response.status_code >= 400:
            status = response.status_code
            # 503 = a MongoDB/data source problem, already explained in its own log line; 500 = a real bug.
            level = logging.ERROR if status >= 500 and status != 503 else logging.WARNING if status >= 400 and status != 404 else logging.INFO
            HTTP_LOG.log(level, "%s %s -> %s in %d ms (client %s)", request.method, path, response.status_code, duration, client)
        return response

    # every area's endpoints; registration order = route matching order (same as the old make_app())
    pages.register(app, ctx)
    designer.register(app, ctx)
    repository.register(app, ctx)
    connections.register(app, ctx)
    workspace.register(app, ctx)
    server_portal.register(app, ctx)
    jobs.register(app, ctx)
    users.register(app, ctx)
    api_keys.register(app, ctx)
    system.register(app, ctx)
    generate.register(app, ctx)
    return app
