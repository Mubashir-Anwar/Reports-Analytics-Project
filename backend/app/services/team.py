"""Who is working right now, and the team chat.

Presence: every open page (a "tab") sends a heartbeat every few seconds with the app (designer, analytics, server, admin)
and the place in it (the report or workbook it has open). A tab that has not sent one for STALE_SECONDS is gone.
Chat: messages to everyone or to one person, the last MAX_MESSAGES kept. A direct message is only given to the two
people in it.

Both live in the store (files or PostgreSQL) so every app instance sees the same people and messages.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from backend.app.storage.store import get_store

PRESENCE_KEY = "meta/team-presence.json"
CHAT_KEY = "meta/team-chat.json"
STALE_SECONDS = 25.0
MAX_MESSAGES = 500
MAX_TEXT = 2000
APPS = ("designer", "analytics", "server", "admin", "runner")
_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


def _iso(stamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


def heartbeat(username: str, tab: str, app: str, place: str = "", title: str = "") -> None:
    tab = str(tab or "")[:64]
    if not tab:
        raise ValueError("tab is needed")
    app = app if app in APPS else "server"
    with _LOCK:
        store = get_store()
        data = store.read_json(PRESENCE_KEY) or {}
        fresh: dict[str, Any] = {}
        for tab_id, item in data.items():
            if item.get("seen", 0) > _now() - STALE_SECONDS:
                fresh[tab_id] = item
        first = fresh.get(tab, {}).get("since") if fresh.get(tab, {}).get("username") == username else None
        fresh[tab] = {"username": username, "app": app, "place": str(place or "")[:200], "title": str(title or "")[:200],
                      "seen": _now(), "since": first or _now()}
        store.write_json(PRESENCE_KEY, fresh)


def leave(username: str, tab: str) -> None:
    with _LOCK:
        store = get_store()
        data = store.read_json(PRESENCE_KEY) or {}
        if (data.get(tab) or {}).get("username") == username:
            data.pop(tab)
            store.write_json(PRESENCE_KEY, data)


def people() -> list[dict[str, Any]]:
    """Everyone with an open page: [{username, places: [{app, place, title, since}]}], by name."""
    data = get_store().read_json(PRESENCE_KEY) or {}
    out: dict[str, dict[str, Any]] = {}
    for item in data.values():
        if item.get("seen", 0) <= _now() - STALE_SECONDS:
            continue
        person = out.setdefault(item["username"].casefold(), {"username": item["username"], "places": []})
        where = {"app": item["app"], "place": item.get("place", ""), "title": item.get("title", ""), "since": _iso(item.get("since", _now()))}
        if where not in person["places"]:
            person["places"].append(where)
    return sorted(out.values(), key=lambda p: p["username"].casefold())


# ------------------------------------------------------------------ chat
def _visible(message: dict[str, Any], username: str) -> bool:
    me = username.casefold()
    return not message.get("to") or message["to"].casefold() == me or message["from"].casefold() == me


def messages(username: str, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    data = get_store().read_json(CHAT_KEY) or {}
    found = [m for m in data.get("messages", []) if m["seq"] > after and _visible(m, username)]
    return found[-limit:]


def last_seq(username: str) -> int:
    found = messages(username, 0, 1)
    return found[-1]["seq"] if found else 0


def post(username: str, text: str, to: str | None = None, place: str = "", known_users: set[str] | None = None) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ValueError("Write a message first")
    if len(text) > MAX_TEXT:
        raise ValueError(f"A message can be at most {MAX_TEXT} characters")
    to = str(to or "").strip() or None
    if to and known_users is not None and to.casefold() not in {u.casefold() for u in known_users}:
        raise ValueError(f"There is no user called {to}")
    if to and to.casefold() == username.casefold():
        raise ValueError("Choose someone else to write to")
    with _LOCK:
        store = get_store()
        data = store.read_json(CHAT_KEY) or {}
        seq = int(data.get("seq", 0)) + 1
        message = {"seq": seq, "id": uuid.uuid4().hex[:12], "from": username, "to": to, "text": text,
                   "place": str(place or "")[:200], "at": _iso(_now())}
        data = {"seq": seq, "messages": [*data.get("messages", []), message][-MAX_MESSAGES:]}
        store.write_json(CHAT_KEY, data)
    return message
