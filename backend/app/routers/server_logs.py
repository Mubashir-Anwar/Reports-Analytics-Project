"""Report Server → Logs (administrators): everything the server records, on the /server page.

    GET /api/server/logs/requests   every request that reached the server (system database), one day at a time
    GET /api/server/logs/runs       report runs (who ran what, parameters, rows, time, status)
    GET /api/server/logs/service    the service log (errors, warnings, startup), newest last
    GET /api/server/logs/audit      the audit trail (sign-ins, rights, publishing, deletes…)
    GET /api/server/logs/downloads  report files being made in the background right now (all callers), and capacity

The same records as Admin → Logs, so they can be read where the reports are, even with the Admin page switched off.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.db import get_records
from backend.app.logs import _FILE_HANDLER, LOG_LEVELS, log_file_path, read_log_entries
from backend.app.services import deliveries
from backend.app.services.request_log import RequestLog


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    require_session = ctx.require_session

    def admin(request: Request) -> dict[str, Any]:
        return require_session(request, "admin")

    @app.get("/api/server/logs/requests")
    def server_request_log(request: Request, day: str = "", limit: int = 500, status: str = "", search: str = "",
                           method: str = "", actor: str = "") -> dict[str, Any]:
        admin(request)
        day = day or datetime.now(timezone.utc).date().isoformat()
        try:
            found = RequestLog.read(day, limit, status, search, method, actor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        log = getattr(ctx, "request_log", None)
        return {**found, "days": RequestLog.days(), "recorder": log.stats() if log else {"enabled": False}}

    @app.get("/api/server/logs/runs")
    def server_run_log(request: Request, limit: int = 200, reportId: str = "", actor: str = "", channel: str = "",  # noqa: N803
                       status: str = "", since: str = "", until: str = "") -> dict[str, Any]:
        admin(request)
        try:
            return {"records": get_records().runs(limit=max(1, min(limit, 2000)), since=since or None, until=until or None,
                                                  reportId=reportId, actor=actor, channel=channel, status=status)}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/server/logs/service")
    def server_service_log(request: Request, lines: int = 300, level: str = "INFO", search: str = "") -> dict[str, Any]:
        admin(request)
        if level.upper() not in LOG_LEVELS:
            raise HTTPException(status_code=422, detail="level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        return {"file": log_file_path().name, "enabled": _FILE_HANDLER is not None,
                "entries": read_log_entries(max(1, min(lines, 5000)), level, search[:200])}

    @app.get("/api/server/logs/audit")
    def server_audit_log(request: Request, limit: int = 200, action: str = "", actor: str = "", target: str = "",
                         since: str = "", until: str = "") -> dict[str, Any]:
        admin(request)
        try:
            return {"events": get_records().events(limit=max(1, min(limit, 2000)), since=since or None, until=until or None,
                                                   action=action, actor=actor, target=target)}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/server/logs/downloads")
    def server_download_log(request: Request) -> dict[str, Any]:
        admin(request)
        return {"deliveries": deliveries.DELIVERIES.stats(), "capacity": ctx.run_gate.stats()}
