"""Health, adapter status, who-am-i, logs and run history."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.app.connectors.profiles import load_connection_profiles
from backend.app.constants import SERVICE_VERSION
from backend.app.logs import _FILE_HANDLER, LOG_LEVELS, log_file_path, read_log_entries
from backend.app.security.access import enabled_areas
from backend.app.storage.audit import read_audit_records


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the system endpoints to the app."""
    get_connection = ctx.get_connection
    test_saved_adapter = ctx.test_saved_adapter
    require_admin = ctx.require_admin
    require_report_access = ctx.require_report_access

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            _, database = get_connection("default")
            return JSONResponse({"status": "ok", "version": SERVICE_VERSION, "connection": "default", "database": database})
        except HTTPException as exc:
            status = "not_configured" if exc.status_code == 503 and "set " in str(exc.detail).lower() else "unavailable"
            return JSONResponse({"status": status, "version": SERVICE_VERSION, "detail": exc.detail}, status_code=exc.status_code)

    @app.get("/reports/connections/{connection_id}/status")
    def report_connection_status(connection_id: str, request: Request) -> dict[str, Any]:
        """Is the data adapter of the report the user picked reachable? Used by the /ui runner."""
        require_report_access(request)
        profile = next((item for item in load_connection_profiles() if item["id"] == connection_id), None)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"Data adapter is not registered: {connection_id}")
        try:
            if profile.get("type", "mongodb") == "mongodb":
                _, database = get_connection(connection_id)
            else:
                database = test_saved_adapter(connection_id)["database"]
        except HTTPException as exc:
            return {"status": "unavailable", "connectionId": connection_id, "name": profile.get("name", connection_id),
                    "detail": str(exc.detail)}
        return {"status": "ok", "connectionId": connection_id, "name": profile.get("name", connection_id), "database": database}

    @app.get("/api/admin/whoami")
    def admin_whoami(request: Request) -> dict[str, Any]:
        role = require_admin(request, "designer")
        return {"role": role, "version": SERVICE_VERSION, "areas": sorted(enabled_areas())}

    @app.get("/api/admin/logs")
    def admin_logs(request: Request, lines: int = 300, level: str = "INFO", search: str = "") -> dict[str, Any]:
        require_admin(request)
        if level.upper() not in LOG_LEVELS:
            raise HTTPException(status_code=422, detail="level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        path = log_file_path()
        return {"file": path.name, "enabled": _FILE_HANDLER is not None,
                "entries": read_log_entries(lines, level, search[:200])}

    @app.get("/api/admin/logs/download")
    def admin_logs_download(request: Request) -> Response:
        require_admin(request)
        path = log_file_path()
        if not path.exists():
            raise HTTPException(status_code=404, detail="No log file has been written yet")
        return Response(path.read_bytes(), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{path.name}"'})

    @app.get("/api/admin/audit")
    def admin_audit(request: Request, limit: int = 200) -> dict[str, Any]:
        require_admin(request)
        return {"records": read_audit_records(limit)}
