"""/analytics: datasets, the model (relationships), dashboards and the query engine.

Who may do what (accounts need the Analytics right for all of it):
    see dashboards and datasets, build visuals, run queries      everybody with the Analytics right
    create and change dashboards                                  everybody with the Analytics right (their own;
                                                                  people with the Admin right change any)
    datasets, refreshes and relationships (they read data
    adapters)                                                     Analytics + Designer or Admin right

    GET    /api/analytics/info                         what this user may do, limits, data adapters (modellers)
    GET    /api/analytics/datasets                     POST (create)        POST /api/analytics/datasets/test
    GET    /api/analytics/datasets/{id}                PUT  DELETE           GET  …/{id}/rows   GET …/{id}/refreshes
    POST   /api/analytics/datasets/{id}/refresh        import now in the background (202)
    GET    /api/analytics/relationships                POST  PUT  DELETE /api/analytics/relationships/{id}
    POST   /api/analytics/relationships/check          GET   /api/analytics/relationships/suggest
    GET    /api/analytics/dashboards                   POST  GET/PUT/DELETE /api/analytics/dashboards/{id}
    POST   /api/analytics/query                        one visual → columns and rows
    POST   /api/analytics/query-batch                  every visual of a dashboard at once (one report place)

    GET    /api/analytics/dashboards/{id}/file          the dashboard as one .mdash file (bundle.py)
    POST   /api/analytics/dashboards/import             upload a .mdash file (modellers)
    POST   /api/analytics/powerbi/inspect               a Power BI template (.pbit, base64 "content") or the report part of a
                                                        .pbix ("parts": {name: base64}): its pages, visuals and tables
    POST   /api/analytics/powerbi/import                {content | parts, name, pages, tables: {table: choice}, folder}

Report Server (/server: the Report Server right; the folder permissions of the dashboard's folder apply):
    GET    /api/server/dashboards/{id}                  the dashboard and the columns of its datasets
    POST   /api/server/dashboards/{id}/query-batch      its visuals (only the datasets the dashboard uses)
    GET    /api/server/dashboards/{id}/file             download .mdash (Manage)
    POST   /api/server/dashboards/{id}/move             to another folder (Manage on both)
    DELETE /api/server/dashboards/{id}                  (admin, Manage)
    POST   /api/server/dashboards/upload                upload a .mdash into a folder (designer, Manage)

Administration (/admin → Analytics: the Admin right, without needing the Analytics right):
    GET    /api/admin/analytics                         limits, disk use, every dataset and dashboard, recent imports
    POST   /api/admin/analytics/datasets/{id}/refresh   import now
    PUT    /api/admin/analytics/datasets/{id}/schedule  {"refreshEveryMinutes": n} (0 = only by hand)
    DELETE /api/admin/analytics/datasets/{id}           ?force=true when dashboards use it
    PUT    /api/admin/analytics/dashboards/{id}/owner   {"owner": "user"} (someone left the team)
    POST   /api/admin/analytics/cleanup                 delete Parquet files no dataset uses any more

Dashboards in a folder someone may not see (folder permissions, as for reports) are hidden in /analytics too.

Public API v1 (an API key; its folders apply to the datasets' and dashboards' folders):
    GET  /api/v1/analytics/datasets      GET /api/v1/analytics/dashboards      GET /api/v1/analytics/dashboards/{id}
    POST /api/v1/analytics/query         POST /api/v1/analytics/datasets/{id}/refresh
"""
from __future__ import annotations

import re
import shutil
import time
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.app.analytics import bundle, engine, importer, ml, model as analytics_model, powerbi, queries as analytics_queries, settings, store
from backend.app.analytics.refresher import next_refresh
from backend.app.connectors.errors import ConnectorQueryError, MongoReadAccessError
from backend.app.connectors.profiles import profiles_in
from backend.app.logs import LOG
from backend.app.security import api_keys, permissions
from backend.app.security.access import client_address, enabled_areas
from backend.app.security.users import find_user
from backend.app.services.capacity import caller_of
from backend.app.storage.files import add_saved_folder, normalize_folder_path

