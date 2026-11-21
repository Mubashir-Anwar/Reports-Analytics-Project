"""Running reports: parameters of a run, the query, subreports, drafts, previews and files.

Every function takes the service context `ctx` first; create_context() binds them onto ctx."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterator

from fastapi import HTTPException
from fastapi.responses import Response
from pymongo.errors import PyMongoError

from backend.app.connectors import mongodb, sql as sql_connector
from backend.app.connectors.errors import ConnectorQueryError, MongoReadAccessError
from backend.app.connectors.mongo_fallback import _row_value
from backend.app.constants import METADATA_KEYS, OUTPUT_FORMATS
from backend.app.definitions import AmbiguousReport, ReportDefinition, _dynamic_report, _safe_value, compile_dataset, validate_parameters
from backend.app.logs import LOG
from backend.app.queries.mongo_query import resolve_collection
from backend.app.rendering import engine as designer_render
from backend.app.rendering.outputs import render_report_output
from backend.app.services.capacity import caller_of

__all__ = ['admin_parameters', 'run_report_rows', 'iter_report_rows', 'too_many_rows', 'subreport_target', 'subreport_loader', 'dataset_loader', 'draft_definition', 'preview_definition', 'run_definition_file']


def admin_parameters(ctx: SimpleNamespace, payload: dict[str, Any], definition: ReportDefinition) -> dict[str, Any]:
    raw_params = payload.get("parameters", {})
    if not isinstance(raw_params, dict):
        raise ValueError("parameters must be a JSON object")
    prompted = {spec.name for spec in definition.parameters if spec.prompt}
    # computed parameters are calculated on the server; ignore any value the UI sends for them
    pairs = [(str(key), str(value)) for key, value in raw_params.items() if key in prompted or key in METADATA_KEYS]
    parameters, _ = validate_parameters(definition, pairs)
    return parameters


def run_report_rows(ctx: SimpleNamespace, definition: ReportDefinition, parameters: dict[str, Any], connection_id: str, *, max_rows: int, limit_rows: bool) -> list[dict[str, Any]]:
    if definition.query_language == "none":
        return [{}]                                    # no data: the page prints once (fixed text, parameters)
    if definition.query_language == "sql":
        kind, url = ctx.sql_adapter(connection_id)
        rows = sql_connector.run_query(kind, url, definition.sql, parameters, max_rows=max_rows, timeout_ms=ctx.settings.max_time_ms)
        if not limit_rows and len(rows) > max_rows:
            raise HTTPException(status_code=413, detail=too_many_rows(ctx, max_rows))
        return rows[:max_rows] if limit_rows else rows
    if ctx.adapter_kind(connection_id) != "mongodb":
        raise HTTPException(status_code=422, detail=f"Data adapter '{connection_id}' is a SQL database; this report has a MongoDB query")
    pipeline = [*definition.pipeline_builder(parameters), {"$limit": max_rows if limit_rows else max_rows + 1}]
    client, database = ctx.get_connection(connection_id)
    rows = mongodb._execute_report_pipeline(
        client[database][resolve_collection(definition.collection, parameters)],
        pipeline,
        max_time_ms=ctx.settings.max_time_ms,
        allow_disk_use=ctx.settings.allow_disk_use,
        max_rows=max_rows,
    )
    if not limit_rows and len(rows) > max_rows:
        raise HTTPException(status_code=413, detail=too_many_rows(ctx, max_rows))
    return rows


def too_many_rows(ctx: SimpleNamespace, max_rows: int) -> str:
    return (f"The report has more than {max_rows:,} records, the most one run makes straight away. Run it in the "
            f"background instead (/server: Run in background; API: POST /api/v1/reports/<id>/jobs): CSV and Excel files "
            f"of up to {ctx.settings.export_max_rows:,} records are then written to disk while the records are read. "
            f"Or narrow the parameters (a shorter date range).")


def iter_report_rows(ctx: SimpleNamespace, definition: ReportDefinition, parameters: dict[str, Any], connection_id: str, *,
                     limit: int, timeout_ms: int | None = None) -> Iterator[dict[str, Any]]:
    """The report's records one by one, read from the database in batches (big exports: memory stays small).
    Stops with ValueError after `limit` records. Uses the longer REPORT_EXPORT_MAX_TIME_MS time limit (or timeout_ms)."""
    timeout = timeout_ms or ctx.settings.export_max_time_ms
    if definition.query_language == "none":
        yield {}
        return
    if definition.query_language == "sql":
        kind, url = ctx.sql_adapter(connection_id)
        rows: Iterator[dict[str, Any]] = sql_connector.iter_query(kind, url, definition.sql, parameters, timeout_ms=timeout)
    else:
        if ctx.adapter_kind(connection_id) != "mongodb":
            raise ValueError(f"Data adapter '{connection_id}' is a SQL database; this report has a MongoDB query")
        client, database = ctx.get_connection(connection_id)
        pipeline = [*definition.pipeline_builder(parameters), {"$limit": limit + 1}]
        rows = mongodb.iter_report_pipeline(client[database][resolve_collection(definition.collection, parameters)], pipeline,
                                            max_time_ms=timeout, allow_disk_use=ctx.settings.allow_disk_use)
    for count, row in enumerate(rows, start=1):
        if count > limit:
            close = getattr(rows, "close", None)
            if close:
                close()
            raise ValueError(f"The report has more than {limit:,} records (REPORT_EXPORT_MAX_ROWS); narrow the parameters")
        yield row


def subreport_target(ctx: SimpleNamespace, parent: Any, report_id: str) -> ReportDefinition | None:
    """The report a Subreport element names: the one with this ID in the parent report's folder first, otherwise the
    only published report with this ID. Raises AmbiguousReport when the ID is in several other folders."""
    folder = parent.folder if isinstance(parent, ReportDefinition) else parent
    if folder:
        found = dict.get(ctx.reports_by_id, f"{folder}/{report_id}")
        if found is not None:
            return found
    return ctx.reports_by_id.find(report_id)


def subreport_loader(ctx: SimpleNamespace, allowed: Any = None, folder: str | None = None) -> Any:
    """Loader for Subreport elements during one render: runs a published report with the given parameters.
    `folder` is the main report's folder (a subreport there wins over one with the same ID elsewhere).
    Results are cached per (report, parameters); at most 200 different runs per report."""
    cache: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}

    def load(report_id: str, values: dict[str, Any]) -> dict[str, Any]:
        key = (report_id, tuple(sorted((str(k), str(v)) for k, v in values.items())))
        if key in cache:
            return cache[key]
        if len(cache) >= 200:
            raise ValueError("more than 200 different subreport runs in one report")
        try:
            definition = ctx.subreport_target(folder, report_id)
        except AmbiguousReport as exc:
            raise ValueError(f"the report ID {report_id} is in several folders ({', '.join(exc.folders[:3])}); "
                             "publish the subreport in the same folder as this report") from None
        if definition is None or (allowed is not None and not allowed(definition)):
            raise ValueError("this report is not published on the server")  # hidden by a folder permission looks the same
        design = (definition.editor_config or {}).get("design")
        if not design:
            raise ValueError("this report has no band design (open it in the designer and publish it)")
        prompted = {spec.name for spec in definition.parameters if spec.prompt}
        parameters, _ = validate_parameters(definition, [(k, str(v)) for k, v in values.items() if k in prompted])
        found = ctx.run_report_rows(definition, parameters, definition.connection_id, max_rows=min(ctx.settings.max_rows, 5000), limit_rows=True)
        columns = list(definition.columns)
        sub_rows = [{column: _safe_value(_row_value(row, column)) for column in columns} for row in found]
        sub_parameters = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in parameters.items()}
        validated = designer_render.validate_design(design, columns, {spec.name for spec in definition.parameters} | set(sub_parameters))
        # the subreport's own datasets read from the subreport's data adapter unless they name one
        validated["datasets"] = [{**d, "connectionId": d["connectionId"] or definition.connection_id} for d in validated["datasets"]]
        cache[key] = {"design": validated, "title": definition.title, "columns": columns, "rows": sub_rows, "parameters": sub_parameters}
        return cache[key]
    return load


def dataset_loader(ctx: SimpleNamespace, connection_id: str, report_id: str = "dataset") -> Any:
    """Loader for Tables / Crosstabs with their own dataset (Jasper datasetRun) during one render. A dataset reads
    from its own data adapter, else the report's. Results are cached per (dataset, parameters); at most 200 runs."""
    cache: dict[tuple[str, tuple[tuple[str, str], ...]], list[dict[str, Any]]] = {}

    def load(dataset: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
        key = (dataset["name"], tuple(sorted((str(k), str(v)) for k, v in values.items())))
        if key in cache:
            return cache[key]
        if len(cache) >= 200:
            raise ValueError("more than 200 different dataset runs in one report")
        definition = compile_dataset(dataset, report_id, connection_id)
        types = {p["name"]: p["type"] for p in dataset["parameters"]}
        pairs = [(k, str(v)[:10] if types.get(k) == "date" else str(v).lower() if isinstance(v, bool) else str(v))
                 for k, v in values.items() if k in types]
        parameters, _ = validate_parameters(definition, pairs)
        try:
            found = ctx.run_report_rows(definition, parameters, definition.connection_id,
                                        max_rows=min(ctx.settings.max_rows, 5000), limit_rows=True)
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from None
        except MongoReadAccessError as exc:
            raise ValueError(ctx.access_error_detail(exc, definition.connection_id)) from None
        except PyMongoError:
            LOG.exception("Dataset %s query failed", dataset["name"])
            raise ValueError("the MongoDB query failed; see the service log") from None
        columns = list(definition.columns)
        cache[key] = [{column: _safe_value(_row_value(row, column)) for column in columns} for row in found]
        return cache[key]
    return load


def draft_definition(ctx: SimpleNamespace, payload: Any) -> ReportDefinition:
    """A designer draft (not published) turned into a runnable definition, in memory only."""
    raw = payload.get("definition") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Send the draft report as 'definition'")
    try:
        return _dynamic_report(ctx.strip_workspace_meta(raw))
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"The draft is not complete yet: {exc}") from None


