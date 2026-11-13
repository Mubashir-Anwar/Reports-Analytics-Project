"""Report access for websites: /reports and /generate/<ID>.<format>."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pymongo.errors import PyMongoError

from backend.app.connectors.errors import ConnectorQueryError, MongoReadAccessError
from backend.app.connectors.mongo_fallback import _row_value
from backend.app.constants import METADATA_KEYS, OUTPUT_FORMATS, REPORT_PATH, SIGNED_LINK_KEYS
from backend.app.definitions import ReportDefinition, validate_parameters
from backend.app.logs import LOG
from backend.app.rendering.outputs import render_report_output
from backend.app.storage import audit as audit_store


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the generate endpoints to the app."""
    get_connection = ctx.get_connection
    run_report_rows = ctx.run_report_rows
    published_report_summaries = ctx.published_report_summaries
    reports_by_id = ctx.reports_by_id
    require_report_access = ctx.require_report_access
    settings = ctx.settings
    subreport_loader = ctx.subreport_loader

    @app.get("/reports")
    def list_reports(request: Request) -> dict[str, Any]:
        require_report_access(request)
        return {"reports": published_report_summaries()}

    @app.get("/generate/{report_file}")
    def generate(report_file: str, request: Request) -> Response:
        # Checked before anything else, so an unauthenticated caller learns nothing (not even
        # whether a report ID exists) and cannot fill the audit file.
        access = require_report_access(request, report_file)
        started = time.monotonic()
        audit: dict[str, Any] = {
            "requestedAt": datetime.now(timezone.utc),
            "path": request.url.path,
            "status": "failed",
            "rowCount": 0,
            "access": access,
        }
        query_pairs = [(key, value) for key, value in request.query_params.multi_items() if key not in SIGNED_LINK_KEYS]
        query_map = dict(query_pairs)
        for key in METADATA_KEYS:
            if key in query_map:
                audit[key] = query_map[key]
        definition: ReportDefinition | None = None
        audit_saved = False
        try:
            match = REPORT_PATH.fullmatch(report_file)
            if not match:
                raise HTTPException(status_code=400, detail="The path must be /generate/<REPORT_ID>.<pdf|xlsx|csv>")
            report_id, output_format = match.group(1), match.group(2).lower()
            definition = reports_by_id.get(report_id)
            if definition is None:
                raise HTTPException(status_code=404, detail="Report ID is not registered")
            enabled_formats = (definition.editor_config or {}).get("formats", sorted(OUTPUT_FORMATS))
            if output_format not in enabled_formats:
                raise HTTPException(status_code=400, detail=f"The {output_format.upper()} format is not enabled for this report")
            audit.update({"reportId": report_id, "format": output_format})
            parameters, metadata = validate_parameters(definition, query_pairs)
            audit.update(metadata)
            audit["parameters"] = {key: value.isoformat() if isinstance(value, date) else value for key, value in parameters.items()}
            # MongoDB pipeline or SQL query, on the report's own data adapter (413 above the row limit)
            rows = run_report_rows(definition, parameters, definition.connection_id, max_rows=settings.max_rows, limit_rows=False)
            rows = [{key: _row_value(row, key) for key in definition.columns} for row in rows]
            body, media_type = render_report_output(definition, parameters, rows, output_format, subreport_loader())
            audit.update({"status": "success", "rowCount": len(rows)})
            if settings.audit_required:
                # Fail closed: the report is released only after its audit record is stored.
                audit["durationMs"] = int((time.monotonic() - started) * 1000)
                try:
                    audit_store._append_report_audit(audit)
                    audit_saved = True
                except OSError:
                    LOG.exception("Required audit record could not be saved")
                    raise HTTPException(status_code=503, detail="The audit record could not be saved; because AUDIT_REQUIRED is on, the report was not released") from None
            filename = f"{definition.report_id}.{output_format}"
            return Response(body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        except HTTPException as exc:
            audit["errorCode"] = str(exc.status_code)
            audit["errorMessage"] = str(exc.detail)[:300]
            raise
        except ValueError as exc:
            audit["errorCode"] = "validation_error"
            audit["errorMessage"] = str(exc)[:300]
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except MongoReadAccessError as exc:
            audit["errorCode"] = "mongodb_read_access_unavailable"
            audit["errorMessage"] = str(exc)[:300]
            LOG.warning("MongoDB report cannot be read with the configured account")
            raise HTTPException(
                status_code=503,
                detail=f"{exc} Report data source ID: '{definition.connection_id}'.",
            ) from None
        except PyMongoError as exc:
            audit["errorCode"] = "mongodb_error"
            audit["errorMessage"] = type(exc).__name__
            LOG.exception("MongoDB report execution failed")
            raise HTTPException(status_code=503, detail="The MongoDB report query failed") from None
        except ConnectorQueryError as exc:
            audit["errorCode"] = "sql_error"
            audit["errorMessage"] = str(exc)[:300]
            LOG.warning("SQL report %s failed: %s", report_id if definition else "?", exc)
            raise HTTPException(status_code=503, detail="The report query failed; the service log has the details") from None
        except RuntimeError as exc:
            audit["errorCode"] = "export_dependency_missing"
            audit["errorMessage"] = str(exc)[:300]
            raise HTTPException(status_code=501, detail=str(exc)) from None
        except Exception as exc:
            audit["errorCode"] = "internal_error"
            audit["errorMessage"] = type(exc).__name__
            LOG.exception("Unexpected report execution error")
            raise HTTPException(status_code=500, detail="The report could not be generated") from None
        finally:
            audit["durationMs"] = int((time.monotonic() - started) * 1000)
            if audit.get("reportId") and not audit_saved:
                try:
                    audit_store._append_report_audit(audit)
                except OSError:
                    LOG.exception("Local report audit record could not be saved")
                if settings.audit_to_mongodb and settings.audit_database:
                    try:
                        audit_client, _ = get_connection("default")
                        audit_client[settings.audit_database][settings.audit_collection].insert_one(audit)
                    except Exception:
                        # Optional MongoDB audit storage must never block a report
                        # generated with a read-only reporting identity.
                        LOG.exception("Optional MongoDB report audit write failed")
