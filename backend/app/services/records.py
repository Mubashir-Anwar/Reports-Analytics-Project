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

__all__ = ['sensitive_parameters', 'log_run', 'log_run_from_audit', 'log_download', 'audit_event', 'caller_identity']

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
            required: bool = False, on_behalf_of: str = "", run_id: str = "") -> dict[str, Any] | None:
    """Store one run. required=True (AUDIT_REQUIRED): a failure raises 503 so the report is not released unlogged.
    run_id: a background / scheduled / API job, whose row in runs already exists: it is completed, not repeated."""
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
        if run_id:
            stored = get_records().put_run({**record, "id": run_id})
        else:
            stored = get_records().add_run({**record, "runType": "interactive"})
        if channel in API_CHANNELS:
            _api_call(stored, request)
        return stored
    except Exception:  # noqa: BLE001 - the database may be down; the report decides below whether that blocks it
        LOG.exception("The run record could not be saved")
        if required:
            raise HTTPException(status_code=503, detail="The run record could not be saved; because AUDIT_REQUIRED is on, the report was not released") from None
        return None


API_CHANNELS = ("api", "embed", "signed-link")      # data that leaves the service through the API


def _api_call(run: dict[str, Any], request: Request | None, *, endpoint: str = "", http_status: int | None = None,
              bytes_returned: int | None = None) -> None:
    """One row of api_calls: which key took which report's data, how much, from where. Never blocks the answer."""
    try:
        scope = getattr(request.state, "report_scope", None) if request is not None and hasattr(request, "state") else None
        key = scope[1] if isinstance(scope, tuple) and scope[0] == "key" else None
        owner = str(run.get("actor") or "")
        key_id = (key or {}).get("id") or (owner[7:] if owner.startswith("apikey:") else None)
        code = http_status
        if code is None:
            code = 200 if run.get("status") == "success" else int(run["errorCode"]) if str(run.get("errorCode") or "").isdigit() else 500
        get_records().add_api_call({
            "apiKeyId": key_id, "caller": (key or {}).get("name") or owner or "?", "callerType": run.get("actorType") or "?",
            "channel": run.get("channel") or "api", "method": request.method if request is not None else None,
            "endpoint": endpoint or (request.url.path if request is not None else f"/api/v1/jobs/{run.get('id')}"),
            "reportId": run.get("reportId"), "folder": run.get("folder"), "runId": run.get("id"), "format": run.get("format"),
            "httpStatus": code, "rowsReturned": run.get("rowCount"),
            "bytesReturned": bytes_returned if bytes_returned is not None else run.get("outputBytes"),
            "durationMs": run.get("durationMs"), "clientIp": client_address(request) if request is not None else None,
        })
    except Exception:  # noqa: BLE001 - the data was already sent; the problem goes to the service log
        LOG.exception("The API call record could not be saved")


def log_download(ctx: SimpleNamespace, request: Request, run: dict[str, Any], size: int | None) -> None:
    """GET /api/v1/jobs/{id}/file: the file of a background API run leaves the service (an api_calls row)."""
    stored = get_records().run(run["id"]) or {"id": run["id"], "actor": run.get("owner"), "actorType": "api-key", "channel": "api"}
    _api_call({**stored, "durationMs": None}, request, http_status=200, bytes_returned=size)


def log_run_from_audit(ctx: SimpleNamespace, audit: dict[str, Any], *, channel: str = "", actor: str = "", actor_type: str = "",
                       definition: Any = None, body: bytes | None = None, request: Request | None = None,
                       required: bool = False, run_id: str = "") -> dict[str, Any] | None:
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
                       required=required, on_behalf_of=str(audit.get("GeneratedBy") or ""), run_id=run_id)


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