def preview_definition(ctx: SimpleNamespace, definition: ReportDefinition, payload: Any, request: Any = None) -> dict[str, Any]:
    """Preview rows for the designer; every preview reads real data, so it is recorded in the run log too."""
    started, clock = datetime.now(timezone.utc), time.monotonic()
    actor, actor_type = ctx.caller_identity(request)
    common = {"channel": "designer-preview", "actor": actor, "actor_type": actor_type, "started_at": started,
              "definition": definition, "request": request,
              "parameters": (payload or {}).get("parameters") if isinstance(payload, dict) and isinstance(payload.get("parameters"), dict) else {}}
    try:
        result = _preview_rows(ctx, definition, payload)
    except HTTPException as exc:
        ctx.log_run(status="failed", error_code=str(exc.status_code), error_message=str(exc.detail)[:300],
                    duration_ms=int((time.monotonic() - clock) * 1000), **common)
        raise
    ctx.log_run(status="success", row_count=result["rowCount"], data_source=result["connectionId"],
                duration_ms=int((time.monotonic() - clock) * 1000), **common)
    return result


def _preview_rows(ctx: SimpleNamespace, definition: ReportDefinition, payload: Any) -> dict[str, Any]:
    connection_id = definition.connection_id
    try:
        if not isinstance(payload, dict):
            raise ValueError("The request must be a JSON object")
        connection_id = ctx.chosen_connection(payload, definition)
        parameters = ctx.admin_parameters(payload, definition)
        rows = ctx.run_report_rows(definition, parameters, connection_id, max_rows=min(ctx.settings.max_rows, 20), limit_rows=True)
        safe_rows = [{key: _safe_value(_row_value(row, key)) for key in definition.columns} for row in rows]
        layout = (definition.editor_config or {}).get("layout", {})
        return {"columns": list(definition.columns), "labels": layout.get("columnLabels", {}), "layout": layout,
                "rows": safe_rows, "rowCount": len(safe_rows), "connectionId": connection_id}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ConnectorQueryError as exc:
        raise HTTPException(status_code=503, detail=f"{exc} (data adapter '{connection_id}')") from None
    except MongoReadAccessError as exc:
        LOG.warning("Admin preview cannot read report collections with the configured MongoDB identity")
        raise HTTPException(status_code=503, detail=ctx.access_error_detail(exc, connection_id)) from None
    except PyMongoError:
        LOG.exception("Admin report preview failed")
        raise HTTPException(status_code=503, detail="The MongoDB preview query failed; see the service log (Logs tab)") from None


