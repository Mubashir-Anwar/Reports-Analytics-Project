"""Security state that every app instance must see: signed-out sessions and failed sign-ins.

Kept in the store (files or PostgreSQL), so a logout on one instance also ends the session on the others, and the
sign-in limit counts attempts on all instances. Reads of the revoked list are cached for a couple of seconds."""
from __future__ import annotations

import threading
import time

from backend.app.storage.store import get_store

REVOKED_KEY = "meta/revoked-sessions.json"
FAILURES_KEY = "meta/login-failures.json"
CACHE_SECONDS = 2.0
_LOCK = threading.Lock()
_cache: dict[str, object] = {"at": 0.0, "revoked": {}, "store": None}


def _now() -> float:
    return time.time()


def _where(store) -> str:
    """Which store the cache belongs to (tests and tools may switch the data directory)."""
    return getattr(store, "url", None) or str(store.root)


def revoke_session(nonce: str, lifetime_seconds: float) -> None:
    if not nonce:
        return
    with _LOCK:
        store = get_store()
        revoked = {n: exp for n, exp in (store.read_json(REVOKED_KEY) or {}).items() if exp > _now()}
        revoked[nonce] = _now() + lifetime_seconds
        store.write_json(REVOKED_KEY, revoked)
        _cache.update({"at": _now(), "revoked": revoked, "store": _where(store)})


def is_revoked(nonce: str | None) -> bool:
    if not nonce:
        return False
    store = get_store()
    if _now() - float(_cache["at"]) > CACHE_SECONDS or _cache["store"] != _where(store):
        try:
            _cache.update({"at": _now(), "revoked": store.read_json(REVOKED_KEY) or {}, "store": _where(store)})
        except (OSError, ValueError):
            pass
    expiry = _cache["revoked"].get(nonce)  # type: ignore[union-attr]
    return bool(expiry and expiry > _now())


def recent_failures(client: str, window_seconds: float = 600) -> int:
    failures = (get_store().read_json(FAILURES_KEY) or {}).get(client, [])
    return sum(1 for stamp in failures if stamp > _now() - window_seconds)


def record_failure(client: str, window_seconds: float = 600) -> None:
    with _LOCK:
        store = get_store()
        data = {c: [s for s in stamps if s > _now() - window_seconds] for c, stamps in (store.read_json(FAILURES_KEY) or {}).items()}
        data = {c: stamps for c, stamps in data.items() if stamps}
        data.setdefault(client, []).append(_now())
        store.write_json(FAILURES_KEY, data)


def clear_failures(client: str) -> None:
    with _LOCK:
        store = get_store()
        data = store.read_json(FAILURES_KEY) or {}
        if client in data:
            data.pop(client)
            store.write_json(FAILURES_KEY, data)


# ----------------------------------------------------------------- one browser per user
# Signing in on another browser (or a private window) replaces the session there: the old one is told so and is signed
# out GRACE seconds later. REPORTING_SINGLE_SESSION=false lets a user stay signed in on several browsers.
ACTIVE_KEY = "meta/active-sessions.json"
REPLACED_KEY = "meta/replaced-sessions.json"
_replaced_cache: dict[str, object] = {"at": 0.0, "replaced": {}, "store": None}


def single_session() -> bool:
    import os  # noqa: PLC0415
    return os.getenv("REPORTING_SINGLE_SESSION", "true").strip().lower() not in ("0", "false", "no", "off")


def grace_seconds() -> float:
    import os  # noqa: PLC0415
    try:
        return max(0.0, min(float(os.getenv("REPORTING_SINGLE_SESSION_GRACE", "10")), 300.0))
    except ValueError:
        return 10.0


def claim_session(username: str, nonce: str, agent: str, lifetime_seconds: float) -> dict | None:
    """Make `nonce` the user's one session. Returns the session it replaces ({agent, at}) or None."""
    if not single_session() or not username or not nonce:
        return None
    key = username.casefold()
    with _LOCK:
        store = get_store()
        active = {u: item for u, item in (store.read_json(ACTIVE_KEY) or {}).items() if item.get("exp", 0) > _now()}
        previous = active.get(key)
        active[key] = {"nonce": nonce, "agent": agent[:120], "at": _now(), "exp": _now() + lifetime_seconds}
        store.write_json(ACTIVE_KEY, active)
        if not previous or previous.get("nonce") == nonce:
            return None
        replaced = {n: item for n, item in (store.read_json(REPLACED_KEY) or {}).items() if item.get("exp", 0) > _now()}
        replaced[previous["nonce"]] = {"logoutAt": _now() + grace_seconds(), "agent": agent[:120], "exp": previous.get("exp", _now() + 3600)}
        store.write_json(REPLACED_KEY, replaced)
        _replaced_cache.update({"at": _now(), "replaced": replaced, "store": _where(store)})
    return {"agent": previous.get("agent", ""), "at": previous.get("at")}


def release_session(username: str, nonce: str) -> None:
    """Sign-out: the user has no active session any more (a later sign-in replaces nothing)."""
    if not username or not nonce:
        return
    with _LOCK:
        store = get_store()
        active = store.read_json(ACTIVE_KEY) or {}
        if (active.get(username.casefold()) or {}).get("nonce") == nonce:
            active.pop(username.casefold())
            store.write_json(ACTIVE_KEY, active)


def replaced(nonce: str | None) -> dict | None:
    """{logoutAt, agent} when this session was replaced by a sign-in on another browser."""
    if not nonce:
        return None
    store = get_store()
    if _now() - float(_replaced_cache["at"]) > CACHE_SECONDS or _replaced_cache["store"] != _where(store):
        try:
            _replaced_cache.update({"at": _now(), "replaced": store.read_json(REPLACED_KEY) or {}, "store": _where(store)})
        except (OSError, ValueError):
            pass
    return _replaced_cache["replaced"].get(nonce)  # type: ignore[union-attr]


def replaced_and_over(nonce: str | None) -> bool:
    item = replaced(nonce)
    return bool(item and item.get("logoutAt", 0) <= _now())


def describe_agent(user_agent: str, address: str = "") -> str:
    """'Firefox on Windows (10.0.0.5)' from a User-Agent header."""
    ua = user_agent or ""
    browser = next((name for token, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                              ("Safari/", "Safari")) if token in ua), "a browser")
    system = next((name for token, name in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                             ("Mac OS", "macOS"), ("Linux", "Linux")) if token in ua), "")
    text = f"{browser} on {system}" if system else browser
    return f"{text} ({address})" if address else text
