"""/home after signing in: a short summary of what this account can do and what is there already.

    GET /api/home    parts by right: {designer, server, query, analytics, admin}; an account sees only its own parts
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request

from backend.app.analytics import store as analytics_store
from backend.app.connectors.profiles import AREAS, profiles_in
from backend.app.db import get_records
from backend.app.logs import LOG
from backend.app.security import permissions
from backend.app.security.access import enabled_areas
from backend.app.security.users import load_users

RECENT = 4


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    def designer(_session: dict[str, Any]) -> dict[str, Any]:
        drafts = [ctx.workspace_summary(data) for _, data in ctx.workspace_entries()]
        drafts.sort(key=lambda item: item.get("updatedAt") or "", reverse=True)
        return {"reports": len(drafts), "notPublished": sum(not item["onServer"] for item in drafts),
                "recent": [{name: item[name] for name in ("id", "title", "folder", "updatedAt")} for item in drafts[:RECENT]]}

    def server(session: dict[str, Any]) -> dict[str, Any]:
        rules = permissions.load_rules()
        reports = [item for item in ctx.published_report_summaries() if permissions.level(session, item["folder"], rules) >= permissions.RUN]
        return {"reports": len(reports), "folders": len({item["folder"] for item in reports})}

    def analytics(session: dict[str, Any]) -> dict[str, Any]:
        rules = permissions.load_rules()
        boards = [item for item in analytics_store.dashboards()
                  if permissions.level(session, item.get("folder") or "Analytics", rules) >= permissions.RUN]
        boards.sort(key=lambda item: item.get("updatedAt") or "", reverse=True)
        return {"dashboards": len(boards), "datasets": len(analytics_store.datasets()),
                "mayModel": bool(session.get("superAdmin")) or bool(set(session["rights"]) & {"designer", "admin"}),
                "recent": [{"id": item["id"], "name": item.get("name"), "tiles": len(item.get("tiles") or [])} for item in boards[:RECENT]]}

    def query(_session: dict[str, Any]) -> dict[str, Any]:
        return {}

    def admin(_session: dict[str, Any]) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        failed = [run for run in get_records().runs(since=since, limit=500) if run.get("status") == "failed"]
        return {"users": sum(not user.get("temporary") for user in load_users()), "failedRuns24h": len(failed)}

    @app.get("/api/home")
    def home(request: Request) -> dict[str, Any]:
        session = ctx.current_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Sign in first")
        parts: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "designer": designer, "server": server, "query": query, "analytics": analytics, "admin": admin}
        out: dict[str, Any] = {}
        areas = enabled_areas()
        for right, part in parts.items():
            if right not in session["rights"] or (right != "query" and right not in areas):
                continue
            try:
                out[right] = part(session)
                if right in AREAS:                             # every part has its own data sources
                    out[right]["dataSources"] = len(profiles_in(right))
            except Exception:  # noqa: BLE001 - one part that cannot be read must not hide the others
                LOG.exception("Home: the %s summary could not be made", right)
                out[right] = {}
        return out