def run_definition_file(ctx: SimpleNamespace, definition: ReportDefinition, payload: Any, path: str, access: str, *,
                        fixed_connection: bool = False, user: str = "", session: dict[str, Any] | None = None,
                        request: Any = None) -> Response:
    """Run a report in full and return the file. fixed_connection=True (the /server portal)
    always uses the report's own data adapter and only its enabled formats."""
    started = time.monotonic()
    audit: dict[str, Any] = {"requestedAt": datetime.now(timezone.utc), "path": path, "reportId": definition.report_id,
                             "status": "failed", "rowCount": 0, "access": access}
    if user:
        audit["GeneratedBy"] = user
    connection_id = definition.connection_id
    body: bytes | None = None
    try:
        if not isinstance(payload, dict):
            raise ValueError("The request must be a JSON object")
        output_format = str(payload.get("format", "pdf")).lower()
        if output_format not in OUTPUT_FORMATS:
            raise ValueError("format must be pdf, xlsx or csv")
        if fixed_connection:
            enabled_formats = (definition.editor_config or {}).get("formats", sorted(OUTPUT_FORMATS))
            if output_format not in enabled_formats:
                raise ValueError(f"The {output_format.upper()} format is not enabled for this report")
        else:
            connection_id = ctx.chosen_connection(payload, definition)
        parameters = ctx.admin_parameters(payload, definition)
        audit.update({"format": output_format, "connectionId": connection_id,
                      "parameters": {k: v.isoformat() if isinstance(v, date) else v for k, v in parameters.items()}})
        who = user or (ctx.caller_identity(request)[0] if request is not None else "")
        with ctx.run_gate.slot(caller_of(access, who)):
            rows = ctx.run_report_rows(definition, parameters, connection_id, max_rows=ctx.settings.max_rows, limit_rows=False)
            rows = [{key: _row_value(row, key) for key in definition.columns} for row in rows]
            body, media_type = render_report_output(definition, parameters, rows, output_format,
                                                    ctx.subreport_loader(ctx.report_allowed_for(session), definition.folder),
                                                    ctx.dataset_loader(connection_id, definition.report_id))
            row_count = len(rows)
            del rows
        audit.update({"status": "success", "rowCount": row_count})
        return Response(body, media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="{definition.report_id}.{output_format}"'})
    except HTTPException as exc:
        audit["errorCode"] = str(exc.status_code)
        raise
    except ValueError as exc:
        audit["errorCode"] = "validation_error"
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ConnectorQueryError as exc:
        audit["errorCode"] = "sql_error"
        raise HTTPException(status_code=503, detail=f"{exc} (data adapter '{connection_id}')") from None
    except MongoReadAccessError as exc:
        audit["errorCode"] = "mongodb_read_access_unavailable"
        raise HTTPException(status_code=503, detail=ctx.access_error_detail(exc, connection_id)) from None
    except PyMongoError:
        audit["errorCode"] = "mongodb_error"
        LOG.exception("Admin report run failed")
        raise HTTPException(status_code=503, detail="The MongoDB report query failed; see the service log (Logs tab)") from None
    finally:
        audit["durationMs"] = int((time.monotonic() - started) * 1000)
        actor, actor_type = (user, "user") if user else ctx.caller_identity(request)
        ctx.log_run_from_audit(audit, channel="server" if access == "server-session" else "designer-run",
                               actor=actor, actor_type=actor_type, definition=definition, body=body, request=request)
