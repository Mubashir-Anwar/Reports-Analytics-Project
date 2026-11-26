"""Designer tools: query parsing, JRXML import, design validate/render, draft preview and run, local bootstrap."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from backend.app import fonts as own_fonts, patterns as own_patterns
from backend.app.connectors import sql as sql_connector
from backend.app.connectors.errors import ConnectorQueryError
from backend.app.constants import OUTPUT_FORMATS, REPORT_ID_PATTERN
from backend.app.definitions import clean_folder
from backend.app.queries.mongo_query import (
    _validate_pipeline_parameter_names,
    _validate_read_only_pipeline,
    parse_query_source,
    pipeline_parameter_names,
    split_jasper_query,
)
from backend.app.queries.sql_query import prepare_sql
from backend.app.rendering import engine as designer_render


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """Add the designer endpoints to the app."""
    draft_definition = ctx.draft_definition
    json_body = ctx.json_body
    preview_definition = ctx.preview_definition
    require_admin = ctx.require_admin
    run_definition_file = ctx.run_definition_file
    sql_adapter = ctx.sql_adapter
    subreport_loader = ctx.subreport_loader
    dataset_loader = ctx.dataset_loader

    @app.post("/api/admin/query/fields")
    async def admin_read_sql_fields(request: Request) -> dict[str, Any]:
        """classic "Read Fields" for a SQL query: checks it (one read-only statement) and asks the database for the
        result columns with LIMIT 0 — no data rows are read. Parameters are NULL, $X{IN} lists empty."""
        require_admin(request, "designer")
        payload = await json_body(request)
        try:
            prepared = prepare_sql(payload.get("querySource"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        connection_id = str(payload.get("connectionId") or "").strip()
        ctx.require_area(connection_id, "designer")
        kind, url = sql_adapter(connection_id)
        try:
            columns = sql_connector.describe(kind, url, prepared)
        except ConnectorQueryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"columns": columns, "parameterNames": list(prepared.parameter_names)}

    @app.post("/api/admin/query/parse")
    async def admin_parse_query(request: Request) -> dict[str, Any]:
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send the query parse request as JSON") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="The query parse request must be a JSON object")
        try:
            pipeline = parse_query_source(payload.get("language", "aggregation-json"), payload.get("source", ""))
            if "parameters" in payload:
                raw_parameters = payload["parameters"]
                if not isinstance(raw_parameters, list) or any(not isinstance(item, dict) for item in raw_parameters):
                    raise ValueError("Query parameters must be a list of JSON objects")
                parameter_names = {str(item.get("name", "")) for item in raw_parameters}
                _validate_pipeline_parameter_names(pipeline, parameter_names)
            _validate_read_only_pipeline(pipeline)
            jasper = None if payload.get("language") == "python" else split_jasper_query(payload.get("source", ""))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"pipeline": pipeline, "stageCount": len(pipeline), "parameterNames": pipeline_parameter_names(pipeline),
                "collection": jasper[0] if jasper else ""}

    @app.post("/api/admin/drafts/preview")
    async def admin_preview_draft(request: Request) -> dict[str, Any]:
        """Live preview of a workspace draft without publishing it to the server."""
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return preview_definition(draft_definition(payload), payload, request)

    @app.post("/api/admin/drafts/run")
    async def admin_run_draft(request: Request) -> Response:
        require_admin(request, "designer")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Send a valid JSON body") from None
        return run_definition_file(draft_definition(payload), payload, request.url.path, "designer-draft", request=request)

    @app.post("/api/admin/jrxml/import")
    async def admin_import_jrxml(request: Request) -> dict[str, Any]:
        """Convert a .jrxml into a draft report + design. Nothing is saved or executed."""
        require_admin(request, "designer")
        try:
            payload = await request.json()
            xml_text = payload.get("xml") if isinstance(payload, dict) else None
            if not isinstance(xml_text, str) or not xml_text.strip():
                raise ValueError("Send the jrxml text as 'xml' in the JSON body")
            from backend.app.importers import jrxml as jrxml_import
            return jrxml_import.import_jrxml(xml_text, payload.get("resources"))   # + its image files
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/admin/jrxml/export")
    async def admin_export_jrxml(request: Request) -> dict[str, Any]:
        """The designer's report (+ design) as a .jrxml: {xml, fileName, warnings}. Nothing is saved."""
        require_admin(request, "designer")
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Send the report definition as the JSON body")
            from backend.app.exporters.jrxml_export import export_jrxml
            return export_jrxml(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/admin/fonts")
    def admin_fonts(request: Request) -> dict[str, Any]:
        """The own fonts of this server (added in the Designer, used in the canvas, PDF and Excel)."""
        require_admin(request, "designer")
        return {"fonts": own_fonts.list_fonts()}

    @app.get("/api/admin/fonts/{font_id}/file")
    def admin_font_file(font_id: str, request: Request) -> Response:
        """One font file, for the designer's canvas."""
        require_admin(request, "designer")
        raw = own_fonts.font_file(font_id)
        if raw is None:
            raise HTTPException(status_code=404, detail="There is no such font")
        return Response(raw, media_type="font/ttf", headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})

    @app.post("/api/admin/fonts")
    async def admin_add_font(request: Request) -> dict[str, Any]:
        """Add a TrueType font: {file: base64 or data URL, fileName, family?, style?}. The same family and style is replaced."""
        require_admin(request, "designer")
        payload = await json_body(request)
        from starlette.concurrency import run_in_threadpool
        try:
            font = await run_in_threadpool(own_fonts.add_font, payload.get("file"), str(payload.get("fileName") or ""),
                                           str(payload.get("family") or ""), str(payload.get("style") or ""),
                                           getattr(request.state, "actor", ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        ctx.audit_event(request, "font.added", font["family"], {"style": font["style"], "bytes": font["bytes"]})
        return {"font": font, "fonts": own_fonts.list_fonts(fresh=True)}

    @app.delete("/api/admin/fonts/{font_id}")
    def admin_delete_font(font_id: str, request: Request) -> dict[str, Any]:
        """Remove a font file (an administrator, or the person who added it). Reports with it print in SansSerif."""
        role = require_admin(request, "designer")
        found = next((item for item in own_fonts.list_fonts(fresh=True) if item["id"] == font_id), None)
        if found is None:
            raise HTTPException(status_code=404, detail="There is no such font")
        if role != "admin" and found.get("by") != getattr(request.state, "actor", ""):
            raise HTTPException(status_code=403, detail="Only an administrator or the person who added this font can remove it")
        own_fonts.delete_font(font_id)
        ctx.audit_event(request, "font.removed", found["family"], {"style": found["style"]})
        return {"fonts": own_fonts.list_fonts(fresh=True)}

    @app.get("/api/admin/patterns")
    def admin_patterns(request: Request) -> dict[str, Any]:
        """The saved format patterns of this server (Designer → Format pattern → Custom)."""
        require_admin(request, "designer")
        return {"patterns": own_patterns.list_patterns()}

    def own_pattern(pattern_id: str, request: Request) -> dict[str, Any]:
        """A saved pattern this person may change: an administrator, or the person who added it."""
        role = require_admin(request, "designer")
        found = next((item for item in own_patterns.list_patterns() if item["id"] == pattern_id), None)
        if found is None:
            raise HTTPException(status_code=404, detail="There is no such pattern")
        if role != "admin" and found.get("by") != getattr(request.state, "actor", ""):
            raise HTTPException(status_code=403, detail="Only an administrator or the person who added this pattern can change it")
        return found

    def save_own_pattern(request: Request, payload: dict[str, Any], replace: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            item = own_patterns.save_pattern(payload.get("name"), payload.get("pattern"), getattr(request.state, "actor", ""), replace)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        ctx.audit_event(request, "pattern.changed" if replace else "pattern.added", item["name"], {"pattern": item["pattern"]})
        return {"pattern": item, "patterns": own_patterns.list_patterns()}

    @app.post("/api/admin/patterns")
    async def admin_add_pattern(request: Request) -> dict[str, Any]:
        """Save a format pattern for every report: {name, pattern}, e.g. {"name": "Bank amount", "pattern": "#,##0.00"}."""
        require_admin(request, "designer")
        return save_own_pattern(request, await json_body(request))

    @app.put("/api/admin/patterns/{pattern_id}")
    async def admin_change_pattern(pattern_id: str, request: Request) -> dict[str, Any]:
        """Change a saved pattern: {name, pattern}. Reports keep the format they have."""
        return save_own_pattern(request, await json_body(request), own_pattern(pattern_id, request))

    @app.delete("/api/admin/patterns/{pattern_id}")
    def admin_delete_pattern(pattern_id: str, request: Request) -> dict[str, Any]:
        """Remove a saved pattern. Reports keep the format they have."""
        found = own_pattern(pattern_id, request)
        own_patterns.delete_pattern(pattern_id)
        ctx.audit_event(request, "pattern.removed", found["name"], {"pattern": found["pattern"]})
        return {"patterns": own_patterns.list_patterns()}

    @app.post("/api/admin/designer/import-design")
    async def admin_import_design(request: Request) -> dict[str, Any]:
        """An existing design (PDF, Illustrator .ai, Word, PNG/JPEG) → page size, background picture and elements.
        Body: {file: data URL, name, mode: "elements" | "picture", page: 1, textOnTop: true}. Nothing is saved."""
        require_admin(request, "designer")
        payload = await json_body(request)
        from starlette.concurrency import run_in_threadpool

        from backend.app.importers import design_import
        try:
            return await run_in_threadpool(
                design_import.import_design, payload.get("file"), str(payload.get("name") or "")[:200],
                str(payload.get("mode") or ""), int(payload.get("page") or 1), payload.get("textOnTop", True) is not False)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/admin/designer/validate")
    async def admin_designer_validate(request: Request) -> dict[str, Any]:
        """Check a report definition (Source tab → Apply) like the server will when it saves or runs it.
        Answers {"ok": true, "design": ...} or 422 with the first problem found."""
        require_admin(request, "designer")
        payload = await json_body(request)
        definition = payload.get("definition")
        if not isinstance(definition, dict):
            raise HTTPException(status_code=422, detail="The source must be one JSON object (the report definition)")
        if not REPORT_ID_PATTERN.fullmatch(str(definition.get("id", ""))):
            raise HTTPException(status_code=422, detail="id: use only letters, numbers, _ and - (starting with a letter or number)")
        columns = definition.get("columns", [])
        parameters = definition.get("parameters", [])
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            raise HTTPException(status_code=422, detail="columns must be a list of field names")
        if not isinstance(parameters, list) or not all(isinstance(p, dict) and p.get("name") for p in parameters):
            raise HTTPException(status_code=422, detail="parameters must be a list of objects with a name")
        try:
            design = designer_render.validate_design(definition.get("design") or {"bands": {}}, columns, {str(p["name"]) for p in parameters})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"design: {exc}") from None
        return {"ok": True, "design": design}

    @app.post("/api/admin/designer/functions/try")
    async def admin_try_function(request: Request) -> dict[str, Any]:
        """Formulas → Custom Functions → Try it: check the report's custom functions and run one expression with
        sample values. Body {"functions": [...], "expression": "FullName(\"Ali\", \"Khan\")", "values": {"Name": "x"}};
        $F{X} / $P{X} in the expression read "values". Answers {"ok": true, "value": "...", "type": "text|number|..."}."""
        require_admin(request, "designer")
        payload = await json_body(request)
        from backend.app.expressions import jexpr
        functions = payload.get("functions") or []
        expression = str(payload.get("expression", "") or "").strip()
        expression = expression[1:].strip() if expression.startswith("=") else expression
        values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
        if not isinstance(functions, list):
            raise HTTPException(status_code=422, detail="functions must be a list")
        try:
            jexpr.compile_functions(functions)
        except jexpr.JExprError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if not expression:
            return {"ok": True, "value": None, "type": "empty"}
        jexpr.use_functions(functions)
        try:
            result = jexpr.evaluate(expression, lambda kind, name: values.get(name))
        except jexpr.JExprError as exc:
            raise HTTPException(status_code=422, detail=f"The test expression failed: {exc}") from None
        kind = ("empty" if result is None else "yes/no" if isinstance(result, bool) else
                "number" if isinstance(result, (int, float)) else "text" if isinstance(result, str) else "date" if hasattr(result, "year") else "value")
        return {"ok": True, "value": jexpr.java_str(result) if result is not None else None, "type": kind}

    @app.post("/api/admin/designer/render")
    async def admin_designer_render(request: Request) -> Response:
        """Render a design for live preview as PDF, Excel or CSV. Uses supplied rows, else synthetic sample rows."""
        require_admin(request, "designer")
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("The request must be a JSON object")
            output_format = str(payload.get("format", "pdf")).lower()
            if output_format not in OUTPUT_FORMATS:
                raise ValueError("format must be pdf, xlsx or csv")
            columns = payload.get("columns", [])
            if not isinstance(columns, list) or not all(isinstance(item, str) for item in columns):
                raise ValueError("columns must be a list of strings")
            raw_parameters = payload.get("parameters", {})
            if not isinstance(raw_parameters, dict):
                raise ValueError("parameters must be a JSON object")
            design = designer_render.validate_design(payload.get("design"), columns, {str(k) for k in raw_parameters})
            rows = payload.get("rows")
            if rows is None:
                rows = designer_render.sample_rows(columns)
            if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
                raise ValueError("rows must be a list of objects")
            title = str(payload.get("title", "Report"))
            rows = rows[:2000]
            # Subreports run their published report (first the one in the same folder as this report)
            token = designer_render.SUBREPORT_LOADER.set(subreport_loader(None, clean_folder(payload.get("folder"))))
            # Tables / Crosstabs with their own dataset: run it on the chosen adapter; without one, sample records
            adapter = str(payload.get("connectionId") or "").strip()
            dataset_token = designer_render.DATASET_LOADER.set(
                dataset_loader(adapter, str(payload.get("reportId") or "preview")) if adapter else None)
            try:
                if output_format == "pdf":
                    body = designer_render.render_design_pdf(design, title, columns, rows, raw_parameters, str(payload.get("reportId", "")))
                    media_type = "application/pdf"
                else:
                    if not designer_render.has_tabular_layout(design):
                        raise ValueError("Excel/CSV output needs a Text Field or Static Text in the Detail band, "
                                         "or the Excel / CSV layout 'Page layout' (Report properties)")
                    renderer = designer_render.render_design_xlsx if output_format == "xlsx" else designer_render.render_design_csv
                    body = renderer(design, title, columns, rows, raw_parameters)
                    media_type = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  "csv": "text/csv; charset=utf-8"}[output_format]
            finally:
                designer_render.DATASET_LOADER.reset(dataset_token)
                designer_render.SUBREPORT_LOADER.reset(token)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from None
        return Response(body, media_type=media_type, headers={"Content-Disposition": f'inline; filename="preview.{output_format}"'})
