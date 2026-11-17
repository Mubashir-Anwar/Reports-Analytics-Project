"""Writing to the system database: one run record per report execution, one audit event per important action.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, Request

from backend.app.db import get_records
from backend.app.db.model import masked_parameters
from backend.app.logs import LOG
from backend.app.security.access import client_address

__all__ = ['sensitive_parameters', 'log_run', 'log_run_from_audit', 'audit_event', 'caller_identity']

# how the old audit "access" value maps to a run channel and actor type
_ACCESS = {"report-token": ("api", "token"), "admin-token": ("api", "token"), "open": ("runner", "anonymous")}


def sensitive_parameters(ctx: SimpleNamespace, definition: Any) -> set[str]:
    """Parameters marked sensitive in the report (CNIC, account number): stored masked in the run log."""
    return {spec.name for spec in getattr(definition, "parameters", ()) or () if getattr(spec, "sensitive", False)}


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def log_run(ctx: SimpleNamespace, *, channel: str, actor: str, actor_type: str, status: str, started_at: datetime,
            definition: Any = None, report_id: str = "", data_source: str = "", output_format: str = "",
            parameters: dict[str, Any] | None = None, error_code: str = "", error_message: str = "", row_count: int | None = None,
            body: bytes | None = None, duration_ms: int | None = None, request: Request | None = None,
            required: bool = False, on_behalf_of: str = "") -> dict[str, Any] | None:
    """Store one run. required=True (AUDIT_REQUIRED): a failure raises 503 so the report is not released unlogged."""
    editor = getattr(definition, "editor_config", None) or {}
    record = {
        "startedAt": started_at, "finishedAt": datetime.now(timezone.utc), "durationMs": duration_ms,
        "channel": channel, "actor": actor or "?", "actorType": actor_type,
        "reportId": report_id or getattr(definition, "report_id", ""), "reportTitle": getattr(definition, "title", ""),
        "folder": editor.get("folder", ""), "dataSource": data_source or getattr(definition, "connection_id", ""),
        "format": output_format,
        "parameters": masked_parameters({k: _plain(v) for k, v in (parameters or {}).items()}, ctx.sensitive_parameters(definition)),
        "status": status, "errorCode": error_code, "errorMessage": error_message, "rowCount": row_count,
        "outputBytes": len(body) if body is not None else None,
        "outputSha256": hashlib.sha256(body).hexdigest() if body is not None else None,
        "clientIp": client_address(request) if request is not None else "", "requestPath": request.url.path if request is not None else "",
        "onBehalfOf": on_behalf_of,
    }
    try:
        return get_records().add_run(record)
    except Exception:  # noqa: BLE001 - the database may be down; the report decides below whether that blocks it
        LOG.exception("The run record could not be saved")
        if required:
            raise HTTPException(status_code=503, detail="The run record could not be saved; because AUDIT_REQUIRED is on, the report was not released") from None
        return None


def log_run_from_audit(ctx: SimpleNamespace, audit: dict[str, Any], *, channel: str = "", actor: str = "", actor_type: str = "",
                       definition: Any = None, body: bytes | None = None, request: Request | None = None,
                       required: bool = False) -> dict[str, Any] | None:
    """The older audit dictionaries (requestedAt, access, connectionId, …) as a run record."""
    access = str(audit.get("access", ""))
    if not channel or not actor_type:
        kind, _, who = access.partition(":")
        guessed = {"api-key": ("api", "api-key"), "embed": ("embed", "api-key"), "signed-link": ("signed-link", "api-key" if who else "token"),
                   "user": ("runner", "user")}.get(kind) or _ACCESS.get(access, ("api", "token"))
        channel, actor_type = channel or guessed[0], actor_type or guessed[1]
        actor = actor or who or access or "?"
    started = audit.get("requestedAt") or datetime.now(timezone.utc)
    return ctx.log_run(channel=channel, actor=actor or "?", actor_type=actor_type,
                       status="success" if audit.get("status") == "success" else "failed", started_at=started,
                       definition=definition, report_id=str(audit.get("reportId") or ""), data_source=str(audit.get("connectionId") or ""),
                       output_format=str(audit.get("format") or ""), parameters=audit.get("parameters") or {},
                       error_code=str(audit.get("errorCode") or ""), error_message=str(audit.get("errorMessage") or ""),
                       row_count=audit.get("rowCount"), body=body, duration_ms=audit.get("durationMs"), request=request,
                       required=required, on_behalf_of=str(audit.get("GeneratedBy") or ""))


def caller_identity(ctx: SimpleNamespace, request: Request | None) -> tuple[str, str]:
    """(actor, actor type) of a request: the signed-in user, an API key, a token or nobody."""
    if request is None:
        return "system", "system"
    actor = getattr(request.state, "actor", "") if hasattr(request, "state") else ""
    if actor in ("admin token", "designer token"):
        return actor, "token"
    if actor:
        return actor, "user"
    session = ctx.current_session(request)
    return (session["username"], "user") if session else ("anonymous", "anonymous")


def audit_event(ctx: SimpleNamespace, request: Request | None, action: str, target: str = "", details: Any = None, *,
                actor: str = "", actor_type: str = "") -> None:
    """Append one audit event. Never blocks the action itself; a failure is written to the service log as an error."""
    if not actor:
        actor, guessed_type = ctx.caller_identity(request)
        actor_type = actor_type or guessed_type
    try:
        get_records().audit(action, actor=actor, actor_type=actor_type or "user", target=target, details=details,
                            client_ip=client_address(request) if request is not None else "")
    except Exception:  # noqa: BLE001
        LOG.exception("Audit event %s could not be saved", action)
