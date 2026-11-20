"""Working together: who else is on the Designer / Analytics / Server right now, the team chat, and the notice that this
browser is being signed out because the same user signed in somewhere else.

    POST   /api/team/heartbeat   {tab, app, place, title} -> {me, people, session: {replaced}, lastMessage}
    DELETE /api/team/heartbeat?tab=   the page is closed
    GET    /api/team/messages?after=N    messages for me (to everyone, from me, or to me)
    POST   /api/team/messages    {text, to?, place?}
    GET    /api/team/users       names of every account (to start a direct message)

Any signed-in user may use them, whatever their rights.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from backend.app.security.users import load_users
from backend.app.services import team
from backend.app.services.guards import replaced_notice


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    def signed_in(request: Request, write: bool = False) -> dict[str, Any]:
        session = ctx.current_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Sign in first")
        if write:
            ctx.require_json_request(request)
        return session

    @app.post("/api/team/heartbeat")
    async def team_heartbeat(request: Request) -> dict[str, Any]:
        session = signed_in(request, write=True)
        payload = await ctx.json_body(request)
        try:
            team.heartbeat(session["username"], str(payload.get("tab", "")), str(payload.get("app", "")),
                           str(payload.get("place", "")), str(payload.get("title", "")))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        me = session["username"].casefold()
        return {"me": session["username"], "people": [p for p in team.people() if p["username"].casefold() != me],
                "session": {"replaced": replaced_notice(session)}, "lastMessage": team.last_seq(session["username"])}

    @app.delete("/api/team/heartbeat")
    def team_leave(request: Request, tab: str = "") -> dict[str, str]:
        session = signed_in(request)
        team.leave(session["username"], tab)
        return {"status": "left"}

    @app.get("/api/team/messages")
    def team_messages(request: Request, after: int = 0) -> dict[str, Any]:
        session = signed_in(request)
        return {"messages": team.messages(session["username"], max(0, after))}

    @app.post("/api/team/messages")
    async def team_post(request: Request) -> dict[str, Any]:
        session = signed_in(request, write=True)
        payload = await ctx.json_body(request)
        users = {u["username"] for u in load_users() if not u.get("disabled")}
        try:
            return team.post(session["username"], payload.get("text", ""), payload.get("to"), str(payload.get("place", "")), users)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/team/users")
    def team_users(request: Request) -> dict[str, Any]:
        session = signed_in(request)
        me = session["username"].casefold()
        return {"users": sorted((u["username"] for u in load_users() if not u.get("disabled") and u["username"].casefold() != me),
                                key=str.casefold)}
