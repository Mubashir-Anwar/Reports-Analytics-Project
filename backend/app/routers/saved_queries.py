"""My queries: queries a user keeps in Query (and uses in the Designer), private unless shared.

    GET    /api/query/saved              -> {mine: [...], shared: [...]}   shared = other people's queries shared with me
    POST   /api/query/saved              {name, connectionId, kind, collection, text, description, sharedWith}
    PUT    /api/query/saved/{id}         the owner changes it (also who it is shared with)
    DELETE /api/query/saved/{id}         the owner deletes it
    GET    /api/query/saved/people       who a query can be shared with (user names)

kind is "mongo" (an aggregation pipeline on `collection`) or "sql". Nobody sees a query that is not theirs unless
its owner shared it with them (by name, or with everyone: "*"). People it is shared with may use it and save a copy.
"""
from __future__ import annotations

import re
import secrets
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.db import model
from backend.app.db.entities import SAVED_QUERIES, get_entities

ID = re.compile(r"^sq_[a-f0-9]{12}$")
KINDS = ("mongo", "sql")
EVERYONE = "*"


def register(app: FastAPI, ctx: SimpleNamespace) -> None:

    def me(request: Request) -> str:
        """The signed-in user (or the automation token) — needs the Query, Designer or Admin right."""
        ctx.require_admin(request, "sources")
        session = getattr(request.state, "session", None)
        if session is not None and not session["rights"] & {"query", "designer", "admin"}:
            raise HTTPException(status_code=403, detail="My queries need the Query or Designer right")
        return str(getattr(request.state, "actor", "") or "admin token")[:128]

    def all_queries() -> list[dict[str, Any]]:
        return get_entities().find(SAVED_QUERIES)

    def shared_with(item: dict[str, Any], user: str) -> bool:
        people = item.get("sharedWith") or []
        return EVERYONE in people or user.casefold() in {str(name).casefold() for name in people}

    def view(item: dict[str, Any], user: str) -> dict[str, Any]:
        mine = str(item.get("owner", "")).casefold() == user.casefold()
        return {key: item.get(key) for key in ("id", "name", "description", "connectionId", "kind", "collection", "text", "stages", "owner",
                                               "createdAt", "updatedAt")} | {"sharedWith": item.get("sharedWith") or [] if mine else [],
                                                                            "mine": mine}

    def clean(payload: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = {**(current or {}), **payload}
        name = str(merged.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Give the query a name")
        if len(name) > 120:
            raise HTTPException(status_code=422, detail="The name is at most 120 characters")
        kind = str(merged.get("kind") or "").lower()
        if kind not in KINDS:
            raise HTTPException(status_code=422, detail="kind is mongo or sql")
        text = str(merged.get("text") or "")
        if not text.strip():
            raise HTTPException(status_code=422, detail="The query is empty")
        if len(text) > 100_000:
            raise HTTPException(status_code=422, detail="The query is too long (at most 100,000 characters)")
        connection_id = str(merged.get("connectionId") or "").strip()
        if not connection_id or len(connection_id) > 64:
            raise HTTPException(status_code=422, detail="Choose the data source of the query")
        collection = str(merged.get("collection") or "").strip() if kind == "mongo" else ""
        if len(collection) > 255:
            raise HTTPException(status_code=422, detail="The collection name is too long")
        people = merged.get("sharedWith") or []
        if not isinstance(people, list) or len(people) > 200:
            raise HTTPException(status_code=422, detail="sharedWith is a list of user names")
        from backend.app.security.users import load_users  # noqa: PLC0415
        known = {str(user["username"]).casefold(): user["username"] for user in load_users()}
        shared: list[str] = []
        for raw in people:
            text_name = str(raw).strip()
            if text_name == EVERYONE:
                shared = [EVERYONE]
                break
            if text_name.casefold() not in known:
                raise HTTPException(status_code=422, detail=f"There is no user called {text_name[:64]}")
            if known[text_name.casefold()] not in shared:
                shared.append(known[text_name.casefold()])
        stages = merged.get("stages") or [] if kind == "mongo" else []       # the Aggregate steps of Query, to open it again
        if not isinstance(stages, list) or len(stages) > 100:
            raise HTTPException(status_code=422, detail="stages is a list of at most 100 steps")
        stages = [{"op": str(s.get("op", ""))[:40], "body": str(s.get("body", ""))[:20_000], "on": s.get("on") is not False}
                  for s in stages if isinstance(s, dict) and str(s.get("op", "")).startswith("$")]
        return {"name": name, "kind": kind, "text": text, "connectionId": connection_id, "collection": collection, "stages": stages,
                "description": str(merged.get("description") or "").strip()[:500], "sharedWith": shared}

    def owned(query_id: str, user: str) -> dict[str, Any]:
        item = get_entities().get(SAVED_QUERIES, query_id) if ID.fullmatch(query_id or "") else None
        if item is None or (str(item.get("owner", "")).casefold() != user.casefold() and not shared_with(item, user)):
            raise HTTPException(status_code=404, detail="There is no such query in your list")
        if str(item.get("owner", "")).casefold() != user.casefold():
            raise HTTPException(status_code=403, detail=f"Only {item.get('owner')} can change this query; save a copy to change yours")
        return item

    @app.get("/api/query/saved")
    def saved_list(request: Request) -> dict[str, Any]:
        user = me(request)
        items = sorted(all_queries(), key=lambda item: str(item.get("name", "")).casefold())
        mine = [view(item, user) for item in items if str(item.get("owner", "")).casefold() == user.casefold()]
        shared = [view(item, user) for item in items if str(item.get("owner", "")).casefold() != user.casefold() and shared_with(item, user)]
        return {"mine": mine, "shared": shared, "me": user}

    @app.get("/api/query/saved/people")
    def saved_people(request: Request) -> dict[str, Any]:
        user = me(request)
        from backend.app.security.users import load_users  # noqa: PLC0415
        return {"people": sorted((u["username"] for u in load_users() if not u.get("disabled") and u["username"].casefold() != user.casefold()),
                                 key=str.casefold)}

    @app.post("/api/query/saved")
    async def saved_create(request: Request) -> dict[str, Any]:
        user = me(request)
        record = clean(await ctx.json_body(request))
        stamp = model.iso(model.now())
        record.update({"id": f"sq_{secrets.token_hex(6)}", "owner": user, "createdAt": stamp, "updatedAt": stamp})
        get_entities().put(SAVED_QUERIES, record)
        return {"query": view(record, user)}

    @app.put("/api/query/saved/{query_id}")
    async def saved_update(query_id: str, request: Request) -> dict[str, Any]:
        user = me(request)
        current = owned(query_id, user)
        record = {**current, **clean(await ctx.json_body(request), current), "updatedAt": model.iso(model.now())}
        get_entities().put(SAVED_QUERIES, record)
        return {"query": view(record, user)}

    @app.delete("/api/query/saved/{query_id}")
    def saved_delete(query_id: str, request: Request) -> dict[str, Any]:
        user = me(request)
        owned(query_id, user)
        get_entities().delete(SAVED_QUERIES, query_id)
        return {"deleted": query_id}
