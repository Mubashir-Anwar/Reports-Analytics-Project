"""/admin → System database and Developer tools.

  * GET  /api/admin/system-db             where the service keeps its own data now (and a saved choice for the next start)
  * POST /api/admin/system-db/test        connect to another database (SQL Server, PostgreSQL, MySQL, Oracle, SQLite, MongoDB)
  * POST /api/admin/system-db/switch      copy the current data there and save the choice (.env); used after a restart
  * GET  /api/admin/postman/collection.json, /api/admin/postman/environment.json   the Postman files for this server

Choosing the database is for super admins only (it holds every account and secret)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.app.constants import BRAND_NAME, PROJECT_ROOT
from backend.app.logs import LOG
from backend.app.storage import switch

POSTMAN_DIR = PROJECT_ROOT / "postman"
COLLECTION = "Reporting-Service.postman_collection.json"
ENVIRONMENT = "Local.postman_environment.json"


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    require_admin = ctx.require_admin
    json_body = ctx.json_body

    def require_super_admin(request: Request) -> None:
        require_admin(request)
        session = getattr(request.state, "session", None)
        if session is not None and not session.get("superAdmin"):
            raise HTTPException(status_code=403, detail="Only a super admin may choose the system database")

    def form_url(payload: dict[str, Any]) -> str:
        try:
            return switch.build_url(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/admin/system-db")
    def system_db(request: Request) -> dict[str, Any]:
        require_admin(request)
        session = getattr(request.state, "session", None)
        return {**switch.current(), "youMayChange": session is None or bool(session.get("superAdmin"))}

    @app.post("/api/admin/system-db/test")
    async def system_db_test(request: Request) -> dict[str, Any]:
        require_super_admin(request)
        url = form_url(await json_body(request))
        return switch.test(url)

    @app.post("/api/admin/system-db/switch")
    async def system_db_switch(request: Request) -> dict[str, Any]:
        require_super_admin(request)
        payload = await json_body(request)
        url = form_url(payload)
        from backend.app.storage.store import storage_settings  # noqa: PLC0415
        if url == storage_settings()[1]:
            raise HTTPException(status_code=409, detail="The service already uses this database")
        checked = switch.test(url)
        if not checked["ok"]:
            raise HTTPException(status_code=400, detail=f"Cannot use this database: {checked['message']}")
        copied: dict[str, int] | None = None
        if payload.get("copy", True):
            try:
                copied = switch.copy_current_to(url, overwrite=bool(payload.get("overwrite")))
            except Exception as exc:  # noqa: BLE001
                LOG.exception("Copying the data to the new system database failed")
                raise HTTPException(status_code=500, detail=f"Copying the data failed: {switch._clean_error(exc, url)}") from None
        saved = switch.save_choice(url)
        details = {"to": checked["label"], "url": checked["url"], "copied": copied, "saved": saved["saved"]}
        ctx.audit_event(request, "storage.switched", checked["label"], details)
        LOG.warning("System database chosen in Admin: %s (%s); restart to use it", checked["label"], checked["url"])
        return {**saved, "copied": copied, "target": {k: checked[k] for k in ("backend", "label", "family", "url")}}

    def postman_file(name: str) -> dict[str, Any]:
        path = POSTMAN_DIR / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="The Postman files are not on this server (postman/ folder)")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/api/admin/postman/collection.json")
    def postman_collection(request: Request) -> JSONResponse:
        require_admin(request)
        return JSONResponse(postman_file(COLLECTION), headers={"Content-Disposition": f'attachment; filename="{COLLECTION}"'})

    @app.get("/api/admin/postman/environment.json")
    def postman_environment(request: Request) -> JSONResponse:
        """The environment for this server: its address and the signed-in user filled in; secrets stay empty."""
        require_admin(request)
        environment = postman_file(ENVIRONMENT)
        session = getattr(request.state, "session", None) or {}
        base = str(request.base_url).rstrip("/")
        fill = {"baseUrl": base, "username": session.get("username", "admin")}
        for item in environment.get("values", []):
            if item.get("key") in fill:
                item["value"] = fill[item["key"]]
        environment["name"] = f"{BRAND_NAME} — {request.url.hostname}"
        name = "Reporting-Service.postman_environment.json"
        return JSONResponse(environment, headers={"Content-Disposition": f'attachment; filename="{name}"'})
