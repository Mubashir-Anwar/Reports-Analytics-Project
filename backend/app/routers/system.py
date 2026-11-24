"""Health, adapter status, who-am-i, logs and run history."""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.app.connectors.profiles import load_connection_profiles
from backend.app.constants import SERVICE_VERSION
from backend.app.db import get_records
from backend.app.logs import _FILE_HANDLER, LOG_LEVELS, log_file_path, read_log_entries
from backend.app.security.access import enabled_areas


def storage_label() -> str:
    """Where the service keeps its data, for the status page (no passwords)."""
    from backend.app.db.urls import dialect_label  # noqa: PLC0415
    from backend.app.storage.store import storage_settings  # noqa: PLC0415
    backend, url = storage_settings()
    if backend == "files":
        return "Files in the data folder (one instance)"
    if backend == "mongodb":
        return "MongoDB (shared, several instances)"
    return f"{dialect_label(url)} (shared, several instances)"


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

    @app.get("/ready")
    def ready() -> JSONResponse:
        """Readiness for a load balancer: the storage (files or PostgreSQL) answers. MongoDB is checked by /health."""
        from backend.app.storage.store import get_store
        try:
            store = get_store()
            store.read_json("connections.json")
        except Exception as exc:  # noqa: BLE001 - any storage failure means "not ready"
            return JSONResponse({"status": "not_ready", "storage": "unavailable", "detail": type(exc).__name__}, status_code=503)
        runner = ctx.job_runner
        return JSONResponse({"status": "ready", "version": SERVICE_VERSION, "storage": store.backend,
                             "scheduler": "on" if runner.scheduler_on else "off"})

    @app.get("/api/admin/system/status")
    def admin_system_status(request: Request) -> dict[str, Any]:
        """Production checklist for /admin → Overview: what is set up, what still needs attention."""
        require_admin(request)
        from backend.app.jobs import mailer
        from backend.app.security.access import enabled_areas
        from backend.app.security.users import bootstrap_login_active
        from backend.app.storage.store import get_store
        store = get_store()
        from backend.app.security.secrets import secrets_encrypted  # noqa: PLC0415
        encrypted = secrets_encrypted()
        env = os.environ
        checks = [
            {"id": "storage", "ok": store.backend != "files", "label": "Storage",
             "value": storage_label(),
             "hint": "REPORTING_STORAGE=sql (SQL Server, Oracle, MySQL, PostgreSQL) or mongodb, for several instances and backups"},
            {"id": "secrets", "ok": encrypted, "label": "Adapter passwords",
             "value": "encrypted (secrets table)" if encrypted else "plain text in the private secrets table",
             "hint": "Set REPORTING_SECRETS_KEY to encrypt them"},
            {"id": "session", "ok": bool(env.get("REPORTING_SESSION_SECRET", "").strip()), "label": "Session secret",
             "value": "set" if env.get("REPORTING_SESSION_SECRET", "").strip() else "random per start (sign-ins end at restart)",
             "hint": "Set REPORTING_SESSION_SECRET (the same on every instance)"},
            {"id": "admin-user", "ok": not bootstrap_login_active(), "label": "Admin account",
             "value": "named admin user exists" if not bootstrap_login_active() else "only the first-setup login (admin + token)",
             "hint": "Create an admin user on the Users tab"},
            {"id": "https", "ok": env.get("REPORTING_SECURE_COOKIES", "").lower() == "true", "label": "HTTPS cookies",
             "value": "secure cookies on" if env.get("REPORTING_SECURE_COOKIES", "").lower() == "true" else "off",
             "hint": "Behind HTTPS set REPORTING_SECURE_COOKIES=true and REPORTING_HSTS=true"},
            {"id": "docs", "ok": "docs" not in enabled_areas(), "label": "API docs",
             "value": "off" if "docs" not in enabled_areas() else "on (/docs)", "hint": "Leave docs out of REPORTING_ENABLED_AREAS in production"},
            {"id": "scheduler", "ok": ctx.job_runner.scheduler_on, "label": "Scheduler",
             "value": "on" if ctx.job_runner.scheduler_on else "off on this instance", "hint": "Exactly one instance should run it"},
            {"id": "email", "ok": mailer.configured(), "label": "E-mail for schedules",
             "value": "configured" if mailer.configured() else "not configured", "hint": "SMTP_HOST and SMTP_FROM"},
        ]
        return {"version": SERVICE_VERSION, "storage": store.backend, "checks": checks}

    @app.get("/reports/connections/{connection_id}/status")
    def report_connection_status(connection_id: str, request: Request) -> dict[str, Any]:
        """Is the data adapter of the report the user picked reachable?"""
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
        session = getattr(request.state, "session", None)
        return {"role": role, "version": SERVICE_VERSION, "areas": sorted(enabled_areas()),
                "user": getattr(request.state, "actor", ""), "rights": sorted(session["rights"]) if session else [],
                "superAdmin": bool(session["superAdmin"]) if session else role == "admin"}

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
    def admin_audit(request: Request, limit: int = 200, reportId: str = "", actor: str = "", channel: str = "",  # noqa: N803
                    status: str = "", since: str = "", until: str = "") -> dict[str, Any]:
        """Report runs from the system database, newest first (filters are optional)."""
        require_admin(request)
        try:
            records = get_records().runs(limit=limit, since=since or None, until=until or None, reportId=reportId,
                                         actor=actor, channel=channel, status=status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"records": records}

    @app.get("/api/admin/audit-events")
    def admin_audit_events(request: Request, limit: int = 200, action: str = "", actor: str = "", target: str = "",
                           since: str = "", until: str = "") -> dict[str, Any]:
        """The audit trail (sign-ins, accounts, rights, API keys, publishing, deletes, permissions), newest first."""
        require_admin(request)
        try:
            events = get_records().events(limit=limit, since=since or None, until=until or None, action=action, actor=actor, target=target)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"events": events}

    @app.get("/api/admin/audit-events/verify")
    def admin_verify_audit(request: Request) -> dict[str, Any]:
        """Re-check the hash chain: a changed or removed event shows up here."""
        require_admin(request)
        return get_records().verify_chain()