MAX_BATCH = 40


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    json_body = ctx.json_body
    refresher = ctx.analytics_refresher

    # ------------------------------------------------------------- who
    def user(request: Request) -> dict[str, Any]:
        return ctx.require_right(request, "analytics")

    def may_model(session: dict[str, Any]) -> bool:
        rights = set(session.get("rights") or [])
        return bool(session.get("superAdmin")) or bool(rights & {"designer", "admin"})

    def modeller(request: Request) -> dict[str, Any]:
        session = user(request)
        if not may_model(session):
            raise HTTPException(status_code=403, detail="Datasets and the model need the Designer or Admin right as well")
        return session

    def may_change(session: dict[str, Any], item: dict[str, Any]) -> bool:
        return item.get("owner") == session["username"] or "admin" in (session.get("rights") or []) or bool(session.get("superAdmin"))

    def audit(request: Request, session: dict[str, Any], action: str, target: str, details: Any = None) -> None:
        try:
            from backend.app.db import get_records  # noqa: PLC0415
            get_records().audit(action, actor=session["username"], actor_type="user", target=target, details=details,
                                client_ip=client_address(request))
        except Exception:  # noqa: BLE001 - the change is made; the audit trail reports its own problems
            LOG.exception("Audit event %s could not be written", action)

    def adapters() -> dict[str, str]:
        return {item["id"]: item.get("type", "mongodb") for item in profiles_in("analytics")}

    async def body(request: Request) -> dict[str, Any]:
        ctx.require_json_request(request)
        return await json_body(request)

    def bad(exc: Exception) -> HTTPException:
        return HTTPException(status_code=422, detail=str(exc))

    # ------------------------------------------------------------- views
    def dataset_view(item: dict[str, Any], full: bool) -> dict[str, Any]:
        view = {name: item.get(name) for name in ("id", "name", "folder", "description", "columns", "status", "rowCount",
                                                  "sizeBytes", "refreshedAt", "nextRefreshAt", "lastError",
                                                  "refreshEveryMinutes", "createdBy", "createdAt", "updatedBy", "updatedAt")}
        view["columns"] = view["columns"] or []
        view["status"] = view["status"] or "new"
        view["busy"] = refresher.busy(item["id"])
        if full:                                  # the source (query text) only for people who may change it
            view.update({name: item.get(name) for name in ("connectionId", "queryLanguage", "collection", "querySource")})
        return view

    def find_dataset(dataset_id: str) -> dict[str, Any]:
        item = store.dataset(dataset_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return item

    def find_dashboard(dashboard_id: str) -> dict[str, Any]:
        item = store.dashboard(dashboard_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        return item

    def dashboard_view(item: dict[str, Any], session: dict[str, Any] | None) -> dict[str, Any]:
        view = {**item, "tiles": item.get("tiles") or [], "filters": item.get("filters") or []}
        if session is not None:
            view["mayChange"] = may_change(session, item)
        return view

    def sees(session: dict[str, Any], item: dict[str, Any], needed: int = permissions.RUN, rules: dict | None = None) -> bool:
        return permissions.level(session, item.get("folder") or "Analytics", rules) >= needed

    def visible_dashboard(session: dict[str, Any], dashboard_id: str) -> dict[str, Any]:
        item = find_dashboard(dashboard_id)
        if not sees(session, item):
            raise HTTPException(status_code=404, detail="Dashboard not found")
        return item

    def file_response(item: dict[str, Any], with_data: bool = False) -> JSONResponse:
        name = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in item.get("name") or "dashboard").strip() or "dashboard"
        try:
            content = bundle.export(item, with_data=with_data)
        except ValueError as exc:
            raise bad(exc) from None
        suffix = " (with data)" if with_data else ""
        return JSONResponse(content, headers={"Content-Disposition": f'attachment; filename="{name}{suffix}.mdash"'})

    def install(request: Request, session: dict[str, Any], payload: dict[str, Any], default_folder: str) -> dict[str, Any]:
        try:
            folder = normalize_folder_path(payload.get("folder") or default_folder)
            content = bundle.read(payload.get("content"))
            result = bundle.install(content, folder=folder, actor=session["username"], adapters=adapters(),
                                    connection_id=str(payload.get("connectionId") or ""), replace=payload.get("replace") is True)
        except (ValueError, TypeError) as exc:
            raise bad(exc) from None
        add_saved_folder(folder)
        for record in result.pop("newDatasets"):
            refresher.request(record, "upload", session["username"])
        board = result["dashboard"]
        audit(request, session, "analytics.dashboard.uploaded", board["id"],
              {"name": board["name"], "folder": folder, "datasetsCreated": result["created"], "replaced": result["replaced"]})
        return {**result, "dashboard": dashboard_view(board, session)}

    def powerbi_template(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if payload.get("parts") is not None:                  # a .pbix: only its report part, taken out by the browser
                return powerbi.open_parts(payload.get("parts"))
            return powerbi.open_template(payload.get("content"))
        except (ValueError, TypeError) as exc:
            raise bad(exc) from None

    def report_name(payload: dict[str, Any]) -> str:
        name = str(payload.get("name") or "").strip()
        return re.sub(r"\.pbi[tx]$", "", name, flags=re.I).strip()[:80]

    def run_queries(visuals: list[Any], filters: Any, caller: str) -> list[dict[str, Any]]:
        try:
            cleaned = []
            for index, visual in enumerate(visuals):
                clean = store.clean_visual(visual, f"queries[{index}]")
                clean["tileId"] = str((visual or {}).get("tileId") or "")[:40]
                cleaned.append(clean)
            dashboard_filters = [store.clean_filter(item, f"filters[{i}]") for i, item in enumerate(filters or [])]
        except (ValueError, TypeError) as exc:
            raise bad(exc) from None
        with ctx.run_gate.slot(caller):
            return engine.run(cleaned, dashboard_filters)

    # ------------------------------------------------------------- info
    @app.get("/api/analytics/info")
    def info(request: Request) -> dict[str, Any]:
        session = user(request)
        modelling = may_model(session)
        return {"user": session["username"], "mayModel": modelling, "limits": settings.limits(),
                "visuals": list(store.VISUALS), "models": list(ml.MODELS), "aggregations": list(store.AGGREGATIONS), "buckets": [b for b in store.BUCKETS if b],
                "operators": list(store.OPERATORS), "languages": list(store.LANGUAGES),
                "adapters": [{"id": item["id"], "name": item.get("name", item["id"]), "type": item.get("type", "mongodb")}
                             for item in profiles_in("analytics")] if modelling else []}

    # ------------------------------------------------------------- datasets
    @app.get("/api/analytics/datasets")
    def list_datasets(request: Request) -> dict[str, Any]:
        session = user(request)
        return {"datasets": [dataset_view(item, may_model(session)) for item in store.datasets()]}

    @app.post("/api/analytics/datasets/test")
    async def test_dataset(request: Request) -> dict[str, Any]:
        modeller(request)
        payload = await body(request)
        try:
            source = store.clean_source(payload, adapters())
            with ctx.run_gate.slot(caller_of("", user=ctx.current_session(request)["username"])):
                return importer.preview(ctx, {**source, "name": "test"})
        except HTTPException:
            raise
        except (ValueError, ConnectorQueryError, MongoReadAccessError) as exc:
            raise bad(exc) from None

    @app.post("/api/analytics/datasets")
    async def create_dataset(request: Request) -> JSONResponse:
        session = modeller(request)
        payload = await body(request)
        try:
            clean = store.clean_dataset(payload, adapters())
        except ValueError as exc:
            raise bad(exc) from None
        when = store.now()
        record = {"id": store.new_id("ds"), **clean, "columns": [], "status": "new", "rowCount": 0, "sizeBytes": 0,
                  "createdBy": session["username"], "createdAt": when, "updatedBy": session["username"], "updatedAt": when}
        record["nextRefreshAt"] = next_refresh(record)
        store.save_dataset(record)
        audit(request, session, "analytics.dataset.created", record["id"], {"name": record["name"], "adapter": record["connectionId"]})
        if payload.get("refresh", True):
            record = refresher.request(record, "manual", session["username"])
        return JSONResponse({"dataset": dataset_view(record, True)}, status_code=201)

    @app.get("/api/analytics/datasets/{dataset_id}")
    def get_dataset(request: Request, dataset_id: str) -> dict[str, Any]:
        session = user(request)
        return {"dataset": dataset_view(find_dataset(dataset_id), may_model(session))}

    @app.put("/api/analytics/datasets/{dataset_id}")
    async def update_dataset(request: Request, dataset_id: str) -> dict[str, Any]:
        session = modeller(request)
        current = find_dataset(dataset_id)
        payload = await body(request)
        try:
            clean = store.clean_dataset(payload, adapters(), current)
        except ValueError as exc:
            raise bad(exc) from None
        source_changed = any(clean[name] != current.get(name) for name in ("connectionId", "queryLanguage", "querySource", "collection"))
        record = {**current, **clean, "updatedBy": session["username"], "updatedAt": store.now()}
        if clean["refreshEveryMinutes"] != current.get("refreshEveryMinutes"):
            record["nextRefreshAt"] = next_refresh(record)
        store.save_dataset(record)
        audit(request, session, "analytics.dataset.changed", dataset_id, {"name": record["name"], "sourceChanged": source_changed})
        if source_changed and payload.get("refresh", True):
            record = refresher.request(record, "manual", session["username"])
        return {"dataset": dataset_view(record, True), "sourceChanged": source_changed}

    @app.delete("/api/analytics/datasets/{dataset_id}")
    async def delete_dataset(request: Request, dataset_id: str, force: bool = False) -> dict[str, Any]:
        session = modeller(request)
        ctx.require_json_request(request)
        item = find_dataset(dataset_id)
        # a dataset counts as used wherever a dashboard names it: a visual's own dataset, its group-by / values /
        # filters (a related dataset) or a page filter, the same check as the Admin delete
        users_of = [d["name"] for d in store.dashboards() if dataset_id in bundle.used_datasets(d)]
        if users_of and not force:
            raise HTTPException(status_code=409, detail=f"Used by the dashboard(s) {', '.join(users_of[:5])}: "
                                                        "remove those visuals first, or delete with force=true")
        store.delete_dataset(dataset_id)
        importer.remove_old_files(dataset_id, keep="")
        audit(request, session, "analytics.dataset.deleted", dataset_id, {"name": item["name"]})
        return {"deleted": dataset_id, "dashboardsAffected": users_of}

    @app.post("/api/analytics/datasets/{dataset_id}/refresh")
    async def refresh_dataset(request: Request, dataset_id: str) -> JSONResponse:
        session = modeller(request)
        ctx.require_json_request(request)
        item = refresher.request(find_dataset(dataset_id), "manual", session["username"])
        return JSONResponse({"dataset": dataset_view(item, True)}, status_code=202)

    @app.get("/api/analytics/datasets/{dataset_id}/refreshes")
    def dataset_refreshes(request: Request, dataset_id: str) -> dict[str, Any]:
        user(request)
        find_dataset(dataset_id)
        return {"refreshes": store.refreshes(dataset_id)}

    @app.get("/api/analytics/datasets/{dataset_id}/rows")
    def dataset_rows(request: Request, dataset_id: str, limit: int = 50) -> dict[str, Any]:
        session = user(request)
        item = find_dataset(dataset_id)
        with ctx.run_gate.slot(caller_of("", user=session["username"])):
            return engine.sample(item, max(1, min(limit, 500)))

    # ------------------------------------------------------------- relationships
    @app.get("/api/analytics/relationships")
    def list_relationships(request: Request) -> dict[str, Any]:
        user(request)
        return {"relationships": store.relationships()}

    @app.post("/api/analytics/relationships")
    async def create_relationship(request: Request) -> JSONResponse:
        session = modeller(request)
        payload = await body(request)
        try:
            clean = store.clean_relationship(payload)
        except ValueError as exc:
            raise bad(exc) from None
        record = {"id": store.new_id("rel"), **clean, "createdBy": session["username"], "createdAt": store.now()}
        store.save_relationship(record)
        audit(request, session, "analytics.relationship.created", record["id"], clean)
        return JSONResponse({"relationship": record}, status_code=201)

    def model_sides(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        left, right = store.dataset(str(payload.get("fromDataset") or "")), store.dataset(str(payload.get("toDataset") or ""))
        if left is None or right is None:
            raise HTTPException(status_code=422, detail="Choose both datasets")
        return left, right

    @app.post("/api/analytics/relationships/check")
    async def check_relationship(request: Request) -> dict[str, Any]:
        """How two columns join on the data (keys found on both sides, cardinality, advice) — before saving."""
        session = modeller(request)
        payload = await body(request)
        left, right = model_sides(payload)
        try:
            with ctx.run_gate.slot(caller_of("", user=session["username"])):
                return analytics_model.check(left, str(payload.get("fromColumn") or ""), right, str(payload.get("toColumn") or ""))
        except engine.QueryError as exc:
            raise bad(exc) from None

    @app.get("/api/analytics/relationships/suggest")
    def suggest_relationships(request: Request) -> dict[str, Any]:
        """Pairs of columns that look like keys of each other, checked on the data."""
        session = modeller(request)
        with ctx.run_gate.slot(caller_of("", user=session["username"])):
            return {"suggestions": analytics_model.suggest(store.datasets(), store.relationships())}

    @app.put("/api/analytics/relationships/{rel_id}")
    async def update_relationship(request: Request, rel_id: str) -> dict[str, Any]:
        session = modeller(request)
        current = store.relationship(rel_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Relationship not found")
        try:
            clean = store.clean_relationship(await body(request), current)
        except ValueError as exc:
            raise bad(exc) from None
        record = store.save_relationship({**current, **clean})
        audit(request, session, "analytics.relationship.updated", rel_id, clean)
        return {"relationship": record}

    @app.delete("/api/analytics/relationships/{rel_id}")
    async def delete_relationship(request: Request, rel_id: str) -> dict[str, Any]:
        session = modeller(request)
        ctx.require_json_request(request)
        if not store.delete_relationship(rel_id):
            raise HTTPException(status_code=404, detail="Relationship not found")
        audit(request, session, "analytics.relationship.deleted", rel_id)
        return {"deleted": rel_id}

    # ------------------------------------------------------------- dashboards
    @app.get("/api/analytics/dashboards")
    def list_dashboards(request: Request) -> dict[str, Any]:
        session = user(request)
        rules = permissions.load_rules()
        return {"dashboards": [{name: item.get(name) for name in ("id", "name", "folder", "description", "owner", "updatedAt", "updatedBy")}
                               | {"tiles": len(item.get("tiles") or []), "mayChange": may_change(session, item)}
                               for item in store.dashboards() if sees(session, item, rules=rules)]}

    @app.post("/api/analytics/dashboards")
    async def create_dashboard(request: Request) -> JSONResponse:
        session = user(request)
        payload = await body(request)
        try:
            clean = store.clean_dashboard(payload)
        except (ValueError, TypeError) as exc:
            raise bad(exc) from None
        when = store.now()
        record = {"id": store.new_id("dash"), **clean, "owner": session["username"], "createdAt": when,
                  "updatedBy": session["username"], "updatedAt": when}
        store.save_dashboard(record)
        add_saved_folder(record["folder"])
        audit(request, session, "analytics.dashboard.created", record["id"], {"name": record["name"]})
        return JSONResponse({"dashboard": dashboard_view(record, session)}, status_code=201)

    @app.get("/api/analytics/dashboards/{dashboard_id}")
    def get_dashboard(request: Request, dashboard_id: str) -> dict[str, Any]:
        session = user(request)
        return {"dashboard": dashboard_view(visible_dashboard(session, dashboard_id), session)}

    @app.put("/api/analytics/dashboards/{dashboard_id}")
    async def update_dashboard(request: Request, dashboard_id: str) -> dict[str, Any]:
        session = user(request)
        current = visible_dashboard(session, dashboard_id)
        if not may_change(session, current):
            raise HTTPException(status_code=403, detail=f"Only {current.get('owner')} or an administrator may change this dashboard")
        payload = await body(request)
        try:
            clean = store.clean_dashboard(payload, current)
        except (ValueError, TypeError) as exc:
            raise bad(exc) from None
        record = {**current, **clean, "updatedBy": session["username"], "updatedAt": store.now()}
        store.save_dashboard(record)
        add_saved_folder(record["folder"])
        audit(request, session, "analytics.dashboard.changed", dashboard_id, {"name": record["name"], "tiles": len(record["tiles"])})
        return {"dashboard": dashboard_view(record, session)}

    @app.delete("/api/analytics/dashboards/{dashboard_id}")
    async def delete_dashboard(request: Request, dashboard_id: str) -> dict[str, Any]:
        session = user(request)
        ctx.require_json_request(request)
        current = visible_dashboard(session, dashboard_id)
        if not may_change(session, current):
            raise HTTPException(status_code=403, detail=f"Only {current.get('owner')} or an administrator may delete this dashboard")
        store.delete_dashboard(dashboard_id)
        audit(request, session, "analytics.dashboard.deleted", dashboard_id, {"name": current["name"]})
        return {"deleted": dashboard_id}

    @app.get("/api/analytics/dashboards/{dashboard_id}/file")
    def export_dashboard(request: Request, dashboard_id: str, data: bool = False) -> JSONResponse:
        """?data=true: with the imported records of its datasets (opens on a server without the database)."""
        session = user(request)
        item = visible_dashboard(session, dashboard_id)
        if not (may_model(session) or may_change(session, item)):      # the file holds the datasets' queries
            raise HTTPException(status_code=403, detail="Downloading a dashboard needs the Designer or Admin right, or owning it")
        return file_response(item, with_data=data)

    @app.post("/api/analytics/dashboards/import")
    async def import_dashboard(request: Request) -> JSONResponse:
        session = modeller(request)
        payload = await body(request)
        return JSONResponse(install(request, session, payload, "Analytics"), status_code=201)

    @app.post("/api/analytics/powerbi/inspect")
    async def powerbi_inspect(request: Request) -> dict[str, Any]:
        modeller(request)
        payload = await body(request)
        template = powerbi_template(payload)
        return {"name": report_name(payload), **powerbi.inspect(template, store.datasets(), adapters()),
                "adapters": [{"id": key, "type": kind} for key, kind in adapters().items()]}

    @app.post("/api/analytics/powerbi/import")
    async def powerbi_import(request: Request) -> JSONResponse:
        session = modeller(request)
        payload = await body(request)
        template = powerbi_template(payload)
        name = report_name(payload)
        try:
            folder = normalize_folder_path(payload.get("folder") or "Analytics")
            pages = payload.get("pages")
            bundles, notes = powerbi.build(template, report_name=name, mapping=payload.get("tables") or {},
                                           page_indexes=pages if isinstance(pages, list) else list(range(len(template["pages"]))))
            result = powerbi.install(bundles, folder=folder, actor=session["username"], adapters=adapters())
        except (ValueError, TypeError) as exc:
            raise bad(exc) from None
        add_saved_folder(folder)
        for record in result.pop("newDatasets"):
            refresher.request(record, "upload", session["username"])
        audit(request, session, "analytics.powerbi.imported", ",".join(board["id"] for board in result["dashboards"]),
              {"template": name, "folder": folder, "dashboards": [board["name"] for board in result["dashboards"]],
               "datasetsCreated": result["created"]})
        return JSONResponse({**result, "dashboards": [dashboard_view(board, session) for board in result["dashboards"]],
                             "notes": notes[:100]}, status_code=201)

    # ------------------------------------------------------------- Report Server (/server)
    def server_dashboard(request: Request, dashboard_id: str, needed: int = permissions.RUN,
                         minimum: str = "viewer") -> tuple[dict[str, Any], dict[str, Any]]:
        session = ctx.require_session(request, minimum)
        item = store.dashboard(dashboard_id)
        if item is None or not sees(session, item):
            raise HTTPException(status_code=404, detail="This dashboard is not on the server")
        if not sees(session, item, needed):
            raise HTTPException(status_code=403, detail=f"Changing the dashboards in /{item.get('folder')} needs the Manage permission")
        return session, item

    @app.get("/api/server/dashboards/{dashboard_id}")
    def server_get_dashboard(request: Request, dashboard_id: str) -> dict[str, Any]:
        session, item = server_dashboard(request, dashboard_id)
        used = bundle.used_datasets(item)
        datasets = [dataset_view(found, False) for found in (store.dataset(i) for i in used) if found is not None]
        level = permissions.level(session, item.get("folder") or "Analytics")
        return {"dashboard": {**dashboard_view(item, None), "mayChange": False}, "datasets": datasets,
                "relationships": [rel for rel in store.relationships() if rel["fromDataset"] in used and rel["toDataset"] in used],
                "access": permissions.NAMES[level], "mayOpenAnalytics": "analytics" in session["rights"]}

    @app.post("/api/server/dashboards/{dashboard_id}/query-batch")
    async def server_query_dashboard(request: Request, dashboard_id: str) -> dict[str, Any]:
        session, item = server_dashboard(request, dashboard_id)
        payload = await json_body(request)
        queries = payload.get("queries")
        if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_BATCH:
            raise HTTPException(status_code=422, detail=f"Send 'queries': a list of 1 to {MAX_BATCH} visuals")
        allowed = set(bundle.used_datasets(item))      # a viewer may only ask what the dashboard shows
        asked = set(bundle.used_datasets({"tiles": [q for q in queries if isinstance(q, dict)],
                                          "filters": payload.get("filters") if isinstance(payload.get("filters"), list) else []}))
        if not asked <= allowed:
            raise HTTPException(status_code=403, detail="These visuals use a dataset this dashboard does not use")
        return {"results": run_queries(queries, payload.get("filters"), caller_of("", user=session["username"]))}

    @app.get("/api/server/dashboards/{dashboard_id}/file")
    def server_dashboard_file(request: Request, dashboard_id: str, data: bool = False) -> JSONResponse:
        _session, item = server_dashboard(request, dashboard_id, permissions.MANAGE, "designer")
        return file_response(item, with_data=data)

    @app.post("/api/server/dashboards/{dashboard_id}/move")
    async def server_move_dashboard(request: Request, dashboard_id: str) -> dict[str, Any]:
        session, item = server_dashboard(request, dashboard_id, permissions.MANAGE, "designer")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("folder"))
        except ValueError as exc:
            raise bad(exc) from None
        ctx.require_folder(session, folder, permissions.MANAGE)
        add_saved_folder(folder)
        record = store.save_dashboard({**item, "folder": folder, "updatedBy": session["username"], "updatedAt": store.now()})
        audit(request, session, "analytics.dashboard.moved", dashboard_id, {"name": item["name"], "from": item.get("folder"), "to": folder})
        return {"dashboard": dashboard_view(record, None)}

    @app.delete("/api/server/dashboards/{dashboard_id}")
    def server_delete_dashboard(request: Request, dashboard_id: str) -> dict[str, Any]:
        session, item = server_dashboard(request, dashboard_id, permissions.MANAGE, "admin")
        store.delete_dashboard(dashboard_id)
        audit(request, session, "analytics.dashboard.deleted", dashboard_id, {"name": item["name"], "from": "server"})
        return {"deleted": dashboard_id}

    @app.post("/api/server/dashboards/upload")
    async def server_upload_dashboard(request: Request) -> JSONResponse:
        session = ctx.require_session(request, "designer")
        payload = await json_body(request)
        try:
            folder = normalize_folder_path(payload.get("folder") or "General")
        except ValueError as exc:
            raise bad(exc) from None
        ctx.require_folder(session, folder, permissions.MANAGE)
        return JSONResponse(install(request, session, {**payload, "folder": folder}, folder), status_code=201)

    # ------------------------------------------------------------- inside another website (routers/embed.py checks the token)
    def embedded_dashboard(item: dict[str, Any]) -> dict[str, Any]:
        """What a visitor of another website gets: the layout, without the names of people or the filters' values."""
        return {"id": item["id"], "name": item.get("name"), "description": item.get("description") or "", "filters": [], "mayChange": False,
                "tiles": [{**{k: v for k, v in tile.items() if k != "filters"}, "filters": []} for tile in item.get("tiles") or []]}

    def embedded_query(item: dict[str, Any], tile_ids: Any, picks: Any, fixed: list[dict[str, Any]], caller: str) -> list[dict[str, Any]]:
        """Answers for tiles of a dashboard as it was saved. A visitor sends only which tiles, and values picked in the
        dashboard's own group-by fields; `fixed` filters (from the token) apply to every tile."""
        tiles = {tile["id"]: tile for tile in item.get("tiles") or []}
        if not isinstance(tile_ids, list) or not 1 <= len(tile_ids) <= MAX_BATCH or any(not isinstance(t, str) or t not in tiles for t in tile_ids):
            raise HTTPException(status_code=422, detail="Send 'tiles': the IDs of visuals of this dashboard")
        if picks is not None and not isinstance(picks, list):
            raise HTTPException(status_code=422, detail="filters must be a list")
        offered = {(tile["groupBy"][0].get("dataset") or tile.get("dataset"), tile["groupBy"][0]["column"], tile["groupBy"][0].get("bucket", ""))
                   for tile in tiles.values() if tile.get("groupBy")}
        chosen = []
        for pick in picks or []:
            if (isinstance(pick, dict) and pick.get("op") == "in"
                    and (pick.get("dataset"), pick.get("column"), pick.get("bucket") or "") in offered):
                chosen.append(pick)
        queries = []
        for tile_id in tile_ids:
            tile = tiles[tile_id]
            query = {**tile, "tileId": tile_id, "filters": [*(tile.get("filters") or []), *fixed]}
            if tile.get("type") == "slicer":
                query.update({"groupBy": tile["groupBy"][:1], "values": [{"agg": "countrows", "column": ""}], "sort": [{"by": "group:0", "dir": "asc"}], "limit": 500})
            queries.append(query)
        return run_queries(queries, [*(item.get("filters") or []), *chosen], caller)

    ctx.embedded_dashboard, ctx.embedded_query = embedded_dashboard, embedded_query

    # ------------------------------------------------------------- administration (/admin → Analytics)
    def admin(request: Request) -> str:
        ctx.require_admin(request)
        session = getattr(request.state, "session", None)
        return (session or {}).get("username") or getattr(request.state, "actor", "") or "admin"

    def admin_audit(request: Request, actor: str, action: str, target: str, details: Any = None) -> None:
        audit(request, {"username": actor}, action, target, details)

    def stray_files() -> list[Any]:
        """Parquet files no dataset points to (left by a crash, or by a dataset deleted while its file was open)."""
        kept = {item.get("file") for item in store.datasets()}
        out = []
        for path in settings.directory().glob("*.parquet"):
            match = importer.FILE_PATTERN.fullmatch(path.name)
            if path.name in kept or (match and refresher.busy(match.group(1))):
                continue
            out.append(path)
        return out

    @app.get("/api/admin/analytics")
    def admin_analytics(request: Request) -> dict[str, Any]:
        admin(request)
        datasets = store.datasets()
        dashboards = store.dashboards()
        used_by: dict[str, list[str]] = {}
        for board in dashboards:
            for dataset_id in bundle.used_datasets(board):
                used_by.setdefault(dataset_id, []).append(board.get("name") or board["id"])
        folder = settings.directory()
        files = list(folder.glob("*.parquet"))
        strays = stray_files()
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            free = None
        recent = []
        for item in datasets:
            for entry in store.refreshes(item["id"], limit=10):
                recent.append({**entry, "dataset": item.get("name")})
        recent.sort(key=lambda entry: entry.get("startedAt") or "", reverse=True)
        rows = [dataset_view(item, True) | {"dashboards": used_by.get(item["id"], [])} for item in datasets]
        rows.sort(key=lambda item: (item.get("name") or "").casefold())
        return {
            "enabled": "analytics" in enabled_areas(),
            "limits": settings.limits(),
            "storage": {"directory": str(folder), "files": len(files), "bytes": sum(path.stat().st_size for path in files),
                        "strayFiles": len(strays), "strayBytes": sum(path.stat().st_size for path in strays), "freeBytes": free},
            "totals": {"datasets": len(datasets), "ready": sum(1 for item in datasets if item.get("status") == "ready"),
                       "failed": sum(1 for item in datasets if item.get("status") == "failed" or item.get("lastError")),
                       "busy": sum(1 for item in datasets if refresher.busy(item["id"])),
                       "scheduled": sum(1 for item in datasets if item.get("refreshEveryMinutes")),
                       "rows": sum(int(item.get("rowCount") or 0) for item in datasets),
                       "dashboards": len(dashboards), "relationships": len(store.relationships())},
            "datasets": rows,
            "dashboards": sorted(({name: board.get(name) for name in ("id", "name", "folder", "owner", "updatedAt", "updatedBy")}
                                  | {"tiles": len(board.get("tiles") or []),
                                     "ownerExists": bool(board.get("owner")) and find_user(str(board.get("owner"))) is not None}
                                  for board in dashboards), key=lambda item: (item.get("name") or "").casefold()),
            "recentImports": recent[:30],
        }

    @app.post("/api/admin/analytics/datasets/{dataset_id}/refresh")
    def admin_refresh(request: Request, dataset_id: str) -> JSONResponse:
        actor = admin(request)
        ctx.require_json_request(request)
        item = refresher.request(find_dataset(dataset_id), "admin", actor)
        return JSONResponse({"dataset": dataset_view(item, True)}, status_code=202)

    @app.put("/api/admin/analytics/datasets/{dataset_id}/schedule")
    async def admin_schedule(request: Request, dataset_id: str) -> dict[str, Any]:
        actor = admin(request)
        current = find_dataset(dataset_id)
        payload = await body(request)
        try:
            clean = store.clean_dataset({"refreshEveryMinutes": payload.get("refreshEveryMinutes")}, adapters(), current)
        except (ValueError, TypeError) as exc:
            raise bad(exc) from None
        record = {**current, "refreshEveryMinutes": clean["refreshEveryMinutes"], "updatedBy": actor, "updatedAt": store.now()}
        record["nextRefreshAt"] = next_refresh(record)
        store.save_dataset(record)
        admin_audit(request, actor, "analytics.dataset.changed", dataset_id,
                    {"name": record["name"], "refreshEveryMinutes": record["refreshEveryMinutes"], "by": "admin"})
        return {"dataset": dataset_view(record, True)}

    @app.delete("/api/admin/analytics/datasets/{dataset_id}")
    def admin_delete_dataset(request: Request, dataset_id: str, force: bool = False) -> dict[str, Any]:
        actor = admin(request)
        ctx.require_json_request(request)
        item = find_dataset(dataset_id)
        users_of = [d["name"] for d in store.dashboards() if dataset_id in bundle.used_datasets(d)]
        if users_of and not force:
            raise HTTPException(status_code=409, detail=f"Used by the dashboard(s) {', '.join(users_of[:5])}: delete with force=true to remove it anyway")
        store.delete_dataset(dataset_id)
        importer.remove_old_files(dataset_id, keep="")
        admin_audit(request, actor, "analytics.dataset.deleted", dataset_id, {"name": item["name"], "by": "admin"})
        return {"deleted": dataset_id, "dashboardsAffected": users_of}

    @app.put("/api/admin/analytics/dashboards/{dashboard_id}/owner")
    async def admin_owner(request: Request, dashboard_id: str) -> dict[str, Any]:
        actor = admin(request)
        current = find_dashboard(dashboard_id)
        payload = await body(request)
        owner = str(payload.get("owner") or "").strip()
        user_record = find_user(owner) if owner else None
        if user_record is None:
            raise HTTPException(status_code=422, detail=f"There is no user '{owner[:64]}'")
        record = store.save_dashboard({**current, "owner": user_record["username"], "updatedBy": actor, "updatedAt": store.now()})
        admin_audit(request, actor, "analytics.dashboard.owner_changed", dashboard_id,
                    {"name": current["name"], "from": current.get("owner"), "to": user_record["username"]})
        return {"dashboard": dashboard_view(record, None)}

    @app.post("/api/admin/analytics/cleanup")
    def admin_cleanup(request: Request) -> dict[str, Any]:
        actor = admin(request)
        ctx.require_json_request(request)
        removed, freed, kept = 0, 0, 0
        for path in stray_files():
            try:
                size = path.stat().st_size
                path.unlink()
                removed, freed = removed + 1, freed + size
            except OSError:                              # still open (Windows): next time
                kept += 1
        if removed:
            admin_audit(request, actor, "analytics.files.cleaned", "analytics", {"files": removed, "bytes": freed})
        return {"removed": removed, "freedBytes": freed, "stillOpen": kept}

    # ------------------------------------------------------------- Queries (DAX / SQL), saved
    def query_view(item: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        return {**item, "mayChange": may_change(session, item)}

    def visible_query(session: dict[str, Any], query_id: str) -> dict[str, Any]:
        item = store.saved_query(query_id)
        if item is None or not sees(session, item):
            raise HTTPException(status_code=404, detail="Query not found")
        return item

    def run_source(session: dict[str, Any], item: dict[str, Any], limit: Any = None) -> dict[str, Any]:
        """A read-only query on the data adapter itself (MongoDB pipeline or the database's SQL): the first rows."""
        if not may_model(session):
            raise HTTPException(status_code=403, detail="Queries that read a database directly are run by modellers. "
                                "Ask one to make a dataset from this query, then use DAX or SQL over the dataset")
        try:
            cap = max(1, min(int(limit or 1000), 10_000))
        except (TypeError, ValueError):
            cap = 1000
        started = time.monotonic()
        try:
            source = store.clean_source({"connectionId": item.get("connectionId"), "queryLanguage": item.get("queryLanguage"),
                                         "collection": item.get("collection"), "querySource": item.get("text")}, adapters())
            with ctx.run_gate.slot(caller_of("", user=session["username"])):
                found = importer.preview(ctx, {**source, "name": "query"}, limit=cap + 1)
        except HTTPException:
            raise
        except (ValueError, ConnectorQueryError, MongoReadAccessError) as exc:
            raise bad(exc) from None
        rows = found["rows"][:cap]

        def kind(index: int) -> str:
            values = [row[index] for row in rows if row[index] is not None][:50]
            if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                return "number"
            if values and all(isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}(T|$)", v) for v in values):
                return "date"
            return "text"
        columns = [{"name": name, "type": kind(i)} for i, name in enumerate(found["columns"])]
        return {"language": "source", "queryLanguage": source["queryLanguage"], "connectionId": source["connectionId"],
                "collection": source["collection"], "sql": "", "columns": columns, "rows": rows,
                "rowCount": len(rows), "truncated": len(found["rows"]) > cap,
                "durationMs": int((time.monotonic() - started) * 1000), "tables": []}

    def run_text(session: dict[str, Any], language: str, text: str, limit: Any = None, item: dict[str, Any] | None = None) -> dict[str, Any]:
        if str(language or "").lower() == "source":
            return run_source(session, {**(item or {}), "text": text}, limit)
        rules = permissions.load_rules()
        mine = [d for d in store.datasets() if sees(session, d, rules=rules)]
        try:
            with ctx.run_gate.slot(caller_of("", user=session["username"])):
                return analytics_queries.run(language, text, mine, int(limit) if limit else None, store.relationships())
        except (engine.QueryError, engine.QueryTimeout, ValueError) as exc:
            raise bad(exc) from None

    @app.get("/api/analytics/queries")
    def list_queries(request: Request) -> dict[str, Any]:
        session = user(request)
        rules = permissions.load_rules()
        return {"queries": [query_view(q, session) for q in store.queries() if sees(session, q, rules=rules)]}

    @app.post("/api/analytics/queries/run")
    async def run_query_text(request: Request) -> dict[str, Any]:
        """Run a query that is being written (not saved): {"language": "dax" | "sql", "text": "EVALUATE …"}, or
        {"language": "source", "connectionId", "queryLanguage": "mongosh" | "aggregation-json" | "python" | "javascript" | "sql",
        "collection", "text"} to read the data adapter itself (read-only, the first rows)."""
        session = modeller(request)
        payload = await body(request)
        return run_text(session, payload.get("language"), payload.get("text"), payload.get("limit"), payload)

    @app.post("/api/analytics/queries")
    async def create_query(request: Request) -> dict[str, Any]:
        session = modeller(request)
        payload = await body(request)
        try:
            clean = store.clean_query(payload, adapters=adapters())
        except ValueError as exc:
            raise bad(exc) from None
        if not sees(session, clean, permissions.MANAGE) and not may_model(session):
            raise HTTPException(status_code=403, detail="You may not save queries in this folder")
        record = {"id": store.new_id("qry"), **clean, "owner": session["username"], "createdAt": store.now(),
                  "updatedBy": session["username"], "updatedAt": store.now()}
        store.save_query(record)
        audit(request, session, "analytics.query.created", record["id"], {"name": record["name"], "language": record["language"]})
        return {"query": query_view(record, session)}

    @app.get("/api/analytics/queries/{query_id}")
    def get_query(query_id: str, request: Request) -> dict[str, Any]:
        session = user(request)
        return {"query": query_view(visible_query(session, query_id), session)}

    @app.put("/api/analytics/queries/{query_id}")
    async def update_query(query_id: str, request: Request) -> dict[str, Any]:
        session = modeller(request)
        current = visible_query(session, query_id)
        if not may_change(session, current):
            raise HTTPException(status_code=403, detail="Only its owner or an administrator may change this query")
        try:
            clean = store.clean_query(await body(request), current, adapters=adapters())
        except ValueError as exc:
            raise bad(exc) from None
        record = {**current, **clean, "updatedBy": session["username"], "updatedAt": store.now()}
        store.save_query(record)
        audit(request, session, "analytics.query.updated", query_id, {"name": record["name"]})
        return {"query": query_view(record, session)}

    @app.delete("/api/analytics/queries/{query_id}")
    def delete_query(query_id: str, request: Request) -> dict[str, Any]:
        session = modeller(request)
        current = visible_query(session, query_id)
        if not may_change(session, current):
            raise HTTPException(status_code=403, detail="Only its owner or an administrator may delete this query")
        store.delete_query(query_id)
        audit(request, session, "analytics.query.deleted", query_id, {"name": current["name"]})
        return {"deleted": query_id}

    @app.post("/api/analytics/queries/{query_id}/run")
    def run_saved_query(query_id: str, request: Request) -> dict[str, Any]:
        """Anyone with the Analytics right who sees the query's folder may run it (over the datasets they see)."""
        session = user(request)
        item = visible_query(session, query_id)
        return {**run_text(session, item["language"], item["text"], item=item), "query": query_view(item, session)}

    # ------------------------------------------------------------- queries
    @app.post("/api/analytics/query")
    async def query(request: Request) -> dict[str, Any]:
        session = user(request)
        payload = await body(request)
        result = run_queries([payload], payload.get("dashboardFilters"), caller_of("", user=session["username"]))[0]
        if "error" in result:
            raise HTTPException(status_code=422, detail=result["error"])
        return result

    @app.post("/api/analytics/query-batch")
    async def query_batch(request: Request) -> dict[str, Any]:
        session = user(request)
        payload = await body(request)
        queries = payload.get("queries")
        if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_BATCH:
            raise HTTPException(status_code=422, detail=f"Send 'queries': a list of 1 to {MAX_BATCH} visuals")
        return {"results": run_queries(queries, payload.get("filters"), caller_of("", user=session["username"]))}

    # ------------------------------------------------------------- public API v1
    def api_key(request: Request) -> dict[str, Any]:
        ctx.require_report_access(request)
        scope = getattr(request.state, "report_scope", None)
        if scope is None or scope[0] != "key":
            raise HTTPException(status_code=403, detail="Analytics in the public API needs an API key (X-API-Key)")
        return scope[1]

    def key_sees(key: dict[str, Any], item: dict[str, Any] | None) -> bool:
        return item is not None and api_keys.folder_allowed(key, item.get("folder") or "Analytics")

    @app.get("/api/v1/analytics/datasets", tags=["Public API v1"])
    def v1_datasets(request: Request) -> dict[str, Any]:
        """Datasets this key may query (in its folders): names, columns, status, row count."""
        key = api_key(request)
        return {"datasets": [dataset_view(item, False) for item in store.datasets() if key_sees(key, item)]}

    @app.get("/api/v1/analytics/dashboards", tags=["Public API v1"])
    def v1_dashboards(request: Request) -> dict[str, Any]:
        """Dashboards in this key's folders."""
        key = api_key(request)
        return {"dashboards": [{name: item.get(name) for name in ("id", "name", "folder", "description", "updatedAt")}
                               | {"tiles": len(item.get("tiles") or [])} for item in store.dashboards() if key_sees(key, item)]}

    @app.get("/api/v1/analytics/dashboards/{dashboard_id}", tags=["Public API v1"])
    def v1_dashboard(request: Request, dashboard_id: str) -> dict[str, Any]:
        """One dashboard: its tiles (visual definitions) and filters, to draw it in your own page."""
        key = api_key(request)
        item = store.dashboard(dashboard_id)
        if not key_sees(key, item):
            raise HTTPException(status_code=404, detail="Dashboard not found")
        return {"dashboard": dashboard_view(item, None)}

    @app.post("/api/v1/analytics/query", tags=["Public API v1"])
    async def v1_query(request: Request) -> dict[str, Any]:
        """One visual: {"dataset", "groupBy": [{"column", "bucket"}], "values": [{"column", "agg"}], "filters": [...]}
        → {"columns", "rows"}. Every dataset it touches must be in the key's folders."""
        key = api_key(request)
        payload = await json_body(request)
        touched = {payload.get("dataset")} | {f.get("dataset") for part in ("groupBy", "values", "filters", "columns")
                                               for f in payload.get(part) or [] if isinstance(f, dict) and f.get("dataset")}
        if not all(key_sees(key, store.dataset(str(item))) for item in touched if item):
            raise HTTPException(status_code=404, detail="Dataset not found")
        result = run_queries([payload], [], f"api-key:{key['id']}")[0]
        if "error" in result:
            raise HTTPException(status_code=422, detail=result["error"])
        return result

    @app.post("/api/v1/analytics/datasets/{dataset_id}/refresh", tags=["Public API v1"])
    def v1_refresh(request: Request, dataset_id: str) -> JSONResponse:
        """Import the dataset again now (in the background; 202). Poll GET /api/v1/analytics/datasets for its status."""
        key = api_key(request)
        item = store.dataset(dataset_id)
        if not key_sees(key, item):
            raise HTTPException(status_code=404, detail="Dataset not found")
        item = refresher.request(item, "api", f"api-key:{key.get('name', key['id'])}")
        return JSONResponse({"dataset": dataset_view(item, False)}, status_code=202)

